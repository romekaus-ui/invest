#!/usr/bin/env python3
"""Run the review-tone pipeline end to end, stopping at the first unmet gate.

This is the orchestration layer. It exists so that the pipeline runs on a
schedule instead of being retyped as a prompt, and so that the phase gates in
CORE-PROMPT s8 are enforced by code rather than by whoever is at the keyboard
at the time.

What it does, in order:

  1. Reads project state and evaluates every entry gate (pipeline_state.py).
     No gate decision is made in prose.
  2. Runs three integrity checks that override everything else: held-out
     contamination, append-only violation, and spent hypotheses.
  3. Walks the stage list in phase order. A stage runs only when every gate it
     depends on is met and its output is missing. The walk stops at the FIRST
     thing that is not satisfied - an unmet gate, a step that needs a human, or
     a script that has not been written - and says which one. It never
     continues "provisionally".
  4. Appends one row to results/results.jsonl, validated first.
  5. Appends a session summary to REVIEW-TONE-CHANGELOG.md and regenerates the
     dashboard.
  6. Writes a short run report.

Three things it will not do, each of them a rule an automated weekly loop is
otherwise likely to break:

  * It does not read, plot or summarise held-out quarters while the prereg is
    unfrozen. Downstream stages are invoked with `--window dev` until the
    freeze, and the report says so explicitly (s1.4).
  * It does not re-run the backtest against held-out data for a prereg hash
    that has already failed there. A held-out failure is written to MISSES.md
    with verdict no_signal and that is the end of that hypothesis. "Try again
    next week" is exactly the behaviour this refusal exists to prevent.
  * It does not re-run a stage whose output is frozen and hashed. Re-screening
    the universe on a schedule would rewrite the hash that every result row
    points at.

It never outputs a buy, sell, hold, target, size or allocation (s1.1).

Exit codes: 0 reported normally (a gate block is a normal outcome);
2 integrity violation; 3 a stage the run needed is not built yet.

Standard library only.
"""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline_state as ps      # noqa: E402
import results_row               # noqa: E402

MIN_QUARTERS = 12                # CORE-PROMPT s5
EXIT_OK, EXIT_INTEGRITY, EXIT_NOT_BUILT = 0, 2, 3

DASHBOARD_CANDIDATES = (
    ".claude/skills/review-tone-dashboard/scripts/build_dashboard.py",
    ".claude/skills/review-tone-signal/scripts/dashboard.py",
    "scripts/build_dashboard.py",
)


class Stage(object):
    """One pipeline stage.

    kind:
      script  - a script in scripts/ runs it
      manual  - a human does it; a scheduled run must report it, never fake it
      frozen  - produces a hashed artefact that is never regenerated on a
                schedule (explicit flag required)
    """

    def __init__(self, sid, phase, what, kind, requires, outputs,
                 script=None, extra_args=(), per_company=False, heldout=False,
                 reform_flag=None, wants_tickers=False, positional_cik=False):
        self.sid, self.phase, self.what, self.kind = sid, phase, what, kind
        self.requires, self.outputs = requires, outputs
        self.script, self.extra_args = script, list(extra_args)
        self.per_company, self.heldout = per_company, heldout
        self.reform_flag = reform_flag
        self.wants_tickers = wants_tickers or per_company
        self.positional_cik = positional_cik


