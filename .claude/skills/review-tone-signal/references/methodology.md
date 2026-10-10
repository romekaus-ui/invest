# Methodology

Signal construction, endpoints and every deviation from the source paper.

Source: *"More than words: Can tone of consumer product reviews help predict firms'
fundamentals?"* (IDEAS/RePEc). **Before the first real run, fetch and read the paper, and
reconcile this file against its actual construction.** The spec below is a faithful
reconstruction of the method as described second-hand; where the paper differs, the paper
wins and the deviation is logged in §6 with a reason.

---

## 1. Unit of observation

One `(company, fiscal_quarter)` row. Fiscal quarters, not calendar — a company's Q4 is
whatever its own fiscal calendar says, verified per company from its 10-K.

A review belongs to quarter Q if its timestamp falls inside Q's fiscal date range. No
forward fill. No interpolation. No reassignment.

## 2. Tone

Each review is scored on an integer scale from −2 to +2 by the rubric in
`tone-rubric-v1.md`, and tagged:

- `seller_or_shipping_only` — concerns the marketplace, seller, packaging or delivery and
  says nothing about the product. **Excluded from the aggregate.**
- `defect` — reports a functional failure.
- `price_complaint` — concerns price or value, not quality.

Defect and price tags are retained as separate series. They are plausibly the
informative part — a tone drop driven by defect reports is a different object from one
driven by price grumbling, and collapsing them loses that.

## 3. Aggregation, and the mix decomposition

Naive company tone for quarter Q is the mean of included review scores. That number is
**not** the signal, because it moves for two different reasons:

- **within-ASIN change** — the same products getting better or worse received
- **mix shift** — the product mix drifting toward items with structurally different tone

A maturing portfolio drifts grumpier regardless of company health. Only the within-ASIN
component is interpretable as a signal about the business.

Decomposition, over ASINs present in both Q and Q−1:

```
Δtone_total   = tone(Q) − tone(Q−1)
Δtone_within  = Σ_i w_i(Q−1) · [tone_i(Q) − tone_i(Q−1)]
Δtone_mix     = Σ_i [w_i(Q) − w_i(Q−1)] · tone_i(Q−1)
```

where `w_i` is ASIN i's share of that quarter's included reviews. Entrants and exits are
reported separately as a residual; a large residual means the portfolio churned and the
quarter's signal is weak regardless of its value.

**Only `Δtone_within` feeds the signal.**

## 4. The signal

Abnormal tone for quarter Q, following the reel's construction and pending reconciliation
with the paper:

```
baseline(Q) = mean of within-ASIN tone over the prior four quarters
signal(Q)   = tone_within(Q) − baseline(Q)
```

Every `signal(Q)` is reported with a bootstrap 95% CI from resampling reviews within the
quarter. A quarter below `MIN_REVIEWS_PER_QUARTER` (default 30) emits
`insufficient_history` and no number. The source video's worked example rests on six
reviews; six reviews cannot distinguish a mood from a rounding error.

## 5. Endpoints

### Primary: revenue surprise

**Not raw revenue direction.** Revenue direction is largely anticipated; predicting it
demonstrates nothing, because the expectation was already public. The question is whether
tone carries information the expectation did not.

```
surprise(Q+1) = reported_revenue(Q+1) − expected_revenue(Q+1)
```

Expectation baseline, in order of preference:

1. Analyst consensus, if available. Not purchased before Phase C passes.
2. **Seasonal random walk** (default, free): `expected = revenue(Q+1 − 4 quarters)`.
3. Seasonal random walk with drift, drift estimated on development data only.

The null hypothesis is that `signal(Q)` has no relationship with `surprise(Q+1)` once the
baseline is accounted for. The test is specified in the prereg before it runs.

### Secondary: stock return

Reported, always labelled as far noisier, never load-bearing for a conclusion. Price
responds to rates, multiple compression, index flows, buybacks and narrative — none
observable in review text. A result that holds on revenue and not on price is a genuine
positive for this system. A result that holds **only** on price is treated as noise until
a mechanism explains it.

## 6. Point-in-time discipline

- Revenue is known at its EDGAR `filed` date, not at period end. The gap is typically
  4–8 weeks and is where look-ahead bias enters.
- Restatements keep their original vintage. The backtest sees what was published then.
- Fiscal calendars are verified per company and stored in the manifest. Getting a fiscal
  calendar wrong shifts every join by a quarter and will manufacture or destroy a result.

## 7. Guarding against a manufactured result

- **Prereg frozen and hashed** before the backtest: hypothesis, threshold, horizon,
  endpoint, test statistic. The hash is recorded in every result row.
- **Held-out:** final 8 complete quarters, untouched during development.
- **Base rate always reported** alongside hit rate. Revenue rises in most quarters; 60%
  accuracy against a 65% base rate is a failure and is reported as one.
- **Shuffled-date null** run alongside every real run: identical pipeline, review
  timestamps permuted within company. If the real result does not separate from the null
  distribution, the result is null.
- **Deviations logged.** Any change to a formula after seeing development results is
  recorded here with the date and reason. Undocumented tuning is how a backtest becomes
  fiction.

## 8. Deviations from the source paper

| Date | Deviation | Reason |
|---|---|---|
| 2026-10-11 | Primary endpoint is revenue *surprise* vs a baseline, not revenue level | Predicting an anticipated quantity demonstrates nothing about information content |
| 2026-10-11 | Within-ASIN/mix decomposition added | Portfolio ageing otherwise masquerades as business decline |
| _pending_ | Reconcile all of the above against the paper's actual construction | Paper not yet read in full |
