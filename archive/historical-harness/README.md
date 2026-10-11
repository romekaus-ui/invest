# Historical falsification harness — archived 2026-10-11

Tested whether the tone of dated Amazon reviews carried information about a
company's later SEC-reported revenue, using `McAuley-Lab/Amazon-Reviews-2023`,
a frozen research corpus ending September 2023.

**Why archived:** the project changed shape. The live system collects fresh
reviews from Amazon and tracks global peer groups, which conflicts with this
harness's three foundations — a US-only SIC-screened universe, SEC-only revenue,
and a corpus that cannot be extended. See `/CORE-PROMPT.md`.

**Why kept rather than deleted:** it is the only cheap way to ask whether review
tone has *ever* led revenue. The live system cannot answer that for several
quarters; this one could answer it in days, on free data, if the question
becomes pressing.

**State when archived:** Phase A, stopped at gate A2. Universe panel of 3,522
in-band company-years across seven annual formations; a mechanical pre-filter
cutting it to 865; 3 company-years hand-scored. No review was ever read, no tone
ever computed. Two recorded runs, both `insufficient_data`. 118 orchestrator
checks and 72 pre-filter checks, all passing.

**What was NOT archived, because it is source-agnostic and the live system uses
it:** `score_reviews.py` (tone scoring and calibration), `aggregate.py`,
`fetch_sec.py` (point-in-time US revenue), `results_row.py` (append-only
writer), `env_stamp.py`, `window.py`, the tone rubric, the data contracts, the
numeric-stack decision, and the dashboard skill.

Everything here is intact in git history regardless of this directory.
