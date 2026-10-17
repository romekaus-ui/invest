#!/usr/bin/env python3
"""Tests for declare_group.py. Standard library only; run directly.

    python3 .claude/skills/review-tone-peer-group/tests/test_declare_group.py

The lexicon tests matter most. A refusal lexicon that fires on an innocent
sentence is worse than none: it teaches the agent to write around the checker
instead of writing the reason. Every phrase that a legitimate reason would
plausibly contain is pinned here as a must-pass.
"""

import copy
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import declare_group as dg  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def member(**over):
    m = {
        "company": "The Coca-Cola Company",
        "country_of_domicile": "US",
        "venue": "NYSE:KO",
        "cik": "0000021344",
        "concentration": {
            "dominant_line": "nonalcoholic beverages",
            "share_of_revenue": 0.98,
            "evidence": "FY2025 10-K, revenue by operating segment",
            "source_url": "https://www.sec.gov/Archives/edgar/data/21344/x.htm",
        },
        "brands": ["Coca-Cola", "Sprite"],
        "reason": ("Sells carbonated cola and lemon-lime soft drinks under "
                   "Coca-Cola and Sprite, which a shopper buys instead of a "
                   "cola from another maker."),
        "reporting": {"calendar": "quarterly", "currency": "USD", "source": "SEC",
                      "regime": "SEC 10-Q / 10-K", "fy_end": "1231",
                      "calendar_source": "SEC EDGAR submissions CIK0000021344",
                      "resolved_by": "edgar"},
        "products": [{
            "asin": "B000TNY0AM", "parent_asin": "B0PARENTKO",
            "marketplace": "amazon.com", "brand": "Coca-Cola",
            "name": "Coca-Cola Classic, 12 fl oz, 12 Pack",
            "continuity_reason": ("The standard 12-can pack of the core cola "
                                  "line, listed continuously since 2012."),
            "verification": {"status": "resolved", "checked_at": "2026-10-11T00:00:00Z",
                             "route": "http-direct",
                             "title_seen": "Coca-Cola Classic, 12 fl oz, 12 Pack",
                             "parent_asin_seen": "B0PARENTKO"},
        }],
    }
    m.update(over)
    return m


def other(**over):
    """A valid, distinct second member - its own company, ASIN and brand."""
    m = member(
        company="PepsiCo, Inc.", venue="NASDAQ:PEP", cik="0000077476",
        brands=["Pepsi"],
        reason="Sells cola under Pepsi, the direct alternative a shopper reaches "
               "for instead of a Coca-Cola can at the same shelf.",
        products=[dict(member()["products"][0], asin="B00N4OTQKM",
                       parent_asin="B0PARENTPE", brand="Pepsi",
                       name="Pepsi Cola, 12 oz Cans, 12 Pack",
                       verification={"status": "resolved", "checked_at": "x",
                                     "route": "http-direct",
                                     "title_seen": "Pepsi Cola, 12 oz Cans, 12 Pack"})])
    m.update(over)
    return m


def group(members=None, **over):
    g = {
        "group_id": "soft-drinks-global",
        "seed": "carbonated soft drinks",
        "seed_kind": "category",
        "substitutability_test": dg.SUBSTITUTABILITY_TEST,
        "solo": False,
        "peer_relative_reading": True,
        "members": members if members is not None else [member(), member(
            company="PepsiCo, Inc.",
            venue="NASDAQ:PEP", cik="0000077476",
            brands=["Pepsi"],
            reason=("Sells cola under Pepsi, the direct alternative a shopper "
                    "reaches for instead of a Coca-Cola can at the same "
                    "shelf."),
            products=[{
                "asin": "B00N4OTQKM", "parent_asin": "B0PARENTPE",
                "marketplace": "amazon.com",
                "brand": "Pepsi", "name": "Pepsi Cola, 12 oz Cans, 12 Pack",
                "continuity_reason": ("The standard 12-can pack of the flagship "
                                      "cola, in the permanent range."),
                "verification": {"status": "resolved", "checked_at": "2026-10-11T00:00:00Z",
                                 "route": "http-direct",
                                 "title_seen": "Pepsi Cola, 12 oz Cans, 12 Pack"},
            }])],
    }
    g.update(over)
    return g


def problems(g, **kw):
    return dg.validate(copy.deepcopy(g), **kw)


def _unver_problems():
    return "see the group built inline above"


# ----------------------------------------------------------- the happy path
print("baseline")
check("a well-formed two-member group validates", problems(group()) == [],
      problems(group()))

# -------------------------------------------- lexicon false positives (F1)
# These sentences are what a correct reason actually looks like. Any of them
# tripping the refusal lexicon would push the agent into evasive prose.
print("\nlexicon must NOT fire on legitimate reasons")
INNOCENT = [
    "Sells cola and lemon-lime drinks under Coca-Cola; shoppers substitute freely.",
    "Sells Sprite in the same chilled cabinet, at a similar price per litre.",
    "Coca-Cola is sold in a value pack of twelve cans, the same format as Pepsi.",
    "Sprite competes for the same purchase; buyers hold no strong brand loyalty.",
    "Sells Pepsi in multipacks; the price tier is the same as the Coca-Cola pack.",
    "Coca-Cola holds a large share of the chilled soft-drinks aisle in the US.",
    "Sells Sprite, a growth category in zero-sugar variants since 2019.",
]
for sentence in INNOCENT:
    hits = dg.lexicon_hits(sentence, dg.PERFORMANCE_LEXICON)
    check("innocent: %s..." % sentence[:44], hits == [], hits)

print("\nlexicon MUST fire on selection-on-performance")
GUILTY = {
    "Coca-Cola looks undervalued against its peers.": "undervalued",
    "Sprite has driven strong revenue growth for three years.": "revenue growth",
    "Coca-Cola is a compelling business with pricing power.": "compelling",
    "Sells Pepsi; the share price has outperformed the index.": "share price",
    "Coca-Cola is the market leader and a wide moat business.": "market leader",
    "Sells Sprite with a high gross margin.": "gross margin",
    "Coca-Cola trades at a P/E ratio below Pepsi.": "p e ratio",
}
for sentence, want in GUILTY.items():
    hits = dg.lexicon_hits(sentence, dg.PERFORMANCE_LEXICON)
    check("guilty: %s..." % sentence[:40], want in hits, "got %s" % hits)

# The false positive live data found: a brand name that is also a performance
# word. "Momentum" is Sennheiser's headphone line.
print("\na member's own brand name is never a performance word")
SENN = ("Sells wireless noise-cancelling over-ear headphones under the "
        "Sennheiser brand as Momentum 4, cross-shopped against the Sony "
        "WH-1000X at the same price.")
check("'momentum' fires with no brand exemption",
      "momentum" in dg.lexicon_hits(SENN, dg.PERFORMANCE_LEXICON))
check("'momentum' does not fire when it is the member's declared brand",
      dg.lexicon_hits(SENN, dg.PERFORMANCE_LEXICON,
                      exempt=["Sennheiser", "Momentum"]) == [])
check("the exemption is narrow: another member's brand does not spare it",
      "momentum" in dg.lexicon_hits(SENN, dg.PERFORMANCE_LEXICON,
                                    exempt=["Sony", "WH-1000X"]))
check("the exemption does not spare a genuine performance word",
      "undervalued" in dg.lexicon_hits(
          "Momentum 4 looks undervalued.", dg.PERFORMANCE_LEXICON,
          exempt=["Sennheiser", "Momentum"]))
check("a full group with a Momentum member validates",
      problems(group(members=[
          member(),
          member(company="Sonova Holding AG", brands=["Sennheiser", "Momentum"],
                 venue="SIX:SOON",
                 reason=SENN,
                 reporting={"calendar": "half-yearly", "currency": "CHF",
                            "source": "SIX", "regime": "SIX half-yearly",
                            "fy_end": "0331",
                            "calendar_source": "Sonova financial calendar",
                            "resolved_by": "agent"},
                 products=[{"asin": "B0B6GHW1SX", "parent_asin": "B0PARENTSE",
                            "marketplace": "amazon.com",
                            "brand": "Sennheiser",
                            "name": "Sennheiser Momentum 4 Wireless "
                                                "Headphones, ANC, 60 Hr Battery, Black",
                            "continuity_reason": "On sale since 2022 and still the "
                                                 "current generation of the long-running "
                                                 "Momentum line.",
                            "verification": {"status": "resolved", "checked_at": "x",
                                             "route": "http-direct",
                                             "title_seen": "Sennheiser Momentum 4 "
                                                           "Wireless Headphones, ANC, "
                                                           "60 Hr Battery, Black",
                                             "parent_asin_seen": "B0PARENTSE"}}])])) == [],
      problems(group()))

print("\npopularity lexicon on continuity reasons")
check("bestseller is refused",
      "bestseller" in dg.lexicon_hits("The bestseller in the range.", dg.POPULARITY_LEXICON))
check("'top rated' is refused",
      "top rated" in dg.lexicon_hits("Top rated pack, 4.6 stars.", dg.POPULARITY_LEXICON))
check("a continuity sentence is not refused",
      dg.lexicon_hits("The standard 12-can pack of the core line, sold since 2012.",
                      dg.POPULARITY_LEXICON) == [])

print("\ncanon folding")
check("dashes fold", dg.canon("best-seller") == "best seller")
check("slashes fold", dg.canon("P/E ratio") == "p e ratio")
check("'sells' does not contain the word 'sell' for a boundary match",
      dg.lexicon_hits("It sells cola.", ("sell",)) == [])


