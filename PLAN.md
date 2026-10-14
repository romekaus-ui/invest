# Plan

What has been decided and what is next. Short by design — the rules live in
[`CORE-PROMPT.md`](CORE-PROMPT.md), not here.

## Decided

- **Fresh Amazon reviews**, not a frozen research corpus. The corpus-based harness is
  archived under `archive/historical-harness/`.
- **Global peer groups**, not a US SIC-screened universe. Companies belong together
  because they sell substitutable products, wherever they are listed.
- **Collection route A is the user's logged-in Amazon session**, because sort-by-recent
  is only available behind login, and that is the difference between a time series and
  a popularity ranking. Commercial provider APIs are route B, for scale.
- **Second specification: peer-relative tone.** A company's tone minus its peer group's
  mean, alongside absolute tone. A category-wide shock moves every brand together, and
  absolute tone reads that as company news.
- Providers on **free tiers** until the real volume is known. No spend yet.

## BLOCKED on one human action

**Sign up for Apify's free tier ($5/mo credit, no card) and export `APIFY_TOKEN`.**

Nothing else moves until that number exists. Route A was measured on 2026-10-12 and
cannot carry the project: 8 US reviews per product, ~0.09% coverage, and — decisively —
no way to ask for what is new, so repeated collection re-reads the same rows instead of
accumulating a series. Route B is unmeasured because no provider token exists and
creating accounts is the user's step, not the assistant's (CORE-PROMPT §9).

The collector runs `fetch --provider apify --group <id> --asin <asin>` as soon as the
token is there, and applies the same date-order check Route A just failed, so Route B is
judged on identical terms. Three numbers decide the project's shape:

| Measurement | If good | If bad |
|---|---|---|
| reviews returned per product | a few hundred | the same ~8 |
| dates genuinely descending | a real series | no series |
| how far back they reach | cadence is buildable | cadence is pointless |

**If a provider returns the same ~8 from the same public surface** — a real possibility,
since several of them scrape the page just measured — then the constraint is Amazon's,
not the route's, and the project needs a **different premise**, not a different
provider. Do not wire stage C's cadence, and do not let the dashboard promise a trend,
until it is known which.

## History mode: fails on product availability, 2026-10-14

**Not on budget, and not on the route.** Measured across six groups and twelve companies.

Reaching 2024 needs products in a narrow velocity band (~0.098 reviews/day, ~50-100
reviews inside the window), and **four such products per company** to clear the
30-review quarter floor. The candidates that exist:

| company | product | ratings | verdict |
|---|---|---|---|
| Dyson | V12 Detect Slim | 132 | in band |
| Dyson | V15 Detect TCE | 59 | band edge |
| Arlo | Pro 2K HDR | 50 | in band |
| GoPro | HERO13 | 1,046 | ~10x too fast |
| Casper | Element | 3 | far too slow |

**Dyson is the only one of twelve companies with two plausible in-band products, and the
plan needs four.** Worse for the purpose: **Dyson is private** — no revenue to predict —
and Arlo was already dropped on concentration. So of the in-band products found, **none
belongs to a company whose revenue could be read.**

**Two corrections that removed the cheap screen:**

1. **`reviews_per_day` cannot be estimated from a page fetch.** *Date First Available* is
   absent from most `/dp/` static HTML (present for 1 of 3 sampled directly, 0 of ~20
   resolved earlier). Where present it is the **child's** listing date while
   `total_ratings` is the **parent's** pooled count — goodr `B0GFSJZ5DH` reads as
   108/day from a 89-day-old child against a family's 9,662 ratings. Ratings are free;
   age is not; and they are not measured at the same level. "Screening is free" was
   wrong, and it was what made four-products-per-company cheap.
2. **The screen is only meaningful where `parent_asin == asin`.** Only **7 of ~25**
   resolved products are their own parent; for the rest the count is the family's.

**Caveat, since the conclusion rests on it:** those listing ages are inferred from product
generation, not read from the page. Indicative, not measured. Settling it needs the
parent's first-available date (not exposed) or one collection pass per candidate — the
cost the band screen existed to avoid.

**Consequence: do not buy a plan tier for history.** Forward collection is unaffected —
flagship products are exactly what recency mode wants.

## The sensor selects the universe, and it has a bias

The 10x asymmetry rule works, and applying it exposed something about what this project
can ever see. Results on six groups:

- `clogs-and-sandals` — **refused at 225.71x**. Crocs Classic Clog 489,107 ratings
  against Birkenstock's largest findable listing at 1,877; Birkenstock is 62% B2B by its
  own 20-F. Not rescuable: it would need 48,911.
- `athletic-footwear` — **the rule improved it**. 18.05x as drafted (Skechers 44,728 vs
  On 2,478); dropping Skechers gives **1.09x** with two revenue-readable members.
- `upright-and-stick-vacuums` — 15.24x → **2.92x** once Dyson tracks three stick vacuums
  across its range. Legitimate: adding products to a company Amazon genuinely sees is not
  what Birkenstock would have needed.
- `action-cameras` 4.59x · `sunglasses` 6.66x · `mattresses` 1.10x — pass.

**The healthiest groups are the two all-private ones.** DTC brands meet their customers on
Amazon symmetrically; retail-floor brands do not. So the sensor sees DTC brands well and
retail brands poorly, **and that bias now selects the universe** — the rule killed the
group holding the biggest brand name. Worth weighing against the chosen 10x, which remains
arguable.

