---
description: Run the review-tone-signal pipeline end to end, stopping at the first unmet phase gate, and report what changed
argument-hint: "[TICKER ...] [--dry-run] [--reform-universe] [--refresh-coverage]"
allowed-tools: Bash, Read, Glob, Grep
---

# /review-tone-run

Runs the `review-tone-signal` pipeline for one ticker or the whole eligible set,
enforces the phase gates in `REVIEW-TONE-CORE-PROMPT.md` §8, appends a validated
row to `results/results.jsonl`, regenerates the dashboard, and writes a short run
report.

Arguments: `$ARGUMENTS`

---

## Before anything else

`REVIEW-TONE-CORE-PROMPT.md` is locked and binding, and this command does not
override one word of it. Carry out the start-of-every-session routine from
`.claude/skills/review-tone-signal/SKILL.md` first:

```bash
cat REVIEW-TONE-CORE-PROMPT.md
ls prereg/ && cat prereg/*.hash 2>/dev/null
tail -5 results/results.jsonl 2>/dev/null; tail -40 MISSES.md
```

Read §8 (phase gates), §9 (output contract) and §1 (prohibitions) before you run
anything. If any of those has changed since this command was written, the core
prompt wins and you stop and say so.

## Run it

```bash
python3 .claude/skills/review-tone-signal/scripts/orchestrate.py
```

Scope it to one company — which must already be in the universe with usable
coverage — or add companies with repeated flags:

```bash
python3 .claude/skills/review-tone-signal/scripts/orchestrate.py --ticker HELE
```

Rehearse without writing anything:

```bash
python3 .claude/skills/review-tone-signal/scripts/orchestrate.py --dry-run
```

Exit codes: **0** reported normally — including a gate block, which is the system
working; **2** an integrity violation, which needs a person; **3** a stage the run
needed is not built yet.

## Your job after it runs

The orchestrator does the work. You relay it.

1. **Do not compute, adjust, interpolate or "estimate" any number it reports.**
   Every figure that reaches a result comes from a script and is read from its
   output (§1.3). If a number looks wrong, say it looks wrong; do not fix it in
   prose.
2. **Do not second-guess a gate.** The gate verdicts come from
   `scripts/pipeline_state.py`. If one is unmet, the answer is which gate and
   why, not a workaround. Never report a blocked run as a partial success and
   never continue "provisionally".
3. **Relay the report it wrote** (`results/reports/<run_id>.md`): what changed,
   which gates are met, which companies are blocked and why, and the verdict.
   Keep it to that. The user does not want the stage log.
4. **If exit code is 2, stop.** Do not re-run, do not pass a flag to get past it,
   do not commit. An integrity violation means held-out data was written, or an
   append-only file was edited. Surface it and leave it alone.
5. **If exit code is 3**, say which stage is not built and what it is waiting on.
   That is the honest state of the project, not a failure of the run.
6. Commit what the run produced:

```bash
git add results/ REVIEW-TONE-CHANGELOG.md data/ MISSES.md && \
  git commit -m "review-tone run <run_id>: <phase>, verdict <verdict>"
```

## What this command refuses to do

Each of these is a rule an automated weekly loop would otherwise break, so each
is enforced in code and not left to the judgement of whoever reads the output.

**It does not touch held-out quarters before the prereg is frozen.** The held-out
window is the final 8 complete quarters (§6). While no prereg is frozen, every
downstream stage is invoked with `--window dev`, so the held-out quarters are out
of reach mechanically rather than by intention, and the report states that they
were not read, not plotted and not summarised. If a stage writes a held-out
quarter anyway, the next state read catches it, the run aborts with exit 2, and
nothing is appended.

**It never re-runs a failed hypothesis against the same held-out data.** A
held-out failure is appended to `MISSES.md` with verdict `no_signal`, and that
prereg hash is then dead: the orchestrator refuses to enter the backtest under it
on every later run, forever. This is the rule most likely to be broken by a
scheduled job that quietly tries again next week, so it is not a warning — the
stage does not run. Advancing after a held-out failure needs a new hypothesis and
a held-out window that has not been spent, which is the user's decision to make
explicitly, not a thing a Monday-morning cron job does.

**It never outputs a buy, sell, hold, target, size or allocation** (§1.1), in the
report or in your relay of it. If the user asks what to do with the result, give
them what the instrument produces — the effect, its interval, the baseline, the
base rate, and how often it has been wrong — and decline the call itself.

**It never re-screens the universe or re-scores eligibility on a schedule.** The
universe is hashed and every result row points at that hash; re-forming it would
silently invalidate the log. A missing or altered universe is reported, not
rebuilt. Re-screening takes `--reform-universe` typed by a person who means it.
Eligibility is scored by hand from filings and is never revised after an outcome
is known (`references/company-selection.md` §2), so a missing `eligibility.csv`
stops the run and is reported as a human step, not as a missing script.