# ------------------------------------------------------- contract refusals
print("\nthe contract refuses an inadmissible member")


def refuses(name, g, needle, **kw):
    hits = [p for p in problems(g, **kw) if needle in p]
    check(name, bool(hits), "no problem mentioning %r in %s" % (needle, problems(g, **kw)))


refuses("one member without --solo", group(members=[member()]),
        "at least two")
refuses("no brands", group(members=[member(brands=[]), member(company="PepsiCo")]),
        "brands")
refuses("no products", group(members=[member(products=[]), member(company="PepsiCo")]),
        "products: none")
refuses("reason naming no owned brand",
        group(members=[member(reason="A very large beverage company with global "
                                     "distribution and many bottling partners."),
                       member(company="PepsiCo")]),
        "names none of the member's own brands")
refuses("reason too short", group(members=[member(reason="Sells Coca-Cola."),
                                           member(company="PepsiCo")]),
        "char floor")
refuses("missing reporting block",
        group(members=[member(reporting=None), member(company="PepsiCo")]),
        "reporting: missing")
refuses("non-ISO currency",
        group(members=[member(reporting=dict(member()["reporting"], currency="dollars")),
                       member(company="PepsiCo")]),
        "ISO 4217")
refuses("bad calendar",
        group(members=[member(reporting=dict(member()["reporting"], calendar="monthly")),
                       member(company="PepsiCo")]),
        "reporting.calendar")
refuses("malformed ASIN",
        group(members=[member(products=[dict(member()["products"][0], asin="TOOSHORT")]),
                       member(company="PepsiCo")]),
        "must be 10 characters")
refuses("unknown marketplace",
        group(members=[member(products=[dict(member()["products"][0],
                                             marketplace="amazon.example")]),
                       member(company="PepsiCo")]),
        "marketplace")
refuses("product brand not among the member's brands",
        group(members=[member(products=[dict(member()["products"][0], brand="Fanta")]),
                       member(company="PepsiCo")]),
        "not among the member's declared brands")
refuses("empty continuity reason",
        group(members=[member(products=[dict(member()["products"][0],
                                             continuity_reason="")]),
                       member(company="PepsiCo")]),
        "continuity_reason: empty")
refuses("continuity reason asserting nothing checkable",
        group(members=[member(products=[dict(member()["products"][0],
                                             continuity_reason="A good product that "
                                             "people seem to like buying a lot.")]),
                       member(company="PepsiCo")]),
        "asserts nothing checkable")
refuses("missing verification",
        group(members=[member(products=[{k: v for k, v in member()["products"][0].items()
                                         if k != "verification"}]),
                       member(company="PepsiCo")]),
        "verification: missing")
refuses("unresolved product without --allow-unverified",
        group(members=[member(products=[dict(member()["products"][0],
                                             verification={"status": "not_found",
                                                           "checked_at": "x",
                                                           "route": "http-direct"})]),
                       member(company="PepsiCo")]),
        "not 'resolved'")
_unver = group(members=[
    member(products=[dict(member()["products"][0],
                          verification={"status": "not_found", "checked_at": "x",
                                        "route": "http-direct"})]),
    other()])
check("the same unresolved product passes WITH --allow-unverified",
      problems(_unver, allow_unverified=True) == [],
      problems(_unver, allow_unverified=True))

refuses("one ASIN claimed by two companies",
        group(members=[member(), member(company="PepsiCo, Inc.", brands=["Pepsi"],
                                        reason="Sells cola under Pepsi, a direct "
                                               "alternative at the same shelf for "
                                               "the same purchase.",
                                        products=[dict(member()["products"][0],
                                                       brand="Pepsi")])]),
        "cannot be two companies'")
refuses("two members tracking children of the same parent ASIN",
        group(members=[
            member(products=[dict(member()["products"][0], parent_asin="B0PARENT01")]),
            other(products=[dict(other()["products"][0], parent_asin="B0PARENT01")])]),
        "is already tracked by members[0]")
check("distinct parents are fine",
      problems(group(members=[
          member(products=[dict(member()["products"][0], parent_asin="B0PARENT01")]),
          other(products=[dict(other()["products"][0], parent_asin="B0PARENT02")])])) == [])
refuses("duplicate company", group(members=[member(), member()]), "duplicate of")
refuses("altered substitutability test",
        group(substitutability_test="Does it look like a competitor?"),
        "frozen wording")
refuses("a venue that is not EXCHANGE:TICKER and not 'private'",
        group(members=[member(venue="NYSE"), member(company="PepsiCo")]),
        'must be "EXCHANGE:TICKER"')
refuses("an empty venue", group(members=[member(venue=""),
                                         member(company="PepsiCo")]),
        "venue: empty")
_priv = group(members=[
    member(venue="private",
           concentration=dict(member()["concentration"], share_of_revenue=None,
                              no_public_report=True, revenue_readable=False),
           reporting={"calendar": "annual", "currency": "EUR", "source": "none",
                      "regime": "unlisted, no public interim reporting",
                      "fy_end": "", "calendar_source": "company website, no filings",
                      "resolved_by": "agent"}),
    other()])
check("a private member validates", problems(_priv) == [], problems(_priv))
refuses("fy_end set on a member with no public reporting",
        group(members=[
            member(venue="private",
                   concentration=dict(member()["concentration"], share_of_revenue=None,
                                      no_public_report=True, revenue_readable=False),
                   reporting=dict(member()["reporting"], source="none", fy_end="1231")),
            other()]),
        "set on a member with no public reporting")
refuses("a listed member claiming the private evidence exemption",
        group(members=[member(concentration=dict(member()["concentration"],
                                                 no_public_report=True)), other()]),
        "set on a LISTED member")
refuses("a missing fy_end on a member whose revenue will be read",
        group(members=[member(reporting=dict(member()["reporting"], fy_end="")),
                       other()]),
        "requires MMDD")
# `parent_asin` may be absent from the draft as long as check-products recorded
# it; `_freeze` lifts it onto the product, so the frozen file always carries it.
check("a draft without parent_asin passes while verification carries it",
      problems(group(members=[
          member(products=[{k: v for k, v in member()["products"][0].items()
                            if k != "parent_asin"}]), other()])) == [])
refuses("a product with no parent ASIN anywhere",
        group(members=[member(products=[dict(
            {k: v for k, v in member()["products"][0].items() if k != "parent_asin"},
            verification={k: v for k, v
                          in member()["products"][0]["verification"].items()
                          if k != "parent_asin_seen"})]), other()]),
        "parent_asin: empty")
check("_freeze lifts parent_asin from the verification record onto the product",
      dg.normalise_for_contract(group(members=[
          member(products=[{k: v for k, v in member()["products"][0].items()
                            if k != "parent_asin"}]), other()])
      )["members"][0]["products"][0]["parent_asin"] == "B0PARENTKO")
check("normalise makes the two §1 flags explicit on a listed member",
      dg.normalise_for_contract(group())["members"][0]["concentration"]
      == dict(member()["concentration"], no_public_report=False,
              revenue_readable=True))

refuses("an ASIN whose served title does not carry the declared brand",
        group(members=[member(products=[dict(
            member()["products"][0], brand="Coca-Cola",
            name="Pepsi Cola, 12 oz Cans, 12 Pack",
            verification={"status": "resolved", "checked_at": "x",
                          "route": "http-direct",
                          "title_seen": "Pepsi Cola, 12 oz Cans, 12 Pack"})]),
                       member(company="PepsiCo")]),
        "does not appear in the title the marketplace served")
refuses("a declared title that does not match the served one",
        group(members=[member(products=[dict(
            member()["products"][0],
            name="Coca-Cola Classic, 12 fl oz, 24 Pack",
            verification={"status": "resolved", "checked_at": "x",
                          "route": "http-direct",
                          "title_seen": "Coca-Cola Classic, 12 fl oz, 12 Pack"})]),
                       member(company="PepsiCo")]),
        "does not match the served title")
refuses("a resolved product that recorded no title at all",
        group(members=[member(products=[dict(
            member()["products"][0], name="Coca-Cola Classic, 12 fl oz, 12 Pack",
            verification={"status": "resolved", "checked_at": "x",
                          "route": "http-direct"})]),
                       member(company="PepsiCo")]),
        "recorded no title_seen")

# Every refusal path below was found by tests/test_guard_coverage.py to have
# never fired in any test - guards nobody had seen work (CONTRACTS.md §5).
# The two that matter most are the lexicon ones: both were tested at the level
# of `lexicon_hits` and never through `validate`, so the lexicons could have
# been disconnected from the validator entirely and the suite would have passed.
print("\nguards that had never been seen to fire")

refuses("a reason that selects on performance, through validate",
        group(members=[member(reason="Sells Coca-Cola, and the share price has "
                                     "outperformed its peers for three years."),
                       other()]),
        "selects on performance, price or attractiveness")
refuses("a continuity reason arguing from popularity, through validate",
        group(members=[member(products=[dict(member()["products"][0],
                                             continuity_reason="The bestseller in the "
                                             "range since 2012, top rated by buyers.")]),
                       other()]),
        "argues from popularity")
refuses("a solo reason that selects on performance",
        group(members=[member()], solo=True, peer_relative_reading=False,
              solo_reason="No peer sells anything comparable and the business looks "
                          "undervalued against the wider sector right now."),
        "selects on performance, not substitutability")

refuses("a malformed group id", group(group_id="Soft Drinks!"), "must match")
refuses("an empty seed", group(seed=""), "seed: empty")
refuses("a declared_at carrying a wall clock",
        group(declared_at="2026-10-12T19:00:00Z"), "requires a DATE")