STAGES = [
    Stage("A1", "A", "Screen US-listed registrants by SIC and point-in-time "
          "market cap", "frozen", [], ["universe"],
          script="screen_rolling.py", reform_flag="--reform-universe"),
    Stage("A2", "A", "Score structural eligibility from filings", "manual",
          ["A1.universe"], ["data/eligibility.csv"]),
    Stage("A3", "A", "Check Amazon review coverage and record the gap",
          "script", ["A2.eligibility"], ["data/coverage.csv"],
          script="coverage.py", reform_flag="--refresh-coverage"),

    # --- development stages. ---------------------------------------------
    # These run before the prereg is frozen, because they have to: the prereg
    # cannot record a calibration kappa it has not measured (prereg s4), and it
    # cannot state the base rate "computed on development data" (s6) before
    # that data has been aggregated. They are therefore invoked with
    # `--window dev` while the prereg is unfrozen, which is what keeps the
    # held-out quarters out of reach mechanically rather than by good
    # intentions. None of them produces a verdict.
    Stage("B2", "B-prep", "Extract reviews from the 2023 corpus", "script",
          ["A3.coverage"], ["reviews.csv"], script="extract_reviews.py",
          per_company=True),
    Stage("B3", "B-prep", "Calibrate the scorer against human labels", "script",
          ["A3.coverage"], ["data/calibration.json"],
          script="score_reviews.py", extra_args=["--calibrate"],
          wants_tickers=True),
    Stage("B4", "B-prep", "Score reviews", "script",
          ["B3.calibration"], ["scores.csv"],
          script="score_reviews.py", per_company=True),
    Stage("B5", "B-prep", "Aggregate quarterly tone and decompose mix",
          "script", ["B3.calibration"], ["quarters.csv"],
          script="aggregate.py", per_company=True),

    # --- the freeze closes development. ----------------------------------
    Stage("B1", "B", "Freeze and commit the prereg", "manual",
          ["A3.coverage", "B3.calibration"], ["prereg_frozen"]),

    # --- everything past the freeze may see the full window. -------------
    Stage("B6", "B", "Fetch point-in-time revenue", "script",
          ["B1.prereg_frozen", "B3.calibration"], ["fundamentals.csv"],
          script="fetch_sec.py", per_company=True, positional_cik=True),
    Stage("B7", "B", "Backtest against baseline and shuffled-date null",
          "script", ["B1.prereg_frozen", "B3.calibration"], [],
          script="backtest.py", heldout=True, wants_tickers=True),
    Stage("C", "C", "Re-run under the degraded ~50-review sampling regime",
          "script", ["B7.heldout"], [], script="degrade.py", heldout=True,
          wants_tickers=True),
    Stage("D", "D", "Live forward collection", "manual", ["C.degradation"], []),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def make_run_id(fp, now):
    seed = json.dumps(fp, sort_keys=True, default=str).encode()
    return "%s-%s" % (now.strftime("%Y%m%dT%H%M%SZ"),
                      hashlib.sha256(seed).hexdigest()[:6])


def outputs_present(stage, root, tickers):
    """Is this stage's output already there? (present, missing_description)"""
    if stage.outputs == ["universe"]:
        u = ps.detect_universe(root)
        return (bool(u["files"]) and not u["problems"]), "data/universe*.csv"
    if stage.outputs == ["prereg_frozen"]:
        p = ps.detect_prereg(root)
        return p["frozen"], "a frozen, committed prereg"
    if not stage.outputs:
        return False, "(produces a result row, not a file)"
    if stage.per_company:
        missing = [t for t in tickers
                   if not all(os.path.exists(os.path.join(root, "data", t, o))
                              for o in stage.outputs)]
        return (not missing and bool(tickers)), "%s for %s" % (
            ", ".join(stage.outputs),
            ", ".join(missing) if missing else "(none)")
    ok = all(os.path.exists(os.path.join(root, o)) for o in stage.outputs)
    return ok, ", ".join(stage.outputs)


def run_script(root, script, argv, dry):
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        return None, "NOT BUILT"
    cmd = [sys.executable, path] + list(argv)
    printable = " ".join([script] + list(argv))
    if dry:
        print("    would run: %s" % printable)
        return 0, printable
    print("    running: %s" % printable)
    r = subprocess.run(cmd, cwd=root)
    return r.returncode, printable


def blocked_companies(root, state, window, frozen):
    """Why each company is out, with the reason recorded at the time.

    Quarter-level floors are read only for quarters inside the development
    window while the prereg is unfrozen: a company blocked by a held-out
    quarter is not something this run is allowed to know (s1.4).
    """
    out = []
    for r in state["eligibility"]["ineligible"]:
        out.append({"ticker": r["ticker"], "stage": "A2 structural",
                    "why": r["reason"]})
    for r in state["coverage"]["unusable"]:
        out.append({"ticker": r["ticker"], "stage": "A3 coverage",
                    "why": r["reason"]})

    cutoff = None
    if not frozen and window and "heldout" in window:
        cutoff = window["heldout"][0]
    d = os.path.join(root, "data")
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            qp = os.path.join(d, name, "quarters.csv")
            if not os.path.exists(qp):
                continue
            try:
                rows = ps.read_csv(qp)
            except (OSError, Exception):
                continue
            visible = []
            for row in rows:
                k = ps.qkey(row.get("fiscal_quarter", ""))
                if cutoff and (k is None or k >= cutoff):
                    continue
                visible.append(row)
            thin = [r for r in visible if not ps.truthy(r.get("sufficient"))]
            usable = len(visible) - len(thin)
            if visible and usable < MIN_QUARTERS:
                out.append({
                    "ticker": name, "stage": "s5 sample floor",
                    "why": "%d usable quarter(s) of %d visible, below the "
                           "minimum of %d%s"
                           % (usable, len(visible), MIN_QUARTERS,
                              " (development window only; held-out not read)"
                              if cutoff else "")})
            elif thin:
                out.append({
                    "ticker": name, "stage": "s5 sample floor",
                    "why": "%d quarter(s) below the review floor -> "
                           "insufficient_history" % len(thin)})
    return out


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------

def walk(root, state, gates, tickers, args):
    """Execute stages until something is not satisfied.

    State and gates are re-read after every stage that runs. A gate evaluated
    once at the top would be stale the moment a stage changed the thing it
    measures, and a stale gate is indistinguishable from an unenforced one.

    Contamination is re-checked after every stage too: an upstream script that
    ignores `--window dev` and writes held-out quarters is caught in the same
    run that caused it, not a week later.
    """
    rec = {"ran": [], "skipped": [], "stop": None, "measurement": None,
           "not_built": False, "integrity": [], "state": state,
           "gates": gates}

    for stage in STAGES:
        gmap = {g["id"]: g for g in rec["gates"]}
        state = rec["state"]

        unmet = [gmap[r] for r in stage.requires
                 if r in gmap and not gmap[r]["met"]]
        if unmet:
            g = unmet[0]
            # A gate this run has just satisfied is not yet visible to the gate
            # evaluator, because the evidence for it is the result row and the
            # row is written after the walk. Say so, rather than reporting a
            # successful backtest as a blocked one.
            if rec.get("measurement") and g["id"].startswith(
                    rec["ran"][-1]["stage"] if rec["ran"] else "\0"):
                rec["stop"] = {
                    "kind": "deferred", "stage": stage.sid,
                    "phase": stage.phase,
                    "message": "stage %s (%s) becomes available once this run's "
                               "result row is written. It runs on the next pass."
                               % (stage.sid, stage.what)}
                return rec
            rec["stop"] = {
                "kind": "gate", "stage": stage.sid, "gate": g["id"],
                "phase": stage.phase,
                "message": "stage %s (%s) requires gate %s, which is not met: %s"
                           % (stage.sid, stage.what, g["id"], g["detail"])}
            return rec

        present, desc = outputs_present(stage, root, tickers)
        if present:
            rec["skipped"].append("%s already satisfied (%s)"
                                  % (stage.sid, desc))
            continue

        if stage.kind == "frozen":
            rec["stop"] = {
                "kind": "frozen_missing", "stage": stage.sid,
                "phase": stage.phase,
                "message": "stage %s produces a hashed artefact (%s) that is "
                           "missing or unverified. A scheduled run will not "
                           "create or re-create it, because every result row "
                           "points at its hash. Run it deliberately with %s."
                           % (stage.sid, desc, stage.reform_flag)}
            return rec

        if stage.kind == "manual":
            rec["stop"] = {
                "kind": "manual", "stage": stage.sid, "phase": stage.phase,
                "message": "stage %s (%s) is a human step, not a script. "
                           "Missing: %s. A scheduled run reports it and stops; "
                           "it does not invent the output."
                           % (stage.sid, stage.what, desc)}
            return rec

        if stage.heldout:
            ph = state["prereg"]["hash"]
            if ph and ph in state["dead_hashes"]:
                rec["stop"] = {
                    "kind": "terminal", "stage": stage.sid,
                    "phase": stage.phase,
                    "message": "prereg %s has already failed on held-out data. "
                               "Stage %s will not run again under it. A "
                               "held-out failure ends the hypothesis; it is not "
                               "re-specified and re-run against the same "
                               "held-out quarters." % (ph[:12], stage.sid)}
                return rec

        failed = False
        for argv, label, window_mode in stage_invocations(root, state, stage,
                                                          tickers, args):
            code, printable = run_script(root, stage.script, argv, args.dry_run)
            if code is None:
                rec["not_built"] = True
                rec["stop"] = {
                    "kind": "not_built", "stage": stage.sid,
                    "phase": stage.phase,
                    "message": "stage %s (%s) not built yet: scripts/%s does "
                               "not exist. Every gate ahead of it is met, so "
                               "this is what the pipeline is waiting on."
                               % (stage.sid, stage.what, stage.script)}
                return rec
            if code != 0:
                rec["stop"] = {
                    "kind": "stage_failed", "stage": stage.sid,
                    "phase": stage.phase,
                    "message": "stage %s failed (exit %d): %s"
                               % (stage.sid, code, printable)}
                failed = True
                break
            rec["ran"].append({"stage": stage.sid, "cmd": printable,
                               "window": window_mode, "label": label})
        if failed:
            return rec

        if args.dry_run:
            continue

        rec["state"] = state = ps.collect(root)
        rec["gates"] = ps.evaluate_gates(state)

        viol = state["append_only"] + [
            "HELD-OUT CONTAMINATION: %s" % v
            for v in state["contamination"]["violations"]]
        if viol:
            rec["integrity"] = viol
            rec["stop"] = {
                "kind": "integrity", "stage": stage.sid, "phase": stage.phase,
                "message": "stage %s produced an integrity violation; the run "
                           "stops and writes no result" % stage.sid}
            return rec

        if stage.outputs and stage.outputs not in (["universe"],
                                                   ["prereg_frozen"]):
            ok, missing = outputs_present(stage, root, tickers)
            if not ok:
                rec["stop"] = {
                    "kind": "stage_failed", "stage": stage.sid,
                    "phase": stage.phase,
                    "message": "stage %s exited 0 but its declared output is "
                               "still missing: %s" % (stage.sid, missing)}
                return rec

        if stage.heldout:
            m = read_measurement(root, stage)
            if m is None:
                rec["stop"] = {
                    "kind": "stage_failed", "stage": stage.sid,
                    "phase": stage.phase,
                    "message": "stage %s ran but wrote no measurement to %s; "
                               "there is nothing to put in a result row"
                               % (stage.sid, measurement_path(stage))}
                return rec
            rec["measurement"] = m
    return rec


def measurement_path(stage):
    return os.path.join("results", ".measurement-%s.json" % stage.sid)


def read_measurement(root, stage):
    p = os.path.join(root, measurement_path(stage))
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def stage_invocations(root, state, stage, tickers, args):
    """Yield (argv, label, window_mode) - one call, or one call per company.

    Until the prereg is frozen, every stage is invoked with `--window dev`.
    This is the mechanical half of s1.4; the other half is that this
    orchestrator's own report never reads a held-out row either.
    """
    frozen = state["prereg"]["frozen"]
    window_mode = "full" if frozen else "dev"

    if stage.script == "coverage.py":
        yield list(stage.extra_args) + ["--categories"] + args.categories, \
            "all", "n/a"
        return

    if stage.positional_cik:
        # fetch_sec.py's real interface: positional TICKER CIK, no window flag.
        # Point-in-time revenue is only fetched after the freeze (this stage
        # sits behind B1), so there is no held-out exposure to restrict.
        for t in tickers:
            cik = state["universe"]["ciks"].get(t)
            if not cik:
                raise SystemExit(
                    "no CIK for %s in the universe files; fetch_sec.py needs "
                    "one and it is not this orchestrator's to invent" % t)
            yield list(stage.extra_args) + [t, cik], t, "n/a"
        return

    argv = list(stage.extra_args)
    if stage.wants_tickers:
        argv += ["--tickers"] + list(tickers)
    if stage.heldout:
        argv += ["--prereg", state["prereg"]["path"] or "",
                 "--out", measurement_path(stage)]
    argv += ["--window", window_mode]
    yield argv, "all", window_mode


# --------------------------------------------------------------------------
# result row
# --------------------------------------------------------------------------

NULL_MEASUREMENT = {k: None for k in
                    ("point_estimate", "ci_low", "ci_high", "baseline_value",
                     "base_rate", "null_run_result", "n_quarters",
                     "n_per_quarter")}


def build_row(state, gates, rec, run_id, now, fp, tickers, window):
    phase = ps.phase_now(gates)
    pr = state["prereg"]
    row = {
        "run_id": run_id,
        "run_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase": phase,
        "prereg_hash": pr["hash"] or pr.get("document_hash") or "none",
        "universe_hash": state["universe"]["hash"] or "none",
        "rubric_version": (pr["fields"].get("rubric_version")
                           or state["calibration"]["data"].get("rubric_version")
                           or "none"),
        "scorer_model_id": (pr["fields"].get("scorer_model_id")
                            or state["calibration"]["data"].get("scorer_model_id")
                            or "none"),
        "companies": sorted(tickers),
        "window": window_label(window, pr["frozen"]),
        "specifications_tested": pr["fields"].get("specifications_tested", 0),
        "heldout_evaluated": False,
        "state_fingerprint": fp,
        "orchestrator": "orchestrate.py",
        "stages_run": [r["stage"] for r in rec["ran"]],
    }
    row.update(NULL_MEASUREMENT)

    m = rec.get("measurement")
    if m:
        for k in list(NULL_MEASUREMENT) + ["specifications_tested", "companies"]:
            if m.get(k) is not None:
                row[k] = m[k]
        row["verdict"] = m.get("verdict", "insufficient_data")
        row["heldout_evaluated"] = bool(m.get("heldout_evaluated"))
        if m.get("window"):
            row["window"] = m["window"]
        for k in ("hit_rate", "p_value", "null_distribution",
                  "specifications", "endpoint", "test_statistic"):
            if k in m:
                row[k] = m[k]
    else:
        row["verdict"] = "insufficient_data"
        stop = rec["stop"] or {}
        unmet = ps.first_unmet(gates)
        row["gate_unmet"] = (stop.get("gate") or (unmet or {}).get("id")
                             or "none (no stage produced a measurement)")
        row["stopped_at"] = "%s [%s]" % (stop.get("stage", "?"),
                                         stop.get("kind", "?"))
        row["blocked_reason"] = stop.get("message", "no stage produced a "
                                                    "measurement")
    return row


def window_label(window, frozen):
    if not window:
        return "unknown"
    key = "full" if frozen else "development"
    if key not in window:
        key = "full" if "full" in window else None
    if not key:
        return "unknown"
    (y1, q1), (y2, q2) = window[key]
    return "%dQ%d-%dQ%d (%s)" % (y1, q1, y2, q2,
                                 "full" if frozen else
                                 "development only; held-out not read")


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def build_report(state, gates, rec, row, changes, blocked, dash):
    L = []
    stop = rec["stop"] or {}
    A = L.append
    A("# Review-tone run %s" % row["run_id"])
    A("")
    A("**%s** · phase **%s** · verdict **%s**"
      % (row["run_at"], row["phase"], row["verdict"]))
    if stop:
        A("")
        A("**Stopped at:** %s" % stop["message"])
    A("")

    A("## What changed since the last run")
    A("")
    for c in changes:
        A("- %s" % c)
    A("- log now holds %d result row(s) and %d recorded miss(es)"
      % (len(state["results"]["rows"]) + 1, len(state["misses"]["entries"])))
    A("")

    A("## Gates")
    A("")
    A("| Gate | Entry to | Met | Detail |")
    A("|---|---|---|---|")
    for g in gates:
        A("| `%s` | phase %s | %s | %s |"
          % (g["id"], g["phase"], "yes" if g["met"] else "**NO**",
             g["detail"].replace("|", "/")))
    A("")

    A("## Companies blocked")
    A("")
    if not blocked:
        A("- none recorded (no eligibility, coverage or floor failures on disk)")
    else:
        for b in blocked:
            A("- **%s** — %s: %s" % (b["ticker"], b["stage"], b["why"]))
    A("")

    A("## Verdict")
    A("")
    if row["verdict"] == "insufficient_data":
        A("`insufficient_data`. No measurement was produced this run, so there "
          "is no effect, interval or base rate to report. The pipeline is "
          "waiting on the item under *Stopped at* above.")
    else:
        A("`%s` — point estimate %s (95%% CI %s..%s) against baseline %s, "
          "base rate %s, shuffled-date null %s, over %s company-quarters "
          "(%s per quarter), %s specification(s) tested."
          % (row["verdict"], row["point_estimate"], row["ci_low"],
             row["ci_high"], row["baseline_value"], row["base_rate"],
             row["null_run_result"], row["n_quarters"], row["n_per_quarter"],
             row["specifications_tested"]))
        if row.get("hit_rate") is not None:
            A("")
            try:
                short = float(row["hit_rate"]) <= float(row["base_rate"])
            except (TypeError, ValueError):
                short = False
            A("Hit rate %s against a base rate of %s.%s"
              % (row["hit_rate"], row["base_rate"],
                 " The hit rate does not exceed the base rate: always guessing "
                 "the common outcome would have scored as well, so this is a "
                 "failure and is reported as one (F7)." if short else ""))
    A("")

    A("## Held-out window")
    A("")
    if state["prereg"]["frozen"]:
        dead = state["prereg"]["hash"] in state["dead_hashes"]
        A("Prereg `%s` is frozen (%s). Held-out evaluation is %s."
          % (state["prereg"]["hash"][:12], state["prereg"]["path"],
             "CLOSED for this prereg" if dead else "permitted"))
        if dead:
            A("")
            A("This prereg has already failed on held-out data. The hypothesis "
              "is finished: it is not re-specified and re-run against the same "
              "held-out quarters. Advancing requires a new hypothesis and a "
              "held-out window that has not been spent — a decision for the "
              "user, not for a scheduled run.")
    else:
        A("Prereg not frozen, so development is open and the held-out window "
          "was **not read, not plotted and not summarised** this run. "
          "Downstream stages were invoked with `--window dev`.")
        if state["window"] and "heldout" in state["window"]:
            (y, q), (y2, q2) = state["window"]["heldout"]
            A("")
            A("Held-out quarters (from %s, for exclusion only): %dQ%d–%dQ%d."
              % (state["window"]["source"], y, q, y2, q2))
    A("")

    if state["universe"]["notes"]:
        A("## Staleness")
        A("")
        for n in state["universe"]["notes"]:
            A("- %s" % n)
        A("")

    A("## Dashboard")
    A("")
    A("- %s" % dash)
    A("")
    A("---")
    A("")
    A("Research instrument. It reports measured effects, their intervals and "
      "the base rates they are measured against, and expresses no view on any "
      "security (CORE-PROMPT s1.1).")
    return "\n".join(L) + "\n"


def regenerate_dashboard(root, dry, tickers):
    """Render one dashboard per company.

    The dashboard is per-company by construction: `build_dashboard.py` builds a
    payload for one ticker, and the numbers it may touch are deliberately
    limited to that company's record counts — it is forbidden from computing a
    tone mean, an interval or a base rate (CORE-PROMPT s1.3). A whole-set mode
    would have to combine companies, which is arithmetic of exactly the kind it
    exists to refuse. So the loop belongs here, where the company set is already
    known, and the renderer keeps its one-company honesty gate.

    This is the one place the stage contract is settled in the script's favour
    rather than the orchestrator's, and the reason is that the dashboard is not
    a pipeline stage: it reads what the stages wrote, takes no window, and
    cannot contaminate anything.

    A dashboard failure never invalidates the run — the result row is already
    written — so each company's outcome is reported and the rest still render.
    """
    for cand in DASHBOARD_CANDIDATES:
        p = os.path.join(root, cand)
        if not os.path.exists(p):
            continue
        if not tickers:
            return ("%s exists but the run has no company set, so there was "
                    "nothing to render" % cand)
        if dry:
            return ("would regenerate via %s, once per company: %s"
                    % (cand, ", ".join(sorted(tickers))))
        ok, failed = [], []
        for tic in sorted(tickers):
            r = subprocess.run([sys.executable, p, tic], cwd=root,
                               capture_output=True, text=True)
            if r.returncode == 0:
                ok.append(tic)
            else:
                failed.append("%s (exit %d: %s)"
                              % (tic, r.returncode,
                                 (r.stderr or r.stdout or "").strip()[:160]))
        parts = []
        if ok:
            parts.append("regenerated via %s for %s" % (cand, ", ".join(ok)))
        if failed:
            parts.append("failed for %s" % "; ".join(failed))
        return "; ".join(parts)
    return ("dashboard not built yet — none of %s exists, so there was nothing "
            "to regenerate" % ", ".join(DASHBOARD_CANDIDATES))


CHANGELOG_ENTRY = """
## {date} · orchestrated run {run_id}

Phase {phase}, verdict `{verdict}`. {stop}

- Gates met: {met}
- Gates unmet: {unmet}
- Stages run: {ran}
- Changed since last run: {changes}
- Held-out: {heldout}
- Report: `{report}`
"""


def append_changelog(root, row, gates, rec, changes, frozen, report_rel):
    met = [g["id"] for g in gates if g["met"]] or ["none"]
    unmet = [g["id"] for g in gates if not g["met"]] or ["none"]
    ran = [r["stage"] for r in rec["ran"]] or ["none"]
    stop = (rec["stop"] or {}).get("message", "Ran to completion.")
    entry = CHANGELOG_ENTRY.format(
        date=row["run_at"][:10], run_id=row["run_id"], phase=row["phase"],
        verdict=row["verdict"], stop=stop, met=", ".join(met),
        unmet=", ".join(unmet), ran=", ".join(ran),
        changes="; ".join(changes),
        heldout=("evaluated" if row["heldout_evaluated"] else
                 ("permitted but not evaluated this run" if frozen else
                  "not read (prereg unfrozen, development open)")),
        report=report_rel)
    path = os.path.join(root, "REVIEW-TONE-CHANGELOG.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)
    return entry


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticker", action="append", default=[],
                   help="restrict per-company stages to this ticker "
                        "(repeatable). Must already be in the universe with "
                        "usable coverage.")
    p.add_argument("--all", action="store_true",
                   help="every company with usable coverage (default)")
    p.add_argument("--categories", nargs="+",
                   default=["Home_and_Kitchen", "Appliances"],
                   help="corpus categories for stage A3")
    p.add_argument("--dry-run", action="store_true",
                   help="evaluate gates and print the report; write nothing, "
                        "run nothing")
    p.add_argument("--reform-universe", action="store_true",
                   help="deliberately re-screen the universe. Changes the "
                        "universe hash that existing result rows point at.")
    p.add_argument("--refresh-coverage", action="store_true",
                   help="deliberately re-run the coverage check")
    p.add_argument("--root", default=None, help="project root")
    args = p.parse_args()

    root = os.path.abspath(args.root or os.path.join(HERE, "..", "..", "..",
                                                     ".."))
    if not os.path.exists(os.path.join(root, "REVIEW-TONE-CORE-PROMPT.md")):
        sys.exit("not a review-tone project root: %s (no "
                 "REVIEW-TONE-CORE-PROMPT.md). Pass --root." % root)

    now = utcnow()
    state = ps.collect(root)
    gates = ps.evaluate_gates(state)
    fp = ps.fingerprint(state)
    run_id = make_run_id(fp, now)

    print("review-tone orchestrator · %s · root %s"
          % (run_id, os.path.relpath(root, os.getcwd()) or "."))
    print("")

    # ---- integrity first. These override every other consideration. ----
    fatal = []
    fatal += state["append_only"]
    for v in state["contamination"]["violations"]:
        fatal += ["HELD-OUT CONTAMINATION: %s" % v]
    fatal += ["%s" % x for x in state["results"]["problems"]]
    if fatal:
        print("INTEGRITY VIOLATION - the run stops here and writes nothing.")
        print("")
        for f in fatal:
            print("  !! %s" % f)
        print("")
        print("Nothing was appended to results.jsonl, MISSES.md or the "
              "changelog. These are not conditions a scheduled run may work "
              "around; they need a person.")
        return EXIT_INTEGRITY
    for u in state["contamination"]["boundary_uncertain"]:
        print("  ?? held-out boundary unverifiable: %s" % u)

    # ---- company set ----
    usable = sorted(state["coverage"]["usable"])
    if args.ticker:
        unknown = [t for t in args.ticker if t not in usable]
        if unknown:
            print("refusing: %s %s not in the set of companies with usable "
                  "coverage. A company may not enter the test by being named "
                  "on the command line (CORE-PROMPT s2)."
                  % (", ".join(unknown), "is" if len(unknown) == 1 else "are"))
            if not usable:
                print("  (no company has usable coverage yet: stage A3 has not "
                      "produced data/coverage.csv)")
            return EXIT_INTEGRITY
        tickers = sorted(args.ticker)
    else:
        tickers = usable

    # ---- deliberate re-runs of frozen artefacts ----
    if args.reform_universe:
        print("!! --reform-universe: re-screening the universe. Every existing "
              "result row points at the OLD universe hash.")
        code, _ = run_script(root, "screen_rolling.py", [], args.dry_run)
        if code is None:
            print("   screen_rolling.py missing")
            return EXIT_NOT_BUILT
        state = ps.collect(root)
        gates = ps.evaluate_gates(state)
    if args.refresh_coverage:
        code, _ = run_script(root, "coverage.py",
                             ["--categories"] + args.categories, args.dry_run)
        if code is None:
            return EXIT_NOT_BUILT
        state = ps.collect(root)
        gates = ps.evaluate_gates(state)

    # ---- walk the stages ----
    rec = walk(root, state, gates, tickers, args)
    state, gates = rec["state"], rec["gates"]

    for line in rec["skipped"]:
        print("  -- %s" % line)
    for r in rec["ran"]:
        print("  ++ ran %s%s (window=%s)"
              % (r["stage"], "" if r["label"] == "all" else " [%s]" % r["label"],
                 r["window"]))
    if rec["stop"]:
        print("")
        print("  STOP [%s] %s" % (rec["stop"]["kind"], rec["stop"]["message"]))
    print("")

    if rec["integrity"]:
        print("INTEGRITY VIOLATION caused by this run - writing nothing.")
        for v in rec["integrity"]:
            print("  !! %s" % v)
        print("")
        print("No result row, no MISSES.md entry, no changelog entry. A stage "
              "wrote inside the held-out window or altered an append-only "
              "file; that needs a person, not a retry.")
        return EXIT_INTEGRITY

    # ---- build the row from the state the run ENDED in ----
    m = rec.get("measurement") or {}
    fp = ps.fingerprint(state)
    row = build_row(state, gates, rec, run_id, now, fp, tickers, state["window"])
    changes = ps.diff_fingerprints(
        (state["results"]["rows"][-1].get("state_fingerprint")
         if state["results"]["rows"] else None), fp)
    blocked = blocked_companies(root, state, state["window"],
                               state["prereg"]["frozen"])
    dash = regenerate_dashboard(root, args.dry_run, tickers)

    report = build_report(state, gates, rec, row, changes, blocked, dash)
    report_rel = os.path.join("results", "reports", "%s.md" % run_id)

    if args.dry_run:
        print("--- DRY RUN: nothing written ---")
        print("")
        print(report)
        problems = results_row.validate(row)
        print("result row would %s"
              % ("VALIDATE" if not problems else
                 "BE REJECTED:\n  - " + "\n  - ".join(problems)))
        return EXIT_NOT_BUILT if rec["not_built"] else EXIT_OK

    if row["heldout_evaluated"] and row["verdict"] == "no_signal":
        entry = results_row.append_miss(
            root, now.strftime("%Y-%m-%d"), run_id,
            predicted=m.get("predicted") or
            "signal(Q) carries information about revenue surprise at Q+1 "
            "beyond the baseline",
            actual=m.get("actual") or
            "held-out result did not clear the prereg threshold "
            "(estimate %s, CI %s..%s, baseline %s, base rate %s, null %s)"
            % (m.get("point_estimate"), m.get("ci_low"), m.get("ci_high"),
               m.get("baseline_value"), m.get("base_rate"),
               m.get("null_run_result")),
            prereg_hash=row["prereg_hash"],
            why=m.get("why") or "not yet diagnosed; see the report",
            failure_mode=m.get("failure_mode") or "none identified")
        print("  appended to MISSES.md:")
        print("".join("    %s\n" % l for l in entry.strip().splitlines()))
        print("  This hypothesis is now closed. The orchestrator will refuse "
              "to re-run the backtest under prereg %s."
              % row["prereg_hash"][:12])

    results_row.append_result(root, row)
    print("  appended 1 row to results/results.jsonl (verdict %s)"
          % row["verdict"])

    os.makedirs(os.path.join(root, "results", "reports"), exist_ok=True)
    with open(os.path.join(root, report_rel), "w", encoding="utf-8") as f:
        f.write(report)
    append_changelog(root, row, gates, rec, changes,
                     state["prereg"]["frozen"], report_rel)
    print("  wrote %s and appended to REVIEW-TONE-CHANGELOG.md" % report_rel)
    print("")
    print(report)

    for stage in STAGES:
        mp = os.path.join(root, measurement_path(stage))
        if os.path.exists(mp):
            os.remove(mp)

    return EXIT_NOT_BUILT if rec["not_built"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
