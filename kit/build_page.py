"""Render the findings page and the README results block from out/results.json (and out/replication.json if present).

Project-specific checks plug in through out/extra_sections.json, written by the project's own scripts so their
numbers are generated too: {"tests": [box], "reporting": {"title", "text"}, "sections": [{"title", "note",
"boxes": [box], "findings": [{"title", "text", "red"}]}], "readme": [[label, text]]}, where a box is {"id", "title",
"text", "labels", "datasets": [{"label", "data", "color"}], "ytitle", "kind": "bar" or "line", "horizontal", "tall",
"legend"} and color is one of red, blue, blueLight, muted, grey2. Tests join the rival-explanation grid, reporting
replaces the default reporting note, and sections follow it.

House style: near-black background, red for the focus group, blue for comparisons, outlined bars, Chart.js,
mobile friendly, no em dashes. Every sentence with a number is generated from the results, so text cannot go stale.
Sections appear only when the data supports them.

  python build_page.py analysis.json      ->  <project>/index.html, and the block between
                                               <!-- results:start --> and <!-- results:end --> in <project>/README.md
"""
import html
import json
import sys

from common import SEX_WORD, Project

esc = html.escape


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else f"{n:,.0f}" if isinstance(n, float) and n >= 100 else str(n)


def x(v):
    """A ratio for prose: one decimal, no trailing .0"""
    r = round(v, 1)
    return str(int(r)) if r == int(r) else str(r)


