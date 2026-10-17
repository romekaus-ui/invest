---
name: review-tone-peer-group
description: Declare a peer group of companies selling substitutable consumer products from a seed — a company, a brand or a product category — with revenue-concentration evidence from each member's own filing, frozen before any review is collected. Use when asked to build, declare, revise, inspect or verify a peer group for the review-tone monitor, to screen a candidate pool against the concentration filter, to find who competes with a company on the shelf, or to pick the product identifiers (Amazon ASINs) a company will be tracked by. Declares and verifies only; it never collects reviews and never scores tone.
---

# Review Tone Peer Group

Turns a **seed** — a company, a brand, or a product category — into a **declared peer
group**: the set of companies selling substitutable consumer products, anywhere in the
world, with the specific products each will be tracked by.

**Read `CORE-PROMPT.md` in the project root in full before doing anything.** §2 is this
component's specification; §1 is binding on everything. Then read **`CONTRACTS.md` §1**
(the output shape the workflow reads) and **`PLAN.md`'s concentration filter** (which
decides most memberships). The rules most easily broken in the moment:

- **Declared before collection.** A peer added once tone is known is not a peer, it is a
  choice. A frozen version is never edited; a change is a new version with its reason.
- **Never select or reject a company on its performance, its price, or how promising it
  looks.** The group is about product substitutability and nothing else.
- **Products are chosen for continuity, not popularity.** An item that will still be sold
  in a year. A bestseller that is discontinued ends a series.
- **One product line must dominate the member's revenue, evidenced from its own filing.**
  This is stricter than substitutability and it is the test that usually decides. A
  member with no evidence is not a member.
- **Collect nothing here.** Fresh review collection is a separate component. This one's
  output is its input.
- **No number in prose** (§1.2). The counts, the hash and the comparability block come
  out of the script and are read from its output.

## The hard boundary

This component never produces an investment view. It is not selecting companies to hold;
it is declaring which companies' products a shopper swaps between, so that one company's
consumer tone can be read against theirs. If asked which member looks best, say what the
instrument produces and stop (§1.1).

## Running it

One script, `scripts/declare_group.py`, in a fixed order:

```bash
S=.claude/skills/review-tone-peer-group/scripts/declare_group.py
export SEC_USER_AGENT="Your Name your@email.example"

# Optional first pass for a whole pool: fetch concentration evidence for every US
# registrant in data/peer_groups/candidate_pool.json. ~0.7s per company.
python3 .claude/skills/review-tone-peer-group/scripts/screen_pool.py

python3 $S draft --group-id grills --seed-kind category --seed "outdoor barbecue grills"
# fill the members in the draft — this is the judgement, see below

python3 $S resolve-us     data/peer_groups/grills.draft.json  # CIK + calendar, US only
python3 $S concentration  data/peer_groups/grills.draft.json  # evidence from own filings
python3 $S check-products data/peer_groups/grills.draft.json  # do the ASINs resolve?
python3 $S check          data/peer_groups/grills.draft.json  # validate, freeze nothing
python3 $S declare        data/peer_groups/grills.draft.json  # freeze v1 + .hash + view
```

`declare` writes three things: the versioned **declaration of record** under
`data/peer_groups/`, its `.hash` sidecar, and the **`CONTRACTS.md` §1 view** at
`groups/<id>.json` that the workflow reads.

### Stage G, and keeping it satisfied

`CONTRACTS.md` §4 invokes every stage as `script --group <group_id>`:

```bash
python3 $S reresolve grills   # re-resolve every declared parent; run on a cadence
python3 $S --group grills     # exit 0 satisfied · 2 integrity violation · 3 not built
```

Stage G reads a group id and nothing else, and **makes no network call**. Satisfied means
a declaration exists, its hash verifies, the view matches it, every member has
concentration evidence, **and the parents were re-resolved within the last 7 days with
nothing found**.

That last clause is the one people trip on. Declaring a group is not enough to keep it
valid: Amazon re-parents variation families, so two members declared under distinct
parents can converge afterwards with no identifier changing anywhere. `reresolve` does
the network work and appends what it saw to a hash-chained log; stage G refuses when the
sweep is missing, stale, incomplete, of an older version, or found a convergence, a
re-parenting or an ended product.

