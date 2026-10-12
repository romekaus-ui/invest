# Changelog

The live consumer-tone monitor (`CORE-PROMPT.md`). The previous design's changelog is
archived at `archive/historical-harness/REVIEW-TONE-CHANGELOG.md` and is not continued
here.

---

## 2026-10-11 — peer groups: the `review-tone-peer-group` skill

First component of the live monitor. Takes a seed — a company, a brand or a product
category — and produces a **declared peer group**: companies selling substitutable
consumer products, anywhere in the world, with the specific products each will be tracked
by. Declared and frozen before any review is collected (CORE-PROMPT §2).

Added:

- `.claude/skills/review-tone-peer-group/scripts/declare_group.py` — stdlib only
- `.claude/skills/review-tone-peer-group/references/substitutability.md` — the test, the
  selection boundary, what continuity costs per category
- `.claude/skills/review-tone-peer-group/references/peer-group-contract.md` — the schema
- `.claude/skills/review-tone-peer-group/tests/test_declare_group.py` — 102 checks, no network
- `.claude/agents/peer-group-proposer.md` — the research subagent
- `data/peer_groups/` — three declared groups, below

Collects no reviews and scores no tone. Its output is the collector's input.

### The method

The script owns what must not be done in prose; the agent owns the judgement, recorded
with the reason it came to. Mirrors the division the archived
`scripts/score_eligibility.py` used, which worked.

**The script owns:**

1. *Completeness.* A member without brands, a tracked product, a reason naming what it
   sells, or a reporting calendar is not a member.
2. *The freeze.* `declare` writes `<id>.v1.json` plus a `.hash` sidecar and refuses to
   overwrite. A change is a new version carrying its reason and the sha256 of the version
   it supersedes. `verify` re-hashes every version and reports an edit in place as a break
   rather than repairing it — repairing would destroy the only evidence it happened.
3. *The order of operations.* Every version records how many rows
   `results/readings.jsonl` held for that group at freeze time. A version declared after
   tone was known is marked `post_collection: true` in the file and in the sidecar, and
   keeps the mark. The script cannot stop a late revision; it can stop a silent one.
4. *The selection boundary.* A reason that selects on performance, price or
   attractiveness is refused by name. A product justified by popularity rather than
   continuity is refused.
5. *Comparability.* Marketplaces, reporting calendars, currencies and private members are
   computed and recorded.

**The agent owns:** which companies sell substitutable products, which brands matter to
this group, which products will survive a year, and the reason in a sentence.

A subagent, `peer-group-proposer`, does the research and **has no access to `results/`**.
The agent that chooses the peers cannot read a tone number. That is the strongest
available form of "declared before collection": not a rule it is asked to follow but a
fact about what it looked at.

### Thresholds and judgement rules chosen

Each is a floor that stops an empty or inadmissible field passing, not a certificate that
the field is good. Stated so a later reader can disagree with the number rather than
guess at it.

| Rule | Value | Why this value |
|---|---|---|
| Members per group | ≥ 2, or exactly 1 with `--solo` + reason | §2. A solo member gets `peer_relative_reading: false` and every number from it must say so |
| `reason` length | ≥ 60 chars | A reason too short to name a product and a swap is not a reason |
| `reason` must name an owned brand | required | Forces a statement about what it **sells** rather than about the company. "A very large beverage company with global distribution" is true and refused |
| `continuity_reason` length | ≥ 30 chars | Same floor, lower: it is one clause, not a sentence |
| `continuity_reason` must assert something checkable | a year in 1950–2049, **or** a term from `CONTINUITY_ANCHORS` | Stops "a good product people like buying". A year is verifiable; the anchor terms (flagship, core line, permanent range, no successor) name a place in the catalogue |
| `PERFORMANCE_LEXICON` | 50 phrases, refused in `reason`, `solo_reason`, `continuity_reason` | The boundary the component exists to hold. Refused by name, never rewritten: rewriting hides that the judgement was made on the wrong basis |
| `POPULARITY_LEXICON` | 26 phrases, refused in `continuity_reason` | §2 chooses for continuity. A bestseller that is discontinued ends a series exactly as an obscure item does |
| Brand exemption from both lexicons | that member's own declared brands only | See defect 2 |
| ASIN | `^[A-Z0-9]{10}$`, unique across the whole group | One product cannot be two companies' |
| Product brand | must be one of the member's declared brands | |
| `title_at_declare` | must equal the title the marketplace served | Catches a transposed identifier; prose and ASIN are written in different moments |
| Unresolved product | blocks the freeze unless `--allow-unverified` | Refusing outright would tempt a swap for a product that happens to resolve, which is selection by availability. The count travels in the sidecar instead |
| `currency` | ISO 4217, native, no conversion | §5 |
| Cross-marketplace / mixed calendar / private member | **warned, never refused** | §2 asks for a global group. The facts are computed into the file so it cannot be charted without them being visible |

