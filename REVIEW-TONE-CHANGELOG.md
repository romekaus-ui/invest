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
## 2026-10-11 · Phase A2 scorer

Built the A2 structural-eligibility stage: a subagent that scores every in-band
company in a point-in-time universe, and the script that does its arithmetic.

**Delivered:**
- `.claude/agents/review-eligibility-scorer.md` — the A2 subagent. Reads the core
  prompt, `company-selection.md`, `data-contracts.md` and `failure-modes.md`;
  scores the seven criteria 0–5 from the filing in force at formation; writes
  judgements with per-criterion evidence and accession; never computes a total,
  never consults review coverage, never excludes on performance. Tools are
  Read/Write/Edit/Glob/Grep/Bash only — no WebSearch, no WebFetch, no browser,
  so present-day information about a company is out of reach by construction.
- `.claude/skills/review-tone-signal/scripts/score_eligibility.py` — stdlib only,
  reuses `fetch()`/`ua()` from `screen_universe.py`. Five modes: `queue`,
  `filing`, `probe`, `build`, `verify`. It owns the three things the model must
  not do — the weighting arithmetic (core prompt §1.3), the point-in-time
  boundary, and the completeness check.

**Design decisions worth recording:**
- *Evidence by frozen probe, not by free reading.* A 10-K flattens to ~400k
  characters; reading one whole costs ~100k tokens and nobody would do it 40
  times. `probe` runs a fixed regex set per criterion and prints bounded ±180
  character windows, deduped across overlapping matches. The same questions in
  the same words for every company, so the evidence gathered does not vary with
  how interesting a company looks. Adding a probe is a change to the instrument.
- *Judgements are append-only JSONL, built in a separate step.* `build` validates
  then computes. A score with no evidence string, a missing `estimated` flag on
  c1, a score outside 0–5, a filing dated after formation, a criterion citing
  later evidence, a ticker not in the universe, or outcome language in a `reason`
  all stop the build and write nothing. Each was tested and each fires.
- *The universe is scored whole or not at all.* `build` refuses while any in-band
  company is unscored. `--partial` exists for a verification sample and marks the
  hash file `partial=True`; a partial file does not meet the A2 gate.
- *Prospectus fallback.* A company that listed shortly before formation has no
  10-K. Dropping those names removes exactly the recently-listed Amazon-native
  brands the rolling formation exists to capture, so `filing` falls back to the
  last registration statement filed on or before formation (S-1/424B/F-1/10-12B)
  and labels it. Deviation from "the 10-K in force at formation"; the constraint
  that matters is unchanged, since the prospectus was public at formation. Lovesac
  at the 2019 formation resolves to its 424B1 of 2018-10-30, which quantifies
  ecommerce at 16.8% of sales — better c1 evidence than most 10-Ks carry.

**Verified on three companies at the 2016 formation** (`--partial`, not a universe):

| Ticker | Filing in force | Score | Verdict |
|---|---|---|---|
| LOGI | 10-K filed 2015-06-05 (FY to 2015-03-31) | 69.0 | pass |
| HELE | 10-K filed 2015-04-29 (FY to 2015-02-28) | 49.0 | fail |
| NTAP | 10-K filed 2015-06-12 (FY to 2015-04-24) | 31.0 | fail |

Cost: one probe packet is 13–15k characters (~3.5–4k tokens); a scored company
runs 6–8k tokens all in, two or three EDGAR requests, about a second each. A
formation year is one session's work.

**Defects and discrepancies found:**
- *The scorecard's worked verdicts do not survive point-in-time scoring.*
  `company-selection.md` §3 calls Helen of Troy a likely pass and Logitech
  marginal. Scored from the filings in force at 2016-01-01 the order reverses:
  HELE 49, LOGI 69. HELE's FY2015 10-K names Amazon only as a competitive threat
  to its own brick-and-mortar customers, lists e-commerce as one of ten channels
  with no quantified share, and reports four segments including a direct-mail
  supplements business. Logitech's FY2015 10-K names Amazon.com as a sales
  channel and discloses dollar revenue per product category *and* per channel.
  §3 was written from present-day knowledge of these companies; it demonstrates
  the scorecard, and it is not a target to reproduce. Recorded, not fixed —
  §3 says as much itself.
