# Interface contracts

What each component hands the next one. **Settled here, before the workflow is wired**,
because the alternative has already cost a day on this project: six components were
built in parallel against a loosely-described interface and none of them could be
invoked — four argument mismatches plus a fifth that made a gate permanently
unreachable, all found only when something tried to run them together.

A component satisfies this file or the workflow cannot call it.

---

## 1. Peer-group finder → everything downstream

`groups/<group_id>.json`

```jsonc
{
  "group_id": "robot-vacuums",
  "declared_at": "2026-10-12",          // date, never a wall-clock during collection
  "seed": "iRobot",                      // what the group was grown from
  "hash": "<sha256 of this file minus the hash field>",
  "members": [
    {
      "company": "iRobot Corporation",
      "venue": "NASDAQ:IRBT",            // or "private"
      "brands": ["Roomba", "Braava"],
      "concentration": {
        "dominant_line": "robot vacuums and mops",
        "share_of_revenue": 0.92,        // null when the filing does not split it
        "evidence": "FY2024 10-K, revenue by product category",
        "source_url": "https://www.sec.gov/...",
        "no_public_report": false,       // true only for a private member
        "revenue_readable": true         // false whenever no_public_report
      },
      "products": [
        {"route": "amazon",              // amazon | bazaarvoice | yotpo | powerreviews
         "asin": "B0...",                // the variant actually sold
         "parent_asin": "B0...",         // REQUIRED on amazon - where reviews are served
         "marketplace": "amazon.com", "name": "Roomba j7+"},
        {"route": "bazaarvoice",         // a brand's own review platform
         "client": "traegerservice",     // the deployment zone
         "product_id": "TFB57GLE",       // ONE representative per review FAMILY
         "name": "Pro 575"}
      ],
      "reporting": {"calendar": "quarterly", "currency": "USD",
                    "fy_end": "1231",    // MMDD; "0102" for a 52/53-week year.
                                         // EMPTY when no_public_report is true
                    "source": "SEC"}
      // source: SEC | companies-house | EDINET | TDnet | SIX | DART
      //       | other-national | none
    }
  ]
}
```

Rules this encodes (CORE-PROMPT §2, PLAN.md concentration filter):

- `declared_at` is set **before any review is collected** for the group.
- `concentration.evidence` cites the company's own filing, for **every member whose
  revenue will be read**. A private member sets `no_public_report: true`, which forces
  `share_of_revenue: null` and `revenue_readable: false` — but still has to say what it
  sells and where that was established. A listed member is never exempt.
  (The earlier wording, "a member with no evidence is not a member", deleted every
  private member and so contradicted the line below it. Concentration exists so tone can
  be joined to *revenue*; a private member has no revenue to join and is there to tell a
  category-wide move from a company-specific one.)
