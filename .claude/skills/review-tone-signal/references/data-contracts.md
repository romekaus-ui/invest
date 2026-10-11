# Data Contracts

Exact schemas. `scripts/validate.py` enforces these; a stage that violates its contract
stops the pipeline rather than passing a malformed frame downstream.

All timestamps are UTC. All monetary values are USD unless a `currency` column says
otherwise. All CSVs are UTF-8 with a header row.

---

## `data/universe.csv`
Point-in-time screen output. Frozen and hashed before any review data is touched.

| column | type | notes |
|---|---|---|
| `ticker` | str | as listed at formation |
| `cik` | str | 10-digit, zero-padded |
| `company_name` | str | as filed |
| `sic` | str | 4-digit |
| `exchange` | str | NYSE / NASDAQ / NYSEAMERICAN |
| `formation_date` | date | screen date |
| `shares_outstanding` | int | from `dei:EntityCommonStockSharesOutstanding` |
| `shares_asof` | date | filing date of the shares figure |
| `price_at_formation` | float | Stooq close |
| `market_cap` | float | shares × price, point-in-time |
| `in_band` | bool | 200M <= cap <= 10B |
| `status_later` | str | `active` / `acquired` / `delisted` — recorded, never used to filter |

## `data/eligibility.csv`
Scorecard output. One row per universe member, including failures.

| column | type | notes |
|---|---|---|
| `ticker` | str | |
| `c1_amazon_share` … `c7_reseller_noise` | int | 0–5 each |
| `c1_estimated` | bool | true if Amazon share was inferred, not disclosed |
| `weighted_score` | float | out of 100 |
| `eligible` | bool | `weighted_score >= 60` |
| `reason` | str | binding reason, required when `eligible` is false |
| `scored_asof` | date | evidence vintage; must be <= formation_date |

## `data/{TICKER}/products.csv`

| column | type | notes |
|---|---|---|
| `parent_asin` | str | join key to reviews |
| `title` | str | from corpus metadata |
| `store` | str | corpus `store` field |
| `main_category` | str | |
| `matched_by` | str | `store_exact` / `store_alias` / `manual` |
| `price` | float | nullable |
| `rating_number` | int | corpus review count |

## `data/{TICKER}/reviews.csv`

| column | type | notes |
|---|---|---|
| `review_id` | str | stable hash of `user_id` + `parent_asin` + `timestamp` |
| `parent_asin` | str | |
| `timestamp` | int | epoch ms, from corpus |
| `review_date` | date | derived |
| `fiscal_quarter` | str | e.g. `FY2021Q3`, from the company's own calendar |
| `rating` | float | **retained but withheld from the scorer** |
| `title` | str | |
| `text` | str | |
| `verified_purchase` | bool | |
| `helpful_vote` | int | required for the Phase C degradation test |

## `data/{TICKER}/scores.csv`

| column | type | notes |
|---|---|---|
| `review_id` | str | |
| `tone` | int | −2..+2, or null when `scoring_failed` |
| `seller_or_shipping_only` | bool | |
| `defect` | bool | |
| `price_complaint` | bool | |
| `scoring_failed` | bool | |
| `rubric_version` | str | e.g. `v1` |
| `scorer_model_id` | str | exact model ID |
| `scored_at` | datetime | |

## `data/{TICKER}/quarters.csv`

| column | type | notes |
|---|---|---|
| `fiscal_quarter` | str | |
| `n_reviews` | int | included in aggregate |
| `n_excluded_seller` | int | |
| `n_scoring_failed` | int | |
| `verified_share` | float | F12 check |
| `tone_mean` | float | null when below floor |
| `tone_ci_low`, `tone_ci_high` | float | bootstrap 95% |
| `tone_within`, `tone_mix`, `tone_residual` | float | decomposition |
| `baseline_prior4` | float | |
| `signal` | float | `tone_within − baseline_prior4` |
| `defect_rate`, `price_complaint_rate` | float | |
| `sufficient` | bool | false → `insufficient_history` |

## `data/{TICKER}/fundamentals.csv`
Point-in-time. One row per reported quarter, original vintage preserved.

| column | type | notes |
|---|---|---|
| `fiscal_quarter` | str | |
| `period_end` | date | |
| `filed_date` | date | **the join key for decisions** |
| `revenue` | float | as first reported |
| `xbrl_tag` | str | which concept supplied it |
| `accession` | str | source filing |
| `restated` | bool | true if a later vintage differs |

## `data/{TICKER}/manifest.json`
Provenance. Written by every stage, never overwritten wholesale.

```json
{
  "ticker": "XXXX",
  "fiscal_calendar": {"fy_end_month": 3, "verified_from": "10-K accession ..."},
  "corpus": {"dataset": "McAuley-Lab/Amazon-Reviews-2023", "categories": ["Electronics"]},
  "extracted_at": "...", "rubric_version": "v1", "scorer_model_id": "...",
  "universe_hash": "...", "prereg_hash": "...",
  "coverage_ratio": 0.0, "coverage_method": "..."
}
```

