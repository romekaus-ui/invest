#!/usr/bin/env python3
"""Measure a brand-hosted review-platform display API on the project's standard terms.

This is the tool that produced brand-widget-routes-2026-10-15.json. It is kept beside
its output so the numbers there are reproducible rather than merely described. It is a
ROUTE TEST, not a collector: it does not write a corpus, does not score anything, and
does not know what a peer group is. A declared collection is a separate thing and goes
through the pipeline in .claude/skills/review-tone-signal/scripts/.

It measures exactly what CORE-PROMPT and PLAN.md require every route to be judged on:
reviews available for one product, whether pagination caps and where, whether dates
GENUINELY sort descending (checked against the dates, never trusted from the sort
parameter), how far back it reaches, what fraction carry real text versus star-only,
the age of the newest review, and whether an account is needed (none is).

It never sends a Referer or an Origin header. The display keys it uses are the ones the
platform's own CDN publishes, in the clear, to every visitor's browser. If an endpoint
refuses, the refusal is printed and the walk stops; nothing is routed around.

Usage
-----
  # what deployment does a brand run, and what key does its CDN publish?
  measure_widget_route.py bv-key   <bv_client> <bv_site> [locale]

  # what are the most-reviewed products in that deployment?
  measure_widget_route.py bv-top   <passkey> [n]

  # probe how deep pagination goes and what date each depth lands on
  measure_widget_route.py bv-depth <passkey> <product_id> <offset> [offset ...]

  # full measurement, walking to exhaustion
  measure_widget_route.py bv       <passkey> <product_id> [max_pages] [out.json]
  measure_widget_route.py pr       <merchant_id> <page_id> <apikey> [max_pages] [out.json]
  measure_widget_route.py yotpo    <app_key> <product_id> [max_pages] [out.json]

  # do several products share one pooled review set, and do they clear the floor?
  measure_widget_route.py quarters <out.json> [out.json ...]
"""
import datetime as dt
import gzip
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
FLOOR = 30  # CORE-PROMPT sample-size floor: scored reviews per quarter


