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

## 2026-10-11 · weekly routine verified by a manual fire

Fired `trig_01MExfvhuF2g6841QWZ8htBR` by hand rather than letting the first
scheduled run be the first test. Run `cse_01LdT3qXLUTp8b2fSQvae9pW`: clone,
orchestrator, validated row, report, branch and PR #1 all worked. Phase A,
verdict `insufficient_data`, stopped at `A2.eligibility` — the expected state.

**Defect found and fixed:** `gh pr create` returns HTTP 403 in the cloud
sandbox; the GraphQL API is blocked there. The run recovered by falling back to
`gh api repos/.../pulls`, but a weekly job should not depend on improvising past
a known failure, so the routine prompt now names the REST route and says why.

**Confirmed working across machines:** the run's report read
`universe: 622c774f7a84 -> 28db9bdb5609`, diffing its own state against the
fingerprint in the row a different machine wrote before the rolling panel
landed. Change detection reads the append-only log, so it needs no shared
mutable state and survives running somewhere else.

## 2026-10-11 · numeric stack decided: stdlib only

**Decision: the pipeline stays standard library only, including the statistical
stages. numpy and scipy are adopted as a development-only dependency — the oracle
the in-repo statistics are cross-checked against, never the engine that computes a
result.** Blocking question for B5–B7; now answered.

The reasoning matters more than the choice, because the expected answer was the
other one.

**What was expected, and what measurement showed.** The case for numpy was that
bootstrap resampling and a permutation null are exactly where it earns its place.
Half of that was false. Benchmarked on the realistic Phase B workload (850
company-quarters, B = 10,000 bootstrap resamples at a mean of 200 reviews/quarter,
1,000 date-shuffles over 850 pairs), CPython 3.9.6 / macOS arm64:

| Stage | stdlib | numpy | |
|---|---|---|---|
| Bootstrap CIs | 73.5 s | 4.3 s | 17.0x |
| Permutation null | 0.4 s | 0.5 s | numpy *slower* |
| Total | 73.9 s | 4.8 s | 15.3x |

The permutation null is not a bottleneck in either stack — at 850 pairs numpy's
per-call overhead exceeds the work. And 74 seconds for the heaviest stage of a full
run is not worth a dependency. Speed did not justify it.

The correctness argument was tested the same way rather than asserted. A hand-rolled
Spearman with no midrank correction, on tone scores in {−2..+2} where ties are
dense, was wrong by 0.2% of the estimate on shuffled input and 1% on a panel read in
ticker order — the adversarial case, where arbitrary tiebreaks correlate with the
company fixed effect the test is meant to remove. Real and worth fixing, but not the
silent catastrophe that would justify restructuring the repo, and fixable in ~15
lines with scipy available in a test to prove it is fixed.

**What actually decided it, in order of weight:**

1. **Inference here comes from resampling, not from distributions.** CORE-PROMPT §6
   requires a shuffled-date null alongside every real run, which replaces the
   analytic p-value. That removes scipy.stats' main draw — we do not need its
   distribution functions, because significance comes from permutation, and
   permutation is arithmetic. The stdlib also turned out to cover the rest:
   `statistics.NormalDist().inv_cdf()` is the only non-trivial ingredient of a BCa
   interval, and it is there.
2. **In-repo arithmetic is versioned by git.** Decisive for the reproducibility
   constraint. If the statistics live in `scripts/`, the commit SHA *is* the version
   pin — exact, always recoverable, already recorded. A pinned external version is
   only as good as its installability.
3. **The available pins are fragile in exactly the way that matters.** The build
   machine has one interpreter, CPython 3.9.6, which forces numpy 2.0.2 and scipy
   1.13.1 — the last releases supporting 3.9, with wheels only for CPython 3.9–3.12.
   A row produced against those pins gets harder to reproduce as Python moves on,
   which is the precise failure the constraint exists to prevent.
4. **Staying stdlib avoids creating the two-tier split rather than documenting it.**
   The four collection scripts stay stdlib regardless; adopting numpy for the stats
   would have *introduced* the seam and the "did you activate the venv" failure mode.

**Trip-wire — what would overturn this.** Stated so the decision is sized rather
than preferred: mean included reviews per company-quarter above ~2,000, or any single
quarter above ~50,000 (bootstrap cost is linear in review-slots × B, so a mean of
5,000 turns the measured 74 s into ~31 min); a prereg test statistic whose null
genuinely cannot be obtained by permutation; or a primitive that cannot be
cross-checked against scipy. Before reaching for numpy, note that bootstrapping the
mean of a 5-valued integer score is a multinomial resample over 5 counts and need
not touch n items at all. Whoever overturns this: record the measurement that did.

**Built:**
- `references/numeric-stack.md` — the decision, the measurements, the binding
  seeding convention, the run-metadata contract. Registered in `SKILL.md`.
