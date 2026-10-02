---
name: racial-discrimination-check
description: Audit a race-disparity analysis and its writeup for racial bias before publishing. Checks the data (officer-recorded race against Census definitions, overlapping denominators, unmapped and missing race and where it is concentrated, unstable small-group comparisons, enforcement and reporting effects) and the writeup (causal claims, offender implications, deficit or stereotype language, essentializing phrasing, capitalization, people-share claims, explained-away framing, missing caveats). Use on any page, README, draft or pitch that compares racial groups, and always before publishing or sending to a reporter.
argument-hint: "[path to analysis.json] [extra files to review]"
---

# Racial discrimination check

Two kinds of bias can enter a disparity analysis: in the data (who gets counted as what, and where) and in the interpretation (what the words invite a reader to believe). This skill screens both, then requires a judgment pass. The screen finds candidates. You decide.

## 1. Run the screen

```bash
~/.claude/disparity-kit/.venv/bin/python ~/.claude/disparity-kit/kit/bias_scan.py <project>/analysis.json [draft.md pitch.md ...]
```

It reviews the page and README by default; pass any other text (pitch emails, social posts, drafts). Output: `out/bias_review.md`.

## 2. Judge every flag in context

For **data** flags, quantify the effect and decide whether the headline survives:
- **Race coding**: the worst-case ratios must appear in the caveats. If the headline does not hold at the worst case, soften the headline.
- **Where race is missing**: if missing race clusters where the focus group is scarce, other groups' rates are understated and the gap overstated (and the reverse). Estimate the size: redistribute the unknowns in proportion and recompute. State the direction in the caveats.
- **Unmapped race codes** (often "Other"): confirm none belong to a group.
- **Unstable comparisons**: keep them out of the headline.
- **Enforcement and reporting**: the writeup must say police data cannot separate more policing or more reporting from more victimization.
- **Controls are not neutral**: neighborhood and income are shaped by segregation and discrimination. A gap that shrinks after them has been located, not explained away.

For **writeup** flags, read the sentence. Many will be fine (for example "Nothing here measures causes" contains a causal word on purpose). Fix the ones that:
- imply who the offenders are, or attribute the gap to a group's culture, character or behavior;
- use a group as a noun ("Blacks"), or describe what a group "is";
- claim causes the data does not measure, or certainty it does not have;
- turn report rates into shares of people ("1 in 28");
- capitalize race words inconsistently;
- lead with the most extreme comparison when it is the least stable one.

Then answer the reviewer questions at the end of the report in prose, for the user.

## 3. Fix, rebuild, rerun

Edit the config (`extra_caveats`, `headline`, `question`) or the prose source, rebuild with `/findings-page`, and rerun this check until every data flag is reflected in the caveats and every writeup flag is fixed or justified in one line.

## 4. Report to the user

A short list: what was found, what was changed, what remains a judgment call. Never present the screen's absence of flags as proof of no bias.
