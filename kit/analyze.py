"""Rates, rival-explanation tests and the adjustment model, for one focus group against the rest.

Reads the incidents in the config and out/population.json. Writes out/results.json with:
  counts and data quality       totals, by kind, known race and sex, unknown race by sex
  rates                         per 100,000 residents per year, every group by sex
  tests                         age standardization, location (indirect standardization by district),
                                type, year, premises, weapons, flags (for example intimate partner)
  model                         tract-level Poisson ladder: group, + age, + year, + district,
                                + socioeconomics, + optional covariates; by type; pairwise; covariate sensitivity
  race_coding_bound             ratios if the focus group's residents were counted alone or in combination

  python analyze.py analysis.json
"""
import math
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from common import AGE_LABELS, COARSE, COARSE_OF, Project, age_band, load_incidents, year_spans

SES = ["poverty", "log_income", "unemployment", "renters", "log_density"]


def weapon_class(w, rules):
    w = str(w).upper()
    for label, keys in rules:
        if any(k in w for k in keys):
            return label
    return "Other or unknown"


DEFAULT_WEAPONS = [("Strong-arm", ["STRONG-ARM", "HANDS", "FIST", "PERSONAL WEAPON"]),
                   ("Firearm", ["GUN", "PISTOL", "FIREARM", "RIFLE", "REVOLVER", "SHOTGUN"]),
                   ("Knife or cutting", ["KNIFE", "CUTTING", "BLADE", "MACHETE", "RAZOR", "SCISSORS", "BOTTLE", "GLASS"]),
                   ("Blunt or other object", ["BLUNT", "CLUB", "BAT", "PIPE", "ROCK", "BRICK", "VEHICLE", "STICK", "HAMMER"])]


