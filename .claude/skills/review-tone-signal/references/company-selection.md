# Company Selection

Two separate problems. Conflating them is the single most common way this kind of
study produces a result that means nothing.

1. **Which companies are allowed to be considered at all** — a mechanical, point-in-time
   universe rule that cannot see outcomes.
2. **Which of those are structurally capable of carrying the signal** — a scorecard
   applied to the whole universe, on grounds knowable at formation.

Selection on outcome — "let's test Nike, which collapsed" — is the trick that makes a
worthless method look clairvoyant. It is prohibited by §2 of the core prompt.

---

## 1. Universe rule (mechanical, point-in-time)

Run the **market screen first**, over the full population. Check review coverage
**afterwards**. Never the other way round: starting from "which brands are in the review
dataset" lets data availability pick the companies, and data availability correlates with
Amazon-channel success, which is downstream of the thing being predicted.

**Formation: re-run every January 1, from 2016 to 2022.**

A single formation date forces a bad trade. An early date has a long window but
excludes every company that listed later — YETI (2018), Sonos (2018), e.l.f.,
SharkNinja (2023) — which are precisely the Amazon-native brands the hypothesis
is about. A late date buys those companies by discarding half the window.

Re-forming annually removes the trade. Each screen sees only data available on
its own date, so a company enters the test the year it first qualifies and
leaves the year it stops. No screen sees its own future, so no hindsight enters.

The output is an **unbalanced panel of company-years**, not a fixed roster. The
backtest therefore operates on company-quarters, with entries and exits
explicit. A company contributes only the quarters during which it was in the
universe.

**Population:** all registrants in SEC `company_tickers_exchange.json` listed on
NYSE, NASDAQ or NYSE American at the formation date.

**SIC filter:** the consumer-product codes in `scripts/sic_consumer.py` — 126
codes in 17 groups, taken from SEC's official Division of Corporation Finance
list. The selection rule, fixed before any company was examined:

> include a code when a typical registrant under it makes or sells a physical
> consumer product that could plausibly be bought on Amazon and reviewed by
> the buyer.

Excluded on that rule: semiconductors and electronic components, industrial and
construction machinery, primary metals, aerospace and defence, petroleum,
building materials, and wholesale distribution.

Communications equipment (3661/3663/3669) is **kept** despite being mostly
enterprise, because it also contains consumer audio and accessory makers —
Turtle Beach sits in 3669. The enterprise names are removed downstream by
criterion 4, which leaves a documented reason per company rather than a silent
omission at the SIC stage. The same applies to computers and peripherals: it
catches NetApp and Fortinet alongside Logitech, and the scorecard is what
separates them.

**Cap band at formation: USD 50M – 10B.**

- The floor is low deliberately. Amazon-native brands are frequently small, and
  a $200M floor removes much of the population the hypothesis concerns. Below
  $50M, filing quality and review volume both degrade badly.
- The ceiling stays at $10B. Above it the company is a conglomerate or a macro
  asset: reported revenue sums over businesses the reviews cannot see, and the
  share price answers to rates, multiples and index flows. This band is where a
  single product line can still *be* the company.

Market cap is point-in-time: `dei:EntityCommonStockSharesOutstanding` from the
last filing on or before the formation date, times the close on that date,
multiplied back up by any split that happened afterwards. **Never a present-day
market cap** — that quietly excludes everything that later shrank, which is
exactly the population the hypothesis is about.

**Survivors are not the universe.** Companies later acquired, delisted or taken
to zero stay in for the years they qualified. Dropping them is survivorship bias
and inflates every statistic downstream. One known gap remains: registrants that
delisted before today are missing from SEC's current ticker file, so the panel
understates the delisted population. This is recorded rather than hidden, and
closed in a later pass with historical ticker data.

Output: `data/universe_panel.csv`, hashed and committed before any review data
is touched. Built by `scripts/screen_rolling.py`.

---

## 2. Structural eligibility scorecard

Applied to every name in the universe. Scored 0–5 on each criterion, weighted, out of
100. All evidence must be knowable at formation — the 10-K in force at that date, not a
later one.

