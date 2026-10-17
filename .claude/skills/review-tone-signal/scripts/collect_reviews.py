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
    Route C   the BRAND's own review platform (Bazaarvoice today). The brand
              pays a vendor to host and display its reviews, and that vendor's
              display API serves them to every visitor's browser in the clear.
              No account, no key of ours, no cap: Offset paginates to
              exhaustion. Measured on Traeger 2026-10-15: 7.6 years, 97% text,
              newest review 4 days old, $0.

Every route writes the same contract and EVERY BATCH RECORDS WHICH ROUTE
PRODUCED IT (CORE-PROMPT §1.4). A tone number whose batch cannot say how it was
sampled is not a measurement.

Why Route C exists, in one number: Amazon caps at ~100 reviews PER FILTER
COMBINATION, which for a busy product is weeks of history, and that cap is
Amazon's rather than any provider's - no budget moves it (references/
live-collection.md). A brand platform has no such cap. The cost of the route is
that it sees only the brand's own site, which is a different channel from
Amazon and must never be pooled with it as though it were the same population.


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
#
# The first twelve are the original Amazon-shaped contract. The last four were
# added when the brand-platform route landed, APPENDED rather than interleaved
# so a reader slicing on the original twelve is unaffected:
#
#   route         which route served this review. Per REVIEW, not per file:
#                 reviews.csv is merged across batches, so a per-file route
#                 stamp becomes a lie the moment two routes meet on one
#                 product. Previously spelled `collection_route`; load_reviews
#                 back-fills that name so the nine files already on disk are
#                 not blanked by the rename.
#   client        the brand platform's deployment zone ("traegerservice").
#                 Empty on an Amazon route.
#   product_id    the brand platform's own product identifier ("TFB57GLE").
#                 Empty on an Amazon route, where `asin` carries it.
#   family        THE JOIN KEY, for every route. See below.
REVIEW_FIELDS = ("review_id", "asin", "parent_asin", "marketplace",
                 "review_country", "review_date", "rating", "title", "text",
                 "verified_purchase", "helpful_vote", "batch_id",
                 "route", "client", "product_id", "family")

# `family` is the ONE join key both routes populate, and it exists because the
# two routes pool reviews in exactly the same way under different names.
#
# Amazon serves reviews at the PARENT asin, pooled across every variant.
# Bazaarvoice serves them at a product FAMILY, pooled across every model in it.
# Measured on Traeger 2026-10-15: TFB57GLE (Pro 575) and TFB78GLE (Pro 780) are
# different grills at different prices and return IDENTICAL 1,215-review id
# sets. TFB89BLF and TFB65BLF likewise. Nine products resolved to seven
# distinct families.
#
# So a brand-route row keyed on the declared `product_id` claims a narrower
# population than it holds, and - exactly as on Amazon - dedup by review_id
# does NOT catch it, because those are genuine distinct reviews of a wider
# product. The failure is identical in shape; only the vocabulary differs.
#
# `family` therefore holds the parent_asin on an Amazon route and the verified
# family label on a brand route, so anything downstream has a single column to
# pool on and neither route is special.
#
# HOW THE FAMILY IS DETERMINED, and the two ways that are forbidden:
#   - by COMPARING RETURNED ID SETS between the declared siblings. Two products
#     serving the same ids are one family. This is the only method that reads
#     what the platform actually did.
#   - NEVER inferred from the product ids differing. TFB57GLE and TFB78GLE
#     differ and are one family; the id looks like a product and is not one.
#   - NEVER by summing TotalReviewCount across a catalog, which double-counts
#     a pooled family and overstated Traeger's unique count by 30%.

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
PROVENANCE_FIELDS = ("collected_at", "review_id_scheme",
                     "review_date_precision", "vine_voice", "sort_requested",
                     "sort_verified", "syndicated", "syndication_source",
                     "incentivized")

# reviews.csv called the route column `collection_route` before the brand route
# existed, and nine files on disk carry that spelling. CONTRACTS.md §3 spells
# the batch-record field `route`, so the review column is spelled `route` too
# rather than leaving one concept with two names. The old name is read back as
# the new one in load_reviews(): reviews.csv is DERIVED and rewritten on every
# collection, so without that mapping the first re-collection of an existing
# group would write an empty `route` for every review already held - silently,
# because DictWriter(extrasaction="ignore") drops the unknown key without a
# word. A rename that blanks a provenance column is worse than the rename it
# was trying to tidy up.
LEGACY_FIELD_NAMES = {"collection_route": "route"}

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
        # Each platform spells its own recency sort. `submissiontime:desc` is
        # Bazaarvoice's, and it is listed so resolve_sort recognises it as the
        # sort that was asked for instead of warning that it matches neither.
        # This only affects the LABEL check: `sort_verified` is still derived
        # from the collected dates, because a platform echoing a sort
        # parameter is a claim about the platform and the dates are the only
        # evidence that cannot be mislabelled.
        "label_contains": ("most recent", "submissiontime:desc"),
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


# Set by refusal_audit.py to the path of its log. When present, every refusal
# records the source line it fired from, in this process AND in any subprocess
# the suite spawns, which is how the audit sees refusals raised behind a
# `subprocess.run`. Unset in normal use, where this costs nothing.
REFUSAL_LOG_ENV = "COLLECT_REFUSAL_LOG"


def _record_refusal():
    path = os.environ.get(REFUSAL_LOG_ENV)
    if not path:
        return
    try:
        import traceback
        # The frame that called die(), i.e. the line the refusal is written on,
        # which is what refusal_audit.py parses out of this file.
        st = traceback.extract_stack()
        here = os.path.abspath(__file__)
        for fr in reversed(st):
            # Skip this helper AND die() itself, or every refusal records die()'s
            # own line - one distinct "site" for all 26, which reads as a working
            # audit finding nothing. The audit caught exactly that on its first run.
            if fr.name in ("_record_refusal", "die"):
                continue
            if os.path.abspath(fr.filename) == here:
                with open(path, "a", encoding="utf-8") as f:
                    f.write("%d\n" % fr.lineno)
                return
    except Exception:            # noqa: BLE001 - never let auditing break a run
        pass


def die(msg, code=1):
    _record_refusal()
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


def product_route(pr):
    """Which route a declared product is collected on.

    CONTRACTS.md §1 gives every product a `route`. It defaults to `amazon`
    because the fifteen products declared before the field existed have none,
    and silently treating one of those as a brand product would look for a
    `product_id` that is not there.
    """
    return (pr or {}).get("route") or "amazon"


def product_key(pr):
    """The identifier a product is TRACKED by, per route.

    An ASIN on the Amazon routes, the platform's own product id on a brand
    route. One function so that batch filtering, status and the collector
    cannot disagree about what identifies a product - which is how series
    coverage once read "0 of 10,000 with ten reviews on disk".
    """
    if product_route(pr) == "amazon":
        return (pr.get("asin") or "").upper()
    return str(pr.get("product_id") or "")


def group_member(group, company=None, key=None):
    """The member this batch belongs to, by slug or by the product it tracks.

    `key` is an ASIN on an Amazon product and a platform product id on a brand
    product; product_key() decides which, from the declaration rather than
    from the shape of the string.
    """
    if company:
        c = company.lower()
        for m in group["members"]:
            if m["company_slug"] == c:
                return m, None
        die("no company %r in %s; have %s"
            % (company, group["_path"],
               ", ".join(sorted(m["company_slug"] for m in group["members"]))))
    want = str(key or "")
    hits = [(m, pr) for m in group["members"] for pr in (m.get("products") or [])
            if product_key(pr) in (want, want.upper())]
    if not hits:
        die("%s is not tracked by any member of %s.\n"
            "Add it to the group file first - a product that enters a series\n"
            "because it was easy to collect is not a tracked product."
            % (key, group["_path"]))
    if len(hits) > 1:
        die("%s is tracked by %s; say which with --company"
            % (key, ", ".join(sorted(m["company_slug"] for m, _ in hits))))
    return hits[0]


def member_product(member, key):
    want = str(key or "")
    for pr in member.get("products") or []:
        if product_key(pr) in (want, want.upper()):
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

# Which KIND of instrument each route is. Three kinds, because they fail and
# cost in three different ways and a reading that pooled them would be pooling
# three instruments: `session` risks the user's own account, `provider` costs
# money per review, `brand_platform` is free but sees only the brand's own
# channel.
ROUTE_KINDS = {"session": "session",
               "apify": "provider",
               "oxylabs": "provider",
               "rainforest": "provider",
               "bazaarvoice": "brand_platform"}


def route_kind_of(route):
    kind = ROUTE_KINDS.get(route)
    if kind is None:
        die("route %r has no kind in ROUTE_KINDS.\n"
            "Every batch records what kind of instrument produced it (§1.4),\n"
            "and guessing the kind from the name is how the brand route would\n"
            "have been filed as a commercial provider. Add the route to\n"
            "ROUTE_KINDS rather than defaulting it." % route)
    return kind


class Batch(object):
    """What a route returns. Normalised; not yet written."""

    def __init__(self, route, sort_observed, filters, reviews, claimed,
                 requested, marketplace="amazon.com", page_url=None,
                 blocked=False, block_reason=None, provider=None,
                 plan_tier=None, cost=None, raw_meta=None, requested_at=None,
                 review_id_scheme="amazon_review_id", date_precision="day",
                 parent_asin=None, parent_asin_via=None, route_kind=None,
                 client=None, product_id=None):
        self.route = route
        # Looked up from the route name rather than computed at the write site.
        # record_batch used to derive it as `"session" if route == "session"
        # else "provider"`, which would silently have labelled the brand route
        # a commercial provider - a wrong provenance field, and §1.4 exists to
        # prevent exactly that. A route name absent from the registry raises
        # here rather than being guessed into the nearest existing kind.
        self.route_kind = route_kind or route_kind_of(route)
        # Brand-platform identity. Empty on the Amazon routes, where `asin`
        # and `parent_asin` carry it.
        self.client = client
        self.product_id = product_id
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
    """Apify. $5-6/month of platform credit on the free plan.

    Charged PER REVIEW, so the cost of a batch is known before it is run and is
    recorded with it. `junglee/amazon-reviews-scraper` is the default because
    its input schema documents a `sort` of `helpful|recent` - which is the
    question this route exists to answer - and because an actor whose input
    shape has to be guessed can burn the budget returning nothing.

    EVERY RUN CARRIES A HARD SPEND CAP. `maxTotalChargeUsd` is sent to Apify,
    which stops the run rather than billing past it. The budget is the user's
    and it is small; an actor that decided to return 10,000 reviews would
    otherwise spend the month in one call.
    """

    name = "apify"
    provider = "apify"
    free_tier = "$5/month platform credit, no card"
    token_env = "APIFY_TOKEN"
    signup_note = "https://console.apify.com/account/integrations -> API token"
    default_actor = "junglee~amazon-reviews-scraper"

    def __init__(self, token=None, plan_tier="free", actor=None, timeout=900,
                 max_charge_usd=1.0, price_per_review=None, max_pages=10,
                 date_from=None, date_to=None, min_charge_usd=None):
        ProviderRoute.__init__(self, token, plan_tier, timeout)
        self.actor = (actor or self.default_actor).replace("/", "~")
        self.max_charge_usd = max_charge_usd
        self.price_per_review = price_per_review
        self.max_pages = max_pages
        self.date_from = date_from
        self.date_to = date_to
        # junglee publishes minimalMaxTotalChargeUsd 0.5; others have none.
        self.min_charge_usd = (min_charge_usd if min_charge_usd is not None
                               else (0.5 if self.actor.startswith("junglee~") else None))

    # The collector speaks Amazon's own URL vocabulary for star filters
    # (`all_stars`, `five_star`), because that is what the site uses and what a
    # Route A batch records. Each provider has its own spelling, and mapping it
    # here keeps the filter comparable across routes instead of leaking one
    # vendor's enum into the contract.
    STARS = {"all_stars": "allStars", "five_star": "fiveStar",
             "four_star": "fourStar", "three_star": "threeStar",
             "two_star": "twoStar", "one_star": "oneStar",
             "positive": "positive", "critical": "critical"}

    def sort_param(self, sort):
        return {"recent": "recent", "helpful": "helpful"}[sort]

    def star_param(self, star):
        v = self.STARS.get(star or "all_stars")
        if v is None:
            die("apify does not accept star filter %r; one of %s"
                % (star, ", ".join(sorted(self.STARS))))
        return v

    # Apify actors share an API and share nothing else: each defines its own
    # input object. One adapter per actor, rather than one vendor's schema
    # hardcoded as if it were Apify's - which is what sent the first run into a
    # 400 and would send every new actor into another one.
    def _input_for(self, product_id, sort, limit, f):
        if self.actor.startswith("delicious_zebu~"):
            # Fans out over every star filter in one run. That matters for
            # DEPTH, not just count: Amazon caps pagination per filter
            # combination, so each star rating has its own ~10-page window, and
            # a rare rating reaches much further back in time than a common one
            # at the same page depth.
            return {"ASIN_or_URL": [product_id],
                    "country": "United States",
                    "max_reviews": limit,
                    "sort_reviews_by": ["recent"],
                    # Honours --star. This was hardcoded to the full list and
                    # ignored --star entirely, so a `one_star` request silently
                    # ran as `all_stars` and returned 61 reviews already held,
                    # for $0.396. A filter the caller asked for and the adapter
                    # dropped is worse than one the provider rejects: the
                    # provider's 400 costs nothing and says so.
                    "filter_by_ratings": [f.get("star") or "all_stars"],
                    "filter_by_verified_purchase_only": ["all_reviews"],
                    "filter_by_mediaType": ["all_contents"],
                    "variant_scope": "all_variants",
                    "unique_only": True,
                    "get_customers_say": False,
                    "include_personal_data": False,
                    "End_date": self.date_from or "1990-01-01"}
        if self.actor.startswith("memo23~"):
            # The only adapter with a real DATE RANGE. dateFrom/dateTo bound
            # the window directly, rather than paging from the newest review
            # backwards - which is what decides whether reaching 2024 costs
            # what is inside the window or everything newer than it.
            b = {"products": [product_id],
                 "marketplaces": ["com"],
                 "maxReviewsPerProduct": limit,
                 "sort": self.sort_param(sort),
                 "includeVariantReviews": True,
                 "includeProductSummary": True,
                 "verifiedOnly": False}
            if self.date_from:
                b["dateFrom"] = self.date_from
            if self.date_to:
                b["dateTo"] = self.date_to
            star = f.get("star")
            if star and star != "all_stars":
                b["filterByStars"] = [star.replace("_star", "").replace("_", "")]
            return b
        if self.actor.startswith("axesso_data~"):
            # Depth comes from maxPages here, not from a row count: Amazon
            # serves ~10 reviews a page and caps pagination, so pages are the
            # unit that actually bounds how far back a run reaches.
            return {"input": [{
                "asin": product_id,
                "domainCode": "com",
                "sortBy": self.sort_param(sort),
                "maxPages": self.max_pages,
                "filterByStar": f.get("star") or "all_stars",
                "reviewerType": "all_reviews",
                "formatType": "all_formats",
                "mediaType": "all_contents",
            }]}
        return {
            "productUrls": [{"url": "https://www.amazon.com/dp/%s" % product_id}],
            "maxReviews": limit,
            "sort": self.sort_param(sort),
            "filterByRatings": [self.star_param(f.get("star"))],
            # False means "do not restrict to one variant", i.e. take the
            # parent's pooled reviews - which is the population Amazon actually
            # serves and the one CONTRACTS.md §2 keys on.
            "reviewsUseProductVariantFilter": False,
            "includeGdprSensitive": False,
        }

    def collect(self, product_id, sort, limit, filters=None):
        f = filters or {}
        body = self._input_for(product_id, sort, limit, f)
        # Apify refuses a cap below the actor's own minimum (junglee's is
        # $0.50). Caught here rather than as a 400, so the message names the
        # actor's floor instead of the operator guessing.
        if self.min_charge_usd and self.max_charge_usd < self.min_charge_usd:
            die("--max-charge-usd %.2f is below %s's minimum of $%.2f, so the\n"
                "run would be refused. Raise the cap or choose a cheaper actor;\n"
                "do not assume the floor is what you will be billed - this actor\n"
                "charges per result and the cap is only a ceiling."
                % (self.max_charge_usd, self.actor, self.min_charge_usd))
        url = ("https://api.apify.com/v2/acts/%s/run-sync-get-dataset-items"
               "?token=%s&maxTotalChargeUsd=%s"
               % (self.actor, self.token, self.max_charge_usd))
        at = utcnow()
        code, text, secs = self._http(url, "POST", body)
        meta = {"actor": self.actor, "seconds": round(secs, 1),
                "max_total_charge_usd": self.max_charge_usd,
                "max_pages": self.max_pages,
                "date_from": self.date_from, "date_to": self.date_to,
                "input": {k: v for k, v in body.items() if k != "productUrls"}}

        if code not in (200, 201):
            return Batch(route="apify", sort_observed=None, filters=f, reviews=[],
                         claimed={}, requested=limit, blocked=True,
                         block_reason="apify_http_%s" % code, requested_at=at,
                         provider="apify", plan_tier=self.plan_tier,
                         raw_meta=dict(meta, body=text[:2000]))
        try:
            items = json.loads(text)
        except ValueError:
            return Batch(route="apify", sort_observed=None, filters=f, reviews=[],
                         claimed={}, requested=limit, blocked=True,
                         block_reason="apify_bad_json", requested_at=at,
                         provider="apify", plan_tier=self.plan_tier,
                         raw_meta=dict(meta, body=text[:2000]))

        rows, claimed, obs, par = normalise_provider(items, product_id, "apify")
        cost = (round(len(items) * self.price_per_review, 4)
                if self.price_per_review else None)
        meta["n_items"] = len(items)
        meta["price_per_review_usd"] = self.price_per_review
        return Batch(route="apify", sort_observed=obs, filters=f, reviews=rows,
                     claimed=claimed, requested=limit, requested_at=at,
                     provider="apify", plan_tier=self.plan_tier, cost=cost,
                     parent_asin=par, parent_asin_via="provider field",
                     raw_meta=meta, review_id_scheme="provider_review_id")


