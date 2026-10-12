#!/usr/bin/env python3
"""Declare a peer group: substitutable consumer products, anywhere in the world.

CORE-PROMPT §2 is the specification. A peer group is a set of companies whose
consumer products substitute for one another - the Pepsi/Coca-Cola relation -
declared in writing BEFORE any review is collected, with the reason each member
belongs.

This script owns four things the model must never do itself:

  1. COMPLETENESS. A member without brands, without a tracked product, without
     a reason naming what it sells, or without a reporting calendar is not a
     member. The draft is validated whole or it does not freeze.
  2. THE FREEZE. `declare` writes `<group>.vN.json` plus a `.hash` sidecar and
     refuses to overwrite an existing version. A changed group is a NEW version
     carrying the reason and the hash of the version it supersedes, never an
     edit in place (CORE-PROMPT §2, §1.5).
  3. THE ORDER OF OPERATIONS. Every version records how many readings already
     existed for the group when it was declared. A revision declared after tone
     is known is marked `post_collection: true` and carries that mark forever.
     The script cannot stop a late revision; it can stop a silent one.
  4. THE SELECTION BOUNDARY. A reason that selects on performance, price or
     attractiveness is refused, and so is a product justified by popularity
     rather than continuity. The group is about substitutability only.

What it deliberately does NOT do: choose the members. Which companies sell
substitutable products is a judgement made by an agent reading about what the
companies sell (see `.claude/agents/peer-group-proposer.md`), recorded with the
reason it came to. The script only refuses the ones that are inadmissible.

It also never collects a review and never scores tone. Its output is the input
to the collector.

Modes
-----
  draft           skeleton for a seed -> data/peer_groups/<id>.draft.json
  resolve-us      fill CIK + reporting calendar for US registrants from EDGAR
  check-products  resolve each ASIN on its recorded marketplace
  check           validate a draft against the contract; freeze nothing
  declare         validate -> <id>.v1.json + .hash  (refuses to overwrite)
  revise          validate -> <id>.v<N+1>.json with --reason and a hash chain
  show            current version, its hash, and whether the hash still matches
  verify          re-hash every version of every group; detect an edit in place

Standard library only (references/numeric-stack.md §1).
`resolve-us` needs SEC_USER_AGENT; nothing else makes a network call unless
asked.
"""

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain  # noqa: E402

GROUPS_DIR = os.path.join("data", "peer_groups")

# Bumped when the shape of a declaration changes. `verify` hash-checks every
# frozen file but only contract-validates files at the CURRENT schema: a group
# frozen under an older shape is reported as not revalidated rather than as
# broken. Without this, one schema change would make every past declaration
# look like an integrity violation forever, and the real ones would be lost in
# the noise. Schema 1 is the CONTRACTS.md §1 shape (2026-10-12).
SCHEMA_VERSION = 1
READINGS = os.path.join("results", "readings.jsonl")

GROUP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
YEAR_RE = re.compile(r"\b(19[5-9][0-9]|20[0-4][0-9])\b")

SEED_KINDS = ("company", "brand", "category")
CALENDARS = ("quarterly", "half-yearly", "annual", "irregular")
RESOLVED_BY = ("edgar", "agent")

# CONTRACTS.md §1 `reporting.source`. The contract names four; a global group
# needs more, because Sonova files to SIX, Samsung to the Korean DART system and
# Red Bull is an Austrian GmbH with statutory accounts only. Extended rather
# than bent: a value outside this set cannot be recorded, and the four the
# contract names are kept spelled exactly as it spells them. Raised as
# amendment A3 in CHANGELOG.md.
REPORTING_SOURCES = ("SEC", "companies-house", "EDINET", "none",
                     "TDnet", "SIX", "DART", "other-national")

GROUPS_VIEW_DIR = "groups"

# How often every declared parent is re-resolved (CONTRACTS.md §1). A component
# constant rather than a per-group field: this is an operational property of the
# instrument, like the request rate, not something each group declares. If a
# group ever needs its own cadence that is a schema bump, and it should be one.
#
# Seven days is chosen, not measured - nobody knows how often Amazon re-parents.
# It is short enough that a convergence is caught within one collection cycle
# and long enough not to be a second scraping load. Revisit once there is a
# record of how often `reparented` actually appears.
REPARENT_CHECK_DAYS = 7

# SEC's ticker index is keyed on SYMBOL ALONE, so a non-US venue must never be
# looked up in it. "EPA:EL" is EssilorLuxottica in Paris and "EL" is Estee
# Lauder in New York; "EPA:BB" is Societe BIC and "BB" is BlackBerry. Both
# matched, and the screener fetched the wrong companies' 10-Ks under the right
# companies' names (CHANGELOG.md 2026-10-12, defect 11). The exchange is now
# checked before the symbol is, and a venue outside this set is never resolved
# mechanically.
US_EXCHANGES = ("NYSE", "NASDAQ", "NYSEAMERICAN", "NYSEARCA", "AMEX", "BATS",
                "CBOE", "OTC", "OTCMKTS")
VENUE_RE = re.compile(r"^[A-Z0-9.\-]{2,12}:[A-Z0-9.\-]{1,12}$")

# PLAN.md's filter has no number in it, so one is chosen here and recorded.
# 0.60 is deliberately permissive: it admits a company with a clear dominant
# line plus a real secondary business, and excludes the four-segment
# conglomerate the filter exists to keep out. It is the threshold most likely to
# be wrong, and it is one constant so it can be argued with.
CONCENTRATION_FLOOR = 0.60

MIN_REASON_CHARS = 60
MIN_CONTINUITY_CHARS = 30

# Amazon marketplaces, as recorded in a product's `marketplace` field. The host
# is what `check-products` requests. A marketplace absent from this map cannot
# be recorded, because a batch whose marketplace cannot be resolved cannot say
# where it sampled from (CORE-PROMPT §1.4).
MARKETPLACES = {
    "amazon.com": "www.amazon.com",
    "amazon.co.uk": "www.amazon.co.uk",
    "amazon.de": "www.amazon.de",
    "amazon.fr": "www.amazon.fr",
    "amazon.it": "www.amazon.it",
    "amazon.es": "www.amazon.es",
    "amazon.nl": "www.amazon.nl",
    "amazon.se": "www.amazon.se",
    "amazon.pl": "www.amazon.pl",
    "amazon.co.jp": "www.amazon.co.jp",
    "amazon.ca": "www.amazon.ca",
    "amazon.com.mx": "www.amazon.com.mx",
    "amazon.com.br": "www.amazon.com.br",
    "amazon.in": "www.amazon.in",
    "amazon.com.au": "www.amazon.com.au",
    "amazon.sg": "www.amazon.sg",
    "amazon.ae": "www.amazon.ae",
    "amazon.sa": "www.amazon.sa",
    "amazon.com.tr": "www.amazon.com.tr",
    "amazon.com.be": "www.amazon.com.be",
}

# The question asked of every candidate, in the same words every time. Frozen
# deliberately: the test a company is put to must not vary with how interesting
# the company looks. Changing this wording changes the instrument and belongs in
# CHANGELOG.md.
SUBSTITUTABILITY_TEST = (
    "Would a consumer shopping for this seed's product consider this company's "
    "product instead of it, as a direct alternative for the same need, at the "
    "same point of purchase? If yes, it is a peer. Size, country, listing "
    "venue and currency are irrelevant to the answer."
)

# §2 is about substitutable products. These words select on something else, and
# a reason containing one is refused rather than rewritten, because rewriting it
# here would hide that the judgement was made on the wrong basis.
#
# Phrases, not bare words, wherever a bare word has an innocent use: a reason is
# SUPPOSED to say what a company sells, so "sells", "price" in "price tier" and
# "value" in "value pack" must survive. Each entry is matched on word
# boundaries, case-insensitively, over a dash- and space-normalised string.
PERFORMANCE_LEXICON = (
    "undervalued", "overvalued", "fairly valued", "valuation", "cheap stock",
    "trading at", "price target", "share price", "stock price", "price action",
    "upside", "downside", "outperform", "underperform", "outperformed",
    "underperformed", "beat the market", "market beating", "total return",
    "p e ratio", "price to earnings", "ev ebitda", "free cash flow",
    "revenue growth", "sales growth", "earnings growth", "margin expansion",
    "operating margin", "gross margin", "profitability", "profitable",
    "roic", "return on capital", "bullish", "bearish", "momentum",
    "attractive", "promising", "compelling", "best in class",
    "strong balance sheet", "good investment", "investment case",
    "buy rating", "overweight", "underweight", "well positioned",
    "market leader", "dominant", "wide moat", "pricing power",
)

# §2: products are chosen for continuity, not popularity. A continuity reason
# that argues from rank or volume is arguing the wrong thing - a bestseller that
# is discontinued ends a series just as surely as an obscure item does.
POPULARITY_LEXICON = (
    "bestseller", "best seller", "best selling", "best sellers rank",
    "top seller", "top selling", "most popular", "most reviewed",
    "most sold", "highest rated", "top rated", "best rated", "highly rated",
    "amazon s choice", "amazons choice", "number one", "no 1", "rank",
    "ranked", "trending", "viral", "popular", "popularity", "sales volume",
    "high volume", "many reviews", "review count",
)

# What a continuity reason has to actually assert. Either an anchoring year (an
# item demonstrably on sale since a dated point) or a term that names the item's
# place in the catalogue. A lexicon is gameable by an agent that knows it is
# there; it is here to stop an empty field passing, not to certify a good one.
CONTINUITY_ANCHORS = (
    "flagship", "core line", "core range", "staple", "catalogue staple",
    "catalog staple", "standard pack", "standard size", "continuously",
    "continuous", "replenish", "reorder", "repeat purchase", "long running",
    "long-running", "perennial", "permanent range", "year round",
    "year-round", "no successor", "unchanged", "never discontinued",
    "generational", "multi year", "multi-year", "signature",
)


# ---------------------------------------------------------------- primitives

