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

## 2026-10-11 · orchestrated run 20261011T114148Z-8743ba

Phase A, verdict `insufficient_data`. stage A2 (Score structural eligibility from filings) is a human step, not a script. Missing: data/eligibility.csv. A scheduled run reports it and stops; it does not invent the output.

- Gates met: A1.universe
- Gates unmet: A2.eligibility, A3.coverage, B1.prereg_frozen, B3.calibration, B7.heldout, C.degradation
- Stages run: none
- Changed since last run: first recorded run: nothing to compare against
- Held-out: not read (prereg unfrozen, development open)
- Report: `results/reports/20261011T114148Z-8743ba.md`

## 2026-10-11 · orchestration layer

Built the layer that runs the pipeline on a schedule instead of as a retyped
prompt: `/review-tone-run`, three scripts behind it, and a weekly cloud routine.

**What was built:**
- `scripts/pipeline_state.py` — reads phase from disk and git, evaluates the
  seven entry gates, and runs three integrity checks on every pass. Read-only:
  nothing that evaluates a frozen artefact can also rewrite it.
- `scripts/results_row.py` — the only writer of `results/results.jsonl` and
  `MISSES.md`. Validates the §9 contract before opening the file, then verifies
  the prefix is unchanged after appending.
- `scripts/orchestrate.py` — walks the stages, stops at the first unsatisfied
  one, appends a row, writes `results/reports/<run_id>.md`, appends here.
- `.claude/commands/review-tone-run.md` — the command, with the interface every
  unbuilt stage must satisfy.
- `references/weekly-routine.md` — the schedule's config and verbatim prompt.

**The rules that are now enforced by code rather than by attention:**
- A gate's predecessor must pass before its phase starts. Gates are re-evaluated
  after every stage, because a gate read once is stale the moment a stage changes
  what it measures.
- Until the prereg is frozen, every stage is invoked with `--window dev`. If one
  writes a held-out quarter anyway, the next state read aborts the run with exit
  2 and nothing is appended.
- A prereg hash that has failed on held-out data is dead. The backtest will not
  run under it again, on any later run, ever. This is the rule a weekly loop
  would otherwise break by quietly trying again, so it is a refusal and not a
  warning.
- The universe is never re-screened and eligibility never re-scored on a
  schedule. A missing or altered universe is reported, not rebuilt.
- A row missing any provenance field, or claiming a verdict with a null estimate,
  interval, baseline, base rate or null run, is rejected and not written.

**Reconciliation, recorded rather than resolved silently:** SKILL.md orders B1
(freeze) before B3 (calibrate), but `prereg/TEMPLATE.md` §4 requires the frozen
document to record the achieved kappa and §6 a base rate computed on development
data. Extraction, calibration, scoring and aggregation therefore run pre-freeze
under `--window dev` and are labelled phase `B-prep`; only `fetch_sec.py` and the
backtest sit behind `B1.prereg_frozen`, and they are the only stages that can
produce a verdict.

**Verified:** 88 checks against throwaway repo copies covering every gate and
every refusal — truncated scans, a company scored outside the universe, kappa
0.48, adjacent agreement 0.70, a prereg with surviving placeholders, an
uncommitted prereg, a hash mismatch, a prereg/calibration kappa disagreement,
scorer drift, contamination from a stage ignoring `--window dev`, a truncated
`results.jsonl`, a rewritten `MISSES.md`, a hand-picked ticker, and a held-out
failure followed by a second run that refuses to retry it. One real run against
this repo: phase A, `insufficient_data`, stopped at `A2.eligibility`.

**Wired but not executed:** `extract_reviews.py`, `score_reviews.py`,
`aggregate.py`, `backtest.py`, `degrade.py` and the dashboard do not exist yet,
so B2–B7, C and the dashboard step were exercised against stubs matching the
documented interface, not the real scripts. `fetch_sec.py` is invoked on its real
positional `TICKER CIK` interface but was tested with a stub, since the stages
ahead of it are unbuilt. A1 and A3 exist and were not re-run: A1 is frozen, and
A3 needs an `eligibility.csv` that is a human's to write.

**Not done:** the weekly routine could not be created. The routine API validates
repository access at creation time and returned `403 repo_access_denied` —
`romek-aus/invest` does not exist yet and this repo has no remote. Its exact
configuration is committed in `references/weekly-routine.md`; creating it is one
call once the repo is pushed.

## 2026-10-11 · weekly routine created

