# Changelog

The live review-tone monitor. The archived falsification harness has its own changelog at
`archive/historical-harness/REVIEW-TONE-CHANGELOG.md`.

---

## 2026-10-12 — Stage C: live review collection, two routes

Added `scripts/collect_reviews.py` and `scripts/capture_reviews.js`: fresh Amazon review
collection behind one interface, Route A (the user's logged-in session) and Route B (a
named commercial provider). Documented in `references/live-collection.md`. Replaces the
archived corpus extractor, whose data source — a snapshot frozen at 2023-09 — cannot
produce a series that starts today.

Built to `CONTRACTS.md` §2/§3/§4: invoked as `--group <group_id> [--company <slug>]`,
writes `data/<group_id>/<company_slug>/reviews.csv` and an append-only `batches.jsonl`,
reads `groups/<group_id>.json` and never writes it.

### What Amazon actually served, and what it means

Collected from two real products on `amazon.com`, signed in, 2026-10-11:

| | Logitech MX Master 3S | Hydro Flask 32oz |
|---|---|---|
| returned | 8 | 8 |
| page claims | 8,672 ratings | 30,986 ratings |
| coverage | 0.000923 | 0.000258 |
| dates descending | no (4 inversions) | no (3 inversions) |

- **`sortBy=recent` is ignored and the page offers no sort control at all.** Both
  products came back in an order that is recency-weighted but not recency-sorted.
- **Pagination is dead on that surface** — `pageNumber=2` returns the identical 8 reviews.
  The `/portal/customer-reviews/` path behaves the same and its "Load more" button is
  `visibility:hidden`, 0×0.
- **Coverage is ~0.1%**, against a *ratings* count, which is the wrong population: ratings
  include star-only ratings with no text that this pipeline can never sample.
- **`amazon.co.uk` requires its own sign-in.** Recorded as a block, not attempted.

The consequence, which is the most important line in this entry: **nothing collected
through Route A today is a time series.** Stage C can be satisfied while the data still
cannot support a trend claim. `status` says so explicitly whenever no batch in a group has
a verified recency sort.

### Decisions these findings forced

- **Sort is verified against the collected dates, not read off a label.** There is no
  label to read. A batch requested as `recent` whose dates are not descending is recorded
  `sort_verified: false` / `sort_produces_series: false`, and `--require-recency` refuses
  it. A label-based check passes this page silently, which is the failure CORE-PROMPT §4
  exists to prevent.
- **`claimed_total` carries a `claimed_total_basis`** (`reviews` / `ratings` /
  `unavailable`). Without it the coverage ratio is not comparable between two members of a
  peer group. Raised with the contract owner.
- **§1.3 is enforced mechanically.** `capture_reviews.js` hashes the review text in the
  browser; `capture` recomputes it over what reached disk and refuses the batch on any
  mismatch, so a paraphrase cannot enter the corpus undetected. The limit is stated rather
  than implied: the hash proves the file matches what the extractor saw, not that the
  extractor that ran was the repo's, so the extractor's own SHA-256 is recorded per batch.
  This check fired for real during development and caught a one-byte header bug.
- **Collected reviews are now committed** (`.gitignore`). The archived harness ignored
  `reviews.csv` because it was reproducible from a public corpus. A live batch is not
  reproducible at all — the page has already changed — so it is the only copy there will
  ever be.
- **First observation is canonical on re-collection.** `helpful_vote` grows between
  batches, and `score_reviews.py` treats one `review_id` keying two different rows as a
  hard failure; a naive append would eventually kill the scorer. Later observations of
  mutable fields go to `review_observations.jsonl`, so vote growth is not lost.
- **No `fiscal_quarter` column**, per CONTRACTS.md §2 — aggregation assigns periods.
  Consequence recorded: `score_reviews.py` exits loudly if ever run with a restricting
  `--window`.

### Verified

- `collect_reviews.py self-test` — 69 checks, offline, no page touched.
- **The handoff was run, not assumed:** `score_reviews.py` against the collector's actual
  output for both companies. 8/8 rows read and scored each, `review_id` joins 8/8 with no
  orphans. (Stub backend: this verifies the scoring path, not scores — its tone values are
  synthetic and its failure rate is injected.)
- Route B probed against all three providers with invalid credentials: apify 401,
  oxylabs 401, **rainforest 503 `"reviews request type is temporarily unavailable"`** —
  not an auth error, since the same key against `type=product` returns 401. Rainforest's
  reviews endpoint is down independently of credentials. Route B could not be exercised
  end to end: no provider token exists and no account was created (§9).

### Fixed in passing — two live stages were unimportable on `main`

Both left by the harness→monitor restructure:

- `fetch_sec.py` imported `screen_universe`, now archived. Stage B6 could not be imported
  at all. Inlined the two helpers it needed.
- `window.py` imported `pipeline_state`, also archived — which took `score_reviews.py`
  down with it, so the scorer could not load either. The import is now conditional:
  `--window full` still refused, `--window dev` does not restrict and says so loudly on
  every run, and it hard-exits if a `prereg/` directory exists while the module is
  missing. A window that parses and does not bind is worse than no window.

### Not done

- `groups/premium-everyday-carry.json` is a **hand-written fixture** for testing this
  stage against real ASINs. Its concentration evidence is a placeholder, so it does not
  satisfy stage G and no peer-relative reading may be taken from it.
- No tone number is computed anywhere in this stage, and none may be inferred from its
  output (§1.2).
