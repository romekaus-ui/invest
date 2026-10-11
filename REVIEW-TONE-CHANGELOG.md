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