| # | Criterion | What scores 5 | What scores 0 | Weight |
|---|---|---|---|---|
| 1 | **Amazon share of revenue** | Amazon or e-commerce is a disclosed, material channel | Sold through grocery/mass; Amazon immaterial | 25 |
| 2 | **Portfolio narrowness** | SKUs enumerable in the hundreds | Tens of thousands across dozens of brands | 20 |
| 3 | **Pure-play revenue** | One reportable segment drives results | Conglomerate; unrelated segments dominate | 20 |
| 4 | **Low B2B/OEM mix** | Consumer sales are nearly all of revenue | Large enterprise, OEM or licensing revenue | 10 |
| 5 | **Quality-sensitive durable** | Defects drive returns and repeat purchase | Commodity consumable; quality barely varies | 10 |
| 6 | **Segment disclosure** | 10-Q reports at a level matching what is measured | Single undifferentiated revenue line | 10 |
| 7 | **Low 3P reseller noise** | Brand controls the listing | Grey-market resellers dominate the buy box | 5 |

**Pass bar: 60/100.** Below that, the company is recorded as structurally ineligible with
its scores — not deleted. The ineligible list is part of the result.

Criterion 1 is the one that kills most candidates and the one most often waved through.
If Amazon is 2% of sales, review tone is measuring 2% of the company, and no amount of
statistical care downstream repairs that.

### Scoring discipline

- Score from filings and channel disclosure, not from intuition about the brand.
- Where Amazon share is undisclosed — which is usual — score it from the disclosed
  e-commerce or DTC share and **mark the estimate as such**. An unquantifiable
  criterion 1 scores 0, not a guess in the company's favour.
- Never revise a score after seeing an outcome. Revisions require a new formation run.

---

## 3. Worked verdicts on the reel's example list

Not the universe — these are the twelve names the source video used, scored here to
demonstrate the scorecard and to show how much of a casually assembled list fails.
**None of these may enter a real run except by passing the mechanical screen in §1.**

| Company | Verdict | Binding reason |
|---|---|---|
| SharkNinja (SN) | Likely pass | Near pure-play consumer appliances, Amazon-dominant, US-heavy, quality-sensitive |
| Helen of Troy (HELE) | Likely pass | Multi-brand but all consumer housewares; heavy Amazon presence; segment disclosure usable |
| Hamilton Beach (HBB) | Likely pass | Pure-play small appliances, narrow portfolio; watch review volume against the floor |
| YETI | Likely pass | Narrow portfolio, quality-sensitive, meaningful Amazon channel alongside DTC |
| Logitech (LOGI) | Marginal | Good Amazon presence, but roughly half international and a real B2B/video-conferencing segment. The video's demo company is the weakest of its strong candidates |
| Newell (NWL) | Fail | Portfolio sprawl across dozens of unrelated brands; criterion 2 and 3 |
| Spectrum Brands (SPB) | Fail | Pet, home & garden, hardware — unrelated segments; criterion 3 |
| Sony | Fail | Conglomerate. Housewares tone says nothing about image sensors, music or pictures |
| Procter & Gamble | Fail | Grocery channel, unenumerable SKU count, Amazon immaterial to revenue |
| Colgate (CL) | Fail | As above |
| Clorox (CLX) | Fail | As above |
| Church & Dwight (CHD) | Fail | As above, despite a growing online share |

Also note what the list reveals: it has no failures-that-didn't-happen and no delisted
names. A list assembled by a person thinking about consumer brands is not a universe.

---

## 4. Coverage check (runs last, never first)

For each structurally eligible company, match products in the review corpus via the
metadata `store` field plus a brand-alias table, then compute:

- matched `parent_asin` count
- reviews per fiscal quarter across the window
- quarters meeting `MIN_REVIEWS_PER_QUARTER`
- the **coverage ratio**: a revenue proxy for matched ASINs against reported revenue

A company that is structurally eligible but has unusable coverage is **recorded as a
coverage failure and dropped**. It is not replaced by a better-covered company outside
the screen. The size of this gap — how many structurally sound candidates simply have no
usable review data — is a finding and belongs in the results, because it bounds how
general the method could ever be.
