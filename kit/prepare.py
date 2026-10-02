"""Split a raw incident file into one file per kind by offense code, once the user has decided what counts.

  python prepare.py data/raw/<file>.csv --code crm_cd --kind simple=624,625,626 --kind aggravated=230,231,236 --out-dir data [--prefix la_]
      [--keep victim_type=Person,Individual] [--hispanic-first --race RACE_COL --ethnicity ETH_COL --hispanic H --race-out race_group]

Writes <out-dir>/<prefix><kind>.csv per kind, every column kept with its raw values, and <out-dir>/prepare.json
recording the rule and the counts, so the split can be repeated. A code listed under two kinds stops the run.

--keep COL=V1,V2   keep only rows whose COL is one of the values (for example individuals, not businesses). Repeatable.
--hispanic-first   for sources that record ethnicity apart from race: adds --race-out, which is "H" when the ethnicity
                   column holds a --hispanic value and the recorded race otherwise. Unknown ethnicity keeps the recorded
                   race (as in DC). That is a method choice; say so in the caveats.
"""
import argparse
import json
import pathlib
import sys

import numpy as np
import pandas as pd


def norm(s):
    return s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv"); ap.add_argument("--code", required=True); ap.add_argument("--kind", action="append", required=True)
    ap.add_argument("--out-dir", required=True); ap.add_argument("--prefix", default="")
    ap.add_argument("--keep", action="append", default=[])
    ap.add_argument("--hispanic-first", action="store_true"); ap.add_argument("--race"); ap.add_argument("--ethnicity")
    ap.add_argument("--hispanic", default="H"); ap.add_argument("--race-out", default="race_group")
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
