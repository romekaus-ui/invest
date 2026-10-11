#!/usr/bin/env python3
"""Stage A2 - structural eligibility scorecard.

Scores companies in a point-in-time universe on the seven criteria in
references/company-selection.md s2, weighted to 100, pass bar 60.

Input is a formation year's slice of the company-year panel, optionally
narrowed to a SHORTLIST produced upstream by the mechanical pre-filter. The
pre-filter is a separate component and is not built here. A2 scores the set it
is handed; it never decides which companies reach it.

The unit is a COMPANY-YEAR. The panel re-forms every January 1, so a company
appearing in four formation years is scored four times, each against the filing
in force on that year's formation date. One output file per formation year.

This script owns three things the model must never do itself:

  1. ARITHMETIC. The weighting, the total and the pass/fail test are computed
     here and read from the output (CORE-PROMPT s1.3: no number that reaches a
     result is calculated in prose).
  2. THE POINT-IN-TIME BOUNDARY. `filing` resolves the 10-K *in force at
     formation* - the last annual report filed on or before formation_date -
     and refuses to name a later one. Every criterion's evidence date is
     re-checked at build time against the formation date, and a single
     violation stops the build (CORE-PROMPT s2, failure-modes F1).
  3. COMPLETENESS. The universe is scored whole or not at all. A build missing
     companies writes nothing unless `--partial` is passed, and a partial
     build is marked as such in the hash file and is not a universe.

What it deliberately does NOT do: decide a score. A 0-5 judgement per criterion
comes from the agent reading the filing, and is recorded with the accession it
came from. The script only refuses the ones that are impossible.

It also never touches review coverage. Coverage is A3, it runs afterwards, and
letting data availability influence selection is the bias the design exists to
prevent (company-selection.md s4).

Modes
-----
  queue    which in_band companies still need scoring (resumable)
  filing   resolve the 10-K in force at formation; --text to flatten it locally
  probe    bounded, fixed-regex evidence windows from that filing, per criterion
  build    validate judgements -> data/eligibility_<YEAR>.csv (+ .hash, evidence)
  verify   re-check an existing eligibility CSV against the contract

Output: data/eligibility_<FORMATION_YEAR>.csv       (data-contracts.md)
        data/eligibility_<FORMATION_YEAR>.hash
        data/eligibility_<FORMATION_YEAR>.evidence.json
Input:  data/universe_panel.csv  (preferred) or data/universe_<YEAR>.csv
        --shortlist <path>       tickers from the pre-filter, optional
        data/eligibility_<FORMATION_YEAR>.judgements.jsonl   (append-only)

Standard library only. Needs SEC_USER_AGENT (see screen_universe.py).
"""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen_universe import fetch, ua  # noqa: E402

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE = "https://data.sec.gov/submissions/{name}"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{doc}"

# references/company-selection.md s2. Order is the CSV column order.
# Weights sum to 100; a criterion contributes score/5 * weight.
CRITERIA = [
    ("c1_amazon_share",       25, "Amazon share of revenue"),
    ("c2_portfolio_narrow",   20, "Portfolio narrowness"),
    ("c3_pureplay_revenue",   20, "Pure-play revenue"),
    ("c4_b2b_oem_mix",        10, "Low B2B/OEM mix"),
    ("c5_quality_sensitive",  10, "Quality-sensitive durable"),
    ("c6_segment_disclosure", 10, "Segment disclosure"),
    ("c7_reseller_noise",      5, "Low 3P reseller noise"),
]
PASS_BAR = 60.0

