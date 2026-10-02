# disparity-kit

Claude Code skills for race and sex disparity analysis of public police data: from a raw incident file to a published, caveated findings page.

Built from the [Los Angeles assault analysis](https://mngoh.github.io/LA-Crime/) ([code](https://github.com/mngoh/LA-Crime)), and tested by reproducing it from a config file.

## Skills

| Skill | Does |
|---|---|
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

Or run any step alone. Each project keeps one `analysis.json` ([reference](docs/config.md)); outputs go to `out/`. See [examples/la-assault](examples/la-assault/analysis.json).

## Layout

```
kit/          common.py, audit.py, denominators.py, analyze.py, replicate.py, bias_scan.py, build_page.py
skills/       one folder per skill, each with SKILL.md
docs/         config.md (every config key), lessons.md (mistakes this kit checks for)
examples/     la-assault/analysis.json
```

## Limits

The kit computes; it does not decide. Which offenses count, which groups to compare and what a result means stay with the analyst, and the skills ask rather than guess. Police data counts reports that reached the police, by race as officers recorded it; every page says so.
