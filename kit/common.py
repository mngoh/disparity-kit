"""Shared helpers for the disparity kit: config, cached downloads, incident loading, time spans.

Every script takes the path to a project's analysis.json. Paths inside the config are relative
to the folder that holds it. Outputs go to <project>/out/, downloads are cached in <project>/out/cache/.
"""
import datetime as dt
import json
import pathlib
import sys
import urllib.request

import numpy as np
import pandas as pd

UA = "disparity-kit/1.0 (open-data rate analysis)"

# ACS "sex by age" tables by group. Hispanic is any race; White is non-Hispanic White alone.
ACS_SEX_AGE = {"Black": "B01001B", "Hispanic": "B01001I", "White": "B01001H", "Asian": "B01001D",
               "AIAN": "B01001C", "NHPI": "B01001E", "Other": "B01001F", "Multiracial": "B01001G"}
# race alone (B02001 variable) and alone-or-in-combination tables, for the race-coding bound
ALONE_VAR = {"White": "B02001002", "Black": "B02001003", "AIAN": "B02001004", "Asian": "B02001005", "NHPI": "B02001006"}
COMBO_TABLE = {"White": "B02008", "Black": "B02009", "AIAN": "B02010", "Asian": "B02011", "NHPI": "B02012"}
# ACS age bands, in table order (male 003..016, female 018..031)
AGE_BANDS = [(0, 4), (5, 9), (10, 14), (15, 17), (18, 19), (20, 24), (25, 29), (30, 34), (35, 44), (45, 54), (55, 64), (65, 74), (75, 84), (85, 200)]
AGE_LABELS = ["0-4", "5-9", "10-14", "15-17", "18-19", "20-24", "25-29", "30-34", "35-44", "45-54", "55-64", "65-74", "75-84", "85+"]
COARSE = {"0-17": [0, 1, 2, 3], "18-29": [4, 5, 6], "30-44": [7, 8], "45-64": [9, 10], "65+": [11, 12, 13]}
COARSE_OF = {i: name for name, idx in COARSE.items() for i in idx}
SEX_WORD = {"F": ("women", "woman"), "M": ("men", "man")}


class Project:
    def __init__(self, config_path):
        self.config_path = pathlib.Path(config_path).resolve()
        self.root = self.config_path.parent
        self.cfg = json.loads(self.config_path.read_text())
        self.out = self.root / "out"
        self.cache = self.out / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)

    def path(self, p):
        p = pathlib.Path(p)
        return p if p.is_absolute() else self.root / p

    def read_json(self, name):
        p = self.out / name
        if not p.exists():
            sys.exit(f"missing {p}: run the earlier step first")
        return json.loads(p.read_text())

    def write_json(self, name, obj):
        (self.out / name).write_text(json.dumps(obj, indent=1, default=_jsonable))
        print("wrote", self.out / name)

    # config accessors with defaults
    @property
    def groups(self):
        return self.cfg["groups"]

    @property
    def focus(self):
        f = self.cfg["focus"]
        return f["group"], f.get("sex", "F")

    @property
    def focus_label(self):
        g, s = self.focus
        return self.cfg["focus"].get("label", f"{g} {SEX_WORD[s][0]}")

    @property
    def others(self):
        return [g for g in self.groups if g != self.focus[0]]

    @property
    def min_pop(self):
        return self.cfg.get("min_pop", 2000)

    def window(self):
        w = self.cfg["window"]
        return pd.Timestamp(w["start"]), pd.Timestamp(w["end"])


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


def fetch(project, url, name):
    """Download url once into the project cache and return the path."""
    path = project.cache / name
    if not path.exists():
        print("downloading", name)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=300) as r:
            path.write_bytes(r.read())
    return path


def read_any_csv(path):
    try:
        return pd.read_csv(path, low_memory=False)
    except UnicodeDecodeError:
        return pd.read_csv(path, low_memory=False, encoding="latin-1")


def load_incidents(project):
    """All incident files, standardized: date, year, race, sex, age, lat, lon, district, premise, weapon, code, kind, flags."""
    cfg = project.cfg
    cols = cfg["columns"]
    frames = []
    for src in cfg["incidents"]:
        d = read_any_csv(project.path(src["path"]))
        d["kind"] = src.get("kind", "all")
        frames.append(d)
    raw = pd.concat(frames, ignore_index=True)
    df = pd.DataFrame({"kind": raw["kind"]})
    for key in ["id", "race", "sex", "age", "lat", "lon", "district", "premise", "weapon", "code"]:
        df[key] = raw[cols[key]] if key in cols and cols[key] in raw else np.nan
    df["date"] = pd.to_datetime(raw[cols["date"]], errors="coerce")
    start, end = project.window()
    df = df[(df["date"] >= start) & (df["date"] <= end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))].copy()
    df["year"] = df["date"].dt.year
    df["race_raw"] = df["race"]
    df["race"] = df["race"].astype(str).str.strip().map(cfg["race_map"])
    sex_map = cfg.get("sex_values", {"F": "F", "M": "M"})
    df["sex"] = df["sex"].astype(str).str.strip().map(sex_map)
    df["age"] = pd.to_numeric(df["age"], errors="coerce")
    df.loc[df["age"] <= 0, "age"] = np.nan
    for c in ["lat", "lon"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.loc[(df["lat"].abs() < 1) | (df["lon"].abs() < 1), ["lat", "lon"]] = np.nan  # 0 is a common "missing" code
    df["code"] = df["code"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    for name, rule in cfg.get("flags", {}).items():
        col = rule.get("column", "code")  # a standard name, or any column in the incident files
        src = df[col] if col in df else raw.loc[df.index, col]
        df[name] = src.astype(str).str.strip().isin([str(v) for v in rule["values"]])
    if "id" in cols:
        before = len(df)
        df = df.drop_duplicates("id")
        if before != len(df):
            print(f"dropped {before - len(df):,} duplicate ids")
    return df


def year_spans(project):
    """Fraction of each calendar year inside the window, and the total in years."""
    start, end = project.window()
    spans = {}
    for y in range(start.year, end.year + 1):
        a, b = max(start, pd.Timestamp(y, 1, 1)), min(end, pd.Timestamp(y, 12, 31))
        days_in_year = 366 if pd.Timestamp(y, 12, 31).dayofyear == 366 else 365
        spans[y] = round(((b - a).days + 1) / days_in_year, 4)
    return spans, round(sum(spans.values()), 4)


def age_band(age):
    if pd.isna(age):
        return None
    for i, (lo, hi) in enumerate(AGE_BANDS):
        if lo <= age <= hi:
            return i
    return None


def today():
    return dt.date.today().isoformat()
