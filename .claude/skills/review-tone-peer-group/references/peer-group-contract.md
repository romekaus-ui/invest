# Peer Group Contract

Exact schema for a declared peer group. `scripts/declare_group.py` enforces it; a
draft that violates it does not freeze.

**`/CONTRACTS.md` §1 is the authority on the output shape, and this file implements it.**
The declaration carries §1's field names directly rather than translating into them, so
there is one schema and not two. Where this component carries a field §1 has no slot for
— the reason, the continuity reason, the verification record, the parent ASIN, the
comparability block — it is additive: a reader that knows only §1 is unaffected. Each of
those fields encodes a CORE-PROMPT §2 rule, and the four that §1 should probably absorb
are raised as amendments A1–A4 in `CHANGELOG.md`.

A declared group is the **input to collection**. Nothing here reads a tone number, a
price or a result, and nothing here may be changed once frozen.

---

## Files

| Path | What it is |
|---|---|
| `data/peer_groups/<id>.draft.json` | Working draft. Editable, unhashed, not a declaration. |
| `data/peer_groups/<id>.v<N>.json` | **The declaration of record.** Frozen version N, never edited. |
| `data/peer_groups/<id>.v<N>.hash` | Sidecar: digest plus the facts a reader needs first. |
| `data/peer_groups/<id>.concentration.json` | Verbatim evidence windows from each member's own filing, with the URL fetched. |
| `groups/<id>.json` | **The CONTRACTS.md §1 view** — what the workflow reads. Generated from the current version, never hand-edited, carrying its own `hash`. |
| `data/peer_groups/candidate_pool.json` | Screening input: candidate companies by category. Not a declaration. |
| `data/peer_groups/pool_evidence.jsonl` | **Append-only, hash-chained evidence record.** The truth. |
| `data/peer_groups/pool_evidence.json` | Snapshot derived from the log. Regenerated, never hand-edited. |
| `data/peer_groups/pool_evidence.hash` | sha256 of the snapshot. |

### The evidence log

`pool_evidence.json` feeds the counts everyone quotes — how many focused companies
exist, how many have a disclosed split, whether the pool runs out. It was the one
artefact here a silent edit would not have shown, and it is the artefact this component
had already edited: two poisoned records were deleted from it when `EPA:EL` turned out to
have resolved to Estée Lauder. Deleting them was right; leaving no trace was not.

So it is now written the way the declarations are (CORE-PROMPT §1.5). Every line of
`pool_evidence.jsonl` carries `prev`, the sha256 of the line before it, so removing or
altering a line breaks the chain at that point and at every point after it. A withdrawal
is an appended `withdraw` record with a reason, **never a deletion** — the wrong evidence
stays visible, marked wrong, which is the only way a reader can tell "never fetched" from
"fetched and wrong". The two deletions are reconstructed in the log and marked as
reconstructions rather than dressed up as original records.

```bash
python3 .claude/skills/review-tone-peer-group/scripts/evidence_log.py verify
# chain intact · snapshot matches the log · snapshot hash ok · 2 withdrawals
```

`verify` exits 2 on any of: a broken chain, a snapshot that disagrees with the log, or a
snapshot edited after it was hashed.

### Two paths, one truth

The versioned file is the declaration of record: append-only, hash-chained, never
rewritten. `groups/<id>.json` is the view of its current version at the path
`CONTRACTS.md` §4 tells the workflow to read. The view carries `version`, `supersedes`
and `record_of_declaration`, so a reader of the view alone can tell a first declaration
from a third and go find the ones it replaced. A view that fails its own hash is rebuilt
from the record with `reemit`, never patched — the record is the truth and the view is
derived.

`<id>` matches `^[a-z0-9][a-z0-9-]{1,48}$`.

`schema_version` is stamped into every frozen declaration. `verify` hash-checks every
version but contract-validates only those at the current schema; an older one is reported
as *not revalidated* rather than as broken, so one schema change does not make every past
declaration look like an integrity violation.

The hash is `sha256` over the **exact bytes** of the `.json` file, which are produced by
one serialisation (`json.dumps(sort_keys=True, indent=2, ensure_ascii=False)` plus a
trailing newline). Re-serialising the same object reproduces the same digest, so a
mismatch means the file changed, not that the writer changed.

### The sidecar line

```
<sha256>  <file>  group=<id>  version=<N>  members=<K>  products=<P>  unverified=<U>
          declared=<iso>  supersedes=<sha12|none>  readings_at_declare=<R>
          post_collection=<bool>  peer_relative_reading=<bool>
```

`unverified`, `readings_at_declare` and `post_collection` are there so that a group
cannot be quoted without its provenance being visible in one line.

---

## Group level

