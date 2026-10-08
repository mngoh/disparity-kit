"""Neighborhoods, their residents, and where each call location sits.

  python geo.py response.json      ->  data/neighborhoods.csv, out/neighborhoods.json, data/points.parquet

1. Neighborhood polygons (config geo.neighborhoods), with the residential ones marked.
2. ACS 5-year tract tables from Census Reporter (no key): population (B01003), Hispanic origin by race (B03002),
   household income brackets (B19001). Tracts go to neighborhoods by the config's crosswalk field when the tract
   file has one, otherwise by a point inside each tract. Neighborhood median household income is interpolated
   from the summed brackets, the way the Census Bureau computes medians for combined areas.
3. Groups, set from residents only (no call data): income quintiles that each hold about a fifth of the
   residents of residential neighborhoods, and racial and ethnic makeup (the group that is more than half of
   residents, or "No majority"). Hispanic is any race; Black, White and Asian are non-Hispanic.
4. Every distinct call location (lat, lon, district) from data/calls.parquet, if it exists: its neighborhood,
   whether it falls inside its own district's polygon, and the straight-line distance in km to that district's
   station house (geo.stations, a CSV with district, lat, lon).
"""
import csv
import json
import math
import sys

import numpy as np
import pandas as pd
from shapely import STRtree, points
from shapely.geometry import shape

from rt import RTProject, fetch

CR = "https://api.censusreporter.org/1.0"
# B19001 brackets: lower and upper bounds in dollars, variables 002 to 017
BRACKETS = [(0, 10000), (10000, 15000), (15000, 20000), (20000, 25000), (25000, 30000), (30000, 35000), (35000, 40000),
            (40000, 45000), (45000, 50000), (50000, 60000), (60000, 75000), (75000, 100000), (100000, 125000),
            (125000, 150000), (150000, 200000), (200000, None)]
MAKEUP = {"Hispanic": "B03002012", "Black": "B03002004", "White": "B03002003", "Asian": "B03002006"}


def load_geojson(p, key):
    g = p.cfg["geo"][key]
    path = fetch(p, g["url"], f"{key}.geojson") if g["url"].startswith("http") else p.path(g["url"])
    return json.loads(path.read_text())["features"], g


def interpolated_median(counts):
    """Median of a grouped distribution, linear within the bracket that holds it. None if empty.
    Returns (value, capped): capped is True when the median falls in the open top bracket."""
    total = sum(counts)
    if total <= 0:
        return None, False
    half, cum = total / 2, 0.0
    for (lo, hi), c in zip(BRACKETS, counts):
        if cum + c >= half and c > 0:
            if hi is None:
                return float(lo), True
            return lo + (half - cum) / c * (hi - lo), False
        cum += c
    return float(BRACKETS[-1][0]), True


def acs_tracts(p):
    c = p.cfg["census"]
    tables = ["B01003", "B03002", "B19001"]
    url = f"{CR}/data/show/{c.get('release', 'acs2024_5yr')}?table_ids={','.join(tables)}&geo_ids={c['tracts_geo']}"
    d = json.loads(fetch(p, url, "acs_tracts_response.json").read_text())
    rows = {}
    for geo, tabs in d["data"].items():
        e = lambda t, v: float((tabs[t]["estimate"].get(f"{t}{v:03d}") or 0))
        rows[geo.split("US")[1]] = {
            "pop": e("B01003", 1),
            **{g: e("B03002", int(v[-3:])) for g, v in MAKEUP.items()},
            "income": [e("B19001", i) for i in range(2, 18)],
        }
    return d["release"]["name"], rows


