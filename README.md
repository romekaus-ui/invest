# review tone monitor

Tracks the tone of **fresh Amazon customer reviews** for peer groups of consumer-facing
companies worldwide, and shows each company against the competitors it actually faces.

It is a measuring instrument. It reports tone, its change, the peers, and the volume and
coverage behind every number. It does not produce a buy, sell, target or position size.

**Read [`CORE-PROMPT.md`](CORE-PROMPT.md) first.** It governs everything here.

## How it fits together

```
peer group          who competes with whom, declared before anything is collected
    ↓
collection          fresh reviews from Amazon — logged-in session (sort by recent)
                    or a commercial provider
    ↓
tone scoring        versioned rubric, calibrated against human labels first
    ↓
aggregation         tone over time, per company and relative to its peer group
    ↓
dashboard           the chart, with coverage and volume shown beside every number
```

## State

| Component | Status |
|---|---|
| Peer-group finder (`review-tone-peer-group`) | built — 6 groups, 15 companies declared |
| Fresh review collector | built — Route B measured working through Apify |
| Workflow (`/tone-run`) | built — walks the stages, stops at the first unmet |
| Tone scorer (`score_reviews.py`) | built, carried over |
| Aggregation (`aggregate.py`) | built, carried over — needs rework for live cadence |
| US revenue (`fetch_sec.py`) | built, carried over |
| Dashboard skill | built, carried over |

No reviews collected yet, so there is nothing to chart. The series starts the day
collection starts.

Declared groups are in [`groups/`](groups/) (the `CONTRACTS.md` §1 view the workflow
reads) and `data/peer_groups/` (the hash-chained declarations of record). See
[`CHANGELOG.md`](CHANGELOG.md) for what the concentration filter did to the candidate
pool — the short version is that the pool of focused, Amazon-selling, publicly-reporting
consumer companies is roughly 40–60, not 80+, and six of 36 US candidates have left the
public market recently.

## The two things worth knowing before trusting a number

**Coverage.** Amazon moved reviews behind a login wall in November 2024 and caps
pagination. What is collectable is a fraction of what exists, so every tone number is
reported with the ratio of reviews collected to reviews the product claims. A tone mean
over 2% of a product's reviews is not that product's tone.

**The series is forward-only.** Fresh collection cannot reconstruct what reviews looked
like last year. A claim that tone *predicts* revenue needs the test declared in advance
and then several reporting periods to run. Until then the honest answer is *not yet
known* — see `CORE-PROMPT.md` §8.

## Archive

[`archive/historical-harness/`](archive/historical-harness/) holds the previous design: a
falsification harness testing the same idea against `McAuley-Lab/Amazon-Reviews-2023`, a
frozen research corpus ending September 2023. Archived rather than deleted — it is the
only cheap way to ask whether review tone has *ever* led revenue, which the live system
cannot answer for several quarters.
