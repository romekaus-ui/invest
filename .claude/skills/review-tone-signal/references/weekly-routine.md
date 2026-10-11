# The weekly scheduled run

The orchestrator is meant to run on a schedule, not to be retyped as a prompt.
This file holds the exact configuration of that schedule so it is
version-controlled and can be re-created without reconstructing it from memory.

Created with the `schedule` skill (cloud routines via the `RemoteTrigger` API),
not with hand-rolled cron.

## Configuration

| | |
|---|---|
| Routine ID | `trig_01MExfvhuF2g6841QWZ8htBR` — created 2026-10-11 |
| Name | `review-tone-signal weekly run` |
| Cron | `0 6 * * 1` — Mondays 06:00 UTC = **09:00 Europe/Tallinn** |
| Environment | `env_017LE8TtL82aXVN53oPVcwNv` (Default) |
| Model | `claude-sonnet-5-5` — the hard logic is in `orchestrate.py`; the agent reads the gate verdicts and relays them |
| Repository | `https://github.com/romekaus-ui/invest` |
| Tools | Bash, Read, Write, Edit, Glob, Grep |
| Output | commits to `review-tone-run/<date>`, pushes, opens a PR. Never main. |
| Connectors | none. The server attaches the account's connectors by default; they were cleared with `clear_mcp_connections`. A job that reads SEC filings has no business holding Gmail or Drive access. |

Routines are listed and managed at https://claude.ai/code/routines

## Why weekly and not daily

The inputs do not change daily, so a daily run would be identical six days out of
seven. Revenue arrives on quarterly SEC filings — one new point per company per
~13 weeks, and only at its EDGAR `filed` date. The review corpus
(`McAuley-Lab/Amazon-Reviews-2023`) **ends in September 2023** and will never
grow. The universe is point-in-time and re-formed annually. Calibration re-runs
only when the rubric version or scorer model changes.

A daily report that says nothing almost every day is one the user stops reading,
and then the one run that *did* say something goes unread too. That is a worse
failure than running less often. Weekly is slow enough that "no change" is
genuine information and fast enough to catch a newly met gate, a stage a sibling
session just built, or a drifted hash within days.

## Prerequisite

The API checks repository access when the routine is created, against the
account's claude.ai GitHub connection — not against local git credentials. A
`403 repo_access_denied` means the repo does not exist, the owner is not the one
you typed, or the GitHub App installation is scoped to selected repositories and
this one is not among them (https://github.com/settings/installations).

The routine clones the **default branch**, so work has to be on `main` before a
run can use it.

## What the cloud run can and cannot do

A cloud sandbox gets a fresh checkout and nothing else. It can evaluate every
gate from committed state, and run any stage whose inputs are committed.

It cannot see uncommitted local artefacts, the `.cache/` screen cache, or
`SEC_USER_AGENT`, and streaming the 750 GB corpus from a sandbox is slow. In
practice the weekly cloud run is an honest gate-and-staleness report; the
corpus-touching stages are better run locally, and the orchestrator reports
exactly which it could execute either way.

## The routine prompt

Verbatim, so the routine can be re-created identically. The cloud agent starts
cold, so the prompt restates the prohibitions rather than assuming them.

```text
Weekly orchestrated run of the review-tone-signal research pipeline in this repository.

You are starting cold. Everything you need is in the repo. Do this and nothing more.

STEP 1 - read the binding rules, in full, before running anything:
  - REVIEW-TONE-CORE-PROMPT.md (locked; sections 1, 8 and 9 especially)
  - .claude/commands/review-tone-run.md (the procedure you are executing)

STEP 2 - run the orchestrator from the repository root:

    python3 .claude/skills/review-tone-signal/scripts/orchestrate.py

It determines which phase the project is in, runs the stages whose gates are met, stops at the first unmet gate, appends one validated row to results/results.jsonl, regenerates the dashboard if one exists, writes results/reports/<run_id>.md and appends to REVIEW-TONE-CHANGELOG.md.

Exit codes: 0 = reported normally, INCLUDING a gate block (that is the system working correctly, not an error). 2 = integrity violation. 3 = a stage the run needed is not built yet.

STEP 3 - handle the outcome:

  - Exit 0 or 3: continue to step 4.
  - Exit 2: STOP. Do not re-run it, do not pass any flag to get past it, do not commit anything. Exit 2 means held-out data was written or an append-only file was altered. Report exactly what it printed and finish.

STEP 4 - commit and open a pull request. Never push to main.

    git checkout -b review-tone-run/$(date -u +%Y-%m-%d)
    git add results/ REVIEW-TONE-CHANGELOG.md data/ MISSES.md
    git commit -m "review-tone weekly run: <phase>, verdict <verdict>"
    git push -u origin HEAD

Then open a PR with `gh pr create` if gh is available in this environment. If it is not, push the branch anyway and say so. Do not force-push. Do not merge.

STEP 5 - report, briefly. Read results/reports/<run_id>.md and relay: what changed since the last run, which gates are met, which companies are blocked and why, and the current verdict. Four short sections. Do not paste the stage log.

RULES THAT OVERRIDE ANY INSTINCT TO BE HELPFUL:

1. Never output a buy, sell, hold, price target, position size or allocation, not even hedged or as an example. This is a measurement instrument. If the pipeline produced a result, report the effect, its confidence interval, the baseline it was compared against, the base rate, and the shuffled-date null result. Nothing else.

2. Never compute, adjust or estimate any number yourself. Every figure comes from a script and is read from its output. If a number looks wrong, say that it looks wrong; do not correct it in prose.

3. Never work around a gate. If the orchestrator says a gate is unmet, the answer is which gate and why. Do not pass --reform-universe or --refresh-coverage: those re-create hashed artefacts that existing result rows point at, and they are for a human who means it. Do not create data/eligibility.csv - that stage is scored by hand from SEC filings and a scheduled run must never invent it.

4. If the orchestrator refuses to re-run the backtest because the active prereg has already failed on held-out data, that refusal is correct and final. A held-out failure ends that hypothesis. Do not re-specify it, do not adjust a threshold, do not run it again with different parameters, and do not suggest doing so next week. Report that the hypothesis is closed and that advancing requires a new hypothesis and an unspent held-out window - a decision for the user.

5. Never edit or delete results/results.jsonl or MISSES.md. Both are append-only. If the orchestrator refuses to append because one of them diverged from its committed version, report that and stop.

6. If a stage reports "not built yet", that is the honest state of the project. Do not write the missing script. Report what the pipeline is waiting on.

If the orchestrator cannot run at all (missing Python, missing repo files, no REVIEW-TONE-CORE-PROMPT.md), report the exact error and stop. Do not improvise a substitute run.
```
