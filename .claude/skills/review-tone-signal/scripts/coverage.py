#!/usr/bin/env python3
"""Stage A3 - Amazon review coverage for structurally eligible companies.

RUNS LAST. Coverage must never choose the companies: data availability
correlates with Amazon-channel success, which is downstream of the thing being
predicted. By the time this runs, the universe and the eligibility scores are
already frozen (CORE-PROMPT s2).

A structurally eligible company with unusable coverage is DROPPED and recorded.
It is never replaced by a better-covered company from outside the screen. The
size of that gap is a finding and belongs in the results.

Inputs : data/eligibility.csv, data/brand_aliases.json
Outputs: data/{TICKER}/products.csv, data/coverage.csv

Standard library only. That is a binding policy for every pipeline stage,
not an accident of this script: see references/numeric-stack.md. numpy and
scipy are development-only, and no stage may import them.
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402


def load_aliases(path):
    """{TICKER: [brand strings]}. Written by hand, from the 10-K brand list."""
    if not os.path.exists(path):
        sys.exit("missing %s\n\nCreate it from each company's 10-K brand list:\n"
                 '  {"HELE": ["OXO", "Hydro Flask", "Braun", "Honeywell"],\n'
                 '   "HBB": ["Hamilton Beach", "Proctor Silex"]}\n'
                 "Aliases come from filings, never from browsing the corpus for\n"
                 "whatever happens to have data." % path)
    with open(path) as f:
        raw = json.load(f)
    out = {}
    for tic, names in raw.items():
        out[tic] = set(corpus.normalise_brand(n) for n in names if n)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--eligibility", default="data/eligibility.csv")
    p.add_argument("--aliases", default="data/brand_aliases.json")
    p.add_argument("--categories", nargs="+", required=True,
                   help="corpus categories to stream, e.g. Home_and_Kitchen Appliances")
    p.add_argument("--outdir", default="data")
    p.add_argument("--max-bytes", type=int, default=0,
                   help="truncate each stream - ESTIMATION ONLY, marks output truncated")
    p.add_argument("--min-products", type=int, default=5)
    args = p.parse_args()

    aliases = load_aliases(args.aliases)

    eligible = []
    if os.path.exists(args.eligibility):
        with open(args.eligibility) as f:
            eligible = [r["ticker"] for r in csv.DictReader(f)
                        if r.get("eligible", "").lower() == "true"]
    else:
        eligible = sorted(aliases)
        print("!! %s not found - falling back to every ticker in the alias table."
              % args.eligibility)
        print("!! That is a DEV SHORTCUT. A real run scores eligibility first.")

    missing = [t for t in eligible if t not in aliases]
    if missing:
        sys.exit("eligible tickers with no brand aliases: %s\nAdd them to %s."
                 % (", ".join(missing), args.aliases))

    # brand key -> tickers claiming it (a shared brand is ambiguous, not a match)
    claim = defaultdict(set)
    for tic in eligible:
        for b in aliases[tic]:
            claim[b].add(tic)
    shared = {b: t for b, t in claim.items() if len(t) > 1}
    if shared:
        print("!! brands claimed by more than one company, excluded as ambiguous:")
        for b, t in sorted(shared.items()):
            print("     %-28s %s" % (b, ", ".join(sorted(t))))

    est = sum(corpus.CATEGORY_SIZES_GB.get(c, (0, 0))[0] for c in args.categories)
    print("streaming metadata for %d categories (~%.1f GB, nothing stored)%s"
          % (len(args.categories), est,
             "  [TRUNCATED SAMPLE]" if args.max_bytes else ""))

    products = defaultdict(list)
    store_hits = defaultdict(int)
    for cat in args.categories:
        for rec in corpus.stream_meta(cat, max_bytes=args.max_bytes):
            key = corpus.normalise_brand(rec.get("store"))
            if not key or key in shared:
                continue
            owners = claim.get(key)
            if not owners:
                continue
            tic = next(iter(owners))
            store_hits[tic] += 1
            products[tic].append({
                "parent_asin": rec.get("parent_asin", ""),
                "title": (rec.get("title") or "")[:300],
                "store": rec.get("store", ""),
                "main_category": rec.get("main_category", "") or cat,
                "matched_by": "store_exact",
                "price": rec.get("price") if isinstance(rec.get("price"), (int, float)) else "",
                "rating_number": rec.get("rating_number", 0) or 0,
            })

    rows = []
    for tic in eligible:
        items = products.get(tic, [])
        seen, uniq = set(), []
        for it in items:
            if it["parent_asin"] and it["parent_asin"] not in seen:
                seen.add(it["parent_asin"])
                uniq.append(it)
        reviews_known = sum(int(i["rating_number"] or 0) for i in uniq)
        usable = len(uniq) >= args.min_products

        if uniq:
            d = os.path.join(args.outdir, tic)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "products.csv"), "w", newline="",
                      encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(uniq[0].keys()))
                w.writeheader()
                w.writerows(uniq)

        rows.append({
            "ticker": tic, "matched_products": len(uniq),
            "corpus_review_count": reviews_known,
            "categories": "|".join(args.categories),
            "usable": usable,
            "reason": "" if usable else "fewer than %d matched products" % args.min_products,
            "truncated_scan": bool(args.max_bytes),
            # Deliberately NOT computed here: coverage_ratio needs a revenue
            # proxy per ASIN and reported revenue. Left blank rather than
            # guessed - an unquantifiable coverage scores 0 (criterion 1).
            "coverage_ratio": "",
        })

    out = os.path.join(args.outdir, "coverage.csv")
    os.makedirs(args.outdir, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("\n%-8s %10s %14s  %s" % ("ticker", "products", "corpus revs", "usable"))
    for r in sorted(rows, key=lambda x: -x["matched_products"]):
        print("%-8s %10d %14d  %s" % (r["ticker"], r["matched_products"],
                                      r["corpus_review_count"],
                                      "yes" if r["usable"] else "NO - " + r["reason"]))
    ok = sum(1 for r in rows if r["usable"])
    print("\n%d of %d eligible companies have usable coverage -> %s"
          % (ok, len(rows), out))
    if args.max_bytes:
        print("!! TRUNCATED SCAN - estimation only, not a coverage result.")
    elif ok < 8:
        print("!! Phase A gate NOT met (needs >= 8). Record the gap; do not")
        print("!! substitute companies from outside the screen to reach it.")


if __name__ == "__main__":
    main()