# Fixed evidence probes, one set per criterion. Frozen deliberately: the same
# questions are asked of every company in the same words, so the evidence
# gathered does not vary with how interesting a company looks. Adding a probe
# is a change to the instrument and belongs in the changelog.
PROBES = {
    "c1_amazon_share": [
        r"\bAmazon\b",
        r"e-?commerce|\bonline\b|\binternet (?:sales|retail)",
        r"direct[- ]to[- ]consumer|\bDTC\b|our (?:own )?websites?",
        r"accounted for approximately [0-9]{1,3}[^.]{0,80}(?:net sales|revenue)",
        r"(?:largest|significant) customers?[^.]{0,120}(?:accounted|percent)",
    ],
    "c2_portfolio_narrow": [
        r"\b(?:our|we (?:market|sell|offer))[^.]{0,60}\b(?:brands|SKUs|"
        r"stock[- ]keeping units)\b",
        r"(?:approximately |over |more than )[0-9,]{2,9} (?:SKUs|products|"
        r"items|models)",
        r"product (?:lines|categories)[^.]{0,100}(?:include|consist)",
    ],
    "c3_pureplay_revenue": [
        r"(?:one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+) "
        r"(?:reportable|operating) segments?",
        r"we (?:operate|report)[^.]{0,80}segments?",
        r"segment(?:s)? (?:are|consist(?:s)? of|comprise)",
    ],
    "c4_b2b_oem_mix": [
        r"\bOEM\b|original equipment manufacturer",
        r"\b(?:enterprise|commercial|institutional|professional|industrial|"
        r"government) (?:customers|market|channel|sales|revenue)",
        r"licens(?:e|ing) revenue|royalt(?:y|ies) revenue",
        r"\bdistributors?\b|\bresellers?\b|value[- ]added reseller",
    ],
    "c5_quality_sensitive": [
        r"warrant(?:y|ies)[^.]{0,80}(?:cost|reserve|claims|accrual)",
        r"\breturns?\b[^.]{0,80}(?:rate|allowance|reserve|defect)",
        r"product (?:recall|defect|liability|quality)",
        r"repeat (?:purchase|customers)|brand loyalty",
    ],
    "c6_segment_disclosure": [
        r"net sales (?:revenue )?by (?:segment|product|category|geograph)",
        r"disaggregat(?:ion|ed) of revenue",
        r"the following table (?:summarizes|presents|sets forth)[^.]{0,90}"
        r"(?:segment|net sales|revenue)",
    ],
    "c7_reseller_noise": [
        r"(?:grey|gray)[- ]market|unauthoriz(?:ed|ed) (?:dealers?|sellers?|"
        r"resellers?|distribut)",
        r"counterfeit|knock[- ]?off|infring(?:ing|ement)[^.]{0,60}product",
        r"minimum (?:advertised|resale) pric",
    ],
}

PROBE_WINDOW = 180      # chars either side of a match
PROBE_MAX_PER_RE = 3    # matches kept per regex
PROBE_MAX_PER_CRIT = 6  # matches kept per criterion

ANNUAL_FORMS = ("10-K", "10-K405", "10-KSB", "20-F", "40-F")
# An amendment is evidence only if it was itself filed before formation.
ANNUAL_FORMS_AMENDED = ANNUAL_FORMS + tuple(f + "/A" for f in ANNUAL_FORMS)

# A company that listed shortly before formation has no annual report yet, and
# dropping it would remove exactly the recently-listed Amazon-native brands the
# rolling formation exists to capture (company-selection.md s1: YETI, Sonos,
# e.l.f.). Its registration statement or prospectus WAS public at formation and
# carries the same business, channel and segment description, so it is admitted
# as a clearly-labelled fallback. The boundary is unchanged: filed on or before
# formation, or not evidence at all.
FALLBACK_FORMS = ("S-1", "S-1/A", "424B1", "424B3", "424B4", "F-1", "F-1/A",
                  "10-12B", "10-12B/A", "S-4", "S-4/A")
EVIDENCE_FORMS = ANNUAL_FORMS_AMENDED + FALLBACK_FORMS


def die(msg):
    sys.exit("score_eligibility: " + msg)


# ---------------------------------------------------------------- universe


def universe_path(year, datadir):
    """The panel is the real universe (company-selection.md s1). The per-year
    files are the superseded narrow screen's output and are a fallback only.
    """
    panel = os.path.join(datadir, "universe_panel.csv")
    if os.path.exists(panel):
        return panel
    return os.path.join(datadir, "universe_%s.csv" % year)


