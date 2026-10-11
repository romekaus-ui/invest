#!/usr/bin/env python3
"""Generate a LABELLED SYNTHETIC fixture shaped like Phase B5 output.

This exists so the dashboard template can be exercised before `aggregate.py`
and `backtest.py` are written. It is a test harness for the RENDERER, not a
stand-in for the pipeline.

READ THIS BEFORE USING THE OUTPUT FOR ANYTHING:

  Every number this script writes is invented. The tone means are drawn from a
  seeded generator, the intervals are arithmetic around them, and the hit rate
  is a constant chosen to sit BELOW its base rate so the F7 presentation can be
  checked. None of it describes any company. A fixture-built dashboard carries
  a permanent banner saying so, and `build_dashboard.py` only applies that
  banner when told `--data-source synthetic_fixture`, so never run a fixture
  through the pipeline path.

  This script computing tone figures is not a breach of CORE-PROMPT s1.3. That
  rule governs numbers that reach a RESULT. These reach a test fixture, and are
  marked unusable at every layer that touches them.

Scenarios:
  below-floors   8 quarters, 6-28 reviews each - the Logitech M510 pilot's
                 shape. Under both floors. The dashboard must REFUSE to chart.
  mixed          16 quarters, several under the review floor - the line must
                 break at those quarters rather than running through them.
  above-floors   16 clean quarters plus a results row whose hit rate is below
                 its base rate - the dashboard must read that as a failure.

Outputs: {outdir}/{TICKER}/quarters.csv, fundamentals.csv, manifest.json
         {outdir}/../results/results.jsonl  (above-floors only)

Standard library only.
"""

import argparse
import csv
import json
import os
import random

FIXTURE_BANNER = "SYNTHETIC FIXTURE - invented numbers, describes no company"


def quarters_sequence(start_fy, start_q, count):
    out = []
    fy, q = start_fy, start_q
    for _ in range(count):
        out.append("FY%dQ%d" % (fy, q))
        q += 1
        if q > 4:
            q = 1
            fy += 1
    return out


def make_rows(scenario, rng):
    """Return (labels, n_reviews, tone_mean or None) per quarter."""
    if scenario == "below-floors":
        # The pilot's actual quarterly counts, which is the point: this is the
        # shape of data that looked chartable and was not.
        counts = [18, 28, 15, 11, 11, 4, 1, 6]
        labels = quarters_sequence(2025, 3, len(counts))
    elif scenario == "mixed":
        counts = [64, 71, 58, 12, 83, 90, 77, 9, 68, 74, 81, 26, 95, 88, 102, 79]
        labels = quarters_sequence(2021, 1, len(counts))
    else:  # above-floors
        counts = [58, 64, 71, 69, 83, 90, 77, 85, 68, 74, 81, 92, 95, 88, 102, 79]
        labels = quarters_sequence(2021, 1, len(counts))

    tones = [round(rng.uniform(-0.35, 0.95), 3) for _ in counts]
    return labels, counts, tones


def write_quarters(path, labels, counts, tones, min_reviews, rng):
    rows = []
    prior = []
    for label, n, tone in zip(labels, counts, tones):
        sufficient = n >= min_reviews
        # A fixture-wide invariant mirroring the real contract: below the floor
        # the aggregator emits no tone figure at all. The dashboard must cope
        # with absence, not with a small number.
        if sufficient:
            # Interval width shrinks with n, the way a bootstrap interval would.
            half = round(1.6 / (n ** 0.5), 3)
            ci_low, ci_high = round(tone - half, 3), round(tone + half, 3)
            within = round(tone - rng.uniform(-0.05, 0.05), 3)
            mix = round(rng.uniform(-0.08, 0.08), 3)
            resid = round(rng.uniform(-0.03, 0.03), 3)
        else:
            tone = ci_low = ci_high = within = mix = resid = None

        base = None
        if len(prior) >= 4 and all(p is not None for p in prior[-4:]):
            base = round(sum(prior[-4:]) / 4, 3)
        sig = round(within - base, 3) if (base is not None and within is not None) else None

        rows.append({
            "fiscal_quarter": label,
            "n_reviews": n,
            "n_excluded_seller": max(0, int(n * rng.uniform(0.03, 0.09))),
            "n_scoring_failed": max(0, int(n * rng.uniform(0.0, 0.03))),
            "verified_share": round(rng.uniform(0.78, 0.96), 3),
            "tone_mean": "" if tone is None else tone,
            "tone_ci_low": "" if ci_low is None else ci_low,
            "tone_ci_high": "" if ci_high is None else ci_high,
            "tone_within": "" if within is None else within,
            "tone_mix": "" if mix is None else mix,
            "tone_residual": "" if resid is None else resid,
            "baseline_prior4": "" if base is None else base,
            "signal": "" if sig is None else sig,
            "defect_rate": round(rng.uniform(0.02, 0.18), 3),
            "price_complaint_rate": round(rng.uniform(0.01, 0.12), 3),
            "sufficient": "true" if sufficient else "false",
        })
        prior.append(within)

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return rows