## `results/results.jsonl`
One object per run. Append-only.

Required keys: `run_id`, `run_at`, `phase`, `prereg_hash`, `universe_hash`,
`rubric_version`, `scorer_model_id`, `numeric_env`, `companies`, `window`, `n_quarters`,
`point_estimate`, `ci_low`, `ci_high`, `baseline_value`, `base_rate`, `null_run_result`,
`specifications_tested`, `verdict` ∈ {`signal`, `no_signal`, `insufficient_data`}.

`specifications_tested` is a count, not a list of the good ones. Reporting only the
surviving specification is F6.

`numeric_env` is the object produced by `scripts/env_stamp.py`, and it is what makes
the row's arithmetic recoverable. The pipeline is stdlib only, so the commit pins the
statistics the way a pinned library version would otherwise have to
(`references/numeric-stack.md` §4).

| key | type | notes |
|---|---|---|
| `stack` | str | `stdlib`. Any other value means the §2 trip-wire was hit. |
| `python_version` | str | e.g. `3.9.6` — the arithmetic is stdlib, so this is load-bearing |
| `python_implementation` | str | `CPython` |
| `platform` | str | e.g. `Darwin-25.5.0-arm64` |
| `code_commit` | str | git SHA at run time; **this is the version pin** |
| `code_dirty` | bool | true → produced from uncommitted code, verdict not citable |
| `rng` | str | `stdlib-mt19937` |
| `seed` | int | base seed, fixed in the prereg |
| `stage_seeds` | object | derived per stage, e.g. `{"bootstrap": ..., "null": ...}` |
| `bootstrap_reps` | int | required whenever a CI is reported |
| `null_permutations` | int | required whenever `null_run_result` is populated |

Row validation must reject a row with no `numeric_env`, and must reject
`verdict: "signal"` on a row with `code_dirty: true`.

---

# Orchestration contracts

Added when the orchestration layer was built (`scripts/orchestrate.py`). These
are the interfaces the orchestrator reads and writes; the stages above are
unchanged.

## `data/calibration.json`
Written by `score_reviews.py --calibrate`. The calibration gate in
`tone-rubric-v1.md` is read from this file, so a missing or incomplete one blocks
the pipeline rather than being treated as a pass.

| key | type | notes |
|---|---|---|
| `n` | int | reviews hand-labelled; the rubric requires >= 100 |
| `exact_agreement` | float | |
| `adjacent_agreement` | float | **gate: >= 0.85** |
| `quadratic_weighted_kappa` | float | **gate: >= 0.60** |
| `rubric_version` | str | must match the prereg |
| `scorer_model_id` | str | must match the prereg; a change forces re-calibration (F10) |
| `labelled_by` | str | who labelled, blind to model output |
| `scored_at` | datetime | |

## The measurement JSON (`backtest.py`, `degrade.py`)
Written to the `--out` path. The orchestrator reads it and builds the result row
from it; the model never assembles these numbers itself (§1.3).

Required: `verdict` ∈ {`signal`, `no_signal`, `insufficient_data`},
`heldout_evaluated` (bool), `point_estimate`, `ci_low`, `ci_high`,
`baseline_value`, `base_rate`, `null_run_result`, `n_quarters`, `n_per_quarter`,
`specifications_tested` (a count).

Optional, carried through: `hit_rate`, `p_value`, `null_distribution`,
`endpoint`, `test_statistic`, `window`, `specifications`. On a held-out failure,
`predicted`, `actual`, `why` and `failure_mode` are carried into the `MISSES.md`
entry; absent ones fall back to a generic line, which is worse evidence, so write
them.

## `results/results.jsonl` — keys the orchestrator adds
Beyond the required set in §9, every orchestrated row carries:

| key | type | why |
|---|---|---|
| `n_per_quarter` | int/null | §9 says "n per quarter"; `n_quarters` is a count of quarters, not a count per quarter, so both are carried |
| `heldout_evaluated` | bool | without it a spent hypothesis cannot be told from an unfinished one, and the no-retry rule cannot be enforced mechanically |
| `state_fingerprint` | object | content hashes and counts of what the run saw, so "what changed since last run" is a diff of the append-only log rather than a mutable state file that could disagree with it |
| `gate_unmet`, `stopped_at`, `blocked_reason` | str | required when `verdict` is `insufficient_data`: the row must say what stopped it |
| `stages_run`, `orchestrator` | list/str | provenance of the run itself |

A row with `verdict` `signal` or `no_signal` and a null point estimate, interval,
baseline, base rate or null-run result is rejected and not written. Validate with:

```bash
python3 .claude/skills/review-tone-signal/scripts/results_row.py results/results.jsonl
```
