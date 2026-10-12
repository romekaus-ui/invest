---
name: review-tone-peer-group
description: Declare a peer group of companies selling substitutable consumer products from a seed — a company, a brand or a product category — and freeze it before any review is collected. Use when asked to build, declare, revise, inspect or verify a peer group for the review-tone monitor, to find who competes with a company on the shelf, or to pick the product identifiers (Amazon ASINs) a company will be tracked by. Declares and verifies only; it never collects reviews and never scores tone.
---

# Review Tone Peer Group

Turns a **seed** — a company, a brand, or a product category — into a **declared peer
group**: the set of companies selling substitutable consumer products, anywhere in the
world, with the specific products each will be tracked by.

**Read `CORE-PROMPT.md` in the project root in full before doing anything.** §2 is this
component's specification; §1 is binding on everything. The rules most easily broken in
the moment:

- **Declared before collection.** A peer added once tone is known is not a peer, it is a
  choice. A frozen version is never edited; a change is a new version with its reason.
- **Never select or reject a company on its performance, its price, or how promising it
  looks.** The group is about product substitutability and nothing else.
- **Products are chosen for continuity, not popularity.** An item that will still be sold
  in a year. A bestseller that is discontinued ends a series.
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

python3 $S draft --group-id soft-drinks --seed-kind category --seed "carbonated soft drinks"
# fill the members in the draft — this is the judgement, see below

export SEC_USER_AGENT="Your Name your@email.example"
python3 $S resolve-us    data/peer_groups/soft-drinks.draft.json   # CIK + calendar, US only
python3 $S check-products data/peer_groups/soft-drinks.draft.json  # do the ASINs resolve?
python3 $S check          data/peer_groups/soft-drinks.draft.json  # validate, freeze nothing
python3 $S declare        data/peer_groups/soft-drinks.draft.json  # freeze as v1 + .hash
```

Afterwards:

```bash
python3 $S show   soft-drinks          # current version, and whether its hash still holds
python3 $S verify                      # re-hash every version; detect an edit in place
python3 $S revise soft-drinks --from-draft <draft> --reason "..."   # v2, with a hash chain
```

`check-products` writes the observed title into each product's `verification`. Set
`title_at_declare` to what the marketplace actually served — the contract compares them,
which is how a transposed ASIN gets caught.

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
- `references/peer-group-contract.md` — the exact schema, the hash and sidecar format,
  and what the collector is handed.

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

## Tests

```bash
python3 .claude/skills/review-tone-peer-group/tests/test_declare_group.py
```

Standard library only, no network. The lexicon tests are the ones that matter: a refusal
lexicon that fires on an innocent sentence is worse than none, because it teaches the
agent to write around the checker instead of writing the reason. Every phrase a
legitimate reason would plausibly contain is pinned as a must-pass.

## Known weaknesses

Recorded in `CHANGELOG.md` under 2026-10-11 and summarised here because they change how a
group should be read:

- **Grocery ASIN continuity is poor exactly for the core SKU**, which is re-listed under
  fresh identifiers. Choosing for continuity pulls away from the flagship pack.
- **An electronics model's ASIN lives about three years.** Prefer the current generation
  early in its life; a model change ends a series and is recorded as ended.
- **Cross-marketplace groups mix reviewer populations.** Flagged, not refused.
- **The lexicons are floors.** They catch the honest slip, not a determined one.