| key | type | notes |
|---|---|---|
| `group_id` | str | stable id, also the filename stem |
| `seed` | str | what the group was grown from (§1) |
| `seed_kind` | str | `company` / `brand` / `category` |
| `substitutability_test` | str | **the frozen wording, verbatim.** A group tested by a different question is a different instrument, so the validator compares it byte for byte |
| `solo` | bool | true only for a company with no peer |
| `solo_reason` | str | required when `solo`: what was searched for and not found |
| `peer_relative_reading` | bool | false iff `solo`; a solo member's tone has no peer comparison and every number from it must say so |
| `members` | list | ≥ 2, or exactly 1 when `solo` |

### Written by the script at freeze time — never by hand

| key | type | notes |
|---|---|---|
| `version` | int | |
| `declared_at` | datetime | UTC, when the freeze happened |
| `readings_at_declare` | int | rows in `results/readings.jsonl` for this group id at freeze |
| `post_collection` | bool | `readings_at_declare > 0`. A version declared after tone was known carries this mark permanently |
| `declared_by` | obj | `{tool, code_commit}` |
| `comparability` | obj | computed; see below |
| `supersedes` | obj | on v2+: `{version, file, sha256, declared_at}` of the version replaced |
| `revision_reason` | str | on v2+: ≥ 40 chars saying what changed and why |
| `member_diff_from_previous` | obj | on v2+: `{members_added, members_removed}` |

## Member level

| key | type | notes |
|---|---|---|
| key | type | notes |
|---|---|---|
| `company` | str | unique within the group (case- and dash-insensitively) |
| `venue` | str | `"EXCHANGE:TICKER"` (e.g. `"NASDAQ:IRBT"`) or exactly `"private"` |
| `cik` | str | 10 digits, when SEC knows the company |
| `country_of_domicile` | str | recorded, never used to include or exclude |
| `secondary_listings` | list | optional provenance, e.g. an ADR beside a primary home listing |
| `brands` | list[str] | ≥ 1. The consumer brands it owns **that are relevant to this group** |
| `concentration` | obj | **required**; see below |
| `reason` | str | ≥ 60 chars, must name one of this member's own `brands`, must not select on performance |
| `reporting` | obj | see below |
| `products` | list | ≥ 1 |

### `concentration` — PLAN.md's filter, CONTRACTS.md §1

One main product line must dominate revenue, and it must be the line that sells on
Amazon. Review tone on a handful of products can only say something about a company's
revenue if those products *are* most of the revenue.

| key | notes |
|---|---|
| `dominant_line` | the product line that dominates revenue, in the filing's own words |
| `share_of_revenue` | a fraction in (0, 1], **or null** when no split is disclosed. The floor is **0.60** |
| `evidence` | a citation of the company's own filing. Never asserted. The performance lexicon applies: a citation says what is sold and in what proportion, not how the company is doing |
| `source_url` | https URL to the filing itself |
| `no_public_report` | private members only; see below |
| `revenue_readable` | must be `false` when `no_public_report` is set |

**At what level is the line defined?** At the level the *tracked products* sit at. YETI's
drinkware alone is 58% of net sales and fails the floor; the insulated drinkware and
coolers it actually sells on Amazon are 98% and pass. Traeger's grills alone are 53%; the
grills, pellets and accessories that are its whole revenue are 100%. Widening is
legitimate **only if the tracked products span the wider line** — it is a statement about
what is being measured, not a way around the floor.

**Null is honest.** A single-segment filer's "100%" is an inference, not a disclosure.
Half the members declared so far carry `share_of_revenue: null` with real evidence, and
that is the correct record.

**Private members.** §1 holds two rules that collide on a private company: every member
needs concentration evidence from its own filing, and a private member is valid and
useful as a category control. Bose, Stanley/PMI and Trove publish no financial report at
all, so the first rule would delete the members the second asks for. Resolved by asking
what the evidence is *for*: concentration exists so a tone series can be connected to a
revenue series, and a private member has no revenue series to connect to — it is in the
group to tell a category-wide tone move from a company-specific one. A private member may
therefore set `no_public_report: true`, which still requires saying what it sells and
where that was established, forces `share_of_revenue: null`, and marks
`revenue_readable: false`. **A listed member is never exempt.** Raised as amendment A1.

### `reporting` — CORE-PROMPT §5

Recorded, **never normalised**. The point is that a downstream stage has to read what
calendar a member is on before it can align anything.

