---
name: disparity-tests
description: Compute victimization or enforcement rates by race group and sex, test rival explanations for a focus group's gap (age standardization, location by district, offense type, year, premises, weapons, subtypes such as intimate partner), and fit a tract-level Poisson adjustment ladder with pairwise comparisons and covariate sensitivity. Use after rate-denominators, or when the user asks whether a disparity survives controls.
argument-hint: "[path to analysis.json]"
---

# Disparity tests

```bash
~/.claude/disparity-kit/.venv/bin/python ~/.claude/disparity-kit/kit/analyze.py <project>/analysis.json
```

Reads the incidents and `out/population.json`; writes `out/results.json`.

## What it computes

- **Rates** per 100,000 residents per year for every group and sex; the focus group's ratio to each other group; the focus sex's share of victims by type. Partial years are weighted by their share of the year.
- **Age**: age-specific rates and direct standardization to one combined age mix.
- **Location**: indirect standardization (the focus group's rate if it faced other groups' rate in each district) and the ratio inside every district.
- **Type, time, premises, weapons, flags**: each a rival explanation. Flags (for example intimate partner) give rates with and without.
- **Model**: Poisson GLM on tract x group x age band x year cells, log exposure offset, robust (HC0) errors. Ladder: group only, + age, + year, + district fixed effects, + tract socioeconomics, + each configured covariate. Also by type, pairwise against each group separately, and a sensitivity run for each covariate definition.
- **Race-coding bound**: ratios if the focus group's denominator were alone or in combination.

## Read the results for

- Coverage: the share of focus-group victims who fall in tracts with resident population of their group and age. Low coverage means victims were assaulted away from where people like them live; say so.
- Pairwise spread: a pooled comparison hides how different the gap is against each group. Report each.
- Which control moves the ratio most. Phrase it as "located" rather than "explained away": controls like neighborhood are shaped by segregation.
- Anything that contradicts an earlier sentence in a draft. Results change when definitions change; regenerate prose from JSON.

## Config notes

`covariates` takes tract-level CSVs with a `geoid` column; the first numeric column enters the ladder, every numeric column gets a sensitivity run. `weapon_classes` overrides the default keyword classes. `min_pop` (default 2000) suppresses rates on small populations.