Routine `trig_01MExfvhuF2g6841QWZ8htBR`, Mondays 06:00 UTC against
`github.com/romekaus-ui/invest`. Config and verbatim prompt in
`references/weekly-routine.md`.

Connectors cleared after creation: the API attaches the account's connectors by
default, which gave a job that reads SEC filings access to Gmail, Drive and
Claude Docs. It now holds none.

Not yet verified by a real fire — the repo had no commits pushed when the routine
was created, and this machine has no git credentials, so the first run is the
first test. If it fails, `list_runs` then `get_run_log` on the routine will say
why.
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

## 2026-10-11 · A2 rescoped to a shortlist of company-years

Narrowed the A2 component after a correction from the session building the
mechanical pre-filter. Two facts behind it, both verified here:

- The committed `data/universe_2016.csv` / `universe_2019.csv` are output of the
  **superseded** 27-code screen in `screen_universe.py` — their hash files carry
  that script's format, and commit 8ca42b2's own message describes the 40-company
  band they produced as the problem it was fixing. `sic_consumer.py` now defines
  126 codes and `screen_rolling.py` re-forms annually to
  `data/universe_panel.csv`, **which has not been run**. The 40/51 in-band counts
  in the previous entry are accurate for the committed files and stale as a
  description of the universe; expected scale after the new screen runs is
  800–1,000 company-years.
- A mechanical eligibility pre-filter, cutting that panel to roughly 60–80 using
  only point-in-time filing data, is a separate component built elsewhere. Not
  built here, not duplicated here.

**Changes:**
- `load_universe` reads `data/universe_panel.csv` when present and slices it by
  formation year, falling back to the per-year files. The unit is now explicitly
  a company-year: a company in four formation years is scored four times, each
  against that year's filing, each in that year's output file.
- `--shortlist` (queue, build) takes the pre-filter's ticker list — one per line
  or a CSV with a `ticker` column — and makes it the scope. Completeness is
  measured against the shortlist, not the panel. A shortlist naming a ticker that
  is not an in-band company-year at that formation is refused, as is a judgement
  for a ticker outside the shortlist: scoring beyond the handed-down set is
  selection, which is not A2's to do.
- Provenance is recorded rather than assumed. The hash file and evidence JSON now
  carry the universe file and its sha256, the shortlist and its sha256, the scope
  size, and `not_in_scope` — every in-band company-year the pre-filter cut.

**Methodological caveat, raised and not resolved here.** `company-selection.md` §2
requires the ineligible list to be part of the result. With a shortlist, the
company-years the pre-filter cut carry no scores in `eligibility_<YEAR>.csv`, so
that file no longer bounds the population on its own — and the pre-filter cuts on
segment count and e-commerce disclosure, which are the same evidence as criteria
3 and 1, so the two stages are correlated rather than independent. The requirement
is satisfied only if the pre-filter publishes its own cut list with per-company
reasons. `build` now prints the upstream cut count and records the tickers, so the
gap is visible in the output instead of silent.

Guards tested after the change: stray shortlist ticker refused, off-shortlist
judgement refused, panel precedence over per-year files confirmed, and the
2016 verification CSV byte-identical (sha256 e15547c1…) — the scope change is
recorded in the hash file and evidence JSON, never in the contract CSV.

**Still not run:** the new 126-code rolling screen, the pre-filter, and therefore
A2 at real scale. The 2016 verification file covers 3 company-years from the stale
universe and does not meet the A2 gate.

## 2026-10-11 · A1b mechanical pre-filter

The component the root planning session described as "a cheap mechanical
pre-filter on the panel first (segment count, revenue concentration, SIC group)"
and never built. It was the binding constraint on the whole project: A1 produces
3,522 in-band company-years, A2 costs 6–8k tokens and 2–3 EDGAR requests of a
human's attention each, three had been scored, and `eligibility_2016.csv` was
marked `partial=True`. A scorecard that is never applied bounds nothing.

`scripts/prefilter_panel.py` reads `data/universe_panel.csv` and writes, per
formation year, the shortlist A2 scores with `--shortlist`, plus a complete
record of every company-year it cut and why. Documented in
`references/company-selection.md` §1b, which sits between the universe rule and
the scorecard because that is where the stage sits. Probe version
`prefilter-v3`.

### Counts in and out

Panel `universe_panel.csv`, sha256 `28db9bdb5609`. Every number below is from
`data/prefilter.manifest.json`, not from prose.