refuses("no members at all", group(members=[]), "members: none")
refuses("solo set on a group of two", group(solo=True), "solo: set, but the group has")
refuses("a member that is not an object", group(members=[member(), "PepsiCo"]),
        "not an object")
refuses("a member with an empty company name",
        group(members=[member(company=""), other()]), ".company: empty")
refuses("a CIK that is not ten digits",
        group(members=[member(cik="21344"), other()]), "must be 10 digits")

refuses("a reporting source outside the contract's enum",
        group(members=[member(reporting=dict(member()["reporting"], source="Bloomberg")),
                       other()]), "must be one of")
refuses("an empty reporting regime",
        group(members=[member(reporting=dict(member()["reporting"], regime=""))
                       , other()]), ".reporting.regime: empty")
refuses("an empty calendar source",
        group(members=[member(reporting=dict(member()["reporting"], calendar_source="")),
                       other()]), ".reporting.calendar_source: empty")
refuses("a resolved_by outside the enum",
        group(members=[member(reporting=dict(member()["reporting"], resolved_by="memory")),
                       other()]), ".reporting.resolved_by")

refuses("a share set alongside no_public_report",
        group(members=[member(venue="private",
                              concentration=dict(member()["concentration"],
                                                 share_of_revenue=0.9,
                                                 no_public_report=True,
                                                 revenue_readable=False),
                              reporting=dict(member()["reporting"], source="none",
                                             fy_end="")),
                       other()]),
        "set alongside no_public_report")
refuses("revenue_readable left true under no_public_report",
        group(members=[member(venue="private",
                              concentration=dict(member()["concentration"],
                                                 share_of_revenue=None,
                                                 no_public_report=True,
                                                 revenue_readable=True),
                              reporting=dict(member()["reporting"], source="none",
                                             fy_end="")),
                       other()]),
        "must be false when no_public_report is set")
refuses("a share that is not a number",
        group(members=[member(concentration=dict(member()["concentration"],
                                                 share_of_revenue="92%")), other()]),
        "a fraction in (0, 1]")

refuses("an empty reason",
        group(members=[member(reason=""), other()]), ".reason: empty")
refuses("a product that is not an object",
        group(members=[member(products=["B000TNY0AM"]), other()]), "not an object")
refuses("an empty product name",
        group(members=[member(products=[dict(member()["products"][0], name="")]),
                       other()]), ".name: empty")
refuses("an empty product brand",
        group(members=[member(products=[dict(member()["products"][0], brand="")]),
                       other()]), ".brand: empty")
refuses("a verification with no timestamp",
        group(members=[member(products=[dict(
            member()["products"][0],
            verification=dict(member()["products"][0]["verification"],
                              checked_at=""))]), other()]),
        ".verification.checked_at: empty")
refuses("a verification that cannot say how it was made",
        group(members=[member(products=[dict(
            member()["products"][0],
            verification=dict(member()["products"][0]["verification"],
                              route=""))]), other()]),
        ".verification.route: empty")

print("\nthe solo rule")
solo_ok = group(members=[member()], solo=True, peer_relative_reading=False,
                solo_reason=("Searched grocery and specialist retailers for a "
                             "substitutable product sold by another listed "
                             "company and found none at this format."))
check("a solo group with a reason validates", problems(solo_ok) == [], problems(solo_ok))
refuses("solo without a reason",
        group(members=[member()], solo=True, peer_relative_reading=False),
        "solo_reason")
refuses("solo with peer_relative_reading true",
        group(members=[member()], solo=True, peer_relative_reading=True,
              solo_reason="Looked for a substitutable product from another company "
                          "and found none in this format anywhere."),
        "must be false")
refuses("two members with peer_relative_reading false",
        group(peer_relative_reading=False), "must be true")


# ------------------------------------------- the concentration filter (PLAN)
print("\nthe concentration filter")


def conc(**over):
    c = dict(member()["concentration"])
    c.update(over)
    return c


refuses("no concentration block",
        group(members=[member(concentration=None), member(company="PepsiCo")]),
        "concentration: missing")
refuses("no dominant line",
        group(members=[member(concentration=conc(dominant_line="")),
                       member(company="PepsiCo")]),
        "dominant_line: empty")
refuses("no evidence",
        group(members=[member(concentration=conc(evidence="")),
                       member(company="PepsiCo")]),
        "evidence: empty")
refuses("no source url",
        group(members=[member(concentration=conc(source_url="")),
                       member(company="PepsiCo")]),
        "source_url: empty")
refuses("a source url that is not https",
        group(members=[member(concentration=conc(source_url="ko.com/10k")),
                       member(company="PepsiCo")]),
        "must be an https URL")
refuses("a share given as a percentage instead of a fraction",
        group(members=[member(concentration=conc(share_of_revenue=98)),
                       member(company="PepsiCo")]),
        "a FRACTION in (0, 1]")
refuses("a share below the floor",
        group(members=[member(concentration=conc(share_of_revenue=0.45)),
                       member(company="PepsiCo")]),
        "below the 0.60 floor")
check("the floor message says the line can be widened, not only dropped",
      any("widen it rather than drop" in pr for pr in
          problems(group(members=[member(concentration=conc(share_of_revenue=0.45)),
                                  member(company="PepsiCo")]))))
_null = group(members=[member(concentration=conc(share_of_revenue=None)), other()])
check("a null share with real evidence is admissible",
      problems(_null) == [], problems(_null))
_floor = group(members=[member(concentration=conc(share_of_revenue=0.60)), other()])
check("a share exactly at the floor passes", problems(_floor) == [], problems(_floor))
refuses("evidence that argues instead of citing",
        group(members=[member(concentration=conc(
            evidence="the drinkware line drives revenue growth and a high gross margin")),
                       member(company="PepsiCo")]),
        "reads as a performance claim")
check("a plain filing citation does not trip the lexicon",
      dg.lexicon_hits("FY2025 10-K, net sales by product category: Drinkware 58%, "
                      "Coolers & Equipment 40%", dg.PERFORMANCE_LEXICON) == [])

# ------------------------------------------------------------ comparability
# The wrong-company defect live data found. SEC's ticker index is keyed on
# symbol alone, so a non-US venue must never be looked up in it.
print("\nUS venue guard (defect 11)")
check("a US venue resolves", dg.us_ticker({"venue": "NASDAQ:LOGI"}) == "LOGI")
check("EPA:EL is NOT resolved as Estee Lauder's EL",
      dg.us_ticker({"venue": "EPA:EL"}) is None)
check("EPA:BB is NOT resolved as BlackBerry's BB",
      dg.us_ticker({"venue": "EPA:BB"}) is None)
check("a Shanghai venue is not resolved",
      dg.us_ticker({"venue": "SHA:688169"}) is None)
check("a private member is not resolved",
      dg.us_ticker({"venue": "private"}) is None)
check("a US venue in lower case still resolves",
      dg.us_ticker({"venue": "nasdaq:celh"}) == "CELH")

print("\nthe comparability block")
c = dg.comparability(group())
check("a single-marketplace group is not cross-marketplace",
      c["cross_marketplace"] is False, c)
check("a single-calendar group is not mixed", c["mixed_reporting_calendars"] is False, c)
check("a clean group prints no comparability notes", dg.print_comparability(c) == [],
      dg.print_comparability(c))
check("the clean group is revenue-comparable without stated handling",
      c["revenue_comparable_without_stated_handling"] is True, c)

mixed = group(members=[
    member(products=[dict(member()["products"][0], marketplace="amazon.co.uk")]),
    member(company="Suntory Beverage & Food Limited", brands=["Lucozade"],
           venue="private",
           reason="Sells caffeinated glucose energy drinks under Lucozade, taken "
                  "instead of a cola by the same shopper at the same shelf.",
           concentration=dict(member()["concentration"], share_of_revenue=None,
                              no_public_report=True, revenue_readable=False),
           reporting={"calendar": "half-yearly", "currency": "JPY", "source": "none",
                      "regime": "no interim reporting", "fy_end": "",
                      "calendar_source": "company reports", "resolved_by": "agent"},
           products=[dict(member()["products"][0], asin="B001LION9E",
                          parent_asin="B0PARENTLU", brand="Lucozade",
                          name="Lucozade Energy Original 380 ml",
                          verification={"status": "resolved", "checked_at": "x",
                                        "route": "http-direct",
                                        "title_seen": "Lucozade Energy Original 380 ml"})])])
cm = dg.comparability(mixed)
check("a group spanning marketplaces is flagged", cm["cross_marketplace"] is True, cm)
check("mixed calendars are flagged", cm["mixed_reporting_calendars"] is True, cm)
check("mixed currencies are flagged", cm["mixed_currencies"] is True, cm)
check("a private member is listed as having no public reporting",
      cm["members_without_public_reporting"] == ["Suntory Beverage & Food Limited"], cm)
check("it is NOT revenue-comparable without stated handling",
      cm["revenue_comparable_without_stated_handling"] is False, cm)
notes = dg.print_comparability(cm)
check("four notes are printed", len(notes) == 4, notes)
check("the cross-marketplace note is first and names both marketplaces",
      "amazon.co.uk" in notes[0] and "amazon.com" in notes[0], notes[0])
# Comparability is a warning, never a refusal: a global group is what §2 asks for.
check("a cross-marketplace group still validates", problems(mixed) == [], problems(mixed))

