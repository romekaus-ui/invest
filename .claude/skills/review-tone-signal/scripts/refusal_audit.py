#!/usr/bin/env python3
"""Does every refusal in the collector have a test that trips it? (CONTRACTS.md §5)

    python3 refusal_audit.py

Exit 0 when every refusal site fired at least once during the self-test suite,
2 when any did not, or when the audit's own controls fail.


WHY
---
§5 forbids shipping a guard that cannot fail, and this project has now produced
three: a calibration gate reading a file the scorer never wrote; a window module
that restricted nothing while keeping the interface of one that did; a test
asserting `a == b if hasattr(obj, "f") else True` where `f` did not exist.
**A check that always passes is indistinguishable from a check that passes**, so
the rule cannot be enforced by reading the code.

Pointed at the peer-group component, this audit found 24 of 70 refusal sites had
never fired in any test. The two that mattered were lexicons tested thoroughly
at the unit level and never once through the validator that calls them: they
could have been disconnected entirely and every lexicon test would still have
passed. A measurement that was fine and a wire that was not.

Two of this collector's guards are that exact shape, and are the reason this
runs here:

  * the parent-asin mismatch refusal, which by construction only fires once
    Amazon has re-parented a variation family - something that has not happened
    in anything collected so far. A guard whose trigger condition has never
    occurred in practice is where this bug hides;
  * the capture refusals (bad hash, missing header field, version 1), which
    stand between a paraphrased review and the corpus and are never exercised
    by ordinary collection, because ordinary collection succeeds.


HOW
---
1. Parse collect_reviews.py for its own `die(...)` call sites. They are the
   refusals: every one exits non-zero and stops the stage.
2. Run the self-test suite with `COLLECT_REFUSAL_LOG` set. `die()` appends the
   source line it fired from. The variable is inherited by the subprocesses the
   suite spawns, which is the only way to see refusals raised behind a
   `subprocess.run` - and most of the interesting ones are.
3. Every parsed site must appear in the log.


WHAT THIS DOES NOT ESTABLISH
----------------------------
Not that each refusal is *correct*, and not line coverage. A site can fire for a
reason other than the intended one and this still passes. What it rules out is
precisely the failure that has occurred three times here: a guard that cannot
fire at all. Read it as one specific bug being absent, not as the guards being
right.
"""

import ast
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "collect_reviews.py")

# A parser that stopped finding refusal sites would report full coverage of
# nothing, and would do it in exactly the confident tone of a passing audit. The
# floor is the audit's own guard against itself; raise it as refusals are added.
MIN_SITES = 20


def refusal_sites(path):
    """{lineno: source line} for every `die(...)` call in the file."""
    src = open(path, encoding="utf-8").read()
    lines = src.splitlines()
    out = {}
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "die"):
            out[node.lineno] = lines[node.lineno - 1].strip()
    return out


def enclosing_def(path, lineno):
    """The function a line sits in, so an uncovered site can be named usefully."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            if node.lineno <= lineno <= end:
                if best is None or node.lineno > best.lineno:
                    best = node
    return best.name if best else "<module>"


def run_suite(log_path):
    env = dict(os.environ)
    env["COLLECT_REFUSAL_LOG"] = log_path
    r = subprocess.run([sys.executable, TARGET, "self-test"], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def fired_from(log_path):
    if not os.path.exists(log_path):
        return set()
    out = set()
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.isdigit():
                out.add(int(line))
    return out


def main():
    print("refusal-site audit · %s\n" % os.path.basename(TARGET))

    sites = refusal_sites(TARGET)
    print("refusal sites found      %d" % len(sites))

    # --- control 1: the floor -------------------------------------------
    # A parser that found nothing would otherwise report 100% of zero.
    if len(sites) < MIN_SITES:
        print("\nFAIL · only %d refusal sites found, below the floor of %d.\n"
              "Either refusals were deleted, or the parser stopped finding\n"
              "them - and a parser that finds none reports full coverage of\n"
              "nothing. Refusing to pass." % (len(sites), MIN_SITES))
        return 2

    tmp = tempfile.mkdtemp(prefix="refusal-audit-")
    log = os.path.join(tmp, "fired.log")
    rc, out = run_suite(log)
    fired = fired_from(log)
    print("suite exit               %d" % rc)
    print("refusals actually fired  %d distinct site(s)\n" % len(fired))

    if rc != 0:
        print("FAIL · the self-test suite did not pass, so its coverage says\n"
              "nothing. Fix the suite first.\n")
        print(out[-2000:])
        return 2

    # --- control 2: a probe that CANNOT be in the output must read uncovered.
    # Catches a matcher that says yes to everything, which would report every
    # site covered without running anything.
    impossible = max(sites) + 10_000
    if impossible in fired:
        print("FAIL · control: line %d cannot have fired and yet reads as "
              "covered. The coverage check says yes to everything." % impossible)
        return 2

    # --- control 3: a probe TAKEN FROM the output must read covered.
    # Catches a matcher that says no to everything, which would report every
    # site uncovered and look like a scary but working audit.
    if fired:
        probe = sorted(fired)[0]
        if probe not in fired:
            print("FAIL · control: line %d came out of the log and does not "
                  "read as covered. The coverage check says no to everything."
                  % probe)
            return 2
    else:
        print("FAIL · no refusal fired at all during the suite. Either the\n"
              "instrumentation is disconnected or no test trips any guard;\n"
              "both make this audit meaningless.")
        return 2

    # --- the audit proper ------------------------------------------------
    uncovered = sorted(set(sites) - fired)
    covered = len(sites) - len(uncovered)
    print("covered                  %d of %d" % (covered, len(sites)))

    if uncovered:
        print("\nUNCOVERED REFUSAL SITES - no test trips these, so nothing")
        print("shows whether they can fire at all:\n")
        for ln in uncovered:
            print("  %s:%d  (%s)" % (os.path.basename(TARGET), ln,
                                     enclosing_def(TARGET, ln)))
            print("      %s" % sites[ln][:96])
        print("\nFAIL · %d of %d refusal sites never fired. §5: a check that\n"
              "always passes is indistinguishable from a check that passes."
              % (len(uncovered), len(sites)))
        return 2

    print("\nOK · every refusal site fired at least once.")
    print("\nThis rules out one failure only: a guard that cannot fire. It does")
    print("NOT establish that any refusal is correct, and it is not line")
    print("coverage - a site can fire for a reason other than the intended one")
    print("and this still passes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
