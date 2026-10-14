#!/usr/bin/env python3
"""Append-only, hash-chained log of concentration evidence.

`pool_evidence.json` feeds the counts everyone quotes - how many focused
companies exist, how many have a disclosed split, whether the pool runs out -
and it was the one artefact here that a silent edit would not have shown. It is
also the artefact this component has already edited: two poisoned records were
deleted from it when `EPA:EL` turned out to have resolved to Estee Lauder
(CHANGELOG.md defect 11). Deleting them was right and leaving no trace was not.

So the evidence is now written the way the declarations are (CORE-PROMPT §1.5):

  data/peer_groups/pool_evidence.jsonl   append-only, hash-chained. The record.
  data/peer_groups/pool_evidence.json    derived snapshot of the current state.
  data/peer_groups/pool_evidence.hash    sha256 of the snapshot.

Every line carries `prev`, the sha256 of the line before it, so removing or
altering a line in the middle breaks the chain at that point and every point
after it. A withdrawal is an appended `withdraw` record with a reason, never a
deletion: the wrong evidence stays visible, marked wrong, which is the only way
a reader can tell "this was never fetched" from "this was fetched and was
wrong".

Modes
-----
  verify    re-walk the chain and re-derive the snapshot; report any break
  withdraw  append a withdrawal for one record, with a reason
  show      the current snapshot's counts

Standard library only.
"""

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain  # noqa: E402
import declare_group as dg  # noqa: E402

LOG = os.path.join("data", "peer_groups", "pool_evidence.jsonl")
SNAPSHOT = os.path.join("data", "peer_groups", "pool_evidence.json")
SNAPSHOT_HASH = os.path.join("data", "peer_groups", "pool_evidence.hash")

GENESIS = chain.GENESIS
line_hash = chain.line_hash


def read_log(path=LOG):
    return chain.read(path)


def append(record, path=LOG):
    return chain.append(path, record)


def walk(path=LOG):
    return chain.walk(path, actions=("fetch", "withdraw", "note"))


def derive(path=LOG):
    """The current snapshot: the last fetch per key, minus withdrawals."""
    state = {}
    for _, rec in read_log(path):
        if not rec:
            continue
        key = rec.get("key")
        if rec.get("action") == "fetch":
            # A fetch carrying no evidence is a bookkeeping line, not a record.
            if rec.get("evidence") is not None:
                state[key] = rec["evidence"]
        elif rec.get("action") == "withdraw":
            state.pop(key, None)
    return state


def write_snapshot(path=LOG):
    state = derive(path)
    digest = dg.write_json(SNAPSHOT, state)
    with open(SNAPSHOT_HASH, "w", encoding="utf-8") as f:
        f.write("%s  %s  records=%d  log_lines=%d\n"
                % (digest, os.path.basename(SNAPSHOT), len(state),
                   len(read_log(path))))
    return digest, len(state)


def mode_verify(args):
    problems = walk()
    rows = read_log()
    print("log: %s  (%d lines)" % (LOG, len(rows)))
    if problems:
        print("CHAIN BROKEN:")
        for pr in problems:
            print("  - " + pr)
    else:
        print("chain intact")

    state = derive()
    if os.path.exists(SNAPSHOT):
        on_disk = dg.read_json(SNAPSHOT)
        if on_disk != state:
            only_disk = sorted(set(on_disk) - set(state))
            only_log = sorted(set(state) - set(on_disk))
            problems.append(
                "snapshot does not match the log: %d key(s) only on disk (%s), "
                "%d only in the log (%s)"
                % (len(only_disk), ", ".join(only_disk[:3]) or "-",
                   len(only_log), ", ".join(only_log[:3]) or "-"))
            print("SNAPSHOT DRIFT: " + problems[-1])
        else:
            print("snapshot matches the log (%d records)" % len(state))
        if os.path.exists(SNAPSHOT_HASH):
            with open(SNAPSHOT_HASH, encoding="utf-8") as f:
                recorded = f.read().split()[0]
            actual = dg.sha256_file(SNAPSHOT)
            print("snapshot hash %s" % ("ok" if recorded == actual else
                                        "MISMATCH %s vs %s" % (recorded[:12],
                                                               actual[:12])))
            if recorded != actual:
                problems.append("snapshot was edited after it was hashed")
    else:
        print("no snapshot on disk")

    withdrawn = [r for _, r in read_log() if r and r.get("action") == "withdraw"]
    if withdrawn:
        print("\n%d withdrawal(s), kept visible rather than deleted:" % len(withdrawn))
        for r in withdrawn:
            print("  %s  %s" % (r.get("key"), r.get("reason", "")[:96]))
    return 2 if problems else 0


def mode_withdraw(args):
    reason = (args.reason or "").strip()
    if len(reason) < 20:
        sys.exit("--reason is required and must say why the record is wrong")
    if args.key not in derive():
        sys.exit("%r is not in the current snapshot" % args.key)
    rec = append({"action": "withdraw", "key": args.key, "reason": reason})
    write_snapshot()
    print("withdrawn: %s" % args.key)
    print("  %s" % reason)
    print("  line hash %s" % line_hash(rec)[:12])
    return 0


def mode_show(args):
    state = derive()
    strong = sum(1 for v in state.values()
                 if v and any(p in ("by_category", "disaggregation",
                                    "single_segment", "segments")
                              for p in (v.get("probes") or {})))
    print("records %d   with a disclosed split to read %d" % (len(state), strong))
    for k in sorted(state):
        v = state[k] or {}
        f = v.get("filing") or {}
        print("  %-52s %-8s %s" % (k[:52], f.get("form") or "-",
                                   (f.get("entity") or "")[:34]))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="evidence_log.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    v = sub.add_parser("verify", help="re-walk the chain and check the snapshot")
    v.set_defaults(fn=mode_verify)
    w = sub.add_parser("withdraw", help="append a withdrawal, with a reason")
    w.add_argument("key")
    w.add_argument("--reason", required=True)
    w.set_defaults(fn=mode_withdraw)
    sh = sub.add_parser("show", help="the current snapshot")
    sh.set_defaults(fn=mode_show)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
