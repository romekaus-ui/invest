#!/usr/bin/env python3
"""Stage D1 - collect FRESH Amazon reviews, behind one interface, two routes.

Replaces the archived corpus extractor (`archive/historical-harness/scripts/
extract_reviews.py`). Its fiscal-calendar handling and its dedup discipline are
reused here almost unchanged, because they were right. Its data source is not:
the 2023 corpus is a frozen snapshot ending 2023-09, and a frozen snapshot
cannot produce a series that starts today (CORE-PROMPT §0).

    Route A   the user's own logged-in Amazon session, read through the
              built-in browser. The only route that can sort by MOST RECENT,
              and therefore the only route that produces a time series rather
              than a ranking (CORE-PROMPT §3, §4).
    Route B   a named commercial provider (Apify / Oxylabs / Rainforest).
              Scales, costs money, and its sort order is whatever the provider
              actually gives you rather than what its documentation advertises.

Both routes write the same contract and EVERY BATCH RECORDS WHICH ROUTE
PRODUCED IT (CORE-PROMPT §1.4). A tone number whose batch cannot say how it was
sampled is not a measurement.


THE NUMBER THAT MUST TRAVEL WITH EVERY TONE NUMBER
--------------------------------------------------
`coverage_ratio` = reviews collected / the total the product page claims.

CORE-PROMPT §4 requires it reported with every tone number, and the reason is
blunt: Amazon caps review pagination at 10 pages of ~10, so one recency pass
over a product with 8,672 ratings reaches on the order of 1% of them. A tone
mean over 1% of a product's reviews is a tone mean over 1% of a product's
reviews. It is not the product's tone and must never be presented as if it
were. This script computes the ratio, writes it into every batch and into the
manifest, and refuses to write a batch that cannot state its denominator.


WHAT THIS SCRIPT WILL NOT DO
----------------------------
- It never computes a tone number. That is `score_reviews.py`'s job and the
  separation is the point (CORE-PROMPT §1.2).
- It never writes review text that did not come off a page. Route A payloads
  carry a SHA-256 computed in the browser over the review text as it stood in
  the DOM; `capture` recomputes it and REFUSES THE BATCH on mismatch. The
  assistant is the transport between the page and the disk, and this makes
  §1.3 a mechanical check rather than a promise.
- It never defeats a block. A sign-in redirect, CAPTCHA, throttle or challenge
  is recorded as a batch with `blocked` set and a reason, and collection stops
  (CORE-PROMPT §9).

Standard library only (references/numeric-stack.md).
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, OrderedDict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTOR_JS = os.path.join(SCRIPT_DIR, "capture_reviews.js")

COLLECTOR_VERSION = "d1-collect-v1"

# `data/<group_id>/<company_slug>/reviews.csv` as CONTRACTS.md §2 defines it.
REVIEW_FIELDS = ("review_id", "asin", "parent_asin", "marketplace",
                 "review_country", "review_date", "rating", "title", "text",
                 "verified_purchase", "helpful_vote", "batch_id")

# `parent_asin` is the join key, not `asin` (CONTRACTS.md §2).
#
# Amazon serves reviews at the PARENT asin, pooled across every variant -
# colour, size, pack count. A row keyed on the child asin therefore holds the
# parent's reviews under a label claiming a narrower population, and dedup by
# review_id does NOT catch it: those are genuinely distinct reviews, just of a
# different population than the label says. Of the fifteen products in the
# peer-group component's first real pass, fourteen were variant children.
#
# Two consequences this column exists to keep visible:
#   - what is measured is variation-family tone, not the named product's tone,
#     so nothing downstream may caption a chart with the child's name as though
#     the reviews were of that variant;
#   - choosing a product for continuity does not buy continuity of the review
#     pool, because Amazon re-parents variation families over time. The
#     population can change underneath a series without any id changing, which
#     is why the parent is captured from the PAGE and checked against the group
#     file on every batch rather than copied from it.

# There is deliberately NO `fiscal_quarter` column (CONTRACTS.md §2). Fresh
# collection is not organised on a company's fiscal calendar: aggregation
# assigns periods, and labelling them here would bake one company's calendar
# into another's data. The archived corpus extractor did label quarters, and
# its two-method fiscal handling (exact filed period bounds, falling back to
# month arithmetic off EDGAR's own fiscalYearEnd) is the right implementation
# for whoever builds that stage - it lives in
# archive/historical-harness/scripts/extract_reviews.py, not here.
#
# One consequence worth knowing: score_reviews.py exits with "has no
# fiscal_quarter column, so --window cannot be honoured" if it is ever run
# with a restricting window. That is a loud failure rather than a silent
# mislabelling, which is the right way round.

# Live-collection columns the contract does not name. Appended AFTER the
# contract columns so a reader that slices on the contract is unaffected, and
# carried per review rather than only per batch because reviews.csv is merged
# across batches and a per-file route stamp would be a lie the moment two
# routes meet.
PROVENANCE_FIELDS = ("collection_route", "collected_at", "review_id_scheme",
                     "review_date_precision", "vine_voice", "sort_requested",
                     "sort_verified")

ALL_FIELDS = REVIEW_FIELDS + PROVENANCE_FIELDS

# Amazon's own review id, e.g. RHBCYOKA8617N.
AMAZON_RID = re.compile(r"^R[A-Z0-9]{8,}$")

# Epoch-ms bounds for a timestamp that could belong to an Amazon review.
# Inherited from the corpus extractor: a row outside them cannot be honestly
# assigned to a quarter, so it is dropped and counted rather than clamped.
# The upper bound is "now plus a day" here rather than a constant, because this
# collector runs forward in time and a fixed ceiling would start rejecting real
# reviews on a date nobody would connect to this line.
TS_MIN_MS = 788918400000  # 1995-01-01

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

SORTS = {
    "recent": {
        "param": "recent",
        "label_contains": ("most recent",),
        "produces_series": True,
        "note": "login-only; the only sort that yields a time series",
    },
    "helpful": {
        "param": "helpful",
        "label_contains": ("top review", "most helpful"),
        "produces_series": False,
        "note": "Amazon's ranking; a series built on it partly measures the "
                "ranking algorithm (CORE-PROMPT §4)",
    },
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_max_ms():
    return int(time.time() * 1000) + 86400000


def die(msg, code=1):
    sys.stderr.write("collect_reviews: %s\n" % msg)
    sys.exit(code)


def warn(msg):
    print("!! %s" % msg)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def append_jsonl(path, obj):
    """Append-only (CORE-PROMPT §1.5). Never rewrites, never truncates."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError as e:
                die("%s line %d is not JSON: %s" % (path, n, e))
    return out


# --------------------------------------------------------------------------
# The collection plan: the declared cadence (CORE-PROMPT §4)
# --------------------------------------------------------------------------
#
# "Repeated collection of the same product at a fixed cadence is what creates
# the time series. The cadence is declared and kept; changing it changes the
# instrument."
#
# So the cadence lives in a file, with the date it was declared, and every
# batch records the cadence it was collected under plus how far off schedule it
# actually ran. A cadence that exists only in whenever-someone-ran-it is not a
# cadence, and a series whose spacing is unknown cannot be read as a series.
# Changing it appends to data/cadence-log.jsonl rather than overwriting, so a
# break in the instrument is visible after the fact.

PLAN_PATH = "collection-plan.json"
CADENCE_LOG = "cadence-log.jsonl"
GROUPS_DIR = "groups"


def slugify(name):
    """A company slug: 'iRobot Corporation' -> 'irobot-corporation'."""
    out = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return out or "unnamed"


def load_group(root, group_id):
    """`groups/<group_id>.json` (CONTRACTS.md §1). The peer-group finder owns it.

    Read, never written here. A product enters a series because the group file
    says the company sells it, and a collector that could add one would be
    choosing the peer group by what was easy to collect.
    """
    path = os.path.join(root, GROUPS_DIR, "%s.json" % group_id)
    g = read_json(path)
    if g is None:
        die("no %s.\n"
            "The peer group is declared before any review is collected\n"
            "(CORE-PROMPT §2, CONTRACTS.md §1). Stage G produces this file; this\n"
            "stage only reads it." % path)
    if not g.get("members"):
        die("%s has no members" % path)
    for m in g["members"]:
        m["company_slug"] = m.get("company_slug") or slugify(m.get("company"))
    g["_path"] = path
    return g


def group_member(group, company=None, asin=None):
    """The member this batch belongs to, by slug or by the ASIN it tracks."""
    if company:
        c = company.lower()
        for m in group["members"]:
            if m["company_slug"] == c:
                return m, None
        die("no company %r in %s; have %s"
            % (company, group["_path"],
               ", ".join(sorted(m["company_slug"] for m in group["members"]))))
    hits = [(m, pr) for m in group["members"] for pr in (m.get("products") or [])
            if (pr.get("asin") or "").upper() == (asin or "").upper()]
    if not hits:
        die("%s is not tracked by any member of %s.\n"
            "Add it to the group file first - a product that enters a series\n"
            "because it was easy to collect is not a tracked product."
            % (asin, group["_path"]))
    if len(hits) > 1:
        die("%s is tracked by %s; say which with --company"
            % (asin, ", ".join(sorted(m["company_slug"] for m, _ in hits))))
    return hits[0]


def member_product(member, asin):
    for pr in member.get("products") or []:
        if (pr.get("asin") or "").upper() == (asin or "").upper():
            return pr
    return None

CADENCES = {"daily": 1, "weekly": 7, "fortnightly": 14, "monthly": 28}


def plan_path(root):
    return os.path.join(root, PLAN_PATH)


def load_cadence(root, group_id, required=True):
    """The declared cadence for one group.

    CONTRACTS.md §4 makes stage C satisfied when "every tracked ASIN has a
    batch within the declared cadence", but §1's group file carries no cadence
    field - only `declared_at`. So the cadence lives here until that is
    settled, and stage C can read it from this file.
    """
    p = read_json(plan_path(root)) or {}
    c = (p.get("groups") or {}).get(group_id)
    if c is None and required:
        die("no cadence declared for group %r in %s.\n\n"
            "    %s plan --group %s --cadence weekly \\\n"
            "        --reason 'series begins <date>'\n\n"
            "CORE-PROMPT §4: the cadence is declared and kept. A series whose\n"
            "spacing is whenever-someone-ran-it is not a series."
            % (group_id, plan_path(root), prog(), group_id))
    return c


def prog():
    return "python3 .claude/skills/review-tone-signal/scripts/collect_reviews.py"


def plan_product(plan, product_id, ticker=None):
    """The tracked series for this product, under this company if one is named.

    Keyed on (ticker, product_id), not product_id alone. One ASIN can
    legitimately belong to two entries over time: a product first tracked under
    a brand and then re-attributed to the listed company that owns the brand is
    the same object in the world but a different series, and the first series
    is ENDED rather than edited (CORE-PROMPT §2). Keying on the ASIN alone made
    that correction impossible to record - the only way to fix a mis-attributed
    company was to erase the evidence that it had been wrong.
    """
    pid = product_id.upper()
    live = [pr for pr in plan["products"]
            if pr["product_id"].upper() == pid and not pr.get("ended_at")]
    if ticker:
        t = ticker.upper()
        for pr in plan["products"]:
            if pr["product_id"].upper() == pid and pr["ticker"].upper() == t:
                return pr
        return None
    if len(live) == 1:
        return live[0]
    if len(live) > 1:
        die("%s is tracked under %s. Say which with --ticker: they are separate\n"
            "series and a batch has to belong to one of them."
            % (pid, ", ".join(sorted(p["ticker"] for p in live))))
    for pr in plan["products"]:
        if pr["product_id"].upper() == pid:
            return pr
    return None


