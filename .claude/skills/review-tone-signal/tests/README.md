# Orchestrator tests

89 checks over throwaway copies of the repo. They exercise the paths that only
appear when something is wrong, which is where a gate either holds or quietly
does not.

```bash
python3 .claude/skills/review-tone-signal/tests/run_tests.py
```

Each case builds a fresh git repo from the committed tree (`mkfixture.sh`),
plants synthetic fixtures (`fixtures.py`), and runs the real orchestrator against
it. Nothing touches `data/`, `results/` or `MISSES.md` in the working repo.

The five unbuilt upstream stages are stubbed at the interface documented in
`.claude/commands/review-tone-run.md`. When a real one lands, delete its stub
from `fixtures.py` and the suite tests the real script.

Covered refusals, in the order a run would meet them: a missing universe, an
edited universe, the rolling panel taking precedence over the legacy screens,
fewer than 8 eligible companies, an ineligible row with no recorded reason,
eligibility scored after the formation date, a company scored from outside the
universe, a truncated coverage scan, coverage below the floor, a hand-picked
ticker, kappa 0.48, adjacent agreement 0.70, a calibration set under 100
reviews, a prereg with surviving placeholders, an uncommitted prereg, a hash
mismatch, a prereg whose claimed kappa disagrees with the measured one, scorer
drift against the prereg, a stage that ignores `--window dev` and writes
held-out quarters, a truncated `results.jsonl`, a rewritten `MISSES.md`, a row
missing any provenance field, a verdict with no measurement behind it, a
specification list where a count belongs, and — the one most likely to be broken
by a schedule — a held-out failure followed by a second run that refuses to
retry it.

---

# Pre-filter tests

63 checks over the A1b mechanical pre-filter (`scripts/prefilter_panel.py`).
No network: the probes run against filing text written in the test file, and
the build runs against a synthetic measurement cache.

```bash
python3 .claude/skills/review-tone-signal/tests/test_prefilter.py
```

Covered: the segment-count parse (reportable beats operating, `single` reads as
1, a bare "segment" is not a count), every fixed exclusion that keeps a
non-channel out of a channel count (Amazon as AWS, direct-to-consumer
advertising, "available on our website", DTC the Depository Trust Company), the
cut precedence, a seeded order that is the same order twice and is still a
permutation, a build that is byte-identical on a second run, a manifest that
records the panel sha256 and the thresholds it was cut from, and the refusals:
an unmeasured company-year, a half-written cache file, a shortlist edited after
its hash was recorded, a cut whose reason was deleted, and an in-band
company-year dropped from the record.

The last four checks are the handoff: `score_eligibility.load_shortlist` reads
the shortlist, in the order written, records the same sha256 the hash file
does, and refuses a ticker that is not an in-band company-year.
