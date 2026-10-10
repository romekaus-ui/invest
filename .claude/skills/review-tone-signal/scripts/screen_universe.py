#!/usr/bin/env python3
"""Stage A1 - mechanical, point-in-time universe screen.

Screens the full population of US-listed registrants by SIC code and by market
capitalisation computed AS OF the formation date. Review-data coverage is NOT
consulted here; that happens in coverage.py, afterwards and never before.

Output: data/universe.csv  (+ data/universe.hash)

Standard library only - no venv required.

SEC requires a declarative User-Agent with contact details. Set it yourself:

    export SEC_USER_AGENT="Your Name your@email.example"

Rules this script exists to enforce (CORE-PROMPT s2):
  - market cap is point-in-time, never present-day
  - companies later acquired or delisted remain in the universe
  - nothing is filtered on outcome
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

# Consumer durables and hardware: a physical product whose quality is plausibly
# visible in customer reviews. See references/company-selection.md s1.
DEFAULT_SIC = [
    "3630", "3634", "3635", "3639",            # household appliances, housewares
    "3651",                                      # household audio & video
    "3571", "3572", "3575", "3577", "3578",    # computers & peripherals
    "3661", "3663", "3669",                    # communications equipment
    "3827", "3861",                              # optical, photographic
    "3944", "3949",                              # toys, sporting goods
    "3089", "3420", "3421", "3429",            # plastics, cutlery, hardware
    "2844", "3991",                              # toiletries, brooms & brushes
    "5712", "5731", "5940", "5945",            # consumer retail of the above
]

CAP_MIN = 200_000_000
CAP_MAX = 10_000_000_000
EXCHANGES = {"NYSE", "Nasdaq", "NYSE American", "NYSEAMERICAN", "NASDAQ"}

SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_BROWSE = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
              "&SIC={sic}&type=10-K&dateb=&owner=include&count=100&start={start}"
              "&output=atom")
SEC_CONCEPT = ("https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}"
               "/dei/EntityCommonStockSharesOutstanding.json")
YAHOO = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?period1={p1}&period2={p2}&interval={iv}&events=split")

_last_call = [0.0]


def ua():
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not agent or "@" not in agent:
        sys.exit(
            "SEC_USER_AGENT is not set.\n"
            "SEC requires a contact in the User-Agent header. Set it to your own\n"
            "name and email - this script will not guess one for you:\n\n"
            '    export SEC_USER_AGENT="Your Name your@email.example"\n'
        )
    return agent


def fetch(url, rate=0.22, retries=3):
    """Rate-limited GET. SEC asks for <10 req/s; we stay well under."""
    for attempt in range(retries):
        wait = rate - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": ua(),
            "Accept-Encoding": "gzip, deflate",
            "Accept": "*/*",
        })
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return None


def companies_for_sic(sic):
    """All registrants with this SIC, via EDGAR browse atom feed."""
    out = {}
    start = 0
    while True:
        body = fetch(SEC_BROWSE.format(sic=sic, start=start))
        if not body:
            break
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            break
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//a:entry", ns)
        # A single-company result renders as a company-info document, not a list.
        if not entries:
            cik_el = root.find(".//a:company-info/a:cik", ns)
            name_el = root.find(".//a:company-info/a:conformed-name", ns)
            if cik_el is not None and cik_el.text:
                out[cik_el.text.strip().zfill(10)] = (
                    name_el.text.strip() if name_el is not None and name_el.text else "")
            break
        for e in entries:
            cik_el = e.find(".//a:cik", ns)
            name_el = e.find(".//a:conformed-name", ns)
            if cik_el is None or not cik_el.text:
                continue
            out[cik_el.text.strip().zfill(10)] = (
                name_el.text.strip() if name_el is not None and name_el.text else "")
        if len(entries) < 100:
            break
        start += 100
        if start > 2000:  # guard against a pathological feed
            break
    return out


def ticker_map():
    """CIK -> (ticker, exchange) for everything currently listed."""
    body = fetch(SEC_TICKERS)
    data = json.loads(body)
    fields = data["fields"]
    i_cik, i_tic, i_exch = (fields.index("cik"), fields.index("ticker"),
                            fields.index("exchange"))
    i_name = fields.index("name") if "name" in fields else None
    out = {}
    for row in data["data"]:
        cik = str(row[i_cik]).zfill(10)
        name = row[i_name] if i_name is not None else ""
        out.setdefault(cik, (row[i_tic], row[i_exch], name))
    return out


def shares_at(cik, asof):
    """Shares outstanding from the last filing on or before `asof`.

    Point-in-time by construction: a later filing's figure is never used.
    """
    body = fetch(SEC_CONCEPT.format(cik=cik))
    if not body:
        return None, None
    try:
        data = json.loads(body)
    except ValueError:
        return None, None
    best = None
    for unit_rows in data.get("units", {}).values():
        for row in unit_rows:
            filed = row.get("filed")
            val = row.get("val")
            if not filed or not val:
                continue
            if filed <= asof and (best is None or filed > best[1]):
                best = (val, filed)
    return best if best else (None, None)


def price_at(ticker, asof):
    """Unadjusted close on or just before `asof`.

    Yahoo returns SPLIT-ADJUSTED closes. Share counts from a 2015 filing are
    pre-split, so an adjusted price multiplied by historical shares understates
    market cap by the cumulative split factor (Apple at 2015-12-31: $26.32
    adjusted vs $105.26 as traded, a 4x error). We therefore multiply the
    adjusted close back up by every split that occurred AFTER `asof`.
    """
    d2 = datetime.strptime(asof, "%Y-%m-%d").date()
    d1 = d2 - timedelta(days=21)
    p1 = int(datetime(d1.year, d1.month, d1.day).timestamp())
    p2 = int(datetime(d2.year, d2.month, d2.day).timestamp()) + 86400
    sym = ticker.upper().replace(".", "-")

    def chart(a, b, iv):
        try:
            body = fetch(YAHOO.format(sym=sym, p1=a, p2=b, iv=iv), rate=0.3)
        except urllib.error.HTTPError:
            return None
        if not body:
            return None
        try:
            return json.loads(body)["chart"]["result"][0]
        except (ValueError, KeyError, TypeError, IndexError):
            return None

    res = chart(p1, p2, "1d")
    if res is None:
        return None
    closes = res.get("indicators", {}).get("quote", [{}])[0].get("close") or []
    last = None
    for c in closes:
        if c is not None:
            last = c
    if last is None:
        return None

    # Splits are only returned inside the requested window, so the price window
    # (three weeks) would never contain them. Ask separately, monthly interval,
    # from `asof` to now - cheap, and it catches every later split.
    factor = 1.0
    ev_res = chart(p2, int(time.time()), "1mo")
    for ev in ((ev_res or {}).get("events", {}) or {}).get("splits", {}).values():
        try:
            if int(ev["date"]) > p2:
                factor *= float(ev["numerator"]) / float(ev["denominator"])
        except (KeyError, ValueError, ZeroDivisionError, TypeError):
            continue
    return last * factor


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--formation", default="2016-01-01",
                   help="formation date, YYYY-MM-DD (default 2016-01-01)")
    p.add_argument("--out", default="data/universe.csv")
    p.add_argument("--sic", nargs="*", default=DEFAULT_SIC)
    p.add_argument("--cap-min", type=float, default=CAP_MIN)
    p.add_argument("--cap-max", type=float, default=CAP_MAX)
    p.add_argument("--limit", type=int, default=0,
                   help="cap companies processed (smoke tests only - a limited "
                        "run is NOT a valid universe and is marked as such)")
    args = p.parse_args()

    ua()  # fail fast if unset
    formation = args.formation
    print("formation date: %s" % formation)
    print("cap band: $%.0fM - $%.0fM" % (args.cap_min / 1e6, args.cap_max / 1e6))
    print("SIC codes: %d" % len(args.sic))

    print("\n[1/4] enumerating registrants by SIC ...")
    pool = {}
    for sic in args.sic:
        found = companies_for_sic(sic)
        for cik, name in found.items():
            pool.setdefault(cik, {"name": name, "sic": sic})
        print("  SIC %s: %4d registrants (pool %d)" % (sic, len(found), len(pool)))

    print("\n[2/4] joining to listed tickers ...")
    tmap = ticker_map()
    listed = {}
    for cik, meta in pool.items():
        if cik in tmap:
            tic, exch, name = tmap[cik]
            if exch and exch in EXCHANGES:
                listed[cik] = dict(meta, ticker=tic, exchange=exch,
                                   name=meta["name"] or name)
    print("  %d of %d registrants are currently listed on a major US exchange"
          % (len(listed), len(pool)))
    print("  NOTE: this join uses the CURRENT ticker file. Registrants delisted")
    print("  before today are absent from it - a survivorship gap recorded in")
    print("  the run notes and closed in a later pass via historical ticker data.")

    items = sorted(listed.items())
    truncated = False
    if args.limit and len(items) > args.limit:
        items = items[:args.limit]
        truncated = True
        print("  !! --limit active: %d companies. NOT a valid universe." % len(items))

    print("\n[3/4] point-in-time market cap as of %s ..." % formation)
    rows = []
    for n, (cik, meta) in enumerate(items, 1):
        shares, shares_asof = shares_at(cik, formation)
        price = price_at(meta["ticker"], formation) if shares else None
        cap = shares * price if (shares and price) else None
        in_band = bool(cap and args.cap_min <= cap <= args.cap_max)
        rows.append({
            "ticker": meta["ticker"], "cik": cik, "company_name": meta["name"],
            "sic": meta["sic"], "exchange": meta["exchange"],
            "formation_date": formation,
            "shares_outstanding": shares or "", "shares_asof": shares_asof or "",
            "price_at_formation": price if price else "",
            "market_cap": round(cap, 2) if cap else "",
            "in_band": in_band, "status_later": "active",
        })
        if n % 25 == 0 or n == len(items):
            print("  %d/%d  (in band so far: %d)"
                  % (n, len(items), sum(1 for r in rows if r["in_band"])))

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cols = list(rows[0].keys()) if rows else []
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(out.replace(".csv", ".hash"), "w") as f:
        f.write("%s  %s  formation=%s  truncated=%s\n"
                % (digest, os.path.basename(out), formation, truncated))

    in_band = [r for r in rows if r["in_band"]]
    no_cap = [r for r in rows if not r["market_cap"]]
    print("\n[4/4] done")
    print("  screened:        %d" % len(rows))
    print("  in cap band:     %d" % len(in_band))
    print("  no cap computed: %d (missing shares or price at formation)" % len(no_cap))
    print("  -> %s" % out)
    print("  sha256: %s" % digest)
    if truncated:
        print("\n  !! TRUNCATED RUN - smoke test only, not a universe.")
    print("\nNext: score every in-band company with the scorecard in")
    print("references/company-selection.md -> data/eligibility.csv")
    print("Do NOT look at review coverage until that is done.")


if __name__ == "__main__":
    main()
