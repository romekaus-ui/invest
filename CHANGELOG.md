# Changelog

The live consumer-tone monitor (`CORE-PROMPT.md`). The previous design's changelog is
archived at `archive/historical-harness/REVIEW-TONE-CHANGELOG.md` and is not continued
here.

---

## 2026-10-12 (later) — schema 2, and the evidence record made tamper-evident

`CONTRACTS.md` §1 was amended the same day (A1–A4 adopted on `main`, `5473f2f`), and a
peer pointed out the one artefact in this component that a silent edit would not have
shown. Both addressed. 147 + 27 tests, 12 versions hash-clean, stage G satisfied for all
six groups.

### Schema 2

§1 now requires `parent_asin` per product, `no_public_report` / `revenue_readable`
explicit on every member, and `fy_end` in `reporting`. `SCHEMA_VERSION` is 2 and the six
groups were **revised to v2 rather than edited** — the contract genuinely changed, the
member diff is empty, and an empty diff is exactly what a schema-only revision should
leave behind.

`fy_end` is required only for a member whose revenue will be read, and must be *empty*
for one with `no_public_report: true`: nothing publishes a year end for a private
company, so inventing one would be the same class of mistake as inventing a revenue
share. `parent_asin` may be absent from a draft when `check-products` recorded it in the
verification block; the freeze lifts it onto the product, so a frozen file always carries
it.

### The evidence record is now append-only and hash-chained

Raised by the peer who settled the contract, and it was right: `pool_evidence.json` feeds
the counts everyone quotes, and it was the one artefact here held to a lower standard
than the declarations — **while being the artefact this component had already edited.**
Two poisoned records were deleted from it under defect 11. Deleting them was correct;
leaving no trace was not, and a reader afterwards could not tell "never fetched" from
"fetched and wrong".

| File | What it is |
|---|---|
| `pool_evidence.jsonl` | append-only, hash-chained. **The record.** |
| `pool_evidence.json` | snapshot derived from the log, regenerated |
| `pool_evidence.hash` | sha256 of the snapshot |

Every line carries `prev`, the sha256 of the line before it. A withdrawal is an appended
record with a reason, never a deletion. `evidence_log.py verify` exits 2 on a broken
chain, a snapshot that disagrees with the log, or a snapshot edited after it was hashed —
each case has a test that performs the tamper and checks it is caught.

The two deleted records are reconstructed in the log and **marked as reconstructions**,
not dressed up as contemporaneous records, and the opening line says plainly that
everything before it is unwitnessed: a backfill cannot prove the file was not edited
before the log existed. When the genesis line turned out to put a stray `__genesis__` key
in the derived snapshot, the fix was a correction appended to the log and a change to
`derive`, **not an edit of the opening line** — editing it would have broken the chain
and defeated the point of having one.

### The check that would have caught defect 11

`screen_pool.py` now compares the entity name SEC returned against the company name
asked for, and **refuses to record evidence on a mismatch** rather than recording it with
a warning. Wrong evidence that looks right is worse than none.

Tested against the real awkward cases rather than only the obvious ones: it rejects
`EssilorLuxottica S.A.` / `ESTEE LAUDER COMPANIES INC` and `Societe BIC` /
`BLACKBERRY Ltd`, while accepting `Helen of Troy Limited (Hydro Flask)` /
`HELEN OF TROY LTD`, `Church & Dwight Co., Inc. (Waterpik)` /
`CHURCH & DWIGHT CO INC /DE/` and `The Simply Good Foods Company` /
`Simply Good Foods Co`. A parenthetical brand name, a `/DE/` suffix and a dropped article
must not read as a different company, or the check gets switched off.

### Defect

13. **A no-op assertion sat in the new test file.** `check("the snapshot matches the log",
    el.derive() == el.read_json_snapshot() if hasattr(el, "read_json_snapshot") else
    True)` — `read_json_snapshot` does not exist, so the ternary always evaluated `True`
    and the check passed without testing anything. Caught on reading the file back, not
    by running it, because a test that always passes looks exactly like a test that
    passes. Replaced with two real assertions: the snapshot on disk equals what the log
    derives, and the hash sidecar matches the snapshot bytes. Worth recording because the
    whole point of this entry is tamper-evidence, and a test that cannot fail is the
    software equivalent of the mutable evidence file.

---

