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
| `data/peer_groups/<id>.parents.jsonl` | **Append-only, hash-chained parent re-resolution.** What stage G refuses on. |
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

Every product records the **`route`** it is read through (`CONTRACTS.md` §1, amended
2026-10-15). An absent `route` means `amazon`, which is what keeps the declarations
frozen before the field existed valid without rewriting a frozen file.

| key | notes |
|---|---|
| `route` | `amazon` \| `bazaarvoice` \| `yotpo` \| `powerreviews`. Absent = `amazon` |
| `brand` | must be one of the member's declared `brands`. **Every route** |
| `name` | what the platform served. Compared against `verification.title_seen`, so a silently relisted product is visible later. **Every route** |
| `continuity_reason` | ≥ 30 chars. Why this item will still be sold in a year. Must not argue from popularity, and must assert something checkable: a year, or a term naming the item's place in the range. **Every route** |
| `verification` | written by `check-products`. **Every route**; the fields differ, see below |

On `route: amazon`:

| key | notes |
|---|---|
| `asin` | `^[A-Z0-9]{10}$`, unique across the **whole group**: one product cannot be two companies' |
| `marketplace` | one of the known hosts, e.g. `amazon.com`, `amazon.co.uk`, `amazon.co.jp` |
| `parent_asin` | required — where the reviews are actually served |

On a **brand-platform route**:

| key | notes |
|---|---|
| `client` | the deployment zone as the platform's own CDN spells it: `traegerservice`, `weber`, `yeti` |
| `site` | the deployment site within the client. Optional, default `main_site` — YETI publishes a key under both `yeticom_sfra` and `main_site` |
| `product_id` | the platform's product id. **ONE representative per review family** |
| `review_family` | required — this route's `parent_asin`; see below |
| `liveness_finding` | required when the newest review is over a year old |

The Amazon identifiers must be **absent** on a brand-route product. A half-converted
draft carrying both makes two contradictory claims about where the reviews come from,
and a reader cannot tell which the collector will follow.

### `verification`

Common to every route:

| key | notes |
|---|---|
| `status` | `resolved` / `not_found` / `blocked` / `unavailable` / `error` |
| `checked_at`, `route` | provenance (§1.4) |
| `title_seen` | the name the platform served |

On `route: amazon`: `url`, `marketplace`, `http_status`, `availability_seen` (read from
the buy box only, or a statement that it was not rendered), and
`parent_asin_seen` / `is_variant_child` — **the collector needs these**, because Amazon
serves reviews at the parent ASIN, pooled across every variant.

On a brand-platform route, `check-products` resolves the product against the live display
API and records what that API can establish:

| key | notes |
|---|---|
| `client`, `site`, `product_id_seen` | which deployment answered, and for which id |
| `total_claimed` | the review count the deployment reports for this family |
| `newest`, `newest_review_age_days` | **liveness is its own check.** Owala serves 14,029 reviews flawlessly and its newest is 2022-07-28 — 1,540 days stale. Depth says nothing about whether a deployment is alive |
| `first_review`, `reach_years` | how far **back** the family reaches, asked for directly with `Sort=SubmissionTime:asc&Limit=1`. Not the sample's oldest row: Traeger's Pro 575 sample bottoms out in 2024 while the family reaches 2019-03-20 |
| `oldest_in_sample`, `sample_window`, `sampled` | the bounds of what was actually walked, named as the sample's |
| `with_text_pct`, `with_text_pct_basis` | a star-only review can never be scored. Measured over the most recent reviews, which is the useful reading — Weber ran 100% text in 2019 and 29–37% now — and labelled so it is not mistaken for a whole-history figure |
| `dates_descending_verified`, `date_inversions` | **verified against the dates, never trusted from the sort parameter.** Claiming verified order while recording inversions is a contradiction and is refused: that is what a parameter-trusting check looks like from outside. §3 records the same failure on Amazon, where the claim was false |
| `family_fingerprint`, `family_sample_ids`, `family_sample_end`, `family_sampled`, `family_shared_with` | the family, below |

#### The review pool must be unique across the group, on either route

Parent ASINs must be **unique across the group**. Two members tracking children of
one parent would be handed the same reviews, and their peer-relative tone would be a
comparison of a series with itself.

**`review_family` is the brand route's `parent_asin`, and it is just as required.** A
brand review platform pools across a product *family* exactly as Amazon pools across
variants. Measured on Traeger 2026-10-15: `TFB57GLE` (Pro 575) and `TFB78GLE` (Pro 780)
are different grills at different prices and return **identical 1,215-review id sets**;
`TFB89BLF` and `TFB65BLF` likewise. Nine products resolved to **seven distinct families**.

So the family is established by **comparing the returned review-id sets pairwise**, which
`check-products` does across the whole draft after resolving it. It is never inferred from
the product ids differing — that is precisely the inference the measurement falsified —
and `TotalReviewCount` is never summed across products, which overstated uniques by 30%.

**The fingerprint is taken over the OLDEST 100 ids, and that is not a detail.** Over the
newest page it changes the moment one review arrives, so the next cadenced `reresolve`
would report every live product as `reparented` and stage G would refuse a healthy group.
That was measured and fixed before the first cadence ran (schema 2, and `barbecue-grills`
v3 → v4). The oldest page is stable against arrivals — Traeger's Pro 575 oldest page
spans 2019-03-20..2019-06-30 while its newest spans 2024-06-13..2026-10-11, and the two
are **disjoint** — and it costs nothing in discrimination: the pooled pair still shares
100 of 100 oldest ids and fingerprints identically, and a distinct family shares zero.
A family is identified by its history, not by its last page.

