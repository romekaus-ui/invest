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
- **Route C, from 2026-10-15: brand-hosted review-platform display APIs** (Bazaarvoice,
  PowerReviews, Yotpo). Free, uncapped, true date order, up to 12.5 years of history,
  and it reaches public companies. It is now the primary source for *history*; Amazon
  stays as the independent control on brand moderation. Its own batches carry the same
  provenance contract (`CORE-PROMPT.md` §3: route, sort, filters, timestamp, product id,
  requested, returned, total claimed) with `route` naming the platform and deployment.

## No longer blocked — superseded 2026-10-15

Everything below in this section described a project blocked on an Apify token because
Amazon was the only conceivable surface. **It is not blocked any more.** Traeger
(NYSE:COOK) yields 28 consecutive quarters of dated review text for free, with no
account and no cap, and five more public companies clear two years. See *"The
revenue-readable member is confirmed"* below. The Apify route stays as the Amazon
control described in `CORE-PROMPT.md` §3 — an independent check on a brand's own
moderated data, which is exactly what it is now needed for — but nothing waits on it.

Kept verbatim because it is the record of what was believed and why:

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

## Yotpo: confirmed, and it changes the depth question

Measured 2026-10-15 against Stanley's own widget API. No account, no login wall,
no cost.

| | |
|---|---|
| reviews available | **8,193** for one product |
| pagination | pages 1-82 return 100 each, 83 is empty. **No cap** |
| date order | genuinely descending, verified against the dates |
| reach | **2021-03-29 to 2026-08-10 - 5.4 years** |
| text | **100 of 100** carry real review text |
| newest review | 2026-08-10, i.e. **~2 months stale** |
| score spread | 1*:13 2*:2 3*:7 4*:4 5*:74 - not obviously curated |

Page 20 lands in March 2025, page 50 in January 2023, page 82 in March 2021. This is
the thing Amazon's ~100-per-filter cap makes impossible, available for free.

**Four caveats, and the first is structural.**

1. **These reviews are hosted and moderated by the company being measured.** Amazon's
   are not. A firm has the most reason to delete negative reviews precisely when tone
   would otherwise fall - the exact moment the series is supposed to be informative. The
   schema carries a `deleted` field, which means deletion is a thing that happens;
   0 flagged on page 1 is not evidence that none ever are.
   **Deletion test started:** `data/_route-tests/yotpo-stanley-snapshot-2026-10-15.json`
   holds 300 review ids taken today. Re-pull the same three pages **on or after
   2026-10-22** and diff the id set. Do not infer the answer; watch it.
2. **~2 months of freshness lag.** Amazon served a review 2 days old; Yotpo's newest is
   2026-08-10. Fine for history, useless for a recency cadence.
3. ~~**Coverage is unknown.**~~ **Measured 2026-10-15** — see *"Coverage: the route
   reaches less than half of what is declared"* below. Two of seven declared groups have
   both members reached; three yield nothing. The guesses in this line were mostly wrong:
   YETI and Traeger do serve Bazaarvoice and both are reachable, but **Sonos serves no
   reviews at all** (PowerReviews present with a null merchant id), and the keys turned
   out not to be script-injected — Bazaarvoice's own CDN publishes them in a static file.
   **A route that reaches one member of a peer group cannot support peer-relative tone**,
   which is the project's second specification, and that is now recorded per group.
4. ~~**Stanley is private.**~~ **Resolved 2026-10-15.** Traeger (NYSE:COOK), YETI
   (NYSE:YETI), BellRing (NYSE:BRBR), Simply Good Foods (NASDAQ:SMPL) and Turtle Beach
   (NASDAQ:TBCH) are all reachable and all public. Stanley keeps its job as the
   `insulated-drinkware` control.

**It does not revive history mode until a revenue-readable member is confirmed.** What it
establishes is that the 2-year wait is not necessarily the only path - which it was,
an hour ago.

## The revenue-readable member is confirmed. 2026-10-15.

**Traeger, Inc. (NYSE: COOK). 28 consecutive quarters of dated review text above the
30-review floor, 2019Q2 through 2026Q1.** Free, no account, no cap. The 2-year wait is
over before it started, and the history question is settled for this company.

Full measurements, request syntax and every dead end:
`data/_route-tests/brand-widget-routes-2026-10-15.json`.
Coverage per declared group: `data/_route-tests/group-coverage-2026-10-15.json`.
The tool that produced those numbers, kept beside them so they are reproducible rather
than merely described: `data/_route-tests/measure_widget_route.py`. It is a route test,
not a collector — `measure_widget_route.py quarters <files…>` re-derives the 28-quarter
claim and the pooling check below.