def load_universe(year, datadir):
    """in_band rows for the formation on <year>-01-01, by ticker.

    Reads the company-year panel and takes that year's slice. A company
    appearing in several formation years is a separate company-year in each,
    scored separately against each year's filing.
    """
    path = universe_path(year, datadir)
    if not os.path.exists(path):
        die("no universe at %s. Run screen_rolling.py first (it writes "
            "data/universe_panel.csv)." % path)
    rows = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if str(r.get("in_band", "")).strip().lower() != "true":
                continue
            if not r["formation_date"].startswith(str(year)):
                continue
            rows[r["ticker"]] = r
    if not rows:
        die("%s holds no in_band rows for formation %s-01-01. The panel covers "
            "the years screen_rolling.py was run for." % (path, year))
    return rows


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_shortlist(path, uni, year):
    """Tickers handed down by the mechanical pre-filter.

    Accepts one ticker per line, or a CSV with a `ticker` column. A2 does not
    produce this list and does not second-guess it; it only refuses tickers
    that are not in_band company-years at this formation, because scoring one
    would be adding to the universe rather than scoring it.
    """
    if not os.path.exists(path):
        die("no shortlist at %s" % path)
    tickers = []
    with open(path, newline="", encoding="utf-8") as f:
        head = f.readline()
        f.seek(0)
        if "," in head or head.strip().lower().startswith("ticker"):
            for r in csv.DictReader(f):
                t = (r.get("ticker") or "").strip()
                if t:
                    tickers.append(t)
        else:
            for line in f:
                line = line.split("#")[0].strip()
                if line:
                    tickers.append(line)
    seen, ordered = set(), []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    stray = [t for t in ordered if t not in uni]
    if stray:
        die("shortlist names %d ticker(s) that are not in_band company-years at "
            "%s-01-01: %s\nA2 scores the universe, it never adds to it. Either "
            "the shortlist was built against a different panel or a different "
            "formation year." % (len(stray), year, ", ".join(stray)))
    if not ordered:
        die("shortlist %s is empty" % path)
    return ordered, sha256_file(path)


def judgements_path(year, datadir):
    return os.path.join(datadir, "eligibility_%s.judgements.jsonl" % year)


def load_judgements(year, datadir):
    """Append-only JSONL, last record per ticker wins (a re-score supersedes)."""
    path = judgements_path(year, datadir)
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except ValueError as e:
                die("%s line %d is not valid JSON: %s" % (path, n, e))
            if "ticker" not in rec:
                die("%s line %d has no ticker." % (path, n))
            rec["_line"] = n
            out[rec["ticker"]] = rec
    return out


# ---------------------------------------------------------------- filings


def evidence_filings(cik):
    """Every annual report and registration statement on record, date-sorted.

    Reads filings.recent plus the older paginated files, because a 10-K in
    force at a 2016 formation is usually outside the recent window.
    """
    body = fetch(SUBMISSIONS.format(cik=cik))
    if not body:
        die("EDGAR has no submissions document for CIK %s." % cik)
    data = json.loads(body)
    blocks = [data.get("filings", {}).get("recent", {})]
    for extra in data.get("filings", {}).get("files", []) or []:
        page = fetch(SUBMISSIONS_PAGE.format(name=extra["name"]))
        if page:
            blocks.append(json.loads(page))

    out = []
    for b in blocks:
        forms = b.get("form") or []
        for i, form in enumerate(forms):
            if form not in EVIDENCE_FORMS:
                continue

            def at(key):
                col = b.get(key) or []
                return col[i] if i < len(col) else ""

            out.append({
                "filing_date": at("filingDate"),
                "form": form,
                "accession": at("accessionNumber"),
                "report_date": at("reportDate"),
                "primary_document": at("primaryDocument"),
            })
    out = [r for r in out if r["filing_date"] and r["accession"]]
    out.sort(key=lambda r: r["filing_date"])
    return out, (data.get("name") or "")


def choose_in_force(filings, formation):
    """THE point-in-time boundary, as a pure function of a filing index.

    The last ANNUAL report filed on or before formation, or failing that the
    last registration statement. Annual reports always win; the fallback is
    reached only when none exists yet. It cannot return a later filing.

    Separated from the fetch so that A1b's pre-filter, which resolves the same
    question for up to seven formations per company from one cached index, can
    apply this rule rather than a second copy of it. One definition of "in
    force" in the project, in one place.
    """
    at_or_before = [f for f in filings if f["filing_date"] <= formation]
    annual = [f for f in at_or_before if f["form"] in ANNUAL_FORMS_AMENDED]
    if annual:
        return dict(annual[-1], source="annual")
    fallback = [f for f in at_or_before if f["form"] in FALLBACK_FORMS]
    if fallback:
        return dict(fallback[-1], source="prospectus")
    return None


def in_force_filing(cik, formation):
    """choose_in_force() against a freshly fetched filing index."""
    filings, name = evidence_filings(cik)
    return choose_in_force(filings, formation), name, len(filings)


def archive_url(cik, accn, doc):
    return ARCHIVE.format(cik=str(int(cik)), accn=accn.replace("-", ""), doc=doc)


