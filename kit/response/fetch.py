"""Download calls for service through the portal API, one month per file, into Parquet with a source note.

  python fetch.py response.json [--source historic] [--workers 4]

For every source in the config's "sources" (Socrata for now), each calendar month in its window is pulled
with a date filter, checked against the portal's own count for that filter, and written to
data/raw/<source>/<YYYY-MM>.parquet with every column kept as text, exactly as the portal serves it.
data/raw/<source>/sources.json records for each month: the URL, the query, when it ran and the row count.
A month whose file already matches its recorded count is skipped, so an interrupted run can be restarted.

Set SOCRATA_APP_TOKEN to raise the portal's rate limit (optional).
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from rt import UA, RTProject

PAGE = 500_000


def socrata_url(src, kind, params):
    return f"https://{src['domain']}/resource/{src['dataset']}.{kind}?" + urllib.parse.urlencode(params)


def get(url, dest=None, tries=6):
    headers = {"User-Agent": UA}
    if os.environ.get("SOCRATA_APP_TOKEN"):
        headers["X-App-Token"] = os.environ["SOCRATA_APP_TOKEN"]
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=900) as r:
                if dest is None:
                    return r.read()
                with open(dest, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                return dest
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            code = getattr(e, "code", None)
            if i == tries - 1 or code in (400, 403, 404):
                raise
            wait = 30 * (i + 1) if code == 429 else 5 * 2 ** i
            print(f"  retry in {wait}s after {e}", flush=True)
            time.sleep(wait)


def months(start, end):
    a = dt.date.fromisoformat(start).replace(day=1)
    b = dt.date.fromisoformat(end)
    while a <= b:
        nxt = (a.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield a, nxt
        a = nxt


def month_where(src, a, b):
    lo = max(a, dt.date.fromisoformat(src["start"]))
    hi = min(b, dt.date.fromisoformat(src["end"]) + dt.timedelta(days=1))
    w = f"{src['date_field']} >= '{lo.isoformat()}T00:00:00' AND {src['date_field']} < '{hi.isoformat()}T00:00:00'"
    if src.get("where"):
        w += f" AND ({src['where']})"
    return w


def fetch_month(src, a, b, out_dir):
    name = a.strftime("%Y-%m")
    where = month_where(src, a, b)
    count_url = socrata_url(src, "json", {"$select": "count(*) as n", "$where": where})
    expected = int(json.loads(get(count_url))[0]["n"])
    cols = src["columns"]
    tables, offset = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        while offset < expected:
            params = {"$select": ",".join(cols), "$where": where, "$order": ":id", "$limit": PAGE, "$offset": offset}
            path = get(socrata_url(src, "csv", params), pathlib.Path(tmp) / f"{offset}.csv")
            t = pacsv.read_csv(path, convert_options=pacsv.ConvertOptions(column_types={c: pa.string() for c in cols},
                                                                           strings_can_be_null=True))
            if t.num_rows == 0:
                break
            tables.append(t)
            offset += t.num_rows
    table = pa.concat_tables(tables) if tables else pa.table({c: pa.array([], pa.string()) for c in cols})
    if table.num_rows != expected:
        raise RuntimeError(f"{name}: got {table.num_rows:,} rows, portal count {expected:,}")
    pq.write_table(table, out_dir / f"{name}.parquet", compression="zstd")
    return name, {"rows": expected, "where": where, "select": ",".join(cols), "order": ":id", "page_size": PAGE,
                  "url": socrata_url(src, "csv", {"$select": ",".join(cols), "$where": where, "$order": ":id"}),
                  "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def run_source(p, key, src, workers):
    out_dir = p.raw / key
    out_dir.mkdir(parents=True, exist_ok=True)
    note_path = out_dir / "sources.json"
    note = json.loads(note_path.read_text()) if note_path.exists() else {}
    note.update({k: src[k] for k in ("portal", "domain", "dataset", "date_field", "start", "end") if k in src})
    note["page"] = f"https://{src['domain']}/d/{src['dataset']}"
    note.setdefault("months", {})
    todo = []
    for a, b in months(src["start"], src["end"]):
        name = a.strftime("%Y-%m")
        f = out_dir / f"{name}.parquet"
        rec = note["months"].get(name)
        if rec and f.exists() and pq.ParquetFile(f).metadata.num_rows == rec["rows"]:
            continue
        todo.append((a, b))
    print(f"{key}: {len(todo)} months to fetch", flush=True)
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_month, src, a, b, out_dir): a for a, b in todo}
        for fut in cf.as_completed(futs):
            name, rec = fut.result()
            note["months"][name] = rec
            note["rows"] = sum(m["rows"] for m in note["months"].values())
            note_path.write_text(json.dumps(note, indent=1, sort_keys=True))
            print(f"  {key} {name}: {rec['rows']:,} rows", flush=True)
    write_source_md(out_dir, key, src, note)


def write_source_md(out_dir, key, src, note):
    ms = note["months"]
    first, last = min(ms), max(ms)
    lines = [f"# Source: {src.get('title', key)}", "",
             f"- Portal page: {note['page']}",
             f"- API: `https://{src['domain']}/resource/{src['dataset']}.csv`, one request series per calendar month",
             f"- Filter: `{src['date_field']}` from {src['start']} to {src['end']}" + (f", and `{src['where']}`" if src.get("where") else ""),
             f"- Columns: `{', '.join(src['columns'])}` (all kept as text, as served)",
             f"- Ordered by `:id`, paged {PAGE:,} rows at a time; each month's rows checked against the portal's `count(*)` for the same filter",
             f"- Fetched: {min(m['fetched_at'] for m in ms.values())} to {max(m['fetched_at'] for m in ms.values())} (UTC)",
             f"- Rows: {note['rows']:,} in {len(ms)} monthly files, {first} to {last}", "",
             "Every month's exact URL, filter, time and count is in `sources.json` beside this note.", ""]
    (out_dir / "SOURCE.md").write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--source", action="append")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    p = RTProject(a.config)
    for key, src in p.cfg["sources"].items():
        if a.source and key not in a.source:
            continue
        if src.get("portal", "socrata") != "socrata":
            sys.exit(f"{key}: only Socrata sources are supported so far")
        run_source(p, key, src, a.workers)


if __name__ == "__main__":
    main()
