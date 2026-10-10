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

## 2026-10-11 · review-tone-dashboard

Built the `review-tone-dashboard` skill: a presentation layer that renders one
company's pipeline output as a published Artifact. Takes a ticker and an
optional formation year, reads `quarters.csv`, `fundamentals.csv`,
`manifest.json` and `results.jsonl`, and writes a standalone HTML page.

**The honesty rules are enforced in code, not in prose.** Each is a specific way
a chart can make a null result look like a finding, so each sits in
`build_dashboard.py` (data gating) or in the template's chart geometry, and is
documented in `references/render-contract.md`:

- The per-quarter review counts are drawn *inside* the tone chart's SVG, below
  the axis. Welded into the geometry so a later layout change cannot drop them
  and leave a tone line with no sample sizes.
- A quarter under the 30-review floor is a hollow ring and **a break in the
  line**, never a point on it. Interpolating across a six-review quarter invents
  the quarter.
- A tone mean arriving without a bootstrap interval is not rendered at all.
  Empty CSV cells parse to `None`, never `0.0`.
- A company under the 12-quarter floor gets **no trend chart**. A refusal panel
  replaces it. The tempting version — draw the line and caption it "small
  sample" — loses, because the caption is read once and the shape is read every
  time.
- A hit rate without its base rate is dropped. Below its base rate it is
  labelled `BELOW THE BASE RATE — THIS IS A FAILURE`, never "58% accurate" (F7).
- Verdicts are downgrade-only: a `signal` row for a company under the floors
  still renders `insufficient_data`.
- `signal` is blue, never green. A green verdict pill reads as "go", and "go" is
  a recommendation.
- Scope, limits, the checks list (including the gates that FLAG) and the
  no-advice framing render unconditionally, outside the tab system.

**Decisions worth recording:**
- Grey was the first choice for the seller-only series, since those records are
  excluded from tone. It fails the data-viz chroma floor as a categorical series
  (OKLCH C 0.009) and reads as chart furniture rather than data. Replaced with
  palette slot 2; exclusion is carried by the legend and stacking order.
- The volume chart labels the **scored** product-review count, not the stack
  total. Caught while rendering the pilot fixture: a bar labelled 30 sat across
  the 30-review floor line while only 28 of those were scored reviews, inviting
  a comparison of the wrong number against the floor.
- The floor is re-derived from `n_reviews` rather than trusted from the
  `sufficient` column. Disagreement between them is surfaced as a warning.

**Verification.** Phase B5 has not run, so no real `quarters.csv` exists. The
skill was built against the documented contract and rendered from three labelled
synthetic fixtures (`make_fixture.py`), which carry a permanent banner and are
marked SYNTHETIC in every field that could be mistaken for provenance:

| scenario | shape | result |
|---|---|---|
| `below-floors` | 8 quarters, 1–28 reviews — the Logitech M510 pilot's shape | refused to chart; `insufficient_data` |
| `mixed` | 16 quarters, three under the review floor | line drawn broken at all three |
| `above-floors` | 16 clean quarters, hit rate 58% vs base rate 65% | read as a failure |

**It has not been validated against real pipeline output.** The column reads,
null handling and floor arithmetic follow `data-contracts.md`, but no stage of
this skill has seen a figure that came from the corpus or from EDGAR.

Palette validated with the dataviz skill's `validate_palette.py` (no `node` on
this machine) against the page's own surfaces, passing in both modes: worst
adjacent CVD ΔE 24.7 light / 26.8 dark against a target of 8.
