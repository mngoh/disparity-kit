"""Audit the call table before any comparison: who started each event, what is missing or implausible, and where.

  python audit.py response.json      ->  out/audit.md, out/audit.json,
                                         out/audit_by_neighborhood.csv, out/audit_by_district.csv

Per priority category, overall and for each neighborhood and district:
  duplicate entries (rows sharing an event id), missing arrival, missing dispatch,
  negative intervals (arrival or dispatch logged before entry, arrival before dispatch),
  implausible waits (entry to arrival over 24 hours), and calls with no location or no neighborhood.
Also: monthly coverage, who started the events (the config's origin rules), calls by hour of day (a time zone
check), and citywide medians by year for comparison with the city's official figures.

The audit does not use the neighborhoods' income or racial makeup. Those comparisons wait for the plan.
"""
import sys

import numpy as np
import pandas as pd

from rt import RTProject, md_table, write_md

FLAGS = {
    "missing_arrival": "t_arrive IS NULL",
    "missing_dispatch": "t_dispatch IS NULL",
    "arrival_before_entry": "t_arrive < t_create",
    "dispatch_before_entry": "t_dispatch < t_create",
    "arrival_before_dispatch": "t_arrive < t_dispatch",
    "over_24h": "wait > 1440",
    "no_location": "lat IS NULL OR lon IS NULL",
    "no_neighborhood": "(lat IS NOT NULL AND lon IS NOT NULL) AND nbhd IS NULL",
    "nonresidential": "nbhd IS NOT NULL AND NOT coalesce(residential, false)",
    "duplicate_entries": "n_entries > 1",
}
LABEL = {
    "missing_arrival": "No arrival time", "missing_dispatch": "No dispatch time",
    "arrival_before_entry": "Arrival before entry", "dispatch_before_entry": "Dispatch before entry",
    "arrival_before_dispatch": "Arrival before dispatch", "over_24h": "Wait over 24 hours",
    "no_location": "No location", "no_neighborhood": "Location outside every neighborhood",
    "nonresidential": "In a park, airport or other non-residential area", "duplicate_entries": "Event entered more than once",
    "not_public": "Not from the public (all events)", "on_scene": "Unit on scene at entry (all events)",
}


def pct(x):
    return f"{100 * x:.1f}%" if x is not None and x == x else ""


def pct2(x):
    return f"{100 * x:.2f}%" if x is not None and x == x else ""


