"""Audit a raw incident file before trusting it. Works on any CSV, no config needed.

Reports: size, columns, missing and coded-missing values, duplicate ids, every value of the
offense-code columns (so related codes are not missed), and monthly coverage with breaks flagged
(records-system changes, partial years, reporting lag).

  python audit.py incidents.csv [--date COL] [--code COL] [--desc COL] [--id COL] [--out audit.md]
"""
import argparse
import re

import pandas as pd

from common import read_any_csv

CODED_MISSING = {"0", "0.0", "X", "U", "UNKNOWN", "UNK", "NONE", "N/A", "NA", "-", "?", ""}


def guess(df, patterns):
    for p in patterns:
        for c in df.columns:
            if re.search(p, c, re.I):
                return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--date"); ap.add_argument("--code"); ap.add_argument("--desc"); ap.add_argument("--id")
    ap.add_argument("--out")
    a = ap.parse_args()
    df = read_any_csv(a.csv)
    date = a.date or guess(df, [r"date.*occ", r"occ.*date", r"incident.*date", r"^date", r"date"])
    code = a.code or guess(df, [r"crm.?cd$", r"offense.?code", r"crime.?code", r"nibr.?code", r"code$"])
    desc = a.desc or guess(df, [r"crm.?cd.?desc", r"offense.?desc", r"crime.?desc", r"description", r"desc"])
    idc = a.id or guess(df, [r"^dr.?no", r"case.?n", r"incident.?id", r"report.?n", r"^id$"])
    L = [f"# Audit: {a.csv}", "", f"{len(df):,} rows, {df.shape[1]} columns. Guessed: date `{date}`, code `{code}`, description `{desc}`, id `{idc}`. Override with flags if wrong.", ""]

    L += ["## Missing and coded-missing values", "", "| column | blank | coded missing (0, X, UNKNOWN...) | distinct |", "|---|---|---|---|"]
    for c in df.columns:
        s = df[c]
        blank = s.isna().mean() * 100
        coded = s.dropna().astype(str).str.strip().str.upper().isin(CODED_MISSING).mean() * 100 * (1 - blank / 100)
        L.append(f"| {c} | {blank:.1f}% | {coded:.1f}% | {s.nunique():,} |")
    L.append("")

    if idc:
        dup = df[idc].duplicated().sum()
        L += ["## Duplicates", "", f"{dup:,} rows share an id in `{idc}`." + (" One row per victim or offense? Decide before counting." if dup else ""), ""]

    if code or desc:
        key = [c for c in [code, desc] if c]
        vc = df.groupby(key, dropna=False).size().sort_values(ascending=False)
        L += ["## Every offense value", "", "Read the whole list. Related offenses are often coded separately (intimate partner, on police, on children, attempts).", "", "| " + " | ".join(key) + " | rows |", "|" + "---|" * (len(key) + 1)]
        for k, n in vc.items():
            k = k if isinstance(k, tuple) else (k,)
            L.append("| " + " | ".join(str(x) for x in k) + f" | {n:,} |")
        L.append("")

    if date:
        d = pd.to_datetime(df[date], errors="coerce")
        L += ["## Coverage by month", "", f"{d.isna().sum():,} unparseable dates. Range {d.min():%Y-%m-%d} to {d.max():%Y-%m-%d}.", ""]
        m = d.dt.to_period("M").value_counts().sort_index()
        med = m.median()
        flagged = [(p, n) for p, n in m.items() if n < 0.6 * med]
        L += [f"Median {med:,.0f} rows a month. Months under 60% of that (system changes, partial periods, reporting lag):", ""]
        L += [f"- {p}: {n:,}" for p, n in flagged] or ["- none"]
        L += ["", "Full series:", "", " ".join(f"{p}:{n}" for p, n in m.items()), ""]
        L += ["Use only full calendar years where you can; weight any partial year by its share of the year."]

    text = "\n".join(L)
    if a.out:
        open(a.out, "w").write(text)
        print("wrote", a.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
