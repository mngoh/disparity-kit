---
name: disparity-analysis
description: End-to-end race and sex disparity analysis of police incident or victim data, from raw files to a published findings page. Runs data-audit, rate-denominators, disparity-tests, replicate-check, racial-discrimination-check and findings-page in order. Use when the user wants to analyze who is affected by a crime or enforcement outcome across racial groups, as population rates, with rival explanations tested.
argument-hint: "[path to data file or project folder]"
---

# Disparity analysis

Turns incident-level data into per-capita rates by group and sex, tests whether plain explanations account for a focus group's gap, adjusts with a tract-level model, checks the result against a second source, screens for racial bias, and publishes a page.

Kit: `~/.claude/disparity-kit` (scripts in `kit/`, Python at `~/.claude/disparity-kit/.venv/bin/python`).
Config reference: `~/.claude/disparity-kit/docs/config.md`. Lessons from past runs: `~/.claude/disparity-kit/docs/lessons.md`. Read both before starting.
Worked example: `~/.claude/disparity-kit/examples/la-assault/analysis.json` (LAPD assault victims 2020 to 2023).

## Before any analysis: three decisions belong to the user

Ask these together, once, after the audit (step 1) so the choices are informed. Do not guess them.

1. **The question.** Which group and sex is the focus, compared against which groups? (Example: Black women against Hispanic, White and Asian women.)
2. **What counts.** Which offense codes are in, which are out, and which form subtypes (flags) such as intimate partner. Show the user the full code list from the audit; related offenses are often coded separately.
3. **The window.** Full calendar years where possible. Stop before any records-system change the audit finds.

## Steps

Run each skill's step in order. Each one can also be run on its own.

1. `/data-audit` on every raw file. Read the whole report.
2. Ask the three decisions. Write `analysis.json` in a project folder (see the config reference). Keep raw data paths relative.
3. `/rate-denominators`: population, tracts, districts, race-coding bound.
4. `/disparity-tests`: rates, rival explanations, model.
5. `/replicate-check` if a second source exists (new records system, later years, another agency). Skip with a note if none does.
6. `/findings-page`: build the page and README results block.
7. `/racial-discrimination-check` on the built page and README. Fix every real flag, rebuild, rerun until the data section is reflected in the caveats and the writeup flags are resolved or justified.
8. Show the user the page locally (screenshot desktop and mobile). Publish only when asked.

## Rules that apply throughout

- No imputation of missing values, ever. Missing stays missing and is reported.
- Rates count reports, not people. Never write "1 in X".
- Every number in prose comes from the JSON outputs, never typed by hand.
- No em dashes. Short sentences. Bullets over pills.
- Say what the data shows and what it cannot. The finding is "the data shows X; it does not explain why", unless a measured factor does explain it.
- Verify any external fact or citation (open the source) before it goes on a page.
- Commit without adding Claude as co-author.