`reresolve` never rewrites the declaration — a real change is a new version through
`revise`. Editing the file to match what Amazon now serves would hide the convergence.

Afterwards:

```bash
python3 $S show   soft-drinks          # current version, and whether its hash still holds
python3 $S verify                      # re-hash every version; detect an edit in place
python3 $S revise soft-drinks --from-draft <draft> --reason "..."   # v2, with a hash chain
```

`check-products` writes the observed title into each product's `verification`. Set `name`
to what the marketplace actually served — the contract compares them, which is how a
transposed ASIN gets caught. It also records `parent_asin_seen`: **reviews are served at
the parent**, and 14 of the first 15 products declared were variant children, so the
parent is the real unit of the review pool.

### Two routes, and every product says which

`CONTRACTS.md` §1 was amended on 2026-10-15 to add **`route`** per product, because Amazon
is no longer the only sensor: a brand's own review platform serves its reviews through a
display API whose key the platform's CDN publishes to every visitor's browser.

```jsonc
{"route": "amazon",
 "asin": "B0...", "parent_asin": "B0...", "marketplace": "amazon.com",
 "brand": "...", "name": "...", "continuity_reason": "...", "verification": {...}}

{"route": "bazaarvoice",
 "client": "traegerservice",      // the deployment zone
 "site": "main_site",             // optional, defaults to main_site
 "product_id": "TFB57GLE",        // ONE representative per review FAMILY
 "family": "traeger-pro-575",     // §2b's JOIN KEY - the collector pools on it
 "family_product_ids": ["TFB57GLE"],   // the ids actually compared
 "brand": "...", "name": "...", "continuity_reason": "...", "verification": {...}}
```

**`family` is `CONTRACTS.md` §2b's one join key for every route** — the parent ASIN on
Amazon, the declared label on a brand platform. The collector reads it off the group file,
so a declaration without it cannot be collected against however well it validates here.

**An absent `route` is `amazon`.** That is what keeps the six declarations frozen before
the field existed valid; a frozen file is never rewritten to add it (§1.5).

The brand route gets its **own equivalent of every Amazon guard, never an exemption**:

- **`review_family` is this route's `parent_asin` and is just as required.** The platform
  pools across a product family exactly as Amazon pools across variants: `TFB57GLE` (Pro
  575) and `TFB78GLE` (Pro 780) are different grills at different prices and return
  identical 1,215-review id sets. `check-products` establishes the family by **comparing
  the returned review-id sets pairwise** across the whole draft, and two products in one
  family are refused — inside one member and across two. Never infer it from the product
  ids differing, and never sum the reported counts, which overstated uniques by 30%.
  The fingerprint is over the **oldest** 100 ids: over the newest page it changes the
  moment a review arrives, and every live product would read as re-parented within a week.
- **`brand` and `continuity_reason` stay required**, unchanged.
- **`verification` stays required** and records what the display API establishes: the
  count, how far the family reaches **back** and **forward**, the text fraction, and
  whether dates sort descending **verified against the dates, never taken from the sort
  parameter**.
- **Liveness is its own check.** Owala serves 14,029 reviews flawlessly and its newest is
  2022-07-28 — 1,540 days stale. Depth says nothing about whether a deployment is alive. A
  deployment a year without a review must carry a `liveness_finding` in writing; it is not
  refused, because an abandoned deployment is still usable inside a window that ends
  before it went quiet.

`check-products` reuses `data/_route-tests/measure_widget_route.py` rather than carrying a
second copy of the walk. **Accept gzip**: Bazaarvoice gzips some zones regardless of
`Accept-Encoding`, and decoding one as text gives a binary blob in which every regex
silently finds nothing — five false negatives in one session. A regex finding nothing is
evidence only once the body is known to be text.

**The `apps.bazaarvoice.com/bfd/v1/` endpoint returns 401. Leave it alone** — no `Referer`,
no `Origin`, nothing. A block is a finding (§9). The scout route is a different endpoint
that does not refuse.

`yotpo` and `powerreviews` are named in the contract and **have no resolver here**. A
product on one is refused unless `--allow-unverified` is passed. "Not implemented" is the
honest answer; "assumed to work" is not.