- *In-band counts.* The universe files hold 40 in-band companies at the 2016
  formation and 51 at 2019, not the 29/41 assumed when this stage was specified.
  Both commits of `data/universe_*.csv` agree, so the figures are the screen's,
  not a regression.
- *Cap-floor drift between documents.* `company-selection.md` §1 specifies a
  USD 50M–10B band and `screen_rolling.py` defaults to 50M, but
  `screen_universe.py` defaults to 200M and `data-contracts.md` documents
  `in_band` as `200M <= cap <= 10B`. The committed 2016/2019 universes came from
  the 200M path. Not touched here — changing the floor changes the universe, and
  A2 may not reach upstream of a frozen screen. Flagged for the user.

**Not run:** the full universe. Three companies were scored to verify the
instrument. `data/eligibility_2016.csv` is marked `partial=True` and 37 of 40
companies at the 2016 formation remain unscored, so the A2 gate (>= 8 eligible)
is not met and A3 coverage must not begin.

## 2026-10-11 · A2 rescoped to a shortlist of company-years

Narrowed the A2 component after a correction from the session building the
mechanical pre-filter. Two facts behind it, both verified here:

- The committed `data/universe_2016.csv` / `universe_2019.csv` are output of the
  **superseded** 27-code screen in `screen_universe.py` — their hash files carry
  that script's format, and commit 8ca42b2's own message describes the 40-company
  band they produced as the problem it was fixing. `sic_consumer.py` now defines
  126 codes and `screen_rolling.py` re-forms annually to
  `data/universe_panel.csv`, **which has not been run**. The 40/51 in-band counts
  in the previous entry are accurate for the committed files and stale as a
  description of the universe; expected scale after the new screen runs is
  800–1,000 company-years.
- A mechanical eligibility pre-filter, cutting that panel to roughly 60–80 using
  only point-in-time filing data, is a separate component built elsewhere. Not
  built here, not duplicated here.

**Changes:**
- `load_universe` reads `data/universe_panel.csv` when present and slices it by
  formation year, falling back to the per-year files. The unit is now explicitly
  a company-year: a company in four formation years is scored four times, each
  against that year's filing, each in that year's output file.
- `--shortlist` (queue, build) takes the pre-filter's ticker list — one per line
  or a CSV with a `ticker` column — and makes it the scope. Completeness is
  measured against the shortlist, not the panel. A shortlist naming a ticker that
  is not an in-band company-year at that formation is refused, as is a judgement
  for a ticker outside the shortlist: scoring beyond the handed-down set is
  selection, which is not A2's to do.
- Provenance is recorded rather than assumed. The hash file and evidence JSON now
  carry the universe file and its sha256, the shortlist and its sha256, the scope
  size, and `not_in_scope` — every in-band company-year the pre-filter cut.

**Methodological caveat, raised and not resolved here.** `company-selection.md` §2
requires the ineligible list to be part of the result. With a shortlist, the
company-years the pre-filter cut carry no scores in `eligibility_<YEAR>.csv`, so
that file no longer bounds the population on its own — and the pre-filter cuts on
segment count and e-commerce disclosure, which are the same evidence as criteria
3 and 1, so the two stages are correlated rather than independent. The requirement
is satisfied only if the pre-filter publishes its own cut list with per-company
reasons. `build` now prints the upstream cut count and records the tickers, so the
gap is visible in the output instead of silent.

Guards tested after the change: stray shortlist ticker refused, off-shortlist
judgement refused, panel precedence over per-year files confirmed, and the
2016 verification CSV byte-identical (sha256 e15547c1…) — the scope change is
recorded in the hash file and evidence JSON, never in the contract CSV.

**Still not run:** the new 126-code rolling screen, the pre-filter, and therefore
A2 at real scale. The 2016 verification file covers 3 company-years from the stale
universe and does not meet the A2 gate.
