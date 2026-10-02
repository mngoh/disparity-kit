"""Find, inspect and fetch incident data on open-data portals (Socrata and ArcGIS). No keys needed.

  python sources.py search "Los Angeles, CA" [--domain data.lacity.org] [--topic assault] [--out FILE]
  python sources.py search "Los Angeles, CA" --boundaries [--out FILE]
  python sources.py inspect <dataset url> [--out FILE]
  python sources.py fetch <dataset url> --out data/raw/<name>.csv [--where "<filter>"]

search    candidate datasets, scored on what the analysis needs: victim race and sex, a date, offense, location.
          --boundaries looks for police district polygons instead, and gives a GeoJSON url for each.
inspect   one dataset: columns with descriptions and code legends, row count, date range, monthly counts
          (Socrata), values of the race, sex and ethnicity columns, and whose race it records.
fetch     every row through the API into a CSV (raw codes, no cleaning), with <csv>.source.json beside it
          recording the url, filter, time and row count, so the download can be repeated.

A dataset url can be a portal page, a Socrata resource or views url, an ArcGIS FeatureServer or MapServer
layer, an ArcGIS item page or a Hub dataset page.
"""
import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import html
import io
import json
import pathlib
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from common import UA
from fields import guess_roles, parse_legend, subject

SOC_ID = re.compile(r"\b([a-z0-9]{4}-[a-z0-9]{4})\b")
AGS_ITEM = re.compile(r"\b([0-9a-f]{32})(?:_(\d+))?\b")
TOPIC_DEFAULT = ["crime", "incidents", "offenses", "victims", "nibrs"]
BOUNDARY_TERMS = ["police district", "police division", "police precinct", "police beat", "patrol area", "police boundaries"]
OTHER_KINDS = {"arrest": "arrests", "stop": "stops", "citation": "citations", "call": "calls for service", "use of force": "use of force",
               "complaint": "complaints", "collision": "traffic collisions", "traffic": "traffic", "shooting": "shootings", "homicide": "homicides"}


# ---------- http

def http(url, params=None, data=None, tries=3):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    body = urllib.parse.urlencode(data).encode() if data else None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            if i == tries - 1 or (isinstance(e, urllib.error.HTTPError) and e.code in (400, 403, 404)):
                raise
            time.sleep(2 * (i + 1))


def get_json(url, params=None, data=None):
    out = json.loads(http(url, params, data))
    if isinstance(out, dict) and "error" in out and isinstance(out["error"], dict):
        raise RuntimeError(f"{url}: {out['error'].get('message')} {out['error'].get('details', '')}")
    return out


# ---------- which platform

def resolve(url):
    """Return ("socrata", domain, id) or ("arcgis", layer_url, None)."""
    u = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    if re.search(r"/(FeatureServer|MapServer)", u.path, re.I):
        base = re.sub(r"/query.*$", "", url.split("?")[0]).rstrip("/")
        if not re.search(r"/(FeatureServer|MapServer)/\d+$", base, re.I):
            layers = get_json(base, {"f": "json"}).get("layers") or []
            if not layers:
                sys.exit(f"no layers in {base}")
            if len(layers) > 1:
                print("layers:", ", ".join(f"{l['id']} {l['name']}" for l in layers), "(using the first; pass a layer url to choose)")
            base = f"{base}/{layers[0]['id']}"
        return "arcgis", base, None
    item = AGS_ITEM.search(url)
    if item and ("arcgis" in u.netloc or "hub" in u.netloc or "/datasets/" in u.path or "geohub" in u.netloc or "item.html" in u.path):
        info = get_json(f"https://www.arcgis.com/sharing/rest/content/items/{item.group(1)}", {"f": "json"})
        if not info.get("url"):
            sys.exit(f"ArcGIS item {item.group(1)} has no service url")
        layer = item.group(2) or "0"
        base = info["url"].rstrip("/")
        return "arcgis", base if re.search(r"/\d+$", base) else f"{base}/{layer}", None
    ids = SOC_ID.findall(u.path)  # the id is the last 4x4 in the path ("Crime-Data-from-2020..." holds "from-2020")
    if ids:
        return "socrata", u.netloc, ids[-1]
    sys.exit(f"cannot tell the platform of {url}; give a Socrata dataset url or an ArcGIS layer url")