Lexicon phrases are matched on word boundaries over a dash-, slash- and
space-normalised string, so "best-seller", "Best Seller" and "p/e ratio" all reach their
entry while "sells", "value pack" and "price tier" survive. Phrases rather than bare
words wherever a bare word has an innocent use — a reason is *supposed* to say what a
company sells.

### Verified end to end on three real seeds

Every ASIN below was resolved live against the marketplace recorded, and the title shown
is the one the page actually served.

**`carbonated-soft-drinks`** — seed: category, the clean case. All three US registrants;
EDGAR resolved all three calendars mechanically and caught PepsiCo's 52/53-week year
ending **26 December**, not 31 December. No comparability notes at all: one marketplace,
one calendar, one currency.

| Company | Venue | Calendar | ASIN | Served title |
|---|---|---|---|---|
| The Coca-Cola Company | NYSE:KO | quarterly USD, FY 1231 | B01KHRNMQ4 | Coca-Cola Bottle, 12 Ounce, Pack of 24 |
| PepsiCo, Inc. | NASDAQ:PEP | quarterly USD, FY 1226 | B00FCMBLL8 | Pepsi, Bottles, 16.9 Fl Oz (Pack of 6) |
| Keurig Dr Pepper Inc. | NASDAQ:KDP | quarterly USD, FY 1231 | B0BR8FCGM5 | Dr. Pepper Soda, 20 oz Bottle - Pack of 12 |

**`anc-over-ear-headphones`** — seed: category, the global case. Five members, five
countries of domicile, four currencies, three reporting cadences, one private company.
All five products on amazon.com, so no cross-marketplace problem. EDGAR resolved Apple
alone (FY ending **26 September**) and explicitly left the other four to the agent rather
than guessing.

| Company | Venue | Calendar | ASIN | Served title |
|---|---|---|---|---|
| Sony Group Corporation | TSE:6758 | quarterly JPY, FY 0331 | B0F3PQHWTZ | Sony WH-1000XM6 … Black |
| Apple Inc. | NASDAQ:AAPL | quarterly USD, FY 0926 | B0DGJC52FP | Apple AirPods Max … |
| Bose Corporation | private | none | B0FDKR293G | Bose QuietComfort Ultra … (2nd Gen) |
| Sonova Holding AG | SIX:SOON | **half-yearly** CHF, FY 0331 | B0B6GHW1SX | Sennheiser Momentum 4 … Black |
| Samsung Electronics Co., Ltd. | KRX:005930 | quarterly KRW, FY 1231 | B0DWTD2D4M | JBL Tour One M3 … (Mocha) |

Samsung is in the group through Harman/JBL and Sonova through Sennheiser's consumer
division — the parent that reports, not the brand on the box. Sonova is the §5
half-yearly European reporter: H1 to 30 September, FY to 31 March, no quarterly revenue.

**`energy-drinks-global`** — seed: category, declared deliberately to find out what the
contract lets through. It is admissible under §2 and a **poor instrument**, and the script
now says so in four lines rather than leaving it to be discovered after charting. See
defect 3.

| Company | Venue | Calendar | ASIN | Marketplace |
|---|---|---|---|---|
| Suntory Beverage & Food Limited | TSE:2587 | quarterly JPY | B001LION9E | **amazon.co.uk** |
| Monster Beverage Corporation | NASDAQ:MNST | quarterly USD | B01MQX4P7V | amazon.com |
| Red Bull GmbH | private | annual statutory only | B006O3ASKE | amazon.com |

### Defects found

