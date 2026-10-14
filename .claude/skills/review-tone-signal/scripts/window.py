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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))

CHOICES = ("dev", "full")

_ANNOUNCED = [False]


def _refuse_if_discipline_returns(root):
    """Hard-exit if held-out discipline has been reintroduced without this module.

    If someone restores a `prereg/` directory, they mean to hold data back. This
    module would then be permitting exactly what it used to prevent, and the
    only signal would be a line of stdout nobody reads. Stop instead.
    """
    prereg = os.path.join(root, "prereg")
    if os.path.isdir(prereg) and any(
            f.endswith(".md") and f != "TEMPLATE.md"
            for f in os.listdir(prereg)):
        sys.exit(
            "refusing to run: %s exists, so held-out discipline has been\n"
            "reintroduced, but window.py no longer enforces it. It was made\n"
            "inert when the project moved to forward-only collection, where\n"
            "there is no unspent period in the data to protect.\n\n"
            "Restoring the prereg means restoring the enforcement with it.\n"
            "The archived implementation is at\n"
            "  archive/historical-harness/scripts/pipeline_state.py\n"
            "(detect_prereg, parse_window, heldout_contamination).\n\n"
            "This is a refusal and not a warning, because a guard that permits\n"
            "everything while looking like a guard is worse than no guard."
            % prereg)


class Window(object):
    """Accepts every period. Kept so callers need not branch on the design."""

    def __init__(self, mode, root):
        self.mode, self.root = mode, root
        self.dropped = 0
        # Callers record the window they ran under in their manifests. There is
        # no bound to record, and None says that honestly where a tuple would
        # imply a restriction that is not applied.
        self.bounds = None
        self.source = "no window (forward-only collection)"
        self.frozen = False

    @property
    def restricts(self):
        return False

    def allows(self, label):          # noqa: ARG002 - every label is allowed
        return True

    def describe(self):
        return ("no window restriction: the live monitor collects forward, so "
                "there is no held-out period in the data (CORE-PROMPT s8)")


def add_argument(parser):
    parser.add_argument("--window", choices=CHOICES, default="dev",
                        help="retained for the stage contract; restricts "
                             "nothing in the live design (see window.py)")
    parser.add_argument("--root", default=None,
                        help="project root; default: inferred from this script")


def resolve(args, stage="this stage"):
    root = os.path.abspath(getattr(args, "root", None) or DEFAULT_ROOT)
    _refuse_if_discipline_returns(root)
    mode = getattr(args, "window", "dev")
    if not _ANNOUNCED[0]:
        sys.stderr.write(
            "note: --window %s accepted but restricts nothing. The live monitor\n"
            "      collects forward, so no period is held out; %s reads every\n"
            "      period it has. CORE-PROMPT s8 governs what may be claimed.\n"
            % (mode, stage))
        _ANNOUNCED[0] = True
    return Window(mode, root)


def add_tickers_argument(parser, help_text):
    parser.add_argument("--tickers", nargs="+", default=None, help=help_text)
