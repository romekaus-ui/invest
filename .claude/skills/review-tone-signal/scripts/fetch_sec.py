#!/usr/bin/env python3
"""Stage B6 - point-in-time quarterly revenue from EDGAR.

The whole point of this script is the `filed` date. Revenue for a quarter is
not knowable at period end; it becomes public weeks later. Joining on period
end instead of filing date hands the backtest information it could not have
had, and is the single most effective way to manufacture a signal
(failure-modes F2).

So: every row carries `filed_date`, rows keep their ORIGINAL vintage, and a
later restatement is flagged rather than substituted.

Output: data/{TICKER}/fundamentals.csv

Standard library only. That is a binding policy for every pipeline stage,
not an accident of this script: see references/numeric-stack.md. numpy and
scipy are development-only, and no stage may import them.
Needs SEC_USER_AGENT (see screen_universe.py).
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http_util import fetch, ua  # noqa: E402

FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"

# Preference order. Companies migrated to ASC 606 tags around 2018, so a long
# window needs several, and which one supplied a figure is recorded per row.
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]

QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 80, 100

# A figure whose earliest XBRL appearance is this far after period end was
# almost certainly public sooner, as a prior-year comparative in a later
# filing. We cannot evidence when it was first public, so it is marked
# `comparative_only` and excluded from the usable window rather than treated
# as a genuine 400-day reporting lag.
MAX_CREDIBLE_LAG_DAYS = 120


def fiscal_year_end_month(cik):
    """Fiscal year end from EDGAR's own `fiscalYearEnd` field (e.g. '0228').

    Inferring it from which months 10-K periods end in gets it wrong: a 10-K
    carries quarterly comparatives too, and the mode lands on the wrong month
    (Helen of Troy came out as August when it in fact ends in February).
    A one-month error shifts every join by a quarter - F11.
    """
    body = fetch(SUBMISSIONS.format(cik=cik))
    if body:
        try:
            fye = (json.loads(body).get("fiscalYearEnd") or "").strip()
            if len(fye) == 4 and fye.isdigit():
                m = int(fye[:2])
                if 1 <= m <= 12:
                    return m
        except ValueError:
            pass
    sys.exit("could not read fiscalYearEnd for CIK %s. Verify it from the 10-K "
             "and pass it explicitly rather than letting the pipeline guess." % cik)


def fiscal_quarter(period_end, fy_end_month):
    """Label a period by the company's OWN fiscal calendar.

    Never from the XBRL `fp` field: an annual filing carries fp='FY' for what
    is really Q4, which produced labels like 'FY2011QFY'. A mislabelled
    quarter shifts every join by one period (failure-modes F11).
    """
    d = datetime.strptime(period_end, "%Y-%m-%d")
    months_in = (d.month - fy_end_month - 1) % 12
    q = months_in // 3 + 1
    fy = d.year + 1 if d.month > fy_end_month else d.year
    return "FY%dQ%d" % (fy, q)


def quarterly_revenue(cik):
    body = fetch(FACTS.format(cik=cik))
    if not body:
        return [], {}
    facts = json.loads(body).get("facts", {}).get("us-gaap", {})

    # period -> list of vintages, earliest filing first
    periods = {}
    tag_used = {}
    for tag in REVENUE_TAGS:
        node = facts.get(tag)
        if not node:
            continue
        for unit, rows in node.get("units", {}).items():
            if not unit.startswith("USD"):
                continue
            for r in rows:
                start, end, filed = r.get("start"), r.get("end"), r.get("filed")
                val, form = r.get("val"), r.get("form", "")
                if not (start and end and filed and val is not None):
                    continue
                if form not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
                    continue
                d = (datetime.strptime(end, "%Y-%m-%d")
                     - datetime.strptime(start, "%Y-%m-%d")).days
                if not QUARTER_MIN_DAYS <= d <= QUARTER_MAX_DAYS:
                    continue  # annual/half-year/cumulative figures
                key = (start, end)
                if key in periods and tag_used.get(key) != tag:
                    continue  # first tag in preference order wins the period
                tag_used.setdefault(key, tag)
                periods.setdefault(key, []).append({
                    "val": float(val), "filed": filed, "form": form,
                    "accn": r.get("accn", ""), "fy": r.get("fy"), "fp": r.get("fp"),
                })

    fy_end_month = fiscal_year_end_month(cik)

    out = []
    for (start, end), vintages in sorted(periods.items(), key=lambda kv: kv[0][1]):
        vintages.sort(key=lambda v: v["filed"])
        first = vintages[0]                       # ORIGINAL vintage - what was public
        restated = any(abs(v["val"] - first["val"]) > 0.005 * abs(first["val"] or 1)
                       for v in vintages[1:])
        lag = (datetime.strptime(first["filed"], "%Y-%m-%d")
               - datetime.strptime(end, "%Y-%m-%d")).days
        out.append({
            "fiscal_quarter": fiscal_quarter(end, fy_end_month),
            "period_start": start,
            "period_end": end,
            "filed_date": first["filed"],
            "revenue": first["val"],
            "xbrl_tag": tag_used[(start, end)],
            "accession": first["accn"],
            "form": first["form"],
            "restated": restated,
            "n_vintages": len(vintages),
            "lag_days": lag,
            "comparative_only": lag > MAX_CREDIBLE_LAG_DAYS,
        })
    return out, fy_end_month


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ticker")
    p.add_argument("cik", help="10-digit CIK, from data/universe.csv")
    p.add_argument("--outdir", default="data")
    args = p.parse_args()
    ua()

    rows, fy_end_month = quarterly_revenue(args.cik.zfill(10))
    if not rows:
        sys.exit("no quarterly revenue found for CIK %s. Check the tag list - a "
                 "company using an unusual concept needs it added explicitly, "
                 "never inferred." % args.cik)

    d = os.path.join(args.outdir, args.ticker)
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "fundamentals.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    usable = [r for r in rows if not r["comparative_only"]]
    comp = len(rows) - len(usable)
    lags = sorted(r["lag_days"] for r in usable) or [0]
    med = lags[len(lags) // 2]
    restated = sum(1 for r in rows if r["restated"])

    print("%s: %d quarters, %s to %s  (FY ends month %d)"
          % (args.ticker, len(rows), rows[0]["period_end"], rows[-1]["period_end"],
             fy_end_month))
    print("  usable (original vintage): %d" % len(usable))
    print("  comparative_only, excluded: %d  (first XBRL appearance >%d days late)"
          % (comp, MAX_CREDIBLE_LAG_DAYS))
    print("  reporting lag on usable: median %d days, range %d-%d"
          % (med, lags[0], lags[-1]))
    print("  restated later: %d quarters (original vintage retained)" % restated)
    print("  -> %s" % out)
    print("\n  Decisions join on filed_date, never period_end. That median %d-day" % med)
    print("  gap is exactly the look-ahead this prevents.")


if __name__ == "__main__":
    main()