# ------------------------------------------- the route field (§1, 2026-10-15)
# Amazon was the only sensor this project had; brand-owned review platforms are
# the second. The rule the whole section exists to hold: the brand route gets
# its OWN equivalent of every Amazon guard, never an exemption from one.
#
# The must-pass cases below matter at least as much as the refusals. A verifier
# that fires on a legitimate declaration gets switched off within a week, and
# then it guards nothing - which is why the Weber case is pinned: four of its
# five grills are served under names that do not contain the word "Weber", and
# a brand-in-the-title rule carried over from Amazon would refuse all four.
print("\nthe route field defaults to amazon")

check("a product with no route reads as amazon", dg.route_of({}) == "amazon")
check("an explicit route is read as given",
      dg.route_of({"route": "bazaarvoice"}) == "bazaarvoice")
check("whitespace is not a route", dg.route_of({"route": "   "}) == "amazon")
check("the default is the value CONTRACTS.md §1 names",
      dg.DEFAULT_ROUTE == "amazon" and dg.DEFAULT_ROUTE in dg.ROUTES)
check("§1's four route names are all accepted",
      dg.ROUTES == ("amazon", "bazaarvoice", "yotpo", "powerreviews"), dg.ROUTES)
check("the brand routes are the three platform ones",
      set(dg.BRAND_ROUTES) == {"bazaarvoice", "yotpo", "powerreviews"})

# Measured live 2026-10-15 against the Traeger deployment, and the numbers are
# the ones data/_route-tests/brand-widget-routes-2026-10-15.json records.
BV_VERIFICATION = {
    "status": "resolved", "checked_at": "2026-10-15T20:00:00Z",
    "route": "bazaarvoice-display-api", "client": "traegerservice",
    "site": "main_site", "product_id_seen": "TFB57GLE",
    "title_seen": "Pro 780 Pellet Grill - Black",
    "total_claimed": 1215, "collected": 200,
    "newest": "2026-10-11", "oldest": "2024-06-13",
    "newest_review_age_days": 4, "with_text_pct": 100.0,
    "dates_descending_verified": True, "date_inversions": 0,
    "pagination_caps": False, "sampled": 100,
    "family_fingerprint": "d9aa93cb2b20ffb0" * 4,
    "family_sample_end": "oldest",
    "family_sampled": 100,
    "first_review": "2019-03-20",
    "family_shared_with": [],
}


def bv_product(**over):
    pr = {
        "route": "bazaarvoice", "client": "traegerservice",
        "product_id": "TFB57GLE", "brand": "Traeger",
        "name": "Pro 780 Pellet Grill - Black",
        "continuity_reason": ("A core-range pellet grill in the catalogue "
                             "continuously since 2019, with no successor "
                             "announced."),
        "verification": dict(BV_VERIFICATION),
    }
    pr.update(over)
    # CONTRACTS.md §2b: `family` is the one column every route is joined on, and
    # the collector reads it off the group file. Derived from the product id
    # here so that overriding the id in a test keeps the two consistent -
    # otherwise every case would trip the label guard instead of the one it is
    # about, which is how a fixture ends up testing the fixture.
    pid = pr.get("product_id") or ""
    pr.setdefault("family", "fam-%s" % pid.lower().replace("_", "-") or "fam")
    pr.setdefault("family_product_ids", [pid] if pid else [])
    return pr


def bv_verification(**over):
    return dict(BV_VERIFICATION, **over)


def bv_member(**over):
    m = member(
        company="Traeger, Inc.", venue="NYSE:COOK", cik="0001857853",
        brands=["Traeger"],
        reason=("Sells wood pellet barbecue grills under Traeger, which a "
                "shopper buying an outdoor grill considers instead of another "
                "maker's at the same point of purchase."),
        concentration={
            "dominant_line": "wood pellet grills, with the pellets and "
                             "accessories used with them",
            "share_of_revenue": 1.0,
            "evidence": "FY2025 10-K, revenue by product category",
            "source_url": "https://www.sec.gov/Archives/edgar/data/1857853/x.htm",
        },
        products=[bv_product()])
    m.update(over)
    return m


# Weber's own catalogue does not repeat its name in its product names: this is
# the served name, verbatim, for ProductId 44010001.
def weber_member(**over):
    m = member(
        company="Weber Inc.", venue="private", cik="",
        brands=["Weber"],
        reason=("Sells outdoor barbecue grills under Weber, the direct "
                "alternative a shopper weighs against a Traeger when buying a "
                "grill."),
        concentration={
            "dominant_line": "outdoor barbecue grills and accessories",
            "share_of_revenue": None, "no_public_report": True,
            "revenue_readable": False,
            "evidence": "Taken private in February 2023; last 10-K was FY2022",
            "source_url": "",
        },
        reporting={"calendar": "annual", "currency": "USD", "source": "none",
                   "regime": "taken private February 2023; no current reporting",
                   "fy_end": "", "calendar_source": "last 10-K was FY2022",
                   "resolved_by": "agent"},
        products=[bv_product(
            client="weber", product_id="44010001", brand="Weber",
            name="Spirit II E-210 Gas Grill (Liquid Propane)",
            verification=bv_verification(
                client="weber", product_id_seen="44010001",
                title_seen="Spirit II E-210 Gas Grill (Liquid Propane)",
                total_claimed=18772, newest="2026-09-12",
                newest_review_age_days=33, with_text_pct=34.0,
                family_fingerprint="a1b2c3d4e5f60718" * 4))])
    m.update(over)
    return m


bv_group = group(members=[bv_member(), weber_member()])
check("a two-member brand-route group validates",
      problems(bv_group) == [], problems(bv_group))
WEBER_TITLE = "Spirit II E-210 Gas Grill (Liquid Propane)"
check("the brand name is genuinely absent from the served name, so this is the "
      "real case and not a contrived one", "weber" not in WEBER_TITLE.lower())
check("the brand route does NOT require the brand in the served name - four of "
      "Weber's five grills are served without it",
      not any("does not appear in the title" in p
              for p in problems(group(members=[bv_member(), weber_member()]))),
      problems(group(members=[bv_member(), weber_member()])))
check("the same absence on the AMAZON route is still refused, so the rule was "
      "relaxed for one route and not deleted",
      any("does not appear in the title" in p for p in problems(group(members=[
          member(products=[dict(member()["products"][0], brand="Coca-Cola",
                                name=WEBER_TITLE,
                                verification={"status": "resolved",
                                              "checked_at": "x",
                                              "route": "http-direct",
                                              "title_seen": WEBER_TITLE})]),
          other()]))))
check("a mixed-route group validates: one member on amazon, one on a platform",
      problems(group(members=[member(), weber_member()])) == [],
      problems(group(members=[member(), weber_member()])))

print("\nthe route field refuses what it cannot read")
refuses("a route outside the contract's enum",
        group(members=[bv_member(products=[bv_product(route="retailer-syndication")]),
                       weber_member()]),
        "unknown. CONTRACTS.md §1 names")
refuses("a brand product with no deployment zone",
        group(members=[bv_member(products=[bv_product(client="")]), weber_member()]),
        ".client")
refuses("a deployment zone that is not a zone name",
        group(members=[bv_member(products=[bv_product(client="Traeger Service!")]),
                       weber_member()]),
        ".client")
refuses("a malformed deployment site",
        group(members=[bv_member(products=[bv_product(site="main site/../x")]),
                       weber_member()]),
        ".site")
refuses("a brand product with no product id",
        group(members=[bv_member(products=[bv_product(product_id="")]),
                       weber_member()]),
        "ONE representative per review family")
refuses("Amazon identifiers left on a product switched to the brand route",
        group(members=[bv_member(products=[bv_product(asin="B000TNY0AM")]),
                       weber_member()]),
        "These are Amazon identifiers")
refuses("a brand product whose name is not what the platform served",
        group(members=[bv_member(products=[bv_product(name="Pro 575 family")]),
                       weber_member()]),
        "does not match the served title")
refuses("a brand product with an empty continuity reason",
        group(members=[bv_member(products=[bv_product(continuity_reason="")]),
                       weber_member()]),
        ".continuity_reason: empty")
refuses("a brand product with no verification at all",
        group(members=[bv_member(products=[bv_product(verification=None)]),
                       weber_member()]),
        ".verification: missing")
refuses("a brand product whose brand is not the member's",
        group(members=[bv_member(products=[bv_product(brand="Weber")]),
                       weber_member()]),
        "not among the member's declared brands")
refuses("one product cannot be two companies' on this route either",
        group(members=[bv_member(),
                       weber_member(products=[bv_product(
                           brand="Weber",
                           verification=bv_verification(
                               family_fingerprint="a1b2c3d4e5f60718" * 4))])]),
        "already tracked by members[0]")

# A route named in the contract that this script cannot resolve. Yotpo and
# PowerReviews are both reachable and both measured, but no resolver is wired
# here, so nothing has established that the identifier resolves to anything.
# "Not implemented" is the honest answer; "assumed to work" is not.
refuses("a contract route with no resolver behind it",
        group(members=[bv_member(products=[bv_product(
            route="yotpo",
            verification=bv_verification(route="yotpo-display-api"))]),
                       weber_member()]),
        "has no resolver for it")
check("--allow-unverified is the documented way past it, and it records the count",
      problems(group(members=[bv_member(products=[bv_product(
          route="yotpo", verification=bv_verification(route="yotpo-display-api"))]),
                              weber_member()]), allow_unverified=True) == [])

