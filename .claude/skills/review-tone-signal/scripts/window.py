#!/usr/bin/env python3
"""The `--window {dev,full}` contract shared by every pipeline stage.

CORE-PROMPT s1.4 forbids inspecting held-out quarters before the prereg is
frozen, and s6 fixes held-out at the final 8 complete quarters. The orchestrator
invokes every pre-freeze stage with `--window dev` (`.claude/commands/
review-tone-run.md`), and that invocation is the *mechanical* half of the
guarantee: the discipline is not meant to depend on anyone remembering it.

A flag that parses and does nothing is worse than no flag at all. It converts a
silent guarantee into a late crash, because `pipeline_state.heldout_contamination`
aborts the run with exit 2 once the held-out quarters are already on disk - by
which time the contamination has happened and no later discipline undoes it. So
every stage that reads or writes quarter-labelled data resolves its window here
and genuinely restricts itself to it.

Two deliberate choices:

  * **The default is `dev`, not `full`.** The two mistakes are not symmetric.
    Defaulting to `dev` and wanting `full` costs a re-run; defaulting to `full`
    before the freeze costs the held-out window permanently. A stage run by hand
    with no flag therefore gets the conservative window.

  * **`--window full` is refused while the prereg is not frozen.** This is what
    makes the flag binding rather than advisory: before the freeze there is no
    argument that produces held-out output from a pre-freeze stage, so the rule
    cannot be worked around per run. The stages that legitimately see the full
    window (`backtest.py`, `degrade.py`) sit behind the `B1.prereg_frozen` gate,
    so they only ever ask for `full` once it is true.

The boundary itself is never written down twice: it is read from the active
prereg (or, when none is frozen, `prereg/TEMPLATE.md`) through
`pipeline_state.parse_window`, which is the same function the contamination
check uses. One source of truth, so the filter and the check cannot disagree.

Standard library only (`references/numeric-stack.md`).
"""

import os
import sys

import pipeline_state as ps

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))

CHOICES = ("dev", "full")


class Window(object):
    """A resolved window, and the predicate that enforces it."""

    def __init__(self, mode, bounds, source, frozen, heldout_start):
        self.mode = mode
        self.bounds = bounds              # (start, end) as (year, q), or None
        self.source = source
        self.frozen = frozen
        self.heldout_start = heldout_start
        self.dropped = 0                  # quarters/reviews refused, for the log

    @property
    def restricts(self):
        return self.mode == "dev" and self.bounds is not None

    def allows(self, label):
        """Is this fiscal-quarter label inside the window?

        An unparseable label is NOT allowed through under `dev`. The fiscal
        labels are on the company's own calendar while the window is in calendar
        quarters (the boundary caveat recorded in
        `pipeline_state.heldout_contamination`), so an unreadable label cannot be
        shown to be outside held-out. Refusing it loses a quarter; admitting it
        risks the window.
        """
        if not self.restricts:
            return True
        k = ps.qkey(label)
        if k is None:
            self.dropped += 1
            return False
        ok = self.bounds[0] <= k <= self.bounds[1]
        if not ok:
            self.dropped += 1
        return ok

    def describe(self):
        if self.bounds is None:
            return ("window %s - no window table found in %s, so nothing is "
                    "restricted" % (self.mode, self.source))
        return ("window %s - %dQ%d..%dQ%d (from %s)%s"
                % ((self.mode,) + self.bounds[0] + self.bounds[1]
                   + (self.source,
                      "" if self.restricts else " - full window, no restriction")))


def add_argument(parser):
    """Add the stage contract's `--window` flag."""
    parser.add_argument("--window", choices=CHOICES, default="dev",
                        help="dev: restrict reads and writes to the prereg's "
                             "development window, leaving the held-out quarters "
                             "untouched (CORE-PROMPT s1.4). full: the whole "
                             "window, allowed only once the prereg is frozen. "
                             "Default dev, because defaulting to full before "
                             "the freeze would spend the held-out window.")
    parser.add_argument("--root", default=None,
                        help="project root; default: inferred from this script")


def resolve(args, stage="this stage"):
    """Resolve `--window` into a Window, or exit if the request is not allowed."""
    root = os.path.abspath(getattr(args, "root", None) or DEFAULT_ROOT)
    pr = ps.detect_prereg(root)
    win = pr.get("window") or {}
    frozen = bool(pr.get("frozen"))
    source = win.get("source") or "no prereg found"
    heldout_start = (win.get("heldout") or (None,))[0]

    mode = getattr(args, "window", "dev")

    if mode == "full" and not frozen:
        sys.exit(
            "refusing --window full: the prereg is not frozen (%s).\n\n"
            "%s is a pre-freeze stage. Before the freeze, the final %d quarters\n"
            "are held out and may not be inspected, plotted, described or\n"
            "reasoned about (CORE-PROMPT s1.4, s6). Running it over the full\n"
            "window would write them to disk, which is the contamination\n"
            "itself - and it is not undone by deleting the files afterwards.\n\n"
            "Run it with --window dev. The stages that legitimately see the\n"
            "full window (backtest.py, degrade.py) sit behind the\n"
            "B1.prereg_frozen gate and will be allowed once it is met.\n\n"
            "This is not a flag to override. Freezing the prereg is the step\n"
            "that opens the full window, and it is a deliberate human act."
            % (source, stage, ps.HELDOUT_QUARTERS))

    bounds = win.get("development") if mode == "dev" else win.get("full")

    if mode == "dev" and bounds is None:
        sys.exit(
            "refusing to run --window dev with no development window: %s\n"
            "carries no parseable window table, so there is no boundary to\n"
            "restrict to and nothing would stop held-out quarters being\n"
            "written. Fix the window table (s3) before running %s."
            % (source, stage))

    return Window(mode, bounds, source, frozen, heldout_start)


def add_tickers_argument(parser, help_text):
    """The stage contract's `--tickers T [T ...]`.

    The orchestrator builds every stage call as `--tickers T1 T2 ...`
    (`.claude/commands/review-tone-run.md`), so that spelling is the contract.
    """
    parser.add_argument("--tickers", nargs="+", metavar="TICKER",
                        help=help_text)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="print the resolved window")
    add_argument(p)
    print(resolve(p.parse_args(), "window.py").describe())
