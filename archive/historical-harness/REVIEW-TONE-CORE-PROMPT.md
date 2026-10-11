# REVIEW-TONE-CORE-PROMPT — LOCKED

Governing rules for the `review-tone-signal` skill. Treat every rule here as binding.
Never edit this file directly. Changes go through `REVIEW-TONE-CHANGE-PROPOSALS.md`
and require explicit user approval.

Version: 1.0 · Locked 2026-10-11

---

## 0. What this system is

A **falsification harness**. It measures whether the tone of dated Amazon customer
reviews carries information about a company's later SEC-reported revenue, beyond what a
naive expectation model already implies.

"No signal" is a correct, expected and publishable result. The system is built to detect
its own failure, not to produce a trade.

Origin: Nurmukhametov & Shabalin-style construction described in *"More than words: Can
tone of consumer product reviews help predict firms' fundamentals?"* (IDEAS/RePEc). The
paper is the baseline spec; every deviation from it is logged in
`references/methodology.md` with a justification.

## 1. Hard prohibitions

These are not preferences. Violating any one invalidates the run.

1. **No investment advice.** This system never outputs a buy, sell, hold, price target,
   position size, entry/exit, allocation or portfolio weight — not for the user, not as
   an example, not hedged, not hypothetical. It outputs measured signal, sample size,
   confidence interval, and whether the result is distinguishable from noise. If asked
   for a recommendation, state that this is a research instrument and decline the call.
2. **No hindsight selection.** A company may never enter the universe because of what
   happened to it. See §2.
3. **No arithmetic by model.** Every number that reaches a result — tone means, deltas,
   baselines, hit rates, p-values, confidence intervals — is computed by a script in
   `scripts/` and read from its output. The model never calculates these in prose.
4. **No held-out contamination.** Held-out quarters are not inspected, plotted, described
   or reasoned about until the prereg is frozen and the development phase is closed.
5. **No logged-in Amazon scraping, no CAPTCHA solving, no bot-detection evasion.** The
   browser is permitted only for small manual validation samples on publicly visible
   pages.
6. **No deletion of a negative result.** Misses are appended to `MISSES.md`, never
   removed or rewritten.

## 2. Universe construction

The universe is defined **mechanically and point-in-time**, before any outcome is seen.

- Screen the full population of US-listed registrants first, by SIC code and by
  point-in-time market capitalisation at the **formation date**. Only then check Amazon
  review coverage. The gap between "structurally eligible" and "has usable review data"
  is itself a recorded finding — it is never silently closed by swapping in a company
  that happens to have data.
- Market cap at formation is computed from EDGAR `dei:EntityCommonStockSharesOutstanding`
  as of the last filing on or before the formation date, multiplied by the closing price
  on that date. Never a present-day market cap.
- Every company meeting the rule enters the test, including ones that performed well,
  were acquired, or delisted. Delisted and acquired names are **required** — dropping
  them is survivorship bias.
- The eligibility scorecard in `references/company-selection.md` may exclude a company
  only on **structural** grounds knowable at formation (channel mix, portfolio breadth,
  segment disclosure). It may never exclude on performance.
- The resulting `universe.csv` is hashed and committed before any review data is touched.

## 3. Endpoints

- **Primary: revenue surprise.** Reported quarterly revenue versus an expectation
  baseline, not raw revenue direction. Raw direction is largely anticipated and a model
  that predicts it has demonstrated nothing.
- **Secondary: stock return.** Reported, clearly labelled as far noisier, and never the
  basis of a conclusion. Price moves on rates, multiples, index flows and narrative,
  none of which consumer review tone can observe.
- A result that holds on revenue but not on price is a legitimate positive result for
  this system. A result that holds only on price is treated as noise until explained.

## 4. Point-in-time discipline

- Revenue is known only at its EDGAR `filed` date, never at period end.
- Restated figures retain their original vintage. The backtest sees the number that was
  actually published at the time.
- A review enters quarter Q only if its timestamp falls inside Q. No forward fill, no
  interpolation across quarters.

## 5. Sample-size floors

- A quarter with fewer than `MIN_REVIEWS_PER_QUARTER` scored reviews (default 30) emits
  `insufficient_history`, not a number.
- A company with fewer than `MIN_QUARTERS` usable quarters (default 12) is dropped from
  the test and recorded as dropped, with the reason.
- Every tone mean is reported with a bootstrap confidence interval. A point estimate
  without an interval is an incomplete result.

## 6. Multiple testing

- The prereg in `prereg/` fixes the hypothesis, thresholds, horizons and endpoints
  **before** the backtest runs. It is hashed and committed; the hash appears in every
  result row.
- Held-out: the final 8 complete quarters of the window, untouched during development.
- Every reported hit rate is accompanied by the **base rate** of the same event. 60%
  accuracy against a 65% base rate is a failure, and must be reported as one.
- A shuffled-date null run is executed alongside every real run. If the real result does
  not separate from the null, the result is null.

## 7. Tone scoring

- The rubric is versioned and frozen (`references/tone-rubric-v1.md`). Scores record the
  rubric version and the exact model ID that produced them.
- Before any scored result is trusted, a human-labelled calibration set of at least 100
  reviews is scored and agreement is measured. Agreement below the threshold in the
  rubric blocks the pipeline.
- Reviews that concern only the seller, shipping, packaging or marketplace logistics are
  tagged and excluded from the company-tone aggregate.
- Tone change is decomposed into **within-ASIN** change and **product-mix shift**. Only
  the within-ASIN component feeds the signal. A portfolio that ages into grumpier reviews
  is not a company in decline.

## 8. Phase gates

No phase begins until the prior phase passes its gate.

| Phase | Gate |
|---|---|
| A · Universe + coverage | >= 8 structurally eligible companies with usable coverage |
| B · Full-history backtest | Beats baseline on held-out quarters, separates from null |
| C · Degradation test | Signal survives the ~50-review helpfulness-sorted regime |
| D · Live forward run | Only reachable if C passes |

Phase C exists because Amazon closed review access in late 2024: ~5 pages visible,
recency sort behind login, logged-out pages blocked. Live collection today returns a
helpfulness-sorted slice, which is not a time series. If the signal dies under that
sampling regime, the live product is dead, and that is established for free before any
money is spent.

## 9. Output contract

Every run writes a row to `results/results.jsonl` containing at minimum: run ID, prereg
hash, universe hash, rubric version, scorer model ID, phase, company set, window,
n per quarter, point estimate, CI, baseline value, base rate, null-run result, and
verdict in {`signal`, `no_signal`, `insufficient_data`}.

Session summaries append to `REVIEW-TONE-CHANGELOG.md`.
