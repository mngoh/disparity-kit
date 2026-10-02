---
name: findings-page
description: Build a published findings page (dark theme, red focus group, blue comparisons, outlined Chart.js bars, mobile friendly, no em dashes) and a README results block from disparity-analysis outputs, verify it renders, and publish to GitHub Pages when asked. Use after disparity-tests, or to rebuild a page after any change to results or config.
argument-hint: "[path to analysis.json]"
---

# Findings page

```bash
~/.claude/disparity-kit/.venv/bin/python ~/.claude/disparity-kit/kit/build_page.py <project>/analysis.json
```

Writes `<project>/index.html` (or `page_path`) and replaces the block between `<!-- results:start -->` and `<!-- results:end -->` in `<project>/README.md`. If the README has no markers, the block is written to `out/README_results.md` to paste in once.

## Structure

Headline, the question and its answer, dataset cards, overview charts, the rival-explanation tests, the model, replication (if `out/replication.json` exists), caveats, method and sources. Every sentence with a number is generated from `out/results.json`. Do not hand-edit numbers into the HTML; change the config or the generator.

## The headline

Set `headline` in the config once the user has seen the results. It must:
- name the comparison groups, not "other races";
- use ranges that survive the race-coding worst case, or say "about";
- say what is and is not explained in plain words (for example "and nothing we measured explains why").

## Verify before showing the user

1. HTML balance and no em dashes (the builder replaces any that slip in).
2. Screenshot at desktop width (headless Chrome: `--window-size=1440,6000 --virtual-time-budget=9000 --screenshot`) and read it.
3. Mobile at 375px: use the browser pane's mobile emulation, not headless Chrome (headless has a minimum width that fakes overflow). Check `document.documentElement.scrollWidth === innerWidth` and that every canvas has a chart.
4. Run `/racial-discrimination-check` on the built page.

## Publish (only when the user asks)

Commit (no Claude co-author line), push, then confirm the GitHub Pages build: `gh api repos/<owner>/<repo>/pages/builds/latest --jq '.status + " " + .commit[0:7]'` should read `built <sha>`, and `curl` the live page for a phrase from the new content.