| formation | in band | kept | cut | shortlist sha256 |
|---|---|---|---|---|
| 2016-01-01 | 396 | **83** | 313 | `866550a5fe02` |
| 2017-01-01 | 412 | **96** | 316 | `55280c6cafc8` |
| 2018-01-01 | 421 | **107** | 314 | `ace494661a42` |
| 2019-01-01 | 500 | **127** | 373 | `5efed8a9d43b` |
| 2020-01-01 | 526 | **137** | 389 | `f6a49f84422a` |
| 2021-01-01 | 582 | **144** | 438 | `aa6f84fd50ea` |
| 2022-01-01 | 685 | **171** | 514 | `8b2f3b9abd03` |
| **all** | **3522** | **865** (24.6%) | **2657** | panel record `e91ffb20719a` |

865 company-years over **235 distinct companies**, of 779 in the panel. Every
cut carries the same reason, `no_consumer_channel_disclosed`: 2,457 at zero
distinct channel mentions and 200 at one. No company-year lacked a filing in
force at its formation, and no filing failed to parse — `no_filing_at_formation`
and `filing_unreadable` are both zero.

**Is that a scoreable amount of A2 work? No, not whole.** At 6–8k tokens per
company-year, 865 is roughly 6M tokens. The shortlist is therefore written in a
seeded random order (`numeric-stack.md` §3 convention, seed 20261011, stage
offset 4 claimed here, Fisher–Yates on `randrange`), and A2 works it front to
back: a prefix of a seeded permutation is a random sample of the keep set, so
whatever A2 gets through estimates the keep set's pass rate instead of being the
start of the alphabet. **Take a prefix from each formation year, not all of one
year** — scoring 2016 to exhaustion gives a panel whose companies all entered in
2016, which throws away the reason §1 re-forms annually.

The thresholds were not tuned to make 865 smaller. Where the number is
sensitive, for the record: moving the floor to 1 mention would add 200
company-years; moving it to 3 would remove 100, **including Logitech, the only
company-year A2 has passed.**

### What it reads, and what it cannot

Structural facts from the filing in force at the formation date: whether the
registrant discloses selling through Amazon, e-commerce or a direct channel
(the cut), and how many reportable segments it says it has (recorded, decides
nothing). The filing is resolved by `score_eligibility.choose_in_force()` —
refactored out of `in_force_filing()` so that A2's point-in-time boundary is
*called* rather than copied, leaving one definition of "in force" and one EDGAR
client in the project. Nothing about how a company turned out is read, computed
or available in the stage: no later revenue, no price, no survival, no
present-day anything. CORE-PROMPT §1.2.

Cuts are **absence-only**: the filter never decides a company is good, only that
the structural evidence is missing from the filing in force. A company-year it
keeps is one A2 may still fail, which is the division of labour working — Helen
of Troy is kept with eighteen channel mentions and then fails A2 at 49.0.

### Inputs deliberately not used

- **SIC group.** §1 keeps communications equipment and computers at the SIC
  stage on purpose, so the enterprise names leave "with a documented reason per
  company rather than a silent omission at the SIC stage". A SIC-group cut here
  reinstates that omission one stage later — and could not work anyway, since
  Logitech, NetApp and Fortinet share SIC 357x. The group is recorded on every
  row and used by nothing. **This has a visible cost and it is not hidden:**
  retail (SIC 52xx–59xx) is 326 of the 865 kept company-years, 38% of the
  shortlist, and most will fail criterion 1 because a retailer's shelf is not
  its own product reviewed on Amazon. Cutting them mechanically would also have
  cut Williams-Sonoma, RH and Build-A-Bear, which are own-brand sellers. A2 is
  what separates them.
- **Quantification of the channel share.** Measured and recorded — 407 of 865
  keeps disclose a quantified share — because it is what criterion 1 asks for.
  Not a cut: Logitech's Amazon share is undisclosed.
- **A revenue floor.** Considered, to cut the clinical-stage registrants that
  dominate SIC 2834. Rejected: the channel test already cuts them on structure
  (personal_care, which holds 2833/2834, goes from 1,309 company-years to 87),
  and a revenue level threshold is the input closest to the outcome the study
  measures.
- **A disclosed SKU count.** Structural and point-in-time, but almost no filing
  states a number, so it would fire only on companies that disclose one —
  penalising disclosure rather than applying one rule to everyone.

### Defects found, and what was done about each

Every one came from reading real filings in the seeded audit of the filter's own
decisions. None was fixed by moving a threshold.

