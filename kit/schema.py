"""Map a downloaded incident file onto analysis.json: columns, race and sex codes, districts, Census place.

  python schema.py map <file.csv> [--source source.json] --out out/mapping.json
  python schema.py districts <file.csv> --geojson <url or path> [--district COL --lat COL --lon COL] [--mapping out/mapping.json] --out out/districts.json
  python schema.py place "Los Angeles, CA" --out out/place.json
  python schema.py draft --mapping out/mapping.json [--districts out/districts.json] [--place out/place.json] --out analysis.draft.json

map        guesses each standard column from names, portal descriptions and values, and proposes race and sex
           maps from the portal's code legend where there is one. Each guess carries its evidence.
districts  matches district names in the data to a boundary file by location: each incident point is placed in a
           polygon, and the name most incidents in that polygon carry is its data name. Picks the name field and
           writes the rename map. No string matching.
place      the Census Reporter id of the place (TIGERweb lookup, no key), with population.
draft      assembles analysis.draft.json: everything except the three decisions (question, codes, window), which
           stay null for the person running the analysis.
"""
import argparse
import json
import pathlib
import re
import sys
import urllib.parse

import numpy as np
import pandas as pd

from fields import ethnicity_code, guess_roles, parse_legend, propose_race_map, propose_sex_map, subject
from sources import get_json

STATES = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13",
          "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24", "MA": "25",
          "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36",
          "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48",
          "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56", "PR": "72"}
TIGER = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
CR = "https://api.censusreporter.org/1.0"
CODED_MISSING = {"", "0", "X", "U", "UNK", "UNKNOWN", "NAN", "NONE", "NULL", "-", "?"}


def header(path):
    return list(pd.read_csv(path, nrows=0).columns)


# ---------- map