class ApifyDatasetRoute(ApifyRoute):
    """Re-read a dataset an earlier run already produced. Costs nothing.

    Apify charges per result produced, not per read, so a parsing mistake
    should never cost a second run - and on this budget that is the difference
    between a fixable bug and a spent month. The first delicious_zebu run
    returned 100 good reviews that the normaliser dropped because the actor
    capitalises its field names; this re-read them for free.

    Recorded as the same route and provider as the run that produced them,
    with the dataset id, because the reviews ARE that batch - re-parsed, not
    re-collected.
    """

    def __init__(self, dataset_id, **kw):
        ApifyRoute.__init__(self, **kw)
        self.dataset_id = dataset_id

    def collect(self, product_id, sort, limit, filters=None):
        at = utcnow()
        code, text, secs = self._http(
            "https://api.apify.com/v2/datasets/%s/items?token=%s"
            % (self.dataset_id, self.token))
        meta = {"actor": self.actor, "dataset_id": self.dataset_id,
                "seconds": round(secs, 1), "reparse_of_stored_dataset": True,
                "cost_note": "no actor run; reading a stored dataset is free"}
        if code != 200:
            return Batch(route="apify", sort_observed=None, filters=filters or {},
                         reviews=[], claimed={}, requested=limit, blocked=True,
                         block_reason="apify_dataset_http_%s" % code,
                         requested_at=at, provider="apify",
                         plan_tier=self.plan_tier, raw_meta=dict(meta, body=text[:800]))
        items = json.loads(text)
        rows, claimed, obs, par = normalise_provider(items, product_id, "apify")
        meta["n_items"] = len(items)
        return Batch(route="apify", sort_observed=obs, filters=filters or {},
                     reviews=rows, claimed=claimed, requested=limit,
                     requested_at=at, provider="apify", plan_tier=self.plan_tier,
                     cost=0.0, parent_asin=par, parent_asin_via="provider field",
                     raw_meta=meta, review_id_scheme="provider_review_id")


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
        rows, claimed, obs, _p = normalise_provider(items, product_id, "oxylabs")
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
        rows, claimed, obs, _p = normalise_provider(items, product_id, "rainforest")
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


# ---- Route C: the brand's own review platform ----------------------------

# Bazaarvoice ships this key in every scout file it serves. It is
# Bazaarvoice's own, not any brand's, and a route that accepted it would be
# querying the wrong deployment with full confidence.
BV_HOUSE_PASSKEY = "b8d57893b7754e2d390348dc172b7c76"

# The display endpoint. `apiversion` 5.4 is what the brands' own widgets send.
BV_API = "https://api.bazaarvoice.com/data/reviews.json"
BV_DEPLOY = ("https://apps.bazaarvoice.com/deployments/%s/%s/production/%s/bv.js")

# Offset pagination has no cap on this route, but an unbounded loop against
# someone else's API is not a thing to ship. 400 pages x 100 = 40,000 reviews,
# comfortably past the deepest deployment measured (Owala, 14,029).
BV_MAX_PAGES = 400
BV_PAGE_LIMIT = 100          # the API's own ceiling; 200 silently returns 100
BV_PAUSE_S = 0.4             # reading pace, not scraping pace

# Past this, a deployment is reported as probably dead. CHOSEN, not measured,
# and it only ever warns - it never refuses, because a stale deployment's old
# reviews are still real reviews and dropping them would be worse than
# labelling them. The number sits in a wide gap: the live deployments measured
# on 2026-10-15 were 4-20 days stale and Owala was 1,540.
BRAND_STALE_DAYS = 180


def http_get_text(url, accept_gzip=True, timeout=60):
    """GET a public URL as TEXT, decompressing gzip whatever was negotiated.

    ACCEPT GZIP. Bazaarvoice gzips some deployment zones regardless of what
    `Accept-Encoding` asked for, and a gzip body decoded as text is a binary
    blob in which every regex below silently finds nothing. That produced FIVE
    false negatives in the 2026-10-15 survey - Monster, Red Bull, Owala and
    Simply Good Foods were all recorded as running no deployment when all four
    publish a key - and one of them, Red Bull, turned out to be among the best
    samples available. A regex finding nothing is evidence only once the body
    is known to be text.

    No Referer. No Origin. No cookies. The keys this route reads are the ones
    the platform's own CDN publishes in the clear to every visitor's browser;
    nothing here is authenticated and nothing here is circumvented.
    """
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "review-tone-monitor/%s (research; contact via repo owner)"
                      % COLLECTOR_VERSION,
        "Accept": "*/*",
        "Accept-Encoding": "gzip" if accept_gzip else "identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                import gzip
                import io
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return r.getcode(), raw.decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return e.code, "", "http_%s" % e.code
    except Exception as e:                                     # noqa: BLE001
        return None, "", "transport: %s" % e


