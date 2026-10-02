---
name: replicate-check
description: Test whether a disparity finding holds in a second, independent source (a new records system such as NIBRS, a later period, or another agency), with offense definitions matched, by computing the same rates against the same population and comparing the focus group's ratios. Use after disparity-tests, or when the user asks whether a result replicates.
argument-hint: "[path to analysis.json]"
---

# Replicate check

A finding that appears in one source could be an artifact of how that source codes things. The same gap in a second source, with no overlap in time or a different records system, is much harder to dismiss.

## 1. Find the second source

Look for: the agency's NIBRS files (agencies moving to NIBRS often publish them separately), a later or earlier period, the state's crime data, or a neighboring agency. Check field lists first; NIBRS victim files often carry offense labels that identify subtypes such as intimate partner.

## 2. Match definitions

Write down, for the user, how each category in the first source maps to the second. Exclude the same victim types in both (for example officers and children if the first source coded them separately). Labels rarely map one to one, so compare ratios between groups, not raw levels between sources. Drop ramp-up months after a system change and the most recent weeks (reporting lag).

## 3. Aggregate and compare

Pull counts for the focus sex by category and group (server-side aggregation where the portal supports it), and write `out/replication_counts.json` in the shape described at the top of `~/.claude/disparity-kit/kit/replicate.py`, including `definition_notes`. Then:

```bash
~/.claude/disparity-kit/.venv/bin/python ~/.claude/disparity-kit/kit/replicate.py <project>/analysis.json
```

## 4. Report

- Ratios within about 10% across sources: the finding replicates.
- A comparison that moves more than 25%: unstable; do not headline it.
- Say what changed between sources besides time (coding, coverage) so the reader can judge.

Save the fetch code in the project (`scripts/` or `fetch_replication.py`) so the result can be rebuilt.
