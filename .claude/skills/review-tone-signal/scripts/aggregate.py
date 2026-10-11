#!/usr/bin/env python3
"""Stage B5 - quarterly tone aggregation with the within-ASIN / mix decomposition.

Reads data/{TICKER}/reviews.csv + data/{TICKER}/scores.csv.
Writes data/{TICKER}/quarters.csv (schema fixed by references/data-contracts.md),
data/dropped.csv, and an `aggregate` block in data/{TICKER}/manifest.json.

WHY THE DECOMPOSITION IS THE WHOLE POINT (CORE-PROMPT s7, methodology s3, F4)
----------------------------------------------------------------------------
Naive company tone moves for two unrelated reasons: the same products being
received better or worse, and the product mix drifting toward items that sit at
a different tone level. A maturing portfolio ages grumpier whatever the business
is doing. Only the within-ASIN component is interpretable as news about the
firm, so only the within-ASIN component feeds the signal. The mix term and the
entrant/exit residual are emitted next to it, every quarter, because a quarter
whose change is mostly churn said nothing regardless of its value.

PROVENANCE WARNING - THE DECOMPOSITION FORMULA IS PROVISIONAL
------------------------------------------------------------
The *motivation* above is settled: CORE-PROMPT s7 requires the within/mix split
and F4 is a blocking check. The *formula* is not. It comes from
references/methodology.md s3, which that file's own header describes as "a
faithful reconstruction of the method as described second-hand" - a
reconstruction from a 61-second video, not from the source paper
("More than words: Can tone of consumer product reviews help predict firms'
fundamentals?", IDEAS/RePEc). methodology.md s8 still carries an open row:
"Reconcile all of the above against the paper's actual construction - paper not
yet read in full." Where the paper differs, the paper wins.

The decomposition is therefore the part of this script MOST likely to be wrong,
and it is the part that decides whether a reading means a company declining or a
catalogue aging into grumpier reviews. It is isolated in exactly one function,
`decompose()`, tagged DECOMPOSITION_SPEC, and that tag is written into every
manifest. Swapping the formula after the paper is read is a change to one
function; the tag tells you which outputs were built under the old one.

Nothing else in this script depends on the paper. The column contract, the
review and quarter floors, the bootstrap intervals, verified_share and the
reconciliation assertion stand on CORE-PROMPT s5/s7 and data-contracts.md alone.

WHAT THIS SCRIPT REFUSES TO DO
------------------------------
- Emit a tone number for a quarter below the review floor (CORE-PROMPT s5, F5).
  A pilot on a single Logitech ASIN produced quarters of 1, 4 and 6 reviews. A
  tone line drawn through those is noise rendered as a trend. Such a quarter
  emits sufficient=false and tone_mean=null; it does not emit a number.
- Emit a point estimate without an interval (CORE-PROMPT s5). Every tone mean
  carries a bootstrap 95% CI or it is not emitted at all.
- Bridge a gap in the quarter sequence. If Q-1 is missing or insufficient, Q has
  no decomposition - interpolating one is forward fill by another name
  (CORE-PROMPT s4).
- Keep a company with fewer than MIN_QUARTERS usable quarters. It is dropped and
  recorded with the reason, never carried along thin.

Reproducibility: pure standard library. The bootstrap is seeded, and the seed is
recorded in the manifest and printed in the report. Each quarter draws from its
own deterministic substream keyed by (seed, ticker, fiscal_quarter), so a
quarter's CI does not depend on how many quarters were processed before it or in
what order.

Standard library only.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import random
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import window as windowmod  # noqa: E402

# ---------------------------------------------------------------------------
# Thresholds. Configurable, but these defaults are the prereg's stated values
# (prereg/TEMPLATE.md s4) and CORE-PROMPT s5. Changing them for a real run is a
# prereg change, not a command-line convenience.
# ---------------------------------------------------------------------------
MIN_REVIEWS_PER_QUARTER = 30     # below this: insufficient_history, no number
MIN_QUARTERS = 12                # below this: company dropped from the test
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261011
CI_LEVEL = 0.95

# Diagnostic flags. These do not change any emitted number - they are reported
# so that a bad quarter is visible rather than averaged over.
SCORING_FAILURE_FLAG = 0.10      # F13: >10% of a quarter lost to scorer failure
VERIFIED_SHARE_DROP_FLAG = 0.15  # F12: verified share collapse vs prior quarters
RESIDUAL_DOMINANT_FLAG = 0.50    # F4: |residual| > 50% of |total change|

# Provenance tags recorded in every manifest. These are not cosmetic: they say
# which construction produced a given quarters.csv, so that re-running after the
# paper is reconciled (or after a numeric-stack change) is distinguishable from a
# result rather than silently overwriting one. Same discipline as F10 applies to
# the scorer, applied to the arithmetic.
DECOMPOSITION_SPEC = "methodology-s3-reconstruction-v0-UNVERIFIED"
BOOTSTRAP_IMPL = "stdlib-random.choices-cum_weights"

QUARTER_RE = re.compile(r"^FY(\d{4})Q([1-4])$")

# The tone scale is frozen at integers -2..+2 (tone-rubric-v1.md). Anything
# outside it means the scorer or the contract drifted, and that stops the run
# rather than being coerced into range.
TONE_MIN, TONE_MAX = -2, 2


# ---------------------------------------------------------------------------
# fiscal quarter arithmetic
# ---------------------------------------------------------------------------

def q_index(label):
    """FY2021Q3 -> a dense integer so quarters can be ordered and differenced."""
    m = QUARTER_RE.match((label or "").strip())
    if not m:
        return None
    return int(m.group(1)) * 4 + (int(m.group(2)) - 1)


def q_label(idx):
    return "FY%04dQ%d" % (idx // 4, idx % 4 + 1)


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------

def bootstrap_mean_ci(tones, replicates, rng, level=CI_LEVEL):
    """Percentile bootstrap CI for the mean of a quarter's review tones.

    Resamples reviews within the quarter with replacement, which is the
    resampling unit methodology s4 specifies. Tone is a 5-point integer scale,
    so a resample is drawn over the distinct values with their empirical
    weights - that is the identical multinomial draw, just not spelled out one
    review at a time.

    NUMERIC-STACK SEAM. This function is the only numerically intensive code in
    the script and the only consumer of `rng`, so a numpy/scipy implementation
    replaces this body and nothing else. Assumed here: stdlib only, per the
    brief and the repo as it stands. Note for whoever swaps it - the seed alone
    does not pin the output; the RNG *stream* is part of reproducibility, which
    is why BOOTSTRAP_IMPL is recorded next to the seed in the manifest. A numpy
    run is a new vintage, not a re-run of this one.

    Returns (low, high). Never returns a mean without an interval; callers that
    cannot get an interval must not emit the mean.
    """
    n = len(tones)
    if n == 0:
        return None, None

    values = sorted(set(tones))
    counts = [0] * len(values)
    pos = {v: i for i, v in enumerate(values)}
    for t in tones:
        counts[pos[t]] += 1
    cum, run = [], 0
    for c in counts:
        run += c
        cum.append(run)  # cumulative weights in review counts, not floats

    if len(values) == 1:
        # A degenerate quarter: every review scored the same. The bootstrap
        # distribution is a point mass. Reported as a zero-width interval,
        # which is the honest answer, not an error.
        return float(values[0]), float(values[0])

    means = []
    for _ in range(replicates):
        draw = rng.choices(values, cum_weights=cum, k=n)
        means.append(sum(draw) / n)
    means.sort()

    alpha = (1.0 - level) / 2.0
    lo = means[min(replicates - 1, max(0, int(alpha * replicates)))]
    hi = means[min(replicates - 1, max(0, int((1.0 - alpha) * replicates) - 1))]
    return lo, hi


def quarter_rng(seed, ticker, quarter):
    """Deterministic per-quarter substream, independent of processing order."""
    key = "%s|%s|%s" % (seed, ticker, quarter)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def as_bool(v):
    return str(v).strip().lower() in ("true", "1", "yes", "t")


def load_quarters(ticker, datadir, win=None):
    """Join reviews to scores and bucket by fiscal quarter.

    `win` restricts which quarters are bucketed at all. This is the stage the
    contamination check actually watches - `pipeline_state.heldout_contamination`
    reads `data/{TICKER}/quarters.csv` - so a held-out quarter dropped here
    never reaches disk, and the run is not aborted at exit 2 for a file it
    should never have written (CORE-PROMPT s1.4).

    Precedence on the three mutually exclusive dispositions, so that no review
    is counted twice and none vanishes:
      1. scoring_failed  - tags are not trustworthy when the score is not
      2. seller_or_shipping_only - excluded from the tone aggregate (s7)
      3. included
    """
    d = os.path.join(datadir, ticker)
    rpath = os.path.join(d, "reviews.csv")
    spath = os.path.join(d, "scores.csv")
    for p in (rpath, spath):
        if not os.path.exists(p):
            return None, "missing %s" % p

    scores = {}
    with open(spath, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            scores[row["review_id"]] = row
    if not scores:
        return None, "%s has no rows" % spath

    buckets = defaultdict(lambda: {
        "included": [],           # (parent_asin, tone)
        "n_excluded_seller": 0,
        "n_scoring_failed": 0,
        "n_verified": 0,
        "n_defect": 0,
        "n_price_complaint": 0,
    })
    unscored = 0
    out_of_window = 0
    bad_quarter = defaultdict(int)
    with open(rpath, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if win is not None and not win.allows(row.get("fiscal_quarter")):
                out_of_window += 1
                continue
            qi = q_index(row.get("fiscal_quarter"))
            if qi is None:
                bad_quarter[row.get("fiscal_quarter", "")] += 1
                continue
            s = scores.get(row["review_id"])
            if s is None:
                # A review with no score row is not a zero and not a failure -
                # it means the scoring stage did not cover the extract. That is
                # a pipeline inconsistency, reported and then fatal.
                unscored += 1
                continue

            b = buckets[qi]
            if as_bool(s.get("scoring_failed")):
                b["n_scoring_failed"] += 1
                continue
            if as_bool(s.get("seller_or_shipping_only")):
                b["n_excluded_seller"] += 1
                continue

            raw = (s.get("tone") or "").strip()
            if raw == "":
                # tone null without scoring_failed set: the contract says tone
                # is null only when scoring_failed. Treat as a contract breach.
                return None, ("review %s has null tone but scoring_failed is "
                              "false - scores.csv violates its contract"
                              % row["review_id"])
            tone = int(float(raw))
            if not (TONE_MIN <= tone <= TONE_MAX):
                return None, ("review %s has tone %d outside the frozen -2..+2 "
                              "scale (tone-rubric-v1.md)" % (row["review_id"], tone))

            b["included"].append((row.get("parent_asin", ""), tone))
            if as_bool(row.get("verified_purchase")):
                b["n_verified"] += 1
            if as_bool(s.get("defect")):
                b["n_defect"] += 1
            if as_bool(s.get("price_complaint")):
                b["n_price_complaint"] += 1

    if bad_quarter:
        return None, ("unparseable fiscal_quarter labels (expected FY####Q#): %s"
                      % ", ".join("%r x%d" % (k, v)
                                  for k, v in sorted(bad_quarter.items())[:5]))
    if unscored:
        return None, ("%d reviews in reviews.csv have no row in scores.csv - "
                      "run the scoring stage to completion before aggregating"
                      % unscored)
    if not buckets:
        if out_of_window:
            return None, ("all %d reviews fall outside %s, so there is nothing "
                          "to aggregate in this window"
                          % (out_of_window, win.describe() if win else "the window"))
        return None, "no reviews survived parsing"
    if out_of_window:
        print("  window           %d reviews outside %s, not aggregated"
              % (out_of_window, win.mode))
    return buckets, None


# ---------------------------------------------------------------------------
# decomposition
# ---------------------------------------------------------------------------

def asin_profile(included):
    """ASIN -> (share of the quarter's included reviews, mean tone)."""
    by = defaultdict(list)
    for asin, tone in included:
        by[asin].append(tone)
    n = float(len(included))
    return {a: (len(ts) / n, sum(ts) / float(len(ts))) for a, ts in by.items()}


def decompose(prev_included, cur_included):
    """Split the total tone change into within-ASIN, mix and residual.

    *** THE SWAP SEAM. PROVISIONAL FORMULA, UNVERIFIED SOURCE. ***

    This is the only place the decomposition arithmetic lives, deliberately.
    methodology.md s3 is a second-hand reconstruction, not the source paper's
    construction, and s8 carries an open row to reconcile it. When the paper is
    read, replace the body of this function and bump DECOMPOSITION_SPEC; every
    caller, the reconciliation assertion, the F4 flag and the CSV writer are
    independent of which formula sits here. Do not let a second copy of the
    arithmetic appear anywhere else in the pipeline.

    What is NOT provisional: that the split must happen at all (CORE-PROMPT s7),
    that only the within component may feed the signal, and that the three
    components must reconcile to the total change.

    methodology s3 as currently reconstructed, over ASINs present in both
    quarters:

        d_total  = tone(Q) - tone(Q-1)
        d_within = sum_i w_i(Q-1) * [tone_i(Q) - tone_i(Q-1)]
        d_mix    = sum_i [w_i(Q) - w_i(Q-1)] * tone_i(Q-1)

    The residual is built from its own terms rather than as the leftover, so
    that the reconciliation below is a real check on this code and not a
    tautology. It collects exactly the three things the two named components do
    not cover: entrants, exits, and the within-by-mix interaction.

        entrants    = sum_{i in Q only}   w_i(Q)   * tone_i(Q)
        exits       = -sum_{i in Q-1 only} w_i(Q-1) * tone_i(Q-1)
        interaction = sum_i [w_i(Q) - w_i(Q-1)] * [tone_i(Q) - tone_i(Q-1)]

    Identity, exact and asserted:
        d_within + d_mix + d_residual == d_total
    """
    prev = asin_profile(prev_included)
    cur = asin_profile(cur_included)
    common = set(prev) & set(cur)

    within = mix = interaction = 0.0
    for a in common:
        wp, tp = prev[a]
        wc, tc = cur[a]
        within += wp * (tc - tp)
        mix += (wc - wp) * tp
        interaction += (wc - wp) * (tc - tp)

    entrants = sum(w * t for a, (w, t) in cur.items() if a not in common)
    exits = -sum(w * t for a, (w, t) in prev.items() if a not in common)
    residual = interaction + entrants + exits

    tone_prev = sum(w * t for w, t in prev.values())
    tone_cur = sum(w * t for w, t in cur.values())
    total = tone_cur - tone_prev

    gap = abs((within + mix + residual) - total)
    tol = 1e-9 * max(1.0, abs(total), abs(within), abs(mix), abs(residual))
    assert gap <= tol, (
        "decomposition does not reconcile: within=%.12f mix=%.12f residual=%.12f "
        "sum=%.12f total=%.12f gap=%.3e" %
        (within, mix, residual, within + mix + residual, total, gap))

    return {
        "total": total, "within": within, "mix": mix, "residual": residual,
        "entrants": entrants, "exits": exits, "interaction": interaction,
        "n_common": len(common), "n_entrant": len(set(cur) - common),
        "n_exit": len(set(prev) - common),
    }


# ---------------------------------------------------------------------------
# per-company aggregation
# ---------------------------------------------------------------------------

def aggregate_ticker(ticker, buckets, args):
    """Build the ordered quarter rows for one company."""
    indices = sorted(buckets)
    # The sequence spans every quarter between the first and last observed one,
    # including empty ones. A quarter with no reviews is a real fact about the
    # company and is emitted as insufficient, not skipped into invisibility.
    full = list(range(indices[0], indices[-1] + 1))

    rows, flags = [], []
    by_idx = {}

    for qi in full:
        b = buckets.get(qi)
        label = q_label(qi)
        if b is None:
            b = {"included": [], "n_excluded_seller": 0, "n_scoring_failed": 0,
                 "n_verified": 0, "n_defect": 0, "n_price_complaint": 0}
        inc = b["included"]
        n = len(inc)
        sufficient = n >= args.min_reviews

        row = {
            "fiscal_quarter": label,
            "n_reviews": n,
            "n_excluded_seller": b["n_excluded_seller"],
            "n_scoring_failed": b["n_scoring_failed"],
            "verified_share": (b["n_verified"] / float(n)) if n else None,
            "tone_mean": None, "tone_ci_low": None, "tone_ci_high": None,
            "tone_within": None, "tone_mix": None, "tone_residual": None,
            "baseline_prior4": None, "signal": None,
            # Tag rates describe the sample, not the signal, and F12/F13 need
            # them on thin quarters too - that is where authenticity shifts show
            # up first. They are emitted whenever the quarter has any included
            # review, and are never a substitute for tone_mean.
            "defect_rate": (b["n_defect"] / float(n)) if n else None,
            "price_complaint_rate": (b["n_price_complaint"] / float(n)) if n else None,
            "sufficient": sufficient,
        }

        if sufficient:
            tones = [t for _, t in inc]
            row["tone_mean"] = sum(tones) / float(n)
            rng = quarter_rng(args.seed, ticker, label)
            lo, hi = bootstrap_mean_ci(tones, args.bootstrap, rng)
            if lo is None or hi is None:
                # Unreachable with n >= 1, but a mean without an interval is an
                # incomplete result (CORE-PROMPT s5) and must not be emitted.
                row["tone_mean"] = None
                row["sufficient"] = False
            else:
                row["tone_ci_low"], row["tone_ci_high"] = lo, hi

        # F13 - silent scoring failures
        attempted = n + b["n_excluded_seller"] + b["n_scoring_failed"]
        if attempted and b["n_scoring_failed"] / float(attempted) > args.scoring_failure_flag:
            flags.append("F13 %s: %.0f%% of %d scored reviews failed to score"
                         % (label, 100.0 * b["n_scoring_failed"] / attempted, attempted))

        by_idx[qi] = row
        rows.append((qi, row))

    # --- decomposition, strictly on adjacent sufficient pairs -------------
    for qi, row in rows:
        prev = by_idx.get(qi - 1)
        if not row["sufficient"] or prev is None or not prev["sufficient"]:
            continue
        d = decompose(buckets[qi - 1]["included"], buckets[qi]["included"])
        row["tone_within"] = d["within"]
        row["tone_mix"] = d["mix"]
        row["tone_residual"] = d["residual"]
        row["_decomp"] = d

        # F4 - a quarter whose movement is mostly not the within-ASIN component
        # is weak regardless of its value. The residual is measured against the
        # GROSS magnitude of the three components, not against the total change:
        # the total can be near zero through cancellation, and dividing by it
        # turns a quiet quarter into a 900% alarm.
        gross = abs(d["within"]) + abs(d["mix"]) + abs(d["residual"])
        if gross > 1e-12 and abs(d["residual"]) > args.residual_flag * gross:
            # Name which part of the residual dominates - entrant/exit churn and
            # the within-by-mix interaction are different problems.
            parts = sorted((("entrant", d["entrants"]), ("exit", d["exits"]),
                            ("interaction", d["interaction"])),
                           key=lambda kv: -abs(kv[1]))
            flags.append("F4 %s: residual %+.4f is %.0f%% of gross movement "
                         "(mostly %s; %d entrant / %d exited ASINs; total change "
                         "%+.4f, within %+.4f) - weak quarter"
                         % (q_label(qi), d["residual"],
                            100.0 * abs(d["residual"]) / gross, parts[0][0],
                            d["n_entrant"], d["n_exit"], d["total"], d["within"]))

    # --- baseline and signal ---------------------------------------------
    # baseline_prior4 requires all four prior within-ASIN values. A partial
    # baseline would be a different estimator in different quarters, which is
    # how a formula quietly changes mid-series.
    for qi, row in rows:
        if row["tone_within"] is None:
            continue
        prior = [by_idx[qi - k]["tone_within"] for k in (1, 2, 3, 4)
                 if (qi - k) in by_idx]
        if len(prior) < 4 or any(v is None for v in prior):
            continue
        row["baseline_prior4"] = sum(prior) / 4.0
        row["signal"] = row["tone_within"] - row["baseline_prior4"]

    # --- F12 verified-purchase share ------------------------------------
    for qi, row in rows:
        if row["verified_share"] is None or not row["sufficient"]:
            continue
        prior = [by_idx[qi - k]["verified_share"] for k in (1, 2, 3, 4)
                 if (qi - k) in by_idx and by_idx[qi - k]["sufficient"]
                 and by_idx[qi - k]["verified_share"] is not None]
        if len(prior) < 2:
            continue
        ref = sum(prior) / float(len(prior))
        if ref - row["verified_share"] > args.verified_drop_flag:
            flags.append("F12 %s: verified share %.2f vs %.2f over the prior %d "
                         "quarters - review authenticity changed, not a tone event"
                         % (q_label(qi), row["verified_share"], ref, len(prior)))

    return [r for _, r in rows], flags


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

COLUMNS = ["fiscal_quarter", "n_reviews", "n_excluded_seller", "n_scoring_failed",
           "verified_share", "tone_mean", "tone_ci_low", "tone_ci_high",
           "tone_within", "tone_mix", "tone_residual", "baseline_prior4",
           "signal", "defect_rate", "price_complaint_rate", "sufficient"]

FLOATS = {"verified_share", "tone_mean", "tone_ci_low", "tone_ci_high",
          "tone_within", "tone_mix", "tone_residual", "baseline_prior4",
          "signal", "defect_rate", "price_complaint_rate"}


def fmt(col, v):
    if v is None:
        return ""          # null, per the contract - never 0.0
    if col in FLOATS:
        return "%.6f" % v
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def write_quarters(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([fmt(c, r.get(c)) for c in COLUMNS])


def update_dropped(path, outcomes):
    """data/dropped.csv - a dropped company is recorded, never just absent."""
    fields = ["ticker", "dropped", "reason", "n_quarters_observed",
              "n_quarters_sufficient", "min_quarters", "min_reviews_per_quarter",
              "recorded_at"]
    existing = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row.get("ticker", "")] = {k: row.get(k, "") for k in fields}
    existing.update({o["ticker"]: o for o in outcomes})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for t in sorted(existing):
            w.writerow(existing[t])


def update_manifest(path, block):
    """Never overwritten wholesale (data-contracts.md) - merged key by key."""
    man = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                man = json.load(f)
        except (ValueError, OSError):
            man = {}
    man.setdefault("aggregate", {}).update(block)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(man, f, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def n(v, w=8, p=3):
    return ("%*.*f" % (w, p, v)) if v is not None else "%*s" % (w, "-")


def report(ticker, rows, flags, args, dropped, reason):
    print("\n=== %s ===" % ticker)
    print("%-10s %6s %5s %5s %6s   %-17s %8s %8s %8s %8s %8s  %s"
          % ("quarter", "n", "sell", "fail", "verif", "tone (95% CI)",
             "within", "mix", "resid", "base4", "signal", "ok"))
    for r in rows:
        ci = ("%6.3f [%6.3f,%6.3f]" % (r["tone_mean"], r["tone_ci_low"], r["tone_ci_high"])
              if r["tone_mean"] is not None else "%21s" % "insufficient_history")
        print("%-10s %6d %5d %5d %6s   %-17s %s %s %s %s %s  %s"
              % (r["fiscal_quarter"], r["n_reviews"], r["n_excluded_seller"],
                 r["n_scoring_failed"],
                 ("%.2f" % r["verified_share"]) if r["verified_share"] is not None else "-",
                 ci, n(r["tone_within"]), n(r["tone_mix"]), n(r["tone_residual"]),
                 n(r["baseline_prior4"]), n(r["signal"]),
                 "y" if r["sufficient"] else "NO"))

    suff = sum(1 for r in rows if r["sufficient"])
    sig = sum(1 for r in rows if r["signal"] is not None)
    print("\n  quarters observed            : %d" % len(rows))
    print("  clear the %d-review floor     : %d" % (args.min_reviews, suff))
    print("  below the floor (no number)  : %d" % (len(rows) - suff))
    print("  with a decomposition         : %d"
          % sum(1 for r in rows if r["tone_within"] is not None))
    print("  with a signal (needs 4 prior): %d" % sig)
    if flags:
        print("\n  blocking-check flags (reported, never smoothed):")
        for fl in flags:
            print("    !! %s" % fl)
    if any(r["tone_within"] is not None for r in rows):
        print("\n  !! within/mix/residual above come from %s - provisional."
              % DECOMPOSITION_SPEC)
    if dropped:
        print("\n  !! DROPPED from the test: %s" % reason)
        print("  !! Recorded in data/dropped.csv. Do not substitute another")
        print("  !! company to refill the count (CORE-PROMPT s2).")


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def self_test(args):
    """Code correctness only. Produces NO research result and NO quarters.csv.

    This exists because the decomposition identity and the floor behaviour are
    exactly the kind of thing that fails silently on real data. It is not a
    substitute for running the script on real reviews, and the constructed
    numbers below are not findings about any company.
    """
    print("self-test: code correctness only, not a research result\n")
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print("  %-52s %s%s" % (name, "pass" if cond else "FAIL",
                                "" if cond else "  <- " + detail))
        ok = ok and cond

    # 1. identity holds on a case with within change, mix shift, entry and exit
    prev = ([("A", 2)] * 60 + [("B", -2)] * 40 + [("X", 0)] * 20)
    cur = ([("A", 1)] * 20 + [("B", -1)] * 80 + [("E", 2)] * 30)
    d = decompose(prev, cur)          # asserts internally
    check("decomposition reconciles (within+mix+resid == total)",
          abs(d["within"] + d["mix"] + d["residual"] - d["total"]) < 1e-12)
    check("residual is built from entrants/exits/interaction",
          abs(d["residual"] - (d["entrants"] + d["exits"] + d["interaction"])) < 1e-12)

    # 2. a pure mix shift must put nothing in the within component
    prev = [("A", 2)] * 50 + [("B", -2)] * 50
    cur = [("A", 2)] * 10 + [("B", -2)] * 90
    d = decompose(prev, cur)
    check("pure mix shift -> within == 0", abs(d["within"]) < 1e-12)
    check("pure mix shift -> mix carries the whole change",
          abs(d["mix"] - d["total"]) < 1e-12, "mix=%.6f total=%.6f" % (d["mix"], d["total"]))

    # 3. a pure within change must put nothing in the mix component
    prev = [("A", 2)] * 50 + [("B", 0)] * 50
    cur = [("A", -2)] * 50 + [("B", 0)] * 50
    d = decompose(prev, cur)
    check("pure within change -> mix == 0", abs(d["mix"]) < 1e-12)
    check("pure within change -> within carries the whole change",
          abs(d["within"] - d["total"]) < 1e-12)

    # 4. the floor is a hard stop. Quarters of 1, 4 and 6 reviews - the pilot's
    #    actual counts - must emit no tone number.
    buckets = {}
    base = 2020 * 4
    for k, cnt in enumerate([1, 4, 6, 40, 40, 40]):
        buckets[base + k] = {
            "included": [("A", 1)] * cnt, "n_excluded_seller": 0,
            "n_scoring_failed": 0, "n_verified": cnt, "n_defect": 0,
            "n_price_complaint": 0}
    rows, _ = aggregate_ticker("TEST", buckets, args)
    thin = [r for r in rows if r["n_reviews"] in (1, 4, 6)]
    check("quarters of 1/4/6 reviews emit sufficient=false",
          len(thin) == 3 and all(not r["sufficient"] for r in thin))
    check("quarters below the floor emit tone_mean=null",
          all(r["tone_mean"] is None for r in thin))
    check("quarters below the floor emit no CI",
          all(r["tone_ci_low"] is None and r["tone_ci_high"] is None for r in thin))
    check("quarters below the floor emit no decomposition",
          all(r["tone_within"] is None for r in thin))
    check("no sufficient quarter lacks an interval",
          all((r["tone_ci_low"] is not None and r["tone_ci_high"] is not None)
              for r in rows if r["tone_mean"] is not None))
    check("a gap in the sequence is not bridged",
          rows[4]["tone_within"] is not None and rows[3]["tone_within"] is None)

    # 5. baseline/signal timing: within needs a predecessor, baseline needs four
    buckets = {}
    for k in range(10):
        tone = 1 if k < 5 else -1
        buckets[base + k] = {
            "included": [("A", tone)] * 50, "n_excluded_seller": 0,
            "n_scoring_failed": 0, "n_verified": 50, "n_defect": 0,
            "n_price_complaint": 0}
    rows, _ = aggregate_ticker("TEST", buckets, args)
    check("tone_within null in the first quarter (no predecessor)",
          rows[0]["tone_within"] is None)
    check("baseline_prior4 null for the first four within values",
          all(r["baseline_prior4"] is None for r in rows[:5]))
    check("baseline_prior4 present once four prior within values exist",
          rows[5]["baseline_prior4"] is not None)
    check("signal == tone_within - baseline_prior4",
          abs(rows[5]["signal"] - (rows[5]["tone_within"] - rows[5]["baseline_prior4"])) < 1e-12)

    # 6. bootstrap reproducibility and coverage of the mean
    tones = [2] * 30 + [-1] * 40 + [0] * 30
    a = bootstrap_mean_ci(tones, 500, quarter_rng(args.seed, "T", "FY2020Q1"))
    b = bootstrap_mean_ci(tones, 500, quarter_rng(args.seed, "T", "FY2020Q1"))
    c = bootstrap_mean_ci(tones, 500, quarter_rng(args.seed, "T", "FY2020Q2"))
    mean = sum(tones) / float(len(tones))
    check("same seed + quarter -> identical CI", a == b)
    check("different quarter -> different substream", a != c)
    check("CI brackets the point estimate", a[0] <= mean <= a[1],
          "%.4f not in [%.4f, %.4f]" % (mean, a[0], a[1]))
    lo2, hi2 = bootstrap_mean_ci([1] * 50, 200, quarter_rng(args.seed, "T", "FY2020Q3"))
    check("degenerate quarter -> zero-width interval, not an error",
          lo2 == 1.0 and hi2 == 1.0)

    # 7. MIN_QUARTERS drop
    check("fewer than %d usable quarters -> dropped" % args.min_quarters,
          sum(1 for r in rows if r["sufficient"]) < args.min_quarters)

    print("\n%s" % ("all self-tests passed" if ok else "SELF-TEST FAILURES ABOVE"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    windowmod.add_tickers_argument(
        p, "the orchestrator's spelling (.claude/commands/review-tone-run.md); "
           "combined with any --ticker flags, not overridden by them")
    windowmod.add_argument(p)
    p.add_argument("--ticker", action="append", dest="ticker_each", default=None,
                   help="repeatable; default is every data/*/ with scores.csv")
    p.add_argument("--datadir", default="data")
    p.add_argument("--min-reviews", type=int, default=MIN_REVIEWS_PER_QUARTER,
                   help="hard floor per quarter (default %d)" % MIN_REVIEWS_PER_QUARTER)
    p.add_argument("--min-quarters", type=int, default=MIN_QUARTERS,
                   help="hard floor per company (default %d)" % MIN_QUARTERS)
    p.add_argument("--bootstrap", type=int, default=BOOTSTRAP_REPLICATES)
    p.add_argument("--seed", type=int, default=BOOTSTRAP_SEED)
    p.add_argument("--scoring-failure-flag", type=float, default=SCORING_FAILURE_FLAG)
    p.add_argument("--verified-drop-flag", type=float, default=VERIFIED_SHARE_DROP_FLAG)
    p.add_argument("--residual-flag", type=float, default=RESIDUAL_DOMINANT_FLAG,
                   help="flag a quarter when |residual| exceeds this share of"
                        " |within|+|mix|+|residual| (default %.2f)"
                        % RESIDUAL_DOMINANT_FLAG)
    p.add_argument("--self-test", action="store_true",
                   help="check this code against constructed cases; writes nothing")
    args = p.parse_args()

    if args.self_test:
        return self_test(args)

    if args.min_reviews != MIN_REVIEWS_PER_QUARTER or args.min_quarters != MIN_QUARTERS:
        print("!! floors overridden: min_reviews=%d min_quarters=%d (prereg says %d/%d)"
              % (args.min_reviews, args.min_quarters,
                 MIN_REVIEWS_PER_QUARTER, MIN_QUARTERS))
        print("!! A real run uses the prereg's values. This output is not a result.")

    tickers, seen_t = [], set()
    for x in list(args.tickers or []) + list(args.ticker_each or []):
        u = x.upper()
        if u not in seen_t:
            seen_t.add(u)
            tickers.append(u)
    if not tickers:
        tickers = sorted(
            d for d in os.listdir(args.datadir)
            if os.path.isdir(os.path.join(args.datadir, d))
            and os.path.exists(os.path.join(args.datadir, d, "scores.csv")))
    if not tickers:
        sys.exit(
            "no company has %s/{TICKER}/scores.csv.\n\n"
            "Stage B5 aggregates what B2 (extract) and B4 (score) produced; it "
            "cannot\nbe run before them, and it will not invent a fixture to "
            "stand in for them.\nRun the extract and scoring stages first, then "
            "re-run this.\n\nTo check this code without data: --self-test"
            % args.datadir)

    win = windowmod.resolve(args, "aggregate.py (B5)")
    print(win.describe())
    print("stage B5 aggregate - floors %d reviews/quarter, %d quarters/company"
          % (args.min_reviews, args.min_quarters))
    print("bootstrap: %d replicates, seed %d, 95%% percentile CI, impl %s"
          % (args.bootstrap, args.seed, BOOTSTRAP_IMPL))
    print("decomposition spec: %s" % DECOMPOSITION_SPEC)
    print("!! The within/mix formula is PROVISIONAL. methodology.md s3 is a")
    print("!! second-hand reconstruction, not the source paper; s8 has an open")
    print("!! row to reconcile it. Treat tone_within / tone_mix / tone_residual")
    print("!! and every signal built on them as traceable to an unverified")
    print("!! construction until the paper is read.")

    outcomes, failures = [], []
    for ticker in tickers:
        buckets, err = load_quarters(ticker, args.datadir, win)
        if err:
            print("\n=== %s ===\n  !! %s" % (ticker, err))
            failures.append((ticker, err))
            continue

        rows, flags = aggregate_ticker(ticker, buckets, args)
        suff = sum(1 for r in rows if r["sufficient"])
        dropped = suff < args.min_quarters
        reason = ("only %d usable quarters, floor is %d" % (suff, args.min_quarters)
                  if dropped else "")

        d = os.path.join(args.datadir, ticker)
        write_quarters(os.path.join(d, "quarters.csv"), rows)
        update_manifest(os.path.join(d, "manifest.json"), {
            "stage": "B5",
            "aggregated_at": datetime.datetime.utcnow().isoformat() + "Z",
            # Which window produced quarters.csv. Without this a short series
            # cannot be told apart from a restricted one, and the restriction
            # is the whole of the held-out guarantee.
            "window": win.mode,
            "window_bounds": ("%dQ%d-%dQ%d" % (win.bounds[0] + win.bounds[1])
                              if win.bounds else None),
            "window_source": win.source,
            "min_reviews_per_quarter": args.min_reviews,
            "min_quarters": args.min_quarters,
            "bootstrap_replicates": args.bootstrap,
            "bootstrap_seed": args.seed,
            "bootstrap_impl": BOOTSTRAP_IMPL,
            "ci_level": CI_LEVEL,
            "decomposition_spec": DECOMPOSITION_SPEC,
            "decomposition_provisional": True,
            "quarters_observed": len(rows),
            "quarters_sufficient": suff,
            "quarters_with_signal": sum(1 for r in rows if r["signal"] is not None),
            "dropped": dropped,
            "dropped_reason": reason,
            "flags": flags,
        })
        outcomes.append({
            "ticker": ticker, "dropped": "true" if dropped else "false",
            "reason": reason, "n_quarters_observed": len(rows),
            "n_quarters_sufficient": suff, "min_quarters": args.min_quarters,
            "min_reviews_per_quarter": args.min_reviews,
            "recorded_at": datetime.date.today().isoformat(),
        })
        report(ticker, rows, flags, args, dropped, reason)

    if outcomes:
        update_dropped(os.path.join(args.datadir, "dropped.csv"), outcomes)

    kept = [o for o in outcomes if o["dropped"] == "false"]
    print("\n%s\n%d companies aggregated, %d kept, %d dropped, %d failed to load"
          % ("-" * 72, len(outcomes), len(kept),
             len(outcomes) - len(kept), len(failures)))
    for o in outcomes:
        if o["dropped"] == "true":
            print("  dropped  %-8s %s" % (o["ticker"], o["reason"]))
    if len(kept) < 8:
        print("\n!! Fewer than 8 companies carry usable quarters. Record the gap;")
        print("!! do not substitute companies from outside the screen (CORE-PROMPT s2).")
    print("\nNext: B6 fetch_sec.py. backtest.py stays unwritten until the prereg")
    print("is frozen - its test statistic and thresholds are prereg choices, and")
    print("choosing them now would mean choosing them having seen this output.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
