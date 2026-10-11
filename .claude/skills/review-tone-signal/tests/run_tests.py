#!/usr/bin/env python3
"""Exercise every gate and refusal path of the orchestrator on throwaway copies."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fixtures as F

# Fixtures are throwaway repo copies. Keep them out of the project tree.
BASE = os.environ.get("REVIEW_TONE_TEST_DIR") or os.path.join(
    tempfile.gettempdir(), "review-tone-tests")
ORCH = ".claude/skills/review-tone-signal/scripts/orchestrate.py"
RESULTS = []


def fresh(name):
    dest = os.path.join(BASE, name)
    subprocess.run(["bash", os.path.join(HERE, "mkfixture.sh"), dest],
                   check=True, capture_output=True)
    return dest


def orch(root, *args):
    r = subprocess.run([sys.executable, ORCH] + list(args), cwd=root,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def nrows(root):
    p = os.path.join(root, "results/results.jsonl")
    return len(open(p).readlines()) if os.path.exists(p) else 0


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name,
                         "" if cond else "   <-- " + detail[:400]))


# ---------------------------------------------------------------- 1
print("\n1. real repo state: stops at the manual eligibility stage")
root = fresh("t1")
code, out = orch(root)
check("1a exit 0", code == 0, "exit %d" % code)
check("1b stops at A2", "STOP [manual] stage A2" in out, out[-600:])
check("1c row appended", os.path.exists(os.path.join(root, "results/results.jsonl")))
rows = [json.loads(l) for l in open(os.path.join(root, "results/results.jsonl"))]
check("1d verdict insufficient_data", rows[-1]["verdict"] == "insufficient_data")
check("1e gate_unmet recorded", rows[-1].get("gate_unmet") == "A2.eligibility",
      str(rows[-1].get("gate_unmet")))
check("1e2 stopped_at recorded", rows[-1].get("stopped_at") == "A2 [manual]",
      str(rows[-1].get("stopped_at")))
check("1f heldout not evaluated", rows[-1]["heldout_evaluated"] is False)
check("1g changelog appended",
      "orchestrated run" in open(os.path.join(root, "REVIEW-TONE-CHANGELOG.md")).read())
check("1h report written", os.path.isdir(os.path.join(root, "results/reports")))
check("1i dashboard reported missing", "dashboard not built yet" in out)
check("1j no advice words",
      not any(w in out.lower() for w in (" buy ", " sell ", "price target",
                                         "position size", "allocation of")),
      "advice-shaped language in report")

# second run on unchanged state -> "no change"
code, out2 = orch(root)
check("1k second run reports no change",
      "no change in any tracked artefact" in out2, out2[-600:])

# ---------------------------------------------------------------- 2
print("\n2. fewer than 8 eligible companies -> phase A gate refuses")
root = fresh("t2")
F.eligibility(root, n_eligible=7)
code, out = orch(root)
check("2a stops at A2 gate", "requires gate A2.eligibility" in out, out[-500:])
check("2b says 7 of 8", "7 structurally eligible (needs >= 8)" in out, out[-500:])

print("\n2c. ineligible row with no reason is a contract violation")
root = fresh("t2c")
F.eligibility(root, n_eligible=8, reason_missing=True)
code, out = orch(root)
check("2c reason required", "ineligible with no reason recorded" in out, out[-500:])

print("\n2d. eligibility scored after formation date")
root = fresh("t2d")
F.eligibility(root, late_asof=True)
code, out = orch(root)
check("2d evidence vintage checked", "after its formation date" in out, out[-500:])

print("\n2e. a company scored but not in the universe")
root = fresh("t2e")
F.eligibility(root, stranger=True)
code, out = orch(root)
check("2e stranger refused", "not in the universe" in out, out[-500:])

# ---------------------------------------------------------------- 3
print("\n3. truncated coverage scan does not pass a gate")
root = fresh("t3")
F.eligibility(root)
F.coverage(root, truncated=True)
code, out = orch(root)
check("3a truncated refused", "truncated corpus scan" in out, out[-500:])

print("\n3b. coverage below 8 usable")
root = fresh("t3b")
F.eligibility(root)
F.coverage(root, n_usable=5)
code, out = orch(root)
check("3b coverage gate", "5 of 9 eligible companies have usable coverage" in out,
      out[-500:])

# ---------------------------------------------------------------- 4
print("\n4. gates met, next script not built -> loud, exit 3")
root = fresh("t4")
F.eligibility(root)
F.coverage(root)
code, out = orch(root)
check("4a exit 3", code == 3, "exit %d" % code)
check("4b names the stage and script",
      "stage B2" in out and "extract_reviews.py does not exist" in out, out[-600:])
check("4c not silently skipped", "not built yet" in out)

# ---------------------------------------------------------------- 5
print("\n5. calibration gate: kappa below 0.60 blocks the pipeline")
root = fresh("t5")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root)
F.calibration(root, kappa=0.48)
code, out = orch(root)
check("5a kappa refused", "kappa 0.480 < 0.60" in out, out[-600:])
check("5b stops before B4", "requires gate B3.calibration" in out, out[-600:])

print("\n5c. adjacent agreement below 0.85 also blocks")
root = fresh("t5c")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root)
F.calibration(root, kappa=0.72, adjacent=0.70)
code, out = orch(root)
check("5c adjacent refused", "adjacent agreement 0.700 < 0.85" in out, out[-600:])

print("\n5d. calibration on fewer than 100 reviews")
root = fresh("t5d")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root)
F.calibration(root, n=40)
code, out = orch(root)
check("5d n floor", "at least 100" in out, out[-600:])

# ---------------------------------------------------------------- 6
print("\n6. development runs with --window dev and never reads held-out")
root = fresh("t6")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root); F.calibration(root)
code, out = orch(root)
check("6a dev stages ran with window=dev", "window=dev" in out, out[-900:])
check("6b stops at the freeze", "stage B1" in out and "human step" in out,
      out[-900:])
check("6c report says held-out not read",
      "not read, not plotted and not summarised" in out, out[-900:])
qp = os.path.join(root, "data/HELE/quarters.csv")
if os.path.exists(qp):
    qs = open(qp).read()
    check("6d no held-out quarter on disk",
          "FY2021Q3" not in qs and "FY2022" not in qs and "FY2023" not in qs,
          "held-out quarters present in dev aggregate")
else:
    check("6d quarters written", False, "no quarters.csv")

# ---------------------------------------------------------------- 7
print("\n7. a stage that writes held-out data pre-freeze is caught as contamination")
root = fresh("t7")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root); F.calibration(root)
F.quarters_stub(root)
rows_before = nrows(root)
# break the stub so it ignores --window
p = os.path.join(root, F.SCRIPTS, "aggregate.py")
src = open(p).read().replace('if a.window == "dev" and (y, q) > dev_end:',
                             'if False:')
open(p, "w").write(src)
code, out = orch(root)
check("7a contamination detected", "HELD-OUT CONTAMINATION" in out, out[-900:])
check("7b exit 2", code == 2, "exit %d" % code)
check("7c nothing written", nrows(root) == rows_before,
      "%d rows before, %d after" % (rows_before, nrows(root)))

# ---------------------------------------------------------------- 8
print("\n8. prereg freeze checks")
root = fresh("t8")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root); F.calibration(root)
F.prereg(root, placeholder=True)
code, out = orch(root)
check("8a placeholders refused", "unfilled template placeholders" in out,
      out[-700:])

root = fresh("t8b")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root); F.calibration(root)
F.prereg(root, commit=False)
code, out = orch(root)
check("8b uncommitted prereg refused", "is not committed" in out, out[-700:])

root = fresh("t8c")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root); F.calibration(root)
F.prereg(root, bad_hash=True)
code, out = orch(root)
check("8c hash mismatch refused", "does not match its recorded hash" in out,
      out[-700:])

root = fresh("t8d")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root)
F.calibration(root, kappa=0.55)       # prereg claims 0.712
F.prereg(root)
code, out = orch(root)
check("8d prereg/calibration kappa disagreement caught",
      "the prereg records kappa" in out, out[-700:])

root = fresh("t8e")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root)
F.calibration(root, model="claude-some-other-model")
F.prereg(root)
code, out = orch(root)
check("8e scorer drift caught", "calibration must be re-run" in out, out[-700:])

# ---------------------------------------------------------------- 9
print("\n9. full run to a held-out SIGNAL verdict")
root = fresh("t9")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root); F.calibration(root)
F.prereg(root)
F.backtest(root, verdict="signal")
F.stub(root, "fetch_sec.py", "fundamentals.csv",
       "fiscal_quarter,period_end,filed_date,revenue,xbrl_tag,accession,restated")
# fetch_sec is called positionally; make the stub accept and record that
open(os.path.join(root, F.SCRIPTS, "fetch_sec.py"), "w").write(
    '''#!/usr/bin/env python3
import os, sys
tic, cik = sys.argv[1], sys.argv[2]
assert len(cik) == 10 and cik.isdigit(), "expected a 10-digit CIK, got %r" % cik
os.makedirs(os.path.join("data", tic), exist_ok=True)
open(os.path.join("data", tic, "fundamentals.csv"), "w").write(
    "fiscal_quarter,period_end,filed_date,revenue,xbrl_tag,accession,restated\\n")
open("fetch_sec_calls.log", "a").write("%s %s\\n" % (tic, cik))
print("STUB fetch_sec.py %s %s" % (tic, cik))
''')
code, out = orch(root)
check("9a exit 0", code == 0, "exit %d\n%s" % (code, out[-1500:]))
check("9b window full after freeze", "window=full" in out, out[-1500:])
check("9c fetch_sec got ticker+cik",
      os.path.exists(os.path.join(root, "fetch_sec_calls.log")), out[-900:])
rows = [json.loads(l) for l in open(os.path.join(root, "results/results.jsonl"))]
last = rows[-1]
check("9d verdict signal", last["verdict"] == "signal", str(last["verdict"]))
check("9e heldout_evaluated true", last["heldout_evaluated"] is True)
required = ("run_id run_at phase prereg_hash universe_hash rubric_version "
            "scorer_model_id companies window n_quarters n_per_quarter "
            "point_estimate ci_low ci_high baseline_value base_rate "
            "null_run_result specifications_tested verdict").split()
check("9f every required key present and non-null",
      all(last.get(k) is not None for k in required),
      str([k for k in required if last.get(k) is None]))
check("9g base rate next to hit rate",
      "base rate of 0.61" in out and "0.68" in out, out[-1500:])
check("9h specifications_tested is a count",
      last["specifications_tested"] == 3, str(last["specifications_tested"]))
check("9i prereg hash is the committed one", len(last["prereg_hash"]) == 64)
check("9j no measurement temp files left",
      not any(f.startswith(".measurement") for f in
              os.listdir(os.path.join(root, "results"))))
check("9k C gate now met for next run", "C.degradation" in out)

# ---------------------------------------------------------------- 10
print("\n10. held-out FAILURE -> MISSES.md, then permanent refusal")
root = fresh("t10")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root); F.calibration(root)
F.prereg(root)
F.backtest(root, verdict="no_signal")
open(os.path.join(root, F.SCRIPTS, "fetch_sec.py"), "w").write(
    '''#!/usr/bin/env python3
import os, sys
tic = sys.argv[1]
os.makedirs(os.path.join("data", tic), exist_ok=True)
open(os.path.join("data", tic, "fundamentals.csv"), "w").write("fiscal_quarter\\n")
''')
code, out = orch(root)
check("10a verdict no_signal", '"no_signal"' in open(
    os.path.join(root, "results/results.jsonl")).read(), out[-800:])
misses = open(os.path.join(root, "MISSES.md")).read()
check("10b MISSES.md entry appended", "**Prereg hash:**" in misses, misses[-400:])
for field in ("**Predicted:**", "**Actual:**", "**Prereg hash:**",
              "**Why it missed", "**Failure mode suspected:**"):
    check("10c format: %s" % field, field in misses)
check("10d hypothesis declared closed", "hypothesis is now closed" in out,
      out[-800:])

# the critical one: run it again
subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
subprocess.run(["git", "commit", "-qm", "record miss"], cwd=root,
               capture_output=True)
code2, out2 = orch(root)
check("10e second run REFUSES the backtest",
      "already failed on held-out data" in out2, out2[-900:])
check("10f refusal names the no-retry rule",
      "not re-specified and re-run against the same held-out" in out2,
      out2[-900:])
rows = [json.loads(l) for l in open(os.path.join(root, "results/results.jsonl"))]
check("10g no second held-out evaluation",
      sum(1 for r in rows if r["heldout_evaluated"]) == 1,
      "%d held-out rows" % sum(1 for r in rows if r["heldout_evaluated"]))
check("10h phase C still unreachable",
      "no phase-C result: Phase D is unreachable" in out2, out2[-900:])

# ---------------------------------------------------------------- 11
print("\n11. append-only enforcement")
root = fresh("t11")
code, out = orch(root)
subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
subprocess.run(["git", "commit", "-qm", "r1"], cwd=root, capture_output=True)
# truncate results.jsonl
open(os.path.join(root, "results/results.jsonl"), "w").write("")
code, out = orch(root)
check("11a truncation detected", "append-only" in out and "INTEGRITY" in out,
      out[-700:])
check("11b exit 2", code == 2, "exit %d" % code)

root = fresh("t11c")
code, out = orch(root)
subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
subprocess.run(["git", "commit", "-qm", "r1"], cwd=root, capture_output=True)
m = os.path.join(root, "MISSES.md")
txt = open(m).read().replace("Append-only.", "Edited!")
open(m, "w").write(txt)
code, out = orch(root)
check("11c MISSES.md rewrite detected", "MISSES.md is not an extension" in out,
      out[-700:])

# ---------------------------------------------------------------- 12
print("\n12. row validation refuses an incomplete result")
root = fresh("t12")
sys.path.insert(0, os.path.join(root, F.SCRIPTS))
import importlib
rr = importlib.import_module("results_row")
good = {k: 1 for k in rr.REQUIRED}
good.update({"verdict": "signal", "companies": ["A"], "heldout_evaluated": True,
             "specifications_tested": 3})
check("12a complete row validates", rr.validate(good) == [], str(rr.validate(good)))
for drop in ("base_rate", "null_run_result", "prereg_hash", "ci_low",
             "n_per_quarter", "universe_hash", "rubric_version",
             "scorer_model_id", "state_fingerprint"):
    bad = dict(good)
    del bad[drop]
    check("12b missing %s rejected" % drop, rr.validate(bad) != [])
bad = dict(good); bad["base_rate"] = None
check("12c null base rate on a signal rejected", rr.validate(bad) != [])
bad = dict(good); bad["verdict"] = "buy"
check("12d bad verdict rejected", rr.validate(bad) != [])
bad = dict(good); bad["specifications_tested"] = ["the one that worked"]
check("12e spec list instead of count rejected", rr.validate(bad) != [])
bad = dict(good); bad["verdict"] = "insufficient_data"
check("12f insufficient_data needs a reason", rr.validate(bad) != [])
bad = dict(good); bad["heldout_evaluated"] = True; bad["prereg_hash"] = ""
check("12g heldout with empty prereg hash rejected", rr.validate(bad) != [])

# ---------------------------------------------------------------- 13
print("\n13. a company may not enter by being named on the command line")
root = fresh("t13")
F.eligibility(root); F.coverage(root)
code, out = orch(root, "--ticker", "NIKE")
check("13a hand-picked ticker refused", "may not enter the test by being named"
      in out, out[-600:])
check("13b exit 2", code == 2, "exit %d" % code)
code, out = orch(root, "--ticker", "HELE")
rows = [json.loads(l) for l in open(os.path.join(root, "results/results.jsonl"))]
check("13c a covered ticker scopes the run", rows[-1]["companies"] == ["HELE"],
      str(rows[-1]["companies"]) + " exit %d" % code)

# ---------------------------------------------------------------- 14
print("\n14. the universe is never re-screened on a schedule")
root = fresh("t14")
code, out = orch(root, "--dry-run")
check("14z rolling panel preferred over legacy screens",
      "data/universe_panel.csv (rolling panel)" in out
      and "universe_2016" not in out.split("## Companies")[0], out[:1200])
for f in ("universe_panel.csv", "universe_panel.hash", "universe_2016.csv",
          "universe_2016.hash", "universe_2019.csv", "universe_2019.hash"):
    os.remove(os.path.join(root, "data", f))
code, out = orch(root)
check("14a missing universe reported, not rebuilt",
      "no universe file" in out and "--reform-universe" not in out.split("STOP")[0],
      out[-700:])
root = fresh("t14b")
# tamper with the universe after it was hashed
p = os.path.join(root, "data/universe_panel.csv")
open(p, "a").write("FAKE,0000000001,FAKE CO,3420,NYSE,2016-01-01,1,2015-01-01,1.0,1.0,True,active\n")
code, out = orch(root)
check("14b edited universe detected",
      "does not match its recorded hash" in out, out[-700:])

# ---------------------------------------------------------------- 15
print("\n15. dry run writes nothing")
root = fresh("t15")
F.eligibility(root); F.coverage(root); F.all_b_stubs(root); F.calibration(root)
CL = os.path.join(root, "REVIEW-TONE-CHANGELOG.md")
before_cl = open(CL).read()
rows_before = nrows(root)
code, out = orch(root, "--dry-run")
check("15a nothing appended", nrows(root) == rows_before,
      "%d -> %d" % (rows_before, nrows(root)))
check("15b changelog untouched", open(CL).read() == before_cl)
check("15c says it would run", "would run:" in out, out[-600:])
check("15d validates the row it would write", "result row would VALIDATE" in out,
      out[-400:])

# ---------------------------------------------------------------- summary
print("\n" + "=" * 70)
bad = [r for r in RESULTS if not r[1]]
print("%d checks, %d passed, %d FAILED" % (len(RESULTS), len(RESULTS) - len(bad),
                                           len(bad)))
for n, _, d in bad:
    print("  FAILED: %s" % n)
sys.exit(1 if bad else 0)