| key | notes |
|---|---|
| `calendar` | `quarterly` / `half-yearly` / `annual` / `irregular` |
| `currency` | ISO 4217, native. Silent conversion is forbidden |
| `source` | §1's enum, extended: `SEC`, `companies-house`, `EDINET`, `none`, plus `TDnet`, `SIX`, `DART`, `other-national`. §1's four cannot express Sonova (SIX), Samsung (DART) or an Austrian GmbH — amendment A3 |
| `regime` | what it files and how often, in words, e.g. `SEC 10-Q / 10-K`, `SIX half-year to 30 September` |
| `fy_end` | `MMDD`. **Load-bearing and absent from §1** (amendment A4). Of the six groups declared, the fiscal year ends are `1231`, `0102`, `1003`, `0930`, `0826` and `0331` — five of six members are not on a 31 December year |
| `calendar_source` | where the calendar was read from, so a wrong one can be traced |
| `resolved_by` | `edgar` (resolved mechanically) or `agent` (recorded by hand) |

`resolve-us` fills this from EDGAR for a 10-Q filer. For a **foreign private issuer** it
deliberately fills nothing: a 20-F filer's SEC filings are annual while its home market
may be quarterly, and writing `annual` would be the normalisation §5 forbids.

### `products` — CORE-PROMPT §2

| key | notes |
|---|---|
| `asin` | `^[A-Z0-9]{10}$`, unique across the **whole group**: one product cannot be two companies' |
| `marketplace` | one of the known hosts, e.g. `amazon.com`, `amazon.co.uk`, `amazon.co.jp` |
| `brand` | must be one of the member's declared `brands` |
| `name` | what the marketplace served. Compared against `verification.title_seen`, so a silently relisted ASIN is visible later |
| `continuity_reason` | ≥ 30 chars. Why this item will still be sold in a year. Must not argue from popularity, and must assert something checkable: a year, or a term naming the item's place in the range |
| `verification` | written by `check-products`; see below |

### `verification`

| key | notes |
|---|---|
| `status` | `resolved` / `not_found` / `blocked` / `unavailable` / `error` |
| `checked_at`, `route`, `url`, `marketplace`, `http_status` | provenance (§1.4) |
| `title_seen` | the title the marketplace served |
| `availability_seen` | read from the buy box only, or a statement that it was not rendered |
| `parent_asin_seen`, `is_variant_child` | **the collector needs these.** Amazon serves reviews at the parent ASIN, pooled across every variant |

Parent ASINs must also be **unique across the group**. Two members tracking children of
one parent would be handed the same reviews, and their peer-relative tone would be a
comparison of a series with itself.

A `status` other than `resolved` blocks the freeze unless `--allow-unverified` is passed,
and the count then travels in the sidecar. Refusing outright would tempt a swap for a
product that happens to resolve, which is selection by availability.

`blocked` is a finding, not an obstacle (§3). `check-products` stops at the first block
and never retries around it.

## `comparability` — computed, not asserted

Facts about how comparable the members are. None of them refuses a group: a group
spanning marketplaces, calendars and currencies is what §2 asks for. They exist so the
group cannot be charted without them being visible.

| key | notes |
|---|---|
| `marketplaces` | `{marketplace: product count}` |
| `cross_marketplace` | more than one. **The sharp one**: Amazon's reviewer populations are national, so a tone gap across marketplaces is partly a gap between countries of shopper and partly a language difference, before any company difference |
| `reporting_calendars`, `mixed_reporting_calendars` | §5: comparable on tone, not on revenue |
| `currencies`, `mixed_currencies` | §5: no silent conversion |
| `members_without_public_reporting` | private members: tone and peer position, no revenue series |
| `revenue_comparable_without_stated_handling` | true only when one calendar, one currency and no private member |

---

## What the collector is handed

For each member: the ASINs, their marketplaces, and `parent_asin` for each.

**Read this before keying anything on `asin`.** Amazon serves reviews at the *parent*
ASIN, pooled across every variant. Of the 15 products declared so far, **14 are variant
children** — their parents are `B0BLGMF97X`, `B07SGPJBZ6`, `B0H5F1SG6J` and so on, none
equal to the ASIN chosen. So the review pool is the variation family, not the item: the
Premier Protein chocolate 12-pack's reviews are the reviews of every Premier Protein
shake flavour and pack size under its parent. Three consequences:

1. "Product-level tone" is really **variation-family-level tone**, and should be described
   that way rather than as the tone of the item.
2. Choosing a product for continuity does not buy continuity of the review pool. Amazon
   re-parents products, and a re-parenting silently changes what the series measures.
   `parent_asin` is recorded at declaration so a later change is detectable.
3. `CONTRACTS.md` §2 keys reviews on `asin` alone. It should carry `parent_asin` beside it
   — amendment A2, and the one most likely to bite.

For each member: the reporting calendar and currency, so no stage can align two members'
revenue without having read that they differ.

For the group: `peer_relative_reading`. When false, no peer comparison may be computed or
reported for the single member.
