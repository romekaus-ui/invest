#!/usr/bin/env python3
"""Tests for the A1b mechanical pre-filter (scripts/prefilter_panel.py).

No network. The probes are exercised against filing text written here, and the
build is exercised against a synthetic measurement cache, so every check runs
offline and nothing touches data/ in the working repo.

What these cover is the claim the filter makes about itself: the same rule for
every company-year, a reason on every cut, a shortlist that is reproducible
byte for byte, and a refusal rather than a quiet partial result.

    python3 .claude/skills/review-tone-signal/tests/test_prefilter.py
"""

import csv
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)
import prefilter_panel as P                                      # noqa: E402
import score_eligibility as A2                                   # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name,
                         ("  -- " + detail) if detail and not cond else ""))


# --------------------------------------------------------------- the probes


def t_segments():
    print("\nsegments_from")
    n, basis, _m = P.segments_from(
        "We operate under four business segments. Each business segment is "
        "also one operating segment.")
    check("no reportable statement falls back to the max of the rest",
          (n, basis) == (4, "other"), "got %r %r" % (n, basis))

    n, basis, _m = P.segments_from(
        "Prior to the acquisition we had one reportable segment. We now have "
        "three reportable segments: A, B and C. We also describe nine "
        "operating segments internally.")
    check("reportable wins over operating, and takes the max",
          (n, basis) == (3, "reportable"), "got %r %r" % (n, basis))

    n, basis, _m = P.segments_from("The Company has a single reportable segment.")
    check("'single' reads as 1", (n, basis) == (1, "reportable"),
          "got %r %r" % (n, basis))

    n, basis, m = P.segments_from("We sell products in one segment of the market.")
    check("a bare 'segment' is not a segment count",
          (n, basis, m) == (None, "unknown", ""), "got %r %r %r" % (n, basis, m))

    n, _b, _m = P.segments_from("We report in 12 reportable segments.")
    check("digits parse", n == 12, "got %r" % n)

    _n, _b, matches = P.segments_from(
        "two reportable segments. two reportable segments. one operating segment")
    check("the match distribution is recorded, not just the answer",
          "reportable:2=2" in matches and "operating:1=1" in matches, matches)


def t_channel():
    print("\nchannel_from")
    c, _s, dropped, _q = P.channel_from(
        "This release works with hyperscale cloud services from Amazon Web "
        "Services (AWS). We added support for Amazon Simple Storage Service "
        "(S3) and shipped three solutions as Amazon Machine Images.")
    check("Amazon as cloud infrastructure is not a channel",
          c["amazon"] == 0 and dropped >= 3, "%r dropped=%d" % (c, dropped))

    c, _s, _d, _q = P.channel_from(
        "FDA rules impose restrictions on direct-to-consumer advertising of "
        "our product candidates.")
    check("direct-to-consumer advertising is not a channel",
          c["direct"] == 0, repr(c))

    c, _s, _d, _q = P.channel_from(
        "A copy of the code of ethics is available on our website. We will "
        "post any amendments on our website.")
    check("'available on our website' is not a channel",
          sum(c.values()) == 0, repr(c))

    c, _s, _d, _q = P.channel_from(
        "Shares are held in book-entry form through DTC. DTC participants may "
        "transfer such shares.")
    check("DTC the Depository Trust Company is not a channel",
          c["direct"] == 0, repr(c))

    c, _s, _d, _q = P.channel_from(
        "Our products can be purchased online either directly from our online "
        "store or through e-tailers such as Amazon.com. Internet sales "
        "channels continue to grow and we also sell direct-to-consumer.")
    check("a real channel sentence counts in every family",
          c["amazon"] == 1 and c["ecommerce"] >= 1 and c["direct"] >= 1,
          repr(c))

    _c, _s, _d, q = P.channel_from(
        "E-commerce accounted for approximately 14% of net sales in fiscal 2015.")
    check("a quantified channel share is detected", bool(q), repr(q))

    _c, _s, _d, q = P.channel_from(
        "We sell through e-commerce retailers and various distributors.")
    check("an unquantified channel is not reported as quantified", not q,
          repr(q))


