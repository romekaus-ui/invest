# Prereg v2 — does review tone carry information about revenue surprise? A panel test

**Status: DRAFT. Not frozen. Not in force.** It becomes a prereg only when §7 is
satisfied and its hash is recorded in `CHANGELOG.md`. Until then no result may cite it.

Supersedes nothing. `prereg/traeger-tone-v1.md` stays frozen over FY2026Q3+ and is a
separate, already-weakened document.

## 0. What the author has seen, stated first

The v1 hypothesis — that a fall in Traeger's review tone precedes a worse revenue
surprise — **failed** (`results/backtest-traeger-tone-v1.md`): 5/15 and 6/15 against a
66.7% base rate, both specifications in the bottom tail of their shuffled-date null.

Looking further, post-hoc, the continuous association on those same 15 quarters was
**negative and moderately strong** — Spearman −0.51 (S1) and −0.54 (S2), permutation
p = 0.051 and 0.043. That was found after the sign test failed, among at least four
looks across two specifications, with every outcome already known. It is a hypothesis,
not a result, and this document exists to test it on data the author has not spent.

**Therefore Traeger and Weber are excluded from the test set.** Their quarters cannot
appear in any confirmatory analysis here, under any specification. They may appear only
in §3's lag estimation, which touches no revenue figure.

## 1. Hypothesis

> Within a company, the quarter-over-quarter move in its customer-review tone is
> rank-associated with that quarter's reported revenue surprise against a seasonal
> naive expectation, and the association has a consistent sign across companies.

**The direction is not asserted.** The primary test is **two-sided**. The v1 backtest
suggests a negative sign, but it suggests it from spent data, and promoting that to a
one-sided hypothesis would be the re-specification §5 of v1 exists to forbid. The
negative direction is recorded as a declared secondary (§4) and counted as a test.

Falsified if the pooled association does not separate from the company-permutation null
in §4.

## 2. The test set, frozen before collection

| | |
|---|---|
| Companies | **BellRing Brands (BRBR), The Simply Good Foods Co (SMPL), YETI Holdings (YETI), Turtle Beach (TBCH)** |
| Excluded, spent | Traeger (COOK), Weber |
| Excluded, no revenue | Weber, Red Bull, Bose, Trove/Owala, Pacific Market International |
| Excluded, unreachable | Celsius, Monster, Logitech, Corsair, Sonos, Helen of Troy |

Membership is fixed by this document. A company is **not** added after collection
begins, and a company whose collection disappoints is **not** dropped — it is reported
with whatever quarters it yields, or reported as yielding none. Both failure modes are
how a four-company panel becomes a one-company panel by accident.

Each company's reviews come from the brand-hosted display API recorded for it in
`data/_route-tests/brand-widget-routes-2026-10-15.json`. Product families are selected
by the §1 rule in `CONTRACTS.md` — catalogue breadth, before any review is scored —
and the selection is written into `groups/` and hashed before scoring runs.

**Expected size, to be measured not assumed:** each company has roughly 15–28 quarters
where review depth and an EDGAR revenue vintage both exist (SMPL public since 2017,
YETI since 2018, BRBR since 2019, TBCH longest). The panel is therefore expected to
land near **60–100 company-quarters**. If it lands below 40, the test is reported as
underpowered and no verdict is spoken.

## 3. Quarter alignment, settled before the outcome is touched

v1 aligned reviews in quarter Q to revenue in quarter Q without checking whether that
is even the right pairing. A review is written after a purchase, so review-quarter and
purchase-quarter differ by an unknown lag, and a signal aligned to the wrong quarter
cannot work however good it is.

The lag is estimated **from review data only**, using no revenue figure, so it spends no
outcome:

1. On Traeger's and Weber's already-collected reviews — permitted here precisely because
   revenue is not involved — measure the distribution of (review date − purchase or
   ship date) wherever the platform exposes both fields.
2. Where it does not, measure the lag between a product's first-available date and its
   first review, as an upper bound on how late reviews arrive.
3. Take the **median** lag, rounded to whole quarters, as **L**.

**L is written into this file before §7 freezes it, and is then fixed.** The signal for
revenue quarter Q is built from reviews in quarter **Q + L**. One alignment is tested,
not several. If the estimate is ambiguous between two values, both are declared now and
counted as two specifications in §4.

### The measurement, run 2026-10-22

No route exposes a purchase or ship date, so method 1 was unavailable. Lag was estimated
from **stated ownership duration in review text** — a review-only field, touching no
revenue figure.

| | Traeger | Weber |
|---|---|---|
| reviews stating a duration | 614 / 3,840 (16.0%) | 3,621 / 41,775 (8.7%) |
| bought within ~1 month | 38.9% | 53.1% |
| ~1 quarter earlier | 12.7% | 24.0% |
| ~2 quarters earlier | 6.7% | 1.9% |
| **7.5+ months earlier** | **41.7%** | **21.1%** |
| median months owned | 4.0 | 1.0 |
| implied L | **1** | **0** |

> **`L ∈ {0, 1}`.** The estimate is ambiguous between two values and §3 requires both to
> be declared and counted. L=0 and L=1 are each run as full specifications; neither is
> promoted to primary after the fact.

