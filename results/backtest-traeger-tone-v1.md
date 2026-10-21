# Backtest — Traeger review tone vs revenue surprise, FY2021Q2–FY2026Q1

**This is a backtest, not a test of `prereg/traeger-tone-v1.md`.**
That prereg (sha256 `fa1f375d98a2ba9c…`) covers **FY2026Q3 onward** and is untouched by
this document. Every quarter below was scored by an author who had already seen its
reported revenue. No result here may cite the prereg hash, and a positive result here
would have been worth very little for exactly that reason.

Run 2026-10-21. Data `data/bbq-backtest/`, reviews collected to the earliest available
date (Traeger 3,840 from 2016-02-11; Weber 41,775 from 2018-01-05), 1,548 reviews
scored at 50/quarter sampling, rubric v1 / `claude-haiku-4-5-20251001` / temperature 0,
the pairing calibrated at quadratic-weighted kappa 0.811. Scoring cost $1.0216.

## Result

| | hit rate | base rate (always-negative) | shuffled-date null, 2,000 reps |
|---|---|---|---|
| **S1** absolute | **5 / 15 = 33.3%** | 10 / 15 = 66.7% | null mean 7.69; observed at the **1st percentile** |
| **S2** peer-relative | **6 / 15 = 40.0%** | 10 / 15 = 66.7% | null mean 7.96; observed at the **4th percentile** |

Both specifications lose to the free baseline by roughly 27–33 points, and both fall
below the bulk of their own permutation null.

## Reading it

Under §4 of the methodology this is a failure and is recorded as one. "Always predict a
negative surprise" — which requires no reviews, no scorer and no calibration — beats
both declared specifications on the same quarters.

The signs sit **opposite** more often than chance, which invites the obvious move of
inverting the signal. That move is refused. §5 of the prereg forbids re-specifying a
hypothesis against the quarters that killed it, and the inversion has no mechanism
behind it: the 1st-percentile position is one draw from a 15-quarter window in which
two thirds of the outcomes share a sign, and an inverted rule would score 10/15 — the
base rate — without adding anything to it.

## Why this is the useful direction of error

The whole apparatus was biased toward flattering the signal. The outcomes were known
before the scores were read, the sampling and the product families were both chosen
after looking, and the author wrote the specifications having already seen Traeger's
tone collapse alongside falling revenue. Every one of those pressures pushes the hit
rate up. It came out at a third.

## Window limit

Revenue begins FY2021Q1 — Traeger listed in July 2021, so EDGAR has no earlier
quarterly vintages. Review history reaches 2016 and tone is measurable from FY2017Q2,
but the endpoint is not, so 20 scorable quarters yield 15 evaluable ones. More review
history cannot extend this; only more filings can, at one quarter per quarter.

## What is unchanged

- The prereg stands, frozen, over FY2026Q3+. It is now a test of a hypothesis that has
  already failed on its own pre-period, which is worth saying out loud when FY2026Q3's
  revenue lands around 2026-11-06.
- `CORE-PROMPT.md` §1.1 is unaffected in either direction. No buy, sell or hold follows
  from this file.