- `venue: "private"` is valid and useful — a private competitor is a category control.
- At least two members, or `members[0]` is explicitly flagged as having no peer.
- Revising a group means a **new file with a new hash**, never an edit in place.
- **`reviews_per_day` is recorded per product, and it decides what the product can be
  used for.** Amazon caps depth at ~100 reviews per pass, so velocity alone determines
  how many days those 100 buy. Measured 2026-10-14:

  | product | reviews/day | 100 reviews span | coverage |
  |---|---|---|---|
  | YETI Rambler (flagship) | 5.00 | 20 days, all 2026 | 1.0% |
  | C&Berg tumbler lids (slow) | 0.0987 | **1,013 days — 2023-12 to 2026-10** | 18.0% |

  **Reaching 2024-01-01 in one pass requires ≤ ~0.10 reviews/day.** A flagship product
  reaches back three weeks; a slow one reaches back three years.

  **Depth is not capped at 100 — that was wrong when first written here.** The ~100 is
  per *filter combination*, and each star rating gets its own 10-page window. A rare
  rating reaches much further back at the same depth: on C&Berg, `all_stars` returned
  100 reviews over 2.8 years for $0.63 while `one_star` returned 49 over **6.0 years
  for $0.03**. The union was 143 reviews across 7.2 years for $0.66.

  **The quarter floor is on the COMPANY-quarter, not the product.** `aggregate.py`
  keys its buckets on the quarter alone and pools every ASIN in a company's
  `scores.csv` — which is why the within-ASIN/mix decomposition exists at all. So no
  single slow product clearing 30 reviews in a quarter is not a structural barrier; it
  is an argument for collecting several products per company.

  For a window `D` days deep: a pass reaches back `100/v` days at `v` reviews/day, so
  reaching `D` needs `v ≤ 100/D`; each such product contributes `v × 91.3` reviews per
  quarter, and clearing a floor of 30 needs `N ≥ 30 / (91.3 × 100/D)`. For
  2024-01-01 → today that is **≤ 0.098 reviews/day and N ≥ 4 products**.

  **The screen is a velocity BAND, not a minimum — "find slow products" was wrong.**
  Measured 2026-10-14 across three YETI sub-lines and C&Berg:

  | product | reviews in window | per quarter | vs ideal | products needed |
  |---|---|---|---|---|
  | Tundra 105 | 8 | 0.7 | 0.08× | 42 |
  | TANK 45 | 10 | 0.9 | 0.10× | 34 |
  | Tundra 110 | 12 | 1.1 | 0.12× | 28 |
  | **C&Berg lids** | **99** | **8.9** | **0.99×** | **4** |

  The ideal is a *specific rate*, `v ≈ 100/D` — for the 2024 window, **0.098/day**,
  ~9 reviews per quarter, ~100 inside the window. Both edges bite:

  > **too fast** → the 100-review pass cannot reach back far enough
  > **too slow** → the quarters inside the window are empty

  **§1 screens for velocity within ~0.5×–1.0× of `100/D`** — products accumulating
  **~50–100 reviews inside the target window**. This is computable for free from a
  listing's ratings count and age; no collection is needed to apply it.

  **Lumpiness: measured low, so pooling smooths.** Across the 45 quarters the three
  YETI products jointly cover, pairwise correlation ran **r = +0.07 to +0.23** — quiet
  quarters do not coincide. At band velocity with that independence, **4 products per
  company** clears the 30-review floor.

  **The caveat, stated because the 4 depends on it:** that correlation was measured on
  products ~10× below the band, where quarters hold 0–3 reviews and `r` cannot be pinned
  tightly. It **points to** independence; it does not prove it. A clean test needs two
  or three products near 0.10/day, and C&Berg is the only one found so far. **The risk
  is therefore not the pooling — it is picking products outside the band**, which costs
  28–42 products instead of 4. That is a screening failure, and screening is free.
### Amazon presence is a membership criterion, not a detail

**Amazon is the only sensor this project has.** A company Amazon barely sees cannot be
measured at all, and a group that mixes a company Amazon sees well with one it barely
sees is not comparing two companies — it is comparing a core channel against a marginal
one and calling the difference tone.

The case that forced this: in the declared `energy-drinks` group, **Celsius's flagship
carries 28,121 ratings and the largest Monster listing findable carries 553** — a 50×
gap. Monster sells through convenience retail. No amount of collection fixes that, and a
peer-relative reading across the two would be an artefact of channel mix.

Every member therefore records:

```jsonc
"amazon_presence": {
  "total_ratings": 28121,          // summed across the products tracked
  "reviews_per_day": 5.0,          // from a pass, or estimated from ratings + listing age
  "channel_evidence": "FY2024 10-K: e-commerce 22% of revenue",  // or null
  "material": true                 // false disqualifies the member
}
```

Three distinct failures, and all three disqualify:

1. **Absolute** — too few reviews to clear the floors however many products are tracked.
2. **Relative to the company** — Amazon is a marginal channel for it, so its Amazon tone
   is not evidence about its revenue even when the counts are sufficient.