**It never writes a row that is missing any of its provenance.** Every row
carries run ID, prereg hash, universe hash, rubric version, scorer model ID,
phase, company set, window, n per quarter, point estimate, CI, baseline, base
rate, null-run result, specification count and verdict. A row missing any of
those is not a result: it is rejected and nothing is written. A verdict of
`signal` or `no_signal` with a null point estimate, interval, baseline, base rate
or null run is rejected the same way — a verdict needs a measurement behind it.

**It never edits or deletes `results/results.jsonl` or `MISSES.md`.** Both are
append-only (§1.6). Before appending, the run checks that each file is still an
extension of its committed version; if either has been shortened or rewritten, it
refuses to append at all rather than laundering the edit.

## Why weekly, and not daily

**The inputs do not change daily, so a daily run would be identical six days out
of seven.** Revenue arrives on quarterly SEC filings — one new data point per
company per ~13 weeks, and only at its EDGAR `filed` date (§4). The review corpus
is `McAuley-Lab/Amazon-Reviews-2023`, which **ends in September 2023** and will
never grow. The universe is point-in-time and re-formed annually, deliberately.
Calibration is re-run only when the rubric version or the scorer model changes.

A daily run would therefore produce the same report with a new timestamp almost
every day, and a report that says nothing almost every day is one the user stops
reading — at which point the one run that *did* say something goes unread too.
That is a worse failure than running less often: the gate it reports is the gate
nobody sees.

Weekly is slow enough that a run which reports no change is a genuine signal
about the project rather than a foregone conclusion, and fast enough to catch a
newly met gate, a stage a sibling session just built, or a drifted hash within a
few days.

Anything faster than weekly should be a deliberate `--dry-run` typed by a person
mid-development, not a schedule.

## The contract for stages that are not built yet

`extract_reviews.py`, `score_reviews.py`, `aggregate.py`, `backtest.py` and
`degrade.py` were deliberately left unbuilt — they depend on choices the prereg
has to fix first. The orchestrator invokes them against the interface below and
reports `stage X not built yet` when one is missing, loudly, instead of skipping
it. A stage that exists must accept:

| Stage | Script | Invoked as | Must write |
|---|---|---|---|
| B2 | `extract_reviews.py` | `--tickers T [T ...] --window {dev,full}` | `data/{T}/reviews.csv` |
| B3 | `score_reviews.py` | `--calibrate --tickers T [T ...] --window {dev,full}` | `data/calibration.json` |
| B4 | `score_reviews.py` | `--tickers T [T ...] --window {dev,full}` | `data/{T}/scores.csv` |
| B5 | `aggregate.py` | `--tickers T [T ...] --window {dev,full}` | `data/{T}/quarters.csv` |
| B6 | `fetch_sec.py` | `TICKER CIK` (its existing interface, once per company) | `data/{T}/fundamentals.csv` |
| B7 | `backtest.py` | `--prereg <path> --out <json> --tickers ... --window full` | the measurement JSON |
| C | `degrade.py` | as B7 | the measurement JSON |

`--window dev` is binding, not advisory: a stage that ignores it and writes
held-out quarters triggers the contamination abort.

The measurement JSON written by B7 and C must contain `verdict`,
`heldout_evaluated`, `point_estimate`, `ci_low`, `ci_high`, `baseline_value`,
`base_rate`, `null_run_result`, `n_quarters`, `n_per_quarter` and
`specifications_tested`; `hit_rate`, `endpoint`, `test_statistic`, `why`,
`actual` and `failure_mode` are optional and are carried through to the result
row and, on a failure, into the `MISSES.md` entry. Schemas are in
`references/data-contracts.md`.

The dashboard is regenerated from the first of these that exists:
`.claude/skills/review-tone-dashboard/scripts/build_dashboard.py`,
`.claude/skills/review-tone-signal/scripts/dashboard.py`,
`scripts/build_dashboard.py`. If none exists the run says so and carries on; the
result row is already written, and a missing dashboard does not invalidate it.

## One reconciliation you should know about

The phase map in `SKILL.md` lists B1 (freeze the prereg) before B2 (extract) and
B3 (calibrate), but `prereg/TEMPLATE.md` requires the frozen document to record
the achieved calibration kappa (§4) and a base rate "computed on development
data" (§6). Both cannot be true in that order.

The orchestrator resolves it the only way that keeps every rule intact:
extraction, calibration, scoring and aggregation run **before** the freeze, under
`--window dev`, so they can never see a held-out quarter; the freeze then closes
development; and only `fetch_sec.py` and the backtest — the stages that see the
full window and the only ones that can produce a verdict — sit behind
`B1.prereg_frozen`. Those pre-freeze stages are labelled phase `B-prep` in the
result row so the log never claims a phase-B result before the prereg existed.

If you disagree with that reading, change it in the orchestrator and say so in
`REVIEW-TONE-CHANGELOG.md`. Do not work around it per run.