1. **The segment-count cut was removed outright.** v1 cut the 57 company-years
   disclosing five or more reportable segments. The audit showed the test does
   not measure what it claims: criterion 3 asks whether *unrelated* segments
   dominate, and a count cannot tell unrelated businesses from one business
   reported by brand, channel or geography. It had cut **Deckers** (six
   segments, 106 e-commerce mentions — UGG, Teva and Sanuk by brand) and
   **Carter's** (five, children's apparel by brand and geography), and GameStop
   crossed the bar between 2017 and 2021 on a reporting change rather than a
   structural one. Removing it **enlarged** the shortlist by 57. The count is
   still recorded, so A2 judges relatedness from the filing.
2. **The segment count was reading note numbers and page numbers.** v1 took the
   max of the statements it found. BorgWarner's "NOTE 19 REPORTING SEGMENTS AND
   RELATED INFORMATION" was read as 19 in a filing that says "two reporting
   segments" seven times; Atkore's "...between 2018 and 2022. 29 Reportable
   Segments We operate our business through two operating segments" was read as
   29, where the 29 is a page number and the answer is two words later. Fourteen
   company-years carried a count of 17–29. Now the **mode** of the plausible
   statements, ties to the larger; values above 12 recorded but never chosen; a
   number directly preceded by NOTE or ITEM is not a statement. Zero rows now
   carry a chosen count above 16, and seven are marked `implausible_only`. Had
   the v1 cut survived, it would have cut those fourteen on a page number.
3. **One clause was counting as two mentions.** Natural Alternatives' FY2019
   10-K says its customers market "through direct sales marketing channels,
   direct to consumer ecommerce channels and retail stores" — one clause,
   matched by two probe families, summing to two and clearing a floor of two on
   a single phrase. A2's own `probe_text` already treats a paragraph matched by
   three probes as one piece of evidence; the filter now does the same within
   120 characters. Raw per-family counts stay on the row beside the
   deduplicated total.
4. **The direct-to-consumer-advertising exclusion required adjacency.** Acadia's
   filing says "direct-to-consumer **television** advertisements" and Vanda's
   "direct-to-consumer **campaign**"; both reached the shortlist. The exclusion
   now tolerates up to 40 characters between the phrase and advertising,
   promotion, marketing, campaign, television, media or print.
5. **`our direct channel` is a sales force, not a consumer channel.** Aviat
   Networks was kept at exactly two mentions on it. Probe dropped.
6. **`our website` and bare `DTC` were dropped before the first run**, after
   reading what they matched: every 10-K says the code of conduct is available
   on a website (it kept NetApp, an enterprise storage vendor, on a *channel*
   test), and `DTC` in a 10-K is usually the Depository Trust Company.
7. **My own cache could have mixed two instruments.** The measurement cache was
   keyed by (cik, accession) with no probe version, so changing a regex left
   every cached measurement silently stale. The key now carries the probe
   version, and a measurement taken under different probes refuses the build.
   `measure` also reads the flattened text that `--keep-text` saved, so a probe
   change costs CPU rather than a second pass over EDGAR for 3,506 filings.

**Defects found and not fixed**, because no regex fixes them:

- **It cannot tell whose channel it is.** Natural Alternatives is a
  private-label contract manufacturer whose 10-K describes the channels *its
  customers* sell through. Two distinct mentions, so it is kept, and criterion 4
  is what fails it.
- **It reads the filing, not the world.** Bassett Furniture's FY2014 10-K
  mentions the internet as an advertising medium and as a competitor's channel,
  and its own website only in the "available free of charge" boilerplate. It is
  cut, whether or not the company was selling online. A company that sells
  online without disclosing it leaves the universe for a year and re-enters the
  year it discloses — the rolling formation behaving as §1 intends, but the
  filter measures *disclosure* and nothing more.
- **ClearOne @2021 is the closest call on record**, cut at exactly one distinct
  mention (one naming of Amazon).

**Stopping rule, stated because it matters:** the v3 audit of 6 cuts and 6 keeps
found no further defect, and that is where the iteration stopped. Auditing after
every fix until nothing turns up would eventually be fitting the probes to the
sample.

### The caveat on record — addressed, not closed

`company-selection.md` §2 requires the ineligible list to be part of the result.
The pre-filter cuts on channel disclosure, which is the same evidence as
criterion 1, so **the cut is correlated with the scorecard and is not
independent of it.** That cannot be engineered away by being careful. What can
be done is to make the cut set complete and inspectable, which is what was done:

- `data/prefilter_panel.csv` carries **all 3,522** in-band company-years, kept
  or cut, with the facts read, the filing they were read from, and a structured
  reason per cut.
