# Numeric Stack

**Decision: the pipeline is standard library only. numpy and scipy are
development-only, and no pipeline stage may import them.**

Decided 2026-10-11, from measurement rather than preference. Read §2 before
proposing a change; it records what was weighed, including the arguments that
pointed the other way and why they did not survive being measured.

---

## 1. The rule

| Code | Stack | Why |
|---|---|---|
| Everything under `scripts/` | stdlib only | a `results.jsonl` row must stay reproducible |
| Cross-check tests | `requirements-dev.txt` (numpy, scipy) | scipy is the oracle, never the engine |

Every statistical primitive this project needs is written in-repo against the
standard library, and **ships with a test that cross-checks it against scipy's
reference implementation.** scipy is how we know the arithmetic is right. It is
not how the arithmetic is done.

Primitives in that category: tie-corrected (midrank) Spearman, quadratic-weighted
kappa, percentile and BCa bootstrap intervals, the within-company demeaning that
stands in for company fixed effects, and the shuffled-date null distribution.

A primitive without a passing scipy cross-check is not finished and may not
produce a number that reaches `results/results.jsonl`.

### What the stdlib already gives us

Checked on CPython 3.9.6, which is the only interpreter on the build machine:

- `statistics.NormalDist().inv_cdf()` — the normal quantile function, which is
  the only non-trivial ingredient of a BCa interval.
- `statistics.quantiles()` — percentile intervals.
- `random.Random.random()` / `.getrandbits()` — a documented, stream-stable
  PRNG (see §3).

Nothing in §1's primitive list needs a special function the stdlib lacks.

## 2. The reasoning

### What was expected, and what measurement showed

The case for adopting numpy was that the heavy stages — bootstrap resampling and
a permutation null — are exactly where it earns its place. Half of that turned
out to be false.

Benchmarked on the realistic Phase B workload (850 company-quarters, B = 10,000
bootstrap resamples at a mean of 200 reviews per quarter, 1,000 date-shuffle
permutations over 850 company-quarter pairs), CPython 3.9.6, macOS 15 arm64:

| Stage | stdlib | numpy | speedup |
|---|---|---|---|
| Bootstrap CIs | 73.5 s | 4.3 s | 17.0x |
| Permutation null | 0.4 s | 0.5 s | 0.7x — numpy is *slower* |
| **Total** | **73.9 s** | **4.8 s** | 15.3x |

The permutation null is not a bottleneck in either stack; at 850 pairs, numpy's
per-call overhead exceeds the work. And 74 seconds for a full run of the heaviest
stage is not a cost worth buying a dependency to avoid. **Speed does not justify
the dependency at this panel size.**

The correctness argument was then tested the same way. A hand-rolled Spearman
with no midrank correction, run on tone scores in {−2..+2} where ties are dense,
was wrong by 0.2% of the estimate on shuffled input and 1% on a panel read in
ticker order (the adversarial case, where arbitrary tiebreaks correlate with the
company fixed effect). Real, worth fixing, but not the silent catastrophe that
would justify restructuring the repo — and fixable in about fifteen lines, with
scipy available in a test to prove it is fixed.

### What actually decided it

1. **Inference here comes from resampling, not from distributions.** CORE-PROMPT
   §6 requires a shuffled-date null run alongside every real run, which replaces
   the analytic p-value. That removes scipy.stats' main draw: we do not need its
   distribution functions, because significance is established by permutation,
   and permutation is arithmetic.

2. **In-repo arithmetic is versioned by git.** This is the decisive point for the
   reproducibility constraint. If the statistics live in `scripts/`, the commit
   SHA *is* the version pin — exact, always recoverable, and already recorded.
   A pinned external version is only as good as its installability.

3. **The pins available are fragile in exactly the way that matters.** The build
   machine has one interpreter, CPython 3.9.6, which forces numpy 2.0.2 and
   scipy 1.13.1 — the last releases supporting 3.9, shipping wheels only for
   CPython 3.9–3.12. A result row produced against those pins becomes
   progressively harder to reproduce as Python moves on, which is the precise
   failure the constraint exists to prevent. stdlib arithmetic has no such decay.