def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def canon(text):
    """Lower-case, with every dash and run of whitespace folded to one space.

    So "best-seller", "Best Seller" and "best  seller" are the same string to a
    lexicon check, and "p/e ratio" reaches the "p e ratio" entry.
    """
    text = (text or "").lower()
    text = re.sub(r"[‐-―/_,.]", " ", text)
    text = re.sub(r"[^a-z0-9%&+' ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def lexicon_hits(text, lexicon, exempt=()):
    """Every phrase in `lexicon` present in `text` on word boundaries.

    `exempt` names the member's own brands. A phrase that IS one of them is not
    a hit: "Momentum" is Sennheiser's headphone line, and a reason naming it is
    doing exactly what §2 asks - citing what the company sells. Found the hard
    way, on the first real non-US group declared (CHANGELOG.md, 2026-10-11).

    The exemption is narrow on purpose. It only covers that member's declared
    brands, and those brands are themselves checked against the product titles
    the marketplace actually served, so a brand cannot be invented to smuggle a
    performance word past the check.
    """
    hay = " %s " % canon(text)
    spared = {canon(b) for b in exempt if canon(b)}
    hits = []
    for phrase in lexicon:
        if (" %s " % phrase) in hay and phrase not in spared:
            hits.append(phrase)
    return hits


def dumps(obj):
    """The one serialisation. The hash is over these bytes, so it is fixed."""
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def write_json(path, obj):
    raw = dumps(obj).encode("utf-8")
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw)
    return sha256_bytes(raw)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def git_sha(root="."):
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode:
            return None
        sha = out.stdout.strip()
        dirty = subprocess.run(["git", "-C", root, "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10)
        return sha + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return None


def readings_for(group_id, path=READINGS):
    """How many readings already exist for this group.

    The order-of-operations witness. A v1 declared with readings already on
    disk, or a revision declared after collection started, is recorded as such
    (CORE-PROMPT §2: a peer added after seeing a result is not a peer).
    `results/readings.jsonl` is append-only, so this count never falls.
    """
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("peer_group_id") == group_id:
                n += 1
    return n


# ------------------------------------------------------------------ versions

VERSION_RE = re.compile(r"^(?P<gid>[a-z0-9][a-z0-9-]*)\.v(?P<n>\d+)\.json$")


def versions_of(group_id, groups_dir=GROUPS_DIR):
    """[(n, path)] for every frozen version of a group, lowest first."""
    found = []
    if not os.path.isdir(groups_dir):
        return found
    for name in sorted(os.listdir(groups_dir)):
        if os.path.isdir(os.path.join(groups_dir, name)):
            continue
        m = VERSION_RE.match(name)
        if m and m.group("gid") == group_id:
            found.append((int(m.group("n")), os.path.join(groups_dir, name)))
    return sorted(found)


def all_group_ids(groups_dir=GROUPS_DIR):
    ids = set()
    if not os.path.isdir(groups_dir):
        return []
    for name in os.listdir(groups_dir):
        if os.path.isdir(os.path.join(groups_dir, name)):
            continue
        m = VERSION_RE.match(name)
        if m:
            ids.add(m.group("gid"))
    return sorted(ids)


def hash_path(version_path):
    return version_path[:-len(".json")] + ".hash"


def write_hash_file(version_path, digest, group):
    """The sidecar. Carries what a reader needs before opening the JSON.

    Mirrors the archived harness's convention: digest, filename, then the facts
    that decide whether the artefact can be trusted.
    """
    sup = group.get("supersedes") or {}
    n_products = sum(len(m.get("products") or []) for m in group["members"])
    unverified = sum(
        1
        for m in group["members"]
        for p in (m.get("products") or [])
        if (p.get("verification") or {}).get("status") != "resolved"
    )
    line = (
        "%s  %s  group=%s  version=%s  members=%d  products=%d  "
        "unverified=%d  declared=%s  supersedes=%s  "
        "readings_at_declare=%d  post_collection=%s  "
        "peer_relative_reading=%s\n"
        % (
            digest, os.path.basename(version_path), group["group_id"],
            group["version"], len(group["members"]), n_products, unverified,
            group["declared_at"], (sup.get("sha256") or "none")[:12],
            group["readings_at_declare"],
            str(group["post_collection"]).lower(),
            str(group["peer_relative_reading"]).lower(),
        )
    )
    with open(hash_path(version_path), "w", encoding="utf-8") as f:
        f.write(line)
    return line


# ----------------------------------------------------------------- validation

def _s(obj, key):
    v = obj.get(key)
    return v.strip() if isinstance(v, str) else ""


def validate(group, allow_unverified=False):
    """Every reason this draft is not a peer group. Empty list means it is.

    Order is the order a reader would want them in: the group, then each member
    in turn. Nothing here looks at a tone number, a price or a result, because
    nothing here is allowed to.
    """
    p = []

    gid = _s(group, "group_id")
    if not GROUP_ID_RE.match(gid):
        p.append("group_id %r: must match %s" % (gid, GROUP_ID_RE.pattern))

    if not _s(group, "seed"):
        p.append("seed: empty. CONTRACTS.md §1 - the group records what it was "
                 "grown from, as a string")
    if group.get("seed_kind") not in SEED_KINDS:
        p.append("seed_kind %r: must be one of %s"
                 % (group.get("seed_kind"), ", ".join(SEED_KINDS)))
    declared = _s(group, "declared_at")
    if declared and not re.match(r"^\d{4}-\d{2}-\d{2}$", declared):
        p.append("declared_at %r: CONTRACTS.md §1 requires a DATE, never a "
                 "wall-clock during collection" % declared)

    if _s(group, "substitutability_test") != SUBSTITUTABILITY_TEST:
        p.append("substitutability_test: must be the frozen wording verbatim; "
                 "a group tested by a different question is a different instrument")

    members = group.get("members")
    if not isinstance(members, list) or not members:
        p.append("members: none")
        return p

    solo = bool(group.get("solo"))
    if len(members) == 1 and not solo:
        p.append("members: 1. §2 requires at least two. A company with no peer "
                 "is declared with --solo and a reason, and is labelled as "
                 "having no peer-relative reading.")
    if len(members) > 1 and solo:
        p.append("solo: set, but the group has %d members" % len(members))
    if solo:
        if not _s(group, "solo_reason"):
            p.append("solo_reason: required when a company is tracked alone - "
                     "say what was looked for and not found")
        if group.get("peer_relative_reading") is not False:
            p.append("peer_relative_reading: must be false for a solo group")
        for hit in lexicon_hits(group.get("solo_reason"), PERFORMANCE_LEXICON):
            p.append("solo_reason: %r selects on performance, not substitutability" % hit)
    elif group.get("peer_relative_reading") is not True:
        p.append("peer_relative_reading: must be true for a group of two or more")

    seen_company = {}
    seen_asin = {}
    seen_parent = {}
    for i, m in enumerate(members):
        tag = "members[%d]" % i
        if not isinstance(m, dict):
            p.append("%s: not an object" % tag)
            continue
        company = _s(m, "company")
        if not company:
            p.append("%s.company: empty" % tag)
        else:
            tag = "%s(%s)" % (tag, company)
            key = canon(company)
            if key in seen_company:
                p.append("%s.company: duplicate of members[%d]" % (tag, seen_company[key]))
            seen_company[key] = i

        p.extend(_validate_venue(m, tag))
        p.extend(_validate_reporting(m, tag))

        brands = m.get("brands")
        if not isinstance(brands, list) or not [b for b in brands if _s({"b": b}, "b")]:
            p.append("%s.brands: §2 requires the consumer brands it owns that "
                     "are relevant to this group" % tag)
            brands = []
        brands = [b.strip() for b in brands if isinstance(b, str) and b.strip()]

        p.extend(_validate_concentration(m, tag))
        p.extend(_validate_reason(m, tag, brands))
        p.extend(_validate_products(m, tag, brands, seen_asin, seen_parent, i,
                                    allow_unverified))

    return p


def _validate_venue(m, tag):
    """CONTRACTS.md §1: `venue` is "EXCHANGE:TICKER", or the string "private".

    A private company is a full member. §2 and PLAN.md both say so, and for the
    tone comparison a private competitor is the category control - only the
    company whose revenue is eventually wanted has to be public.
    """
    p = []
    venue = _s(m, "venue")
    if not venue:
        p.append('%s.venue: empty. Record "EXCHANGE:TICKER", or "private"' % tag)
        return p
    if venue == "private":
        return p
    if not VENUE_RE.match(venue):
        p.append('%s.venue %r: must be "EXCHANGE:TICKER" (e.g. "NASDAQ:IRBT") '
                 'or exactly "private"' % (tag, venue))
    cik = _s(m, "cik")
    if cik and not re.match(r"^\d{10}$", cik):
        p.append("%s.cik %r: must be 10 digits, zero-padded" % (tag, cik))
    return p


def venue_is_private(m):
    return _s(m, "venue") == "private"


def venue_parts(m):
    """(exchange, ticker) or (None, None) for a private member."""
    venue = _s(m, "venue")
    if venue == "private" or ":" not in venue:
        return None, None
    ex, tk = venue.split(":", 1)
    return ex, tk


def us_ticker(m):
    """The symbol to look up in SEC's index, or None if this is not a US venue.

    The guard that stops a Paris symbol being resolved as the New York company
    that happens to share it.
    """
    ex, tk = venue_parts(m)
    if not tk or (ex or "").upper() not in US_EXCHANGES:
        return None
    return tk.strip().upper()


def _validate_reporting(m, tag):
    """CORE-PROMPT §5: record the calendar and the currency, do not normalise.

    The point of this block is that a later stage CANNOT silently align two
    members' figures, because it has to read what calendar each one is on
    first.
    """
    p = []
    rep = m.get("reporting")
    if not isinstance(rep, dict):
        p.append("%s.reporting: missing. §5 requires the reporting calendar and "
                 "currency per company" % tag)
        return p
    if rep.get("calendar") not in CALENDARS:
        p.append("%s.reporting.calendar %r: must be one of %s"
                 % (tag, rep.get("calendar"), ", ".join(CALENDARS)))
    if rep.get("source") not in REPORTING_SOURCES:
        p.append("%s.reporting.source %r: CONTRACTS.md §1 - must be one of %s"
                 % (tag, rep.get("source"), ", ".join(REPORTING_SOURCES)))
    if not _s(rep, "regime"):
        p.append("%s.reporting.regime: empty (e.g. 'SEC 10-Q', 'SIX half-year "
                 "to 30 September', 'TDnet quarterly / EDINET'). The contract's "
                 "`source` says which system; `regime` says what it files and "
                 "how often, which is what §5 needs" % tag)
    cur = _s(rep, "currency")
    if not CURRENCY_RE.match(cur):
        p.append("%s.reporting.currency %r: must be a 3-letter ISO 4217 code. "
                 "§5 forbids silent conversion, so the native currency is "
                 "recorded even when it is awkward" % (tag, cur))
    readable = (m.get("concentration") or {}).get("revenue_readable") is not False
    fy = _s(rep, "fy_end")
    if readable and not re.match(r"^(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])$", fy):
        p.append("%s.reporting.fy_end %r: CONTRACTS.md §1 requires MMDD for any "
                 "member whose revenue will be read. §5 forbids aligning two "
                 "companies' figures as if their calendars matched, and this is "
                 "the field that says they do not" % (tag, fy))
    elif not readable and fy:
        p.append("%s.reporting.fy_end %r: set on a member with no public "
                 "reporting. Nothing publishes a year end for it, so empty is "
                 "the honest value" % (tag, fy))
    if not _s(rep, "calendar_source"):
        p.append("%s.reporting.calendar_source: empty. Say where the calendar "
                 "was read from, so a wrong one can be traced" % tag)
    if rep.get("resolved_by") not in RESOLVED_BY:
        p.append("%s.reporting.resolved_by %r: must be one of %s"
                 % (tag, rep.get("resolved_by"), ", ".join(RESOLVED_BY)))
    return p


def _validate_concentration(m, tag):
    """PLAN.md's concentration filter. CONTRACTS.md §1: no evidence, no member.

    Review tone on a handful of products can only say something about a
    company's revenue if those products ARE most of the revenue. A company with
    800 SKUs across four reportable segments can have its reviews sour while
    revenue is driven by something nobody reviews.

    The filter is about where the revenue comes from, which is a hair's breadth
    from the performance language §2 forbids - so the distinction is held
    mechanically. `dominant_line` and `share_of_revenue` are structured fields
    about WHAT is sold, not free prose, and the performance lexicon is still
    applied to `evidence`. A concentration note may say "92% of revenue is
    robot vacuums"; it may not say the line is growing, profitable or
    attractive.

    `share_of_revenue` is nullable, because plenty of focused companies do not
    disclose a split - a single-segment filer's "100%" is an inference, not a
    disclosure. Null with real evidence is admissible and honest; a made-up
    number is neither.
    """
    p = []
    c = m.get("concentration")
    if not isinstance(c, dict):
        p.append("%s.concentration: missing. CONTRACTS.md §1 - a member with no "
                 "concentration evidence is not a member" % tag)
        return p
    if not _s(c, "dominant_line"):
        p.append("%s.concentration.dominant_line: empty. Name the product line "
                 "that dominates revenue, in the words the filing uses" % tag)

    # CONTRACTS.md §1 holds two rules that collide on a private company: every
    # member needs concentration evidence from its own filing, AND a private
    # member is valid and useful as a category control. Bose, Stanley/PMI and
    # DJI publish no financial report at all, so the first rule would delete
    # the members the second one asks for.
    #
    # Resolved by asking what the evidence is FOR. Concentration exists so that
    # a tone series can be connected to a revenue series. A private member has
    # no revenue series to connect to - it is in the group to tell a
    # category-wide tone move from a company-specific one, and that job needs
    # no filing. So a private member may declare `no_public_report: true`, which
    # requires saying what it sells and where that was established, and marks
    # the member as one whose revenue is never readable. A PUBLIC member is
    # never exempt. Raised as amendment A1 in CHANGELOG.md.
    no_report = bool(c.get("no_public_report"))
    if no_report and not venue_is_private(m):
        p.append("%s.concentration.no_public_report: set on a LISTED member. "
                 "Only a private company may use it; a listed one files" % tag)
        no_report = False

    if not _s(c, "evidence"):
        p.append("%s.concentration.evidence: empty. Cite the company's own "
                 "filing - segment disclosure or revenue by product category. "
                 "PLAN.md: never asserted" % tag)
    url = _s(c, "source_url")
    if not url and not no_report:
        p.append("%s.concentration.source_url: empty" % tag)
    elif url and not url.startswith("https://"):
        p.append("%s.concentration.source_url %r: must be an https URL to the "
                 "filing or report itself" % (tag, url[:60]))
    if no_report:
        if c.get("share_of_revenue") is not None:
            p.append("%s.concentration.share_of_revenue: set alongside "
                     "no_public_report. A company that publishes no report "
                     "cannot have a disclosed share; null is the honest value"
                     % tag)
        if c.get("revenue_readable") is not False:
            p.append('%s.concentration.revenue_readable: must be false when '
                     "no_public_report is set. The member contributes tone and "
                     "a category control, and no revenue series" % tag)

    share = c.get("share_of_revenue")
    if share is not None:
        if not isinstance(share, (int, float)) or isinstance(share, bool):
            p.append("%s.concentration.share_of_revenue %r: a fraction in "
                     "(0, 1], or null when the company does not disclose a "
                     "split" % (tag, share))
        elif not 0 < share <= 1:
            p.append("%s.concentration.share_of_revenue %r: a FRACTION in "
                     "(0, 1], not a percentage" % (tag, share))
        elif share < CONCENTRATION_FLOOR:
            p.append("%s.concentration.share_of_revenue %.2f is below the %.2f "
                     "floor. PLAN.md: a company whose dominant line is a "
                     "minority of revenue cannot have its revenue read from "
                     "that line's reviews.\n      The line is defined at the "
                     "level the TRACKED PRODUCTS sit at, so the fix may be to "
                     "widen it rather than drop the member: YETI's drinkware "
                     "alone is 58%% of net sales and fails, while the "
                     "insulated drinkware and coolers it actually sells on "
                     "Amazon are 98%% and pass. Widening is legitimate only if "
                     "the tracked products span the wider line."
                     % (tag, share, CONCENTRATION_FLOOR))
    # The evidence is a quotation from a filing, not a case for the company.
    # `evidence` is a CITATION - which filing, which table - not a case for the
    # company. The full performance lexicon applies, with no exemption beyond
    # the member's brands: a citation never needs the word "margin" in it, and
    # a concentration note that reaches for one has stopped citing and started
    # arguing.
    for hit in lexicon_hits(c.get("evidence"), PERFORMANCE_LEXICON,
                            exempt=m.get("brands") or []):
        p.append("%s.concentration.evidence: %r reads as a performance claim. "
                 "The evidence cites which filing and which table said what is "
                 "sold and in what proportion, not how the company is doing"
                 % (tag, hit))
    return p


def _validate_reason(m, tag, brands):
    """§2: the reason this company belongs, in a sentence, citing what it sells.

    Two mechanical teeth. The reason must NAME one of the member's own brands,
    which is what makes it a statement about product substitutability rather
    than about the company. And it must not select on performance, which is the
    boundary the whole component exists to hold.
    """
    p = []
    reason = _s(m, "reason")
    if not reason:
        p.append("%s.reason: empty. §2 requires the reason this company belongs, "
                 "citing what it sells" % tag)
        return p
    if len(reason) < MIN_REASON_CHARS:
        p.append("%s.reason: %d chars, under the %d-char floor. A reason too "
                 "short to name a product is not a reason"
                 % (tag, len(reason), MIN_REASON_CHARS))
    if brands:
        hay = canon(reason)
        if not any(canon(b) and canon(b) in hay for b in brands):
            p.append("%s.reason: names none of the member's own brands (%s). "
                     "The reason must cite what it SELLS, not what the company "
                     "is" % (tag, ", ".join(brands)))
    for hit in lexicon_hits(reason, PERFORMANCE_LEXICON, exempt=brands):
        p.append("%s.reason: %r selects on performance, price or attractiveness. "
                 "A peer group is about product substitutability only; rewrite "
                 "the reason around what the company sells, or drop the member"
                 % (tag, hit))
    return p


def _validate_products(m, tag, brands, seen_asin, seen_parent, idx, allow_unverified):
    """§2: the specific product identifiers, chosen for continuity."""
    p = []
    products = m.get("products")
    if not isinstance(products, list) or not products:
        p.append("%s.products: none. §2 requires the specific product "
                 "identifiers to be tracked" % tag)
        return p
    for j, pr in enumerate(products):
        ptag = "%s.products[%d]" % (tag, j)
        if not isinstance(pr, dict):
            p.append("%s: not an object" % ptag)
            continue
        asin = _s(pr, "asin")
        if not ASIN_RE.match(asin):
            p.append("%s.asin %r: must be 10 characters, A-Z0-9" % (ptag, asin))
        elif asin in seen_asin:
            p.append("%s.asin %s: already tracked by members[%d]. One product "
                     "cannot be two companies'" % (ptag, asin, seen_asin[asin]))
        else:
            seen_asin[asin] = idx
        mk = _s(pr, "marketplace")
        if mk not in MARKETPLACES:
            p.append("%s.marketplace %r: unknown. Known: %s"
                     % (ptag, mk, ", ".join(sorted(MARKETPLACES))))
        if not _s(pr, "name"):
            p.append("%s.name: empty. The name at declaration is how a silently "
                     "relisted ASIN is caught later" % ptag)
        brand = _s(pr, "brand")
        if not brand:
            p.append("%s.brand: empty" % ptag)
        elif brands and canon(brand) not in [canon(b) for b in brands]:
            p.append("%s.brand %r: not among the member's declared brands (%s)"
                     % (ptag, brand, ", ".join(brands)))
        p.extend(_validate_continuity(pr, ptag, brands))
        p.extend(_validate_verification(pr, ptag, allow_unverified))
        p.extend(_validate_title_agreement(pr, ptag, brand))

        # Amazon serves reviews at the PARENT asin, pooled across variants, so
        # two members tracking children of one parent would be handed the same
        # reviews and their "peer-relative" tone would be a comparison of a
        # series with itself. Caught here because nothing downstream could tell.
        parent = _s(pr, "parent_asin") or _s(
            (pr.get("verification") or {}), "parent_asin_seen")
        if not parent:
            p.append("%s.parent_asin: empty. CONTRACTS.md §1 requires it - "
                     "Amazon serves reviews at the parent, so this is where the "
                     "reviews actually come from and what the collector joins "
                     "on. `check-products` records it" % ptag)
        if parent:
            if parent in seen_parent and seen_parent[parent] != idx:
                p.append("%s: parent ASIN %s is already tracked by members[%d]. "
                         "Reviews are served at the parent, so both members "
                         "would be scored on the same reviews"
                         % (ptag, parent, seen_parent[parent]))
            else:
                seen_parent[parent] = idx
    return p


def _validate_title_agreement(pr, ptag, brand):
    """Does the page the ASIN served actually look like the declared product?

    The cheap check that catches a transposed identifier - one company's ASIN
    recorded under another's - which no amount of careful prose would catch,
    because the prose and the ASIN are written in different moments. The
    declared brand has to appear in the title the marketplace served.

    It does NOT catch an accessory: "Earpads Replacement for JBL Tour One M3"
    carries the brand too. That one is on the reader of the title, and it is
    why `name` is compared to what was served rather than trusted.
    """
    out = []
    v = pr.get("verification") or {}
    if _s(v, "status") != "resolved":
        return out
    seen = _s(v, "title_seen")
    if not seen:
        out.append("%s.verification: resolved but recorded no title_seen" % ptag)
        return out
    if brand and canon(brand) not in canon(seen):
        out.append("%s: declared brand %r does not appear in the title the "
                   "marketplace served (%r). Either the ASIN belongs to a "
                   "different product or the brand is mis-recorded"
                   % (ptag, brand, seen[:90]))
    declared = _s(pr, "name")
    if declared and canon(declared) != canon(seen):
        out.append("%s.name %r does not match the served title %r. Record what "
                   "the marketplace actually serves, so a silently relisted "
                   "ASIN is visible later" % (ptag, declared[:60], seen[:60]))
    return out


def _validate_continuity(pr, ptag, brands=()):
    """§2: chosen for continuity, not popularity.

    The anchor requirement is a floor, not a certificate: an agent that knows
    the lexicon can satisfy it with a word. It stops an empty justification
    passing, and it makes the basis of the choice legible to whoever reads the
    file in a year when the series breaks.
    """
    p = []
    cr = _s(pr, "continuity_reason")
    if not cr:
        p.append("%s.continuity_reason: empty. §2 chooses products for "
                 "continuity - say why this item will still be sold in a year" % ptag)
        return p
    if len(cr) < MIN_CONTINUITY_CHARS:
        p.append("%s.continuity_reason: %d chars, under the %d-char floor"
                 % (ptag, len(cr), MIN_CONTINUITY_CHARS))
    for hit in lexicon_hits(cr, POPULARITY_LEXICON, exempt=brands):
        p.append("%s.continuity_reason: %r argues from popularity. §2 chooses "
                 "for continuity: a bestseller that is discontinued ends a "
                 "series exactly as an obscure item does" % (ptag, hit))
    anchors = lexicon_hits(cr, CONTINUITY_ANCHORS)
    year = YEAR_RE.search(cr)
    if not anchors and not year:
        p.append("%s.continuity_reason: asserts nothing checkable. Give a date "
                 "the item has been on sale since, or say what place it holds "
                 "in the range (%s...)" % (ptag, ", ".join(CONTINUITY_ANCHORS[:4])))
    return p


def _validate_verification(pr, ptag, allow_unverified):
    """Did the ASIN resolve to a real, currently-sold product?

    An unresolved product may still be declared - refusing would tempt a swap
    for one that happens to resolve, which is selection by availability - but
    only with --allow-unverified, and the count travels in the hash file.
    """
    p = []
    v = pr.get("verification")
    if not isinstance(v, dict) or not _s(v, "status"):
        p.append("%s.verification: missing. Run `check-products` before "
                 "declaring" % ptag)
        return p
    status = _s(v, "status")
    if status not in ("resolved", "not_found", "blocked", "unavailable", "error"):
        p.append("%s.verification.status %r: unknown" % (ptag, status))
    if not _s(v, "checked_at"):
        p.append("%s.verification.checked_at: empty" % ptag)
    if not _s(v, "route"):
        p.append("%s.verification.route: empty. §1.4 - a check that cannot say "
                 "how it was made is not evidence" % ptag)
    if status != "resolved" and not allow_unverified:
        p.append("%s.verification.status is %r, not 'resolved'. Either fix the "
                 "identifier or declare with --allow-unverified, which records "
                 "the count in the hash file" % (ptag, status))
    return p


# -------------------------------------------------------------------- EDGAR

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_last_call = [0.0]


def sec_ua():
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not agent or "@" not in agent:
        sys.exit(
            "SEC_USER_AGENT is not set.\n"
            "SEC requires a contact in the User-Agent header. Set it to your\n"
            "own name and email - this script will not guess one for you:\n\n"
            '    export SEC_USER_AGENT="Your Name your@email.example"\n'
        )
    return agent


def http_get(url, headers, rate=0.3, retries=3, timeout=45):
    """Rate-limited GET. Returns (status, body) or (status, None) on 404.

    Shared by the EDGAR and the marketplace callers so that neither can pace
    itself differently by accident.
    """
    for attempt in range(retries):
        wait = rate - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return r.status, raw
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return e.code, None
            # 503 from a marketplace is bot protection, not a transport fault.
            # CORE-PROMPT §3: record it and stop. Never retry around a block.
            if e.code in (403, 429, 503):
                return e.code, None
            if attempt == retries - 1:
                return e.code, None
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == retries - 1:
                return 0, None
            time.sleep(2 ** attempt)
    return 0, None


def sec_json(url):
    status, raw = http_get(url, {
        "User-Agent": sec_ua(),
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json",
    })
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


_ticker_index = [None]


def ticker_index():
    if _ticker_index[0] is None:
        data = sec_json(TICKERS_URL)
        idx = {}
        if data:
            fields = data.get("fields") or []
            try:
                ci, ti, ei = (fields.index("cik"), fields.index("ticker"),
                              fields.index("exchange"))
            except ValueError:
                ci = ti = ei = None
            if ci is not None:
                for row in data.get("data") or []:
                    tk = (row[ti] or "").strip().upper()
                    if tk:
                        idx.setdefault(tk, ("%010d" % int(row[ci]),
                                            (row[ei] or "").strip()))
        _ticker_index[0] = idx
    return _ticker_index[0]


def us_reporting(cik):
    """Reporting calendar for a US registrant, from its own filing history.

    Read rather than assumed: a 10-Q filer is quarterly, a 20-F/40-F filer is
    an annual SEC filer whose home-market calendar is something else entirely
    and is NOT resolved here - that one is left to the agent, which is the
    honest answer (§5).
    """
    data = sec_json(SUBMISSIONS_URL.format(cik=cik))
    if not data:
        return None
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    seen = set(forms)
    out = {
        "entity_name": data.get("name"),
        "fy_end": data.get("fiscalYearEnd"),
        "sic_description": data.get("sicDescription"),
        "forms_seen": sorted({f for f in seen if f in
                              ("10-K", "10-Q", "20-F", "40-F", "6-K")}),
    }
    if "10-Q" in seen:
        out["calendar"] = "quarterly"
        out["regime"] = "SEC 10-Q / 10-K"
        out["confident"] = True
    elif "20-F" in seen or "40-F" in seen:
        # Annual to the SEC; the home market may well be quarterly or
        # half-yearly. Saying "annual" here would be a normalisation §5 forbids.
        out["calendar"] = None
        out["regime"] = "SEC 20-F/40-F annual (foreign private issuer)"
        out["confident"] = False
        out["note"] = ("foreign private issuer: SEC filings are annual, but the "
                       "home-market reporting calendar is what §5 needs and is "
                       "not resolvable from EDGAR. Record it by hand.")
    else:
        out["calendar"] = None
        out["regime"] = None
        out["confident"] = False
    return out


def mode_resolve_us(args):
    """Fill listing.cik and reporting for members whose ticker SEC knows.

    Never invents. A ticker SEC does not list is left exactly as it was, and
    said so, because a guessed calendar is worse than an absent one.
    """
    draft = read_json(args.draft)
    idx = ticker_index()
    if not idx:
        sys.exit("could not fetch the SEC ticker index; nothing changed")
    touched, skipped = [], []
    for m in draft.get("members") or []:
        tk = us_ticker(m)
        if not tk:
            ex, raw = venue_parts(m)
            skipped.append("%s: %s" % (
                m.get("company"),
                "private or no ticker" if not raw else
                "%s is not a US exchange, so SEC's symbol-keyed index is not "
                "consulted - record the calendar by hand" % ex))
            continue
        hit = idx.get(tk)
        if not hit:
            skipped.append("%s (%s): not in the SEC ticker index - not a US "
                           "registrant under that symbol" % (m.get("company"), tk))
            continue
        cik, exch = hit
        m["cik"] = cik
        rep = us_reporting(cik) or {}
        if rep.get("confident"):
            m["reporting"] = {
                "calendar": rep["calendar"],
                "currency": (m.get("reporting") or {}).get("currency") or "USD",
                "source": "SEC",
                "regime": rep["regime"],
                "fy_end": rep.get("fy_end"),
                "calendar_source": "SEC EDGAR submissions CIK%s, forms seen: %s"
                                   % (cik, ", ".join(rep.get("forms_seen") or [])),
                "resolved_by": "edgar",
            }
            touched.append("%s: %s, fy_end %s" % (m.get("company"),
                                                  rep["calendar"], rep.get("fy_end")))
        else:
            skipped.append("%s (CIK%s): %s" % (m.get("company"), cik,
                                               rep.get("note") or "no 10-Q and no 20-F/40-F seen"))
    write_json(args.draft, draft)
    print("resolved from EDGAR (%d):" % len(touched))
    for t in touched:
        print("  " + t)
    print("left to the agent (%d):" % len(skipped))
    for s in skipped:
        print("  " + s)
    return 0


# ------------------------------------------------------- product resolution

BLOCK_MARKERS = (
    "api-services-support@amazon.com", "to discuss automated access",
    "type the characters you see in this image", "enter the characters you see",
    "robot check", "sorry, we just need to make sure you",
    "/errors/validatecaptcha", "captcha",
)
NOT_FOUND_MARKERS = (
    "page not found", "dogs of amazon", "we're sorry. the web address you "
    "entered is not a functioning page", "looking for something?",
)
UNAVAILABLE_MARKERS = (
    "currently unavailable", "we don't know when or if this item will be back",
    "derzeit nicht verf", "no disponible por el momento",
    "non disponibile al momento", "actuellement indisponible",
)

TITLE_PATTERNS = (
    re.compile(r'id="productTitle"[^>]*>(.*?)</span>', re.S | re.I),
    re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I),
)


def _detag(raw):
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = txt.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    txt = txt.replace("&nbsp;", " ").replace("&rsquo;", "'")
    return re.sub(r"\s+", " ", txt).strip()


SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
PARENT_ASIN_RE = re.compile(r'"parentAsin"\s*:\s*"([A-Z0-9]{10})"')
AVAILABILITY_RE = re.compile(
    r'<div[^>]*id="availability"[^>]*>(?P<inner>.{0,600}?)</div>', re.S | re.I)


def strip_scripts(text):
    """Remove script and style bodies before any marker is looked for.

    Not cosmetic. An Amazon product page embeds a localisation table carrying
    the literal string "Currently unavailable." on EVERY page, in stock or not,
    as the caption the variant selector would show if a colour were out of
    stock. A whole-page substring scan therefore calls every product
    unavailable - which it did, on the first live run, for five flagship
    products that were all in stock. Availability is read from the buy box
    (`#availability`), never from the page's script data.
    """
    return SCRIPT_RE.sub(" ", text)


def classify_product_page(status, body, asin):
    """What the marketplace said about this ASIN. Reports, never interprets.

    A block is a finding (CORE-PROMPT §3): it is recorded as `blocked` and the
    caller stops. It is never retried around, and a blocked check is never
    reported as a resolved one.
    """
    out = {"http_status": status}
    if body is None:
        if status in (403, 429, 503):
            out["status"] = "blocked"
            out["note"] = ("marketplace refused the request (HTTP %d). \u00a73: "
                           "recorded, not routed around." % status)
        elif status in (404, 410):
            out["status"] = "not_found"
        else:
            out["status"] = "error"
            out["note"] = "no response body (status %s)" % status
        return out

    text = body.decode("utf-8", "replace")
    visible = strip_scripts(text)
    low = visible.lower()

    if any(mk in low for mk in BLOCK_MARKERS):
        out["status"] = "blocked"
        out["note"] = ("bot protection served instead of the product page. "
                       "\u00a73: recorded, not solved.")
        return out
    if any(mk in low for mk in NOT_FOUND_MARKERS) and 'id="productTitle"' not in text:
        out["status"] = "not_found"
        return out

    title = ""
    for pat in TITLE_PATTERNS:
        m = pat.search(visible)
        if m:
            title = _detag(m.group(1))
            break
    title = re.sub(r"\s*[:|-]\s*Amazon\.[a-z.]+\s*$", "", title).strip()
    if title:
        out["title_seen"] = title[:300]

    # The ASIN appearing in the served page is the check that the page is the
    # one asked for, rather than a redirect to something else.
    out["asin_on_page"] = asin in text

    # Amazon serves REVIEWS at the parent ASIN, pooled across every variant of
    # the product - every colour of one headphone, every pack size of one
    # drink. A collector pointed at the child ASIN gets the parent's reviews
    # anyway, so the parent is recorded here and the declaration carries it.
    # Without it, two members' series could silently be built from different
    # levels of the same tree, and dedup by review id would not notice.
    pm = PARENT_ASIN_RE.search(text)
    if pm:
        out["parent_asin_seen"] = pm.group(1)
        out["is_variant_child"] = pm.group(1) != asin

    # Availability. Scanned over the script-stripped text only: with the
    # localisation tables gone, "currently unavailable" appears on a page only
    # when the page is actually saying it.
    avail_block = AVAILABILITY_RE.search(visible)
    avail = _detag(avail_block.group("inner")) if avail_block else ""
    out["availability_seen"] = avail[:120] or (
        "buy box not rendered in the static HTML (loaded by script); "
        "availability not established")

    if not title or not out["asin_on_page"]:
        out["status"] = "error"
        out["note"] = "page served but no product title or ASIN found in it"
        return out
    hits = [mk for mk in UNAVAILABLE_MARKERS if mk in low]
    if hits:
        out["status"] = "unavailable"
        out["note"] = "page says: %s" % hits[0]
        return out
    out["status"] = "resolved"
    return out


def resolve_product(asin, marketplace, rate, route):
    host = MARKETPLACES[marketplace]
    url = "https://%s/dp/%s" % (host, asin)
    status, body = http_get(url, {
        # A plain desktop browser string. One request per product, paced; this
        # is reading a public product page, not bulk access (§3), and the
        # script neither logs in nor touches review pagination.
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }, rate=rate, retries=1, timeout=30)
    out = classify_product_page(status, body, asin)
    out.update({"checked_at": now_iso(), "route": route, "url": url,
                "marketplace": marketplace})
    return out


def mode_check_products(args):
    """Resolve every ASIN in the draft, and write what happened into the draft.

    Stops on the first block rather than working through the rest, because a
    block means the route is refusing and the remaining checks would be noise.
    """
    draft = read_json(args.draft)
    counts = {}
    blocked = False
    for m in draft.get("members") or []:
        for pr in m.get("products") or []:
            asin = _s(pr, "asin")
            mk = _s(pr, "marketplace")
            if args.only_missing and (pr.get("verification") or {}).get("status") == "resolved":
                counts["skipped"] = counts.get("skipped", 0) + 1
                continue
            if not ASIN_RE.match(asin) or mk not in MARKETPLACES:
                pr["verification"] = {
                    "status": "error", "checked_at": now_iso(),
                    "route": "none", "note": "asin or marketplace not well-formed",
                }
                counts["error"] = counts.get("error", 0) + 1
                continue
            v = resolve_product(asin, mk, args.rate, args.route)
            pr["verification"] = v
            counts[v["status"]] = counts.get(v["status"], 0) + 1
            print("  %-10s %-14s %-11s %s" % (asin, mk, v["status"],
                                              (v.get("title_seen") or v.get("note") or "")[:80]))
            if v["status"] == "blocked":
                blocked = True
                break
        if blocked:
            break
    write_json(args.draft, draft)
    print("\n" + "  ".join("%s=%d" % kv for kv in sorted(counts.items())))
    if blocked:
        print("\nSTOPPED: the marketplace refused the request. CORE-PROMPT §3 - "
              "a block is a finding to report, not an obstacle to route around.\n"
              "Options: check the remaining ASINs by hand in a browser and fill\n"
              "verification by hand with route='browser-manual', or declare with\n"
              "--allow-unverified so the count travels in the hash file.")
        return 2
    return 0 if not counts.get("error") and not counts.get("not_found") else 1


def comparability(group):
    """Facts about how comparable this group's members actually are.

    Computed, never asserted in prose (\u00a71.2). None of these is a reason to
    refuse a group - a global group spanning marketplaces and calendars is what
    \u00a72 asks for - but a reading taken across them is comparing populations as
    well as companies, and the file has to say so before anyone charts it.

    `cross_marketplace` is the sharpest one. Amazon's reviewer populations are
    national: a tone gap between a product on amazon.co.uk and one on
    amazon.com is partly a gap between British and American shoppers, and
    partly a language difference, before any company enters into it.
    """
    members = group.get("members") or []
    mk, cal, cur = {}, {}, {}
    no_public = []
    for m in members:
        for pr in m.get("products") or []:
            k = _s(pr, "marketplace")
            if k:
                mk[k] = mk.get(k, 0) + 1
        rep = m.get("reporting") or {}
        if rep.get("calendar"):
            cal[rep["calendar"]] = cal.get(rep["calendar"], 0) + 1
        if _s(rep, "currency"):
            cur[rep["currency"]] = cur.get(rep["currency"], 0) + 1
        if venue_is_private(m):
            no_public.append(m.get("company"))
    return {
        "marketplaces": mk,
        "cross_marketplace": len(mk) > 1,
        "reporting_calendars": cal,
        "mixed_reporting_calendars": len(cal) > 1,
        "currencies": cur,
        "mixed_currencies": len(cur) > 1,
        "members_without_public_reporting": no_public,
        "revenue_comparable_without_stated_handling": (
            len(cal) <= 1 and len(cur) <= 1 and not no_public),
    }


def print_comparability(c):
    """What a reader has to be told before using the group."""
    out = []
    if c["cross_marketplace"]:
        out.append(
            "CROSS-MARKETPLACE: products span %s. Amazon reviewer populations "
            "are national, so a peer-relative tone gap across these is partly a "
            "gap between countries of shopper and partly a language difference, "
            "before any company difference. Either keep the group to one "
            "marketplace or read the tone per marketplace, never pooled."
            % ", ".join("%s (%d)" % kv for kv in sorted(c["marketplaces"].items())))
    if c["mixed_reporting_calendars"]:
        out.append(
            "MIXED CALENDARS: %s. \u00a75 - tone is comparable across these, "
            "reported revenue is not. The group may not be compared on revenue "
            "without saying how the calendars were handled."
            % ", ".join("%s (%d)" % kv for kv in sorted(c["reporting_calendars"].items())))
    if c["mixed_currencies"]:
        out.append("MIXED CURRENCIES: %s. \u00a75 forbids silent conversion."
                   % ", ".join(sorted(c["currencies"])))
    if c["members_without_public_reporting"]:
        out.append(
            "NO PUBLIC REPORTING: %s. These members contribute tone and a peer "
            "comparison, and no revenue series at all."
            % ", ".join(c["members_without_public_reporting"]))
    return out


# ----------------------------------------------- concentration from filings

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
ARCHIVE_DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{doc}"
FILING_INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/"

# Where a revenue split is actually disclosed. Lifted from the archived
# scorecard's c3/c6 probes (archive/historical-harness/scripts/
# score_eligibility.py), which were solving this same question and were right
# about where to look. Fixed deliberately: the same questions are asked of
# every company in the same words, so the evidence gathered does not vary with
# how interesting a company looks.
CONCENTRATION_PROBES = (
    ("segments", r"(?:one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+) "
                 r"(?:reportable|operating) segments?"),
    ("single_segment", r"(?:one|a single) (?:reportable|operating) segment"),
    ("disaggregation", r"disaggregat(?:ion|ed) of revenue"),
    ("by_category", r"(?:net sales|revenues?) (?:revenue )?by "
                    r"(?:product |major )?(?:segment|product|categor|line|"
                    r"geograph)"),
    ("table_intro", r"the following table (?:summarizes|presents|sets forth|"
                    r"disaggregates)[^.]{0,90}(?:segment|net sales|revenue)"),
    ("percent_of_revenue", r"(?:represented|accounted for|comprised|was|were) "
                           r"(?:approximately )?[0-9]{1,3}(?:\.[0-9])?%[^.]{0,90}"
                           r"(?:net sales|net revenue|revenues?|total)"),
)


class _Text(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.out = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)

    def text(self):
        return re.sub(r"\s+", " ", html.unescape(" ".join(self.out)))


def flatten_html(raw):
    pr = _Text()
    try:
        pr.feed(raw.decode("utf-8", "replace"))
    except Exception:
        return ""
    return pr.text()


def latest_annual(cik):
    """The most recent annual report. Not point-in-time: concentration is a
    statement about the company NOW, so the newest filing is the right one.
    (The archived harness needed the filing in force at a past formation date;
    this component has no formation date, because nothing here is backtested.)
    """
    data = sec_json(SUBMISSIONS_URL.format(cik=cik))
    if not data:
        return None
    r = (data.get("filings") or {}).get("recent") or {}
    rows = list(zip(r.get("form") or [], r.get("filingDate") or [],
                    r.get("accessionNumber") or [],
                    r.get("primaryDocument") or []))
    annual = [x for x in rows if x[0] in ANNUAL_FORMS]
    if not annual:
        return None
    annual.sort(key=lambda x: x[1])
    form, filed, accn, doc = annual[-1]
    return {"form": form, "filed": filed, "accession": accn,
            "primary_document": doc, "entity": data.get("name"),
            "url": ARCHIVE_DOC.format(cik=str(int(cik)),
                                      accn=accn.replace("-", ""), doc=doc)}


def concentration_evidence(cik, window=320):
    """Verbatim windows from the company's own latest annual report.

    The point is that the share figure cannot be recalled from memory and
    written down as if it were read. The script fetches the filing, captures
    the sentences around each fixed probe verbatim, and records the URL it
    actually fetched. The agent reads the number off that text.
    """
    f = latest_annual(cik)
    if not f:
        return {"ok": False, "why": "no annual report found for CIK%s" % cik}
    status, raw = http_get(f["url"], {
        "User-Agent": sec_ua(), "Accept-Encoding": "gzip, deflate",
        "Accept": "*/*"}, rate=0.3)
    if raw is None:
        return {"ok": False, "why": "could not fetch %s (status %s)"
                                    % (f["url"], status), "filing": f}
    text = flatten_html(raw)
    hits = {}
    for name, pat in CONCENTRATION_PROBES:
        found = []
        for m in re.finditer(pat, text, re.I):
            a = max(0, m.start() - window // 2)
            found.append(text[a:m.end() + window].strip())
            if len(found) >= 3:
                break
        if found:
            hits[name] = found
    return {"ok": True, "filing": f, "chars": len(text), "probes": hits,
             "fetched_at": now_iso()}


def mode_concentration(args):
    """Fetch concentration evidence for every member SEC knows, and save it.

    Writes data/peer_groups/<id>.concentration.json. The declaration's
    `source_url` must appear in that file, which is what ties the recorded
    share to a filing the script actually read.
    """
    draft = read_json(args.draft)
    gid = _s(draft, "group_id")
    out_path = os.path.join(GROUPS_DIR, "%s.concentration.json" % gid)
    store = read_json(out_path) if os.path.exists(out_path) else {}
    idx = ticker_index()
    for m in draft.get("members") or []:
        company = m.get("company")
        cik = _s(m, "cik")
        if not cik:
            tk = us_ticker(m)
            hit = idx.get(tk) if tk else None
            cik = hit[0] if hit else ""
        if not cik:
            print("  %-34s not a US registrant SEC can resolve - evidence must "
                  "be recorded by hand with a source_url to the company's own "
                  "report" % company)
            continue
        if args.only_missing and company in store and store[company].get("ok"):
            print("  %-34s already have it" % company)
            continue
        ev = concentration_evidence(cik)
        store[company] = ev
        if ev.get("ok"):
            f = ev["filing"]
            print("  %-34s %s filed %s  probes: %s" % (
                company, f["form"], f["filed"],
                ", ".join("%s(%d)" % (k, len(v)) for k, v in sorted(ev["probes"].items()))
                or "NONE"))
            print("      %s" % f["url"])
        else:
            print("  %-34s FAILED: %s" % (company, ev.get("why")))
    write_json(out_path, store)
    print("\nevidence: %s" % out_path)
    print("Read the probe windows, record dominant_line / share_of_revenue / "
          "evidence / source_url per member, then `check`.")
    return 0


# ------------------------------------------------- the CONTRACTS.md §1 view

# Exactly the keys CONTRACTS.md §1 names, in its order, per member.
CONTRACT_MEMBER_KEYS = ("company", "venue", "brands", "concentration",
                        "products", "reporting")
CONTRACT_PRODUCT_KEYS = ("asin", "parent_asin", "marketplace", "name")
CONTRACT_REPORTING_KEYS = ("calendar", "currency", "fy_end", "source")


def content_hash(view):
    """sha256 of the file minus the hash field (CONTRACTS.md §1).

    Computed over the same canonical serialisation the file is written with, so
    a reader recomputes it by deleting `hash`, re-serialising and comparing.
    """
    bare = {k: v for k, v in view.items() if k != "hash"}
    return sha256_bytes(dumps(bare).encode("utf-8"))


def contract_view(group):
    """The group as CONTRACTS.md §1 shapes it, for the workflow to read.

    The versioned file under data/peer_groups/ stays the declaration of record:
    append-only, hash-chained, never rewritten. This is the view of its current
    version at the path the workflow was told to read. It is generated, never
    hand-edited, and it carries `version` and `supersedes` so a reader of the
    view alone can tell a first declaration from a third and go find the ones
    it replaced.

    The fields §1 names come first and are mandatory. The fields this component
    adds - the reason, the continuity reason, the verification record, the
    parent ASIN, the comparability block - are carried through beside them,
    because each one encodes a CORE-PROMPT §2 rule that §1 has no slot for.
    They are additive: a reader that knows only §1 is unaffected.
    """
    members = []
    for m in group.get("members") or []:
        out = {k: m.get(k) for k in CONTRACT_MEMBER_KEYS}
        out["products"] = [
            dict({k: pr.get(k) for k in CONTRACT_PRODUCT_KEYS},
                 continuity_reason=pr.get("continuity_reason"),
                 verification=pr.get("verification"))
            for pr in m.get("products") or []]
        rep = m.get("reporting") or {}
        out["reporting"] = dict({k: rep.get(k) for k in CONTRACT_REPORTING_KEYS},
                                **{k: v for k, v in rep.items()
                                   if k not in CONTRACT_REPORTING_KEYS})
        out["reason"] = m.get("reason")
        for extra in ("cik", "country_of_domicile", "secondary_listings"):
            if m.get(extra):
                out[extra] = m[extra]
        members.append(out)

    view = {
        "group_id": group["group_id"],
        "declared_at": group["declared_at"],
        "seed": group["seed"],
        "members": members,
        # beyond §1, and load-bearing
        "seed_kind": group.get("seed_kind"),
        "version": group["version"],
        "supersedes": group.get("supersedes"),
        "revision_reason": group.get("revision_reason"),
        "peer_relative_reading": group["peer_relative_reading"],
        "solo": bool(group.get("solo")),
        "solo_reason": group.get("solo_reason"),
        "substitutability_test": group["substitutability_test"],
        "comparability": group.get("comparability"),
        "readings_at_declare": group.get("readings_at_declare"),
        "post_collection": group.get("post_collection"),
        "declared_by": group.get("declared_by"),
        "record_of_declaration": "data/peer_groups/%s.v%d.json"
                                 % (group["group_id"], group["version"]),
    }
    view["hash"] = content_hash(view)
    return view


def write_contract_view(group, groups_dir=GROUPS_VIEW_DIR):
    view = contract_view(group)
    path = os.path.join(groups_dir, "%s.json" % group["group_id"])
    write_json(path, view)
    return path, view["hash"]


def verify_contract_view(path):
    """Does the view's own `hash` still describe its contents?"""
    view = read_json(path)
    recorded = view.get("hash")
    actual = content_hash(view)
    return recorded == actual, recorded, actual


# ------------------------------------------- parent drift (CONTRACTS.md §1)

def parents_log(group_id, groups_dir=GROUPS_DIR):
    return os.path.join(groups_dir, "%s.parents.jsonl" % group_id)


def declared_parents(group):
    """[(company_index, company, asin, marketplace, declared_parent)]"""
    out = []
    for i, m in enumerate(group.get("members") or []):
        for pr in m.get("products") or []:
            out.append((i, m.get("company"), _s(pr, "asin"),
                        _s(pr, "marketplace"),
                        _s(pr, "parent_asin") or
                        _s((pr.get("verification") or {}), "parent_asin_seen")))
    return out


def mode_reresolve(args):
    """Re-resolve every declared parent and append what was found.

    The gap this closes: the same-parent refusal in `validate` fires when a
    group is DECLARED, but Amazon re-parents variation families afterwards. Two
    members declared under distinct parents can converge later with no
    identifier changing anywhere - not the child ASINs, not the group file, not
    its hash - and the group is then handing two members identical reviews and
    comparing them against each other. Nothing watched that route until here.

    This verb does the network work and writes down what it saw. It NEVER
    rewrites the declaration: the collector verifies the parent from the page
    and treats the group file as the declaration rather than the authority, and
    editing the file to match what Amazon now serves would paper over exactly
    the convergence this exists to find. A real change is a new version with a
    reason, through `revise`.

    Stage G reads this log and is the thing that refuses. Kept separate so the
    gate stays offline, cheap and deterministic: a gate that makes fifteen
    network requests and can be blocked by the marketplace is not a gate.
    """
    gid = args.group_id.strip()
    versions = versions_of(gid)
    if not versions:
        sys.exit("no declaration of record for %s" % gid)
    n, path = versions[-1]
    group = read_json(path)
    log = parents_log(gid)

    seen, findings, blocked = {}, [], False
    for idx, company, asin, marketplace, declared in declared_parents(group):
        if not ASIN_RE.match(asin) or marketplace not in MARKETPLACES:
            continue
        v = resolve_product(asin, marketplace, args.rate, args.route)
        now_parent = _s(v, "parent_asin_seen")
        rec = {"action": "resolve", "group_version": n, "company": company,
               "asin": asin, "marketplace": marketplace,
               "declared_parent": declared, "parent_now": now_parent or None,
               "status": v.get("status"), "title_seen": v.get("title_seen"),
               "http_status": v.get("http_status")}

        if v.get("status") == "blocked":
            rec["finding"] = "blocked"
            blocked = True
        elif v.get("status") in ("not_found", "unavailable"):
            rec["finding"] = "ended"
            findings.append("%s %s: no longer on sale (%s). \u00a72 - the series "
                            "ENDS and is recorded as ended, never swapped for "
                            "another product" % (company, asin, v.get("status")))
        elif not now_parent:
            rec["finding"] = "no_parent_on_page"
            findings.append("%s %s: the page served no parentAsin" % (company, asin))
        elif declared and now_parent != declared:
            rec["finding"] = "reparented"
            findings.append("%s %s: parent moved %s -> %s. The review pool is a "
                            "different variation family than the one declared"
                            % (company, asin, declared, now_parent))
        else:
            rec["finding"] = "unchanged"

        if now_parent:
            if now_parent in seen and seen[now_parent][0] != idx:
                rec["finding"] = "converged"
                rec["converged_with"] = seen[now_parent][1]
                findings.append(
                    "CONVERGED: %s (%s) and %s now share parent %s. They would "
                    "be handed identical reviews and compared against each "
                    "other" % (company, asin, seen[now_parent][1], now_parent))
            else:
                seen[now_parent] = (idx, company)

        chain.append(log, rec)
        print("  %-34s %-11s %-11s -> %-11s %s"
              % (company[:34], asin, declared or "-", now_parent or "-",
                 rec["finding"]))
        if blocked:
            break

    summary = {"action": "sweep", "group_version": n,
               "products": len(declared_parents(group)),
               "findings": findings, "blocked": blocked,
               "complete": not blocked}
    chain.append(log, summary)

    print("\n%s v%d: %d product(s) re-resolved" % (gid, n, len(declared_parents(group))))
    if blocked:
        print("STOPPED: the marketplace refused the request. \u00a73 - recorded, "
              "not routed around. The sweep is incomplete and stage G will say so.")
        return 2
    for f in findings:
        print("  ! " + f)
    if findings:
        print("\nNothing here is repaired by editing the declaration - that "
              "would hide the change. A real change is a new version:\n"
              "  declare_group.py revise %s --from-draft <draft> --reason \"...\""
              % gid)
    return 2 if findings else 0


def parent_drift_state(group_id, group, now=None):
    """What the parents log says about this group, for stage G to refuse on.

    Offline. Reads the log and nothing else.
    """
    log = parents_log(group_id)
    st = {"problems": [], "checked_at": None, "age_days": None,
          "log_lines": len(chain.read(log))}

    broken = chain.walk(log, actions=("resolve", "sweep", "note"))
    if broken:
        st["problems"].extend("parents log: " + b for b in broken)
        return st

    sweeps = [r for r in chain.records(log) if r.get("action") == "sweep"]
    if not sweeps:
        st["problems"].append(
            "parents have never been re-resolved. CONTRACTS.md \u00a71 requires "
            "every declared parent to be re-resolved on a cadence, because two "
            "members can converge onto one parent after declaration with no "
            "identifier changing. Run: declare_group.py reresolve %s" % group_id)
        return st

    last = sweeps[-1]
    st["checked_at"] = last.get("recorded_at")
    try:
        when = dt.datetime.strptime(last["recorded_at"], "%Y-%m-%dT%H:%M:%SZ")
        when = when.replace(tzinfo=dt.timezone.utc)
        age = ((now or dt.datetime.now(dt.timezone.utc)) - when).total_seconds() / 86400.0
        st["age_days"] = round(age, 2)
    except (KeyError, ValueError):
        age = None

    if last.get("group_version") != group.get("version"):
        st["problems"].append(
            "the last sweep was of v%s but the declaration of record is v%s - "
            "re-resolve the current version"
            % (last.get("group_version"), group.get("version")))
    if not last.get("complete"):
        st["problems"].append(
            "the last sweep did not complete (the marketplace refused it), so "
            "the parents are unchecked rather than checked and clean")
    if age is not None and age > REPARENT_CHECK_DAYS:
        st["problems"].append(
            "the last parent sweep was %.1f days ago, past the %d-day cadence. "
            "A convergence that happened since would not have been seen"
            % (age, REPARENT_CHECK_DAYS))
    for f in last.get("findings") or []:
        st["problems"].append(f)
    return st


# -------------------------------------------------------------------- modes

def mode_draft(args):
    gid = args.group_id.strip()
    if not GROUP_ID_RE.match(gid):
        sys.exit("group_id %r must match %s" % (gid, GROUP_ID_RE.pattern))
    out = args.out or os.path.join(GROUPS_DIR, "%s.draft.json" % gid)
    if os.path.exists(out) and not args.force:
        sys.exit("%s exists; pass --force to start it over" % out)
    if versions_of(gid):
        print("note: %s already has frozen versions (%s). A changed group is a "
              "new version via `revise`, never an edit of a frozen file."
              % (gid, ", ".join("v%d" % n for n, _ in versions_of(gid))))
    skeleton = {
        "group_id": gid,
        "seed": args.seed,
        "seed_kind": args.seed_kind,
        "substitutability_test": SUBSTITUTABILITY_TEST,
        "solo": False,
        "peer_relative_reading": True,
        "members": [_member_skeleton()],
    }
    write_json(out, skeleton)
    print("draft: %s" % out)
    print("Fill members, then: resolve-us -> check-products -> check -> declare")
    return 0


def _member_skeleton():
    return {
        "company": "",
        "venue": "",
        "cik": "",
        "country_of_domicile": "",
        "brands": [],
        "concentration": {"dominant_line": "", "share_of_revenue": None,
                          "evidence": "", "source_url": ""},
        "reason": "",
        "reporting": {"calendar": "", "currency": "", "source": "",
                      "regime": "", "fy_end": "", "resolved_by": "agent"},
        "products": [{"asin": "", "marketplace": "amazon.com", "brand": "",
                      "name": "", "continuity_reason": ""}],
    }


def mode_check(args):
    draft = read_json(args.draft)
    problems = validate(draft, allow_unverified=args.allow_unverified)
    gid = _s(draft, "group_id")
    n = readings_for(gid)
    print("group %s: %d member(s), %d product(s), %d existing reading(s)"
          % (gid or "?", len(draft.get("members") or []),
             sum(len(m.get("products") or []) for m in draft.get("members") or []), n))
    for n in print_comparability(comparability(draft)):
        print("  ! " + n)
    if not problems:
        print("OK - the draft satisfies the contract.")
        return 0
    print("\n%d problem(s):" % len(problems))
    for pr in problems:
        print("  - " + pr)
    return 1


def normalise_for_contract(group):
    """Fill the §1 fields that are mechanical rather than judgements.

    `parent_asin` is lifted from the verification record onto the product,
    because §1 requires it on the product and the collector joins on it.
    `no_public_report` / `revenue_readable` are made explicit on every member:
    §1 shows them on a listed member as false/true, and leaving them absent
    would make "not private" and "not yet checked" look the same.
    """
    group = json.loads(json.dumps(group))
    for m in group.get("members") or []:
        c = m.setdefault("concentration", {})
        if not isinstance(c, dict):
            continue
        if venue_is_private(m):
            c.setdefault("no_public_report", False)
        else:
            c["no_public_report"] = False
        c["revenue_readable"] = not c.get("no_public_report")
        for pr in m.get("products") or []:
            seen = _s((pr.get("verification") or {}), "parent_asin_seen")
            if seen and not _s(pr, "parent_asin"):
                pr["parent_asin"] = seen
    return group


def _freeze(draft, version, supersedes, revision_reason, allow_unverified):
    """Validate, stamp, write <gid>.v<N>.json + .hash. Refuses to overwrite."""
    gid = _s(draft, "group_id")
    problems = validate(draft, allow_unverified=allow_unverified)
    if problems:
        print("%d problem(s); nothing written:" % len(problems))
        for pr in problems:
            print("  - " + pr)
        return None, 1

    path = os.path.join(GROUPS_DIR, "%s.v%d.json" % (gid, version))
    if os.path.exists(path):
        print("%s exists. A frozen version is never rewritten (§1.5, §2). "
              "Use `revise` to add a new version with its reason." % path)
        return None, 1

    n_readings = readings_for(gid)
    group = dict(draft)
    group = normalise_for_contract(group)
    group["schema_version"] = SCHEMA_VERSION
    group["version"] = version
    # CONTRACTS.md §1: a DATE, never a wall-clock during collection. The exact
    # instant is kept beside it, because the freeze is also a provenance event
    # and losing the time would make two same-day versions indistinguishable.
    group["declared_at"] = today()
    group["declared_at_utc"] = now_iso()
    group["readings_at_declare"] = n_readings
    group["post_collection"] = n_readings > 0
    group["declared_by"] = {
        "tool": "declare_group.py",
        "code_commit": git_sha() or "unknown",
    }
    if supersedes:
        group["supersedes"] = supersedes
    if revision_reason:
        group["revision_reason"] = revision_reason
    group.pop("verification_summary", None)
    group["comparability"] = comparability(group)

    digest = write_json(path, group)
    line = write_hash_file(path, digest, group)
    view_path, view_hash = write_contract_view(group)
    print("declared: %s" % path)
    print("hash:     %s" % hash_path(path))
    print("contract: %s  (hash %s)" % (view_path, view_hash[:12]))
    print("  " + line.strip())
    notes = print_comparability(group["comparability"])
    if notes:
        print("")
        for n in notes:
            print("  ! " + n)
    if group["post_collection"]:
        print("\nWARNING: %d reading(s) already exist for %s, so this version "
              "was declared AFTER tone was known. It is marked "
              "post_collection=true in the file and in the hash sidecar, and it "
              "keeps that mark. §2: a peer added after seeing a result is not a "
              "peer, it is a choice." % (n_readings, gid))
    if not group["peer_relative_reading"]:
        print("\nNOTE: one member. This company is tracked ALONE and has NO "
              "peer-relative reading. Every number from it must say so.")
    unverified = sum(1 for m in group["members"] for p in m["products"]
                     if (p.get("verification") or {}).get("status") != "resolved")
    if unverified:
        print("\nNOTE: %d product(s) did not resolve on their marketplace. The "
              "count is in the hash file and does not go away." % unverified)
    return path, 0


def mode_declare(args):
    draft = read_json(args.draft)
    gid = _s(draft, "group_id")
    existing = versions_of(gid)
    if existing:
        print("%s already has %s. A changed group is a new version:\n"
              "  declare_group.py revise %s --from-draft %s --reason \"...\""
              % (gid, ", ".join("v%d" % n for n, _ in existing), gid, args.draft))
        return 1
    _, rc = _freeze(draft, 1, None, None, args.allow_unverified)
    return rc


def mode_revise(args):
    gid = args.group_id.strip()
    existing = versions_of(gid)
    if not existing:
        sys.exit("%s has no frozen version yet; use `declare`" % gid)
    reason = (args.reason or "").strip()
    if len(reason) < 40:
        sys.exit("--reason is required and must say what changed and why, in at "
                 "least 40 characters. §2: a revision is recorded, never "
                 "applied quietly.")
    last_n, last_path = existing[-1]
    prev = read_json(last_path)
    draft = read_json(args.from_draft)
    if _s(draft, "group_id") != gid:
        sys.exit("draft group_id %r != %r" % (_s(draft, "group_id"), gid))

    supersedes = {
        "version": last_n,
        "file": os.path.basename(last_path),
        "sha256": sha256_file(last_path),
        "declared_at": prev.get("declared_at"),
    }
    before = {canon(m.get("company", "")) for m in prev.get("members") or []}
    after = {canon(m.get("company", "")) for m in draft.get("members") or []}
    diff = {
        "members_added": sorted(
            m.get("company") for m in draft.get("members") or []
            if canon(m.get("company", "")) not in before),
        "members_removed": sorted(
            m.get("company") for m in prev.get("members") or []
            if canon(m.get("company", "")) not in after),
    }
    draft = dict(draft)
    draft["member_diff_from_previous"] = diff
    path, rc = _freeze(draft, last_n + 1, supersedes, reason, args.allow_unverified)
    if rc:
        return rc
    print("\nsupersedes v%d (%s)" % (last_n, supersedes["sha256"][:12]))
    if diff["members_added"]:
        print("added:   " + ", ".join(diff["members_added"]))
    if diff["members_removed"]:
        print("removed: " + ", ".join(diff["members_removed"]))
    return 0


def mode_show(args):
    gid = args.group_id.strip()
    existing = versions_of(gid)
    if not existing:
        sys.exit("no frozen version of %s" % gid)
    n, path = existing[-1]
    group = read_json(path)
    digest = sha256_file(path)
    recorded = _recorded_hash(path)
    print("%s v%d  %s" % (gid, n, path))
    print("  declared_at          %s" % group.get("declared_at"))
    print("  seed                 %s: %s" % (group.get("seed_kind"),
                                             group.get("seed")))
    print("  sha256               %s" % digest)
    print("  sidecar              %s" % (recorded or "MISSING"))
    print("  hash matches         %s" % ("yes" if recorded == digest else "NO"))
    print("  readings_at_declare  %s" % group.get("readings_at_declare"))
    print("  post_collection      %s" % group.get("post_collection"))
    print("  peer_relative        %s" % group.get("peer_relative_reading"))
    print("  versions             %s" % ", ".join("v%d" % k for k, _ in existing))
    print("  members")
    for m in group.get("members") or []:
        rep = m.get("reporting") or {}
        print("    %-34s %-16s %-12s %s" % (
            m.get("company"), _s(m, "venue"),
            "%s/%s" % (rep.get("calendar"), rep.get("currency")),
            ", ".join(p.get("asin") for p in m.get("products") or [])))
    return 0 if recorded == digest else 1


def _recorded_hash(version_path):
    hp = hash_path(version_path)
    if not os.path.exists(hp):
        return None
    with open(hp, encoding="utf-8") as f:
        first = f.read().split()
        return first[0] if first else None


def mode_verify(args):
    """Re-hash every version of every group. This is the edit-in-place detector.

    A frozen file whose bytes no longer match its sidecar was edited after
    declaration, which §2 forbids outright. It is reported as a break, not
    repaired - repairing it would destroy the only evidence that it happened.
    """
    gids = [args.group_id] if args.group_id else all_group_ids()
    if not gids:
        print("no frozen peer groups in %s" % GROUPS_DIR)
        return 0
    broken, chain_broken, stale, ok = [], [], [], 0
    for gid in gids:
        prev_sha = None
        for n, path in versions_of(gid):
            digest = sha256_file(path)
            recorded = _recorded_hash(path)
            if recorded is None:
                broken.append("%s v%d: no .hash sidecar" % (gid, n))
            elif recorded != digest:
                broken.append("%s v%d: EDITED IN PLACE - sidecar %s, file %s"
                              % (gid, n, recorded[:12], digest[:12]))
            else:
                ok += 1
            group = read_json(path)
            sup = group.get("supersedes") or {}
            if n > 1:
                if not sup:
                    chain_broken.append("%s v%d: no supersedes block" % (gid, n))
                elif prev_sha and sup.get("sha256") != prev_sha:
                    chain_broken.append(
                        "%s v%d: supersedes %s but v%d hashes to %s"
                        % (gid, n, (sup.get("sha256") or "?")[:12], n - 1, prev_sha[:12]))
                if not _s(group, "revision_reason"):
                    chain_broken.append("%s v%d: no revision_reason" % (gid, n))
            prev_sha = digest
            if group.get("schema_version") != SCHEMA_VERSION:
                stale.append("%s v%d: schema %s, current is %d - hash checked, "
                             "contract not revalidated"
                             % (gid, n, group.get("schema_version"), SCHEMA_VERSION))
                continue
            for pr in validate(group, allow_unverified=True):
                broken.append("%s v%d: %s" % (gid, n, pr))
    print("%d version(s) hash-clean" % ok)
    for b in broken:
        print("  BREAK  " + b)
    for c in chain_broken:
        print("  CHAIN  " + c)
    for t in stale:
        print("  STALE  " + t)
    return 1 if (broken or chain_broken) else 0


def mode_stage(args):
    """CONTRACTS.md §4 stage G: `declare_group.py --group <group_id>`.

    Reports whether stage G is satisfied for this group. Satisfied means: a
    group file exists, its hash verifies, and every member has concentration
    evidence. Exit codes are the contract's: 0 ran or reported a clean stop,
    2 an integrity violation needing a human, 3 not built yet.

    This mode reads a group id and nothing else. No stage reads a path it was
    not given (§4), which is the line that broke the last integration.
    """
    gid = args.group.strip()
    view_path = os.path.join(GROUPS_VIEW_DIR, "%s.json" % gid)
    versions = versions_of(gid)

    if not versions and not os.path.exists(view_path):
        print("G %s: not declared yet" % gid)
        print("  declare_group.py draft --group-id %s --seed-kind category "
              "--seed '...'" % gid)
        return 3

    problems = []
    if not versions:
        problems.append("no declaration of record under %s; the contract view "
                        "exists with nothing behind it" % GROUPS_DIR)
    else:
        n, path = versions[-1]
        if _recorded_hash(path) != sha256_file(path):
            problems.append("%s was edited in place after it was declared" % path)
        group = read_json(path)
        for pr in validate(group, allow_unverified=True):
            problems.append(pr)
        if not os.path.exists(view_path):
            problems.append("no contract view at %s" % view_path)
        else:
            ok, recorded, actual = verify_contract_view(view_path)
            if not ok:
                problems.append("%s: hash field says %s, contents hash to %s"
                                % (view_path, (recorded or "none")[:12], actual[:12]))
            view = read_json(view_path)
            if view.get("version") != n:
                problems.append("%s is v%s but the record of declaration is v%d"
                                % (view_path, view.get("version"), n))

        missing = [m.get("company") for m in group.get("members") or []
                   if not _s((m.get("concentration") or {}), "evidence")]
        if missing:
            problems.append("no concentration evidence: %s (CONTRACTS.md §1 - a "
                            "member with no evidence is not a member)"
                            % ", ".join(missing))

        drift = parent_drift_state(gid, group)
        problems.extend(drift["problems"])

    if problems:
        print("G %s: NOT satisfied" % gid)
        for pr in problems:
            print("  - " + pr)
        return 2
    group = read_json(versions[-1][1])
    print("G %s: satisfied  v%d  %d member(s)  %d product(s)"
          % (gid, versions[-1][0], len(group["members"]),
             sum(len(m["products"]) for m in group["members"])))
    drift = parent_drift_state(gid, group)
    print("  record %s" % versions[-1][1])
    print("  view   %s" % view_path)
    print("  parents re-resolved %s (%.1f days ago, cadence %d)"
          % (drift["checked_at"], drift["age_days"] or 0.0, REPARENT_CHECK_DAYS))
    for n in print_comparability(group.get("comparability") or {}):
        print("  ! " + n)
    return 0


def mode_reemit(args):
    """Regenerate the contract view from the declaration of record.

    For a group frozen before the view existed, and as the repair for a view
    that fails its own hash: the record is the source of truth and the view is
    derived, so a bad view is rebuilt rather than patched.
    """
    gids = [args.group_id] if args.group_id else all_group_ids()
    for gid in gids:
        versions = versions_of(gid)
        if not versions:
            print("%s: no declaration of record" % gid)
            continue
        group = read_json(versions[-1][1])
        path, h = write_contract_view(group)
        print("%s -> %s  (v%d, hash %s)" % (gid, path, group["version"], h[:12]))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="declare_group.py",
        description="Declare a peer group of substitutable consumer products "
                    "(CORE-PROMPT §2). Collects nothing and scores nothing.")
    # CONTRACTS.md §4: every stage is invoked as `script --group <group_id>`.
    ap.add_argument("--group", help="CONTRACTS.md §4 stage G: report whether "
                                    "this group's declaration is satisfied")
    sub = ap.add_subparsers(dest="mode")

    d = sub.add_parser("draft", help="skeleton for a seed")
    d.add_argument("--group-id", required=True)
    d.add_argument("--seed", required=True)
    d.add_argument("--seed-kind", required=True, choices=SEED_KINDS)
    d.add_argument("--out")
    d.add_argument("--force", action="store_true")
    d.set_defaults(fn=mode_draft)

    r = sub.add_parser("resolve-us", help="CIK + calendar from EDGAR")
    r.add_argument("draft")
    r.set_defaults(fn=mode_resolve_us)

    c = sub.add_parser("check-products", help="resolve each ASIN")
    c.add_argument("draft")
    c.add_argument("--rate", type=float, default=3.0,
                   help="minimum seconds between requests (default 3)")
    c.add_argument("--route", default="http-direct")
    c.add_argument("--only-missing", action="store_true")
    c.set_defaults(fn=mode_check_products)

    k = sub.add_parser("check", help="validate a draft; freeze nothing")
    k.add_argument("draft")
    k.add_argument("--allow-unverified", action="store_true")
    k.set_defaults(fn=mode_check)

    dc = sub.add_parser("declare", help="freeze as v1")
    dc.add_argument("draft")
    dc.add_argument("--allow-unverified", action="store_true")
    dc.set_defaults(fn=mode_declare)

    rv = sub.add_parser("revise", help="freeze as the next version, with a reason")
    rv.add_argument("group_id")
    rv.add_argument("--from-draft", required=True)
    rv.add_argument("--reason", required=True)
    rv.add_argument("--allow-unverified", action="store_true")
    rv.set_defaults(fn=mode_revise)

    s = sub.add_parser("show", help="current version and whether its hash holds")
    s.add_argument("group_id")
    s.set_defaults(fn=mode_show)

    v = sub.add_parser("verify", help="re-hash every version; detect an edit")
    v.add_argument("group_id", nargs="?")
    v.set_defaults(fn=mode_verify)

    cn = sub.add_parser("concentration",
                        help="fetch revenue-concentration evidence from filings")
    cn.add_argument("draft")
    cn.add_argument("--only-missing", action="store_true")
    cn.set_defaults(fn=mode_concentration)

    rr = sub.add_parser("reresolve",
                        help="re-resolve every declared parent (CONTRACTS.md §1)")
    rr.add_argument("group_id")
    rr.add_argument("--rate", type=float, default=3.0)
    rr.add_argument("--route", default="http-direct")
    rr.set_defaults(fn=mode_reresolve)

    re_ = sub.add_parser("reemit", help="regenerate the CONTRACTS.md §1 view")
    re_.add_argument("group_id", nargs="?")
    re_.set_defaults(fn=mode_reemit)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if getattr(args, "group", None) and not getattr(args, "mode", None):
        return mode_stage(args)
    if not getattr(args, "mode", None):
        ap.error("give a subcommand, or --group <group_id> for the stage check")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