**Nothing here was routed around.** The display keys are the ones the platforms' own CDNs
publish to every visitor. No `Referer` or `Origin` header was sent anywhere, no account
was created, and the one endpoint that refused — Bazaarvoice's `bfd` proxy, 401 — was
recorded and left alone.

**What the route is.** Bazaarvoice, PowerReviews and Yotpo each have a documented
*display* API whose key is published in the clear by the platform's own CDN to every
visitor's browser. Reading that key and calling that endpoint is what the page does.
For Bazaarvoice: `apps.bazaarvoice.com/deployments/<client>/<site>/production/<locale>/bv.js`
gives `legacyScoutUrl`; that file (**fetch it gzipped**, raw it is a binary blob)
publishes `passkey:"ca…"`; then
`api.bazaarvoice.com/data/reviews.json?apiversion=5.4&passkey=…&Filter=ProductId:eq:<id>&Limit=100&Offset=<n>&Sort=SubmissionTime:desc`.
`Limit` maxes at 100 and `Offset` paginates to exhaustion.

**Measured on the same terms as every other route:**

| | Traeger · BV | YETI · BV | BellRing · PR | Quest/SMPL · BV | Turtle Beach · Yotpo | Red Bull · BV |
|---|---|---|---|---|---|---|
| venue | NYSE:COOK | NYSE:YETI | NYSE:BRBR | NASDAQ:SMPL | NASDAQ:TBCH | private |
| reviews, one product | 1,215 | **35,516** | 2,201 | 1,388 | 555 | 8,859 |
| pagination caps | **no** | **no** | **no** | **no** | **no** | **no** |
| dates descending | **verified, 0 inversions** | verified | verified | verified | verified | verified |
| reach back | 7.6 yr | **12.5 yr** | 10.0 yr | 6.0 yr | 2.35 yr | 7.8 yr |
| carry text | **97.4%** | **24.3%** | **100%** | 75% | **100%** | **100%** |
| newest review | 4 days | **same day** | 17 days | 23 days | 5 days | 1 day |
| cost / account | free / none | free / none | free / none | free / none | free / none | free / none |

Also reachable and measured: **Olaplex (NASDAQ:OLPX)** 4,886 / 7.6 yr — the lead handed
to this session, and not a member of any declared group; **Atkins (NASDAQ:SMPL)** 3,422 /
9.9 yr through a *second* SMPL zone; **Weber** 18,772 / 8.5 yr and **Bose** 5,113 / 2.7 yr
as private controls; **Owala** 14,029 but abandoned in 2022.

Sort order was checked **against the dates**, never trusted from the parameter: zero
inversions in every collection taken. **The ~2-month Yotpo freshness lag is not a
property of brand-hosted data** — Bazaarvoice served a YETI review from the same day.
So this route is usable for history *and* for a recency cadence, which Amazon is not and
Yotpo is not.

**The yeti.com Akamai block was never the obstacle.** The block is on `yeti.com`; the
reviews are on `api.bazaarvoice.com`, which did not refuse. Worth remembering: a brand
site refusing is no evidence about the brand's review data.

### Three things measured here that change how a collection set must be built

1. **Bazaarvoice pools across a product family, exactly as Amazon pools across
   variants.** `TFB57GLE` (Pro 575) and `TFB78GLE` (Pro 780) are different grills at
   different prices and return **identical 1,215-review id sets** — 100% overlap. The
   other 20 pairs tested shared 0. Summing `TotalReviewCount` across a catalog
   double-counts: 4,013 claimed against 2,798 unique across the first four products, a
   30% overstatement. **Dedupe by review id and check overlap pairwise.** Never infer
   independence from the ProductId differing. This is the same error already recorded
   for Amazon parent/child pooling — changing platform did not fix it.
2. **One product cannot carry a series; its flow decays as the model ages.** The Pro 575
   has 7.6 years at 100% text and still clears the floor in only **11 of 32** quarters —
   75–390 reviews a quarter in 2019-20, 1–12 in 2025-26. Seven families together clear
   **30 of 44**, with the 28-quarter run above. So **product coverage, not depth per
   product, is the binding constraint** — the inverse of the 2026-10-14 history-mode
   failure, where no product sat in the right velocity band. Here the band need not be
   hit at all, because products can be summed. The cost is that a set needs a new
   product added at the recent end as old models age out, and a mid-series swap changes
   the instrument (`CORE-PROMPT.md` §4).
