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
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

GROUPS_DIR = os.path.join("data", "peer_groups")
READINGS = os.path.join("results", "readings.jsonl")

GROUP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
YEAR_RE = re.compile(r"\b(19[5-9][0-9]|20[0-4][0-9])\b")

SEED_KINDS = ("company", "brand", "category")
CALENDARS = ("quarterly", "half-yearly", "annual", "irregular")
RESOLVED_BY = ("edgar", "agent")

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
        m = VERSION_RE.match(name)
        if m and m.group("gid") == group_id:
            found.append((int(m.group("n")), os.path.join(groups_dir, name)))
    return sorted(found)


def all_group_ids(groups_dir=GROUPS_DIR):
    ids = set()
    if not os.path.isdir(groups_dir):
        return []
    for name in os.listdir(groups_dir):
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

    seed = group.get("seed")
    if not isinstance(seed, dict):
        p.append("seed: missing; §2 requires the group to record what it came from")
    else:
        if seed.get("kind") not in SEED_KINDS:
            p.append("seed.kind %r: must be one of %s" % (seed.get("kind"), ", ".join(SEED_KINDS)))
        if not _s(seed, "value"):
            p.append("seed.value: empty")

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

        p.extend(_validate_listing(m, tag))
        p.extend(_validate_reporting(m, tag))

        brands = m.get("brands")
        if not isinstance(brands, list) or not [b for b in brands if _s({"b": b}, "b")]:
            p.append("%s.brands: §2 requires the consumer brands it owns that "
                     "are relevant to this group" % tag)
            brands = []
        brands = [b.strip() for b in brands if isinstance(b, str) and b.strip()]

        p.extend(_validate_reason(m, tag, brands))
        p.extend(_validate_products(m, tag, brands, seen_asin, i, allow_unverified))

    return p


def _validate_listing(m, tag):
    p = []
    listing = m.get("listing")
    if not isinstance(listing, dict):
        p.append("%s.listing: missing; record a ticker and exchange, or "
                 '{"status": "private"}' % tag)
        return p
    status = _s(listing, "status")
    if status == "private":
        return p
    if status and status != "listed":
        p.append("%s.listing.status %r: must be 'listed' or 'private'" % (tag, status))
    if not _s(listing, "ticker"):
        p.append("%s.listing.ticker: empty (use status 'private' if it is "
                 "not listed)" % tag)
    if not _s(listing, "exchange"):
        p.append("%s.listing.exchange: empty. Venue does not matter for tone "
                 "but it is how the company is identified" % tag)
    cik = _s(listing, "cik")
    if cik and not re.match(r"^\d{10}$", cik):
        p.append("%s.listing.cik %r: must be 10 digits, zero-padded" % (tag, cik))
    return p


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
    if not _s(rep, "regime"):
        p.append("%s.reporting.regime: empty (e.g. 'SEC 10-Q', 'FCA DTR 4 "
                 "half-yearly', 'TDnet quarterly / EDINET')" % tag)
    cur = _s(rep, "currency")
    if not CURRENCY_RE.match(cur):
        p.append("%s.reporting.currency %r: must be a 3-letter ISO 4217 code. "
                 "§5 forbids silent conversion, so the native currency is "
                 "recorded even when it is awkward" % (tag, cur))
    if not _s(rep, "source"):
        p.append("%s.reporting.source: empty. Say where the calendar was read "
                 "from, so a wrong one can be traced" % tag)
    if rep.get("resolved_by") not in RESOLVED_BY:
        p.append("%s.reporting.resolved_by %r: must be one of %s"
                 % (tag, rep.get("resolved_by"), ", ".join(RESOLVED_BY)))
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


def _validate_products(m, tag, brands, seen_asin, idx, allow_unverified):
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
        if not _s(pr, "title_at_declare"):
            p.append("%s.title_at_declare: empty. The title at declaration is "
                     "how a silently relisted ASIN is caught later" % ptag)
        brand = _s(pr, "brand")
        if not brand:
            p.append("%s.brand: empty" % ptag)
        elif brands and canon(brand) not in [canon(b) for b in brands]:
            p.append("%s.brand %r: not among the member's declared brands (%s)"
                     % (ptag, brand, ", ".join(brands)))
        p.extend(_validate_continuity(pr, ptag, brands))
        p.extend(_validate_verification(pr, ptag, allow_unverified))
        p.extend(_validate_title_agreement(pr, ptag, brand))
    return p


