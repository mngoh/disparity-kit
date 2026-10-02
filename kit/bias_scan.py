"""Screen a race-disparity analysis for racial bias in the data and in the writeup.

This finds candidates; it does not decide. Every flag needs a human (or Claude) to read it in context.

DATA checks (from the incidents, out/population.json and out/results.json)
  race coding         officer-recorded race against a race-alone denominator; the alone-or-in-combination gap
  overlap             residents counted in two group denominators (Hispanic of any race, and a race alone)
  unmapped race       raw race codes that fell out of every group, and how large they are
  unknown race        whether missing race is concentrated where the focus group lives (biases its rate)
  instability         small groups whose ratios swing between sources or periods
  enforcement         police data reflects where police are; reminds the writeup to say so

WRITEUP checks (every text file passed, default index.html and README.md)
  causal claims, offender implications, deficit or stereotype language, essentializing phrasing,
  inconsistent race capitalization, people-share claims from report rates, absolutes, "explained away"
  language, and whether the required caveats are present

  python bias_scan.py analysis.json [extra.md ...]     ->  out/bias_review.md
"""
import html
import re
import sys

import numpy as np

from common import Project, load_incidents

WRITEUP_RULES = [
    ("Causal claim", r"\b(because|caused by|causes|due to|leads? to|results? in|drives|driven by|the reason)\b",
     "The data shows rates, not causes. Keep causal words only in sentences that say a cause is not measured."),
    ("Offender implication", r"\b(perpetrat\w*|offenders?|suspects?|committed by|attackers?|assailants?)\b|(black|white|hispanic|latino|asian)[- ]on[- ](black|white|hispanic|latino|asian)",
     "Victim data says nothing about who offended. Remove, or state that offenders are not in the data."),
    ("Deficit or stereotype language", r"\b(cultur\w*|lifestyle|fatherless|thugs?|ghetto|inner[- ]city|dysfunction\w*|broken (homes?|families)|at[- ]risk population|dangerous (neighborhood|community|area)s?|crime[- ]ridden|bad neighborhood|strong black wom[ae]n)\b",
     "Attributes a disparity to group character or culture. Describe conditions and data, not people."),
    ("Essentializing phrasing", r"\b(blacks|whites|hispanics|asians|latinos)\b(?! women| men| residents| victims)|\b(the|a) (black|white|hispanic|asian)\b(?! (women|men|residents|victims|population|people|share|rate|comparison|descent|codes?|group|alone|category|categories|table))|\bminorities\b|\b(black|white|hispanic|asian|latino) people are\b",
     "Use groups as adjectives (Black women, White residents), never as nouns or as statements about what a group is."),
    ("Share-of-people claim", r"\b1 in \d[\d,]*\b|\bone in \w+\b|\b\d+(\.\d+)?% of (black|white|hispanic|asian) (women|men|residents)\b(?! victims)",
     "Report rates count reports, not people. Do not convert them into shares of a population."),
    ("Absolute or proof language", r"\b(prove[sd]?|proof|always|never|undeniabl\w*|definitively|clearly shows)\b",
     "Overstates certainty. Use 'the data shows' and keep the caveats attached."),
    ("Explained-away language", r"\b(explain(s|ed)? (away|the gap)|accounts? for (most|all|the gap)|not about race|race (doesn't|does not) matter|colorblind)\b",
     "Controls like neighborhood and income are themselves shaped by segregation and discrimination. 'Explained by location' does not mean 'not related to race'."),
    ("Sensational framing", r"\b(epidemic|war zone|plague|crisis of|explosion|shocking|staggering|horrif\w*)\b",
     "Let the numbers carry weight. Sensational words invite readings the data cannot support."),
]
REQUIRED = [
    ("says it does not explain why", r"(not (say|explain|measure)|cannot say|does not say) (why|causes)|not why"),
    ("names reporting differences", r"report(ing|ed)? (behavior|differ|more or less|rates?)|willingness to report|only what was reported|reported crimes only"),
    ("names the race-coding limit", r"recorded by officers|officer-recorded|alone or in combination"),
    ("separates reports from people", r"reports, not people|counts twice|not the share of"),
]
RACE_WORDS = ["Black", "White", "Hispanic", "Latino", "Asian", "Indigenous"]


def text_of(path):
    s = path.read_text(errors="ignore")
    if path.suffix in (".html", ".htm"):
        s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
        s = html.unescape(re.sub(r"<[^>]+>", "\n", s))
    return [l.strip() for l in s.splitlines() if l.strip()]


