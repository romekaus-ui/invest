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
        {"asin": "B0...",                // the variant actually sold
         "parent_asin": "B0...",         // REQUIRED - where Amazon serves the reviews
         "marketplace": "amazon.com", "name": "Roomba j7+"}
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
- `fy_end` is required **for every member whose revenue will be read**, and must be
  **empty** for one with `no_public_report: true`. CORE-PROMPT §5 forbids aligning two
  companies' figures as if their calendars matched, so a readable member needs it — but
  nothing publishes a year end for Bose or Stanley/PMI, and writing a plausible-looking
  `1231` for them would be inventing provenance, the same class of error as inventing a
  revenue share. Measured across six declared groups: `1231`, `0102`, `1003`, `0930`,
  `0826`, `0331` — five of six are not 31 December and two are 52/53-week years that
  move annually.

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

`coverage_ratio` travels with every tone number downstream (CORE-PROMPT §4). A tone mean
over 0.6% of a product's reviews is not that product's tone and must never be rendered
as if it were.

## 4. Workflow stages

The runner walks these in order and **stops at the first one that is not satisfied**. It
never invents a stage's output and never skips a stage that is missing.

| Stage | Does | Satisfied when |
|---|---|---|
| `G` | declare peer groups | ≥1 group file, hash verified, every member has concentration evidence |
| `C` | collect reviews | every tracked ASIN has a batch within the cadence declared in `collection-plan.json` |
| `K` | calibrate the scorer | `data/calibration.json` exists, agreement recorded — **human step** |
| `S` | score tone | `scores.csv` for every company with reviews |
| `A` | aggregate | tone per company per period, plus peer-relative |
| `D` | render | dashboard per group |

**Every stage is invoked as `script --group <group_id> [--company <slug>]`.** No stage
reads a path it was not given. This single line is what was wrong last time.

Exit codes: `0` ran or reported a clean stop · `2` an integrity violation, needs a human
· `3` a stage is not built yet.

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