## 2026-10-12 — peer groups: the contract, the concentration filter, and six groups

Adopted `CONTRACTS.md` §1 as the declaration's own shape, added PLAN.md's concentration
filter with evidence fetched from filings, and declared **six groups covering 15
companies**. 134 tests, all six hash-clean, stage G satisfied for each.

### Built to the contract rather than translated into it

`CONTRACTS.md` §1 field names are now the declaration's field names — `venue`,
`products[].name`, `reporting.{calendar,currency,source}`, a string `seed`, a date
`declared_at`. One schema, no translation layer. Two paths:

- `data/peer_groups/<id>.v<N>.json` — the **declaration of record**: append-only,
  hash-chained, never rewritten.
- `groups/<id>.json` — the **§1 view** the workflow reads, generated from the current
  version with its own in-file `hash` (sha256 of the file minus the hash field). A view
  that fails its own hash is rebuilt from the record with `reemit`, never patched.

Stage G is invoked as `declare_group.py --group <group_id>`, exactly as §4 requires, and
reads a group id and nothing else. Exit codes are §4's: `0` satisfied, `2` integrity
violation, `3` not built.

Also added `schema_version`. `verify` hash-checks every frozen version but
contract-validates only those at the current schema, reporting an older one as *not
revalidated* rather than broken — otherwise one schema change makes every past
declaration look like an integrity violation and the real ones are lost in the noise.

### Four proposed amendments to CONTRACTS.md §1 and §2

Raised because the peer who settled the contract asked for them, and because each one
cost something real to work around. The component conforms to §1 as written in every
case; these are additive.

**A1 — a private member cannot satisfy "no evidence, no member".** §1 holds two rules
that collide: every member needs concentration evidence from its own filing, *and*
`venue: "private"` is valid and useful. Bose, Stanley/PMI, Trove, Red Bull and Weber
publish no periodic report, so the first rule deletes the members the second asks for —
and those are five of the best category controls available. Resolved by asking what the
evidence is *for*: concentration exists so a tone series can be joined to a revenue
series, and a private member has no revenue series to join. A private member may set
`no_public_report: true`, which still requires saying what it sells and where that was
established, forces `share_of_revenue: null`, and sets `revenue_readable: false`. A
listed member is never exempt. **Suggested §1 change:** make the evidence requirement
read "every member whose revenue will be read".

**A2 — §2 should carry `parent_asin` beside `asin`, and this is the one most likely to
bite.** Amazon serves reviews at the *parent* ASIN, pooled across every variant. Of the
15 products declared, **14 are variant children**: the Premier Protein chocolate 12-pack
`B07MJL8NXR` resolves to parent `B0BLGMF97X`, which pools every Premier Protein shake
flavour and pack size. So §2's `reviews.csv` keyed on `asin` alone will contain the
parent's reviews under a child's id, and dedup by `review_id` will not notice. Three
consequences worth writing into the contract: product-level tone is really
variation-family-level tone and should be described that way; choosing a product for
continuity does not buy continuity of the review pool, because Amazon re-parents; and two
members tracking children of one parent would be scored on identical reviews. The
declaration records `parent_asin` and refuses a group where two members share a parent.

