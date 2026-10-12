#!/usr/bin/env python3
"""Append-only integrity checks.

`results/readings.jsonl` and `MISSES.md` are appended to and never edited
(CORE-PROMPT s1.5). This verifies each file is still an extension of its
committed version, so an edit is refused rather than laundered into the log.

Lifted from the archived `pipeline_state.py`, unchanged. Standard library only.
"""

import os
import subprocess


def is_tracked(root, relpath):
    ok, _ = git(root, "ls-files", "--error-unmatch", relpath)
    return ok


def append_only_violations(root):
    """Has an append-only file been shortened or rewritten since HEAD?

    CORE-PROMPT s1.6: a negative result is never removed or rewritten. The
    check is cheap and catches the one edit that would destroy the evidence.
    """
    out = []
    for rel in ("results/results.jsonl", "MISSES.md"):
        path = os.path.join(root, rel)
        if not os.path.exists(path) or not is_tracked(root, rel):
            continue
        blob = committed_blob(root, rel)
        if blob is None:
            continue
        with open(path, encoding="utf-8") as f:
            now = f.read()
        if not now.startswith(blob):
            out.append("%s is not an extension of its committed version: it has "
                       "been edited or truncated. %s is append-only "
                       "(CORE-PROMPT s1.6)" % (rel, rel))
    return out