# ---------- Socrata

def soc_columns(meta):
    return [{"field": c["fieldName"], "name": c.get("name", ""), "type": c.get("dataTypeName", ""), "description": c.get("description", "") or ""}
            for c in meta.get("columns", []) if not c["fieldName"].startswith(":")]


def soc_q(domain, ds, **params):
    return get_json(f"https://{domain}/resource/{ds}.json", {f"${k}": v for k, v in params.items()})


def soc_inspect(domain, ds):
    meta = get_json(f"https://{domain}/api/views/{ds}.json")
    cols = soc_columns(meta)
    out = {"platform": "socrata", "url": f"https://{domain}/d/{ds}", "api": f"https://{domain}/resource/{ds}", "title": meta.get("name"),
           "publisher": meta.get("attribution") or domain, "updated": _ts(meta.get("rowsUpdatedAt")), "description": (meta.get("description") or "")[:600],
           "columns": cols}
    out["rows"] = int(soc_q(domain, ds, select="count(*) as n")[0]["n"])
    return out, lambda f, top=200: {str(r.get(f, "")): int(r["n"]) for r in soc_q(domain, ds, select=f"`{f}`, count(*) as n", group=f"`{f}`", order="n DESC", limit=top)}, \
        lambda f: soc_q(domain, ds, select=f"min(`{f}`) as lo, max(`{f}`) as hi")[0], \
        lambda f: {r["m"][:7]: int(r["n"]) for r in soc_q(domain, ds, select=f"date_trunc_ym(`{f}`) as m, count(*) as n", group="m", order="m", limit=5000) if r.get("m")}


def soc_fetch(domain, ds, dest, where=None, page=50000):
    n, offset = 0, 0
    with open(dest, "w", newline="") as fh:
        w = csv.writer(fh)
        while True:
            params = {"$limit": page, "$offset": offset, "$order": ":id"}
            if where:
                params["$where"] = where
            reader = csv.reader(io.StringIO(http(f"https://{domain}/resource/{ds}.csv", params).decode("utf-8")))
            header = next(reader, None)
            if offset == 0 and header:
                w.writerow(header)
            rows = list(reader)
            w.writerows(rows)
            n += len(rows)
            print(f"  {n:,} rows", end="\r")
            if len(rows) < page:
                break
            offset += page
    print()
    return n


def soc_search(city, domain, terms, boundaries=False):
    hits = {}
    for term in terms:
        params = {"q": term if domain else f"{city} {term}", "only": "dataset,map" if boundaries else "dataset", "limit": 50}
        if domain:
            params.update(domains=domain, search_context=domain)
        try:
            res = get_json("https://api.us.socrata.com/api/catalog/v1", params)["results"]
        except Exception as e:
            print("socrata search failed:", e)
            continue
        for r in res:
            rs, md = r["resource"], r["metadata"]
            text = " ".join([rs.get("name", ""), rs.get("description", ""), rs.get("attribution", "") or "", md.get("domain", "")]).lower()
            if not domain and not all(w in text for w in city.lower().split()[:2]):
                continue
            if re.search(r"\bdemo\b|sandbox|\btest\b", md.get("domain", "")):
                continue  # Socrata demo and test sites mirror real datasets
            cols = [{"field": f, "name": n, "type": t, "description": d} for f, n, t, d in
                    zip(rs.get("columns_field_name", []), rs.get("columns_name", []), rs.get("columns_datatype", []), rs.get("columns_description", []))]
            hits[rs["id"]] = {"platform": "socrata", "title": rs.get("name"), "publisher": rs.get("attribution") or md.get("domain"),
                              "url": r.get("link") or r.get("permalink"), "api": f"https://{md['domain']}/resource/{rs['id']}",
                              "updated": (rs.get("data_updated_at") or "")[:10], "description": (rs.get("description") or "")[:300], "columns": cols,
                              "geojson_url": f"https://{md['domain']}/resource/{rs['id']}.geojson?$limit=50000" if boundaries else None,
                              "polygon": any("polygon" in (t or "").lower() for t in rs.get("columns_datatype", []))}
    return list(hits.values())