**A3 — `reporting.source`'s four values cannot express a global group.** §1 allows `SEC |
companies-house | EDINET | none`. Sonova files to SIX, Samsung to the Korean DART system,
Sony to TDnet, Red Bull is an Austrian GmbH. Extended to add `TDnet`, `SIX`, `DART` and
`other-national`, keeping §1's four spelled exactly as §1 spells them.

**A4 — §1's `reporting` has no `fy_end`, and CORE-PROMPT §5 needs one.** Of the members
declared, the fiscal year ends are `1231`, `0102` (YETI), `1003` (Sonos), `0930`
(BellRing), `0826` (Simply Good) and `0331` (Logitech). Five of six are not 31 December,
and two are 52/53-week years that move annually. §5 forbids aligning two companies'
figures as if their calendars matched; without `fy_end` a downstream stage cannot tell
that they do not. Recorded as an additive field.

### The concentration filter

**A company belongs in a group only if one main product line dominates its revenue, and
that line is what sells on Amazon.** Added as required validation: `dominant_line`,
`share_of_revenue` (a fraction in (0, 1] **or null**), `evidence`, `source_url`.

Evidence is fetched, never asserted. `scripts/screen_pool.py` and
`declare_group.py concentration` pull each US registrant's latest annual report and
capture verbatim windows around a fixed probe set — reportable segments, revenue
disaggregation, revenue by product category — lifted from the archived scorecard's c3 and
c6 probes, which were solving this same question and were right about where to look. The
agent reads the share off that text; the URL recorded is the filing the script fetched.

| Judgement rule | Value | Why |
|---|---|---|
| Concentration floor | **0.60** | PLAN.md states the filter with no number, so one is chosen and recorded. Permissive on purpose: admits a clear dominant line plus a real secondary business, excludes the four-segment conglomerate. The most arguable constant in the component, and it is one constant |
| `share_of_revenue` nullable | yes | A single-segment filer discloses that management runs one business, not that one product is 100% of revenue. **7 of 15 members carry null** with real evidence; inventing 1.00 would have been the dishonest alternative |
| Level of `dominant_line` | the level the **tracked products** sit at | YETI's drinkware alone is 58% and fails; its drinkware and coolers together are 98% and pass, and both are what it sells on Amazon. Traeger's grills alone are 53%; grills + pellets + accessories are 100%. Widening is legitimate only if the tracked products span the wider line |
| `evidence` lexicon | full performance lexicon, brands exempt only | A citation never needs the word "margin". One that reaches for one has stopped citing and started arguing |

### The finding that reframes the component

**The concentration filter, not substitutability, decides most memberships — and it rules
out the household names.**

The substitutability test admits Coca-Cola, PepsiCo, Sony, Apple, Samsung and Sonova. The
concentration filter rejects every one except Coca-Cola: PepsiCo is roughly 55% snacks,
headphones are a rounding error in Sony and Apple, JBL is a rounding error in Samsung,
and Sennheiser consumer audio is small inside Sonova's hearing-aid business.

So **the two groups declared on 2026-10-11 as the component's smoke test — carbonated soft
drinks and over-ear headphones — are the wrong shape for this project**, and both were
retired to `data/peer_groups/pre-contract/` rather than rewritten. They are kept because
they are the evidence that the component was tested against live data, and the two
defects that run exposed are below.

What the filter wants instead is focused mid-caps and private companies: Celsius, YETI,
Traeger, Sonos, Turtle Beach, BellRing, Crocs, e.l.f., GoPro, Purple. PLAN.md's own
archetype list was right and the Pepsi/Coca-Cola framing is an illustration of pairing,
not a quality bar.

### Six groups declared

Every ASIN resolved live against amazon.com; every share quoted from the filing linked in
the declaration.

| Group | Members | Concentration |
|---|---|---|
| `energy-drinks` | Celsius `NASDAQ:CELH`, Monster `NASDAQ:MNST`, Red Bull `private` | Monster **0.924** ("our Monster Energy Drinks segment represented 92.4% … of our net sales"); Celsius null (single operating segment) |
| `insulated-drinkware` | YETI `NYSE:YETI`, Stanley/PMI `private`, Trove/Owala `private` | YETI **0.98** (drinkware 58% + coolers 40%) |
| `wireless-speakers` | Sonos `NASDAQ:SONO`, Bose `private` | Sonos **0.777** (speakers $1,121,808k of $1,443,276k) |
| `barbecue-grills` | Traeger `NYSE:COOK`, Weber `private` | Traeger **1.00** (grills $298,026k + consumables $127,474k + accessories $134,020k = all of $559,520k; grills alone 53.3%) |
| `protein-shakes-and-bars` | BellRing `NYSE:BRBR`, Simply Good `NASDAQ:SMPL` | BellRing **0.817** (shakes $1,892.9m of $2,316.6m); Simply Good null (three brands aggregated into one reportable segment) |
| `gaming-headsets` | Turtle Beach `NASDAQ:TBCH`, Corsair `NASDAQ:CRSR`, Logitech `NASDAQ:LOGI` | all null — single or all-gaming segments, no product-line split disclosed |

`protein-shakes-and-bars` and `gaming-headsets` are the only two with **no comparability
warnings at all**: one marketplace, one calendar, one currency, no private member. The
other four each carry a private member and therefore mixed calendars, which is correct
and recorded rather than smoothed over.

### Scale: what 80 companies actually costs, and whether the pool runs out

The target is ≥80 companies in however many groups fall out naturally, subordinate to the
filter. Measured rather than estimated:

**Cost per stage, measured.**

| Stage | Cost | Scales? |
|---|---|---|
| Screen a pool against filings | **0.7s per company** — 31 filings in 24s | Yes. 80 companies is under a minute |
| Resolve CIK + reporting calendar | within the same EDGAR pass | Yes |
| Resolve and verify ASINs | ~3s per product, paced | Yes |
| **Find a good ASIN per member** | **~1 marketplace search per member, and it needs reading** | **No. This is the bottleneck** |
| Read a share off the evidence | ~1 filing window per member, needs reading | No |

So the mechanical half scales to 80 and beyond; the judgement half costs roughly **one
marketplace search and one filing window per company**, and that is what 80 companies
costs. Six groups and 15 companies were declared in this pass. **At this rate 80
companies is about five more passes of the same size** — not a different kind of problem,
just more of the same one.

**Does the method degrade as it goes?** Not observably across six groups, because the
contract is mechanical: the 6th group was refused for the same reasons as the 1st and the
checker does not get tired. What degrades is **category quality, not method quality** —
and it degrades for a reason worth stating. The categories were worked in roughly
descending order of how obviously focused their companies are, so later groups needed more
hunting for a member that passes the filter. The failure mode to watch is not a sloppier
50th group; it is a 50th group padded with a diversified member because nothing focused
was left in that category. The floor is what stops that, and `share_of_revenue: null`
without evidence is what it would look like if it were happening.

**The pool.** `data/peer_groups/candidate_pool.json` holds 25 categories and 74 distinct
companies, screened in `pool_evidence.json`:

| | Count |
|---|---|
| Distinct candidate companies | 74 |
| US registrants SEC knows | 30 |
| …with a disclosed revenue split to read | **25** |
| Listed outside the US (evidence by hand) | 15 |
| Private (category controls, no revenue) | 26 |
| **US tickers SEC no longer lists** | **6** |

**The defining fact, and the user should hear it plainly: the pool of focused,
Amazon-selling, publicly-reporting consumer companies is roughly 40–60, not 80+, and it is
shrinking.** Six of 36 US candidates (17%) have left the public market recently — iRobot
`IRBT`, Sleep Number `SNBR`, Skechers `SKX`, Vista Outdoor `VSTO`, Voxx `VOXX`, Solo
Brands `DTC` all failed to resolve in SEC's ticker index. Focused consumer companies are
exactly the take-private and delisting targets, so the public half of the pool erodes
while the private half does not.

The target is still reachable as **80 companies in total**, because private members count
and there are 26 of them here. It is *not* reachable as 80 companies whose revenue can
eventually be read: this pool's ceiling is **45** (30 US registrants plus 15 listed
elsewhere), and only 25 of those have a disclosed split to read today. Reaching the high
end would need more categories, not more names in these ones. Planning should assume
roughly **40–50 revenue-readable companies** across 20–30 groups, with private peers
making up the rest as category controls. Padding to 80 public names would require
admitting diversified companies, which is the one thing the filter exists to prevent.

### Defects found

1. **The availability check called every in-stock product unavailable.** Found on the
   first live run: five flagship products, all in stock, all classified `unavailable`. An
   Amazon product page embeds a localisation table carrying the literal string
   `"Currently unavailable."` on *every* page, as the caption the variant selector would
   show if a colour were out of stock. A whole-page substring scan matched everywhere.
   Fixed by stripping `<script>` and `<style>` bodies before any marker is looked for, and
   reading availability from the `#availability` buy box. Pinned by a regression test that
   puts the string inside a `<script>` and asserts `resolved`. **The defect to remember:**
   the check passed its own unit tests on synthetic HTML and was only shown to be inverted
   by live data.