What *does* move it is an **old review being deleted**, which is a finding worth having on
a route where the company being measured runs the moderation queue. `family_sample_end`
records which end was sampled, and a record that cannot say is refused — one sampled from
the other end cannot be compared with one sampled from this one.

Two declared products in one family are refused, both ways round:

- **inside one member**, because both would be collected and the same reviews counted
  twice;
- **across two members**, because the platform serves those reviews pooled, so both
  members would be scored on the same reviews — the same refusal the Amazon route makes
  for a shared parent.

The brand-in-the-served-name half of the title check does **not** apply on a brand route,
and the reason is measured: a brand's own catalogue does not repeat its name in its
product names. Four of the five Weber grills declared are served as e.g. "Spirit II E-210
Gas Grill (Liquid Propane)" with no "Weber" in them. The **name comparison** — the half
that catches a transposed identifier — applies on every route, and caught six of seven
products on the first brand-route draft.

The served name is read from `products.json?Filter=Id:eq:<id>`, **never** off the reviews
query's `Includes.Products`: that include carries the whole pooled family, so its first
entry is *a* product of the family and not the one asked for. Read that way, `TFB57GLE`
came back named "Pro 780" and `TFB88PZB` "Pro Series 22", against "Pro 575" and "Pro
Series 34" from the direct lookup. A name check reading the wrong name is worse than
none, because it fails in the direction of agreeing with whatever was declared.

#### Routes the contract names and this component cannot resolve

`yotpo` and `powerreviews` are reachable and both measured
(`data/_route-tests/brand-widget-routes-2026-10-15.json`), but their key discovery and
paging differ and **no resolver is wired**. A product on one is refused rather than
declared on an unchecked identifier, unless `--allow-unverified` is passed. "Not
implemented" is the honest answer; "assumed to work" is not.

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
| `routes` | `{route: product count}` |
| `mixed_routes` | more than one route in the group |
| `routes_differ_between_members` | **the sharper one.** §3 requires a route change to be recorded *in* a series; this is the route differing *across* members at the same instant, so a peer-relative reading subtracts a peer mean computed on a different instrument. A mix *within* every member is a different and milder thing: the comparison between members is sound and each member's own series carries the mix |
| `reporting_calendars`, `mixed_reporting_calendars` | §5: comparable on tone, not on revenue |
| `currencies`, `mixed_currencies` | §5: no silent conversion |
| `members_without_public_reporting` | private members: tone and peer position, no revenue series |
| `revenue_comparable_without_stated_handling` | true only when one calendar, one currency and no private member |

---

## Pool drift, and why declaring is not enough

The same-pool refusal in `validate` fires **when a group is declared**. Amazon
re-parents variation families afterwards, so two members declared under distinct parents
can converge later with **no identifier changing anywhere** — not the child ASINs, not
the group file, not its hash. The group is then handing two members identical reviews and
comparing them against each other: exactly the condition already refused, arriving by a
route nothing watched.

The brand platforms are no different: a catalogue is re-organised, and two product ids
that returned disjoint id sets at declaration can be merged into one pooled family
afterwards. `reresolve` therefore sweeps **both routes**, re-measuring the parent ASIN on
amazon and the review-family fingerprint on a brand platform, and treats a convergence
identically either way.

So `CONTRACTS.md` §1 requires every declared review pool to be **re-resolved on a
cadence**, and a convergence found later is the same refusal as one found at declaration.

| | |
|---|---|
| `declare_group.py reresolve <id>` | does the network work, appends what it saw |
| `declare_group.py --group <id>` | reads the log and refuses; makes no network call |
| Cadence | `REPARENT_CHECK_DAYS = 7` |

**Split deliberately.** Stage G is a gate the workflow calls; a gate that makes fifteen
network requests and can be blocked by the marketplace is not a gate. `reresolve` is the
verb that can be slow, blocked or scheduled; stage G stays offline, cheap and
deterministic, and refuses when the sweep is missing, stale, incomplete, of an older
version, or found something.

What a sweep records per product: `unchanged`, `reparented` (the pool is now a different
family), `converged` (**fatal** — two members share a pool), `ended` (the product no
longer resolves, so the series ends and is recorded as ended, never swapped), `stale` (a
brand deployment that has stopped receiving reviews since it was declared — liveness
moves after declaration too), `no_parent_on_page`, or `blocked`.

**`reresolve` never rewrites the declaration.** The collector verifies the parent from
the page and treats the group file as the declaration, not the authority; editing the
file to match what Amazon now serves would paper over the very convergence this exists to
find. A real change is a new version through `revise`, with its reason.

**A blocked sweep is "unchecked", not "checked and clean".** §3: recorded, never routed
around, and stage G says the parents are unchecked rather than passing them.

**Parents are read from `/dp/<ASIN>` only.** `/product-reviews/` does not expose
`parentAsin`, so a parent taken from there would be asserted rather than confirmed. The
one marketplace URL this component builds is `https://<host>/dp/<asin>`.

## What the collector is handed

For each member, per product, **the route first** — then the identifiers that route uses:
on `amazon` the ASIN, its marketplace and its `parent_asin`; on a brand platform the
`client`, the `product_id` and its `review_family`. The §1 view projects each product
through its own route's keys rather than writing a row of nulls for the other route's.

**Both routes pool, and the pooled unit is what the collector must join on.** The rest of
this section is about Amazon's parent; the brand platforms' `review_family` is the same
problem with the same consequences, and `check-products` establishes it by comparing the
returned review-id sets pairwise.

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
