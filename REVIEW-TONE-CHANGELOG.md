# Changelog

## 2026-10-11 · scaffold

Built the `review-tone-signal` skill: locked core prompt, five reference documents,
and a verified Phase A pipeline.

**Verified working against live sources:**
- `screen_universe.py` — SIC enumeration via EDGAR atom feeds, join to listed
  tickers, point-in-time market cap. Cross-checked: AAPL $587B, Hasbro $8.39B,
  Helen of Troy $2.65B, UEIC $0.74B at 2016-01-01, all matching history.
- `corpus.py` — streaming access to the 750 GB review corpus, nothing stored.
  Confirmed schema and brand matching on an Appliances sample.
- `coverage.py` — brand→ASIN matching, ambiguity handling, gate reporting.
- `fetch_sec.py` — point-in-time quarterly revenue with original vintages.
  Verified on HELE (FY ends Feb) and HBB (FY ends Dec).

**Defects found and fixed during the build:**
- Stooq is behind a JavaScript bot-check as of 2026-10; replaced with the Yahoo
  chart API.
- Yahoo returns split-adjusted closes; multiplying them by historical share
  counts understated market cap by the cumulative split factor (Apple by 4x).
  Now corrected by a separate split-event query.
- Fiscal quarter labels were taken from the XBRL `fp` field, producing
  `FY2011QFY` for annual filings. Now derived from EDGAR's `fiscalYearEnd`.
- Fiscal year end inferred from the mode of 10-K period ends put Helen of Troy
  in August; it ends in February. A one-month error shifts every join by a
  quarter (F11). Now read from the authoritative field.
- Early quarters whose first XBRL appearance is 400+ days after period end are
  prior-year comparatives, not late reports. Marked `comparative_only` and
  excluded rather than being treated as genuine reporting lags.

**Not built, deliberately:** `extract_reviews.py`, `score_reviews.py`,
`aggregate.py`, `backtest.py`, `degrade.py`. These depend on choices the prereg
has to fix first — test statistic, thresholds, weighting. Writing them now would
mean choosing those after having seen the data, which is the thing the whole
design exists to prevent.

## 2026-10-11 · B5 aggregation

Built `scripts/aggregate.py` — quarterly tone aggregation with the within-ASIN /
product-mix decomposition. Stops at aggregation.

**What it does:** reads `data/{TICKER}/reviews.csv` + `scores.csv`, writes
`data/{TICKER}/quarters.csv` (16 columns, exactly the contract's schema and
order — verified by parsing the column list out of `data-contracts.md` and
diffing it against the emitted header), plus `data/dropped.csv` and an
`aggregate` block merged into each `manifest.json`.

**The decomposition.** Total tone change is split per methodology §3 over ASINs
present in both Q and Q−1: `within = Σ w_i(Q−1)·Δtone_i`, `mix = Σ Δw_i·tone_i(Q−1)`,
residual = entrants + exits + the within×mix interaction. Only `tone_within`
feeds the signal. The residual is built from its own three terms and the
identity `within + mix + residual == Δtotal` is then asserted, so the assertion
tests the arithmetic rather than restating a definition — a residual computed as
the leftover would reconcile by construction and check nothing.

**Floors are hard stops.** A quarter below 30 included reviews emits
`sufficient=false` with `tone_mean`, both CI bounds and all three decomposition
columns null. It emits no number. A company below 12 usable quarters is dropped
and recorded in `data/dropped.csv` with the reason, never replaced. Both
thresholds are module constants, defaulted to 30 and 12; overriding them on the
command line prints that the output is not a result.

**Every mean carries an interval.** Percentile bootstrap, 2000 replicates,
seed 20261011, resampling reviews within the quarter. Each quarter draws from a
substream keyed by SHA-256 of (seed, ticker, quarter), so a CI does not depend on
how many quarters preceded it or in what order they were processed. The one path
that could produce a mean without an interval nulls the mean instead.

**Blocking checks wired in, reported and never smoothed:** F4 (residual share of
gross movement, naming whether entrant churn or the interaction term dominates),
F12 (verified-purchase share against the prior-four-quarter mean), F13 (share of
a quarter lost to scoring failure). Flags also land in the manifest.