def listing(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def main():
    p = Project(sys.argv[1])
    cfg, R = p.cfg, p.read_json("results.json")
    rep = json.loads((p.out / "replication.json").read_text()) if (p.out / "replication.json").exists() else None
    extra = json.loads((p.out / "extra_sections.json").read_text()) if (p.out / "extra_sections.json").exists() else {}
    G, S = p.focus
    FL = esc(p.focus_label)
    sexw = SEX_WORD[S][0]
    ev = cfg.get("event", {"noun": "incident", "plural": "incidents", "verb": "victimized"})
    place = cfg["place"]
    others, groups = p.others, p.groups
    rates, ratios = R["rates"], R["ratios"]
    fr = rates[G][S]
    kinds = R["kinds"]
    klabel = lambda k: cfg.get("kind_labels", {}).get(k, k.title())
    M, T = R.get("model"), R["tests"]
    start, end = cfg["window"]["start"], cfg["window"]["end"]
    window_label = f"{start[:4]} to {end[:4]}" if start[:4] != end[:4] else start[:4]

    # headline and answer
    ranked = sorted(others, key=lambda g: ratios.get(g, 0))
    lede = cfg.get("headline") or (f"The reported {ev['noun']} rate for {p.focus_label} in {place.get('short', place['name'])} is "
                                   f"{listing(x(ratios[g]) for g in ranked)} times that of {listing(ranked)} {sexw}.")
    answer = [f"Per 100,000 residents a year, the reported {ev['noun']} rate for {p.focus_label} is {fmt(fr)}: " +
              ", ".join(f"{x(ratios[g])} times {g} {sexw}'s" for g in ranked) + "."]
    if M:
        final = M["ladder"][-1]
        answer.append(f"Adjusted for age, year, {cfg.get('districts', {}).get('label', 'district')}, neighborhood socioeconomics"
                      f"{' and ' + ', '.join(c['label'] for c in cfg.get('covariates', [])) if cfg.get('covariates') else ''}, "
                      f"the gap against all other {sexw} is {final['rate_ratio']}x (95% CI {final['ci_low']} to {final['ci_high']}).")
    question = cfg.get("question", f"Is the reported {ev['noun']} rate for {p.focus_label} higher than for other {sexw}, and do the obvious explanations account for it?")

    charts, js = [], []  # (section html) and chart scripts

    def box(cid, title, sub, tall=False):
        return (f'<div class="chart-box"><h3>{esc(title)}</h3><div class="chart-sub">{sub}</div>'
                f'<div class="chart-wrap{" tall" if tall else ""}"><canvas id="{cid}"></canvas></div></div>')

    def bars(cid, labels, datasets, ytitle, horizontal=False, legend=True, ymax=None):
        ds = ",".join(f"{{label:{json.dumps(lab)},data:{json.dumps(data)},...bar({col})}}" for lab, data, col in datasets)
        axis_v, axis_c = ("x", "y") if horizontal else ("y", "x")
        mx = f",max:{ymax}" if ymax else ""
        ia = 'indexAxis:"y",' if horizontal else ""
        js.append(f"new Chart(document.getElementById('{cid}'),{{type:'bar',data:{{labels:{json.dumps(labels)},datasets:[{ds}]}},"
                  f"options:{{...base,{ia}plugins:{{legend:{{display:{str(legend).lower()}}}}},"
                  f"scales:{{{axis_c}:{{grid:{{display:false}},ticks:{{color:C.text}}}},{axis_v}:{{min:0{mx},title:{{display:true,text:{json.dumps(ytitle)}}}}}}}}}}});")

    def lines(cid, labels, datasets, ytitle):
        ds = ",".join(f"{{label:{json.dumps(lab)},data:{json.dumps(data)},...line({col})}}" for lab, data, col in datasets)
        js.append(f"new Chart(document.getElementById('{cid}'),{{type:'line',data:{{labels:{json.dumps(labels)},datasets:[{ds}]}},"
                  f"options:{{...base,spanGaps:true,plugins:{{legend:{{display:true}}}},scales:{{x:{{grid:{{display:false}}}},y:{{min:0,title:{{display:true,text:{json.dumps(ytitle)}}}}}}}}}}});")

    palette = ["C.blue", "C.blueLight", "C.muted", "C.grey2", "C.grey3", "C.grey4", "C.grey4"]
    gcol = {G: "C.red", **{g: palette[i] for i, g in enumerate(others)}}

    # overview
    ov = [box("rateChart", f"{ev['noun'].title()} rate by group and sex", "Victims per 100,000 residents per year, citywide. " + esc(R.get("population_note", "Population: ACS five-year estimates.")))]
    bars("rateChart", groups, [("Women", [rates[g]["F"] for g in groups], "C.red"), ("Men", [rates[g]["M"] for g in groups], "C.blue")], "Victims per 100,000 per year")
    share = R["sex_share"]
    if len(kinds) > 1:
        ov.append(box("shareChart", f"Share of victims who are {sexw}", f"{FL} are {share['all'][G]}% of {G} victims; other groups {min(share['all'][g] for g in others)}% to {max(share['all'][g] for g in others)}%."))
        bars("shareChart", groups, [(klabel(k), [share[k][g] for g in groups], c) for k, c in zip(kinds, ["C.red", "C.blue", "C.muted"])], f"{sexw.title()} as % of victims")
    else:
        yrs = list(R["by_year_counts"])
        ov.append(box("yearChart", "Victims by year", "Victims with known sex."))
        bars("yearChart", yrs, [("Women", [R["by_year_counts"][y]["F"] for y in yrs], "C.red"), ("Men", [R["by_year_counts"][y]["M"] for y in yrs], "C.blue")], "Victims")

    # tests
    tests, notes = [], []
    if "age" in T:
        a = T["age"]; std = a["standardized"]
        tests.append(box("ageChart", "Age", f"Does the gap survive comparing {sexw} of the same age? Standardized to one age mix, {FL}: {fmt(std[G])} against " + ", ".join(f"{fmt(std[g])} ({g})" for g in others) + "."))
        lines("ageChart", a["labels"], [(f"{g} {sexw}", a["rates"][g], gcol[g]) for g in groups], "Victims per 100,000 per year")
        notes.append(("Age", f"standardized, {FL} {fmt(std[G])} vs " + ", ".join(f"{fmt(std[g])} {g}" for g in others)))
    if "location" in T:
        L = T["location"]; rated = sorted([d for d in L["districts"] if d["ratio"]], key=lambda d: -d["ratio"])
        if rated:
            dl = cfg.get("districts", {}).get("label", "district")
            tests.append(box("locChart", "Location", f"Is it where {ev['plural']} happen? At other {sexw}'s rate in each {esc(dl)}, {FL} would be at {fmt(L['expected_if_other_rates'])} per 100,000 instead of {fmt(L['actual_rate'])}. Inside every rated {esc(dl)} the rate is still {x(rated[-1]['ratio'])} to {x(rated[0]['ratio'])} times other {sexw}'s.", tall=True))
            bars("locChart", [d["district"] for d in rated], [("Ratio", [d["ratio"] for d in rated], "C.red")], f"{p.focus_label}'s rate as a multiple of other {sexw}'s", horizontal=True, legend=False)
            notes.append(("Location", f"{fmt(L['expected_if_other_rates'])} expected vs {fmt(L['actual_rate'])} actual; {x(rated[-1]['ratio'])} to {x(rated[0]['ratio'])} times inside every rated {dl}"))
    if len(kinds) > 1:
        ty = T["type"]
        tests.append(box("typeChart", "Type", "Rates by type: " + "; ".join(f"{klabel(k).lower()} {x(ty[k][G] / ty[k][ranked[0]])} times {ranked[0]} {sexw}" for k in kinds if ty[k][ranked[0]]) + "."))
        bars("typeChart", groups, [(klabel(k), [ty[k][g] for g in groups], c) for k, c in zip(kinds, ["C.red", "C.blue", "C.muted"])], "Victims per 100,000 per year")
        notes.append(("Type", "; ".join(f"{klabel(k).lower()} " + ", ".join(f"{x(ty[k][G] / ty[k][g])} times {g}" for g in ranked if ty[k][g]) for k in kinds)))
    tm = T["time"]; ys = list(tm)
    if len(ys) > 1:
        tests.append(box("timeChart", "Time", f"Does it persist? {FL}: " + ", ".join(fmt(tm[y][G]) for y in ys) + f" per 100,000, {ys[0]} to {ys[-1]}."))
        lines("timeChart", ys, [(f"{g} {sexw}", [tm[y][g] for y in ys], gcol[g]) for g in groups], "Victims per 100,000 per year")
        notes.append(("Time", ", ".join(fmt(tm[y][G]) for y in ys)))
    if "premises" in T:
        pr = T["premises"]
        tests.append(box("premChart", "Premises", f"Different places? Share of each group's {ev['plural']}, top premises for {FL}.", tall=True))
        bars("premChart", pr["labels"], [(p.focus_label, pr["focus"], "C.red"), (f"Other {sexw}", pr["other"], "C.blue")], "% of victims", horizontal=True)
        notes.append(("Premises", ", ".join(f"{pr['labels'][i]} {pr['focus'][i]}% vs {pr['other'][i]}%" for i in range(min(3, len(pr["labels"])))) + f" ({FL} vs other {sexw})"))
    if "weapons" in T:
        wp = T["weapons"]
        tests.append(box("weapChart", "Weapons", f"Different circumstances? Firearm or not, by share of each group's {ev['plural']}." if "Firearm" not in wp["labels"] else
                         f"A firearm in {wp['focus'][wp['labels'].index('Firearm')]}% of {ev['plural']} on {FL} against {wp['other'][wp['labels'].index('Firearm')]}% for other {sexw}."))
        bars("weapChart", wp["labels"], [(p.focus_label, wp["focus"], "C.red"), (f"Other {sexw}", wp["other"], "C.blue")], "% of victims")
        notes.append(("Weapons", ", ".join(f"{lab} {f}% vs {o}%" for lab, f, o in zip(wp["labels"], wp["focus"], wp["other"])) + f" ({FL} vs other {sexw})"))
    for flag, fl in T.get("flags", {}).items():
        lab = cfg["flags"][flag].get("label", flag.title())
        r_f, r_u = fl["ratios"]["flagged"], fl["ratios"]["unflagged"]
        tests.append(box(f"flag_{flag}", lab, f"{lab} is {fl['share'][G]}% of {ev['plural']} on {FL} (" + ", ".join(f"{fl['share'][g]}% {g}" for g in others) +
                         f"). Ratio to {ranked[0]} {sexw}: {x(r_f.get(ranked[0], 0))} with, {x(r_u.get(ranked[0], 0))} without."))
        bars(f"flag_{flag}", groups, [(lab, [fl["rates"]["flagged"][g] for g in groups], "C.blue"), (f"Not {lab.lower()}", [fl["rates"]["unflagged"][g] for g in groups], "C.red")], "Victims per 100,000 per year")
        notes.append((lab, f"{fl['share'][G]}% of {ev['plural']} on {FL} (" + ", ".join(f"{fl['share'][g]}% {g}" for g in others) + "); ratios with it "
                      + ", ".join(f"{x(r_f[g])}x {g}" for g in ranked if g in r_f) + "; without it " + ", ".join(f"{x(r_u[g])}x {g}" for g in ranked if g in r_u)))
    COLOR = {"red": "C.red", "blue": "C.blue", "blueLight": "C.blueLight", "muted": "C.muted", "grey2": "C.grey2"}

    def extra_box(b):
        ds = [(d["label"], d["data"], COLOR.get(d.get("color", "blue"), "C.blue")) for d in b["datasets"]]
        if b.get("kind", "bar") == "line":
            lines(b["id"], b["labels"], ds, b.get("ytitle", ""))
        else:
            bars(b["id"], b["labels"], ds, b.get("ytitle", ""), horizontal=b.get("horizontal", False), legend=b.get("legend", True))
        return box(b["id"], b["title"], esc(b["text"]), tall=b.get("tall", False))
    tests += [extra_box(b) for b in extra.get("tests", [])]
    notes += [tuple(n) for n in extra.get("readme", [])]
    rp = extra.get("reporting", {"title": "Reporting: not testable here", "text": f"Police data holds only what was reported. If {p.focus_label} report more or less often than other {sexw}, every rate here moves, and this data cannot say which way."})
    reporting = f'<div class="finding"><h4>{esc(rp["title"])}</h4><p>{esc(rp["text"])}</p></div>'
    sections_html = "".join(
        f'<div class="section-title">{esc(sec["title"])}</div>' + (f'<p class="note">{esc(sec["note"])}</p>' if sec.get("note") else "")
        + grid([extra_box(b) for b in sec.get("boxes", [])])
        + ('<div class="findings">' + "".join(f'<div class="finding{" red" if f.get("red") else ""}"><h4>{esc(f["title"])}</h4><p>{esc(f["text"])}</p></div>' for f in sec.get("findings", [])) + "</div>" if sec.get("findings") else "")
        for sec in extra.get("sections", []))

    # model
    model_html = ""
    if M:
        lad = M["ladder"]; fin = lad[-1]
        mc = [box("ladderChart", "Surviving rate ratio", f"Crude {lad[0]['rate_ratio']}x. Fully adjusted {fin['rate_ratio']}x (95% CI {fin['ci_low']} to {fin['ci_high']}). " + (f"Controls account for about {M['explained_pct']}% of the excess." if M['explained_pct'] is None or M['explained_pct'] >= 0 else "The controls widen the gap rather than narrow it."), tall=True)]
        bars("ladderChart", [m["model"] for m in lad], [("Rate ratio", [m["rate_ratio"] for m in lad], "C.red")], f"{p.focus_label}'s rate as a multiple of other {sexw}'s", horizontal=True, legend=False)
        if len(M["by_kind"]) > 1:
            mc.append(box("kindModel", "Fully adjusted, by type", "The same controls, run separately: " + "; ".join(f"{klabel(k).lower()} {v['rate_ratio']}x" for k, v in M["by_kind"].items()) + "."))
            bars("kindModel", [klabel(k) for k in M["by_kind"]], [("Adjusted", [v["rate_ratio"] for v in M["by_kind"].values()], "C.red")], "Adjusted rate ratio", legend=False)
        pw = M["pairwise"]
        mc.append(box("pairModel", "Each comparison group on its own", "Fully adjusted: " + ", ".join(f"{pw[g]['adjusted']['rate_ratio']}x {g} {sexw}" for g in pw) + ". Crude in grey."))
        bars("pairModel", [f"{g} {sexw}" for g in pw], [("Crude", [pw[g]["crude"]["rate_ratio"] for g in pw], "C.muted"), ("Fully adjusted", [pw[g]["adjusted"]["rate_ratio"] for g in pw], "C.red")], "Rate ratio")
        if M["sensitivity"]:
            sv = M["sensitivity"]
            mc.append(box("sensModel", "Does the covariate definition matter?", "Swapping definitions: " + ", ".join(f"{k.replace('_', ' ')} {v['rate_ratio']}x" for k, v in sv.items()) + "."))
            bars("sensModel", [k.replace("_", " ") for k in sv], [("Adjusted", [v["rate_ratio"] for v in sv.values()], "C.red")], "Adjusted rate ratio", legend=False)
        model_html = (f'<div class="section-title">The model</div><p class="note">How much of the gap survives adjustment, one control at a time. Poisson rate models at the census-tract level, '
                      f'{sexw} only, {fmt(M["cells"])} tract by group by age by year cells, covering {M["coverage"]["focus"]}% of located {FL} victims and {M["coverage"]["other"]}% of other {sexw}.</p>'
                      + grid(mc) +
                      f'<div class="findings"><div class="finding red"><h4>What the model says</h4><p>A {fin["rate_ratio"]}-fold gap remains after every measured control. {f"About {M['explained_pct']}% of the crude excess is explained." if M["explained_pct"] is None or M["explained_pct"] >= 0 else f"None of the crude excess is explained: adjusted, the gap is wider than the crude {lad[0]['rate_ratio']}x."}</p></div>'
                      f'<div class="finding"><h4>What it cannot say</h4><p>Residents are the denominator, so exposure away from home is unmeasured. Reporting behavior is invisible to police data. Nothing here measures offenders or circumstances.</p></div></div>')

    # replication
    rep_html = ""
    if rep and rep["comparison"]:
        cat = "all" if "all" in rep["comparison"] else next(iter(rep["comparison"]))
        cmp = rep["comparison"][cat]; srcs = list(rep["sources"].values())
        rep_html = (f'<div class="section-title">Replication</div><p class="note">{esc(rep.get("definition_notes", ""))}</p>' +
                    grid([box("repChart", "Does it hold in a second source?", f"{FL}'s rate as a multiple of each group's: " +
                              "; ".join(f"{g} {sexw} {v['first']}x then {v['second']}x" for g, v in cmp.items()) + ".")]))
        bars("repChart", [f"{g} {sexw}" for g in cmp], [(srcs[0]["label"], [v["first"] for v in cmp.values()], "C.muted"), (srcs[1]["label"], [v["second"] for v in cmp.values()], "C.red")], "Rate ratio")

    # caveats
    c = R["counts"]
    cav = [("This shows what, not why", f"The data says the reported {ev['noun']} rate for {p.focus_label} is higher. It does not say why. Nothing here measures causes, offenders or circumstances."),
           ("Reported crimes only", "Every number is a report that reached the police. Willingness to report, and recording practice, differ by group, area and time."),
           ("Reports, not people", f"Rates count reports. Someone {ev['verb']} twice counts twice, so a rate is not the share of people {ev['verb']}."),
           ("Exposure is not population", "Rates divide by where people live, not where they spend time.")]
    if "race_coding_bound" in R:
        b = R["race_coding_bound"]
        cav.append(("Who is recorded as " + G, f"Race is recorded by officers; the population counts people who are {G} alone. There are {round((b['combo_ratio'] - 1) * 100)}% more people who are {G} alone or in combination. "
                    f"If multiracial victims are recorded as {G}, the worst case is " + ", ".join(f"{v}x {g}" for g, v in b["ratios_worst_case"].items()) + " instead of " + ", ".join(f"{ratios[g]}x" for g in b["ratios_worst_case"]) + "."))
    pop = p.read_json("population.json")
    if pop.get("hispanic_overlap", {}).get(G):
        cav.append(("Overlapping groups", f"{pop['hispanic_overlap'][G]}% of {G} residents are also Hispanic, so they sit in both denominators."))
    cav.append(("Who is counted", f"{fmt(c['total'] - c['known'])} victims are left out of the rates: their sex is unknown, or their race is unknown or outside the compared groups. "
                f"Race is unknown or outside the groups for {c['unknown_race_share_by_sex']['F']}% of women and {c['unknown_race_share_by_sex']['M']}% of men."))
    cav += [(h, t) for h, t in cfg.get("extra_caveats", [])]
    cav_html = '<div class="section-title">Caveats</div><div class="findings">' + "".join(f'<div class="finding red"><h4>{esc(h)}</h4><p>{esc(t)}</p></div>' for h, t in cav) + "</div>"

    cards = [("Victims", fmt(c["total"]), f"{start} to {end}")] + [(klabel(k), fmt(c["by_kind"][k]), "") for k in kinds if len(kinds) > 1] + \
            [("Known race and sex", fmt(c["known"]), "used for rates")] + ([("Districts", c["districts"], "")] if c.get("districts") else []) + \
            ([("Median victim age", int(c["median_age"]), "")] if c.get("median_age") else [])
    cards_html = '<div class="cards">' + "".join(f'<div class="card"><div class="label">{esc(l)}</div><div class="value">{v}</div><div class="sub">{esc(s)}</div></div>' for l, v, s in cards) + "</div>"
    links = cfg.get("links", {})
    nav = "".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in [("Code", links.get("code")), (links.get("home", "").replace("https://", ""), links.get("home"))] if u)
    sources = "; ".join(esc(s) for s in cfg.get("sources", []))

    page = TEMPLATE.format(
        title=esc(cfg["title"]), description=esc(lede), lede=esc(lede), author=esc(cfg.get("author", "")), window=f"{start} to {end}", total=fmt(c["total"]),
        nav=nav, question=esc(question), answer="".join(f"<p>{esc(a)}</p>" for a in answer), cards=cards_html, overview=grid(ov),
        tests=grid(tests), reporting=f'<div class="findings">{reporting}</div>' + sections_html, model=model_html, replication=rep_html, caveats=cav_html,
        method=f'Rates are victims per 100,000 residents per year over {R["years"]} years; cells under {fmt(p.min_pop)} residents are not rated. {sexw.title()} only from the tests onward. Sources: {sources}.',
        js="\n  ".join(js), groups_n=fmt(T["n"]["focus"]), others_n=fmt(T["n"]["other"]), focus=FL, sexw=sexw, sexw_cap=sexw.capitalize())
    for bad in ["—", "–"]:
        page = page.replace(bad, ", " if bad == "—" else " to ")
    out = p.root / cfg.get("page_path", "index.html")
    out.write_text(page)
    print("wrote", out)
    readme(p, R, rep, lede, notes, cav)


