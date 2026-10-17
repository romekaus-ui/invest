# Live collection — stage C

`scripts/collect_reviews.py` collects **fresh** Amazon reviews for the products a peer
group tracks. It replaces the archived corpus extractor: the 2023 corpus is a frozen
snapshot ending 2023-09, and a frozen snapshot cannot produce a series that starts today
(CORE-PROMPT §0).

One interface, **three** routes, and **every batch records which produced it** (§1.4).

```bash
S=.claude/skills/review-tone-signal/scripts/collect_reviews.py

python3 $S plan   --group <group_id> --cadence weekly --reason '...'
python3 $S extractor                                  # the Route A browser script
python3 $S capture --group <group_id> --asin <ASIN> --capture <payload.json>
python3 $S fetch   --group <group_id> --asin <ASIN> --provider apify
python3 $S brand  --group <group_id> --product-id <ID>   # Route C, the brand's platform
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

**3. The ~500-per-product star fanout does not work: the star filter is ignored too.**
CORE-PROMPT §3 notes the 10-page cap is per filter combination, so a 1★–5★ fanout should
multiply yield roughly fivefold. Measured on B09HM94VDS:

| request | reviews returned | ids |
|---|---|---|
| `?sortBy=recent&pageNumber=1` | 8 | set X |
| `?sortBy=recent&pageNumber=2` | 8 | **identical set X** |
| `?filterByStar=five_star` | 8 | **identical set X** |
| `?filterByStar=one_star` | 8 | **identical set X** — including a 4★ review |

Four different requests, one identical set of eight review ids. `one_star` returning a
4★ review is the proof that the filter is not merely unhelpful but ignored. `--star` stays
wired in case the surface changes, but on this route the fanout buys nothing.

**3b. The product detail page is the better surface: 13 rather than 8.** `/dp/<ASIN>`
returns a strict superset — the same 8 plus 5 more. But the extra 5 are the
*international* block (UAE, Canada, Brazil, Egypt, Mexico) and they are short and
largely non-English: "Sensacional. Recomendo" (22 bytes), "Perfecto, Gracias !!! A+++"
(26 bytes). For a US-listed registrant's revenue they are a different population, so the
usable US yield is still 8.

Two cautions if you collect from `/dp/` instead. It renders the same review text with
different paragraph whitespace than `/product-reviews/` does (4,398 vs 4,387 bytes for
the same review id), so **mixing the two surfaces makes every shared review look edited**
and trips the text-changed warning. Pick one surface per series and record it. And
`review_country` must be read, because that page mixes marketplaces.

**4. Non-US marketplaces need their own sign-in.** `amazon.co.uk` redirected to
`/ap/signin` for this account. Recorded as a block; not attempted.

**5. Freshness varies by product and cannot be requested.** Newest review shown on
2026-10-12: Hydro Flask 2026-10-06 (6 days old), Logitech 2026-08-26 (47 days old) on a
product with 8,672 ratings. So the set does refresh, but there are only 8 slots, no way
to ask for the newest, and no way to tell a slot that changed from one that did not
without diffing against the previous batch.

**The consequence to carry forward:** nothing collected through Route A today is a time
series. Stage C can be satisfied — every tracked ASIN has a batch within the cadence —
while the data still cannot support a trend claim. Those are different conditions and
`status` says so explicitly when no batch in a group has a verified recency sort.

---

## Reviews are served at the PARENT asin — join on that, not `asin`

Amazon pools reviews across every variant of a product: colour, size, pack count. A
collector keyed on the child asin therefore files the parent's reviews under a label
claiming a narrower population, and **dedup by `review_id` does not catch it** — those
are genuine, distinct reviews, just of a wider product than the label says.

Both products in the test fixture turned out to be variant children:

| declared | real parent |
|---|---|
| `B09HM94VDS` Logitech MX Master 3S | `B0GWMVC57F` |
| `B07YXM6XTF` Hydro Flask 32 oz | `B0FCZK1WMV` |

One batch collected under "Hydro Flask 32oz Wide Mouth" contained reviews tagged
`Style: 20 Oz`, `Style: 40 Oz` and `Style: 64 Oz` — three of eight were of a different
bottle size — plus reviews from Australia, Spain, Singapore, Canada and Italy. Both
pooling axes, visible in a single batch.

So:

- `parent_asin` is a contract column and **the join key** (§2). `asin` records which
  variant was declared; it does not describe the review population.
- It is captured from the **page**, hashed with the rest of the header, and **checked
  against the group file on every batch**. A mismatch refuses the batch rather than
  being reconciled, because Amazon re-parents variation families over time: a group file
  that was right when written can stop being right with no id anywhere changing, and the
  series would change population underneath whoever is reading it. The right repair is
  to re-resolve the parent in stage G, not to edit the file to match — two members may
  have converged on one parent, in which case the peer group itself no longer holds.
- A capture that cannot state a parent is refused. Version 1 payloads carry none.

**This is also the reason to collect from `/dp/<ASIN>`.** The `/product-reviews/`
surface does not expose `parentAsin` at all, so the parent cannot be confirmed there —
only asserted. `/dp/` exposes it, and returns 13 rather than 8 besides.

**What is being measured is variation-family tone, not the named product's tone.**
Nothing downstream may caption a chart with the child product's name as though the
reviews were of that variant. And choosing a product for continuity (§2) does not buy
continuity of the review pool.

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

### Measured on the free tier, 2026-10-13

One product, properly: YETI Rambler (NYSE:YETI), declared variant `B07FM5HLMF`, collected
at parent `B07SGPJBZ6` via `junglee/amazon-reviews-scraper`, `sort=recent`.

| | Route A (logged-in session) | Route B (apify, free) |
|---|---|---|
| returned per product | 8 | **10** (free-plan cap, not Amazon's) |
| dates descending | **no** — 4 inversions | **yes — 0 inversions** |
| newest review age | 47 days (Logitech) | **2 days** |
| claimed total basis | ratings (8,672) | **reviews (10,000)** |
| pagination | dead — page 2 = page 1 | **works** — actor walked pages 5–10 |
| cost | none | $0.06 per run of 10 ($0.006/review) |

**This overturns part of the Route A verdict.** The actor's log reports
`[REVIEWS] - Total reviews 10000, scraped 10` while requesting
`/product-reviews/B0CT68X4NR?pageNumber=5…10`, and each page returned reviews. So
pagination and recency sort are **not** universally dead on Amazon — they were dead on
the surface served to this signed-in account, which has an Estonian delivery address.
The §3 "~500 per product via a star fanout" premise may well hold through a provider; it
does not hold through Route A as measured.

**The 10-review ceiling is the actor's free plan, not Amazon.** Its own log says so:
*"as a free user, you are limited to only 1 start URL and you'll get only 10 Amazon
reviews per run … To access more reviews in a single run, upgrade to the Starter plan."*
This is a pricing limit with a known price, which is a different kind of problem from a
platform limit with no way round it.

Two cautions on the numbers above. `claimed_total` of exactly 10,000 is suspiciously
round and is very likely Amazon's display cap rather than a true count, so the 0.1%
coverage figure is a **lower bound on the denominator and therefore an upper bound on
coverage**. And `helpful_vote` is 0 for all ten because they are three days old — the
provider returns `reviewReaction: null`, checked, not assumed.

### Depth: can the 2024–2026 window be reached? (measured 2026-10-14)

No, for a busy product, at any price. The limit is Amazon's pagination, not the budget.

Four actors tried against YETI Rambler, parent `B07SGPJBZ6`, `sort=recent`:

| actor | $/review | result |
|---|---|---|
| `junglee` | 0.006 | **10 per run** — free-plan cap, stated in its own log |
| `axesso_data` | 0.0009 | **refused**: *"Actor usage only allowed for paying user"* ($0) |
| `memo23` | 0.004 | `dateFrom`/`dateTo` is a **post-filter**: harvested 13, kept 0 ($0.015) |
| `delicious_zebu` | 0.006 | **100 reviews, 10 pages** ($0.63) |

The best result, 100 reviews, spans **2026-09-22 → 2026-10-12: twenty days**. All 2026.
None from 2024 or 2025.

**The arithmetic that settles it.** This product runs ~5.0 reviews/day. Reaching
2024-01-01 is 1,017 days back, so ~5,085 reviews ≈ $31 — five times the whole monthly
budget for one product. But money is not the binding constraint: **Amazon caps pagination
at 10 pages of ~10 per filter combination**, so ~100 is the ceiling per pass however much
is spent. `delicious_zebu` used exactly 10 pages and stopped.

So the requirement is a **velocity** test, not a budget test:

> a product can span 2024-01-01 → today inside one 100-review pass only if it averages
> **≤ 0.10 reviews/day** — roughly 100 reviews in three years.

A star fanout multiplies this, because each star rating has its own 10-page window and a
rare rating reaches much further back at the same page depth. It was requested here and
the actor served only `all_stars`, so the multiplier is **unverified**; at best it raises
the bar to ~0.6 reviews/day, still far below a flagship consumer product.

**`dateFrom`/`dateTo` does not help**, and that is the most useful negative result:
`memo23` harvested the public page's 13 reviews and then discarded the ones outside the
window. The date range filters what was fetched; it does not ask Amazon for older
reviews. Nothing in these actors reaches past the newest ~100.

One caveat that belongs with any history collected this way: what is scraped today is
the reviews that **survived**. Deleted and edited ones are not recoverable, so a 2024
slice is a survivor sample, not a contemporaneous record.

### The positive case: low-velocity products DO reach 2024

The ≤0.10 reviews/day rule above was arithmetic. It was then tested on a deliberately
slow product — C&Berg 30 oz tumbler lids (`B07PLW68CM`), 2,173 ratings, listed ~2019, and
**its own parent**, so the span is not confounded by variant pooling. One pass, $0.63:

| | YETI Rambler (busy) | C&Berg lids (slow) |
|---|---|---|
| returned | 100 | 100 |
| span | 2026-09-22 → 2026-10-12 | **2023-12-30 → 2026-10-08** |
| | 20 days | **1,013 days (2.77 years)** |
| by year | 2026: 100 | **2024: 57 · 2025: 18 · 2026: 24** |
| reviews/day | 5.00 | **0.0987** |
| claimed total | 10,000 reviews | 555 reviews |
| coverage | 1.0% | **18.0%** |
| newest | 2 days old | 6 days old |

**The requested 2024–2025–2026 window is covered, in one $0.63 pass, at 18% coverage.**
The measured velocity is 0.0987/day against a predicted threshold of ≤0.10 — the
arithmetic held.

So the constraint is **product velocity, not budget and not the route**. Depth per pass
is fixed at ~100 by Amazon's page cap; what varies is how many days those 100 reviews
buy. The selection rule for anything that needs history:

> **reviews/day ≤ 100 / (days of history wanted)**
>
> For 2024-01-01 → today that is ≤ ~0.10/day. A product above it cannot reach back,
> at any price, through any of these actors.

This cuts directly against ordinary peer-group selection, which favours flagship
products — exactly the ones that are too busy. A group whose members are all
heavily-reviewed can have **recency but no history**; one built from slower products can
have history but may be less representative of the company's revenue. That tension is
real and belongs in the peer-group component's criteria, not here.

### The star fanout works — and it still cannot fill a quarter

A star filter gets its own 10-page window, so a **rare** rating reaches much further back
than a common one. Measured on C&Berg (`B07PLW68CM`):

| filter | returned | span | cost |
|---|---|---|---|
| `all_stars` | 100 | 2023-12-30 → 2026-10-08 (2.8 yr) | $0.63 |
| `one_star` | 49 | **2019-08-04 → 2026-… (6.0 yr)** | $0.03 |

All 49 were genuinely 1-star and the page URL confirmed `filterByStar=one_star`. Union:
143 reviews over **7.2 years**, 26 quarters, for $0.66.

**And it is still not enough.** `aggregate.py` sets `MIN_REVIEWS_PER_QUARTER = 30` and
`MIN_QUARTERS = 12`, and refuses a tone number below the floor:

| | reviews | span | quarters | **quarters ≥30** |
|---|---|---|---|---|
| C&Berg (slow + fanout) | 143 | 7.2 yr | 26 | **0** |
| CELSIUS Grape Rush family | 12 | 7.6 yr | 9 | **0** |
| Monster Ultra Blue 4ct | 5 | 4.7 yr | 5 | **0** |

Best single quarter across all three: 16 reviews. The floor is 30.

**Correction (2026-10-14): the floor is per COMPANY-quarter, not per product.**
`aggregate.py` keys its buckets on the quarter alone and `included` holds
`(parent_asin, tone)` pairs pooled across every ASIN in the company's `scores.csv`; the
within/mix decomposition exists precisely because a company has several products in one
quarter. So the table above shows that no *single* product clears the floor — which is
true and much weaker than the conflict originally claimed here. Pooling several products
per company is the intended design, and it changes this from a structural impossibility
to an arithmetic requirement. What follows is kept because the per-product numbers are
still what a selection rule has to work from:

**This is the real constraint, and it is a direct conflict:**

> Reaching back needs a **slow** product — depth per pass is capped at ~100, so fewer
> reviews per day buys more days. Computing a quarterly tone needs a **busy** one —
> 30 reviews per quarter is ≥0.33/day sustained. **A product slow enough to reach 2024 is
> by construction too slow to fill its quarters.**

### How many products per company, then?

With pooling, the requirement becomes a count. For a window `D` days deep:

- an `all_stars` pass returns ~100 and reaches back `100/v` days at `v` reviews/day, so
  reaching `D` needs `v ≤ 100/D`;
- such a product contributes `v × 91.3` reviews per quarter;
- clearing 30 needs `N ≥ 30 / (91.3 × 100/D)`.

For `D` = 1,017 days (2024-01-01 → 2026-10-14) that is `v ≤ 0.098/day`, ~9.0 reviews per
quarter each, and **N ≥ 3.3, so 4 products per company** — *if reviews arrive uniformly*.

**They do not.** C&Berg inside the window, where the `all_stars` pass is complete
(it reached 2023-12-30):

```
2024Q1:16 2024Q2:13 2024Q3:15 2024Q4:13 2025Q1:4 2025Q2:6
2025Q3:6  2025Q4:2  2026Q1:1  2026Q2:4  2026Q3:17 2026Q4:2
```

Mean 8.2/quarter — close to the predicted 9.0, so the model is right on average. But the
**thinnest quarter is 2026Q1 with 1 review**, and 2026Q3 has 17. Sized on the mean, 4
products. Sized on the thinnest, 30.

**Which of those is right depends on something not yet measured: whether lumpiness is
independent across a company's products.** If one product's quiet quarter is another's
busy one, pooling smooths it and ~4–7 products suffice. If a company's products go quiet
together — a promotion ending, a seasonal pattern, a review-solicitation campaign — then
pooling does not smooth at all and the thin quarters stay thin however many products are
added.

**Tested (2026-10-14, $1.07):** three YETI products from different sub-lines with
distinct parents — Tundra 105 (`B003V51GUE`), Tundra 110 (`B0DXKXJNYZ`), TANK 45
(`B07PNC9HBZ`) — 180 reviews spanning 2014–2026.

**Correlation is low.** Over the 45 quarters any of them covers, pairwise `r` runs
**+0.07 to +0.23**. Quiet quarters do not coincide, so pooling does smooth. (Over the
2024–2026 window alone the estimates swing to −0.20…+0.67, but those quarters hold 0–3
reviews each and the estimate is noise; the 45-quarter figures are the ones to read.)

**But the pooled quarters still cleared nothing — 0 of 12 — and the reason is not
correlation.** It is that these products are far too slow:

| product | reviews in window | per quarter | v/day | vs ideal | products needed |
|---|---|---|---|---|---|
| Tundra 105 | 8 | 0.7 | 0.0079 | 0.08× | 42 |
| TANK 45 | 10 | 0.9 | 0.0098 | 0.10× | 34 |
| Tundra 110 | 12 | 1.1 | 0.0118 | 0.12× | 28 |
| **C&Berg lids** | **99** | **8.9** | **0.0973** | **0.99×** | **4** |

**"Slow" was the wrong target, and that is the correction this test produced.** The ideal
is a *specific rate*, not a minimum: `v ≈ 100/D` — for this window **0.098 reviews/day**,
about 9 per quarter, about 100 inside the window. It is a **two-sided band**:

> **too fast** → the 100-review pass does not reach back far enough
> **too slow** → the quarters inside the window are empty
> C&Berg sits at 0.99× the ideal; the YETI picks at 0.08–0.12×, needing 28–42 products
> each instead of 4.

So the §1 screen is not "find slow products". It is **velocity within roughly 0.5×–1.0×
of `100/D`**, which for a 2024 window means products accumulating **~50–100 reviews
inside 2024→today**. At that rate, and given the low correlation measured here, **4
products per company** pool past the 30-review floor.

**What this test does not establish.** The correlation was measured on products ~10×
below the target rate, so the per-quarter counts (0–3) are too sparse to pin `r` tightly.
It points to independence rather than proving it. A clean test needs two or three
products near 0.10/day — where counts are ~9 per quarter — and C&Berg is the only such
product found so far.

**One caution on reading per-year counts from this collector.** Outside the `all_stars`
reach, coverage is whatever the rare-star passes happened to supply:

| filter | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| `all_stars` | 0 | 0 | 0 | 0 | 1 | 57 | 18 | 24 |
| `one_star` | 11 | 11 | 5 | 3 | 13 | 0 | 0 | 0 |

Pre-2024 looks sparse because only 1-star reviews reach there, not because the product
was quiet. **Inside** the `all_stars` window the counts are the product's real density;
outside it they are a property of which filters were run. Any distribution read off this
data has to say which side of that line it sits on.

### Re-parsing costs nothing — use it

Apify charges per result produced, not per read. `--dataset-id` re-reads a stored
dataset through the same normaliser for $0. It has now paid for itself twice:

- the YETI run parsed to **zero rows**, because `delicious_zebu` capitalises its field
  names (`ReviewId`, `ReviewDate`, `ReviewContent`);
- the C&Berg run recorded **0% verified purchases**, because the actor's field is
  `Verified` and the mapping looked for `VerifiedPurchase`. Re-reading gave 100%.

Neither cost a second run. The second is the more instructive: it was not an empty
result that announces itself, it was a plausible wrong number that would have reached
the F12 verified-share check as evidence.

It also demonstrated cross-provider dedup: 10 of the 100 were already held from the
`junglee` batch and merged as one review, and two came back with different text from the
two providers, which was flagged rather than silently overwritten.

### What the first live run cost in defects

The parent-mismatch guard fired on its first real provider data — correctly, against a
wrong input of mine. junglee returns `productOriginalAsin` (the asin requested, i.e. the
parent) alongside `productAsin`/`variantAsin` (the variant the reviewer bought); mapping
the variant as the parent produced a confident, fully-evidenced false mismatch. The
guard did its job, and the answer was still wrong. A second defect hid behind it: series
coverage counted on `asin`, so once rows carried the reviewer's variant it read
**0 of 10,000 with ten reviews on disk**.

### ScrapingBee: evaluated 2026-10-14, no reviews endpoint

Proposed because it bills **per request** rather than per result, implying ~47× cheaper
reviews. **It does not sell review access, so the comparison is void.** From its own API
documentation, not its marketing page:

> We provide three endpoints: Search (`/api/v1/amazon/search`), Product
> (`/api/v1/amazon/product`), Pricing (`/api/v1/amazon/pricing`).

There is no reviews endpoint. The product endpoint's **own documented response example**
carries `"reviews": []` with `"reviews_count": 114` and
`"rating_stars_distribution": null` — the count, not the reviews. Its `sort_by`
(`most_recent`, `average_review`, …) sorts **search results**, i.e. products, not reviews.
The marketing page's "Customer reviews" bullet is not backed by an endpoint.

One qualifier, in its favour: the docs say to disable `light_request` "if you're looking
to get reviews or other dynamic content", so `reviews` may populate with full JS
rendering. That would be the **product page's** handful — the same 8–13 public reviews
`/dp/` already serves, which Route A gets for nothing. It is not paginated review access
and cannot reach the ~100 per filter that the depth measurements depend on.

The remaining path would be its generic HTML API against `/product-reviews/` URLs, which
raises three problems and solves none of the important ones:

1. **The login wall.** Anonymous requests to `/product-reviews/` redirect to sign-in with
   zero review blocks. Beating that through a third-party proxy means **sending the
   user's Amazon session cookies to that third party** — which is not a trade to make for
   a cheaper price per review, and is not something to set up on the user's behalf.
2. **You own the parser.** Apify's actor parses and its author fixes the selectors when
   Amazon changes its markup. This collector has already been bitten twice by
   *field-name* mismatches on actors that did the parsing (`Verified` vs
   `VerifiedPurchase`; capitalised keys returning zero rows). Owning the selectors means
   owning that failure permanently, against a site that changes markup deliberately to
   break scrapers.
3. **It buys breadth, never depth.** The 10-page-per-filter cap is Amazon's. A cheaper
   request price buys *more products*, and nothing about the velocity band or the
   ~100-per-filter ceiling moves.

**Not tested, and not testable here:** whether it defeats the bot challenge. Evaluating
that needs an account, and signing up for one is the user's decision, not this
assistant's. Given that there is no reviews endpoint, the question is moot regardless.

Route B's sort is **not trusted from the documentation** — providers advertise sort
options that do not always arrive. The observed sort is read back off the response where
the provider returns one, and the same date-monotonicity check is applied to what comes
back. Rainforest's outage is the same lesson one level up: the endpoint the docs describe
may not exist today.

Route B has one provenance advantage worth stating: its response arrives over HTTP into
the process, so the model never touches the text and there is nothing to hash-verify.

---

## Route C — the brand's own review platform

Added 2026-10-17. **This is the route that solves depth**, and it solves it by not being
on Amazon at all.

Everything above establishes that Amazon caps pagination at ~10 pages of ~10 **per filter
combination**, so ~100 reviews is the ceiling per pass however much is spent, and reaching
2024 needs a product averaging ≤0.10 reviews/day — which is by construction too slow to
fill its own quarters. That conflict is Amazon's, not a provider's. A brand that pays a
vendor to host its reviews has no such cap.

### What the route is, precisely

A brand pays Bazaarvoice to host and **render** its reviews, and the renderer is
JavaScript running in every visitor's browser. That JavaScript needs a display key, so
the brand's own CDN publishes one, in the clear, to everybody. This route reads the same
key from the same public file and calls the same documented display endpoint the widget
calls.

```
GET apps.bazaarvoice.com/deployments/<client>/main_site/production/en_US/bv.js
  -> legacyScoutUrl: display.ugc.bazaarvoice.com/static/<Client>/main_site/en_US/bvapi.js
  -> that file publishes passkey:"ca..."
