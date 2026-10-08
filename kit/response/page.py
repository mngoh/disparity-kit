"""Findings page and README results block from out/results.json, in the kit's house style.

  python page.py response.json      ->  <project>/index.html, and the README block between
                                        <!-- results:start --> and <!-- results:end -->

Every sentence with a number is generated from the results. Words that need judgment come from the config's
"page" section: headline, question, extra_caveats ([title, text]), focus (per grouping, the level drawn in red),
and links (code, home).
"""
import html
import json
import sys

from rt import RTProject

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from build_page import TEMPLATE as KIT  # noqa: E402

esc = html.escape
STYLE = KIT[KIT.index("<style>"):KIT.index("</style>") + len("</style>")]
PRELUDE = KIT[KIT.index("const C ="):KIT.index("{js}")]


def m1(v):
    return f"{v:.1f}"


def gapword(d, unit="minutes"):
    return f"{abs(d):.1f} {unit} {'longer' if d > 0 else 'shorter'}"


def pctword(r):
    return f"{abs(100 * r):.0f}% {'longer' if r > 0 else 'shorter'}"


def listing(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def main():
    p = RTProject(sys.argv[1])
    cfg, pg = p.cfg, p.cfg.get("page", {})
    a = cfg["analysis"]
    R = p.read_json("results.json")
    audit = p.read_json("audit.json")
    prep = p.read_json("prepare.json")
    nbj = p.read_json("neighborhoods.json")
    plab = {x["value"]: x["label"] for x in p.priorities}
    prios = [x["value"] for x in p.priorities]

    def L(gkey, lev):
        return a["groupings"][gkey].get("level_labels", {}).get(str(lev), str(lev))

    def lv(gkey, pr, lev):
        return next(r for r in R["levels"] if r["grouping"] == gkey and r["priority"] == pr and str(r["level"]) == str(lev))

    prim = {(r["grouping"], r["priority"], str(r["group"])): r for r in R["primary"]}
    js, sections = [], []

    def box(cid, title, sub, tall=False):
        return (f'<div class="chart-box"><h3>{esc(title)}</h3><div class="chart-sub">{sub}</div>'
                f'<div class="chart-wrap{" tall" if tall else ""}"><canvas id="{cid}"></canvas></div></div>')

    def bars(cid, labels, datasets, ytitle, horizontal=False, legend=True):
        ds = ",".join(f"{{label:{json.dumps(lab)},data:{json.dumps([round(v, 2) for v in data])},...bar({col})}}" for lab, data, col in datasets)
        av, ac = ("x", "y") if horizontal else ("y", "x")
        ia = 'indexAxis:"y",' if horizontal else ""
        js.append(f"new Chart(document.getElementById('{cid}'),{{type:'bar',data:{{labels:{json.dumps(labels)},datasets:[{ds}]}},"
                  f"options:{{...base,{ia}plugins:{{legend:{{display:{str(legend).lower()}}}}},"
                  f"scales:{{{ac}:{{grid:{{display:false}},ticks:{{color:C.text}}}},{av}:{{min:0,title:{{display:true,text:{json.dumps(ytitle)}}}}}}}}}}});")

    def colors(gkey):
        g = a["groupings"][gkey]
        focus = str(pg.get("focus", {}).get(gkey, ""))
        return ["C.red" if str(l) == focus else "C.blue" if l == g["reference"] else "C.muted" for l in g["levels"]]

    # charts: adjusted median by group, per priority
    for gkey, g in a["groupings"].items():
        boxes = []
        for pr in prios:
            cid = f"{gkey}_{pr.replace(' ', '')}"
            vals = [lv(gkey, pr, l)["adj_50"] for l in g["levels"]]
            cols = colors(gkey)
            ds = ",".join(f"{{label:'Adjusted median',data:{json.dumps([round(v, 2) for v in vals])},backgroundColor:'transparent',"
                          f"borderColor:[{','.join(cols)}],borderWidth:1.5,borderRadius:3,borderSkipped:false}}" for _ in [0])
            js.append(f"new Chart(document.getElementById('{cid}'),{{type:'bar',data:{{labels:{json.dumps([L(gkey, l) for l in g['levels']])},datasets:[{ds}]}},"
                      f"options:{{...base,indexAxis:'y',plugins:{{legend:{{display:false}}}},scales:{{y:{{grid:{{display:false}},ticks:{{color:C.text}}}},"
                      f"x:{{min:0,title:{{display:true,text:'Minutes from entry to arrival, adjusted median'}}}}}}}}}});")
            ref = lv(gkey, pr, g["reference"])
            prs = [prim[(gkey, pr, str(l))] for l in a["primary_comparisons"][gkey] if (gkey, pr, str(l)) in prim]
            sub = (f"{esc(plab[pr])} calls. {esc(L(gkey, g['reference']))} neighborhoods: {m1(ref['adj_50'])} minutes. " +
                   " ".join(f"{esc(L(gkey, r['group']))}: {m1(r['estimate'])} ({'+' if r['diff'] >= 0 else '-'}{abs(r['diff']):.1f}, "
                            f"95% interval {r['diff_lo']:+.1f} to {r['diff_hi']:+.1f}; {esc(r['verdict'])})." for r in prs))
            boxes.append(box(cid, f"{plab[pr]}", sub, tall=len(g["levels"]) > 5))
        sections.append(f'<div class="section-title">{esc(g["label"])}</div>'
                        f'<p class="note">Adjusted median minutes from the call being entered to the first officer arriving, calls from the public, '
                        f'{esc(cfg["periods"][a["main_periods"][0]][0][:4])} to {esc(cfg["periods"][a["main_periods"][-1]][1][:4])}. '
                        f'Each group\'s calls are reweighted to the citywide mix of call types, hours of the week and precinct busyness.</p>'
                        f'<div class="charts section-end">{"".join(boxes)}</div>')

    # primary table
    rows = "".join(
        f"<tr><td>{esc(plab[r['priority']])}</td><td>{esc(L(r['grouping'], r['group']))} vs {esc(L(r['grouping'], r['reference']))}</td>"
        f"<td>{m1(r['estimate'])} vs {m1(r['reference_estimate'])}</td><td>{r['diff']:+.1f} ({r['diff_lo']:+.1f} to {r['diff_hi']:+.1f})</td>"
        f"<td>{100 * r['rel']:+.0f}%</td><td>{r['p_holm']:.2g}</td><td>{esc(r['threshold'])}</td><td>{esc(r['verdict'])}</td></tr>"
        for r in R["primary"])
    table = ('<div class="section-title">Every planned comparison</div>'
             '<p class="note">The 20 comparisons set in the plan before the analysis ran. Gap in adjusted median minutes, with a 95% interval '
             'from resampling neighborhoods. p is Holm-adjusted for 20 tests. A gap "matters" if it is statistically clear and at least as large as '
             'the smallest gap that matters, set in advance; it is "smaller than the smallest gap that matters" if the whole interval sits inside that bound.</p>'
             '<div class="tablewrap section-end"><table><thead><tr><th>Priority</th><th>Comparison</th><th>Median (min)</th><th>Gap (min)</th>'
             f'<th>Gap (%)</th><th>p</th><th>Smallest gap that matters</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>')

    # ladder
    lad_boxes = []
    for gkey, g in a["groupings"].items():
        for pr in prios:
            steps = [r for r in R["ladder"] if r["grouping"] == gkey and r["priority"] == pr]
            levs = [str(l) for l in a["primary_comparisons"][gkey]]
            cid = f"lad_{gkey}_{pr.replace(' ', '')}"
            pal = ["C.red", "C.blueLight", "C.muted", "C.grey2"]
            bars(cid, [s["step"].replace("The plan's controls", "Plan").replace("Call type, hour of the week, precinct busyness (the plan's controls)", "Plan's controls") for s in steps],
                 [(L(gkey, l), [100 * s[l] for s in steps], pal[i % len(pal)]) for i, l in enumerate(levs)],
                 f"% difference from {L(gkey, g['reference']).lower()} neighborhoods", horizontal=True)
            lad_boxes.append(box(cid, f"{g['label']}: {plab[pr]}", "Percent difference in typical minutes against the reference after each set of controls. "
                                 "Regression of log minutes; errors clustered by neighborhood.", tall=True))
    ladder_html = ('<div class="section-title">What narrows the gaps</div><div class="charts section-end">' + "".join(lad_boxes) + "</div>")

    # no arrival
    na = [r for r in R["no_arrival"]]
    na_rows = "".join(f"<tr><td>{esc(plab[r['priority']])}</td><td>{esc(L(r['grouping'], r['level']))}</td><td>{r['calls']:,}</td>"
                      f"<td>{100 * r['share']:.1f}% ({100 * r['lo']:.1f} to {100 * r['hi']:.1f})</td></tr>" for r in na)
    na_html = ('<div class="section-title">Calls with no arrival time</div><p class="note">Share of calls from the public with no recorded arrival. '
               'These calls are not in the waiting times above.</p>'
               f'<div class="tablewrap section-end"><table><thead><tr><th>Priority</th><th>Neighborhoods</th><th>Calls</th><th>No arrival (95% interval)</th></tr></thead><tbody>{na_rows}</tbody></table></div>')

    # replication
    rp = [r for r in R["periods"] if r["quantile"] == 0.5 and (r["grouping"], r["priority"], str(r["group"])) in prim]
    rp_rows = "".join(f"<tr><td>{esc(plab[r['priority']])}</td><td>{esc(L(r['grouping'], r['group']))} vs {esc(L(r['grouping'], r['reference']))}</td>"
                      f"<td>{esc(r['period'])}</td><td>{r['diff']:+.1f} ({r['diff_lo']:+.1f} to {r['diff_hi']:+.1f})</td><td>{100 * r['rel']:+.0f}%</td></tr>" for r in rp)
    rep_html = ('<div class="section-title">Does it hold in other years?</div><p class="note">The same adjusted gaps in each half of the window and in the first half of 2026, '
                f'which was set aside as a check.</p><div class="tablewrap section-end"><table><thead><tr><th>Priority</th><th>Comparison</th><th>Period</th>'
                f'<th>Gap (min)</th><th>Gap (%)</th></tr></thead><tbody>{rp_rows}</tbody></table></div>')

    # caveats
    cav = [("This shows what, not why", "The data shows differences in recorded times between groups of neighborhoods. It does not show why: "
            "how units are deployed, what else they were handling, or how dispatchers chose."),
           ("Where calls come from", "Times depend on which calls are made, from where, and how call takers classify them. "
            "Neighborhoods differ in all three."),
           ("How the system records time", "The clock starts when the call taker enters the job, not when the phone rings, and stops when a unit is "
            "marked as arrived. Officers mark arrival themselves; some calls never get an arrival time."),
           ("Neighborhoods, not people", "Groups describe neighborhoods by their residents' makeup and income (Census estimates). "
            "They say nothing about who made a call.")] + [tuple(c) for c in pg.get("extra_caveats", [])]
    cav_html = '<div class="section-title">Limits</div><div class="findings">' + "".join(
        f'<div class="finding red"><h4>{esc(h)}</h4><p>{esc(t)}</p></div>' for h, t in cav) + "</div>"

    n_public = sum(r["n"] for r in audit["flags_public"])
    cards = [("Events", f"{prep['events']:,}", "2022 to mid-2026"), ("Calls from the public", f"{n_public:,}", "after officer-initiated events"),
             ("Neighborhoods", nbj["in_groups"], "residential NTAs"), ("Census", nbj["acs_release"], "")]
    cards_html = '<div class="cards">' + "".join(f'<div class="card"><div class="label">{esc(l)}</div><div class="value">{v}</div><div class="sub">{esc(s)}</div></div>' for l, v, s in cards) + "</div>"
    links = pg.get("links", {})
    nav = "".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in [("Code", links.get("code")), ("Residents Count", links.get("home"))] if u)
    lede = pg.get("headline", cfg["title"])
    answer = "".join(f"<p>{esc(s)}</p>" for s in pg.get("answer", []))
    method = esc(pg.get("method", ""))
    body = f"""
  <p class="lede">{esc(lede)}</p>
  <div class="answer"><div class="q">The question</div><div class="question">{esc(pg.get('question', ''))}</div>{answer}</div>
  <div class="section-title">Dataset</div>
  {cards_html}
  {''.join(sections)}
  {table}
  {ladder_html}
  {na_html}
  {rep_html}
  {cav_html}
  <div class="section-title">Method and sources</div>
  <p class="note">{method}</p>"""
    extra_css = ("<style>.tablewrap{overflow-x:auto;background:var(--surface);border:1px solid var(--border);border-radius:8px}"
                 "table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}"
                 "th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:10px;letter-spacing:.6px}</style>")
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(cfg['title'])}</title>
  <meta name="description" content="{esc(lede)}" />
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  {STYLE.replace('{{', '{').replace('}}', '}')}
  {extra_css}
</head>
<body>
<header>
  <div><h1>{esc(cfg['title'])}</h1><p>{esc(pg.get('byline', ''))}</p></div>
  <nav>{nav}</nav>
</header>
<div class="container">{body}
</div>
<footer>{esc(cfg['title'])} · {esc(pg.get('author', ''))}</footer>
<script>
  {PRELUDE.replace('{{', '{').replace('}}', '}')}
  {chr(10).join('  ' + j for j in js)}
</script>
</body>
</html>
"""
    for bad in ["—", "–"]:
        page = page.replace(bad, ", " if bad == "—" else " to ")
    out = p.root / pg.get("path", "index.html")
    out.write_text(page)
    print("wrote", out)


if __name__ == "__main__":
    main()