# ---- the family: this route's parent_asin ----
# Bazaarvoice pools across a product FAMILY exactly as Amazon pools across
# variants. TFB57GLE (Pro 575) and TFB78GLE (Pro 780) are different grills at
# different prices and return identical 1,215-review id sets; nine products
# resolved to seven families. Never infer the grouping from the product id
# differing - that is precisely the inference the measurement falsified.
print("\nthe review family, which is not optional")

refuses("a brand product with no family label - the collector's join key",
        group(members=[bv_member(products=[bv_product(family="")]),
                       weber_member()]),
        "CONTRACTS.md §2b requires it")
refuses("a family label that is not a slug the collector can join on",
        group(members=[bv_member(products=[bv_product(family="Pro 575 / 780")]),
                       weber_member()]),
        "as a slug")
refuses("a brand product with no family membership recorded",
        group(members=[bv_member(products=[bv_product(family_product_ids=[])]),
                       weber_member()]),
        "requires the product ids whose review-id sets were compared")
refuses("a family membership list that omits the representative itself",
        group(members=[bv_member(products=[bv_product(
            family_product_ids=["TFB78GLE"])]), weber_member()]),
        "is a member of its own family")
refuses("a malformed id in the family membership list",
        group(members=[bv_member(products=[bv_product(
            family_product_ids=["TFB57GLE", "not a product id!"])]),
                       weber_member()]),
        "is not a well-formed product id")

# The direction nothing else catches. Two genuinely DIFFERENT families sharing
# one label: every fingerprint check passes because the fingerprints differ, and
# then the collector - which joins on the LABEL - pools two unrelated families
# into one series with nothing downstream able to tell.
refuses("two different families sharing one label",
        group(members=[
            bv_member(products=[
                bv_product(product_id="TFB57GLE", family="pro-series"),
                bv_product(product_id="TFB89BLF", family="pro-series",
                           name="Ironwood 885 Pellet Grill",
                           verification=bv_verification(
                               product_id_seen="TFB89BLF",
                               title_seen="Ironwood 885 Pellet Grill",
                               family_fingerprint="96914968f1b56cca" * 4))]),
            weber_member()]),
        "would be pooled into one series")
refuses("and across members too, where it would fuse two companies' series",
        group(members=[bv_member(products=[bv_product(family="shared-label")]),
                       weber_member(products=[bv_product(
                           client="weber", product_id="44010001", brand="Weber",
                           family="shared-label",
                           name="Spirit II E-210 Gas Grill (Liquid Propane)",
                           verification=bv_verification(
                               client="weber", product_id_seen="44010001",
                               title_seen="Spirit II E-210 Gas Grill (Liquid Propane)",
                               family_fingerprint="a1b2c3d4e5f60718" * 4))])]),
        "would be pooled into one series")

refuses("a brand product whose family was never established at all",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(family_fingerprint=""))]),
                       weber_member()]),
        "never inferred from the product ids differing")
refuses("a brand product whose ids were never compared against the others",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(family_fingerprint=""))]),
                       weber_member()]),
        ".verification.family_fingerprint: empty")

# The real pair, with the fingerprints the live measurement produced.
POOLED = group(members=[
    bv_member(products=[
        bv_product(product_id="TFB57GLE",
                   verification=bv_verification(product_id_seen="TFB57GLE")),
        bv_product(product_id="TFB78GLE",
                   verification=bv_verification(product_id_seen="TFB78GLE"))]),
    weber_member()])
refuses("two products of ONE member resolving to the same review family",
        POOLED, "declare ONE representative per family")
refuses("and it says what the cost is, not just that it is wrong",
        POOLED, "counted twice")
refuses("two MEMBERS resolving to the same review family",
        group(members=[bv_member(),
                       weber_member(products=[bv_product(
                           client="weber", product_id="44010001", brand="Weber",
                           name="Spirit II E-210 Gas Grill (Liquid Propane)",
                           verification=bv_verification(
                               client="weber", product_id_seen="44010001",
                               title_seen="Spirit II E-210 Gas Grill (Liquid Propane)"))])]),
        "is already tracked by members[0]")
refuses("the across-member refusal names the Amazon rule it mirrors",
        group(members=[bv_member(),
                       weber_member(products=[bv_product(
                           client="weber", product_id="44010001", brand="Weber",
                           name="Spirit II E-210 Gas Grill (Liquid Propane)",
                           verification=bv_verification(
                               client="weber", product_id_seen="44010001",
                               title_seen="Spirit II E-210 Gas Grill (Liquid Propane)"))])]),
        "the amazon route makes for a shared parent")
refuses("an overlap `check-products` found pairwise, even without equal fingerprints",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(
                family_shared_with=["traegerservice/TFB78GLE"]))]),
                       weber_member()]),
        "one pooled family however")

check("two genuinely distinct families in one member validate",
      problems(group(members=[
          bv_member(products=[
              bv_product(product_id="TFB57GLE"),
              bv_product(product_id="TFB89BLF",
                         name="Ironwood 885 Pellet Grill",
                         verification=bv_verification(
                             product_id_seen="TFB89BLF",
                             title_seen="Ironwood 885 Pellet Grill",
                             total_claimed=836,
                             family_fingerprint="189a1080b52f4ed5" * 4))]),
          weber_member()])) == [])

# compare_families is what produces the evidence the refusals above act on. It
# is the pairwise comparison itself, so it is tested against the measured pair
# rather than against a fingerprint someone typed.
print("\nthe pairwise comparison that establishes a family")
shared_ids = ["307353637", "305965212", "305252716", "300776887"]
other_ids = ["111111111", "222222222", "333333333", "444444444"]
draft_cf = group(members=[
    bv_member(products=[
        bv_product(product_id="TFB57GLE",
                   verification=bv_verification(family_sample_ids=shared_ids)),
        bv_product(product_id="TFB78GLE",
                   verification=bv_verification(family_sample_ids=list(shared_ids)))]),
    weber_member(products=[bv_product(
        client="weber", product_id="44010001", brand="Weber",
        name="Spirit II E-210 Gas Grill (Liquid Propane)",
        verification=bv_verification(
            client="weber", product_id_seen="44010001",
            title_seen="Spirit II E-210 Gas Grill (Liquid Propane)",
            family_sample_ids=other_ids))])])
n_units, overlaps = dg.compare_families(draft_cf)
check("every brand-route product is compared", n_units == 3, n_units)
check("the pooled pair is found", len(overlaps) == 1, overlaps)
check("and it is the right pair",
      overlaps and overlaps[0][0] == "traegerservice/TFB57GLE"
      and overlaps[0][2] == "traegerservice/TFB78GLE", overlaps)
check("the overlap count is the measured one, not a guess",
      overlaps and overlaps[0][4] == len(shared_ids), overlaps)
check("the finding is written onto both products, so validate can refuse on it",
      draft_cf["members"][0]["products"][0]["verification"]["family_shared_with"]
      == ["traegerservice/TFB78GLE"]
      and draft_cf["members"][0]["products"][1]["verification"]["family_shared_with"]
      == ["traegerservice/TFB57GLE"])
check("a distinct product is not implicated",
      draft_cf["members"][1]["products"][0]["verification"]["family_shared_with"] == [])
check("the draft it just annotated is refused",
      any("ONE representative per family" in p for p in problems(draft_cf)))
check("comparing an already-annotated draft clears a stale finding first",
      dg.compare_families(copy.deepcopy(draft_cf))[1] and
      dg.compare_families(group(members=[bv_member(products=[bv_product(
          verification=bv_verification(
              family_sample_ids=shared_ids,
              family_shared_with=["traegerservice/GONE"]))]),
          weber_member(products=[bv_product(
              client="weber", product_id="44010001", brand="Weber",
              name="Spirit II E-210 Gas Grill (Liquid Propane)",
              verification=bv_verification(
                  client="weber", product_id_seen="44010001",
                  title_seen="Spirit II E-210 Gas Grill (Liquid Propane)",
                  family_sample_ids=other_ids))])]))[1] == [])

# The fingerprint is taken over the OLDEST ids, and that is not a detail. Over
# the newest page it changes the moment one review arrives, so `reresolve` would
# report `reparented` on every live product within a week of declaration and
# stage G would refuse a perfectly healthy group. Measured on the live route
# before the first cadence ran: Traeger's Pro 575 oldest page spans
# 2019-03-20..2019-06-30 while its newest page spans 2024-06-13..2026-10-11, and
# the two are disjoint - so an arrival lands only in the page the fingerprint
# does not read. It costs nothing in discrimination: the pooled pair shares 100
# of 100 oldest ids, and a distinct family shares zero.
print("\nthe fingerprint must survive a week of normal trading")
check("the sample is declared to come from the oldest end",
      dg.FAMILY_SAMPLE == 100)
OLD = ["100000001", "100000002", "100000003"]
check("an arriving review does not change a fingerprint over the oldest ids",
      dg.sha256_bytes("\n".join(sorted(OLD)).encode("utf-8"))
      == dg.sha256_bytes("\n".join(sorted(OLD + [])).encode("utf-8")))
check("but a DELETED old review does, which is a finding worth having",
      dg.sha256_bytes("\n".join(sorted(OLD)).encode("utf-8"))
      != dg.sha256_bytes("\n".join(sorted(OLD[:-1])).encode("utf-8")))
refuses("a fingerprint that cannot say which end it sampled",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(family_sample_end=None))]),
                       weber_member()]),
        "must be taken over the OLDEST ids")
refuses("a fingerprint taken over the newest page, as the first version did",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(family_sample_end="newest"))]),
                       weber_member()]),
        "would read as re-parented within a week")

# ---- what the brand route must have measured ----
print("\nthe brand-route measurement record")

