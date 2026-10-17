#!/usr/bin/env python3
"""Every guard must have a case that demonstrably trips it (CONTRACTS.md §5).

    python3 .claude/skills/review-tone-peer-group/tests/test_guard_coverage.py

§5's rule - *a check that always passes is indistinguishable from a check that
passes* - is unenforceable by reading, because the failure looks exactly like
success. So it is enforced mechanically here:

  1. parse `declare_group.py` and find every refusal site in the validators,
     i.e. every `...append(<message>)` inside `validate`, its helpers, and
     `parent_drift_state`;
  2. run the whole declare-group suite with `validate` and `parent_drift_state`
     spied on, recording every problem string any test ever produced;
  3. assert that each refusal site produced at least one of them.

A site with no test is a guard nobody has ever seen fire, which is the same
position the calibration gate, the window module and the no-op assertion were
in before each was found. This test is how that is noticed on the day it is
written rather than months later.

Standard library only, no network.
"""

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
import declare_group as dg  # noqa: E402

SOURCE = os.path.join(SCRIPTS, "declare_group.py")
MAIN_SUITE = os.path.join(HERE, "test_declare_group.py")

# The functions whose appends are refusals a caller acts on.
#
# Keeping this list current is itself load-bearing: a new validator helper that
# is not named here has its refusals excluded from the audit, which is exactly
# the invisible-failure shape §5 exists to prevent. The `sites found` floor
# below is the backstop - it fires if the parser stops seeing them - but it
# cannot notice a whole function missing, so this list is checked when one is
# added. The four route helpers were added with the `route` field on
# 2026-10-15.
GUARD_FUNCS = ("validate", "_validate_venue", "_validate_reporting",
               "_validate_concentration", "_validate_reason",
               "_validate_products", "_validate_amazon_product",
               "_validate_brand_product", "_validate_continuity",
               "_validate_verification", "_validate_brand_measurement",
               "_validate_title_agreement", "parent_drift_state")


def literal_of(node):
    """The static format string of an append argument, or None."""
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        node = node.left
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def probe(text):
    """The longest literal fragment, used to match a produced message."""
    parts = re.split(r"%[sdrf]|%\.\d+f|%%|\s*\n\s*", text)
    parts = [re.sub(r"\s+", " ", x).strip() for x in parts]
    return max(parts, key=len) if parts else ""


def refusal_sites():
    tree = ast.parse(open(SOURCE, encoding="utf-8").read())
    sites = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name not in GUARD_FUNCS:
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "append"
                    and node.args):
                lit = literal_of(node.args[0])
                if lit:
                    sites.append((fn.name, node.lineno, probe(lit)))
    return sites


def run_suite_recording():
    """Run the declare-group suite, recording every problem it produced."""
    seen = []
    orig_validate, orig_drift = dg.validate, dg.parent_drift_state

    def spy_validate(*a, **k):
        out = orig_validate(*a, **k)
        seen.extend(out)
        return out

    def spy_drift(*a, **k):
        out = orig_drift(*a, **k)
        seen.extend(out.get("problems") or [])
        return out

    dg.validate = spy_validate
    dg.parent_drift_state = spy_drift
    # mode_stage and _freeze resolve these at call time through the module, so
    # the spies are seen by them too.
    cwd, out = os.getcwd(), sys.stdout
    try:
        sys.stdout = open(os.devnull, "w")
        src = open(MAIN_SUITE, encoding="utf-8").read()
        try:
            exec(compile(src, MAIN_SUITE, "exec"),
                 {"__name__": "__main__", "__file__": MAIN_SUITE})
        except SystemExit as e:
            if e.code:
                sys.stdout = out
                print("the declare-group suite FAILED; fix it before reading "
                      "guard coverage")
                raise
    finally:
        sys.stdout.close() if sys.stdout is not out else None
        sys.stdout = out
        os.chdir(cwd)
        dg.validate, dg.parent_drift_state = orig_validate, orig_drift
    return seen


def self_check(produced_flat):
    """This audit is itself a check, so §5 applies to it too.

    An audit whose matcher silently matched everything would report full
    coverage for ever and look exactly like an audit that passed. Two negative
    controls, run every time rather than remembered from the day it was
    written:

      - a probe that cannot be in the output must be reported uncovered;
      - a probe taken FROM the output must be reported covered.

    The first catches a matcher that always says yes, the second one that
    always says no.
    """
    fails = []
    impossible = "this exact sentence is not a refusal message anywhere"
    if impossible in produced_flat:
        fails.append("the impossible probe matched; the control is not a control")
    if not produced_flat:
        fails.append("no problems were recorded at all, so coverage is vacuous")
    else:
        real = re.sub(r"\s+", " ", sorted(set(produced_flat.split(" || ")),
                                          key=len)[-1])[:60]
        if real not in produced_flat:
            fails.append("a probe taken from the output did not match it; the "
                         "matcher is broken in the other direction")
    return fails


def main():
    sites = refusal_sites()
    produced = run_suite_recording()
    flat = " || ".join(re.sub(r"\s+", " ", p) for p in produced)

    control = self_check(flat)
    if control:
        print("SELF-CHECK FAILED - this audit cannot be trusted:")
        for c in control:
            print("  - " + c)
        return 1
    if len(sites) < 50:
        print("only %d refusal sites found; the parser has probably stopped "
              "seeing them, which would report full coverage of nothing" % len(sites))
        return 1

    uncovered = [(f, ln, pr) for f, ln, pr in sites if pr and pr not in flat]
    print("self-check:            ok (both negative controls behaved)")
    print("refusal sites found:   %d" % len(sites))
    print("problems produced:     %d (%d distinct)"
          % (len(produced), len(set(produced))))
    print("sites never tripped:   %d" % len(uncovered))
    if uncovered:
        print("\nEvery one of these is a guard no test has ever seen fire "
              "(CONTRACTS.md §5):")
        for fn, ln, pr in uncovered:
            print("  declare_group.py:%-5d %-26s %s" % (ln, fn, pr[:76]))
        return 1
    print("\nevery refusal site is tripped by at least one test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