class BazaarvoiceRoute(Route):
    """Route C. The brand's own Bazaarvoice deployment.

    WHAT THIS ROUTE IS, precisely, because the distinction decides whether it
    may be used at all: a brand pays Bazaarvoice to host and RENDER its
    reviews, and the renderer is JavaScript running in every visitor's browser.
    That JavaScript needs a display key, so the brand's CDN publishes one, in
    the clear, to everybody. This route reads the same key from the same public
    file and calls the same documented display endpoint the widget calls.

    No account is created. No key of ours is used. No credential is sent. No
    cookie is sent. Nothing is logged into. There is no bot challenge on this
    surface and so nothing is solved or evaded (CORE-PROMPT §9).

    `apps.bazaarvoice.com/bfd/v1/...` is a DIFFERENT endpoint and it returns
    401. It is left alone. It would answer if sent a Referer or an Origin
    header forged to look like the brand's own site, and that is exactly the
    thing CONTRACTS.md §5 forbids: a block is a finding, recorded and not
    routed around. The scout route below is not a way past that refusal; it is
    a separate endpoint that does not refuse in the first place.

    What the route costs, and it is not money:

      - it sees the BRAND'S OWN SITE, which is a different sales channel from
        Amazon with a different customer. A brand-route series and an Amazon
        series are two instruments, and pooling them would be reading channel
        mix as tone.
      - syndication. Bazaarvoice networks share reviews between retailers, so
        some rows did not originate on this brand's site. `IsSyndicated` and
        the source are recorded per review rather than filtered here, because
        whether to include them is an aggregation decision.

    Measured on Traeger (`traegerservice`, TFB57GLE) 2026-10-15: 1,215
    reviews, 100% carrying text, Offset paginating to exhaustion with no cap,
    dates genuinely descending, newest review 4 days old, cost $0.
    """

    name = "bazaarvoice"
    route_kind = "brand_platform"
    provider = "bazaarvoice"
    platform = "bazaarvoice"

    def __init__(self, client, site="main_site", locale="en_US", passkey=None,
                 max_pages=BV_MAX_PAGES, cache_dir=None, timeout=60,
                 pause_s=BV_PAUSE_S):
        self.client = client
        self.site = site or "main_site"
        self.locale = locale or "en_US"
        self.passkey = passkey
        self.max_pages = max_pages
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.pause_s = pause_s
        self.key_meta = {}

    def describe(self):
        return "bazaarvoice:%s/%s" % (self.client, self.site)

    # -- key discovery ----------------------------------------------------

    def discover_passkey(self):
        """Read the display key the brand's own CDN publishes.

            bv.js  ->  legacyScoutUrl  ->  bvapi.js  ->  passkey:"ca..."

        Both files are fetched WITH gzip accepted; see http_get_text for the
        five false negatives that cost.
        """
        if self.passkey:
            self.key_meta = {"passkey_source": "supplied on the command line"}
            return self.passkey
        url = BV_DEPLOY % (self.client, self.site, self.locale)
        code, js, err = http_get_text(url)
        self.key_meta = {"bv_js_url": url, "bv_js_http": code,
                         "bv_js_bytes": len(js)}
        if err or code != 200:
            return None
        dc = re.search(r'displayCode:"(\d+)"', js)
        self.key_meta["display_code"] = dc.group(1) if dc else None
        scout = re.search(r'legacyScoutUrl:"([^"]+)"', js)
        if not scout:
            # A real, informative state rather than an error: Bazaarvoice
            # serves a stub bv.js for a zone that exists but is provisioned to
            # display nothing. Distinguish it from a 404 and from a gzip blob.
            self.key_meta["scout_url"] = None
            self.key_meta["looks_like_text"] = "legacyScoutUrl" in js or "bv" in js
            return None
        self.key_meta["scout_url"] = scout.group(1)
        code2, js2, err2 = http_get_text(scout.group(1))
        self.key_meta["scout_http"] = code2
        self.key_meta["scout_bytes"] = len(js2)
        if err2 or code2 != 200:
            return None
        keys = [k for k in sorted(set(re.findall(r'passkey:"([A-Za-z0-9]{24,})', js2)))
                if k != BV_HOUSE_PASSKEY]
        self.key_meta["passkeys_published"] = len(keys)
        self.key_meta["house_passkey_seen"] = BV_HOUSE_PASSKEY in js2
        if not keys:
            return None
        self.passkey = keys[0]
        self.key_meta["passkey_source"] = "published by the brand's own CDN"
        return self.passkey

    # -- collection -------------------------------------------------------

    def page_url(self, product_id, offset, limit):
        import urllib.parse
        return BV_API + "?" + urllib.parse.urlencode({
            "apiversion": "5.4", "passkey": self.passkey,
            "Filter": "ProductId:eq:%s" % product_id,
            "Limit": limit, "Offset": offset,
            "Sort": "SubmissionTime:desc",
            "Include": "Products", "Stats": "Reviews"})

    def _cache_page(self, product_id, offset, body):
        """Keep the raw response. A parser bug must never cost a re-fetch.

        This route is free, so the money argument does not apply - but the
        other two reasons the Apify route learned the hard way do, and they are
        the ones that actually bit:

          - a run parsed to ZERO ROWS on a field-name mismatch. That announces
            itself.
          - a run recorded 0% VERIFIED PURCHASES when the truth was 100%. That
            does not. A plausible wrong number reaches the verified-share check
            and is read as evidence about the review population rather than
            about the parser.

        And one this route adds: the pool is live. Re-fetching next week does
        not reproduce today's batch, so the raw response is the only thing that
        can ever be re-parsed into the batch that was actually written.
        """
        if not self.cache_dir:
            return None
        os.makedirs(self.cache_dir, exist_ok=True)
        p = os.path.join(self.cache_dir,
                         "%s-offset-%06d.json" % (product_id, offset))
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def _pages_from_cache(self):
        out = []
        for n in sorted(os.listdir(self.cache_dir)):
            if not n.endswith(".json"):
                continue
            with open(os.path.join(self.cache_dir, n), encoding="utf-8") as f:
                out.append((n, json.load(f)))
        return out

    def id_set(self, product_id, limit=BV_PAGE_LIMIT):
        """The newest page's review ids, for the family comparison.

        One page is enough and the whole pool is not: two products that share a
        pool return the same ids in the same order at the same offset, and two
        that do not share one return disjoint ids on the first page. Walking
        both pools to exhaustion to answer that would cost ~24 extra requests
        per sibling to learn what the first page already says.
        """
        code, body, err = http_get_text(self.page_url(product_id, 0, limit),
                                        timeout=self.timeout)
        if err or code != 200:
            return None, {"http": code, "error": err}
        try:
            d = json.loads(body)
        except ValueError:
            return None, {"http": code, "error": "not json"}
        if d.get("HasErrors"):
            return None, {"http": code,
                          "errors": [e.get("Code") for e in (d.get("Errors") or [])]}
        res = d.get("Results") or []
        return ({str(r.get("Id")) for r in res if r.get("Id")},
                {"http": code, "returned": len(res),
                 "total_results": d.get("TotalResults")})

    def collect(self, product_id, sort, limit, filters=None):
        f = dict(filters or {})
        at = utcnow()
        meta = {"platform": "bazaarvoice", "client": self.client,
                "site": self.site, "locale": self.locale,
                "endpoint": BV_API, "sort_param": "SubmissionTime:desc",
                "pagination": "Offset, to exhaustion; the API caps Limit at %d "
                              "and does not cap Offset" % BV_PAGE_LIMIT,
                "account_needed": False,
                "headers_sent": ["User-Agent", "Accept", "Accept-Encoding"],
                "referer_or_origin_sent": False,
                "cost_note": "no account, no key of ours, no plan; $0"}

        if self.cache_dir and f.get("from_cache"):
            return self._collect_from_cache(product_id, f, at, meta)

        key = self.discover_passkey()
        meta["key_discovery"] = self.key_meta
        if not key:
            # A deployment that publishes no key is a finding about the brand,
            # recorded as a block with its own reason rather than retried from
            # another angle. There is no other angle that does not involve
            # forging a header.
            return Batch(route="bazaarvoice", route_kind=self.route_kind,
                         sort_observed=None, filters=f, reviews=[], claimed={},
                         requested=limit, marketplace=None, blocked=True,
                         block_reason="bazaarvoice_no_published_passkey",
                         requested_at=at, provider="bazaarvoice",
                         plan_tier="public display key", cost=0.0,
                         raw_meta=meta)
        meta["passkey_prefix"] = key[:4] + "..."

        rows_raw, pages, total, blocked = [], [], None, None
        offset = 0
        for _ in range(self.max_pages):
            url = self.page_url(product_id, offset, BV_PAGE_LIMIT)
            code, body, err = http_get_text(url, timeout=self.timeout)
            if err or code != 200:
                pages.append({"offset": offset, "http": code, "refused": err})
                blocked = "bazaarvoice_http_%s" % code
                break
            try:
                d = json.loads(body)
            except ValueError:
                pages.append({"offset": offset, "http": code,
                              "refused": "not json"})
                blocked = "bazaarvoice_bad_json"
                break
            if d.get("HasErrors"):
                codes = [e.get("Code") for e in (d.get("Errors") or [])]
                pages.append({"offset": offset, "http": code, "errors": codes})
                blocked = "bazaarvoice_api_error_%s" % (",".join(str(c) for c in codes)
                                                        or "unknown")
                break
            self._cache_page(product_id, offset, body)
            total = d.get("TotalResults") if d.get("TotalResults") is not None else total
            res = d.get("Results") or []
            pages.append({"offset": offset, "http": code, "returned": len(res)})
            rows_raw.extend(res)
            if not res:
                break
            offset += len(res)
            if total is not None and offset >= total:
                break
            if limit and len(rows_raw) >= limit:
                break
            time.sleep(self.pause_s)

        meta["pages"] = pages
        meta["pages_walked"] = len(pages)
        meta["raw_results"] = len(rows_raw)
        meta["raw_cache_dir"] = self.cache_dir
        meta["walk_exhausted"] = (total is not None and len(rows_raw) >= total)
        meta["page_cap_reached"] = len(pages) >= self.max_pages

        if blocked and not rows_raw:
            return Batch(route="bazaarvoice", route_kind=self.route_kind,
                         sort_observed=None, filters=f, reviews=[], claimed={},
                         requested=limit, marketplace=None, blocked=True,
                         block_reason=blocked, requested_at=at,
                         provider="bazaarvoice", plan_tier="public display key",
                         cost=0.0, raw_meta=meta)
        if blocked:
            # Partial walks are kept and SAID SO. Discarding them would throw
            # away real reviews; recording them without the flag would let a
            # truncated history read as a complete one.
            meta["walk_truncated_by"] = blocked
            warn("the walk stopped early (%s) after %d review(s); the batch is "
                 "recorded as a PARTIAL pass, so its span is not this product's "
                 "full history" % (blocked, len(rows_raw)))

        return self._finish(product_id, rows_raw, total, f, at, meta, limit)

    def _collect_from_cache(self, product_id, f, at, meta):
        """Re-parse a cached walk. Costs nothing and re-fetches nothing.

        The live pool moves, so this is the only way to re-parse the batch that
        was actually written rather than a newer one that resembles it.
        """
        files = self._pages_from_cache()
        meta["reparse_of_cached_walk"] = True
        meta["cache_dir"] = self.cache_dir
        meta["cached_pages"] = [n for n, _ in files]
        if not files:
            die("no cached pages in %s.\n"
                "Nothing to re-parse. A re-parse reads the raw responses an\n"
                "earlier walk stored; it never re-fetches, because the review\n"
                "pool moves and a re-fetch would be a different batch wearing\n"
                "this one's id." % self.cache_dir)
        rows_raw, total = [], None
        for _, d in files:
            if d.get("TotalResults") is not None:
                total = d.get("TotalResults")
            rows_raw.extend(d.get("Results") or [])
        meta["raw_results"] = len(rows_raw)
        return self._finish(product_id, rows_raw, total, f, at, meta, None)

    def _finish(self, product_id, rows_raw, total, f, at, meta, limit):
        rows, notes = normalise_bazaarvoice(rows_raw, product_id, self.locale)
        meta["normalise_notes"] = dict(notes)
        if limit:
            rows = rows[:limit]
        claimed = {}
        if total is not None:
            # TotalResults counts REVIEWS in this family's pool, not ratings.
            # Unlike Amazon this route states a review count directly, so the
            # coverage denominator is the population actually being sampled and
            # `claimed_total_basis` is `reviews` rather than `ratings`.
            claimed = {"reviews_total": int(total),
                       "reviews_source_text":
                           "bazaarvoice reviews.json TotalResults for "
                           "ProductId:eq:%s" % product_id}
        return Batch(
            route="bazaarvoice", route_kind=self.route_kind,
            # Read back off the response, never taken from the sort parameter.
            # The date-monotonicity check in check_recency is what actually
            # decides sort_verified; this is only the label the API echoed.
            sort_observed="SubmissionTime:desc",
            filters=f, reviews=rows, claimed=claimed,
            # None means "everything there was", which is what a limit of 0
            # asked for. Writing 0 into §3's `requested` would read as a batch
            # that asked for nothing and got 1,215.
            requested=(limit or None), marketplace=None, requested_at=at,
            provider="bazaarvoice", plan_tier="public display key",
            # $0 is a MEASUREMENT, not a missing field. A route whose cost is
            # unrecorded cannot be compared against one whose cost is known,
            # and "free" is the single most load-bearing fact about this route.
            cost=0.0,
            client=self.client, product_id=product_id,
            review_id_scheme="bazaarvoice_review_id",
            page_url=self.page_url(product_id, 0, BV_PAGE_LIMIT).split("&passkey=")[0],
            raw_meta=meta)


PLATFORMS = {"bazaarvoice": BazaarvoiceRoute}


def _badge_true(v):
    """Is a Bazaarvoice badge set?

    A badge arrives as an OBJECT whose presence is the signal - there is no
    boolean to read. So a dict means set, a real boolean is honoured as one,
    and absence means not set. Spelled out rather than left as `bool(v)`
    because `bool({"Id": ...})` would also be true for a badge the platform
    sent with a false value, and that reading would be undetectable.
    """
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, dict):
        return True
    return bool(v)