## Where the judgement happens

The script refuses the inadmissible; it does not choose the members. Choosing them is a
research task, and it is run in a subagent on purpose:

**`.claude/agents/peer-group-proposer.md`** proposes members from a seed and **has no
access to `results/`.** The agent that chooses the peers cannot see a tone number, which
is the strongest available form of "declared before collection" — not a rule it is asked
to follow, but a fact about what it can read.

Spawn it for a seed you do not already know cold. Fill the draft yourself for one you do;
the contract is the same either way.

## Reference files

- `references/substitutability.md` — the frozen test, the selection boundary, and what
  product continuity actually costs per category. **Read before any company or product
  enters or leaves a group.**
- `references/peer-group-contract.md` — the exact schema, how it implements
  `CONTRACTS.md` §1, the hash and view format, and **what the collector is handed**,
  including the parent-ASIN problem.

## What the script owns

1. **Completeness.** A member without brands, a tracked product, a reason naming what it
   sells, or a reporting calendar is not a member.
2. **The freeze.** `declare` writes `<id>.v1.json` + `.hash` and refuses to overwrite.
   `verify` re-hashes every version and reports an edit in place as a break rather than
   repairing it — repairing would destroy the only evidence it happened.
3. **The order of operations.** Every version records how many readings existed for the
   group at freeze. A version declared after tone was known is marked
   `post_collection: true`, permanently, in the file and the sidecar.
4. **The selection boundary.** A reason that selects on performance is refused by name. A
   product justified by popularity rather than continuity is refused.
5. **Comparability.** The marketplaces, calendars, currencies and private members are
   computed and recorded, so a group spanning them cannot be charted without that being
   visible.
6. **The concentration floor.** A disclosed share below 0.60 is refused, and a listed
   member can never claim the private-company evidence exemption.
7. **The evidence record.** `pool_evidence.jsonl` is append-only and hash-chained; a
   withdrawal is appended with a reason, never deleted. `evidence_log.py verify` detects
   an altered line, a removed line, a snapshot that drifts from the log, or a snapshot
   edited after it was hashed.
8. **The company it asked for.** A venue is resolved only when its exchange is a US one,
   and the entity name SEC returns is compared to the company name before the evidence is
   recorded. A mismatch is refused, not recorded (`CONTRACTS.md` §5).

## Tests

```bash
python3 .claude/skills/review-tone-peer-group/tests/test_declare_group.py   # 290
python3 .claude/skills/review-tone-peer-group/tests/test_evidence_log.py    # 27
python3 .claude/skills/review-tone-peer-group/tests/test_guard_coverage.py  # audit
```

`test_guard_coverage.py` enforces `CONTRACTS.md` §5 mechanically: it finds every refusal
site in the validators and asserts each one is tripped by at least one test. A guard no
test has ever seen fire is indistinguishable from a guard that works — it found 24 of 70
in that position on the day it was written. It carries its own negative controls, because
an audit that always passes is the thing it exists to prevent. 96 sites now, all tripped.

**A new validator helper must be added to its `GUARD_FUNCS`**, or its refusals are
excluded from the audit — which is the same invisible failure the audit exists to catch.

Standard library only, no network. The lexicon tests are the ones that matter: a refusal
lexicon that fires on an innocent sentence is worse than none, because it teaches the
agent to write around the checker instead of writing the reason. Every phrase a
legitimate reason would plausibly contain is pinned as a must-pass.

## Known weaknesses

Recorded in `CHANGELOG.md` under 2026-10-11 and summarised here because they change how a
group should be read:

- **Reviews come from the parent ASIN, not the product.** 14 of 15 declared products are
  variant children, so what is measured is a variation family.
- **The concentration filter, not substitutability, decides most memberships** — and it
  rules out the household names. Build from focused companies.
- **Grocery ASIN continuity is poor exactly for the core SKU**, which is re-listed under
  fresh identifiers. Choosing for continuity pulls away from the flagship pack.
- **An electronics model's ASIN lives about three years.** Prefer the current generation
  early in its life; a model change ends a series and is recorded as ended.
- **Cross-marketplace groups mix reviewer populations.** Flagged, not refused.
- **The lexicons are floors.** They catch the honest slip, not a determined one.