def _get(url, gz=False):
    """Plain GET. No Referer, no Origin, no cookies. Refusals are raised, not evaded."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip" if gz else "identity"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return r.status, raw.decode("utf-8", "replace")


def _json(url):
    code, body = _get(url)
    return code, json.loads(body)


# ---------------------------------------------------------------- key discovery

def bv_key(client, site, locale="en_US"):
    """Read the display passkey the Bazaarvoice CDN publishes for a deployment zone.

    NB: fetch bv.js and the scout file WITH gzip accepted. Bazaarvoice gzips some zones
    regardless of Accept-Encoding, and decoding that as text yields a binary blob in
    which the regexes below silently find nothing. That failure mode produced four false
    "unprovisioned stub" readings on 2026-10-15 -- Monster, Red Bull, Owala and Simply
    Good Foods were all recorded as having no deployment when all four in fact publish
    a key. A missing match is only evidence once the body is known to be text.
    """
    try:
        code, js = _get("https://apps.bazaarvoice.com/deployments/%s/%s/production/%s/bv.js"
                        % (client, site, locale), gz=True)
    except urllib.error.HTTPError as e:
        print("bv.js            HTTP %s -- no such deployment zone" % e.code)
        return None
    dc = re.search(r'displayCode:"(\d+)"', js)
    scout = re.search(r'legacyScoutUrl:"([^"]+)"', js)
    print("bv.js            HTTP %s" % code)
    print("displayCode      %s" % (dc.group(1) if dc else "- (unprovisioned stub)"))
    if not scout:
        print("legacyScoutUrl   - (unprovisioned stub: this zone serves no reviews)")
        return None
    print("legacyScoutUrl   %s" % scout.group(1))
    # The scout file is gzip-only in practice; fetched raw it is a binary blob.
    code2, js2 = _get(scout.group(1), gz=True)
    keys = [k for k in sorted(set(re.findall(r'passkey:"([A-Za-z0-9]{24,})', js2)))
            # Bazaarvoice's own key, present in every scout file; not the brand's.
            if k != "b8d57893b7754e2d390348dc172b7c76"]
    print("scout            HTTP %s" % code2)
    print("passkey(s)       %s" % (keys or "- none published"))
    return keys[0] if keys else None


def bv_top(passkey, n=12):
    """Most-reviewed products in a deployment. NB: TotalReviewCount double-counts a
    pooled product family -- confirm with `quarters`, which compares id sets."""
    q = urllib.parse.urlencode({"apiversion": "5.4", "passkey": passkey, "Limit": 100,
                                "Sort": "TotalReviewCount:desc", "Stats": "Reviews"})
    code, d = _json("https://api.bazaarvoice.com/data/products.json?" + q)
    print("HTTP %s | catalog products: %s | errors: %s" % (
        code, d.get("TotalResults"), [e.get("Code") for e in (d.get("Errors") or [])]))
    rows = [((p.get("ReviewStatistics") or {}).get("TotalReviewCount") or 0,
             p.get("Id"), (p.get("Name") or "")[:58]) for p in d.get("Results", [])]
    for c, pid, nm in sorted(rows, reverse=True)[:n]:
        print("%7d  %-42s %s" % (c, pid, nm))


def bv_depth(passkey, pid, offsets):
    """Probe the cap directly instead of assuming one."""
    for off in offsets:
        q = urllib.parse.urlencode({"apiversion": "5.4", "passkey": passkey,
                                    "Filter": "ProductId:eq:%s" % pid, "Limit": 100,
                                    "Offset": off, "Sort": "SubmissionTime:desc"})
        try:
            code, d = _json("https://api.bazaarvoice.com/data/reviews.json?" + q)
        except urllib.error.HTTPError as e:
            print("offset %-7d REFUSED HTTP %s -- recorded, stopping" % (off, e.code))
            return
        res = d.get("Results", [])
        ds = [x.get("SubmissionTime") for x in res if x.get("SubmissionTime")]
        txt = sum(1 for x in res if (x.get("ReviewText") or "").strip())
        print("offset %-7d HTTP %s total=%-6s returned=%-4d text=%-4d %s..%s %s" % (
            off, code, d.get("TotalResults"), len(res), txt,
            (ds[0] or "")[:10] if ds else "-", (ds[-1] or "")[:10] if ds else "-",
            ",".join(e.get("Code") for e in (d.get("Errors") or []))))
        time.sleep(0.4)


# ------------------------------------------------------------------ collection

def walk_bv(passkey, pid, max_pages):
    rows, pages, offset, total = [], [], 0, None
    for _ in range(max_pages):
        q = urllib.parse.urlencode({"apiversion": "5.4", "passkey": passkey,
                                    "Filter": "ProductId:eq:%s" % pid, "Limit": 100,
                                    "Offset": offset, "Sort": "SubmissionTime:desc",
                                    "Include": "Products", "Stats": "Reviews"})
        try:
            code, d = _json("https://api.bazaarvoice.com/data/reviews.json?" + q)
        except urllib.error.HTTPError as e:
            pages.append({"offset": offset, "refused_http": e.code}); break
        if d.get("HasErrors"):
            pages.append({"offset": offset, "errors": d.get("Errors")}); break
        total = d.get("TotalResults")
        res = d.get("Results", [])
        pages.append({"offset": offset, "returned": len(res), "http": code})
        if not res:
            break
        for r in res:
            src = r.get("SyndicationSource") or {}
            rows.append({"id": r.get("Id"), "date": r.get("SubmissionTime"),
                         "rating": r.get("Rating"), "text": r.get("ReviewText"),
                         "syndicated": r.get("IsSyndicated"), "source": src.get("Name")})
        offset += len(res)
        if total is not None and offset >= total:
            break
        time.sleep(0.4)
    return rows, pages, total


def walk_pr(merchant, page_id, apikey, max_pages):
    """PowerReviews. paging.size caps at 25 -- 100 returns HTTP 400. The rating is at
    metrics.rating; details.rating is always null. created_date is epoch ms."""
    base = ("https://display.powerreviews.com/m/%s/l/en_US/product/%s/reviews"
            % (merchant, page_id))
    rows, pages, frm, total = [], [], 0, None
    for _ in range(max_pages):
        q = urllib.parse.urlencode({"paging.from": frm, "paging.size": 25, "filters": "",
                                    "search": "", "sort": "Newest",
                                    "image_only": "false", "apikey": apikey})
        try:
            code, d = _json(base + "?" + q)
        except urllib.error.HTTPError as e:
            pages.append({"paging_from": frm, "refused_http": e.code}); break
        total = (d.get("paging") or {}).get("total_results", total)
        res = (d.get("results") or [{}])[0].get("reviews") or []
        pages.append({"paging_from": frm, "returned": len(res), "http": code})
        if not res:
            break
        for x in res:
            det, met = x.get("details") or {}, x.get("metrics") or {}
            ms = det.get("created_date")
            rows.append({
                "id": x.get("review_id"),
                "date": dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat() if ms else None,
                "rating": met.get("rating"), "text": det.get("comments"),
                "syndicated": (x.get("badges") or {}).get("is_syndicated"), "source": None})
        frm += len(res)
        if total and frm >= total:
            break
        time.sleep(0.4)
    return rows, pages, total


def walk_yotpo(app_key, pid, max_pages):
    """Yotpo. A WRONG app key returns HTTP 200 with pagination.page=0 and total=0
    rather than an error, so a non-zero total is the only confirmation the key is right."""
    base = "https://api-cdn.yotpo.com/v1/widget/%s/products/%s/reviews" % (app_key, pid)
    rows, pages, total = [], [], None
    for p in range(1, max_pages + 1):
        q = urllib.parse.urlencode({"per_page": 100, "page": p, "sort": "date",
                                    "direction": "desc"})
        try:
            code, d = _json(base + "?" + q)
        except urllib.error.HTTPError as e:
            pages.append({"page": p, "refused_http": e.code}); break
        resp = d.get("response") or {}
        total = (resp.get("pagination") or {}).get("total", total)
        revs = resp.get("reviews") or []
        pages.append({"page": p, "returned": len(revs), "http": code})
        if not revs:
            break
        for x in revs:
            rows.append({"id": x.get("id"), "date": x.get("created_at"),
                         "rating": x.get("score"), "text": x.get("content"),
                         "syndicated": None, "source": None,
                         "deleted_flag": x.get("deleted")})
        if total and len(rows) >= total:
            break
        time.sleep(0.3)
    return rows, pages, total


def report(platform, pid, rows, pages, total, out=None):
    ds = [r["date"] for r in rows if r["date"]]
    inv = sum(1 for i in range(len(ds) - 1) if ds[i] < ds[i + 1])
    txt = sum(1 for r in rows if (r["text"] or "").strip())
    stars = {}
    for r in rows:
        stars[r["rating"]] = stars.get(r["rating"], 0) + 1
    newest = max(ds) if ds else None
    age = None
    if newest:
        age = (dt.datetime.now(dt.timezone.utc)
               - dt.datetime.fromisoformat(newest.replace("Z", "+00:00"))).days
    rep = {
        "platform": platform, "product_id": pid,
        "total_claimed": total, "collected": len(rows),
        "unique_ids": len(set(r["id"] for r in rows)),
        "pagination_caps": bool(rows) and total is not None and len(rows) < total,
        "pages_walked": len(pages), "last_pages": pages[-3:],
        # Verified against the dates. The sort parameter is never taken on trust.
        "dates_descending_verified": inv == 0, "date_inversions": inv,
        "newest": newest, "oldest": min(ds) if ds else None,
        "newest_review_age_days": age,
        "reach_years": round((len(set(d[:10] for d in ds)) and
                              (dt.date.fromisoformat(max(ds)[:10])
                               - dt.date.fromisoformat(min(ds)[:10])).days / 365.25), 2) if ds else None,
        "with_text": txt,
        "with_text_pct": round(100.0 * txt / len(rows), 1) if rows else None,
        "syndicated": sum(1 for r in rows if r.get("syndicated")),
        "syndication_sources": sorted({r["source"] for r in rows if r.get("source")}),
        "star_spread": {str(k): v for k, v in sorted(
            stars.items(), key=lambda kv: (kv[0] is None, kv[0]))},
        "cost": "free", "account_needed": False,
    }
    print(json.dumps(rep, indent=2))
    if out:
        json.dump({"report": rep, "reviews": rows}, open(out, "w"), indent=1)
        print("\nwrote %s -- a ROUTE TEST, not a declared corpus (see PLAN.md)" % out)
    return rep


# ------------------------------------------------------- pooling + floor checks

def quarters(files):
    """Two questions at once.

    1. Do these products actually hold DIFFERENT reviews? Bazaarvoice pools across a
       product family exactly as Amazon pools across variants: Traeger TFB57GLE and
       TFB78GLE are different grills and return identical 1,215-review id sets. Summing
       TotalReviewCount across a catalog therefore double-counts.
    2. Does the deduplicated union clear the per-quarter floor, and over what run?
       One product cannot carry a series -- its flow decays as the model ages.
    """
    sets, byq = {}, {}
    for f in files:
        d = json.load(open(f))
        pid = d["report"]["product_id"]
        sets[pid] = set(r["id"] for r in d["reviews"])
        for r in d["reviews"]:
            if not r.get("date") or not (r.get("text") or "").strip():
                continue
            k = "%sQ%d" % (r["date"][:4], (int(r["date"][5:7]) - 1) // 3 + 1)
            byq.setdefault(k, set()).add(r["id"])

    names = list(sets)
    print("pairwise review-id overlap (100% means one pooled family, not two products)")
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = sets[names[i]], sets[names[j]]
            n = len(a & b)
            print("  %-12s vs %-12s shared=%-6d (%.0f%% of smaller)" % (
                names[i], names[j], n, 100.0 * n / max(1, min(len(a), len(b)))))
    union = set().union(*sets.values()) if sets else set()
    print("\nunion %d unique vs %d summed across parts -- the gap is double counting"
          % (len(union), sum(len(s) for s in sets.values())))

    print("\ndeduplicated TEXT-BEARING reviews per quarter (floor %d):" % FLOOR)
    ks, ok, run, best = sorted(byq), 0, 0, (0, None, None)
    for k in ks:
        n = len(byq[k])
        good = n >= FLOOR
        ok += good
        run = run + 1 if good else 0
        if run > best[0]:
            best = (run, ks[ks.index(k) - run + 1], k)
        print("  %-8s %5d  %s" % (k, n, "yes" if good else "NO"))
    print("\n%d of %d quarters clear the floor" % (ok, len(ks)))
    if best[1]:
        print("longest consecutive run: %d quarters, %s through %s" % best)
    print("MIN_QUARTERS of 12: %s" % ("cleared" if ok >= 12 else "NOT cleared"))
    print("note: the first and last quarter may be partial at the collection edge")


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "bv-key":
        bv_key(a[0], a[1], a[2] if len(a) > 2 else "en_US")
    elif cmd == "bv-top":
        bv_top(a[0], int(a[1]) if len(a) > 1 else 12)
    elif cmd == "bv-depth":
        bv_depth(a[0], a[1], [int(x) for x in a[2:]])
    elif cmd == "bv":
        rows, pages, total = walk_bv(a[0], a[1], int(a[2]) if len(a) > 2 else 400)
        report("bazaarvoice", a[1], rows, pages, total, a[3] if len(a) > 3 else None)
    elif cmd == "pr":
        rows, pages, total = walk_pr(a[0], a[1], a[2], int(a[3]) if len(a) > 3 else 400)
        report("powerreviews", a[1], rows, pages, total, a[4] if len(a) > 4 else None)
    elif cmd == "yotpo":
        rows, pages, total = walk_yotpo(a[0], a[1], int(a[2]) if len(a) > 2 else 400)
        report("yotpo", a[1], rows, pages, total, a[3] if len(a) > 3 else None)
    elif cmd == "quarters":
        quarters(a)
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