def normalise_bazaarvoice(results, product_id, locale="en_US"):
    """Bazaarvoice rows -> the same review dicts the other routes produce.

    A row that cannot be labelled honestly is dropped and COUNTED, never
    patched: a review with no date cannot be put in a quarter and a review with
    no text cannot be scored. Both are reported rather than defaulted, which is
    the same rule the other two routes follow.

    `ReviewText` is taken verbatim and nothing here rewrites, trims, joins or
    summarises it (CORE-PROMPT §1.3). There is no hash to verify on this route
    for the same reason Route B has none: the response arrives over HTTP into
    this process and the model never touches the text.
    """
    rows, notes = [], Counter()
    country = (locale.split("_")[-1] if "_" in (locale or "") else "") or ""
    for i, r in enumerate(results):
        if not isinstance(r, dict):
            notes["not_a_dict"] += 1
            continue
        rid = r.get("Id")
        if not rid:
            # No id means no dedup key, and this route re-walks the same pool
            # every cadence. A row that cannot be deduped would be re-added for
            # ever, so it is dropped rather than given a synthetic id that
            # would change if any field moved.
            notes["dropped_no_review_id"] += 1
            continue
        st = r.get("SubmissionTime") or ""
        date = None
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(st))
        if m:
            try:
                datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                date = m.group(0)
            except ValueError:
                date = None
        if not date:
            notes["dropped_unparseable_date"] += 1
            continue
        text = r.get("ReviewText") or ""
        title = r.get("Title") or ""
        if not str(text).strip() and not str(title).strip():
            # 3% of Traeger's pool. Star-only entries this pipeline can never
            # score; counted so the 97% text share is a measurement.
            notes["dropped_star_only_no_text"] += 1
            continue
        try:
            rating = float(r.get("Rating"))
        except (TypeError, ValueError):
            rating = None
        src = r.get("SyndicationSource") or {}
        badges = r.get("Badges") or {}
        rows.append({
            "review_id": str(rid),
            # Amazon identifiers do not exist on this route and are left EMPTY
            # rather than filled with the brand's product id. A product_id in
            # an `asin` column is a fabricated identifier that would join
            # against the Amazon route's rows and look like it worked.
            "asin": "",
            "parent_asin": "",
            "client": "",            # set by the collector from the batch
            "product_id": product_id,
            "review_date": date,
            "title": str(title).replace("\x00", ""),
            "text": str(text).replace("\x00", ""),
            "rating": "" if rating is None else rating,
            # VERIFIED PURCHASE IS A BADGE, AND THE BADGE IS A PRESENCE.
            # Measured on Traeger 2026-10-15: `IsVerifiedPurchaser` is absent
            # from all 1,215 rows, and the information lives in
            # `Badges.verifiedPurchaser`, present on 744 of them with a badge
            # OBJECT as its value. So presence is the whole semantics.
            #
            # This is the Apify 0%-vs-100% trap one platform along. Reading
            # only `IsVerifiedPurchaser` here would have recorded 0% verified
            # for a pool that is 61% verified - a plausible number, which is
            # the dangerous kind: it would have reached the verified-share
            # check as evidence about Traeger's customers rather than about
            # this parser. _badge_true is explicit about presence-vs-value so
            # a future `verifiedPurchaser: false` cannot read as true, which
            # `bool({...})` would.
            "verified_purchase": (_badge_true(r.get("IsVerifiedPurchaser"))
                                  or _badge_true(badges.get("verifiedPurchaser"))),
            # 179 of Traeger's 1,215 (14.7%) carry `incentivizedReview`. A
            # brand that solicited a review with an incentive has sampled a
            # different population, and tone is exactly what an incentive
            # moves. Recorded rather than filtered, like syndication: whether
            # to include them is an aggregation decision, and dropping them
            # here would change the population without saying so.
            "incentivized": _badge_true(badges.get("incentivizedReview")),
            "helpful_vote": int(r.get("TotalPositiveFeedbackCount") or 0),
            "review_country": country,
            "vine_voice": False,     # an Amazon programme; not this platform's
            # Bazaarvoice networks share reviews between retailers, so a row
            # may not have originated on this brand's own site. Recorded, not
            # filtered: whether to include syndicated reviews in a company's
            # tone is an aggregation decision, and silently dropping them here
            # would change the population without saying so.
            "syndicated": bool(r.get("IsSyndicated")),
            "syndication_source": str(src.get("Name") or ""),
            "review_id_scheme": "bazaarvoice_review_id",
            "position_on_page": i,
        })
        notes["kept"] += 1
    return rows, notes


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
    parents = Counter()
    tmax = ts_max_ms()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        rid = (it.get("reviewId") or it.get("ReviewId") or it.get("id") or
               it.get("review_id") or it.get("reviewUrl") or "")
        m = re.search(r"(R[A-Z0-9]{8,})", str(rid))
        scheme = "provider_review_id"
        if m:
            rid = m.group(1)
            scheme = "amazon_review_id"
        date_raw = (it.get("date") or it.get("ReviewDate") or it.get("reviewedAt") or
                    it.get("timestamp") or it.get("reviewDate") or "")
        if isinstance(date_raw, dict):
            date_raw = date_raw.get("utc") or date_raw.get("raw") or ""
        date, ts = _provider_date(str(date_raw), tmax)
        if date is None:
            continue
        text = (it.get("reviewDescription") or it.get("ReviewContent") or
                it.get("text") or it.get("body") or it.get("content") or "")
        title = (it.get("reviewTitle") or it.get("ReviewTitle") or
                 it.get("title") or "")
        if not str(text).strip() and not str(title).strip():
            continue
        rating = (it.get("ratingScore") or it.get("ReviewScore") or
                  it.get("rating") or it.get("stars"))
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
        for k, dest in (("TotalRatings", "ratings_total"),
                        ("RatingTypeTotalReviewsCount", "reviews_total"),
                        ("totalCategoryRatings", "ratings_total"),
                        ("countRatings", "ratings_total"),
                        ("totalRatings", "ratings_total"),
                        ("totalCategoryReviews", "reviews_total"),
                        ("reviewsCount", "reviews_total"),
                        ("totalReviews", "reviews_total")):
            if it.get(k) and not claimed.get(dest):
                claimed[dest] = int(it[k])
                claimed[dest.replace("_total", "_source_text")] = "%s item.%s" % (provider, k)
        # junglee: `productOriginalAsin` is the asin that was REQUESTED (the
        # parent), while `productAsin`/`variantAsin` are the variant the
        # reviewer actually bought. Reading the variant as the parent produced
        # a false PARENT ASIN MISMATCH on the first live run - the guard fired
        # correctly against a wrong input, which is the right failure but still
        # a wrong answer. Parent first, variant never.
        pa = (it.get("productOriginalAsin") or it.get("parent_asin") or
              it.get("parentAsin") or it.get("ASIN") or "")
        if pa:
            parents[pa.upper()] += 1
        rows.append({
            "review_id": str(rid),
            "asin": (it.get("variantAsin") or it.get("VariantASIN") or
                     it.get("productAsin") or it.get("asin") or
                     product_id or "").upper(),
            "parent_asin": pa.upper(),
            "review_date": date,
            "title": str(title).replace("\x00", ""),
            "text": str(text).replace("\x00", ""),
            "rating": "" if rating is None else rating,
            "verified_purchase": bool(it.get("isVerified") or it.get("verified") or
                                      it.get("verified_purchase") or
                                      it.get("Verified") or
                                      it.get("VerifiedPurchase")),
            "helpful_vote": _provider_votes(it),
            "review_country": str(it.get("country") or it.get("ReviewCountry") or
                                  it.get("reviewCountry") or ""),
            "vine_voice": bool(it.get("isAmazonVine") or it.get("vine") or
                               it.get("isVine")),
            "review_id_scheme": scheme,
            "position_on_page": i,
        })
    obs = sorts.most_common(1)[0][0] if sorts else None
    par = parents.most_common(1)[0][0] if parents else None
    return rows, claimed, obs, par


def _provider_votes(it):
    """Helpful votes, from a count or from the sentence providers return.

    junglee gives `reviewReaction` as "3 people found this helpful" and nothing
    numeric, so a plain int() would silently record 0 for every review that has
    votes - the field the degradation test reads.
    """
    for k in ("helpfulVotes", "HelpfulVotes", "helpful_votes", "helpful"):
        v = it.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().isdigit():
            return int(v)
    s = str(it.get("reviewReaction") or "")
    if re.match(r"^one person", s, re.I):
        return 1
    m = re.search(r"([0-9][0-9,.]*)", s)
    return int(m.group(1).replace(",", "").replace(".", "")) if m else 0


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

def _is_unfiltered(filters):
    """Was this batch taken over the whole review population?

    Only an unfiltered batch's claimed total describes the population a series
    is a fraction OF. A star-filtered batch counts that star alone.
    """
    f = filters or {}
    star = f.get("star") or "all_stars"
    return str(star) in ("all_stars", "allStars", "all", "")


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
            # Read the pre-rename spelling back as the current one. Without
            # this the first re-collection of an existing group rewrites every
            # already-held review with an EMPTY route, because reviews.csv is
            # derived and DictWriter drops an unknown key without a word.
            for old, new in LEGACY_FIELD_NAMES.items():
                if old in r and not r.get(new):
                    r[new] = r.pop(old)
            # Same hazard, second column. A file written before `family`
            # existed holds the join key in `parent_asin` only, and rewriting
            # it would leave `family` empty for every review already held -
            # which is not a cosmetic gap, it is the join key going missing on
            # exactly the rows that are already in a series.
            if not r.get("family") and r.get("parent_asin"):
                r["family"] = r["parent_asin"]
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
                sort_verified, family=None):
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
            "route": batch.route,
            "client": r.get("client") or (batch.client or ""),
            "product_id": r.get("product_id") or (batch.product_id or ""),
            # THE JOIN KEY, and it is route-independent by design: the parent
            # asin on an Amazon route, the verified family on a brand route.
            # Both routes pool reviews across a group of products that are
            # sold separately, and `family` is the one column that says which
            # group a review actually belongs to. Falling back to the row's
            # own parent_asin keeps a re-parse of an Amazon batch correct.
            "family": (family or r.get("parent_asin")
                       or (batch.parent_asin or "")),
            "collected_at": batch.requested_at,
            "review_country": r["review_country"],
            "review_id_scheme": r["review_id_scheme"],
            "review_date_precision": batch.date_precision,
            "vine_voice": r["vine_voice"],
            "sort_requested": sort_requested,
            "sort_verified": "" if sort_verified is None else sort_verified,
            "syndicated": r.get("syndicated", ""),
            "syndication_source": r.get("syndication_source", ""),
            "incentivized": r.get("incentivized", ""),
        }

        prev = existing.get(r["review_id"])
        if prev is None:
            existing[r["review_id"]] = row
            new += 1
            continue

        dupes += 1
        diffs = {}
        for k in ("helpful_vote", "title", "text", "rating", "verified_purchase",
                  "review_date", "parent_asin", "family"):
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