refuses("a verification with no review count",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(total_claimed=None))]), weber_member()]),
        ".total_claimed")
refuses("a review count that is not a positive integer",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(total_claimed=0))]), weber_member()]),
        ".total_claimed")
refuses("a verification that cannot say when the newest review arrived",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(newest=""))]), weber_member()]),
        "the newest review's date")
refuses("a verification with no text fraction",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(with_text_pct=None))]), weber_member()]),
        "carrying text")
refuses("a text fraction recorded as a fraction rather than a percentage",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(with_text_pct=137.0))]), weber_member()]),
        "0-100")
refuses("a date order that is not a boolean",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(dates_descending_verified=None))]),
                       weber_member()]),
        "established from the DATES")
refuses("a verification with no inversion count behind that boolean",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(date_inversions=None))]), weber_member()]),
        ".date_inversions")

# The one that matters most on this route. CONTRACTS.md §3 records the same
# failure on Amazon: a sort taken from the request parameter was a claim about
# the platform, and the claim was false. A record asserting verified order while
# also recording inversions is what a parameter-trusting check looks like from
# outside, so it is a contradiction rather than a warning.
refuses("a sort order claimed verified while inversions were counted",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(dates_descending_verified=True,
                                         date_inversions=4))]), weber_member()]),
        "taken from the sort parameter rather than verified")
check("the honest combination - not descending, inversions counted - validates",
      problems(group(members=[bv_member(products=[bv_product(
          verification=bv_verification(dates_descending_verified=False,
                                       date_inversions=4))]),
                              weber_member()])) == [])

# ---- liveness is its own check ----
# Owala serves 14,029 reviews flawlessly and its newest is 2022-07-28, 1,540
# days stale. Depth says nothing about whether a deployment is alive.
print("\nliveness, which depth says nothing about")

refuses("a verification that cannot say how old the newest review is",
        group(members=[bv_member(products=[bv_product(
            verification=bv_verification(newest_review_age_days=None))]),
                       weber_member()]),
        "deep and dead")

OWALA = bv_verification(
    client="owala", product_id_seen="owala-flip-24",
    title_seen="Owala FreeSip 24 oz", total_claimed=14029,
    newest="2022-07-28", oldest="2020-03-07", newest_review_age_days=1540,
    with_text_pct=68.0, family_fingerprint="0wa1a0wa1a0wa1a0" * 4)
refuses("a deployment a year without a review, carrying no finding",
        group(members=[bv_member(products=[bv_product(
            client="owala", product_id="owala-flip-24",
            name="Owala FreeSip 24 oz", verification=OWALA)]),
                       weber_member()]),
        "is a FINDING the declaration has to carry")
check("the same product validates once the finding is written down",
      problems(group(members=[bv_member(products=[bv_product(
          client="owala", product_id="owala-flip-24",
          name="Owala FreeSip 24 oz", verification=OWALA,
          liveness_finding=("ABANDONED: newest review 2022-07-28, 1,540 days "
                            "stale. Usable only inside a window ending "
                            "mid-2022; no forward cadence can be run on it."))]),
                              weber_member()])) == [],
      problems(group(members=[bv_member(products=[bv_product(
          client="owala", product_id="owala-flip-24",
          name="Owala FreeSip 24 oz", verification=OWALA,
          liveness_finding="x" * 40)]), weber_member()])))
check("a live deployment needs no finding - 4 days old passes untouched",
      problems(bv_group) == [])
check("the staleness line is a year", dg.LIVENESS_STALE_DAYS == 365)

# ---- the Amazon route is untouched by any of this ----
print("\nthe amazon route keeps every guard it had")
check("an amazon product still needs a parent_asin",
      any("parent_asin: empty" in p for p in problems(group(members=[
          member(products=[dict(member()["products"][0], parent_asin="",
                                verification={"status": "resolved",
                                              "checked_at": "x",
                                              "route": "http-direct",
                                              "title_seen": "Coca-Cola Classic, "
                                                            "12 fl oz, 12 Pack"})]),
          other()]))))
check("an amazon product still needs the brand in the served title",
      any("does not appear in the title" in p for p in problems(group(members=[
          member(products=[dict(member()["products"][0], brand="Sprite",
                                name="Coca-Cola Classic, 12 fl oz, 12 Pack")]),
          other()]))))
check("an amazon product still needs a well-formed ASIN",
      any("must be 10 characters" in p for p in problems(group(members=[
          member(products=[dict(member()["products"][0], asin="B00")]), other()]))))
check("an amazon product still needs a known marketplace",
      any("marketplace" in p for p in problems(group(members=[
          member(products=[dict(member()["products"][0],
                                marketplace="amazon.example")]), other()]))))
check("a brand product is NOT asked for an ASIN",
      not any(".asin" in p for p in problems(bv_group)), problems(bv_group))
check("a brand product is NOT asked for a marketplace",
      not any("marketplace" in p for p in problems(bv_group)), problems(bv_group))

# ---- the route in the comparability block ----
print("\nthe route mix, which §3 requires to be visible")
cr = dg.comparability(bv_group)
check("a single-route group is not mixed", cr["mixed_routes"] is False, cr)
check("the routes are counted", cr["routes"] == {"bazaarvoice": 2}, cr)
check("a single-route group prints no route note",
      not any("ROUTE" in n for n in dg.print_comparability(cr)),
      dg.print_comparability(cr))
cx = dg.comparability(group(members=[member(), weber_member()]))
check("a group whose members sit on different routes is flagged",
      cx["routes_differ_between_members"] is True, cx)
check("and the note says a peer mean would be computed on another instrument",
      any("different instrument" in n for n in dg.print_comparability(cx)),
      dg.print_comparability(cx))
# A route mix WITHIN every member is a different situation: the comparison
# between members is sound, and it is each member's own series that carries it.
cw = dg.comparability(group(members=[
    bv_member(products=[bv_product(), member()["products"][0]]),
    weber_member(products=[
        bv_product(client="weber", product_id="44010001", brand="Weber",
                   name="Spirit II E-210 Gas Grill (Liquid Propane)"),
        dict(other()["products"][0], brand="Weber")])]))
check("a route mix inside every member is flagged differently",
      cw["mixed_routes"] is True
      and cw["routes_differ_between_members"] is False, cw)
check("and that note does not claim the members are incomparable",
      any("comparison between members is sound" in n
          for n in dg.print_comparability(cw)), dg.print_comparability(cw))

# ---- the schema gate decides whether checking keeps happening ----
# Schema 2 tightened only the BRAND-ROUTE product shape. The blunt test
# `schema_version == SCHEMA_VERSION` would have stopped contract-revalidating
# the six amazon declarations - twelve frozen versions - over a change that
# cannot touch them, which is the §5 failure wearing a version number.
print("\nwhich frozen files the current validator may still judge")
check("a file at the current schema is revalidated",
      dg.revalidatable(dict(bv_group, schema_version=dg.SCHEMA_VERSION)))
check("an older amazon-only file is STILL revalidated - nothing the brand-route "
      "bumps tightened can apply to it",
      dg.revalidatable(dict(group(), schema_version=1)))
check("an older brand file is revalidated when it carries the fields the "
      "bumps added - the shape is what matters, not the number",
      dg.revalidatable(dict(bv_group, schema_version=1)))
check("a brand file missing schema 3's join key is NOT revalidated",
      not dg.revalidatable(dict(group(members=[
          bv_member(products=[dict(bv_product(), family="")]), weber_member()]),
          schema_version=1)))
check("a brand file missing schema 3's membership list is NOT revalidated",
      not dg.revalidatable(dict(group(members=[
          bv_member(products=[dict(bv_product(), family_product_ids=[])]),
          weber_member()]), schema_version=1)))
check("a brand file that cannot say which end it fingerprinted is NOT "
      "revalidated",
      not dg.revalidatable(dict(group(members=[
          bv_member(products=[bv_product(
              verification=bv_verification(family_sample_end=""))]),
          weber_member()]), schema_version=1)))
check("one stale brand product among good ones is enough to hold a file back",
      not dg.revalidatable(dict(group(members=[
          member(),
          weber_member(products=[dict(bv_product(
              client="weber", product_id="44010001", brand="Weber",
              name="Spirit II E-210 Gas Grill (Liquid Propane)"), family="")])]),
          schema_version=1)))

# ---- declared_units covers both routes ----
print("\nwhat reresolve will sweep")
units = dg.declared_units(bv_group)
check("every brand product is a unit to re-resolve", len(units) == 2, units)
check("a unit is labelled by its platform identifier",
      units[0]["label"] == "traegerservice/TFB57GLE", units[0])
check("a unit carries the review pool it was declared against",
      units[0]["declared_pool"] == BV_VERIFICATION["family_fingerprint"], units[0])
amz_units = dg.declared_units(group())
check("an amazon unit is still labelled by its ASIN",
      amz_units[0]["label"] == "B000TNY0AM", amz_units[0])
check("and still carries its declared parent",
      amz_units[0]["declared_pool"] == "B0PARENTKO", amz_units[0])

# ------------------------------------------------- the CONTRACTS.md §1 view
print("\nthe contract view")
v = dg.contract_view(dict(group(), version=1, declared_at="2026-10-12",
                          readings_at_declare=0, post_collection=False,
                          comparability=dg.comparability(group())))
check("the view carries every field CONTRACTS.md §1 names",
      all(k in v for k in ("group_id", "declared_at", "seed", "hash", "members")), v.keys())
