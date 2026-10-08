"""Bubble map: one bubble per neighborhood, sized by calls, colored by how long they waited against the city.

  python map.py response.json      ->  <project>/map.html (standalone) and out/map_fragment.html (to embed)

For each call from the public with an arrival (main periods), the wait is divided by the citywide median wait
for the same call type and priority. A neighborhood's value is the median of those ratios: "+30%" means its
calls typically waited 30% longer than the city's typical wait for the same kind of call. Tiers (config
"map.tiers") pool priorities, for example urgent (Critical, Serious) and less urgent (the rest); each call
is still compared with its own call type. Bubble area is calls a year. A switch flips between tiers.
Colors are a diverging scale (shorter in blue, longer in red, within 10% gray) in log-symmetric bins, checked with
the dataviz palette validator on the dark page: every adjacent pair clears color-blind and normal-vision separation.
"""
import html
import json
import math
import sys

import pandas as pd
from shapely.geometry import shape

from rt import RTProject

BINS = [(-1e9, -1 / 3, "33% or more shorter", "#9cc8ff"), (-1 / 3, -0.2, "20 to 33% shorter", "#4f8ae6"),
        (-0.2, -0.1, "10 to 20% shorter", "#34598f"), (-0.1, 0.1, "Within 10%", "#3a3a38"),
        (0.1, 0.25, "10 to 25% longer", "#93423a"), (0.25, 0.5, "25 to 50% longer", "#ec6556"),
        (0.5, 1e9, "50% or more longer", "#ffb3a8")]
W = 760


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

    # geometry: neighborhood shapes for the base, a point inside each for the bubble
    g = cfg["geo"]["neighborhoods"]
    feats = json.loads((p.cache / "neighborhoods.geojson").read_text())["features"]
    shapes = {str(f["properties"][g["id"]]): shape(f["geometry"]) for f in feats}
    b = [s.bounds for s in shapes.values()]
    x0, y0, x1, y1 = min(t[0] for t in b), min(t[1] for t in b), max(t[2] for t in b), max(t[3] for t in b)
    k = math.cos(math.radians((y0 + y1) / 2))
    sc = W / ((x1 - x0) * k)
    H = round((y1 - y0) * sc)
    px = lambda lon, lat: (round((lon - x0) * k * sc, 1), round((y1 - lat) * sc, 1))
    paths = []
    for i, s in shapes.items():
        s = s.simplify(0.0004)
        polys = s.geoms if s.geom_type == "MultiPolygon" else [s]
        dd = ""
        for poly in polys:
            pts = [px(*c) for c in poly.exterior.coords]
            dd += "M" + "L".join(f"{a},{c}" for a, c in pts) + "Z"
        paths.append(dd)
    data = {}
    for tier, t in d.groupby("tier"):
        nmax = t["n"].max()
        data[tier] = []
        for r in t.itertuples():
            pt = shapes[r.nbhd].representative_point()
            x, y = px(pt.x, pt.y)
            data[tier].append({"id": r.nbhd, "name": r.name, "area": r.area, "x": x, "y": y,
                               "r": round(2 + 16 * math.sqrt(r.n / nmax), 1), "n": round(r.n / years),
                               "med": round(r.med, 1), "idx": round(r.idx, 3),
                               "income": int(r.median_income) if r.median_income == r.median_income else None,
                               "capped": bool(r.income_capped), "makeup": r.makeup})
        data[tier].sort(key=lambda z: -z["r"])
    order = list(tiers)
    labels = {k: v["label"] for k, v in tiers.items()}
    notes = {k: v.get("note", "") for k, v in tiers.items()}
    makeup = {k: v for k, v in cfg["analysis"]["groupings"]["makeup"].get("level_labels", {}).items()}
    frag = fragment(paths, data, order, labels, notes, H, makeup, m)
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


