#!/usr/bin/env python3
"""Stage B2 - extract dated reviews for matched ASINs from the 2023 corpus.

Reads data/{TICKER}/products.csv (written by coverage.py), streams the review
files for the relevant corpus categories, keeps the rows whose `parent_asin`
belongs to a tracked company, and writes data/{TICKER}/reviews.csv.

NOTHING IS STORED BEYOND THE MATCHED ROWS. The corpus is 750 GB; corpus.py
streams and discards. A whole category is never downloaded. `--max-bytes`
truncates a pass for match-rate estimation and marks the output truncated -
a truncated pass is not an extraction result.

Two passes, so the expensive half is never repeated for a cheap mistake:

  pass 1  stream the categories, append matched rows to
          data/{TICKER}/reviews_raw.jsonl  (gitignored)
  pass 2  dedupe, assign fiscal quarters, sort, write reviews.csv + manifest

`--from-raw` runs pass 2 alone. Getting a fiscal calendar wrong is the most
likely defect in this stage (F11) and must not cost a 22 GB re-stream to fix.


WITHHOLD `rating` FROM THE SCORER
---------------------------------
reviews.csv carries `rating` because the Phase B star-vs-tone correlation
check needs it (F8) and because a tone series that merely reproduces the star
average has demonstrated nothing. Whoever writes score_reviews.py must pass
`title` and `text` ONLY. A scorer that sees the star rating echoes it, and the
resulting correlation of ~0.95 is not a finding about language - it is the
star average wearing a new label.


FISCAL QUARTERS ARE THE COMPANY'S OWN (F11)
-------------------------------------------
Never calendar quarters. The fiscal year end comes from EDGAR's authoritative
`fiscalYearEnd` field via fetch_sec.fiscal_year_end_month - the same code path
that writes fundamentals.csv, reused rather than reimplemented. Inferring it
from the mode of 10-K period ends put Helen of Troy in August when it in fact
ends in February, and a one-month error shifts every join by a quarter.

Two labelling methods, in order of preference:

  period_map          exact period_start/period_end intervals read from
                      data/{TICKER}/fundamentals.csv. These are the boundaries
                      the company actually filed, so they are right even for a
                      52/53-week (4-4-5) calendar.
  fy_month_arithmetic month arithmetic from the fiscal year end month, via
                      fetch_sec.fiscal_quarter. Correct for calendar-month
                      fiscal years; off by up to a few days at the boundaries
                      of a 52/53-week year.

`--quarter-method auto` (default) prefers the period map and falls back per
review; the split is counted and recorded in the manifest. XBRL starts around
2009 and a 10-K usually tags the full year rather than Q4, so a long history
will legitimately use both. `--quarter-method fy_month` forces one consistent
method when a mixed series is not acceptable.

A review enters a quarter only if its own timestamp falls inside that quarter
(CORE-PROMPT s4). No forward fill, no interpolation, no reassignment.


F12 - INCENTIVISED AND DELETED REVIEWS
--------------------------------------
Unverified reviews are RETAINED, never silently dropped: the contract is that
they are analysed as a separate series before any decision to include them.
`verified_purchase` share is reported per quarter so the non-stationarity is
visible here rather than discovered downstream.

What this stage cannot fix, and therefore states: the corpus is a snapshot.
Reviews Amazon had already deleted before it was taken are invisible, and no
count here can recover them. The review population is not a random sample of
buyers in any quarter, and early quarters of a product's life are the ones
most distorted by review gating and vine/incentivised placement.


Inputs : data/{TICKER}/products.csv, data/coverage.csv (for the category list),
         data/{TICKER}/fundamentals.csv (optional, for exact quarter bounds),
         data/universe*.csv (for the CIK)
Outputs: data/{TICKER}/reviews.csv, data/{TICKER}/manifest.json

Standard library only. Needs SEC_USER_AGENT unless --fy-end-month is given.
"""

import argparse
import bisect
import csv
import glob
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402
from fetch_sec import fiscal_quarter, fiscal_year_end_month  # noqa: E402
from screen_universe import ua  # noqa: E402

# CORE-PROMPT s5. Enforced downstream; reported here so a company that cannot
# clear the floors is known before anything is spent on scoring it.
MIN_REVIEWS_PER_QUARTER = 30
MIN_QUARTERS = 12

