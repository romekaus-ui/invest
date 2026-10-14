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

## 4b. Concentration: the filter that actually binds

**A company belongs in a group only if one main product line dominates its revenue, and
that line is what sells and gets reviewed on Amazon** (PLAN.md).

This is a *separate and stricter* test than substitutability, and it is the one that
decides most memberships. Measured on the first real pass: the substitutability test
admits Coca-Cola, PepsiCo, Sony, Apple and Samsung; the concentration filter rejects
every one of them except Coca-Cola. **Pepsi/Coca-Cola is the illustration of pairing, not
the quality bar** — PepsiCo is roughly 55% snacks, and a souring review on a Pepsi
multipack says nothing about Frito-Lay.

The practical consequence: the groups this component should produce are built from
**focused mid-cap and private companies**, not household-name giants. Celsius, YETI,
Traeger, Sonos, Turtle Beach, BellRing, Crocs, e.l.f., GoPro — companies where one line
is most of the revenue and that line is on Amazon.

### How it is established

Never asserted. `screen_pool.py` fetches each US registrant's latest annual report and
captures verbatim windows around a fixed set of probes — reportable segments, revenue
disaggregation, revenue by product category — lifted from the archived scorecard's c3 and
c6 probes, which were solving this same question. The agent reads the share off that
text; the URL recorded is the filing the script actually fetched. For a non-US or private
member the evidence is recorded by hand against the company's own report.

The floor is **0.60**, chosen here because PLAN.md states the filter without a number. It
is permissive on purpose: it admits a company with a clear dominant line plus a real
secondary business, and excludes the four-segment conglomerate the filter exists to keep
out. It is the single most arguable constant in the component and it is one constant, so
it can be argued with.

### The level question, and it is not a technicality

`dominant_line` is defined at the level the **tracked products** sit at.

- YETI: Drinkware alone is 58% and *fails* the floor. Insulated drinkware and coolers
  together are 98% and pass — and both are what YETI sells on Amazon, so the wider line
  is the honest one.
- Traeger: Grills alone are 53% and fail. Grills, pellets and accessories are 100% of
  revenue, and a grill and a bag of pellets are both tracked.

Widening is legitimate **only if the tracked products span the wider line.** Otherwise it
is a way around the floor, and the floor is the filter.

### Null is a real answer

A single-segment filer discloses that management runs one business, not that one product
is 100% of revenue. Turtle Beach's "single reportable segment, referred to as gaming
accessories" is strong evidence and gives no number. Record `share_of_revenue: null` with
the quotation. Seven of the fifteen members declared carry null, and inventing a 1.00 for
them would have been the dishonest alternative.

## 5. Group shape

- **At least two members.** A group of one is declared with `--solo` and a reason naming
  what was searched for and not found. Its `peer_relative_reading` is false and every
  number from it must say it has no peer comparison.
- **Global by design.** US, Europe, Asia. Listing venue is irrelevant to tone and is
  recorded only to identify the company.
- **Private peers count, and are often the best ones.** A private competitor is a
  perfect category control: it tells a category-wide tone move from a company-specific
  one. Only the company whose revenue is eventually wanted has to be public. Several of
  the most focused competitors in consumer goods are private — Bose, Stanley/PMI, Trove,
  Red Bull, Weber, Dyson — and dropping a group because one member is private would throw
  away the control.
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
