#!/usr/bin/env python3
"""Validate and append the two append-only logs.

`results/results.jsonl` and `MISSES.md` are the only durable evidence this
system produces. CORE-PROMPT s1.6 and s9 govern them:

  * every row carries the full provenance set, and a row missing any of it is
    not a result - it is rejected, not written with gaps;
  * nothing is edited or deleted, ever. Both files may only grow, and only at
    the end.

Appending is therefore done here and nowhere else, so there is exactly one
place where the invariants are enforced:

  1. the row validates against the s9 contract before the file is opened;
  2. the existing bytes are read, the new line is appended, and the prefix is
     re-read and compared - a write that disturbed earlier rows is detected
     rather than assumed impossible;
  3. the file is refused outright if it has already diverged from its committed
     version, because appending to a tampered log launders the tampering.

Standard library only.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import integrity as ps  # noqa: E402

VERDICTS = ("signal", "no_signal", "insufficient_data")

# CORE-PROMPT s9 / data-contracts.md, plus three keys this orchestrator adds:
#   n_per_quarter      - s9 says "n per quarter"; `n_quarters` is a count of
#                        quarters, not a count per quarter, so both are carried
#   heldout_evaluated  - whether this row touched the held-out window. Without
#                        it, a spent hypothesis cannot be told from an
#                        unfinished one, and the no-retry rule cannot be
#                        enforced by a machine
#   state_fingerprint  - what the run saw, so "what changed since last run" is
#                        a diff of the append-only log itself
REQUIRED = (
    "run_id", "run_at", "phase", "prereg_hash", "universe_hash",
    "rubric_version", "scorer_model_id", "companies", "window", "n_quarters",
    "n_per_quarter", "point_estimate", "ci_low", "ci_high", "baseline_value",
    "base_rate", "null_run_result", "specifications_tested", "verdict",
    "heldout_evaluated", "state_fingerprint",
)

# Measured quantities. Present-and-null is legitimate only for
# `insufficient_data`; a row claiming signal or no_signal without them is a
# claim without a measurement behind it.
MEASURED = ("point_estimate", "ci_low", "ci_high", "baseline_value",
            "base_rate", "null_run_result", "n_quarters", "n_per_quarter")


def validate(row):
    """Return a list of problems. Empty means the row is a result."""
    bad = []
    missing = [k for k in REQUIRED if k not in row]
    if missing:
        bad.append("missing required keys: %s" % ", ".join(missing))

    v = row.get("verdict")
    if v not in VERDICTS:
        bad.append("verdict %r is not one of %s" % (v, ", ".join(VERDICTS)))

    if v in ("signal", "no_signal"):
        nulls = [k for k in MEASURED if row.get(k) is None]
        if nulls:
            bad.append("verdict %s but no measurement behind it: %s are null. "
                       "A verdict without a point estimate, interval, baseline, "
                       "base rate and null run is not a result (s9)"
                       % (v, ", ".join(nulls)))
        if row.get("base_rate") is None:
            bad.append("base rate absent: a hit rate without its base rate is "
                       "failure mode F7")
    elif v == "insufficient_data":
        if not row.get("gate_unmet") and not row.get("blocked_reason"):
            bad.append("verdict insufficient_data with no gate_unmet or "
                       "blocked_reason: the row must say what stopped it")

    for k in ("prereg_hash", "universe_hash", "rubric_version",
              "scorer_model_id"):
        if k in row and row[k] in ("", {}, []):
            bad.append("%s is empty" % k)

    if row.get("heldout_evaluated") not in (True, False):
        bad.append("heldout_evaluated must be true or false, not %r"
                   % row.get("heldout_evaluated"))
    if row.get("heldout_evaluated") and not row.get("prereg_hash"):
        bad.append("heldout_evaluated is true with no prereg hash: held-out "
                   "data may not be touched before the prereg is frozen (s1.4)")

    spec = row.get("specifications_tested")
    if spec is not None and not isinstance(spec, int):
        bad.append("specifications_tested must be a count, not %r. Reporting "
                   "only the surviving specification is F6" % (spec,))

    if not isinstance(row.get("companies"), (list, tuple)):
        bad.append("companies must be a list of tickers")
    return bad


def _refuse_if_tampered(root, rel):
    for v in ps.append_only_violations(root):
        if v.startswith(rel):
            raise SystemExit("REFUSING TO APPEND: %s" % v)


def append_result(root, row):
    """Validate, then append exactly one line. Returns the row's byte offset."""
    problems = validate(row)
    if problems:
        raise SystemExit("INVALID RESULT ROW - nothing written:\n  - %s"
                         % "\n  - ".join(problems))
    rel = "results/results.jsonl"
    _refuse_if_tampered(root, rel)
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    before = b""
    if os.path.exists(path):
        with open(path, "rb") as f:
            before = f.read()
    if before and not before.endswith(b"\n"):
        raise SystemExit("REFUSING TO APPEND: %s does not end in a newline; its "
                         "last row is truncated and appending would merge two "
                         "rows" % rel)
    line = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
    with open(path, "ab") as f:
        f.write(line.encode("utf-8"))
    with open(path, "rb") as f:
        after = f.read()
    if not after.startswith(before) or after != before + line.encode("utf-8"):
        raise SystemExit("APPEND CORRUPTED %s: earlier rows changed. Restore "
                         "from git and investigate before running again" % rel)
    return len(before)


