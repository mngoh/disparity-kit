---
name: new-city
description: Start a race and sex disparity analysis for a new US city from just its name. Runs source-finder (find, check and download the city's incident data), schema-mapper (draft analysis.json from the data) and then disparity-analysis (audit, decisions, rates, tests, page). Use when the user names a city or agency to analyze, or asks whether an analysis like LA's or DC's can be done somewhere else.
argument-hint: "\"<city, state>\" [topic, for example \"assault victims\"]"
---

# New city

Takes a city to a draft config, then hands off to the full pipeline:

```
/new-city
├── /source-finder        is there usable data, and where? download it
├── /schema-mapper        columns, codes, place, districts -> analysis.draft.json
└── /disparity-analysis   audit, the three decisions, rates, tests, replication, bias check, page
```

Each step can run alone. Read `~/.claude/disparity-kit/docs/lessons.md` before starting: every lesson there applies to a new city.

## 0. Set up

Ask for the topic if none was given (the LA, DC and Baltimore analyses were assault victims). Make a project folder next to the user's other projects unless they say otherwise, and inside it create `data/raw/` and `out/`.

Name the folder, and later the GitHub repo, for what it holds: place, state, what is measured and the years, `<City>-<ST>-<Topic>-Rates-by-Race-and-Sex-<start>-<end>` (for example `Richmond-VA-Assault-Victim-Rates-by-Race-and-Sex-2022-2024`). The years are only known after the decisions, so start with a working name. Rename the folder after the decisions, once downloads have finished writing into it, and before `git init`.

## 1. `/source-finder "<City, ST>" [topic]`

Checkpoint: the user confirms the primary dataset, and the replication source if there is one. Then download.

Stop here if no source has victim race, and report what exists. Do not go on with suspect or arrest race as if it described victims. Offer the NIBRS route or an enforcement question instead.

## 2. `/schema-mapper <project>`

Checkpoint: show the role table, the race and sex maps, the place and the district match. Ask about anything uncertain: codes read by convention, values with no ACS group, and how to combine a separate ethnicity column.

The result is `analysis.draft.json`.

## 3. `/disparity-analysis <project>`

Run it as written, starting from the draft:

1. `/data-audit` on the raw file. The audit's full code list is what the decisions rest on.
2. The three decisions: the question, what counts, the window. Then:
   - Split the raw file with `prepare.py` (or `nibrs.py victims`) into one file per kind.
   - Write `analysis.json` from the draft. Fill `title`, `event`, `window`, `groups`, `focus` and `flags`. List `incidents` by kind. Build `race_map` from `race_map_by_group` for the chosen groups only. Copy `columns`, `place`, `districts`, `sex_values` and `sources`. Drop the `_status`, `race_map_by_group`, `race_left_out` and `source_urls` keys. Set `place.short`.
   - Add a caveat for each value left out of the race map, and for any ethnicity rule.
3. Continue with `/rate-denominators`, `/disparity-tests`, `/replicate-check`, `/findings-page` and `/racial-discrimination-check`, as `/disparity-analysis` says.

## 4. Close

- Say what this city's data could and could not test, compared with LA (full location data) and DC (none).
- If something went wrong that `docs/lessons.md` does not cover, propose adding it there. Ask the user first; the lessons file is shared by every future run.
- Publish only when asked.

## Never

- Skip a checkpoint because the data looks like a city already done. Codes, legends and records systems differ by agency.
- Fill the three decisions from an earlier city's config without asking.