def grid(boxes):
    return f'<div class="charts section-end">{"".join(boxes)}</div>' if boxes else ""


def readme(p, R, rep, lede, notes, cav):
    G, S = p.focus
    sexw = SEX_WORD[S][0]
    L = ["<!-- results:start -->", f"**{lede}**", "", f"Rates per 100,000 residents a year, {p.cfg['window']['start']} to {p.cfg['window']['end']}:", "",
         "| Group | Women | Men |", "|---|---|---|"] + [f"| {g} | {fmt(R['rates'][g]['F'])} | {fmt(R['rates'][g]['M'])} |" for g in p.groups]
    L += ["", "Tests:", ""] + [f"- {k}: {v}." for k, v in notes]
    if "model" in R:
        L += ["", f"| Controls | {p.focus_label} vs other {sexw} |", "|---|---|"] + [f"| {m['model']} | {m['rate_ratio']}x |" for m in R["model"]["ladder"]]
        L += ["", "Pairwise, fully adjusted: " + ", ".join(f"{g} {v['adjusted']['rate_ratio']}x" for g, v in R["model"]["pairwise"].items()) + "."]
    if rep and rep["comparison"]:
        cat = "all" if "all" in rep["comparison"] else next(iter(rep["comparison"]))
        L += ["", "Replication: " + "; ".join(f"{g} {v['first']}x then {v['second']}x" for g, v in rep["comparison"][cat].items()) + "."]
    L += ["", "Caveats:", ""] + [f"- {h}: {t}" for h, t in cav] + ["<!-- results:end -->"]
    block = "\n".join(L)
    rd = p.root / "README.md"
    if rd.exists() and "<!-- results:start -->" in rd.read_text():
        s = rd.read_text()
        a, b = s.index("<!-- results:start -->"), s.index("<!-- results:end -->") + len("<!-- results:end -->")
        rd.write_text(s[:a] + block + s[b:])
        print("updated README results block")
    else:
        (p.out / "README_results.md").write_text(block)
        print("wrote", p.out / "README_results.md", "(paste into README.md, between the markers it carries)")


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <meta name="description" content="{description}" />
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{ --bg: #0a0a0a; --surface: #121212; --border: #262626; --text: #f2f2f2; --muted: #8b8b8b; --blue: #2563eb; --blue-light: #93c5fd; --red: #ef4444; }}
    body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; font-size: 14px; line-height: 1.6; }}
    a {{ color: var(--blue-light); text-decoration: none; }} a:hover {{ text-decoration: underline; }}
    header {{ border-bottom: 1px solid var(--border); padding: 28px 40px; display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; flex-wrap: wrap; }}
    header h1 {{ font-size: 22px; font-weight: 600; letter-spacing: -0.3px; }}
    header p {{ color: var(--muted); margin-top: 4px; font-size: 13px; }}
    header nav {{ display: flex; gap: 20px; font-size: 13px; }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 32px 40px; }}
    .lede {{ font-size: 21px; font-weight: 600; line-height: 1.4; letter-spacing: -0.2px; max-width: 860px; margin-bottom: 24px; }}
    .answer {{ background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--red); border-radius: 0 8px 8px 0; padding: 20px 24px; margin-bottom: 40px; max-width: 860px; }}
    .answer .q {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); margin-bottom: 8px; font-weight: 600; }}
    .answer .question {{ font-size: 15px; line-height: 1.7; margin-bottom: 8px; }}
    .answer p {{ font-size: 14px; line-height: 1.7; color: var(--muted); }}
    .answer p + p {{ margin-top: 8px; }}
    .section-title {{ font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }}
    .note {{ font-size: 12px; color: var(--muted); margin: -6px 0 16px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 16px; }}
    .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 18px 20px; }}
    .card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); margin-bottom: 8px; }}
    .card .value {{ font-size: 26px; font-weight: 700; line-height: 1; }}
    .card .sub {{ font-size: 11px; color: var(--muted); margin-top: 5px; }}
    .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
    .section-end {{ margin-bottom: 40px; }}
    .chart-box {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 22px 24px; min-width: 0; }}
    .chart-box h3 {{ font-size: 13px; font-weight: 600; margin-bottom: 4px; }}
    .chart-sub {{ font-size: 11px; color: var(--muted); margin-bottom: 16px; line-height: 1.5; }}
    .chart-wrap {{ position: relative; height: 270px; }}
    .chart-wrap.tall {{ height: 360px; }}
    .findings {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 40px; }}
    .finding {{ background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--blue); border-radius: 0 8px 8px 0; padding: 16px 20px; }}
    .finding.red {{ border-left-color: var(--red); }}
    .finding h4 {{ font-size: 13px; font-weight: 600; margin-bottom: 4px; }}
    .finding p {{ font-size: 12px; color: var(--muted); line-height: 1.5; }}
    footer {{ border-top: 1px solid var(--border); padding: 20px 40px; color: var(--muted); font-size: 12px; }}
    @media (max-width: 768px) {{
      header, footer {{ padding: 20px 16px; }} .container {{ padding: 20px 16px; }}
      .charts, .findings {{ grid-template-columns: 1fr; }}
      .chart-box {{ padding: 18px 16px; }} .lede {{ font-size: 18px; }}
    }}
  </style>