MISS_TEMPLATE = """
## {date} · {run_id}
**Predicted:** {predicted}
**Actual:** {actual}
**Prereg hash:** {prereg_hash}
**Why it missed (best current understanding):** {why}
**Failure mode suspected:** {failure_mode}
"""


def append_miss(root, date, run_id, predicted, actual, prereg_hash, why,
                failure_mode="none identified"):
    """Append one MISSES.md entry in the format the file itself specifies.

    Called when a held-out evaluation fails. That write is the end of the
    hypothesis: `pipeline_state.dead_prereg_hashes` reads these entries back
    and the orchestrator will not re-enter the backtest for that prereg hash
    afterwards.
    """
    rel = "MISSES.md"
    _refuse_if_tampered(root, rel)
    path = os.path.join(root, rel)
    entry = MISS_TEMPLATE.format(date=date, run_id=run_id, predicted=predicted,
                                 actual=actual, prereg_hash=prereg_hash or "none",
                                 why=why, failure_mode=failure_mode)
    before = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            before = f.read()
    with open(path, "a", encoding="utf-8") as f:
        if before and not before.endswith("\n"):
            f.write("\n")
        f.write(entry)
    with open(path, encoding="utf-8") as f:
        after = f.read()
    if not after.startswith(before):
        raise SystemExit("APPEND CORRUPTED %s: earlier entries changed" % rel)
    return entry


def main():
    """Validate rows on stdin or in a file; write nothing."""
    import argparse
    p = argparse.ArgumentParser(
        description="Validate results.jsonl rows against CORE-PROMPT s9. "
                    "Reads a file or stdin; never writes.")
    p.add_argument("path", nargs="?", default="-",
                   help="JSONL file, or - for stdin")
    args = p.parse_args()

    src = sys.stdin if args.path == "-" else open(args.path, encoding="utf-8")
    bad = 0
    n = 0
    for i, line in enumerate(src, 1):
        line = line.strip()
        if not line:
            continue
        n += 1
        try:
            row = json.loads(line)
        except ValueError as e:
            print("line %d: not JSON (%s)" % (i, e))
            bad += 1
            continue
        probs = validate(row)
        if probs:
            bad += 1
            print("line %d (%s): NOT A RESULT" % (i, row.get("run_id", "?")))
            for pr in probs:
                print("    - %s" % pr)
    print("%d row(s) checked, %d rejected" % (n, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
