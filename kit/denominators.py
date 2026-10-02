"""Population denominators from the ACS, via the Census Reporter API (no key needed).

For the place in the config and every census tract in it:
  residents by group, sex and age band (ACS B01001 race-iterated tables)
  tract socioeconomics: poverty, median income, unemployment, renter share, density
  tract geometry, and each tract's police district (by tract centroid, from the district GeoJSON)
  the race-coding bound: residents of each group alone vs alone or in combination

  python denominators.py analysis.json        ->  out/population.json, out/cache/tracts_geometry.json
"""
import json
import math
import sys

from shapely.geometry import shape

from common import ACS_SEX_AGE, ALONE_VAR, COMBO_TABLE, Project, fetch

CR = "https://api.censusreporter.org/1.0"
SES_TABLES = ["B01003", "B17001", "B19013", "B23025", "B25003"]


def acs(project, tables, geo, name):
    release = project.cfg.get("acs_release", "acs2024_5yr")
    url = f"{CR}/data/show/{release}?table_ids={','.join(tables)}&geo_ids={geo}"
    return json.loads(fetch(project, url, name).read_text())


def bands(est, table, sex):
    start = 3 if sex == "M" else 18
    return [float(est[f"{table}{i:03d}"] or 0) for i in range(start, start + 14)]


def district_lookup(project):
    d = project.cfg.get("districts")
    if not d:
        return None
    src = d.get("geojson_url") or d.get("geojson_path")
    path = fetch(project, src, "districts.geojson") if src.startswith("http") else project.path(src)
    feats = json.loads(path.read_text())["features"]
    polys = [(shape(f["geometry"]), str(f["properties"][d["name_field"]])) for f in feats]
    rename = d.get("rename", {})
    title = d.get("title_case", True)

    def lookup(pt):
        for poly, name in polys:
            if poly.contains(pt):
                return rename.get(name, name.title() if title else name)
        return None
    return lookup


def main():
    p = Project(sys.argv[1])
    place = p.cfg["place"]["census_geoid"]
    groups = p.groups
    missing = [g for g in groups if g not in ACS_SEX_AGE]
    if missing:
        sys.exit(f"no ACS sex-by-age table for {missing}; groups must be among {list(ACS_SEX_AGE)}")
    tables = [ACS_SEX_AGE[g] for g in groups]

    city = acs(p, ["B01001"] + tables, place, "acs_place.json")
    est = city["data"][place]
    out = {"release": city["release"]["name"], "place_total": float(est["B01001"]["estimate"]["B01001001"]),
           "city": {}, "city_age": {}}
    for g in groups:
        t = ACS_SEX_AGE[g]; e = est[t]["estimate"]
        out["city"][g] = {"M": float(e[t + "002"]), "F": float(e[t + "017"])}
        out["city_age"][g] = {s: bands(e, t, s) for s in "FM"}

    # race-coding bound: alone vs alone or in combination, where the ACS has both
    combo_groups = [g for g in groups if g in COMBO_TABLE]
    if combo_groups:
        c = acs(p, ["B02001"] + [COMBO_TABLE[g] for g in combo_groups], place, "acs_place_race.json")["data"][place]
        out["combo_ratio"] = {g: round(c[COMBO_TABLE[g]]["estimate"][COMBO_TABLE[g] + "001"] / c["B02001"]["estimate"][ALONE_VAR[g]], 3)
                              for g in combo_groups if c["B02001"]["estimate"][ALONE_VAR[g]]}
        print("alone-or-in-combination / alone:", out["combo_ratio"])

    # overlap: "Hispanic" counts every race, and race-alone tables include Hispanic residents of that race,
    # so these people sit in two denominators (B03002: Hispanic or Latino origin by race)
    hisp_of = {"Black": ("B03002004", "B03002014"), "AIAN": ("B03002005", "B03002015"), "Asian": ("B03002006", "B03002016"), "NHPI": ("B03002007", "B03002017")}
    if "Hispanic" in groups and any(g in hisp_of for g in groups):
        h = acs(p, ["B03002"], place, "acs_place_hispanic.json")["data"][place]["B03002"]["estimate"]
        out["hispanic_overlap"] = {g: round(h[hisp_of[g][1]] / (h[hisp_of[g][0]] + h[hisp_of[g][1]]) * 100, 1) for g in groups if g in hisp_of}
        print("% of each race-alone group who are also Hispanic:", out["hispanic_overlap"])

    # tracts: population, socioeconomics, geometry, district
    geo = f"140|{place}"
    tr = acs(p, tables, geo, "acs_tracts.json")["data"]
    ses = acs(p, SES_TABLES, geo, "acs_tracts_ses.json")["data"]
    tiger = p.cfg.get("tiger", "tiger2024")
    gj = json.loads(fetch(p, f"{CR}/geo/show/{tiger}?geo_ids={geo}", "tracts_geometry.json").read_text())["features"]
    geom = {f["properties"]["geoid"].split("US")[1]: f for f in gj}
    lookup = district_lookup(p)
    tracts, unassigned = [], 0
    for geo_id, tabs in tr.items():
        gid = geo_id.split("US")[1]
        f = geom.get(gid)
        if f is None:
            continue
        poly = shape(f["geometry"])
        district = lookup(poly.representative_point()) if lookup else None
        if lookup and district is None:
            unassigned += 1
        s = ses.get(geo_id, {})
        e = lambda tb, v: (s.get(tb, {}).get("estimate", {}) or {}).get(v)
        pop_total, pov_u, labor, tenure, inc = e("B01003", "B01003001"), e("B17001", "B17001001"), e("B23025", "B23025003"), e("B25003", "B25003001"), e("B19013", "B19013001")
        aland = float(f["properties"].get("aland") or 0)
        tracts.append({
            "geoid": gid, "district": district,
            "pop": {g: {sx: bands(tabs[ACS_SEX_AGE[g]]["estimate"], ACS_SEX_AGE[g], sx) for sx in "FM"} for g in groups},
            "ses": {"poverty": e("B17001", "B17001002") / pov_u if pov_u else None,
                    "log_income": math.log(inc) if inc and inc > 0 else None,
                    "unemployment": e("B23025", "B23025005") / labor if labor else None,
                    "renters": e("B25003", "B25003003") / tenure if tenure else None,
                    "log_density": math.log(pop_total / (aland / 1e6)) if pop_total and aland else None},
        })
    out["tracts"] = tracts
    out["unassigned_tracts"] = unassigned
    if lookup:
        dist = {}
        for t in tracts:
            if t["district"]:
                for g in groups:
                    cell = dist.setdefault(t["district"], {}).setdefault(g, {"M": 0.0, "F": 0.0})
                    for sx in "FM":
                        cell[sx] += sum(t["pop"][g][sx])
        out["districts"] = dist
        print(f"{len(tracts)} tracts, {len(tracts) - unassigned} assigned to {len(dist)} districts, {unassigned} outside")
    p.write_json("population.json", out)


if __name__ == "__main__":
    main()
