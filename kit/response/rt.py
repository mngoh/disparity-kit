"""Shared helpers for the response-time scripts: the project config, DuckDB, paths and the standard call table.

Every script takes the path to a project's response.json. Paths inside it are relative to the folder that
holds it. Raw downloads go to <project>/data/raw/<source>/, the standard call table to <project>/data/calls.parquet,
outputs to <project>/out/, and cached downloads (boundaries, Census) to <project>/out/cache/.

Standard call table (one row per CAD event, written by prepare.py):
  event_id, source, t_create, t_dispatch, t_arrive, t_close, priority, call_type, call_desc, district,
  lat, lon, origin ('public', 'officer', 'sensor'), origin_rule, plus city-specific extras named in the config.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from common import UA, Project, _jsonable, fetch, today  # noqa: E402,F401

import duckdb  # noqa: E402


class RTProject(Project):
    """A response-time project. Same layout as a disparity project, plus data/ for large files."""

    def __init__(self, config_path):
        super().__init__(config_path)
        self.data = self.root / "data"
        self.raw = self.data / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.calls = self.data / "calls.parquet"

    def con(self, threads=None, memory="8GB"):
        c = duckdb.connect()
        c.execute(f"SET memory_limit='{memory}'")
        c.execute(f"SET temp_directory='{self.data / 'duckdb_tmp'}'")
        if threads:
            c.execute(f"SET threads={threads}")
        return c

    @property
    def priorities(self):
        """Priority categories in order, most urgent first, as {'value': ..., 'label': ...}."""
        return self.cfg["priority"]["order"]

    def priority_label(self, value):
        return next((p["label"] for p in self.priorities if p["value"] == value), value)

    def periods(self):
        return self.cfg["periods"]


def write_md(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print("wrote", path)


def md_table(rows, cols, fmt=None):
    """Markdown table from a list of dicts. fmt maps a column to a format string or function."""
    fmt = fmt or {}
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c)
            f = fmt.get(c)
            if v is None or (isinstance(v, float) and v != v):
                cells.append("")
            elif callable(f):
                cells.append(f(v))
            elif f:
                cells.append(format(v, f))
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def dump(obj):
    return json.dumps(obj, indent=1, default=_jsonable)
