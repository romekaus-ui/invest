#!/usr/bin/env python3
"""Synthetic fixtures for exercising the orchestrator's gate paths."""
import hashlib
import json
import os
import subprocess
import sys

TICKERS = ['HELE', 'HBB', 'YETI', 'LOGI', 'CMTL', 'KODK', 'HAS', 'AOS', 'SNA']
SCRIPTS = ".claude/skills/review-tone-signal/scripts"


def sh(root, *a):
    return subprocess.run(a, cwd=root, capture_output=True, text=True)


def eligibility(root, n_eligible=9, reason_missing=False, late_asof=False,
                stranger=False):
    cols = ["ticker"] + ["c%d_x" % i for i in range(1, 8)] + [
        "c1_estimated", "weighted_score", "eligible", "reason", "scored_asof"]
    rows = []
    tics = list(TICKERS)
    if stranger:
        tics = tics + ["NOTINUNIVERSE"]
    for i, t in enumerate(tics):
        ok = i < n_eligible
        rows.append([t] + [3] * 7 + [
            "False", "72.0" if ok else "41.0", str(ok),
            "" if ok else ("" if reason_missing else
                           "criterion 1: Amazon immaterial to revenue"),
            "2015-12-01" if not late_asof else "2017-06-01"])
    p = os.path.join(root, "data", "eligibility.csv")
    with open(p, "w") as f:
        f.write(",".join(cols) + "\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def eligibility_year(root, year, n_eligible=9, partial=False):
    """A per-formation scorecard with the hash line score_eligibility writes.

    A2 writes one of these per formation. `partial` sets the marker that makes
    a file a verification sample rather than a scored formation.
    """
    cols = ["ticker"] + ["c%d_x" % i for i in range(1, 8)] + [
        "c1_estimated", "weighted_score", "eligible", "reason", "scored_asof"]
    p = os.path.join(root, "data", "eligibility_%d.csv" % year)
    with open(p, "w") as f:
        f.write(",".join(cols) + "\n")
        for i, t in enumerate(TICKERS):
            ok = i < n_eligible
            f.write(",".join([t] + ["3"] * 7 + [
                "False", "72.0" if ok else "41.0", str(ok),
                "" if ok else "criterion 1: Amazon immaterial to revenue",
                "2015-12-01"]) + "\n")
    with open(p, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(p.replace(".csv", ".hash"), "w") as f:
        f.write("%s  eligibility_%d.csv  formation=%d-01-01  scored=%d/%d  "
                "universe=universe_panel.csv  partial=%s\n"
                % (digest, year, year, len(TICKERS), len(TICKERS), partial))


def drop_shortlists(root):
    """Remove every A1b artefact, so the pre-filter reads as not yet run."""
    d = os.path.join(root, "data")
    for f in sorted(os.listdir(d)):
        if f.startswith("shortlist_") or f.startswith("prefilter_panel"):
            os.remove(os.path.join(d, f))


def corrupt_shortlist(root, year=2016):
    """Append a ticker after the hash was recorded."""
    p = os.path.join(root, "data", "shortlist_%d.csv" % year)
    with open(p) as f:
        ncols = len(f.readline().strip().split(","))
    with open(p, "a") as f:
        f.write(",".join(["999", "SNUCKIN"] + [""] * (ncols - 2)) + "\n")


def coverage(root, n_usable=9, truncated=False):
    cols = ["ticker", "matched_products", "corpus_review_count", "categories",
            "usable", "reason", "truncated_scan", "coverage_ratio"]
    with open(os.path.join(root, "data", "coverage.csv"), "w") as f:
        f.write(",".join(cols) + "\n")
        for i, t in enumerate(TICKERS):
            ok = i < n_usable
            f.write("%s,%d,1000,Home_and_Kitchen,%s,%s,%s,\n"
                    % (t, 40 if ok else 2, ok,
                       "" if ok else "fewer than 5 matched products",
                       truncated))


PREREG = """# Preregistration - orchestrator test

## 1. Hypothesis

Within-ASIN abnormal review tone in fiscal quarter Q is positively associated
with revenue surprise in Q+1, among companies passing the structural screen.

## 2. Universe

- Universe file: `data/universe_2016.csv`
- Universe SHA-256: `abc`
- Formation date: `2016-01-01`
- Eligibility file and SHA-256: `def`
- Companies entering the test (count): `9`
- Companies dropped for coverage (count and list): `0`

## 3. Window

| | |
|---|---|
| Full window | `2016Q1` - `2023Q2` (corpus ends Sep 2023) |
| Development | `2016Q1` - `2021Q2` |
| **Held-out** | `2021Q3` - `2023Q2` (8 quarters) |

## 4. Signal definition

- Tone rubric version: `v1`
- Scorer model ID: `claude-haiku-4-5-20251001`
- Calibration kappa achieved: `0.712` (gate: >= 0.60)
- Signal: `tone_within(Q) - mean(tone_within(Q-1..Q-4))`
- Minimum reviews per quarter: `30`
- Minimum usable quarters per company: `12`

## 5. Endpoints

- **Primary:** revenue surprise at Q+1 versus seasonal random walk
- **Secondary (non-binding):** stock return over 60 days, labelled noisy

## 6. Test statistic and decision rule

- Statistic: Spearman rank correlation, pooled, company fixed effects
- Direction: one-sided
- **Pass threshold, stated now:** held-out Spearman rho >= 0.15, 95% CI
  excluding 0, and hit rate exceeding base rate by >= 5pp
- Base rate to beat: 0.61 on development data

## 7. Specifications

| # | Horizon | Threshold | Weighting | Notes |
|---|---|---|---|---|
| 1 | Q+1 | - | review-count | primary |
| 2 | Q+2 | - | review-count | secondary horizon |
| 3 | Q+1 | - | equal | robustness |

## 8. Null run

Shuffled-date null: timestamps permuted within company, 1000 permutations.
The real result must separate from the null at the 5% level.

## 9. What would falsify this

A held-out Spearman rho below 0.15, or a CI covering zero, or a hit rate at or
below the base rate. Any of those ends the hypothesis.

## 10. Stop conditions

- Calibration kappa below 0.60 -> stop, fix the scorer.
- Fewer than 8 companies with usable coverage -> stop, record the gap.
- Held-out fails the threshold in s6 -> record in `MISSES.md`, verdict
  `no_signal`, and do not re-specify and re-run against the same held-out data.
"""


def prereg(root, frozen=True, placeholder=False, commit=True, bad_hash=False):
    text = PREREG
    if placeholder:
        text = text.replace("`0.712`", "`<value>`")
    name = "prereg/2026-10-11-test.md"
    p = os.path.join(root, name)
    with open(p, "w") as f:
        f.write(text)
    if frozen:
        out = sh(root, "shasum", "-a", "256", name).stdout
        if bad_hash:
            out = "0" * 64 + "  " + name + "\n"
        with open(p[:-3] + ".hash", "w") as f:
            f.write(out)
    if commit:
        sh(root, "git", "add", "-A")
        sh(root, "git", "commit", "-qm", "freeze prereg test")


def calibration(root, kappa=0.712, adjacent=0.91, n=120,
                model="claude-haiku-4-5-20251001"):
    with open(os.path.join(root, "data", "calibration.json"), "w") as f:
        json.dump({"n": n, "exact_agreement": 0.68, "adjacent_agreement": adjacent,
                   "quadratic_weighted_kappa": kappa, "rubric_version": "v1",
                   "scorer_model_id": model, "labelled_by": "user",
                   "scored_at": "2026-10-11T10:00:00Z"}, f, indent=2)


STUB = """#!/usr/bin/env python3
import argparse, os, sys
p = argparse.ArgumentParser()
p.add_argument("--tickers", nargs="*", default=[])
p.add_argument("--window", default="dev")
p.add_argument("--calibrate", action="store_true")
p.add_argument("--prereg", default="")
p.add_argument("--out", default="")
a, _ = p.parse_known_args()
print("STUB %s window=%s tickers=%s" % (os.path.basename(__file__), a.window, a.tickers))
for t in a.tickers:
    os.makedirs(os.path.join("data", t), exist_ok=True)
    open(os.path.join("data", t, "{OUT}"), "w").write("{HEADER}\\n")
"""

QUARTERS_HEADER = ("fiscal_quarter,n_reviews,n_excluded_seller,n_scoring_failed,"
                   "verified_share,tone_mean,tone_ci_low,tone_ci_high,tone_within,"
                   "tone_mix,tone_residual,baseline_prior4,signal,defect_rate,"
                   "price_complaint_rate,sufficient")


def quarters_stub(root, heldout=False, thin=0):
    """aggregate.py stub that writes quarters.csv honouring --window."""
    body = '''#!/usr/bin/env python3
import argparse, os
p = argparse.ArgumentParser()
p.add_argument("--tickers", nargs="*", default=[])
p.add_argument("--window", default="dev")
a, _ = p.parse_known_args()
HEADER = "%s"
rows = []
yq = [(y, q) for y in range(2016, 2024) for q in (1, 2, 3, 4)][:30]
dev_end = (2021, 2)
for (y, q) in yq:
    if a.window == "dev" and (y, q) > dev_end:
        continue
    rows.append("FY%%dQ%%d,120,4,1,0.82,0.31,0.2,0.42,0.3,0.01,0.0,0.25,0.05,0.04,0.02,True" %% (y, q))
for t in a.tickers:
    os.makedirs(os.path.join("data", t), exist_ok=True)
    with open(os.path.join("data", t, "quarters.csv"), "w") as f:
        f.write(HEADER + chr(10))
        f.write(chr(10).join(rows) + chr(10))
print("STUB aggregate.py window=%%s wrote %%d quarters for %%s" %% (a.window, len(rows), a.tickers))
''' % QUARTERS_HEADER
    write_script(root, "aggregate.py", body)


def write_script(root, name, body):
    p = os.path.join(root, SCRIPTS, name)
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)


def stub(root, name, out, header):
    write_script(root, name, STUB.replace("{OUT}", out).replace("{HEADER}", header))


def backtest(root, verdict="signal", heldout=True):
    body = '''#!/usr/bin/env python3
import argparse, json, os
p = argparse.ArgumentParser()
p.add_argument("--prereg", default="")
p.add_argument("--out", required=True)
p.add_argument("--window", default="dev")
a, _ = p.parse_known_args()
m = {
  "verdict": "%s",
  "heldout_evaluated": %s,
  "point_estimate": 0.21, "ci_low": 0.06, "ci_high": 0.35,
  "baseline_value": 0.0, "base_rate": 0.61, "hit_rate": 0.68,
  "null_run_result": "real rho 0.21 outside the 95th pct of 1000 shuffled-date permutations (0.09)",
  "n_quarters": 214, "n_per_quarter": 118, "specifications_tested": 3,
  "window": "2016Q1-2023Q2 (full, held-out included)",
  "endpoint": "revenue surprise at Q+1 vs seasonal random walk",
  "test_statistic": "Spearman rho, pooled, company fixed effects"
}
if m["verdict"] == "no_signal":
    m.update({"point_estimate": 0.03, "ci_low": -0.09, "ci_high": 0.15,
              "hit_rate": 0.58,
              "null_run_result": "real rho 0.03 inside the null distribution (95th pct 0.11)",
              "why": "within-ASIN tone change showed no association with revenue surprise once the seasonal-random-walk baseline was accounted for",
              "failure_mode": "none identified",
              "actual": "held-out Spearman rho 0.03 (CI -0.09..0.15) against a threshold of 0.15; hit rate 0.58 below the 0.61 base rate"})
os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
json.dump(m, open(a.out, "w"), indent=2)
print("STUB backtest.py window=%%s verdict=%%s -> %%s" %% (a.window, m["verdict"], a.out))
''' % (verdict, "True" if heldout else "False")
    write_script(root, "backtest.py", body)


def all_b_stubs(root):
    stub(root, "extract_reviews.py", "reviews.csv",
         "review_id,parent_asin,timestamp,review_date,fiscal_quarter,rating,"
         "title,text,verified_purchase,helpful_vote")
    stub(root, "score_reviews.py", "scores.csv",
         "review_id,tone,seller_or_shipping_only,defect,price_complaint,"
         "scoring_failed,rubric_version,scorer_model_id,scored_at")
    quarters_stub(root)
    stub(root, "fetch_sec.py", "fundamentals.csv",
         "fiscal_quarter,period_end,filed_date,revenue,xbrl_tag,accession,restated")


if __name__ == "__main__":
    print("import me")
