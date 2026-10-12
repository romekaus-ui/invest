# Live collection — stage C

`scripts/collect_reviews.py` collects **fresh** Amazon reviews for the products a peer
group tracks. It replaces the archived corpus extractor: the 2023 corpus is a frozen
snapshot ending 2023-09, and a frozen snapshot cannot produce a series that starts today
(CORE-PROMPT §0).

One interface, two routes, and **every batch records which produced it** (§1.4).

```bash
S=.claude/skills/review-tone-signal/scripts/collect_reviews.py

python3 $S plan   --group <group_id> --cadence weekly --reason '...'
python3 $S extractor                                  # the Route A browser script
python3 $S capture --group <group_id> --asin <ASIN> --capture <payload.json>
python3 $S fetch   --group <group_id> --asin <ASIN> --provider apify
python3 $S status  --group <group_id>
python3 $S self-test
```

Exit codes follow CONTRACTS.md §4: `0` ran or reported a clean stop, `2` an integrity
violation needing a human (this is what a **block** returns), `3` not built.

---

## Before you run this: the risk is the user's account

Route A reads through **the user's own logged-in Amazon session**. Amazon's terms of use
prohibit automated bulk access, and the account that would be suspended for it is the
user's, not a service account, not ours. That asymmetry sets every pacing decision here:

- **The script does not page, click, scroll or retry.** It ingests one capture of one
  page that a human-paced session already had open. The looping, if any, is a person's
  decision each time.
- **The user signs in themselves.** The assistant never types a password and never
  creates an account (CORE-PROMPT §9). There is no code path here that submits a
  credential.
- **A block ends the run.** Sign-in redirect, CAPTCHA, throttle, bot challenge: recorded
  as a batch with `blocked: true` and a reason, exit 2. Never solved, never worked
  around, never retried from another angle. A block is a finding about the instrument.

If a reading needs more reviews than reading-pace collection can supply, that is an
argument for Route B, not for going faster on Route A.

---

## What Amazon actually served, 2026-10-11

Measured, on `amazon.com`, signed in, against two real products. This is the single most
important thing in this file, because it decides what the data can be used for.

| | B09HM94VDS (Logitech MX Master 3S) | B07YXM6XTF (Hydro Flask 32oz) |
|---|---|---|
| reviews returned | 8 | 8 |
| page claims | 8,672 **ratings** | 30,986 **ratings** |
| coverage ratio | 0.000923 (0.09%) | 0.000258 (0.03%) |
| dates descending? | **no** — 4 inversions | **no** — 3 inversions |
| sort control on page | **none** | **none** |
| pagination | **none** — `pageNumber=2` returns the identical 8 | same |

Four findings follow, and each one is wired into the code rather than left in prose:

**1. `sortBy=recent` is ignored.** Logitech came back 2022-12-20, 2025-10-24, 2024-09-08,
2026-06-06, 2026-05-11, 2026-08-26, 2023-06-27, 2023-08-08. The current page renders no
sort `<select>` and no dropdown, so there is no label to read a sort off either.

Therefore the sort is **verified against the dates**, not read off the page
(`check_recency`). A batch requested as `recent` whose dates are not descending is
recorded with `sort_verified: false` and `sort_produces_series: false`, and
`--require-recency` refuses it outright. §4 turns on exactly this distinction: a series
built from a ranked sample partly measures Amazon's ranking algorithm.

**2. Coverage is ~0.1%, and the denominator is the wrong population.** Amazon stated only
a *ratings* count on both products. Ratings include star-only ratings with no text, which
this pipeline can never sample, so dividing by them understates coverage — and a ratio
whose basis is unlabelled is not comparable between two members of a peer group. Every
batch therefore carries `claimed_total_basis` ∈ `reviews` / `ratings` / `unavailable`
alongside `claimed_total`.

**3. The ~500-per-product star fanout does not apply here.** CORE-PROMPT §3 notes the
10-page cap is per filter combination, so a 1★–5★ fanout multiplies yield. That assumes
pagination works. On this surface it does not: page 2 is page 1. A star fanout may still
help — `--star` is wired for it — but it has not been shown to on this route.

**4. Non-US marketplaces need their own sign-in.** `amazon.co.uk` redirected to
`/ap/signin` for this account. Recorded as a block; not attempted.

**The consequence to carry forward:** nothing collected through Route A today is a time
series. Stage C can be satisfied — every tracked ASIN has a batch within the cadence —
while the data still cannot support a trend claim. Those are different conditions and
`status` says so explicitly when no batch in a group has a verified recency sort.

---

## Route A and the §1.3 problem

§1.3: review text is collected verbatim or it does not exist, and a model-written review
entering the corpus is unrecoverable, because nothing downstream can tell it from a real
one.

The assistant sits between the page and the disk. A transport that *can* paraphrase
eventually will. So the rule is made mechanical:

1. `scripts/capture_reviews.js` runs in the browser and reads the review text out of the
   DOM.
2. It computes a **SHA-256 over that text, in the browser**, before anything else sees
   it — length-prefixed field encoding, not JSON, because `JSON.stringify` and
   `json.dumps` disagree about escaping lone surrogates and U+2028, and a hash scheme
   that disagrees across languages fails open on exactly the reviews with unusual
   characters.
3. `capture` recomputes that hash over what actually reached disk and **refuses the
   batch** on any mismatch. One smoothed typo, one trimmed sentence, one re-wrapped line
   fails it.