2. **The performance lexicon refused a brand name.** `Momentum` is Sennheiser's headphone
   line and `momentum` is a performance word, so a member was refused for naming the
   product it sells. Fixed with a narrow exemption: a phrase that is one of *that
   member's own declared brands* is not a hit. It cannot be used to smuggle a performance
   word past the check, because declared brands are themselves checked against the titles
   the marketplace served.

3. **A group can be admissible and still be a bad instrument.** `energy-drinks-global`
   (retired) passed the contract clean while mixing amazon.co.uk with amazon.com. Amazon's
   reviewer populations are national, so a peer-relative tone gap across those is partly a
   gap between British and American shoppers before any company difference. Added the
   computed `comparability` block, printed at `check` and `declare` and stamped into the
   frozen file. **Warned, not refused** — a global group is what §2 asks for. The
   remaining weakness is that nobody is forced to act on the warning.

4. **Reviews are not served at the ASIN you choose.** See amendment A2. 14 of 15 products
   are variant children. This is the most consequential defect found and it is not fixed,
   because it cannot be: the parent is Amazon's unit, not ours. It is recorded per product
   so a re-parenting is detectable, and groups sharing a parent are refused.

5. **Grocery ASIN continuity is worst exactly for the core SKU.** The flagship Coca-Cola
   12-can pack is re-listed under fresh ASINs repeatedly; the ASINs that survive a decade
   are odd formats and legacy variants. Choosing for continuity in fast-moving grocery
   pulls *away* from the flagship pack. A property of the marketplace, not fixable here.

