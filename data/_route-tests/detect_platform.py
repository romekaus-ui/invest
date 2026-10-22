#!/usr/bin/env python3
"""Brand domain -> which review platform it runs, and the key its own CDN publishes.

The stage before measure_widget_route.py, which takes keys as input and cannot
find them. This finds them the way a visitor's browser does: fetch the page,
read the script tags the site itself serves. No Referer, no Origin, no cookies,
no account. A refusal is recorded as a refusal and the brand is skipped.

Review widgets usually load on a product page rather than the homepage, so a
miss on the homepage is followed by one product link found in the homepage's
own markup. Two fetches per brand, then it stops.
"""
import json, re, sys, time, urllib.error, urllib.parse, urllib.request, gzip, io

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

SIGS = [
    # platform,      regex over page HTML,                                       what group(s) mean
    ("bazaarvoice", r"apps\.bazaarvoice\.com/deployments/([\w\-\.]+)/([\w\-\.]+)/production/([\w_]+)/bv\.js", ("client","site","locale")),
    ("bazaarvoice", r"bazaarvoice\.com/[\w/\-\.]*?/([\w\-\.]+)/([\w\-\.]+)/production", ("client","site")),
    ("powerreviews",r"display\.powerreviews\.com/[\w/\-\.]*", ()),
    ("powerreviews",r"[\"']merchant_?id[\"']\s*:\s*[\"'](\d+)[\"']", ("merchant_id",)),
    ("yotpo",       r"staticw2\.yotpo\.com/([\w\-]{8,})/widget\.js", ("app_key",)),
    ("yotpo",       r"cdn-widgetsrepository\.yotpo\.com/v1/loader/([\w\-]{8,})", ("app_key",)),
    ("okendo",      r"okendo\.io|okendo\.reviews", ()),
    ("reviews.io",  r"widget\.reviews\.(io|co\.uk)", ()),
    ("stamped",     r"stamped\.io", ()),
    ("judge.me",    r"judge\.me", ()),
    ("loox",        r"loox\.io", ()),
    ("trustpilot",  r"widget\.trustpilot\.com", ()),
    ("feefo",       r"feefo\.com", ()),
]
USABLE = {"bazaarvoice", "powerreviews", "yotpo"}   # what the collector supports today


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,*/*", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return raw.decode("utf-8", "replace"), r.geturl()


def scan(html):
    out = []
    for plat, rx, names in SIGS:
        m = re.search(rx, html, re.I)
        if not m:
            continue
        keys = {}
        for i, n in enumerate(names, 1):
            try:
                keys[n] = m.group(i)
            except Exception:
                pass
        out.append({"platform": plat, "keys": keys, "usable": plat in USABLE})
    # dedupe by platform, richest key set wins
    best = {}
    for h in out:
        if h["platform"] not in best or len(h["keys"]) > len(best[h["platform"]]["keys"]):
            best[h["platform"]] = h
    return list(best.values())


def product_link(html, domain):
    """One plausible product-page link from the homepage's own markup."""
    pats = [r'href="([^"]*/products?/[^"#?]{3,80})"', r'href="([^"]*/p/[^"#?]{3,80})"',
            r'href="([^"]*/shop/[^"#?]{3,80})"', r'href="([^"]*/collections/[^"#?]*/products/[^"#?]{3,80})"']
    for p in pats:
        for m in re.finditer(p, html, re.I):
            u = m.group(1)
            if any(b in u.lower() for b in ("/account", "/cart", "gift-card", "/policies")):
                continue
            return urllib.parse.urljoin(domain, u)
    return None


def screen(name, ticker, domain):
    rec = {"company": name, "ticker": ticker, "domain": domain,
           "hits": [], "pages_fetched": [], "error": None}
    try:
        html, final = get(domain)
        rec["pages_fetched"].append(final)
        rec["hits"] = scan(html)
        if not any(h["usable"] for h in rec["hits"]):
            pl = product_link(html, final)
            if pl:
                time.sleep(1.0)
                try:
                    h2, f2 = get(pl)
                    rec["pages_fetched"].append(f2)
                    for h in scan(h2):
                        if h["platform"] not in {x["platform"] for x in rec["hits"]}:
                            rec["hits"].append(h)
                        elif len(h["keys"]) > len(next(x for x in rec["hits"] if x["platform"] == h["platform"])["keys"]):
                            rec["hits"] = [x for x in rec["hits"] if x["platform"] != h["platform"]] + [h]
                except Exception as e:
                    rec["product_page_error"] = "%s: %s" % (type(e).__name__, e)
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, e)
    rec["usable_platform"] = next((h["platform"] for h in rec["hits"] if h["usable"]), None)
    rec["usable_keys"] = next((h["keys"] for h in rec["hits"] if h["usable"]), {})
    return rec


if __name__ == "__main__":
    cands = json.load(open(sys.argv[1]))
    out = []
    for c in cands:
        r = screen(c["company"], c["ticker"], c["domain"])
        flag = r["usable_platform"] or ("—" if not r["hits"] else
                                        "(" + ",".join(h["platform"] for h in r["hits"]) + ")")
        print("%-28s %-7s %-34s %s" % (c["company"][:28], c["ticker"], flag,
                                       r["error"] or ""), flush=True)
        out.append(r)
        time.sleep(1.2)
    json.dump(out, open(sys.argv[2], "w"), indent=1)
    print("\nwrote %s" % sys.argv[2])