def main():
    p = RTProject(sys.argv[1])
    con = p.con()
    nb = pd.read_csv(p.data / "neighborhoods.csv", dtype={"id": str})
    con.register("nb_df", nb[["id", "name", "area", "residential"]])
    con.execute(f"""CREATE VIEW c AS
        SELECT c.*, pt.nbhd, pt.in_district, pt.station_km, nb.name AS nbhd_name, nb.residential
        FROM read_parquet('{p.calls}') c
        LEFT JOIN read_parquet('{p.data / 'points.parquet'}') pt USING (lat, lon, district)
        LEFT JOIN nb_df nb ON nb.id = pt.nbhd""")
    prios = [x["value"] for x in p.priorities]
    plab = {x["value"]: x["label"] for x in p.priorities}
    flag_sql = ", ".join(f"avg(({w})::INT) AS {k}" for k, w in FLAGS.items())
    out, md = {}, [f"# Data audit: {p.cfg['title']}", ""]
    prep = p.read_json("prepare.json")

    # rows and events
    md += ["## Rows and events", "",
           f"{prep['raw_rows']:,} rows; {prep['events']:,} events after rows that share an event id are combined "
           f"({prep['duplicate_rows']:,} extra rows, {pct2(prep['duplicate_rows'] / prep['raw_rows'])}). "
           "An event entered more than once keeps its earliest entry, earliest dispatch and earliest arrival.", ""]
    src = con.execute("SELECT source, min(t_create)::DATE, max(t_create)::DATE, count(*) FROM c GROUP BY 1 ORDER BY 2").fetchall()
    md += [md_table([{"Source": s, "First": str(a), "Last": str(b), "Events": n} for s, a, b, n in src],
                    ["Source", "First", "Last", "Events"], {"Events": ","}), ""]

    # monthly coverage
    mon = con.execute("""SELECT strftime(t_create, '%Y-%m') m, count(*) n, avg((t_arrive IS NOT NULL)::INT) arr,
                         avg((origin='public')::INT) pub FROM c GROUP BY 1 ORDER BY 1""").df()
    med = mon["n"].median()
    mon["flag"] = np.where(mon["n"] < 0.6 * med, "under 60% of median", "")
    out["monthly"] = mon.to_dict("records")
    md += ["## Monthly coverage", "",
           f"Events per month: median {med:,.0f}, range {mon['n'].min():,} to {mon['n'].max():,}. "
           f"Months under 60% of the median: {', '.join(mon.loc[mon['flag'] != '', 'm']) or 'none'}. "
           f"Share with an arrival time by month: {pct(mon['arr'].min())} to {pct(mon['arr'].max())}.", ""]

    # time zone check
    hrs = con.execute("SELECT hour, count(*) FROM c WHERE origin='public' GROUP BY 1 ORDER BY 1").fetchall()
    low = min(hrs, key=lambda x: x[1])[0]
    out["hour_low"] = low
    md += [f"Public calls are fewest at {low}:00 in the stored times, the overnight low in local time, so the times are local.", ""]

    # origin
    org = con.execute("""SELECT priority, origin, origin_rule, count(*) n FROM c GROUP BY 1, 2, 3""").df()
    tot = org.groupby("priority")["n"].sum()
    rows = []
    for (o, r), x in org.groupby(["origin", "origin_rule"]):
        row = {"Origin": o, "Rule": r}
        for pr in prios:
            v = x.loc[x["priority"] == pr, "n"].sum()
            row[plab[pr]] = f"{v:,} ({pct(v / tot.get(pr, np.nan))})"
        rows.append(row)
    out["origin"] = org.to_dict("records")
    out["origin_totals"] = {k: int(v) for k, v in org.groupby("origin")["n"].sum().items()}
    out["origin_totals"]["not_public"] = sum(v for k, v in out["origin_totals"].items() if k != "public")
    md += ["## Who started each event", "",
           "The data has no field for whether an event came from a 911 call or from an officer. "
           "These rules from the config separate them; everything else counts as a call from the public.", "",
           md_table(rows, ["Origin", "Rule"] + [plab[p_] for p_ in prios]), ""]

    # flags by priority, all events and public calls
    for scope, where in [("public", "origin = 'public'"), ("all", "TRUE")]:
        f = con.execute(f"SELECT priority, count(*) n, {flag_sql} FROM c WHERE {where} GROUP BY 1").df().set_index("priority")
        out[f"flags_{scope}"] = f.reset_index().to_dict("records")
        rows = [{"Check": LABEL[k], **{plab[pr]: pct2(f.loc[pr, k]) if pr in f.index else "" for pr in prios}} for k in FLAGS]
        rows.insert(0, {"Check": "Events", **{plab[pr]: f"{int(f.loc[pr, 'n']):,}" if pr in f.index else "" for pr in prios}})
        title = "Calls from the public" if scope == "public" else "All events, including officer-initiated"
        md += [f"## Checks by priority: {title.lower()}", "", md_table(rows, ["Check"] + [plab[p_] for p_ in prios]), ""]

    # by neighborhood (residential, public calls)
    g = con.execute(f"""SELECT nbhd, nbhd_name, priority, count(*) n, {flag_sql}
        FROM c WHERE origin='public' AND residential GROUP BY 1, 2, 3""").df()
    o = con.execute("""SELECT nbhd, priority, count(*) n_all, avg((origin<>'public')::INT) not_public,
        avg((origin='on_scene')::INT) on_scene FROM c WHERE residential GROUP BY 1, 2""").df()
    g = g.merge(o, on=["nbhd", "priority"], how="left")
    g = g.sort_values(["nbhd", "priority"])
    g.to_csv(p.out / "audit_by_neighborhood.csv", index=False)
    d = con.execute(f"""SELECT district, priority, count(*) n, avg(in_district::INT) in_own_polygon, {flag_sql}
        FROM c WHERE origin='public' GROUP BY 1, 2""").df()
    d = d.sort_values(["district", "priority"])
    d.to_csv(p.out / "audit_by_district.csv", index=False)
    min_n = 200
    md += ["## How the checks vary across neighborhoods", "",
           f"Calls from the public in residential neighborhoods, neighborhoods with at least {min_n} calls of that priority. "
           "The 10th and 90th percentiles are across neighborhoods. Full table: `out/audit_by_neighborhood.csv`.", ""]
    spread = []
    for pr in prios:
        x = g[(g["priority"] == pr) & (g["n"] >= min_n)]
        for k in ["missing_arrival", "over_24h", "arrival_before_entry", "duplicate_entries", "not_public", "on_scene"]:
            v = x[k]
            spread.append({"Priority": plab[pr], "Check": LABEL[k], "Neighborhoods": len(x), "Lowest": pct(v.min()), "10th pct": pct(v.quantile(.1)), "Median": pct(v.median()),
                           "90th pct": pct(v.quantile(.9)), "Highest": pct(v.max())})
    out["neighborhood_spread"] = spread
    md += [md_table(spread, ["Priority", "Check", "Neighborhoods", "Lowest", "10th pct", "Median", "90th pct", "Highest"]), ""]
    for pr in prios:
        x = g[(g["priority"] == pr) & (g["n"] >= min_n)].sort_values("missing_arrival")
        if x.empty:
            continue
        both = pd.concat([x.tail(10)[::-1], x.head(10)])
        md += [f"### {plab[pr]}: neighborhoods with the highest and lowest share of calls with no arrival time", "",
               md_table([{"Neighborhood": r.nbhd_name, "Calls": int(r.n), "No arrival": pct(r.missing_arrival)} for r in both.itertuples()],
                        ["Neighborhood", "Calls", "No arrival"], {"Calls": ","}), ""]

    # single locations that produce many calls with no arrival (named by neighborhood only)
    hot = con.execute("""SELECT priority, nbhd_name, count(*) n, sum((t_arrive IS NULL)::INT) no_arr,
            avg((t_arrive IS NULL AND epoch(t_close - t_create) <= 120)::INT) closed_2min
        FROM c WHERE origin='public' AND priority IN (SELECT priority FROM c GROUP BY 1 ORDER BY count(*) LIMIT 3)
        GROUP BY priority, lat, lon, nbhd_name HAVING sum((t_arrive IS NULL)::INT) >= 50 ORDER BY no_arr DESC LIMIT 15""").df()
    out["repeat_locations"] = hot.to_dict("records")
    if len(hot):
        md += ["## Single locations with many calls and no arrival", "",
               "Calls from the public at one location (one street segment) with 50 or more calls that have no arrival time, "
               "in the three smallest priority categories. Named by neighborhood only.", "",
               md_table([{"Priority": plab.get(r.priority, r.priority), "Neighborhood": r.nbhd_name, "Calls": int(r.n), "No arrival": int(r.no_arr),
                          "Closed within 2 minutes, no arrival": pct(r.closed_2min)} for r in hot.itertuples()],
                        ["Priority", "Neighborhood", "Calls", "No arrival", "Closed within 2 minutes, no arrival"], {"Calls": ","}), ""]

    # districts
    pol = con.execute("SELECT avg(in_district::INT) FROM c WHERE lat IS NOT NULL").fetchone()[0]
    md += ["## Districts", "",
           f"{pct(pol)} of located events fall inside the polygon of the district the event is coded to. "
           "Full table: `out/audit_by_district.csv`.", ""]
    first = con.execute("""SELECT district, min(t_create)::DATE, count(*) FROM c GROUP BY 1
                           HAVING min(t_create) > (SELECT min(t_create) + INTERVAL 31 DAY FROM c) ORDER BY 2""").fetchall()
    if first:
        md += ["Districts that first appear after the start of the data: " +
               "; ".join(f"{dd} from {a} ({n:,} events)" for dd, a, n in first) + ".", ""]
    out["districts_starting_late"] = [{"district": dd, "first": str(a), "events": n} for dd, a, n in first]

    # citywide medians by year
    cw = con.execute("""SELECT priority, year, count(*) n, avg((t_arrive IS NOT NULL)::INT) arrived,
        quantile_cont(wait, 0.5) FILTER (WHERE wait >= 0 AND wait <= 1440) med_wait,
        avg(wait) FILTER (WHERE wait >= 0 AND wait <= 1440) mean_wait,
        quantile_cont(wait, 0.9) FILTER (WHERE wait >= 0 AND wait <= 1440) p90_wait,
        quantile_cont(to_dispatch, 0.5) FILTER (WHERE to_dispatch >= 0 AND to_dispatch <= 1440) med_dispatch,
        avg(to_dispatch) FILTER (WHERE to_dispatch >= 0 AND to_dispatch <= 1440 AND wait >= 0 AND wait <= 1440) mean_dispatch,
        quantile_cont(travel, 0.5) FILTER (WHERE travel >= 0 AND wait <= 1440) med_travel,
        avg(travel) FILTER (WHERE travel >= 0 AND wait >= 0 AND wait <= 1440) mean_travel
        FROM c WHERE origin='public' GROUP BY 1, 2 ORDER BY 1, 2""").df()
    cw.to_csv(p.out / "citywide_by_year.csv", index=False)
    out["citywide_by_year"] = cw.to_dict("records")
    md += ["## Citywide times by year (calls from the public)", "",
           "Minutes. Medians and means over calls with an arrival between 0 and 24 hours after entry.", "",
           md_table([{**r, "priority": plab.get(r["priority"], r["priority"])} for r in cw.to_dict("records")],
                    ["priority", "year", "n", "arrived", "med_wait", "mean_wait", "p90_wait", "med_dispatch", "mean_dispatch", "med_travel", "mean_travel"],
                    {"n": ",", "arrived": pct, **{k: ".1f" for k in ["med_wait", "mean_wait", "p90_wait", "med_dispatch", "mean_dispatch", "med_travel", "mean_travel"]}}), ""]

    p.write_json("audit.json", out)
    write_md(p.out / "audit.md", "\n".join(md))


if __name__ == "__main__":
    main()