def fragment(paths, data, order, labels, notes, H, makeup, m):
    legend = "".join(f'<span class="rcm-key"><i style="background:{c}"></i>{html.escape(lab)}</span>' for _, _, lab, c in BINS)
    buttons = "".join(f'<button type="button" data-tier="{k}" aria-pressed="{"true" if i == 0 else "false"}">{html.escape(labels[k])}</button>'
                      for i, k in enumerate(order))
    base = "".join(f'<path d="{d}"/>' for d in paths)
    return f"""<figure class="rcm" id="rcm">
<style>
.rcm{{margin:32px 0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;font-size:14px;color:#c4c4c4}}
.rcm figcaption strong{{display:block;font-size:18px;color:#f2f2f2;margin-bottom:4px}}
.rcm .rcm-tabs{{display:flex;gap:8px;margin:14px 0 10px;flex-wrap:wrap}}
.rcm .rcm-tabs button{{font:inherit;font-size:14px;color:#f2f2f2;background:transparent;border:1px solid #4a4a4a;border-radius:4px;padding:7px 14px;cursor:pointer}}
.rcm .rcm-tabs button[aria-pressed="true"]{{background:#f2f2f2;color:#0b0b0b;border-color:#f2f2f2}}
.rcm .rcm-note{{margin:0 0 8px;color:#9a9a9a}}
.rcm .rcm-wrap{{position:relative}}
.rcm svg{{display:block;width:100%;height:auto}}
.rcm .rcm-base path{{fill:#161616;stroke:#2b2b2b;stroke-width:.6}}
.rcm circle{{stroke:#0b0b0b;stroke-width:1;cursor:pointer}}
.rcm circle.mid{{stroke:#8a8a86;stroke-width:.8}}
.rcm circle:hover,.rcm circle.on{{stroke:#f2f2f2;stroke-width:1.5}}
.rcm .rcm-legend{{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:10px}}
.rcm .rcm-key{{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}}
.rcm .rcm-key i{{width:12px;height:12px;border-radius:50%;display:inline-block}}
.rcm .rcm-tip{{position:absolute;pointer-events:none;background:#161616;border:1px solid #3a3a3a;border-radius:4px;padding:8px 10px;color:#f2f2f2;font-size:14px;line-height:1.45;max-width:240px;display:none;z-index:2}}
.rcm .rcm-tip b{{display:block}}
.rcm .rcm-source{{color:#9a9a9a;margin:10px 0 0}}
.rcm details{{margin-top:10px}} .rcm summary{{cursor:pointer;font-weight:700;color:#c4c4c4}}
.rcm table{{border-collapse:collapse;width:100%;margin-top:8px}} .rcm th,.rcm td{{text-align:left;padding:4px 8px;border-bottom:1px solid #262626}}
.rcm .rcm-scroll{{max-height:320px;overflow:auto}}
</style>
<figcaption><strong>{html.escape(m.get('title', 'How long calls waited, by neighborhood'))}</strong>{html.escape(m.get('subtitle', ''))}</figcaption>
<div class="rcm-tabs" role="group" aria-label="Call type">{buttons}</div>
<p class="rcm-note" id="rcm-note"></p>
<div class="rcm-wrap"><svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(m.get('aria', 'Map of neighborhoods'))}"><g class="rcm-base">{base}</g><g id="rcm-dots"></g></svg><div class="rcm-tip" id="rcm-tip"></div></div>
<div class="rcm-legend">{legend}<span class="rcm-key">Bubble size: calls a year</span></div>
<p class="rcm-source">{html.escape(m.get('source', ''))}</p>
<details><summary>Show the numbers</summary><div class="rcm-scroll"><table id="rcm-table"></table></div></details>
<script>(function(){{
var D={json.dumps(data, separators=(',', ':'))}, N={json.dumps(notes)}, MK={json.dumps(makeup)};
var B={json.dumps([[lo, hi, c] for lo, hi, _, c in BINS])};
function col(v){{for(var i=0;i<B.length;i++)if(v>=B[i][0]&&v<B[i][1])return B[i][2];return B[B.length-1][2];}}
function pct(v){{var p=Math.round(v*100);return (p>0?'+':'')+p+'%';}}
function vs(v){{var p=Math.abs(Math.round(v*100));return p===0?'Same as the city':p+'% '+(v>0?'longer':'shorter')+' than the city';}}
var g=document.getElementById('rcm-dots'),tip=document.getElementById('rcm-tip'),wrap=tip.parentNode,ns='http://www.w3.org/2000/svg';
function tipHtml(d){{return '<b>'+d.name+'</b>'+d.area+'<br>Median wait: '+d.med.toFixed(1)+' min<br>'+vs(d.idx)+' for the same call types<br>'+d.n.toLocaleString('en-US')+' calls a year<br>Median household income: '+(d.income?'$'+d.income.toLocaleString('en-US')+(d.capped?' or more':''):'n/a')+'<br>'+(MK[d.makeup]||d.makeup||'');}}
function show(e,d,c){{tip.innerHTML=tipHtml(d);tip.style.display='block';var r=wrap.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
 tip.style.left=Math.min(Math.max(0,x+12),r.width-tip.offsetWidth)+'px';tip.style.top=Math.max(0,y-tip.offsetHeight-12)+'px';
 var o=g.querySelector('.on');if(o)o.classList.remove('on');c.classList.add('on');}}
function draw(t){{g.textContent='';D[t].forEach(function(d){{var c=document.createElementNS(ns,'circle');c.setAttribute('cx',d.x);c.setAttribute('cy',d.y);c.setAttribute('r',d.r);c.setAttribute('fill',col(d.idx));if(Math.abs(d.idx)<0.1)c.setAttribute('class','mid');
 c.addEventListener('mousemove',function(e){{show(e,d,c);}});c.addEventListener('click',function(e){{show(e,d,c);e.stopPropagation();}});c.addEventListener('mouseleave',function(){{tip.style.display='none';c.classList.remove('on');}});g.appendChild(c);}});
 document.getElementById('rcm-note').textContent=N[t]||'';
 var rows=D[t].slice().sort(function(a,b){{return b.idx-a.idx;}});
 document.getElementById('rcm-table').innerHTML='<tr><th>Neighborhood</th><th>Median wait</th><th>Against the city</th><th>Calls a year</th></tr>'+rows.map(function(d){{return '<tr><td>'+d.name+'</td><td>'+d.med.toFixed(1)+' min</td><td>'+pct(d.idx)+'</td><td>'+d.n.toLocaleString('en-US')+'</td></tr>';}}).join('');
 document.querySelectorAll('#rcm .rcm-tabs button').forEach(function(b){{b.setAttribute('aria-pressed',b.dataset.tier===t?'true':'false');}});}}
document.querySelectorAll('#rcm .rcm-tabs button').forEach(function(b){{b.addEventListener('click',function(){{tip.style.display='none';draw(b.dataset.tier);}});}});
document.addEventListener('click',function(){{tip.style.display='none';}});
draw(D[location.hash.slice(1)]?location.hash.slice(1):{json.dumps(order[0])});
}})();</script>
</figure>"""


if __name__ == "__main__":
    main()
