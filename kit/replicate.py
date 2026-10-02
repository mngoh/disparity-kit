"""Does the finding hold in a second source? Same rates, same population, definitions matched.

Data from a second source (a new records system, another agency, a later period) is shaped differently
every time, so the fetch is written per project. It must produce out/replication_counts.json:

  {"sources": {
     "primary":   {"label": "Old system, 2020 to 2023", "years": 4.0,
                   "counts": {"all": {"Black": 3350, "Hispanic": 4400, ...}, "partner": {...}}},
     "secondary": {"label": "NIBRS, 2024 to 2026", "years": 2.25, "counts": {...}}},
   "definition_notes": "how categories were matched between sources"}

Counts are for the focus sex only. This script turns them into rates against out/population.json and
compares the focus group's ratios across sources.

  python replicate.py analysis.json        ->  out/replication.json
"""
import sys

from common import Project


def main():
    p = Project(sys.argv[1])
    G, S = p.focus
    pop = p.read_json("population.json")["city"]
    spec = p.read_json("replication_counts.json")
    out = {"sources": {}, "definition_notes": spec.get("definition_notes", ""), "comparison": {}}
    for key, src in spec["sources"].items():
        o = {"label": src["label"], "years": src["years"], "rates": {}, "ratios": {}}
        for cat, cnt in src["counts"].items():
            o["rates"][cat] = {g: round(cnt[g] / pop[g][S] / src["years"] * 1e5) for g in cnt if g in pop}
            o["ratios"][cat] = {g: round(o["rates"][cat][G] / r, 2) for g, r in o["rates"][cat].items() if g != G and r}
        out["sources"][key] = o
    keys = list(out["sources"])
    if len(keys) >= 2:
        a, b = out["sources"][keys[0]], out["sources"][keys[1]]
        for cat in [c for c in a["ratios"] if c in b["ratios"]]:  # stable order, so reruns do not reshuffle the output
            out["comparison"][cat] = {g: {"first": a["ratios"][cat][g], "second": b["ratios"][cat].get(g),
                                          "change_pct": round((b["ratios"][cat][g] / a["ratios"][cat][g] - 1) * 100) if b["ratios"][cat].get(g) else None}
                                      for g in a["ratios"][cat]}
    p.write_json("replication.json", out)
    for cat, rows in out["comparison"].items():
        print(cat, {g: f"{v['first']}x -> {v['second']}x ({v['change_pct']:+d}%)" for g, v in rows.items() if v["change_pct"] is not None})


if __name__ == "__main__":
    main()