# ---------- ArcGIS

AGS_TYPE = {"esriFieldTypeDate": "date", "esriFieldTypeString": "text", "esriFieldTypeDouble": "number", "esriFieldTypeSingle": "number",
            "esriFieldTypeInteger": "number", "esriFieldTypeSmallInteger": "number", "esriFieldTypeBigInteger": "number", "esriFieldTypeOID": "oid",
            "esriFieldTypeDateOnly": "date", "esriFieldTypeTimestampOffset": "date"}


def ags_columns(meta):
    cols = []
    for f in meta.get("fields", []):
        legend = {str(c["code"]): c["name"] for c in (f.get("domain") or {}).get("codedValues", [])} if f.get("domain") else {}
        desc = f.get("description") or ""
        if desc.startswith("{"):
            try:
                desc = json.loads(desc).get("value", "")
            except ValueError:
                pass
        cols.append({"field": f["name"], "name": f.get("alias") or f["name"], "type": AGS_TYPE.get(f["type"], f["type"]), "description": desc, "legend": legend})
    return cols


def ags_q(layer, **params):
    params.setdefault("where", "1=1")
    params["f"] = "json"
    return get_json(f"{layer}/query", data=params)


def ags_inspect(layer):
    meta = get_json(layer, {"f": "json"})
    cols = ags_columns(meta)
    out = {"platform": "arcgis", "url": layer, "api": layer, "title": meta.get("name"), "publisher": (meta.get("copyrightText") or "")[:120],
           "updated": _ts((meta.get("editingInfo") or {}).get("dataLastEditDate"), ms=True), "description": re.sub("<[^>]+>", " ", meta.get("description") or "")[:600],
           "columns": cols, "geometry": meta.get("geometryType"), "max_records": meta.get("maxRecordCount")}
    item_dictionary(meta, out)
    out["rows"] = ags_q(layer, returnCountOnly="true").get("count")
    oid = meta.get("objectIdField") or next((c["field"] for c in cols if c["type"] == "oid"), None)

    def values(f, top=200):
        stats = json.dumps([{"statisticType": "count", "onStatisticField": oid or f, "outStatisticFieldName": "n"}])
        res = ags_q(layer, groupByFieldsForStatistics=f, outStatistics=stats, orderByFields="n DESC")
        return {str(r["attributes"].get(f)): int(r["attributes"]["n"]) for r in res.get("features", [])[:top]}

    def span(f):
        stats = json.dumps([{"statisticType": "min", "onStatisticField": f, "outStatisticFieldName": "lo"},
                            {"statisticType": "max", "onStatisticField": f, "outStatisticFieldName": "hi"}])
        a = ags_q(layer, outStatistics=stats)["features"][0]["attributes"]
        typ = next((c["type"] for c in cols if c["field"] == f), "")
        return {k: _ts(a[k], ms=True) if typ == "date" else a[k] for k in ("lo", "hi")}
    return out, values, span, None