What this does **not** cover, stated plainly rather than implied: the hash proves the
file matches what the extractor *saw*, not that the extractor which ran was the repo's.
A mistyped extractor would read the wrong element and hash the wrong text faithfully. The
repo extractor's own SHA-256 is recorded with every batch (`raw_meta.extractor_sha256`)
so the intended version is pinned, and the paste step is the one link checked by review
rather than by machine.

Verbatim also means **unedited after the fact**. When a review already collected comes
back with different text, both versions are kept: the first observation stays in
`reviews.csv`, the new one goes to `review_observations.jsonl`, and the run warns. Neither
is discarded and neither is merged.

### Running it

```bash
python3 $S extractor          # prints capture_reviews.js
```

Paste it into the browser's JavaScript evaluator on a review page the user has already
signed into, save what it returns to a file, then `capture` it. One page per call, one
batch per page.

---

## Route B — named providers

`apify`, `oxylabs`, `rainforest`, behind the same interface, selected with `--provider`.

Each refuses to run without its token, naming the free tier and where to get one. **No
account is created and no plan is bought here** (§9): the user signs up, exports the
token, re-runs.

| provider | env var | free tier |
|---|---|---|
| apify | `APIFY_TOKEN` | $5/month platform credit, no card |
| oxylabs | `OXYLABS_AUTH` (`user:pass`) | 2,000 results, 7-day trial |
| rainforest | `RAINFOREST_API_KEY` | 100 credits on signup |

Probed 2026-10-11 with deliberately invalid credentials, to check reachability and
response shape without spending anything:

- **apify** — HTTP 401, `user-or-token-not-found`. Wiring correct; needs a token.
- **oxylabs** — HTTP 401. Wiring correct; needs credentials.
- **rainforest** — HTTP **503**, `"reviews request type is temporarily unavailable"`.
  Not an auth error: the same key against `type=product` returns 401, so authentication
  is checked *after* this. Rainforest's reviews endpoint is **down independently of
  credentials**. A free tier would not have tested it either.

Route B's sort is **not trusted from the documentation** — providers advertise sort
options that do not always arrive. The observed sort is read back off the response where
the provider returns one, and the same date-monotonicity check is applied to what comes
back. Rainforest's outage is the same lesson one level up: the endpoint the docs describe
may not exist today.

Route B has one provenance advantage worth stating: its response arrives over HTTP into
the process, so the model never touches the text and there is nothing to hash-verify.

---

## Cadence

CORE-PROMPT §4: repeated collection at a fixed cadence is what creates the series; the
cadence is declared and kept, and changing it changes the instrument.

`collection-plan.json` holds it per group, with `declared_at`. Changing it requires
`--reason` and appends to `cadence-log.jsonl` rather than overwriting. Every batch records
the cadence it ran under **and how far off schedule it actually was**
(`schedule.days_off_cadence`), so the spacing of the series is a property of the data
rather than of who remembered to run it.

CONTRACTS.md §1's group file has no cadence field, so stage C's "within the declared
cadence" condition is read from here until that is settled.

---

## Dedup, and why `reviews.csv` is rewritten

Reviews are deduplicated by **review identifier** across collections (§4) — Amazon's own
`R…` id where present, which is the identifier Amazon itself dedupes on, falling back to
a stable hash and recording per row which scheme was used.

The first observation is canonical. This is not a preference, it is a requirement:
`helpful_vote` grows between collections, so a re-collected review differs from the stored
one, and `score_reviews.py` treats one `review_id` keying two different rows as a hard
failure. A naive append would eventually kill the scorer on exactly the products being
read most often. Later observations of mutable fields go to `review_observations.jsonl`,
so vote growth — which the degradation test uses — is not lost.

`reviews.csv` is therefore **derived and rewritten**; the append-only files are
`batches.jsonl` and `review_observations.jsonl`, which together reproduce it. That is the
right way round: §1.5 protects the evidence, and a CSV that could only be appended to
would have to carry duplicate ids for every re-collection.

---

## What this stage does not do

- **No tone number.** That is `score_reviews.py`'s job and the separation is the point
  (§1.2). The self-test parses this module's own AST and fails if any function, variable
  or argument name suggests otherwise, or if the scorer is imported.
- **No fiscal quarters.** CONTRACTS.md §2 removed the column: aggregation assigns periods,
  and labelling them here would bake one company's calendar into another's data. The
  archived extractor's fiscal handling — exact filed period bounds, falling back to month
  arithmetic off EDGAR's own `fiscalYearEnd`, never inferred from the mode of 10-K period
  ends — is the right implementation for whoever builds that stage, in
  `archive/historical-harness/scripts/extract_reviews.py`.

  One consequence: `score_reviews.py` exits with *"has no fiscal_quarter column, so
  --window cannot be honoured"* if run with a restricting window. Loud, not silent.
- **No peer-group decisions.** `groups/<group_id>.json` is read, never written. A
  collector that could add a product would be choosing the peer group by what was easy to
  collect.
- **No review date it was not shown.** A review whose date will not parse has no date and
  is dropped and counted, never inferred and never filled (§2).
- **No reviewer identity.** The reviewer's profile URL is deliberately not captured: it
  identifies a person, it is not needed for tone, and a corpus carrying it is a corpus
  that has to be defended.