3. **Text fraction is not stable across time, and on YETI it collapses.** 98% of YETI's
   2014 reviews carry text, 96% of 2018, 35% of 2021, **6% of 2026Q4**. Weber shows the
   same shape. Traeger, BellRing, Quest and Turtle Beach hold 75–100% throughout. Where
   it moves, the scored population drifts away from the rated population over the life
   of the series, so a tone trend partly measures a change in who bothers to type.
   **Report it with every YETI and Weber reading**, and prefer Traeger / BellRing /
   Quest as the first companies tested.

### The structural objection is unchanged, and now has two more pieces of evidence

These reviews are still **moderated by the company being measured**. Olaplex maintains a
catalog entry literally named `RemovedProductsReviews` holding **8,928** reviews swept
off delisted products — better than deletion, and direct evidence that the brand curates
what appears against a live product. Bazaarvoice's entire display model *is* a
brand-configured moderation queue.

**Three more deletion snapshots taken 2026-10-15**, same shape and same purpose as the
Yotpo one, 300 ids each at fixed offsets, **to be re-pulled on or after 2026-10-22** and
diffed:
`bazaarvoice-traeger-snapshot-2026-10-15.json`,
`bazaarvoice-yeti-snapshot-2026-10-15.json`,
`powerreviews-bellring-snapshot-2026-10-15.json`.

**Still open and it matters:** Stanley's Yotpo data gave `pearson(tone, star) = 0.498`
against 0.874–0.921 on Amazon data. That test has **not** been repeated on Bazaarvoice
or PowerReviews data. Do it per company before any of these series is trusted — it is
the cheapest available check on whether the brand-hosted population differs from the
marketplace one.

## Coverage: the route reaches less than half of what is declared

| group | declared | reached | S2 peer-relative |
|---|---|---|---|
| **barbecue-grills** | 2 | **2 — Traeger (COOK) + Weber** | **yes** |
| **protein-shakes-and-bars** | 2 | **2 — BellRing (BRBR) + Quest *and* Atkins (SMPL)** | **yes** |
| insulated-drinkware | 3 | **3** — YETI (YETI) + Stanley + Owala, but Owala's newest review is **2022-07-28** | yes, to 2022; Stanley alone after |
| gaming-headsets | 3 | 1 — Turtle Beach (TBCH) only | **no** |
| wireless-speakers | 2 | 1 — **Bose only, the private one** | **no** |
| energy-drinks | 3 | 1 — **Red Bull only, the private one** | **no** |
| premium-everyday-carry (draft) | 2 | **0** | no |

**A correction made inside the same session, because it nearly became a permanent false
negative.** The first sweep of the Bazaarvoice CDN fetched `bv.js` without accepting
gzip. Bazaarvoice gzips some zones regardless of `Accept-Encoding`, so those bodies
decoded to binary and every regex silently found nothing. **Five zones were written off
as "unprovisioned stubs" on that basis and all five publish a key:** `monsterenergy`,
`redbull`, `owala`, `simplygoodfoods`, `logitech`. Red Bull turned out to be one of the
best samples found all day (8,859 reviews, 7.8 years, **100% text**, newest 1 day old)
and Owala holds 14,029. *A regex finding nothing is evidence only once the body is known
to be text; a negative from a parser is not a negative from the server.*

**Three distinct ways a member fails, and they want different responses:**

- **no route** — no platform, or one shipped unconfigured. Celsius, Sonos, Hydro Flask.
- **route works, catalog empty** — the API answers correctly and holds nothing. Corsair
  (143 reviews over 34 days), Logitech (**0 products**), Monster (**1 product, 52
  reviews, ending June 2019**). A company fact, not a route fact. Corsair's is filling
  up; the other two are not.
- **route works, deployment abandoned** — full depth, served perfectly, newest review
  years old. **Owala: 14,029 reviews and a newest of 2022-07-28, 1,540 days stale.**
  Fine inside a backtest window, fatal for a cadence.

**So reach is two numbers, not one** — how far *back* and how far *forward*. Every route
before today had a newest review within days or a couple of months, so only the back
edge was ever recorded. Record `newest_review_age_days` on every batch from here.

