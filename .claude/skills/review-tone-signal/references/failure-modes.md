# Failure Modes

Each entry is a blocking check. Read this file **when a result looks good** — that is the
moment it matters, and the moment it is least likely to be read.

Every one of these fails *silently*. None of them throws an error. All of them produce a
plausible number.

---

## F1 · Hindsight selection
**Symptom:** strong result on a handful of famous collapses.
**Mechanism:** the companies were chosen because they collapsed. The signal is your own
memory, laundered through a pipeline.
**Check:** universe hash exists, formation date precedes every outcome in the window,
delisted and acquired names are present. If the universe contains no companies that did
fine, it is not a universe.

## F2 · Look-ahead through the filing date
**Symptom:** suspiciously strong results, especially near period ends.
**Mechanism:** joining on period end instead of EDGAR `filed` date gives the model
revenue 4–8 weeks before it was public.
**Check:** every revenue join carries a `filed` date; assert `filed <= decision_date`.

## F3 · Coverage illusion
**Symptom:** clean tone series on a company where Amazon is a trivial channel.
**Mechanism:** measuring 2% of revenue precisely and calling it a read on the firm.
**Check:** coverage ratio computed and reported per company; unquantifiable coverage
scores 0 on criterion 1, never a charitable guess.

## F4 · Product-mix drift read as decline
**Symptom:** smooth multi-quarter tone decay with no corresponding event.
**Mechanism:** the portfolio aged; new SKUs entered at different tone levels.
**Check:** `Δtone_mix` and the entrant/exit residual reported next to every
`Δtone_within`. A large residual invalidates the quarter.

## F5 · Sample too small
**Symptom:** large, exciting swings in the signal.
**Mechanism:** a mean of six reviews. The source video's own example.
**Check:** `MIN_REVIEWS_PER_QUARTER` enforced in code; CI reported on every estimate. If
the CI spans zero, the quarter said nothing.

## F6 · Multiple testing
**Symptom:** one company, one threshold, one horizon works beautifully.
**Mechanism:** 12 companies × thresholds × horizons guarantees a winner.
**Check:** prereg hash present and frozen before the run; held-out untouched; shuffled
null run executed; all tested specifications reported, not just the surviving one.

## F7 · Base rate illusion
**Symptom:** "it is right 62% of the time."
**Mechanism:** revenue rises in most quarters. Always guessing "up" also scores well.
**Check:** base rate printed adjacent to every hit rate, always, including in summaries
and conversation.

## F8 · Star-rating circularity
**Symptom:** tone tracks the star average almost exactly.
**Mechanism:** the scorer saw the star rating and echoed it. You have rebuilt the star
average and attributed novelty to it.
**Check:** stars withheld from the scorer; correlation between tone and star mean
reported. If it is ~0.95, the text added nothing and the result is about stars.

## F9 · Sampling-regime mismatch
**Symptom:** backtest passes; live results behave nothing like it.
**Mechanism:** the backtest used complete dated history. Live collection since late 2024
returns a helpfulness-sorted slice of ~50 reviews — biased toward old, heavily-upvoted
text. That is not a time series.
**Check:** Phase C. Degrade the historical data to the live regime and re-run. No live
spending before it passes.

## F10 · Scorer drift
**Symptom:** results shift after a re-run with no code change.
**Mechanism:** model version changed underneath the pipeline.
**Check:** exact model ID and rubric version stored on every score row; re-score is a new
version, never an overwrite.

## F11 · Fiscal calendar error
**Symptom:** signal works at a strange lag, or inverts.
**Mechanism:** calendar quarters used where fiscal quarters were meant. Shifts every join.
**Check:** fiscal calendar verified per company from the 10-K and stored in the manifest.

## F12 · Incentivised and deleted reviews
**Symptom:** tone implausibly high in a product's early quarters.
**Mechanism:** review gating, vine/incentivised reviews, and Amazon's own deletions make
the review population non-stationary and not a random sample of buyers.
**Check:** `verified_purchase` share reported per quarter; unverified reviews analysed as
a separate series before any decision to include them.

## F13 · Silent scoring failures
**Symptom:** a clean-looking quarter with an unremarked gap.
**Mechanism:** malformed scorer output dropped without being counted.
**Check:** `scoring_failed` count reported per quarter; a quarter losing >10% of reviews
to scoring failure is flagged, not quietly averaged.

---

## The honest default

Most of these end in the same place: **the result is null.** That is the expected outcome
for a method reconstructed from a 61-second video, and reporting it is the system working
correctly, not a failure of effort.
