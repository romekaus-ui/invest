#!/usr/bin/env python3
"""Tests for evidence_log.py — the append-only, hash-chained evidence record.

    python3 .claude/skills/review-tone-peer-group/tests/test_evidence_log.py

The point of the log is that a silent edit to the evidence is detectable, so
the tests that matter are the ones that tamper with it and check that the
tampering is found. Standard library only, no network.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import evidence_log as el  # noqa: E402
import screen_pool as sp  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def ev(company, entity, probes=("single_segment",)):
    return {"ok": True, "company": company,
            "filing": {"form": "10-K", "entity": entity,
                       "url": "https://www.sec.gov/x.htm"},
            "probes": {p: ["..."] for p in probes}}


tmp = tempfile.mkdtemp(prefix="evlog-")
cwd = os.getcwd()
try:
    os.chdir(tmp)
    os.makedirs(os.path.join("data", "peer_groups"))

    print("the chain")
    el.append({"action": "fetch", "key": "A|NYSE:A", "evidence": ev("A Corp", "A CORP")})
    el.append({"action": "fetch", "key": "B|NYSE:B", "evidence": ev("B Inc", "B INC")})
    el.append({"action": "fetch", "key": "C|NYSE:C", "evidence": ev("C Ltd", "C LTD")})
    check("three lines are written", len(el.read_log()) == 3)
    check("the chain walks clean", el.walk() == [], el.walk())
    rows = [r for _, r in el.read_log()]
    check("the first line chains to genesis", rows[0]["prev"] == el.GENESIS)
    check("each line chains to the hash of the one before",
          rows[1]["prev"] == el.line_hash(rows[0])
          and rows[2]["prev"] == el.line_hash(rows[1]))
    check("every record is stamped with when it was recorded",
          all(r.get("recorded_at") for r in rows))

    print("\nthe snapshot")
    digest, n = el.write_snapshot()
    check("the snapshot derives three records", n == 3)
    check("the snapshot on disk equals what the log derives",
          json.load(open(el.SNAPSHOT)) == el.derive())
    check("the snapshot hash sidecar matches the snapshot bytes",
          open(el.SNAPSHOT_HASH).read().split()[0] == digest)
    check("verify passes on an untouched log", el.mode_verify(None) == 0)

    print("\na withdrawal is appended, never deleted")
    el.append({"action": "withdraw", "key": "B|NYSE:B",
               "reason": "wrong company: SEC returned a different entity"})
    el.write_snapshot()
    state = el.derive()
    check("the withdrawn record leaves the snapshot", "B|NYSE:B" not in state)
    check("the other two remain", set(state) == {"A|NYSE:A", "C|NYSE:C"}, sorted(state))
    check("the withdrawn evidence is still IN the log",
          any(r.get("key") == "B|NYSE:B" and r.get("action") == "fetch"
              for _, r in el.read_log()))
    check("the chain still walks clean after a withdrawal", el.walk() == [])
    check("verify still passes", el.mode_verify(None) == 0)

    print("\ntampering is detected")
    # (1) a line altered in the middle
    with open(el.LOG, encoding="utf-8") as f:
        original = f.read()
    lines = original.strip().split("\n")
    doctored = json.loads(lines[1])
    doctored["evidence"]["probes"] = {"by_category": ["fabricated"]}
    lines[1] = json.dumps(doctored, sort_keys=True, separators=(",", ":"))
    with open(el.LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    problems = el.walk()
    check("an altered line breaks the chain", problems != [], problems)
    check("the break names the line after the altered one",
          "line 3" in problems[0], problems[0])
    check("verify exits 2 on a broken chain", el.mode_verify(None) == 2)

    # (2) a line removed from the middle
    with open(el.LOG, "w", encoding="utf-8") as f:
        f.write(original)
    check("restoring the original clears the break", el.walk() == [])
    lines = original.strip().split("\n")
    with open(el.LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[:1] + lines[2:]) + "\n")
    check("a removed line breaks the chain", el.walk() != [], el.walk())

    # (3) the snapshot edited without touching the log
    with open(el.LOG, "w", encoding="utf-8") as f:
        f.write(original)
    el.write_snapshot()
    snap = json.load(open(el.SNAPSHOT))
    snap["D|NYSE:D"] = ev("D Co", "D CO")
    with open(el.SNAPSHOT, "w") as f:
        json.dump(snap, f)
    check("a snapshot edited behind the log is caught", el.mode_verify(None) == 2)

    print("\nthe entity cross-check (defect 11)")
    check("a different company is refused",
          sp.entity_matches("EssilorLuxottica S.A.", "ESTEE LAUDER COMPANIES INC")
          is False)
    check("Societe BIC vs BlackBerry is refused",
          sp.entity_matches("Societe BIC", "BLACKBERRY Ltd") is False)
    check("an exact match passes",
          sp.entity_matches("Celsius Holdings, Inc.", "Celsius Holdings, Inc."))
    check("a legal-suffix difference passes",
          sp.entity_matches("Helen of Troy Limited (Hydro Flask)", "HELEN OF TROY LTD"))
    check("a /DE/ suffix passes",
          sp.entity_matches("Church & Dwight Co., Inc. (Waterpik)",
                            "CHURCH & DWIGHT CO INC /DE/"))
    check("a dropped article passes",
          sp.entity_matches("The Simply Good Foods Company", "Simply Good Foods Co"))
    check("an empty entity is not treated as a mismatch",
          sp.entity_matches("Anything Inc", ""))
finally:
    os.chdir(cwd)
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(FAILURES))
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
