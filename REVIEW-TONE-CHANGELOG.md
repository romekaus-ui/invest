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

## 2026-10-11 · tone scorer (B3 + B4)

Built `scripts/score_reviews.py`: scoring, the calibration gate, and the offline
verification that proves the three failure modes this component owns are closed
by construction rather than by care.

**Script, not a subagent — recommended and implemented that way.** The rubric
fixes `claude-haiku-4-5-20251001`, temperature 0, one review per call and no
conversation history. A Claude Code subagent runs on the session's model with the
session's system prompt and tools, carries context across turns, and exposes no
temperature, so it can satisfy none of those four constraints and cannot stamp a
reproducible `scorer_model_id`. CORE-PROMPT §1.3 also puts every reported number
in a script, and kappa and the agreement rates are reported numbers. Nothing was
added to `.claude/agents/`.

**Standard library only.** API calls go through `urllib.request`; the Anthropic
SDK is not used, so the script has no third-party imports. The single external
requirement is `ANTHROPIC_API_KEY`. `ANTHROPIC_BASE_URL` in the environment is
deliberately ignored — inside a Claude Code session it points at a gateway that
does not accept `x-api-key` — and is overridable only via `--base-url`.

**F8 · the star rating cannot reach the scorer.** Prompts are built by
`build_prompt(review_id, title, text)`, which takes three strings and has no
access to the review row, so `rating` has no path in. The template is asserted at
import time to contain no rating/star/helpful/verified placeholder, the rendered
message is reconstruction-checked against the template, and the self-test proves
two rows differing only in `rating` produce byte-identical prompts. After scoring,
`pearson(tone, star)` is computed and printed: ~0.95 means the text added nothing
over the star average and the run is a result about stars.

**F10 · scorer drift.** Every row stamps `rubric_version` and the model ID the API
*answered with*, not the alias requested; responses from more than one model abort
the run before anything is written. `tone-rubric-v1.md`'s SHA-256 is pinned in the
script, so an in-place edit to the frozen rubric stops the pipeline and says to
create v2 — verified by appending one newline to the file. The scorer is shown the
Scale, Rules, Tags and Output format sections extracted verbatim from that file,
never a paraphrase; the calibration-gate section is withheld. `scores.csv` is
append-only: re-scoring under a new model adds rows beside the old ones and
downstream selects on `(rubric_version, scorer_model_id)`.

**F13 · silent scoring failures.** Malformed output is retried once, then written
as a row with `scoring_failed=True` and an empty `tone` — never dropped, never
guessed at. Strict parsing: a bare JSON object with exactly the five rubric keys,
an integer tone in −2..+2, three real booleans, and a `review_id` matching the one
requested. Prose, code fences, extra keys, stringified tones and echoed wrong ids
are all rejected. Transport exhaustion also yields `scoring_failed` but is counted
separately so a network outage is never read as a model problem. Counts land in
`scoring_report.json` overall and per fiscal quarter, quarters over 10% are
flagged, and a run over `--max-failure-rate` (default 10%) exits non-zero.

**The calibration gate blocks; it does not warn.** `--calibrate` draws 100 scored
reviews stratified across the model's own score range under a recorded seed, and
writes a blind labelling sheet carrying `review_id`, `title` and `text` only —
no model score and **no star rating**, because a human who sees either is no
longer an independent rater and the agreement becomes circular on the human side.
`--calibrate --labels <file>` computes exact agreement, adjacent agreement and
quadratic-weighted kappa, each with a bootstrap 95% CI, writes `calibration.json`,
and exits non-zero unless kappa >= 0.60 AND adjacent >= 0.85 on n = 100.
`require_calibration()` is exported for `aggregate.py` and `backtest.py` to call.

