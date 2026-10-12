# Substitutability

How a company gets into a peer group, and the ways that judgement goes wrong.

CORE-PROMPT §2 is the rule. This file is the working method under it, and the thresholds
the script enforces. Read it before adding or removing a member.

---

## 1. The test, asked in the same words every time

> Would a consumer shopping for this seed's product consider this company's product
> instead of it, as a direct alternative for the same need, at the same point of
> purchase? If yes, it is a peer. Size, country, listing venue and currency are
> irrelevant to the answer.

The wording is frozen in `declare_group.py` and compared byte for byte at freeze time. It
is asked of every candidate identically **so that the test does not vary with how
interesting the candidate looks.** Changing it changes the instrument and belongs in
`CHANGELOG.md`.

The target relation is Pepsi/Coca-Cola: two companies whose products a shopper swaps
between without much thought. It is not "same industry", not "same index", not "same
size", and emphatically not "both would make a good holding".

### What the test is not

- **Not corporate similarity.** Samsung Electronics is a semiconductor company that
  happens to own JBL. It is a peer in an over-ear headphone group because JBL Tour One
  competes with Sony WH-1000X on the same product page, and for no other reason.
- **Not market share.** A small maker whose product a shopper genuinely considers is a
  peer. A large one whose product they do not is not.
- **Not vertical relatedness.** A bottler, a distributor and a shelf-stable syrup
  supplier are not peers of a soft-drinks brand. The consumer does not choose between
  them.

## 2. The boundary the component exists to hold

> Never select or reject a company on its performance, its price, or how promising it
> looks.

This is the failure the whole design is pointed at, because it is invisible afterwards: a
group assembled from companies that looked good produces a peer-relative tone reading
that is about the assembler's taste, not about consumer tone.

The script refuses a `reason` containing valuation, price, return, growth, margin or
attractiveness language, and names the offending phrase rather than rewriting it —
rewriting would hide that the judgement was made on the wrong basis. The full lexicon is
`PERFORMANCE_LEXICON` in `declare_group.py`.

**The lexicon is a floor, not a certificate.** An agent that knows it is there can write
a performance-driven choice in clean language. What the lexicon catches is the honest
slip, which is the common case.

**Exemption:** a phrase that is one of the member's *own declared brands* is not a hit.
"Momentum" is Sennheiser's headphone line, and a reason naming it is doing exactly what
§2 asks. The exemption covers that member's brands only, and those brands are themselves
checked against the titles the marketplace served, so a brand cannot be invented to
smuggle a performance word past the check.

## 3. The reason has to cite what it sells

`reason` must be ≥ 60 characters and must **name one of the member's own brands.** That is
what makes it a statement about product substitutability rather than about the company.

> Sells regular cola in multipack bottles and cans under Coca-Cola, and flavoured soda
> under Sprite and Fanta, which a shopper buying a case of soft drinks takes instead of a
> case of Pepsi or Dr Pepper from the same shelf.

The failing version of the same sentence:

> A very large beverage company with global distribution and many bottling partners.

True, and about the company rather than the product. Refused.

## 4. Products: continuity, not popularity

> An item likely to still be sold in a year. A bestseller that is discontinued ends a
> series.

`continuity_reason` must be ≥ 30 characters, must not argue from rank or volume
(`POPULARITY_LEXICON`), and must assert something checkable: a year the item has been on
sale since, or a term naming its place in the range (`CONTINUITY_ANCHORS`) — flagship,
core line, permanent range, standard pack, no successor.

### What continuity actually costs, by category

Measured while declaring the three worked examples, and it differs sharply:

- **Fast-moving grocery is the worst case.** The flagship Coca-Cola 12-can pack is
  re-listed under a fresh ASIN repeatedly; the ASINs that survive for a decade are the
  odd formats and legacy variants. Choosing for ASIN continuity in grocery therefore
  pulls *away* from the core SKU, which is a real trade-off and not a solved problem.
- **Consumer electronics is better but finite.** One headphone model holds one ASIN for
  its life, but that life is roughly three years. Prefer the **current generation early in
  its life** over the outgoing one: the Sony WH-1000XM6 over the XM5, the JBL Tour One M3
  over the M2. This is the clearest case where continuity and accumulated review volume
  point in opposite directions, and §2 says continuity wins.
- **A model change ends a series.** It is recorded as ended, never silently swapped for
  its successor. A swap would splice two products into one line and nothing downstream
  could tell.

### Colour and pack variants

Amazon serves **reviews at the parent ASIN**, pooled across every variant. A child ASIN
therefore yields the parent's reviews. `check-products` records `parent_asin_seen` and
`is_variant_child` for exactly this reason. Prefer the core colourway or pack size; where
a variant is unavoidable, the parent is on record.

### Accessories crowd out the product

Searching a marketplace for a specific electronics model returns mostly third-party ear
pads, cases and headbands. A naive picker would track a replacement ear pad. The brand is
in the accessory's title too, so the brand check does not catch it — reading the served
title is what catches it, which is why `title_at_declare` is compared against what the
page actually returned rather than trusted.

## 5. Group shape

- **At least two members.** A group of one is declared with `--solo` and a reason naming
  what was searched for and not found. Its `peer_relative_reading` is false and every
  number from it must say it has no peer comparison.
- **Global by design.** US, Europe, Asia. Listing venue is irrelevant to tone and is
  recorded only to identify the company.
- **One marketplace if you can.** Members may sit on different Amazon marketplaces, and
  the group is still admissible — but the reviewer populations are national, so a pooled
  peer-relative tone gap across marketplaces is partly a difference between countries of
  shopper and partly a language difference. The script flags it as `cross_marketplace`
  and the group may not be charted without that being visible. Read per marketplace, or
  keep the group to one.

## 6. Declared before collection

- The group is written and frozen **before any review is collected**.
- A frozen version is never edited. A change is a new version carrying its reason and the
  hash of the version it replaces.
- Every version records how many readings already existed for the group. One declared
  after tone was known is marked `post_collection: true` in the file and in the sidecar,
  and keeps that mark.

The script cannot stop a late revision. It can stop a silent one, which is the part that
matters: a reader a year later can tell a group that was declared blind from one that was
adjusted once the answer was visible.