class _Flatten(HTMLParser):
    """Enough HTML stripping to grep a 10-K. Not a renderer."""

    SKIP = {"script", "style", "head"}
    BREAK = {"p", "div", "br", "tr", "td", "th", "li", "table", "h1", "h2",
             "h3", "h4", "h5", "h6", "section", "span"}

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.split(":")[-1]
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.split(":")[-1]
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self):
        t = html.unescape("".join(self.parts))
        t = t.replace("\xa0", " ")
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r" *\n[ \n]*", "\n", t)
        return t.strip()


def flatten_filing(cik, accn, doc, dest):
    raw = fetch(archive_url(cik, accn, doc))
    if not raw:
        die("could not fetch %s" % archive_url(cik, accn, doc))
    try:
        body = raw.decode("utf-8", "replace")
    except AttributeError:
        body = raw
    p = _Flatten()
    p.feed(body)
    text = p.text()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    return dest, len(text)


def probe_text(text, only=None):
    """Bounded evidence windows. Same probes, same caps, every company.

    Returns {criterion: [snippet, ...]}. A 10-K flattens to ~400k characters;
    reading it whole costs ~100k tokens per company and nobody would do it 40
    times. This keeps a company's evidence packet to a few thousand tokens
    without letting the agent choose what to look for.
    """
    out = {}
    for crit, regexes in PROBES.items():
        if only and crit not in only:
            continue
        snippets, covered = [], []
        for rx in regexes:
            n = 0
            for m in re.finditer(rx, text, re.I):
                # One paragraph matched by three probes is one piece of
                # evidence, not three. Skip anything already inside an
                # emitted window.
                if any(a <= m.start() <= b for a, b in covered):
                    continue
                a = max(0, m.start() - PROBE_WINDOW)
                b = min(len(text), m.end() + PROBE_WINDOW)
                covered.append((a, b))
                snippets.append(re.sub(r"\s+", " ", text[a:b]).strip())
                n += 1
                if n >= PROBE_MAX_PER_RE or len(snippets) >= PROBE_MAX_PER_CRIT:
                    break
            if len(snippets) >= PROBE_MAX_PER_CRIT:
                break
        out[crit] = snippets
    return out


# ---------------------------------------------------------------- scoring


def weighted(scores):
    """The only place the total is computed. score/5 * weight, summed."""
    return round(sum(scores[k] / 5.0 * w for k, w, _ in CRITERIA), 2)


def validate(rec, uni_row, year):
    """Every reason a judgement is not usable. Returns a list of problems."""
    t = rec["ticker"]
    formation = uni_row["formation_date"]
    bad = []

    filing = rec.get("filing") or {}
    no_filing = bool(filing.get("no_filing") or rec.get("no_filing"))
    fdate = str(filing.get("filing_date", "") or (formation if no_filing else ""))
    if no_filing:
        # Nothing was public at formation. Legitimate for a company that listed
        # just before the screen; `filing` mode is what establishes it.
        if filing.get("accession"):
            bad.append("%s: no_filing is set but an accession is given - one or "
                       "the other" % t)
        if str(rec.get("scored_asof", formation)) != formation:
            bad.append("%s: with no_filing, scored_asof is the formation date "
                       "%s (the vintage of 'nothing was public')"
                       % (t, formation))
    elif not filing.get("accession"):
        bad.append("%s: filing.accession missing - every score cites a filing" % t)
    if not fdate:
        bad.append("%s: filing.filing_date missing" % t)
    elif fdate > formation:
        bad.append("%s: filing %s was filed %s, AFTER formation %s. This is "
                   "look-ahead (F1); re-score from the filing in force."
                   % (t, filing.get("accession"), fdate, formation))

    crit = rec.get("criteria") or {}
    scores = {}
    for key, _w, label in CRITERIA:
        c = crit.get(key)
        if c is None:
            bad.append("%s: %s (%s) not scored" % (t, key, label))
            continue
        v = c.get("score")
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 5:
            bad.append("%s: %s score %r is not an integer 0-5" % (t, key, v))
        else:
            scores[key] = v
        if not str(c.get("evidence", "")).strip():
            bad.append("%s: %s has no evidence string" % (t, key))
        src = str(c.get("from_filing_date", fdate) or "")
        if src and src > formation:
            bad.append("%s: %s cites evidence dated %s, after formation %s"
                       % (t, key, src, formation))

    c1 = crit.get("c1_amazon_share") or {}
    if "estimated" not in c1:
        bad.append("%s: c1_amazon_share needs an explicit `estimated` flag - an "
                   "undisclosed Amazon share is an estimate or it is a 0, never "
                   "an unmarked number (company-selection.md s2)" % t)
    if c1.get("score", 0) > 0 and c1.get("estimated") is None:
        bad.append("%s: c1_amazon_share scored above 0 with no estimated flag" % t)

    if not str(rec.get("reason", "")).strip():
        bad.append("%s: reason missing - it is required on every row, binding on "
                   "failures" % t)
    if not str(rec.get("scored_by", "")).strip():
        bad.append("%s: scored_by (model ID) missing" % t)

    # A performance word in a reason is how hindsight gets in. Structural
    # grounds only (CORE-PROMPT s2).
    banned = re.findall(
        r"\b(collapsed|collapse|outperform\w*|underperform\w*|went to zero|"
        r"stock (?:rose|fell|crashed)|bankrupt\w*|turned out|later (?:grew|fell|"
        r"rose|declined)|in hindsight|we know (?:now|today)|share price)\b",
        str(rec.get("reason", "")), re.I)
    if banned:
        bad.append("%s: reason contains outcome language %s. Exclusion is on "
                   "structural grounds only, never performance (CORE-PROMPT s2)"
                   % (t, sorted(set(w.lower() for w in banned))))

    scored_asof = str(rec.get("scored_asof", fdate) or "")
    if not scored_asof:
        bad.append("%s: scored_asof could not be determined" % t)
    elif scored_asof > formation:
        bad.append("%s: scored_asof %s is after formation %s (contract requires "
                   "<=)" % (t, scored_asof, formation))

    return bad, scores, scored_asof


