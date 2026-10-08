"""Response-time comparisons between groups of neighborhoods, as set in the project's PLAN.md and config "analysis".

  python analyze.py response.json [--shuffle SEED]   ->  out/results.json, out/results.md, out/*.csv

--shuffle assigns neighborhoods to groups at random (keeping group sizes), to test the code without
looking at real comparisons. Its outputs go to out/shuffled/.

Method, per priority category:
  Sample      calls from the public (origin 'public') in residential neighborhoods that have groups,
              with an arrival 0 to 24 hours after entry. The share with no arrival is reported separately.
  Measure     minutes from entry to first arrival (wait); also entry to dispatch and dispatch to arrival.
  Adjusted    each group's calls are reweighted (raking) so their mix of call type, hour of the week and
              how busy the precinct was that hour matches the citywide mix for that priority. Adjusted
              median and 90th percentile are weighted quantiles.
  Uncertainty cluster bootstrap over neighborhoods, resampled within each group; 95% percentile intervals;
              two-sided p from the bootstrap standard error; Holm across the primary family.
  Threshold   each gap is classed against the smallest effect that matters (config thresholds).
  Ladder      OLS of log minutes with fixed effects, errors clustered by neighborhood: what narrows a gap.
  Replication the adjusted gaps in each period (config periods), weights raked within each period.
"""
import argparse
import json
import math
import sys

import numpy as np
import pandas as pd

from rt import RTProject, dump, md_table, write_md

# histogram bins (minutes): 3-second bins to 2 hours, then 1-minute bins to 24 hours
EDGES = np.concatenate([np.arange(0, 120, 0.05), np.arange(120, 1441, 1.0)])
NB = len(EDGES) - 1
MEASURES = {"wait": "Entry to arrival", "to_dispatch": "Entry to dispatch", "travel": "Dispatch to arrival"}


# ---------- data

def load(p, shuffle=None):
    a = p.cfg["analysis"]
    con = p.con()
    nb = pd.read_csv(p.data / "neighborhoods.csv", dtype={"id": str})
    nb = nb[nb["in_groups"]].copy()
    if shuffle is not None:
        rng = np.random.default_rng(shuffle)
        for g in a["groupings"].values():
            nb[g["column"]] = rng.permutation(nb[g["column"]].to_numpy())
    cols = [g["column"] for g in a["groupings"].values()]
    con.register("nb_df", nb[["id"] + cols])
    stf = p.cfg["geo"].get("staffing")
    origins = "', '".join(a.get("origins", ["public"]))
    # calls from the public per district and clock hour, all priorities, every location
    con.execute(f"""CREATE TEMP TABLE vol AS SELECT district, clock_hour, count(*) AS volume
                    FROM read_parquet('{p.calls}') WHERE origin IN ('{origins}') GROUP BY 1, 2""")
    df = con.execute(f"""
        SELECT c.priority, c.period, c.year, pt.nbhd, c.district, c.call_type, c.call_desc, c.hour, c.dow,
               c.origin, c.wait, c.to_dispatch, c.travel, (c.t_arrive IS NULL) AS no_arrival,
               epoch(c.t_close - c.t_create) / 60.0 AS to_close, pt.station_km, v.volume,
               {', '.join(f'nb.{x}' for x in cols)}
        FROM read_parquet('{p.calls}') c
        JOIN read_parquet('{p.data / 'points.parquet'}') pt USING (lat, lon, district)
        JOIN nb_df nb ON nb.id = pt.nbhd
        LEFT JOIN vol v USING (district, clock_hour)
        WHERE c.period IS NOT NULL""").df()
    df["how"] = (df["dow"] - 1) * 24 + df["hour"]
    if stf:
        s = pd.read_csv(p.path(stf), dtype={"district": str})
        df = df.merge(s[["district", "officers"]], on="district", how="left")
    return df, nb


def valid(df, measure="wait"):
    m = df["wait"].notna() & (df["wait"] > 0) & (df["wait"] <= 1440)
    if measure != "wait":
        m &= df[measure].notna() & (df[measure] >= 0)
    return m