def _validate_title_agreement(pr, ptag, brand):
    """Does the page the ASIN served actually look like the declared product?

    The cheap check that catches a transposed identifier - one company's ASIN
    recorded under another's - which no amount of careful prose would catch,
    because the prose and the ASIN are written in different moments. The
    declared brand has to appear in the title the marketplace served.

    It does NOT catch an accessory: "Earpads Replacement for JBL Tour One M3"
    carries the brand too. That one is on the reader of the title, and it is
    why `title_at_declare` is compared to what was served rather than trusted.
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
    declared = _s(pr, "title_at_declare")
    if declared and canon(declared) != canon(seen):
        out.append("%s.title_at_declare %r does not match the served title "
                   "%r. Record what the marketplace actually serves, so a "
                   "silently relisted ASIN is visible later"
                   % (ptag, declared[:60], seen[:60]))
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
        listing = m.get("listing") or {}
        tk = (listing.get("ticker") or "").strip().upper()
        if not tk or listing.get("status") == "private":
            skipped.append("%s: no ticker" % m.get("company"))
            continue
        hit = idx.get(tk)
        if not hit:
            skipped.append("%s (%s): not in the SEC ticker index - not a US "
                           "registrant under that symbol" % (m.get("company"), tk))
            continue
        cik, exch = hit
        listing["cik"] = cik
        if exch and not listing.get("exchange"):
            listing["exchange"] = exch
        m["listing"] = listing
        rep = us_reporting(cik) or {}
        if rep.get("confident"):
            m["reporting"] = {
                "calendar": rep["calendar"],
                "regime": rep["regime"],
                "currency": (m.get("reporting") or {}).get("currency") or "USD",
                "fy_end": rep.get("fy_end"),
                "source": "SEC EDGAR submissions CIK%s, forms seen: %s"
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
        if (m.get("listing") or {}).get("status") == "private":
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
        "seed": {"kind": args.seed_kind, "value": args.seed},
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
        "country_of_domicile": "",
        "listing": {"status": "listed", "ticker": "", "exchange": "", "cik": ""},
        "brands": [],
        "reason": "",
        "reporting": {"calendar": "", "regime": "", "currency": "",
                      "fy_end": "", "source": "", "resolved_by": "agent"},
        "products": [{"asin": "", "marketplace": "amazon.com", "brand": "",
                      "title_at_declare": "", "continuity_reason": ""}],
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
    group["version"] = version
    group["declared_at"] = now_iso()
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
    print("declared: %s" % path)
    print("hash:     %s" % hash_path(path))
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
    print("  seed                 %s: %s" % ((group.get("seed") or {}).get("kind"),
                                             (group.get("seed") or {}).get("value")))
    print("  sha256               %s" % digest)
    print("  sidecar              %s" % (recorded or "MISSING"))
    print("  hash matches         %s" % ("yes" if recorded == digest else "NO"))
    print("  readings_at_declare  %s" % group.get("readings_at_declare"))
    print("  post_collection      %s" % group.get("post_collection"))
    print("  peer_relative        %s" % group.get("peer_relative_reading"))
    print("  versions             %s" % ", ".join("v%d" % k for k, _ in existing))
    print("  members")
    for m in group.get("members") or []:
        lst = m.get("listing") or {}
        venue = ("private" if lst.get("status") == "private"
                 else "%s:%s" % (lst.get("exchange"), lst.get("ticker")))
        rep = m.get("reporting") or {}
        print("    %-34s %-16s %-12s %s" % (
            m.get("company"), venue,
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
    broken, chain_broken, ok = [], [], 0
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
            problems = validate(group, allow_unverified=True)
            for pr in problems:
                broken.append("%s v%d: %s" % (gid, n, pr))
    print("%d version(s) hash-clean" % ok)
    for b in broken:
        print("  BREAK  " + b)
    for c in chain_broken:
        print("  CHAIN  " + c)
    return 1 if (broken or chain_broken) else 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="declare_group.py",
        description="Declare a peer group of substitutable consumer products "
                    "(CORE-PROMPT §2). Collects nothing and scores nothing.")
    sub = ap.add_subparsers(dest="mode", required=True)

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
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