3. **Asymmetric within the group** — the comparison itself is invalid, which is the
   Monster/Celsius failure and the one most easily missed, because each member can look
   acceptable alone.

**A group is refused when a member cannot clear the per-quarter review floor across the
window being compared.** Raw ratio is the wrong test and the 10× rule below misfires:

> Amended 2026-10-15, and I am amending a rule in a way that lets a group I wanted to
> declare pass, so the reasoning is stated rather than assumed. Traeger carries ~140
> text reviews per quarter and Weber ~2,286 — a 16× ratio that the old rule refuses. But
> **a tone mean is an average, not a sum.** Asymmetric n changes the *width* of the
> interval, not the validity of the estimate; both clear the 30-review floor, by 5× and
> 76×. The case the rule was written for is different in kind: Monster's 553 ratings in
> total could not fill a single quarter, so it was not a noisier measurement, it was
> **no measurement**. Measurability is the thing that matters, and floor-clearance tests
> it directly.
>
> The ratio test is kept below as a **screening heuristic** — a large gap is a reason to
> check floor-clearance, not a reason to refuse on its own.

**Screening heuristic: a gap of more than 10× in `total_ratings` requires the
floor-clearance check to be run and recorded.** That factor is **chosen, not measured** — recorded here as the second
most arguable constant in the project after the 0.60 concentration floor. It should be
revisited once several groups have been declared against it. 50× is plainly too much;
where the real line sits is not known.

A private member is held to the same standard. It has no revenue to read, but it is a
category control, and a control Amazon cannot see controls for nothing.

- `fy_end` is required **for every member whose revenue will be read**, and must be
  **empty** for one with `no_public_report: true`. CORE-PROMPT §5 forbids aligning two
  companies' figures as if their calendars matched, so a readable member needs it — but
  nothing publishes a year end for Bose or Stanley/PMI, and writing a plausible-looking
  `1231` for them would be inventing provenance, the same class of error as inventing a
  revenue share. Measured across six declared groups: `1231`, `0102`, `1003`, `0930`,
  `0826`, `0331` — five of six are not 31 December and two are 52/53-week years that
  move annually.

**Brand platforms pool across a product family exactly as Amazon pools across variants.**
Measured on Traeger 2026-10-15: `TFB57GLE` (Pro 575) and `TFB78GLE` (Pro 780) are
different grills at different prices and return **identical 1,215-review id sets**;
`TFB89BLF` and `TFB65BLF` likewise. Nine products measured resolved to **seven distinct
families**. Declare one representative per family and verify the grouping by **comparing
returned id sets pairwise** — never infer it from the product id differing, and never sum
`TotalReviewCount` across products, which overstated the unique count by 30%.

**One product cannot carry a series.** Traeger's Pro 575 has 7.6 years at 97% text and
clears the floor in only **11 of 32** quarters, because review flow decays as a model
ages. Seven families together clear it in **30 of 44**, with a 28-quarter consecutive run.
Product *coverage*, not depth per product, is the binding constraint on this route.

**Depth and liveness are separate questions.** Owala serves 14,029 reviews flawlessly and
its newest is **2022-07-28** — 1,540 days stale. Record the newest review's age alongside
the count; a deployment can be deep and dead.

### `parent_asin`, and why it is required

**Amazon serves reviews at the parent ASIN, pooled across every variant** — colour, size,
pack count. Of fifteen products declared in the first real pass, **fourteen were variant
children**: Premier Protein chocolate 12-pack `B07MJL8NXR` resolves to parent
`B0BLGMF97X`, which pools every Premier Protein flavour and pack size.

Three consequences that have to be understood rather than worked around:

1. A `reviews.csv` keyed on `asin` alone holds the **parent's** reviews filed under a
   child's id, and dedup by `review_id` will not notice, because the ids are genuinely
   distinct reviews.
2. "Product-level tone" is really **variation-family-level tone**. Say so; do not label
   a chart with the child product's name as though the reviews were of that variant.
