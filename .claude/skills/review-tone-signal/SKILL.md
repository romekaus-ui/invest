---
name: review-tone-signal
description: Measure whether the tone of dated Amazon customer reviews predicts a company's later SEC-reported revenue surprise. Use when building, running or extending the review-tone research pipeline — screening a company universe, extracting and scoring reviews, aggregating quarterly tone, backtesting against EDGAR revenue, or testing whether a signal survives degraded sampling. Research and measurement only; never produces investment recommendations.
---

# Review Tone Signal

A falsification harness for one hypothesis: *does consumer review tone carry information
about a firm's later reported revenue, beyond a naive expectation baseline?*

**Read `REVIEW-TONE-CORE-PROMPT.md` in the project root in full before doing anything.**
It is locked and binding. The most important rules, restated because they are the ones
most easily broken in the moment:

- Never output a buy, sell, hold, target, size or allocation. This is an instrument, not
  an advisor.
- Never let a company into the universe because of how it turned out.
- Never compute a result number in prose. Run the script and read its output.
- Never look at held-out quarters before the prereg is frozen.

## Start of every session

1. Read `REVIEW-TONE-CORE-PROMPT.md`.
2. Read the active file in `prereg/` if one is frozen; check its hash matches.
3. Skim `results/results.jsonl` (what has been run) and `MISSES.md` (what has failed).
4. Report: current phase, what is frozen, what is stale, and the plan for this session.

## Phase map

Work proceeds in gated phases. Do not skip ahead; do not start Phase B while Phase A's
gate is unmet.

| Phase | What happens | Script | Gate |
|---|---|---|---|
| A1 | Screen all US-listed registrants by SIC + point-in-time market cap, re-forming annually | `scripts/screen_rolling.py` | panel produced and hashed |
| A1b | Cut the panel to a shortlist A2 can actually score | `scripts/prefilter_panel.py` | shortlist hashed, every cut reasoned |
| A2 | Score structural eligibility, one company-year at a time | `scripts/score_eligibility.py` + `references/company-selection.md` | >= 8 pass **in one formation**, file not partial |
| A3 | Check Amazon review coverage; record the gap | `scripts/coverage.py` | >= 8 with usable data |
| B1 | Freeze prereg | `prereg/TEMPLATE.md` | hash committed |
| B2 | Extract reviews from the 2023 corpus | `scripts/extract_reviews.py` | contracts validated |
| B3 | Calibrate scorer against human labels | `scripts/score_reviews.py --calibrate` | agreement >= threshold |
| B4 | Score reviews | `scripts/score_reviews.py` | — |
| B5 | Aggregate quarterly tone, decompose mix | `scripts/aggregate.py` | floors met |
| B6 | Fetch point-in-time revenue | `scripts/fetch_sec.py` | vintages intact |
| B7 | Backtest vs baseline + null | `scripts/backtest.py` | beats baseline on held-out |
| C | Re-run under degraded sampling | `scripts/degrade.py` | signal survives |
| D | Live forward collection | — | only if C passes |

## Reference files

Load these as needed; do not load them all up front.

- `references/company-selection.md` — universe rule and the eligibility scorecard.
  **Read before any company enters or leaves the test.**
- `references/methodology.md` — signal construction, endpoints, baselines, deviations
  from the source paper. Read before changing any formula.
- `references/tone-rubric-v1.md` — the frozen scoring rubric. Read before scoring.
  Changing it requires a new version file, never an edit in place.
- `references/failure-modes.md` — the blocking checks, each with how it fails silently.
  Read when a result looks good; that is when it matters.
- `references/data-contracts.md` — exact schemas for every CSV and JSONL in the pipeline.
- `references/weekly-routine.md` — the scheduled weekly run: cron, config and its
  verbatim prompt. Read before changing the schedule.
- `scripts/window.py` — the `--window {dev,full}` contract every stage shares.
  Read before writing a stage that reads or writes quarter-labelled data: the
  development bound is resolved there from the prereg, and `--window full` is
  refused until the prereg is frozen.
- `references/numeric-stack.md` — the pipeline is **stdlib only**, including the
  statistics. Binding. Read before writing any stage that resamples, correlates or
  computes an interval, and before adding any dependency. Fixes the seeding
  convention and the `numeric_env` run metadata.

## Running the pipeline

Do not retype the phase map as a prompt. `/review-tone-run` runs it:

```bash
python3 .claude/skills/review-tone-signal/scripts/orchestrate.py            # advance
python3 .claude/skills/review-tone-signal/scripts/orchestrate.py --dry-run  # rehearse
```

The orchestrator reads the phase from disk, refuses to start a phase whose gate
is unmet, invokes every stage with `--window dev` until the prereg is frozen,
appends a validated row to `results/results.jsonl`, and will not re-run a
backtest under a prereg hash that has already failed on held-out data. Gate
decisions come from `scripts/pipeline_state.py`; never make one in prose.

`.claude/commands/review-tone-run.md` documents the stage interfaces that
`extract_reviews.py`, `score_reviews.py`, `aggregate.py`, `backtest.py` and
`degrade.py` must satisfy. Until each exists the orchestrator stops at it and
says `stage X not built yet` rather than skipping it.

## Data sources

- **Reviews (historical):** `McAuley-Lab/Amazon-Reviews-2023` on HuggingFace. 571.5M
  reviews, May 1996 – Sep 2023, second-level timestamps, `parent_asin`, `store`,
  `verified_purchase`, `helpful_vote`. 750 GB total — **always stream, never download a
  whole category.**
- **Fundamentals:** SEC EDGAR `companyfacts` API. Free, no key, carries the `filed` date
  that makes point-in-time joins possible.
- **Prices:** Stooq daily CSV. Free, no key.
- **Live reviews (Phase D only):** Apify, pluggable behind one interface. Free tier is
  sufficient for a spot check; do not buy a higher tier before Phase C passes.

## When the user asks for a recommendation

They will, eventually, and it will be reasonable in context. Decline the call itself and
give them what the instrument actually produces: the measured effect, its interval, the
baseline it is being compared against, and how often the thing has been wrong. Then stop.
