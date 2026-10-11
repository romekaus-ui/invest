#!/usr/bin/env python3
"""Render a review-tone dashboard payload from pipeline CSVs.

This script is the honesty gate. The template downstream can only draw what
this script puts in the payload, so every rule that must survive time pressure
is enforced HERE, in code, rather than in a reviewer's memory.

WHAT THIS SCRIPT MAY COMPUTE (and nothing else):
  - sums and counts of INTEGER RECORD COUNTS (n_reviews, n_excluded_seller,
    n_scoring_failed) for the record tiles
  - comparisons of those counts against the floors
  - min/max of already-computed values, to size a chart axis

WHAT IT MAY NEVER COMPUTE:
  tone means, tone deltas, baselines, confidence intervals, hit rates, base
  rates, p-values, null-run results. CORE-PROMPT s1.3 puts every result number
  in a script's output and this is not that script - `aggregate.py` and
  `backtest.py` are. Here those fields are READ, validated, and passed through
  untouched. If a field is missing, the dashboard renders a gap and says so.
  It never fills one in.

The floors (CORE-PROMPT s5) are applied as RENDER GATES, not as captions:

  - a quarter under MIN_REVIEWS_PER_QUARTER is marked `insufficient_history`
    and is NOT a point on the tone line (F5)
  - a tone mean arriving without a bootstrap interval is NOT renderable at all,
    interval-less point estimates being an incomplete result
  - a company under MIN_QUARTERS gets `render_trend: false`, and the template
    draws a refusal panel where the trend chart would be. The dashboard
    declines to draw the line rather than drawing it with a warning under it.

A verdict is never upgraded here. `results.jsonl` may supply `signal` or
`no_signal`, but only when the floors already passed; otherwise the verdict is
`insufficient_data` regardless of what any results row claims.

Inputs : data/{TICKER}/quarters.csv      (required)
         data/{TICKER}/fundamentals.csv  (optional)
         data/{TICKER}/manifest.json     (optional, provenance)
         results/results.jsonl           (optional, verdict + accuracy)
Outputs: the rendered standalone HTML, and the payload JSON beside it

Standard library only.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

# CORE-PROMPT s5 defaults. Overridable on the command line because the prereg
# fixes them per run, but NEVER silently: whatever is used is written into the
# payload and printed on the dashboard face.
MIN_REVIEWS_PER_QUARTER = 30
MIN_QUARTERS = 12

# data-contracts.md : data/{TICKER}/quarters.csv
QUARTER_COLUMNS = [
    "fiscal_quarter", "n_reviews", "n_excluded_seller", "n_scoring_failed",
    "verified_share", "tone_mean", "tone_ci_low", "tone_ci_high",
    "tone_within", "tone_mix", "tone_residual", "baseline_prior4", "signal",
    "defect_rate", "price_complaint_rate", "sufficient",
]

VERDICTS = ("signal", "no_signal", "insufficient_data")


# -- parsing -------------------------------------------------------------------

def num(v):
    """float or None. An empty cell is a genuine absence, never a zero.

    Coercing a missing tone mean to 0.0 would place a fabricated point on the
    line at the exact spot where the pipeline had nothing to say.
    """
    if v is None:
        return None
    v = v.strip()
    if v == "" or v.upper() in ("NA", "NAN", "NULL", "NONE"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def integer(v):
    f = num(v)
    return None if f is None else int(round(f))


def boolean(v):
    if v is None:
        return False
    return v.strip().lower() in ("1", "true", "t", "yes", "y")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# -- loading -------------------------------------------------------------------

def load_quarters(path, min_reviews, warnings):
    """Read quarters.csv and decide, per quarter, what may be drawn.

    Three outcomes per quarter, and the template honours them literally:
      renderable        -> a point on the line, with its interval band
      insufficient      -> a hollow low-confidence marker, the line BREAKS here
      no_estimate       -> a gap; the pipeline produced no usable tone figure
    """
    rows = read_csv(path)
    if not rows:
        sys.exit("%s has no rows" % path)

    missing = [c for c in QUARTER_COLUMNS if c not in rows[0]]
    if missing:
        # Contract violation stops the pipeline rather than passing a malformed
        # frame downstream (data-contracts.md).
        sys.exit("%s violates the quarters.csv contract - missing columns: %s"
                 % (path, ", ".join(missing)))

    out = []
    for r in rows:
        n_reviews = integer(r.get("n_reviews")) or 0
        tone_mean = num(r.get("tone_mean"))
        ci_low = num(r.get("tone_ci_low"))
        ci_high = num(r.get("tone_ci_high"))

        # The floor is re-derived from n_reviews rather than trusted from the
        # `sufficient` column alone. If the two disagree the run is wrong
        # somewhere upstream and the disagreement is surfaced, not smoothed.
        meets_floor = n_reviews >= min_reviews
        claimed = boolean(r.get("sufficient"))
        if claimed != meets_floor:
            warnings.append(
                "%s: quarters.csv says sufficient=%s but n_reviews=%d against a "
                "floor of %d. The floor was applied; the column was not trusted."
                % (r.get("fiscal_quarter"), claimed, n_reviews, min_reviews))

        has_interval = ci_low is not None and ci_high is not None
        if tone_mean is not None and not has_interval:
            # CORE-PROMPT s5: a point estimate without an interval is an
            # incomplete result. It does not get rendered.
            warnings.append(
                "%s: tone_mean present with no bootstrap interval. Not rendered - "
                "a point estimate without an interval is an incomplete result."
                % r.get("fiscal_quarter"))

        if tone_mean is None:
            state = "no_estimate"
        elif not meets_floor:
            state = "insufficient"
        elif not has_interval:
            state = "no_estimate"
        else:
            state = "renderable"

        n_failed = integer(r.get("n_scoring_failed")) or 0
        # F13: a quarter losing >10% of its reviews to scoring failure is
        # flagged, not quietly averaged.
        denom = n_reviews + n_failed
        failure_flag = bool(denom and (n_failed / denom) > 0.10)
        if failure_flag:
            warnings.append(
                "%s: %d of %d scored reviews failed (>10%%). F13 - flagged, not averaged."
                % (r.get("fiscal_quarter"), n_failed, denom))

        out.append({
            "fiscal_quarter": (r.get("fiscal_quarter") or "").strip(),
            "n_reviews": n_reviews,
            "n_excluded_seller": integer(r.get("n_excluded_seller")) or 0,
            "n_scoring_failed": n_failed,
            "scoring_failure_flag": failure_flag,
            "verified_share": num(r.get("verified_share")),
            "tone_mean": tone_mean,
            "tone_ci_low": ci_low,
            "tone_ci_high": ci_high,
            "tone_within": num(r.get("tone_within")),
            "tone_mix": num(r.get("tone_mix")),
            "tone_residual": num(r.get("tone_residual")),
            "baseline_prior4": num(r.get("baseline_prior4")),
            "signal": num(r.get("signal")),
            "defect_rate": num(r.get("defect_rate")),
            "price_complaint_rate": num(r.get("price_complaint_rate")),
            "meets_floor": meets_floor,
            "state": state,
        })
    return out


def load_fundamentals(path, warnings):
    """Point-in-time revenue. `filed_date` is the join key, never `period_end` (F2)."""
    if not os.path.exists(path):
        return []
    rows = read_csv(path)
    out = []
    for r in rows:
        if not r.get("filed_date", "").strip():
            warnings.append("fundamentals.csv row %s has no filed_date. F2 - a "
                            "revenue figure with no filing date cannot be joined "
                            "point-in-time." % r.get("fiscal_quarter"))
        out.append({
            "fiscal_quarter": (r.get("fiscal_quarter") or "").strip(),
            "period_end": (r.get("period_end") or "").strip(),
            "filed_date": (r.get("filed_date") or "").strip(),
            "revenue": num(r.get("revenue")),
            "xbrl_tag": (r.get("xbrl_tag") or "").strip(),
            "restated": boolean(r.get("restated")),
        })
    return out


def load_result_row(path, ticker):
    """Most recent results.jsonl row naming this ticker, or None.

    Supplies the verdict and the hit-rate/base-rate pair. Nothing here is
    recomputed; an absent field stays absent.
    """
    if not os.path.exists(path):
        return None
    found = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            companies = row.get("companies") or []
            if isinstance(companies, str):
                companies = [companies]
            if ticker in companies:
                found = row  # append-only file; last match is the newest run
    return found


# -- assembly ------------------------------------------------------------------

def build_payload(ticker, args, warnings):
    base = os.path.join(args.data_dir, ticker)
    qpath = os.path.join(base, "quarters.csv")
    if not os.path.exists(qpath):
        sys.exit(
            "no %s\n\n"
            "Phase B5 (aggregate.py) has not produced quarterly tone for %s.\n"
            "This dashboard renders pipeline output; it does not stand in for it.\n"
            "To see the template against the contract, build a labelled fixture:\n"
            "  python3 scripts/make_fixture.py --scenario below-floors --outdir <dir>\n"
            % (qpath, ticker))

    quarters = load_quarters(qpath, args.min_reviews, warnings)
    fundamentals = load_fundamentals(os.path.join(base, "fundamentals.csv"), warnings)

    manifest = {}
    mpath = os.path.join(base, "manifest.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            try:
                manifest = json.load(f)
            except json.JSONDecodeError:
                warnings.append("manifest.json is not valid JSON; provenance unknown.")

    result_row = load_result_row(args.results, ticker)

    # -- record tiles. Integer counts only. ------------------------------------
    n_product = sum(q["n_reviews"] for q in quarters)
    n_seller = sum(q["n_excluded_seller"] for q in quarters)
    n_failed = sum(q["n_scoring_failed"] for q in quarters)
    n_all = n_product + n_seller + n_failed

    # -- floor gates -----------------------------------------------------------
    renderable = [q for q in quarters if q["state"] == "renderable"]
    n_usable = len(renderable)
    meets_quarter_floor = n_usable >= args.min_quarters
    render_trend = meets_quarter_floor and n_usable >= 2

    # -- verdict. Downgrade-only. ---------------------------------------------
    if not meets_quarter_floor:
        verdict = "insufficient_data"
        verdict_reason = (
            "%d usable quarters against a floor of %d. Below the floor this "
            "company is dropped from the test (CORE-PROMPT s5); no trend is drawn."
            % (n_usable, args.min_quarters))
    elif result_row and result_row.get("verdict") in VERDICTS:
        verdict = result_row["verdict"]
        verdict_reason = (
            "From results.jsonl run %s, phase %s."
            % (result_row.get("run_id", "?"), result_row.get("phase", "?")))
        if verdict == "insufficient_data":
            verdict_reason += " The backtest itself returned insufficient_data."
    else:
        verdict = "insufficient_data"
        verdict_reason = (
            "The quarterly floors pass, but no backtest row for %s exists in %s. "
            "Tone has been measured; whether it predicts anything has not been "
            "tested, so there is no signal to claim." % (ticker, args.results))

    # -- accuracy. Only ever shown as a PAIR (F7). ----------------------------
    accuracy = None
    if result_row:
        hit = result_row.get("point_estimate")
        base_rate = result_row.get("base_rate")
        if hit is not None and base_rate is None:
            # F7 is the one failure mode this dashboard could most easily
            # launder into something authoritative. A lone hit rate is dropped.
            warnings.append(
                "results.jsonl supplies a hit rate with no base_rate. Not rendered - "
                "a hit rate without its base rate is F7 and reads as a result when "
                "it is not one.")
        elif hit is not None and base_rate is not None:
            accuracy = {
                "hit_rate": hit,
                "base_rate": base_rate,
                "ci_low": result_row.get("ci_low"),
                "ci_high": result_row.get("ci_high"),
                "null_run_result": result_row.get("null_run_result"),
                "specifications_tested": result_row.get("specifications_tested"),
                "n_quarters": result_row.get("n_quarters"),
                "window": result_row.get("window"),
            }

    provenance = {
        "run_id": (result_row or {}).get("run_id"),
        "run_at": (result_row or {}).get("run_at"),
        "phase": (result_row or {}).get("phase"),
        "prereg_hash": (result_row or {}).get("prereg_hash") or manifest.get("prereg_hash"),
        "universe_hash": (result_row or {}).get("universe_hash") or manifest.get("universe_hash"),
        "rubric_version": (result_row or {}).get("rubric_version") or manifest.get("rubric_version"),
        "scorer_model_id": (result_row or {}).get("scorer_model_id") or manifest.get("scorer_model_id"),
        "coverage_ratio": manifest.get("coverage_ratio"),
        "coverage_method": manifest.get("coverage_method"),
        "corpus": (manifest.get("corpus") or {}).get("dataset"),
        "fy_end_month": (manifest.get("fiscal_calendar") or {}).get("fy_end_month"),
    }

    if provenance["prereg_hash"] is None:
        warnings.append("No prereg hash found. F6 - without a frozen prereg, any "
                        "reported result is one of an unknown number of specifications.")
    if provenance["coverage_ratio"] is None:
        warnings.append("No coverage ratio recorded. F3 - the share of revenue these "
                        "reviews can see is unquantified, so the series cannot be "
                        "read as a view of the company.")

    return {
        "ticker": ticker,
        "company_name": args.company or manifest.get("company_name") or ticker,
        "formation_year": args.formation_year,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "data_source": args.data_source,
        "fixture_note": args.fixture_note,
        "floors": {
            "min_reviews_per_quarter": args.min_reviews,
            "min_quarters": args.min_quarters,
            "quarters_total": len(quarters),
            "quarters_usable": n_usable,
            "meets_quarter_floor": meets_quarter_floor,
        },
        "render_trend": render_trend,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "records": {
            "all": n_all, "product": n_product,
            "seller_only": n_seller, "scoring_failed": n_failed,
        },
        "quarters": quarters,
        "fundamentals": fundamentals,
        "accuracy": accuracy,
        "provenance": provenance,
        "warnings": warnings,
    }


def render(payload, template_path, out_path):
    with open(template_path, encoding="utf-8") as f:
        html = f.read()
    token = "/*__PAYLOAD__*/null"
    if token not in html:
        sys.exit("template %s has no %s placeholder" % (template_path, token))
    # </script> inside the JSON would close the host script tag early.
    blob = json.dumps(payload, allow_nan=False).replace("</", "<\\/")
    html = html.replace(token, blob)
    html = html.replace("__TICKER__", payload["ticker"])
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ticker")
    p.add_argument("--formation-year", type=int, default=None,
                   help="universe formation year, for the scope line")
    p.add_argument("--company", default=None, help="display name")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--results", default="results/results.jsonl")
    p.add_argument("--template", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--min-reviews", type=int, default=MIN_REVIEWS_PER_QUARTER)
    p.add_argument("--min-quarters", type=int, default=MIN_QUARTERS)
    p.add_argument("--data-source", default="pipeline",
                   choices=["pipeline", "synthetic_fixture"],
                   help="synthetic_fixture puts a permanent banner on the page")
    p.add_argument("--fixture-note", default=None,
                   help="what the fixture is for; shown in the banner")
    args = p.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    template = args.template or os.path.join(here, "..", "templates", "dashboard.html")
    out = args.out or "%s-review-tone.html" % args.ticker.lower()

    warnings = []
    payload = build_payload(args.ticker.upper(), args, warnings)
    render(payload, template, out)

    with open(os.path.splitext(out)[0] + ".payload.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    f = payload["floors"]
    print("%s  verdict=%s  usable=%d/%d quarters (floor %d)  records=%d"
          % (payload["ticker"], payload["verdict"], f["quarters_usable"],
             f["quarters_total"], f["min_quarters"], payload["records"]["all"]))
    print("trend chart: %s" % ("drawn" if payload["render_trend"] else "REFUSED - below floor"))
    for w in warnings:
        print("  warn: %s" % w)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
