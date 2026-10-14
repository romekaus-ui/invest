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

    check("declared_parents lists every tracked product",
          len(dg.declared_parents(gv)) == len(gv["members"]), dg.declared_parents(gv))
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