def main():
    p = Project(sys.argv[1])
    df = load_incidents(p)
    pop = p.read_json("population.json")
    G, S = p.focus
    groups, others = p.groups, p.others
    spans, years = year_spans(p)
    MIN = p.min_pop
    rate = lambda n, residents: round(n / residents / years * 1e5) if residents and residents >= MIN else None
    known = df.dropna(subset=["race", "sex"])
    fs = known[known["sex"] == S]
    focus = fs["race"] == G
    res = {"focus": {"group": G, "sex": S, "label": p.focus_label}, "window": p.cfg["window"], "years": years, "spans": spans,
           "groups": groups, "kinds": sorted(df["kind"].unique().tolist())}

    # counts and data quality
    res["counts"] = {"total": len(df), "known": len(known), "by_kind": df["kind"].value_counts().to_dict(),
                     "unknown_race": int(df["race"].isna().sum()), "unknown_sex": int(df["sex"].isna().sum()),
                     "unknown_race_share_by_sex": {s: round(float(df.loc[df["sex"] == s, "race"].isna().mean() * 100), 1) for s in "FM"},
                     "raw_race_values": df.loc[df["race"].isna(), "race_raw"].astype(str).value_counts().head(8).to_dict(),
                     "median_age": float(df["age"].median()) if df["age"].notna().any() else None,
                     "located": int(df["lat"].notna().sum())}
    if df["district"].notna().any():
        res["counts"]["districts"] = int(df["district"].nunique())

    # rates by group and sex
    res["rates"] = {g: {sx: rate(int(((known["race"] == g) & (known["sex"] == sx)).sum()), pop["city"][g][sx]) for sx in "FM"} for g in groups}
    fr = res["rates"][G][S]
    res["ratios"] = {g: round(fr / res["rates"][g][S], 2) for g in others if res["rates"][g][S]}
    other_sex = "M" if S == "F" else "F"
    res["ratio_to_other_sex"] = {g: round(res["rates"][g][S] / res["rates"][g][other_sex], 2) for g in groups if res["rates"][g][other_sex]}
    res["sex_share"] = {k: {g: round(float((known[(known["race"] == g) & (known["kind"] == k)]["sex"] == S).mean() * 100), 1) for g in groups}
                        for k in res["kinds"]}
    res["sex_share"]["all"] = {g: round(float((known[known["race"] == g]["sex"] == S).mean() * 100), 1) for g in groups}
    if "combo_ratio" in pop and G in pop["combo_ratio"]:
        c = pop["combo_ratio"][G]
        res["race_coding_bound"] = {"combo_ratio": c, "ratios_worst_case": {g: round(r / c, 2) for g, r in res["ratios"].items()},
                                    "note": f"if every multiracial {G} resident is recorded as {G}, the denominator grows by {round((c - 1) * 100)}%"}
    by_year = known.groupby(["year", "sex"]).size().unstack(fill_value=0)
    res["by_year_counts"] = {str(y): {s: int(by_year.loc[y].get(s, 0)) for s in "FM"} for y in by_year.index}

    T = res["tests"] = {}
    # age: age-specific rates and direct standardization to the combined age mix
    if df["age"].notna().any():
        aged = fs[fs["age"].notna()].copy()
        aged["band"] = aged["age"].apply(age_band)
        standard = [sum(pop["city_age"][g][S][i] for g in groups) for i in range(14)]
        age = {"labels": AGE_LABELS, "rates": {}, "standardized": {}, "crude": {}}
        for g in groups:
            cnt = aged[aged["race"] == g]["band"].value_counts()
            pops = pop["city_age"][g][S]
            r_ = [(cnt.get(i, 0) / pops[i] / years * 1e5) if pops[i] >= 500 else None for i in range(14)]
            age["rates"][g] = [round(x) if x is not None else None for x in r_]
            age["standardized"][g] = round(sum((x or 0) * w for x, w in zip(r_, standard)) / sum(standard))
        T["age"] = age

    # location: indirect standardization by district
    if "districts" in pop and df["district"].notna().any():
        names_data, names_pop = set(df["district"].dropna().astype(str)), set(pop["districts"])
        unmatched = sorted(names_data - names_pop)
        loc, expected = [], 0.0
        for d, cells in sorted(pop["districts"].items()):
            w = fs[fs["district"].astype(str) == d]
            fn, on = int((w["race"] == G).sum()), int((w["race"].isin(others)).sum())
            fpop, opop = cells[G][S], sum(cells[g][S] for g in others)
            if opop:
                expected += fpop * on / opop
            fr_d, or_d = rate(fn, fpop), rate(on, opop)
            loc.append({"district": d, "focus_rate": fr_d, "other_rate": or_d, "ratio": round(fr_d / or_d, 2) if fr_d and or_d else None,
                        "focus_n": fn, "focus_pop": round(fpop)})
        fpop_total = sum(c[G][S] for c in pop["districts"].values())
        T["location"] = {"districts": loc, "unmatched_district_names": unmatched,
                         "actual_rate": round(int((fs["race"] == G).sum()) / fpop_total / years * 1e5),
                         "expected_if_other_rates": round(expected / fpop_total / years * 1e5)}
        if unmatched:
            print("WARNING district names in the data with no population match:", unmatched[:10])
    # type and year
    T["type"] = {k: {g: rate(int(((fs["race"] == g) & (fs["kind"] == k)).sum()), pop["city"][g][S]) for g in groups} for k in res["kinds"]}
    T["time"] = {str(y): {g: round(int(((fs["race"] == g) & (fs["year"] == y)).sum()) / pop["city"][g][S] / spans[y] * 1e5) for g in groups}
                 for y in spans if spans[y] >= 0.25}
    # premises and weapons
    if fs["premise"].notna().any():
        prem = fs["premise"].fillna("Unknown").astype(str).str.title().str.replace(r"\s*\(.*\)", "", regex=True).str.strip()
        top = prem[focus].value_counts().head(8).index.tolist()
        T["premises"] = {"labels": top, "focus": [round(float((prem[focus] == x).mean() * 100), 1) for x in top],
                         "other": [round(float((prem[~focus] == x).mean() * 100), 1) for x in top]}
    if fs["weapon"].notna().any():
        rules = [(r["label"], r["keywords"]) for r in p.cfg["weapon_classes"]] if "weapon_classes" in p.cfg else DEFAULT_WEAPONS
        wc = fs["weapon"].apply(lambda w: weapon_class(w, rules))
        order = [r[0] for r in rules] + ["Other or unknown"]
        T["weapons"] = {"labels": order, "focus": [round(float((wc[focus] == k).mean() * 100), 1) for k in order],
                        "other": [round(float((wc[~focus] == k).mean() * 100), 1) for k in order]}
    # flags: rates with and without each flag, and its share by group
    for flag in p.cfg.get("flags", {}):
        fl = {"share": {}, "rates": {"flagged": {}, "unflagged": {}}}
        for g in groups:
            sub = fs[fs["race"] == g]
            fl["share"][g] = round(float(sub[flag].mean() * 100), 1) if len(sub) else None
            fl["rates"]["flagged"][g] = rate(int(sub[flag].sum()), pop["city"][g][S])
            fl["rates"]["unflagged"][g] = rate(int((~sub[flag]).sum()), pop["city"][g][S])
        fl["ratios"] = {k: {g: round(fl["rates"][k][G] / fl["rates"][k][g], 2) for g in others if fl["rates"][k][g]} for k in fl["rates"]}
        T.setdefault("flags", {})[flag] = fl
    T["n"] = {"focus": int(focus.sum()), "other": int((~focus).sum())}

    # model
    if df["lat"].notna().any() and df["age"].notna().any():
        res["model"] = model(p, fs, pop, spans)
    p.write_json("results.json", res)
    print(f"{p.focus_label}: {fr} per 100,000 a year; ratios {res['ratios']}")
    if "model" in res:
        print("ladder:", [(m["model"], m["rate_ratio"]) for m in res["model"]["ladder"]])