def cmd_plan(args):
    """Declare or change the collection cadence for one group."""
    root = args.root
    all_p = read_json(plan_path(root)) or {"groups": {}}
    cur = (all_p.get("groups") or {}).get(args.group)
    now = utcnow()

    if args.show or not args.cadence:
        if cur is None:
            die("no cadence declared for group %r" % args.group)
        print(json.dumps(cur, indent=2, sort_keys=True))
        log = [e for e in read_jsonl(os.path.join(root, CADENCE_LOG))
               if e.get("group_id") == args.group]
        if log:
            print("\ncadence history (%d entr%s):"
                  % (len(log), "y" if len(log) == 1 else "ies"))
            for e in log:
                print("  %s  %s  %s" % (e.get("at"), e.get("cadence"),
                                        e.get("reason") or ""))
        return 0

    if args.cadence not in CADENCES:
        die("unknown cadence %r; one of %s" % (args.cadence, "/".join(sorted(CADENCES))))
    if cur and cur.get("cadence") != args.cadence and not args.reason:
        die("changing the cadence from %r to %r needs --reason.\n"
            "CORE-PROMPT §4: changing the cadence changes the instrument, so the\n"
            "change is recorded in the series rather than applied quietly."
            % (cur["cadence"], args.cadence))

    all_p.setdefault("groups", {})[args.group] = {
        "group_id": args.group, "cadence": args.cadence,
        "cadence_days": CADENCES[args.cadence],
        "declared_at": (cur or {}).get("declared_at") or now,
        "last_changed_at": now, "collector_version": COLLECTOR_VERSION}
    append_jsonl(os.path.join(root, CADENCE_LOG),
                 {"at": now, "group_id": args.group, "cadence": args.cadence,
                  "cadence_days": CADENCES[args.cadence],
                  "previous": (cur or {}).get("cadence"),
                  "reason": args.reason or "initial declaration"})
    write_json(plan_path(root), all_p)
    c = all_p["groups"][args.group]
    print("wrote %s" % plan_path(root))
    print("group             %s" % args.group)
    print("cadence           %s (every %d days), declared %s"
          % (c["cadence"], c["cadence_days"], c["declared_at"]))
    g = read_json(os.path.join(root, GROUPS_DIR, "%s.json" % args.group))
    if g:
        n = sum(len(m.get("products") or []) for m in g.get("members") or [])
        print("group file        %d member(s), %d tracked product(s)"
              % (len(g.get("members") or []), n))
    else:
        warn("no groups/%s.json yet - stage G declares the peer group and its "
             "products; this stage only reads it." % args.group)
    return 0


# --------------------------------------------------------------------------
# Date parsing
# --------------------------------------------------------------------------

def parse_review_date(date_text):
    """'December 20, 2022' -> ('2022-12-20', epoch_ms).

    Amazon publishes a DATE, not a timestamp. The 2023 corpus carried
    second-level timestamps; this route does not, and pretending otherwise
    would put a false precision into a column downstream code reads as exact.
    So the epoch value is midnight UTC of that date and every row carries
    `timestamp_precision = day`. Two reviews on the same day are not ordered
    by this field and nothing may assume they are.
    """
    if not date_text:
        return None, None
    s = date_text.strip().replace(",", " ")
    s = re.sub(r"\s+", " ", s)
    m = re.match(r"^([A-Za-z]+) (\d{1,2}) (\d{4})$", s)          # December 20 2022
    if not m:
        m2 = re.match(r"^(\d{1,2}) ([A-Za-z]+) (\d{4})$", s)      # 20 December 2022
        if m2:
            mon, day, year = m2.group(2), m2.group(1), m2.group(3)
        else:
            return None, None
    else:
        mon, day, year = m.group(1), m.group(2), m.group(3)
    month = MONTHS.get(mon.capitalize())
    if not month:
        return None, None
    try:
        d = datetime(int(year), month, int(day), tzinfo=timezone.utc)
    except ValueError:
        return None, None
    return d.date().isoformat(), int(d.timestamp() * 1000)


# --------------------------------------------------------------------------
# The payload hash - the §1.3 check
# --------------------------------------------------------------------------

HASH_HEADER = ("capture_version", "marketplace", "product_id",
               "parent_asin", "page_number")
HASH_REVIEW = ("review_id", "star_rating", "review_date_text", "review_country",
               "title", "text", "verified_purchase", "helpful_votes",
               "variation", "profile_name")


def _canon_value(v):
    if v is None:
        return ""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float):
        # JS String(5) is "5" and String(4.5) is "4.5": an integral float must
        # render without the ".0" Python would add, or the hash disagrees on
        # every whole-star review.
        return repr(int(v)) if v == int(v) else repr(v)
    return str(v)


def _canon_parts(obj, fields):
    out = []
    for f in fields:
        vb = _canon_value(obj.get(f)).encode("utf-8")
        out.append(("%s=%d:" % (f, len(vb))).encode("utf-8"))
        out.append(vb)
    return out


def payload_hash(payload):
    """Recompute the browser's SHA-256 over the content fields.

    Must agree byte for byte with capture_reviews.js. Length-prefixed rather
    than JSON because JSON.stringify and json.dumps disagree about escaping
    lone surrogates and U+2028, and a hash scheme that disagrees across
    languages fails open on exactly the reviews with unusual characters.
    """
    parts = _canon_parts(payload, HASH_HEADER)
    for r in payload.get("reviews") or []:
        parts.extend(_canon_parts(r, HASH_REVIEW))
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Routes - one interface
# --------------------------------------------------------------------------

class Batch(object):
    """What a route returns. Normalised; not yet written."""

    def __init__(self, route, sort_observed, filters, reviews, claimed,
                 requested, marketplace="amazon.com", page_url=None,
                 blocked=False, block_reason=None, provider=None,
                 plan_tier=None, cost=None, raw_meta=None, requested_at=None,
                 review_id_scheme="amazon_review_id", date_precision="day",
                 parent_asin=None, parent_asin_via=None):
        self.route = route
        self.sort_observed = sort_observed
        self.filters = filters or {}
        self.reviews = reviews or []
        self.claimed = claimed or {}
        self.requested = requested
        self.marketplace = marketplace
        self.page_url = page_url
        self.blocked = blocked
        self.block_reason = block_reason
        self.provider = provider
        self.plan_tier = plan_tier
        self.cost = cost
        self.raw_meta = raw_meta or {}
        self.requested_at = requested_at or utcnow()
        self.review_id_scheme = review_id_scheme
        # Where Amazon actually served these reviews from, as the PAGE stated
        # it - never copied from the group file, so the two can be compared.
        self.parent_asin = parent_asin
        self.parent_asin_via = parent_asin_via
        # Amazon publishes a DATE, not a timestamp. The 2023 corpus carried
        # second-level timestamps; this route does not, and recording the
        # difference stops anything downstream assuming an ordering within a
        # day that the data cannot support.
        self.date_precision = date_precision

    @property
    def returned(self):
        return len(self.reviews)


class Route(object):
    """The pluggable interface. Two implementations, one contract.

    A route takes a product identifier and a sort order and returns a Batch:
    normalised review dicts plus everything §1.4 requires about how they were
    sampled. It does not write, does not dedupe and does not know about
    fiscal calendars - that is the collector's job, so both routes get
    identical treatment downstream and a route cannot quietly become special.
    """

    name = None
    route_kind = None          # "session" | "provider"

    def collect(self, product_id, sort, limit, filters=None):
        raise NotImplementedError

    def describe(self):
        return self.name


# ---- Route A: the user's logged-in session -------------------------------

class SessionRoute(Route):
    """Route A. The user's own logged-in Amazon session.

    The one route that can sort by most recent, so the one route that produces
    a series rather than a ranking (CORE-PROMPT §3).

    It does not drive the browser. The browser is driven by the assistant, one
    page at a time, paced like reading - Amazon's terms prohibit automated bulk
    access and THE ACCOUNT CARRYING THE RISK IS THE USER'S OWN. What this class
    does is ingest the resulting capture payload: verify its hash, normalise its
    rows, and hand them on in the same shape Route B produces.

    The split is deliberate. A route that could page by itself would be a
    scraper with a polite comment on top, and the comment would lose.
    """

    name = "session"
    route_kind = "session"

    def __init__(self, capture_path):
        self.capture_path = capture_path

    def collect(self, product_id, sort, limit, filters=None):
        payload = read_json(self.capture_path)
        if payload is None:
            die("no capture at %s" % self.capture_path)
        if isinstance(payload, list):
            die("%s holds a list. One capture file is one page; pass them one at "
                "a time so each gets its own batch record." % self.capture_path)
        v = str(payload.get("capture_version") or "")
        if v == "1":
            die("capture_version 1 from %s. Version 1 payloads carry no\n"
                "parent_asin, and Amazon serves reviews at the PARENT asin,\n"
                "pooled across every variant. A version 1 capture cannot say\n"
                "which population its reviews are of, and guessing the parent\n"
                "from the child would be inventing the provenance.\n"
                "Re-capture the page with the current extractor."
                % self.capture_path)
        if v != "2":
            die("capture_version %r from %s; this collector speaks version 2"
                % (v, self.capture_path))

        got_pid = (payload.get("product_id") or "").upper()
        if product_id and got_pid and got_pid != product_id.upper():
            die("capture is for %s but --product says %s. Writing it would file "
                "one product's reviews under another." % (got_pid, product_id))

        claimed = payload.get("claimed") or {}
        obs = payload.get("sort_order_observed") or {}

        if payload.get("blocked"):
            return Batch(route="session", sort_observed=None,
                         parent_asin=payload.get("parent_asin"),
                         parent_asin_via=payload.get("parent_asin_via"),
                         filters=payload.get("filters"), reviews=[],
                         claimed=claimed, requested=limit,
                         marketplace=payload.get("marketplace") or "amazon.com",
                         page_url=payload.get("page_url"), blocked=True,
                         block_reason=payload.get("block_reason") or "blocked",
                         requested_at=payload.get("collected_at"),
                         raw_meta={"page_title": payload.get("page_title"),
                                   "extractor_sha256": extractor_sha256()})

        # §1.3, mechanically. The browser hashed the review text as it stood in
        # the DOM; if what reached disk hashes differently, something edited it
        # on the way and the batch is refused rather than reconciled.
        want = payload.get("payload_sha256")
        if not want:
            die("capture %s carries no payload_sha256.\n"
                "An unverified capture cannot be distinguished from text a model\n"
                "retyped, and CORE-PROMPT §1.3 makes that unrecoverable. Re-run\n"
                "the extractor (`%s extractor`) and save what it returns."
                % (self.capture_path, prog()))
        # Name the likely cause before the generic one. A header field the
        # extractor hashed but did not emit at the top level makes EVERY
        # capture fail, and "the text was edited" is then exactly the wrong
        # thing to go looking for.
        absent = [f for f in HASH_HEADER if payload.get(f) in (None, "")]
        got = payload_hash(payload)
        if got != want and absent:
            die("CAPTURE HASH MISMATCH in %s, and the payload is missing the\n"
                "hashed header field(s) %s at its top level.\n"
                "That alone explains the mismatch: the extractor hashed a value\n"
                "the verifier cannot see, so it hashed an empty string instead.\n"
                "This is an extractor bug, NOT evidence that the review text was\n"
                "altered. Fix %s to emit those fields alongside the reviews and\n"
                "re-capture."
                % (self.capture_path, ", ".join(sorted(absent)),
                   os.path.basename(EXTRACTOR_JS)))
        if got != want:
            die("CAPTURE HASH MISMATCH in %s\n"
                "  browser computed  %s\n"
                "  file hashes to    %s\n"
                "The review text on disk is not the text that was on the page.\n"
                "Refusing the batch. CORE-PROMPT §1.3: verbatim or it does not\n"
                "exist, and a model-written review in the corpus is\n"
                "unrecoverable. Re-capture the page; do not hand-edit the file."
                % (self.capture_path, want, got))

        rows, notes = normalise_capture(payload)
        return Batch(route="session",
                     parent_asin=payload.get("parent_asin"),
                     parent_asin_via=payload.get("parent_asin_via"),
                     sort_observed=obs.get("label") or obs.get("value"),
                     filters=payload.get("filters"), reviews=rows,
                     claimed=claimed, requested=limit,
                     marketplace=payload.get("marketplace") or "amazon.com",
                     page_url=payload.get("page_url"),
                     requested_at=payload.get("collected_at"),
                     raw_meta={"page_title": payload.get("page_title"),
                               "sort_observed_raw": obs,
                               "normalise_notes": notes,
                               "capture_sha256": want,
                               "extractor_sha256": extractor_sha256(),
                               "extractor_file": os.path.basename(EXTRACTOR_JS),
                               "capture_file": os.path.basename(self.capture_path)})


