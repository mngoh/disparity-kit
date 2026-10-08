"""Every table from out/results.json as Markdown, for review before the page is written.

  python report.py response.json      ->  out/results.md
"""
import json
import sys

from rt import RTProject, md_table, write_md


def labels(p):
    a = p.cfg["analysis"]
    lab = {}
    for gkey, g in a["groupings"].items():
        for k, v in g.get("level_labels", {}).items():
            lab[(gkey, k)] = v
    plab = {x["value"]: x["label"] for x in p.priorities}
    return (lambda gkey, lev: lab.get((gkey, str(lev)), str(lev))), plab


def mins(v):
    return f"{v:.1f}"


def signed(v):
    return f"{v:+.1f}"


def pct(v):
    return f"{100 * v:+.0f}%"


def main():
    p = RTProject(sys.argv[1])
    out = p.out / "shuffled" if len(sys.argv) > 2 and sys.argv[2] == "--shuffled" else p.out
    R = json.loads((out / "results.json").read_text())
    L, plab = labels(p)
    md = [f"# Results: {p.cfg['title']}", ""]
    if R.get("shuffled") is not None:
        md += ["**Neighborhood groups shuffled at random: a code test, not results.**", ""]

    md += ["## Primary comparisons (Holm family)", "",
           "Adjusted median minutes from entry to first arrival, calls from the public, 2022 to 2025. "
           "Gap = group minus reference; 95% intervals from a cluster bootstrap over neighborhoods.", ""]
    rows = [{"Priority": plab[r["priority"]], "Group": L(r["grouping"], r["group"]), "Reference": L(r["grouping"], r["reference"]),
             "Group median": mins(r["estimate"]), "Reference median": mins(r["reference_estimate"]),
             "Gap (min)": f"{signed(r['diff'])} ({signed(r['diff_lo'])} to {signed(r['diff_hi'])})",
             "Gap (%)": f"{pct(r['rel'])} ({pct(r['rel_lo'])} to {pct(r['rel_hi'])})",
             "Holm p": f"{r['p_holm']:.3g}", "Threshold": r["threshold"], "Verdict": r["verdict"],
             "Nbhds": f"{r['neighborhoods']} vs {r['ref_neighborhoods']}"} for r in R["primary"]]
    md += [md_table(rows, list(rows[0])) if rows else "(none)", ""]

    md += ["## Medians and 90th percentiles by group", "", "Minutes. Raw is unweighted; adjusted is reweighted to the citywide call mix.", ""]
    rows = [{"Priority": plab[r["priority"]], "Group": L(r["grouping"], r["level"]), "Nbhds": r["neighborhoods"], "Calls": f"{r['calls']:,}",
             "Median raw": mins(r["raw_50"]), "Median adj": mins(r["adj_50"]), "P90 raw": mins(r["raw_90"]), "P90 adj": mins(r["adj_90"]),
             "To dispatch adj": mins(r["to_dispatch_adj_50"]), "Dispatch to arrival adj": mins(r["travel_adj_50"])} for r in R["levels"]]
    md += [md_table(rows, list(rows[0])), ""]

    md += ["## Other gaps (not in the Holm family)", ""]
    rows = [{"Priority": plab[r["priority"]], "Group": L(r["grouping"], r["group"]), "Kind": r["kind"], "Quantile": int(r["quantile"] * 100),
             "Gap (min)": f"{signed(r['diff'])} ({signed(r['diff_lo'])} to {signed(r['diff_hi'])})", "Gap (%)": f"{pct(r['rel'])}", "p": f"{r['p']:.3g}"}
            for r in R["secondary"]]
    md += [md_table(rows, list(rows[0])), ""]

    md += ["## The two parts of the wait", "", "Adjusted medians, calls with an arrival.", ""]
    rows = [{"Priority": plab[r["priority"]], "Group": L(r["grouping"], r["group"]), "Part": r["measure"],
             "Gap (min)": f"{signed(r['diff'])} ({signed(r['diff_lo'])} to {signed(r['diff_hi'])})", "Gap (%)": pct(r["rel"])}
            for r in R["parts"] if r["kind"] == "adj" and r["quantile"] == 0.5]
    md += [md_table(rows, list(rows[0])), ""]

    md += ["## Calls with no arrival time", ""]
    rows = [{"Priority": plab[r["priority"]], "Group": L(r["grouping"], r["level"]), "Calls": f"{r['calls']:,}",
             "No arrival": f"{100 * r['share']:.1f}% ({100 * r['lo']:.1f} to {100 * r['hi']:.1f})"} for r in R["no_arrival"]]
    md += [md_table(rows, list(rows[0])), ""]

    md += ["## What narrows a gap", "", "Percent difference in typical minutes against the reference (OLS of log minutes, errors clustered by neighborhood).", ""]
    for gkey, g in p.cfg["analysis"]["groupings"].items():
        levs = [str(l) for l in g["levels"] if l != g["reference"]]
        rows = []
        for r in R["ladder"]:
            if r["grouping"] != gkey:
                continue
            rows.append({"Priority": plab[r["priority"]], "Controls": r["step"],
                         **{L(gkey, l): f"{pct(r[l])} ({pct(r[l + '_lo'])} to {pct(r[l + '_hi'])})" for l in levs}})
        md += [f"### {g['label']}", "", md_table(rows, list(rows[0])), ""]

    md += ["## By period (replication)", ""]
    rows = [{"Priority": plab[r["priority"]], "Group": L(r["grouping"], r["group"]), "Period": r["period"], "Quantile": int(r["quantile"] * 100),
             "Gap (min)": f"{signed(r['diff'])} ({signed(r['diff_lo'])} to {signed(r['diff_hi'])})", "Gap (%)": pct(r["rel"]),
             "Verdict": r.get("verdict", "")} for r in R["periods"] if r["quantile"] == 0.5]
    md += [md_table(rows, list(rows[0])), ""]

    md += ["## Sensitivity", "", "Adjusted median gaps under other choices.", ""]
    rows = [{"Choice": r["sensitivity"], "Priority": plab[r["priority"]], "Group": L(r["grouping"], r["group"]),
             "Gap (min)": f"{signed(r['diff'])} ({signed(r['diff_lo'])} to {signed(r['diff_hi'])})", "Gap (%)": pct(r["rel"])} for r in R["sensitivity"]]
    md += [md_table(rows, list(rows[0])), ""]

    md += ["## Continuous measures", "", "Percent difference in typical minutes per 10 points of a group's share of residents, "
           "or per $25,000 lower median household income, with the plan's controls.", ""]
    rows = [{"Priority": plab[r["priority"]], "Term": r["term"], "Difference": f"{pct(r['pct'])} ({pct(r['lo'])} to {pct(r['hi'])})"} for r in R["continuous"]]
    md += [md_table(rows, list(rows[0])), ""]
    write_md(out / "results.md", "\n".join(md))


if __name__ == "__main__":
    main()