def t_decide():
    print("\ndecide")
    check("no measurement at all is no_filing_at_formation",
          P.decide(None) == (False, P.CUT_NO_FILING))
    check("an unreadable filing is its own reason, not a channel verdict",
          P.decide({"error": "boom", "channel_total": 0})
          == (False, P.CUT_UNREADABLE))
    check("one channel mention is below the floor",
          P.decide({"channel_total": 1, "segments": 1})
          == (False, P.CUT_NO_CHANNEL))
    check("two channel mentions clear it",
          P.decide({"channel_total": 2, "segments": 1}) == (True, ""))
    check("five segments is sprawl",
          P.decide({"channel_total": 9, "segments": 5})
          == (False, P.CUT_SEGMENTS))
    check("four segments is kept (A2 judges it, not this filter)",
          P.decide({"channel_total": 9, "segments": 4}) == (True, ""))
    check("an unknown segment count is not read as sprawl",
          P.decide({"channel_total": 9, "segments": None}) == (True, ""))


def t_permute():
    print("\npermute")
    items = list(range(50))
    a = P.permute(items, random.Random(P.SCAN_SEED + P.SCAN_STAGE_OFFSET))
    b = P.permute(items, random.Random(P.SCAN_SEED + P.SCAN_STAGE_OFFSET))
    check("the same seed gives the same order", a == b)
    check("it is a permutation, nothing lost or duplicated",
          sorted(a) == items)
    c = P.permute(items, random.Random(P.SCAN_SEED + P.SCAN_STAGE_OFFSET + 1))
    check("a different seed gives a different order", a != c)
    check("it does not leave the input in order", a != items)


# ------------------------------------------------------------- the build


PANEL_HEADER = ("ticker,cik,company_name,sic,exchange,formation_date,"
                "shares_asof,price_at_formation,market_cap,in_band\n")

# (ticker, cik, sic, channel_total, segments) -> the expected verdict is
# whatever decide() says; the point of the fixture is the plumbing.
FIXTURE = [
    ("AAA", "0000000001", "3630", 9, 1),
    ("BBB", "0000000002", "3651", 0, 1),
    ("CCC", "0000000003", "5712", 4, 7),
    ("DDD", "0000000004", "2834", 1, 1),
    ("EEE", "0000000005", "3944", 18, 4),
]


def plant(root, years=("2016", "2017")):
    os.makedirs(os.path.join(root, "data"))
    cache = os.path.join(root, ".cache", "prefilter")
    os.makedirs(cache)
    lines = [PANEL_HEADER]
    for y in years:
        for tic, cik, sic, chan, segs in FIXTURE:
            lines.append("%s,%s,%s CORP,%s,NASDAQ,%s-01-01,%d-06-01,10.0,"
                         "500000000.0,True\n"
                         % (tic, cik, tic, sic, y, int(y) - 1))
            # One filing per company-year, filed BEFORE that formation: a
            # 10-K filed in June 2015 is the one in force on 2016-01-01.
            accn = "%s-%s-000001" % (cik[-10:], str(int(y) - 1)[-2:])
            with open(os.path.join(cache, "m_%s_%s.json"
                                   % (cik, accn.replace("-", ""))), "w") as f:
                json.dump({"probe_version": P.PROBE_VERSION,
                           "segments": segs, "segments_basis": "reportable",
                           "segments_matches": "reportable:%d=1" % segs,
                           "channel_counts": {"amazon": chan, "ecommerce": 0,
                                              "direct": 0},
                           "channel_total": chan, "channel_excluded": 0,
                           "channel_snippets": {}, "quantified": False,
                           "quantified_snippets": [],
                           "accession": accn,
                           "filing_date": "%d-06-01" % (int(y) - 1),
                           "form": "10-K", "source": "annual"}, f)
    # One filing index per company, carrying every year's filing.
    for tic, cik, _sic, _c, _s in FIXTURE:
        filings = [{"filing_date": "%d-06-01" % (int(y) - 1), "form": "10-K",
                    "accession": "%s-%s-000001" % (cik[-10:],
                                                   str(int(y) - 1)[-2:]),
                    "report_date": "%d-03-31" % (int(y) - 1),
                    "primary_document": "x.htm"} for y in years]
        with open(os.path.join(cache, "subs_%s.json" % cik), "w") as f:
            json.dump({"filings": filings, "name": tic}, f)
    with open(os.path.join(root, "data", "universe_panel.csv"), "w") as f:
        f.writelines(lines)
    return root