def coded(df, a, prio_mask, vband="vband"):
    """Integer codes for the adjustment margins (call type, hour of the week, busyness band) for calls in prio_mask."""
    x = df.loc[prio_mask]
    min_n = a.get("call_type_min", 200)
    vc = x["call_type"].value_counts()
    keep = set(vc[vc >= min_n].index)
    ct = np.where(x["call_type"].isin(keep), x["call_type"], "other")
    return {"call_type": pd.factorize(ct)[0], "how": x["how"].to_numpy().astype(int), "volume": x[vband].to_numpy().astype(int)}


# ---------- raking and weighted quantiles

def rake(margins, groups, max_iter=200, tol=1e-7, cap=20.0):
    """Weights so each group's distribution on every margin matches the pooled distribution."""
    w = np.ones(len(groups))
    targets = {k: np.bincount(v) / len(v) for k, v in margins.items()}
    for g in np.unique(groups):
        idx = np.flatnonzero(groups == g)
        wg = np.ones(len(idx))
        for _ in range(max_iter):
            worst = 0.0
            for k, v in margins.items():
                lev = v[idx]
                cur = np.bincount(lev, weights=wg, minlength=len(targets[k]))
                cur = cur / cur.sum()
                t = np.where(cur > 0, targets[k], 0)
                t = t / t.sum()
                ratio = np.divide(t, cur, out=np.zeros_like(t), where=cur > 0)
                wg *= ratio[lev]
                worst = max(worst, np.abs(cur - t).max())
            if worst < tol:
                break
        wg = np.minimum(wg / wg.mean(), cap)
        w[idx] = wg / wg.mean()
    return w


def hist_by_unit(units, values, weights, n_units):
    b = np.clip(np.searchsorted(EDGES, values, side="right") - 1, 0, NB - 1)
    H = np.zeros((n_units, NB))
    np.add.at(H, (units, b), weights)
    return H


def quantile_rows(H, q):
    """q-quantile of each row of a histogram matrix, linear within the bin."""
    H = np.atleast_2d(H)
    c = np.cumsum(H, axis=1)
    tot = c[:, -1:]
    target = q * tot
    i = np.minimum((c < target).sum(axis=1), NB - 1)
    prev = np.where(i > 0, c[np.arange(len(c)), i - 1], 0.0)
    inbin = H[np.arange(len(H)), i]
    frac = np.divide(target[:, 0] - prev, inbin, out=np.zeros(len(H)), where=inbin > 0)
    return EDGES[i] + frac * (EDGES[i + 1] - EDGES[i])


# ---------- one comparison set

def compare(df, a, mask, group_col, levels, units_index, weights, measure, rng, B, qs=(0.5, 0.9)):
    """Raw and adjusted quantiles per group with bootstrap draws. Returns {level: {...}} and draws."""
    x = df.loc[mask]
    u = x["nbhd"].map(units_index).to_numpy()
    vals = x[measure].to_numpy()
    out, draws = {}, {}
    for kind, w in [("raw", np.ones(len(x))), ("adj", weights)]:
        H = hist_by_unit(u, vals, w, len(units_index))
        for lev in levels:
            rows = np.array([units_index[n] for n in x.loc[x[group_col] == lev, "nbhd"].unique()])
            if len(rows) == 0:
                continue
            Hg = H[rows]
            est = {q: float(quantile_rows(Hg.sum(axis=0), q)[0]) for q in qs}
            counts = rng.multinomial(len(rows), np.full(len(rows), 1 / len(rows)), size=B)
            Hb = counts @ Hg
            out.setdefault(lev, {})[kind] = est
            draws.setdefault(lev, {})[kind] = {q: quantile_rows(Hb, q) for q in qs}
            if kind == "raw":
                out[lev].update({"neighborhoods": int(len(rows)), "calls": int((x[group_col] == lev).sum())})
    return out, draws


def gap_stats(est, draws, lev, ref, kind, q, threshold):
    d = est[lev][kind][q] - est[ref][kind][q]
    db = draws[lev][kind][q] - draws[ref][kind][q]
    rel = est[lev][kind][q] / est[ref][kind][q] - 1
    relb = draws[lev][kind][q] / draws[ref][kind][q] - 1
    se = float(np.std(db, ddof=1))
    z = d / se if se > 0 else float("inf")
    p = float(math.erfc(abs(z) / math.sqrt(2)))
    lo, hi = np.percentile(db, [2.5, 97.5])
    rlo, rhi = np.percentile(relb, [2.5, 97.5])
    r = {"group": lev, "reference": ref, "estimate": est[lev][kind][q], "reference_estimate": est[ref][kind][q],
         "diff": d, "diff_lo": float(lo), "diff_hi": float(hi), "rel": rel, "rel_lo": float(rlo), "rel_hi": float(rhi),
         "se": se, "p": p}
    if threshold:
        if "minutes" in threshold:
            T = threshold["minutes"]
            r.update({"threshold": f"{T} minutes", "meets": abs(d) >= T, "inside": (-T < lo) and (hi < T)})
        else:
            T = threshold["percent"] / 100
            r.update({"threshold": f"{threshold['percent']}%", "meets": abs(rel) >= T, "inside": (-T < rlo) and (rhi < T)})
    return r


