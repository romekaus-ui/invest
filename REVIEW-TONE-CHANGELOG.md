# Changelog

## 2026-10-11 · scaffold

Built the `review-tone-signal` skill: locked core prompt, five reference documents,
and a verified Phase A pipeline.

**Verified working against live sources:**
- `screen_universe.py` — SIC enumeration via EDGAR atom feeds, join to listed
  tickers, point-in-time market cap. Cross-checked: AAPL $587B, Hasbro $8.39B,
  Helen of Troy $2.65B, UEIC $0.74B at 2016-01-01, all matching history.
- `corpus.py` — streaming access to the 750 GB review corpus, nothing stored.
  Confirmed schema and brand matching on an Appliances sample.
- `coverage.py` — brand→ASIN matching, ambiguity handling, gate reporting.
- `fetch_sec.py` — point-in-time quarterly revenue with original vintages.
  Verified on HELE (FY ends Feb) and HBB (FY ends Dec).

**Defects found and fixed during the build:**
- Stooq is behind a JavaScript bot-check as of 2026-10; replaced with the Yahoo
  chart API.
- Yahoo returns split-adjusted closes; multiplying them by historical share
  counts understated market cap by the cumulative split factor (Apple by 4x).
  Now corrected by a separate split-event query.
- Fiscal quarter labels were taken from the XBRL `fp` field, producing
  `FY2011QFY` for annual filings. Now derived from EDGAR's `fiscalYearEnd`.
- Fiscal year end inferred from the mode of 10-K period ends put Helen of Troy
  in August; it ends in February. A one-month error shifts every join by a
  quarter (F11). Now read from the authoritative field.
- Early quarters whose first XBRL appearance is 400+ days after period end are
  prior-year comparatives, not late reports. Marked `comparative_only` and
  excluded rather than being treated as genuine reporting lags.

**Not built, deliberately:** `extract_reviews.py`, `score_reviews.py`,
`aggregate.py`, `backtest.py`, `degrade.py`. These depend on choices the prereg
has to fix first — test statistic, thresholds, weighting. Writing them now would
mean choosing those after having seen the data, which is the thing the whole
design exists to prevent.

## 2026-10-11 · orchestrated run 20261011T114148Z-8743ba

Phase A, verdict `insufficient_data`. stage A2 (Score structural eligibility from filings) is a human step, not a script. Missing: data/eligibility.csv. A scheduled run reports it and stops; it does not invent the output.

- Gates met: A1.universe
- Gates unmet: A2.eligibility, A3.coverage, B1.prereg_frozen, B3.calibration, B7.heldout, C.degradation
- Stages run: none
- Changed since last run: first recorded run: nothing to compare against
- Held-out: not read (prereg unfrozen, development open)
- Report: `results/reports/20261011T114148Z-8743ba.md`

## 2026-10-11 · orchestration layer

Built the layer that runs the pipeline on a schedule instead of as a retyped
prompt: `/review-tone-run`, three scripts behind it, and a weekly cloud routine.

**What was built:**
- `scripts/pipeline_state.py` — reads phase from disk and git, evaluates the
  seven entry gates, and runs three integrity checks on every pass. Read-only:
  nothing that evaluates a frozen artefact can also rewrite it.
- `scripts/results_row.py` — the only writer of `results/results.jsonl` and
  `MISSES.md`. Validates the §9 contract before opening the file, then verifies
  the prefix is unchanged after appending.
- `scripts/orchestrate.py` — walks the stages, stops at the first unsatisfied
  one, appends a row, writes `results/reports/<run_id>.md`, appends here.
- `.claude/commands/review-tone-run.md` — the command, with the interface every
  unbuilt stage must satisfy.
- `references/weekly-routine.md` — the schedule's config and verbatim prompt.

**The rules that are now enforced by code rather than by attention:**
- A gate's predecessor must pass before its phase starts. Gates are re-evaluated
  after every stage, because a gate read once is stale the moment a stage changes
  what it measures.
- Until the prereg is frozen, every stage is invoked with `--window dev`. If one
  writes a held-out quarter anyway, the next state read aborts the run with exit
  2 and nothing is appended.
- A prereg hash that has failed on held-out data is dead. The backtest will not
  run under it again, on any later run, ever. This is the rule a weekly loop
  would otherwise break by quietly trying again, so it is a refusal and not a
  warning.
- The universe is never re-screened and eligibility never re-scored on a
  schedule. A missing or altered universe is reported, not rebuilt.
- A row missing any provenance field, or claiming a verdict with a null estimate,
  interval, baseline, base rate or null run, is rejected and not written.

**Reconciliation, recorded rather than resolved silently:** SKILL.md orders B1
(freeze) before B3 (calibrate), but `prereg/TEMPLATE.md` §4 requires the frozen
document to record the achieved kappa and §6 a base rate computed on development
data. Extraction, calibration, scoring and aggregation therefore run pre-freeze
under `--window dev` and are labelled phase `B-prep`; only `fetch_sec.py` and the
backtest sit behind `B1.prereg_frozen`, and they are the only stages that can
produce a verdict.

**Verified:** 88 checks against throwaway repo copies covering every gate and
every refusal — truncated scans, a company scored outside the universe, kappa
0.48, adjacent agreement 0.70, a prereg with surviving placeholders, an
uncommitted prereg, a hash mismatch, a prereg/calibration kappa disagreement,
scorer drift, contamination from a stage ignoring `--window dev`, a truncated
`results.jsonl`, a rewritten `MISSES.md`, a hand-picked ticker, and a held-out
failure followed by a second run that refuses to retry it. One real run against
this repo: phase A, `insufficient_data`, stopped at `A2.eligibility`.

**Wired but not executed:** `extract_reviews.py`, `score_reviews.py`,
`aggregate.py`, `backtest.py`, `degrade.py` and the dashboard do not exist yet,
so B2–B7, C and the dashboard step were exercised against stubs matching the
documented interface, not the real scripts. `fetch_sec.py` is invoked on its real
positional `TICKER CIK` interface but was tested with a stub, since the stages
ahead of it are unbuilt. A1 and A3 exist and were not re-run: A1 is frozen, and
A3 needs an `eligibility.csv` that is a human's to write.

**Not done:** the weekly routine could not be created. The routine API validates
repository access at creation time and returned `403 repo_access_denied` —
`romek-aus/invest` does not exist yet and this repo has no remote. Its exact
configuration is committed in `references/weekly-routine.md`; creating it is one
call once the repo is pushed.

## 2026-10-11 · weekly routine created

Routine `trig_01MExfvhuF2g6841QWZ8htBR`, Mondays 06:00 UTC against
`github.com/romekaus-ui/invest`. Config and verbatim prompt in
`references/weekly-routine.md`.

Connectors cleared after creation: the API attaches the account's connectors by
default, which gave a job that reads SEC filings access to Gmail, Drive and
Claude Docs. It now holds none.

Not yet verified by a real fire — the repo had no commits pushed when the routine
was created, and this machine has no git credentials, so the first run is the
first test. If it fails, `list_runs` then `get_run_log` on the routine will say
why.