</head>
<body>
<header>
  <div><h1>{title}</h1><p>{window} · {total} victims · {author}</p></div>
  <nav>{nav}</nav>
</header>
<div class="container">
  <p class="lede">{lede}</p>
  <div class="answer"><div class="q">The question</div><div class="question">{question}</div>{answer}</div>
  <div class="section-title">Dataset</div>
  {cards}
  {overview}
  <div class="section-title">Testing explanations</div>
  <p class="note">{sexw_cap} only ({groups_n} {focus}, {others_n} other {sexw}). Each cut asks whether a plain explanation accounts for the gap.</p>
  {tests}
  {reporting}
  {model}
  {replication}
  {caveats}
  <div class="section-title">Method</div>
  <p class="note">{method}</p>
</div>
<footer>{title} · {author}</footer>
<script>
  const C = {{ blue: '#2563eb', blueLight: '#93c5fd', red: '#ef4444', text: '#f2f2f2', muted: '#8b8b8b', grey2: '#6b7280', grey3: '#4b5563', grey4: '#374151', border: '#262626', surface: '#121212' }};
  Chart.defaults.color = C.muted; Chart.defaults.borderColor = C.border;
  Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif'; Chart.defaults.font.size = 11;
  Object.assign(Chart.defaults.plugins.tooltip, {{ backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, titleColor: C.text, bodyColor: C.muted }});
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  const base = {{ responsive: true, maintainAspectRatio: false }};
  const bar = c => ({{ backgroundColor: 'transparent', borderColor: c, borderWidth: 1.5, borderRadius: 3, borderSkipped: false }});
  const line = c => ({{ borderColor: c, backgroundColor: c, borderWidth: 1.5, pointRadius: 2.5, tension: 0.25, fill: false }});
  {js}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
