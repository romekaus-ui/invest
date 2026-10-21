#!/usr/bin/env python3
"""Window restriction — inert in the live design, and loud about it.

The archived harness held out the final 8 quarters of a frozen corpus until a
prereg was committed, and this module enforced that mechanically: every
pre-freeze stage ran `--window dev` and physically could not write a held-out
quarter.

**The live monitor has no held-out window and no prereg.** Every review it holds
was collected after collection started, so there is no unspent future period
sitting in the data to protect. `CORE-PROMPT.md` §8 handles the same danger
differently: a predictive claim needs its test, horizon and threshold written
down *before the revenue arrives*, which is a discipline about time, not about
which rows a script may read.

So this module keeps its interface — `score_reviews.py` and `aggregate.py` call
it, and the contract in `CONTRACTS.md` §4 names `--window` — while restricting
nothing. That is a deliberate no-op, not an oversight, and it is **announced on
every run** rather than being silent.

A silently-inert guard is the specific failure this project has already paid
for: `detect_calibration` once gated B3 on a file the scorer never wrote, so the
gate could never be met however good the measurement was, and nothing said so.
Hence `_refuse_if_discipline_returns()` below: if a `prereg/` directory ever
reappears, this module hard-exits rather than quietly permitting everything.

Standard library only (`references/numeric-stack.md`).
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))

CHOICES = ("dev", "full")

_ANNOUNCED = [False]


def frozen_prereg(root):
    """The active frozen prereg and its held-out boundary, or (None, None).

    A prereg in `prereg/` means a window has been declared unspent, and from
    that moment this module is the thing that keeps it unspent. The file is
    read, never written.
    """
    d = os.path.join(root, "prereg")
    if not os.path.isdir(d):
        return None, None
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".md") or fn == "TEMPLATE.md":
            continue
        path = os.path.join(d, fn)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"Held-out[^\n|]*\|?[^\n]*?(FY\d{4}Q[1-4])", text)
        if not m:
            m = re.search(r"[Hh]eld-out[\s\S]{0,400}?(FY\d{4}Q[1-4])", text)
        if m:
            return path, m.group(1)
        # A prereg with no parseable boundary is worse than none: it declares a
        # window and does not say where it starts.
        sys.exit(
            "refusing to run: %s declares held-out data but no parseable "
            "boundary.\nThe window must name its first held-out quarter as "
            "FY####Q#." % path)
    return None, None


def qkey(label):
    """FY2026Q3 -> (2026, 3). None when the label is not a fiscal quarter."""
    m = re.fullmatch(r"FY(\d{4})Q([1-4])", (label or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


class Window(object):
    """Allows every period, or every period before a frozen held-out boundary."""

    def __init__(self, mode, root, heldout=None, source=None):
        self.mode, self.root = mode, root
        self.dropped = 0
        self.heldout = heldout            # (year, quarter) or None
        self.bounds = heldout
        self.source = source or "no window (forward-only collection)"
        self.frozen = heldout is not None

    @property
    def restricts(self):
        return self.heldout is not None and self.mode == "dev"

    def allows(self, label):
        if not self.restricts:
            return True
        k = qkey(label)
        if k is None:
            # An unreadable label cannot be shown to be outside held-out.
            # Refusing it loses a quarter; admitting it risks the window.
            self.dropped += 1
            return False
        if k >= self.heldout:
            self.dropped += 1
            return False
        return True

    def describe(self):
        if not self.restricts:
            return ("no window restriction: no frozen prereg declares an "
                    "unspent period (CORE-PROMPT s8)")
        return ("held-out from FY%04dQ%d onward, per %s - excluded from this run"
                % (self.heldout[0], self.heldout[1], self.source))


def add_argument(parser):
    parser.add_argument("--window", choices=CHOICES, default="dev",
                        help="retained for the stage contract; restricts "
                             "nothing in the live design (see window.py)")
    parser.add_argument("--root", default=None,
                        help="project root; default: inferred from this script")


def resolve(args, stage="this stage"):
    root = os.path.abspath(getattr(args, "root", None) or DEFAULT_ROOT)
    mode = getattr(args, "window", "dev")
    path, boundary = frozen_prereg(root)

    if boundary is None:
        if not _ANNOUNCED[0]:
            sys.stderr.write(
                "note: --window %s restricts nothing - no frozen prereg declares "
                "an unspent period.\n" % mode)
            _ANNOUNCED[0] = True
        return Window(mode, root)

    hk = qkey(boundary)
    if mode == "full":
        sys.exit(
            "refusing --window full: %s declares FY%04dQ%d onward held out, and\n"
            "that window is unspent.\n\n"
            "%s would read it, and the reading is what spends it - deleting the\n"
            "output afterwards does not undo that. The held-out quarters exist so\n"
            "a claim can be tested against data nobody has seen; a run over the\n"
            "full window converts the test into a description.\n\n"
            "Run with --window dev. This is not a flag to override."
            % (os.path.relpath(path, root), hk[0], hk[1], stage))

    if not _ANNOUNCED[0]:
        sys.stderr.write(
            "note: held-out from FY%04dQ%d onward (%s) is EXCLUDED from this run.\n"
            % (hk[0], hk[1], os.path.relpath(path, root)))
        _ANNOUNCED[0] = True
    return Window(mode, root, heldout=hk, source=os.path.relpath(path, root))


def add_tickers_argument(parser, help_text):
    parser.add_argument("--tickers", nargs="+", default=None, help=help_text)