- `data/prefilter.evidence.json` carries the matched windows per company-year,
  so a cut is auditable without refetching.
- `prefilter_panel.csv` is therefore what bounds the population — not
  `eligibility_<YEAR>.csv`. `score_eligibility.py build` already prints the
  upstream cut count so the two are never read as if the second were the whole
  story.

What is **not** closed: a cut company-year carries a structural reason and its
evidence but **no scorecard score**. The ineligible list is complete in the
first sense and not the second, and no run may describe the eligibility file as
a complete account of what was considered.

### Verified

- **Reproducible.** Two consecutive builds over the same panel and cache produce
  byte-identical shortlists and the same sha256. Artefacts carry no timestamps,
  which is what makes that true.
- **Contract.** `prefilter_panel.py verify` re-derives every decision and checks
  the written artefacts: 3,522 rows against 3,522 in-band company-years, every
  cut carrying a reason, every shortlist matching its recorded hash and its kept
  count. Clean.
- **The handoff is real, not assumed.** `score_eligibility.py queue --year 2016
  --shortlist data/shortlist_2016.csv` reports "83 in scope, 2 scored, 81 to
  go"; handing the 2022 shortlist to the 2016 formation is refused by name, 66
  strays listed.
- **72 offline tests** (`tests/test_prefilter.py`), no network: the segment
  parse including both heading artefacts, every fixed exclusion, the dedupe, a
  seeded order that is the same order twice and still a permutation, a
  byte-identical second build, and the refusals — an unmeasured company-year, a
  half-written cache file, a measurement from other probes, a shortlist edited
  after its hash was recorded, a cut whose reason was deleted, an in-band
  company-year dropped from the record. The last four check the handoff against
  the real `load_shortlist`.
- **Spot check**, seeded so it is not a hand-picked set: 11 cuts and 9 keeps read
  against their actual filings across both audit rounds. The v1 round found
  defects 1–5 above. The v3 round found none: six cuts correct (two
  clinical-stage biotechs, a CDMO, a snack-food maker, an industrial, an
  enterprise AV maker) and the keeps showing genuine channel disclosure with the
  deduplication visible — Harley-Davidson raw 3 → distinct 2 on its "online
  eCommerce channel", Lands' End raw 69 → distinct 43.
- **The prospectus fallback does not disadvantage newly-listed companies**, which
  was a live worry since they are the Amazon-native brands the rolling formation
  exists to capture. 165 company-years were scored from a registration statement
  rather than an annual report, and their keep rate is 22.4% against 24.7% for
  annual reports. Academy Sports, which listed in October 2020, was kept at the
  2021 formation from its 424B4.

### Two defects in neighbouring components, reported not fixed

Neither is this component's to change, and both are now blocking or misleading.

1. **A2 cannot build 2016 while holding a judgement the pre-filter cut.**
   `score_eligibility.py build --year 2016 --shortlist data/shortlist_2016.csv`
   refuses: "judgements for 1 ticker(s) outside the shortlist: NTAP". NetApp was
   hand-scored at 31.0 before this component existed, and the filter cuts it —
   **the two agree**, since A2's own binding reason was "no consumer product
   portfolio and no disclosed consumer or e-commerce channel", and the filter
   records 0 channel mentions with 3 Amazon-as-AWS exclusions. The refusal is
   correct in spirit and has no case for a pre-existing judgement. The one-line
   fix belongs to A2: `scope |= set(judg) & set(uni)`, so a company-year already
   scored from the filing in force stays in scope while nothing new outside the
   shortlist can be scored. Not made here, because it changes A2's scope
   semantics.
2. **The orchestrator reads a file A2 does not write.** `pipeline_state.py`
   checks `data/eligibility.csv`; A2 writes `data/eligibility_<YEAR>.csv`, one
   per formation. The weekly run therefore reports `A2.eligibility` as "absent"
   rather than "partial", and it has no gate for A1b at all — a run with no
   shortlist will say A2 is a missing human step rather than that the pre-filter
   has not been run. Also minor: SKILL.md's phase map still credits A1 to
   `screen_universe.py`, while the panel is built by `screen_rolling.py`.
3. Related: `eligibility_2016.hash` records `universe=universe_2016.csv` with
   `of_in_band=3/40`, the superseded 27-code screen. The three judgements remain
   valid — all three are in-band 2016 company-years in the panel — but that
   denominator is stale, and the shortlist supersedes it.