- `requirements-dev.txt` — `numpy==2.0.2`, `scipy==1.13.1`, exact pins, never
  ranges. Resolved and verified in a venv on this machine. Dev-only; no stage may
  import them.
- `scripts/env_stamp.py` — emits the `numeric_env` block, derives per-stage seeds,
  and `assert_no_third_party()` fails loudly if numpy/scipy ever leak from the
  verification tests into a stage that writes a result. Verified: unknown stage
  names raise rather than silently sharing a stream; the guard fires in the dev venv.
- `.gitignore` — `.venv/`, `venv/`, with the reason recorded.
- `README.md` — the "no setup" promise is now stated as a deliberate guarantee that
  covers the statistics, plus the optional dev setup and the seeding convention.
- The five existing scripts' docstrings now state stdlib-only as a binding policy
  with a pointer, instead of as an incidental observation. **No logic was touched** —
  churning verified code would risk reintroducing the split-adjustment and
  fiscal-year-end defects above. Confirmed: docstrings only, all five still parse.

**Seeding convention — binding, so two sessions cannot each invent one.**
`random.Random(seed)` per stage, never the module-level `random.*` (shared global
state). Draw only through `.random()`, `.getrandbits()`, `.randrange()`: those are
covered by CPython's compatibility promise, and **`choices`/`sample`/`shuffle` are
not** — their internals have changed across versions, so the same seed can yield a
different stream on a different interpreter. Base seed `20261011`, per-stage offsets
(bootstrap 0, null 1, calibration 2, degradation 3). In the dev venv only,
`np.random.Generator(np.random.PCG64(seed))`, never legacy `np.random.seed`.

**Where versions are recorded — for the `/review-tone-run` session.**
`results/results.jsonl` rows carry a required `numeric_env` object alongside
`prereg_hash`, `universe_hash`, `rubric_version` and `scorer_model_id`. Its schema is
now in `references/data-contracts.md`. Row validation must reject a row with no
`numeric_env`, and must reject `verdict: "signal"` from a row with
`code_dirty: true` — that row was produced from uncommitted code and is not
reproducible. Stages call `env_stamp.numeric_env(...)` rather than assembling it.

**Not done, deliberately:** `aggregate.py`, `backtest.py`, `degrade.py` belong to
other sessions and the latter two are gated on a frozen prereg. The statistical
primitives and their scipy cross-check tests are therefore not written here either —
they belong with the stages that use them. This entry sets the environment and the
contract they must meet.

## 2026-10-11 · Phase B2 extractor

Built `scripts/extract_reviews.py`: products.csv → streamed corpus → `reviews.csv`
on the company's own fiscal calendar, with a merged manifest entry.

Two passes, because the fiscal calendar is the most likely thing to be wrong here
and a mistake must not cost a re-stream: pass 1 streams the category review files
and appends matched rows to the gitignored `reviews_raw.jsonl`; pass 2 dedupes,
labels quarters, sorts and writes `reviews.csv`. `--from-raw` runs pass 2 alone.

**Verified working against live sources** (HBB and HELE, full `Appliances` review
file — 2,128,605 records, 929 MB streamed in 67 s, nothing stored):

| | HBB | HELE |
|---|---|---|
| reviews extracted | 2,900 | 4,516 |
| ASINs with reviews | 28 of 28 tracked | 128 of 128 tracked |
| date range | 2003-12-11 → 2023-08-07 | 2006-01-29 → 2023-07-16 |
| fiscal quarters touched | 75 | 69 |
| quarters ≥ 30 reviews (§5) | 38 | 42 |
| span of clearing quarters | FY2013Q1 → FY2023Q1 | FY2011Q4 → FY2024Q1 |
| `verified_purchase` share | 93.7% | 94.0% |
| fiscal year end | month 12 (EDGAR) | month 2 (EDGAR) |

Both clear the 12-quarter floor **at extraction**, which is an upper bound: the §5
floor is on *scored* reviews, and seller/shipping exclusions plus scoring failures
can only lower these counts. The clearing quarters are also not contiguous — HBB
loses FY2020Q2–Q4 to the pandemic-era gap, and HELE clears only 2 quarters before
FY2014 out of the 26 it touches.

Checks that actually ran, rather than being asserted:
- **Re-run determinism.** `reviews.csv` is byte-identical across reruns
  (`73c0c195…` HBB, `77f8f7d9…` HELE). `review_id` is sha1 of a delimited
  `user_id|parent_asin|timestamp`, never the builtin `hash()`, which is salted
  per process and would have produced new ids on every run.
- **Fiscal calendar (F11).** Read from EDGAR's `fiscalYearEnd` by reusing
  `fetch_sec.fiscal_year_end_month` — not re-inferred. Returned month 12 for HBB
  and month 2 for HELE, matching the values verified in the previous session.