def cmd_map(a):
    cols = header(a.csv)
    meta = {}
    if a.source:
        src = json.loads(pathlib.Path(a.source).read_text())
        meta = {c["field"]: c for c in src.get("columns", [])}
    columns = [{"field": c, "name": meta.get(c, {}).get("name", c), "type": meta.get(c, {}).get("type", ""),
                "description": meta.get(c, {}).get("description", ""), "legend": meta.get(c, {}).get("legend") or parse_legend(meta.get(c, {}).get("description", ""))}
               for c in cols]
    roles, cands = guess_roles(columns)
    use = sorted({f for lst in cands.values() for f in lst[:4]} | set(roles.values()))
    df = pd.read_csv(a.csv, usecols=use, dtype=str, low_memory=False, keep_default_na=False)
    n = len(df)
    legend = {c["field"]: c["legend"] for c in columns}
    out = {"file": str(a.csv), "rows": n, "columns": cols, "roles": {}, "candidates": cands, "notes": []}

    def share(mask):
        return round(float(mask.mean()) * 100, 1) if n else 0.0

    # check each role's best candidate against its values; fall back to the next candidate
    for role, lst in cands.items():
        lst = ([roles[role]] if role in roles else []) + [f for f in lst if f != roles.get(role)]  # guess_roles' pick first (it prefers the victim's column)
        for f in lst[:4]:
            s = df[f].str.strip()
            filled = s[s != ""]
            ev = {"field": f, "blank_pct": share(s == ""), "distinct": int(filled.nunique())}
            if role in ("race", "sex", "age", "ethnicity"):
                ev["subject"] = subject(f, meta.get(f, {}).get("description", ""))
            ok = True
            if role == "date":
                parsed = pd.to_datetime(filled.sample(min(5000, len(filled)), random_state=0), errors="coerce", format="mixed") if len(filled) else pd.Series([], dtype="datetime64[ns]")
                ev["parses_pct"] = round(float(parsed.notna().mean()) * 100, 1) if len(parsed) else 0.0
                ev["range"] = [str(parsed.min())[:10], str(parsed.max())[:10]] if parsed.notna().any() else None
                ok = ev["parses_pct"] >= 90
            elif role in ("lat", "lon"):
                x = pd.to_numeric(filled, errors="coerce")
                lim = 90 if role == "lat" else 180
                ev["numeric_pct"] = round(float(x.notna().mean()) * 100, 1) if len(x) else 0.0
                ev["zero_pct"] = share(pd.to_numeric(s, errors="coerce").abs() < 1)
                ok = ev["numeric_pct"] >= 90 and x.abs().max() <= lim
            elif role == "age":
                x = pd.to_numeric(filled, errors="coerce")
                ev["numeric_pct"] = round(float(x.notna().mean()) * 100, 1) if len(x) else 0.0
                ev["zero_or_less_pct"] = share(pd.to_numeric(s, errors="coerce") <= 0)
                ok = ev["numeric_pct"] >= 80 and (x.dropna() < 130).mean() > 0.99
                if not ok:
                    ev["sample"] = filled.value_counts().head(8).index.tolist()
            elif role in ("sex", "race", "ethnicity", "victim_type"):
                ok = ev["distinct"] <= (12 if role == "sex" else 80)
            elif role == "district":
                ok = 2 <= ev["distinct"] <= 300
                ev["values"] = filled.value_counts().head(40).to_dict()
            elif role == "id":
                ev["duplicate_rows"] = int(filled.duplicated().sum())
                # prefer a column that is unique; otherwise the candidate with the fewest repeats
                dups = {g: int(df[g].duplicated().sum()) for g in lst[:4]}
                ok = f == min(dups, key=lambda g: (dups[g], lst.index(g)))
            elif role in ("code", "desc"):
                ev["top"] = filled.value_counts().head(5).to_dict()
            if ok:
                out["roles"][role] = ev
                break
            else:
                ev["rejected"] = True
                out.setdefault("rejected", []).append({"role": role, **ev})

    # people columns: race, sex, ethnicity proposals
    if "race" in out["roles"]:
        f = out["roles"]["race"]["field"]
        rows = propose_race_map(df[f].str.strip().value_counts().to_dict(), legend.get(f, {}))
        out["race"] = {"field": f, "legend_from": "portal description" if legend.get(f) else None, "values": rows,
                       "by_group": _by_group(rows)}
        if any(r["basis"].startswith("convention") for r in rows):
            out["notes"].append(f"Race codes in {f} are read by common convention, not a legend: confirm them with the agency's code list.")
        amb = [r for r in rows if r["group"] and (r["group"].startswith("ambiguous") or r["group"].startswith("no ACS"))]
        if amb:
            out["notes"].append("Race values with no single ACS group (left out unless the user decides otherwise): "
                                + ", ".join(f"{r['value']} ({r['label']}, {r['n']:,})" for r in amb))
        unrec = [r for r in rows if r["group"] is None]
        if unrec:
            out["notes"].append("Race values not recognized: " + ", ".join(f"{r['value']!r} ({r['n']:,})" for r in unrec[:15]))
    else:
        out["notes"].append("No usable race column.")
    if "sex" in out["roles"]:
        f = out["roles"]["sex"]["field"]
        rows = propose_sex_map(df[f].str.strip().value_counts().to_dict(), legend.get(f, {}))
        sv = {r["value"]: r["code"] for r in rows if r["code"]}
        out["sex"] = {"field": f, "values": rows, "sex_values": sv if sv != {"F": "F", "M": "M"} else None}
    if "ethnicity" in out["roles"]:
        f = out["roles"]["ethnicity"]["field"]
        vals = df[f].str.strip().value_counts().to_dict()
        lg = legend.get(f, {})
        out["ethnicity"] = {"field": f, "values": [{"value": v, "n": int(k), "label": lg.get(v, v), "code": ethnicity_code(lg.get(v, v))} for v, k in vals.items()]}
        out["notes"].append(f"Ethnicity is recorded in {f}, apart from race. Combining them (for example Hispanic of any race first, as the ACS groups do) "
                            "is a method choice: ask the user, then build the combined column with prepare.py.")
    for r in out.get("rejected", []):
        if r["role"] in out["roles"]:
            continue
        why = {"age": f"values are not numeric ages (for example {r.get('sample', [])[:4]}): the age test switches off unless ages can be recovered",
               "date": "fewer than 90% of values parse as dates", "lat": "values are not latitudes", "lon": "values are not longitudes",
               "district": f"{r['distinct']} distinct values, outside 2 to 300"}.get(r["role"], "values do not fit")
        out["notes"].append(f"{r['role']} column {r['field']} rejected: {why}.")
    if "date" in out["roles"] and re.search(r"rpt|report", out["roles"]["date"]["field"], re.I):
        out["notes"].append("The date column looks like a report date. Use the occurrence date if the file has one.")
    if "id" in out["roles"] and out["roles"]["id"].get("duplicate_rows"):
        out["notes"].append(f"{out['roles']['id']['duplicate_rows']:,} rows repeat an id in {out['roles']['id']['field']}: decide whether a row is a report, a victim or an offense.")
    missing = [r for r in ("date", "race", "sex", "age", "lat", "lon", "district", "premise", "weapon", "code", "id") if r not in out["roles"]]
    if missing:
        out["notes"].append("Not found: " + ", ".join(missing) + ". Tests that need them switch off.")
    write(a.out, out)
    report(out)


