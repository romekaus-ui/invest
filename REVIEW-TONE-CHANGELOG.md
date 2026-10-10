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

## 2026-10-11 · Phase A2 scorer

Built the A2 structural-eligibility stage: a subagent that scores every in-band
company in a point-in-time universe, and the script that does its arithmetic.

**Delivered:**
- `.claude/agents/review-eligibility-scorer.md` — the A2 subagent. Reads the core
  prompt, `company-selection.md`, `data-contracts.md` and `failure-modes.md`;
  scores the seven criteria 0–5 from the filing in force at formation; writes
  judgements with per-criterion evidence and accession; never computes a total,
  never consults review coverage, never excludes on performance. Tools are
  Read/Write/Edit/Glob/Grep/Bash only — no WebSearch, no WebFetch, no browser,
  so present-day information about a company is out of reach by construction.
- `.claude/skills/review-tone-signal/scripts/score_eligibility.py` — stdlib only,
  reuses `fetch()`/`ua()` from `screen_universe.py`. Five modes: `queue`,
  `filing`, `probe`, `build`, `verify`. It owns the three things the model must
  not do — the weighting arithmetic (core prompt §1.3), the point-in-time
  boundary, and the completeness check.

**Design decisions worth recording:**
- *Evidence by frozen probe, not by free reading.* A 10-K flattens to ~400k
  characters; reading one whole costs ~100k tokens and nobody would do it 40
  times. `probe` runs a fixed regex set per criterion and prints bounded ±180
  character windows, deduped across overlapping matches. The same questions in
  the same words for every company, so the evidence gathered does not vary with
  how interesting a company looks. Adding a probe is a change to the instrument.
- *Judgements are append-only JSONL, built in a separate step.* `build` validates
  then computes. A score with no evidence string, a missing `estimated` flag on
  c1, a score outside 0–5, a filing dated after formation, a criterion citing
  later evidence, a ticker not in the universe, or outcome language in a `reason`
  all stop the build and write nothing. Each was tested and each fires.
- *The universe is scored whole or not at all.* `build` refuses while any in-band
  company is unscored. `--partial` exists for a verification sample and marks the
  hash file `partial=True`; a partial file does not meet the A2 gate.
- *Prospectus fallback.* A company that listed shortly before formation has no
  10-K. Dropping those names removes exactly the recently-listed Amazon-native
  brands the rolling formation exists to capture, so `filing` falls back to the
  last registration statement filed on or before formation (S-1/424B/F-1/10-12B)
  and labels it. Deviation from "the 10-K in force at formation"; the constraint
  that matters is unchanged, since the prospectus was public at formation. Lovesac
  at the 2019 formation resolves to its 424B1 of 2018-10-30, which quantifies
  ecommerce at 16.8% of sales — better c1 evidence than most 10-Ks carry.

**Verified on three companies at the 2016 formation** (`--partial`, not a universe):

| Ticker | Filing in force | Score | Verdict |
|---|---|---|---|
| LOGI | 10-K filed 2015-06-05 (FY to 2015-03-31) | 69.0 | pass |
| HELE | 10-K filed 2015-04-29 (FY to 2015-02-28) | 49.0 | fail |
| NTAP | 10-K filed 2015-06-12 (FY to 2015-04-24) | 31.0 | fail |

Cost: one probe packet is 13–15k characters (~3.5–4k tokens); a scored company
runs 6–8k tokens all in, two or three EDGAR requests, about a second each. A
formation year is one session's work.

**Defects and discrepancies found:**
- *The scorecard's worked verdicts do not survive point-in-time scoring.*
  `company-selection.md` §3 calls Helen of Troy a likely pass and Logitech
  marginal. Scored from the filings in force at 2016-01-01 the order reverses:
  HELE 49, LOGI 69. HELE's FY2015 10-K names Amazon only as a competitive threat
  to its own brick-and-mortar customers, lists e-commerce as one of ten channels
  with no quantified share, and reports four segments including a direct-mail
  supplements business. Logitech's FY2015 10-K names Amazon.com as a sales
  channel and discloses dollar revenue per product category *and* per channel.
  §3 was written from present-day knowledge of these companies; it demonstrates
  the scorecard, and it is not a target to reproduce. Recorded, not fixed —
  §3 says as much itself.
- *In-band counts.* The universe files hold 40 in-band companies at the 2016
  formation and 51 at 2019, not the 29/41 assumed when this stage was specified.
  Both commits of `data/universe_*.csv` agree, so the figures are the screen's,
  not a regression.
- *Cap-floor drift between documents.* `company-selection.md` §1 specifies a
  USD 50M–10B band and `screen_rolling.py` defaults to 50M, but
  `screen_universe.py` defaults to 200M and `data-contracts.md` documents
  `in_band` as `200M <= cap <= 10B`. The committed 2016/2019 universes came from
  the 200M path. Not touched here — changing the floor changes the universe, and
  A2 may not reach upstream of a frozen screen. Flagged for the user.

**Not run:** the full universe. Three companies were scored to verify the
instrument. `data/eligibility_2016.csv` is marked `partial=True` and 37 of 40
companies at the 2016 formation remain unscored, so the A2 gate (>= 8 eligible)
is not met and A3 coverage must not begin.