**Interpretation resolved and logged** in `methodology.md` §8, six rows. The
load-bearing one: the three decomposition columns are the **Δ components** of §3,
not levels, because that is the only reading under which they reconcile to the
total change as the contract requires — so `baseline_prior4` is the mean of the
prior four within-ASIN *changes* and `signal = tone_within(Q) − baseline_prior4(Q)`,
the prereg's formula, is well defined. Also logged as an **open gap**: the
contract has CI columns for `tone_mean` only, so the signal-level interval
methodology §4 asks for is deferred to the backtest stage and must be specified
in the prereg before `backtest.py` exists.

**Not verified against real data, because there is none.** `data/` holds the two
point-in-time universes and the brand alias table and nothing else — no
`eligibility.csv`, no per-ticker directories, no `reviews.csv`, no `scores.csv`,
and no frozen prereg. B2 (extract), B3 (calibrate) and B4 (score) have not run,
so there are no per-quarter counts to report and no statement to make about how
many quarters clear the floor. Running the script in that state exits non-zero
and says so rather than producing anything.

What was verified: 21 self-tests via `--self-test` (code correctness only, writes
nothing, produces no research result) covering the reconciliation identity, a
pure mix shift putting zero in the within component and vice versa, the floor
nulling tone on quarters of 1, 4 and 6 reviews — the pilot's actual counts — gaps
not being bridged, baseline/signal timing, bootstrap reproducibility and the
degenerate single-value quarter. Separately, the full read/write path was
exercised once end to end on a synthetic fixture in the scratchpad, which
confirmed the schema match and that F4/F12 fire; that fixture was never written
into `data/` and its numbers are not a finding.

**Defect found and fixed during the build:** F4's residual flag initially divided
by the total tone change, which goes to zero through cancellation — a quarter
with a +0.00 total change was reported as a 958% residual alarm. Now measured
against `|within|+|mix|+|residual|` and the message names which term dominates.

**Still not built, deliberately:** `backtest.py` and `degrade.py`. They encode
the test statistic, thresholds and weighting, which are prereg choices. Writing
them now would mean choosing those after having seen aggregation output, which is
the thing the design exists to prevent. Next is B6 `fetch_sec.py`, then freeze
the prereg.

### 2026-10-11 · B5 addendum — decomposition marked provisional

A peer session flagged that `methodology.md` §3 is not derived from the source
paper. Verified against the repo before acting: §3's own header calls the spec "a
faithful reconstruction of the method as described second-hand", §4 says
"following the reel's construction", `failure-modes.md` names the 61-second
video, and §8 carries an open row to reconcile against the paper. The flag is
correct, and the first B5 entry above under-stated it — it logged *interpretation*
decisions while presenting §3's formula itself as settled.

Taken: isolate behind a marked seam, **not** pause the decomposition. Pausing
would contradict the brief, which requires the split to ship; the seam satisfies
both. No emitted number changed.

- `DECOMPOSITION_SPEC = "methodology-s3-reconstruction-v0-UNVERIFIED"` and
  `BOOTSTRAP_IMPL`, both written into every manifest. Re-running after the paper
  is reconciled is then distinguishable from a result rather than silently
  overwriting one — the F10 scorer-drift discipline applied to the arithmetic.
- `decompose()` documented as the single swap point: replace its body, bump the
  tag, and every caller, the reconciliation assertion, the F4 flag and the CSV
  writer are unaffected. A second copy of the arithmetic must never appear.
- Module docstring separates what is provisional (the formula) from what is not
  (that the split must happen, that only `tone_within` feeds the signal, that the
  three components reconcile) — those rest on CORE-PROMPT §5/§7 and the contract.
- Run header and per-company footer print the provisional warning.

**Numeric stack: assumed stdlib-only**, per the brief and the repo today. No venv
set up. `bootstrap_mean_ci()` is the only numerically intensive code and the only
consumer of the RNG, so a numpy/scipy version replaces that one body. Recorded
there and in the manifest: the seed alone does not pin output — the RNG *stream*
is part of reproducibility, so a numpy run is a new vintage, not a re-run.

Still unbuilt, deliberately: `backtest.py`, `degrade.py`.
