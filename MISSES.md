# MISSES — review tone monitor

Append-only (CORE-PROMPT §1.5). A method's miss log is the only honest evidence about it.

---

## 2026-11-09 — insulated-drinkware weekly pass not collected (budget gate)

**What was supposed to happen:** the weekly collection pass over the three
`insulated-drinkware` members (`yeti-holdings-inc` / B07FM5HLMF,
`pacific-market-international-llc` / B0CJZW65R3, `trove-brands-llc` / B085DTZQNZ),
50 reviews each at sort=recent via Apify.

**What happened:** nothing was collected. The pre-collection budget gate stopped the run.

Figures read from `GET /v2/users/me/limits` and a script over it, not computed by hand:

- `monthlyUsageUsd` used: 4.8283
- `maxMonthlyUsageUsd` cap: 6.0000
- remaining in cycle: 1.1717
- gate threshold: 1.20 → **STOP**
- cycle ends: 2026-11-12T23:59:59.999Z (next cycle begins 2026-11-13)

The run would have cost roughly $0.90 against $1.1717 remaining. That fits in the
nominal balance but not in the gate's margin, and the gate is the margin — it exists so
that a run cannot silently exhaust the cycle mid-pass and leave a partial batch.

**Second, larger miss recorded at the same time:** the declared cadence has already
lapsed, and by more than this one pass. Days since last successful batch, computed from
each manifest's `last_collected_at` against 2026-11-09:

| company | last collected | days since | declared cadence |
|---|---|---|---|
| yeti-holdings-inc | 2026-10-14T07:18:27Z | 25 | 7 |
| pacific-market-international-llc | 2026-10-14T20:51:51Z | 25 | 7 |
| trove-brands-llc | 2026-10-15T06:26:52Z | 24 | 7 |

So this is not a one-week gap. Roughly three weekly passes were already missed before
this run reached the gate. Under §4 the cadence is declared and kept, and a cadence that
is not kept changes the instrument: these series are no longer weekly-sampled, whatever
the declaration says. That is a defect in the series, not a scheduling inconvenience,
and it is recorded here rather than smoothed over.

**Not done, deliberately:** no collection, no actor call, no retry at a lower limit, no
switch of provider, and the workflow was not run (there were no new reviews for it to
score). No peer group was revised. The group remains frozen as declared 2026-10-12.

**Recency verification:** no `recency_verified` field is recorded anywhere in this
group's batch or manifest data. The manifests record `any_sort_verified: true` with
`sorts_requested: ["recent"]`, which is a weaker and different condition. Stage B5 passing
is therefore not evidence that these are date-ordered series.
