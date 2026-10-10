# review-tone-signal

Does the tone of Amazon customer reviews carry information about a company's later
SEC-reported revenue, beyond what a naive expectation already implies?

This is a falsification harness, not a trading system. It produces measured effects
with confidence intervals and an explicit verdict in {`signal`, `no_signal`,
`insufficient_data`}. It never produces a buy, sell, target or position size.

## Quick start

```bash
export SEC_USER_AGENT="Your Name your@email.example"   # SEC requires a contact
python3 .claude/skills/review-tone-signal/scripts/screen_universe.py --formation 2016-01-01
```

That writes `data/universe.csv` and its hash. Score every in-band company with the
scorecard in `references/company-selection.md`, then — and only then — run coverage.

## Layout

| Path | What |
|---|---|
| `REVIEW-TONE-CORE-PROMPT.md` | Locked rules. Read first. |
| `.claude/skills/review-tone-signal/` | The skill: router, references, scripts |
| `prereg/` | Frozen, hashed hypotheses |
| `data/` | Universe, eligibility, coverage, per-company folders |
| `results/results.jsonl` | Append-only run log |
| `MISSES.md` | Append-only failure log |

## Data

- **Reviews:** `McAuley-Lab/Amazon-Reviews-2023` — 571.5M reviews, 1996–Sep 2023,
  streamed, never stored. 41 GB metadata + 120 GB reviews across the relevant
  categories. Free.
- **Fundamentals:** EDGAR `companyfacts`, with filing dates. Free.
- **Prices:** Yahoo chart API, split-corrected. Free.

Phase A and B cost nothing but bandwidth. Nothing is purchased before Phase C passes.

## Why the backtest uses a 2023 corpus rather than live data

Amazon closed review access through late 2024: roughly five pages visible per
product, the recency sort behind login, logged-out review pages blocked. Live
collection today returns a helpfulness-sorted slice of ~50 reviews — biased toward
old, heavily-upvoted text. That is not a time series, and a tone chart built on it
measures Amazon's ranking algorithm rather than consumer sentiment.

So the historical corpus carries the backtest, and Phase C degrades that corpus to
the sample you could actually buy today and re-runs the identical test. If the
signal dies under the degraded regime, the live product is dead — established for
free, before any money is spent.
