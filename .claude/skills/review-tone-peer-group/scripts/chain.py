#!/usr/bin/env python3
"""A hash-chained append-only log. Shared by every record in this component.

Two things here are written this way (CORE-PROMPT §1.5):

  pool_evidence.jsonl     concentration evidence, which feeds the pool counts
  <group>.parents.jsonl   parent-ASIN re-resolution, which feeds stage G

Both are records that a silent edit would otherwise not show, and both have the
same shape: append a line, chain it to the one before, never rewrite. Extracted
into one module after the second one needed it, so there is one definition of
"chained" and the two cannot drift apart.

Every line carries `prev`, the sha256 of the line before it as written. Removing
or altering a line therefore breaks the chain at that point and at every point
after it. Nothing here deletes: a correction is an appended record.

Standard library only.
"""

import datetime as dt
import hashlib
import json
import os

GENESIS = "0" * 64


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def serialise(record):
    """The one serialisation. The hash is over these bytes, so it is fixed."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def line_hash(record):
    """sha256 of the record as written, which includes its own `prev`."""
    return hashlib.sha256(serialise(record).encode("utf-8")).hexdigest()


def read(path):
    """[(line_number, record_or_None)] — None marks a line that is not JSON."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append((n, json.loads(line)))
            except ValueError:
                out.append((n, None))
    return out


def append(path, record):
    """Append one record, chained to the last line."""
    rows = read(path)
    prev = line_hash(rows[-1][1]) if rows and rows[-1][1] else GENESIS
    record = dict(record, prev=prev, recorded_at=_now())
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(serialise(record) + "\n")
    return record


def walk(path, actions=()):
    """Every break in the chain. Empty means the log has not been altered."""
    problems = []
    prev = GENESIS
    for n, rec in read(path):
        if rec is None:
            problems.append("line %d: not valid JSON" % n)
            return problems
        if rec.get("prev") != prev:
            problems.append(
                "line %d: chained to %s but the previous line hashes to %s - a "
                "line was altered or removed at or before here"
                % (n, (rec.get("prev") or "none")[:12], prev[:12]))
            return problems
        if actions and rec.get("action") not in actions:
            problems.append("line %d: unknown action %r" % (n, rec.get("action")))
        prev = line_hash(rec)
    return problems


def records(path):
    """Every well-formed record, in order."""
    return [r for _, r in read(path) if r]