CORPUS_DATASET = "McAuley-Lab/Amazon-Reviews-2023"
# The corpus is a fixed snapshot: reviews from May 1996 to Sep 2023. Recorded
# because it bounds every claim this pipeline can make, and because reviews
# deleted before the snapshot are not in it and cannot be counted (F12).
CORPUS_SNAPSHOT = "2023-09"

# Epoch-ms bounds for a timestamp that could belong to an Amazon review. A row
# outside them cannot be honestly assigned to a quarter, so it is dropped and
# counted rather than clamped.
TS_MIN_MS = 788918400000      # 1995-01-01
TS_MAX_MS = 1727740800000     # 2024-10-01

RAW_FIELDS = ("user_id", "parent_asin", "timestamp", "rating", "title", "text",
              "verified_purchase", "helpful_vote")

# data-contracts.md - exact, in this order.
REVIEW_FIELDS = ("review_id", "parent_asin", "timestamp", "review_date",
                 "fiscal_quarter", "rating", "title", "text",
                 "verified_purchase", "helpful_vote")


def review_id(user_id, parent_asin, timestamp):
    """Stable hash of user_id + parent_asin + timestamp.

    sha1 of an explicit delimited string, NOT the builtin hash(), which is
    salted per process and would give a different id on every run. Stable
    means a re-extraction produces byte-identical ids, so scores.csv keyed on
    review_id survives a re-run of this stage.
    """
    key = "%s|%s|%s" % (user_id or "", parent_asin or "", timestamp)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def load_products(outdir, ticker):
    """Tracked parent_asins for one ticker. Phase A3 must have produced these."""
    path = os.path.join(outdir, ticker, "products.csv")
    if not os.path.exists(path):
        sys.exit(
            "no %s\n\n"
            "Phase A3 has not produced a product list for %s. Run coverage.py\n"
            "first. Do not hand-write a product list here: an ASIN set assembled\n"
            "by looking for whatever has review data is coverage choosing the\n"
            "companies, which is the thing CORE-PROMPT s2 forbids."
            % (path, ticker))
    with open(path, encoding="utf-8") as f:
        asins = {r["parent_asin"].strip() for r in csv.DictReader(f)
                 if r.get("parent_asin", "").strip()}
    if not asins:
        sys.exit("%s has no parent_asin values." % path)
    return asins


def load_categories(outdir, explicit):
    """Which category review files to stream.

    Defaults to the categories coverage.py scanned, recorded in coverage.csv.
    Extracting reviews from a category whose metadata was never scanned would
    match ASINs that are not in any products.csv, so the two lists have to
    agree; this makes them agree by construction.
    """
    if explicit:
        return list(explicit)
    path = os.path.join(outdir, "coverage.csv")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                cats = [c for c in (r.get("categories") or "").split("|") if c]
                if cats:
                    return cats
    sys.exit("no --categories given and no category list in %s.\n"
             "Pass the same categories coverage.py scanned." % path)


def load_cik(outdir, ticker, universe):
    """CIK for a ticker, from the frozen point-in-time universe."""
    paths = [universe] if universe else sorted(
        glob.glob(os.path.join(outdir, "universe.csv"))
        + glob.glob(os.path.join(outdir, "universe_*.csv")))
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("ticker") or "").strip().upper() == ticker.upper():
                    cik = (r.get("cik") or "").strip()
                    if cik:
                        return cik.zfill(10), path
    return None, None


def resolve_fiscal_calendar(outdir, ticker, universe, fy_end_month):
    """(month, provenance string). EDGAR's own field, or an explicit override.

    Reuses fetch_sec.fiscal_year_end_month. Deliberately no third option: the
    pipeline does not get to guess a fiscal calendar.
    """
    if fy_end_month:
        return fy_end_month, "operator-supplied --fy-end-month (verify from the 10-K)"
    cik, src = load_cik(outdir, ticker, universe)
    if not cik:
        sys.exit("no CIK for %s in the universe file(s).\n"
                 "Either add it or pass --fy-end-month after verifying it from\n"
                 "the company's 10-K. This stage will not infer a fiscal calendar."
                 % ticker)
    ua()  # fail now, with instructions, rather than mid-stream
    month = fiscal_year_end_month(cik)
    return month, "EDGAR submissions fiscalYearEnd, CIK %s (via %s)" % (cik, src)