1. **The availability check called every in-stock product unavailable.** Found on the
   first live run: five flagship products, all in stock, all classified `unavailable`. An
   Amazon product page embeds a localisation table carrying the literal string
   `"Currently unavailable."` on *every* page, in stock or not, as the caption the variant
   selector would show if a colour were out of stock. A whole-page substring scan
   therefore matched everywhere. Fixed by stripping `<script>` and `<style>` bodies before
   any marker is looked for, and reading availability from the `#availability` buy box.
   Pinned by a regression test that puts the string inside a `<script>` and asserts
   `resolved`. **This is the defect to remember**: the check had passed its own unit tests
   on synthetic HTML, and only live data showed it was inverted.

2. **The performance lexicon refused a brand name.** `Momentum` is Sennheiser's headphone
   line and `momentum` is a performance word, so the fourth member of the headphone group
   was refused for naming the product it sells. Fixed with a narrow exemption: a phrase
   that is one of *that member's own declared brands* is not a hit. The exemption cannot
   be used to smuggle a performance word past the check, because the declared brands are
   themselves checked against the titles the marketplace served.

3. **A group can be admissible and still be a bad instrument, and nothing caught it.**
   `energy-drinks-global` passed the contract clean while mixing amazon.co.uk with
   amazon.com. Amazon's reviewer populations are national, so a peer-relative tone gap
   across those is partly a gap between British and American shoppers and partly a
   language difference, before any company difference. Added the computed `comparability`
   block — marketplaces, calendars, currencies, members with no public reporting — printed
   at `check` and `declare` and stamped into the frozen file. **Warned, not refused**: a
   global group is what §2 asks for, and refusing would push the choice back to the home
   market. The remaining weakness is that nobody is forced to act on the warning.

4. **Grocery ASIN continuity is worst exactly for the core SKU.** The flagship
   Coca-Cola 12-can pack is re-listed under fresh ASINs repeatedly; the ASINs that survive
   a decade are odd formats and legacy variants. So choosing for continuity in
   fast-moving grocery pulls *away* from the flagship pack. Not fixed, because it is a
   property of the marketplace: the three soft-drink products are a 12 oz glass 24-pack, a
   16.9 oz PET 6-pack and a 20 oz PET 12-pack. All three are the regular flavour in a
   bottle multipack, and the format mismatch is on record rather than solved. Packaging
   and shipping complaints differ by format, which the tone scorer's
   `seller_or_shipping_only` flag mitigates but does not remove.

5. **An electronics ASIN lives about three years.** One headphone model holds one ASIN for
   its life, which is far better than grocery, but a generation change ends the series.
   Handled by preferring the current generation early in its life — WH-1000XM6 over XM5,
   Tour One M3 over M2 — which is the clearest case where continuity and accumulated
   review volume point in opposite directions. Unfixable in general: in roughly three
   years these series end and are recorded as ended, never swapped for a successor.

6. **Accessory listings crowd out the first-party product.** Searching amazon.com for
   `JBL Tour One M3 Black` returned fourteen results, every one a third-party ear pad,
   case or headband. The brand check does not catch this, because the accessory's title
   carries the brand too. What catches it is comparing `title_at_declare` against the
   served title and reading it. A future automated picker would need more than the brand
   check.

7. **Both lexicons are gameable by an agent that knows they exist.** They catch the honest
   slip, which is the common case, and they make the basis of a choice legible to a reader
   a year later. They do not certify that a reason is sound. There is a second-order risk
   that a refusal teaches evasive prose rather than a better reason; mitigated only by
   refusing by name and never rewriting, so the refusal is visible rather than silently
   smoothed over.

8. **`post_collection` is a witness, not a lock.** Nothing prevents declaring a revised
   group after seeing tone. The mark is permanent and travels in the sidecar, which makes
   a late revision visible to anyone who looks — and makes no difference to anyone who
   does not.

9. **Carried a tool change forward as v2 rather than editing v1.** The comparability block
   was added after the first two groups were already frozen. Rather than edit two frozen
   files, both were revised to v2 with the reason recorded and an empty
   `member_diff_from_previous` — which is itself the useful signal: the revision changed no
   member. Noted because it is the first real exercise of the revision path, and because
   the alternative, deleting and re-declaring, was the tempting one.

### Known-good baseline

```bash
python3 .claude/skills/review-tone-peer-group/tests/test_declare_group.py   # 102 checks
python3 .claude/skills/review-tone-peer-group/scripts/declare_group.py verify  # 5 versions
```
