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

> **The formula in this section is PROVISIONAL and unverified.** It is the
> highest-risk part of this file: reconstructed second-hand, and the part that
> decides whether a tone reading means a company declining or a catalogue aging
> into grumpier reviews. §8 carries the open row to reconcile it against the
> paper; where the paper differs, the paper wins. `scripts/aggregate.py`
> isolates it in one function (`decompose()`) tagged `DECOMPOSITION_SPEC`, and
> that tag is written into every manifest, so outputs built under this version
> stay identifiable after the paper is read.
>
> What is **not** provisional, because it rests on CORE-PROMPT §5/§7 and
> `data-contracts.md` rather than on the paper: that the split must happen at
> all, that only the within-ASIN component may feed the signal, and that the
> three components must reconcile to the total change.

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

### 4b. Second specification: peer-relative tone

**Decided 2026-10-11, not yet frozen.** Two specifications will be preregistered, not
one:

```
S1 (primary)  signal(Q)      = tone_within(Q) − baseline(Q)          [as above]
S2 (peer)     signal_peer(Q) = signal(Q) − mean_{j in peers(i)} signal_j(Q)
```

A category-wide shock moves every brand in a group together. If snack-food reviews sour
across the board in one quarter, S1 reads that as news about each company; S2 reads it as
news about the category and keeps only what is specific to the firm. Which of the two
carries information is an empirical question, and preregistering both is cheaper and more
honest than preregistering one and wishing afterwards it had been the other.

**Peer set construction**, fixed at formation and never revised:

- Peers are the other companies in the same **3-digit SIC group**, within the **same
  formation cohort**, that clear A2 eligibility, A3 coverage and the §5 quarter floor.
- The set is computed once, from the cohort, before any tone is scored. A peer set
  chosen after seeing which companies produced a clean series is selection.
- A company with **no surviving peer** emits no `signal_peer` for any quarter. It is
  reported as peerless and **stays in S1** — dropping it would make the two
  specifications run on different samples, and a difference between them would then be
  partly a difference of population.

**Feasibility, measured before committing to it.** At the 2016 formation the shortlist's
83 company-years fall into 38 three-digit SIC groups, of which 20 hold two or more —
covering 65 of the 83. The largest are apparel retail (7), supplements (5), furniture (5)
and consumer electronics retail (5). This is measured at the **shortlist** stage: A2 and
A3 both cut further, so the realised peer count will be smaller, and a group that loses
all but one member yields a peerless company rather than a peer signal.

**Cost, stated plainly.** This is a second specification against §6's multiple-testing
budget. Both are declared in the prereg with their thresholds before the backtest runs,
`specifications_tested` records 2, and **a result that holds on S2 but not S1 is reported
as exactly that** — not as "the signal works". The shuffled-date null runs against both.

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
| 2026-10-11 | `tone_within` / `tone_mix` / `tone_residual` in `quarters.csv` are the **Δ components** of §3, not levels | The contract requires the three to reconcile to the total tone change, which only holds if they are the change components. §4's "within-ASIN tone" therefore reads as `Δtone_within`, and `baseline_prior4` is the mean of the prior four within-ASIN *changes*. The alternative — chaining the within components into a mix-adjusted level — would break the reconciliation the decomposition exists to guarantee |
| 2026-10-11 | The residual carries the within×mix **interaction** as well as entrants and exits | §3 names only entrants and exits, but `Δw·Δt` is left over by the two named components. Folding it into the residual makes `within + mix + residual = total` exact rather than approximate. `aggregate.py` builds the residual from its three terms explicitly and then asserts the identity, so the assertion tests the code instead of restating a definition |
| 2026-10-11 | The emitted bootstrap CI is on `tone_mean`, not on `signal` | §4 asks for a CI on `signal(Q)`; the contract's `quarters.csv` has only `tone_ci_low` / `tone_ci_high`, and a signal CI requires a joint resample across five quarters. CORE-PROMPT §5's requirement (every tone mean carries an interval) is met. **Open gap:** the signal-level interval belongs to the backtest stage and must be specified in the prereg before `backtest.py` is written |
| 2026-10-11 | `baseline_prior4` requires all four prior within values; no partial baseline | A mean over three quarters in one row and four in another is two different estimators in one column. That is how a formula changes mid-series without anyone deciding to change it |
| 2026-10-11 | The decomposition requires Q and Q−1 to be *adjacent* and both above the review floor | Reaching past a gap to the last usable quarter is forward fill by another name (CORE-PROMPT §4). A quarter after a gap emits no decomposition |
| 2026-10-11 | F4's residual flag is measured against `\|within\|+\|mix\|+\|residual\|`, not against the total change | The total change can sit near zero through cancellation; dividing by it reported a quiet quarter as a 900% residual alarm during the build. The gross-magnitude denominator is scale-free and the flag names which term dominates, since entrant/exit churn and the interaction term are different problems |
| 2026-10-11 | A second specification, peer-relative tone (§4b), will be preregistered alongside the primary | A category-wide demand or sentiment shock moves every brand in a group together, and the primary specification reads that as company news. Feasibility was measured before the decision rather than assumed: 20 of 38 SIC groups in the 2016 shortlist hold two or more company-years. It costs one specification against §6 and is declared before the freeze, not chosen after a result |
| _pending_ | Reconcile all of the above against the paper's actual construction | Paper not yet read in full |