3. Choosing a product for continuity does **not** buy continuity of the review pool,
   because Amazon re-parents variation families over time.

A group where two members track children of the **same parent** is refused: they would
be handed identical reviews and compared against each other.

**Re-resolution, not just declaration.** That refusal happens at declaration time, but
re-parenting happens afterwards. Two members declared under distinct parents can
converge later with **no id anywhere changing**, and nothing currently rechecks. Stage G
therefore re-resolves every declared parent on a cadence, not only when a group is
first written, and a convergence found later is the same refusal as one found at
declaration.

### What pooling looks like in one real batch

Measured 2026-10-12, a single batch collected under "Hydro Flask 32 oz Wide Mouth":

- three of eight reviews were tagged `Style: 20 Oz`, `Style: 40 Oz` and `Style: 64 Oz` —
  a different bottle
- reviews came from Australia, Spain, Singapore, Canada and Italy

**Both pooling axes, one product, one page.** This is why `parent_asin` and
`review_country` are columns and not notes.

## 2. Collector → scorer

`data/<group_id>/<company_slug>/reviews.csv`

Columns, extending the existing contract in
`.claude/skills/review-tone-signal/references/data-contracts.md`:

| column | type | notes |
|---|---|---|
| `review_id` | str | Amazon's own review id where available, else a stable hash |
| `asin` | str | the declared variant |
| `parent_asin` | str | **where the reviews actually came from** (§1). Join on this |
| `marketplace` | str | `amazon.com`, `amazon.de`, … |
| `review_country` | str | `/dp/` pools international reviews; a US revenue join needs this |
| `review_date` | date | **as displayed by Amazon** — never inferred, never filled |
| `rating` | float | retained but **withheld from the scorer** |
| `title` | str | |
| `text` | str | verbatim. Never paraphrased, summarised or generated (§1.3) |
| `verified_purchase` | bool | |
| `helpful_vote` | int | |
| `batch_id` | str | joins to the batch record below |

**The parent is captured from the page and verified — never copied from the group file.**
A declared parent that no longer matches what Amazon serves **refuses the batch**. The
repair belongs in stage G, and the group file must not simply be edited to match: two
members may have converged on one parent, in which case the group no longer holds. A
group file that was right when written can stop being right silently, and trusting §1
would make that undetectable.

**`/dp/<ASIN>` is the only defensible Route A surface.** The `/product-reviews/` page
does not expose `parentAsin` at all, so on that surface the parent can only be asserted,
never confirmed; `/dp/` exposes it and returns 13 reviews rather than 8. The two surfaces
render identical reviews with **different paragraph whitespace**, so a collector must
never mix them — the same review would look edited.

`review_date` carries no `fiscal_quarter` column. Fresh collection is not organised on a
company's fiscal calendar; aggregation assigns periods, and doing it here would bake one
company's calendar into another's data.

## 3. Every collection batch

`data/<group_id>/<company_slug>/batches.jsonl` — append-only, one line per batch.

```jsonc
{
  "batch_id": "...",
  "collected_at": "2026-10-12T08:00:00Z",
  "route": "session",                    // "session" (logged in) | "<provider name>"
  "filters": {"star": 5},                // whatever narrowed the request
  "asin": "B0...", "marketplace": "amazon.com",
  "requested": 100, "returned": 87,

  "claimed_total": 8672,                 // null when the page gives neither
  "claimed_total_basis": "ratings",      // "reviews" | "ratings" | "unavailable"
  "coverage_ratio": 0.0061,              // returned / claimed_total

  "sort_requested": "recent",            // what was ASKED for
  "sort_verified": false,                // true | false | null (no dates to test)
  "date_order": {"monotonic_descending": false, "inversions": 4,
                 "oldest": "2022-12-20", "newest": "2026-08-26"},
  "sort_produces_series": false,         // false whenever sort_verified is false

  "blocked": false, "block_reason": null // a refusal is recorded, never routed around
}
```

