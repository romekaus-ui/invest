#!/usr/bin/env python3
"""Screen a candidate pool against PLAN.md's concentration filter.

Reads `data/peer_groups/candidate_pool.json` - groups of companies that
plausibly sell substitutable products - and for every US registrant in it
fetches the concentration evidence from the company's own latest annual report
(`declare_group.concentration_evidence`).

It does NOT decide. It gathers the same evidence for every company, in the same
words, and writes it down with the URL it came from. Reading a share of revenue
off that evidence is a judgement, and it is made where judgements are made:
by the agent, recorded in the declaration.

This exists because the target is ~80 companies. Fetching one filing per
company by hand does not scale and would make the evidence vary with how
much attention each company happened to get.

Output: data/peer_groups/pool_evidence.json
        a table on stdout, and the counts that answer "does the pool run out"

Standard library only. Needs SEC_USER_AGENT.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import declare_group as dg  # noqa: E402

POOL = os.path.join("data", "peer_groups", "candidate_pool.json")
OUT = os.path.join("data", "peer_groups", "pool_evidence.json")

# Probes whose presence means a revenue split is actually disclosed somewhere in
# the filing. `percent_of_revenue` alone is weak - it matches a customer
# concentration or a geographic split just as readily as a product one.
STRONG = ("by_category", "disaggregation", "single_segment", "segments")


def main():
    pool = dg.read_json(POOL)
    store = dg.read_json(OUT) if os.path.exists(OUT) else {}
    idx = dg.ticker_index()
    if not idx:
        sys.exit("could not fetch the SEC ticker index")

    us, nonus, private, missing = [], [], [], []
    t0 = time.time()
    for grp in pool["groups"]:
        for company, venue in grp["members"]:
            if venue == "private":
                private.append((company, grp["group_id"]))
                continue
            ex, tk = venue.split(":", 1)
            hit = idx.get(tk.upper())
            if not hit:
                (missing if ex in ("NYSE", "NASDAQ") else nonus).append(
                    (company, venue, grp["group_id"]))
                continue
            cik = hit[0]
            us.append((company, venue, cik, grp["group_id"]))

    print("pool: %d groups, %d member slots" % (
        len(pool["groups"]), sum(len(g["members"]) for g in pool["groups"])))
    print("  US registrants SEC knows      %d" % len(us))
    print("  US tickers SEC does NOT know  %d" % len(missing))
    print("  listed elsewhere              %d" % len(nonus))
    print("  private                       %d" % len(private))
    print()

    for company, venue, cik, gid in us:
        key = "%s|%s" % (company, venue)
        if key in store and store[key].get("ok"):
            print("  %-46s %-14s cached" % (company[:46], venue))
            continue
        ev = dg.concentration_evidence(cik)
        ev["company"], ev["venue"], ev["group_id"], ev["cik"] = company, venue, gid, cik
        store[key] = ev
        f = ev.get("filing") or {}
        probes = sorted((ev.get("probes") or {}).keys())
        strong = [p for p in probes if p in STRONG]
        print("  %-46s %-14s %-7s %-11s %s" % (
            company[:46], venue, f.get("form") or "-", f.get("filed") or "-",
            ",".join(strong) if strong else ("WEAK:" + ",".join(probes) if probes
                                             else "NO EVIDENCE FOUND")))
    elapsed = time.time() - t0

    dg.write_json(OUT, store)
    got = [v for v in store.values() if v.get("ok")]
    strong_n = sum(1 for v in got
                   if any(p in STRONG for p in (v.get("probes") or {})))
    print()
    print("fetched %d filings in %.0fs (%.1fs each)"
          % (len(got), elapsed, elapsed / max(1, len(us))))
    print("with a disclosed revenue split to read: %d of %d US registrants"
          % (strong_n, len(us)))
    print("evidence: %s" % OUT)
    print()
    print("Not screened mechanically, evidence recorded by hand against the "
          "company's own report:")
    for company, venue, gid in nonus:
        print("  %-46s %-14s %s" % (company[:46], venue, gid))
    for company, venue, gid in missing:
        print("  %-46s %-14s %s  (US ticker SEC does not list - check whether "
              "the company still trades)" % (company[:46], venue, gid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