m0 = v["members"][0]
check("a member carries §1's keys",
      all(k in m0 for k in ("company", "venue", "brands", "concentration",
                            "products", "reporting")), sorted(m0))
check("venue is the contract's string form", m0["venue"] == "NYSE:KO", m0["venue"])
check("a product carries asin, marketplace and name",
      all(k in m0["products"][0] for k in ("asin", "marketplace", "name")),
      sorted(m0["products"][0]))
check("reporting carries calendar, currency and source",
      all(k in m0["reporting"] for k in ("calendar", "currency", "source")),
      sorted(m0["reporting"]))
check("the reason survives into the view, which §1 has no slot for",
      m0["reason"].startswith("Sells carbonated"), m0.get("reason"))
check("the continuity reason survives into the view",
      "continuity_reason" in m0["products"][0], sorted(m0["products"][0]))
# A product is projected through its OWN route's keys. Writing an amazon row of
# nulls for a brand product would invite a reader to join on them.
bv_view = dg.contract_view(dict(
    dg.normalise_for_contract(bv_group), version=1, declared_at="2026-10-15",
    readings_at_declare=0, post_collection=False,
    comparability=dg.comparability(bv_group)))
bvp = bv_view["members"][0]["products"][0]
check("a brand product carries the route it is read through",
      bvp["route"] == "bazaarvoice", bvp)
check("a brand product carries the zone, the product id and §2b's join key",
      all(k in bvp for k in ("client", "product_id", "family",
                             "family_product_ids")), sorted(bvp))
check("the join key is the declared label, which the collector reads",
      bvp["family"] == "fam-tfb57gle", bvp)
check("the fingerprint stays in the verification record as the EVIDENCE, not "
      "as the join key",
      bvp["verification"]["family_fingerprint"]
      == BV_VERIFICATION["family_fingerprint"])
check("a family with no compared sibling is reported UNVERIFIED, never verified",
      bvp["verification"]["family_verified"] is False,
      bvp["verification"].get("family_verified"))
check("and it says what was and was not established",
      "does_not_establish" in bvp["verification"]["family_check"],
      bvp["verification"].get("family_check"))
two_id = dg.normalise_for_contract(group(members=[
    bv_member(products=[bv_product(
        family_product_ids=["TFB57GLE", "TFB78GLE"])]), weber_member()]))
check("a family with a compared sibling IS verified",
      two_id["members"][0]["products"][0]["verification"]["family_verified"]
      is True)
check("a brand product does NOT carry Amazon keys at all",
      not any(k in bvp for k in ("asin", "parent_asin", "marketplace")), sorted(bvp))
check("the continuity reason and the verification travel with it",
      bvp["continuity_reason"] and bvp["verification"]["status"] == "resolved")
check("an amazon product still carries its own keys, and says so explicitly",
      v["members"][0]["products"][0]["route"] == "amazon"
      and "asin" in v["members"][0]["products"][0],
      v["members"][0]["products"][0])
check("a default route is written explicitly at freeze time, not left implied",
      dg.normalise_for_contract(group())["members"][0]["products"][0]["route"]
      == "amazon")
check("the brand view hashes over its own contents",
      bv_view["hash"] == dg.content_hash(bv_view))

check("the hash is over the file minus the hash field",
      v["hash"] == dg.content_hash(v))
check("changing any field changes the hash",
      dg.content_hash(dict(v, seed="something else")) != v["hash"])
check("re-adding the same hash field is idempotent",
      dg.content_hash(dict(v, hash="deadbeef")) == v["hash"])

# -------------------------------------------- freeze, chain, edit detection
print("\nthe freeze and the version chain")

tmp = tempfile.mkdtemp(prefix="pg-test-")
cwd = os.getcwd()
try:
    os.chdir(tmp)
    os.makedirs(dg.GROUPS_DIR)
    os.makedirs("results")
    draft_path = os.path.join(dg.GROUPS_DIR, "soft-drinks-global.draft.json")
    dg.write_json(draft_path, group())

    rc = dg.main(["declare", draft_path])
    v1 = os.path.join(dg.GROUPS_DIR, "soft-drinks-global.v1.json")
    check("declare writes v1", rc == 0 and os.path.exists(v1))
    check("declare writes the .hash sidecar", os.path.exists(dg.hash_path(v1)))
    v1obj = dg.read_json(v1)
    check("v1 records the declaration date", bool(v1obj.get("declared_at")))
    check("v1 carries the computed comparability block",
          v1obj.get("comparability", {}).get("cross_marketplace") is False, v1obj.get("comparability"))
    check("v1 records readings_at_declare = 0", v1obj.get("readings_at_declare") == 0)
    check("v1 is not post_collection", v1obj.get("post_collection") is False)
    check("the sidecar hash matches the file bytes",
          dg._recorded_hash(v1) == dg.sha256_file(v1))
    check("the hash is over the exact serialisation",
          dg.sha256_file(v1) == dg.sha256_bytes(dg.dumps(v1obj).encode("utf-8")))

    rc = dg.main(["declare", draft_path])
    check("a second declare is refused", rc == 1)

    # revise: add Keurig Dr Pepper
    kdp = member(company="Keurig Dr Pepper Inc.",
                 venue="NASDAQ:KDP",
                 brands=["Dr Pepper", "7UP"],
                 reason=("Sells carbonated soft drinks under Dr Pepper and 7UP, "
                         "bought instead of a cola or a lemon-lime can at the "
                         "same shelf."),
                 products=[{"asin": "B01N0XCS0Y", "parent_asin": "B0PARENTDP",
                            "marketplace": "amazon.com",
                            "brand": "Dr Pepper",
                            "name": "Dr Pepper Soda, 12 fl oz, 12 Pack",
                            "continuity_reason": "The standard 12-can pack of the "
                                                 "core line, sold since 2014.",
                            "verification": {"status": "resolved",
                                             "checked_at": "2026-10-11T00:00:00Z",
                                             "route": "http-direct",
                                             "parent_asin_seen": "B0PARENTDP",
                                             "title_seen": "Dr Pepper Soda, 12 fl oz, "
                                                           "12 Pack"}}])
    g2 = group(members=group()["members"] + [kdp])
    d2 = os.path.join(dg.GROUPS_DIR, "soft-drinks-global.draft2.json")
    dg.write_json(d2, g2)

    rc = dg.main(["revise", "soft-drinks-global", "--from-draft", d2,
                  "--reason", "too short"])
    check("revise refuses a short reason", rc != 0)
except SystemExit as e:
    check("revise refuses a short reason", e.code not in (0, None), "exit %s" % e.code)