- **Quarter labels cross-checked two ways.** Where the filed period bounds from
  `fundamentals.csv` cover a review, the exact-interval label and the month
  arithmetic agree on **all** 1,894 HBB and 3,856 HELE mapped reviews, 0 differ.
  Hand-checked boundaries: HELE 2023-02-28 → FY2023Q4, 2023-03-01 → FY2024Q1;
  HBB 2022-12-31 → FY2022Q4, 2023-01-01 → FY2023Q1.
- **Row accounting reconciles.** 2,914 raw → 2,900 rows (14 dropped), 4,560 → 4,516
  (44 dropped); the deltas are exactly the duplicates.
- **Schema.** Header matches `data-contracts.md` column for column.
- **Error paths.** Missing `products.csv`, missing `SEC_USER_AGENT`, and one
  `--fy-end-month` across two companies all stop with an instruction, not a
  traceback or a guess.

**Defects found and fixed during the build:**
- The corpus contains **duplicate review records**: 14 repeated keys for HBB, 36
  for HELE, within a single category file. Checked before deduping rather than
  after — all of them are byte-identical rows, so they are genuine corpus
  duplicates and not `review_id` collisions. Undeduped they would have inflated
  n per quarter and double-weighted those reviews in every tone mean.
- `bool("false")` is `True`. `verified_purchase` was coerced with bare `bool()`,
  so a single stringified field in the corpus would have silently inverted the
  F12 check for every row while producing a perfectly plausible 100% verified
  share. Now coerced explicitly.
- `--fy-end-month` applied to every ticker in a multi-ticker run, so
  `extract_reviews.py HBB HELE --fy-end-month 12` would have labelled HELE on
  HBB's December calendar and shifted its entire series by a quarter — F11
  delivered by a convenience flag. The combination is now refused.
- A `--from-raw` rebuild blanked `records_scanned` in the manifest, erasing the
  provenance of the stream that actually produced the data. Scan counts are now
  carried forward.
- Quarter lookup assumed the filed periods are disjoint. Two vintages filing
  different bounds for one quarter would have made a boundary review's label
  depend on scan order. The assumption is now checked and reported, not trusted.
- `n_asins_tracked` was written into the manifest before it was set — a plain
  `KeyError` on first run, found by reading the code rather than by running it.
- Linear scan over the period map per review was fine at 4.5k reviews and would
  have been minutes of pure Python on `Home_and_Kitchen`. Replaced with a bisect.

**Contract drift noted, not changed:** `data-contracts.md` lists
`data/{TICKER}/fundamentals.csv` without `period_start`, `form`, `n_vintages`,
`lag_days` or `comparative_only`, all of which `fetch_sec.py` writes. The
extractor depends on `period_start` for exact quarter bounds. Left alone rather
than edited in passing, because a contract is the wrong thing to adjust quietly.

**F8 — star-rating circularity.** `rating` is retained, because the star-vs-tone
correlation check needs it and a tone series that merely reproduces the star
average has demonstrated nothing. It must be **withheld from the scorer**:
`score_reviews.py` passes `title` and `text` only. This is stated in the module
docstring, in the manifest as `rating_withheld_from_scorer`, and printed at the
end of every run, because it is a one-line mistake that produces a beautiful
result.

**F12 — incentivised and deleted reviews.** Unverified reviews are retained, never
silently dropped; the contract is that they are analysed as a separate series
first. Verified share is reported per quarter, and the spread is real: HELE's
lowest among its clearing quarters is 66% against a 94.0% overall. What this stage
cannot fix and therefore states plainly: the corpus is a fixed snapshot, reviews
Amazon deleted before it was taken are invisible, and no count here recovers them.

**Phase A3 has still not run.** There was no `products.csv` anywhere, and rather
than invent a fixture the verification ran the real `coverage.py` on the smallest
category (`Appliances`, 0.29 GB of metadata). That used coverage.py's own
documented dev shortcut — `eligibility.csv` does not exist, so it fell back to the
alias table — and scanned 1 of 11 candidate categories. It is **not** a Phase A3
result: it matched 2 of 4 tickers against a gate of ≥ 8, and HAS and UEIC matched
0 products only because toys and remote controls are not in `Appliances`.
Those artefacts (`data/coverage.csv`, `data/{HBB,HELE}/products.csv`,
`fundamentals.csv`, `manifest.json`) are deliberately **not committed**, so the
repository does not carry something that reads like a finished screen. Reproduce
with:

```
python3 .claude/skills/review-tone-signal/scripts/coverage.py --categories Appliances
python3 .claude/skills/review-tone-signal/scripts/fetch_sec.py HBB 0001709164
python3 .claude/skills/review-tone-signal/scripts/extract_reviews.py HBB
```

**Still not built:** `score_reviews.py`, `aggregate.py`, `backtest.py`,
`degrade.py`. Unchanged reason: the prereg has to fix the test statistic,
thresholds and weighting first, and B2 running does not move that gate.