GET api.bazaarvoice.com/data/reviews.json?apiversion=5.4&passkey=<key>
    &Filter=ProductId:eq:<id>&Limit=100&Offset=<n>&Sort=SubmissionTime:desc
```

`Limit` caps at 100. **`Offset` does not cap** — it paginates to exhaustion.

**No account is created. No key of ours is used. No credential, no cookie, nothing logged
into. There is no bot challenge on this surface, so nothing is solved or evaded** (§9).

**Ignore `b8d57893b7754e2d390348dc172b7c76`.** It is Bazaarvoice's own key and appears in
every scout file; a route that accepted it would query the wrong deployment with full
confidence.

### Two things that will cost you if you skip them

**Accept gzip.** Bazaarvoice gzips some deployment zones regardless of what
`Accept-Encoding` asked for, and a gzip body decoded as text is a binary blob in which
every regex above silently finds nothing. That produced **five false negatives** in the
2026-10-15 survey — Monster, Red Bull, Owala and Simply Good Foods were all written off as
running no deployment when all four publish a key, and **Red Bull turned out to be among
the best samples available**. A regex finding nothing is evidence only once the body is
known to be text.

**`apps.bazaarvoice.com/bfd/v1/...` returns 401. Leave it alone.** It would answer a
`Referer` or `Origin` header forged to look like the brand's own site, and forging one to
get past a refusal is exactly what §5 forbids: a block is a finding, recorded and not
routed around. The scout route is a different endpoint that does not refuse in the first
place. A self-test asserts neither header string appears anywhere in the collector.

### Measured on Traeger (NYSE:COOK), 2026-10-17

`client: traegerservice`, product `TFB57GLE` (Pro 575), one pass:

| | Route A (session) | Route B (apify, best) | **Route C (bazaarvoice)** |
|---|---|---|---|
| returned | 8 | 100 | **1,183** |
| span | — | 20 days | **7.6 years** (2019-03-20 → 2026-10-11) |
| pages | dead at 1 | 10, then Amazon's cap | **13, to exhaustion** |
| dates descending | no, 4 inversions | yes | **yes, 0 inversions** |
| carrying text | — | — | **97.4%** (32 star-only dropped) |
| claimed basis | ratings | reviews (likely a display cap) | **reviews, 1,215** |
| coverage | 0.09% | 1.0% | **97.4%** |
| newest review | 47 days | 2 days | **6 days** |
| account | the user's own | a provider's | **none** |
| cost | — | $0.63 | **$0.00** |

### The family, and why it is `parent_asin` again

**Bazaarvoice pools across a product family exactly as Amazon pools across variants.**
`TFB57GLE` (Pro 575) and `TFB78GLE` (Pro 780) are different grills at different prices
and return **identical 1,215-review id sets**. `TFB89BLF` and `TFB65BLF` likewise. Nine
products measured resolved to **seven distinct families**.

So the join key is the **family**, never the declared `product_id` — and as on Amazon,
**dedup by review id does not catch the mislabelling**, because those are genuine distinct
reviews of a wider product. The first row of the committed Traeger file says *"I was able
to purchase the 780 pro"* and is filed under the declared 575. The pooling is visible in
the data itself, the same way one Hydro Flask batch carried `Style: 20 Oz`, `40 Oz` and
`64 Oz`.

**Determine the family by comparing returned id sets.** Not by the product ids differing —
they do differ, and they are one family. Not by summing `TotalReviewCount`, which
double-counts a pooled family and **overstated Traeger's unique count by 30%**. The group
file declares the family and the collector verifies it against the ids, refusing on a
mismatch; a singleton family is reported **unverified**, never verified.

**One product cannot carry a series.** The Pro 575 family clears the 30-review quarter
floor in only **11 of 32** quarters, because review flow decays as a model ages. Seven
families together clear it in **30 of 44**, with a 28-quarter consecutive run. On this
route **product coverage, not depth per product, is the binding constraint** — which is
the opposite of Amazon, where depth per pass is the wall.

### Depth is not liveness

**Owala serves 14,029 reviews flawlessly and its newest is 2022-07-28** — 1,540 days
stale. Every depth, coverage and sort field on such a batch reads healthy, and a cadence
pointed at it would collect nothing new for ever without a single number moving. So
`newest_review_age_days` is recorded per batch, and past 180 days the collector warns.

It **warns and never refuses**: a stale deployment's old reviews are still real reviews,
and dropping them would be worse than labelling them.

### What the live run caught that the fixtures could not

**`verified_purchase` was going to read 0% for a 61%-verified pool.** Traeger carries no
`IsVerifiedPurchaser` on any of its 1,215 rows; the information lives in
`Badges.verifiedPurchaser`, present on 744 of them with a badge **object** as its value,
so presence is the whole semantics. This is the Apify `Verified` / `VerifiedPurchase`
trap one platform along, and it is the dangerous shape — **0% verified is entirely
believable** and would have reached the verified-share check as evidence about Traeger's
customers rather than about the parser. Caught by diffing the parsed CSV against the
cached raw responses.

Reading the same badges turned up **`incentivizedReview` on 179 of 1,215 (14.7%)**. A
brand that solicited a review with an incentive sampled a different population and tone
is precisely what an incentive moves, so it is recorded per review — like
`syndicated`, and not filtered, because whether to include them is an aggregation
decision.

Also: `--limit` defaulted to 10, inherited from the Amazon routes where that is about
what a page serves. On a route whose whole point is that Offset is uncapped, that quietly
turned a 1,215-review family into the same thin slice Amazon gives for free. The brand
route defaults to **0, meaning walk to exhaustion**.

### Re-parsing, and the cache

```bash
python3 $S brand --group <g> --product-id <ID> --from-cache 20261017T142638Z
```

Raw responses are written to `_raw/<platform>-<stamp>/` per batch. The Apify rule applies
(never re-run a provider to fix a parser) and this route adds a reason of its own: **the
pool is live, so a re-fetch is not a re-read — it is a different batch wearing this one's
id.** The cache is the only thing that can ever be re-parsed into the batch that was
actually written.

A re-parse makes **no network call**, so it verifies no family and says so in its own
words rather than reporting a failed probe: "could not be checked here" and "the sibling
was unreachable" are different findings.

**Unsettled:** the cache is 4.6× `reviews.csv` (2.7 MB against 588 KB for one product). It
is committed on the reasoning `.gitignore` already gives for `reviews.csv` — live
collection is the only copy there will ever be — but a weekly cadence across a real group
needs a retention policy and there is none.

### What this route is not

**It sees the brand's own site, which is a different sales channel from Amazon**, with a
different customer. A brand-route series and an Amazon series are **two instruments**, and
`route` is a per-review column so that a reading which pooled them is visible rather than
silent. Pooling them without saying so would read channel mix as tone — the
Monster/Celsius failure in CONTRACTS.md §1 wearing different clothes.

And it is not a reason to retire Route A or B. A company with no brand-platform
deployment is still only reachable on Amazon, and the gap between "structurally eligible"
and "has usable review data" is a recorded finding either way (CORE-PROMPT §2).

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
a stable hash and recording per row which scheme was used. Route C uses Bazaarvoice's own
`Id` (scheme `bazaarvoice_review_id`); a row arriving without one is **dropped and
counted** rather than given a synthetic id, because this route re-walks the whole family
every cadence and a row that cannot be deduped would be re-added for ever.

That re-walk is why dedup matters more here than on Amazon: a brand pass returns the
**entire** pool, not a slice, so a naive append would double the corpus on the second run
and then kill the scorer, which treats one `review_id` keying two different rows as a hard
failure. Verified: a second walk of Traeger's pool recorded 1,183 duplicates and 0 new.

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

## Refusal-site audit (CONTRACTS.md §5)

```bash
python3 .claude/skills/review-tone-signal/scripts/refusal_audit.py
```

§5 forbids shipping a guard that cannot fail, and that cannot be enforced by
reading the code: **a check that always passes is indistinguishable from a check that
passes.** So it is audited mechanically. The audit parses this collector for its own
`die(...)` sites, runs the self-test suite with `COLLECT_REFUSAL_LOG` set so every
refusal records the line it fired from — in subprocesses too, which is where most of the
interesting ones live — and asserts each site fired at least once.

As of 2026-10-17: **36 of 36 covered**, 163 self-test checks, `MIN_SITES` raised to 30.
The brand route added nine refusal sites and two of them are the §5 shape — written,
plausible, and unreachable from any real collection:

- the **FAMILY MISMATCH** refusal only fires when two declared siblings do *not* share a
  pool. Every real Traeger family does share one, so nothing in a live collection can
  reach it. Tripped with disjoint id sets from a fake route.
- **`--require-recency` on the brand route** needs an *ascending* brand batch, and the
  descending fixture cannot produce one — exactly how the Amazon `--require-recency`
  guard sat unreachable against a single-review fixture until a two-review ascending one
  was built for it.

Verified end to end by deleting the FAMILY MISMATCH test with its guard left in place:
the audit drops to 35 of 36 and names the site.

**First run: 0 of 26 covered**, which was the audit catching itself. The instrumentation
walked the stack to the wrong frame and recorded `die()`'s own line for every refusal:
one distinct "site" for all 26, reported with the full confidence of a working tool. The
fix is in `_record_refusal`, and it is the reason control 3 below exists.

**Second run: 10 of 26.** What the remaining 16 turned up:

- **`plan_product` was dead code** — left behind when the collection plan was replaced by
  group files, never called, its refusal unreachable. Deleted rather than tested.
- Two guards duplicated checks `argparse` already makes (`--cadence`, `--provider`
  choices), so nothing reaching them through the CLI could trip them. Kept, because
  argparse only protects the CLI path, and made reachable by calling the command
  functions directly in the suite.
- **`--require-recency` could not fire against the single-review fixture**, because one
  review cannot be out of date order. It needed a two-review ascending batch. This is the
  shape §5 is about: the guard was written, looked tested, and had never once run.

The audit carries its own negative controls, run every time rather than remembered, and
all three are verified by deliberately breaking them:

| control | what it catches | verified by |
|---|---|---|
| floor on site count | a parser that stopped finding refusals and reports full coverage of nothing | raising `MIN_SITES` to 999 → refuses |
| a probe that cannot be in the output must read *uncovered* | a matcher that says yes to everything | built in |
| a probe taken from the output must read *covered* | a matcher that says no to everything | built in |

And end to end: deleting the `--require-recency` test while leaving its guard in place
drops the audit to 24 of 25 and names the site. Neutering a guard instead fails the
self-test suite first, which is the right order — the suite is the defence, the audit is
the backstop for guards nothing tests.

**What it does not establish.** Not that any refusal is *correct*, and not line coverage:
a site can fire for a reason other than the intended one and this still passes. It rules
out one failure — a guard that cannot fire at all — which has now appeared three times on
this project, plus `plan_product` here.

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