**`barbecue-grills` is the group to run first.** It is the only declared group where the
public member under test *and* its peer are both reached, so S1 and S2 are both
computable and the member whose revenue is needed is the one that is reachable.
`protein-shakes-and-bars` is second, with the caveat that its two members sit on
**different platforms** — PowerReviews against Bazaarvoice — with different text
fractions and different moderation regimes. `CORE-PROMPT.md` §3 requires a route change
to be recorded *in* a series; here the route differs *across members at the same
instant*, which is a confound S2 was not designed for and which has to be stated with
the reading.

**`wireless-speakers` and `energy-drinks` both fail in the worst direction:** the reached
member is the *private* one. Bose and Red Bull contribute tone and can never contribute
revenue (`references/signal.md`), so a route reaching only the control produces a
category reading and no test.

**That asymmetry is the thing here most worth worrying about.** Of the five groups with
any reachable member, the private control is reachable in **all five** and the public
member in only four — and twice the control is reachable while the company under test is
not. **The sensor sees category controls more reliably than it sees the companies whose
revenue the project wants to predict**, because private consumer brands are
disproportionately DTC. That is the same reason the all-private groups looked healthiest
under the 10x rule, and it is now visible through a second, independent sensor.

**Turtle Beach is recorded as peerless** and stays in S1 per `signal.md` limit 3.
Running S2 on `gaming-headsets` today would run the two specifications on different
samples.

**Coverage splits on channel, not on company size.** Brands selling direct from their own
storefront run a review platform; brands selling through grocery, retail floors or
carriers largely do not. Traeger, YETI, Premier Protein, Quest, Atkins, Olaplex and
Turtle Beach are all DTC web storefronts; Celsius and Monster are grocery — and Red Bull,
also grocery, is the exception that shows this is a choice rather than a law. **That is
the same DTC selection bias already recorded for the 10x Amazon asymmetry rule, arriving
independently through a second sensor** — which makes it a property of the companies
rather than of either instrument, and it is the single most important thing this session
learned about what the project can ever see.

## Doors closed 2026-10-15, so they are not re-opened

