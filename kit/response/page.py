"""Findings page and README results block from out/results.json, in the kit's house style.

  python page.py response.json      ->  <project>/index.html, and the README block between
                                        <!-- results:start --> and <!-- results:end -->

Words that need judgment come from the config's "page" section; every number in them is a placeholder filled
from the outputs, so text cannot drift from the results:

  [[p:Critical|income|1|diff|+.1f]]            a primary comparison (fields of out/results.json "primary")
  [[lv:Critical|makeup|White|adj_50|.1f]]      a group's medians ("levels")
  [[lad:Critical|income|1|precinct|pct]]       a ladder step (key, or key_lo / key_hi)
  [[per:Critical|income|1|2026 holdout|diff|+.1f]]   a period's gap ("periods")
  [[na:Critical|makeup|White|share|pct1]]      calls with no arrival ("no_arrival")
  [[sen:Critical|income|1|calls with no arrival|diff|+.1f]]  a sensitivity (by the start of its label)
  [[j:prepare.json|events|,]]                   any value in an out/ JSON file (dotted path)

Formats: any Python format spec, or pct (40%), pct+ (+40%), pct1 (40.1%), abs1 (absolute value, one decimal),
abspct (absolute percent).

Page keys: headline, question, answer [paragraphs], focus {grouping: [levels drawn in red]}, findings
[[title, text]], extra_caveats [[title, text]], method, sources, byline, author, links {code, home}, path.
"""
import html
import json
import re
import sys
from pathlib import Path

from rt import RTProject

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_page import TEMPLATE as KIT  # noqa: E402

esc = html.escape
STYLE = KIT[KIT.index("<style>"):KIT.index("</style>") + len("</style>")].replace("{{", "{").replace("}}", "}")
PRELUDE = KIT[KIT.index("const C ="):KIT.index("{js}")].replace("{{", "{").replace("}}", "}")
SHORT_STEPS = {"none": "No controls", "type": "Call type", "type_how": "+ hour of week", "plan": "+ precinct busyness",
               "distance": "+ distance to station", "staffing": "+ officers per call", "precinct": "Same precinct only"}


def lf(label):
    """Lower-case a label's first letter only, so race words keep their capitals ("majority-White")."""
    return label[:1].lower() + label[1:]


def fmt(v, spec):
    if spec == "pct":
        return f"{100 * v:.0f}%"
    if spec == "pct+":
        return f"{100 * v:+.0f}%"
    if spec == "pct1":
        return f"{100 * v:.1f}%"
    if spec == "abs1":
        return f"{abs(v):.1f}"
    if spec == "abspct":
        return f"{abs(100 * v):.0f}%"
    return format(v, spec)


class Filler:
    def __init__(self, p, R):
        self.p, self.R = p, R

    def find(self, rows, **kw):
        for r in rows:
            if all(str(r.get(k)) == str(v) or (k == "sensitivity" and str(r.get(k, "")).startswith(v)) for k, v in kw.items()):
                return r
        raise KeyError(f"no row with {kw}")

    def value(self, token):
        kind, rest = token.split(":", 1)
        a = rest.split("|")
        R = self.R
        if kind == "p":
            pr, g, lev, field, spec = a
            return fmt(self.find(R["primary"], priority=pr, grouping=g, group=lev)[field], spec)
        if kind == "lv":
            pr, g, lev, field, spec = a
            return fmt(self.find(R["levels"], priority=pr, grouping=g, level=lev)[field], spec)
        if kind == "lad":
            pr, g, lev, key, spec = a
            suffix = "_lo" if key.endswith("_lo") else "_hi" if key.endswith("_hi") else ""
            row = self.find(R["ladder"], priority=pr, grouping=g, key=key[:len(key) - len(suffix)] if suffix else key)
            return fmt(row[lev + suffix], spec)
        if kind == "per":
            pr, g, lev, period, field, spec = a
            return fmt(self.find(R["periods"], priority=pr, grouping=g, group=lev, period=period, quantile=0.5)[field], spec)
        if kind == "na":
            pr, g, lev, field, spec = a
            return fmt(self.find(R["no_arrival"], priority=pr, grouping=g, level=lev)[field], spec)
        if kind == "sen":
            pr, g, lev, tag, field, spec = a
            return fmt(self.find(R["sensitivity"], priority=pr, grouping=g, group=lev, sensitivity=tag)[field], spec)
        if kind == "j":
            name, path, spec = a
            v = self.p.read_json(name)
            for k in path.split("."):
                v = v[int(k)] if isinstance(v, list) else v[k]
            return fmt(v, spec)
        raise ValueError(token)

    def __call__(self, text):
        return re.sub(r"\[\[([^\]]+)\]\]", lambda m: self.value(m.group(1)), text)