def write_fundamentals(path, labels, rng):
    rows = []
    rev = 420_000_000
    for label in labels:
        rev = int(rev * rng.uniform(0.96, 1.07))
        year = int(label[2:6])
        q = int(label[-1])
        end_month = (q * 3)
        rows.append({
            "fiscal_quarter": label,
            "period_end": "%d-%02d-28" % (year, end_month),
            # filed weeks after period end - the gap that makes F2 possible
            "filed_date": "%d-%02d-%02d" % (year + (1 if end_month > 10 else 0),
                                            (end_month % 12) + 1, 14),
            "revenue": rev,
            "xbrl_tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "accession": "0000000000-00-%06d" % rng.randint(1, 999999),
            "restated": "false",
        })
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticker", default="FIXT")
    p.add_argument("--scenario", default="below-floors",
                   choices=["below-floors", "mixed", "above-floors"])
    p.add_argument("--outdir", default="data")
    p.add_argument("--results", default="results/results.jsonl")
    p.add_argument("--min-reviews", type=int, default=30)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    rng = random.Random(args.seed)
    base = os.path.join(args.outdir, args.ticker.upper())
    os.makedirs(base, exist_ok=True)

    labels, counts, tones = make_rows(args.scenario, rng)
    write_quarters(os.path.join(base, "quarters.csv"), labels, counts, tones,
                   args.min_reviews, rng)
    write_fundamentals(os.path.join(base, "fundamentals.csv"), labels, rng)

    with open(os.path.join(base, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({
            "ticker": args.ticker.upper(),
            "company_name": "%s (%s)" % (args.ticker.upper(), FIXTURE_BANNER),
            "SYNTHETIC": True,
            "fixture_scenario": args.scenario,
            "fiscal_calendar": {"fy_end_month": 12, "verified_from": "SYNTHETIC - no filing"},
            "corpus": {"dataset": "SYNTHETIC - no corpus was read"},
            "rubric_version": "v1",
            "scorer_model_id": "SYNTHETIC - no model scored these",
            "universe_hash": "SYNTHETIC",
            "prereg_hash": "SYNTHETIC" if args.scenario == "above-floors" else None,
            "coverage_ratio": 0.34 if args.scenario == "above-floors" else None,
            "coverage_method": "SYNTHETIC - invented",
        }, f, indent=2)

    if args.scenario == "above-floors":
        os.makedirs(os.path.dirname(args.results) or ".", exist_ok=True)
        # Hit rate BELOW base rate on purpose. The dashboard has to read this
        # as a failure (F7); a renderer that shows 0.58 as a win is broken.
        row = {
            "run_id": "FIXTURE-0001",
            "run_at": "2026-10-11T00:00:00Z",
            "phase": "B7 (SYNTHETIC FIXTURE)",
            "prereg_hash": "SYNTHETIC",
            "universe_hash": "SYNTHETIC",
            "rubric_version": "v1",
            "scorer_model_id": "SYNTHETIC - no model scored these",
            "companies": [args.ticker.upper()],
            "window": "FY2021Q1-FY2024Q4",
            "n_quarters": 16,
            "point_estimate": 0.58,
            "ci_low": 0.41,
            "ci_high": 0.74,
            "baseline_value": 0.65,
            "base_rate": 0.65,
            "null_run_result": "shuffled-date null scored 0.56; real run does not separate",
            "specifications_tested": 24,
            "verdict": "no_signal",
            "SYNTHETIC": True,
        }
        with open(args.results, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    print("%s fixture: %s, %d quarters, counts %d-%d"
          % (FIXTURE_BANNER, args.scenario, len(counts), min(counts), max(counts)))
    print("wrote %s" % base)


if __name__ == "__main__":
    main()
