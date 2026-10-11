#!/usr/bin/env python3
"""Run metadata for the numeric environment, for every results.jsonl row.

The pipeline computes its own statistics against the standard library, so the
thing that pins the arithmetic behind a result row is the COMMIT, not a library
version (references/numeric-stack.md s2). This module produces the
`numeric_env` block that records it, so that no stage assembles it by hand and
no two stages spell a key differently.

It also enforces the seeding convention. Stage seeds are derived, never chosen
per stage, because two sessions left to invent their own would not agree.

Usage from a stage:

    import env_stamp
    env = env_stamp.numeric_env(bootstrap_reps=10000, null_permutations=1000)
    seed = env_stamp.stage_seed("bootstrap")

Usage as a check, before a long run:

    python3 scripts/env_stamp.py

Standard library only. That is a binding policy for every pipeline stage,
not an accident of this script: see references/numeric-stack.md. numpy and
scipy are development-only, and no stage may import them.
"""

import json
import os
import platform
import random
import subprocess
import sys

# Fixed in the prereg. Overridden only by a frozen prereg, never ad hoc.
DEFAULT_SEED = 20261011

# Derived offsets, so distinct stages never share a stream.
STAGE_OFFSETS = {
    "bootstrap": 0,
    "null": 1,
    "calibration": 2,
    "degradation": 3,
}

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


def stage_seed(stage, seed=DEFAULT_SEED):
    """The seed for one stage. Raises on an unknown stage rather than guessing.

    An unknown stage name means a new stage was added without giving it an
    offset, which would silently share a stream with an existing one.
    """
    if stage not in STAGE_OFFSETS:
        raise KeyError(
            "unknown stage %r; add it to STAGE_OFFSETS and record the addition "
            "in REVIEW-TONE-CHANGELOG.md. Known: %s"
            % (stage, ", ".join(sorted(STAGE_OFFSETS)))
        )
    return seed + STAGE_OFFSETS[stage]


def rng(stage, seed=DEFAULT_SEED):
    """A seeded generator for one stage.

    Returns a random.Random instance, never the module-level random.* API,
    whose global state two stages in one process would share. Draw only via
    .random(), .getrandbits() and .randrange(): those are stream-stable across
    CPython versions, and .choices()/.sample()/.shuffle() are not.
    """
    return random.Random(stage_seed(stage, seed))


def _git(*args):
    try:
        out = subprocess.run(
            ("git", "-C", _REPO) + args,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        )
        return out.stdout.decode().strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def code_commit():
    """(sha, dirty) for the scripts that are about to produce a number.

    `dirty` reflects uncommitted changes anywhere tracked, not just under
    scripts/: a changed reference or prereg can move a result just as surely.
    """
    sha = _git("rev-parse", "HEAD")
    if sha is None:
        return None, True          # no git: treat as unreproducible
    status = _git("status", "--porcelain")
    return sha, bool(status)


def numeric_env(seed=DEFAULT_SEED, bootstrap_reps=None, null_permutations=None,
                stages=("bootstrap", "null")):
    """The `numeric_env` block for a results.jsonl row.

    Required in every row. Row validation should reject a row without it, and
    should refuse a verdict of `signal` from a row with code_dirty true.
    """
    sha, dirty = code_commit()
    env = {
        "stack": "stdlib",
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": "%s-%s-%s" % (platform.system(), platform.release(),
                                  platform.machine()),
        "code_commit": sha,
        "code_dirty": dirty,
        "rng": "stdlib-mt19937",
        "seed": seed,
        "stage_seeds": {s: stage_seed(s, seed) for s in stages},
    }
    if bootstrap_reps is not None:
        env["bootstrap_reps"] = bootstrap_reps
    if null_permutations is not None:
        env["null_permutations"] = null_permutations
    return env


def assert_no_third_party():
    """Fail loudly if a stage has pulled numpy or scipy into the pipeline.

    The dev venv has both installed on purpose. This is what stops them
    drifting from the verification tests into a stage that writes a result.
    """
    leaked = [m for m in ("numpy", "scipy", "pandas", "sklearn")
              if m in sys.modules]
    if leaked:
        raise RuntimeError(
            "third-party numerics imported by a pipeline stage: %s. The "
            "pipeline is stdlib only (references/numeric-stack.md)."
            % ", ".join(leaked)
        )


if __name__ == "__main__":
    assert_no_third_party()
    env = numeric_env(bootstrap_reps=10000, null_permutations=1000)
    print(json.dumps(env, indent=2, sort_keys=True))
    if env["code_dirty"]:
        print("\nWARNING: tracked files are dirty. A row produced now records "
              "code_dirty: true and its verdict is not citable.", file=sys.stderr)
