# disparity-kit

Claude Code skills for race and sex disparity analysis of public police data: from a raw incident file to a published, caveated findings page.

Built from the [Los Angeles assault analysis](https://mngoh.github.io/Los-Angeles-CA-Assault-Victim-Rates-by-Race-and-Sex-2020-2023/) ([code](https://github.com/mngoh/Los-Angeles-CA-Assault-Victim-Rates-by-Race-and-Sex-2020-2023)), and tested by reproducing it from a config file.

## What it has produced

| Analysis | Source | Result |
|---|---|---|
| [59 large US cities, 2022 to 2025](https://mngoh.github.io/US-Large-Cities-Assault-Victim-Rates-by-Race-and-Sex-2022-2025/) | FBI NIBRS, 59 departments | Black women's police-recorded assault rate about 3.8x White women's in the typical city, higher in every city |
| [What that number measures](https://mngoh.github.io/Police-Records-vs-Survey-Assault-Victims-by-Race-and-Sex-2015-2025/) | NCVS, NHAMCS, 911 calls in four cities | Hospitals see the same gap; the survey does not; police recording adds little |
| [Nine cities side by side](https://mngoh.github.io/Nine-Cities-Assault-Victim-Rates-by-Race-and-Sex-2020-2025/) | FBI NIBRS and city records | The pattern holds in all nine |
| [Los Angeles](https://mngoh.github.io/Los-Angeles-CA-Assault-Victim-Rates-by-Race-and-Sex-2020-2023/), [DC](https://mngoh.github.io/DC-Assault-Victims-by-Race-and-Sex-2022-2025/), [Baltimore](https://mngoh.github.io/Baltimore-MD-Assault-Victim-Rates-by-Race-and-Sex-2022-2024/), [Dallas](https://mngoh.github.io/Dallas-TX-Assault-Victim-Rates-by-Race-and-Sex-2022-2025/) | City records and NIBRS | Single-city analyses with tract models, replication and city-specific checks |

The seven cities analyzed first were run again through the 59-city pipeline on the same data: every rate and ratio matched exactly.

## Skills

| Skill | Does |
|---|---|
| `/new-city` | Starts from a city name: runs `/source-finder` and `/schema-mapper`, then hands the draft config to `/disparity-analysis`. |
| `/source-finder` | Searches open-data portals (Socrata, ArcGIS) for incident data, checks whose race each dataset records, code legends, dates, records-system breaks and location, finds district boundaries, and downloads the chosen dataset through its API. |
| `/schema-mapper` | Drafts `analysis.json` from the data: columns, race and sex maps from the portal's legends, Census place, district names matched to polygons by location. Flattens FBI NIBRS state files for cities whose portal has no victim race. |
| `/disparity-analysis` | Runs the whole pipeline in order, and asks the three decisions that belong to a person: the question, what counts, the window. |
| `/data-audit` | Every offense code, monthly coverage with records-system breaks, missing and coded-missing values, duplicates. Works on any CSV. |
| `/rate-denominators` | ACS population by group, sex and age for a place and its tracts (Census Reporter, no key), tract socioeconomics, district assignment, race-coding bound. |
| `/disparity-tests` | Rates, age standardization, location, type, time, premises, weapons, subtypes, and a tract-level Poisson adjustment ladder with pairwise and sensitivity runs. |
| `/replicate-check` | The same rates in a second source (new records system, later period, another agency), definitions matched. |
| `/racial-discrimination-check` | Screens the data and the writeup for racial bias: race coding, overlapping denominators, where race is missing, unstable comparisons, causal and offender language, stereotypes, essentializing, people-share claims, missing caveats. |
| `/findings-page` | The page and README results block, every number generated from the outputs, plus render checks and publishing. |

## Install

```bash
git clone https://github.com/mngoh/disparity-kit && cd disparity-kit && ./install.sh
```

`install.sh` links each skill into `~/.claude/skills/`, links the repo to `~/.claude/disparity-kit`, and creates `.venv` with the requirements. Edits in the repo take effect immediately. `./install.sh --remove` undoes the links.

## Use

In Claude Code, from a folder holding the data:

```
/disparity-analysis data/incidents.csv
```

Or start from a city name, and the kit finds the data and drafts the config:

```
/new-city "Chicago, IL" assault victims
```

Or run any step alone. Each project keeps one `analysis.json` ([reference](docs/config.md)); outputs go to `out/`. See [examples/la-assault](examples/la-assault/analysis.json).

## Layout

```
kit/          common.py, audit.py, denominators.py, analyze.py, replicate.py, bias_scan.py, build_page.py,
              sources.py, fields.py, schema.py, prepare.py, nibrs.py (finding and mapping a new city's data)
skills/       one folder per skill, each with SKILL.md
docs/         config.md (every config key), lessons.md (mistakes this kit checks for)
examples/     la-assault/analysis.json
```

## Limits

The kit computes; it does not decide. Which offenses count, which groups to compare and what a result means stay with the analyst, and the skills ask rather than guess. Police data counts reports that reached the police, by race as officers recorded it; every page says so.

## Tests

```bash
.venv/bin/python -m unittest tests.test_kit
```

Age bands, weapon classes, ACS band slicing, and an end-to-end run of `analyze.py` on a synthetic project with known counts (rates, ratios, the race-coding bound, age standardization).