def neighborhoods(p):
    feats, g = load_geojson(p, "neighborhoods")
    res = g.get("residential")
    nb = {}
    for f in feats:
        pr = f["properties"]
        nb[str(pr[g["id"]])] = {"id": str(pr[g["id"]]), "name": pr[g["name"]], "area": pr.get(g.get("area", ""), ""),
                                "residential": (str(pr.get(res["field"])) in res["values"]) if res else True,
                                "geom": shape(f["geometry"])}
    release, tracts = acs_tracts(p)
    tfeats, tg = load_geojson(p, "tracts")
    t2n, unassigned = {}, 0
    tree_ids = list(nb)
    tree = STRtree([nb[i]["geom"] for i in tree_ids])
    for f in tfeats:
        pr = f["properties"]
        geoid = str(pr[tg["geoid"]])
        if tg.get("neighborhood") and pr.get(tg["neighborhood"]):
            t2n[geoid] = str(pr[tg["neighborhood"]])
        else:
            pt = shape(f["geometry"]).representative_point()
            hit = [tree_ids[i] for i in tree.query(pt, predicate="within")]
            if hit:
                t2n[geoid] = hit[0]
            else:
                unassigned += 1
    for n in nb.values():
        n.update({"pop": 0.0, "income_counts": [0.0] * 16, **{k: 0.0 for k in MAKEUP}})
    matched = 0
    for geoid, t in tracts.items():
        n = nb.get(t2n.get(geoid))
        if n is None:
            continue
        matched += 1
        n["pop"] += t["pop"]
        for k in MAKEUP:
            n[k] += t[k]
        n["income_counts"] = [a + b for a, b in zip(n["income_counts"], t["income"])]
    rows = []
    for n in nb.values():
        med, capped = interpolated_median(n["income_counts"])
        pop = n["pop"]
        shares = {f"share_{k.lower()}": (n[k] / pop if pop else None) for k in MAKEUP}
        major = next((k for k in MAKEUP if pop and n[k] / pop > 0.5), "No majority") if pop else None
        rows.append({"id": n["id"], "name": n["name"], "area": n["area"], "residential": n["residential"],
                     "pop": round(pop), "households": round(sum(n["income_counts"])),
                     "median_income": round(med) if med is not None else None, "income_capped": capped,
                     **{k: round(v, 4) if v is not None else None for k, v in shares.items()}, "makeup": major})
    df = pd.DataFrame(rows)
    min_pop = p.cfg["geo"]["neighborhoods"].get("min_pop", 1000)
    df["in_groups"] = df["residential"] & (df["pop"] >= min_pop) & df["median_income"].notna()
    df["income_quintile"] = None
    g = df[df["in_groups"]].sort_values("median_income")
    cum = (g["pop"].cumsum() - g["pop"] / 2) / g["pop"].sum()
    df.loc[g.index, "income_quintile"] = np.minimum((cum * 5).astype(int) + 1, 5)
    df.loc[~df["in_groups"], "makeup"] = None
    df = df.sort_values("id")
    df.to_csv(p.data / "neighborhoods.csv", index=False)
    summary = {
        "acs_release": release, "tracts_with_data": len(tracts), "tracts_matched": matched, "tracts_unassigned": unassigned,
        "neighborhoods": len(df), "residential": int(df["residential"].sum()), "in_groups": int(df["in_groups"].sum()),
        "min_pop": min_pop, "residents_in_groups": int(df.loc[df["in_groups"], "pop"].sum()),
        "residents_total": int(df["pop"].sum()),
        "quintiles": [{"quintile": int(q), "neighborhoods": len(x), "residents": int(x["pop"].sum()),
                       "income_min": int(x["median_income"].min()), "income_max": int(x["median_income"].max())}
                      for q, x in df[df["in_groups"]].groupby("income_quintile")],
        "makeup": [{"makeup": k, "neighborhoods": len(x), "residents": int(x["pop"].sum())}
                   for k, x in df[df["in_groups"]].groupby("makeup")],
        "capped_income": df.loc[df["in_groups"] & df["income_capped"], "name"].tolist(),
    }
    p.write_json("neighborhoods.json", summary)
    return nb, df


def call_points(p, nb):
    if not p.calls.exists():
        print("no data/calls.parquet yet: skipping call locations")
        return
    con = p.con()
    pts = con.execute(f"SELECT lat, lon, district, count(*) AS n FROM read_parquet('{p.calls}') "
                      "WHERE lat IS NOT NULL AND lon IS NOT NULL GROUP BY 1, 2, 3").df()
    ids = list(nb)
    tree = STRtree([nb[i]["geom"] for i in ids])
    geom = points(pts["lon"].to_numpy(), pts["lat"].to_numpy())
    pi, gi = tree.query(geom, predicate="within")
    nb_of = pd.Series([None] * len(pts), dtype=object)
    nb_of.iloc[pi] = [ids[i] for i in gi]
    pts["nbhd"] = nb_of.values

    dfe, dg = load_geojson(p, "districts")
    dpoly = {str(f["properties"][dg["id"]]): shape(f["geometry"]) for f in dfe}
    dist_ids = list(dpoly)
    dtree = STRtree([dpoly[i] for i in dist_ids])
    di, dgi = dtree.query(geom, predicate="within")
    poly_of = pd.Series([None] * len(pts), dtype=object)
    poly_of.iloc[di] = [dist_ids[i] for i in dgi]
    pts["district_polygon"] = poly_of.values
    pts["in_district"] = pts["district_polygon"] == pts["district"].astype(str)

    st = pd.read_csv(p.path(p.cfg["geo"]["stations"]), dtype={"district": str})
    st_lat, st_lon = dict(zip(st["district"], st["lat"])), dict(zip(st["district"], st["lon"]))
    # stations inside their own precinct polygon
    bad = [d for d in st["district"] if d in dpoly and not dpoly[d].contains(points(st_lon[d], st_lat[d]))]
    lat0 = math.radians(pts["lat"].mean())
    slat = pts["district"].astype(str).map(st_lat)
    slon = pts["district"].astype(str).map(st_lon)
    dx = (pts["lon"] - slon) * 111.32 * math.cos(lat0)
    dy = (pts["lat"] - slat) * 110.57
    pts["station_km"] = np.sqrt(dx ** 2 + dy ** 2)
    pts.drop(columns="n").to_parquet(p.data / "points.parquet", index=False)
    n = pts["n"].sum()
    s = {"locations": len(pts), "calls_with_location": int(n),
         "share_in_a_neighborhood": round(float(pts.loc[pts["nbhd"].notna(), "n"].sum() / n), 4),
         "share_in_own_district_polygon": round(float(pts.loc[pts["in_district"], "n"].sum() / n), 4),
         "share_with_station": round(float(pts.loc[pts["station_km"].notna(), "n"].sum() / n), 4),
         "stations_outside_own_district": bad,
         "districts_without_station": sorted(set(pts["district"].dropna().astype(str)) - set(st["district"]))}
    p.write_json("points.json", s)
    print(s)


def main():
    p = RTProject(sys.argv[1])
    nb, df = neighborhoods(p)
    print(df[df["in_groups"]].groupby("income_quintile").agg(n=("id", "size"), pop=("pop", "sum"), lo=("median_income", "min"), hi=("median_income", "max")))
    print(df[df["in_groups"]].groupby("makeup").agg(n=("id", "size"), pop=("pop", "sum")))
    call_points(p, nb)


if __name__ == "__main__":
    main()
