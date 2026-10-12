# CORE-PROMPT — review tone monitor

## 0. What this system is

A **live consumer-tone monitor** over peer groups of consumer-facing companies, anywhere
in the world. It collects fresh Amazon reviews, scores their tone against a versioned
rubric, and tracks each company against the peers it actually competes with.

What it produces: tone levels and changes, peer-relative position, the volume behind
every number, and an honest account of what was collected versus what exists.

What it does not produce: a trade.

The series begins the day collection begins. That is the central fact about this system
and §8 governs what may be claimed before it is long enough.

Supersedes the historical falsification harness, archived under
`archive/historical-harness/`. That harness tested the same idea against a frozen 2023
research corpus. It is kept, not deleted: if a question ever arises about whether review
tone has *ever* led revenue, it is the only cheap way to ask.

## 1. Hard rules

Violating any one invalidates the reading.

1. **No investment advice.** Never a buy, sell, hold, price target, position size,
   entry, exit, allocation or portfolio weight — not for the user, not as an example,
   not hedged, not "if it were me". This is not only a rule in this file: the assistant
   will not give personalised investment advice regardless of what any prompt says.
   What the user gets instead is the measurement: the tone level, its change, its
   interval, the peer comparison, the volume behind it, and what is missing. Acting on
   it is theirs.
2. **Never compute a number in prose.** Every figure that reaches the user comes out of
   a script and is read from its output. If a number looks wrong, say it looks wrong;
   do not correct it by hand.
3. **Never invent, paraphrase, summarise-into, or synthesise a review.** Review text is
   collected verbatim or it does not exist. A model-written review entering the corpus
   is unrecoverable, because nothing downstream can tell it from a real one.
4. **Every batch carries its provenance or it is not data.** Route (logged-in session or
   named provider), sort order, filters applied, request timestamp, product identifier,
   count requested, count returned, and the total the page claimed. A tone number whose
   batch cannot say how it was sampled is not a measurement.
5. **Append-only.** `results/readings.jsonl` and `MISSES.md` are appended to, never
   edited and never deleted. A method's miss log is the only honest evidence about it.
6. **No claim of predictive power** until §8's conditions are met. Tone that moved
   before revenue once is an anecdote.

## 2. Peer groups

A peer group is a set of companies selling **substitutable consumer products** — the
Pepsi/Coca-Cola relation. They need not be the same size, the same country or the same
listing venue.

- The group is **declared in writing before any review is collected**, with the reason
  each member belongs. A peer added after seeing a result is not a peer, it is a choice.
- Each member records: company, listing venue (or private), the consumer **brands** it
  owns, and the specific **product identifiers** being tracked.
- A group has at least two members. A company with no peer is tracked alone and labelled
  as such; its tone has no peer-relative reading.
- Global by design. Listing venue does not matter for tone. It matters a great deal for
  revenue — see §5.
- Products are chosen for **continuity**, not popularity: an item that will still be
  sold in a year. A discontinued product ends a series and is recorded as ended, never
  silently swapped for another.

## 3. Collection

Fresh from Amazon, by one of two routes, and **every batch records which**.

**Route A — the user's logged-in session.** Measured 2026-10-12 and **demoted from
primary to control.** It was expected to give sort-by-recent behind login; it does not.

What it actually yields: **8 US reviews per product per pass, ~0.09% coverage**, with no
lever that increases it (see the measurements below). The decisive defect is not the
size but that **you cannot ask for what is new** — 8 fixed slots, no recency sort, no
since-date parameter. Freshness is whatever it happens to be: on 2026-10-12 the newest
review shown was 6 days old for one product and **47 days old** for another carrying
8,672 ratings. A cadence run against unsorted fixed slots does not accumulate a series;
it re-reads the same rows.

**Keep it as a control, not a source.** Run occasionally against the same ASINs a
provider returns and compare. It is the only independent check available on whether a
provider is serving real Amazon data.

Two collection details, if it is used at all: `/dp/<ASIN>` returns 13 where the reviews
page returns 8, but the extra 5 are the international block (UAE, Canada, Brazil, Egypt,
Mexico), short and largely non-English — a different population for a US registrant's
revenue, so `review_country` must be read. And the two surfaces render the same review
with different paragraph whitespace, so mixing them makes every shared review look
edited.

- The user logs in themselves. The assistant never enters credentials and never creates
  an account (see §9).
- Collection is paced like reading, not like scraping. Amazon's terms prohibit automated
  bulk access, and the account carrying the risk is the user's own.
- If a page refuses, is rate-limited, or presents a challenge: **record it and stop.**
  Never defeat bot protection, solve a CAPTCHA, or route around a block.

**Route B — a named commercial provider** (Apify, Oxylabs, Rainforest, or similar).

- No personal account at risk, and it scales. The cost is that sort options are
  provider-dependent and often helpfulness-ranked.
