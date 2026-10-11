#!/usr/bin/env python3
"""Stage A1b - mechanical pre-filter between the universe panel and A2.

A1 (screen_rolling.py) produces 3,522 in_band company-years. A2
(score_eligibility.py) costs 6-8k tokens and 2-3 EDGAR requests per
company-year of a human's attention. Nobody will hand-score 3,522. This script
is the component that stands between them: it reads the hashed panel and writes
a shortlist of company-years for A2 to score, plus a complete, reasoned record
of every company-year it cut.

It is part of the instrument, so it obeys the same rules the rest of the
pipeline does:

  1. STRUCTURE ONLY, NEVER OUTCOME. Every input is a structural fact disclosed
     in a filing that was public at the formation date: how many reportable
     segments the registrant says it has, and whether it discloses selling
     through Amazon, e-commerce or a direct channel. Nothing about how the
     company turned out is read, computed or available here - no later revenue,
     no price, no survival. CORE-PROMPT s1.2.
  2. POINT-IN-TIME, MECHANICALLY. The evidence is the filing in force at
     formation, resolved by score_eligibility.in_force_filing() - the last
     annual report filed on or before that date, falling back to the last
     registration statement when no annual report existed yet. That function is
     A2's point-in-time boundary and it is reused rather than reimplemented, so
     there is one definition of "in force" in the project and one EDGAR client.
  3. THE SAME RULE FOR EVERY COMPANY-YEAR. The regexes and the thresholds are
     frozen constants below, declared before any company was looked at, applied
     uniformly across all seven formations. A rule that varied with how
     interesting a company looked would be selection, not filtering.
  4. ABSENCE-ONLY CUTS, WITH ONE EXCEPTION. The filter does not decide that a
     company is good. It removes a company-year when the structural evidence
     for a criterion is ABSENT from the filing in force (no channel disclosed
     at all, no filing at all). The one positive-evidence cut is segment
     sprawl, and its threshold is set deliberately loose - see THRESHOLDS.

What it deliberately does NOT do:

  - It does not score. No 0-5 judgement, no weighted total, no pass/fail
    against the 60-point bar. That is A2, a human step with the
    review-eligibility-scorer subagent, and this script must not pre-empt it.
    A company-year this filter KEEPS is one A2 may still fail, and that is the
    correct division of labour.
  - It does not look at review coverage. Coverage is A3 and runs after A2.
    company-selection.md s4: letting data availability influence selection is
    the bias the design exists to prevent.
  - It does not cut on SIC group, although the panel carries one and the
    planning note that asked for this component listed it as a candidate
    input. company-selection.md s1 keeps communications equipment and
    computers at the SIC stage on purpose, so that the enterprise names leave
    "with a documented reason per company rather than a silent omission at the
    SIC stage". A SIC-group cut here would reinstate exactly that silent
    omission one stage later - and it could not do the job anyway, because
    Logitech, NetApp and Fortinet share SIC 357x. The group is recorded on
    every row and used by nothing.
  - It does not cut on revenue, or on anything quantified. Whether the
    disclosed channel share is quantified IS measured and recorded, because it
    is what A2's criterion 1 asks for - but it is not a cut. LOGI is the only
    company-year A2 has passed so far (69.0), and its Amazon share is
    undisclosed; a quantification cut would have removed it.

Modes
-----
  measure    resolve the in-force filing per company-year, fetch it, apply the
             frozen probes, cache the measurement per (cik, accession).
             Resumable: re-running costs nothing for what is already cached.
  build      apply the thresholds to the cache -> the shortlists and the panel
             record. Reads the cache only; never the network.
  show       print one company-year's filing, probe hits and evidence windows,
             for hand-verifying a cut or a keep against the actual filing.
  verify     re-check written artefacts against the panel and the thresholds.

Output: data/prefilter_panel.csv        every in_band company-year, kept or cut
        data/prefilter_panel.hash
        data/shortlist_<YEAR>.csv       what A2 scores, per formation year
        data/shortlist_<YEAR>.hash
        data/prefilter.evidence.json    matched windows, per company-year
        data/prefilter.manifest.json    thresholds, hashes, counts in and out
Input:  data/universe_panel.csv         hashed; never rewritten here
Cache:  .cache/prefilter/               safe to delete, costs only refetch time

Artefacts carry no timestamps, so the same panel and the same cache produce
byte-identical files and the same sha256 on every run.

Standard library only (references/numeric-stack.md). Needs SEC_USER_AGENT.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from screen_universe import fetch, ua  # noqa: E402
from score_eligibility import (in_force_filing, flatten_filing,  # noqa: E402
                               archive_url, _Flatten)
import sic_consumer  # noqa: E402

PROBE_VERSION = "prefilter-v1"

# ------------------------------------------------------------------ THRESHOLDS
# Declared up front, fixed for every company-year in every formation year.
# Changing one is a change to the instrument and belongs in the changelog.

MIN_CHANNEL_MENTIONS = 2
# Why 2 and not 1: a single mention is as often a cost line or a boilerplate
# risk factor as a channel. TransAct Technologies' FY2014 10-K says
# "e-commerce" exactly once, inside a list of promotional marketing expenses.
# Why not higher: the test is meant to detect ABSENCE of a disclosed channel,
# not to rank companies by how much they talk about one. Ranking is A2's job.

SCAN_SEED = 20261011
SCAN_STAGE_OFFSET = 4
# The shortlist is written in a SEEDED RANDOM order, per formation year, and A2
# works it front to back. This is not decoration. The filter keeps roughly a
# third of the panel - still far more company-years than anyone will hand-score
# in one pass - so in practice A2 scores a PREFIX of the shortlist. A prefix of
# a seeded permutation is a random sample of the keep set, so whatever A2 gets
# through estimates the keep set's pass rate without being the alphabet, and
# the sample is reproducible from the seed. An order by channel mentions or by
# market cap would have made the prefix the companies that look best on
# criterion 1, which is A2's judgement pre-empted by a sort.
#
# Seeding follows references/numeric-stack.md s3, which is binding: one
# random.Random(seed) per stage, drawn through randrange only (never shuffle or
# sample - their internals are not stream-stable across interpreters), seed
# fixed in code and never drawn from the data. That file assigns stage offsets
# 0-3 to bootstrap, null, calibration and degradation sampling; offset 4 is
# claimed here for shortlist ordering and is recorded in the manifest. It lands
# with the integration session on claude/frosty-gould-047c87.

SEG_MAX = 4
# Cut at five or more reportable segments. Deliberately loose. Helen of Troy
# has four and is kept, then fails A2 at 49.0 on the evidence - which is the
# behaviour wanted: the filter removes what cannot carry the signal, A2 judges
# what can. A tighter bar here would be A2's judgement made mechanically and
# worse. Note that no segment count makes a 60/100 pass arithmetically
# impossible (c2 and c3 together are only 40 of 100), so this threshold cannot
# be justified as "could not have passed anyway" and is not presented as such.
#
# One further test was considered and rejected: cutting on a disclosed SKU
# count ("over 30,000 SKUs" is a criterion-2 zero). It is structural and
# point-in-time, but almost no filing states a number, so it would fire only
# on the companies that happen to disclose one - penalising disclosure rather
# than applying one rule to everyone. Recorded here so the omission is a
# decision and not an oversight.

# ------------------------------------------------------------------ THE PROBES
# Frozen regexes. The same questions, in the same words, of every filing.

# How many segments the registrant says it has. The qualifier is captured
# because "reportable" is the term the segment standard uses and the one that
# matters; "operating" and "business" are looser and are a fallback only.
SEGMENT_RE = (
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"single|[0-9]{1,2})\s+"
    r"(reportable|reporting|operating|business)\s+"
    r"(?:operating\s+|business\s+)?segments?\b"
)
SEGMENT_WORDS = {"single": 1, "one": 1, "two": 2, "three": 3, "four": 4,
                 "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11, "twelve": 12}
SEGMENT_STRONG = ("reportable", "reporting")

# Channel disclosure, in three families. A match in any family counts.
CHANNEL_PROBES = {
    "amazon": r"\bAmazon(?:\.com)?\b",
    "ecommerce": (r"\be-?commerce\b"
                  r"|\bonline (?:sales|sale|retail|retailers?|channel|"
                  r"channels|store|stores|marketplace|marketplaces)\b"
                  r"|\binternet (?:sales|retail|retailers?)\b"
                  r"|\bweb(?:site)? sales\b"),
    "direct": (r"\bdirect[- ]to[- ]consumer\b"
               r"|\bour (?:own )?online stores?\b"
               r"|\bour (?:own )?(?:e-?commerce|direct) "
               r"(?:sites?|websites?|platforms?|channels?)\b"),
}
# Two probes that look obvious and are not, both dropped after reading what
# they actually matched in the panel:
#   "our website" - every 10-K says the code of conduct is available on our
#     website. It matched NetApp's investor-relations boilerplate twice and
#     would have kept an enterprise storage vendor on a channel test.
#   bare "DTC" - in a 10-K that is usually the Depository Trust Company, in
#     the book-entry and transfer-agent discussion. Where DTC does mean the
#     channel it is nearly always introduced as "direct-to-consumer (DTC)",
#     which the spelled-out probe already catches.

# Matches the probes find that are NOT a consumer channel. Fixed list, applied
# to every company identically. Each entry is here because it was observed
# producing a false channel on a real filing in the panel, named in the
# changelog - not because of what it did to a particular company's verdict.
CHANNEL_EXCLUDE = (
    # Amazon as cloud infrastructure or a developer platform. NetApp's FY2015
    # 10-K mentions Amazon three times, every one of them AWS.
    r"Amazon(?:\.com)?[,\s]*(?:Inc\.?[,\s]*)?(?:Web Services|AWS|S3|"
    r"Simple Storage|Simple Queue|Machine Image|Elastic|EC2|Redshift|"
    r"CloudFront|DynamoDB|Kinesis|Glacier|Lambda|Aurora|Alexa|Echo|"
    r"Fire ?TV|Kindle Direct|Marketplace Web Service)"
    r"|\bAWS\b"
    # Direct-to-consumer as pharmaceutical advertising regulation. Every
    # clinical-stage registrant under SIC 2834 carries this sentence.
    r"|direct[- ]to[- ]consumer (?:advertis\w+|promotion\w*|marketing|"
    r"communication\w*)"
)

# Is the channel QUANTIFIED - a share of sales attached to it. Recorded, never
# a cut: criterion 1 wants a quantified share, but A2 is what judges whether
# an unquantified disclosure is worth 1 or 4, and the only company-year A2 has
# passed so far has an undisclosed Amazon share.
QUANTIFIED_RE = (
    r"(?:[0-9]{1,3}(?:\.[0-9]{1,2})?\s*(?:%|percent)[^.]{0,140}?"
    r"(?:e-?commerce|online|internet|direct[- ]to[- ]consumer|Amazon|DTC)"
    r"|(?:e-?commerce|online|internet|direct[- ]to[- ]consumer|Amazon|DTC)"
    r"[^.]{0,140}?[0-9]{1,3}(?:\.[0-9]{1,2})?\s*(?:%|percent))"
)

WINDOW = 150           # chars either side of a kept match
MAX_SNIPPETS = 2       # per family, per company-year, in the evidence file

CUT_NO_FILING = "no_filing_at_formation"
CUT_NO_CHANNEL = "no_consumer_channel_disclosed"
CUT_SEGMENTS = "segment_sprawl"
CUT_UNREADABLE = "filing_unreadable"

SIC_GROUP = {code: group for group, codes in sic_consumer.GROUPS.items()
             for code in codes}


def die(msg):
    sys.exit("prefilter_panel: " + msg)


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ------------------------------------------------------------------ the panel


def load_panel(path):
    """Every in_band company-year, in panel order. The panel is read, never
    written: it is hashed and a committed result row points at that hash.
    """
    if not os.path.exists(path):
        die("no panel at %s. Run screen_rolling.py first." % path)
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if str(r.get("in_band", "")).strip().lower() == "true":
                rows.append(r)
    if not rows:
        die("%s holds no in_band rows." % path)
    return rows


def key_of(row):
    return "%s@%s" % (row["ticker"], row["formation_date"][:4])


# ------------------------------------------------------------- the measurement


def segments_from(text):
    """(count, basis, matches) - what the filing says about its own segments.

    Prefers the statements that use the word the segment standard uses. Takes
    the MAX of those, because a filing that names four reportable segments in
    its segment note and one somewhere else has four; taking the minimum would
    read a stale or partial sentence as pure-play. The full match distribution
    is recorded so a human can see what the number was read from.
    """
    found = Counter()
    for m in re.finditer(SEGMENT_RE, text, re.I):
        word = m.group(1).lower()
        n = SEGMENT_WORDS.get(word)
        if n is None:
            try:
                n = int(word)
            except ValueError:
                continue
        if not 1 <= n <= 30:
            continue
        found[(n, m.group(2).lower())] += 1
    if not found:
        return None, "unknown", ""
    strong = [n for (n, q) in found if q in SEGMENT_STRONG]
    basis = "reportable" if strong else "other"
    count = max(strong) if strong else max(n for (n, _q) in found)
    matches = ";".join("%s:%d=%d" % (q, n, c)
                       for (n, q), c in sorted(found.items(),
                                               key=lambda kv: (kv[0][1], kv[0][0])))
    return count, basis, matches


def channel_from(text):
    """Surviving channel mentions per family, plus evidence windows.

    A match is dropped when CHANNEL_EXCLUDE matches the same span - that is
    how Amazon-the-cloud-vendor and direct-to-consumer-advertising-regulation
    are kept out of a channel count, uniformly and without reference to the
    company.
    """
    excluded_spans = [(m.start(), m.end())
                      for m in re.finditer(CHANNEL_EXCLUDE, text, re.I)]
    counts, snippets, dropped = {}, {}, 0
    for family, rx in CHANNEL_PROBES.items():
        kept = []
        for m in re.finditer(rx, text, re.I):
            if any(a <= m.start() < b for a, b in excluded_spans):
                dropped += 1
                continue
            kept.append(m)
        counts[family] = len(kept)
        snippets[family] = [
            re.sub(r"\s+", " ",
                   text[max(0, m.start() - WINDOW):
                        min(len(text), m.end() + WINDOW)]).strip()
            for m in kept[:MAX_SNIPPETS]]
    quant = [re.sub(r"\s+", " ", m.group(0)).strip()
             for m in re.finditer(QUANTIFIED_RE, text, re.I)]
    return counts, snippets, dropped, quant


def measure_text(text):
    seg_n, seg_basis, seg_matches = segments_from(text)
    counts, snippets, dropped, quant = channel_from(text)
    return {
        "probe_version": PROBE_VERSION,
        "chars": len(text),
        "segments": seg_n,
        "segments_basis": seg_basis,
        "segments_matches": seg_matches,
        "channel_counts": counts,
        "channel_total": sum(counts.values()),
        "channel_excluded": dropped,
        "channel_snippets": {k: v for k, v in snippets.items() if v},
        "quantified": bool(quant),
        "quantified_snippets": quant[:MAX_SNIPPETS],
    }


def cache_dir(cache):
    d = os.path.join(cache, "prefilter")
    os.makedirs(d, exist_ok=True)
    return d


def filings_cached(cache, cik):
    """score_eligibility.in_force_filing() refetches a company's filing index
    on every call. One company-year is one call, and a company appears in up to
    seven formations, so the index is cached here and the resolution is replayed
    from it. The resolution rule itself is A2's, unchanged.
    """
    p = os.path.join(cache_dir(cache), "subs_%s.json" % cik)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except ValueError:
            pass
    from score_eligibility import evidence_filings
    try:
        filings, name = evidence_filings(cik)
        val = {"filings": filings, "name": name}
    except SystemExit as e:
        # evidence_filings exits when EDGAR has no submissions document. One
        # registrant must not end a 3,522-row run, and "the index could not be
        # read" is a different thing from "nothing was filed before formation",
        # so it is recorded as its own condition and surfaces as unreadable.
        val = {"filings": [], "name": "", "error": str(e)}
    except Exception as e:                           # noqa: BLE001
        val = {"filings": [], "name": "", "error": "%s: %s" % (type(e).__name__, e)}
    with open(p, "w") as f:
        json.dump(val, f)
    return val


def resolve_in_force(filings, formation):
    """A2's rule, replayed against the cached index. Kept identical to
    score_eligibility.in_force_filing: annual reports win, the registration
    statement is the fallback, and nothing filed after formation is eligible.
    """
    from score_eligibility import ANNUAL_FORMS_AMENDED, FALLBACK_FORMS
    at_or_before = [f for f in filings if f["filing_date"] <= formation]
    annual = [f for f in at_or_before if f["form"] in ANNUAL_FORMS_AMENDED]
    if annual:
        return dict(annual[-1], source="annual")
    fallback = [f for f in at_or_before if f["form"] in FALLBACK_FORMS]
    if fallback:
        return dict(fallback[-1], source="prospectus")
    return None


def measurement_path(cache, cik, accn):
    return os.path.join(cache_dir(cache),
                        "m_%s_%s.json" % (cik, accn.replace("-", "")))


def measure_filing(cache, cik, filing, keep_text=False, textdir=None):
    """Cached per (cik, accession): the same document in force at two
    formations is measured once, and the measurement is the same both times.
    """
    p = measurement_path(cache, cik, filing["accession"])
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except ValueError:
            pass
    if not filing.get("primary_document"):
        m = {"error": "no primaryDocument recorded for %s" % filing["accession"]}
    else:
        try:
            raw = fetch(archive_url(cik, filing["accession"],
                                    filing["primary_document"]))
        except Exception as e:                       # noqa: BLE001
            raw = None
            err = "%s: %s" % (type(e).__name__, e)
        else:
            err = "fetch returned nothing"
        if not raw:
            m = {"error": err}
        else:
            parser = _Flatten()
            parser.feed(raw.decode("utf-8", "replace")
                        if isinstance(raw, bytes) else raw)
            text = parser.text()
            m = measure_text(text)
            if keep_text and textdir:
                os.makedirs(textdir, exist_ok=True)
                with open(os.path.join(textdir, "%s_%s.txt"
                                       % (cik, filing["accession"].replace("-", ""))),
                          "w", encoding="utf-8") as f:
                    f.write(text)
    m["accession"] = filing["accession"]
    m["filing_date"] = filing["filing_date"]
    m["form"] = filing["form"]
    m["source"] = filing.get("source", "")
    with open(p, "w") as f:
        json.dump(m, f, sort_keys=True)
    return m


def cmd_measure(args):
    ua()
    rows = load_panel(args.panel)
    if args.year:
        rows = [r for r in rows if r["formation_date"].startswith(str(args.year))]
    if args.ticker:
        want = set(t.strip().upper() for t in args.ticker)
        rows = [r for r in rows if r["ticker"].upper() in want]
    if args.reverse:
        # Two processes, one from each end of the panel, meet in the middle and
        # find the rest already cached. Measurement is dominated by parsing a
        # 3MB filing, not by the request rate, so this halves the wall clock
        # while leaving the combined request rate well inside SEC's limit.
        rows = rows[::-1]
    if args.limit:
        rows = rows[:args.limit]
    print("%d in_band company-year(s) to measure" % len(rows))
    print("probes: %s · evidence: the filing in force at each formation\n"
          % PROBE_VERSION)

    t0 = time.time()
    done = errors = no_filing = 0
    for n, row in enumerate(rows, 1):
        idx = filings_cached(args.cache, row["cik"])
        f = resolve_in_force(idx["filings"], row["formation_date"])
        if idx.get("error"):
            errors += 1
        elif f is None:
            no_filing += 1
        else:
            m = measure_filing(args.cache, row["cik"], f,
                               args.keep_text, args.textdir)
            if m.get("error"):
                errors += 1
            done += 1
        if n % 25 == 0 or n == len(rows):
            print("  %d/%d · %d measured · %d no filing · %d unreadable · %.0fs"
                  % (n, len(rows), done, no_filing, errors, time.time() - t0))
    print("\nmeasurement cached under %s" % cache_dir(args.cache))
    print("Next: build (reads the cache only, never the network).")


# ------------------------------------------------------------------ the build


PANEL_COLS = ["ticker", "cik", "company_name", "sic", "sic_group",
              "formation_date", "filing_form", "filing_date", "accession",
              "filing_source", "segments", "segments_basis",
              "segments_matches", "amazon_mentions", "ecommerce_mentions",
              "direct_mentions", "channel_mentions", "channel_excluded",
              "channel_quantified", "kept", "cut_reason"]

SHORTLIST_COLS = ["scan_order", "ticker", "cik", "formation_date", "accession",
                  "filing_date", "segments", "channel_mentions",
                  "channel_quantified"]


def permute(items, rng):
    """Fisher-Yates on randrange only. random.shuffle is forbidden by
    numeric-stack.md s3: its algorithm is a CPython implementation detail, so
    the same seed can produce a different order on a different interpreter,
    and an order that is not reproducible is not a declared order.
    """
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = rng.randrange(i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def decide(m):
    """kept, cut_reason. The whole decision, in one place, in this order."""
    if m is None:
        return False, CUT_NO_FILING
    if m.get("error"):
        return False, CUT_UNREADABLE
    if m.get("channel_total", 0) < MIN_CHANNEL_MENTIONS:
        return False, CUT_NO_CHANNEL
    seg = m.get("segments")
    if seg is not None and seg > SEG_MAX:
        return False, CUT_SEGMENTS
    return True, ""


def cmd_build(args):
    panel_rows = load_panel(args.panel)
    panel_sha = sha256_file(args.panel)
    out_rows, evidence, missing = [], {}, []

    for row in panel_rows:
        idx_path = os.path.join(cache_dir(args.cache), "subs_%s.json" % row["cik"])
        if not os.path.exists(idx_path):
            missing.append(key_of(row))
            continue
        try:
            with open(idx_path) as f:
                idx = json.load(f)
        except ValueError:
            # A half-written cache file counts as unmeasured, so the build
            # refuses rather than reading a truncated index as "no filings"
            # and cutting a company-year that has one.
            missing.append(key_of(row))
            continue
        f_in_force = resolve_in_force(idx["filings"], row["formation_date"])
        m = None
        if idx.get("error"):
            m = {"error": "filing index: %s" % idx["error"]}
        elif f_in_force is not None:
            mp = measurement_path(args.cache, row["cik"],
                                  f_in_force["accession"])
            if not os.path.exists(mp):
                missing.append(key_of(row))
                continue
            try:
                with open(mp) as f:
                    m = json.load(f)
            except ValueError:
                missing.append(key_of(row))
                continue
        kept, reason = decide(m)
        cc = (m or {}).get("channel_counts") or {}
        out_rows.append({
            "ticker": row["ticker"], "cik": row["cik"],
            "company_name": row["company_name"], "sic": row["sic"],
            "sic_group": SIC_GROUP.get(row["sic"], ""),
            "formation_date": row["formation_date"],
            "filing_form": (f_in_force or {}).get("form", ""),
            "filing_date": (f_in_force or {}).get("filing_date", ""),
            "accession": (f_in_force or {}).get("accession", ""),
            "filing_source": (f_in_force or {}).get("source", ""),
            "segments": "" if not m or m.get("segments") is None
                        else m["segments"],
            "segments_basis": (m or {}).get("segments_basis", ""),
            "segments_matches": (m or {}).get("segments_matches", ""),
            "amazon_mentions": cc.get("amazon", ""),
            "ecommerce_mentions": cc.get("ecommerce", ""),
            "direct_mentions": cc.get("direct", ""),
            "channel_mentions": "" if not m else m.get("channel_total", ""),
            "channel_excluded": "" if not m else m.get("channel_excluded", ""),
            "channel_quantified": "" if not m else bool(m.get("quantified")),
            "kept": kept, "cut_reason": reason,
        })
        ev = {"formation_date": row["formation_date"],
              "cik": row["cik"],
              "kept": kept, "cut_reason": reason}
        if f_in_force:
            ev["filing"] = {k: f_in_force.get(k) for k in
                            ("form", "filing_date", "accession", "source",
                             "primary_document")}
            ev["filing"]["url"] = archive_url(
                row["cik"], f_in_force["accession"],
                f_in_force.get("primary_document") or "")
        if m:
            if m.get("error"):
                ev["error"] = m["error"]
            if m.get("segments_matches"):
                ev["segments_matches"] = m["segments_matches"]
            if m.get("channel_snippets"):
                ev["channel_snippets"] = m["channel_snippets"]
            if m.get("quantified_snippets"):
                ev["quantified_snippets"] = m["quantified_snippets"]
        evidence[key_of(row)] = ev

    if missing and not args.partial:
        die("%d of %d in_band company-years have no cached measurement: %s%s\n"
            "The panel is filtered whole or not at all - a shortlist cut from a "
            "partial measurement understates what was considered, and nothing "
            "downstream could tell. Run `measure` to completion, or pass "
            "--partial for an inspection build that writes no shortlist."
            % (len(missing), len(panel_rows), ", ".join(missing[:8]),
               " ..." if len(missing) > 8 else ""))

    out_rows.sort(key=lambda r: (r["formation_date"], r["ticker"]))
    panel_out = os.path.join(args.datadir, "prefilter_panel.csv")
    os.makedirs(args.datadir, exist_ok=True)
    with open(panel_out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PANEL_COLS)
        w.writeheader()
        w.writerows(out_rows)
    panel_out_sha = sha256_file(panel_out)

    years = sorted({r["formation_date"][:4] for r in out_rows})
    per_year, shortlists = {}, {}
    import random
    rng = random.Random(SCAN_SEED + SCAN_STAGE_OFFSET)
    for y in years:
        ys = [r for r in out_rows if r["formation_date"].startswith(y)]
        kept = [r for r in ys if r["kept"]]
        cuts = Counter(r["cut_reason"] for r in ys if not r["kept"])
        per_year[y] = {"in_band": len(ys), "kept": len(kept),
                       "cut": len(ys) - len(kept), "cut_reasons": dict(cuts)}
        # One generator, years in ascending order: deterministic, and the same
        # panel plus the same seed gives the same order every time.
        scan = permute(sorted(kept, key=lambda r: r["ticker"]), rng)
        if missing:
            continue
        sl = os.path.join(args.datadir, "shortlist_%s.csv" % y)
        with open(sl, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SHORTLIST_COLS)
            w.writeheader()
            for n, r in enumerate(scan, 1):
                row = {k: r[k] for k in SHORTLIST_COLS if k != "scan_order"}
                row["scan_order"] = n
                w.writerow(row)
        sha = sha256_file(sl)
        shortlists[y] = {"file": os.path.basename(sl), "sha256": sha,
                         "company_years": len(kept)}
        with open(sl.replace(".csv", ".hash"), "w") as f:
            f.write("%s  %s  formation=%s-01-01  kept=%d/%d  "
                    "panel=%s:%s  probes=%s  min_channel_mentions=%d  "
                    "seg_max=%d  scan_seed=%d\n"
                    % (sha, os.path.basename(sl), y, len(kept), len(ys),
                       os.path.basename(args.panel), panel_sha[:12],
                       PROBE_VERSION, MIN_CHANNEL_MENTIONS, SEG_MAX,
                       SCAN_SEED + SCAN_STAGE_OFFSET))

    ev_path = os.path.join(args.datadir, "prefilter.evidence.json")
    with open(ev_path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=1, sort_keys=True)

    total_kept = sum(1 for r in out_rows if r["kept"])
    manifest = {
        "probe_version": PROBE_VERSION,
        "thresholds": {"min_channel_mentions": MIN_CHANNEL_MENTIONS,
                       "seg_max": SEG_MAX},
        "scan_order": {"rng": "stdlib-mt19937", "seed": SCAN_SEED,
                       "stage_offset": SCAN_STAGE_OFFSET,
                       "effective_seed": SCAN_SEED + SCAN_STAGE_OFFSET,
                       "method": "fisher-yates on randrange, per formation "
                                 "year, years ascending",
                       "why": "A2 scores a prefix of the shortlist; a prefix "
                              "of a seeded permutation is a random sample of "
                              "the keep set"},
        "probes": {"segment_re": SEGMENT_RE,
                   "channel_probes": CHANNEL_PROBES,
                   "channel_exclude": CHANNEL_EXCLUDE,
                   "quantified_re": QUANTIFIED_RE},
        "panel": {"file": os.path.basename(args.panel), "sha256": panel_sha,
                  "in_band_company_years": len(panel_rows)},
        "prefilter_panel": {"file": os.path.basename(panel_out),
                            "sha256": panel_out_sha,
                            "rows": len(out_rows)},
        "shortlists": shortlists,
        "kept_total": total_kept,
        "cut_total": len(out_rows) - total_kept,
        "cut_reasons": dict(Counter(r["cut_reason"] for r in out_rows
                                    if not r["kept"])),
        "per_formation_year": per_year,
        "partial": bool(missing),
        "unmeasured": sorted(missing),
    }
    man_path = os.path.join(args.datadir, "prefilter.manifest.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    with open(panel_out.replace(".csv", ".hash"), "w") as f:
        f.write("%s  %s  rows=%d  kept=%d  cut=%d  panel=%s:%s  probes=%s  "
                "min_channel_mentions=%d  seg_max=%d  partial=%s\n"
                % (panel_out_sha, os.path.basename(panel_out), len(out_rows),
                   total_kept, len(out_rows) - total_kept,
                   os.path.basename(args.panel), panel_sha[:12], PROBE_VERSION,
                   MIN_CHANNEL_MENTIONS, SEG_MAX, bool(missing)))

    print("panel %s (sha256 %s)" % (os.path.basename(args.panel),
                                    panel_sha[:12]))
    print("probes %s · min_channel_mentions=%d · seg_max=%d"
          % (PROBE_VERSION, MIN_CHANNEL_MENTIONS, SEG_MAX))
    print("\n  year   in band    kept    cut   %s"
          % "  ".join("%s" % r for r in
                      (CUT_NO_CHANNEL, CUT_SEGMENTS, CUT_NO_FILING,
                       CUT_UNREADABLE)))
    for y in years:
        d = per_year[y]
        print("  %s   %7d  %6d %6d   %s"
              % (y, d["in_band"], d["kept"], d["cut"],
                 "  ".join("%*d" % (len(r), d["cut_reasons"].get(r, 0))
                           for r in (CUT_NO_CHANNEL, CUT_SEGMENTS,
                                     CUT_NO_FILING, CUT_UNREADABLE))))
    print("  %s   %7d  %6d %6d" % ("all ", len(out_rows), total_kept,
                                   len(out_rows) - total_kept))

    # Recorded, never used as a cut (see the module docstring). Printed because
    # where the survivors sit is the first thing anyone will ask of the funnel.
    groups = Counter(r["sic_group"] for r in out_rows)
    kept_g = Counter(r["sic_group"] for r in out_rows if r["kept"])
    print("\n  kept by SIC group (recorded, not a filter input):")
    for g, tot in sorted(groups.items(), key=lambda kv: -kept_g[kv[0]]):
        print("    %-22s %4d / %4d" % (g or "(unmapped)", kept_g[g], tot))
    print("\n  -> %s  (sha256 %s)" % (panel_out, panel_out_sha))
    print("  -> %s" % ev_path)
    print("  -> %s" % man_path)
    if missing:
        print("\n  !! PARTIAL: %d company-years unmeasured. No shortlist "
              "written." % len(missing))
        return
    for y in years:
        print("  -> data/shortlist_%s.csv  %d company-years  (sha256 %s)"
              % (y, shortlists[y]["company_years"], shortlists[y]["sha256"][:12]))
    print("\n  Shortlist order is a seeded permutation (seed %d, offset %d)."
          % (SCAN_SEED, SCAN_STAGE_OFFSET))
    print("  A2 works it front to back, so a partial scoring is a RANDOM")
    print("  SAMPLE of the keep set rather than the start of the alphabet.")
    print("  At 6-8k tokens per company-year, %d is not one sitting's work;"
          % total_kept)
    print("  say how far down the order you got, in the changelog.")
    print("\n  A2 scores the shortlist, one formation year at a time:")
    print("    python3 %s queue --year %s --shortlist data/shortlist_%s.csv"
          % (os.path.join(os.path.relpath(HERE), "score_eligibility.py"),
             years[0], years[0]))
    print("\n  Every cut company-year carries a structural reason in")
    print("  prefilter_panel.csv and its evidence in prefilter.evidence.json.")
    print("  It carries NO scorecard score: this filter does not score, and")
    print("  the cut set is not independent of A2's criteria 1 and 3. See the")
    print("  changelog entry for what that does and does not bound.")


# ------------------------------------------------------------------- the rest


def cmd_show(args):
    ua()
    rows = load_panel(args.panel)
    want = args.ticker[0].upper()
    year = str(args.year or "")
    cand = [r for r in rows if r["ticker"].upper() == want
            and (not year or r["formation_date"].startswith(year))]
    if not cand:
        die("%s is not an in_band company-year%s"
            % (want, " at %s-01-01" % year if year else ""))
    for row in cand:
        f, name, n_total = in_force_filing(row["cik"], row["formation_date"])
        print("=" * 72)
        print("%s · %s · formation %s"
              % (row["ticker"], name or row["company_name"],
                 row["formation_date"]))
        if f is None:
            print("  NO filing on or before formation (%d exist, all later)"
                  % n_total)
            print("  -> cut: %s" % CUT_NO_FILING)
            continue
        print("  in force: %s filed %s  %s%s"
              % (f["form"], f["filing_date"], f["accession"],
                 "  [registration statement]" if f["source"] == "prospectus"
                 else ""))
        print("  %s" % archive_url(row["cik"], f["accession"],
                                   f["primary_document"]))
        m = measure_filing(args.cache, row["cik"], f, args.keep_text,
                           args.textdir)
        if m.get("error"):
            print("  UNREADABLE: %s" % m["error"])
            continue
        kept, reason = decide(m)
        print("  segments: %s (%s) from %s"
              % (m["segments"], m["segments_basis"],
                 m["segments_matches"] or "no statement found"))
        print("  channel mentions: %s  total=%d  excluded=%d  quantified=%s"
              % (m["channel_counts"], m["channel_total"],
                 m["channel_excluded"], m["quantified"]))
        print("  -> %s%s" % ("KEEP" if kept else "CUT", "" if kept
                             else " (%s)" % reason))
        for fam, sns in sorted((m.get("channel_snippets") or {}).items()):
            for s in sns:
                print("\n  [%s] ...%s..." % (fam, s))
        for s in m.get("quantified_snippets") or []:
            print("\n  [quantified] %s" % s)


# A deliberately wider net than the probes, for auditing cuts. If this finds a
# real channel disclosure in a filing the narrow probes cut, the probes are
# wrong and that is a defect to report - not a threshold to move.
AUDIT_WIDE = (r"\bamazon\b|\be-?commerce\b|\bonline\b|\binternet\b"
              r"|\bweb ?sites?\b|\bdirect[- ]to[- ]consumer\b|\bmarketplaces?\b"
              r"|\bdot-?com\b|\be-?tailer")


def cmd_audit(args):
    """Re-read a seeded sample of decided company-years with a wider net.

    A filter checked against its own matched snippets is checked against
    nothing. This refetches the filing and greps it with AUDIT_WIDE, so a
    channel disclosure the frozen probes missed shows up as text a human can
    read. The sample is seeded, so it is not a hand-picked set of easy cases.
    """
    ua()
    import random
    pf = os.path.join(args.datadir, "prefilter_panel.csv")
    if not os.path.exists(pf):
        die("no %s. Run build first." % pf)
    rows = list(csv.DictReader(open(pf, newline="", encoding="utf-8")))
    rng = random.Random(args.seed)
    n = args.sample

    def draw(pool):
        pool = sorted(pool, key=lambda r: (r["formation_date"], r["ticker"]))
        return [pool[i] for i in sorted(
            set(rng.randrange(len(pool)) for _ in range(n * 3)))[:n]] \
            if pool else []

    groups = [
        ("CUT · %s" % CUT_NO_CHANNEL,
         [r for r in rows if r["cut_reason"] == CUT_NO_CHANNEL]),
        ("CUT · %s" % CUT_SEGMENTS,
         [r for r in rows if r["cut_reason"] == CUT_SEGMENTS]),
        ("KEPT", [r for r in rows if r["kept"] == "True"]),
    ]
    for label, pool in groups:
        sample = draw(pool)
        print("\n" + "#" * 72)
        print("# %s · %d of %d, seed %d" % (label, len(sample), len(pool),
                                            args.seed))
        print("#" * 72)
        for r in sample:
            print("\n%s @ %s · %s · %s %s %s"
                  % (r["ticker"], r["formation_date"][:4],
                     r["company_name"][:40], r["filing_form"],
                     r["filing_date"], r["accession"]))
            print("  recorded: segments=%s(%s) channel=%s amazon=%s ecom=%s "
                  "direct=%s excluded=%s"
                  % (r["segments"] or "?", r["segments_basis"],
                     r["channel_mentions"], r["amazon_mentions"],
                     r["ecommerce_mentions"], r["direct_mentions"],
                     r["channel_excluded"]))
            idx = filings_cached(args.cache, r["cik"])
            f = resolve_in_force(idx["filings"], r["formation_date"])
            if not f or not f.get("primary_document"):
                print("  no primary document to re-read")
                continue
            raw = fetch(archive_url(r["cik"], f["accession"],
                                    f["primary_document"]))
            if not raw:
                print("  could not refetch")
                continue
            parser = _Flatten()
            parser.feed(raw.decode("utf-8", "replace"))
            text = parser.text()
            hits = list(re.finditer(AUDIT_WIDE, text, re.I))
            print("  wide net: %d hit(s) over %dk chars"
                  % (len(hits), len(text) // 1000))
            shown, covered = 0, []
            for m in hits:
                if any(a <= m.start() <= b for a, b in covered):
                    continue
                a, b = max(0, m.start() - 170), min(len(text), m.end() + 170)
                covered.append((a, b))
                print("    - ...%s..."
                      % re.sub(r"\s+", " ", text[a:b]).strip())
                shown += 1
                if shown >= args.windows:
                    print("    (%d further hit(s) not shown)"
                          % (len(hits) - shown))
                    break
    print("\nRead these against what the filter recorded. A channel the "
          "probes missed is a defect to report, not a threshold to move.")


def cmd_verify(args):
    """Re-derive every decision from the cache and the thresholds, and check
    the written artefacts against it. A disagreement means an artefact was
    edited by hand or the thresholds moved under it.
    """
    panel_sha = sha256_file(args.panel)
    pf = os.path.join(args.datadir, "prefilter_panel.csv")
    man = os.path.join(args.datadir, "prefilter.manifest.json")
    for p in (pf, man):
        if not os.path.exists(p):
            die("no %s. Run build first." % p)
    with open(man) as f:
        manifest = json.load(f)
    bad = []
    if manifest["panel"]["sha256"] != panel_sha:
        bad.append("manifest was built from panel sha %s but the panel on disk "
                   "is %s" % (manifest["panel"]["sha256"][:12], panel_sha[:12]))
    if manifest["thresholds"] != {"min_channel_mentions": MIN_CHANNEL_MENTIONS,
                                  "seg_max": SEG_MAX}:
        bad.append("manifest thresholds %s disagree with the script's %s"
                   % (manifest["thresholds"],
                      {"min_channel_mentions": MIN_CHANNEL_MENTIONS,
                       "seg_max": SEG_MAX}))
    if manifest["probe_version"] != PROBE_VERSION:
        bad.append("manifest probe_version %s != %s"
                   % (manifest["probe_version"], PROBE_VERSION))

    rows = list(csv.DictReader(open(pf, newline="", encoding="utf-8")))
    in_band = {key_of(r) for r in load_panel(args.panel)}
    seen = set()
    kept_by_year = Counter()
    for r in rows:
        k = "%s@%s" % (r["ticker"], r["formation_date"][:4])
        seen.add(k)
        if k not in in_band:
            bad.append("%s is in prefilter_panel.csv but is not an in_band "
                       "company-year" % k)
        if str(r["kept"]).lower() == "true":
            kept_by_year[r["formation_date"][:4]] += 1
            if r["cut_reason"]:
                bad.append("%s is kept but carries cut_reason %s"
                           % (k, r["cut_reason"]))
        elif not r["cut_reason"]:
            bad.append("%s is cut with no reason - every cut carries a "
                       "structural reason" % k)
    for k in sorted(in_band - seen):
        bad.append("%s is an in_band company-year and is absent from "
                   "prefilter_panel.csv" % k)

    for y, meta in sorted(manifest.get("shortlists", {}).items()):
        sl = os.path.join(args.datadir, meta["file"])
        if not os.path.exists(sl):
            bad.append("manifest names %s, which does not exist" % meta["file"])
            continue
        if sha256_file(sl) != meta["sha256"]:
            bad.append("%s sha256 %s != manifest %s - the file changed after "
                       "it was recorded" % (meta["file"],
                                            sha256_file(sl)[:12],
                                            meta["sha256"][:12]))
        n = len(list(csv.DictReader(open(sl, newline="", encoding="utf-8"))))
        if n != kept_by_year[y]:
            bad.append("%s holds %d rows but %d company-years are kept in %s"
                       % (meta["file"], n, kept_by_year[y], y))

    print("%s: %d rows, %d in_band company-years" % (pf, len(rows), len(in_band)))
    for y in sorted(kept_by_year):
        print("  %s  kept %d" % (y, kept_by_year[y]))
    if bad:
        for b in bad:
            print("  FAIL %s" % b)
        sys.exit("\n%d violation(s)." % len(bad))
    print("  contract OK")


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["measure", "build", "show", "audit",
                               "verify"])
    p.add_argument("--panel", default="data/universe_panel.csv")
    p.add_argument("--datadir", default="data")
    p.add_argument("--cache", default=".cache")
    p.add_argument("--year", type=int, help="restrict to one formation year")
    p.add_argument("--ticker", nargs="*", help="restrict to these tickers")
    p.add_argument("--limit", type=int, help="measure mode: stop after N rows")
    p.add_argument("--reverse", action="store_true",
                   help="measure mode: walk the panel from the end, so a "
                        "second process can work toward the first")
    p.add_argument("--keep-text", action="store_true",
                   help="also write the flattened filing text")
    p.add_argument("--textdir", default=".cache/filings")
    p.add_argument("--sample", type=int, default=6,
                   help="audit mode: company-years per group")
    p.add_argument("--windows", type=int, default=6,
                   help="audit mode: evidence windows printed per company-year")
    p.add_argument("--seed", type=int, default=SCAN_SEED + SCAN_STAGE_OFFSET,
                   help="audit mode: which sample to draw")
    p.add_argument("--partial", action="store_true",
                   help="build mode: allow unmeasured company-years; writes "
                        "the panel record for inspection but no shortlist")
    args = p.parse_args()

    if args.mode == "measure":
        cmd_measure(args)
    elif args.mode == "build":
        cmd_build(args)
    elif args.mode == "show":
        if not args.ticker:
            die("show mode needs --ticker")
        cmd_show(args)
    elif args.mode == "audit":
        cmd_audit(args)
    else:
        cmd_verify(args)


if __name__ == "__main__":
    main()