def as_bool(v):
    """Coerce a corpus truth value without the string trap.

    bool("false") is True. The corpus ships real booleans, but a single
    stringified field would silently invert `verified_purchase` for every row
    and quietly destroy the F12 check, so the coercion is explicit.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y", "t")
    return bool(v)


def load_period_map(outdir, ticker):
    """[(start, end, label)] of filed fiscal periods, earliest first.

    From fetch_sec.py's fundamentals.csv, which carries the period bounds the
    company actually reported. `comparative_only` rows are kept: the flag is
    about when the revenue figure became public, which has no bearing on where
    a calendar boundary sits.

    Returns (periods, overlaps). Lookup assumes the periods are disjoint, so
    that assumption is CHECKED rather than trusted: two vintages that filed
    different bounds for one quarter would make a boundary review's quarter
    depend on scan order, which is F11 arriving quietly.
    """
    path = os.path.join(outdir, ticker, "fundamentals.csv")
    if not os.path.exists(path):
        return [], []
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a, b, q = (r.get("period_start"), r.get("period_end"),
                       r.get("fiscal_quarter"))
            if a and b and q:
                out.append((a, b, q))
    out = sorted(set(out))
    overlaps = [(out[i], out[i + 1]) for i in range(len(out) - 1)
                if out[i + 1][0] <= out[i][1]]
    return out, overlaps


def assign_quarter(review_date, periods, starts, fy_end_month, method):
    """(label, how). A review lands in the quarter containing its timestamp.

    CORE-PROMPT s4: containment only. Nothing is forward-filled onto a review
    whose timestamp sits outside every known period, and nothing is nudged
    across a boundary.
    """
    if method != "fy_month" and periods:
        i = bisect.bisect_right(starts, review_date) - 1
        if i >= 0 and review_date <= periods[i][1]:
            return periods[i][2], "period_map"
    if method == "period_map":
        return "", "unmapped"
    return fiscal_quarter(review_date, fy_end_month), "fy_month_arithmetic"


def stream_pass(categories, asin_owner, tickers, outdir, max_bytes):
    """Pass 1. Stream review files; append matched rows per ticker.

    Returns {category: records_scanned}. The count is of records corpus.py
    parsed and yielded; lines it could not parse are reported to stderr by
    corpus.py and are not visible here.
    """
    handles = {}
    for tic in tickers:
        d = os.path.join(outdir, tic)
        os.makedirs(d, exist_ok=True)
        # Truncate: a re-stream replaces the raw file rather than doubling it.
        handles[tic] = open(os.path.join(d, "reviews_raw.jsonl"), "w",
                            encoding="utf-8")

    est = sum(corpus.CATEGORY_SIZES_GB.get(c, (0, 0))[1] for c in categories)
    print("streaming reviews for %d categories (~%.1f GB, nothing stored)%s"
          % (len(categories), est,
             "  [TRUNCATED SAMPLE]" if max_bytes else ""))
    unknown = [c for c in categories if c not in corpus.CATEGORY_SIZES_GB]
    if unknown:
        print("   (size unknown for %s - not in corpus.CATEGORY_SIZES_GB)"
              % ", ".join(unknown))

    scanned = {}
    matched = defaultdict(int)
    try:
        for cat in categories:
            n = 0
            for rec in corpus.stream_reviews(cat, max_bytes=max_bytes):
                n += 1
                tic = asin_owner.get(rec.get("parent_asin"))
                if not tic:
                    continue
                handles[tic].write(json.dumps(
                    {k: rec.get(k) for k in RAW_FIELDS}, ensure_ascii=False) + "\n")
                matched[tic] += 1
            scanned[cat] = n
            print("  %-32s %12d scanned, %d matched"
                  % (cat, n, sum(matched[t] for t in tickers)))
    finally:
        for h in handles.values():
            h.close()
    return scanned


def build_ticker(ticker, outdir, categories, scanned, n_tracked, args):
    """Pass 2. Raw rows -> deduped, quarter-labelled, sorted reviews.csv."""
    raw = os.path.join(outdir, ticker, "reviews_raw.jsonl")
    if not os.path.exists(raw):
        sys.exit("no %s - run without --from-raw first." % raw)

    fy_end_month, fy_from = resolve_fiscal_calendar(
        outdir, ticker, args.universe, args.fy_end_month)
    period_map, overlaps = load_period_map(outdir, ticker)
    starts = [p[0] for p in period_map]
    if overlaps:
        print("!! %s: %d overlapping filed periods in fundamentals.csv. Quarter"
              % (ticker, len(overlaps)))
        print("!! assignment near those bounds is ambiguous - resolve it before")
        print("!! trusting this series (F11). First: %s / %s"
              % (overlaps[0][0], overlaps[0][1]))

    rows, seen = [], set()
    dupes = bad_ts = unmapped = no_helpful = 0
    how_counts = defaultdict(int)

    with open(raw, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            try:
                ts = int(rec.get("timestamp"))
            except (TypeError, ValueError):
                bad_ts += 1
                continue
            if not TS_MIN_MS <= ts <= TS_MAX_MS:
                bad_ts += 1
                continue
            rid = review_id(rec.get("user_id"), rec.get("parent_asin"), ts)
            if rid in seen:
                dupes += 1   # same review reached us from two category files
                continue
            seen.add(rid)

            d = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).date().isoformat()
            q, how = assign_quarter(d, period_map, starts, fy_end_month,
                                    args.quarter_method)
            how_counts[how] += 1
            if not q:
                unmapped += 1
                continue

            text = rec.get("text") or ""
            title = rec.get("title") or ""
            # Phase C reproduces Amazon's helpfulness-sorted ~50-review slice
            # and cannot run without this field. A missing one defaults to 0,
            # which is a real value, so the substitution is counted.
            hv = rec.get("helpful_vote")
            if hv is None:
                no_helpful += 1
            try:
                rating = float(rec.get("rating"))
            except (TypeError, ValueError):
                rating = ""
            rows.append({
                "review_id": rid,
                "parent_asin": rec.get("parent_asin") or "",
                "timestamp": ts,
                "review_date": d,
                "fiscal_quarter": q,
                # Retained for the F8 star-vs-tone check. NOT an input to the
                # scorer - see the module docstring.
                "rating": rating,
                # Verbatim apart from NULs, which break CSV readers. The scorer
                # must see what was actually published, markup and all.
                "title": title.replace("\x00", ""),
                "text": text.replace("\x00", ""),
                "verified_purchase": as_bool(rec.get("verified_purchase")),
                "helpful_vote": int(hv or 0),
            })

    # Deterministic order, so a re-run produces a byte-identical file that can
    # be hashed and compared.
    rows.sort(key=lambda r: (r["timestamp"], r["review_id"]))

    out = os.path.join(outdir, ticker, "reviews.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(REVIEW_FIELDS))
        w.writeheader()
        w.writerows(rows)

    if args.discard_raw:
        os.remove(raw)

    stats = summarise(ticker, rows, period_map, fy_end_month)
    stats.update({
        "n_asins_tracked": n_tracked,
        "raw_rows_duplicates_dropped": dupes,
        "bad_timestamp_dropped": bad_ts,
        "unmapped_dropped": unmapped,
        "missing_helpful_vote": no_helpful,
        "overlapping_periods": len(overlaps),
        "quarter_source_counts": dict(how_counts),
        "quarter_method": args.quarter_method,
        "fy_end_month": fy_end_month,
        "fy_verified_from": fy_from,
        "n_periods_in_map": len(period_map),
        "out": out,
    })
    write_manifest(outdir, ticker, categories, scanned, stats, args)
    return stats


def summarise(ticker, rows, period_map, fy_end_month):
    """Per-quarter counts, verified share (F12) and the s5 floor check."""
    by_q = defaultdict(list)
    for r in rows:
        by_q[r["fiscal_quarter"]].append(r)

    per_q = []
    for q in sorted(by_q, key=lambda k: min(x["timestamp"] for x in by_q[k])):
        rs = by_q[q]
        per_q.append({
            "fiscal_quarter": q,
            "n": len(rs),
            "n_asins": len({x["parent_asin"] for x in rs}),
            "verified_share": round(
                sum(1 for x in rs if x["verified_purchase"]) / len(rs), 4),
            "date_min": min(x["review_date"] for x in rs),
            "date_max": max(x["review_date"] for x in rs),
            "clears_review_floor": len(rs) >= MIN_REVIEWS_PER_QUARTER,
        })

    clearing = [q for q in per_q if q["clears_review_floor"]]
    return {
        "ticker": ticker,
        "n_reviews": len(rows),
        "n_asins_with_reviews": len({r["parent_asin"] for r in rows}),
        "review_date_min": min((r["review_date"] for r in rows), default=""),
        "review_date_max": max((r["review_date"] for r in rows), default=""),
        "verified_share": round(
            sum(1 for r in rows if r["verified_purchase"]) / len(rows), 4)
        if rows else 0.0,
        "quarters_total": len(per_q),
        "quarters_clearing_review_floor": len(clearing),
        "clears_min_quarters": len(clearing) >= MIN_QUARTERS,
        "per_quarter": per_q,
    }


def write_manifest(outdir, ticker, categories, scanned, stats, args):
    """Merge into manifest.json. Never overwritten wholesale (contract)."""
    path = os.path.join(outdir, ticker, "manifest.json")
    man = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                man = json.load(f)
        except ValueError:
            man = {}

    man["ticker"] = ticker
    man["fiscal_calendar"] = {
        "fy_end_month": stats["fy_end_month"],
        "verified_from": stats["fy_verified_from"],
    }
    man["corpus"] = {
        "dataset": CORPUS_DATASET,
        "categories": list(categories),
        "snapshot": CORPUS_SNAPSHOT,
    }
    man["extracted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prior = man.get("extraction") or {}
    # A --from-raw rebuild did not stream anything. Carry the earlier pass's
    # scan counts forward rather than blanking the provenance of the stream
    # that actually produced reviews_raw.jsonl.
    if scanned:
        rec_scanned = scanned
        rec_total = sum(scanned.values())
    else:
        rec_scanned = prior.get("records_scanned") or {}
        rec_total = prior.get("records_scanned_total")

    man["extraction"] = {
        "script": "extract_reviews.py",
        # Records corpus.py parsed and yielded. Lines it could not parse are
        # counted to stderr by corpus.py and are not included here.
        "records_scanned": rec_scanned,
        "records_scanned_total": rec_total,
        "n_reviews": stats["n_reviews"],
        "n_asins_tracked": stats["n_asins_tracked"],
        "n_asins_with_reviews": stats["n_asins_with_reviews"],
        "raw_rows_duplicates_dropped": stats["raw_rows_duplicates_dropped"],
        "bad_timestamp_dropped": stats["bad_timestamp_dropped"],
        "unmapped_dropped": stats["unmapped_dropped"],
        "missing_helpful_vote": stats["missing_helpful_vote"],
        "overlapping_periods": stats["overlapping_periods"],
        "review_date_min": stats["review_date_min"],
        "review_date_max": stats["review_date_max"],
        "quarter_method": stats["quarter_method"],
        "quarter_source_counts": stats["quarter_source_counts"],
        "n_periods_in_map": stats["n_periods_in_map"],
        "verified_share": stats["verified_share"],
        "quarters_total": stats["quarters_total"],
        "quarters_clearing_review_floor": stats["quarters_clearing_review_floor"],
        "min_reviews_per_quarter": MIN_REVIEWS_PER_QUARTER,
        "min_quarters": MIN_QUARTERS,
        "clears_min_quarters": stats["clears_min_quarters"],
        "floor_is_on_scored_reviews": (
            "Counts here are pre-scoring and are an UPPER BOUND. The s5 floor "
            "applies to scored reviews, after seller/shipping exclusions and "
            "scoring failures, so a quarter clearing it here may still fail."),
        "rating_withheld_from_scorer": True,
        "truncated_scan": bool(args.max_bytes),
        "from_raw_only": bool(args.from_raw),
        "per_quarter": stats["per_quarter"],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(man, f, indent=2, sort_keys=False)
        f.write("\n")


def report(stats):
    s = stats
    print("\n%s: %d reviews across %d ASINs (%d tracked), %s to %s"
          % (s["ticker"], s["n_reviews"], s["n_asins_with_reviews"],
             s["n_asins_tracked"], s["review_date_min"] or "-",
             s["review_date_max"] or "-"))
    print("  fiscal year ends month %d  [%s]"
          % (s["fy_end_month"], s["fy_verified_from"]))
    print("  quarter labels: %s  (%d filed periods in map)"
          % (", ".join("%s=%d" % kv for kv in sorted(
              s["quarter_source_counts"].items())) or "-",
             s["n_periods_in_map"]))
    dropped = (s["raw_rows_duplicates_dropped"] + s["bad_timestamp_dropped"]
               + s["unmapped_dropped"])
    if dropped:
        print("  dropped: %d duplicate, %d bad timestamp, %d unmapped quarter"
              % (s["raw_rows_duplicates_dropped"], s["bad_timestamp_dropped"],
                 s["unmapped_dropped"]))
    if s["missing_helpful_vote"]:
        print("  !! %d rows had no helpful_vote, defaulted to 0 - Phase C reads"
              % s["missing_helpful_vote"])
        print("  !! this field and a defaulted 0 is indistinguishable from a real one")
    print("  verified_purchase share overall: %.1f%%  (F12)"
          % (100 * s["verified_share"]))

    print("\n  %-10s %7s %7s %9s  %s" % ("quarter", "n", "asins", "verified",
                                         "floor"))
    for q in s["per_quarter"]:
        print("  %-10s %7d %7d %8.0f%%  %s"
              % (q["fiscal_quarter"], q["n"], q["n_asins"],
                 100 * q["verified_share"],
                 "ok" if q["clears_review_floor"] else "below"))

    print("\n  %d of %d quarters clear the %d-review floor (CORE-PROMPT s5)"
          % (s["quarters_clearing_review_floor"], s["quarters_total"],
             MIN_REVIEWS_PER_QUARTER))
    print("  %s needs >= %d usable quarters: %s"
          % (s["ticker"], MIN_QUARTERS,
             "clears it at extraction" if s["clears_min_quarters"]
             else "DOES NOT CLEAR IT - record as dropped, with the reason"))
    print("  Those counts are PRE-SCORING and an upper bound: seller/shipping")
    print("  exclusions and scoring failures can only lower them.")
    print("  -> %s" % s["out"])


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tickers", nargs="+",
                   help="tickers with a data/{TICKER}/products.csv from coverage.py")
    p.add_argument("--categories", nargs="+",
                   help="corpus categories to stream; default: those in coverage.csv")
    p.add_argument("--outdir", default="data")
    p.add_argument("--universe", help="universe CSV holding the CIK; default: data/universe*.csv")
    p.add_argument("--fy-end-month", type=int,
                   help="fiscal year end month, verified from the 10-K; "
                        "overrides the EDGAR lookup")
    p.add_argument("--quarter-method", choices=("auto", "period_map", "fy_month"),
                   default="auto",
                   help="auto: filed period bounds where known, month arithmetic "
                        "elsewhere (default). period_map: filed bounds only, drop "
                        "reviews outside them. fy_month: month arithmetic only")
    p.add_argument("--from-raw", action="store_true",
                   help="skip the stream; rebuild from reviews_raw.jsonl")
    p.add_argument("--discard-raw", action="store_true",
                   help="delete reviews_raw.jsonl after building reviews.csv")
    p.add_argument("--max-bytes", type=int, default=0,
                   help="truncate each stream - ESTIMATION ONLY, marks output truncated")
    args = p.parse_args()

    tickers = [t.upper() for t in args.tickers]
    if args.fy_end_month and len(tickers) > 1:
        sys.exit("--fy-end-month applies to every ticker in the run, and these\n"
                 "companies do not share a fiscal calendar unless you have\n"
                 "checked that they do. Run them one at a time, or drop the\n"
                 "flag and let EDGAR answer per company (F11).")
    categories = load_categories(args.outdir, args.categories)

    asin_owner, tracked = {}, {}
    ambiguous = set()
    for tic in tickers:
        asins = load_products(args.outdir, tic)
        tracked[tic] = len(asins)
        for a in asins:
            if a in asin_owner and asin_owner[a] != tic:
                ambiguous.add(a)
            asin_owner[a] = tic
    for a in ambiguous:
        asin_owner.pop(a, None)
    if ambiguous:
        print("!! %d ASINs claimed by more than one ticker, excluded as ambiguous"
              % len(ambiguous))

    print("%d tickers, %d tracked ASINs, categories: %s"
          % (len(tickers), len(asin_owner), ", ".join(categories)))

    scanned = {}
    if args.from_raw:
        print("--from-raw: not streaming; rebuilding from reviews_raw.jsonl")
    else:
        scanned = stream_pass(categories, asin_owner, tickers, args.outdir,
                              args.max_bytes)

    for tic in tickers:
        stats = build_ticker(tic, args.outdir, categories, scanned,
                             tracked[tic], args)
        report(stats)

    if args.max_bytes:
        print("\n!! TRUNCATED SCAN - estimation only, not an extraction result.")
    print("\nreviews.csv carries `rating`. Pass title and text ONLY to the")
    print("scorer: a scorer that sees the stars reproduces them (F8).")


if __name__ == "__main__":
    main()