## Providers evaluated, so they are not re-litigated

| provider | verdict | why |
|---|---|---|
| **Apify** | **in use** | `junglee` and `delicious_zebu` both return real reviews. $0.006/review free tier, tiering down on paid. 100/filter, star fanout multiplies reach |
| Rainforest | no | reviews endpoint returns `503 "temporarily unavailable"` independent of credentials |
| **ScrapingBee** | **no reviews endpoint at all** | Its API documents exactly three: Search, Product, Pricing. The Product response carries `"reviews": [], "reviews_count": 114` — the count, not the reviews. Its `sort_by: most_recent` sorts **search results**, i.e. products, not reviews. The "Customer reviews" bullet on the features page is not backed by an endpoint |
| `scrapehero-code/amazon-review-scraper` (GitHub) | no | Tutorial-grade `requests` + Selectorlib, no pagination, no filters, no anti-bot handling. Its target URL now redirects anonymous requests to Amazon's `/ax/claim` challenge: **0 review blocks returned**, verified 2026-10-14 |

**A cheaper request price would buy breadth, never depth.** The ~100-per-filter ceiling is
Amazon's. No provider moves it, so the velocity band stands whatever is paid.

**One path is refused on principle, not on price.** Fetching `/product-reviews/` through a
third party's raw-HTML endpoint means beating the login wall, which means **sending the
user's Amazon session cookies to that third party**. That is not a trade to make for a
lower price per review, and it is not set up on the user's behalf. If it is ever wanted it
is their explicit, informed decision — never a config change made for them.

## Next

1. **Peer-group finder** — in build. Target **at least 80 companies** in however many
   groups fall out naturally (not 40 forced pairs — bigger groups are a better control),
   **subject to the concentration filter below, which outranks the count.**
2. **Fresh review collector** — in build. Both routes behind one interface.
3. **Calibrate the tone scorer** — the user labels ~100 reviews by hand so the scorer's
   agreement can be measured. Nobody else can do this step, and the scorer's output is
   not a measurement until it is done.
4. **Choose the collection set and the cadence**, declare both, start collecting.
5. **Keep collecting.** Each run is one point in the series.

## Pool size — measured, 2026-10-12

| | count |
|---|---|
| US registrants SEC knows | 30 |
| …with a disclosed revenue split to read | **25** |
| Listed outside the US (evidence read by hand) | 15 |
| Private (category controls, no revenue to read) | 26 |
| US tickers SEC no longer lists | 6 |

**Ceiling for revenue-readable companies in this pool: 45.** Only 25 have a readable
split today. Reaching the upper end needs *more categories*, not more names inside these.

Plan for **40–50 revenue-readable across 20–30 groups**, private peers as the rest.
80 companies in total is reachable; 80 revenue-readable is not.

The six delisted names — iRobot, Sleep Number, Skechers, Vista Outdoor, Voxx, Solo
Brands — are the structural point: focused consumer companies are exactly the
take-private targets, so the public half of this pool erodes while the private half does
not.

## The concentration filter

**A company belongs in a group only if one main product line dominates its revenue, and
that line is what sells and gets reviewed on Amazon.**

Review tone on a handful of products can only say something about a company's revenue if
those products *are* most of the revenue. A company with 800 SKUs across four segments
can have souring reviews while revenue is driven by something nobody reviews.

- Concentration is read from the company's **own filing or annual report** — segment
  disclosure, revenue by product category — and recorded with the group. Never asserted.
- **Private peers count.** For the tone comparison a private competitor is a perfectly
  good category control; only the company whose revenue you eventually want needs to be
  public. Many of the best focused competitors are private (Dyson, Bose, Stanley/PMI).
- Pepsi/Coca-Cola is the pairing *illustration*, not the quality bar: PepsiCo is roughly
  55% snacks. Closer to what is wanted: Celsius/Monster/Zevia, Crocs/Birkenstock,
  YETI/Stanley, Traeger/Weber, Purple/Casper/Sleep Number, Sonos/Bose,
  iRobot/SharkNinja, e.l.f./Coty.
- **Quality outranks the count.** 40 genuinely focused companies beats 80 where half are
  diversified. If the pool of focused, Amazon-selling companies runs out below the
  target, that is a defining fact about the project and gets reported, not padded
  around.

## Open, and worth deciding before step 4

- **80 companies is cheap to find and may be expensive to collect from.** The finder
  costs nothing per company; review collection costs time (route A) or money (route B)
  per product per cycle. If route A turns out to be slow, the collection set will be a
  subset of the 80 and the rest stay as declared-but-not-collected. That is fine —
  what matters is that the subset is chosen before tone is seen, not after.
- **Revenue across jurisdictions** is unsolved beyond US filings. `fetch_sec.py` handles
  US registrants correctly. EU and UK half-yearly reporting and Japanese TDnet/EDINET
  filings have no equivalent yet. Not blocking: tone is comparable across borders even
  when revenue is not.
- **The weekly cloud routine `trig_01MExfvhuF2g6841QWZ8htBR` is stale.** It runs the
  archived orchestrator and will fail. Pause it; it needs a new runner once collection
  exists.

## What may be claimed, today

Nothing about prediction. No review has been collected. `CORE-PROMPT.md` §8 governs: the
series starts the day collection starts, a trend claim needs a declared number of
readings, and a predictive claim needs the test written down before the revenue arrives
and then several reporting periods to run.