**`claimed_total_basis` is not optional.** Amazon often publishes only a *ratings*
count ("8,672 global ratings"), and ratings include star-only entries with no text that
this pipeline can never sample. Dividing by ratings systematically understates coverage
and is not comparable across products or marketplaces — which is exactly what a
peer-relative reading needs. Record which number you got.

**There is no `sort` field, because the page does not support the claim.** Measured on
amazon.com signed in, 2026-10-12: `/product-reviews/<asin>/?sortBy=recent` renders no
sort control, the parameter is ignored, returned dates are not in date order, and
`pageNumber=2` returns the same 8 reviews as page 1. So a sort "as returned" is
unreadable and a sort taken from the request is a claim about Amazon that is false.
`sort_verified` is therefore **derived from the collected dates** — the only evidence
that cannot be mislabelled.

**A per-period count is a property of which filters were run, not of the product.**
Outside an `all_stars` pass's reach, only the filters actually requested contribute. On
C&Berg, `all_stars` returned 0 reviews for 2019–2022 and `one_star` returned 30 — the
pre-2024 history looks sparse only because one-star reviews were the only ones reaching
there. **Any distribution read off collected data must say which side of the `all_stars`
boundary it sits on**, or it reports the sampling as the product's history.

**Never re-run a provider to fix a parser.** Apify charges per result *produced*, not
per read, so `--dataset-id` re-parses a stored run for $0. Two real misses, and the
second is the dangerous one:

- a 100-review run parsed to **zero rows** because the actor capitalises its field
  names. An empty result announces itself.
- a run recorded **0% verified purchases** because the field is `Verified`, not
  `VerifiedPurchase`. Re-reading gave **100%**. A plausible wrong number does not
  announce itself — 0% verified is entirely believable, and it would have reached the
  verified-share check and been read as evidence about the *review population* rather
  than about the parser.

The second is what this rule actually protects against.

`coverage_ratio` travels with every tone number downstream (CORE-PROMPT §4). A tone mean
over 0.6% of a product's reviews is not that product's tone and must never be rendered
as if it were.

## 4. Workflow stages

The runner walks these in order and **stops at the first one that is not satisfied**. It
never invents a stage's output and never skips a stage that is missing.

| Stage | Does | Satisfied when |
|---|---|---|
| `G` | declare peer groups | ≥1 group file, hash verified, every member whose revenue will be read has concentration evidence, **and every declared parent re-resolved within the cadence with nothing found** |
| `C` | collect reviews | every tracked ASIN has a batch within the cadence declared in `collection-plan.json` |
| `K` | calibrate the scorer | `data/calibration.json` exists, agreement recorded — **human step** |
| `S` | score tone | `scores.csv` for every company with reviews |
| `A` | aggregate | tone per company per period, plus peer-relative |
| `D` | render | dashboard per group |

**Every stage is invoked as `script --group <group_id> [--company <slug>]`.** No stage
reads a path it was not given. This single line is what was wrong last time.

Exit codes: `0` ran or reported a clean stop · `2` an integrity violation, needs a human
· `3` a stage is not built yet.

**Re-resolution is a verb; the gate only reads its log.** `reresolve` does the network
work and appends what it saw; stage G reads that log and **makes no network call**. A
gate the runner evaluates on every pass must not itself be fifteen requests the
marketplace can block, or the guard becomes a second point of failure standing in front
of the thing it guards. Stage G refuses when the sweep is missing, stale, incomplete, of
an older group version, or found something — so **"never checked" and "checked and
clean" can never look alike**, and a blocked sweep reports parents as *unchecked* rather
than passing them.

**Stage C being satisfied does not mean the data supports a trend.** They are different
conditions and the runner must never conflate them: `C` asks whether collection ran,
while a trend claim additionally needs `sort_produces_series: true` across the batches
behind it. A run can legitimately pass `C`, render a dashboard, and still have nothing
that may be read as a time series. The dashboard says so on its face.

