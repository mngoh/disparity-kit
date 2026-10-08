"""Map: every neighborhood shaded by how long its calls waited against the city, with a switch between tiers of calls.

  python map.py response.json      ->  <project>/map.html (standalone) and out/map_fragment.html (to embed)

For each call from the public with an arrival (main periods), the wait is divided by the citywide median wait
for the same call type and priority. A neighborhood's value is the median of those ratios: "+30%" means its
calls typically waited 30% longer than the city's typical wait for the same kind of call. Tiers (config
"map.tiers") pool priorities, for example urgent (Critical, Serious) and less urgent (the rest); each call
is still compared with its own call type. A switch flips between tiers; map.html#<tier> opens on one.

Neighborhoods are shaded (no overlapping marks), boroughs or other config "area" groups are outlined and named,
and areas without residents are left plain. Text sizes and colors use the host page's tokens (--rc-fs-small,
--rc-text and the rest) when it has them; area names are rescaled to stay at the small text size at any map width. Colors are a diverging scale (shorter in blue, longer in red,
within 10% gray) in log-symmetric bins, checked with the dataviz palette validator on the dark page: every
adjacent pair clears color-blind and normal-vision separation.
"""
import html
import json
import math
import sys

import pandas as pd
from shapely.geometry import shape
from shapely.ops import unary_union

from rt import RTProject

BINS = [(-1e9, -1 / 3, "33% or more shorter", "#9cc8ff"), (-1 / 3, -0.2, "20 to 33% shorter", "#4f8ae6"),
        (-0.2, -0.1, "10 to 20% shorter", "#34598f"), (-0.1, 0.1, "Within 10%", "#3a3a38"),
        (0.1, 0.25, "10 to 25% longer", "#93423a"), (0.25, 0.5, "25 to 50% longer", "#ec6556"),
        (0.5, 1e9, "50% or more longer", "#ffb3a8")]
NO_RESIDENTS = "#1c1c1c"
W = 760


def path_d(s, px):
    polys = s.geoms if s.geom_type == "MultiPolygon" else [s]
    out = ""
    for poly in polys:
        for ring in [poly.exterior] + list(poly.interiors):
            out += "M" + "L".join(f"{a},{b}" for a, b in (px(*c) for c in ring.coords)) + "Z"
    return out