def build(year, datadir, partial, out_path, shortlist_path=None):
    uni = load_universe(year, datadir)
    judg = load_judgements(year, datadir)
    formation = next(iter(uni.values()))["formation_date"]

    # The scope A2 is accountable for: the shortlist if one was handed down,
    # the whole in_band set otherwise.
    sl_hash = ""
    if shortlist_path:
        scope, sl_hash = load_shortlist(shortlist_path, uni, year)
        scope = set(scope)
    else:
        scope = set(uni)

    stray = sorted(set(judg) - set(uni))
    if stray:
        die("judgements for tickers that are not in_band at %s: %s. A2 scores "
            "the universe, it does not add to it." % (formation, ", ".join(stray)))
    # A judgement that already exists for an in_band company-year stays in
    # scope even when the pre-filter cut it. It was scored from the filing in
    # force at formation, before A1b existed, and discarding it because a later
    # component disagrees would let the cut rule decide which completed scores
    # count. Admitted, but never silently: the set is printed, written to the
    # hash line and listed in the evidence JSON, so the file is readable as
    # "the shortlist plus these named additions" and not as the shortlist's
    # result. Nothing NEW can be scored outside it - a judgement for a
    # company-year that is not in_band is still refused above, which is the
    # boundary that matters, since A2 may not add to the universe.
    admitted = sorted((set(judg) & set(uni)) - scope) if shortlist_path else []
    scope |= set(admitted)

    missing = sorted(scope - set(judg))
    if missing and not partial:
        die("%d of %d companies in scope are unscored: %s\n"
            "The scope is scored whole. Re-run with --partial only for a "
            "verification sample; a partial file is not a result."
            % (len(missing), len(scope), ", ".join(missing)))

    problems = []
    rows, evidence = [], {}
    for ticker in sorted(judg):
        rec = judg[ticker]
        bad, scores, scored_asof = validate(rec, uni[ticker], year)
        problems.extend(bad)
        if bad:
            continue
        total = weighted(scores)
        row = {"ticker": ticker}
        row.update({k: scores[k] for k, _w, _l in CRITERIA})
        row["c1_estimated"] = bool(rec["criteria"]["c1_amazon_share"]["estimated"])
        row["weighted_score"] = total
        row["eligible"] = total >= PASS_BAR
        row["reason"] = rec["reason"].strip()
        row["scored_asof"] = scored_asof
        rows.append(row)
        evidence[ticker] = {
            "cik": uni[ticker]["cik"],
            "formation_date": formation,
            "filing": rec["filing"],
            "scored_by": rec["scored_by"],
            "scored_at": rec.get("scored_at", ""),
            "criteria": {k: rec["criteria"][k] for k, _w, _l in CRITERIA},
        }

    if problems:
        sys.stderr.write("score_eligibility: %d judgement(s) rejected\n\n"
                         % len(problems))
        for p in problems:
            sys.stderr.write("  - %s\n" % p)
        sys.exit("\nNothing written. Fix the judgements and re-run.")

    out = os.path.abspath(out_path or
                          os.path.join(datadir, "eligibility_%s.csv" % year))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cols = (["ticker"] + [k for k, _w, _l in CRITERIA] +
            ["c1_estimated", "weighted_score", "eligible", "reason",
             "scored_asof"])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    uni_path = universe_path(year, datadir)
    if shortlist_path:
        scope_note = ("shortlist=%s:%s  of_in_band=%d/%d"
                      % (os.path.basename(shortlist_path), sl_hash[:12],
                         len(scope), len(uni)))
        if admitted:
            scope_note += ("  admitted_off_shortlist=%s" % "|".join(admitted))
    else:
        scope_note = "scope=all_in_band=%d" % len(uni)
    with open(out.replace(".csv", ".hash"), "w") as f:
        f.write("%s  %s  formation=%s  scored=%d/%d  %s  universe=%s:%s  "
                "partial=%s\n"
                % (digest, os.path.basename(out), formation, len(rows),
                   len(scope), scope_note, os.path.basename(uni_path),
                   sha256_file(uni_path)[:12], bool(missing)))

    ev_path = out.replace(".csv", ".evidence.json")
    with open(ev_path, "w", encoding="utf-8") as f:
        json.dump({"formation_date": formation,
                   "universe": os.path.basename(uni_path),
                   "universe_sha256": sha256_file(uni_path),
                   "in_band_company_years": len(uni),
                   "shortlist": os.path.basename(shortlist_path)
                               if shortlist_path else None,
                   "shortlist_sha256": sl_hash or None,
                   "scope_size": len(scope),
                   "admitted_off_shortlist": admitted,
                   "not_in_scope": sorted(set(uni) - scope),
                   "criteria_weights": {k: w for k, w, _l in CRITERIA},
                   "pass_bar": PASS_BAR,
                   "companies": evidence}, f, indent=2, sort_keys=True)

    passed = [r for r in rows if r["eligible"]]
    est = [r for r in rows if r["c1_estimated"]]
    print("formation %s · %d in_band company-years · %d in scope · %d scored"
          % (formation, len(uni), len(scope), len(rows)))
    if shortlist_path:
        print("  scope: shortlist %s (sha256 %s), %d of %d in_band"
              % (os.path.basename(shortlist_path), sl_hash[:12], len(scope),
                 len(uni)))
        print("  %d in_band company-years were cut upstream and carry NO scores"
              % (len(uni) - len(scope)))
        print("  in this file. The pre-filter's own cut list, with reasons, is")
        print("  what bounds the population - this file does not.")
        if admitted:
            print("  %d judgement(s) outside the shortlist admitted: %s"
                  % (len(admitted), " ".join(admitted)))
            print("  (scored from the filing in force before A1b existed)")
    print("  eligible (>= %.0f):   %d" % (PASS_BAR, len(passed)))
    print("  ineligible, kept:    %d" % (len(rows) - len(passed)))
    print("  c1 estimated:        %d" % len(est))
    if rows:
        srt = sorted(rows, key=lambda r: -r["weighted_score"])
        print("\n  score  elig  ticker  reason")
        for r in srt:
            print("  %5.1f  %-4s  %-6s  %s"
                  % (r["weighted_score"], "PASS" if r["eligible"] else "fail",
                     r["ticker"], r["reason"][:72]))
    print("\n  -> %s\n  -> %s\n  sha256: %s" % (out, ev_path, digest))
    if missing:
        print("\n  !! PARTIAL: %d unscored (%s). Not a universe; A2's gate is "
              "not met by this file." % (len(missing), ", ".join(missing[:8])))
        return
    gate = len(passed) >= 8
    print("\n  Phase A2 gate (>= 8 eligible): %s" % ("MET" if gate else "NOT MET"))
    print("  Next: A3 coverage, on the eligible set only. Coverage has not been")
    print("  consulted up to this point and must not have been.")