def item_dictionary(meta, out):
    """Hub datasets often keep their data dictionary in the item description ("Race  Race of the victim."), not on the
    fields. Fill empty field descriptions from it, and take the item's title and description."""
    if not meta.get("serviceItemId"):
        return
    try:
        item = get_json(f"https://www.arcgis.com/sharing/rest/content/items/{meta['serviceItemId']}", {"f": "json"})
    except Exception:
        return
    text = " ".join(html.unescape(re.sub("<[^>]+>", " ", item.get("description") or "")).split())
    out["title"] = item.get("title") or out["title"]
    out["publisher"] = out["publisher"] or item.get("owner")
    out["description"] = (text or out["description"])[:600]
    start = text.upper().find("DATA DICTIONARY")
    body = re.sub(r"Field Name\s+Description", " ", text[start if start >= 0 else 0:])
    pos = sorted((m.start(), m.end(), c) for c in out["columns"] for m in [re.search(rf"(?<![\w]){re.escape(c['field'])}(?![\w])", body)] if m)
    for i, (a, b, c) in enumerate(pos):
        if not c["description"]:
            c["description"] = body[b:pos[i + 1][0] if i + 1 < len(pos) else len(body)].strip()[:300]
            c["from_item_dictionary"] = True


def ags_fetch(layer, dest, where=None):
    meta = get_json(layer, {"f": "json"})
    dates = {f["name"] for f in meta.get("fields", []) if AGS_TYPE.get(f["type"]) == "date"}
    names = [f["name"] for f in meta.get("fields", [])]
    point = meta.get("geometryType") == "esriGeometryPoint"
    xy = (["lon", "lat"] if not ({"lon", "lat"} & set(names)) else ["_lon", "_lat"]) if point else []
    ids = sorted(ags_q(layer, where=where or "1=1", returnIdsOnly="true").get("objectIds") or [])
    chunk = min(meta.get("maxRecordCount") or 1000, 2000)
    n = 0
    with open(dest, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(names + xy)
        for i in range(0, len(ids), chunk):
            res = ags_q(layer, objectIds=",".join(map(str, ids[i:i + chunk])), outFields="*", outSR="4326", returnGeometry="true" if point else "false")
            for ft in res.get("features", []):
                a = ft["attributes"]
                row = [_ts(a.get(k), ms=True, full=True) if k in dates else a.get(k) for k in names]
                if point:
                    g = ft.get("geometry") or {}
                    row += [g.get("x"), g.get("y")]
                w.writerow(row)
                n += 1
            print(f"  {n:,} of {len(ids):,} rows", end="\r")
    print()
    return n


def ags_search(terms, boundaries=False):
    """terms: list of (query, city or None)."""
    hits = {}
    for term, city in terms:
        q = f'({term})' + (f' AND ("{city}")' if city else "") + ' AND type:"Feature Service"'
        try:
            res = get_json("https://www.arcgis.com/sharing/rest/search", {"q": q, "f": "json", "num": 30, "sortField": "numviews", "sortOrder": "desc"})["results"]
        except Exception as e:
            print("arcgis search failed:", e)
            continue
        for r in res:
            if r.get("url"):
                hits[r["id"]] = r
    items = list(hits.values())[:40]

    def expand(r):
        out = []
        try:
            svc = get_json(r["url"], {"f": "json"})
        except Exception:
            return out
        for lyr in (svc.get("layers") or [])[:6]:
            url = f"{r['url'].rstrip('/')}/{lyr['id']}"
            try:
                meta = get_json(url, {"f": "json"})
            except Exception:
                continue
            poly = meta.get("geometryType") == "esriGeometryPolygon"
            if boundaries and not poly:
                continue
            n = None
            if boundaries:
                try:
                    n = ags_q(url, returnCountOnly="true").get("count")
                except Exception:
                    pass
            out.append({"platform": "arcgis", "title": f"{r['title']} / {lyr['name']}", "publisher": r.get("owner"), "url": f"https://www.arcgis.com/home/item.html?id={r['id']}",
                        "api": url, "updated": _ts(r.get("modified"), ms=True), "description": re.sub("<[^>]+>", " ", r.get("snippet") or "")[:300],
                        "columns": ags_columns(meta), "polygon": poly, "views": r.get("numViews"), "features": n,
                        "geojson_url": f"{url}/query?where=1%3D1&outFields=*&outSR=4326&f=geojson" if poly else None})
        return out
    with cf.ThreadPoolExecutor(8) as ex:
        return [x for lst in ex.map(expand, items) for x in lst]


# ---------- scoring and inspection

def score(c, topic=None, boundaries=False, agency=None):
    roles, cands = guess_roles(c["columns"])
    people = [{"field": f, "role": role, "subject": subject(f, next((x["description"] for x in c["columns"] if x["field"] == f), ""))}
              for role in ("race", "ethnicity", "sex", "age") for f in cands.get(role, [])]
    title = (c["title"] or "").lower()
    notes, s = [], 0
    if c["platform"] == "arcgis" and re.search(r"wfl1|copy|extract|sample|test|_layer\b", title):
        s -= 4
        notes.append("looks like a personal copy or extract: prefer the agency's own dataset")
    if boundaries:
        s += 3 * bool(c.get("polygon")) + sum(w in title for w in ("police", "district", "division", "precinct", "beat", "patrol", "boundar"))
        s += 2 * bool(agency and agency.lower() in title)
        s -= 2 * any(w in title for w in ("council", "school", "fire", "zip", "census", "voting", "supervisor", "park"))
        return s, roles, people, notes
    vr = [p for p in people if p["role"] == "race" and p["subject"] == "victim"]
    any_race = [p for p in people if p["role"] == "race"]
    s += 4 * bool(vr) + 2 * any(p["role"] == "sex" and p["subject"] == "victim" for p in people) + any(p["role"] == "age" and p["subject"] == "victim" for p in people)
    if not vr and any_race:
        subj = {p["subject"] or "unknown" for p in any_race}
        s += 1 + 2 * ("victim" in title and subj == {"unknown"})
        notes.append(f"race recorded for: {', '.join(sorted(subj))}")
    if not any_race:
        notes.append("no race column")
    s += 2 * ("date" in roles) + ("code" in roles or "desc" in roles) + (("lat" in roles and "lon" in roles) or "district" in roles)
    s += sum(w in title for w in ("crime", "incident", "offense", "victim", "nibrs", "part i"))
    if re.search(r"aggregat|summary|totals|by month|by year|statistics", title):
        s -= 3
        notes.append("looks aggregated, not one row per incident or victim")
    for k, label in OTHER_KINDS.items():
        if k in title:
            notes.append(f"looks like {label}")
            break
    if topic and topic.lower() in (title + " " + c.get("description", "").lower()):
        s += 1
    if "ethnicity" in roles:
        notes.append("ethnicity in its own column")
    return s, roles, people, notes


def cmd_search(a):
    city = a.city.split(",")[0].strip()
    terms = [a.topic] + TOPIC_DEFAULT if a.topic else TOPIC_DEFAULT
    if a.boundaries:
        terms = BOUNDARY_TERMS + ([f"{a.agency} {w}" for w in ("division", "district", "precinct", "beat", "area", "boundaries")] if a.agency else [])
        ags_terms = [("police AND (district OR division OR precinct OR beat OR boundaries)", city)]
        if a.agency:  # agency items often never name the city ("LAPD Division Boundaries")
            ags_terms.append((f"{a.agency} AND (district OR division OR precinct OR beat OR area OR boundaries)", None))
    else:
        ags_terms = [(t, city) for t in terms]
    found = soc_search(city, a.domain, terms, a.boundaries) + ags_search(ags_terms, a.boundaries)
    rows = []
    for c in found:
        s, roles, people, notes = score(c, a.topic, a.boundaries, a.agency)
        if a.boundaries and not c.get("polygon"):
            continue
        rows.append({k: c.get(k) for k in ("platform", "title", "publisher", "url", "api", "updated", "geojson_url", "features")} |
                    {"score": s, "roles": roles, "people": people, "notes": notes, "fields": [x["field"] for x in c["columns"]]})
    rows.sort(key=lambda r: (-r["score"], r["title"] or ""))
    for r in rows[:25]:
        print(f"{r['score']:>3}  {r['platform']:<7} {r['title'][:70]:<70}  {r['updated'] or '':<10}  {r['api']}")
        if a.boundaries:
            print("       ", f"{r['features']} polygons; " if r.get("features") is not None else "", "fields:", ", ".join(r["fields"][:12]))
        extra = ", ".join(f"{p['role']} {p['field']} ({p['subject'] or '?'})" for p in r["people"])
        if extra or r["notes"]:
            print("       ", "; ".join(x for x in [extra] + r["notes"] if x))
    if not rows:
        print("nothing found: try --domain with the city's portal, or another search term with --topic")
    if a.out:
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out).write_text(json.dumps(rows, indent=1))
        print("wrote", a.out)


