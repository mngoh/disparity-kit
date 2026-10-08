"""Racial-discrimination screen for a response-time comparison between groups of neighborhoods.

The kit's bias_scan.py is built for victim records. This version keeps its writeup rules and adds what matters
when neighborhoods, not people, are grouped by racial makeup:

DATA checks (from out/results.json, out/neighborhoods.json, out/audit.json)
  ecological        groups describe neighborhoods by residents (Census self-identification), not callers
  small groups      groups with few neighborhoods, whose results can turn on one or two places
  unstable          gaps that change direction or move more than 25% between periods
  missing arrival   whether the share of calls with no arrival differs by group (the waits leave those calls out)
  controls          call type, busyness and precinct are shaped by policing and segregation; a gap they absorb is located

WRITEUP checks (index.html, README.md and any extra files)
  the kit's rules (causal claims, offender implications, deficit language, essentializing, absolutes,
  explained-away, sensational) plus statements about people where the data describes neighborhoods
  ("Black residents wait") and makeup words used without "majority" ("Black neighborhoods").

  python bias_scan.py response.json [extra.md ...]     ->  out/bias_review.md
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bias_scan import RACE_WORDS, WRITEUP_RULES, text_of  # noqa: E402

from rt import RTProject  # noqa: E402

RACE = r"(black|white|hispanic|latino|latinx|asian)"
EXTRA_RULES = [
    ("Statement about people", rf"\b{RACE} (residents|callers|new yorkers|people|families|communities|neighbors|households)\b[^.]{{0,40}}\b(wait|waited|waiting|get|got|receive[sd]?|face[sd]?)\b",
     "The data groups neighborhoods by who lives there; it does not know who called. Say \"calls from majority-Black neighborhoods\"."),
    ("Makeup without 'majority'", rf"(?<!majority-)(?<!majority )\b{RACE} (neighborhoods?|areas?|communities|precincts?)\b",
     "Describe neighborhoods by their makeup (\"majority-Black neighborhoods\"), not as if they had a race."),
]
REQUIRED = [
    ("says it does not show why", r"(not (say|show|explain|measure)|cannot (say|show)|does not (say|show)) (why|causes)|not why"),
    ("says groups describe neighborhoods, not callers", r"(not|nothing about) who (made|called|placed)|neighborhoods, not people|not (the )?callers"),
    ("names where calls come from and how they are classified", r"(where calls come from|which calls are made)"),
    ("names how the system records time", r"(records? (the )?time|marked as arrived|mark (their own )?arrival|clock starts)"),
    ("names calls with no arrival", r"no (recorded )?arrival"),
]


def main():
    p = RTProject(sys.argv[1])
    a = p.cfg["analysis"]
    R = p.read_json("results.json")
    nb = p.read_json("neighborhoods.json")
    out = ["# Racial discrimination review: response times", "",
           "This screen finds candidates; every flag needs to be read in context before acting.", "", "## Data", ""]
    out.append("- **note: neighborhoods, not people.** Makeup groups come from Census estimates of residents (self-identified), "
               "so there is no officer-recorded race and no race-coding bound. The data says nothing about who made each call; "
               "the writeup must not imply that callers of a race waited longer.")
    for m in nb["makeup"]:
        if m["neighborhoods"] < a.get("min_group_neighborhoods", 10):
            out.append(f"- **FLAG: small group.** {m['makeup']}: {m['neighborhoods']} neighborhoods, {m['residents']:,} residents. Do not headline its comparisons.")
    # stability across periods
    prim = {(r["grouping"], r["priority"], str(r["group"])): r for r in R["primary"]}
    for key, r in prim.items():
        per = [x for x in R["periods"] if (x["grouping"], x["priority"], str(x["group"])) == key and x["quantile"] == 0.5]
        signs = {(x["diff"] > 0) for x in per if abs(x["diff"]) > 0} | {r["diff"] > 0}
        moved = [x for x in per if r["diff"] and abs(x["diff"] - r["diff"]) > 0.25 * abs(r["diff"])]
        if len(signs) > 1:
            out.append(f"- **FLAG: direction changes by period.** {key[0]} {key[1]} {key[2]}: " +
                       ", ".join(f"{x['period']} {x['diff']:+.1f} min" for x in per) + f" (pooled {r['diff']:+.1f}). Do not headline.")
        elif moved and r.get("verdict") == "gap that matters":
            out.append(f"- **note: size moves by period.** {key[0]} {key[1]} {key[2]}: " + ", ".join(f"{x['period']} {x['diff']:+.1f}" for x in per) + ".")
    # missing arrivals by group
    for gkey in a["groupings"]:
        for pr in [x["value"] for x in p.priorities]:
            rows = [r for r in R["no_arrival"] if r["grouping"] == gkey and r["priority"] == pr]
            if not rows:
                continue
            hi, lo = max(rows, key=lambda r: r["share"]), min(rows, key=lambda r: r["share"])
            if hi["share"] - lo["share"] > 0.05:
                out.append(f"- **FLAG: missing arrivals differ by group.** {gkey}, {pr}: {100 * lo['share']:.1f}% ({lo['level']}) to {100 * hi['share']:.1f}% ({hi['level']}). "
                           "The waits leave these calls out; state the difference beside the waits and report the upper-bound check.")
    out.append("- **note: controls are not neutral.** Call types, precinct busyness and precinct boundaries reflect where calls come from, "
               "how they are coded, and how police are deployed, which are shaped by segregation. A gap that narrows after them has been located, not explained away.")

    files = [p.root / p.cfg.get("page", {}).get("path", "index.html"), p.root / "README.md"] + [p.path(x) for x in sys.argv[2:]]
    files = [f for f in files if f.exists()]
    out += ["", "## Writeup", "", "Files: " + ", ".join(f"`{f.name}`" for f in files), ""]
    alltext = []
    for f in files:
        lines = text_of(f)
        alltext += lines
        hits, seen = [], set()
        for name, pat, why in WRITEUP_RULES + EXTRA_RULES:
            for line in lines:
                for m in re.finditer(pat, line, re.I):
                    if (name, line[:80]) not in seen:
                        seen.add((name, line[:80]))
                        hits.append((name, why, line, m.group(0)))
        cap = {}
        for w in RACE_WORDS:
            lower = sum(len(re.findall(rf"\b{w.lower()}\b", l)) for l in lines)
            upper = sum(len(re.findall(rf"\b{w}\b", l)) for l in lines)
            if lower and upper:
                cap[w] = (upper, lower)
        out.append(f"### {f.name}")
        if cap:
            out.append("- **FLAG: inconsistent capitalization.** " + ", ".join(f"{w} {u}x vs {w.lower()} {l}x" for w, (u, l) in cap.items()))
        for name, why, line, word in hits:
            snippet = line if len(line) < 220 else line[:220] + "..."
            out.append(f"- **review: {name}** (`{word}`): \"{snippet}\"  \n  {why}")
        if not hits and not cap:
            out.append("- no phrase flags")
        out.append("")
    joined = " ".join(alltext)
    out += ["### Required statements", ""] + [f"- {'present' if re.search(pat, joined, re.I) else '**FLAG: missing**'}: {name}" for name, pat in REQUIRED]
    out += ["", "## Reviewer questions (answer in prose)", "",
            "- Does any sentence turn a neighborhood difference into a statement about people of a race?",
            "- Would the framing read the same if the groups were swapped?",
            "- Do results that favor the police get the same prominence as those that don't?",
            "- Is the headline comparison stable across periods and not the smallest group?",
            "- Are deployment, staffing and dispatch choices named as unmeasured, without being asserted as the cause?"]
    (p.out / "bias_review.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