6. **An electronics ASIN lives about three years.** Handled by preferring the current
   generation early in its life — WH-1000XM6 over XM5, Tour One M3 over M2 — the clearest
   case where continuity and accumulated review volume point in opposite directions.
   Unfixable in general: those series end in roughly three years and are recorded as
   ended, never swapped for a successor.

7. **Accessory listings crowd out the first-party product.** Searching amazon.com for
   `JBL Tour One M3 Black` returned fourteen results, every one a third-party ear pad,
   case or headband; `Traeger Pro 575` and `Weber Spirit E-310` behaved the same way. The
   brand check does not catch it, because the accessory's title carries the brand too.
   What catches it is comparing `name` against the served title and reading it. **A future
   automated product picker would need more than the brand check**, and this is the
   clearest remaining obstacle to declaring groups without a human reading each title.

8. **Both lexicons are gameable by an agent that knows they exist.** They catch the honest
   slip, which is the common case, and they make the basis of a choice legible a year
   later. They do not certify that a reason is sound. Mitigated only by refusing by name
   and never rewriting, so a refusal is visible rather than silently smoothed over.

9. **`post_collection` is a witness, not a lock.** Nothing prevents declaring a revised
   group after seeing tone. The mark is permanent and travels in the sidecar, which makes
   a late revision visible to anyone who looks and makes no difference to anyone who does
   not.

11. **The screener fetched the wrong companies' filings, and called them by the right
    companies' names.** SEC's ticker index is keyed on **symbol alone**, so a non-US venue
    matched a US company that happens to share the symbol: `EPA:EL` (EssilorLuxottica,
    Paris) resolved to `EL` (Estée Lauder, New York) and `EPA:BB` (Société BIC) resolved
    to `BB` (BlackBerry). Both fetched a real 10-K, both produced plausible-looking
    concentration evidence, and nothing in the output looked wrong — the entity name SEC
    returned was the only tell. Caught while re-checking pool counts for this entry, not
    by any check in the component.

    Fixed: the exchange is now tested against a US set *before* the symbol is looked up,
    so a venue outside it is never resolved mechanically. The two poisoned records were
    deleted from `pool_evidence.json` rather than corrected in place. No declared group
    was affected — neither company is in one — but **this is the defect class that would
    have been worst**: a wrong share of revenue attributed to the right company, carried
    into a declaration with a real SEC URL beside it. The lesson is the same as defect 1:
    the component's own checks passed, and only a cross-check against something it had
    not been asked about exposed it. `screen_pool.py` prints the entity name SEC returned
    for exactly this reason.

12. **The 0.60 floor is the weakest number here.** PLAN.md states the filter without one.
    0.60 admits Sonos at 0.777 and would admit a company at 0.61 whose other 39% nobody
    reviews. A stricter 0.75 would have excluded nothing declared here, which is itself
    evidence the floor is not currently doing much work — the real screening came from
    reading the filings.

### Known-good baseline

```bash
python3 .claude/skills/review-tone-peer-group/tests/test_declare_group.py   # 134 checks
python3 .claude/skills/review-tone-peer-group/scripts/declare_group.py verify
for g in energy-drinks insulated-drinkware wireless-speakers barbecue-grills \
         protein-shakes-and-bars gaming-headsets; do
  python3 .claude/skills/review-tone-peer-group/scripts/declare_group.py --group $g
done
```

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