4. **Staying stdlib avoids creating the two-tier split rather than documenting
   it.** The four existing collection scripts stay stdlib regardless. Adopting
   numpy for the statistical stages would *introduce* the seam and the "did you
   activate the venv" failure mode; declining it means there is no seam to
   onboard anyone across.

### What would overturn this — the trip-wire

This decision is sized to the current panel, and it says so. Revisit it if any
of these becomes true:

- Mean included reviews per company-quarter exceeds ~2,000, or any single
  quarter exceeds ~50,000. Bootstrap cost is linear in total review-slots × B:
  at a mean of 5,000 the measured 74 s becomes ~31 minutes, and at 20,000 it is
  ~2 hours. (Before reaching for numpy, note that bootstrapping the mean of a
  5-valued integer score is a multinomial resample over 5 counts and need not
  touch n items at all — that shortcut is worth implementing first.)
- The prereg fixes a test statistic whose null distribution genuinely cannot be
  obtained by permutation.
- A primitive is found that cannot be cross-checked against scipy.

Whoever overturns it: record the measurement that did so, not just the
conclusion.

## 3. Seeding convention — binding

Two sessions must not each invent one. numpy's `Generator` and Python's `random`
do not produce the same stream, and within the stdlib not every method is
stream-stable either.

**The convention:**

- One generator per stage, constructed as `random.Random(seed)`. Never the
  module-level `random.*` functions — they share hidden global state, so two
  stages running in one process would consume each other's stream.
- Draw only through `.random()`, `.getrandbits()` and `.randrange()`. These are
  covered by CPython's documented backward-compatibility promise for `random`.
  **Do not use `random.choices`, `random.sample` or `random.shuffle`** in any
  stage whose output reaches a result: their internal algorithms are
  implementation details and have changed across CPython versions, so the same
  seed can yield a different stream on a different interpreter. Build resampling
  and permutation on `randrange` / `getrandbits` directly.
- Seeds are integers fixed in the prereg, never wall-clock or `os.urandom`
  derived, and never drawn from the data.
- Default seed for all stages unless the prereg overrides it: **`20261011`**.
- Distinct stages get distinct seeds, derived as `seed + stage_offset`:
  `0` bootstrap, `1` shuffled-date null, `2` calibration-set sampling,
  `3` degradation sampling (Phase C).
- The seed, the stage offset and the reps count are recorded in the result row.
  A CI or a null result without its seed is not reproducible and is incomplete.

In the dev/verification venv only, where numpy is permitted, seed with
`np.random.Generator(np.random.PCG64(seed))` — never the legacy
`np.random.seed` / `RandomState`. Keep in mind that a cross-check test can
compare *statistics*, but cannot compare *streams* against the stdlib
implementation; test the estimator on fixed input arrays, not on a shared seed.

## 4. Run metadata — where versions get recorded

Because the arithmetic is in-repo, the thing that pins it is the commit, not a
library version. `results/results.jsonl` rows carry a `numeric_env` object
alongside the existing `prereg_hash`, `universe_hash`, `rubric_version` and
`scorer_model_id`:

```json
"numeric_env": {
  "stack": "stdlib",
  "python_version": "3.9.6",
  "python_implementation": "CPython",
  "platform": "Darwin-25.5.0-arm64",
  "code_commit": "21fc389d59d13a3b625e5d71ff5b4f3642b78cd1",
  "code_dirty": false,
  "rng": "stdlib-mt19937",
  "seed": 20261011,
  "stage_seeds": {"bootstrap": 20261011, "null": 20261012},
  "bootstrap_reps": 10000,
  "null_permutations": 1000
}
```

`scripts/env_stamp.py` produces this block; stages call
`env_stamp.numeric_env(...)` rather than assembling it by hand. `code_dirty`
true means the row was produced from uncommitted code and is **not** a
reproducible result — it may be written, but its verdict is not citable.

`stack` is `"stdlib"` and is expected to stay that way. It exists so that if the
trip-wire in §2 is ever hit, rows produced before and after the change are
distinguishable without archaeology; a numpy-era row would carry
`"stack": "numpy"` plus `numpy_version` and `scipy_version` keys.

**For whoever is building `/review-tone-run` and the `results.jsonl` row
validation:** `numeric_env` is a required key, and row validation should reject
a row that lacks it, or that reports `code_dirty: true` while claiming a verdict
of `signal`. The keys above are the contract.