def main():
    p = RTProject(sys.argv[1])
    cfg, pg = p.cfg, p.cfg.get("page", {})
    a = cfg["analysis"]
    R = p.read_json("results.json")
    F = Filler(p, R)
    prep, nbj, audit = p.read_json("prepare.json"), p.read_json("neighborhoods.json"), p.read_json("audit.json")
    plab = {x["value"]: x["label"] for x in p.priorities}
    prios = [x["value"] for x in p.priorities]
    small = {m["makeup"] for m in nbj["makeup"] if m["neighborhoods"] < a.get("min_group_neighborhoods", 10)}
    main_years = f"{cfg['periods'][a['main_periods'][0]][0][:4]} to {cfg['periods'][a['main_periods'][-1]][1][:4]}"

    def L(gkey, lev):
        return a["groupings"][gkey].get("level_labels", {}).get(str(lev), str(lev))

    def lv(gkey, pr, lev):
        return F.find(R["levels"], grouping=gkey, priority=pr, level=lev)

    js = []

    def box(cid, title, sub, height=None):
        style = f' style="height:{height}px"' if height else ""
        return (f'<div class="chart-box"><h3>{esc(title)}</h3><div class="chart-sub">{sub}</div>'
                f'<div class="chart-wrap"{style}><canvas id="{cid}"></canvas></div></div>')

    def hbars(cid, labels, values, colors, xtitle, suffix=""):
        js.append(f"new Chart(document.getElementById('{cid}'),{{type:'bar',data:{{labels:{json.dumps(labels)},datasets:[{{data:{json.dumps([round(v, 2) for v in values])},"
                  f"backgroundColor:'transparent',borderColor:[{','.join(colors)}],borderWidth:1.5,borderRadius:3,borderSkipped:false}}]}},"
                  f"options:{{...base,indexAxis:'y',plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>c.parsed.x.toFixed(1)+'{suffix}'}}}}}},"
                  f"scales:{{y:{{grid:{{display:false}},ticks:{{color:C.text}}}},x:{{min:0,title:{{display:true,text:{json.dumps(xtitle)}}}}}}}}}}});")

    def grouped(cid, labels, datasets, xtitle):
        ds = ",".join(f"{{label:{json.dumps(lab)},data:{json.dumps([round(v, 1) for v in data])},...bar({col})}}" for lab, data, col in datasets)
        js.append(f"new Chart(document.getElementById('{cid}'),{{type:'bar',data:{{labels:{json.dumps(labels)},datasets:[{ds}]}},"
                  f"options:{{...base,indexAxis:'y',plugins:{{legend:{{display:true}}}},scales:{{y:{{grid:{{display:false}},ticks:{{color:C.text}}}},"
                  f"x:{{title:{{display:true,text:{json.dumps(xtitle)}}}}}}}}}}});")

    focus = {k: [str(x) for x in v] for k, v in pg.get("focus", {}).items()}

    def color(gkey, lev):
        if str(lev) in focus.get(gkey, []):
            return "C.red"
        return "C.blue" if lev == a["groupings"][gkey]["reference"] else "C.muted"

    sections = []
    # 1. medians by group, one small chart per priority
    for gkey, g in a["groupings"].items():
        boxes = []
        for pr in prios:
            cid = f"m_{gkey}_{re.sub(r'[^A-Za-z]', '', pr)}"
            levs = g["levels"]
            hbars(cid, [L(gkey, l) for l in levs], [lv(gkey, pr, l)["adj_50"] for l in levs], [color(gkey, l) for l in levs], "Minutes, adjusted median", " min")
            ref = lv(gkey, pr, g["reference"])
            prs = [F.find(R["primary"], grouping=gkey, priority=pr, group=l) for l in a["primary_comparisons"][gkey]]
            sub = esc(f"{L(gkey, g['reference'])}: {ref['adj_50']:.1f} min. ") + " ".join(
                esc(f"{L(gkey, r['group'])}: {r['diff']:+.1f} min ({r['diff_lo']:+.1f} to {r['diff_hi']:+.1f}), {r['verdict']}.") for r in prs)
            boxes.append(box(cid, plab[pr], sub, 60 + 34 * len(levs)))
        sections.append(f'<div class="section-title">By {esc(g["label"].lower())}</div>'
                        f'<p class="note">Median minutes from the call being entered to the first officer arriving, calls from the public, {main_years}, '
                        f'with each group\'s calls reweighted to the citywide mix of call types, hours of the week and precinct busyness. '
                        f'Gaps against {esc(lf(L(gkey, g["reference"])))} neighborhoods with 95% intervals.</p>'
                        f'<div class="charts section-end">{"".join(boxes)}</div>')

    # 2. across the city against within the same precinct
    lad_boxes = []
    for gkey, g in a["groupings"].items():
        for lev in a["primary_comparisons"][gkey]:
            if str(lev) in small:
                continue
            cid = f"lad_{gkey}_{re.sub(r'[^A-Za-z0-9]', '', str(lev))}"
            city = [100 * F.find(R["ladder"], grouping=gkey, priority=pr, key="plan")[str(lev)] for pr in prios]
            same = [100 * F.find(R["ladder"], grouping=gkey, priority=pr, key="precinct")[str(lev)] for pr in prios]
            grouped(cid, [plab[pr] for pr in prios], [("Across the city", city, "C.muted"), ("Same precinct", same, "C.blueLight")],
                    f"% difference from {lf(L(gkey, g['reference']))} neighborhoods")
            lad_boxes.append(box(cid, f"{L(gkey, lev)} against {lf(L(gkey, g['reference']))} neighborhoods",
                                 "Percent difference in typical minutes, with the plan's controls, across the city and comparing neighborhoods inside the same precinct.", 260))
    steps = [s for s in SHORT_STEPS if any(r["key"] == s for r in R["ladder"])]
    lad_rows = ""
    for gkey, g in a["groupings"].items():
        for lev in a["primary_comparisons"][gkey]:
            for pr in prios:
                cells = "".join(f"<td>{100 * F.find(R['ladder'], grouping=gkey, priority=pr, key=s)[str(lev)]:+.0f}%</td>" for s in steps)
                lad_rows += f"<tr><td>{esc(L(gkey, lev))}</td><td>{esc(plab[pr])}</td>{cells}</tr>"
    ladder_html = ('<div class="section-title">What narrows the gaps</div>'
                   '<p class="note">Regression of log minutes with errors clustered by neighborhood: the percent difference in typical minutes after each set of '
                   'controls, added one at a time. Officers per call uses a 2026 staffing snapshot. "Same precinct only" compares neighborhoods inside the same precinct.</p>'
                   f'<div class="charts">{"".join(lad_boxes)}</div>'
                   '<div class="tablewrap section-end"><table><thead><tr><th>Neighborhoods</th><th>Priority</th>' +
                   "".join(f"<th>{esc(SHORT_STEPS[s])}</th>" for s in steps) + f"</tr></thead><tbody>{lad_rows}</tbody></table></div>")

    # 3. every planned comparison
    rows = "".join(
        f"<tr><td>{esc(plab[r['priority']])}</td><td>{esc(L(r['grouping'], r['group']))}{' *' if str(r['group']) in small else ''}</td>"
        f"<td>{r['estimate']:.1f} vs {r['reference_estimate']:.1f}</td><td>{r['diff']:+.1f} ({r['diff_lo']:+.1f} to {r['diff_hi']:+.1f})</td>"
        f"<td>{100 * r['rel']:+.0f}%</td><td>{r['p_holm']:.2g}</td><td>{esc(r['threshold'])}</td><td>{esc(r['verdict'])}</td></tr>" for r in R["primary"])
    table = ('<div class="section-title">Every planned comparison</div>'
             f'<p class="note">The {len(R["primary"])} comparisons set in the plan before the analysis ran, each against the highest-income fifth or majority-White neighborhoods. '
             'Adjusted median minutes; gap with a 95% interval from resampling neighborhoods; p adjusted for all of them (Holm). '
             'A gap "matters" if it is clear and at least the smallest gap that matters, set in advance. It is "smaller than the smallest gap that matters" if the whole interval '
             f'sits inside that bound. * Fewer than {a.get("min_group_neighborhoods", 10)} neighborhoods: reported, not headlined.</p>'
             '<div class="tablewrap section-end"><table><thead><tr><th>Priority</th><th>Neighborhoods</th><th>Median (min)</th><th>Gap (min)</th>'
             f'<th>Gap</th><th>p</th><th>Bar</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>')

    # 4. parts of the wait
    part_rows = ""
    for gkey in a["groupings"]:
        for lev in a["primary_comparisons"][gkey]:
            for pr in prios:
                cells = []
                for m in ("to_dispatch", "travel"):
                    r = F.find(R["parts"], grouping=gkey, priority=pr, group=lev, measure=m, kind="adj", quantile=0.5)
                    cells.append(f"<td>{r['diff']:+.1f} ({r['diff_lo']:+.1f} to {r['diff_hi']:+.1f})</td>")
                part_rows += f"<tr><td>{esc(L(gkey, lev))}</td><td>{esc(plab[pr])}</td>{''.join(cells)}</tr>"
    parts_html = ('<div class="section-title">Where the extra time comes from</div>'
                  '<p class="note">Gap in adjusted median minutes for each part of the wait: from entry to a unit being dispatched, and from dispatch to arrival.</p>'
                  '<div class="tablewrap section-end"><table><thead><tr><th>Neighborhoods</th><th>Priority</th><th>Entry to dispatch</th><th>Dispatch to arrival</th>'
                  f'</tr></thead><tbody>{part_rows}</tbody></table></div>')

    # 5. no arrival
    na_rows = ""
    for gkey, g in a["groupings"].items():
        for lev in g["levels"]:
            cells = "".join(f"<td>{100 * F.find(R['no_arrival'], grouping=gkey, priority=pr, level=lev)['share']:.1f}%</td>" for pr in prios)
            na_rows += f"<tr><td>{esc(L(gkey, lev))}</td>{cells}</tr>"
    na_html = ('<div class="section-title">Calls with no arrival time</div>'
               '<p class="note">Share of calls from the public with no recorded arrival, which the waits above leave out. Counting them as waiting until the event was '
               'closed makes the gaps for less urgent calls larger, not smaller.</p>'
               '<div class="tablewrap section-end"><table><thead><tr><th>Neighborhoods</th>' + "".join(f"<th>{esc(plab[pr])}</th>" for pr in prios) +
               f'</tr></thead><tbody>{na_rows}</tbody></table></div>')

    # 6. replication
    rp_rows = ""
    periods = list(cfg["periods"])
    for r in R["primary"]:
        if str(r["group"]) in small:
            continue
        cells = "".join(f"<td>{x['diff']:+.1f} ({x['diff_lo']:+.1f} to {x['diff_hi']:+.1f})</td>" for x in
                        [F.find(R["periods"], grouping=r["grouping"], priority=r["priority"], group=r["group"], period=per, quantile=0.5) for per in periods])
        rp_rows += f"<tr><td>{esc(plab[r['priority']])}</td><td>{esc(L(r['grouping'], r['group']))}</td>{cells}</tr>"
    rep_html = ('<div class="section-title">Does it hold in other years?</div>'
                '<p class="note">Gap in adjusted median minutes in each half of the window and in January to June 2026, which was set aside as a check.</p>'
                '<div class="tablewrap section-end"><table><thead><tr><th>Priority</th><th>Neighborhoods</th>' + "".join(f"<th>{esc(x)}</th>" for x in periods) +
                f'</tr></thead><tbody>{rp_rows}</tbody></table></div>')

    findings = "".join(f'<div class="finding{" red" if i % 2 == 0 else ""}"><h4>{esc(F(h))}</h4><p>{esc(F(t))}</p></div>'
                       for i, (h, t) in enumerate(pg.get("findings", [])))
    cav = [("This shows what, not why", "The data shows differences in recorded times between groups of neighborhoods. It does not show why: "
            "how units were deployed, what else they were handling, or how dispatchers chose."),
           ("Where calls come from", "Times depend on which calls are made, from where, and how call takers classify them. Neighborhoods differ in all three; "
            "holding the call type fixed does not hold fixed the judgment behind it."),
           ("How the system records time", "The clock starts when the call taker enters the job, not when the phone rings, and stops when a unit is marked as arrived. "
            "Officers mark their own arrival, and some calls never get an arrival time."),
           ("Neighborhoods, not people", "Groups describe neighborhoods by their residents' income and makeup (Census estimates). The data says nothing about who made a call.")]
    cav += [(F(h), F(t)) for h, t in pg.get("extra_caveats", [])]
    cav_html = '<div class="section-title">Limits</div><div class="findings">' + "".join(
        f'<div class="finding red"><h4>{esc(h)}</h4><p>{esc(t)}</p></div>' for h, t in cav) + "</div>"

    n_public = sum(r["n"] for r in audit["flags_public"])
    cards = [("Calls from the public", f"{n_public:,}", "2022 to mid-2026"), ("All events", f"{prep['events']:,}", "incl. officer-initiated"),
             ("Neighborhoods", str(nbj["in_groups"]), "residential 2020 NTAs"), ("Planned tests", str(len(R["primary"])), "Holm-corrected")]
    cards_html = '<div class="cards">' + "".join(f'<div class="card"><div class="label">{esc(l)}</div><div class="value">{v}</div><div class="sub">{esc(s)}</div></div>' for l, v, s in cards) + "</div>"
    links = pg.get("links", {})
    nav = "".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in [("Plan", links["code"] + "/blob/main/PLAN.md" if links.get("code") else "PLAN.md"), ("Code", links.get("code")), ("Residents Count", links.get("home"))] if u)
    lede = F(pg.get("headline", cfg["title"]))
    answer = "".join(f"<p>{esc(F(s))}</p>" for s in pg.get("answer", []))
    extra_css = ("<style>.tablewrap{overflow-x:auto;background:var(--surface);border:1px solid var(--border);border-radius:8px;margin-bottom:16px}"
                 "table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}"
                 "th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:10px;letter-spacing:.6px}tr:last-child td{border-bottom:0}</style>")
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(cfg['title'])}</title>
  <meta name="description" content="{esc(lede)}" />
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  {STYLE}
  {extra_css}