def inspect(url):
    kind, base, ds = resolve(url)
    out, values, span, monthly = soc_inspect(base, ds) if kind == "socrata" else ags_inspect(base)
    roles, cands = guess_roles(out["columns"])
    for c in out["columns"]:
        c["legend"] = c.get("legend") or parse_legend(c["description"])
        c["subject"] = subject(c["field"], c["description"]) if any(c["field"] in cands.get(r, []) for r in ("race", "sex", "age", "ethnicity")) else None
    out["roles"], out["candidates"] = roles, cands
    out["people"] = [{"field": f, "role": role, "subject": next(c["subject"] for c in out["columns"] if c["field"] == f)}
                     for role in ("race", "ethnicity", "sex", "age") for f in cands.get(role, [])]
    out["values"] = {}
    for role in ("race", "ethnicity", "sex", "victim_type"):
        for f in cands.get(role, [])[:3]:
            try:
                out["values"][f] = values(f)
            except Exception as e:
                out["values"][f] = {"error": str(e)[:200]}
    notes = []
    if roles.get("date"):
        try:
            out["date_range"] = {"field": roles["date"], **span(roles["date"])}
        except Exception as e:
            notes.append(f"date range failed: {e}")
        if monthly:
            try:
                m = monthly(roles["date"])
                med = statistics.median(m.values()) if m else 0
                out["monthly"] = m
                dense = [k for k, v in m.items() if v >= 0.6 * med]
                if dense:
                    out["dense_span"] = [dense[0], dense[-1]]
                    out["sparse_before"] = sum(1 for k in m if k < dense[0])
                    out["thin_months"] = [k for k, v in m.items() if dense[0] <= k and v < 0.6 * med]
            except Exception as e:
                notes.append(f"monthly counts failed: {e}")
    out["verdict"] = verdict(out, notes)
    return out