def run(root, *args):
    r = subprocess.run([sys.executable,
                        os.path.join(SCRIPTS, "prefilter_panel.py")] + list(args),
                       cwd=root, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def t_build(base):
    print("\nbuild")
    root = plant(os.path.join(base, "build"))
    rc, out = run(root, "build")
    check("build succeeds offline from the cache", rc == 0, out[-400:])

    pf = os.path.join(root, "data", "prefilter_panel.csv")
    rows = list(csv.DictReader(open(pf)))
    check("every in_band company-year appears, kept or cut",
          len(rows) == len(FIXTURE) * 2, "%d rows" % len(rows))
    check("every cut carries a reason",
          all(r["cut_reason"] for r in rows if r["kept"] != "True"))
    check("no kept row carries a reason",
          all(not r["cut_reason"] for r in rows if r["kept"] == "True"))

    by = {r["ticker"]: r for r in rows if r["formation_date"] == "2016-01-01"}
    check("a 0-mention filing is cut on the channel",
          by["BBB"]["cut_reason"] == P.CUT_NO_CHANNEL)
    check("a 1-mention filing is cut on the channel",
          by["DDD"]["cut_reason"] == P.CUT_NO_CHANNEL)
    check("7 segments is cut as sprawl",
          by["CCC"]["cut_reason"] == P.CUT_SEGMENTS)
    check("the channel test runs before the segment test",
          by["CCC"]["channel_mentions"] == "4")
    check("a 4-segment, many-mention filing is kept",
          by["EEE"]["kept"] == "True" and by["AAA"]["kept"] == "True")
    check("sic_group is recorded on every row",
          all(r["sic_group"] for r in rows))

    # Reproducibility: the same panel and cache, twice, byte for byte.
    h1 = {n: P.sha256_file(os.path.join(root, "data", n))
          for n in sorted(os.listdir(os.path.join(root, "data")))
          if n.endswith(".csv")}
    rc, out = run(root, "build")
    h2 = {n: P.sha256_file(os.path.join(root, "data", n))
          for n in sorted(os.listdir(os.path.join(root, "data")))
          if n.endswith(".csv")}
    check("a second build is byte-identical", rc == 0 and h1 == h2,
          "%r vs %r" % (h1, h2))

    man = json.load(open(os.path.join(root, "data",
                                      "prefilter.manifest.json")))
    check("the manifest records the panel sha256 it was cut from",
          man["panel"]["sha256"] ==
          P.sha256_file(os.path.join(root, "data", "universe_panel.csv")))
    check("the manifest records the thresholds",
          man["thresholds"] == {"min_channel_mentions": P.MIN_CHANNEL_MENTIONS,
                                "seg_max": P.SEG_MAX})
    check("the manifest records the scan seed",
          man["scan_order"]["effective_seed"]
          == P.SCAN_SEED + P.SCAN_STAGE_OFFSET)
    check("the manifest records counts in and out per formation year",
          set(man["per_formation_year"]) == {"2016", "2017"} and
          all({"in_band", "kept", "cut", "cut_reasons"} <=
              set(v) for v in man["per_formation_year"].values()))

    for y in ("2016", "2017"):
        sl = os.path.join(root, "data", "shortlist_%s.csv" % y)
        check("shortlist_%s.csv written" % y, os.path.exists(sl))
        hashline = open(sl.replace(".csv", ".hash")).read()
        check("shortlist_%s.hash carries its own sha256" % y,
              P.sha256_file(sl) in hashline)
        check("shortlist_%s.hash names the panel and the thresholds" % y,
              "universe_panel.csv" in hashline and
              "min_channel_mentions=%d" % P.MIN_CHANNEL_MENTIONS in hashline)
        srows = list(csv.DictReader(open(sl)))
        check("shortlist_%s holds only kept company-years" % y,
              {r["ticker"] for r in srows} ==
              {r["ticker"] for r in rows
               if r["kept"] == "True" and r["formation_date"].startswith(y)})
        check("shortlist_%s is in scan order, 1..n" % y,
              [r["scan_order"] for r in srows]
              == [str(i) for i in range(1, len(srows) + 1)])

    rc, out = run(root, "verify")
    check("verify passes on what build wrote", rc == 0, out[-400:])
    return root


def t_refusals(base):
    print("\nrefusals")
    root = plant(os.path.join(base, "partial"))
    os.remove(os.path.join(root, ".cache", "prefilter",
                           "m_0000000001_000000000115000001.json"))
    rc, out = run(root, "build")
    check("an unmeasured company-year refuses the build", rc != 0,
          out[-300:])
    check("the refusal says the panel is filtered whole or not at all",
          "whole or not at all" in out)
    check("no shortlist is written by a refused build",
          not os.path.exists(os.path.join(root, "data", "shortlist_2016.csv")))

    rc, out = run(root, "build", "--partial")
    check("--partial writes the panel record for inspection", rc == 0,
          out[-300:])
    check("--partial still writes no shortlist",
          not os.path.exists(os.path.join(root, "data", "shortlist_2016.csv")))
    man = json.load(open(os.path.join(root, "data",
                                      "prefilter.manifest.json")))
    check("a partial build says so in the manifest",
          man["partial"] is True and man["unmeasured"])

    # A shortlist edited after the fact must not pass verify.
    root = plant(os.path.join(base, "tamper"))
    run(root, "build")
    sl = os.path.join(root, "data", "shortlist_2016.csv")
    with open(sl, "a") as f:
        f.write("99,ZZZ,0000000099,2016-01-01,x,2015-06-01,1,9,False\n")
    rc, out = run(root, "verify")
    check("verify catches a shortlist edited after its hash was recorded",
          rc != 0 and "sha256" in out, out[-300:])

    root = plant(os.path.join(base, "tamper2"))
    run(root, "build")
    pf = os.path.join(root, "data", "prefilter_panel.csv")
    txt = open(pf).read().replace(",%s\n" % P.CUT_NO_CHANNEL, ",\n")
    open(pf, "w").write(txt)
    rc, out = run(root, "verify")
    check("verify catches a cut whose reason was removed",
          rc != 0 and "no reason" in out, out[-300:])

    root = plant(os.path.join(base, "tamper3"))
    run(root, "build")
    pf = os.path.join(root, "data", "prefilter_panel.csv")
    rows = open(pf).read().rstrip().split("\n")
    open(pf, "w").write("\n".join(rows[:-1]) + "\n")
    rc, out = run(root, "verify")
    check("verify catches an in_band company-year dropped from the record",
          rc != 0 and "absent" in out, out[-300:])


def t_handoff(base):
    """The shortlist has to be the thing A2 actually accepts."""
    print("\nhandoff to A2")
    root = plant(os.path.join(base, "handoff"))
    run(root, "build")
    cwd = os.getcwd()
    try:
        os.chdir(root)
        uni = A2.load_universe(2016, "data")
        order, sha = A2.load_shortlist("data/shortlist_2016.csv", uni, 2016)
        check("score_eligibility.load_shortlist accepts the shortlist",
              len(order) > 0 and len(sha) == 64)
        check("it reads the scan order as given, not re-sorted",
              order == [r["ticker"] for r in
                        csv.DictReader(open("data/shortlist_2016.csv"))])
        check("the sha256 A2 records is the sha256 the hash file records",
              sha in open("data/shortlist_2016.hash").read())

        with open("data/bad_shortlist.csv", "w") as f:
            f.write("ticker\nAAA\nNOTINPANEL\n")
        try:
            A2.load_shortlist("data/bad_shortlist.csv", uni, 2016)
            check("A2 refuses a ticker that is not an in_band company-year",
                  False, "it was accepted")
        except SystemExit as e:
            check("A2 refuses a ticker that is not an in_band company-year",
                  "NOTINPANEL" in str(e), str(e)[:200])
    finally:
        os.chdir(cwd)


def main():
    base = os.environ.get("REVIEW_TONE_TEST_DIR") or os.path.join(
        tempfile.gettempdir(), "review-tone-prefilter-tests")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    print("fixtures: %s" % base)

    t_segments()
    t_channel()
    t_decide()
    t_permute()
    t_build(base)
    t_refusals(base)
    t_handoff(base)

    failed = [r for r in RESULTS if not r[1]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    if failed:
        for name, _ok, detail in failed:
            print("  FAIL %s  %s" % (name, detail))
        sys.exit(1)
    print("all pass")


if __name__ == "__main__":
    main()
