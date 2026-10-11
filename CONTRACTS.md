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
        "share_of_revenue": 0.92,        // null if not disclosed
        "evidence": "FY2024 10-K, revenue by product category",
        "source_url": "https://www.sec.gov/..."
      },
      "products": [
        {"asin": "B0...", "marketplace": "amazon.com", "name": "Roomba j7+"}
      ],
      "reporting": {"calendar": "quarterly", "currency": "USD",
                    "source": "SEC"}     // SEC | companies-house | EDINET | none
    }
  ]
}
```

Rules this encodes (CORE-PROMPT §2, PLAN.md concentration filter):

- `declared_at` is set **before any review is collected** for the group.
- `concentration.evidence` cites the company's own filing. A member with no evidence is
  not a member.
- `venue: "private"` is valid and useful — a private competitor is a category control.
- At least two members, or `members[0]` is explicitly flagged as having no peer.
- Revising a group means a **new file with a new hash**, never an edit in place.

## 2. Collector → scorer

`data/<group_id>/<company_slug>/reviews.csv`

Columns, extending the existing contract in
`.claude/skills/review-tone-signal/references/data-contracts.md`:

| column | type | notes |
|---|---|---|
| `review_id` | str | Amazon's own review id where available, else a stable hash |
| `asin` | str | |
| `marketplace` | str | `amazon.com`, `amazon.de`, … |
| `review_date` | date | **as displayed by Amazon** — never inferred, never filled |
| `rating` | float | retained but **withheld from the scorer** |
| `title` | str | |
| `text` | str | verbatim. Never paraphrased, summarised or generated (§1.3) |
| `verified_purchase` | bool | |
| `helpful_vote` | int | |
| `batch_id` | str | joins to the batch record below |

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
  "sort": "recent",                      // "recent" | "helpful" | "<as returned>"
  "filters": {"star": 5},                // whatever narrowed the request
  "asin": "B0...", "marketplace": "amazon.com",
  "requested": 100, "returned": 87,
  "claimed_total": 14203,                // what the product page says exists
  "coverage_ratio": 0.0061,              // returned / claimed_total
  "blocked": false, "block_reason": null // a refusal is recorded, never routed around
}
```

`coverage_ratio` travels with every tone number downstream (CORE-PROMPT §4). A tone mean
over 0.6% of a product's reviews is not that product's tone and must never be rendered
as if it were.

## 4. Workflow stages

The runner walks these in order and **stops at the first one that is not satisfied**. It
never invents a stage's output and never skips a stage that is missing.

| Stage | Does | Satisfied when |
|---|---|---|
| `G` | declare peer groups | ≥1 group file, hash verified, every member has concentration evidence |
| `C` | collect reviews | every tracked ASIN has a batch within the declared cadence |
| `K` | calibrate the scorer | `data/calibration.json` exists, agreement recorded — **human step** |
| `S` | score tone | `scores.csv` for every company with reviews |
| `A` | aggregate | tone per company per period, plus peer-relative |
| `D` | render | dashboard per group |

**Every stage is invoked as `script --group <group_id> [--company <slug>]`.** No stage
reads a path it was not given. This single line is what was wrong last time.

Exit codes: `0` ran or reported a clean stop · `2` an integrity violation, needs a human
· `3` a stage is not built yet.

## 5. What no component may do

From CORE-PROMPT §1, repeated here because these are the ones broken by accident:

- Compute a tone number anywhere except the scorer, or an aggregate anywhere except the
  aggregator. Not in the runner, not in the dashboard, not in prose.
- Write a review that Amazon did not serve.
- Record a batch without its provenance.
- Defeat, solve or route around a block. A refusal is recorded and the stage stops.