def _by_group(rows):
    g = {}
    for r in rows:
        if r["group"] and r["group"] not in ("unknown",) and not r["group"].startswith(("ambiguous", "no ACS")):
            g.setdefault(r["group"], []).append(r["value"])
    return g


def report(out):
    print(f"{out['file']}: {out['rows']:,} rows")
    for role, ev in out["roles"].items():
        extra = {k: v for k, v in ev.items() if k in ("parses_pct", "range", "zero_pct", "zero_or_less_pct", "duplicate_rows", "distinct", "blank_pct")}
        print(f"  {role:<12} {ev['field']:<24} " + ", ".join(f"{k} {v}" for k, v in extra.items()) + (f", subject {ev['subject']}" if ev.get("subject") else ""))
    if "race" in out:
        print("  race map:")
        for r in out["race"]["values"]:
            print(f"    {r['value']!r:<8} {r['n']:>9,}  {r['label'][:40]:<40} -> {r['group']}  [{r['basis']}]")
    if "sex" in out:
        print("  sex:", ", ".join(f"{r['value']!r}={r['code']} {r['n']:,}" for r in out["sex"]["values"]))
    for n in out["notes"]:
        print("  -", n)


# ---------- districts

def load_geojson(src):
    if str(src).startswith("http"):
        import urllib.request
        from common import UA
        with urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": UA}), timeout=300) as r:
            return json.loads(r.read())
    return json.loads(pathlib.Path(src).read_text())


