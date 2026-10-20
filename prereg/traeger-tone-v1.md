# Prereg — Traeger review tone vs reported revenue surprise

**Frozen 2026-10-20.** Applies to **FY2026Q3 and every later quarter**. Nothing before
FY2026Q3 is covered by this document and no result from it may cite this hash.

## 0. What the author had already seen, stated first

This prereg is written by someone who has already looked at a great deal of the data,
and a reader must be able to discount it accordingly.

**Seen before freezing:**
- Traeger's quarterly tone, FY2024Q1 through FY2026Q3, including the FY2026Q3 value of
  **−0.944** — the lowest in the series.
- Weber's quarterly tone over the same window, which is flat.
- Traeger's reported revenue **through FY2026Q2** (filed 2026-08-06), which is falling:
  FY2026Q1 of $94.1m is 34% below FY2025Q1.

**Not seen, and the reason this document can exist at all:**
- **Traeger's FY2026Q3 revenue.** The quarter closed 2026-09-30 and files around
  2026-11-06 on a median 40-day lag. It is not public on the freeze date.

So the *signal* side of the first test point is known and the *outcome* side is not.
That is a weaker position than a prereg written before any data, and the honest
description of everything up to FY2026Q2 is **exploratory**: it generated this
hypothesis and cannot test it.

## 1. Hypothesis

> The tone of Traeger's customer reviews in a fiscal quarter carries information about
> that quarter's reported revenue surprise, beyond a seasonal naive expectation, and
> that information is available before the figure is filed.

Falsified if the measured association does not separate from the shuffled-date null
over the held-out window defined in §5.

## 2. The signal

Two specifications, both declared, `specifications_tested = 2`.

```
S1 (primary)  signal(Q)      = tone_within(Q) − mean(tone_within, prior 4 quarters)
S2 (peer)     signal_peer(Q) = signal(Q) − signal_Weber(Q)
```

Tone comes from `aggregate.py` on scores produced under rubric **v1** by
**`claude-haiku-4-5-20251001`** at temperature 0 — the pairing whose calibration gate
passed at quadratic-weighted kappa **0.811**, adjacent agreement **0.900**, against 100
human labels. A change to either requires a new prereg, not an amendment.

Weber contributes **tone only**. It is private and has no revenue; its role is to
separate a Traeger-specific move from a grill-category move.

## 3. The endpoint

```
expected_revenue(Q) = revenue(Q − 4 quarters)        # seasonal random walk
surprise(Q)         = reported_revenue(Q) − expected_revenue(Q)
```

Seasonal random walk, no drift. Chosen because it is the free default already written in
the archived methodology, not because it fits anything observed.

Revenue is the **original vintage** at its EDGAR `filed` date. Restatements do not
replace it.

## 4. The test

For each held-out quarter, record whether **sign(signal) == sign(surprise)**.

- Hit rate is reported **beside the base rate** of the same event — the frequency of a
  negative surprise over the available history. 60% against a 65% base rate is a
  failure and is reported as one.
- A **shuffled-date null** is run alongside every real evaluation: the same signal
  values permuted across quarters, 2,000 replicates. A result that does not separate
  from the null is null.
- Both S1 and S2 are evaluated and **both are reported**, whichever direction they fall.
  A result holding on S2 and not S1 is reported as exactly that, never as "the signal
  works".

## 5. The window, and when a verdict may be spoken

| | |
|---|---|
| Held-out | **FY2026Q3 onward** |
| Quarters before any verdict | **8** |
| First evaluable quarter | FY2026Q3, revenue expected ~2026-11-06 |
| Earliest possible verdict | ~FY2028Q2 |

**No verdict before 8 held-out quarters.** Each quarter is recorded as it arrives, with
its signal, its surprise and whether they agreed; a running tally is not a result and
must not be reported as one. One quarter agreeing is a coin landing heads.

**A hypothesis that fails on this window is dead.** It is not re-specified, not
threshold-adjusted, and not re-run against the same quarters. Advancing after a failure
needs a new hypothesis and an unspent window.

## 6. Known confounds, declared now rather than discovered later

1. **Verified-purchase share is unstable in Traeger's data and stable in Weber's.**
   Traeger runs 0–58% verified; Weber 92–100%. Two of Traeger's most extreme quarters
   already carry the F12 flag — FY2025Q1 at 15% against a 37% prior-four-quarter mean,
   and FY2026Q3 at 0%. **FY2026Q3 is the first held-out quarter and it is flagged.** Its
   tone may reflect a change in who wrote reviews rather than what they thought, and
   that must be reported with the result.
2. **These reviews are hosted and moderated by Traeger.** A firm has most reason to
   remove negative reviews exactly when tone would otherwise fall. The deletion test
   (`data/_route-tests/*snapshot*.json`, due 2026-10-22) sizes this; until it reports,
   the magnitude is unknown.
3. **Coverage is unquantified.** The share of Traeger's revenue these product families
   represent has never been measured. The series cannot be read as a view of the whole
   company.
4. **Weber's text fraction falls over time** — 100% in 2019 to 29–37% recently — so its
   scored population is a shrinking minority of its rated one.
5. **The within/mix decomposition is provisional**, traceable to a second-hand
   reconstruction rather than the source paper.

## 7. What this never produces

No buy, sell, hold, target, position size or allocation, under any result. The outputs
are the effect, its interval, the baseline, the base rate, the null result and the hit
history. `CORE-PROMPT.md` §1.1 is not relaxed by a passing test.
