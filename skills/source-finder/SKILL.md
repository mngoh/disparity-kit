---
name: source-finder
description: Find a US city's public incident data for a disparity analysis. Searches open-data portals (Socrata, ArcGIS) for incident-level datasets, checks each for victim race, sex, dates, offense codes and location, flags records-system changes and personal copies, finds police district boundaries, and downloads the chosen dataset fresh through its API. Use when starting a new city, or when the user asks whether a city publishes usable data.
argument-hint: "\"<city, state>\" [topic, for example assault]"
---

# Source finder

Answers one question: does this city publish incident data that can support a race and sex rate analysis, and where?

Kit: `~/.claude/disparity-kit` (Python at `~/.claude/disparity-kit/.venv/bin/python`, scripts in `kit/`). Below, `K=~/.claude/disparity-kit` and `PY=$K/.venv/bin/python`.

## 1. Search

```bash
$PY $K/kit/sources.py search "<City, ST>" [--topic assault] --out <project>/out/search.json
```

It searches Socrata's catalog and ArcGIS Online and scores each dataset on what the analysis needs: victim race (most), victim sex and age, a date, an offense code, coordinates or a district. Under each hit it prints whose race the columns record and any warnings.

If nothing official turns up, find the city's portal with a web search ("<city> police open data crime incidents") and rerun with `--domain <portal domain>`. Some cities publish through a state portal or only through the FBI (see step 5).

## 2. Shortlist

Keep datasets published by the agency or city itself. Skip the ones flagged as a personal copy or extract (`_WFL1`, `Copy`, class projects). They are snapshots, often stale or filtered.

## 3. Inspect the top one to three

```bash
$PY $K/kit/sources.py inspect <dataset url> --out <project>/out/source_<name>.json
```

Read the whole printout:

- **Whose race.** `race describes: suspect` or `arrestee` or `person stopped` is enforcement data. It supports a different question. Say so; do not treat it as victim data.
- **Code legends.** Inspect reads code lists from the portal's column descriptions (LAPD writes `A - Other Asian B - Black ...` into `vict_descent`). Note any race column without a legend: its codes will need the agency's code list.
- **Dates.** The date range and thin months. A file that stops or thins out often means a move to NIBRS, and the new system is usually a separate dataset. The newer one is the replication source.
- **Location.** No coordinates means no tract model. No district either means no location test (as in DC).
- **Ethnicity column.** Hispanic victims are not in the race codes. Flag it for schema-mapper.
- **Victim type.** NIBRS-style files mix people with businesses and society. Only individuals count.
- **Links** in column descriptions often point to code lists and boundary files.

## 4. Ask the user to confirm

Show a short table: dataset, publisher, rows, date range, whose race, location fields, problems. Recommend a primary source and, if there is one, a second source for `/replicate-check` (another records system or period). Wait for the user's choice before downloading.

## 5. When no dataset has victim race

Say so plainly. That is a finding about the city's data, not a failure. Options to offer:

- The FBI's NIBRS files for the state, which carry victim race, sex, age and ethnicity for agencies that report to NIBRS (DC was built this way). See `/schema-mapper`, "NIBRS sources".
- An enforcement question if the city publishes arrests or stops with race (a different analysis, with its own caveats).
- Stop.

## 6. Boundaries

```bash
$PY $K/kit/sources.py search "<City, ST>" --boundaries --agency <LAPD, CPD, MPD...> --out <project>/out/boundaries.json
```

Prefer a layer whose polygon count equals the number of districts in the data (LAPD: 21 divisions, not 1,135 reporting districts). `/schema-mapper` tests the match by location, so a wrong pick shows up there.

## 7. Download

```bash
$PY $K/kit/sources.py fetch <dataset url> --out <project>/data/raw/<name>.csv
```

Every row comes through the API, raw codes untouched, with `<name>.csv.source.json` beside it recording the url, filter, time and row count. Check that the row count matches what inspect reported. Download the whole dataset, not a filtered one: `/data-audit` needs every offense code to find the related ones. Add `--where` only for files too large to pull whole, and record why.

## Never

- Use an old download or a personal copy when the agency's own dataset is available. Portals are revised with late reports.
- Choose a dataset for the result it might give.
- Call race "victim race" without checking whose race the column records.

## Output

For the user: the chosen source and why, the replication source if any, the boundary candidates, and anything that limits the analysis (no location, separate ethnicity, a records-system break). Files: `out/search.json`, `out/source_<name>.json`, `out/boundaries.json`, `data/raw/<name>.csv` and its `.source.json`.
