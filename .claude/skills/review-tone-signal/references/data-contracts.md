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
`rubric_version`, `scorer_model_id`, `companies`, `window`, `n_quarters`,
`point_estimate`, `ci_low`, `ci_high`, `baseline_value`, `base_rate`, `null_run_result`,
`specifications_tested`, `verdict` ∈ {`signal`, `no_signal`, `insufficient_data`}.

`specifications_tested` is a count, not a list of the good ones. Reporting only the
surviving specification is F6.