def holm(ps):
    order = np.argsort(ps)
    m = len(ps)
    adj = np.empty(m)
    running = 0.0
    for k, i in enumerate(order):
        running = max(running, min(1.0, (m - k) * ps[i]))
        adj[i] = running
    return adj


def verdict(r, alpha):
    sig = r["p_holm"] < alpha if "p_holm" in r else r["p"] < alpha
    if sig and r.get("meets"):
        return "gap that matters"
    if r.get("inside"):
        return "smaller than the smallest gap that matters"
    if sig:
        return "real but smaller than the smallest gap that matters"
    return "inconclusive"


# ---------- regression ladder

def demean(arrs, factors, tol=1e-8, max_iter=500):
    """Remove fixed effects from each column by alternating projections (weights 1)."""
    X = np.column_stack(arrs).astype(float)
    if not factors:
        return X - X.mean(axis=0)
    counts = [np.bincount(f) for f in factors]
    for _ in range(max_iter):
        prev = X.copy()
        for f, cnt in zip(factors, counts):
            means = np.column_stack([np.bincount(f, weights=X[:, j]) for j in range(X.shape[1])]) / cnt[:, None]
            X -= means[f]
        if np.abs(X - prev).max() < tol:
            break
    return X


def cluster_ols(y, X, clusters):
    XtX = X.T @ X
    beta = np.linalg.solve(XtX, X.T @ y)
    e = y - X @ beta
    G = clusters.max() + 1
    S = np.zeros((G, X.shape[1]))
    np.add.at(S, clusters, X * e[:, None])
    n, k = X.shape
    c = G / (G - 1) * (n - 1) / (n - k)
    inv = np.linalg.inv(XtX)
    V = c * inv @ (S.T @ S) @ inv
    return beta, np.sqrt(np.diag(V))


def ladder(x, group_col, levels, ref, cluster_codes, steps):
    """Gap of each level against ref, as a percent difference in typical minutes, after each step's controls."""
    y = np.log(x["wait"].to_numpy())
    dummies = [(x[group_col] == lev).to_numpy().astype(float) for lev in levels if lev != ref]
    names = [lev for lev in levels if lev != ref]
    rows = []
    for step in steps:
        cont = [c(x) for c in step.get("continuous", [])]
        keep = np.ones(len(x), bool)
        for c in cont:
            keep &= np.isfinite(c)
        facs = [pd.factorize(f(x)[keep])[0] for f in step.get("factors", [])]
        cols = [d[keep] for d in dummies] + [c[keep] for c in cont]
        Z = demean([y[keep]] + cols, facs)
        beta, se = cluster_ols(Z[:, 0], Z[:, 1:], cluster_codes[keep])
        rows.append({"step": step["label"], "calls": int(keep.sum()),
                     **{f"{n}": float(np.expm1(b)) for n, b in zip(names, beta)},
                     **{f"{n}_lo": float(np.expm1(b - 1.96 * s)) for n, b, s in zip(names, beta, se)},
                     **{f"{n}_hi": float(np.expm1(b + 1.96 * s)) for n, b, s in zip(names, beta, se)}})
    return rows


# ---------- main

LADDER_LABELS = {
    "none": "No controls",
    "type": "Call type",
    "type_how": "Call type, hour of the week",
    "plan": "Call type, hour of the week, precinct busyness (the plan's controls)",
    "distance": "The plan's controls and distance to the station house",
    "staffing": "The plan's controls, distance, and officers per call (2026 snapshot)",
    "precinct": "The plan's controls, within the same precinct",
}