def normalise_capture(payload):
    """Capture rows -> the review dicts the collector writes. No invention.

    Every row that cannot be labelled honestly is dropped and counted. A review
    with no date cannot be put in a quarter; a review with no id cannot be
    deduped against a later collection. Both are reported rather than patched,
    because patching either is how a series acquires rows nobody can trace.
    """
    rows, notes = [], Counter()
    tmax = ts_max_ms()
    for i, r in enumerate(payload.get("reviews") or []):
        rid = r.get("review_id")
        scheme = "amazon_review_id"
        if not rid or not AMAZON_RID.match(str(rid)):
            # Fall back to the corpus extractor's scheme, over what we actually
            # have. Recorded per row: a series that silently mixed id schemes
            # would dedupe against itself wrongly for exactly the rows whose
            # provenance is weakest.
            notes["no_amazon_review_id"] += 1
            key = "%s|%s|%s|%s" % (r.get("profile_name") or "",
                                   payload.get("product_id") or "",
                                   r.get("review_date_text") or "",
                                   (r.get("title") or "")[:64])
            rid = "X" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:19]
            scheme = "fallback_sha1_profile_asin_date_title"

        date, ts = parse_review_date(r.get("review_date_text"))
        if date is None:
            # CONTRACTS.md §2: review_date is as Amazon displays it, never
            # inferred and never filled. A review whose date will not parse
            # has no date, and is dropped and counted rather than guessed at.
            notes["unparseable_date_dropped"] += 1
            continue
        if not (TS_MIN_MS <= ts <= tmax):
            notes["date_out_of_range_dropped"] += 1
            continue

        text = r.get("text") or ""
        title = r.get("title") or ""
        if not text.strip() and not title.strip():
            notes["empty_text_and_title_dropped"] += 1
            continue
        if not r.get("helpful_votes_stated"):
            # 0 is a real value and the Phase C degradation test reads this
            # field, so substituting it is counted rather than silent.
            notes["helpful_votes_defaulted_to_zero"] += 1

        rating = r.get("star_rating")
        if rating is None:
            notes["no_star_rating"] += 1

        rows.append({
            "review_id": str(rid),
            "asin": (payload.get("product_id") or "").upper(),
            "parent_asin": (payload.get("parent_asin") or "").upper(),
            "review_date": date,
            # Verbatim apart from NULs, which break CSV readers. The scorer
            # must see what was actually published, markup and all.
            "title": title.replace("\x00", ""),
            "text": text.replace("\x00", ""),
            "rating": "" if rating is None else float(rating),
            "verified_purchase": bool(r.get("verified_purchase")),
            "helpful_vote": int(r.get("helpful_votes") or 0),
            "review_country": r.get("review_country") or "",
            "vine_voice": bool(r.get("vine_voice")),
            "review_id_scheme": scheme,
            "position_on_page": i,
        })
    return rows, dict(notes)


# ---- Route B: named commercial providers ---------------------------------

class ProviderRoute(Route):
    """Route B base. A named provider, a recorded plan, a recorded cost.

    What this route buys is scale and the user's account staying out of it.
    What it costs, beyond money, is the sort order: providers advertise sort
    options that do not always arrive, so the observed sort is read back off
    the response and compared against what was asked for. A provider that
    silently served helpfulness order when asked for recency has produced a
    ranking sample, and §4 says that is a different instrument.

    No paid plan is bought here. Every provider below runs on its free tier,
    and a route that cannot do what is asked on a free tier says so and stops
    rather than prompting for a card.
    """

    route_kind = "provider"
    provider = None
    free_tier = None
    token_env = None
    signup_note = ""

    def __init__(self, token=None, plan_tier="free", timeout=90):
        self.token = token or (os.environ.get(self.token_env) if self.token_env else None)
        self.plan_tier = plan_tier
        self.timeout = timeout
        if not self.token:
            die("%s needs %s in the environment.\n"
                "  free tier: %s\n"
                "  %s\n"
                "No account is created here and no plan is bought (CORE-PROMPT §9):\n"
                "sign up yourself, export the token, and re-run."
                % (self.provider, self.token_env, self.free_tier, self.signup_note))

    # -- plumbing shared by all providers --

    def _http(self, url, method="GET", body=None, headers=None):
        import urllib.error
        import urllib.request
        hdrs = {"accept": "application/json", "user-agent":
                "review-tone-monitor/%s (research; contact via repo owner)"
                % COLLECTOR_VERSION}
        hdrs.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["content-type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.getcode(), r.read().decode("utf-8", "replace"), time.time() - t0
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), time.time() - t0
        except Exception as e:                                  # noqa: BLE001
            return None, "transport: %s" % e, time.time() - t0

    def sort_param(self, sort):
        raise NotImplementedError

    def collect(self, product_id, sort, limit, filters=None):
        raise NotImplementedError


class ApifyRoute(ProviderRoute):
    """Apify. $5/month of platform credit on the free plan.

    Uses the synchronous dataset endpoint so one call is one batch with one
    recorded cost. The actor id is an argument rather than a constant: Apify's
    Amazon-reviews actors come and go, their pricing differs, and a hardcoded
    one would quietly start billing differently.
    """

    name = "apify"
    provider = "apify"
    free_tier = "$5/month platform credit, no card"
    token_env = "APIFY_TOKEN"
    signup_note = "https://console.apify.com/account/integrations -> API token"
    default_actor = "junglee~amazon-reviews-scraper"

    def __init__(self, token=None, plan_tier="free", actor=None, timeout=180):
        ProviderRoute.__init__(self, token, plan_tier, timeout)
        self.actor = actor or self.default_actor

    def sort_param(self, sort):
        return {"recent": "recent", "helpful": "helpful"}[sort]

    def collect(self, product_id, sort, limit, filters=None):
        url = ("https://api.apify.com/v2/acts/%s/run-sync-get-dataset-items"
               "?token=%s" % (self.actor.replace("/", "~"), self.token))
        body = {"productUrls": [{"url": "https://www.amazon.com/dp/%s" % product_id}],
                "maxReviews": limit, "sort": self.sort_param(sort),
                "filterByRatings": [(filters or {}).get("star", "allStars")]}
        at = utcnow()
        code, text, secs = self._http(url, "POST", body)
        if code != 201 and code != 200:
            return Batch(route="apify", sort_observed=None,
                         filters=filters, reviews=[], claimed={}, requested=limit,
                         blocked=True, requested_at=at,
                         block_reason="apify_http_%s" % code,
                         provider="apify", plan_tier=self.plan_tier,
                         raw_meta={"body": text[:2000], "seconds": round(secs, 1)})
        try:
            items = json.loads(text)
        except ValueError:
            return Batch(route="apify", sort_observed=None,
                         filters=filters, reviews=[], claimed={}, requested=limit,
                         blocked=True, block_reason="apify_bad_json",
                         requested_at=at, provider="apify",
                         plan_tier=self.plan_tier, raw_meta={"body": text[:2000]})
        rows, claimed, obs = normalise_provider(items, product_id, "apify")
        return Batch(route="apify", sort_observed=obs,
                     filters=filters, reviews=rows, claimed=claimed,
                     requested=limit, requested_at=at, provider="apify",
                     plan_tier=self.plan_tier,
                     raw_meta={"actor": self.actor, "seconds": round(secs, 1),
                               "n_items": len(items)},
                     review_id_scheme="provider_review_id")


class OxylabsRoute(ProviderRoute):
    """Oxylabs E-Commerce Scraper API. 2,000 results on the trial.

    Oxylabs parses amazon_reviews into a documented structure, which is why
    its sort is readable back off the response at all.
    """

    name = "oxylabs"
    provider = "oxylabs"
    free_tier = "2,000 results, 7-day trial"
    token_env = "OXYLABS_AUTH"          # "user:pass"
    signup_note = "https://oxylabs.io/ -> E-Commerce Scraper API trial"

    def sort_param(self, sort):
        return {"recent": "recent", "helpful": "helpful"}[sort]

    def collect(self, product_id, sort, limit, filters=None):
        import base64
        auth = base64.b64encode(self.token.encode()).decode()
        body = {"source": "amazon_reviews", "query": product_id,
                "parse": True, "domain": "com",
                "context": [{"key": "sort_by", "value": self.sort_param(sort)}]}
        at = utcnow()
        code, text, secs = self._http("https://realtime.oxylabs.io/v1/queries",
                                      "POST", body,
                                      {"authorization": "Basic " + auth})
        if code != 200:
            return Batch(route="oxylabs", sort_observed=None,
                         filters=filters, reviews=[], claimed={}, requested=limit,
                         blocked=True, block_reason="oxylabs_http_%s" % code,
                         requested_at=at, provider="oxylabs",
                         plan_tier=self.plan_tier,
                         raw_meta={"body": text[:2000], "seconds": round(secs, 1)})
        obj = json.loads(text)
        results = obj.get("results") or []
        content = (results[0].get("content") if results else {}) or {}
        items = content.get("reviews") or []
        rows, claimed, obs = normalise_provider(items, product_id, "oxylabs")
        if content.get("ratings_count") and not claimed.get("ratings_total"):
            claimed["ratings_total"] = content["ratings_count"]
            claimed["ratings_source_text"] = "oxylabs content.ratings_count"
        if content.get("reviews_count") and not claimed.get("reviews_total"):
            claimed["reviews_total"] = content["reviews_count"]
            claimed["reviews_source_text"] = "oxylabs content.reviews_count"
        return Batch(route="oxylabs",
                     sort_observed=obs or content.get("sort_by"),
                     filters=filters, reviews=rows, claimed=claimed,
                     requested=limit, requested_at=at, provider="oxylabs",
                     plan_tier=self.plan_tier,
                     raw_meta={"seconds": round(secs, 1), "n_items": len(items)},
                     review_id_scheme="provider_review_id")