**Cadence lives in `collection-plan.json`**, not in the group file: the same peer group
can be collected on different cadences, so cadence is a property of a collection
campaign. It records the cadence, its declared date, and an append-only log of changes,
each with a reason. Changing the cadence changes the instrument.

## 5. What no component may do

From CORE-PROMPT §1, repeated here because these are the ones broken by accident:

- Compute a tone number anywhere except the scorer, or an aggregate anywhere except the
  aggregator. Not in the runner, not in the dashboard, not in prose.
- Write a review that Amazon did not serve.
- Record a batch without its provenance.
- Defeat, solve or route around a block. A refusal is recorded and the stage stops.
- **Resolve a company by ticker symbol without its exchange.**
- **Ship a guard that cannot fail.** This project has now produced three, and they are
  the same bug wearing different clothes: a calibration gate reading a file the scorer
  never wrote, so it could never be met however good the measurement; a window module
  that restricted nothing while keeping the interface of one that did; and a test
  asserting `a == b if hasattr(obj, "f") else True` where `f` did not exist, so it
  passed without testing anything. **A check that always passes is indistinguishable
  from a check that passes.** Every guard needs a case that demonstrably trips it, and
  every mechanical check needs its awkward *near-misses* tested too — a verifier that
  fires on `CHURCH & DWIGHT CO INC /DE/` vs `Church & Dwight Co., Inc. (Waterpik)` gets
  switched off, and then it guards nothing at all.

**This rule cannot be enforced by reading, so it is audited mechanically.** Pointed at
the peer-group component, the audit found **24 of 70 refusal sites had never fired in
any test**. The two that mattered were lexicons tested thoroughly at the unit level —
innocent sentences, guilty sentences, the brand exemption — and **never once through the
validator that calls them**. The lexicons could have been disconnected entirely, or
wired to the wrong field, and every lexicon test would still have passed. A measurement
that was fine and a wire that was not.

The audit parses the component for its own refusal sites, spies on whatever returns the
refusal list, runs the whole suite, and asserts each site produced a refusal at least
once. It ports in about forty lines.

**The audit is itself a check, so it carries its own negative controls**, run every time
rather than remembered: a probe that cannot be in the output must be reported
*uncovered* (catching a matcher that always says yes) and a probe taken from the output
must be reported *covered* (catching one that always says no). It also refuses to pass
below a floor of refusal sites, since a parser that stopped finding them would otherwise
report full coverage of nothing.

**What this does and does not establish.** Every refusal site having a test that trips
it is not the same as every refusal being *correct*, and it is not line coverage — a
site can fire for a reason other than the intended one and the audit is still satisfied.
What it rules out is precisely the failure that has now occurred three times on this
project: a guard that cannot fire at all.

### Why that last one is a rule and not advice

SEC's ticker index is keyed on **symbol alone**. So a lookup of `EPA:EL`
(EssilorLuxottica, Paris) returns `EL` — Estée Lauder, New York. `EPA:BB` (Société BIC)
returns `BB` — BlackBerry. Both then fetch a real 10-K and produce entirely plausible
concentration evidence, carrying a genuine `sec.gov` URL, filed under the right
company's name.

**Nothing in the output looks wrong.** The entity name SEC returns is the only tell, and
it is not read by anything that trusts the symbol. This is the worst shape a defect can
take here: a different company's revenue attributed to the company you meant, with a
real primary source beside it.

It was caught in the peer-group pool screen, where it inflated the counts by two. A
component that resolves a venue to a CIK **checks the exchange against a US set before
looking up the symbol**, and records the entity name it got back so a mismatch is
visible.

`fetch_sec.py` is clear: it takes `TICKER CIK` positionally and keys every fetch on the
**CIK**, using the ticker only as a directory name. That does not make the problem go
away — it relocates it to whoever produces the CIK, which is now the peer-group
declaration's `venue` field.