def cmd_districts(a):
    from shapely import points
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    m = json.loads(pathlib.Path(a.mapping).read_text()) if a.mapping else {"roles": {}}
    col = lambda role, given: given or (m["roles"].get(role) or {}).get("field")
    dcol, lat, lon = col("district", a.district), col("lat", a.lat), col("lon", a.lon)
    if not (dcol and lat and lon):
        sys.exit("need the district, lat and lon columns (from --mapping or the flags)")
    df = pd.read_csv(a.csv, usecols=[dcol, lat, lon], dtype={dcol: str}, low_memory=False)
    df[lat], df[lon] = pd.to_numeric(df[lat], errors="coerce"), pd.to_numeric(df[lon], errors="coerce")
    df = df[(df[lat].abs() > 1) & (df[lon].abs() > 1) & df[dcol].notna()]
    data_names = df[dcol].str.strip().value_counts()
    if len(df) > a.sample:
        df = df.sample(a.sample, random_state=0)
    gj = load_geojson(a.geojson)
    feats = [f for f in gj["features"] if f.get("geometry")]
    polys = [shape(f["geometry"]) for f in feats]
    tree = STRtree(polys)
    pts = points(df[lon].to_numpy(), df[lat].to_numpy())
    pi, gi = tree.query(pts, predicate="within")
    hit = pd.DataFrame({"pt": pi, "poly": gi}).drop_duplicates("pt")
    hit["data"] = df[dcol].str.strip().to_numpy()[hit["pt"]]
    inside = len(hit) / len(df) if len(df) else 0
    fields = [k for k, v in feats[0]["properties"].items() if isinstance(v, (str, int)) and not re.search(r"shape|objectid|^fid$|globalid|area$|length|perimeter", k, re.I)]
    best = None
    for f in fields:
        hit["name"] = [str(feats[i]["properties"].get(f)) for i in hit["poly"]]
        ct = hit.groupby(["name", "data"]).size()
        top = ct.groupby(level=0).agg(lambda s: (s.idxmax()[1], s.max() / s.sum(), s.sum()))
        purity = sum(p * n for _, p, n in top) / sum(n for _, _, n in top)
        mapped = {name: d for name, (d, p, nn) in top.items()}
        one_to_one = len(set(mapped.values())) == len(mapped)
        cand = (one_to_one, round(purity, 4), f, top)
        if best is None or cand[:2] > best[:2]:
            best = cand
    one_to_one, purity, name_field, top = best
    mapping = {name: {"data_name": d, "agreement_pct": round(p * 100, 1), "points": int(nn)} for name, (d, p, nn) in top.items()}
    renames = {}
    for tc in (True, False):
        renames[tc] = {n: v["data_name"] for n, v in mapping.items() if (n.title() if tc else n) != v["data_name"]}
    title_case = len(renames[True]) <= len(renames[False])
    rename = renames[title_case]
    poly_names = {str(f["properties"].get(name_field)) for f in feats}
    reached = {v["data_name"] for v in mapping.values()}
    out = {"geojson": a.geojson, "name_field": name_field, "title_case": title_case, "rename": rename, "polygons": len(feats),
           "points_used": len(df), "points_inside_pct": round(inside * 100, 1), "agreement_pct": round(purity * 100, 1), "one_to_one": one_to_one,
           "mapping": mapping, "data_names_without_polygon": {k: int(v) for k, v in data_names.items() if k not in reached},
           "polygons_without_points": sorted(poly_names - set(mapping)), "weak": {n: v for n, v in mapping.items() if v["agreement_pct"] < 90}}
    out["config"] = {"geojson_url" if str(a.geojson).startswith("http") else "geojson_path": a.geojson, "name_field": name_field}
    if rename:
        out["config"]["rename"] = rename
    if not title_case:
        out["config"]["title_case"] = False
    write(a.out, out)
    print(f"name field {name_field}: {len(mapping)} of {len(feats)} polygons matched, {out['agreement_pct']}% of points agree, "
          f"{out['points_inside_pct']}% of points inside a polygon, one to one: {one_to_one}")
    print("rename:", json.dumps(rename) if rename else "none needed", "| title_case:", title_case)
    if out["weak"]:
        print("weak matches (under 90% agreement):", {n: (v["data_name"], v["agreement_pct"]) for n, v in out["weak"].items()})
    if out["data_names_without_polygon"]:
        print("data districts with no polygon:", out["data_names_without_polygon"])
    if out["polygons_without_points"]:
        print("polygons with no points:", out["polygons_without_points"])


# ---------- place

def cmd_place(a):
    name, _, st = [x.strip() for x in a.place.partition(",")]
    fips = STATES.get(st.upper()) if st else None
    esc = name.replace("'", "''")
    found = []
    for layer, sumlev, label in [(f"Places_CouSub_ConCity_SubMCD/MapServer/4", "160", "incorporated place"),
                                 (f"Places_CouSub_ConCity_SubMCD/MapServer/5", "160", "census designated place"),
                                 (f"State_County/MapServer/1", "050", "county")]:
        where = f"BASENAME='{esc}'" + (f" AND STATE='{fips}'" if fips else "")
        try:
            res = get_json(f"{TIGER}/{layer}/query", {"where": where, "outFields": "GEOID,NAME,STATE", "returnGeometry": "false", "f": "json"})
        except Exception as e:
            print("TIGERweb lookup failed:", e)
            continue
        for ft in res.get("features", []):
            at = ft["attributes"]
            geoid = f"{sumlev}00US{at['GEOID']}"
            try:
                p = get_json(f"{CR}/geo/tiger2024/{geoid}")["properties"]
                pop, disp = p.get("population"), p.get("display_name")
            except Exception:
                pop, disp = None, None
            found.append({"census_geoid": geoid, "name": at["NAME"], "display_name": disp, "kind": label, "population": pop})
    found.sort(key=lambda r: (r["kind"] == "county", -(r["population"] or 0)))
    for r in found:
        print(f"  {r['census_geoid']}  {r['display_name'] or r['name']}  ({r['kind']}, population {r['population']:,})" if r["population"] else f"  {r['census_geoid']}  {r['name']} ({r['kind']})")
    if not found:
        print("no match: check the spelling, or search https://censusreporter.org")
    write(a.out, {"query": a.place, "candidates": found})


