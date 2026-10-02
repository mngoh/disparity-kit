---
name: schema-mapper
description: Turn a downloaded incident file into a draft analysis.json. Maps columns to the kit's standard names, proposes race and sex maps from the portal's code legends, detects separate ethnicity columns, finds the Census place id, and matches district names to boundary polygons by location. Also flattens FBI NIBRS state files into victim files. Leaves the question, the offense codes and the window to the user. Use after source-finder, or when a new file needs a config.
argument-hint: "[project folder]"
---

# Schema mapper

Writes `analysis.draft.json`: every config key that can be read off the data, with evidence for each, and none of the three decisions.

`K=~/.claude/disparity-kit`, `PY=$K/.venv/bin/python`. Config reference: `$K/docs/config.md`.

## 1. Columns and codes

```bash
$PY $K/kit/schema.py map <project>/data/raw/<file>.csv --source <project>/out/source_<name>.json --out <project>/out/mapping.json
```

`--source` is the inspect output from `/source-finder`. It carries the portal's column descriptions and code legends. Without it the map works from names and values alone, and says so.

Show the user the role table and check it yourself:

- **date** should be when the incident happened, not when it was reported.
- **race, sex, age** should be the victim's (the subject column says whose).
- **age**: the share at zero or below is coded missing. LAPD uses 0 for unknown.
- **lat, lon**: the zero share is coded missing.
- **district**: names, not numbers, when both exist (the boundary file usually has names).
- **id**: duplicates mean rows are offenses or victims, not reports. Decide which before counting.

**Race map.** Each value carries its basis:

- `legend`: read from the portal's code list. Trust it, but read it.
- `label`: the value is a word ("BLACK", "White/Caucasian").
- `convention (no legend: confirm)`: a bare letter read by common NIBRS usage. Find the agency's code list before relying on it.
- `ambiguous` or `no ACS group`: for example a combined "Asian/Pacific Islander", "Middle Eastern", or "Other". These have no single Census denominator. They stay out unless the user decides otherwise, and the number left out goes in the caveats.

The draft keeps `race_map_by_group` (every recognized code, grouped). The final `race_map` holds only the chosen groups' codes. A code mapped to a group outside `groups` would count as known race and shrink the "who is counted" caveat.

**Ethnicity in its own column.** The race codes then hold no Hispanic victims. Combining them is a method choice, so ask the user. The DC rule (Hispanic of any race first, otherwise the recorded race, and unknown ethnicity keeps the recorded race) matches how the ACS groups are built. `prepare.py --hispanic-first` applies it. Whatever is chosen, quantify it in the caveats: DC's showed how the ratios move if White victims with unknown ethnicity were Hispanic.

## 2. Census place

```bash
$PY $K/kit/schema.py place "<City, ST>" --out <project>/out/place.json
```

Check that the police agency's area is the Census place. A city police department matches the city (`16000US...`). A county sheriff covers unincorporated areas and contract cities, not the county total. A transit or campus agency matches no place. Say so if they differ.

## 3. Districts

```bash
$PY $K/kit/schema.py districts <project>/data/raw/<file>.csv --mapping <project>/out/mapping.json --geojson "<geojson url from source-finder>" --out <project>/out/districts.json
```

It places a sample of incident points in the polygons. For each polygon, the district name most of its points carry is its data name. It picks the boundary field that matches one to one, and writes `rename` only where title-casing the polygon name does not already give the data name. LA: `APREC`, three renames, 99.1% of points agreeing.

- Agreement over 95% and one to one: use it.
- Weak polygons (under 90%), data districts with no polygon, or many points outside: the boundaries are from another period (redistricting) or another level (reporting districts, not divisions). Try the next candidate. If none fits, leave `districts` out. The location test then switches off; say so.

## 4. Draft

```bash
$PY $K/kit/schema.py draft --mapping <project>/out/mapping.json --districts <project>/out/districts.json --place <project>/out/place.json --source <project>/out/source_<name>.json --out <project>/analysis.draft.json
```

Null on purpose: `title`, `window`, `event`, `groups`, `focus`, `flags`, and the incident files by kind. `/disparity-analysis` asks the three decisions after the audit, then `prepare.py` splits the raw file by the chosen codes:

```bash
$PY $K/kit/prepare.py data/raw/<file>.csv --code <code column> --kind simple=<codes> --kind aggravated=<codes> --out-dir data --prefix <city>_
```

Add `--keep <column>=<values>` to keep only individuals or one agency, and `--hispanic-first --race <col> --ethnicity <col>` for a combined race column. If the district column changes scheme inside the window (Baltimore redistricted in 2023), add `--district-from out/districts.json --lat <col> --lon <col>`: it places every incident in the boundary file's districts by its coordinates, as `district_geo`, named the way the denominators name them. Each run writes `data/prepare.json` with the rule and counts.

## NIBRS sources

When the city's portal has no victim race (DC), use the FBI's state files. The user downloads one zip per year from the Crime Data Explorer (Documents & Downloads, NIBRS data by state) into `data/raw/`. Then:

```bash
$PY $K/kit/nibrs.py agencies data/raw/*.zip
$PY $K/kit/nibrs.py flatten data/raw/*.zip --ori <ORI> --out data/interim/victim_offenses.csv
```

Run `/data-audit` on `victim_offenses.csv` with `--code offense_code --desc offense_name --id victim_id --date incident_date`. After the decisions:

```bash
$PY $K/kit/nibrs.py victims data/interim/victim_offenses.csv --ori <ORI> --kind aggravated=13A --kind simple=13B --race-rule hispanic-first --out-dir data --prefix <city>_
```

The victim files have standard columns: `victim_id`, `incident_date`, `race_group` (map `H`, `B`, `W`, `A`, `I`, `P`), `sex`, `age`, `premise`, `weapon`, `offense_code`, and `partner` (`Y`/`N`) and `resident_status` for flags. NIBRS has no address or district, so no location test and no tract model. Pick the agency by ORI and say which agencies were left out (DC: Metro Transit Police) and why.

## Never

- Guess a race code without saying it is a guess.
- Fill in the question, the codes or the window.
- Keep a district match the agreement figures do not support.

## Output

For the user: the role table, the race and sex maps with anything left out, the ethnicity question if there is one, the place, the district match, and the draft path. Files: `out/mapping.json`, `out/place.json`, `out/districts.json`, `analysis.draft.json`.
