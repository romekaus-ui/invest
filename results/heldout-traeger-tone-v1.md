# Held-out log — `prereg/traeger-tone-v1.md`

Running record of each held-out quarter as it arrives. **Not a result.** The prereg
requires **8** held-out quarters before any verdict (§5), and a running tally is
explicitly forbidden from being read as a finding.

Prereg sha256 `fa1f375d98a2ba9cc9be3be648c0481c57eb7d4d2bdbd9d0f549ae20d0c9b3f1`,
verified at the start of this run.

| quarter | revenue | surprise | S1 | S2 | agreement |
|---|---|---|---|---|---|
| FY2026Q3 | $74,682,000, filed 2026-11-05 | **−$50,714,000** (−40.44%) | **not defined** | **not defined** | **none recorded** |

**Evaluable held-out points so far: 0 of 8.** One held-out quarter has elapsed; it
produced no testable point.

## FY2026Q3 — recorded 2026-11-05

### Outcome side: present

`expected_revenue(FY2026Q3) = revenue(FY2025Q3) = $125,396,000` (filed 2025-11-06),
seasonal random walk, no drift, per §3. Reported revenue $74,682,000, original vintage,
filed 2026-11-05 at a 36-day lag, accession `0001628280-26-070415`. Surprise
**−$50,714,000**, sign negative.

### Signal side: not defined, so the point is not evaluable

`quarters.csv` records FY2026Q3 `tone_mean = −0.944444` (CI −1.472 to −0.389, n=36) —
the lowest in the series, and the value §0 of the prereg states was seen before
freezing. **But `tone_mean` is not the input to S1.** The prereg fixes

```
S1 = tone_within(Q) − mean(tone_within, prior 4 quarters)
```

and for FY2026Q3 `tone_within`, `baseline_prior4` and `signal` are all **blank**:

- **FY2026Q2 collected 26 reviews, below the floor of 30**, so it is `sufficient=false`
  with no tone number.
- `aggregate.py` computes the within/mix decomposition **strictly on adjacent
  sufficient pairs** and refuses to bridge a gap — "interpolating one is forward fill
  by another name". With FY2026Q2 insufficient, `tone_within(FY2026Q3)` is null.
- `signal = tone_within − baseline_prior4` is therefore null.

`manifest.json` for the 2026-10-20 aggregate run records `quarters_with_signal: 4` —
FY2025Q2, FY2025Q3, FY2025Q4 and FY2026Q1. **FY2026Q3 is not among them.** The signal
for the first held-out quarter was never computed, before or after the revenue was
known.

**S2 = S1 − S1_Weber is undefined for the same reason**, although Weber's own FY2026Q3
signal is present and positive at `+0.622757`.

No shuffled-date null was run: §4 requires one alongside every real evaluation, and
there is no evaluation here.

### Why this is not re-specified

The gap is a **collection shortfall in FY2026Q2**, not a defect in the hypothesis. It
could be closed by collecting more FY2026Q2-dated reviews, which would be backfilling a
past quarter's sample *after* seeing the outcome, and by CORE-PROMPT §4 the cadence is
the instrument. Prereg §5 forbids re-specifying against the held-out window. So the
point is recorded as not evaluable and nothing is adjusted. **Whether an unevaluable
held-out quarter consumes one of the 8 is not addressed by the prereg** — that gap is
reported, not resolved here.

### Base rate, reported beside the result as §4 requires

Measured on this dataset's original-vintage revenue: **11 of 14** quarters with a
computable surprise were negative, a base rate of **78.6%**; excluding the held-out
point itself, 10 of 13, **76.9%**. "Always predict a negative surprise" needs no
reviews, no scorer and no calibration.

Two other figures are in circulation and do not match: `results/backtest-traeger-tone-v1.md`
reports 10/15 = **66.7%** on the deeper `data/bbq-backtest/` panel, and the run brief for
this quarter cited **69% of 16 quarters**. Three panels, three denominators. The
direction is the same under all of them — the free baseline is high — and no agreement
record exists here to compare against any of them.

### §6.1 confound, which travels with this point

`manifest.json` carries the flag verbatim:

> F12 FY2026Q3: verified share 0.00 vs 0.33 over the prior 3 quarters — review
> authenticity changed, not a tone event

FY2026Q3 verified-purchase share is **0%**, against **33%** over the prior three
sufficient quarters (FY2025Q3 0.395, FY2025Q4 0.409, FY2026Q1 0.200). Weber's is 100%
in the same quarter. The lowest tone reading in the series is also the quarter in which
the reviewer population most obviously changed. The brief described this as a
"prior-four-quarter mean"; the flag is computed over the prior **three** sufficient
quarters, because FY2026Q2 is excluded by the same floor that broke the signal.

### Exploratory history, for context only

Pre-prereg and therefore not a test — scored by an author who had already seen the
revenue. On this dataset the three quarters with both a signal and a computable
surprise give **S1 2/3, S2 1/3**, against the 76.9% base rate. FY2025Q4 has a signal but
no revenue comparator, because 10-Qs do not report a fourth quarter.

The brief cited "2 of 4"; the fourth quarter is not evaluable for that reason. The
deeper backtest over FY2021Q2–FY2026Q1 reported **S1 5/15 = 33.3%, S2 6/15 = 40.0%**,
both below the base rate and both at the 1st and 4th percentile of their own
permutation nulls.

### What does not follow

No buy, sell, hold, target, position size or allocation, under this or any result
(prereg §7, CORE-PROMPT §1.1). One quarter is one quarter, and this one carries no
agreement record at all.
