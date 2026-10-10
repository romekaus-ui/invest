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

**Formation date:** `2016-01-01` (default; set in prereg).

**Population:** all registrants in SEC `company_tickers_exchange.json` listed on NYSE,
NASDAQ or NYSE American at formation.

**SIC filter** — consumer durables and hardware, where a physical product's quality is
plausibly visible in reviews:

| SIC | Description |
|---|---|
| 3630, 3634, 3635, 3639 | Household appliances, housewares, vacuums |
| 3651 | Household audio & video equipment |
| 3571, 3572, 3575, 3577, 3578 | Computers and peripherals |
| 3661, 3663, 3669 | Telephone & communications equipment |
| 3827, 3861 | Optical instruments, photographic equipment |
| 3944, 3949 | Games, toys, sporting goods |
| 3089, 3420, 3421, 3429 | Plastics products, cutlery, hardware |
| 2844, 3991 | Toiletries, brooms & brushes |
| 5712, 5731, 5940, 5945 | Consumer retail of the above |

**Market cap band at formation:** USD 200M – 10B.

- Below 200M: review volume and filing quality are both too thin.
- Above 10B: the company is a conglomerate or a macro asset. Its reported revenue is a
  sum over businesses the reviews cannot see, and its share price is driven by rates,
  multiples and index flows. This band is where a single product line can still *be* the
  company.

Market cap is computed point-in-time: `dei:EntityCommonStockSharesOutstanding` from the
last EDGAR filing on or before formation, times the Stooq close on formation.
**Never a present-day market cap** — that quietly excludes everything that later shrank,
which is exactly the population the hypothesis is about.

**Survivors are not the universe.** Companies that were later acquired, delisted or went
to zero stay in. Dropping them is survivorship bias and will inflate every statistic
downstream.

Output: `data/universe.csv`, hashed and committed before any review data is touched.

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