</head>
<body>
<header>
  <div><h1>{esc(cfg['title'])}</h1><p>{esc(F(pg.get('byline', '')))}</p></div>
  <nav>{nav}</nav>
</header>
<div class="container">
  <p class="lede">{esc(lede)}</p>
  <div class="answer"><div class="q">The question</div><div class="question">{esc(pg.get('question', ''))}</div>{answer}</div>
  <div class="section-title">Dataset</div>
  {cards_html}
  <div class="findings">{findings}</div>
  {''.join(sections)}
  {ladder_html}
  {table}
  {parts_html}
  {na_html}
  {rep_html}
  {cav_html}
  <div class="section-title">Method and sources</div>
  <p class="note">{esc(F(pg.get('method', '')))}</p>
  <p class="note">{esc(F(pg.get('sources', '')))}</p>
</div>
<footer>{esc(cfg['title'])} · {esc(pg.get('author', ''))}</footer>
<script>
  {PRELUDE}
{chr(10).join('  ' + j for j in js)}
</script>
</body>
</html>
"""
    for bad in ["—", "–"]:
        if bad in page:
            sys.exit(f"page has an em or en dash; fix the config text")
    out = p.root / pg.get("path", "index.html")
    out.write_text(page)
    print("wrote", out)
    readme(p, R, F, lede, cav, L, plab, prios, small)


def readme(p, R, F, lede, cav, L, plab, prios, small):
    a, pg = p.cfg["analysis"], p.cfg.get("page", {})
    lines = ["<!-- results:start -->", f"**{lede}**", ""] + [F(s) + "\n" for s in pg.get("answer", [])]
    lines += ["Adjusted median minutes from the call being entered to the first officer arriving, calls from the public, 2022 to 2025. "
              "Gap with a 95% interval; p is Holm-adjusted across the 20 planned comparisons.", "",
              "| Priority | Neighborhoods | Median (min) | Gap (min) | Gap | Holm p | Result |", "|---|---|---|---|---|---|---|"]
    for r in R["primary"]:
        lines.append(f"| {plab[r['priority']]} | {L(r['grouping'], r['group'])}{' *' if str(r['group']) in small else ''} vs {lf(L(r['grouping'], r['reference']))} | "
                     f"{r['estimate']:.1f} vs {r['reference_estimate']:.1f} | {r['diff']:+.1f} ({r['diff_lo']:+.1f} to {r['diff_hi']:+.1f}) | {100 * r['rel']:+.0f}% | {r['p_holm']:.2g} | {r['verdict']} |")
    lines += ["", f"\\* Fewer than {a.get('min_group_neighborhoods', 10)} neighborhoods: reported, not headlined.", "",
              "What narrows the gaps (percent difference in typical minutes, the plan's controls, across the city and inside the same precinct):", "",
              "| Neighborhoods | Priority | Across the city | Same precinct |", "|---|---|---|---|"]
    for gkey in a["groupings"]:
        for lev in a["primary_comparisons"][gkey]:
            if str(lev) in small:
                continue
            for pr in prios:
                c = F.find(R["ladder"], grouping=gkey, priority=pr, key="plan")
                s = F.find(R["ladder"], grouping=gkey, priority=pr, key="precinct")
                lines.append(f"| {L(gkey, lev)} | {plab[pr]} | {100 * c[str(lev)]:+.0f}% | {100 * s[str(lev)]:+.0f}% ({100 * s[str(lev) + '_lo']:+.0f}% to {100 * s[str(lev) + '_hi']:+.0f}%) |")
    lines += ["", "Limits:", ""] + [f"- **{h}.** {t}" for h, t in cav] + ["<!-- results:end -->"]
    block = "\n".join(lines)
    rd = p.root / "README.md"
    s = rd.read_text()
    i, j = s.index("<!-- results:start -->"), s.index("<!-- results:end -->") + len("<!-- results:end -->")
    rd.write_text(s[:i] + block + s[j:])
    print("updated README results block")


if __name__ == "__main__":
    main()