def verify(path, year, datadir):
    uni = load_universe(year, datadir)
    formation = next(iter(uni.values()))["formation_date"]
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    bad = []
    for r in rows:
        scores = {}
        for k, _w, _l in CRITERIA:
            try:
                v = int(r[k])
            except (KeyError, ValueError):
                bad.append("%s: %s is not an integer" % (r.get("ticker"), k))
                continue
            if not 0 <= v <= 5:
                bad.append("%s: %s = %d out of range" % (r["ticker"], k, v))
            scores[k] = v
        if len(scores) == len(CRITERIA):
            want = weighted(scores)
            if abs(float(r["weighted_score"]) - want) > 0.011:
                bad.append("%s: weighted_score %s but criteria imply %.2f"
                           % (r["ticker"], r["weighted_score"], want))
            if (str(r["eligible"]).lower() == "true") != (want >= PASS_BAR):
                bad.append("%s: eligible=%s disagrees with %.2f vs bar %.0f"
                           % (r["ticker"], r["eligible"], want, PASS_BAR))
        if r["scored_asof"] > formation:
            bad.append("%s: scored_asof %s > formation %s"
                       % (r["ticker"], r["scored_asof"], formation))
        if str(r["eligible"]).lower() != "true" and not r["reason"].strip():
            bad.append("%s: ineligible with no reason" % r["ticker"])
    unscored = sorted(set(uni) - set(r["ticker"] for r in rows))
    print("%s: %d rows, %d in_band" % (path, len(rows), len(uni)))
    if unscored:
        print("  unscored in_band companies: %d (%s)"
              % (len(unscored), ", ".join(unscored[:10])))
    if bad:
        for b in bad:
            print("  FAIL %s" % b)
        sys.exit("\n%d contract violation(s)." % len(bad))
    print("  contract OK" + ("" if not unscored else " (but incomplete)"))


