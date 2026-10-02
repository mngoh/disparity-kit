---
name: rate-denominators
description: Build population denominators for per-capita rates from the American Community Survey via Census Reporter (no API key) for a US place and its census tracts, by race group, sex and age band, with tract socioeconomics, tract geometry, police-district assignment and the race-coding bound. Use when counts need to become rates per 100,000 residents.
argument-hint: "[path to analysis.json]"
---

# Rate denominators

Needs `place.census_geoid` (for example `16000US0644000` for Los Angeles city), `groups`, and optionally `districts` in `analysis.json`. Find a place's geoid with `https://api.censusreporter.org/1.0/geo/search?q=<name>&sumlevs=160`.

```bash
~/.claude/disparity-kit/.venv/bin/python ~/.claude/disparity-kit/kit/denominators.py <project>/analysis.json
```

Writes `out/population.json` and caches downloads in `out/cache/`.

## What it builds

- Residents by group, sex and 14 ACS age bands, citywide and per tract (ACS B01001 race-iterated tables: Black B01001B, Hispanic B01001I, White non-Hispanic B01001H, Asian B01001D, plus AIAN, NHPI, Other, Multiracial).
- Tract socioeconomics: poverty rate, log median income, unemployment, renter share, log density.
- Each tract's district, by a point inside the tract, from the district GeoJSON. `districts.rename` maps GeoJSON names to the names in the incident data. Check the printout: the number of unassigned tracts should be small.
- Race-coding bound: residents of each group alone against alone or in combination (B02001, B02008 to B02012).
- Hispanic overlap: share of each race-alone group that is also Hispanic (B03002), who therefore sit in two denominators.

## Check

- The district names in the data match the population's districts (`analyze.py` warns on mismatches).
- The ACS window overlaps the incident window. State any mismatch as a caveat.
- `census.gov` APIs need a key; Census Reporter does not, but it needs a User-Agent (the kit sends one).