class RainforestRoute(ProviderRoute):
    """Rainforest API. 100 free credits on signup.

    Rainforest documents `sort_by=most_recent` and returns the parameters it
    actually applied in `request_parameters`, so the observed sort can be
    checked against the requested one rather than assumed.
    """

    name = "rainforest"
    provider = "rainforest"
    free_tier = "100 credits on signup"
    token_env = "RAINFOREST_API_KEY"
    signup_note = "https://www.rainforestapi.com/ -> API key"

    def sort_param(self, sort):
        return {"recent": "most_recent", "helpful": "most_helpful"}[sort]

    def collect(self, product_id, sort, limit, filters=None):
        import urllib.parse
        q = {"api_key": self.token, "type": "reviews", "amazon_domain": "amazon.com",
             "asin": product_id, "sort_by": self.sort_param(sort),
             "page": str((filters or {}).get("page_number", 1))}
        star = (filters or {}).get("star")
        if star and star not in ("all", "all_stars"):
            q["review_stars"] = star
        at = utcnow()
        code, text, secs = self._http("https://api.rainforestapi.com/request?" +
                                      urllib.parse.urlencode(q))
        if code != 200:
            return Batch(route="rainforest",
                         sort_observed=None, filters=filters, reviews=[],
                         claimed={}, requested=limit, blocked=True,
                         block_reason="rainforest_http_%s" % code,
                         requested_at=at, provider="rainforest",
                         plan_tier=self.plan_tier,
                         raw_meta={"body": text[:2000], "seconds": round(secs, 1)})
        obj = json.loads(text)
        items = obj.get("reviews") or []
        rows, claimed, obs = normalise_provider(items, product_id, "rainforest")
        summary = obj.get("summary") or {}
        if summary.get("total_reviews") and not claimed.get("reviews_total"):
            claimed["reviews_total"] = summary["total_reviews"]
            claimed["reviews_source_text"] = "rainforest summary.total_reviews"
        if summary.get("total_ratings") and not claimed.get("ratings_total"):
            claimed["ratings_total"] = summary["total_ratings"]
            claimed["ratings_source_text"] = "rainforest summary.total_ratings"
        rp = obj.get("request_parameters") or {}
        return Batch(route="rainforest",
                     sort_observed=obs or rp.get("sort_by"), filters=filters,
                     reviews=rows, claimed=claimed, requested=limit,
                     requested_at=at, provider="rainforest",
                     plan_tier=self.plan_tier,
                     raw_meta={"seconds": round(secs, 1), "n_items": len(items),
                               "request_parameters": rp,
                               "credits_used": obj.get("request_info", {}).get("credits_used")},
                     review_id_scheme="provider_review_id")


PROVIDERS = {"apify": ApifyRoute, "oxylabs": OxylabsRoute,
             "rainforest": RainforestRoute}


def normalise_provider(items, product_id, provider):
    """Provider rows -> the same review dicts Route A produces.

    Providers disagree about field names for everything, so the mapping is
    explicit and a row whose date or text cannot be found is dropped and
    counted rather than defaulted. There is no hash to verify here: the
    response came over HTTP into this process and the model never touched it,
    which is the one respect in which Route B has the stronger provenance.
    """
    rows = []
    claimed = {}
    sorts = Counter()
    tmax = ts_max_ms()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        rid = (it.get("id") or it.get("review_id") or it.get("reviewId") or
               it.get("reviewUrl") or "")
        m = re.search(r"(R[A-Z0-9]{8,})", str(rid))
        scheme = "provider_review_id"
        if m:
            rid = m.group(1)
            scheme = "amazon_review_id"
        date_raw = (it.get("date") or it.get("reviewedAt") or it.get("timestamp") or
                    it.get("reviewDate") or "")
        if isinstance(date_raw, dict):
            date_raw = date_raw.get("utc") or date_raw.get("raw") or ""
        date, ts = _provider_date(str(date_raw), tmax)
        if date is None:
            continue
        text = (it.get("text") or it.get("reviewDescription") or it.get("body") or
                it.get("content") or "")
        title = (it.get("title") or it.get("reviewTitle") or "")
        if not str(text).strip() and not str(title).strip():
            continue
        rating = it.get("rating") or it.get("ratingScore") or it.get("stars")
        try:
            rating = float(str(rating).split()[0])
        except (TypeError, ValueError, IndexError):
            rating = None
        if not rid:
            rid = "X" + hashlib.sha1(
                ("%s|%s|%s|%s" % (provider, product_id, date,
                                  str(title)[:64])).encode("utf-8")).hexdigest()[:19]
            scheme = "fallback_sha1_provider_asin_date_title"
        for k in ("sortBy", "sort_by", "sort"):
            if it.get(k):
                sorts[str(it[k])] += 1
        for k, dest in (("countRatings", "ratings_total"),
                        ("totalRatings", "ratings_total"),
                        ("reviewsCount", "reviews_total"),
                        ("totalReviews", "reviews_total")):
            if it.get(k) and not claimed.get(dest):
                claimed[dest] = int(it[k])
                claimed[dest.replace("_total", "_source_text")] = "%s item.%s" % (provider, k)
        rows.append({
            "review_id": str(rid),
            "asin": (it.get("asin") or product_id or "").upper(),
            "parent_asin": (it.get("parent_asin") or "").upper(),
            "review_date": date,
            "title": str(title).replace("\x00", ""),
            "text": str(text).replace("\x00", ""),
            "rating": "" if rating is None else rating,
            "verified_purchase": bool(it.get("verified") or
                                      it.get("verified_purchase") or
                                      it.get("isVerified")),
            "helpful_vote": int(it.get("helpful") or it.get("helpfulVotes") or
                                it.get("helpful_votes") or 0),
            "review_country": str(it.get("country") or it.get("reviewCountry") or ""),
            "vine_voice": bool(it.get("vine") or it.get("isVine")),
            "review_id_scheme": scheme,
            "position_on_page": i,
        })
    obs = sorts.most_common(1)[0][0] if sorts else None
    return rows, claimed, obs


def _provider_date(s, tmax):
    s = s.strip()
    if not s:
        return None, None
    m = re.search(r"on (.+)$", s)
    if m:
        d, ts = parse_review_date(m.group(1))
        if d:
            return d, ts
    d, ts = parse_review_date(s)
    if d:
        return d, ts
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          tzinfo=timezone.utc)
        except ValueError:
            return None, None
        ts = int(dt.timestamp() * 1000)
        if TS_MIN_MS <= ts <= tmax:
            return dt.date().isoformat(), ts
    return None, None


# --------------------------------------------------------------------------
# Coverage (CORE-PROMPT §4)
# --------------------------------------------------------------------------

def coverage(collected_total, claimed):
    """(ratio, claimed_total, basis). None rather than a guess.

    CONTRACTS.md §3 asks for `claimed_total` and `coverage_ratio`. It needs a
    third value to mean anything, because Amazon states two different totals
    and they are different populations:

        "8,672 global ratings"   ratings, INCLUDING star-only ratings with no
                                 text, which this pipeline can never sample
        "1,234 global reviews"   reviews with text - the population actually
                                 being sampled

    Dividing by the ratings total systematically understates coverage, and a
    ratio whose denominator is unlabelled is not comparable between two
    products, let alone between two companies in a peer group. On both products
    collected 2026-10-11 the page gave ONLY the ratings count, so this is the
    normal case and not an edge one.

    `basis` is therefore reported with the ratio: `reviews`, `ratings`, or
    `unavailable` when the page states neither - which is how §4's "record that
    fact explicitly rather than omitting the field" is satisfied.
    """
    rt = claimed.get("reviews_total")
    gt = claimed.get("ratings_total")
    if rt:
        return round(float(collected_total) / float(rt), 6), int(rt), "reviews"
    if gt:
        return round(float(collected_total) / float(gt), 6), int(gt), "ratings"
    return None, None, "unavailable"


# --------------------------------------------------------------------------
# Writing: append-only batch log, deduped reviews.csv
# --------------------------------------------------------------------------

def company_dir(outdir, group_id, slug):
    return os.path.join(outdir, group_id, slug)


def batches_path(outdir, group_id, slug):
    return os.path.join(company_dir(outdir, group_id, slug), "batches.jsonl")


def reviews_path(outdir, group_id, slug):
    return os.path.join(company_dir(outdir, group_id, slug), "reviews.csv")


def observations_path(outdir, group_id, slug):
    return os.path.join(company_dir(outdir, group_id, slug),
                        "review_observations.jsonl")


def load_reviews(outdir, group_id, slug):
    """Existing reviews.csv as {review_id: row}, preserving file order."""
    path = reviews_path(outdir, group_id, slug)
    if not os.path.exists(path):
        return OrderedDict()
    out = OrderedDict()
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["review_id"]] = r
    return out


def write_reviews(outdir, group_id, slug, rows_by_id):
    """Rewrite reviews.csv from the merged set, deterministically ordered.

    reviews.csv is a DERIVED file and is rewritten; the append-only files are
    batches.jsonl and review_observations.jsonl, which together reproduce it.
    That is the right way round: §1.5 protects the evidence, and a CSV that
    could only be appended to would have to carry duplicate ids for every
    re-collection, which is exactly what §4 says to dedupe away.
    """
    rows = sorted(rows_by_id.values(),
                  key=lambda r: (r["review_date"], r["review_id"]))
    path = reviews_path(outdir, group_id, slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(ALL_FIELDS), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)
    return len(rows)


def merge_batch(outdir, group_id, slug, batch, batch_id, sort_requested,
                sort_verified):
    """Dedupe by review identifier across collections (CORE-PROMPT §4).

    The same review appearing in two batches is ONE review, and the first
    observation is the canonical one. That matters more than it looks:
    `helpful_vote` grows between collections, so a later row differs from the
    earlier one. score_reviews.py treats one review_id keying two different
    rows as a hard failure (it would merge two reviews into one score), so a
    naive append would eventually kill the scorer on a product whose reviews
    are being read.

    Rather than overwrite, the later observation of a mutable field is appended
    to review_observations.jsonl. Nothing is lost - vote growth is real
    information the degradation test uses - and reviews.csv stays stable enough
    to hash and compare across runs.
    """
    existing = load_reviews(outdir, group_id, slug)
    new, dupes, changed = 0, 0, 0
    obs_log = []

    for r in batch.reviews:
        row = {
            "review_id": r["review_id"],
            "asin": r["asin"],
            "parent_asin": r.get("parent_asin") or (batch.parent_asin or ""),
            "marketplace": batch.marketplace,
            "review_date": r["review_date"],
            "rating": r["rating"],
            "title": r["title"],
            "text": r["text"],
            "verified_purchase": r["verified_purchase"],
            "helpful_vote": r["helpful_vote"],
            "batch_id": batch_id,
            "collection_route": batch.route,
            "collected_at": batch.requested_at,
            "review_country": r["review_country"],
            "review_id_scheme": r["review_id_scheme"],
            "review_date_precision": batch.date_precision,
            "vine_voice": r["vine_voice"],
            "sort_requested": sort_requested,
            "sort_verified": "" if sort_verified is None else sort_verified,
        }

        prev = existing.get(r["review_id"])
        if prev is None:
            existing[r["review_id"]] = row
            new += 1
            continue

        dupes += 1
        diffs = {}
        for k in ("helpful_vote", "title", "text", "rating", "verified_purchase",
                  "review_date", "parent_asin"):
            a, b = str(prev.get(k, "")), str(row[k])
            if a != b:
                diffs[k] = {"first_seen": a, "now": b}
        if diffs:
            changed += 1
            # The text of an already-collected review changing is not a vote
            # count ticking up. Either the reviewer edited it or the extraction
            # changed, and both need a human to look - so it is called out
            # rather than left in a log nobody reads.
            if "title" in diffs or "text" in diffs:
                warn("review %s: TEXT CHANGED since first collection. The first "
                     "observation is kept; the new one is in "
                     "review_observations.jsonl. Verbatim means the version "
                     "actually collected, so neither is discarded and neither "
                     "is merged (CORE-PROMPT §1.3)." % r["review_id"])
        obs_log.append({"review_id": r["review_id"], "batch_id": batch_id,
                        "observed_at": batch.requested_at, "diffs": diffs,
                        "helpful_vote": row["helpful_vote"],
                        "position_on_page": r.get("position_on_page")})

    for o in obs_log:
        append_jsonl(observations_path(outdir, group_id, slug), o)

    total = write_reviews(outdir, group_id, slug, existing)
    return {"new": new, "duplicates": dupes, "changed": changed,
            "total_after": total}


