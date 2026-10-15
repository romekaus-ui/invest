#!/usr/bin/env python3
"""The workflow: walk the stages in CONTRACTS.md §4, stop at the first unmet one.

It invokes each stage's own command and reads its exit code. It does not
reimplement a stage's satisfaction logic, for the reason established when stage
G's parent sweep was built: a gate that redoes the work it is checking becomes a
second thing that can fail in front of the thing it guards.

It never invents a stage's output, never skips a missing stage, and never
computes a number itself (CORE-PROMPT §1.2).

Exit codes, per §4:
    0  ran, or reported a clean stop
    2  an integrity violation - needs a person
    3  a stage is not built yet

Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))

PEER = os.path.join(ROOT, ".claude/skills/review-tone-peer-group/scripts/declare_group.py")
COLLECT = os.path.join(HERE, "collect_reviews.py")
SCORE = os.path.join(HERE, "score_reviews.py")
AGG = os.path.join(HERE, "aggregate.py")
DASH = os.path.join(ROOT, ".claude/skills/review-tone-dashboard/scripts/build_dashboard.py")

EXIT_OK, EXIT_INTEGRITY, EXIT_NOT_BUILT = 0, 2, 3


def sh(cmd, dry=False):
    """Run a stage. Returns (exit_code, combined_output), or (None, '') if absent."""
    if not os.path.exists(cmd[1]):
        return None, ""
    printable = " ".join([os.path.basename(cmd[1])] + cmd[2:])
    if dry:
        return 0, "would run: %s" % printable
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


sys.path.insert(0, HERE)
from collect_reviews import slugify as _slugify  # noqa: E402


def group_members(group_id):
    """Members of a declared group, from the §1 view the workflow reads."""
    p = os.path.join(ROOT, "groups", "%s.json" % group_id)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        g = json.load(f)
    out = []
    for m in g.get("members") or []:
        # Use the collector's slugify, never a second implementation. A naive
        # lower().replace(" ", "-") produced 'yeti-holdings,-inc.' against the
        # real directory 'yeti-holdings-inc', and every stage then looked in a
        # path that does not exist. Two slug functions is one too many.
        slug = m.get("slug") or _slugify(m.get("company") or "")
        out.append({"company": m.get("company"), "slug": slug,
                    "venue": m.get("venue"),
                    "readable": bool((m.get("concentration") or {}).get(
                        "revenue_readable"))})
    return out


def declared_groups():
    d = os.path.join(ROOT, "groups")
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------

def stage_G(group_id, dry):
    code, out = sh([sys.executable, PEER, "verify", group_id], dry)
    if code is None:
        return "not_built", "declare_group.py is missing", out
    if code != 0:
        return "unmet", "group %s does not verify" % group_id, out
    # `verify` exits 0 on zero versions, so a group that was drafted and never
    # declared would read as verified. "Never declared" and "declared and clean"
    # must not look alike - the same rule stage G applies to its own parent
    # sweep.
    if not dry and out.lstrip().startswith("0 version"):
        return ("unmet",
                "group %s has no declared version - it is a draft, not a "
                "declaration (`declare_group.py declare %s`)" % (group_id, group_id),
                out)
    return "ok", out.strip().splitlines()[-1] if out.strip() else "verified", out


def stage_C(group_id, dry):
    code, out = sh([sys.executable, COLLECT, "status", "--group", group_id], dry)
    if code is None:
        return "not_built", "collect_reviews.py is missing", out
    if code != 0:
        return "unmet", "collection status reports a problem", out
    if dry:
        return "ok", "would check collection status", out
    # The stage's own report is the authority. "no batches yet" anywhere means
    # a tracked product has never been collected, which is C unmet.
    if "no batches yet" in out:
        n = out.count("no batches yet")
        return "unmet", ("%d member(s) have no batch yet - nothing has been "
                         "collected for them" % n), out
    series = "NO BATCH IN THIS GROUP HAS A VERIFIED RECENCY SORT" not in out
    return "ok", ("collected; recency-verified series: %s"
                  % ("yes" if series else "NO - not a time series")), out


def stage_K(group_id, dry):
    p = os.path.join(ROOT, "data", "calibration.json")
    if not os.path.exists(p):
        return ("unmet",
                "data/calibration.json absent. The scorer has not been checked "
                "against human labels, so its numbers are not measurements. "
                "This is a human step and a run must never invent it.", "")
    try:
        with open(p) as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        return "unmet", "cannot read data/calibration.json: %s" % e, ""
    k = d.get("quadratic_weighted_kappa")
    return "ok", "calibrated, kappa %s" % k, ""


def _per_company(script, group_id, dry, extra=()):
    """S, A and D predate the --group contract and still take --tickers.

    The runner translates rather than guessing: it reads the group's members and
    invokes each script the way it actually works. Recorded as a gap in §4, not
    papered over - the scripts should take --group, and until they do this
    translation is the honest wiring.
    """
    members = group_members(group_id) or []
    slugs = [m["slug"] for m in members]
    if not slugs:
        return "unmet", "group %s has no members" % group_id, ""
    # These scripts predate the --group contract and locate data by their own
    # directory flag, which differs per script. The runner translates rather
    # than guessing: it tells each one where this group's data actually lives.
    where = {"score_reviews.py": "--outdir",
             "aggregate.py": "--datadir"}.get(os.path.basename(script))
    argv = [sys.executable, script, "--tickers"] + slugs
    if where:
        argv += [where, os.path.join("data", group_id)]
    code, out = sh(argv + list(extra), dry)
    if code is None:
        return "not_built", "%s is missing" % os.path.basename(script), out
    if code != 0:
        return "unmet", "%s exited %d" % (os.path.basename(script), code), out
    return "ok", "ran for %d member(s)" % len(slugs), out


def stage_S(group_id, dry):
    return _per_company(SCORE, group_id, dry)


def stage_A(group_id, dry):
    return _per_company(AGG, group_id, dry)


def stage_D(group_id, dry):
    members = group_members(group_id) or []
    if not os.path.exists(DASH):
        return "not_built", "build_dashboard.py is missing", ""
    if dry:
        return "ok", "would render %d dashboard(s)" % len(members), ""
    for m in members:
        # Same translation as S and A: build_dashboard.py locates data by
        # --data-dir, and the runner knows which group this is.
        code, out = sh([sys.executable, DASH, m["slug"],
                        "--data-dir", os.path.join("data", group_id),
                        "--company", m["company"] or m["slug"]], dry)
        if code not in (0, None):
            return "unmet", "dashboard failed for %s" % m["slug"], out
    return "ok", "rendered %d dashboard(s)" % len(members), ""


STAGES = [
    ("G", "declare peer groups", stage_G),
    ("C", "collect reviews", stage_C),
    ("K", "calibrate the scorer", stage_K),
    ("S", "score tone", stage_S),
    ("A", "aggregate", stage_A),
    ("D", "render", stage_D),
]


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--group", help="group id; default: every declared group")
    p.add_argument("--dry-run", action="store_true",
                   help="rehearse: report what each stage would do, write nothing")
    args = p.parse_args()

    groups = [args.group] if args.group else declared_groups()
    if not groups:
        print("no declared groups in groups/. Stage G has not run.")
        return EXIT_NOT_BUILT

    worst = EXIT_OK
    for gid in groups:
        print("\n=== %s ===" % gid)
        for sid, what, fn in STAGES:
            state, detail, _out = fn(gid, args.dry_run)
            mark = {"ok": "ok  ", "unmet": "STOP", "not_built": "----"}[state]
            print("  [%s] %s  %s" % (mark, sid, detail))
            if state == "not_built":
                print("       stage %s (%s) is not built yet. That is the honest\n"
                      "       state of the project, not a failure of this run."
                      % (sid, what))
                worst = max(worst, EXIT_NOT_BUILT)
                break
            if state == "unmet":
                print("       stopped at %s (%s). Everything after it is "
                      "untouched." % (sid, what))
                break
    print("\nResearch instrument. It reports measured effects and the coverage "
          "behind them,\nand expresses no view on any security (CORE-PROMPT §1.1).")
    return worst


if __name__ == "__main__":
    sys.exit(main())