def model(p, fs, pop, spans):
    G, S = p.focus
    gj = {f["properties"]["geoid"].split("US")[1]: shape(f["geometry"]) for f in
          __import__("json").loads((p.cache / "tracts_geometry.json").read_text())["features"]}
    geoids, shapes = list(gj), list(gj.values())
    tree = STRtree(shapes)
    w = fs[fs["lat"].notna() & fs["age"].notna()].copy()
    hit = tree.query([Point(x, y) for y, x in zip(w["lat"], w["lon"])], predicate="within")
    tract_of = {}
    for pi, ti in zip(*hit):
        tract_of.setdefault(pi, geoids[ti])
    w["geoid"] = [tract_of.get(i) for i in range(len(w))]
    w = w.dropna(subset=["geoid"])
    w["band"] = w["age"].apply(age_band).map(COARSE_OF)
    rows = []
    for t in pop["tracts"]:
        for g in p.groups:
            for i, n in enumerate(t["pop"][g][S]):
                rows.append({"geoid": t["geoid"], "race": g, "band": COARSE_OF[i], "pop": n})
    tp = pd.DataFrame(rows).groupby(["geoid", "race", "band"], as_index=False)["pop"].sum()
    ses = pd.DataFrame([{"geoid": t["geoid"], "district": t["district"], **t["ses"]} for t in pop["tracts"]])
    extra = []
    for cov in p.cfg.get("covariates", []):
        c = pd.read_csv(p.path(cov["path"]), dtype={"geoid": str}).groupby("geoid", as_index=False).sum(numeric_only=True)
        cols = [x for x in c.columns if x != "geoid"]
        c = c.rename(columns={x: f"cov_{x}" for x in cols})
        ses = ses.merge(c, on="geoid", how="left").fillna({f"cov_{x}": 0 for x in cols})
        for x in cols:
            ses[f"log_cov_{x}"] = np.log1p(ses[f"cov_{x}"])
        extra.append({"label": cov.get("label", cols[0]), "main": f"log_cov_{cols[0]}", "all": [f"log_cov_{x}" for x in cols]})
    years = sorted(int(y) for y in spans if spans[y] >= 0.25)

    def cells(groups, kinds=None, pool=False):
        sub = w[w["race"].isin(groups)].copy()
        pg = tp[tp["race"].isin(groups)].copy()
        if pool:
            sub["race"] = np.where(sub["race"] == G, G, "Other")
            pg["race"] = np.where(pg["race"] == G, G, "Other")
            pg = pg.groupby(["geoid", "race", "band"], as_index=False)["pop"].sum()
        if kinds:
            sub = sub[sub["kind"].isin(kinds)]
        cnt = sub.groupby(["geoid", "race", "band", "year"]).size().rename("n").reset_index()
        grid = pg.merge(pd.DataFrame({"year": years}), how="cross")
        c = grid.merge(cnt, on=["geoid", "race", "band", "year"], how="left").fillna({"n": 0})
        c["exposure"] = c["pop"] * c["year"].map(lambda y: spans[y] if y in spans else spans[str(y)])
        c = c[c["pop"] >= 1].merge(ses, on="geoid", how="left").dropna(subset=["district"] + SES)
        for col in SES:
            c[col] = (c[col] - c[col].mean()) / c[col].std()
        c["focus"] = (c["race"] == G).astype(int)
        c["year"] = c["year"].astype(str)
        return c

    def ratio(c, formula):
        fit = sm.GLM.from_formula(f"n ~ {formula}", data=c, family=sm.families.Poisson(), offset=np.log(c["exposure"])).fit(cov_type="HC0")
        b, se = fit.params["focus"], fit.bse["focus"]
        return {"rate_ratio": round(math.exp(b), 2), "ci_low": round(math.exp(b - 1.96 * se), 2), "ci_high": round(math.exp(b + 1.96 * se), 2)}

    layers = [("group only", "focus"), ("+ age", "focus + C(band)"), ("+ year", "focus + C(band) + C(year)"),
              ("+ district", "focus + C(band) + C(year) + C(district)"),
              ("+ tract socioeconomics", "focus + C(band) + C(year) + C(district) + " + " + ".join(SES))]
    for e in extra:
        layers.append((f"+ {e['label']}", layers[-1][1] + " + " + e["main"]))
    full = layers[-1][1]
    main = cells(p.groups, pool=True)
    located = len(w)
    cov = main.groupby("focus")["n"].sum()
    out = {"cells": len(main), "coverage": {"focus": round(float(cov.get(1, 0) / max(1, (w["race"] == G).sum()) * 100), 1),
                                             "other": round(float(cov.get(0, 0) / max(1, (w["race"] != G).sum()) * 100), 1)},
           "located": located, "ladder": [], "by_kind": {}, "pairwise": {}, "sensitivity": {}}
    for name, f in layers:
        out["ladder"].append({"model": name, **ratio(main, f), "controls": f})
    for k in sorted(w["kind"].unique()):
        out["by_kind"][k] = ratio(cells(p.groups, kinds=[k], pool=True), full)
    for g in p.others:
        pc = cells([G, g])
        out["pairwise"][g] = {"crude": ratio(pc, "focus"), "adjusted": ratio(pc, full)}
    for e in extra:
        for col in e["all"]:
            out["sensitivity"][col.replace("log_cov_", "")] = ratio(main, layers[4][1] + " + " + col)
    l0, l5 = out["ladder"][0]["rate_ratio"], out["ladder"][-1]["rate_ratio"]
    out["explained_pct"] = round((1 - (l5 - 1) / (l0 - 1)) * 100) if l0 > 1 else None
    return out


if __name__ == "__main__":
    main()
