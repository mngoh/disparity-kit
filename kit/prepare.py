"""Split a raw incident file into one file per kind by offense code, once the user has decided what counts.

  python prepare.py data/raw/<file>.csv --code crm_cd --kind simple=624,625,626 --kind aggravated=230,231,236 --out-dir data [--prefix la_]
      [--keep victim_type=Person,Individual] [--hispanic-first --race RACE_COL --ethnicity ETH_COL --hispanic H --race-out race_group]

Writes <out-dir>/<prefix><kind>.csv per kind, every column kept with its raw values, and <out-dir>/prepare.json
recording the rule and the counts, so the split can be repeated. A code listed under two kinds stops the run.

--keep COL=V1,V2   keep only rows whose COL is one of the values (for example individuals, not businesses). Repeatable.
--hispanic-first   for sources that record ethnicity apart from race: adds --race-out, which is "H" when the ethnicity
                   column holds a --hispanic value and the recorded race otherwise. Unknown ethnicity keeps the recorded
                   race (as in DC). That is a method choice; say so in the caveats.
--district-from out/districts.json --lat COL --lon COL
                   adds district_geo: the district whose polygon holds the incident's coordinates, named as the
                   denominators name it (rename, then title case). For sources whose district column changes scheme
                   mid-window (Baltimore redistricted in 2023). Missing coordinates leave it missing.
"""
import argparse
import json
import pathlib
import sys

import numpy as np
import pandas as pd


def norm(s):
    return s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def district_by_location(df, districts, lat, lon):
    from shapely import points
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    from schema import load_geojson
    cfg = districts["config"]
    gj = load_geojson(cfg.get("geojson_url") or cfg.get("geojson_path"))
    feats = [f for f in gj["features"] if f.get("geometry")]
    rename, title = cfg.get("rename", {}), cfg.get("title_case", True)
    names = [str(f["properties"][cfg["name_field"]]) for f in feats]
    names = [rename.get(n, n.title() if title else n) for n in names]  # as denominators.py names them
    y, x = pd.to_numeric(df[lat], errors="coerce"), pd.to_numeric(df[lon], errors="coerce")
    ok = (y.abs() > 1) & (x.abs() > 1)
    out = pd.Series(np.nan, index=df.index, dtype=object)
    idx = np.flatnonzero(ok.to_numpy())
    pi, gi = STRtree([shape(f["geometry"]) for f in feats]).query(points(x.to_numpy()[idx], y.to_numpy()[idx]), predicate="within")
    first = pd.DataFrame({"pt": pi, "poly": gi}).drop_duplicates("pt")
    out.iloc[idx[first["pt"].to_numpy()]] = [names[g] for g in first["poly"]]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv"); ap.add_argument("--code", required=True); ap.add_argument("--kind", action="append", required=True)
    ap.add_argument("--out-dir", required=True); ap.add_argument("--prefix", default="")
    ap.add_argument("--keep", action="append", default=[])
    ap.add_argument("--hispanic-first", action="store_true"); ap.add_argument("--race"); ap.add_argument("--ethnicity")
    ap.add_argument("--hispanic", default="H"); ap.add_argument("--race-out", default="race_group")
    ap.add_argument("--district-from"); ap.add_argument("--lat"); ap.add_argument("--lon")
    a = ap.parse_args()

    kinds = {}
    for k in a.kind:
        name, _, codes = k.partition("=")
        kinds[name] = [c.strip() for c in codes.split(",") if c.strip()]
    seen = {}
    for name, codes in kinds.items():
        for c in codes:
            if c in seen:
                sys.exit(f"code {c} is in both {seen[c]} and {name}")
            seen[c] = name

    df = pd.read_csv(a.csv, dtype=str, low_memory=False, keep_default_na=False)
    total = len(df)
    code = norm(df[a.code])
    rule = {"source": str(a.csv), "rows_in": total, "code_column": a.code, "kinds": kinds, "keep": {}, "counts": {}}
    keep = pd.Series(True, index=df.index)
    for k in a.keep:
        col, _, vals = k.partition("=")
        allowed = [v.strip() for v in vals.split(",")]
        mask = df[col].str.strip().isin(allowed)
        rule["keep"][col] = {"values": allowed, "dropped": int((keep & ~mask & code.isin(seen)).sum())}
        keep &= mask
    if a.hispanic_first:
        if not (a.race and a.ethnicity):
            sys.exit("--hispanic-first needs --race and --ethnicity")
        hisp = [v.strip() for v in a.hispanic.split(",")]
        df[a.race_out] = np.where(df[a.ethnicity].str.strip().isin(hisp), "H", df[a.race])
        rule["race_rule"] = {"column": a.race_out, "hispanic_values": hisp, "from": [a.race, a.ethnicity],
                             "note": "Hispanic of any race first, otherwise the recorded race; unknown ethnicity keeps the recorded race"}
    if a.district_from:
        if not (a.lat and a.lon):
            sys.exit("--district-from needs --lat and --lon")
        df["district_geo"] = district_by_location(df, json.loads(pathlib.Path(a.district_from).read_text()), a.lat, a.lon)
        rule["district_rule"] = {"column": "district_geo", "from": [a.lat, a.lon], "boundaries": json.loads(pathlib.Path(a.district_from).read_text())["config"],
                                 "located_pct": round(float(df.loc[keep & code.isin(seen), "district_geo"].notna().mean()) * 100, 1)}
        print(f"district_geo: {rule['district_rule']['located_pct']}% of kept rows placed in a district")
    out_dir = pathlib.Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, codes in kinds.items():
        part = df[keep & code.isin(codes)]
        dest = out_dir / f"{a.prefix}{name}.csv"
        part.to_csv(dest, index=False)
        by_code = norm(part[a.code]).value_counts().to_dict()
        rule["counts"][name] = {"rows": len(part), "by_code": by_code, "path": str(dest)}
        missing = [c for c in codes if c not in by_code]
        print(f"wrote {dest}: {len(part):,} rows" + (f" (no rows for codes {', '.join(missing)})" if missing else ""))
    (out_dir / "prepare.json").write_text(json.dumps(rule, indent=1))
    print("wrote", out_dir / "prepare.json")


if __name__ == "__main__":
    main()