try:
    rc = dg.main(["revise", "soft-drinks-global", "--from-draft", d2, "--reason",
                  "Keurig Dr Pepper sells Dr Pepper and 7UP into the same purchase "
                  "and was missed when the group was first declared."])
    v2 = os.path.join(dg.GROUPS_DIR, "soft-drinks-global.v2.json")
    check("revise writes v2", rc == 0 and os.path.exists(v2))
    v2obj = dg.read_json(v2)
    check("v2 supersedes v1 by hash",
          (v2obj.get("supersedes") or {}).get("sha256") == dg.sha256_file(v1))
    check("v2 records the revision reason", len(v2obj.get("revision_reason", "")) >= 40)
    check("v2 records the member diff",
          v2obj["member_diff_from_previous"]["members_added"] == ["Keurig Dr Pepper Inc."],
          v2obj.get("member_diff_from_previous"))
    check("v1 was not touched by the revision",
          dg._recorded_hash(v1) == dg.sha256_file(v1))
    check("verify passes on a clean chain", dg.main(["verify"]) == 0)

    # The order-of-operations witness: a revision after a reading exists.
    with open(dg.READINGS, "w") as f:
        f.write(json.dumps({"reading_id": "r1", "peer_group_id": "soft-drinks-global",
                            "company": "The Coca-Cola Company"}) + "\n")
    check("readings_for counts the group's readings",
          dg.readings_for("soft-drinks-global") == 1)
    d3 = os.path.join(dg.GROUPS_DIR, "soft-drinks-global.draft3.json")
    dg.write_json(d3, group())
    dg.main(["revise", "soft-drinks-global", "--from-draft", d3, "--reason",
             "Dropped Keurig Dr Pepper again, which is exactly the move the "
             "post_collection mark exists to make visible."])
    v3obj = dg.read_json(os.path.join(dg.GROUPS_DIR, "soft-drinks-global.v3.json"))
    check("a version declared after a reading is marked post_collection",
          v3obj.get("post_collection") is True and v3obj.get("readings_at_declare") == 1)
    with open(dg.hash_path(os.path.join(dg.GROUPS_DIR,
                                        "soft-drinks-global.v3.json"))) as f:
        side = f.read()
    check("the sidecar carries post_collection=true", "post_collection=true" in side, side)
    check("the diff records the removal",
          v3obj["member_diff_from_previous"]["members_removed"] == ["Keurig Dr Pepper Inc."],
          v3obj.get("member_diff_from_previous"))

    # Stage G, CONTRACTS.md §4: invoked as `--group <group_id>`, exit 0/2/3.
    check("stage G exits 3 for a group that was never declared",
          dg.main(["--group", "no-such-group"]) == 3)
    # A declared group is NOT satisfied until its parents have been
    # re-resolved: two members can converge onto one parent after declaration
    # with no identifier changing, so "declared" is not "still valid".
    check("a declared but never-re-resolved group is NOT satisfied",
          dg.main(["--group", "soft-drinks-global"]) == 2)
    check("declare wrote the contract view",
          os.path.exists(os.path.join(dg.GROUPS_VIEW_DIR, "soft-drinks-global.json")))
    vp = os.path.join(dg.GROUPS_VIEW_DIR, "soft-drinks-global.json")
    vp_record = dg.versions_of("soft-drinks-global")[-1][1]

    def _view_problems(path):
        ok_, rec_, act_ = dg.verify_contract_view(path)
        return [] if ok_ else ["%s: hash field says %s, contents hash to %s"
                               % (path, (rec_ or "none")[:12], act_[:12])]

    ok_, rec, act = dg.verify_contract_view(vp)
    check("the written view verifies its own hash", ok_, (rec, act))
    tampered = dg.read_json(vp)
    tampered["members"][0]["brands"].append("Fanta")
    with open(vp, "w") as f:
        f.write(dg.dumps(tampered))
    ok_, rec, act = dg.verify_contract_view(vp)
    check("a tampered view fails its own hash", not ok_)
    check("stage G exits 2 on an integrity violation",
          dg.main(["--group", "soft-drinks-global"]) == 2)
    check("and names the view hash mismatch, not only the missing sweep",
          any("contents hash to" in pr for pr in
              [p2 for p2 in dg.parent_drift_state("soft-drinks-global",
                                                  dg.read_json(vp_record))["problems"]]
              + _view_problems(vp)), "see stage G output above")
    dg.main(["reemit", "soft-drinks-global"])
    check("reemit rebuilds the view from the record of declaration",
          dg.verify_contract_view(vp)[0])

    # ---- parent drift: the gap between declaration and collection ----
    # validate() refuses two members sharing a parent AT DECLARATION. Amazon
    # re-parents afterwards, so two members can converge with no identifier
    # changing anywhere. These are the tests for the route nothing watched.
    print("\nparent drift after declaration")
    import chain as _chain
    gid = "soft-drinks-global"
    gv = dg.read_json(dg.versions_of(gid)[-1][1])
    plog = dg.parents_log(gid)

    check("declared_units lists every tracked product",
          len(dg.declared_units(gv)) == len(gv["members"]), dg.declared_units(gv))
    check("stage G refuses a group whose parents were never re-resolved",
          dg.main(["--group", gid]) == 2)
    check("the refusal says why, not just that",
          any("never been re-resolved" in pr
              for pr in dg.parent_drift_state(gid, gv)["problems"]))

    def sweep(**over):
        rec = {"action": "sweep", "group_version": gv["version"], "products": 2,
               "findings": [], "blocked": False, "complete": True}
        rec.update(over)
        return _chain.append(plog, rec)

    sweep()
    check("a clean, current sweep satisfies the gate",
          dg.parent_drift_state(gid, gv)["problems"] == [],
          dg.parent_drift_state(gid, gv)["problems"])
    check("stage G passes after a clean sweep", dg.main(["--group", gid]) == 0)

    # A sweep that found a convergence must refuse, exactly as declaration does.
    sweep(findings=["CONVERGED: A (B0AAA) and B now share parent B0PARENTXX. "
                    "They would be handed identical reviews"])
    st = dg.parent_drift_state(gid, gv)
    check("a convergence found AFTER declaration refuses",
          any("CONVERGED" in pr for pr in st["problems"]), st["problems"])
    check("stage G exits 2 on a post-declaration convergence",
          dg.main(["--group", gid]) == 2)

    sweep(findings=["Coca-Cola B000TNY0AM: parent moved B0OLD -> B0NEW"])
    check("a re-parenting refuses too - the review pool is a different family",
          any("parent moved" in pr for pr in dg.parent_drift_state(gid, gv)["problems"]))

    sweep(findings=["Pepsi B00N4OTQKM: no longer on sale (not_found)"])
    check("a product that ended refuses",
          any("no longer on sale" in pr
              for pr in dg.parent_drift_state(gid, gv)["problems"]))

    sweep(blocked=True, complete=False)
    check("a blocked sweep is unchecked, not checked-and-clean",
          any("did not complete" in pr
              for pr in dg.parent_drift_state(gid, gv)["problems"]),
          dg.parent_drift_state(gid, gv)["problems"])

    sweep(group_version=gv["version"] - 1)
    check("a sweep of an older version does not satisfy the current one",
          any("declaration of record is v" in pr
              for pr in dg.parent_drift_state(gid, gv)["problems"]))

    sweep()
    stale = (dt.datetime.now(dt.timezone.utc)
             + dt.timedelta(days=dg.REPARENT_CHECK_DAYS + 1))
    st = dg.parent_drift_state(gid, gv, now=stale)
    check("a sweep older than the cadence refuses",
          any("past the %d-day cadence" % dg.REPARENT_CHECK_DAYS in pr
              for pr in st["problems"]), st["problems"])
    check("and it is clean again inside the cadence",
          dg.parent_drift_state(gid, gv)["problems"] == [])

    # The log is the record, so tampering with it must show.
    with open(plog, encoding="utf-8") as f:
        lines = f.read().strip().split("\n")
    with open(plog, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[:1] + lines[2:]) + "\n")
    check("a removed line in the parents log is caught",
          any("parents log" in pr
              for pr in dg.parent_drift_state(gid, gv)["problems"]),
          dg.parent_drift_state(gid, gv)["problems"])
    with open(plog, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    check("restoring the log clears the break",
          dg.parent_drift_state(gid, gv)["problems"] == [])

    # the edit-in-place detector
    edited = dg.read_json(v1)
    edited["members"][0]["brands"].append("Fanta")
    with open(v1, "w") as f:
        f.write(dg.dumps(edited))
    check("verify detects an edit in place", dg.main(["verify"]) == 1)
finally:
    os.chdir(cwd)
    shutil.rmtree(tmp, ignore_errors=True)

# ------------------------------------------------- the page classifier (§3)
print("\nthe product-page classifier")
check("a block is recorded as blocked, never as resolved",
      dg.classify_product_page(503, None, "B000TNY0AM")["status"] == "blocked")
check("a captcha body is blocked",
      dg.classify_product_page(200, b"<title>Amazon.com</title>Enter the characters "
                               b"you see below", "B000TNY0AM")["status"] == "blocked")
check("a 404 is not_found",
      dg.classify_product_page(404, None, "B000TNY0AM")["status"] == "not_found")
res = dg.classify_product_page(
    200, b'<title>Coca-Cola Classic : Amazon.com</title>'
         b'<span id="productTitle">Coca-Cola Classic, 12 Pack</span>'
         b'<div id="availability">In Stock</div>B000TNY0AM', "B000TNY0AM")
check("a product page resolves", res["status"] == "resolved", res)
check("the title is captured", res["title_seen"] == "Coca-Cola Classic, 12 Pack", res)
unav = dg.classify_product_page(
    200, b'<span id="productTitle">Coca-Cola Classic</span>'
         b'<div id="availability"><span>Currently unavailable.</span></div>'
         b'B000TNY0AM', "B000TNY0AM")
check("an unavailable product is not resolved", unav["status"] == "unavailable", unav)

# The regression that live data found. An in-stock Amazon page carries the
# literal string "Currently unavailable." inside a <script> localisation table
# on EVERY page. A whole-page scan therefore called five flagship in-stock
# products unavailable. Availability is read from script-stripped text only.
instock = dg.classify_product_page(
    200, b'<span id="productTitle">Sony WH-1000XM6</span>'
         b'<script type="a-state">{"currentlyUnavailableMessage":'
         b'"Currently unavailable.","tapToExpand":"Tap to expand."}</script>'
         b'<div id="availability" class="a-section"> In Stock </div>'
         b'B0F3PQHWTZ', "B0F3PQHWTZ")
check("a 'Currently unavailable' string inside a <script> does NOT mark the "
      "product unavailable", instock["status"] == "resolved", instock)
check("availability is read from the buy box",
      instock["availability_seen"] == "In Stock", instock)

# The other half: a page whose buy box is script-loaded is still resolved, and
# says that availability was not established rather than implying it was.
nobox = dg.classify_product_page(
    200, b'<span id="productTitle">Coca-Cola Bottle, 12 Ounce, Pack of 24</span>'
         b'B01KHRNMQ4', "B01KHRNMQ4")
check("a script-loaded buy box does not make a product unavailable",
      nobox["status"] == "resolved", nobox)
check("and the record says availability was not established",
      "not established" in nobox["availability_seen"], nobox)
pa = dg.classify_product_page(
    200, b'<span id="productTitle">JBL Tour One M3</span>'
         b'<div id="availability">In Stock</div>'
         b'{"parentAsin":"B0F1FTMBJX"}B0DWTD2D4M', "B0DWTD2D4M")
check("the parent ASIN is captured", pa.get("parent_asin_seen") == "B0F1FTMBJX", pa)
check("a variant child is flagged as one", pa.get("is_variant_child") is True, pa)
pa2 = dg.classify_product_page(
    200, b'<span id="productTitle">Coca-Cola</span><div id="availability">In Stock</div>'
         b'{"parentAsin":"B01KHRNMQ4"}', "B01KHRNMQ4")
check("a product that is its own parent is not flagged",
      pa2.get("is_variant_child") is False, pa2)

check("strip_scripts removes script bodies",
      "secret" not in dg.strip_scripts('<script>var x="secret";</script>ok'))
wrong = dg.classify_product_page(
    200, b'<span id="productTitle">Something Else</span>', "B000TNY0AM")
check("a page not carrying the ASIN is an error, not a resolution",
      wrong["status"] == "error", wrong)

print("\n%d failure(s)" % len(FAILURES))
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