def schedule_drift(outdir, group_id, slug, tracked, cadence, collected_at):
    """How far off the declared cadence this batch actually ran.

    Recorded per batch so the spacing of the series is a property of the data,
    not of whoever remembered to run it. CORE-PROMPT §4: changing the cadence
    changes the instrument, and so does drifting off it.
    """
    base = {"cadence": cadence["cadence"], "cadence_days": cadence["cadence_days"],
            "cadence_declared_at": cadence["declared_at"]}
    prev = [b for b in read_jsonl(batches_path(outdir, group_id, slug))
            if str(b.get("product_key") or b.get("asin") or "").upper()
            == str(tracked).upper() and not b.get("blocked")]
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
                 merge_stats, stage_note, declared_parent=None,
                 pkey=None, family=None, family_verified=None,
                 family_detail=None):
    """Write the batch's provenance. CORE-PROMPT §1.4: or it is not data.

    The keys CONTRACTS.md §3 names come first and are spelled exactly as it
    spells them. Everything after them is additive, and a reader that only
    knows §3 is unaffected by it.
    """
    slug = member["company_slug"]
    # Counted on the FAMILY, which is the join key for both routes (§2).
    # Counting on `asin` returned zero the moment a provider started reporting
    # the VARIANT each reviewer bought, because none of those equal the
    # declared variant - series coverage silently read 0 of 10,000 while ten
    # reviews sat on disk. The brand route has the identical trap one name
    # along: counting on the declared `product_id` would read zero for every
    # sibling in a pooled family.
    jkey = str(family or declared_parent or asin or "").upper()
    on_product = sum(1 for r in load_reviews(outdir, group_id, slug).values()
                     if str(r.get("family") or r.get("parent_asin")
                            or r.get("asin") or "").upper() == jkey)
    ratio_batch, claimed_total, basis = coverage(batch.returned, batch.claimed)
    # The identifier this product is TRACKED by, used to find this product's
    # own earlier batches. An ASIN on the Amazon routes, a platform product id
    # on a brand route.
    tracked = str(pkey if pkey is not None else (asin or ""))

    # The SERIES denominator must be the unfiltered population, not this
    # batch's. A star-filtered batch reports the count for that star only (49
    # one-star reviews), and dividing the whole series by it gave "143 of 49"
    # - a coverage ratio of 292%. §4 requires this number beside every tone
    # number, so a ratio that can exceed 1 is not a rounding problem.
    widest = dict(batch.claimed)
    wmax = (claimed_total or 0) if _is_unfiltered(batch.filters) else 0
    wsrc = "this batch" if wmax else None
    for b in read_jsonl(batches_path(outdir, group_id, slug)):
        # `product_key` on anything this version wrote; `asin` for the batches
        # written before brand products existed, which had no other key.
        if str(b.get("product_key") or b.get("asin") or "").upper() != tracked.upper():
            continue
        if not _is_unfiltered(b.get("filters")):
            continue
        ct = b.get("claimed_total") or 0
        if ct > wmax:
            wmax, wsrc = ct, b.get("batch_id")
    if wmax:
        widest = {"reviews_total": wmax} if basis == "reviews" else {"ratings_total": wmax}
    ratio_series, series_denom, series_basis = coverage(on_product, widest)

    rec = OrderedDict([
        # --- CONTRACTS.md §3, in its order and with its spellings ---
        ("batch_id", batch_id),
        ("collected_at", batch.requested_at),
        ("route", batch.route),
        ("filters", batch.filters),
        # §3's identifiers are Amazon-shaped, so they are kept with their own
        # spellings and left NULL on a brand route rather than filled with the
        # nearest-looking value. A brand `product_id` written into an `asin`
        # column is a fabricated identifier that would join against the Amazon
        # route's rows and look like it worked.
        ("asin", (asin or "").upper() or None),
        ("parent_asin", batch.parent_asin),
        ("marketplace", batch.marketplace),
        # The brand-route identifiers, alongside §3's rather than instead of
        # them, so one reader handles both routes.
        ("product_key", tracked),
        ("client", batch.client),
        ("product_id", batch.product_id),
        ("family", family),
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
        # DEPTH AND LIVENESS ARE SEPARATE QUESTIONS, and nothing else in this
        # record answers the second. Owala serves 14,029 reviews flawlessly
        # with a newest review of 2022-07-28 - 1,540 days stale - so every
        # depth, coverage and sort field above can read healthy on a deployment
        # that has been dead for four years, and a cadence run against it would
        # collect nothing new for ever without a single number moving.
        ("newest_review_age_days", newest_age_days(newest)),
        ("newest_review_date", newest),
        ("oldest_review_date", oldest),
        # The family and HOW IT WAS ESTABLISHED. `family_verified` is None when
        # nothing could be compared, because §5 forbids "never checked" and
        # "checked and clean" looking alike.
        ("family_verified", family_verified),
        ("family_check", family_detail),
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
        ("coverage_series_denominator", series_denom),
        ("coverage_series_denominator_from", wsrc),
        ("reviews_on_product_after", on_product),
        ("group_id", group_id),
        ("company", member.get("company")),
        ("company_slug", slug),
        ("route_kind", batch.route_kind),
        ("provider", batch.provider),
        ("provider_plan", batch.plan_tier),
        # COST IS $0 ON THE BRAND ROUTE AND THE FIELD STAYS. A zero is a
        # measurement: it is the whole reason this route is preferred over a
        # paid one for depth, and a route whose cost were simply absent could
        # not be compared against one whose cost is known.
        ("provider_cost", batch.cost),
        ("review_id_scheme", batch.review_id_scheme),
        ("review_date_precision", batch.date_precision),
        ("requested_at", batch.requested_at),
        ("written_at", utcnow()),
        ("merge", merge_stats),
        ("schedule", schedule_drift(outdir, group_id, slug, tracked, cadence,
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


# A sibling sharing a pool returns the SAME ids at the same offset; measured on
# Traeger, identically, over 1,215 reviews. The threshold is below 1.0 only
# because the pool is live: a review arriving between two requests shifts the
# 100-row window by one, so a true sibling can read 99/100 rather than 100/100.
#
# This constant is CHOSEN, not measured, and it is recorded as such. What makes
# it safe is not its value but the size of the gap it sits in: a shared pool
# measures ~1.00 and a separate pool measures 0.00, so anything between is a
# third thing nobody has seen and the refusal below names the number rather
# than rounding it away.
FAMILY_OVERLAP_MIN = 0.90


def verify_family(route, collected_ids, declared_family, siblings, collected_key):
    """Is the declared family what the platform actually pools? MEASURED.

    The brand route's analogue of the parent-asin check, and it exists for the
    same reason: a brand platform pools reviews across a product FAMILY, so a
    row filed under one product id can hold a whole family's reviews, and dedup
    by review id does not notice because they are genuine distinct reviews.

    The method is the only honest one: COMPARE RETURNED ID SETS. Two ways that
    are forbidden and both of which would have given the wrong answer on
    Traeger:

      - inferring the family from the product ids DIFFERING. TFB57GLE and
        TFB78GLE are different ids, different grills and different prices, and
        they are one family returning one identical 1,215-review id set.
      - summing TotalReviewCount across the declared products, which
        double-counts a pooled family and overstated Traeger's unique count by
        30%.

    Returns (verified, detail). `verified` is None when there is no sibling to
    compare against - a singleton family is unverifiable rather than verified,
    and the two must never look alike.
    """
    others = [s for s in siblings if str(s) != str(collected_key)]
    detail = {"declared_family": declared_family,
              "declared_members": list(siblings),
              "compared_against": others,
              "method": "pairwise review-id set comparison at offset 0",
              "overlap_min": FAMILY_OVERLAP_MIN,
              "probes": []}
    if not others:
        detail["basis"] = ("one declared product in this family, so there is "
                           "no sibling to compare id sets against")
        return None, detail
    if not collected_ids:
        detail["basis"] = "this batch returned no ids to compare"
        return None, detail

    for sib in others:
        ids, pmeta = route.id_set(sib)
        if ids is None:
            # A sibling that cannot be read leaves the family UNVERIFIED. It is
            # not a mismatch - nothing was compared - and reporting it as
            # verified would be the §5 failure: "never checked" and "checked
            # and clean" must never look alike.
            detail["probes"].append(dict(pmeta, product_id=sib,
                                         comparable=False))
            continue
        shared = len(ids & collected_ids)
        frac = shared / float(min(len(ids), len(collected_ids)) or 1)
        detail["probes"].append(dict(pmeta, product_id=sib, comparable=True,
                                     sibling_ids=len(ids),
                                     shared_ids=shared,
                                     overlap_of_smaller=round(frac, 4)))
        if frac < FAMILY_OVERLAP_MIN:
            die("FAMILY MISMATCH on the brand route.\n"
                "  group file declares family  %s\n"
                "  containing                  %s\n"
                "  collected                   %s  (%d ids)\n"
                "  sibling                     %s  (%d ids)\n"
                "  shared ids                  %d  (%.1f%% of the smaller set)\n\n"
                "Refusing the batch. These two products do NOT serve one pooled\n"
                "review set, so the declared family is wrong and `family` - the\n"
                "join key - would file two populations under one label. Nothing\n"
                "downstream could detect it: both sets are genuine reviews with\n"
                "genuine ids, exactly as with an Amazon re-parenting.\n"
                "Re-resolve the family in stage G by comparing id sets; do not\n"
                "edit the group file to match this batch, because if two members\n"
                "have converged on one pool the peer group itself no longer holds."
                % (declared_family, ", ".join(str(s) for s in siblings),
                   collected_key, len(collected_ids), sib, len(ids),
                   shared, 100.0 * frac))

    comparable = [p for p in detail["probes"] if p.get("comparable")]
    if not comparable:
        detail["basis"] = ("no sibling could be read, so the family is "
                           "UNVERIFIED rather than confirmed")
        return None, detail
    detail["basis"] = ("%d sibling(s) returned the same pooled review ids"
                       % len(comparable))
    return True, detail


def newest_age_days(newest, asof=None):
    """How stale is the freshest review? Depth and LIVENESS are separate.

    Owala serves 14,029 reviews flawlessly and its newest is 2022-07-28 -
    1,540 days stale. A deployment can be deep and dead, and a cadence run
    against a dead one would collect nothing new for ever while every depth
    and coverage number on the batch stayed healthy. Nothing else in the batch
    record would show it, which is why this is its own field.
    """
    if not newest:
        return None
    try:
        d = datetime.strptime(str(newest)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = (asof or datetime.now(timezone.utc)).date()
    return (today - d).days


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
    # One identifier, resolved per route. `--asin` on the Amazon routes,
    # `--product-id` on a brand route; whichever the caller gave.
    tracked = str(getattr(args, "product_id", None)
                  or getattr(args, "asin", None) or "")
    member, product = group_member(group, args.company, tracked)
    if product is None:
        product = member_product(member, tracked)
    if product is None:
        die("%s is not listed under %s in %s"
            % (tracked, member["company_slug"], group["_path"]))
    slug = member["company_slug"]
    declared_route = product_route(product)

    # A product declared on one route may not be collected on another. The two
    # routes read different channels with different customers, so filing a
    # brand-site batch under a product declared for Amazon would pool two
    # populations in one series - and `route` is per review precisely so that
    # cannot happen silently.
    if declared_route != route.name and not (
            declared_route == "amazon" and route.route_kind in ("session", "provider")):
        die("ROUTE MISMATCH for %s in %s.\n"
            "  the group file declares  route %s\n"
            "  this command collects    route %s\n\n"
            "Refusing. A brand platform serves the BRAND'S OWN SITE and Amazon\n"
            "serves Amazon: different channels, different customers, different\n"
            "populations. Pooling them into one series would read channel mix\n"
            "as tone. Declare the product on the route it is collected on, in\n"
            "stage G." % (tracked, group["_path"], declared_route, route.name))

    if route.route_kind == "brand_platform":
        return _collect_brand(args, route, stage_note, group, cadence, member,
                              product, tracked)

    asin = tracked.upper()
    marketplace = product.get("marketplace") or "amazon.com"

    declared_parent = (product.get("parent_asin") or "").upper() or None
    if declared_parent is None:
        die("%s in %s carries no parent_asin.\n"
            "Amazon serves reviews at the PARENT asin, pooled across every\n"
            "variant, so without it this batch cannot say which population its\n"
            "reviews are of - and dedup by review_id will not catch the\n"
            "mislabelling, because those are genuine reviews of a wider\n"
            "product. §1 requires parent_asin per product." % (asin, group["_path"]))

    # Amazon serves reviews at the PARENT, so that is what a provider is asked
    # for. Route A asks for the declared variant instead, because the operator
    # navigates to that product's own page and the extractor reads the parent
    # back off it - which is what makes the declared parent checkable rather
    # than merely asserted.
    request_asin = declared_parent if route.route_kind == "provider" else asin
    if request_asin != asin:
        print("requesting          %s (the parent; reviews are served there)"
              % request_asin)
    batch = route.collect(request_asin, args.sort, args.limit,
                          {"star": args.star, "page_number": args.page})
    batch_id = "b-" + hashlib.sha1(
        ("%s|%s|%s|%s" % (slug, asin, batch.requested_at,
                          batch.route)).encode()).hexdigest()[:10]

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

    # On an Amazon route the family IS the parent asin - the same pooled
    # population under the name Amazon gives it.
    stats = merge_batch(outdir, args.group, slug, batch, batch_id, args.sort,
                        sort_verified, family=declared_parent)
    rec = record_batch(outdir, args.group, member, asin, batch, cadence,
                       batch_id, args.sort, sort_verified, inversions, oldest,
                       newest, stats, stage_note, declared_parent,
                       pkey=asin, family=declared_parent)
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
        print("coverage of series %.6f  (%d of %s, the unfiltered population)"
              % (rec["coverage_ratio_series"], rec["reviews_on_product_after"],
                 rec["coverage_series_denominator"]))
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


def _collect_brand(args, route, stage_note, group, cadence, member, product,
                   tracked):
    """Route C: collect one product's pooled family from a brand platform.

    Deliberately the same shape as the Amazon path above - declare, collect,
    VERIFY the pooled population against what the platform actually served,
    refuse on a mismatch, then merge and record. The population check is the
    only part that differs, because the platform states its pooling by what it
    returns rather than by naming a parent.
    """
    outdir = args.outdir
    slug = member["company_slug"]

    client = product.get("client")
    if not client:
        die("%s in %s carries no `client`.\n"
            "A brand-platform product is identified by its deployment zone plus\n"
            "its product id, and without the zone there is nothing to query.\n"
            "CONTRACTS.md §1 requires `client` on a route-%s product."
            % (tracked, group["_path"], route.name))

    # The FAMILY is required for exactly the reason `parent_asin` is required
    # on Amazon: without it a batch cannot say which population its reviews are
    # of, and dedup by review id will not catch the mislabelling because those
    # are genuine reviews of a wider product.
    family = product.get("family")
    if not family:
        die("%s in %s carries no `family`.\n"
            "A brand platform pools reviews across a product FAMILY exactly as\n"
            "Amazon pools across variants: TFB57GLE and TFB78GLE are different\n"
            "grills at different prices and return IDENTICAL 1,215-review id\n"
            "sets. Without `family` this batch would be filed under one product\n"
            "id while holding a whole family's reviews, and nothing downstream\n"
            "could detect it.\n"
            "§1 requires `family` per brand-route product, and §2 makes it the\n"
            "join key. Resolve it in stage G by comparing returned id sets."
            % (tracked, group["_path"]))

    siblings = product.get("family_product_ids") or [tracked]
    if tracked not in [str(s) for s in siblings]:
        die("%s is not listed in its own family_product_ids %s in %s.\n"
            "The declared family must contain the product being collected, or\n"
            "the id-set comparison below is checking a different family than\n"
            "the one this batch is filed under."
            % (tracked, siblings, group["_path"]))

    route.client = client
    route.site = product.get("site") or route.site
    route.locale = product.get("locale") or route.locale
    # Raw responses are kept per batch, keyed on the collection time, so a
    # re-parse reads the batch that was actually written rather than a newer
    # walk that resembles it. The pool is live; a re-fetch is a different
    # batch.
    stamp = (args.from_cache or utcnow().replace(":", "").replace("-", ""))
    route.cache_dir = os.path.join(company_dir(outdir, args.group, slug),
                                   "_raw", "%s-%s" % (route.name, stamp))

    print("group / company    %s / %s" % (args.group, slug))
    print("route              %s (%s)" % (route.name, route.route_kind))
    print("deployment         %s / %s / %s" % (client, route.site, route.locale))
    print("product            %s  (%s)" % (tracked, product.get("name") or ""))
    print("family declared    %s  containing %s"
          % (family, ", ".join(str(s) for s in siblings)))

    batch = route.collect(tracked, args.sort, args.limit,
                          {"star": args.star,
                           "from_cache": bool(args.from_cache)})
    batch_id = "b-" + hashlib.sha1(
        ("%s|%s|%s|%s" % (slug, tracked, batch.requested_at,
                          batch.route)).encode()).hexdigest()[:10]
    print("requested at       %s" % batch.requested_at)

    if batch.blocked:
        # §9 / §5: a block is a finding, written with the same provenance as a
        # successful batch. `bazaarvoice_no_published_passkey` is the common one
        # and it means the brand runs no readable deployment - which is
        # evidence about the instrument, not an obstacle to get past.
        rec = record_batch(outdir, args.group, member, None, batch, cadence,
                           batch_id, args.sort, None, 0, None, None,
                           {"new": 0, "duplicates": 0, "changed": 0,
                            "total_after": len(load_reviews(outdir, args.group, slug))},
                           stage_note, None, pkey=tracked, family=family)
        update_manifest(outdir, args.group, member, cadence, rec)
        print("\n!! BLOCKED: %s" % batch.block_reason)
        print("!! Recorded as batch %s and STOPPING." % batch_id)
        print("!! CORE-PROMPT §9: a block is recorded, never routed around.")
        print("!! In particular apps.bazaarvoice.com/bfd/v1/... returns 401 and")
        print("!! is left alone: it would answer a forged Referer or Origin, and")
        print("!! forging one to get past a refusal is the thing §5 forbids.")
        print("\nbatch log          %s" % batches_path(outdir, args.group, slug))
        return 2

    print("passkey            %s" % (batch.raw_meta.get("passkey_prefix") or "-"))
    print("pages walked       %d (%d raw result(s))"
          % (batch.raw_meta.get("pages_walked") or 0,
             batch.raw_meta.get("raw_results") or 0))
    print("raw cache          %s" % route.cache_dir)

    # THE FAMILY JOIN, measured rather than asserted.
    collected_ids = {r["review_id"] for r in batch.reviews}
    if args.from_cache:
        # A re-parse makes NO network call, by design, so it cannot compare id
        # sets and must not inherit the verification of the batch that filled
        # the cache. Reported with its own reason rather than as a failed
        # probe: "could not be checked here" and "the sibling was unreachable"
        # are different findings and the record should not confuse them.
        family_verified, family_detail = None, {
            "declared_family": family,
            "declared_members": [str(s) for s in siblings],
            "method": "none - this batch is a re-parse of cached responses",
            "basis": ("a re-parse makes no network call, so the family cannot "
                      "be re-verified here; the verification belongs to the "
                      "batch that produced the cache")}
    else:
        family_verified, family_detail = verify_family(
            route, collected_ids, family, [str(s) for s in siblings], tracked)
    if family_verified is True:
        print("family             %s CONFIRMED - %s"
              % (family, family_detail["basis"]))
        print("                   -> these are the FAMILY's reviews, pooled "
              "across its products;")
        print("                      this is family tone, not %s's alone" % tracked)
    else:
        warn("the declared family %s could NOT be verified (%s). The batch is "
             "recorded with family_verified = null; treat the population as "
             "unconfirmed. §5: never-checked and checked-and-clean must not "
             "look alike." % (family, family_detail["basis"]))

    resolve_sort(args, batch)
    monotonic, inversions, oldest, newest = check_recency(batch.reviews)
    sort_verified = None
    if args.sort == "recent" and batch.reviews:
        # DERIVED FROM THE COLLECTED DATES, never from `Sort=SubmissionTime:desc`.
        # The API echoing a sort parameter is a claim about the API; the dates
        # are the only evidence that cannot be mislabelled, and this is the same
        # rule the Amazon route already follows.
        sort_verified = monotonic
        print("date order         %s (%d inversion%s over %d dated review(s), %s .. %s)"
              % ("descending" if monotonic else "NOT descending", inversions,
                 "" if inversions == 1 else "s", len(batch.reviews), oldest, newest))
        if not monotonic:
            warn("RECENCY WAS REQUESTED AND NOT DELIVERED. The dates returned "
                 "are not in descending order, so this batch is a RANKED "
                 "sample, not a recency sample (CORE-PROMPT §4). Recorded with "
                 "sort_verified=false and sort_produces_series=false.")
            if args.require_recency:
                die("--require-recency was given and recency was not delivered "
                    "on the brand route. Refusing to write a batch that would "
                    "enter a series under a sort order it does not have.")
    elif not SORTS[args.sort]["produces_series"]:
        warn("%s is not a recency sort. %s" % (args.sort, SORTS[args.sort]["note"]))

    stats = merge_batch(outdir, args.group, slug, batch, batch_id, args.sort,
                        sort_verified, family=family)
    rec = record_batch(outdir, args.group, member, None, batch, cadence,
                       batch_id, args.sort, sort_verified, inversions, oldest,
                       newest, stats, stage_note, None, pkey=tracked,
                       family=family, family_verified=family_verified,
                       family_detail=family_detail)
    man = update_manifest(outdir, args.group, member, cadence, rec)

    print("\nrequested          %s"
          % (rec["requested"] if rec["requested"] is not None
             else "all (walked to exhaustion; Offset is not capped here)"))
    print("returned           %s" % rec["returned"])
    notes = batch.raw_meta.get("normalise_notes") or {}
    if notes.get("dropped_star_only_no_text"):
        print("star-only dropped  %d (no text; this pipeline cannot score them)"
              % notes["dropped_star_only_no_text"])
    for k in ("dropped_no_review_id", "dropped_unparseable_date"):
        if notes.get(k):
            print("%-18s %d" % (k.replace("dropped_", "dropped "), notes[k]))
    print("claimed_total      %s  (basis: %s, from %r)"
          % (rec["claimed_total"], rec["claimed_total_basis"],
             rec["claimed_total_source_text"]))
    if rec["coverage_ratio"] is None:
        warn("NO COVERAGE RATIO: the response stated no total, so collected-"
             "over-claimed cannot be computed. §4 requires that ratio beside "
             "every tone number, so this batch is on record as unable to "
             "supply it.")
    else:
        print("coverage_ratio     %.6f  (%.4f%% of %d, %s basis)"
              % (rec["coverage_ratio"], 100.0 * rec["coverage_ratio"],
                 rec["claimed_total"], rec["claimed_total_basis"]))

    # LIVENESS, reported separately from depth because they are separate
    # questions and a deep dead deployment passes every other check here.
    age = rec["newest_review_age_days"]
    if age is None:
        warn("the newest review has no readable date, so this deployment's "
             "LIVENESS is unknown. Depth and liveness are separate: a "
             "deployment can be deep and dead.")
    else:
        print("newest review      %s (%d day%s old)"
              % (rec["newest_review_date"], age, "" if age == 1 else "s"))
        if age > BRAND_STALE_DAYS:
            warn("THIS DEPLOYMENT LOOKS DEAD. Its newest review is %d days old, "
                 "past the %d-day mark. Depth is not liveness: Owala serves "
                 "14,029 reviews flawlessly and its newest is 2022-07-28, 1,540 "
                 "days stale. A cadence run against a dead deployment collects "
                 "nothing new for ever and every other number here stays "
                 "healthy while it does." % (age, BRAND_STALE_DAYS))

    syn = sum(1 for r in batch.reviews if r.get("syndicated"))
    if syn:
        print("syndicated         %d of %d (recorded per review, not filtered; "
              "whether to include them is an aggregation decision)"
              % (syn, len(batch.reviews)))
    print("provider cost      $%.2f  (a zero is a measurement, not a missing "
          "field)" % (rec["provider_cost"] or 0.0))
    print("\nnew                %d" % rec["merge"]["new"])
    print("already held       %d  (deduped by review identifier)"
          % rec["merge"]["duplicates"])
    if rec["merge"]["changed"]:
        print("re-observed        %d with a changed field (logged, first kept)"
              % rec["merge"]["changed"])
    print("reviews.csv        %d rows" % rec["merge"]["total_after"])
    print("\nbatch              %s -> %s"
          % (batch_id, batches_path(outdir, args.group, slug)))
    print("reviews            %s" % reviews_path(outdir, args.group, slug))
    print("manifest           %s" % man)
    print("\nNo tone number is computed here and none may be inferred from this "
          "output (CORE-PROMPT §1.2). Next: score_reviews.py.")
    return 0


def cmd_brand(args):
    cls = PLATFORMS.get(args.platform)
    if cls is None:
        die("unknown brand platform %r; one of %s"
            % (args.platform, "/".join(sorted(PLATFORMS))))
    route = cls(client=args.client or "", site=args.site, locale=args.locale,
                passkey=args.passkey, max_pages=args.max_pages)
    return cmd_collect(args, route,
                       "Route C: %s, the brand's own review platform, read "
                       "through the display key its CDN publishes to every "
                       "visitor. No account, no cap, $0." % args.platform)


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
    if args.provider == "apify":
        kw["max_charge_usd"] = args.max_charge_usd
        kw["price_per_review"] = args.price_per_review
        kw["max_pages"] = args.max_pages
        kw["date_from"] = args.date_from
        kw["date_to"] = args.date_to
        if args.actor:
            kw["actor"] = args.actor
    if args.provider == "apify" and getattr(args, "dataset_id", None):
        route = ApifyDatasetRoute(args.dataset_id, **kw)
        return cmd_collect(args, route,
                           "Route B: re-parse of stored Apify dataset %s (free)"
                           % args.dataset_id)
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
            key = product_key(pr)
            prroute = product_route(pr)
            mine = [b for b in batches
                    if str(b.get("product_key") or b.get("asin") or "").upper()
                    == key.upper()]
            ok = [b for b in mine if not b.get("blocked")]
            blocked = [b for b in mine if b.get("blocked")]
            # Counted on the FAMILY, the join key for both routes. Counting on
            # the tracked id reads zero for every sibling in a pooled family.
            # Compared case-insensitively, DISPLAYED as declared. An ASIN is
            # conventionally upper case; a family label is a label, and
            # shouting it would misrepresent what the group file says.
            jk = str(pr.get("family") or pr.get("parent_asin") or key)
            n = sum(1 for r in rows.values()
                    if str(r.get("family") or r.get("parent_asin")
                           or r.get("asin") or "").upper() == jk.upper())
            print("  %-12s %-30s %d batch%s (%d blocked), %d review(s) [%s]"
                  % (key, (pr.get("name") or "")[:30], len(ok),
                     "" if len(ok) == 1 else "es", len(blocked), n, prroute))
            if ok:
                last = ok[-1]
                # The most recent batch that actually CHECKED, not simply the
                # most recent batch. A re-parse makes no network call and
                # verifies nothing, so reading the last batch would report a
                # verified family as unverified the moment one was re-parsed -
                # and §5 cuts both ways: an unchecked batch must not look
                # checked, and a checked one must not be lost behind an
                # unchecked one.
                # Each route names its own population check: the brand route
                # writes `family_verified`, the Amazon routes already wrote
                # `parent_asin_matches_declared`. Reading only the former
                # reported every Amazon product as NEVER CHECKED, which is the
                # §5 rule inverted - a check that DID run reading as one that
                # never did is just as misleading as the reverse.
                vfield = ("family_verified" if prroute != "amazon"
                          else "parent_asin_matches_declared")
                checked = [b for b in ok if b.get(vfield) is not None]
                fv = checked[-1].get(vfield) if checked else None
                when = (checked[-1].get("collected_at") or "")[:10] if checked else None
                print("    family           %s · verified: %s"
                      % (jk, ("yes, %s" % when) if fv is True
                         else (("NO, %s" % when) if fv is False
                               else "NEVER CHECKED")))
                age = last.get("newest_review_age_days")
                if age is not None:
                    print("    newest review    %s (%d days old)%s"
                          % (last.get("newest_review_date"), age,
                             "  <- LOOKS DEAD" if age > BRAND_STALE_DAYS else ""))
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
                               if str(r.get("family") or r.get("parent_asin")
                                      or r.get("asin") or "").upper() == jk)
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

        check("an unfiltered batch is recognised as the population",
              _is_unfiltered({"star": "all_stars"}) and _is_unfiltered({}))
        check("a star-filtered batch is NOT treated as the population",
              not _is_unfiltered({"star": "one_star"}))

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
        check("series coverage counts on the parent, not the declared variant",
              sum(1 for r in rows.values()
                  if (r.get("parent_asin") or "").upper() == "B000PARENT")
              == len(rows))
        check("batch_id joins a review to its batch record", "batch_id" in cols)
        check("route is per review, not per file", "route" in cols)
        check("the family is a column, and it is the join key",
              "family" in cols)
        check("on an Amazon route the family IS the parent asin",
              all(r.get("family") == r.get("parent_asin") for r in rows.values()))
        check("the brand-route identifiers exist and are EMPTY on an Amazon row",
              "client" in cols and "product_id" in cols
              and all(not r.get("client") and not r.get("product_id")
                      for r in rows.values()))
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

        print("\n11b · every remaining refusal has a case that trips it (§5)")
        # Written against refusal_audit.py's findings. §5: a check that always
        # passes is indistinguishable from a check that passes, so each guard
        # below needs a case that demonstrably fires it - not a case that
        # merely exercises the function containing it.
        def dies(fn, *a, **kw):
            try:
                fn(*a, **kw)
                return False
            except SystemExit as e:
                return (e.code or 0) != 0

        bad = os.path.join(tmp, "bad.jsonl")
        open(bad, "w", encoding="utf-8").write("{not json\n")
        check("malformed jsonl is refused, not skipped", dies(read_jsonl, bad))

        r6 = os.path.join(tmp, "proj6")
        write_json(os.path.join(r6, GROUPS_DIR, "g.json"),
                   {"group_id": "g", "members": []})
        check("a group with no members is refused", dies(load_group, r6, "g"))
        check("a missing group file is refused", dies(load_group, r6, "nope"))

        g = load_group(root, "testgroup")
        check("--company naming a company not in the group is refused",
              dies(group_member, g, "not-a-member", None))
        check("an ASIN no member tracks is refused",
              dies(group_member, g, None, "B0NOTHING1"))
        two = json.loads(json.dumps({k: v for k, v in g.items() if k != "_path"}))
        two["members"].append(json.loads(json.dumps(two["members"][0])))
        two["members"][1]["company"] = "Other Co"
        two["members"][1]["company_slug"] = "other-co"
        two["_path"] = g["_path"]
        check("an ASIN tracked by two members needs --company",
              dies(group_member, two, None, "B000TEST01"))

        r7 = os.path.join(tmp, "proj7")
        _mkgroup(r7)
        check("a group with no declared cadence is refused",
              dies(load_cadence, r7, "testgroup"))
        check("--show with no cadence declared is refused",
              dies(cmd_plan, _args(root=r7, group="testgroup", cadence=None,
                                   reason=None, show=True)))
        check("an unknown cadence is refused even bypassing argparse",
              dies(cmd_plan, _args(root=r7, group="testgroup", cadence="hourly",
                                   reason=None, show=False)))
        check("changing a declared cadence without a reason is refused",
              dies(cmd_plan, _args(root=root, group="testgroup",
                                   cadence="daily", reason=None, show=False)))
        check("a spend cap below the actor's floor is refused before the run",
              dies(ApifyRoute(token="x", actor="junglee~amazon-reviews-scraper",
                              max_charge_usd=0.2).collect, "B0", "recent", 10, {}))
        check("an unknown star filter is refused before the run is paid for",
              dies(ApifyRoute(token="x").star_param, "six_star"))
        check("an unknown provider is refused even bypassing argparse",
              dies(cmd_fetch, _args(provider="nosuch", group="testgroup",
                                    root=root, outdir=outdir, plan="free",
                                    actor=None, asin="B000TEST01")))

        sr = SessionRoute(os.path.join(tmp, "does-not-exist.json"))
        check("a missing capture file is refused",
              dies(sr.collect, "B000TEST01", "recent", 10, {}))
        lst = os.path.join(tmp, "list.json")
        write_json(lst, [cap])
        check("a capture file holding a LIST of pages is refused",
              dies(SessionRoute(lst).collect, "B000TEST01", "recent", 10, {}))
        v9 = os.path.join(tmp, "v9.json")
        write_json(v9, dict(cap, capture_version="9"))
        check("an unknown capture_version is refused",
              dies(SessionRoute(v9).collect, "B000TEST01", "recent", 10, {}))
        check("a capture for a different product than asked for is refused",
              dies(SessionRoute(good).collect, "B0OTHERASN", "recent", 10, {}))

        root8 = os.path.join(tmp, "proj8")
        _mkgroup(root8)
        _mkcadence(root8)
        g8 = json.load(open(os.path.join(root8, GROUPS_DIR, "testgroup.json"),
                            encoding="utf-8"))
        g8["members"].append({"company": "Empty Co", "company_slug": "empty-co",
                              "products": []})
        write_json(os.path.join(root8, GROUPS_DIR, "testgroup.json"), g8)
        rc = _subcall([sys.executable, os.path.abspath(__file__), "capture",
                       "--group", "testgroup", "--root", root8,
                       "--outdir", os.path.join(root8, "data"),
                       "--capture", good, "--asin", "B000TEST01",
                       "--company", "empty-co"], tmp)
        check("an ASIN not listed under the named company is refused",
              rc != 0, "exit %s" % rc)

        # Two reviews, oldest FIRST, so the batch is demonstrably not in
        # descending date order. One review cannot be out of order, which is
        # why the single-review fixture above left this guard unreachable.
        asc_pay = json.loads(json.dumps(pay))
        asc_pay["reviews"].append(json.loads(json.dumps(pay["reviews"][0])))
        asc_pay["reviews"][0]["review_id"] = "R1OLDEST000"
        asc_pay["reviews"][0]["review_date_text"] = "January 2, 2020"
        asc_pay["reviews"][1]["review_id"] = "R2NEWEST000"
        asc_pay["reviews"][1]["review_date_text"] = "March 4, 2026"
        asc = dict(cap, reviews=asc_pay["reviews"], returned=2,
                   payload_sha256=payload_hash(asc_pay))
        ascp = os.path.join(tmp, "ascending.json")
        write_json(ascp, asc)
        rc = _subcall([sys.executable, os.path.abspath(__file__), "capture",
                       "--group", "testgroup", "--root", root3,
                       "--outdir", os.path.join(root3, "data"),
                       "--capture", ascp, "--asin", "B000TEST01"], tmp)
        check("an ascending batch is still WRITTEN by default, flagged not refused",
              rc == 0, "exit %s" % rc)
        rc = _subcall([sys.executable, os.path.abspath(__file__), "capture",
                       "--group", "testgroup", "--root", root3,
                       "--outdir", os.path.join(root3, "data"),
                       "--capture", ascp, "--asin", "B000TEST01",
                       "--require-recency"], tmp)
        check("--require-recency refuses a batch whose dates are not descending",
              rc != 0, "exit %s" % rc)

        print("\n12 · the brand-platform route (Route C), offline")

        # Every check below runs against a FAKE route. The real one would need
        # the network, and a guard tested only against a live deployment is a
        # guard that stops being tested the day the deployment changes.
        bvrows = [
            {"Id": "bv-3", "SubmissionTime": "2026-10-11T08:00:00.000+00:00",
             "Rating": 5, "Title": "Great grill", "ReviewText": "Smokes well.",
             "IsVerifiedPurchaser": True, "TotalPositiveFeedbackCount": 4},
            {"Id": "bv-2", "SubmissionTime": "2025-06-02T08:00:00.000+00:00",
             "Rating": 4, "Title": "Good", "ReviewText": "Holds temperature.",
             "IsSyndicated": True, "SyndicationSource": {"Name": "acehardware"},
             "TotalPositiveFeedbackCount": 0},
            {"Id": "bv-1", "SubmissionTime": "2019-03-20T08:00:00.000+00:00",
             "Rating": 2, "Title": "", "ReviewText": "Auger jammed twice.",
             "TotalPositiveFeedbackCount": 11},
            # dropped: star-only, no text to score
            {"Id": "bv-0", "SubmissionTime": "2024-01-01T00:00:00.000+00:00",
             "Rating": 5, "Title": "", "ReviewText": "   "},
            # dropped: no id, so no dedup key on a route that re-walks the pool
            {"SubmissionTime": "2024-01-01T00:00:00.000+00:00", "Rating": 5,
             "Title": "x", "ReviewText": "y"},
            # dropped: a date that will not parse is never inferred or filled
            {"Id": "bv-x", "SubmissionTime": "not a date", "Rating": 5,
             "Title": "x", "ReviewText": "y"},
        ]
        brows, bnotes = normalise_bazaarvoice(bvrows, "TFB57GLE")
        check("verbatim text survives normalisation unchanged",
              [r["text"] for r in brows]
              == ["Smokes well.", "Holds temperature.", "Auger jammed twice."])
        check("a star-only review with no text is dropped and COUNTED",
              bnotes["dropped_star_only_no_text"] == 1)
        check("a review with no id is dropped, not given a synthetic one",
              bnotes["dropped_no_review_id"] == 1)
        check("an unparseable date is dropped, never inferred or filled",
              bnotes["dropped_unparseable_date"] == 1)
        check("Amazon identifiers are left EMPTY, not filled with product_id",
              all(r["asin"] == "" and r["parent_asin"] == "" for r in brows))
        check("the platform's product id goes in product_id",
              all(r["product_id"] == "TFB57GLE" for r in brows))
        check("syndication is recorded per review, not filtered away",
              [r["syndicated"] for r in brows] == [False, True, False]
              and brows[1]["syndication_source"] == "acehardware")
        check("verified purchase is read from the platform's own field",
              brows[0]["verified_purchase"] is True)

        # The Apify 0%-vs-100% trap, one platform along. Traeger carries NO
        # `IsVerifiedPurchaser` on any of its 1,215 rows; the information is in
        # `Badges.verifiedPurchaser`, whose value is a badge OBJECT. Reading
        # only the top-level field would record 0% verified for a 61%-verified
        # pool - a plausible number, which is the dangerous kind.
        badged, _ = normalise_bazaarvoice([{
            "Id": "b1", "SubmissionTime": "2026-01-01T00:00:00.000+00:00",
            "Rating": 5, "Title": "t", "ReviewText": "x",
            "Badges": {"verifiedPurchaser": {"Id": "verifiedPurchaser"},
                       "incentivizedReview": {"Id": "incentivizedReview"}}}],
            "P")
        check("a verified-purchase BADGE is read when the flat field is absent",
              badged[0]["verified_purchase"] is True)
        check("the incentivized-review badge is recorded, not filtered",
              badged[0]["incentivized"] is True)
        unbadged, _ = normalise_bazaarvoice([{
            "Id": "b2", "SubmissionTime": "2026-01-01T00:00:00.000+00:00",
            "Rating": 5, "Title": "t", "ReviewText": "x", "Badges": {}}], "P")
        check("no badge means not verified and not incentivized",
              unbadged[0]["verified_purchase"] is False
              and unbadged[0]["incentivized"] is False)
        check("a badge sent with a FALSE value does not read as true",
              _badge_true(False) is False and _badge_true({"Id": "x"}) is True
              and _badge_true(None) is False)
        check("helpful votes come from TotalPositiveFeedbackCount",
              [r["helpful_vote"] for r in brows] == [4, 0, 11])
        check("the route kind is brand_platform, not provider",
              route_kind_of("bazaarvoice") == "brand_platform")
        check("an unregistered route name is refused rather than guessed",
              dies(route_kind_of, "nosuchroute"))

        # sort_verified is derived from the DATES. A fake route that returns
        # ascending rows while echoing Sort=SubmissionTime:desc is exactly the
        # case the dates-not-the-parameter rule exists for.
        mono, inv, old, new = check_recency(brows)
        check("descending dates verify as a recency sort", mono and inv == 0)
        mono2, inv2, _, _ = check_recency(list(reversed(brows)))
        check("ascending dates do NOT verify, whatever the sort parameter said",
              not mono2 and inv2 == 2)

        check("newest-review age is measured from the collected date",
              newest_age_days("2022-07-28",
                              datetime(2026, 10, 15, tzinfo=timezone.utc)) == 1540)
        check("a deep-but-dead deployment is past the staleness mark",
              newest_age_days("2022-07-28",
                              datetime(2026, 10, 15, tzinfo=timezone.utc))
              > BRAND_STALE_DAYS)
        check("a live deployment is not", newest_age_days(
            "2026-10-11", datetime(2026, 10, 15, tzinfo=timezone.utc)) == 4)
        check("an unreadable newest date gives None, not a number",
              newest_age_days("not a date") is None)

        class _FakeBV(BazaarvoiceRoute):
            """Controlled id sets, no network. `sets` maps product_id -> ids."""

            def __init__(self, sets, **kw):
                BazaarvoiceRoute.__init__(self, client="fake", **kw)
                self.sets = sets
                self.probed = []

            def id_set(self, product_id, limit=BV_PAGE_LIMIT):
                self.probed.append(product_id)
                v = self.sets.get(product_id)
                if v is None:
                    return None, {"http": 404, "error": "no such product"}
                return set(v), {"http": 200, "returned": len(v)}

        pooled = _FakeBV({"TFB57GLE": ["a", "b", "c"], "TFB78GLE": ["a", "b", "c"]})
        ok, det = verify_family(pooled, {"a", "b", "c"}, "pro-575-780",
                                ["TFB57GLE", "TFB78GLE"], "TFB57GLE")
        check("two products serving one pooled id set verify as one family",
              ok is True and det["probes"][0]["overlap_of_smaller"] == 1.0)
        check("the family is established by COMPARING ID SETS",
              det["method"] == "pairwise review-id set comparison at offset 0"
              and pooled.probed == ["TFB78GLE"])

        # A one-review shift at the live edge must not read as a mismatch.
        shifted = _FakeBV({"A": list("abcdefghij"), "B": list("abcdefghiZ")})
        ok, _ = verify_family(shifted, set("abcdefghij"), "fam", ["A", "B"], "A")
        check("a one-review shift at the live edge still verifies", ok is True)

        # The guard that matters. Two products that do NOT share a pool.
        split = _FakeBV({"A": ["a", "b", "c"], "B": ["x", "y", "z"]})
        check("DISJOINT id sets refuse the batch - the declared family is wrong",
              dies(verify_family, split, {"a", "b", "c"}, "fam",
                   ["A", "B"], "A"))

        lone = _FakeBV({"A": ["a"]})
        ok, det = verify_family(lone, {"a"}, "fam", ["A"], "A")
        check("a singleton family is UNVERIFIED, never verified",
              ok is None and "no sibling" in det["basis"])
        unread = _FakeBV({"A": ["a", "b"]})
        ok, det = verify_family(unread, {"a", "b"}, "fam", ["A", "GONE"], "A")
        check("a sibling that cannot be read leaves the family UNVERIFIED",
              ok is None and det["probes"][0]["comparable"] is False)
        check("never-checked and checked-and-clean do not look alike (§5)",
              verify_family(lone, {"a"}, "f", ["A"], "A")[0] is not
              verify_family(pooled, {"a", "b", "c"}, "f",
                            ["TFB57GLE", "TFB78GLE"], "TFB57GLE")[0])

        check("no Referer and no Origin are ever sent on this route",
              not re.search(r"\"(Referer|Origin)\"",
                            open(os.path.abspath(__file__),
                                 encoding="utf-8").read()))
        check("key discovery accepts gzip, or every regex finds nothing",
              "gzip" in inspect_source(http_get_text)
              and "accept_gzip=True" in inspect_source(http_get_text))
        check("Bazaarvoice's own house passkey is never used as a brand's",
              BV_HOUSE_PASSKEY in inspect_source(BazaarvoiceRoute.discover_passkey)
              or "BV_HOUSE_PASSKEY" in inspect_source(
                  BazaarvoiceRoute.discover_passkey))
        check("the bfd/v1 endpoint is not called from anywhere",
              "bfd/v1" not in re.sub(
                  r"#.*|\"\"\".*?\"\"\"", "",
                  open(os.path.abspath(__file__), encoding="utf-8").read(),
                  flags=re.S))

        check("an unknown brand platform is refused even bypassing argparse",
              dies(cmd_brand, _args(platform="nosuch", group="testgroup",
                                    root=root, outdir=outdir, client=None,
                                    site="main_site", locale="en_US",
                                    passkey=None, max_pages=1,
                                    from_cache=None, product_id="X")))

        empty_cache = os.path.join(tmp, "empty-cache")
        os.makedirs(empty_cache, exist_ok=True)
        check("a re-parse with no cached pages is refused, never re-fetched",
              dies(_FakeBV({}, cache_dir=empty_cache).collect,
                   "TFB57GLE", "recent", 10, {"from_cache": True}))

        # A full brand collection, end to end, against a route that returns
        # fixture pages instead of calling out.
        class _OfflineBV(_FakeBV):
            def discover_passkey(self):
                self.key_meta = {"passkey_source": "fixture"}
                self.passkey = "cafixturekeyfixturekeyfixture"
                return self.passkey

            def collect(self, product_id, sort, limit, filters=None):
                meta = {"platform": "bazaarvoice", "client": self.client,
                        "pages_walked": 1, "raw_results": len(bvrows),
                        "passkey_prefix": "cafi...", "referer_or_origin_sent": False}
                return self._finish(product_id, bvrows, 1215,
                                    dict(filters or {}), utcnow(), meta, None)

        rootb = os.path.join(tmp, "projb")
        _mkbrandgroup(rootb)
        _mkcadence(rootb)
        outb = os.path.join(rootb, "data")
        bargs = _args(group="testgroup", company=None, root=rootb, outdir=outb,
                      product_id="TFB57GLE", sort="recent", star="all_stars",
                      limit=None, require_recency=False, from_cache=None,
                      platform="bazaarvoice", client=None, site="main_site",
                      locale="en_US", passkey=None, max_pages=1)
        broute = _OfflineBV({"TFB57GLE": ["bv-3", "bv-2", "bv-1"],
                             "TFB78GLE": ["bv-3", "bv-2", "bv-1"]})
        rc = cmd_collect(bargs, broute, "self-test")
        check("a brand batch is collected and written", rc == 0, "rc %s" % rc)
        brev = load_reviews(outb, "testgroup", "traeger-fixture")
        check("the reviews reach reviews.csv verbatim",
              len(brev) == 3
              and sorted(r["text"] for r in brev.values())
              == ["Auger jammed twice.", "Holds temperature.", "Smokes well."])
        check("every brand row joins on the FAMILY, not the product id",
              {r["family"] for r in brev.values()} == {"pro-575-780"}
              and {r["product_id"] for r in brev.values()} == {"TFB57GLE"})
        check("the route is stamped per review",
              {r["route"] for r in brev.values()} == {"bazaarvoice"})
        bbat = read_jsonl(batches_path(outb, "testgroup", "traeger-fixture"))
        check("the batch record carries every §3 field", bbat and all(
            k in bbat[-1] for k in
            ("route", "sort_requested", "sort_verified", "date_order",
             "requested", "returned", "claimed_total", "claimed_total_basis",
             "coverage_ratio", "blocked", "block_reason")))
        check("claimed_total basis is `reviews`, not `ratings`",
              bbat[-1]["claimed_total"] == 1215
              and bbat[-1]["claimed_total_basis"] == "reviews")
        check("coverage is collected over the family's own review total",
              bbat[-1]["coverage_ratio"] == round(3 / 1215.0, 6))
        check("sort_verified is derived from the dates and is true here",
              bbat[-1]["sort_verified"] is True
              and bbat[-1]["date_order"]["inversions"] == 0)
        check("the family is recorded as verified, with its evidence",
              bbat[-1]["family"] == "pro-575-780"
              and bbat[-1]["family_verified"] is True
              and bbat[-1]["family_check"]["probes"][0]["shared_ids"] == 3)
        check("the newest review's age is recorded",
              bbat[-1]["newest_review_age_days"] is not None
              and bbat[-1]["newest_review_date"] == "2026-10-11")
        check("cost is recorded as $0 - a zero is a measurement",
              bbat[-1]["provider_cost"] == 0.0
              and bbat[-1]["provider"] == "bazaarvoice")
        check("route_kind is brand_platform, not provider",
              bbat[-1]["route_kind"] == "brand_platform")
        check("the Amazon identifiers are null, not filled with brand values",
              bbat[-1]["asin"] is None and bbat[-1]["parent_asin"] is None)

        # Re-collecting the same pool must dedupe rather than duplicate: this
        # route re-walks the WHOLE family every cadence, so a naive append
        # would double the corpus on the second run and then kill the scorer,
        # which treats one review_id keying two rows as a hard failure.
        rc2 = cmd_collect(bargs, _OfflineBV(
            {"TFB57GLE": ["bv-3", "bv-2", "bv-1"],
             "TFB78GLE": ["bv-3", "bv-2", "bv-1"]}), "self-test")
        brev2 = load_reviews(outb, "testgroup", "traeger-fixture")
        bbat2 = read_jsonl(batches_path(outb, "testgroup", "traeger-fixture"))
        check("a second walk of the same pool adds nothing",
              rc2 == 0 and len(brev2) == 3
              and bbat2[-1]["merge"]["duplicates"] == 3
              and bbat2[-1]["merge"]["new"] == 0)
        check("the batch log is append-only across both walks", len(bbat2) == 2)

        # A changed text keeps the FIRST and logs the second (§1.3).
        edited = json.loads(json.dumps(bvrows))
        edited[0]["ReviewText"] = "Smokes well, polished a little."
        class _EditedBV(_OfflineBV):
            def collect(self, product_id, sort, limit, filters=None):
                return self._finish(product_id, edited, 1215, {}, utcnow(),
                                    {"pages_walked": 1}, None)
        cmd_collect(bargs, _EditedBV({"TFB57GLE": ["bv-3"], }), "self-test")
        brev3 = load_reviews(outb, "testgroup", "traeger-fixture")
        check("a re-observed review keeps the FIRST text, not the new one",
              brev3["bv-3"]["text"] == "Smokes well.")
        obs = read_jsonl(observations_path(outb, "testgroup", "traeger-fixture"))
        check("the changed text is logged rather than merged or discarded",
              any(o.get("review_id") == "bv-3"
                  and "text" in (o.get("diffs") or {}) for o in obs))

        # The three declaration refusals, each tripped on its own.
        def _brand_rc(mutate):
            rt = os.path.join(tmp, "p-" + hashlib.sha1(
                repr(mutate).encode()).hexdigest()[:8])
            _mkbrandgroup(rt)
            _mkcadence(rt)
            gp = os.path.join(rt, GROUPS_DIR, "testgroup.json")
            g = json.load(open(gp, encoding="utf-8"))
            mutate(g["members"][0]["products"][0])
            write_json(gp, g)
            return dies(cmd_collect, _args(
                group="testgroup", company=None, root=rt,
                outdir=os.path.join(rt, "data"), product_id="TFB57GLE",
                sort="recent", star="all_stars", limit=None,
                require_recency=False, from_cache=None), broute, "self-test")

        check("a brand product with no `client` is refused",
              _brand_rc(lambda p: p.pop("client")))
        check("a brand product with no `family` is refused - the join key",
              _brand_rc(lambda p: p.pop("family")))
        check("a family that does not contain its own product is refused",
              _brand_rc(lambda p: p.update(family_product_ids=["TFB78GLE"])))

        # A product declared on one route may not be collected on another.
        check("collecting an Amazon-declared product on the brand route is refused",
              dies(cmd_collect, _args(
                  group="testgroup", company=None, root=root, outdir=outdir,
                  product_id="B000TEST01", asin=None, sort="recent",
                  star="all_stars", limit=None, require_recency=False,
                  from_cache=None), broute, "self-test"))

        # --require-recency on the brand route needs an ASCENDING brand batch;
        # the descending fixture above cannot trip it, which is the same shape
        # as the Amazon guard that had never once run.
        class _AscBV(_OfflineBV):
            def collect(self, product_id, sort, limit, filters=None):
                return self._finish(product_id, list(reversed(bvrows)), 1215,
                                    {}, utcnow(), {"pages_walked": 1}, None)
        rootr = os.path.join(tmp, "projr")
        _mkbrandgroup(rootr)
        _mkcadence(rootr)
        check("--require-recency refuses an ascending BRAND batch",
              dies(cmd_collect, _args(
                  group="testgroup", company=None, root=rootr,
                  outdir=os.path.join(rootr, "data"), product_id="TFB57GLE",
                  sort="recent", star="all_stars", limit=None,
                  require_recency=True, from_cache=None),
                  _AscBV({"TFB57GLE": ["bv-3"]}), "self-test"))

        # The rename must not blank the route on reviews already held.
        legacy = os.path.join(tmp, "legacy", "g", "co")
        os.makedirs(legacy, exist_ok=True)
        with open(os.path.join(legacy, "reviews.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "review_id", "asin", "parent_asin", "review_date",
                "collection_route"])
            w.writeheader()
            w.writerow({"review_id": "R9", "asin": "B0CHILD",
                        "parent_asin": "B0PARENT", "review_date": "2025-01-01",
                        "collection_route": "apify"})
        lrows = load_reviews(os.path.join(tmp, "legacy"), "g", "co")
        check("a pre-rename reviews.csv reads `collection_route` as `route`",
              lrows["R9"]["route"] == "apify"
              and "collection_route" not in lrows["R9"])
        check("a pre-family reviews.csv back-fills the join key from parent_asin",
              lrows["R9"]["family"] == "B0PARENT")

        print("\n13 · this stage computes no tone (§1.2, §5)")
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


class _args(object):
    """A bare argv stand-in, for tripping guards argparse would catch first.

    Those guards are not redundant - argparse only protects the CLI path, and a
    guard nothing can reach is exactly what §5 forbids shipping. Calling the
    command functions directly is what makes them testable instead of either
    unreachable or deleted.
    """

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


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


def inspect_source(obj):
    """The source of one function, for the checks that assert on HOW it works.

    Used for the two invariants that have no observable output: that key
    discovery accepts gzip, and that no Referer or Origin is ever sent. Both
    are absences, and an absence cannot be asserted by calling the thing.
    """
    import inspect
    try:
        return inspect.getsource(obj)
    except (OSError, TypeError):
        return ""


def _mkbrandgroup(root):
    """A group declaring ONE brand-platform product, with its family.

    Mirrors the Traeger declaration measured 2026-10-15: TFB57GLE (Pro 575)
    and TFB78GLE (Pro 780) are different grills at different prices serving one
    pooled review set, so they are one family with one representative.
    """
    write_json(os.path.join(root, GROUPS_DIR, "testgroup.json"),
               {"group_id": "testgroup", "declared_at": "2026-10-15",
                "seed": "fixture", "hash": "fixture",
                "members": [{"company": "Traeger Fixture",
                             "company_slug": "traeger-fixture",
                             "venue": "NYSE:COOK", "brands": ["Traeger"],
                             "concentration": {"dominant_line": "pellet grills",
                                               "share_of_revenue": 1.0,
                                               "evidence": "fixture",
                                               "source_url": "fixture"},
                             "products": [{"route": "bazaarvoice",
                                           "client": "traegerservice",
                                           "site": "main_site",
                                           "product_id": "TFB57GLE",
                                           "family": "pro-575-780",
                                           "family_product_ids":
                                               ["TFB57GLE", "TFB78GLE"],
                                           "name": "Pro 575"}],
                             "reporting": {"calendar": "quarterly",
                                           "currency": "USD", "fy_end": "1231",
                                           "source": "SEC"}}]})


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

    def common(sp, collecting=True, ident="asin", limit_default=10):
        # CONTRACTS.md §4: every stage is invoked as
        # `script --group <group_id> [--company <slug>]`, and no stage reads a
        # path it was not given.
        sp.add_argument("--group", required=True, metavar="GROUP_ID")
        sp.add_argument("--company", metavar="SLUG",
                        help="disambiguates when two members track one product")
        sp.add_argument("--root", default=".",
                        help="project root holding groups/ and the cadence plan")
        sp.add_argument("--outdir", default="data")
        if collecting:
            # One identifier per route, named for what it actually is. A brand
            # platform has no ASINs, and calling its product id `--asin` would
            # put a non-Amazon identifier in an Amazon-shaped field - the same
            # fabrication the `asin` column refuses at write time.
            if ident == "asin":
                sp.add_argument("--asin", required=True, metavar="ASIN")
            else:
                sp.add_argument("--product-id", required=True, dest="product_id",
                                metavar="PRODUCT_ID",
                                help="the brand platform's own product id, e.g. "
                                     "TFB57GLE. ONE representative per review "
                                     "family")
            sp.add_argument("--sort", choices=sorted(SORTS), default="recent",
                            help="recent is the only sort that can yield a series")
            sp.add_argument("--star", default="all_stars",
                            help="filter combination; the 10-page cap applies "
                                 "PER combination, so a 1-5 fanout reaches ~5x "
                                 "a single pass")
            sp.add_argument("--page", type=int, default=1)
            # 10 on the Amazon routes, because that is about what a page
            # serves and what a provider's free tier returns. 0 on the brand
            # route, meaning WALK TO EXHAUSTION: Offset is not capped there,
            # depth is the entire reason the route exists, and inheriting a
            # limit of 10 would have quietly turned a 1,215-review family into
            # the same thin slice Amazon already gives for free.
            sp.add_argument("--limit", type=int, default=limit_default,
                            help="requested, recorded against returned"
                                 + (("; 0 walks to exhaustion"
                                     if limit_default == 0 else "")))
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
    sp.add_argument("--max-charge-usd", type=float, default=1.0,
                    help="hard spend cap sent to the provider; the run is "
                         "stopped rather than billed past it")
    sp.add_argument("--dataset-id",
                    help="re-parse a stored Apify dataset instead of running "
                         "the actor again; costs nothing")
    sp.add_argument("--date-from", help="window start (actors that support it)")
    sp.add_argument("--date-to", help="window end (actors that support it)")
    sp.add_argument("--max-pages", type=int, default=10,
                    help="depth in Amazon review pages (~10 reviews each); "
                         "what actually bounds how far back a run reaches")
    sp.add_argument("--price-per-review", type=float,
                    help="the actor's per-review price, recorded with the batch "
                         "so the budget can be reasoned about")

    sp = sub.add_parser("brand", help="Route C: the brand's own review platform")
    common(sp, ident="product_id", limit_default=0)
    sp.add_argument("--platform", default="bazaarvoice", choices=sorted(PLATFORMS))
    sp.add_argument("--client", help="deployment zone; defaults to the group "
                                     "file's `client` for this product")
    sp.add_argument("--site", default="main_site")
    sp.add_argument("--locale", default="en_US")
    sp.add_argument("--passkey",
                    help="skip key discovery and use this display key. For "
                         "re-measuring a zone whose key is already known; the "
                         "route reads it from the brand's own CDN otherwise")
    sp.add_argument("--max-pages", type=int, default=BV_MAX_PAGES,
                    dest="max_pages",
                    help="Offset pages of %d. Offset is NOT capped by the API; "
                         "this bounds our own loop" % BV_PAGE_LIMIT)
    sp.add_argument("--from-cache", dest="from_cache", metavar="STAMP",
                    help="re-parse the raw responses a previous walk stored "
                         "under _raw/<platform>-<STAMP>/ instead of fetching. "
                         "Costs nothing and, because the pool is live, is the "
                         "only way to re-parse the batch actually written")

    sp = sub.add_parser("status", help="coverage and batch history per product")
    common(sp, collecting=False)

    sub.add_parser("self-test", help="offline verification; touches no page")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return 2
    return {"plan": cmd_plan, "extractor": cmd_extractor, "capture": cmd_capture,
            "fetch": cmd_fetch, "brand": cmd_brand, "status": cmd_status,
            "self-test": cmd_selftest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
