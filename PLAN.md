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

## Next

1. **Peer-group finder** — in build. First real run must produce **at least 80
   companies** in however many groups fall out naturally (likely 20–30 groups of 2–7,
   not 40 forced pairs — bigger groups are a better control).
2. **Fresh review collector** — in build. Both routes behind one interface.
3. **Calibrate the tone scorer** — the user labels ~100 reviews by hand so the scorer's
   agreement can be measured. Nobody else can do this step, and the scorer's output is
   not a measurement until it is done.
4. **Choose the collection set and the cadence**, declare both, start collecting.
5. **Keep collecting.** Each run is one point in the series.

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
