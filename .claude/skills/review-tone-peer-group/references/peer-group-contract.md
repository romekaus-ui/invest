# Peer Group Contract

Exact schema for a declared peer group. `scripts/declare_group.py` enforces it; a
draft that violates it does not freeze.

A declared group is the **input to collection**. Nothing here reads a tone number, a
price or a result, and nothing here may be changed once frozen.

---

## Files

| Path | What it is |
|---|---|
| `data/peer_groups/<id>.draft.json` | Working draft. Editable, unhashed, not a declaration. |
| `data/peer_groups/<id>.v<N>.json` | Frozen version N. **Never edited.** |
| `data/peer_groups/<id>.v<N>.hash` | Sidecar: digest plus the facts a reader needs first. |

`<id>` matches `^[a-z0-9][a-z0-9-]{1,48}$`.

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
| `seed` | obj | `{kind, value}`; `kind` ∈ `company` / `brand` / `category` |
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
| `company` | str | unique within the group (case- and dash-insensitively) |
| `country_of_domicile` | str | recorded, never used to include or exclude |
| `listing` | obj | `{status: "private"}`, or `{status: "listed", ticker, exchange, cik?}`. `cik` is 10 digits when present |
| `secondary_listings` | list | optional provenance, e.g. an ADR beside a primary home listing |
| `brands` | list[str] | ≥ 1. The consumer brands it owns **that are relevant to this group** |
| `reason` | str | ≥ 60 chars, must name one of this member's own `brands`, must not select on performance |
| `reporting` | obj | see below |
| `products` | list | ≥ 1 |

### `reporting` — CORE-PROMPT §5

Recorded, **never normalised**. The point is that a downstream stage has to read what
calendar a member is on before it can align anything.

| key | notes |
|---|---|
| `calendar` | `quarterly` / `half-yearly` / `annual` / `irregular` |
| `regime` | the filing system in words, e.g. `SEC 10-Q / 10-K`, `SIX half-year to 30 September`, `TSE quarterly (TDnet) / EDINET` |
| `currency` | ISO 4217, native. Silent conversion is forbidden |
| `fy_end` | `MMDD`. Load-bearing: PepsiCo's is `1226`, Apple's `0926` — neither is 31 December |
| `source` | where the calendar was read from, so a wrong one can be traced |
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
| `title_at_declare` | what the marketplace served. Compared against `verification.title_seen`, so a silently relisted ASIN is visible later |
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

For each member: the ASINs, their marketplaces, and `parent_asin_seen` for each. Reviews
are served at the parent, so a collector pointed at a child ASIN gets the parent's pooled
reviews — two members' series must not be built from different levels of the same tree.

For each member: the reporting calendar and currency, so no stage can align two members'
revenue without having read that they differ.

For the group: `peer_relative_reading`. When false, no peer comparison may be computed or
reported for the single member.
