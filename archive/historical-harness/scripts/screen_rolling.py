#!/usr/bin/env python3
"""Stage A1 (rolling) - point-in-time universe re-formed every year.

A single formation date forces a bad trade: an early date has a long window but
excludes every company that listed later (YETI, Sonos, e.l.f., SharkNinja), and
a late date buys those companies by throwing away half the window.

Re-forming the universe annually removes the trade entirely. Each January 1 is
screened using only data available on that date, so a company enters the test
the year it first qualifies and leaves the year it stops. No hindsight is
introduced: no screen ever sees its own future.

The output is an unbalanced panel of company-years, not a fixed roster. The
backtest therefore works on company-quarters, with entries and exits explicit.

Efficiency: each company's filing history and price series are fetched ONCE and
cached, then every formation date is computed from the cache. Seven annual
screens cost roughly what one used to.

Output: data/universe_panel.csv, data/universe_panel.hash
Cache:  .cache/  (safe to delete; only costs refetch time)

Standard library only. That is a binding policy for every pipeline stage,
not an accident of this script: see references/numeric-stack.md. numpy and
scipy are development-only, and no stage may import them. Needs SEC_USER_AGENT.
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from screen_universe import (fetch, ua, companies_for_sic, ticker_map,  # noqa: E402
                             SEC_CONCEPT, EXCHANGES)
import sic_consumer  # noqa: E402

YAHOO_SPAN = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
              "?period1={p1}&period2={p2}&interval=1wk&events=split")


def cache_path(cache, kind, key):
    d = os.path.join(cache, kind)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s.json" % key.replace("/", "_"))


def cached(cache, kind, key, producer):
    p = cache_path(cache, kind, key)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except ValueError:
            pass
    val = producer()
    with open(p, "w") as f:
        json.dump(val, f)
    return val


def share_vintages(cache, cik):
    """[(filed_date, shares)] sorted by filing date - fetched once per company."""
    def go():
        body = fetch(SEC_CONCEPT.format(cik=cik))
        if not body:
            return []
        try:
            data = json.loads(body)
        except ValueError:
            return []
        rows = []
        for unit_rows in data.get("units", {}).values():
            for r in unit_rows:
                if r.get("filed") and r.get("val"):
                    rows.append([r["filed"], float(r["val"])])
        rows.sort()
        return rows
    return cached(cache, "shares", cik, go)


def price_series(cache, ticker, start, end):
    """{'closes': [[date, close]], 'splits': [[date, ratio]]} - once per company.

    Weekly bars: a formation date resolves to the last close within the
    preceding week, which is ample precision for a cap-band test and a
    fraction of the payload of daily bars.
    """
    def go():
        p1 = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
        p2 = int(datetime.strptime(end, "%Y-%m-%d").timestamp())
        sym = ticker.upper().replace(".", "-")
        try:
            body = fetch(YAHOO_SPAN.format(sym=sym, p1=p1, p2=p2), rate=0.3)
        except Exception:
            return {"closes": [], "splits": []}
        if not body:
            return {"closes": [], "splits": []}
        try:
            res = json.loads(body)["chart"]["result"][0]
        except (ValueError, KeyError, TypeError, IndexError):
            return {"closes": [], "splits": []}
        ts = res.get("timestamp") or []
        cl = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        closes = [[datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), c]
                  for t, c in zip(ts, cl) if c is not None]
        splits = []
        for ev in (res.get("events", {}) or {}).get("splits", {}).values():
            try:
                splits.append([datetime.utcfromtimestamp(int(ev["date"])).strftime("%Y-%m-%d"),
                               float(ev["numerator"]) / float(ev["denominator"])])
            except (KeyError, ValueError, ZeroDivisionError, TypeError):
                continue
        return {"closes": closes, "splits": sorted(splits)}
    return cached(cache, "prices", ticker, go)


def cap_at(vintages, series, asof):
    """Point-in-time market cap, or None if either input is unavailable then."""
    shares = shares_asof = None
    for filed, val in vintages:
        if filed <= asof and (shares_asof is None or filed > shares_asof):
            shares, shares_asof = val, filed
    if not shares:
        return None, None, None

    price = None
    for d, c in series["closes"]:
        if d <= asof:
            price = c
        else:
            break
    if not price:
        return None, shares_asof, None

    # Yahoo closes are split-adjusted; historical share counts are not. Undo
    # every split after `asof` or the cap is wrong by that factor.
    factor = 1.0
    for d, ratio in series["splits"]:
        if d > asof:
            factor *= ratio
    price *= factor
    return shares * price, shares_asof, price


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from-year", type=int, default=2016)
    p.add_argument("--to-year", type=int, default=2022)
    p.add_argument("--cap-min", type=float, default=50e6,
                   help="default $50M - Amazon-native brands are often small")
    p.add_argument("--cap-max", type=float, default=10e9)
    p.add_argument("--sic", nargs="*", default=None,
                   help="default: every consumer SIC code (sic_consumer.ALL)")
    p.add_argument("--core-only", action="store_true",
                   help="restrict to sic_consumer.CORE")
    p.add_argument("--out", default="data/universe_panel.csv")
    p.add_argument("--cache", default=".cache")
    args = p.parse_args()
    ua()

    sics = args.sic or (sic_consumer.CORE if args.core_only else sic_consumer.ALL)
    years = list(range(args.from_year, args.to_year + 1))
    dates = ["%d-01-01" % y for y in years]
    print("rolling formation: %s" % ", ".join(str(y) for y in years))
    print("cap band: $%.0fM - $%.0fM" % (args.cap_min / 1e6, args.cap_max / 1e6))
    print("SIC codes: %d\n" % len(sics))

    print("[1/4] enumerating registrants by SIC ...")
    pool = {}
    t0 = time.time()
    for i, sic in enumerate(sics, 1):
        for cik, name in companies_for_sic(sic).items():
            pool.setdefault(cik, {"name": name, "sic": sic})
        if i % 15 == 0 or i == len(sics):
            print("  %d/%d codes · pool %d · %.0fs"
                  % (i, len(sics), len(pool), time.time() - t0))

    print("\n[2/4] joining to listed tickers ...")
    tmap = ticker_map()
    listed = {}
    for cik, meta in pool.items():
        if cik in tmap:
            tic, exch, name = tmap[cik]
            if exch and exch in EXCHANGES:
                listed[cik] = dict(meta, ticker=tic, exchange=exch,
                                   name=meta["name"] or name)
    print("  %d of %d registrants listed on a major US exchange" % (len(listed), len(pool)))
    print("  (delisted-before-today registrants are absent from the current")
    print("   ticker file - a known survivorship gap, recorded not hidden)")

    span_start = "%d-01-01" % (args.from_year - 1)
    span_end = "%d-12-31" % (args.to_year + 2)
    print("\n[3/4] fetching filings + prices once per company (cached) ...")
    rows = []
    items = sorted(listed.items())
    for n, (cik, meta) in enumerate(items, 1):
        vint = share_vintages(args.cache, cik)
        series = price_series(args.cache, meta["ticker"], span_start, span_end)
        for asof in dates:
            cap, shares_asof, price = cap_at(vint, series, asof)
            rows.append({
                "ticker": meta["ticker"], "cik": cik,
                "company_name": meta["name"], "sic": meta["sic"],
                "exchange": meta["exchange"], "formation_date": asof,
                "shares_asof": shares_asof or "",
                "price_at_formation": round(price, 4) if price else "",
                "market_cap": round(cap, 2) if cap else "",
                "in_band": bool(cap and args.cap_min <= cap <= args.cap_max),
            })
        if n % 50 == 0 or n == len(items):
            ib = len(set(r["ticker"] for r in rows if r["in_band"]))
            print("  %d/%d companies · %d distinct in band · %.0fs"
                  % (n, len(items), ib, time.time() - t0))

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(out.replace(".csv", ".hash"), "w") as f:
        f.write("%s  %s  years=%s-%s  band=%.0f-%.0f  sics=%d\n"
                % (digest, os.path.basename(out), args.from_year, args.to_year,
                   args.cap_min, args.cap_max, len(sics)))

    print("\n[4/4] done")
    print("  company-years written: %d" % len(rows))
    print("  distinct companies ever in band: %d"
          % len(set(r["ticker"] for r in rows if r["in_band"])))
    print("\n  year   in band")
    for d in dates:
        print("  %s   %d" % (d[:4], sum(1 for r in rows
                                        if r["formation_date"] == d and r["in_band"])))
    print("\n  -> %s\n  sha256: %s" % (out, digest))
    print("\nNext: score every company-year with the scorecard in")
    print("references/company-selection.md. Coverage comes after that, never before.")


if __name__ == "__main__":
    main()
