---
name: data-audit
description: Audit a raw incident or crime CSV before analysis. Lists every offense code with counts, monthly coverage with records-system breaks and partial periods flagged, missing and coded-missing values (0, X, UNKNOWN), and duplicate ids. Use on any new dataset before computing rates, or when the user asks whether a dataset can be trusted.
argument-hint: "[path to CSV]"
---

# Data audit

Run:

```bash
~/.claude/disparity-kit/.venv/bin/python ~/.claude/disparity-kit/kit/audit.py <file.csv> --out <project>/out/audit_<name>.md
```

Add `--date`, `--code`, `--desc`, `--id` if the guessed columns (printed at the top) are wrong.

If the data lives on an open-data portal (Socrata, ArcGIS), prefer pulling it fresh with the portal API over using an old download: the portal is often revised with late reports and extended past the old file's end. For Socrata, `https://<domain>/api/views/<id>.json` lists the columns, and `$select=...,count(*)&$group=...` aggregates server side, which is how to check codes and monthly counts without downloading everything.

## Read the report for

1. **Every offense value.** Read the full list, not the top ten. Related offenses are often coded separately: intimate partner versions, offenses against police or children, attempts, sexual battery. Write down which codes plausibly belong to the question and show the user.
2. **Coverage breaks.** Months under 60% of the median mean a records-system change, a partial period, or reporting lag at the end. Agencies moving to NIBRS thin out their old file for months. Recommend full calendar years before the break.
3. **Coded missing.** `0` coordinates, `X` sex, `UNKNOWN` race are missing values in disguise. Note the share per column.
4. **Duplicates.** Decide whether a row is a report, a victim or an offense before counting.
5. **Race and sex codes.** List every raw value so the race mapping in the config is complete.

## Never

- Back-fill, forward-fill or otherwise impute missing values.
- Drop codes or months without telling the user.

## Output

A short summary for the user: rows, window, the offense codes that look relevant (grouped), coverage problems, and the decisions they need to make. Save the full report beside the project.
