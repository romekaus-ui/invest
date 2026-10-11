# review-tone-signal

Does the tone of Amazon customer reviews carry information about a company's later
SEC-reported revenue, beyond what a naive expectation already implies?

This is a falsification harness, not a trading system. It produces measured effects
with confidence intervals and an explicit verdict in {`signal`, `no_signal`,
`insufficient_data`}. It never produces a buy, sell, target or position size.

## Quick start

No setup. No venv, no install, no dependency resolution — any CPython 3.9 or newer
runs the whole pipeline.

```bash
export SEC_USER_AGENT="Your Name your@email.example"   # SEC requires a contact
python3 .claude/skills/review-tone-signal/scripts/screen_universe.py --formation 2016-01-01
```

That writes `data/universe.csv` and its hash. Score every in-band company with the
scorecard in `references/company-selection.md`, then — and only then — run coverage.

That is a deliberate guarantee rather than a happy accident, and it holds for the
statistical stages too: **the pipeline is standard library only, including the
bootstrap intervals, the rank correlation and the shuffled-date null.** A row in
`results/results.jsonl` must stay reproducible years from now, and arithmetic that
lives in `scripts/` is pinned by the commit SHA — which cannot stop being
installable the way a wheel can. Decision and the measurements behind it:
[`references/numeric-stack.md`](.claude/skills/review-tone-signal/references/numeric-stack.md).

### Development setup (optional, not needed to run anything)

numpy and scipy are **development-only**: they are the oracle that the in-repo
statistics are cross-checked against, never the engine that computes a result.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt   # numpy 2.0.2, scipy 1.13.1
```

Exact pins, never ranges. Needed only to run the cross-check tests; skip it if you
are collecting data or reproducing a published row.

### Seeds

Any stage that resamples uses one convention, fixed in
[`references/numeric-stack.md`](.claude/skills/review-tone-signal/references/numeric-stack.md)
§3 so that two sessions cannot each invent one: `random.Random(seed)` per stage,
seeds derived from `20261011` by a per-stage offset, drawing only through
`.random()` / `.getrandbits()` / `.randrange()`. The seed is recorded in every
result row. `scripts/env_stamp.py` supplies both the generator and that metadata —
call it rather than re-deriving the convention.

## Layout

| Path | What |
|---|---|
| `REVIEW-TONE-CORE-PROMPT.md` | Locked rules. Read first. |
| `.claude/skills/review-tone-signal/` | The skill: router, references, scripts |
| `prereg/` | Frozen, hashed hypotheses |
| `data/` | Universe, eligibility, coverage, per-company folders |
| `results/results.jsonl` | Append-only run log |
| `MISSES.md` | Append-only failure log |
| `requirements-dev.txt` | Pinned dev-only numerics. Not a pipeline dependency. |

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