| door | result |
|---|---|
| **Bazaarvoice `bfd` proxy** (`apps.bazaarvoice.com/bfd/v1/clients/<client>/…`) | **401 Unauthorized**, verified for `traegerservice`/`17661-en_us` and `olaplex`/`10548-en_us`. **Recorded and stopped — no `Referer` or `Origin` was sent to it.** The `bfd` proxy exists so the page need not expose a passkey; the deployments above expose one anyway via the legacy scout file, a *different* endpoint that did not refuse |
| **Sonos (NASDAQ:SONO)** | **serves no reviews at all.** The Era 100 PDP ships PowerReviews with `"merchantGroupId":null,"merchantID":null` — unconfigured, so the widget never fires. No `sonos` Bazaarvoice zone. The earlier "powerreviews + okendo" sighting was a false positive from substring-matching `merchantID` and the filter label `okenDolbyAtmos` |
| **Celsius (NASDAQ:CELH)** | no review widget of any kind on `celsius.com`; no Bazaarvoice zone |
| **Monster (NASDAQ:MNST)** | **route works, catalog essentially empty.** The `monsterenergy` zone holds **1 product and 52 reviews**, all from a 77-day window in 2019. Nothing suggests it is filling up. Its brand site's 403 is real and irrelevant |
| **Helen of Troy / Hydro Flask (NASDAQ:HELE)** | Cloudflare 403 interstitial. Recorded and stopped. No zone exists |
| **Corsair (NASDAQ:CRSR)** | **route works, data absent.** Deepest product in a 6,644-product catalog holds 143 reviews over 34 days; the declared HS80 family holds ~22. Its deployment is new. A company fact, not a route fact — it fixes itself with time |
| **Logitech (NASDAQ:LOGI)** | **key published, catalog empty — 0 products.** `products.json` returns `TotalResults 0` with no errors. PDPs also 308/404 anonymously, so where its reviews actually live is still unknown. Needs the browser pane against a live PDP |
| **Owala / Trove Brands** | **reachable but abandoned.** 14,029 reviews, and the newest is **2022-07-28**. Good for a window ending mid-2022; useless for a cadence |
| **Stanley on Bazaarvoice** | **not Bazaarvoice.** `stanley1913.com` carries the substring but has no deployment path and no zone exists. Stanley's route stays Yotpo. **Substring presence in a bundle is not a deployment** — three brands flagged this way had no zone at all |
| **Retailer syndication** (Target, Walmart, Best Buy, Dick's, Ulta, Sephora) | **not needed, and not tested.** It was the best remaining lead and got leapfrogged: the brand platforms serve their own display API in the clear, which is strictly better — the brand's full history in true date order rather than whichever rows a retailer ingested. `IsSyndicated` was **0** on every Bazaarvoice review collected, so none of this arrived by syndication. Still the right fallback for a brand whose own deployment is locked |

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

## The weekly run, live from 2026-10-15

Scheduled task `review-tone-weekly`, **Mondays 08:37 local**. It collects 3 products at
50 reviews each (~$0.90), runs `run.py --group insulated-drinkware`, commits and reports.

**50 per product is deliberate.** These products run 3-5 reviews a day, so 50 covers
10-16 days and a weekly pass cannot miss anything; raising it costs money and the overlap
is deduplicated away. At 100 the cadence would cost $7.74/month against a $6 free tier
and would silently halt mid-month. At 50 it is $3.87/month and fits.

**It checks the budget before collecting** and stops if under $1.20 remains, reporting the
gap rather than overspending. A recorded gap is fine; a cycle that runs out mid-collection
is not.

It runs while the desktop app is open; if the app is closed on a Monday the run happens at
next launch.

## The series so far

First measurement 2026-10-15, FY2026Q4, 247 reviews, scorer calibrated at kappa 0.811:

| company | tone | 95% interval | n | coverage |
|---|---|---|---|---|
| YETI | 1.113 | [0.716, 1.471] | 53 | ~1% |
| Stanley | 0.753 | [0.443, 1.030] | 97 | ~1% |
| Owala | 0.464 | [0.154, 0.773] | 97 | ~1% |

YETI's interval barely clears Owala's, so that gap sits at the edge of being
distinguishable from noise. The trend chart is refused on all three: it needs 12 quarters
and there are 2.

## Next

Stages G and C are built and `/tone-run` walks them. Every declared group currently
stops at **stage C**, because nothing has been collected. Four things stand between
here and a chart:

1. ~~**Find a route that is not Amazon's capped surface.**~~ **Done, 2026-10-15.**
   Bazaarvoice, PowerReviews and Yotpo display APIs — free, uncapped, true date order,
   up to 12.5 years, and a revenue-readable company confirmed (Traeger, NYSE:COOK). The
   structural objection stands and is now being watched by four deletion snapshots.
   See the two sections above.
2. **Declare a collection set and cadence for `barbecue-grills`, and collect.** This is
   now the blocking step and it needs no money and no token. What the declaration has to
   fix, given what was measured: **which Bazaarvoice ProductIds** (at least ~7 Traeger
   grill families to clear the quarterly floor across the whole window, and the Weber
   equivalents), **the pairwise id-overlap check** that proves they are not one pooled
   family, and **the rule for adding a product at the recent end** as a model ages out.
   A mid-series swap changes the instrument, so the rule is declared now, not improvised
   later.
3. **Calibrate the tone scorer** — the user labels ~100 reviews by hand against
   `references/tone-rubric-v1.md`. **Nobody else can do this step**, and the scorer
   produces numbers rather than measurements until it is done. It needs reviews on disk
   first.
4. **Keep collecting.** Each run is one point in the series; the first real test of
   prediction is several reporting periods away (`CORE-PROMPT.md` §8).

Open and not blocking: `energy-drinks` needs re-declaring for the Monster asymmetry
(553 ratings against Celsius's 28,121); `premium-everyday-carry` is a draft that fails
stage G and should be declared or deleted; stages S, A and D still take `--tickers`
rather than `--group`.

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

Nothing about prediction. `CORE-PROMPT.md` §8 governs: the series starts the day
collection starts, a trend claim needs a declared number of readings, and a predictive
claim needs the test written down before the revenue arrives and then several reporting
periods to run.

**No declared collection exists.** What 2026-10-15 established is that one *could* now
be built with 7 years of history for a public company — a fact about reachability, not a
reading. The review text pulled while measuring the route was a route test; it was **not
committed as a corpus and must not be reused as one**, because the collection set and
cadence have not been declared and choosing them after seeing the text is the thing
`CORE-PROMPT.md` §2 forbids. The route is deterministic and re-collectable to
exhaustion, so declaring first costs nothing but a re-pull.

One consequence worth stating plainly: **the 2-year wait and the forward cadence are now
separate questions.** History is available today; whether tone *predicts* revenue still
needs the prereg written before the revenue arrives, and no amount of back history
substitutes for that.