def ladder_steps(has_staffing):
    plan = [lambda d: d["ct"], lambda d: d["how"], lambda d: d["vband"]]
    dist = lambda d: np.log(d["station_km"].to_numpy() + 0.1)
    steps = [{"key": "none"}, {"key": "type", "factors": plan[:1]}, {"key": "type_how", "factors": plan[:2]},
             {"key": "plan", "factors": plan}, {"key": "distance", "factors": plan, "continuous": [dist]}]
    if has_staffing:
        steps.append({"key": "staffing", "factors": plan, "continuous": [dist, lambda d: np.log(d["officers_per_1000"].to_numpy())]})
    steps.append({"key": "precinct", "factors": plan + [lambda d: d["district"]]})
    for st in steps:
        st["label"] = LADDER_LABELS[st["key"]]
    return steps


class Run:
    def __init__(self, p, shuffle):
        self.p, self.a = p, p.cfg["analysis"]
        self.B, self.alpha = self.a.get("bootstrap", 2000), self.a.get("alpha", 0.05)
        self.rng = np.random.default_rng(self.a.get("seed", 1))
        self.df, self.nb = load(p, shuffle)
        self.main = self.a["main_periods"]
        self.origins = self.a.get("origins", ["public"])
        self.prios = [x["value"] for x in p.priorities]
        df = self.df
        cuts = np.quantile(df.loc[df["period"].isin(self.main), "volume"], np.linspace(0, 1, self.a.get("volume_bands", 5) + 1)[1:-1])
        self.cuts = cuts
        df["vband"] = np.searchsorted(cuts, df["volume"], side="right")
        # busyness relative to the precinct's own average hour (sensitivity)
        rel = df["volume"] / df.groupby("district")["volume"].transform("mean")
        rcuts = np.quantile(rel[df["period"].isin(self.main)], np.linspace(0, 1, self.a.get("volume_bands", 5) + 1)[1:-1])
        df["vband_rel"] = np.searchsorted(rcuts, rel, side="right")
        if "officers" in df:
            per_year = df[df["period"].isin(self.main) & df["origin"].isin(self.origins)].groupby("district").size() / self.a.get("main_years", 4)
            df["officers_per_1000"] = df["officers"] / df["district"].map(per_year) * 1000
        for g in self.a["groupings"].values():
            if pd.api.types.is_numeric_dtype(df[g["column"]]):
                df[g["column"]] = df[g["column"]].astype("Int64")
        self.units = {n: i for i, n in enumerate(sorted(self.nb["id"]))}

    def thr(self, pr):
        return self.a["thresholds"].get(pr, self.a["thresholds"]["default"])

    def gaps(self, mask, gkey, pr, measures=("wait",), vband="vband", tag=None):
        """Adjusted and raw quantile gaps of every level against the reference, for calls in mask."""
        g = self.a["groupings"][gkey]
        col, levels, ref = g["column"], g["levels"], g["reference"]
        df = self.df
        if mask.sum() == 0:
            return [], {}
        mg = coded(df, self.a, mask, vband)
        w = rake(mg, df.loc[mask, col].astype(str).to_numpy())
        rows, ests = [], {}
        for meas in measures:
            est, draws = compare(df, self.a, mask, col, levels, self.units, w, meas, self.rng, self.B)
            ests[meas] = est
            if ref not in est:
                continue
            for lev in levels:
                if lev == ref or lev not in est:
                    continue
                for kind in ("adj", "raw"):
                    for q in (0.5, 0.9):
                        r = gap_stats(est, draws, lev, ref, kind, q, self.thr(pr) if q == 0.5 else None)
                        r.update({"grouping": gkey, "priority": pr, "measure": meas, "kind": kind, "quantile": q,
                                  "neighborhoods": est[lev]["neighborhoods"], "calls": est[lev]["calls"],
                                  "ref_neighborhoods": est[ref]["neighborhoods"], "ref_calls": est[ref]["calls"]})
                        if tag:
                            r["sensitivity"] = tag
                        rows.append(r)
        return rows, ests

    def base(self, pr, periods, origins=None):
        df = self.df
        return (df["priority"] == pr) & df["period"].isin(periods) & df["origin"].isin(origins or self.origins)

    def no_arrival(self, mask, gkey, pr):
        g = self.a["groupings"][gkey]
        x = self.df.loc[mask]
        u = x["nbhd"].map(self.units).to_numpy()
        tot = np.bincount(u, minlength=len(self.units)).astype(float)
        miss = np.bincount(u, weights=x["no_arrival"].to_numpy().astype(float), minlength=len(self.units))
        # leaving out calls closed within two minutes with no arrival
        quick = (x["no_arrival"] & (x["to_close"] <= 2)).to_numpy().astype(float)
        qn = np.bincount(u, weights=quick, minlength=len(self.units))
        out = []
        for lev in g["levels"]:
            rows = np.array([self.units[n] for n in x.loc[x[g["column"]] == lev, "nbhd"].unique()])
            if len(rows) == 0:
                continue
            cnt = self.rng.multinomial(len(rows), np.full(len(rows), 1 / len(rows)), size=self.B)
            sb = (cnt @ miss[rows]) / (cnt @ tot[rows])
            sq = (cnt @ (miss[rows] - qn[rows])) / (cnt @ (tot[rows] - qn[rows]))
            out.append({"grouping": gkey, "priority": pr, "level": lev, "share": float(miss[rows].sum() / tot[rows].sum()),
                        "lo": float(np.percentile(sb, 2.5)), "hi": float(np.percentile(sb, 97.5)), "calls": int(tot[rows].sum()),
                        "share_excl_quick": float((miss[rows] - qn[rows]).sum() / (tot[rows] - qn[rows]).sum()),
                        "excl_lo": float(np.percentile(sq, 2.5)), "excl_hi": float(np.percentile(sq, 97.5))})
        return out

    def ladder(self, gkey, pr):
        g = self.a["groupings"][gkey]
        col = g["column"]
        mask = self.base(pr, self.main) & valid(self.df)
        x = self.df.loc[mask].copy()
        x[col] = x[col].astype(str)
        keep = set(x["call_type"].value_counts().loc[lambda s: s >= self.a.get("call_type_min", 200)].index)
        x["ct"] = np.where(x["call_type"].isin(keep), x["call_type"], "other")
        steps = ladder_steps("officers_per_1000" in x)
        rows = ladder(x, col, [str(l) for l in g["levels"]], str(g["reference"]), pd.factorize(x["nbhd"])[0], steps)
        return [{"grouping": gkey, "priority": pr, "key": st["key"], **r} for st, r in zip(steps, rows)]

    def continuous(self, pr):
        """Percent difference in typical minutes per 10 points of each group's share, and per $25,000 less income."""
        mask = self.base(pr, self.main) & valid(self.df)
        x = self.df.loc[mask, ["nbhd", "wait", "call_type", "how", "vband"]].copy()
        nb = self.nb.set_index("id")
        keep = set(x["call_type"].value_counts().loc[lambda s: s >= self.a.get("call_type_min", 200)].index)
        x["ct"] = np.where(x["call_type"].isin(keep), x["call_type"], "other")
        shares = [c for c in nb.columns if c.startswith("share_") and c != "share_white"]
        regs = [x["nbhd"].map(nb[c]).to_numpy() * 10 for c in shares] + [-x["nbhd"].map(nb["median_income"]).to_numpy() / 25000]
        names = [c.replace("share_", "") for c in shares] + ["income_25k_lower"]
        facs = [pd.factorize(x[c])[0] for c in ("ct", "how", "vband")]
        Z = demean([np.log(x["wait"].to_numpy())] + regs, facs)
        beta, se = cluster_ols(Z[:, 0], Z[:, 1:], pd.factorize(x["nbhd"])[0])
        return [{"priority": pr, "term": n, "pct": float(np.expm1(b)), "lo": float(np.expm1(b - 1.96 * s)), "hi": float(np.expm1(b + 1.96 * s))}
                for n, b, s in zip(names, beta, se)]

    def by_unit(self, unit):
        mask = self.df["period"].isin(self.main) & self.df["origin"].isin(self.origins)
        x = self.df.loc[mask]
        ok = valid(x)
        rows = []
        for (u, pr), y in x.groupby([unit, "priority"]):
            v = y.loc[ok.loc[y.index], "wait"]
            rows.append({unit: u, "priority": pr, "calls": len(y), "with_arrival": len(v),
                         "no_arrival_share": float(y["no_arrival"].mean()),
                         "median_wait": float(v.median()) if len(v) else None, "p90_wait": float(v.quantile(.9)) if len(v) else None})
        return pd.DataFrame(rows)

    def run(self):
        res = {"shuffled": None, "bootstrap": self.B, "volume_cuts": self.cuts.tolist(), "groups": {}, "levels": [],
               "primary": [], "secondary": [], "parts": [], "periods": [], "no_arrival": [], "ladder": [],
               "continuous": [], "sensitivity": []}
        df = self.df
        for gkey, g in self.a["groupings"].items():
            res["groups"][gkey] = [{"level": lev, "neighborhoods": int((self.nb[g["column"]] == lev).sum()),
                                    "residents": int(self.nb.loc[self.nb[g["column"]] == lev, "pop"].sum())} for lev in g["levels"]]
            for pr in self.prios:
                base = self.base(pr, self.main)
                rows, ests = self.gaps(base & valid(df), gkey, pr, measures=list(MEASURES))
                for r in rows:
                    primary = (r["measure"] == "wait" and r["kind"] == "adj" and r["quantile"] == 0.5
                               and r["group"] in self.a["primary_comparisons"][gkey])
                    (res["primary"] if primary else res["secondary"] if r["measure"] == "wait" else res["parts"]).append(r)
                for lev, e in ests["wait"].items():
                    res["levels"].append({"grouping": gkey, "priority": pr, "level": lev, "neighborhoods": e["neighborhoods"], "calls": e["calls"],
                                          **{f"{k}_{int(q * 100)}": v for k in ("raw", "adj") for q, v in e[k].items()},
                                          **{f"{m}_{k}_50": ests[m][lev][k][0.5] for m in ("to_dispatch", "travel") for k in ("raw", "adj")}})
                res["no_arrival"] += self.no_arrival(base, gkey, pr)
                for label in self.p.cfg["periods"]:
                    rows, _ = self.gaps(self.base(pr, [label]) & valid(df), gkey, pr)
                    res["periods"] += [dict(r, period=label) for r in rows if r["kind"] == "adj"]
                res["ladder"] += self.ladder(gkey, pr)
                # sensitivity, adjusted medians only
                sens = {
                    "every event, including officer-initiated, on-scene and sensor": (self.base(pr, self.main, ["public", "officer", "on_scene", "sensor"]) & valid(df), "vband"),
                    "without transit and highway call types": (base & valid(df) & ~df["call_desc"].fillna("").str.contains("TRANSIT|LTD ACC HWY"), "vband"),
                    "busyness relative to the precinct's own average hour": (base & valid(df), "vband_rel"),
                }
                for tag, (m, vb) in sens.items():
                    rows, _ = self.gaps(m, gkey, pr, vband=vb, tag=tag)
                    res["sensitivity"] += [r for r in rows if r["kind"] == "adj" and r["quantile"] == 0.5]
                # calls with no arrival counted as waiting until the event was closed (an upper bound)
                saved = df["wait"].copy()
                fill = df["no_arrival"] & (df["to_close"] > 0) & (df["to_close"] <= 1440)
                df.loc[fill, "wait"] = df.loc[fill, "to_close"]
                rows, _ = self.gaps(base & valid(df), gkey, pr, tag="calls with no arrival counted as waiting until closed")
                res["sensitivity"] += [r for r in rows if r["kind"] == "adj" and r["quantile"] == 0.5]
                df["wait"] = saved
                print(f"{gkey} {pr}: done", flush=True)
        for pr in self.prios:
            res["continuous"] += self.continuous(pr)
        ps = np.array([r["p"] for r in res["primary"]])
        for r, adj in zip(res["primary"], holm(ps)):
            r["p_holm"] = float(adj)
            r["verdict"] = verdict(r, self.alpha)
        for r in res["periods"]:
            if r["quantile"] == 0.5 and r["kind"] == "adj":
                r["verdict"] = verdict(r, self.alpha)
        return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--shuffle", type=int)
    o = ap.parse_args()
    p = RTProject(o.config)
    r = Run(p, o.shuffle)
    res = r.run()
    res["shuffled"] = o.shuffle
    out = p.out / "shuffled" if o.shuffle is not None else p.out
    out.mkdir(exist_ok=True)
    (out / "results.json").write_text(dump(res))
    r.by_unit("nbhd").merge(r.nb[["id", "name", "area"]], left_on="nbhd", right_on="id").drop(columns="id").to_csv(out / "by_neighborhood.csv", index=False)
    r.by_unit("district").to_csv(out / "by_precinct.csv", index=False)
    print("wrote", out / "results.json")


if __name__ == "__main__":
    main()