# ---------- draft

def cmd_draft(a):
    m = json.loads(pathlib.Path(a.mapping).read_text())
    d = json.loads(pathlib.Path(a.districts).read_text()) if a.districts else None
    pl = json.loads(pathlib.Path(a.place).read_text()) if a.place else None
    root = pathlib.Path(a.out).resolve().parent
    data = pathlib.Path(m["file"]).resolve()
    rel = str(data.relative_to(root)) if data.is_relative_to(root) else str(data)
    std = {"id": "id", "date": "date", "race": "race", "sex": "sex", "age": "age", "lat": "lat", "lon": "lon", "district": "district",
           "premise": "premise", "weapon": "weapon", "code": "code"}
    cfg = {"_status": "Draft from /new-city. The three decisions are null: the question (groups, focus), what counts (codes, kinds, flags) "
                      "and the window. race_map_by_group becomes race_map for the chosen groups only.",
           "title": None}
    if pl and pl["candidates"]:
        c = pl["candidates"][0]
        cfg["place"] = {"name": (c["display_name"] or c["name"]).split(",")[0].replace(" city", ""), "short": None, "census_geoid": c["census_geoid"]}
    cfg |= {"acs_release": "acs2024_5yr", "window": None, "event": None, "incidents": [{"path": rel, "kind": "all"}],
            "columns": {k: m["roles"][r]["field"] for r, k in std.items() if r in m["roles"]}}
    if "race" in m:
        cfg["race_map_by_group"] = m["race"]["by_group"]
        cfg["race_left_out"] = {r["value"]: f"{r['label']} ({r['group'] or 'not recognized'})" for r in m["race"]["values"]
                                if not r["group"] or r["group"] == "unknown" or r["group"].startswith(("ambiguous", "no ACS"))}
    if m.get("sex", {}).get("sex_values"):
        cfg["sex_values"] = m["sex"]["sex_values"]
    cfg |= {"groups": None, "focus": None, "flags": None}
    if d:
        cfg["districts"] = d["config"]
    src = [json.loads(pathlib.Path(s).read_text()) for s in a.source or []]
    if src:
        cfg["sources"] = [f"{s['title']} ({s.get('publisher') or s['platform']})" for s in src] + ["US Census Bureau ACS 2020 to 2024 via Census Reporter"]
        cfg["source_urls"] = [s["url"] for s in src]
    pathlib.Path(a.out).write_text(json.dumps(cfg, indent=1))
    print("wrote", a.out)


def write(path, obj):
    if path:
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(path).write_text(json.dumps(obj, indent=1, default=lambda o: o.item() if isinstance(o, np.generic) else str(o)))
        print("wrote", path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("map"); s.add_argument("csv"); s.add_argument("--source"); s.add_argument("--out")
    s = sub.add_parser("districts"); s.add_argument("csv"); s.add_argument("--geojson", required=True); s.add_argument("--mapping")
    s.add_argument("--district"); s.add_argument("--lat"); s.add_argument("--lon"); s.add_argument("--sample", type=int, default=200000); s.add_argument("--out")
    s = sub.add_parser("place"); s.add_argument("place"); s.add_argument("--out")
    s = sub.add_parser("draft"); s.add_argument("--mapping", required=True); s.add_argument("--districts"); s.add_argument("--place")
    s.add_argument("--source", action="append", help="source.json from sources.py inspect; repeat for several"); s.add_argument("--out", required=True)
    a = ap.parse_args()
    {"map": cmd_map, "districts": cmd_districts, "place": cmd_place, "draft": cmd_draft}[a.cmd](a)


if __name__ == "__main__":
    main()
