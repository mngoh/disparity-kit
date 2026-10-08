"""Build the standard call table from the raw monthly files: one row per CAD event, typed, with who started it.

  python prepare.py response.json      ->  data/calls.parquet, out/prepare.json

Config keys used:
  fields     standard name -> DuckDB SQL expression over the raw columns (event_id, t_create, t_dispatch,
             t_arrive, t_close, priority, call_type, call_desc, district, lat, lon; extras allowed)
  dedupe     "first_entry" (default): rows sharing an event_id are one event. Its entry time is the earliest
             entry, its dispatch and arrival the earliest of each, its close the latest; other fields come from
             the earliest entry. The number of rows is kept as n_entries.
  origin     ordered rules, first match wins: {"origin": "officer"|"sensor"|"public", "rule": label, "where": SQL}.
             SQL may use the standard names. Events matching no rule are "public". The data rarely says who
             started an event, so these rules are the analyst's, and the audit reports what each one removes.
  sources    each source's window; rows outside it are dropped. Each row keeps its source key.
  periods    {"label": [start, end]} on the entry date, written to the column period.

Intervals, in minutes: wait (entry to first arrival), to_dispatch (entry to dispatch), travel (dispatch to arrival).
"""
import sys

from rt import RTProject, dump

STANDARD = ["event_id", "t_create", "t_dispatch", "t_arrive", "t_close", "priority", "call_type", "call_desc",
            "district", "lat", "lon"]
TIMES = ["t_create", "t_dispatch", "t_arrive", "t_close"]


def main():
    p = RTProject(sys.argv[1])
    cfg = p.cfg
    f = cfg["fields"]
    missing = [k for k in STANDARD if k not in f]
    if missing:
        sys.exit(f"fields missing from config: {missing}")
    extras = [k for k in f if k not in STANDARD]
    con = p.con()

    # 1. standardize every source's raw rows
    parts = []
    for key, src in cfg["sources"].items():
        glob = p.raw / key / "*.parquet"
        sel = []
        for k in STANDARD + extras:
            expr = f[k]
            if k in TIMES:
                expr = f"try_cast({expr} as TIMESTAMP)"
            elif k in ("lat", "lon"):
                expr = f"try_cast({expr} as DOUBLE)"
            else:
                expr = f"nullif(trim(cast({expr} as VARCHAR)), '')"
            sel.append(f"{expr} AS {k}")
        parts.append(f"SELECT '{key}' AS source, {', '.join(sel)} FROM read_parquet('{glob}') "
                     f"WHERE cast({src['date_field']} as DATE) BETWEEN DATE '{src['start']}' AND DATE '{src['end']}'")
    con.execute("CREATE TEMP TABLE raw AS " + " UNION ALL ".join(parts))
    n_rows = con.execute("SELECT count(*) FROM raw").fetchone()[0]

    # 2. one row per event
    others = ["priority", "call_type", "call_desc", "district", "lat", "lon"] + extras
    con.execute(f"""
        CREATE TEMP TABLE ev AS
        SELECT event_id, source,
               min(t_create) AS t_create, min(t_dispatch) AS t_dispatch, min(t_arrive) AS t_arrive, max(t_close) AS t_close,
               {', '.join(f'arg_min({c}, t_create) AS {c}' for c in others)},
               count(*) AS n_entries
        FROM raw GROUP BY event_id, source""")
    n_events = con.execute("SELECT count(*) FROM ev").fetchone()[0]

    # 3. who started it
    rules = cfg.get("origin", [])
    case_o = " ".join(f"WHEN {r['where']} THEN '{r['origin']}'" for r in rules)
    case_r = " ".join(f"WHEN {r['where']} THEN '{r['rule']}'" for r in rules)
    per = cfg.get("periods", {})
    case_p = " ".join(f"WHEN cast(t_create AS DATE) BETWEEN DATE '{a}' AND DATE '{b}' THEN '{lab}'" for lab, (a, b) in per.items())
    con.execute(f"""
        COPY (
          SELECT *,
                 CASE {case_o} ELSE 'public' END AS origin,
                 CASE {case_r} ELSE 'none' END AS origin_rule,
                 {f'CASE {case_p} ELSE NULL END' if case_p else 'NULL'} AS period,
                 year(t_create) AS year, hour(t_create) AS hour, isodow(t_create) AS dow,
                 date_trunc('hour', t_create) AS clock_hour,
                 epoch(t_arrive - t_create) / 60.0 AS wait,
                 epoch(t_dispatch - t_create) / 60.0 AS to_dispatch,
                 epoch(t_arrive - t_dispatch) / 60.0 AS travel
          FROM ev
          ORDER BY t_create
        ) TO '{p.calls}' (FORMAT parquet, COMPRESSION zstd)""")
    summary = {
        "raw_rows": n_rows, "events": n_events, "duplicate_rows": n_rows - n_events,
        "by_source": dict(con.execute("SELECT source, count(*) FROM ev GROUP BY 1 ORDER BY 1").fetchall()),
        "by_origin_rule": {f"{o} | {r}": n for o, r, n in con.execute(
            f"SELECT CASE {case_o} ELSE 'public' END, CASE {case_r} ELSE 'none' END, count(*) FROM ev GROUP BY 1, 2 ORDER BY 3 DESC").fetchall()},
    }
    p.write_json("prepare.json", summary)
    print(f"{n_rows:,} rows -> {n_events:,} events in {p.calls}")


if __name__ == "__main__":
    main()