def verdict(out, notes):
    roles, people = out["roles"], out["people"]
    race = [p for p in people if p["role"] == "race"]
    vrace = [p for p in race if p["subject"] == "victim"]
    title = (out.get("title") or "").lower()
    if vrace or (race and "victim" in title and all(p["subject"] in (None, "victim") for p in race)):
        subj = "victim"
    elif race:
        subj = ", ".join(sorted({p["subject"] or "unknown" for p in race}))
    else:
        subj = None
    if subj is None:
        notes.append("No race column: this dataset cannot support a race analysis. Look for another dataset, or the FBI NIBRS files for this agency.")
    elif subj != "victim":
        notes.append(f"Race describes: {subj}. That supports an enforcement analysis, not a victim one; confirm with the column descriptions.")
    if "sex" not in roles:
        notes.append("No sex column: rates by group only, and no focus sex.")
    if "date" not in roles:
        notes.append("No date column found.")
    elif "rpt" in roles["date"] or "report" in roles["date"]:
        notes.append(f"Date column {roles['date']} looks like a report date; look for an occurrence date.")
    if not (("lat" in roles and "lon" in roles) or "district" in roles):
        notes.append("No coordinates or district: no location test and no tract model (as in DC).")
    elif not ("lat" in roles and "lon" in roles):
        notes.append("No coordinates: districts only, no tract model.")
    if "ethnicity" in roles:
        notes.append(f"Ethnicity is its own column ({roles['ethnicity']}): Hispanic victims are not in the race codes. Combining them is a method choice for the user.")
    if "victim_type" in roles:
        notes.append(f"{roles['victim_type']} separates people from businesses and society: keep individuals only.")
    hi = (out.get("date_range") or {}).get("hi")
    if hi and str(hi)[:10] < (dt.date.today() - dt.timedelta(days=365)).isoformat():
        notes.append(f"Last record is {str(hi)[:10]}: this file may have been replaced by a newer one (a records-system change?).")
    if out.get("dense_span"):
        a, b = out["dense_span"]
        notes.append(f"Most months from {a} to {b} are full" + (f"; {out['sparse_before']} sparse months before {a} (old cases or bad dates)" if out.get("sparse_before") else "") + ".")
    if out.get("thin_months"):
        t = out["thin_months"]
        notes.append(f"{len(t)} thin months (under 60% of the median) from {t[0]} to {t[-1]}: partial periods, lag or a records-system change. The audit will show where.")
    links = sorted({u for c in out["columns"] for u in re.findall(r"https?://\S+", c.get("description") or "")})
    if links:
        out["links"] = links
        notes.append("Links in column descriptions (code lists, boundaries): " + " ".join(links))
    return {"race_of": subj, "usable_for_victims": subj == "victim" and "sex" in roles and "date" in roles, "notes": notes}