def schedule_drift(outdir, group_id, slug, asin, cadence, collected_at):
    """How far off the declared cadence this batch actually ran.

    Recorded per batch so the spacing of the series is a property of the data,
    not of whoever remembered to run it. CORE-PROMPT §4: changing the cadence
    changes the instrument, and so does drifting off it.
    """
    base = {"cadence": cadence["cadence"], "cadence_days": cadence["cadence_days"],
            "cadence_declared_at": cadence["declared_at"]}
    prev = [b for b in read_jsonl(batches_path(outdir, group_id, slug))
            if (b.get("asin") or "").upper() == asin.upper() and not b.get("blocked")]
    if not prev:
        base.update({"previous_batch_at": None, "days_since_previous": None,
                     "days_off_cadence": None, "first_batch": True})
        return base
    last = max(b.get("collected_at") or "" for b in prev)
    base["previous_batch_at"] = last
    base["first_batch"] = False
    try:
        a = datetime.strptime(last[:19], "%Y-%m-%dT%H:%M:%S")
        b_ = datetime.strptime(collected_at[:19], "%Y-%m-%dT%H:%M:%S")
        gap = (b_ - a).total_seconds() / 86400.0
    except (ValueError, TypeError):
        base.update({"days_since_previous": None, "days_off_cadence": None})
        return base
    base["days_since_previous"] = round(gap, 3)
    base["days_off_cadence"] = round(gap - cadence["cadence_days"], 3)
    return base


def record_batch(outdir, group_id, member, asin, batch, cadence, batch_id,
                 sort_requested, sort_verified, inversions, oldest, newest,
                 merge_stats, stage_note, declared_parent=None):
    """Write the batch's provenance. CORE-PROMPT §1.4: or it is not data.

    The keys CONTRACTS.md §3 names come first and are spelled exactly as it
    spells them. Everything after them is additive, and a reader that only
    knows §3 is unaffected by it.
    """
    slug = member["company_slug"]
    on_product = sum(1 for r in load_reviews(outdir, group_id, slug).values()
                     if (r.get("asin") or "").upper() == asin.upper())
    ratio_batch, claimed_total, basis = coverage(batch.returned, batch.claimed)
    ratio_series, _c, _b = coverage(on_product, batch.claimed)

    rec = OrderedDict([
        # --- CONTRACTS.md §3, in its order and with its spellings ---
        ("batch_id", batch_id),
        ("collected_at", batch.requested_at),
        ("route", batch.route),
        ("filters", batch.filters),
        ("asin", asin.upper()),
        ("parent_asin", batch.parent_asin),
        ("marketplace", batch.marketplace),
        ("requested", batch.requested),
        ("returned", batch.returned),

        ("claimed_total", claimed_total),
        ("claimed_total_basis", basis),
        ("coverage_ratio", ratio_batch),

        # There is deliberately no `sort` field (§3). A sort read back off the
        # page is unreadable - the current page has no sort control - and a
        # sort taken from the request is a claim about Amazon that is false.
        # `sort_verified` is derived from the collected dates, the only
        # evidence that cannot be mislabelled.
        ("sort_requested", sort_requested),
        ("sort_verified", sort_verified),
        ("date_order", {"monotonic_descending": sort_verified,
                        "inversions": inversions,
                        "oldest": oldest, "newest": newest}),
        ("sort_produces_series", bool(SORTS[sort_requested]["produces_series"])
                                 and sort_verified is True),

        ("blocked", bool(batch.blocked)),
        ("block_reason", batch.block_reason),

        # --- additive ---
        ("claimed_total_source_text", batch.claimed.get("reviews_source_text") or
                                      batch.claimed.get("ratings_source_text")),
        ("claimed_reviews", batch.claimed.get("reviews_total")),
        ("claimed_ratings", batch.claimed.get("ratings_total")),
        ("parent_asin_declared", declared_parent),
        ("parent_asin_via", batch.parent_asin_via),
        ("parent_asin_matches_declared",
         None if not (declared_parent and batch.parent_asin)
         else declared_parent.upper() == (batch.parent_asin or "").upper()),
        ("sort_order_observed", batch.sort_observed),
        ("coverage_ratio_series", ratio_series),
        ("reviews_on_product_after", on_product),
        ("group_id", group_id),
        ("company", member.get("company")),
        ("company_slug", slug),
        ("route_kind", "session" if batch.route == "session" else "provider"),
        ("provider", batch.provider),
        ("provider_plan", batch.plan_tier),
        ("provider_cost", batch.cost),
        ("review_id_scheme", batch.review_id_scheme),
        ("review_date_precision", batch.date_precision),
        ("requested_at", batch.requested_at),
        ("written_at", utcnow()),
        ("merge", merge_stats),
        ("schedule", schedule_drift(outdir, group_id, slug, asin, cadence,
                                    batch.requested_at)),
        ("page_url", batch.page_url),
        ("collector_version", COLLECTOR_VERSION),
        ("raw_meta", batch.raw_meta),
        ("note", stage_note),
    ])
    append_jsonl(batches_path(outdir, group_id, slug), rec)
    return rec


