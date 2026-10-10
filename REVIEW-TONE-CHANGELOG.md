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
loses FY2020Q2–Q4, HELE the whole pre-FY2014 era.

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