**Verified, 43 offline checks, all passing** (`--self-test`), against 5,513 real
reviews streamed from the 2023 corpus. Gate arithmetic checked against hand-worked
cases: perfect agreement → kappa 1.0; a rater uniformly one step high → adjacent
1.0, exact 0.2; both raters constant → kappa `null`, not a flattering 1.0; a
constant model against a spread human → kappa 0.0. Gate behaviour checked on four
constructed label sets: kappa 0.978 / adjacent 1.000 → pass; kappa 0.664 with
adjacent 0.780 → **blocked on adjacent alone**, which is the AND in the gate doing
its job; perfect agreement on n = 50 → **blocked for being short of 100**. The
whole B4 path ran offline under `--backend stub` at `--workers 4` over 500 real
reviews: 470 scored, 30 failed (13 malformed, 17 transport), 51 seller-only
tagged, kept in the file and excluded from the aggregate, and `scores.csv` headers
matching the contract exactly.

**Defects found and fixed during the build:**
- The corpus contains exact duplicate records — the same text from the same user
  on the same `parent_asin` at the same second, 1 in 5,513 in the sample. Since
  `review_id` hashes exactly those three fields, the id repeats. An identical row
  is now dropped and counted; two *different* reviews sharing an id is a hard
  failure, because `scores.csv` is keyed on `review_id` and would silently merge
  them. `extract_reviews.py` should dedupe at B2 rather than leave it here.
- A retry at temperature 0 resends an identical prompt and mostly gets an
  identical answer, so the rubric's mandated single retry recovers truncation and
  transport failures, not systematic malformation. Implemented as written, with
  the three outcomes counted separately so the retry's real yield is visible.
- Kappa is undefined only when *both* raters are constant. One constant rater
  gives kappa 0.0, and an early self-test asserting otherwise was wrong, not the
  arithmetic.

**NOT RUN, and the pipeline is therefore blocked at B3.** No scoring credential is
reachable from this environment: `ANTHROPIC_API_KEY` is unset, the SDK is absent,
and a spawned Claude Code CLI reports "Not logged in". So no review has been scored
by `claude-haiku-4-5-20251001`, no calibration sample has been drawn from real
model scores, and no human labels exist. **The calibration gate is unmet. It has
not been evaluated against a single real human label, and no downstream result may
be produced.** The tone distribution reported above is synthetic stub output that
verifies the plumbing; it is not tone data, every row of it is stamped
`stub:deterministic-v1`, the stub refuses to write anywhere under `data/`, and the
gate refuses to evaluate labels against it.

To unblock B3: `extract_reviews.py` (B2) first, then, with a key exported,
`score_reviews.py {TICKER}`, `--calibrate` to draw the sheet, hand-label it, and
`--calibrate --labels` to see whether the gate passes. It may not, and that is a
legitimate outcome — an unvalidated scorer makes every downstream statistic
decorative.

## 2026-10-11 · B5 aggregation

Built `scripts/aggregate.py` — quarterly tone aggregation with the within-ASIN /
product-mix decomposition. Stops at aggregation.

**What it does:** reads `data/{TICKER}/reviews.csv` + `scores.csv`, writes
`data/{TICKER}/quarters.csv` (16 columns, exactly the contract's schema and
order — verified by parsing the column list out of `data-contracts.md` and
diffing it against the emitted header), plus `data/dropped.csv` and an
`aggregate` block merged into each `manifest.json`.

**The decomposition.** Total tone change is split per methodology §3 over ASINs
present in both Q and Q−1: `within = Σ w_i(Q−1)·Δtone_i`, `mix = Σ Δw_i·tone_i(Q−1)`,
residual = entrants + exits + the within×mix interaction. Only `tone_within`
feeds the signal. The residual is built from its own three terms and the
identity `within + mix + residual == Δtotal` is then asserted, so the assertion
tests the arithmetic rather than restating a definition — a residual computed as
the leftover would reconcile by construction and check nothing.

**Floors are hard stops.** A quarter below 30 included reviews emits
`sufficient=false` with `tone_mean`, both CI bounds and all three decomposition
columns null. It emits no number. A company below 12 usable quarters is dropped
and recorded in `data/dropped.csv` with the reason, never replaced. Both
thresholds are module constants, defaulted to 30 and 12; overriding them on the
command line prints that the output is not a result.