def main():
    p = Project(sys.argv[1])
    G, S = p.focus
    pop = p.read_json("population.json")
    R = p.read_json("results.json")
    out = ["# Racial bias review", "", f"Focus: {p.focus_label}. This screen finds candidates; read every flag in context before acting.", ""]

    # ---------------- data ----------------
    out += ["## Data", ""]
    b = R.get("race_coding_bound")
    if b:
        sev = "FLAG" if b["combo_ratio"] > 1.1 else "note"
        out.append(f"- **{sev}: race coding.** Officers record race by sight; the denominator is {G} alone. Alone or in combination is {round((b['combo_ratio'] - 1) * 100)}% larger. Worst-case ratios: "
                   + ", ".join(f"{g} {v}x (from {R['ratios'][g]}x)" for g, v in b["ratios_worst_case"].items()) + ". The writeup must state this bound.")
    else:
        out.append(f"- **FLAG: race coding bound missing.** No alone-or-in-combination figure for {G}; the size of the coding mismatch is unknown.")
    if pop.get("hispanic_overlap"):
        out.append(f"- **note: overlapping denominators.** Share of each race-alone group that is also Hispanic: {pop['hispanic_overlap']}. These residents count in two denominators; small shares are tolerable, large ones need non-Hispanic tables.")
    df = load_incidents(p)
    unm = df.loc[df["race"].isna() & df["race_raw"].notna(), "race_raw"].astype(str).value_counts()
    share_unm = len(df[df["race"].isna()]) / len(df) * 100
    out.append(f"- **{'FLAG' if share_unm > 10 else 'note'}: race outside the groups.** {share_unm:.1f}% of victims map to no group. Largest raw codes: " + ", ".join(f"`{k}` {v:,}" for k, v in unm.head(6).items()) + ". Check none of them should belong to a group.")
    if df["district"].notna().any() and "districts" in pop:
        d = df.groupby("district").agg(unknown=("race", lambda s: s.isna().mean() * 100))
        fshare = {k: v[G][S] / max(1, sum(v[g][S] for g in p.groups)) * 100 for k, v in pop["districts"].items()}
        d["focus_share"] = d.index.map(fshare)
        d = d.dropna()
        if len(d) > 4:
            r = float(np.corrcoef(d["unknown"], d["focus_share"])[0, 1])
            sev = "FLAG" if abs(r) > 0.4 else "note"
            out.append(f"- **{sev}: where race is missing.** Across {len(d)} districts, correlation between the share of victims with unknown race and the focus group's share of residents: {r:+.2f}. "
                       + ("Missing race is concentrated where the focus group lives, so its rate is likely understated there." if r > 0.4 else
                          "Missing race is concentrated where the focus group is scarce, so other groups' rates are likely understated, widening the gap." if r < -0.4 else "No strong pattern."))
    us = R["counts"]["unknown_race_share_by_sex"]
    out.append(f"- **note: unknown race by sex.** Women {us['F']}%, men {us['M']}%.")
    small = [g for g in p.groups if pop["city"][g][S] < 50000]
    rep = (p.out / "replication.json")
    if rep.exists():
        import json
        cmp = json.loads(rep.read_text())["comparison"]
        swings = sorted({g for cat in cmp.values() for g, v in cat.items() if v["change_pct"] is not None and abs(v["change_pct"]) > 25})
        if swings:
            out.append(f"- **FLAG: unstable comparisons.** Ratios moved more than 25% between sources for: {', '.join(swings)}. Do not headline those comparisons; say they are less stable.")
    if small:
        out.append(f"- **note: small groups.** Under 50,000 residents of the focus sex: {', '.join(small)}. Their rates carry more noise.")
    out.append("- **note: enforcement and reporting.** Police data reflects where police patrol and who calls them. Heavier policing or more reporting in some neighborhoods raises recorded rates there. The writeup must say the data cannot separate this from real differences.")
    out.append("- **note: controls are not neutral.** Neighborhood, income and housing are shaped by segregation and discrimination. A gap that shrinks after these controls has been located, not explained away; say so.")

    # ---------------- writeup ----------------
    files = [p.root / p.cfg.get("page_path", "index.html"), p.root / "README.md"] + [p.path(a) for a in sys.argv[2:]]
    files = [f for f in files if f.exists()]
    out += ["", "## Writeup", "", "Files: " + ", ".join(f"`{f.name}`" for f in files), ""]
    alltext = []
    for f in files:
        lines = text_of(f)
        alltext += lines
        hits = []
        for name, pat, why in WRITEUP_RULES:
            for i, l in enumerate(lines):
                for m in re.finditer(pat, l, re.I):
                    hits.append((name, why, l, m.group(0)))
        cap = {}
        for w in RACE_WORDS:
            lower = sum(len(re.findall(rf"\b{w.lower()}\b", l)) for l in lines)
            upper = sum(len(re.findall(rf"\b{w}\b", l)) for l in lines)
            if lower and upper:
                cap[w] = (upper, lower)
        out.append(f"### {f.name}")
        if cap:
            out.append("- **FLAG: inconsistent capitalization.** " + ", ".join(f"{w} {u}x vs {w.lower()} {l}x" for w, (u, l) in cap.items()) + ". Pick one style (AP capitalizes Black; be consistent for White).")
        seen = set()
        for name, why, line, word in hits:
            key = (name, line[:80])
            if key in seen:
                continue
            seen.add(key)
            snippet = line if len(line) < 220 else line[:220] + "..."
            out.append(f"- **review: {name}** (`{word}`): \"{snippet}\"  \n  {why}")
        if not hits and not cap:
            out.append("- no phrase flags")
        out.append("")
    joined = " ".join(alltext)
    out += ["### Required statements", ""]
    for name, pat in REQUIRED:
        ok = re.search(pat, joined, re.I)
        out.append(f"- {'present' if ok else '**FLAG: missing**'}: {name}")
    out += ["", "## Reviewer questions (answer in prose, not by regex)", "",
            "- Does any sentence invite the reader to infer who the offenders are?",
            "- Would the framing read the same if the groups were swapped?",
            "- Is the comparison group chosen to make the gap look larger (for example, headlining the most extreme pair)?",
            "- Are structural explanations (segregation, policing intensity, access to services) acknowledged as unmeasured, without being asserted?",
            "- Does the headline survive the race-coding worst case and the least favorable comparison?",
            "- Is the focus group described with agency and dignity, as people harmed, not as a problem?"]
    (p.out / "bias_review.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
