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
python3 .claude/skills/review-tone-peer-group/tests/test_declare_group.py   # 163
python3 .claude/skills/review-tone-peer-group/tests/test_evidence_log.py    # 27
```

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