**Every mean carries an interval.** Percentile bootstrap, 2000 replicates,
seed 20261011, resampling reviews within the quarter. Each quarter draws from a
substream keyed by SHA-256 of (seed, ticker, quarter), so a CI does not depend on
how many quarters preceded it or in what order they were processed. The one path
that could produce a mean without an interval nulls the mean instead.

**Blocking checks wired in, reported and never smoothed:** F4 (residual share of
gross movement, naming whether entrant churn or the interaction term dominates),
F12 (verified-purchase share against the prior-four-quarter mean), F13 (share of
a quarter lost to scoring failure). Flags also land in the manifest.

**Interpretation resolved and logged** in `methodology.md` §8, six rows. The
load-bearing one: the three decomposition columns are the **Δ components** of §3,
not levels, because that is the only reading under which they reconcile to the
total change as the contract requires — so `baseline_prior4` is the mean of the
prior four within-ASIN *changes* and `signal = tone_within(Q) − baseline_prior4(Q)`,
the prereg's formula, is well defined. Also logged as an **open gap**: the
contract has CI columns for `tone_mean` only, so the signal-level interval
methodology §4 asks for is deferred to the backtest stage and must be specified
in the prereg before `backtest.py` exists.

**Not verified against real data, because there is none.** `data/` holds the two
point-in-time universes and the brand alias table and nothing else — no
`eligibility.csv`, no per-ticker directories, no `reviews.csv`, no `scores.csv`,
and no frozen prereg. B2 (extract), B3 (calibrate) and B4 (score) have not run,
so there are no per-quarter counts to report and no statement to make about how
many quarters clear the floor. Running the script in that state exits non-zero
and says so rather than producing anything.

What was verified: 21 self-tests via `--self-test` (code correctness only, writes
nothing, produces no research result) covering the reconciliation identity, a
pure mix shift putting zero in the within component and vice versa, the floor
nulling tone on quarters of 1, 4 and 6 reviews — the pilot's actual counts — gaps
not being bridged, baseline/signal timing, bootstrap reproducibility and the
degenerate single-value quarter. Separately, the full read/write path was
exercised once end to end on a synthetic fixture in the scratchpad, which
confirmed the schema match and that F4/F12 fire; that fixture was never written
into `data/` and its numbers are not a finding.

**Defect found and fixed during the build:** F4's residual flag initially divided
by the total tone change, which goes to zero through cancellation — a quarter
with a +0.00 total change was reported as a 958% residual alarm. Now measured
against `|within|+|mix|+|residual|` and the message names which term dominates.

**Still not built, deliberately:** `backtest.py` and `degrade.py`. They encode
the test statistic, thresholds and weighting, which are prereg choices. Writing
them now would mean choosing those after having seen aggregation output, which is
the thing the design exists to prevent. Next is B6 `fetch_sec.py`, then freeze
the prereg.

### 2026-10-11 · B5 addendum — decomposition marked provisional

A peer session flagged that `methodology.md` §3 is not derived from the source
paper. Verified against the repo before acting: §3's own header calls the spec "a
faithful reconstruction of the method as described second-hand", §4 says
"following the reel's construction", `failure-modes.md` names the 61-second
video, and §8 carries an open row to reconcile against the paper. The flag is
correct, and the first B5 entry above under-stated it — it logged *interpretation*
decisions while presenting §3's formula itself as settled.

Taken: isolate behind a marked seam, **not** pause the decomposition. Pausing
would contradict the brief, which requires the split to ship; the seam satisfies
both. No emitted number changed.

- `DECOMPOSITION_SPEC = "methodology-s3-reconstruction-v0-UNVERIFIED"` and
  `BOOTSTRAP_IMPL`, both written into every manifest. Re-running after the paper
  is reconciled is then distinguishable from a result rather than silently
  overwriting one — the F10 scorer-drift discipline applied to the arithmetic.
- `decompose()` documented as the single swap point: replace its body, bump the
  tag, and every caller, the reconciliation assertion, the F4 flag and the CSV
  writer are unaffected. A second copy of the arithmetic must never appear.
- Module docstring separates what is provisional (the formula) from what is not
  (that the split must happen, that only `tone_within` feeds the signal, that the
  three components reconcile) — those rest on CORE-PROMPT §5/§7 and the contract.