def main():
    p = RTProject(sys.argv[1])
    cfg = p.cfg
    m = cfg.get("map", {})
    tiers = m.get("tiers") or {"all": {"label": "All calls", "priorities": [x["value"] for x in p.priorities]}}
    periods = "', '".join(cfg["analysis"]["main_periods"])
    origins = "', '".join(cfg["analysis"].get("origins", ["public"]))
    years = cfg["analysis"].get("main_years", 1)
    case = " ".join("WHEN c.priority IN (" + ", ".join(f"'{v}'" for v in t["priorities"]) + f") THEN '{k}'" for k, t in tiers.items())
    con = p.con()
    con.execute(f"""CREATE TEMP VIEW x AS SELECT c.priority, c.call_type, c.wait, pt.nbhd, CASE {case} END AS tier
        FROM read_parquet('{p.calls}') c JOIN read_parquet('{p.data / 'points.parquet'}') pt USING (lat, lon, district)
        WHERE c.origin IN ('{origins}') AND c.period IN ('{periods}') AND c.wait > 0 AND c.wait <= 1440""")
    con.execute("CREATE TEMP TABLE base AS SELECT priority, call_type, median(wait) AS m FROM x GROUP BY 1, 2")
    d = con.execute("""SELECT x.tier, x.nbhd, count(*) AS n, median(x.wait) AS med, median(x.wait / b.m) - 1 AS idx
        FROM x JOIN base b USING (priority, call_type) WHERE x.tier IS NOT NULL GROUP BY 1, 2""").df()
    nb = pd.read_csv(p.data / "neighborhoods.csv", dtype={"id": str})
    d = d.merge(nb, left_on="nbhd", right_on="id")
    d = d[d["in_groups"]]
    d.to_csv(p.out / "map_index.csv", index=False)

    g = cfg["geo"]["neighborhoods"]
    feats = json.loads((p.cache / "neighborhoods.geojson").read_text())["features"]
    shapes = {str(f["properties"][g["id"]]): shape(f["geometry"]).simplify(0.0003) for f in feats}
    areas = {str(f["properties"][g["id"]]): f["properties"].get(g.get("area", ""), "") for f in feats}
    b = [s.bounds for s in shapes.values()]
    x0, y0, x1, y1 = min(t[0] for t in b), min(t[1] for t in b), max(t[2] for t in b), max(t[3] for t in b)
    k = math.cos(math.radians((y0 + y1) / 2))
    sc = W / ((x1 - x0) * k)
    H = round((y1 - y0) * sc)
    px = lambda lon, lat: (round((lon - x0) * k * sc, 1), round((y1 - lat) * sc, 1))
    nbhd_paths = {i: path_d(s, px) for i, s in shapes.items()}
    outlines, labels_xy = [], []
    for area in sorted(set(areas.values()) - {""}):
        u = unary_union([s.buffer(0.0002) for i, s in shapes.items() if areas[i] == area]).buffer(-0.0002)
        outlines.append(path_d(u.simplify(0.0004), px))
        pt = m.get("label_points", {}).get(area)
        x, y = px(*pt) if pt else px(*u.representative_point().coords[0])
        labels_xy.append((area, x, y))
    data = {}
    for tier, t in d.groupby("tier"):
        data[tier] = {r.nbhd: {"name": r.name, "area": r.area, "n": round(r.n / years), "med": round(r.med, 1), "idx": round(r.idx, 3),
                               "income": int(r.median_income) if r.median_income == r.median_income else None,
                               "capped": bool(r.income_capped), "makeup": r.makeup} for r in t.itertuples()}
    order = list(tiers)
    tier_labels = {k: v["label"] for k, v in tiers.items()}
    notes = {k: v.get("note", "") for k, v in tiers.items()}
    makeup = cfg["analysis"]["groupings"]["makeup"].get("level_labels", {})
    frag = fragment(nbhd_paths, outlines, labels_xy, data, order, tier_labels, notes, H, makeup, m)
    (p.out / "map_fragment.html").write_text(frag)
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(m.get('title', 'Response times by neighborhood'))}</title>
<style>body{{margin:0;background:#0b0b0b;color:#f2f2f2;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}
main{{max-width:{W}px;margin:0 auto;padding:24px 16px}}</style></head>
<body><main>{frag}</main></body></html>"""
    out = p.root / m.get("path", "map.html")
    out.write_text(page)
    print("wrote", out, "and", p.out / "map_fragment.html")


def fragment(nbhd_paths, outlines, labels_xy, data, order, tier_labels, notes, H, makeup, m):
    legend = "".join(f'<span class="rcm-key"><i style="background:{c}"></i>{html.escape(lab)}</span>' for _, _, lab, c in BINS)
    legend += f'<span class="rcm-key"><i style="background:repeating-linear-gradient(45deg,#2e2e2e 0 1.5px,{NO_RESIDENTS} 1.5px 4px)"></i>No residents (parks, airports)</span>'
    buttons = "".join(f'<button type="button" data-tier="{k}" aria-pressed="{"true" if i == 0 else "false"}">{html.escape(tier_labels[k])}</button>'
                      for i, k in enumerate(order))
    shapes_svg = "".join(f'<path data-id="{i}" d="{dd}"/>' for i, dd in nbhd_paths.items())
    outline_svg = "".join(f'<path d="{dd}"/>' for dd in outlines)
    label_svg = "".join(f'<text x="{x}" y="{y}">{html.escape(a)}</text>' for a, x, y in labels_xy)
    return f"""<figure class="rcm" id="rcm">
<style>
.rcm{{margin:32px 0;font-family:var(--rc-sans,-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif);font-size:var(--rc-fs-small,14px);color:var(--rc-text-2,#c4c4c4)}}
.rcm figcaption strong{{display:block;font-size:var(--rc-fs-body,18px);color:var(--rc-text,#f2f2f2);margin-bottom:4px}}
.rcm .rcm-tabs{{display:flex;gap:8px;margin:14px 0 10px;flex-wrap:wrap}}
.rcm .rcm-tabs button{{font:inherit;color:var(--rc-text,#f2f2f2);background:transparent;border:1px solid var(--rc-rule-strong,#4a4a4a);border-radius:4px;padding:7px 14px;cursor:pointer}}
.rcm .rcm-tabs button[aria-pressed="true"]{{background:var(--rc-text,#f2f2f2);color:var(--rc-bg,#0b0b0b);border-color:var(--rc-text,#f2f2f2)}}
.rcm .rcm-note{{margin:0 0 8px;color:var(--rc-text-3,#9a9a9a)}}
.rcm .rcm-wrap{{position:relative}}
.rcm svg{{display:block;width:100%;height:auto}}
.rcm .rcm-n path{{fill:url(#rcm-hatch);stroke:var(--rc-bg,#0b0b0b);stroke-width:.7;stroke-linejoin:round}}
.rcm .rcm-n path.has{{cursor:pointer}}
.rcm .rcm-n path.on{{stroke:var(--rc-text,#f2f2f2);stroke-width:2}}
.rcm .rcm-b path{{fill:none;stroke:#bdbdb8;stroke-width:1.4;stroke-linejoin:round;pointer-events:none}}
.rcm .rcm-l text{{font-size:var(--rc-fs-small,14px);font-weight:700;fill:var(--rc-text,#f2f2f2);stroke:var(--rc-bg,#0b0b0b);stroke-width:4px;paint-order:stroke;text-anchor:middle;pointer-events:none}}
.rcm .rcm-legend{{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:10px}}
.rcm .rcm-key{{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}}
.rcm .rcm-key i{{width:12px;height:12px;border-radius:2px;display:inline-block;box-sizing:border-box}}
.rcm .rcm-tip{{position:absolute;pointer-events:none;background:var(--rc-raised,#161616);border:1px solid #3a3a3a;border-radius:4px;padding:8px 10px;color:var(--rc-text,#f2f2f2);line-height:1.45;max-width:240px;display:none;z-index:2}}
.rcm .rcm-tip b{{display:block}}
.rcm .rcm-source{{color:var(--rc-text-3,#9a9a9a);margin:10px 0 0}}
.rcm details{{margin-top:10px}} .rcm summary{{cursor:pointer;font-weight:700;color:var(--rc-text-2,#c4c4c4)}}
.rcm table{{border-collapse:collapse;width:100%;margin-top:8px}} .rcm th,.rcm td{{text-align:left;padding:4px 8px;border-bottom:1px solid #262626}}
.rcm .rcm-scroll{{max-height:320px;overflow:auto}}
</style>
<figcaption><strong>{html.escape(m.get('title', 'How long calls waited, by neighborhood'))}</strong>{html.escape(m.get('subtitle', ''))}</figcaption>
<div class="rcm-tabs" role="group" aria-label="Calls shown">{buttons}</div>
<p class="rcm-note" id="rcm-note"></p>
<div class="rcm-wrap"><svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(m.get('aria', 'Map of neighborhoods'))}"><defs><pattern id="rcm-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="{NO_RESIDENTS}"/><line x1="0" y1="0" x2="0" y2="5" stroke="#2e2e2e" stroke-width="1.5"/></pattern></defs><g class="rcm-n" id="rcm-n">{shapes_svg}</g><g class="rcm-b">{outline_svg}</g><g class="rcm-l">{label_svg}</g></svg><div class="rcm-tip" id="rcm-tip"></div></div>
<div class="rcm-legend">{legend}</div>
<p class="rcm-source">{html.escape(m.get('source', ''))}</p>
<details><summary>Show the numbers</summary><div class="rcm-scroll"><table id="rcm-table"></table></div></details>
<script>(function(){{
var D={json.dumps(data, separators=(',', ':'))}, N={json.dumps(notes)}, MK={json.dumps(makeup)};
var B={json.dumps([[lo, hi, c] for lo, hi, _, c in BINS])}, t0={json.dumps(order[0])}, cur;
function col(v){{for(var i=0;i<B.length;i++)if(v>=B[i][0]&&v<B[i][1])return B[i][2];return B[B.length-1][2];}}
function vs(v){{var p=Math.abs(Math.round(v*100));return p===0?'Same as the city':p+'% '+(v>0?'longer':'shorter')+' than the city';}}
var paths=document.querySelectorAll('#rcm-n path'),tip=document.getElementById('rcm-tip'),wrap=tip.parentNode;
var svg=wrap.querySelector('svg'),labels=svg.querySelectorAll('.rcm-l text'),fs=parseFloat(getComputedStyle(document.getElementById('rcm')).fontSize);
function fit(){{var w=svg.getBoundingClientRect().width;if(!w)return;var k={W}/w;labels.forEach(function(t){{t.style.fontSize=fs*k+'px';t.style.strokeWidth=4*k+'px';}});}}
fit();window.addEventListener('resize',fit);
function tipHtml(d){{return '<b>'+d.name+'</b>'+d.area+'<br>Median wait: '+d.med.toFixed(1)+' min<br>'+vs(d.idx)+' for the same call types<br>'+d.n.toLocaleString('en-US')+' calls a year<br>Median household income: '+(d.income?'$'+d.income.toLocaleString('en-US')+(d.capped?' or more':''):'n/a')+'<br>'+(MK[d.makeup]||d.makeup||'');}}
function hide(){{tip.style.display='none';var o=document.querySelector('#rcm-n .on');if(o)o.classList.remove('on');}}
function show(e,el){{var d=D[cur][el.dataset.id];if(!d){{hide();return;}}tip.innerHTML=tipHtml(d);tip.style.display='block';var r=wrap.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
 tip.style.left=Math.min(Math.max(0,x+12),r.width-tip.offsetWidth)+'px';tip.style.top=Math.max(0,y-tip.offsetHeight-12)+'px';
 var o=document.querySelector('#rcm-n .on');if(o&&o!==el)o.classList.remove('on');el.classList.add('on');el.parentNode.appendChild(el);}}
paths.forEach(function(el){{el.addEventListener('mousemove',function(e){{show(e,el);}});el.addEventListener('click',function(e){{show(e,el);e.stopPropagation();}});el.addEventListener('mouseleave',hide);}});
function draw(t){{cur=t;hide();paths.forEach(function(el){{var d=D[t][el.dataset.id];el.style.fill=d?col(d.idx):'';el.classList.toggle('has',!!d);}});
 document.getElementById('rcm-note').textContent=N[t]||'';
 var rows=Object.values(D[t]).sort(function(a,b){{return b.idx-a.idx;}});
 document.getElementById('rcm-table').innerHTML='<tr><th>Neighborhood</th><th>Median wait</th><th>Against the city</th><th>Calls a year</th></tr>'+rows.map(function(d){{return '<tr><td>'+d.name+'</td><td>'+d.med.toFixed(1)+' min</td><td>'+vs(d.idx)+'</td><td>'+d.n.toLocaleString('en-US')+'</td></tr>';}}).join('');
 document.querySelectorAll('#rcm .rcm-tabs button').forEach(function(b){{b.setAttribute('aria-pressed',b.dataset.tier===t?'true':'false');}});}}
document.querySelectorAll('#rcm .rcm-tabs button').forEach(function(b){{b.addEventListener('click',function(){{draw(b.dataset.tier);}});}});
document.addEventListener('click',hide);
draw(D[location.hash.slice(1)]?location.hash.slice(1):t0);
}})();</script>
</figure>"""


if __name__ == "__main__":
    main()
