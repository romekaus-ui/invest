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
