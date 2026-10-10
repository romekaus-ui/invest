#!/usr/bin/env python3
"""Streaming access to McAuley-Lab/Amazon-Reviews-2023.

The corpus is 750 GB and the HuggingFace datasets-server does not index it
(no preview, no filter, no search - checked 2026-10-11). Every query is
therefore a streaming pass over plain .jsonl files served from the hub.

NOTHING IS STORED. Lines are parsed and discarded as they arrive, so a pass
costs bandwidth and time, not disk.

Relevant category sizes (GB), measured 2026-10-11:

    category                        meta   reviews
    Appliances                      0.29      0.93
    Musical_Instruments             0.63      1.56
    Office_Products                 2.15      5.78
    Health_and_Household            2.47     11.40
    Toys_and_Games                  2.64      7.32
    Patio_Lawn_and_Garden           2.71      7.75
    Cell_Phones_and_Accessories     4.02      9.34
    Sports_and_Outdoors             4.13      9.26
    Tools_and_Home_Improvement      4.85     12.78
    Electronics                     5.25     22.62
    Home_and_Kitchen               11.79     31.41
    TOTAL                          40.92    120.13

Standard library only.
"""

import gzip
import json
import sys
import time
import urllib.request

REPO = "https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main"
META = REPO + "/raw/meta_categories/meta_{cat}.jsonl"
REVIEWS = REPO + "/raw/review_categories/{cat}.jsonl"

CATEGORY_SIZES_GB = {
    "Appliances": (0.29, 0.93), "Musical_Instruments": (0.63, 1.56),
    "Office_Products": (2.15, 5.78), "Health_and_Household": (2.47, 11.40),
    "Toys_and_Games": (2.64, 7.32), "Patio_Lawn_and_Garden": (2.71, 7.75),
    "Cell_Phones_and_Accessories": (4.02, 9.34), "Sports_and_Outdoors": (4.13, 9.26),
    "Tools_and_Home_Improvement": (4.85, 12.78), "Electronics": (5.25, 22.62),
    "Home_and_Kitchen": (11.79, 31.41),
}


def normalise_brand(s):
    """Fold a brand string to a comparable key.

    The corpus `store` field is free text: 'Logitech', 'Logitech G',
    'LOGITECH', 'Logitech Store' all occur. Aliases are resolved by the
    caller's table; this only removes casing and storefront boilerplate.
    """
    if not s:
        return ""
    s = s.lower().strip()
    for junk in (" store", " official", " brand", " shop", "'s"):
        if s.endswith(junk):
            s = s[: -len(junk)].strip()
    return " ".join(s.replace("&", "and").split())


def stream_jsonl(url, max_bytes=0, progress_every=250_000, label=""):
    """Yield parsed objects from a remote .jsonl, never storing the file.

    max_bytes > 0 truncates the pass - use for match-rate estimation before
    committing to a full stream. A truncated pass is NOT a coverage result and
    callers must mark it as such.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": "review-tone-signal/1.0 (research; streaming)",
        "Accept-Encoding": "identity",
    })
    seen = n = bad = 0
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            r = gzip.GzipFile(fileobj=r)
        for raw in r:
            seen += len(raw)
            if max_bytes and seen > max_bytes:
                break
            try:
                yield json.loads(raw)
            except ValueError:
                bad += 1
                continue
            n += 1
            if progress_every and n % progress_every == 0:
                mb = seen / 1e6
                rate = mb / max(time.time() - t0, 1e-9)
                sys.stderr.write("    %s %d recs · %.0f MB · %.1f MB/s\r"
                                 % (label, n, mb, rate))
                sys.stderr.flush()
    if progress_every:
        sys.stderr.write("    %s %d recs · %.0f MB · %.0f s%s\n"
                         % (label, n, seen / 1e6, time.time() - t0,
                            "  [TRUNCATED]" if (max_bytes and seen > max_bytes) else ""))
    if bad:
        sys.stderr.write("    %s %d unparseable lines skipped\n" % (label, bad))


def stream_meta(category, **kw):
    return stream_jsonl(META.format(cat=category), label="meta/%s" % category, **kw)


def stream_reviews(category, **kw):
    return stream_jsonl(REVIEWS.format(cat=category), label="rev/%s" % category, **kw)