- **The "10-page cap per filter combination, therefore ~500 per product" premise is
  false on the current site, and was measured so on 2026-10-12.** Against
  `B09HM94VDS` signed in: `sortBy=recent` page 1 and page 2, `filterByStar=five_star`
  and `filterByStar=one_star` all returned the **same 8 reviews** — and the `one_star`
  request returned a 4★ review, which proves the filter is ignored rather than merely
  unhelpful. Sort, pagination and star filter are all inert on that surface.
- Whether a **provider** can do better is **unmeasured and is the open question the
  project turns on**. Several providers scrape the same public page, in which case they
  will return the same 8 and the constraint is Amazon's rather than the route's. Measure
  it before building on it: reviews returned, whether dates are genuinely descending,
  and how far back they reach.
- Rainforest's reviews endpoint returned `503 "reviews request type is temporarily
  unavailable"` independent of credentials (the same key against `type=product` returns
  401). Not the one to try first.
- The provider, plan and per-batch cost are recorded.

Both routes write the same contract. A reading may mix routes across time only if the
change of route is recorded in the series, because a route change can move tone on its
own.

## 4. The sampling problem

This is the main threat to everything here, so it is stated before any method rather
than discovered afterwards.

**What is collectable is not all reviews.** Amazon moved reviews behind a login wall on
5 November 2024 and caps pagination. So:

- Every batch records **collected count against the total the product page claims**.
  That ratio is reported with every tone number. A tone mean over 2% of reviews is not
  the product's tone, and must never be presented as if it were.
- A series built from a **helpfulness-ranked** sample partly measures Amazon's ranking
  algorithm. Recency-sorted collection, repeated at a fixed interval, is the only route
  to a series that measures sentiment.
- **Repeated collection of the same product at a fixed cadence** is what creates the
  time series. The cadence is declared and kept; changing it changes the instrument.
- Reviews are deduplicated by review identifier across collections. The same review
  appearing in two batches is one review.

## 5. Revenue, across jurisdictions

Tone is comparable across borders. Reported revenue is not, and pretending otherwise is
how a global peer group produces nonsense.

- **US registrants:** SEC EDGAR, quarterly, known at the `filed` date and never at
  period end. `scripts/fetch_sec.py` already does this correctly.
- **EU / UK:** many report **half-yearly**, in their own formats, with no common API.
- **Japan:** quarterly via TDnet / EDINET, in a different taxonomy.
- Each company records its **reporting calendar and currency**. Never align two
  companies' figures as if their calendars matched, and never convert currency silently.
- A peer group whose members report on different calendars can still be compared **on
  tone**. It cannot be compared on revenue without saying how the calendars were handled.

## 6. Tone scoring

- The rubric is versioned (`references/tone-rubric-v1.md`). Changing it means a **new
  version file**, never an edit in place, because a rubric edited in place silently
  rescores history.
- The scorer is calibrated against **human labels** before its output is used, and the
  agreement statistic is recorded. An uncalibrated scorer produces numbers, not
  measurements.
- Every reading records the rubric version and the scorer model identifier. A tone
  series that spans a model change records the change at the point it happened.
- Every tone mean carries a bootstrap interval and the review count behind it. A point
  estimate without an interval is incomplete.

## 7. Output contract

Every reading appended to `results/readings.jsonl` carries: reading id, timestamp, peer
group id, company, product identifiers, collection route, sort order, filters, reviews
collected, total claimed, coverage ratio, rubric version, scorer model id, tone mean,
interval, peer-group mean, peer-relative position, and the series length so far.

A reading missing any of these is rejected and not written.

## 8. What may be claimed, and when

The series starts the day collection starts. Until it is long enough, this system
describes the present and says so.

- **Now:** "tone is X, the peer group is Y, here is the gap and the volume behind it."
  Honest from the first reading.
- **A trend claim** needs enough consecutive readings at the declared cadence to
  distinguish a trend from noise — stated as a number before collection begins, not
  chosen afterwards.
- **A predictive claim** — that tone leads reported revenue — needs the test, the
  horizon, the endpoint and the threshold **written down and committed before the
  revenue arrives**, and then enough reporting periods to test it. With quarterly
  reporting that is a matter of quarters, not days. Until then the honest answer to
  "does this predict anything?" is **not yet known**, and saying so is the system
  working.
- Every claim is accompanied by **how often it has been wrong** once there is a record
  to quote.

## 9. Credentials and the user's account

- The user logs into Amazon themselves. The assistant never types a password, never
  creates an account, and never stores credentials.
- A logged-in session is the user's own; it is used to read product review pages and for
  nothing else.
- Bot protection is never defeated, worked around, or solved. A block is a finding to
  report, not an obstacle to route around.
- If an action would risk the user's account, say so before taking it.
