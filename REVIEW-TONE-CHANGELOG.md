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

## 2026-10-11 · tone scorer (B3 + B4)

Built `scripts/score_reviews.py`: scoring, the calibration gate, and the offline
verification that proves the three failure modes this component owns are closed
by construction rather than by care.

**Script, not a subagent — recommended and implemented that way.** The rubric
fixes `claude-haiku-4-5-20251001`, temperature 0, one review per call and no
conversation history. A Claude Code subagent runs on the session's model with the
session's system prompt and tools, carries context across turns, and exposes no
temperature, so it can satisfy none of those four constraints and cannot stamp a
reproducible `scorer_model_id`. CORE-PROMPT §1.3 also puts every reported number
in a script, and kappa and the agreement rates are reported numbers. Nothing was
added to `.claude/agents/`.

**Standard library only.** API calls go through `urllib.request`; the Anthropic
SDK is not used, so the script has no third-party imports. The single external
requirement is `ANTHROPIC_API_KEY`. `ANTHROPIC_BASE_URL` in the environment is
deliberately ignored — inside a Claude Code session it points at a gateway that
does not accept `x-api-key` — and is overridable only via `--base-url`.

**F8 · the star rating cannot reach the scorer.** Prompts are built by
`build_prompt(review_id, title, text)`, which takes three strings and has no
access to the review row, so `rating` has no path in. The template is asserted at
import time to contain no rating/star/helpful/verified placeholder, the rendered
message is reconstruction-checked against the template, and the self-test proves
two rows differing only in `rating` produce byte-identical prompts. After scoring,
`pearson(tone, star)` is computed and printed: ~0.95 means the text added nothing
over the star average and the run is a result about stars.

**F10 · scorer drift.** Every row stamps `rubric_version` and the model ID the API
*answered with*, not the alias requested; responses from more than one model abort
the run before anything is written. `tone-rubric-v1.md`'s SHA-256 is pinned in the
script, so an in-place edit to the frozen rubric stops the pipeline and says to
create v2 — verified by appending one newline to the file. The scorer is shown the
Scale, Rules, Tags and Output format sections extracted verbatim from that file,
never a paraphrase; the calibration-gate section is withheld. `scores.csv` is
append-only: re-scoring under a new model adds rows beside the old ones and
downstream selects on `(rubric_version, scorer_model_id)`.

**F13 · silent scoring failures.** Malformed output is retried once, then written
as a row with `scoring_failed=True` and an empty `tone` — never dropped, never
guessed at. Strict parsing: a bare JSON object with exactly the five rubric keys,
an integer tone in −2..+2, three real booleans, and a `review_id` matching the one
requested. Prose, code fences, extra keys, stringified tones and echoed wrong ids
are all rejected. Transport exhaustion also yields `scoring_failed` but is counted
separately so a network outage is never read as a model problem. Counts land in
`scoring_report.json` overall and per fiscal quarter, quarters over 10% are
flagged, and a run over `--max-failure-rate` (default 10%) exits non-zero.

**The calibration gate blocks; it does not warn.** `--calibrate` draws 100 scored
reviews stratified across the model's own score range under a recorded seed, and
writes a blind labelling sheet carrying `review_id`, `title` and `text` only —
no model score and **no star rating**, because a human who sees either is no
longer an independent rater and the agreement becomes circular on the human side.
`--calibrate --labels <file>` computes exact agreement, adjacent agreement and
quadratic-weighted kappa, each with a bootstrap 95% CI, writes `calibration.json`,
and exits non-zero unless kappa >= 0.60 AND adjacent >= 0.85 on n = 100.
`require_calibration()` is exported for `aggregate.py` and `backtest.py` to call.

**Verified, 43 offline checks, all passing** (`--self-test`), against 5,513 real
reviews streamed from the 2023 corpus. Gate arithmetic checked against hand-worked
cases: perfect agreement → kappa 1.0; a rater uniformly one step high → adjacent
1.0, exact 0.2; both raters constant → kappa `null`, not a flattering 1.0; a
constant model against a spread human → kappa 0.0. Gate behaviour checked on four
constructed label sets: kappa 0.978 / adjacent 1.000 → pass; kappa 0.664 with
adjacent 0.780 → **blocked on adjacent alone**, which is the AND in the gate doing
its job; perfect agreement on n = 50 → **blocked for being short of 100**. The
whole B4 path ran offline under `--backend stub` at `--workers 4` over 500 real
reviews: 470 scored, 30 failed (13 malformed, 17 transport), 51 seller-only
tagged, kept in the file and excluded from the aggregate, and `scores.csv` headers
matching the contract exactly.

**Defects found and fixed during the build:**
- The corpus contains exact duplicate records — the same text from the same user
  on the same `parent_asin` at the same second, 1 in 5,513 in the sample. Since
  `review_id` hashes exactly those three fields, the id repeats. An identical row
  is now dropped and counted; two *different* reviews sharing an id is a hard
  failure, because `scores.csv` is keyed on `review_id` and would silently merge
  them. `extract_reviews.py` should dedupe at B2 rather than leave it here.
- A retry at temperature 0 resends an identical prompt and mostly gets an
  identical answer, so the rubric's mandated single retry recovers truncation and
  transport failures, not systematic malformation. Implemented as written, with
  the three outcomes counted separately so the retry's real yield is visible.
- Kappa is undefined only when *both* raters are constant. One constant rater
  gives kappa 0.0, and an early self-test asserting otherwise was wrong, not the
  arithmetic.

**NOT RUN, and the pipeline is therefore blocked at B3.** No scoring credential is
reachable from this environment: `ANTHROPIC_API_KEY` is unset, the SDK is absent,
and a spawned Claude Code CLI reports "Not logged in". So no review has been scored
by `claude-haiku-4-5-20251001`, no calibration sample has been drawn from real
model scores, and no human labels exist. **The calibration gate is unmet. It has
not been evaluated against a single real human label, and no downstream result may
be produced.** The tone distribution reported above is synthetic stub output that
verifies the plumbing; it is not tone data, every row of it is stamped
`stub:deterministic-v1`, the stub refuses to write anywhere under `data/`, and the
gate refuses to evaluate labels against it.

To unblock B3: `extract_reviews.py` (B2) first, then, with a key exported,
`score_reviews.py {TICKER}`, `--calibrate` to draw the sheet, hand-label it, and
`--calibrate --labels` to see whether the gate passes. It may not, and that is a
legitimate outcome — an unvalidated scorer makes every downstream statistic
decorative.
