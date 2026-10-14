# Tone Rubric v1

**Frozen.** Changing any wording below changes every score produced under it. To change
the rubric, create `tone-rubric-v2.md`; never edit this file. Scores record the rubric
version and the exact scorer model ID.

---

## Scale

Score the reviewer's **evaluation of the product**, on this integer scale:

| Score | Meaning |
|---|---|
| **+2** | Strongly positive. Delighted, would recommend without reservation, exceeded expectations. |
| **+1** | Positive with reservations. Satisfied, but names a limitation or minor annoyance. |
| **0** | Neutral, mixed, or purely descriptive. Equal praise and complaint, or no evaluative content. |
| **−1** | Negative with reservations. Disappointed, but the product partly works or has redeeming qualities. |
| **−2** | Strongly negative. Product failed, broke, or did not do what it claims. Would not recommend. |

## Rules

1. **Score the text, not the star rating.** The star rating is not shown to the scorer.
   Text and stars disagree often enough that inheriting the star makes the exercise
   circular — you would be predicting revenue from star ratings with extra steps.
2. **Score the product, not the transaction.** A furious review about late delivery on a
   product the reviewer likes is `seller_or_shipping_only`, not −2.
3. **Sarcasm counts as its literal evaluative intent.** "Great, it died in a week" is −2.
4. **Length is not intensity.** A long measured complaint is −1; a short furious one is −2.
5. **No hedging to the middle.** 0 means genuinely neutral or mixed, not "unsure".

## Tags

Each review carries independent boolean tags alongside its score:

- `seller_or_shipping_only` — concerns only marketplace, seller, packaging or delivery,
  and makes no evaluation of the product itself. **Excluded from the tone aggregate.**
- `defect` — reports a functional failure: broke, stopped working, arrived faulty,
  failed under normal use.
- `price_complaint` — concerns price, value for money, or a price change — not quality.

A review may carry several tags. `defect` and `price_complaint` do not exclude it.

## Output format

Strict JSON, one object per review, no prose:

```json
{"review_id": "<id>", "tone": -2, "seller_or_shipping_only": false, "defect": true, "price_complaint": false}
```

Any malformed response is retried once, then recorded as `scoring_failed` and excluded.
Failures are counted and reported — a quarter where 20% of reviews failed to score is not
a clean quarter.

## Calibration gate

Before any scored result is trusted:

1. Draw 100 reviews at random, stratified across the score range.
2. The user labels them by hand against this rubric, blind to model output.
3. Compute exact agreement, adjacent agreement (within 1), and quadratic-weighted kappa.

**Gate: quadratic-weighted kappa >= 0.60 and adjacent agreement >= 0.85.** Below either,
the pipeline is blocked. Fix the rubric or the model; do not proceed with an unvalidated
scorer. An unvalidated scorer makes every downstream statistic decorative.

Calibration is re-run whenever the rubric version or scorer model changes.

## Scorer

Default: `claude-haiku-4-5`, temperature 0, one review per call, no conversation
history between reviews. Reviews are scored independently so that batch ordering cannot
leak context from one review into another.