- Run header and per-company footer print the provisional warning.

**Numeric stack: assumed stdlib-only**, per the brief and the repo today. No venv
set up. `bootstrap_mean_ci()` is the only numerically intensive code and the only
consumer of the RNG, so a numpy/scipy version replaces that one body. Recorded
there and in the manifest: the seed alone does not pin output — the RNG *stream*
is part of reproducibility, so a numpy run is a new vintage, not a re-run.

Still unbuilt, deliberately: `backtest.py`, `degrade.py`.

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

## 2026-10-11 · review-tone-dashboard

Built the `review-tone-dashboard` skill: a presentation layer that renders one
company's pipeline output as a published Artifact. Takes a ticker and an
optional formation year, reads `quarters.csv`, `fundamentals.csv`,
`manifest.json` and `results.jsonl`, and writes a standalone HTML page.

**The honesty rules are enforced in code, not in prose.** Each is a specific way
a chart can make a null result look like a finding, so each sits in
`build_dashboard.py` (data gating) or in the template's chart geometry, and is
documented in `references/render-contract.md`:

- The per-quarter review counts are drawn *inside* the tone chart's SVG, below
  the axis. Welded into the geometry so a later layout change cannot drop them
  and leave a tone line with no sample sizes.
- A quarter under the 30-review floor is a hollow ring and **a break in the
  line**, never a point on it. Interpolating across a six-review quarter invents
  the quarter.
- A tone mean arriving without a bootstrap interval is not rendered at all.
  Empty CSV cells parse to `None`, never `0.0`.
- A company under the 12-quarter floor gets **no trend chart**. A refusal panel
  replaces it. The tempting version — draw the line and caption it "small
  sample" — loses, because the caption is read once and the shape is read every
  time.
- A hit rate without its base rate is dropped. Below its base rate it is
  labelled `BELOW THE BASE RATE — THIS IS A FAILURE`, never "58% accurate" (F7).
- Verdicts are downgrade-only: a `signal` row for a company under the floors
  still renders `insufficient_data`.
- `signal` is blue, never green. A green verdict pill reads as "go", and "go" is
  a recommendation.
- Scope, limits, the checks list (including the gates that FLAG) and the
  no-advice framing render unconditionally, outside the tab system.

**Decisions worth recording:**
- Grey was the first choice for the seller-only series, since those records are
  excluded from tone. It fails the data-viz chroma floor as a categorical series
  (OKLCH C 0.009) and reads as chart furniture rather than data. Replaced with
  palette slot 2; exclusion is carried by the legend and stacking order.
- The volume chart labels the **scored** product-review count, not the stack
  total. Caught while rendering the pilot fixture: a bar labelled 30 sat across
  the 30-review floor line while only 28 of those were scored reviews, inviting
  a comparison of the wrong number against the floor.
- The floor is re-derived from `n_reviews` rather than trusted from the
  `sufficient` column. Disagreement between them is surfaced as a warning.

**Verification.** Phase B5 has not run, so no real `quarters.csv` exists. The
skill was built against the documented contract and rendered from three labelled
synthetic fixtures (`make_fixture.py`), which carry a permanent banner and are
marked SYNTHETIC in every field that could be mistaken for provenance:

| scenario | shape | result |
|---|---|---|
| `below-floors` | 8 quarters, 1–28 reviews — the Logitech M510 pilot's shape | refused to chart; `insufficient_data` |
| `mixed` | 16 quarters, three under the review floor | line drawn broken at all three |
| `above-floors` | 16 clean quarters, hit rate 58% vs base rate 65% | read as a failure |

**It has not been validated against real pipeline output.** The column reads,
null handling and floor arithmetic follow `data-contracts.md`, but no stage of
this skill has seen a figure that came from the corpus or from EDGAR.

Palette validated with the dataviz skill's `validate_palette.py` (no `node` on
this machine) against the page's own surfaces, passing in both modes: worst
adjacent CVD ΔE 24.7 light / 26.8 dark against a target of 8.