def cmd_inspect(a):
    out = inspect(a.url)
    print(f"{out['title']}  ({out['platform']}, {out['rows']:,} rows, updated {out.get('updated')})")
    print(f"  {out['url']}")
    if out.get("date_range"):
        print(f"  dates {out['date_range']['field']}: {str(out['date_range']['lo'])[:10]} to {str(out['date_range']['hi'])[:10]}")
    print("  roles:", ", ".join(f"{k}={v}" for k, v in out["roles"].items()))
    for f, vals in out["values"].items():
        legend = next((c["legend"] for c in out["columns"] if c["field"] == f), {})
        shown = ", ".join(f"{k}{'=' + legend[k] if k in legend else ''} {v:,}" for k, v in list(vals.items())[:25]) if "error" not in vals else vals["error"]
        print(f"  {f}: {shown}")
    print("  verdict:", "usable for a victim analysis" if out["verdict"]["usable_for_victims"] else "not usable as is")
    for n in out["verdict"]["notes"]:
        print("   -", n)
    if a.out:
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out).write_text(json.dumps(out, indent=1, default=str))
        print("wrote", a.out)


def cmd_fetch(a):
    kind, base, ds = resolve(a.url)
    dest = pathlib.Path(a.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    print("fetching", base, ds or "", f"where {a.where}" if a.where else "")
    n = soc_fetch(base, ds, dest, a.where) if kind == "socrata" else ags_fetch(base, dest, a.where)
    side = {"url": a.url, "platform": kind, "api": f"https://{base}/resource/{ds}" if kind == "socrata" else base, "where": a.where,
            "fetched_at": dt.datetime.now().isoformat(timespec="seconds"), "rows": n}
    pathlib.Path(str(dest) + ".source.json").write_text(json.dumps(side, indent=1))
    print(f"wrote {dest} ({n:,} rows) and {dest.name}.source.json")


def _ts(v, ms=False, full=False):
    if v in (None, ""):
        return None
    try:
        t = dt.datetime.fromtimestamp(float(v) / (1000 if ms else 1), dt.timezone.utc)
    except (TypeError, ValueError, OSError):
        return v
    return t.strftime("%Y-%m-%dT%H:%M:%S") if full else t.date().isoformat()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("city"); s.add_argument("--domain"); s.add_argument("--topic"); s.add_argument("--agency", help="abbreviation, for example LAPD")
    s.add_argument("--boundaries", action="store_true"); s.add_argument("--out")
    i = sub.add_parser("inspect"); i.add_argument("url"); i.add_argument("--out")
    f = sub.add_parser("fetch"); f.add_argument("url"); f.add_argument("--out", required=True); f.add_argument("--where")
    a = ap.parse_args()
    {"search": cmd_search, "inspect": cmd_inspect, "fetch": cmd_fetch}[a.cmd](a)


if __name__ == "__main__":
    main()