**The estimator's own bias, stated:** fewer than one review in six states a duration, and
a reviewer who has owned the product for years is more likely to mention it than one who
bought it last week. The long tail is therefore overstated. The measurement establishes
that a wide spread exists; it does not pin its shape.

## 4. Signal, endpoint and analysis

```
tone_move(Q)   = tone(Q+L) − mean(tone, four quarters before Q+L)
expected_rev(Q)= revenue(Q−4)                       # seasonal random walk, unchanged
surprise(Q)    = reported_revenue(Q) − expected_rev(Q)
surprise_pct(Q)= surprise(Q) / |expected_rev(Q)|    # scale-free, required for pooling
```

Tone comes from rubric **v1** scored by **`claude-haiku-4-5-20251001`** at temperature
0 — the pairing calibrated at quadratic-weighted kappa **0.811**, adjacent agreement
0.900. A change to rubric or model voids this document. Sampling is **50 reviews per
company-quarter**, seeded, drawn before any score is seen, with the drawn ids recorded.
Quarters under **30 text-bearing reviews** are excluded, as in v1.

**Primary test.** Rank-correlate `tone_move` with `surprise_pct` across all
company-quarters, after demeaning both within company so that cross-company level
differences cannot create the association. Two-sided. Significance from a
**company-permutation null**: 10,000 replicates permuting quarter labels *within each
company independently*, which preserves each company's own autocorrelation and seasonal
shape. A plain pooled shuffle would overstate significance and is not used.

**Declared secondary tests, counted.** `specifications_tested = 6` (four below, each at L=0 and L=1 where applicable):

1. the one-sided negative direction the v1 backtest suggests;
2. the peer-relative construction (S2 in v1), where a group has a second reachable member;
3. the sign-agreement test v1 used, reported **beside its base rate**, retained only so
   the two documents are comparable;
4. leave-one-company-out: the primary statistic recomputed four times, each omitting one
   company. **An association that survives only with one particular company present is
   reported as a single-company finding, not a panel result.**

All four are reported whichever way they fall. Multiplicity is acknowledged rather than
corrected away: with four looks, a single p near 0.05 is not evidence, and the report
says so in those words.

## 5. What would count, decided now

| outcome | how it is reported |
|---|---|
| no separation from the null | **the hypothesis is dead.** Not re-specified, not re-run, not threshold-tuned. |
| separation, but fails leave-one-company-out | a single-company observation, needing a new panel |
| separation, surviving leave-one-out, sign consistent | **the strongest thing this project can produce from history**, and still not a verdict — it licenses an out-of-sample test, nothing more |
| fewer than 40 company-quarters | underpowered; measurements published, no verdict |

**Power, stated honestly.** At ~80 observations clustered in four companies, the
effective sample is well below 80. This design can detect a strong association
(|rho| ≳ 0.4) and **cannot** rule out a weak real one (|rho| ≈ 0.15–0.2). A null
result therefore means "no strong effect here", never "no effect".

## 6. Confounds, declared now

1. **Four companies is n=4.** A sign consistent across four firms in three categories is
   much weaker than the same across forty, and the leave-one-out test exists because of it.
2. **Brand-moderated reviews.** Every route is a platform the company itself administers,
   with most reason to remove negative reviews when tone would otherwise fall. The
   Traeger deletion test found 0% absent across four sources; that is one company.
3. **Revenue coverage is unquantified for all four.** The share of each company's revenue
   these product families represent has never been measured. No series here may be read
   as a view of a whole company.
4. **YETI's route is partial after 2022** per the coverage measurement, so its recent
   quarters may thin or vanish. Reported, not patched.
5. **Discounting as a non-predictive mechanism.** A promotional quarter can raise review
   tone (buyers got a bargain) while revenue falls — producing the v1 negative
   correlation with no forecasting content. This design **cannot** distinguish that from
   a real leading indicator. Doing so needs price or promotion data the project does not
   have, and that limitation is reported with any positive result.
6. **Purchase dates are smeared, not lagged** — the §3 measurement's real finding, and
   the most likely reason v1 failed. A quarter's reviews mix purchases spanning years
   (41.7% of Traeger's duration-stating reviewers bought 7.5+ months earlier), so no
   choice of L maps a review quarter cleanly onto a revenue quarter. This attenuates any
   true association toward zero by an unmeasured factor, which means **a null result here
   is even weaker evidence of absence than §5's power note already says.** Fixing it
   needs a purchase-dated review source, which no reachable route provides.
7. **The within/mix decomposition remains provisional**, traceable to a second-hand
   reconstruction rather than the source paper.

## 7. Freezing

This document is frozen only when all of the following are true, in this order:

1. §3's lag measurement has run and **L is written in above**;
2. the product families for all four companies are declared and hashed in `groups/`;
3. **no review from any of the four has been scored**;
4. the file's sha256 is recorded in `CHANGELOG.md`.

Then collection runs, then scoring, then the analysis **once**.

## 8. What this never produces

No buy, sell, hold, target, position size or allocation, under any result.
`CORE-PROMPT.md` §1.1 is not relaxed by a passing test. The outputs are the effect, its
interval, the null, the base rate, the leave-one-out table and the declared confounds.
