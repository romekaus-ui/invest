# Preregistration — <RUN NAME>

Copy to `prereg/<YYYY-MM-DD>-<name>.md`, fill every field, then freeze:

```bash
shasum -a 256 prereg/<file>.md | tee prereg/<file>.hash
git add prereg/ && git commit -m "freeze prereg <name>"
```

**Nothing in the held-out window may be inspected before this file is committed.**
The hash goes into every result row. A result whose prereg hash does not match a
committed file is not a result.

---

## 1. Hypothesis

> State it as a falsifiable claim about a named quantity, in one sentence.
> Example: "Within-ASIN abnormal review tone in fiscal quarter Q is positively
> associated with revenue surprise in Q+1, among companies passing the structural
> eligibility screen."

## 2. Universe

- Universe file: `data/universe.csv`
- Universe SHA-256: `<paste>`
- Formation date: `2016-01-01`
- Eligibility file and SHA-256: `<paste>`
- Companies entering the test (count): `<N>`
- Companies dropped for coverage (count and list): `<...>`

## 3. Window

| | |
|---|---|
| Full window | `2016Q1` – `2023Q2` (corpus ends Sep 2023) |
| Development | `2016Q1` – `2021Q2` |
| **Held-out** | `2021Q3` – `2023Q2` (8 quarters) |

Held-out quarters are not inspected, plotted, described or reasoned about until
development is closed and this file is committed.

## 4. Signal definition

- Tone rubric version: `v1`
- Scorer model ID: `<exact id>`
- Calibration kappa achieved: `<value>` (gate: ≥ 0.60)
- Signal: `tone_within(Q) − mean(tone_within(Q−1..Q−4))`
- Minimum reviews per quarter: `30`
- Minimum usable quarters per company: `12`

## 5. Endpoints

- **Primary:** revenue surprise at Q+1 versus `<seasonal random walk | consensus>`
- **Secondary (non-binding):** stock return over `<window>`, labelled noisy

## 6. Test statistic and decision rule

- Statistic: `<e.g. Spearman rank correlation between signal(Q) and surprise(Q+1), pooled, company fixed effects>`
- Direction: `<one-sided / two-sided>`
- **Pass threshold, stated now:** `<e.g. held-out Spearman ρ ≥ 0.15 with 95% CI excluding 0, AND hit rate exceeding base rate by ≥ 5pp>`
- Base rate to beat: `<computed on development data, stated here before held-out is touched>`

## 7. Specifications

List **every** specification that will be run. The count goes into the result row.
Reporting only the surviving specification is failure mode F6.

| # | Horizon | Threshold | Weighting | Notes |
|---|---|---|---|---|
| 1 | Q+1 | — | review-count | primary |
| 2 | | | | |

## 8. Null run

Shuffled-date null: review timestamps permuted within company, pipeline otherwise
identical, `<N>` permutations. The real result must separate from the null
distribution at `<level>`, or the verdict is `no_signal`.

## 9. What would falsify this

> Write down, now, the result that would make you abandon the hypothesis.
> If no such result exists, this is not an experiment.

## 10. Stop conditions

- Calibration kappa below 0.60 → stop, fix the scorer.
- Fewer than 8 companies with usable coverage → stop, record the gap.
- Held-out fails the threshold in §6 → record in `MISSES.md`, verdict `no_signal`,
  and do **not** re-specify and re-run against the same held-out data.