def update_manifest(outdir, group_id, member, cadence, rec):
    """Carry coverage into the manifest. Never overwritten wholesale."""
    path = os.path.join(company_dir(outdir, group_id, member["company_slug"]),
                        "manifest.json")
    man = read_json(path, {}) or {}
    man["group_id"] = group_id
    man["company"] = member.get("company")
    man["company_slug"] = member["company_slug"]
    live = man.get("live_collection") or {}
    live.update({
        "collector_version": COLLECTOR_VERSION,
        "cadence": cadence["cadence"],
        "cadence_days": cadence["cadence_days"],
        "cadence_declared_at": cadence["declared_at"],
        "last_batch_id": rec["batch_id"],
        "last_collected_at": rec["collected_at"],
        "routes_used": sorted(set((live.get("routes_used") or []) + [rec["route"]])),
        "sorts_requested": sorted(set((live.get("sorts_requested") or []) +
                                      [rec["sort_requested"]])),
        "any_sort_verified": bool(live.get("any_sort_verified")) or
                             rec["sort_verified"] is True,
        "review_id_schemes": sorted(set((live.get("review_id_schemes") or []) +
                                        [rec["review_id_scheme"]])),
        "review_date_precision": rec["review_date_precision"],
        "n_batches": len(read_jsonl(batches_path(outdir, group_id,
                                                 member["company_slug"]))),
    })
    man["live_collection"] = live
    man["coverage_ratio"] = rec["coverage_ratio_series"]
    man["coverage_method"] = ("collected/%s claimed (%s basis), route %s, sort %s"
                              % (rec["claimed_total"], rec["claimed_total_basis"],
                                 rec["route"], rec["sort_requested"]))
    man["source"] = "live Amazon collection (CORE-PROMPT §3), not the 2023 corpus"
    write_json(path, man)
    return path


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def extractor_sha256():
    """SHA-256 of capture_reviews.js as it stands in the repo.

    Recorded with every Route A batch, because the payload hash proves only
    that what reached disk is what the extractor SAW - not that the extractor
    which ran was this one. The chain of custody is:

        this file  ->  operator pastes it into the browser  ->  page DOM
                   ->  browser hashes the text  ->  disk  ->  `capture` verifies

    Every link after execution is machine-checked. The PASTE is not: a mistyped
    extractor would read the wrong element and hash the wrong text faithfully.
    So the version that was supposed to run is pinned here, and the check on
    the paste is that the extracted text is spot-read against the page. That is
    a weaker guarantee than the payload hash and is recorded as such rather
    than described as if it were the same thing.
    """
    with open(EXTRACTOR_JS, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def cmd_extractor(args):
    with open(EXTRACTOR_JS, encoding="utf-8") as f:
        src = f.read()
    if args.bare:
        sys.stdout.write(src)
        return 0
    print("# Route A extractor - %s" % EXTRACTOR_JS)
    print("#")
    print("# Run this in the built-in browser, on a review page the USER has")
    print("# already signed into, one page at a time. Save what it returns to a")
    print("# file and feed it to:  %s capture --capture <file> ..." % prog())
    print("#")
    print("# It hashes the review text in the browser. If anything edits the")
    print("# text on its way to disk, `capture` refuses the batch (§1.3).")
    print("# " + "-" * 68)
    sys.stdout.write(src)
    return 0


def resolve_sort(args, batch):
    """Refuse a batch whose sort is not the one that was asked for.

    §4: a helpfulness-ranked series partly measures Amazon's ranking algorithm.
    A URL carrying sortBy=recent that silently served top-reviews order would
    otherwise enter the series labelled `recent`, and nothing downstream could
    ever tell. So the label the PAGE reported is checked against the request,
    and a disagreement stops the batch instead of being written.
    """
    want = SORTS[args.sort]
    obs = batch.sort_observed
    if obs is None:
        return ("%s (requested; page did not state its sort)" % args.sort), False
    low = str(obs).lower()
    if any(s in low for s in want["label_contains"]) or low == want["param"]:
        return "%s (confirmed: %r)" % (args.sort, obs), True
    other = [k for k, v in SORTS.items()
             if any(s in low for s in v["label_contains"]) or low == v["param"]]
    if other:
        die("SORT ORDER MISMATCH.\n"
            "  requested  %s\n"
            "  page says  %r  -> that is %s order\n"
            "Refusing the batch. CORE-PROMPT §4: a series built from a\n"
            "helpfulness-ranked sample partly measures Amazon's ranking\n"
            "algorithm, so a batch mislabelled as recency-sorted would corrupt\n"
            "the series in a way nothing downstream could detect.\n"
            "Re-request the page with the right sort, or collect it as --sort %s\n"
            "and accept that it is not a time series."
            % (args.sort, obs, other[0], other[0]))
    warn("page reports sort %r, which matches neither %s; recorded verbatim"
         % (obs, "/".join(sorted(SORTS))))
    return "%s (requested; page said %r)" % (args.sort, obs), False


def check_recency(rows):
    """Is this batch actually in date order? Measured, not read off a label.

    Written because the label turned out not to exist. On 2026-10-11 the
    amazon.com review surface served to a signed-in account carried no sort
    control at all, ignored `sortBy=recent` in the URL, and returned eight
    reviews in an order that was recency-WEIGHTED but not recency-sorted - one
    product's page ran 2022, 2025, 2024, 2026, 2026, 2026, 2023, 2023.

    A label-based check passes that page silently, because there is no label to
    disagree with. The dates themselves cannot be talked round: if a batch
    requested as `recent` comes back out of order, it is not a recency sample,
    and §4 says a series built on it measures Amazon's ranking rather than
    sentiment. So the order is tested against the only evidence that cannot be
    faked - the review dates that were collected.

    Returns (monotonic, inversions, oldest, newest).
    """
    dates = [r["review_date"] for r in rows if r.get("review_date")]
    if len(dates) < 2:
        return True, 0, (dates[0] if dates else None), (dates[0] if dates else None)
    inversions = sum(1 for a, b in zip(dates, dates[1:]) if b > a)
    return inversions == 0, inversions, min(dates), max(dates)


def cmd_collect(args, route, stage_note):
    root, outdir = args.root, args.outdir
    group = load_group(root, args.group)
    cadence = load_cadence(root, args.group)
    asin = args.asin.upper()
    member, product = group_member(group, args.company, asin)
    if product is None:
        product = member_product(member, asin)
    if product is None:
        die("%s is not listed under %s in %s"
            % (asin, member["company_slug"], group["_path"]))
    slug = member["company_slug"]
    marketplace = product.get("marketplace") or "amazon.com"

    batch = route.collect(asin, args.sort, args.limit,
                          {"star": args.star, "page_number": args.page})
    batch_id = "b-" + hashlib.sha1(
        ("%s|%s|%s|%s" % (slug, asin, batch.requested_at,
                          batch.route)).encode()).hexdigest()[:10]

    declared_parent = (product.get("parent_asin") or "").upper() or None
    if declared_parent is None:
        die("%s in %s carries no parent_asin.\n"
            "Amazon serves reviews at the PARENT asin, pooled across every\n"
            "variant, so without it this batch cannot say which population its\n"
            "reviews are of - and dedup by review_id will not catch the\n"
            "mislabelling, because those are genuine reviews of a wider\n"
            "product. §1 requires parent_asin per product." % (asin, group["_path"]))

    print("group / company    %s / %s" % (args.group, slug))
    print("route              %s" % batch.route)
    print("product            %s  (%s)" % (asin, product.get("name") or ""))
    print("parent asin        %s declared" % declared_parent)
    print("marketplace        %s" % batch.marketplace)
    if batch.marketplace != marketplace:
        warn("the group file says %s but the batch came from %s; recorded as "
             "collected, not as requested" % (marketplace, batch.marketplace))
    print("requested at       %s" % batch.requested_at)

    # The declared parent is CHECKED against the one the page served, never
    # trusted. Amazon re-parents variation families over time, so a group file
    # that was right when it was written can stop being right with no id
    # anywhere changing - and the series would then change population
    # underneath whoever is reading it.
    served = (batch.parent_asin or "").upper() or None
    if not batch.blocked:
        if served is None:
            warn("the page did not state a parent asin (%s), so the declared "
                 "parent %s could not be checked. The batch is recorded with "
                 "parent_asin_matches_declared = null; treat the population as "
                 "unconfirmed." % (batch.parent_asin_via or "not found",
                                   declared_parent))
        elif served != declared_parent:
            die("PARENT ASIN MISMATCH for %s\n"
                "  group file declares  %s\n"
                "  the page served      %s  (via %s)\n\n"
                "Refusing the batch. Amazon serves reviews at the parent, so\n"
                "these reviews are of a different population than the group\n"
                "file says this product belongs to - and nothing downstream\n"
                "could detect it, because they are genuine reviews with genuine\n"
                "ids. Amazon re-parents variation families over time, so the\n"
                "likeliest cause is that the group file is now stale rather\n"
                "than that it was wrong. Re-resolve the parent in stage G; do\n"
                "not edit it to match, because the peer group may no longer\n"
                "hold if two members have converged on one parent."
                % (asin, declared_parent, served, batch.parent_asin_via))
        else:
            print("parent asin        %s confirmed by the page (via %s)"
                  % (served, batch.parent_asin_via))
        if served and served != asin.upper():
            print("                   -> these are the PARENT's reviews, pooled "
                  "across variants;")
            print("                      this is variation-family tone, not %s's "
                  "alone" % asin.upper())

    if batch.blocked:
        # CORE-PROMPT §9 / CONTRACTS.md §5: a block is a finding to report, not
        # an obstacle to route around. It is written to the append-only log
        # with the same provenance as a successful batch, because "we were
        # blocked on this date" is evidence about the instrument.
        rec = record_batch(outdir, args.group, member, asin, batch, cadence,
                           batch_id, args.sort, None, 0, None, None,
                           {"new": 0, "duplicates": 0, "changed": 0,
                            "total_after": len(load_reviews(outdir, args.group, slug))},
                           stage_note, declared_parent)
        update_manifest(outdir, args.group, member, cadence, rec)
        print("\n!! BLOCKED: %s" % batch.block_reason)
        print("!! Recorded as batch %s and STOPPING." % batch_id)
        print("!! CORE-PROMPT §9: bot protection is never defeated, worked around")
        print("!! or solved. A block is a finding to report.")
        if batch.block_reason == "signin_required":
            print("!! The user signs in themselves, in the browser pane. The")
            print("!! assistant never types a password and never creates an account.")
        print("\nbatch log          %s" % batches_path(outdir, args.group, slug))
        return 2

    resolve_sort(args, batch)
    monotonic, inversions, oldest, newest = check_recency(batch.reviews)
    sort_verified = None
    if args.sort == "recent" and batch.reviews:
        sort_verified = monotonic
        print("date order         %s (%d inversion%s over %d dated review(s), %s .. %s)"
              % ("descending" if monotonic else "NOT descending", inversions,
                 "" if inversions == 1 else "s", len(batch.reviews), oldest, newest))
        if not monotonic:
            warn("RECENCY WAS REQUESTED AND NOT DELIVERED. The dates returned are "
                 "not in descending order, so this batch is a RANKED sample, not "
                 "a recency sample. CORE-PROMPT §4: a series built from a ranked "
                 "sample partly measures Amazon's ranking algorithm. Recorded "
                 "with sort_verified=false and sort_produces_series=false; do "
                 "not read it as a time series.")
            if args.require_recency:
                die("--require-recency was given and recency was not delivered. "
                    "Refusing to write a batch that would enter a series under a "
                    "sort order it does not have.")
    elif not SORTS[args.sort]["produces_series"]:
        warn("%s is not a recency sort. %s" % (args.sort, SORTS[args.sort]["note"]))

    stats = merge_batch(outdir, args.group, slug, batch, batch_id, args.sort,
                        sort_verified)
    rec = record_batch(outdir, args.group, member, asin, batch, cadence,
                       batch_id, args.sort, sort_verified, inversions, oldest,
                       newest, stats, stage_note, declared_parent)
    man = update_manifest(outdir, args.group, member, cadence, rec)

    print("\nrequested          %s" % rec["requested"])
    print("returned           %s" % rec["returned"])
    print("claimed_total      %s  (basis: %s, from %r)"
          % (rec["claimed_total"], rec["claimed_total_basis"],
             rec["claimed_total_source_text"]))
    if rec["coverage_ratio"] is None:
        warn("NO COVERAGE RATIO: the page stated no total, so collected-over-"
             "claimed cannot be computed. §4 requires that ratio beside every "
             "tone number, so this batch is on record as unable to supply it.")
    else:
        print("coverage_ratio     %.6f  (%.4f%% of %d, %s basis)"
              % (rec["coverage_ratio"], 100.0 * rec["coverage_ratio"],
                 rec["claimed_total"], rec["claimed_total_basis"]))
        print("coverage of series %.6f  (%d of %d)"
              % (rec["coverage_ratio_series"], rec["reviews_on_product_after"],
                 rec["claimed_total"]))
    print("\nnew                %d" % rec["merge"]["new"])
    print("already held       %d  (deduped by review identifier)"
          % rec["merge"]["duplicates"])
    if rec["merge"]["changed"]:
        print("re-observed        %d with a changed field (logged, first kept)"
              % rec["merge"]["changed"])
    print("reviews.csv        %d rows" % rec["merge"]["total_after"])
    sch = rec["schedule"]
    if sch["days_since_previous"] is not None:
        print("cadence            %s (%dd); %.1fd since last batch (%+.1fd off)"
              % (sch["cadence"], sch["cadence_days"], sch["days_since_previous"],
                 sch["days_off_cadence"]))
    else:
        print("cadence            %s (%dd), declared %s - FIRST batch, the series "
              "starts here" % (sch["cadence"], sch["cadence_days"],
                               sch["cadence_declared_at"]))
    print("\nbatch              %s -> %s"
          % (batch_id, batches_path(outdir, args.group, slug)))
    print("reviews            %s" % reviews_path(outdir, args.group, slug))
    print("manifest           %s" % man)
    print("\nNo tone number is computed here and none may be inferred from this "
          "output (CORE-PROMPT §1.2). Next: score_reviews.py.")
    return 0


def cmd_capture(args):
    route = SessionRoute(args.capture)
    return cmd_collect(args, route,
                       "Route A: read from the user's own logged-in session "
                       "through the built-in browser, one page, paced like "
                       "reading (CORE-PROMPT §3)")


def cmd_fetch(args):
    cls = PROVIDERS.get(args.provider)
    if cls is None:
        die("unknown provider %r; one of %s"
            % (args.provider, "/".join(sorted(PROVIDERS))))
    kw = {"plan_tier": args.plan}
    if args.provider == "apify" and args.actor:
        kw["actor"] = args.actor
    return cmd_collect(args, cls(**kw),
                       "Route B: %s on the %s tier" % (args.provider, args.plan))


def cmd_status(args):
    root, outdir = args.root, args.outdir
    group = load_group(root, args.group)
    cadence = load_cadence(root, args.group, required=False)
    print("group              %s  (%s)" % (args.group, group["_path"]))
    if cadence:
        print("cadence            %s (every %d days), declared %s"
              % (cadence["cadence"], cadence["cadence_days"], cadence["declared_at"]))
    else:
        warn("no cadence declared for this group; stage C cannot say whether a "
             "batch is on schedule")
    print("members            %d\n" % len(group["members"]))

    any_verified = False
    for m in group["members"]:
        slug = m["company_slug"]
        batches = read_jsonl(batches_path(outdir, args.group, slug))
        rows = load_reviews(outdir, args.group, slug)
        print("=== %s (%s) ===" % (m.get("company"), slug))
        if not batches:
            print("  no batches yet")
        for pr in m.get("products") or []:
            asin = (pr.get("asin") or "").upper()
            mine = [b for b in batches if (b.get("asin") or "").upper() == asin]
            ok = [b for b in mine if not b.get("blocked")]
            blocked = [b for b in mine if b.get("blocked")]
            n = sum(1 for r in rows.values()
                    if (r.get("asin") or "").upper() == asin)
            print("  %-12s %-30s %d batch%s (%d blocked), %d review(s)"
                  % (asin, (pr.get("name") or "")[:30], len(ok),
                     "" if len(ok) == 1 else "es", len(blocked), n))
            if ok:
                last = ok[-1]
                cov = last.get("coverage_ratio_series")
                print("    coverage         %s  of %s (%s basis)"
                      % ("%.6f" % cov if cov is not None else "UNAVAILABLE",
                         last.get("claimed_total"), last.get("claimed_total_basis")))
                print("    routes           %s"
                      % ", ".join(sorted({b["route"] for b in ok})))
                ver = {b.get("sort_verified") for b in ok}
                any_verified = any_verified or (True in ver)
                print("    sort             %s · verified: %s"
                      % (", ".join(sorted({str(b.get("sort_requested")) for b in ok})),
                         ", ".join(sorted(str(v) for v in ver))))
                dates = sorted(r["review_date"] for r in rows.values()
                               if (r.get("asin") or "").upper() == asin)
                if dates:
                    print("    review dates     %s .. %s" % (dates[0], dates[-1]))
            if blocked:
                print("    blocks           %s"
                      % ", ".join("%s@%s" % (b.get("block_reason"),
                                             (b.get("collected_at") or "")[:10])
                                  for b in blocked))
        print("")

    print("Coverage is collected-over-claimed (CORE-PROMPT §4). It travels with")
    print("every tone number: a tone mean over 0.1% of a product's reviews is not")
    print("the product's tone and must never be presented as if it were.")
    if not any_verified:
        print("")
        print("NO BATCH IN THIS GROUP HAS A VERIFIED RECENCY SORT. Stage C can be")
        print("satisfied and the data still not support a trend claim - those are")
        print("different conditions. Nothing here is yet a time series (§4, §8).")
    return 0


# --------------------------------------------------------------------------
# Self-test - offline, no network, no page
# --------------------------------------------------------------------------

_T = {"pass": 0, "fail": 0}


def check(label, ok, detail=""):
    _T["pass" if ok else "fail"] += 1
    print("  [%s] %s%s" % ("ok" if ok else "FAIL", label,
                           ("  <- " + detail) if detail and not ok else ""))


def cmd_selftest(args):
    import shutil
    import tempfile

    print("collect_reviews self-test (offline)\n")
    tmp = tempfile.mkdtemp(prefix="collect-selftest-")
    root = os.path.join(tmp, "proj")
    outdir = os.path.join(root, "data")
    try:
        _mkgroup(root)
        _mkcadence(root)

        print("1 · the §1.3 hash agrees with capture_reviews.js's scheme")
        pay = {"capture_version": "2", "marketplace": "amazon.com",
               "product_id": "B000TEST01", "parent_asin": "B000PARENT",
               "page_number": 1,
               "reviews": [{"review_id": "R1AAAAAAAAAA", "star_rating": 5.0,
                            "review_date_text": "December 20, 2022",
                            "review_country": "the United States",
                            "title": "Good", "text": "Line one\nLine two \u2028odd",
                            "verified_purchase": True, "helpful_votes": 3,
                            "variation": None, "profile_name": "A"}]}
        h1 = payload_hash(pay)
        check("hash is a sha256 hex digest", len(h1) == 64 and
              all(c in "0123456789abcdef" for c in h1))
        check("integral star renders as JS String() does (5 not 5.0)",
              b"star_rating=1:5" in b"".join(_canon_parts(pay["reviews"][0], HASH_REVIEW)))
        pay2 = json.loads(json.dumps(pay))
        pay2["reviews"][0]["text"] = pay2["reviews"][0]["text"].replace("Line one", "line one")
        check("one changed character changes the hash", payload_hash(pay2) != h1)
        pay3 = json.loads(json.dumps(pay))
        pay3["reviews"][0]["text"] += " "
        check("trailing whitespace changes the hash", payload_hash(pay3) != h1)
        pay4 = json.loads(json.dumps(pay))
        pay4["reviews"][0]["title"] = "Goo"
        pay4["reviews"][0]["text"] = "d" + pay4["reviews"][0]["text"]
        check("length prefixes stop field-boundary shifting", payload_hash(pay4) != h1)
        pay5 = json.loads(json.dumps(pay))
        pay5["page_title"] = "ignored"
        check("fields outside the content set do not affect the hash",
              payload_hash(pay5) == h1)

        print("\n2 · a mangled capture is refused, not reconciled")
        cap = dict(pay, blocked=False, block_reason=None,
                   collected_at="2026-10-12T12:00:00.000Z",
                   page_url="https://www.amazon.com/product-reviews/B000TEST01/",
                   claimed={"reviews_total": 1000, "ratings_total": 5000},
                   sort_order_observed={"label": "Most recent"},
                   parent_asin_via="page parentAsin",
                   filters={"star": "all_stars"}, returned=1, payload_sha256=h1)
        good = os.path.join(tmp, "good.json")
        write_json(good, cap)
        bad = os.path.join(tmp, "bad.json")
        t = json.loads(json.dumps(cap))
        t["reviews"][0]["text"] = "Line one\nLine two, polished a little."
        write_json(bad, t)
        check("edited review text fails the batch",
              _cap(tmp, root, bad, "B000TEST01") != 0)
        write_json(os.path.join(tmp, "nohash.json"), dict(cap, payload_sha256=None))
        check("an unhashed capture is refused",
              _cap(tmp, root, os.path.join(tmp, "nohash.json"), "B000TEST01") != 0)

        print("\n2b · a hashed field the payload does not expose is caught as such")
        miss = dict(cap)
        del miss["page_number"]
        write_json(os.path.join(tmp, "nohdr.json"), miss)
        check("a payload missing a hashed header field is refused",
              _cap(tmp, root, os.path.join(tmp, "nohdr.json"), "B000TEST01") != 0)
        src = open(EXTRACTOR_JS, encoding="utf-8").read()
        check("every hashed header field is emitted by the extractor",
              all(("%s:" % f) in src for f in HASH_HEADER))

        print("\n3 · dates are as displayed, never inferred or filled (§2)")
        d, ts = parse_review_date("December 20, 2022")
        check("US form parses", d == "2022-12-20", repr(d))
        check("day-first form parses",
              parse_review_date("20 December 2022")[0] == "2022-12-20")
        check("garbage is dropped, not guessed",
              parse_review_date("sometime last spring") == (None, None))
        check("impossible date is dropped",
              parse_review_date("February 31, 2022") == (None, None))

        print("\n4 · coverage ratio and its basis (§3, §4)")
        r, t_, b = coverage(10, {"reviews_total": 1000, "ratings_total": 8672})
        check("divides by the reviews total when stated", (r, t_, b) == (0.01, 1000, "reviews"))
        r, t_, b = coverage(10, {"ratings_total": 8672})
        check("falls back to ratings and SAYS it did", b == "ratings", b)
        check("the ratings fallback uses the ratings denominator", t_ == 8672)
        r, t_, b = coverage(10, {})
        check("no total -> no ratio and basis 'unavailable', not a guess",
              r is None and t_ is None and b == "unavailable")

        print("\n5 · dedup across collections keeps the first observation")
        b1 = _fakebatch([_rev("R1AAAAAAAAAA", "2022-12-20", 3, "t", "x"),
                         _rev("R2BBBBBBBBBB", "2022-12-21", 0, "u", "y")])
        s1 = merge_batch(outdir, "testgroup", "test-co", b1, "b-1", "recent", True)
        check("first batch is all new", (s1["new"], s1["duplicates"]) == (2, 0), str(s1))
        b2 = _fakebatch([_rev("R1AAAAAAAAAA", "2022-12-20", 99, "t", "x"),
                         _rev("R3CCCCCCCCCC", "2022-12-22", 1, "v", "z")])
        s2 = merge_batch(outdir, "testgroup", "test-co", b2, "b-2", "recent", True)
        check("the same review in two batches is one review",
              (s2["new"], s2["duplicates"], s2["total_after"]) == (1, 1, 3), str(s2))
        rows = load_reviews(outdir, "testgroup", "test-co")
        check("first-seen helpful_vote is kept in reviews.csv",
              rows["R1AAAAAAAAAA"]["helpful_vote"] == "3")
        obs = [o for o in read_jsonl(observations_path(outdir, "testgroup", "test-co"))
               if o["review_id"] == "R1AAAAAAAAAA" and o["diffs"]]
        check("vote growth is not lost, it is appended to the observations log",
              len(obs) == 1 and obs[0]["diffs"]["helpful_vote"]["now"] == "99")
        check("re-collection never duplicates a row score_reviews would collide on",
              len({r["review_id"] for r in rows.values()}) == len(rows))

        print("\n6 · reviews.csv satisfies CONTRACTS.md §2")
        cols = open(reviews_path(outdir, "testgroup", "test-co"),
                    encoding="utf-8").readline().strip().split(",")
        check("§2 columns present and in contract order",
              cols[:len(REVIEW_FIELDS)] == list(REVIEW_FIELDS), ",".join(cols))
        check("score_reviews' hard requirement (review_id/title/text) present",
              {"review_id", "title", "text"} <= set(cols))
        check("NO fiscal_quarter column (§2: aggregation assigns periods)",
              "fiscal_quarter" not in cols)
        check("parent_asin is a contract column, and it is the join key",
              "parent_asin" in cols[:len(REVIEW_FIELDS)])
        check("review_country is a contract column (/dp/ pools marketplaces)",
              "review_country" in cols[:len(REVIEW_FIELDS)])
        check("the parent is carried on every row, not just the batch",
              all(r["parent_asin"] == "B000PARENT" for r in rows.values()))
        check("batch_id joins a review to its batch record", "batch_id" in cols)
        check("route is per review, not per file", "collection_route" in cols)
        check("deterministic order (review_date, review_id)",
              [r["review_id"] for r in csv.DictReader(
                  open(reviews_path(outdir, "testgroup", "test-co"), encoding="utf-8"))]
              == ["R1AAAAAAAAAA", "R2BBBBBBBBBB", "R3CCCCCCCCCC"])

        print("\n7 · a block is recorded and stops collection (§9, §5)")
        write_json(os.path.join(tmp, "blk.json"),
                   {"capture_version": "2", "blocked": True,
                    "block_reason": "signin_required",
                    "parent_asin": "B000PARENT", "parent_asin_via": "page parentAsin",
                    "collected_at": "2026-10-12T12:00:00.000Z",
                    "marketplace": "amazon.com", "product_id": "B000TEST01",
                    "page_number": 1, "page_url": "https://www.amazon.com/ap/signin",
                    "claimed": {}, "returned": 0, "reviews": [],
                    "payload_sha256": None, "filters": {}})
        root2 = os.path.join(tmp, "proj2")
        _mkgroup(root2)
        _mkcadence(root2)
        rc = _cap(tmp, root2, os.path.join(tmp, "blk.json"), "B000TEST01")
        check("a blocked capture exits 2 (integrity stop, §4)", rc == 2, "exit %s" % rc)
        bl = read_jsonl(batches_path(os.path.join(root2, "data"), "testgroup", "test-co"))
        check("the block is on record as a batch with its reason",
              len(bl) == 1 and bl[0]["blocked"] and
              bl[0]["block_reason"] == "signin_required")
        check("a blocked batch still carries route and asin (§1.4)",
              bl[0]["route"] == "session" and bl[0]["asin"] == "B000TEST01")

        print("\n8 · recency is checked against the DATES, not a label")
        asc = [_rev("R1AAAAAAAAAA", "2022-12-20", 0, "a", "x"),
               _rev("R2BBBBBBBBBB", "2026-01-02", 0, "b", "y")]
        check("descending dates pass", check_recency(list(reversed(asc)))[0])
        check("ascending dates fail", not check_recency(asc)[0])
        mixed = [_rev("R1AAAAAAAAAA", "2022-12-20", 0, "a", "x"),
                 _rev("R2BBBBBBBBBB", "2025-10-24", 0, "b", "y"),
                 _rev("R3CCCCCCCCCC", "2024-09-08", 0, "c", "z")]
        ok_, inv, o_, n_ = check_recency(mixed)
        check("the real amazon.com order (2022, 2025, 2024) is caught",
              not ok_ and inv == 1, "inversions=%d" % inv)
        check("the span is reported for the batch record",
              (o_, n_) == ("2022-12-20", "2025-10-24"))
        check("a single review cannot be out of order", check_recency(asc[:1])[0])
        check("an empty batch is not called disordered", check_recency([])[0])
        try:
            resolve_sort(type("A", (), {"sort": "recent"}),
                         _fakebatch([], sort_observed="Top reviews"))
            check("a helpfulness page requested as recency is refused", False)
        except SystemExit:
            check("a helpfulness page requested as recency is refused", True)

        print("\n9 · §1.4 / §3 - every batch says how it was sampled")
        root3 = os.path.join(tmp, "proj3")
        _mkgroup(root3)
        _mkcadence(root3)
        rc = _cap(tmp, root3, good, "B000TEST01")
        check("a clean capture is accepted", rc == 0, "exit %s" % rc)
        rec = read_jsonl(batches_path(os.path.join(root3, "data"),
                                      "testgroup", "test-co"))[0]
        for k in ("batch_id", "collected_at", "route", "filters", "asin",
                  "parent_asin", "marketplace", "requested", "returned",
                  "claimed_total",
                  "claimed_total_basis", "coverage_ratio", "sort_requested",
                  "sort_verified", "date_order", "sort_produces_series",
                  "blocked", "block_reason"):
            check("§3 key %r present" % k, k in rec)
        check("no bare 'sort' field (§3: the page does not support the claim)",
              "sort" not in rec)
        check("sort_produces_series is false unless the dates verified it",
              rec["sort_produces_series"] is (rec["sort_verified"] is True))
        check("route is spelled 'session' for the logged-in path (§3)",
              rec["route"] == "session", rec["route"])
        check("coverage_ratio is returned/claimed_total",
              abs(rec["coverage_ratio"] - rec["returned"] / float(rec["claimed_total"]))
              < 1e-9)

        print("\n9b · the parent asin is checked against the page, not trusted")
        root4 = os.path.join(tmp, "proj4")
        _mkgroup(root4)
        _mkcadence(root4)
        wrong = json.loads(json.dumps(cap))
        wrong["parent_asin"] = "B000OTHER0"
        wrong["payload_sha256"] = payload_hash(wrong)
        write_json(os.path.join(tmp, "wrongparent.json"), wrong)
        rc = _cap(tmp, root4, os.path.join(tmp, "wrongparent.json"), "B000TEST01")
        check("a page serving a different parent than declared is REFUSED",
              rc != 0, "exit %s - the reviews would be of another population" % rc)
        v1 = json.loads(json.dumps(cap))
        v1["capture_version"] = "1"
        v1.pop("parent_asin", None)
        write_json(os.path.join(tmp, "v1.json"), v1)
        check("a version 1 capture (no parent_asin) is refused",
              _cap(tmp, root4, os.path.join(tmp, "v1.json"), "B000TEST01") != 0)
        check("parent_asin is inside the hashed header, so it cannot be edited",
              "parent_asin" in HASH_HEADER)
        noparent = {"group_id": "testgroup", "declared_at": "2026-10-12",
                    "seed": "f", "hash": "f",
                    "members": [{"company": "Test Co", "company_slug": "test-co",
                                 "products": [{"asin": "B000TEST01",
                                               "marketplace": "amazon.com"}]}]}
        root5 = os.path.join(tmp, "proj5")
        write_json(os.path.join(root5, GROUPS_DIR, "testgroup.json"), noparent)
        _mkcadence(root5)
        check("a product declared without a parent_asin cannot be collected",
              _cap(tmp, root5, good, "B000TEST01") != 0)

        print("\n10 · the group file is read, never written (§1)")
        rc = _cap(tmp, root3, good, "B0NOTINGROUP")
        check("an ASIN no member tracks cannot be collected", rc != 0, "exit %s" % rc)
        before = open(os.path.join(root3, "groups", "testgroup.json"),
                      encoding="utf-8").read()
        _cap(tmp, root3, good, "B000TEST01")
        check("the group file is unchanged by collection",
              open(os.path.join(root3, "groups", "testgroup.json"),
                   encoding="utf-8").read() == before)
        check("collecting a group with no cadence is refused",
              _cap(tmp, os.path.join(tmp, "nocad"), good, "B000TEST01") != 0)

        print("\n11 · provider routes refuse to invent credentials")
        for name, cls in sorted(PROVIDERS.items()):
            had = os.environ.pop(cls.token_env, None)
            try:
                try:
                    cls()
                    check("%s without %s refuses" % (name, cls.token_env), False)
                except SystemExit:
                    check("%s without %s refuses, naming the free tier"
                          % (name, cls.token_env), True)
            finally:
                if had is not None:
                    os.environ[cls.token_env] = had
        check("every provider declares a free tier",
              all(c.free_tier for c in PROVIDERS.values()))

        print("\n12 · this stage computes no tone (§1.2, §5)")
        import ast
        tree = ast.parse(open(os.path.abspath(__file__), encoding="utf-8").read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
        suspect = sorted(n for n in names
                         if re.search(r"tone|sentiment|polarity", n, re.I))
        check("no function, variable or argument computes a tone",
              not suspect, ", ".join(suspect))
        imported = {n.module for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) and n.module} | \
                   {a.name for n in ast.walk(tree)
                    if isinstance(n, ast.Import) for a in n.names}
        check("the scorer is not imported, so it cannot be called from here",
              "score_reviews" not in imported)
        check("the written review columns carry no tone field",
              not any(re.search(r"tone|sentiment", f, re.I) for f in ALL_FIELDS))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (_T["pass"], _T["fail"]))
    return 1 if _T["fail"] else 0