# ---------------------------------------------------------------- cli


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode",
                   choices=["queue", "filing", "probe", "build", "verify"])
    p.add_argument("--year", type=int, default=2016,
                   help="formation year; reads data/universe_<year>.csv")
    p.add_argument("--datadir", default="data")
    p.add_argument("--ticker", help="filing mode: which company")
    p.add_argument("--text", action="store_true",
                   help="filing mode: also flatten the filing to a text file")
    p.add_argument("--textdir", default=".cache/filings")
    p.add_argument("--partial", action="store_true",
                   help="build mode: allow an incomplete universe (sample only)")
    p.add_argument("--out", help="build mode: override output path")
    p.add_argument("--criterion", nargs="*",
                   help="probe mode: restrict to these criterion keys")
    p.add_argument("--shortlist",
                   help="queue/build modes: the pre-filter's ticker list; "
                        "scope becomes that list instead of every in_band "
                        "company-year. A2 never produces this file.")
    p.add_argument("--csv", help="verify mode: path to the eligibility CSV")
    args = p.parse_args()

    if args.mode == "queue":
        uni = load_universe(args.year, args.datadir)
        judg = load_judgements(args.year, args.datadir)
        if args.shortlist:
            order, sl_hash = load_shortlist(args.shortlist, uni, args.year)
            scope = list(order)
        else:
            scope, sl_hash = sorted(uni), ""
        todo = [t for t in scope if t not in judg]
        done = [t for t in scope if t in judg]
        formation = next(iter(uni.values()))["formation_date"]
        print("formation %s · %d in_band company-years · %d in scope · "
              "%d scored · %d to go"
              % (formation, len(uni), len(scope), len(done), len(todo)))
        if args.shortlist:
            print("scope: shortlist %s (sha256 %s)"
                  % (os.path.basename(args.shortlist), sl_hash[:12]))
        else:
            print("scope: every in_band company-year (no shortlist given)")
        if done:
            print("\nscored:   %s" % " ".join(sorted(done)))
        print("\nto score:")
        for t in todo:
            r = uni[t]
            print("  %-6s cik=%s sic=%s cap=$%.0fM  %s"
                  % (t, r["cik"], r["sic"],
                     float(r["market_cap"]) / 1e6 if r["market_cap"] else 0,
                     r["company_name"]))
        return

    if args.mode == "filing":
        if not args.ticker:
            die("filing mode needs --ticker")
        ua()
        uni = load_universe(args.year, args.datadir)
        if args.ticker not in uni:
            die("%s is not in_band at %s-01-01. A2 scores the universe only."
                % (args.ticker, args.year))
        row = uni[args.ticker]
        formation = row["formation_date"]
        f, name, n_total = in_force_filing(row["cik"], formation)
        if not f:
            print("%s (cik %s): NO annual report or registration statement "
                  "filed on or before %s." % (args.ticker, row["cik"], formation))
            print("  %d such filings exist, every one of them later. There is "
                  "nothing A2 may read. Record the company with "
                  '"no_filing": true, scored_asof = %s, and scores reflecting '
                  "the absence of disclosure - never a later filing."
                  % (n_total, formation))
            return
        print("%s · %s (cik %s)" % (args.ticker, name or row["company_name"],
                                    row["cik"]))
        print("  formation:    %s" % formation)
        print("  in force:     %s  filed %s  (period ending %s)"
              % (f["form"], f["filing_date"], f["report_date"] or "?"))
        if f["source"] == "prospectus":
            print("  !! no annual report existed at formation. This is the "
                  "registration statement")
            print("     in force instead - public at formation, but thinner. "
                  "Score the thinness.")
        print("  accession:    %s" % f["accession"])
        print("  scored_asof:  %s   (<= formation, as the contract requires)"
              % f["filing_date"])
        if f["primary_document"]:
            print("  document:     %s"
                  % archive_url(row["cik"], f["accession"], f["primary_document"]))
        print("\n  This is the ONLY filing A2 may read for %s. Later filings "
              "exist and are out of bounds." % args.ticker)
        if args.text:
            if not f["primary_document"]:
                die("no primaryDocument recorded for %s" % f["accession"])
            dest = os.path.join(args.textdir, "%s_%s.txt"
                                % (args.ticker, f["accession"].replace("-", "")))
            dest, n = flatten_filing(row["cik"], f["accession"],
                                     f["primary_document"], dest)
            print("\n  text:         %s  (%d chars)" % (dest, n))
            print("  grep it; do not read it whole. Useful starting points:")
            print("    grep -n -i -E 'amazon|e-commerce|online' %s" % dest)
            print("    grep -n -i -E 'accounted for .*(net sales|revenue)' %s" % dest)
            print("    grep -n -i -E 'reportable segment|one segment' %s" % dest)
        return

    if args.mode == "probe":
        if not args.ticker:
            die("probe mode needs --ticker")
        ua()
        uni = load_universe(args.year, args.datadir)
        if args.ticker not in uni:
            die("%s is not in_band at %s-01-01." % (args.ticker, args.year))
        row = uni[args.ticker]
        formation = row["formation_date"]
        f, name, n_total = in_force_filing(row["cik"], formation)
        if not f:
            die("%s has no annual report or registration statement filed on or "
                "before %s; there is nothing A2 may read. Record it with "
                '"no_filing": true.' % (args.ticker, formation))
        dest = os.path.join(args.textdir, "%s_%s.txt"
                            % (args.ticker, f["accession"].replace("-", "")))
        if not os.path.exists(dest):
            if not f["primary_document"]:
                die("no primaryDocument recorded for %s" % f["accession"])
            flatten_filing(row["cik"], f["accession"], f["primary_document"], dest)
        with open(dest, encoding="utf-8") as fh:
            text = fh.read()
        print("%s · %s" % (args.ticker, name or row["company_name"]))
        print("evidence source: %s %s filed %s (accession %s)%s"
              % (f["form"], f["report_date"] or "", f["filing_date"],
                 f["accession"],
                 "  [registration statement - no annual report existed at "
                 "formation]" if f["source"] == "prospectus" else ""))
        print("formation: %s · scored_asof must be %s or earlier"
              % (formation, formation))
        print("filing text: %s (%d chars)\n" % (dest, len(text)))
        hits = probe_text(text, args.criterion)
        for key, _w, label in CRITERIA:
            if key not in hits:
                continue
            print("=" * 72)
            print("%s · %s" % (key, label))
            print("=" * 72)
            if not hits[key]:
                print("  (no probe matched - this is itself evidence. An "
                      "unquantifiable criterion scores low, never a charitable "
                      "guess.)\n")
                continue
            for sn in hits[key]:
                print("  - %s\n" % sn)
        return

    if args.mode == "build":
        build(args.year, args.datadir, args.partial, args.out, args.shortlist)
        return

    if args.mode == "verify":
        verify(args.csv or os.path.join(args.datadir,
                                        "eligibility_%s.csv" % args.year),
               args.year, args.datadir)
        return


if __name__ == "__main__":
    main()