def _subcall(argv, cwd):
    import subprocess
    r = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    if os.environ.get("COLLECT_SELFTEST_VERBOSE"):
        sys.stdout.write(r.stdout.decode("utf-8", "replace"))
    return r.returncode


def _cap(tmp, root, capture, asin):
    return _subcall([sys.executable, os.path.abspath(__file__), "capture",
                     "--group", "testgroup", "--root", root,
                     "--outdir", os.path.join(root, "data"),
                     "--capture", capture, "--asin", asin], tmp)


def _rev(rid, date, votes, title, text):
    return {"review_id": rid, "asin": "B000TEST01",
            "parent_asin": "B000PARENT", "review_date": date,
            "title": title, "text": text, "rating": 5.0,
            "verified_purchase": True, "helpful_vote": votes,
            "review_country": "the United States", "vine_voice": False,
            "review_id_scheme": "amazon_review_id", "position_on_page": 0}


def _fakebatch(rows, sort_observed="Most recent"):
    return Batch(route="session", sort_observed=sort_observed, filters={},
                 reviews=rows, claimed={"reviews_total": 1000}, requested=10,
                 requested_at="2026-10-12T12:00:00Z",
                 parent_asin="B000PARENT", parent_asin_via="page parentAsin")


def _mkgroup(root):
    write_json(os.path.join(root, GROUPS_DIR, "testgroup.json"),
               {"group_id": "testgroup", "declared_at": "2026-10-12",
                "seed": "fixture", "hash": "fixture",
                "members": [{"company": "Test Co", "venue": "NASDAQ:TEST",
                             "brands": ["Test"],
                             "concentration": {"dominant_line": "fixtures",
                                               "share_of_revenue": 1.0,
                                               "evidence": "fixture",
                                               "source_url": "fixture"},
                             "products": [{"asin": "B000TEST01",
                                           "parent_asin": "B000PARENT",
                                           "marketplace": "amazon.com",
                                           "name": "fixture"}],
                             "reporting": {"calendar": "quarterly",
                                           "currency": "USD", "source": "SEC"}}]})


def _mkcadence(root):
    write_json(plan_path(root),
               {"groups": {"testgroup": {"group_id": "testgroup",
                                         "cadence": "weekly", "cadence_days": 7,
                                         "declared_at": "2026-10-12T00:00:00Z"}}})


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        prog="collect_reviews.py",
        description="Stage C: collect fresh Amazon reviews. Route A is the "
                    "user's logged-in session; Route B is a named provider. "
                    "Every batch records which, and its coverage ratio.")
    sub = p.add_subparsers(dest="cmd")

    def common(sp, collecting=True):
        # CONTRACTS.md §4: every stage is invoked as
        # `script --group <group_id> [--company <slug>]`, and no stage reads a
        # path it was not given.
        sp.add_argument("--group", required=True, metavar="GROUP_ID")
        sp.add_argument("--company", metavar="SLUG",
                        help="disambiguates when two members track one ASIN")
        sp.add_argument("--root", default=".",
                        help="project root holding groups/ and the cadence plan")
        sp.add_argument("--outdir", default="data")
        if collecting:
            sp.add_argument("--asin", required=True, metavar="ASIN")
            sp.add_argument("--sort", choices=sorted(SORTS), default="recent",
                            help="recent is the only sort that can yield a series")
            sp.add_argument("--star", default="all_stars",
                            help="filter combination; the 10-page cap applies "
                                 "PER combination, so a 1-5 fanout reaches ~5x "
                                 "a single pass")
            sp.add_argument("--page", type=int, default=1)
            sp.add_argument("--limit", type=int, default=10,
                            help="requested, recorded against returned")
            sp.add_argument("--require-recency", action="store_true",
                            help="refuse a batch requested as --sort recent "
                                 "whose dates are not actually descending")

    sp = sub.add_parser("plan", help="declare the collection cadence for a group")
    sp.add_argument("--group", required=True)
    sp.add_argument("--root", default=".")
    sp.add_argument("--cadence", choices=sorted(CADENCES))
    sp.add_argument("--reason", help="required to change a declared cadence")
    sp.add_argument("--show", action="store_true")

    sp = sub.add_parser("extractor", help="print the browser-side extractor")
    sp.add_argument("--bare", action="store_true", help="no header comment")

    sp = sub.add_parser("capture", help="Route A: ingest one verified capture")
    common(sp)
    sp.add_argument("--capture", required=True,
                    help="the payload the extractor returned")

    sp = sub.add_parser("fetch", help="Route B: call a named provider")
    common(sp)
    sp.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    sp.add_argument("--plan", default="free", help="recorded with the batch")
    sp.add_argument("--actor", help="apify actor id")

    sp = sub.add_parser("status", help="coverage and batch history per product")
    common(sp, collecting=False)

    sub.add_parser("self-test", help="offline verification; touches no page")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return 2
    return {"plan": cmd_plan, "extractor": cmd_extractor, "capture": cmd_capture,
            "fetch": cmd_fetch, "status": cmd_status,
            "self-test": cmd_selftest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
