#!/usr/bin/env python3
"""Stages B3 (calibration) and B4 (scoring) - tone scores for extracted reviews.

Scores `data/{TICKER}/reviews.csv` into `data/{TICKER}/scores.csv` under the
frozen rubric in `references/tone-rubric-v1.md`.

Three things this script exists to make structurally impossible:

F8 - STAR-RATING CIRCULARITY. `reviews.csv` carries `rating`, and the scorer must
  never see it. Prompts are built by `build_prompt(review_id, title, text)`, which
  takes three strings and has no access to the row. The template is asserted at
  import time to contain no rating or star placeholder, and `--self-test` proves
  that two rows differing only in `rating` produce byte-identical prompts. Scoring
  text while seeing the star would rebuild the star average and call it novel.

F10 - SCORER DRIFT. Every row stamps `rubric_version` and the model ID the API
  actually answered with, not the one that was requested. The rubric file's SHA-256
  is pinned in this script; a silent edit to the frozen rubric stops the run and
  tells you to create v2. `scores.csv` is APPEND-ONLY: re-scoring under a new model
  adds rows beside the old ones and downstream work selects on
  `(rubric_version, scorer_model_id)`. Nothing is ever overwritten.

F13 - SILENT SCORING FAILURES. Malformed output is retried once, then written as a
  row with `scoring_failed=True` and `tone` empty. Never dropped, never guessed at.
  Counts go to `scoring_report.json`, overall and per fiscal quarter, and a run
  losing more than `--max-failure-rate` of its reviews exits non-zero.

Reviews are scored INDEPENDENTLY: one review per request, a single user message,
no conversation history, temperature 0. `--workers` runs several of those
independent requests at once; it never batches reviews into one call, so batch
ordering cannot leak one review's context into the next. `--batch` sends the
same one-review requests through the Batches API, which is also one review per
request - a batch of 5,794 requests, not 5,794 reviews in one request.

THE THREE COST CHANGES, AND THE CONSTRAINT OVER ALL OF THEM
-----------------------------------------------------------
Scoring measured $0.0040/review on 2026-10-17: $5 of credit bought 1,241
reviews and ran out mid-run on Weber, leaving 5,794 unscored at $23. Three
changes cut that (`--sample-per-quarter`, `--batch`, prompt caching), and the
constraint that outranks all three is that the scorer must keep producing the
SAME SCORES FOR THE SAME INPUT. The calibration gate passed for
(v1, claude-haiku-4-5-20251001) at temperature 0 - kappa 0.811, adjacent 0.900
against 100 human labels - and it covers that scorer and no other.

So the request body is built in ONE place, `request_params()`, for both the
synchronous and the batch path, and `validate_request_params()` refuses six ways
the body could stop being that scorer: a non-zero temperature, more than one
user message, system text that is not the frozen rubric extraction byte for
byte, per-review text that leaked into the system block, a cache breakpoint
anywhere but at the end of the stable prefix, and a changed output schema. A
guard is not enough on its own - `--self-test` trips every one of them, and
re-scoring 40 already-scored reviews through the new path is what actually
establishes agreement.

`--sample-per-quarter N` is a METHODOLOGY change and is built as one. The draw
is a function of (review ids, quarter, seed) and nothing else - never of a
score, which would make it selection - seeded from `env_stamp.stage_seed`
("scoring_sample") so two sessions cannot each invent a seed, and recorded in
the manifest and the scoring report with N, the seed, the drawn ids per quarter
and the population each was drawn from. A quarter sampled down to 150 and a
quarter that genuinely held 150 must be tellable apart, so the population and
its id-set hash travel with every number. A quarter shorter than N is taken
whole; a quarter under MIN_REVIEWS_PER_QUARTER is insufficient exactly as it
always was, never silently dropped.

Prompt caching puts `cache_control` on the system block carrying the rubric.
**Measured 2026-10-18: it does not engage on this model.** Haiku 4.5's minimum
cacheable prefix is 4,096 tokens and the rubric prompt is ~940, so the marker is
accepted and silently caches nothing. Every run reports
`usage.cache_read_input_tokens` and a `cache.engaged` verdict rather than
assuming the discount; `--require-cache` turns a non-engaging cache into a
refusal for the day a model with a lower minimum makes the saving real.

Downstream note, deliberately not fixed here: `aggregate.py` refuses a reviews
row with no score row ("run the scoring stage to completion"), so a sampled
company cannot be aggregated without a change to that file. See the
`SAMPLING_DOWNSTREAM` note below; this script does not edit `aggregate.py`.

Script, not a subagent. The rubric fixes the model, temperature 0, one review per
call and no history. A Claude Code subagent runs on the session's model with the
session's system prompt and tools, carries context across turns, and exposes no
temperature - so it can satisfy none of those constraints and cannot stamp a
reproducible `scorer_model_id`. CORE-PROMPT s1.3 also puts every reported number
in a script; kappa and the agreement rates are computed here.

Note on the mandated retry: at temperature 0 a retry sends an identical prompt and
mostly returns an identical answer, so it recovers truncation and transport
failures rather than systematic malformation. The rubric mandates it; it is
implemented as written, and transport failures are counted separately so a network
problem is never read as a model problem.

Dependencies: standard library only. API calls go through `urllib.request`; the
Anthropic SDK is deliberately not used, so this script has no third-party imports.
The only external requirement is the `ANTHROPIC_API_KEY` environment variable.

Usage
-----
    # B4 - score
    export ANTHROPIC_API_KEY=...
    python3 score_reviews.py HELE
    python3 score_reviews.py HELE --limit 200 --workers 4

    # B4 - score a seeded per-quarter subsample through the Batches API
    python3 score_reviews.py HELE --sample-per-quarter 50 --batch

    # B3 - calibration, step 1: draw a blind labelling sheet
    python3 score_reviews.py HELE --calibrate

    # B3 - calibration, step 2: gate on the returned human labels
    python3 score_reviews.py HELE --calibrate --labels data/HELE/calibration_labels.csv

    # offline verification of the plumbing, writes synthetic tone
    python3 score_reviews.py SAMPLE --self-test --outdir /tmp/x

Exit codes: 0 pass, 1 blocked (gate unmet, failure rate exceeded, drift detected),
2 usage or input error.
"""

import argparse
import concurrent.futures
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aggregate as aggmod  # noqa: E402
import env_stamp  # noqa: E402
import window as windowmod  # noqa: E402

# --------------------------------------------------------------------------
# Refusal instrumentation (CONTRACTS.md s5)
# --------------------------------------------------------------------------

# Set by refusal_audit.py to the path of its log. When present, every refusal
# records the source line it fired from, so the audit can assert that each one
# has a test that trips it. Unset in normal use, where it costs nothing.
REFUSAL_LOG_ENV = "SCORE_REFUSAL_LOG"


def _record_refusal():
    path = os.environ.get(REFUSAL_LOG_ENV)
    if not path:
        return
    try:
        import traceback
        st = traceback.extract_stack()
        here = os.path.abspath(__file__)
        for fr in reversed(st):
            # Skip this helper AND die() itself, or every refusal records die()'s
            # own line - one distinct "site" for all of them, which reads as a
            # working audit finding nothing.
            if fr.name in ("_record_refusal", "die"):
                continue
            if os.path.abspath(fr.filename) == here:
                with open(path, "a", encoding="utf-8") as f:
                    f.write("%d\n" % fr.lineno)
                return
    except Exception:            # noqa: BLE001 - never let auditing break a run
        pass

# --------------------------------------------------------------------------
# Frozen rubric
# --------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
RUBRIC_DIR = os.path.join(os.path.dirname(HERE), "references")

# Pinned so that an in-place edit to a frozen rubric stops the pipeline instead of
# silently changing every score produced under the same version label.
RUBRIC_SHA256 = {
    "v1": "4ce22feb1c52b4b4f2063a774fd542abfc59211667c2c5c349af30a9bdc48b14",
}

# Sections of the rubric sent to the scorer, in order. The calibration-gate and
# scorer-configuration sections are deliberately withheld: they are instructions
# to the operator, not to the scorer.
PROMPT_SECTIONS = ("Scale", "Rules", "Tags", "Output format")

TONE_VALUES = (-2, -1, 0, 1, 2)
TAGS = ("seller_or_shipping_only", "defect", "price_complaint")
RESPONSE_KEYS = frozenset(("review_id", "tone") + TAGS)

# The same contract as the rubric's "Output format" section, in the shape the
# API enforces. Kept beside RESPONSE_KEYS so the two cannot drift apart - the
# parser checks exactly these keys, and a schema that allowed others would make
# the parser the only thing catching it.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": dict({"review_id": {"type": "string"},
                        "tone": {"type": "integer", "enum": list(TONE_VALUES)}},
                       **{t: {"type": "boolean"} for t in TAGS}),
    "required": sorted(RESPONSE_KEYS),
    "additionalProperties": False,
}

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_BASE_URL = "https://api.anthropic.com"
MAX_TEXT_CHARS = 6000
RETRY_STATUS = frozenset((408, 409, 429, 500, 502, 503, 504, 529))

SCORE_FIELDS = ["review_id", "tone", "seller_or_shipping_only", "defect",
                "price_complaint", "scoring_failed", "rubric_version",
                "scorer_model_id", "scored_at"]

GATE_KAPPA = 0.60
GATE_ADJACENT = 0.85

STUB_MODEL_ID = "stub:deterministic-v1"
SELFTEST_MIN = 300

# --------------------------------------------------------------------------
# Cost model
# --------------------------------------------------------------------------
#
# Published list prices, USD per million tokens, recorded 2026-10-18 with the
# date so a stale figure is visible rather than inferred. A model absent from
# this table yields a null cost rather than a guessed one: an invented price is
# worse than no price, because it reads like a measurement.
PRICES_USD_PER_MTOK = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}
PRICES_RECORDED = "2026-10-18, Anthropic published list prices"

CACHE_WRITE_MULTIPLIER = 1.25     # 5-minute TTL
CACHE_READ_MULTIPLIER = 0.10
BATCH_MULTIPLIER = 0.50           # Batches API: 50% off every token

# The minimum cacheable prefix is MODEL-DEPENDENT (512-4096 tokens) and a prefix
# below it silently does not cache: no error, just cache_creation_input_tokens
# and cache_read_input_tokens both zero. Recorded here so the run can say
# whether the discount it is claiming is even available, instead of discovering
# it from a bill. Measured 2026-10-18: the rubric prompt is ~940 tokens, so on
# Haiku 4.5 (minimum 4,096) caching cannot engage and does not.
MIN_CACHEABLE_PREFIX_TOKENS = {
    "claude-haiku-4-5-20251001": 4096,
    "claude-haiku-4-5": 4096,
}

# --------------------------------------------------------------------------
# SAMPLING_DOWNSTREAM - flagged, not worked around
# --------------------------------------------------------------------------
#
# `aggregate.py` reads every row of reviews.csv and refuses the company if any
# row has no matching row in scores.csv:
#
#     "%d reviews in reviews.csv have no row in scores.csv - run the scoring
#      stage to completion before aggregating"
#
# That is correct for a full run and is exactly wrong for a sampled one, where
# unscored rows are the intended outcome. So `--sample-per-quarter` cannot be
# aggregated without a change to `aggregate.py`, which this task is not
# permitted to edit. What it needs, stated precisely so whoever owns that stage
# can make it in one place:
#
#   1. `load_quarters` must read the `scoring_sample` block from manifest.json
#      and, when present, treat a review whose id is not in that quarter's
#      `drawn_review_ids` as DELIBERATELY UNSCORED rather than as a pipeline
#      inconsistency - while still refusing a review that was drawn and has no
#      score row, because that one is the original bug.
#   2. `quarters.csv` needs one more column, `n_population`, beside the existing
#      `n_reviews`. `n_reviews` already reflects what was scored, which is
#      right; without `n_population` a 150-review sampled quarter and a quarter
#      that genuinely held 150 are indistinguishable, and any coverage figure
#      computed from `n_reviews` reads as though the whole quarter were scored.
#
# Until both land, a sampled company scores but does not aggregate, and this
# script says so at the end of every sampled run rather than leaving it to be
# discovered at the next stage.
SAMPLING_DOWNSTREAM = (
    "aggregate.py refuses a reviews row with no score row, so a sampled company "
    "will not aggregate until it learns to read the manifest's scoring_sample "
    "block and carries an n_population column beside n_reviews. See "
    "SAMPLING_DOWNSTREAM in score_reviews.py.")


def rubric_path(version):
    return os.path.join(RUBRIC_DIR, "tone-rubric-%s.md" % version)


def load_rubric(version):
    """Return the rubric text, refusing to run against an altered frozen file."""
    path = rubric_path(version)
    if not os.path.exists(path):
        die("no rubric at %s" % path)
    with open(path, "rb") as f:
        raw = f.read()
    got = hashlib.sha256(raw).hexdigest()
    want = RUBRIC_SHA256.get(version)
    if want is None:
        die("rubric %s has no pinned hash in RUBRIC_SHA256.\n"
            "A new rubric version must be pinned here before it can score." % version)
    if got != want:
        die("FROZEN RUBRIC CHANGED\n\n"
            "  %s\n  expected sha256 %s\n  found    sha256 %s\n\n"
            "tone-rubric-%s.md is frozen: every score ever stamped '%s' was produced\n"
            "under the pinned text. Revert the file, or create tone-rubric-v2.md and\n"
            "pin its hash - never edit a frozen rubric in place (F10)."
            % (path, want, got, version, version))
    return raw.decode("utf-8")


def extract_sections(rubric_text, headings):
    """Pull `## <heading>` blocks out of the rubric verbatim, in the given order.

    Verbatim extraction plus the pinned hash is what makes 'frozen' mechanical:
    the scorer is never shown a paraphrase of the rubric.
    """
    blocks = {}
    current, buf = None, []
    for line in rubric_text.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            if current is not None:
                blocks[current] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        blocks[current] = "\n".join(buf).strip()

    out = []
    for h in headings:
        if h not in blocks:
            die("rubric section '## %s' not found - the rubric structure changed" % h)
        out.append("## %s\n\n%s" % (h, blocks[h]))
    return "\n\n".join(out)


def build_system_prompt(rubric_text):
    return ("You score one Amazon customer review against the rubric below. "
            "Follow it exactly. Respond with the JSON object only, no prose, no "
            "code fences.\n\n" + extract_sections(rubric_text, PROMPT_SECTIONS))


def build_system_blocks(rubric_text, cache=True):
    """The system field as a list of one text block, optionally cached.

    The rubric is 833 of roughly 940 input tokens on every call and is
    byte-identical every time, so it is the whole of the cacheable prefix.
    Render order is `tools` -> `system` -> `messages`: the rubric is first and
    stable, the per-review text goes after the breakpoint in the user message,
    and `validate_request_params` refuses the inverse.

    A string and a single text block are the same prompt to the API - the
    string form is normalised to exactly this - so turning caching on does not
    change what the scorer reads. That is an assertion about the API, not a
    licence to assume it: `--verify-prompt-identity` measures both forms through
    count_tokens, and the 40-review agreement re-score is what establishes it
    for the scores themselves.
    """
    block = {"type": "text", "text": build_system_prompt(rubric_text)}
    if cache:
        # Ephemeral, 5-minute TTL. Whether it engages is MODEL-DEPENDENT and is
        # reported from usage rather than assumed - see MIN_CACHEABLE_PREFIX_TOKENS.
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def request_params(model, system_blocks, prompt, max_tokens):
    """The one request body, for the synchronous path and the batch path alike.

    Both paths call this. If they built their own, the batch path could drift
    into being a different scorer - a different temperature, a reordered system
    field, a loosened schema - and the calibration gate, which passed for one
    exact scorer, would silently stop covering the run. Nothing that reaches
    the API is assembled anywhere else.
    """
    params = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system_blocks,
        # Exactly one user message. No assistant turns, no prior reviews.
        "messages": [{"role": "user", "content": prompt}],
        # The rubric's output contract, enforced by the API rather than
        # asked for in prose. Measured 2026-10-14: without it the model
        # wrapped correct JSON in a ```json fence on 10 of 10 reviews,
        # despite the system prompt saying "no code fences", and the strict
        # parser rejected every one. Loosening the parser to strip fences
        # was the alternative and is worse: it starts the slide toward
        # rescuing JSON out of prose, which is guessing. This makes the
        # malformed case impossible instead of recoverable.
        "output_config": {"format": {"type": "json_schema",
                                     "schema": RESPONSE_SCHEMA}},
    }
    validate_request_params(params)
    return params


def validate_request_params(params):
    """Refuse six ways the body could stop being the calibrated scorer.

    Every one of these is exact rather than heuristic. A near-miss guard gets
    switched off after it fires on something innocent, and then it guards
    nothing at all (CONTRACTS.md s5) - so none of these matches on wording. In
    particular there is no lexical scan for "rating" anywhere near the review
    text: a reviewer writing "the rating system is broken" would trip it, it
    would be removed, and F8 would lose its request-level check. The system text
    is compared byte for byte against the frozen extraction instead.
    """
    if params.get("temperature") != 0:
        die("temperature is %r, not 0.\n"
            "The calibration gate passed for this rubric at temperature 0. Any\n"
            "other value is a different scorer and the gate does not cover it."
            % (params.get("temperature"),))

    msgs = params.get("messages") or []
    if len(msgs) != 1 or msgs[0].get("role") != "user":
        die("the request carries %d message(s) %s, not exactly one user "
            "message.\nReviews are scored independently so that batch ordering\n"
            "cannot leak one review's context into the next; a second turn\n"
            "breaks that and is not the scorer the gate covers."
            % (len(msgs), [m.get("role") for m in msgs]))

    blocks = params.get("system") or []
    if not blocks:
        die("the request carries no system block, so the scorer would be shown\n"
            "no rubric at all and would answer from whatever it already thinks\n"
            "a tone score is.")
    want = expected_system_text()
    got = "".join(b.get("text", "") for b in blocks)

    # Render order is tools -> system -> messages. The per-review text must sit
    # AFTER the breakpoint, or the cached prefix changes on every call and the
    # cache is a pure write surcharge on bytes nothing ever reads back.
    #
    # Checked BEFORE the byte-equality check below, and that order is
    # load-bearing: a system block with the review appended also fails equality,
    # so with the checks the other way round this one could never fire and
    # would be a guard that cannot fail (CONTRACTS.md s5). The audit caught
    # exactly that here. Equality alone would still refuse the request - it
    # would just report "the text changed" instead of naming the mistake.
    user_text = msgs[0].get("content") or ""
    if isinstance(user_text, str) and user_text and user_text in got:
        die("the per-review text appears inside the system block.\n\n"
            "The rubric is the stable prefix and the review is the volatile\n"
            "suffix. With the review above the cache breakpoint the prefix\n"
            "changes on every call: nothing is ever read back, every request\n"
            "pays the write premium, and the 'saving' is a surcharge.")

    if got != want:
        die("SYSTEM TEXT IS NOT THE FROZEN RUBRIC EXTRACTION\n\n"
            "  expected %d chars, sha256 %s\n  got      %d chars, sha256 %s\n\n"
            "Every score ever stamped with this rubric version was produced\n"
            "under the extraction build_system_prompt() returns. A paraphrase,\n"
            "a reordering or an extra sentence is a different prompt, and the\n"
            "calibration gate covers the original and nothing else (F10)."
            % (len(want), hashlib.sha256(want.encode()).hexdigest()[:12],
               len(got), hashlib.sha256(got.encode()).hexdigest()[:12]))

    for i, b in enumerate(blocks):
        if "cache_control" in b and i != len(blocks) - 1:
            die("cache_control is on system block %d of %d, not the last one.\n"
                "The breakpoint has to close the stable prefix. Anything after\n"
                "it is uncached, so a breakpoint in the middle silently drops\n"
                "the rest of the rubric out of the cache." % (i + 1, len(blocks)))

    schema = (((params.get("output_config") or {}).get("format") or {})
              .get("schema"))
    if schema != RESPONSE_SCHEMA:
        die("output_config schema is not RESPONSE_SCHEMA.\n"
            "The schema and the strict parser are the same contract written\n"
            "twice; a schema that allowed other keys would make the parser the\n"
            "only thing catching it, and a looser one would let a differently\n"
            "shaped answer through as if it were a score.")


_EXPECTED_SYSTEM = []
_RUBRIC_VERSION = ["v1"]


def set_expected_system(rubric_text, version="v1"):
    """Pin the system text this process may send, from the hash-checked rubric.

    Called once, with the text `load_rubric` has already verified against the
    pinned sha256. The validator compares against this rather than re-deriving
    it per request, so a request cannot be validated against itself.
    """
    del _EXPECTED_SYSTEM[:]
    _EXPECTED_SYSTEM.append(build_system_prompt(rubric_text))
    _RUBRIC_VERSION[0] = version
    return _EXPECTED_SYSTEM[0]


def expected_system_text():
    if not _EXPECTED_SYSTEM:
        # Reached only if a caller builds a request before loading the rubric,
        # which would mean the pinned-hash check had been skipped.
        die("no rubric has been loaded, so there is nothing to validate the\n"
            "system text against. load_rubric() runs the pinned-hash check and\n"
            "must happen before any request is built (F10).")
    return _EXPECTED_SYSTEM[0]


# --------------------------------------------------------------------------
# Prompt construction - the F8 boundary
# --------------------------------------------------------------------------

PROMPT_TEMPLATE = """review_id: {review_id}
title: {title}
text: {text}"""

# Import-time guard: the template cannot carry a star field even by accident.
for _banned in ("rating", "star", "score", "helpful", "verified"):
    if _banned in PROMPT_TEMPLATE.lower():
        raise AssertionError(
            "PROMPT_TEMPLATE contains '%s' - the scorer must see only the review "
            "id, title and text (F8)." % _banned)


def build_prompt(review_id, title, text):
    """Render the user message from exactly three strings.

    Takes strings, never a review row, so `rating` has no path into the prompt.
    """
    for name, val in (("review_id", review_id), ("title", title), ("text", text)):
        if not isinstance(val, str):
            raise TypeError("%s must be str, got %r" % (name, type(val)))
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
    prompt = PROMPT_TEMPLATE.format(review_id=review_id, title=title, text=text)
    # Reconstruction check: the message is the template with three substitutions
    # and nothing else, so no fourth field can have been appended downstream.
    assert prompt == PROMPT_TEMPLATE.format(
        review_id=review_id, title=title, text=text)
    return prompt


def prompt_for_row(row):
    return build_prompt(row["review_id"], row.get("title") or "",
                        row.get("text") or "")


# --------------------------------------------------------------------------
# Response validation
# --------------------------------------------------------------------------

class Malformed(Exception):
    """Scorer output that does not match the rubric's output format."""


class Transport(Exception):
    """The call did not complete. Not the model's fault; counted separately."""


def parse_response(raw, review_id):
    """Strict parse. No regex rescue of JSON out of prose - that is guessing."""
    s = (raw or "").strip()
    if not s.startswith("{"):
        raise Malformed("response is not a bare JSON object: %.120r" % s)
    try:
        obj = json.loads(s)
    except ValueError as e:
        raise Malformed("not valid JSON: %s" % e)
    if not isinstance(obj, dict):
        raise Malformed("top level is %s, not an object" % type(obj).__name__)

    keys = set(obj)
    if keys != RESPONSE_KEYS:
        raise Malformed("keys %s, expected %s"
                        % (sorted(keys), sorted(RESPONSE_KEYS)))
    if obj["review_id"] != review_id:
        raise Malformed("review_id %r, asked for %r" % (obj["review_id"], review_id))

    tone = obj["tone"]
    if isinstance(tone, bool) or not isinstance(tone, int):
        raise Malformed("tone %r is not an integer" % (tone,))
    if tone not in TONE_VALUES:
        raise Malformed("tone %r outside %s" % (tone, list(TONE_VALUES)))

    for t in TAGS:
        if not isinstance(obj[t], bool):
            raise Malformed("%s %r is not a boolean" % (t, obj[t]))
    return obj


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

class UsageTally(object):
    """Token usage, summed across calls, so cost is measured and not estimated.

    `cache_read_input_tokens` is the only honest answer to "is caching
    happening". The minimum cacheable prefix is model-dependent and a prefix
    below it caches nothing while raising no error, so a run that assumed the
    discount from the presence of the marker would report a saving it never got.
    """

    FIELDS = ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")

    def __init__(self):
        self.totals = dict((f, 0) for f in self.FIELDS)
        self.n_calls = 0
        self.n_with_usage = 0

    def add(self, usage):
        self.n_calls += 1
        if not usage:
            return
        self.n_with_usage += 1
        for f in self.FIELDS:
            v = usage.get(f)
            if isinstance(v, int):
                self.totals[f] += v

    def as_dict(self):
        d = dict(self.totals)
        d["n_calls"] = self.n_calls
        d["n_calls_reporting_usage"] = self.n_with_usage
        return d


class ApiBackend(object):
    """One independent Messages API call per review, temperature 0, no history.

    ANTHROPIC_BASE_URL in the environment is deliberately ignored. In a Claude
    Code session that variable points at a gateway that does not accept
    `x-api-key`, and silently inheriting it produces confusing auth failures.
    Override with --base-url when a proxy is genuinely wanted.
    """

    kind = "api"
    batched = False

    def __init__(self, model, system, base_url=DEFAULT_BASE_URL,
                 max_tokens=256, transport_retries=4, timeout=120):
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            die("ANTHROPIC_API_KEY is not set.\n\n"
                "Scoring makes one API call per review. Set the key in your own\n"
                "shell and re-run:\n\n"
                "    export ANTHROPIC_API_KEY=...\n\n"
                "To check the plumbing without an API key, use --self-test.")
        self._key = key
        self.model = model
        # A list of system blocks (see build_system_blocks), carrying the cache
        # breakpoint. A bare string is accepted so older callers keep working.
        self.system = ([{"type": "text", "text": system}]
                       if isinstance(system, str) else system)
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/v1/messages"
        self.max_tokens = max_tokens
        self.transport_retries = transport_retries
        self.timeout = timeout
        self.resolved_models = Counter()
        self.usage = UsageTally()

    def headers(self):
        return {
            "x-api-key": self._key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "user-agent": "review-tone-signal/1.0 (research; tone scorer)",
        }

    def params_for(self, prompt):
        return request_params(self.model, self.system, prompt, self.max_tokens)

    def __call__(self, prompt):
        body = json.dumps(self.params_for(prompt)).encode("utf-8")
        assert json.loads(body)["messages"].__len__() == 1

        req = urllib.request.Request(self.url, data=body,
                                     headers=self.headers())

        payload = None
        last = None
        delay = 1.0
        for attempt in range(self.transport_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                last = "HTTP %s %s" % (e.code, detail)
                # 400/401/403/404 will not get better by waiting.
                if e.code not in RETRY_STATUS:
                    raise Transport(last)
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, e)
            if attempt < self.transport_retries:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
        if payload is None:
            raise Transport(last or "exhausted transport retries")

        return read_message(payload, self.model, self.resolved_models, self.usage)


def read_message(payload, requested_model, resolved_models, usage):
    """Pull (text, resolved model) out of one Message, tallying its usage.

    Shared by the synchronous and the batch path: a batch result's
    `.result.message` is an ordinary Message, so reading it anywhere else would
    be a second place that could disagree about what counts as truncated.
    """
    # The model that actually answered, not the alias that was asked for.
    resolved = payload.get("model") or requested_model
    resolved_models[resolved] += 1
    usage.add(payload.get("usage") or {})

    parts = [c.get("text", "") for c in payload.get("content", [])
             if c.get("type") == "text"]
    if payload.get("stop_reason") == "max_tokens":
        raise Malformed("response truncated at max_tokens")
    return "".join(parts), resolved


class StubBackend(object):
    """Deterministic synthetic scores for offline plumbing verification.

    NOT A SCORER. It reads nothing but the prompt's own hash, so its tone values
    carry no information about the review. Every row it writes is stamped
    `stub:deterministic-v1`, the calibration gate refuses such rows, and it will
    not write anywhere under data/.
    """

    kind = "stub"
    batched = False

    def __init__(self, fail_transport=29, fail_malformed=23, retry_ok=17):
        self.model = STUB_MODEL_ID
        self.resolved_models = Counter()
        self.usage = UsageTally()
        # Keyed on the prompt, not on a call counter: at temperature 0 a retry
        # sends an identical prompt and gets an identical answer, so a
        # systematically malformed review stays malformed on the second attempt.
        self.fail_transport = fail_transport
        self.fail_malformed = fail_malformed
        self.retry_ok = retry_ok
        self._seen = set()

    def __call__(self, prompt):
        self.resolved_models[STUB_MODEL_ID] += 1
        # No tokens are billed and none are invented: a stub that reported
        # plausible usage would let a cost figure be quoted from synthetic data.
        self.usage.add(None)
        h = int(hashlib.sha1(prompt.encode("utf-8")).hexdigest(), 16)
        first = prompt not in self._seen
        self._seen.add(prompt)

        if self.fail_transport and h % self.fail_transport == 0:
            raise Transport("stub: simulated transport failure")
        if self.fail_malformed and h % self.fail_malformed == 0:
            return "Sure! The tone here is probably negative.", STUB_MODEL_ID
        if self.retry_ok and h % self.retry_ok == 0 and first:
            return '{"review_id": "' + prompt.split("\n", 1)[0].split(": ", 1)[1] \
                   + '", "tone": 0', STUB_MODEL_ID  # truncated: recovers on retry

        obj = {
            "review_id": prompt.split("\n", 1)[0].split(": ", 1)[1],
            "tone": TONE_VALUES[h % 5],
            "seller_or_shipping_only": (h >> 8) % 11 == 0,
            "defect": (h >> 16) % 7 == 0,
            "price_complaint": (h >> 24) % 9 == 0,
        }
        return json.dumps(obj), STUB_MODEL_ID


# --------------------------------------------------------------------------
# Cache verification and measured cost
# --------------------------------------------------------------------------

def cache_verdict(usage, model, requested, n_reviews):
    """Did caching actually happen? Read from usage, never from the request.

    THE TRAP this exists for: the minimum cacheable prefix is model-dependent,
    somewhere between 512 and 4,096 tokens, and a prefix below it SILENTLY does
    not cache. No error, no warning - `cache_control` is accepted and
    `cache_creation_input_tokens` and `cache_read_input_tokens` both come back
    zero. A run that inferred the discount from the marker being accepted would
    report a saving it never received, which is worse than not caching at all.

    Measured 2026-10-18 on claude-haiku-4-5-20251001: the rubric prompt is ~940
    tokens against a 4,096-token minimum, and cache reads stayed at zero across
    repeated requests. The marker is left in place because it costs nothing and
    becomes live the day the scorer moves to a model with a lower minimum - but
    the saving is reported as zero until usage says otherwise.
    """
    reads = usage.totals["cache_read_input_tokens"]
    writes = usage.totals["cache_creation_input_tokens"]
    minimum = MIN_CACHEABLE_PREFIX_TOKENS.get(model)
    # Engagement needs a read, not a write. A write with no read is the pure
    # surcharge case: the premium paid on bytes nothing ever came back for.
    engaged = None
    if not requested:
        engaged = False
    elif usage.n_with_usage == 0:
        engaged = None            # no usage reported: unknown, not "no"
    else:
        engaged = reads > 0
    out = {
        "requested": bool(requested),
        "engaged": engaged,
        "cache_read_input_tokens": reads,
        "cache_creation_input_tokens": writes,
        "min_cacheable_prefix_tokens": minimum,
        "n_calls_reporting_usage": usage.n_with_usage,
    }
    if engaged is False and requested and usage.n_with_usage:
        out["note"] = (
            "cache_control was sent and cache_read_input_tokens stayed 0 across "
            "%d call(s). The prefix is below this model's minimum cacheable "
            "size%s, so it is accepted and caches nothing. The caching saving "
            "for this run is ZERO; any figure claiming otherwise is imaginary."
            % (usage.n_with_usage,
               "" if minimum is None else " of %d tokens" % minimum))
    elif engaged is None and requested:
        out["note"] = ("no usage was reported by any call, so whether caching "
                       "engaged is unknown. Not reported as engaged.")
    return out


def cost_block(usage, model, batched):
    """Measured cost from actual token counts. Null for an unpriced model.

    Every number here comes out of `usage`, which is what the API billed on.
    A model absent from PRICES_USD_PER_MTOK yields nulls rather than a guessed
    price: an invented figure reads exactly like a measured one.
    """
    price = PRICES_USD_PER_MTOK.get(model)
    mult = BATCH_MULTIPLIER if batched else 1.0
    out = {
        "price_source": PRICES_RECORDED,
        "model_priced": price is not None,
        "batch_tier": bool(batched),
        "batch_multiplier": mult,
        "cache_write_multiplier": CACHE_WRITE_MULTIPLIER,
        "cache_read_multiplier": CACHE_READ_MULTIPLIER,
        "usage": usage.as_dict(),
    }
    if price is None:
        out.update({"input_usd_per_mtok": None, "output_usd_per_mtok": None,
                    "usd_total": None, "usd_per_review": None,
                    "note": "model %r is not in PRICES_USD_PER_MTOK, so no cost "
                            "is reported. A guessed price would read like a "
                            "measured one." % model})
        return out
    t = usage.totals
    usd = (
        t["input_tokens"] * price["input"]
        + t["cache_creation_input_tokens"] * price["input"] * CACHE_WRITE_MULTIPLIER
        + t["cache_read_input_tokens"] * price["input"] * CACHE_READ_MULTIPLIER
        + t["output_tokens"] * price["output"]
    ) / 1e6 * mult
    out.update({
        "input_usd_per_mtok": price["input"],
        "output_usd_per_mtok": price["output"],
        "usd_total": usd,
        "usd_per_review": (usd / usage.n_calls) if usage.n_calls else None,
    })
    return out


# --------------------------------------------------------------------------
# Per-quarter sampling - a methodology change, built as one
# --------------------------------------------------------------------------
#
# The per-quarter floor is MIN_REVIEWS_PER_QUARTER (30). Weber averages 509
# reviews per quarter, so scoring all of them pays 17x for a precision the
# method never asks for. Sampling is the biggest of the three savings and the
# only one with methodology attached, so every property that makes it a method
# rather than a cheaper loop is enforced here and recorded on disk:
#
#   SEEDED AND REPRODUCIBLE. The seed comes from env_stamp's binding convention
#   (references/numeric-stack.md s3) rather than from this stage, because two
#   sessions left to invent their own would not agree. The draw uses only
#   .randrange() - .shuffle() and .sample() are implementation details that have
#   changed across CPython versions, so the same seed can give a different
#   stream on a different interpreter.
#
#   DRAWN BEFORE ANY SCORE IS SEEN. The draw is a function of (review ids,
#   quarter, seed) and nothing else. It does not read a tone, a star, a date
#   order, or which reviews happen to be scored already - a sample chosen after
#   looking at scores is selection, and no amount of recording it afterwards
#   makes it a sample. In particular it is taken over the FULL quarter
#   population and only then intersected with what is already scored, so
#   resuming an interrupted run cannot move the draw.
#
#   NESTED IN N. A partial Fisher-Yates prefix means the N=50 draw is a subset
#   of the N=150 draw from the same population and seed. Raising N adds reviews
#   and never swaps them, so a quarter can be deepened without rescoring it.
#
#   THE FLOOR IS NEVER CROSSED SILENTLY. A quarter shorter than N is taken
#   whole. A quarter under 30 is insufficient exactly as it always was, and is
#   recorded as such here rather than quietly producing a thin number later.


def quarter_of(row):
    """The quarter a review belongs to, via aggregate.py's own labeller.

    Imported rather than reimplemented. If this stage drew per "quarter" using
    a different boundary from the one the aggregator buckets on, the sample
    would be recorded against periods that do not exist downstream, and the
    mismatch would be invisible in both files.
    """
    return aggmod.period_label(row)


def draw_quarter_sample(review_ids, ticker, quarter, n, seed):
    """The drawn ids for one quarter: a prefix of a seeded permutation.

    A function of (review ids, quarter, seed) and nothing else. Sorting first
    makes it independent of the order reviews.csv happens to be in, so a
    re-exported corpus with the same ids draws the same sample.
    """
    pool = sorted(review_ids)
    rng = aggmod.quarter_rng(seed, ticker, quarter)
    k = min(n, len(pool))
    # Partial Fisher-Yates. Position i is settled by the i-th draw and never
    # revisited, which is what makes the result nested in n.
    for i in range(k):
        j = i + rng.randrange(len(pool) - i)
        pool[i], pool[j] = pool[j], pool[i]
    return pool[:k]


def sample_per_quarter(reviews, ticker, n, seed, min_reviews=None):
    """Restrict `reviews` to at most `n` per quarter. Returns (rows, block).

    `block` is the record that travels into the manifest and the scoring
    report. It carries the population every quarter was drawn from, and the
    sha256 of that population's id set, because the whole point is that a
    150-review quarter sampled from 509 must be tellable apart from a quarter
    that genuinely held 150 - and from one sampled from a population that has
    since changed underneath it.
    """
    if min_reviews is None:
        min_reviews = aggmod.MIN_REVIEWS_PER_QUARTER
    if n < min_reviews:
        die("--sample-per-quarter %d is below the %d-review floor.\n\n"
            "Every quarter drawn at that size would be insufficient_history by\n"
            "construction, so the run would cost money and produce no tone\n"
            "number anywhere. If the floor is meant to move, move it in\n"
            "aggregate.py where it is defined and recorded, not by sampling\n"
            "under it here." % (n, min_reviews))

    by_q = defaultdict(list)
    unassignable = 0
    for r in reviews:
        q = quarter_of(r)
        if not q:
            unassignable += 1
            continue
        by_q[q].append(r)
    if unassignable:
        die("%d review(s) carry neither a fiscal_quarter nor a parseable\n"
            "review_date, so they cannot be assigned to a quarter.\n\n"
            "Per-quarter sampling needs every review in a named quarter: rows\n"
            "with no quarter would be either dropped from the draw or pooled\n"
            "into one unnamed bucket, and both change which reviews get scored\n"
            "without appearing anywhere in the record." % unassignable)

    per_q, kept = {}, []
    for q in sorted(by_q):
        rows = by_q[q]
        ids = [r["review_id"] for r in rows]
        population = len(ids)
        drawn = draw_quarter_sample(ids, ticker, q, n, seed)
        chosen = set(drawn)
        kept.extend(r for r in rows if r["review_id"] in chosen)
        per_q[q] = {
            "population": population,
            # The id set the draw was taken over. A population that changes
            # between runs changes the sample, which is correct and must be
            # visible rather than inferred from a differing count.
            "population_sha256": hashlib.sha256(
                "\n".join(sorted(ids)).encode("utf-8")).hexdigest(),
            "drawn": len(drawn),
            "drawn_review_ids": sorted(drawn),
            "took_all": len(drawn) == population,
            "below_floor": population < min_reviews,
        }

    block = {
        "mode": "sample_per_quarter",
        "n_per_quarter": n,
        "seed": seed,
        "stage": "scoring_sample",
        "stage_offset": env_stamp.STAGE_OFFSETS["scoring_sample"],
        "rng": "stdlib-mt19937",
        "draw": "partial Fisher-Yates prefix over the sorted review ids, "
                "keyed on (seed, ticker, quarter) via aggregate.quarter_rng",
        "draw_inputs": ["review_ids", "quarter", "seed"],
        "nested_in_n": True,
        "drawn_before_scoring": True,
        "min_reviews_per_quarter": min_reviews,
        "quarter_labeller": "aggregate.period_label",
        "population_total": sum(d["population"] for d in per_q.values()),
        "drawn_total": sum(d["drawn"] for d in per_q.values()),
        "n_quarters": len(per_q),
        "n_quarters_took_all": sum(1 for d in per_q.values() if d["took_all"]),
        "n_quarters_below_floor": sum(1 for d in per_q.values()
                                      if d["below_floor"]),
        "per_quarter": per_q,
        "downstream": SAMPLING_DOWNSTREAM,
    }
    return kept, block


def print_sampling(block):
    print("\n--- per-quarter sample -----------------------------------------")
    print("N per quarter      %d   (floor %d)"
          % (block["n_per_quarter"], block["min_reviews_per_quarter"]))
    print("seed               %d   (stage %r, offset %d - env_stamp convention)"
          % (block["seed"], block["stage"], block["stage_offset"]))
    print("drawn              %d of %d reviews across %d quarters"
          % (block["drawn_total"], block["population_total"],
             block["n_quarters"]))
    print("\n  quarter      population   drawn   note")
    for q in sorted(block["per_quarter"]):
        d = block["per_quarter"][q]
        note = []
        if d["took_all"]:
            note.append("whole quarter (shorter than N)")
        if d["below_floor"]:
            note.append("BELOW THE %d FLOOR - insufficient, as before"
                        % block["min_reviews_per_quarter"])
        print("  %-12s %10d  %6d   %s"
              % (q, d["population"], d["drawn"], "; ".join(note)))
    if block["n_quarters_below_floor"]:
        print("\n!! %d quarter(s) hold fewer than %d reviews. That was already"
              % (block["n_quarters_below_floor"],
                 block["min_reviews_per_quarter"]))
        print("!! true of the corpus and sampling did not cause it - but they")
        print("!! emit insufficient_history and no tone number.")


def update_manifest(path, key, block):
    """Merge one block into manifest.json, never overwriting it wholesale.

    `references/data-contracts.md`: the manifest is written by every stage and
    overwritten by none of them. aggregate.py writes under "aggregate"; this
    writes under its own key and leaves every other one alone.
    """
    man = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                man = json.load(f)
        except (ValueError, OSError):
            man = {}
    man[key] = block
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(man, f, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------------
# Batches API
# --------------------------------------------------------------------------

BATCH_MAX_REQUESTS = 100000       # API limit per batch
BATCH_TERMINAL = ("succeeded", "errored", "canceled", "expired")


class HttpBatchTransport(object):
    """Raw POST/GET against /v1/messages/batches. No SDK (stdlib-only policy)."""

    kind = "api"

    def __init__(self, api, timeout=300):
        self._api = api
        self.timeout = timeout

    def _call(self, url, data=None):
        req = urllib.request.Request(url, data=data,
                                     headers=self._api.headers())
        delay = 1.0
        last = None
        for attempt in range(self._api.transport_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:400]
                except Exception:
                    pass
                last = "HTTP %s %s" % (e.code, detail)
                if e.code not in RETRY_STATUS:
                    raise Transport(last)
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, e)
            if attempt < self._api.transport_retries:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
        raise Transport(last or "exhausted transport retries")

    def create(self, requests_):
        body = json.dumps({"requests": requests_}).encode("utf-8")
        return self._call(self._api.base_url + "/v1/messages/batches", body)

    def retrieve(self, batch_id):
        return self._call(self._api.base_url + "/v1/messages/batches/"
                          + urllib.parse.quote(batch_id))

    def results(self, results_url):
        """Stream the JSONL result file, one parsed object per line."""
        req = urllib.request.Request(results_url, headers=self._api.headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            for raw in r:
                line = raw.decode("utf-8").strip()
                if line:
                    yield json.loads(line)


class BatchBackend(object):
    """The same one-review requests, submitted through the Batches API.

    50% off every token, for work nobody is waiting on. It is one review per
    REQUEST and many requests per BATCH - never several reviews in one request,
    which would let batch ordering leak one review's context into the next and
    would not be the scorer the calibration gate covers.

    Results arrive in ANY ORDER, so everything here is keyed on `custom_id`.
    Nothing is read by position: a results file that came back in a different
    order would otherwise attach every score to the wrong review, and the rows
    would all look perfectly well-formed.

    `.result.type` is one of succeeded / errored / canceled / expired, and only
    a success carries `.result.message`. An `errored` result is a TRANSPORT
    failure, not a malformed response: the model never answered, so counting it
    as malformed would read an outage as a scorer problem - the exact confusion
    the existing split exists to prevent.
    """

    kind = "batch"
    batched = True

    def __init__(self, api, transport, poll_seconds=20, max_wait=24 * 3600,
                 chunk=BATCH_MAX_REQUESTS, quiet=False):
        self._api = api
        self._t = transport
        self.model = api.model
        self.resolved_models = api.resolved_models
        self.usage = api.usage
        self.poll_seconds = poll_seconds
        self.max_wait = max_wait
        self.chunk = min(chunk, BATCH_MAX_REQUESTS)
        self.quiet = quiet
        self.batch_ids = []

    def params_for(self, prompt):
        return self._api.params_for(prompt)

    # -- submission ------------------------------------------------------

    def _submit(self, rows):
        reqs = []
        seen = set()
        for r in rows:
            rid = r["review_id"]
            if rid in seen:
                # Two requests under one custom_id would collapse into one
                # result and silently lose a review. read_reviews already
                # refuses a genuine id collision; this catches the case where
                # the batch builder itself duplicated a row.
                die("review_id %r appears twice in one batch submission.\n"
                    "Results are keyed on custom_id, so two requests sharing\n"
                    "one id return a single result and the other review\n"
                    "vanishes without appearing as a failure." % rid)
            seen.add(rid)
            reqs.append({"custom_id": rid,
                         "params": self.params_for(prompt_for_row(r))})
        created = self._t.create(reqs)
        bid = created.get("id")
        if not bid:
            die("the Batches API accepted the submission but returned no batch\n"
                "id, so there is nothing to poll and %d requests are in flight\n"
                "with no way to collect them." % len(reqs))
        self.batch_ids.append(bid)
        return bid, set(seen)

    # -- polling ---------------------------------------------------------

    def _await(self, batch_id):
        t0 = time.time()
        while True:
            info = self._t.retrieve(batch_id)
            status = info.get("processing_status")
            if status == "ended":
                return info
            waited = time.time() - t0
            if waited > self.max_wait:
                die("batch %s is still %r after %.0f s.\n"
                    "Nothing has been written. The batch is not lost - it is\n"
                    "still on the server and the API keeps results for 29 days\n"
                    "- so re-run to collect it rather than re-submitting, which\n"
                    "would pay for every request twice."
                    % (batch_id, status, waited))
            if not self.quiet:
                counts = info.get("request_counts") or {}
                sys.stderr.write(
                    "    batch %s %s · done %d/%d · %.0fs\r"
                    % (batch_id[:18], status,
                       sum(counts.get(k, 0) for k in BATCH_TERMINAL),
                       sum(counts.values()) or 0, waited))
                sys.stderr.flush()
            time.sleep(self.poll_seconds)

    # -- collection ------------------------------------------------------

    def _collect(self, info, expected):
        """{review_id: (raw_text_or_None, resolved, note)} keyed on custom_id."""
        url = info.get("results_url")
        if not url:
            die("batch %s ended with no results_url.\n"
                "There is no way to read what it produced, and guessing from\n"
                "request_counts would be inventing scores." % info.get("id"))
        out = {}
        for item in self._t.results(url):
            cid = item.get("custom_id")
            if cid not in expected:
                # Never ignored: an unexpected custom_id means the mapping
                # between requests and results is not what this code thinks it
                # is, and every score in the batch is then suspect.
                die("batch %s returned a result for custom_id %r, which was\n"
                    "never submitted in it. Results are keyed on custom_id, so\n"
                    "a key nobody sent means the request/result mapping is\n"
                    "wrong and no score from this batch can be trusted."
                    % (info.get("id"), cid))
            res = item.get("result") or {}
            kind = res.get("type")
            if kind == "succeeded":
                try:
                    raw, resolved = read_message(
                        res.get("message") or {}, self.model,
                        self.resolved_models, self.usage)
                except Malformed as e:
                    out[cid] = (None, self.model, ("malformed", str(e)))
                    continue
                out[cid] = (raw, resolved, ("ok", None))
            elif kind in ("errored", "canceled", "expired"):
                # The model did not answer. Transport, not malformed output.
                err = ((res.get("error") or {}).get("type")
                       or (res.get("error") or {}).get("message") or kind)
                out[cid] = (None, self.model,
                            ("transport", "batch result %s: %s" % (kind, err)))
            else:
                out[cid] = (None, self.model,
                            ("transport", "unknown batch result type %r" % kind))
        return out

    def score_rows(self, rows):
        """Score a list of review rows. Returns {review_id: (result, note)}.

        The rubric's single malformed-output retry is honoured as a second
        batch over just the malformed ids. At temperature 0 that resends an
        identical request, so it recovers truncation rather than systematic
        malformation - exactly as the synchronous path's retry does.
        """
        results, notes = {}, {}
        pending = list(rows)
        by_id = dict((r["review_id"], r) for r in rows)

        for round_ in (1, 2):
            if not pending:
                break
            if not self.quiet and round_ == 2:
                print("\nretry round        %d malformed response(s) resubmitted"
                      % len(pending))
            collected = {}
            for i in range(0, len(pending), self.chunk):
                part = pending[i:i + self.chunk]
                bid, expected = self._submit(part)
                if not self.quiet:
                    print("batch submitted    %s · %d request(s)" % (bid, len(part)))
                info = self._await(bid)
                if not self.quiet:
                    sys.stderr.write("\n")
                got = self._collect(info, expected)
                # A submitted id with no result line is a loss, recorded as one.
                for rid in expected:
                    if rid not in got:
                        got[rid] = (None, self.model,
                                    ("transport",
                                     "no result line for custom_id in batch %s"
                                     % bid))
                collected.update(got)

            retry = []
            for rid, (raw, resolved, note) in collected.items():
                if note[0] == "transport":
                    results[rid] = _failed_row(rid, self.model)
                    notes[rid] = note
                    continue
                try:
                    obj = parse_response(raw, rid)
                except Malformed as e:
                    if round_ == 1:
                        retry.append(by_id[rid])
                    else:
                        results[rid] = _failed_row(rid, self.model)
                        notes[rid] = ("malformed", str(e))
                    continue
                results[rid] = {
                    "review_id": rid,
                    "tone": obj["tone"],
                    "seller_or_shipping_only": obj["seller_or_shipping_only"],
                    "defect": obj["defect"],
                    "price_complaint": obj["price_complaint"],
                    "scoring_failed": False,
                    "rubric_version": None,      # stamped by the caller
                    "scorer_model_id": resolved,
                    "scored_at": None,
                }
                notes[rid] = ("ok" if round_ == 1 else "ok_after_retry", None)
            pending = retry

        for r in rows:
            rid = r["review_id"]
            if rid not in results:
                # Unreachable: every submitted id is either collected or given
                # a no-result-line note above. Written as a failure rather than
                # an assertion so a review can never silently disappear (F13).
                results[rid] = _failed_row(rid, self.model)
                notes[rid] = ("transport", "never returned by any batch")
        return dict((rid, (results[rid], notes[rid])) for rid in results)


class _StubApiShim(object):
    """Just enough of ApiBackend for BatchBackend to run offline.

    The stub has no API key and no URL, but it builds the SAME request body
    through the SAME `request_params`, so the offline batch tests exercise the
    real body and the real validator rather than a simplified stand-in.
    """

    transport_retries = 0

    def __init__(self, stub, max_tokens=256):
        self._stub = stub
        self.model = stub.model
        self.resolved_models = stub.resolved_models
        self.usage = stub.usage
        self.system = build_system_blocks(expected_system_text_source(),
                                          cache=True)
        self.base_url = "stub://"
        self.max_tokens = max_tokens

    def headers(self):
        return {}

    def params_for(self, prompt):
        return request_params(self.model, self.system, prompt, self.max_tokens)


def expected_system_text_source():
    """The rubric text behind the pinned system prompt.

    `set_expected_system` stores the rendered prompt; the stub shim needs the
    rubric it was rendered from so that `build_system_blocks` produces the
    identical blocks and the validator passes for the right reason.
    """
    return load_rubric(_RUBRIC_VERSION[0])


def verify_prompt_identity(api, rubric_text):
    """Does moving `system` from a string to one text block change the prompt?

    Caching needs the rubric in a text block so a `cache_control` key can sit on
    it. The old path sent a bare string. The API normalises a string to exactly
    one text block, so the two should be the same prompt - but "should be" is
    what this project does not accept about anything that touches the scorer, so
    it is measured through count_tokens, which bills nothing.
    """
    url = api.base_url + "/v1/messages/count_tokens"

    def count(system):
        body = json.dumps({
            "model": api.model,
            "system": system,
            "messages": [{"role": "user",
                          "content": build_prompt("probe", "t", "x")}],
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=api.headers())
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))

    plain = build_system_prompt(rubric_text)
    a = count(plain)
    b = count([{"type": "text", "text": plain}])
    c = count(build_system_blocks(rubric_text, cache=True))
    print("--- prompt identity (count_tokens, unbilled) --------------------")
    print("system as a bare string        %s input tokens" % a.get("input_tokens"))
    print("system as one text block       %s input tokens" % b.get("input_tokens"))
    print("same block + cache_control     %s input tokens" % c.get("input_tokens"))
    same = a.get("input_tokens") == b.get("input_tokens") == c.get("input_tokens")
    print("identical                      %s" % ("YES" if same else "NO"))
    if not same:
        print("!! The three forms do not count the same, so moving the rubric")
        print("!! into a block changed the prompt. Do not ship it: re-run the")
        print("!! calibration before trusting any score produced under it.")
    return same


class StubBatchTransport(object):
    """Offline Batches API: out-of-order results, every terminal type, a gap.

    Exercises what the live endpoint cannot be asked to do on demand. The shapes
    it produces are the ones that break a position-keyed reader and a reader
    that treats `errored` as malformed - so the batch path is verified against
    them before a single paid request is sent.
    """

    kind = "stub"

    def __init__(self, backing, drop_ids=(), error_ids=(), expired_ids=(),
                 canceled_ids=(), reverse=True, extra_custom_id=None,
                 stall_rounds=0):
        self._backing = backing
        self.drop_ids = set(drop_ids)
        self.error_ids = set(error_ids)
        self.expired_ids = set(expired_ids)
        self.canceled_ids = set(canceled_ids)
        self.reverse = reverse
        self.extra_custom_id = extra_custom_id
        self.stall_rounds = stall_rounds
        self._batches = {}
        self._polls = Counter()
        self._n = 0

    def create(self, requests_):
        self._n += 1
        bid = "msgbatch_stub_%03d" % self._n
        self._batches[bid] = requests_
        return {"id": bid, "processing_status": "in_progress",
                "request_counts": {"processing": len(requests_)}}

    def retrieve(self, batch_id):
        self._polls[batch_id] += 1
        if self._polls[batch_id] <= self.stall_rounds:
            return {"id": batch_id, "processing_status": "in_progress",
                    "request_counts": {"processing":
                                       len(self._batches[batch_id])}}
        return {"id": batch_id, "processing_status": "ended",
                "results_url": "stub://" + batch_id,
                "request_counts": {"succeeded": len(self._batches[batch_id])}}

    def results(self, results_url):
        bid = results_url[len("stub://"):]
        items = []
        for req in self._batches[bid]:
            cid = req["custom_id"]
            if cid in self.drop_ids:
                continue
            if cid in self.error_ids:
                items.append({"custom_id": cid,
                              "result": {"type": "errored",
                                         "error": {"type": "overloaded_error"}}})
                continue
            if cid in self.expired_ids:
                items.append({"custom_id": cid, "result": {"type": "expired"}})
                continue
            if cid in self.canceled_ids:
                items.append({"custom_id": cid, "result": {"type": "canceled"}})
                continue
            prompt = req["params"]["messages"][0]["content"]
            try:
                raw, _ = self._backing(prompt)
            except Transport as e:
                items.append({"custom_id": cid,
                              "result": {"type": "errored",
                                         "error": {"type": "api_error",
                                                   "message": str(e)}}})
                continue
            items.append({"custom_id": cid, "result": {
                "type": "succeeded",
                "message": {"model": STUB_MODEL_ID, "stop_reason": "end_turn",
                            "content": [{"type": "text", "text": raw}],
                            "usage": None},
            }})
        if self.extra_custom_id:
            items.append({"custom_id": self.extra_custom_id,
                          "result": {"type": "succeeded",
                                     "message": {"content": []}}})
        # Out of order on purpose. A reader that keyed on position would attach
        # every score to the wrong review and nothing in the output would say so.
        if self.reverse:
            items.reverse()
        for it in items:
            yield it


# --------------------------------------------------------------------------
# Scoring one review
# --------------------------------------------------------------------------

def score_one(backend, row):
    """Score a single review. Returns (result_dict, note).

    One malformed-output retry, exactly as the rubric mandates, then
    `scoring_failed`. Transport exhaustion also yields `scoring_failed` but is
    reported under its own heading so a network outage is never read as a model
    problem.
    """
    rid = row["review_id"]
    prompt = prompt_for_row(row)
    attempts = []
    for attempt in (1, 2):
        try:
            raw, resolved = backend(prompt)
        except Transport as e:
            return _failed_row(rid, backend.model), ("transport", str(e))
        try:
            obj = parse_response(raw, rid)
        except Malformed as e:
            attempts.append(str(e))
            continue
        return {
            "review_id": rid,
            "tone": obj["tone"],
            "seller_or_shipping_only": obj["seller_or_shipping_only"],
            "defect": obj["defect"],
            "price_complaint": obj["price_complaint"],
            "scoring_failed": False,
            "rubric_version": None,      # stamped by the caller
            "scorer_model_id": resolved,
            "scored_at": None,
        }, ("ok" if attempt == 1 else "ok_after_retry", None)
    return _failed_row(rid, backend.model), ("malformed", " | ".join(attempts))


def _failed_row(rid, model):
    return {
        "review_id": rid, "tone": "", "seller_or_shipping_only": "",
        "defect": "", "price_complaint": "", "scoring_failed": True,
        "rubric_version": None, "scorer_model_id": model, "scored_at": None,
    }


# --------------------------------------------------------------------------
# IO
# --------------------------------------------------------------------------

def die(msg, code=2):
    _record_refusal()
    sys.stderr.write("\n%s\n\n" % msg)
    sys.exit(code)


def read_csv(path):
    if not os.path.exists(path):
        die("missing %s" % path)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_reviews(path, win=None):
    """Load reviews.csv, restricted to `win` when one is given.

    The scorer filters independently of the extractor rather than trusting that
    reviews.csv was itself built under `--window dev`: the same file may be
    rebuilt over the full window after the freeze, and a scorer that inferred
    its window from its input would then silently score held-out reviews.
    """
    rows = read_csv(path)
    if not rows:
        die("%s has no rows" % path)
    need = {"review_id", "title", "text"}
    missing = need - set(rows[0])
    if missing:
        die("%s is missing required columns: %s" % (path, sorted(missing)))
    if win is not None and win.restricts:
        # The quarter comes from `aggregate.period_label`, the same function the
        # sampler already uses - imported, never reimplemented. A live-collected
        # reviews.csv deliberately carries no `fiscal_quarter`: the collector
        # drops it because fresh reviews are not on any company's fiscal
        # calendar, and assigning one there would bake one company's calendar
        # into another's data (CONTRACTS.md s2). Demanding the column here
        # refused every live file, which would have made the held-out boundary
        # unenforceable on exactly the data it exists to protect.
        labelled = [(r, quarter_of(r)) for r in rows]
        unlabelled = [r for r, q in labelled if not q]
        if unlabelled:
            die("%d review(s) in %s have neither a fiscal_quarter nor a\n"
                "parseable review_date, so --window %s cannot be honoured for\n"
                "them. Scoring them would put possibly-held-out reviews through\n"
                "the scorer with nothing able to tell afterwards (CORE-PROMPT "
                "s1.4)." % (len(unlabelled), path, win.mode))
        kept = [r for r, q in labelled if win.allows(q)]
        if not kept:
            die("%s has no reviews inside %s" % (path, win.describe()))
        if len(kept) != len(rows):
            print("window             %d of %d reviews inside %s"
                  % (len(kept), len(rows), win.mode))
        rows = kept
    # The 2023 corpus contains exact duplicate records - the same review text
    # from the same user on the same parent_asin at the same second appears
    # twice. review_id is a hash of exactly those three fields, so a repeated id
    # is either a corpus duplicate (identical row: dropped, counted, reported)
    # or a real collision between two different reviews (the id would then key
    # two different texts in scores.csv: hard failure).
    first, out, dupes = {}, [], 0
    for r in rows:
        rid = r["review_id"]
        if rid not in first:
            first[rid] = r
            out.append(r)
            continue
        if r == first[rid]:
            dupes += 1
            continue
        die("review_id COLLISION in %s: %r keys two different reviews.\n"
            "  a: %.80r\n  b: %.80r\n"
            "scores.csv is keyed on review_id, so this would merge two reviews\n"
            "into one score. Fix the id scheme in the extraction stage."
            % (path, rid, first[rid].get("text"), r.get("text")))
    if dupes:
        print("!! %d exact duplicate row(s) in %s dropped before scoring"
              % (dupes, path))
    return out


def append_scores(path, rows):
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def load_scores(path):
    if not os.path.exists(path):
        return []
    return read_csv(path)


def select_scores(score_rows, rubric_version, model_id):
    """Rows produced under one exact (rubric, model). F10: never mix vintages."""
    return [r for r in score_rows
            if r.get("rubric_version") == rubric_version
            and r.get("scorer_model_id") == model_id]


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# Statistics - every reported number is computed here (CORE-PROMPT s1.3)
# --------------------------------------------------------------------------

def confusion_matrix(pairs, cats):
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    m = [[0] * k for _ in range(k)]
    for h, p in pairs:
        m[idx[h]][idx[p]] += 1
    return m


def agreement_rates(pairs):
    n = len(pairs)
    if not n:
        return None, None
    exact = sum(1 for h, p in pairs if h == p) / float(n)
    adjacent = sum(1 for h, p in pairs if abs(h - p) <= 1) / float(n)
    return exact, adjacent


def quadratic_weighted_kappa(pairs, cats=TONE_VALUES):
    """QWK over a fixed category set. None when undefined, never a flattering 1.0.

    Expected disagreement is zero when either rater used a single category, and
    kappa is then undefined - which is a blocked calibration, not a perfect one.
    """
    n = len(pairs)
    if n == 0:
        return None
    k = len(cats)
    m = confusion_matrix(pairs, cats)
    w = [[((i - j) ** 2) / float((k - 1) ** 2) for j in range(k)] for i in range(k)]
    row = [sum(m[i]) for i in range(k)]
    col = [sum(m[i][j] for i in range(k)) for j in range(k)]
    num = sum(w[i][j] * m[i][j] for i in range(k) for j in range(k))
    den = sum(w[i][j] * row[i] * col[j] / float(n) for i in range(k) for j in range(k))
    if den == 0:
        return None
    return 1.0 - num / den


def bootstrap_ci(pairs, stat, reps=2000, seed=20261011, lo=2.5, hi=97.5):
    """Percentile CI by resampling labelled pairs. Returns (lo, hi) or (None, None)."""
    n = len(pairs)
    if n < 2:
        return None, None
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        v = stat(sample)
        if v is not None:
            vals.append(v)
    if len(vals) < reps * 0.5:
        return None, None
    vals.sort()

    def pct(p):
        i = (len(vals) - 1) * p / 100.0
        f = int(math.floor(i))
        c = min(f + 1, len(vals) - 1)
        return vals[f] + (vals[c] - vals[f]) * (i - f)

    return pct(lo), pct(hi)


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / float(n)
    my = sum(ys) / float(n)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


# --------------------------------------------------------------------------
# B4 - scoring
# --------------------------------------------------------------------------

def cmd_score(args, paths, rubric_version, backend, win=None):
    reviews = read_reviews(paths["reviews"], win)
    population = len(reviews)

    # THE DRAW HAPPENS HERE: over the full quarter population, before any score
    # is loaded, let alone looked at. Intersecting with `already` afterwards is
    # what keeps a resumed run drawing the same sample as a fresh one - if the
    # draw ran over "reviews not yet scored" it would depend on how far the last
    # run got, and would stop being a function of (ids, quarter, seed).
    sampling = None
    if args.sample_per_quarter:
        if args.limit:
            die("--sample-per-quarter and --limit cannot be combined.\n\n"
                "--limit truncates whatever order the rows arrive in, so it\n"
                "would cut the draw unevenly across quarters after the draw had\n"
                "balanced it - and the manifest would record a per-quarter\n"
                "sample that was never actually scored. Use a smaller\n"
                "--sample-per-quarter instead.")
        reviews, sampling = sample_per_quarter(
            reviews, paths["ticker"], args.sample_per_quarter,
            env_stamp.stage_seed("scoring_sample", args.seed))

    existing = load_scores(paths["scores"])
    already = set(r["review_id"] for r in
                  select_scores(existing, rubric_version, backend.model))

    # For an API backend the exact model is only known once a response arrives,
    # so resume-by-model uses the requested id; a mid-run change is caught below.
    todo = [r for r in reviews if r["review_id"] not in already]
    if args.limit:
        todo = todo[:args.limit]

    print("reviews.csv        %d rows" % population)
    if sampling:
        print("sampled            %d rows (<=%d per quarter, seed %d)"
              % (sampling["drawn_total"], sampling["n_per_quarter"],
                 sampling["seed"]))
    if already:
        print("already scored     %d under (%s, %s) - skipped"
              % (len(already), rubric_version, backend.model))
    print("to score           %d" % len(todo))
    print("backend            %s · model %s · temperature 0 · 1 review/call"
          % (backend.kind, backend.model))
    print("rubric             %s (sha256 %s…)"
          % (rubric_version, RUBRIC_SHA256[rubric_version][:12]))
    truncated = sum(1 for r in todo if len(r.get("text") or "") > MAX_TEXT_CHARS)
    if truncated:
        print("long text          %d review(s) truncated to %d chars"
              % (truncated, MAX_TEXT_CHARS))
    if sampling:
        print_sampling(sampling)
    if not todo:
        print("\nnothing to do.")
        if sampling:
            update_manifest(paths["manifest"], "scoring_sample", sampling)
            print("sample record   -> %s" % paths["manifest"])
        return 0

    results = {}
    notes = {}
    t0 = time.time()
    done = 0

    if backend.batched:
        # One request per review, many requests per batch, keyed on custom_id.
        for rid, (res, note) in backend.score_rows(todo).items():
            results[rid] = res
            notes[rid] = note
        done = len(results)
        sys.stderr.write("    collected %d/%d from %d batch(es)\n"
                         % (done, len(todo), len(backend.batch_ids)))
        pool, it = None, ()
    else:
        def work(row):
            return row["review_id"], score_one(backend, row)

        if args.workers > 1:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
            it = pool.map(work, todo)
        else:
            pool = None
            it = (work(r) for r in todo)

    for rid, (res, note) in it:
        results[rid] = res
        notes[rid] = note
        done += 1
        if done % 25 == 0 or done == len(todo):
            el = time.time() - t0
            sys.stderr.write("    scored %d/%d · %.1f/s\r"
                             % (done, len(todo), done / max(el, 1e-9)))
            sys.stderr.flush()
    if pool:
        pool.shutdown()
    sys.stderr.write("\n")

    # F10 - one model per run, or the run is a blend of two scorers.
    if len(backend.resolved_models) > 1:
        print("\n!! SCORER DRIFT: responses came from more than one model:")
        for m, c in backend.resolved_models.most_common():
            print("     %-40s %d responses" % (m, c))
        print("!! Nothing written. Pin an exact dated model ID and re-run (F10).")
        return 1
    resolved = (backend.resolved_models.most_common(1)[0][0]
                if backend.resolved_models else backend.model)
    if resolved != backend.model:
        print("\n!! requested %s, answered by %s - stamping the answering model."
              % (backend.model, resolved))

    stamped_at = utcnow()
    out_rows = []
    for r in todo:
        res = results[r["review_id"]]
        res["rubric_version"] = rubric_version
        res["scored_at"] = stamped_at
        if not res["scoring_failed"]:
            res["scorer_model_id"] = resolved
        out_rows.append({k: res[k] for k in SCORE_FIELDS})
    append_scores(paths["scores"], out_rows)

    report = build_report(todo, out_rows, notes, rubric_version, resolved,
                          backend, stamped_at, paths, sampling=sampling,
                          cache_requested=not args.no_cache)
    with open(paths["report"], "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    # The sample record goes in the manifest as well as the report, because the
    # report is rewritten by the next scoring run and the manifest is the file
    # downstream stages read for provenance.
    if sampling:
        update_manifest(paths["manifest"], "scoring_sample", sampling)

    print_report(report, paths)
    if sampling:
        print("sample record   -> %s" % paths["manifest"])
        print("\n!! DOWNSTREAM: %s" % SAMPLING_DOWNSTREAM)

    cache = report["cache"]
    if args.require_cache and cache["engaged"] is not True:
        die("--require-cache was given and caching did not engage: "
            "cache_read_input_tokens = %d over %d call(s)%s.\n\n"
            "The scores are written and are fine - this refusal is about the\n"
            "COST claim, not the measurement. A run that assumed the discount\n"
            "because the marker was accepted would report a saving it never\n"
            "received. Drop --require-cache to proceed without the discount,\n"
            "or move to a model whose minimum cacheable prefix the rubric\n"
            "actually clears."
            % (cache["cache_read_input_tokens"],
               cache["n_calls_reporting_usage"],
               "" if cache["min_cacheable_prefix_tokens"] is None
               else ", against a %d-token minimum for this model"
                    % cache["min_cacheable_prefix_tokens"]), code=1)

    if report["failure_rate"] > args.max_failure_rate:
        print("\n!! BLOCKED: %.1f%% of reviews failed to score, over the %.1f%% limit."
              % (100 * report["failure_rate"], 100 * args.max_failure_rate))
        print("!! The rows are in scores.csv marked scoring_failed - inspect them.")
        print("!! A quarter that lost this many reviews is not a clean quarter (F13).")
        return 1
    return 0


def build_report(todo, out_rows, notes, rubric_version, resolved, backend,
                 stamped_at, paths, sampling=None, cache_requested=False):
    by_id = {r["review_id"]: r for r in todo}
    failed = [r for r in out_rows if r["scoring_failed"]]
    ok = [r for r in out_rows if not r["scoring_failed"]]
    n = len(out_rows)

    kinds = Counter(notes[r["review_id"]][0] for r in out_rows)
    tone_dist = Counter(r["tone"] for r in ok)

    # Per-quarter, so F13's >10% rule can be applied where it bites.
    per_q = defaultdict(lambda: {"n": 0, "failed": 0, "seller_only": 0})
    for r in out_rows:
        q = by_id[r["review_id"]].get("fiscal_quarter", "") or "(none)"
        per_q[q]["n"] += 1
        if r["scoring_failed"]:
            per_q[q]["failed"] += 1
        elif r["seller_or_shipping_only"]:
            per_q[q]["seller_only"] += 1
    for q, d in per_q.items():
        d["failure_rate"] = d["failed"] / float(d["n"]) if d["n"] else 0.0
        d["flagged_over_10pct"] = d["failure_rate"] > 0.10

    # F8 - the scorer never saw the star, so this correlation is the check.
    # Near 0.95 means the text added nothing and the result is about stars.
    xs, ys = [], []
    for r in ok:
        try:
            star = float(by_id[r["review_id"]].get("rating"))
        except (TypeError, ValueError):
            continue
        xs.append(int(r["tone"]))
        ys.append(star)
    corr = pearson(xs, ys)

    return {
        "scored_at": stamped_at,
        "reviews_path": paths["reviews"],
        "scores_path": paths["scores"],
        "rubric_version": rubric_version,
        "rubric_sha256": RUBRIC_SHA256[rubric_version],
        "scorer_model_id": resolved,
        "backend": backend.kind,
        "batch_tier": bool(getattr(backend, "batched", False)),
        "batch_ids": list(getattr(backend, "batch_ids", [])),
        "temperature": 0,
        "reviews_per_call": 1,
        "conversation_history": False,
        # The sample, carried into the scoring report as well as the manifest,
        # so a reader of this file alone can tell a 150-review quarter drawn
        # from 509 from a quarter that genuinely held 150. Null on a full run.
        "sampling": sampling,
        "cache": cache_verdict(backend.usage, resolved, cache_requested,
                               len(out_rows)),
        "cost": cost_block(backend.usage, resolved,
                           getattr(backend, "batched", False)),
        "n_attempted": n,
        "n_scored": len(ok),
        "n_failed": len(failed),
        "failure_rate": (len(failed) / float(n)) if n else 0.0,
        "n_failed_malformed": kinds.get("malformed", 0),
        "n_failed_transport": kinds.get("transport", 0),
        "n_ok_after_retry": kinds.get("ok_after_retry", 0),
        "tone_distribution": {str(k): v for k, v in sorted(tone_dist.items())},
        "n_seller_or_shipping_only": sum(1 for r in ok if r["seller_or_shipping_only"]),
        "n_defect": sum(1 for r in ok if r["defect"]),
        "n_price_complaint": sum(1 for r in ok if r["price_complaint"]),
        "n_in_tone_aggregate": sum(1 for r in ok if not r["seller_or_shipping_only"]),
        "tone_star_pearson": corr,
        "tone_star_pearson_n": len(xs),
        "per_quarter": dict(per_q),
    }


def print_report(rep, paths):
    print("\n--- scoring report ---------------------------------------------")
    print("attempted          %d" % rep["n_attempted"])
    print("scored             %d" % rep["n_scored"])
    print("failed             %d  (%.1f%%)  malformed %d · transport %d"
          % (rep["n_failed"], 100 * rep["failure_rate"],
             rep["n_failed_malformed"], rep["n_failed_transport"]))
    if rep["n_ok_after_retry"]:
        print("recovered on retry %d" % rep["n_ok_after_retry"])
    print("\ntone distribution (scored rows)")
    total = max(rep["n_scored"], 1)
    for t in ("-2", "-1", "0", "1", "2"):
        c = rep["tone_distribution"].get(t, 0)
        print("  %+3s  %6d  %5.1f%%  %s" % (t, c, 100.0 * c / total, "#" * int(40.0 * c / total)))
    print("\ntags")
    print("  seller_or_shipping_only  %6d   (excluded from the tone aggregate)"
          % rep["n_seller_or_shipping_only"])
    print("  defect                   %6d" % rep["n_defect"])
    print("  price_complaint          %6d" % rep["n_price_complaint"])
    print("  in tone aggregate        %6d" % rep["n_in_tone_aggregate"])
    c = rep["tone_star_pearson"]
    print("\nF8 check · pearson(tone, star) = %s  on n=%d"
          % ("n/a" if c is None else "%.3f" % c, rep["tone_star_pearson_n"]))
    if c is not None and abs(c) >= 0.95:
        print("  !! ~0.95 means the text added nothing over the star average.")
        print("  !! Treat any downstream result as a result about stars (F8).")
    flagged = sorted(q for q, d in rep["per_quarter"].items()
                     if d["flagged_over_10pct"])
    if flagged:
        print("\n!! quarters losing >10% of reviews to scoring failure (F13):")
        for q in flagged:
            d = rep["per_quarter"][q]
            print("     %-12s %d/%d failed (%.1f%%)"
                  % (q, d["failed"], d["n"], 100 * d["failure_rate"]))
    c = rep.get("cache") or {}
    if c.get("requested"):
        eng = c.get("engaged")
        print("\ncache · requested, engaged = %s"
              % {True: "YES", False: "NO", None: "unknown"}[eng])
        print("  cache_read_input_tokens      %10d" % c["cache_read_input_tokens"])
        print("  cache_creation_input_tokens  %10d"
              % c["cache_creation_input_tokens"])
        if c.get("min_cacheable_prefix_tokens"):
            print("  this model's minimum prefix  %10d tokens"
                  % c["min_cacheable_prefix_tokens"])
        if c.get("note"):
            print("  !! %s" % c["note"])

    cost = rep.get("cost") or {}
    u = cost.get("usage") or {}
    if u.get("n_calls"):
        print("\ncost · %s%s"
              % (cost.get("price_source", ""),
                 " · BATCH TIER (50% off)" if cost.get("batch_tier") else ""))
        print("  input / output tokens        %10d / %d"
              % (u.get("input_tokens", 0), u.get("output_tokens", 0)))
        if cost.get("usd_total") is None:
            print("  !! %s" % cost.get("note", "no price for this model"))
        else:
            print("  measured total               $%10.4f" % cost["usd_total"])
            print("  measured per review          $%10.6f"
                  % cost["usd_per_review"])

    print("\nscores  -> %s" % paths["scores"])
    print("report  -> %s" % paths["report"])


# --------------------------------------------------------------------------
# B3 - calibration
# --------------------------------------------------------------------------

def cmd_calibrate_draw(args, paths, rubric_version, model_id, win=None):
    """Draw a stratified sample and write a blind labelling sheet.

    The draw is restricted to the window too. A calibration sample drawn from
    held-out reviews would have a human read them, which is precisely the
    inspection s1.4 forbids - and it happens before any model scores them.
    """
    reviews = {r["review_id"]: r for r in read_reviews(paths["reviews"], win)}
    scores = select_scores(load_scores(paths["scores"]), rubric_version, model_id)
    if not scores:
        die("no rows in %s for (rubric %s, model %s).\n"
            "Run scoring first - the sample is stratified across the model's own\n"
            "score range, so it needs model scores to stratify on."
            % (paths["scores"], rubric_version, model_id))
    synthetic = model_id.startswith("stub:")

    pool = [r for r in scores
            if r.get("scoring_failed", "").lower() != "true"
            and r["review_id"] in reviews]
    if len(pool) < args.n:
        die("only %d scored reviews available, need %d.\n"
            "The rubric's gate is defined on a 100-review sample; a smaller one\n"
            "is not the gate. Score more reviews first." % (len(pool), args.n))

    strata = defaultdict(list)
    for r in pool:
        strata[int(r["tone"])].append(r["review_id"])
    rng = random.Random(args.seed)
    for v in strata.values():
        v.sort()
        rng.shuffle(v)

    # Equal targets across the five tone values; a thin stratum is topped up from
    # the fattest remaining ones so the draw is still n, and the realised
    # composition is recorded rather than quietly rebalanced.
    present = [t for t in TONE_VALUES if strata[t]]
    per = args.n // max(len(present), 1)
    picked, leftover = [], []
    for t in TONE_VALUES:
        take = strata[t][:per]
        picked.extend((t, rid) for rid in take)
        leftover.extend((t, rid) for rid in strata[t][per:])
    rng.shuffle(leftover)
    picked.extend(leftover[:max(args.n - len(picked), 0)])
    rng.shuffle(picked)  # order must not reveal the strata

    comp = Counter(t for t, _ in picked)
    missing = [t for t in TONE_VALUES if not strata[t]]

    # Blind sheet: no model tone, and NO STAR RATING. Showing the human the star
    # makes the human labels star-contaminated and the agreement circular on the
    # other side of the comparison (F8).
    with open(paths["cal_sample"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["review_id", "title", "text",
                                          "human_tone",
                                          "human_seller_or_shipping_only"])
        w.writeheader()
        for _, rid in picked:
            r = reviews[rid]
            w.writerow({"review_id": rid, "title": r.get("title", ""),
                        "text": r.get("text", ""), "human_tone": "",
                        "human_seller_or_shipping_only": ""})

    with open(paths["cal_key"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["review_id", "model_tone", "stratum"])
        w.writeheader()
        for t, rid in picked:
            w.writerow({"review_id": rid, "model_tone": t, "stratum": t})

    meta = {"drawn_at": utcnow(), "n": len(picked), "seed": args.seed,
            "rubric_version": rubric_version, "scorer_model_id": model_id,
            "rubric_sha256": RUBRIC_SHA256[rubric_version],
            "pool_size": len(pool),
            "stratum_composition": {str(k): v for k, v in sorted(comp.items())},
            "strata_absent_from_pool": missing,
            "gate": "pending_human_labels"}
    with open(paths["cal_result"], "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)

    if synthetic:
        print("!! these are SYNTHETIC stub scores. The stratification carries no")
        print("!! information and the gate will refuse to evaluate labels against")
        print("!! them. Drawing is allowed only to exercise the mechanics.\n")
    print("drew %d reviews, stratified across the model's score range" % len(picked))
    print("  pool              %d scored, non-failed reviews" % len(pool))
    print("  seed              %d  (recorded; the draw is reproducible)" % args.seed)
    print("  composition       %s"
          % ", ".join("%+d:%d" % (k, v) for k, v in sorted(comp.items())))
    if missing:
        print("  !! no reviews at tone %s - those strata are unrepresented and"
              % ", ".join("%+d" % t for t in missing))
        print("  !! kappa is weakest exactly where the sample is thin.")
    print("\nblind labelling sheet -> %s" % paths["cal_sample"])
    print("model answers (DO NOT OPEN BEFORE LABELLING) -> %s" % paths["cal_key"])
    print("""
How to label
  1. Open the sheet. It carries review_id, title and text only - no model score
     and no star rating. Both are withheld on purpose: a human who sees either
     is no longer an independent rater.
  2. Fill `human_tone` with an integer in -2..+2 against tone-rubric-%s.md.
     Optionally fill `human_seller_or_shipping_only` with true/false.
  3. Save and run:

       python3 %s %s --calibrate --labels %s

Gate: quadratic-weighted kappa >= %.2f AND adjacent agreement >= %.2f.
Below either, the pipeline is blocked.""" % (
        rubric_version, os.path.relpath(os.path.abspath(__file__)),
        paths["ticker"], paths["cal_sample"], GATE_KAPPA, GATE_ADJACENT))
    print("\ngate status: PENDING - no human labels yet, so B3 is unmet.")
    return 1


def parse_bool(s):
    s = (s or "").strip().lower()
    if s in ("true", "t", "yes", "y", "1"):
        return True
    if s in ("false", "f", "no", "n", "0"):
        return False
    return None


def cmd_calibrate_report(args, paths, rubric_version, model_id):
    """Compute agreement against human labels and enforce the gate."""
    if model_id.startswith("stub:"):
        die("refusing to compute a calibration gate against synthetic stub\n"
            "scores. The stub reads only a prompt hash, so agreement with it\n"
            "measures nothing. Score with a real model first.", code=1)
    labels = read_csv(args.labels)
    if "human_tone" not in (labels[0] if labels else {}):
        die("%s has no `human_tone` column" % args.labels)
    key = {r["review_id"]: r for r in read_csv(paths["cal_key"])}

    pairs, tag_pairs = [], []
    unlabelled, unknown, bad = 0, [], []
    for r in labels:
        rid = r["review_id"]
        raw = (r.get("human_tone") or "").strip()
        if not raw:
            unlabelled += 1
            continue
        if rid not in key:
            unknown.append(rid)
            continue
        try:
            h = int(raw)
        except ValueError:
            bad.append((rid, raw))
            continue
        if h not in TONE_VALUES:
            bad.append((rid, raw))
            continue
        pairs.append((h, int(key[rid]["model_tone"])))
        hb = parse_bool(r.get("human_seller_or_shipping_only"))
        if hb is not None:
            tag_pairs.append((rid, hb))

    if unknown:
        die("%d labelled review_id(s) are not in %s - the labels came from a\n"
            "different draw. Re-draw or supply the matching sheet.\n  %s"
            % (len(unknown), paths["cal_key"], ", ".join(unknown[:5])))
    if bad:
        die("%d human_tone value(s) are not integers in -2..+2:\n  %s"
            % (len(bad), ", ".join("%s=%r" % b for b in bad[:5])))

    n = len(pairs)
    exact, adjacent = agreement_rates(pairs)
    kappa = quadratic_weighted_kappa(pairs)
    k_lo, k_hi = bootstrap_ci(pairs, quadratic_weighted_kappa, reps=args.boot,
                              seed=args.seed)
    a_lo, a_hi = bootstrap_ci(pairs, lambda p: agreement_rates(p)[1],
                              reps=args.boot, seed=args.seed)

    # Secondary, not part of the gate: agreement on the exclusion tag.
    tag_exact = None
    if tag_pairs:
        scores = {r["review_id"]: r for r in
                  select_scores(load_scores(paths["scores"]), rubric_version, model_id)}
        hits = tot = 0
        for rid, hb in tag_pairs:
            if rid in scores:
                mb = parse_bool(scores[rid].get("seller_or_shipping_only"))
                if mb is not None:
                    tot += 1
                    hits += int(mb == hb)
        tag_exact = (hits / float(tot)) if tot else None

    cats = list(TONE_VALUES)
    m = confusion_matrix(pairs, cats)
    short = n < args.n

    passed = (kappa is not None and adjacent is not None
              and kappa >= GATE_KAPPA and adjacent >= GATE_ADJACENT
              and not short)

    result = {
        "evaluated_at": utcnow(), "labels_path": args.labels,
        "rubric_version": rubric_version,
        "rubric_sha256": RUBRIC_SHA256[rubric_version],
        "scorer_model_id": model_id,
        "n_required": args.n, "n_labelled": n, "n_unlabelled": unlabelled,
        "exact_agreement": exact, "adjacent_agreement": adjacent,
        "adjacent_agreement_ci": [a_lo, a_hi],
        "quadratic_weighted_kappa": kappa,
        "quadratic_weighted_kappa_ci": [k_lo, k_hi],
        "bootstrap_reps": args.boot, "bootstrap_seed": args.seed,
        "gate_kappa_min": GATE_KAPPA, "gate_adjacent_min": GATE_ADJACENT,
        "seller_tag_exact_agreement_secondary": tag_exact,
        "confusion_human_rows_model_cols": m,
        "categories": cats,
        "gate": "pass" if passed else "blocked",
    }
    with open(paths["cal_result"], "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)

    print("--- calibration -------------------------------------------------")
    print("rubric             %s (sha256 %s…)"
          % (rubric_version, RUBRIC_SHA256[rubric_version][:12]))
    print("scorer             %s" % model_id)
    print("labelled           %d of %d required%s"
          % (n, args.n, "   !! SHORT" if short else ""))
    if unlabelled:
        print("left blank         %d" % unlabelled)
    print("\nconfusion (rows = human, cols = model)")
    print("        " + "".join("%6s" % ("%+d" % c) for c in cats))
    for i, c in enumerate(cats):
        print("   %+3d  " % c + "".join("%6d" % v for v in m[i]))
    print("\nexact agreement    %s" % fmt(exact))
    print("adjacent (<=1)     %s   95%% CI [%s, %s]   gate >= %.2f"
          % (fmt(adjacent), fmt(a_lo), fmt(a_hi), GATE_ADJACENT))
    print("QW kappa           %s   95%% CI [%s, %s]   gate >= %.2f"
          % (fmt(kappa), fmt(k_lo), fmt(k_hi), GATE_KAPPA))
    if tag_exact is not None:
        print("seller tag agree   %s   (secondary - not part of the gate)"
              % fmt(tag_exact))
    if kappa is None:
        print("\n!! kappa is undefined: one of the raters used a single category,")
        print("!! so there is no expected disagreement to correct against.")
    print("\nresult -> %s" % paths["cal_result"])

    if passed:
        print("\nGATE PASSED. B3 is met for (%s, %s)." % (rubric_version, model_id))
        print("Re-run calibration whenever the rubric version or the model ID changes.")
        return 0

    print("\n" + "=" * 64)
    print("PIPELINE BLOCKED - calibration gate not met")
    print("=" * 64)
    if short:
        print("  n = %d, the gate is defined on %d." % (n, args.n))
    if kappa is None:
        print("  kappa undefined")
    elif kappa < GATE_KAPPA:
        print("  kappa %.3f < %.2f" % (kappa, GATE_KAPPA))
    if adjacent is not None and adjacent < GATE_ADJACENT:
        print("  adjacent agreement %.3f < %.2f" % (adjacent, GATE_ADJACENT))
    print("\n  Fix the rubric (as a new version) or the model. Do not proceed with")
    print("  an unvalidated scorer: it makes every downstream statistic decorative.")
    return 1


def fmt(v):
    return "n/a" if v is None else "%.3f" % v


def calibration_status(outdir, ticker, rubric_version, model_id):
    """Importable gate check for downstream stages. Returns (ok, detail)."""
    path = os.path.join(outdir, ticker, "calibration.json")
    if not os.path.exists(path):
        return False, "no calibration.json at %s" % path
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    if r.get("gate") != "pass":
        return False, "calibration gate is %r" % r.get("gate")
    if r.get("rubric_version") != rubric_version:
        return False, ("calibration is for rubric %r, scoring used %r"
                       % (r.get("rubric_version"), rubric_version))
    if r.get("scorer_model_id") != model_id:
        return False, ("calibration is for model %r, scoring used %r"
                       % (r.get("scorer_model_id"), model_id))
    return True, "pass"


def require_calibration(outdir, ticker, rubric_version, model_id):
    ok, detail = calibration_status(outdir, ticker, rubric_version, model_id)
    if not ok:
        die("PIPELINE BLOCKED at B3 for %s: %s\n\n"
            "CORE-PROMPT s7: agreement below the rubric threshold blocks the\n"
            "pipeline. Run:  score_reviews.py %s --calibrate"
            % (ticker, detail, ticker), code=1)


# --------------------------------------------------------------------------
# Offline self-test
# --------------------------------------------------------------------------

def cmd_selftest(args, paths, rubric_version):
    """Exercise every path that does not need the API, and assert F8/F13."""
    fails = []

    def check(name, cond, detail=""):
        print("  %-52s %s" % (name, "ok" if cond else "FAIL " + detail))
        if not cond:
            fails.append(name)

    print("--- self-test ---------------------------------------------------")
    print("\n1 · frozen rubric")
    text = load_rubric(rubric_version)
    sysp = build_system_prompt(text)
    check("rubric hash matches the pin", True)
    check("prompt carries the Scale table verbatim",
          "Strongly positive. Delighted" in sysp)
    check("prompt carries the Output format", '"seller_or_shipping_only"' in sysp)
    check("calibration-gate section withheld from the scorer",
          "Calibration gate" not in sysp)

    print("\n2 · F8 · the star rating cannot reach the scorer")
    check("template has no rating/star placeholder",
          not any(b in PROMPT_TEMPLATE.lower() for b in ("rating", "star")))
    hot = {"review_id": "r1", "title": "Works great", "text": "Love it, five years on.",
           "rating": "1.0", "verified_purchase": "True", "helpful_vote": "99"}
    cold = dict(hot, rating="5.0", helpful_vote="0")
    p1, p2 = prompt_for_row(hot), prompt_for_row(cold)
    check("identical text + different star -> identical prompt", p1 == p2,
          "prompts differ")
    check("rendered prompt holds only id/title/text",
          p1 == "review_id: r1\ntitle: Works great\ntext: Love it, five years on.")
    check("'rating' absent from the rendered prompt", "rating" not in p1.lower())
    try:
        build_prompt({"review_id": "r1"}, "", "")
        check("build_prompt refuses a row object", False, "accepted a dict")
    except TypeError:
        check("build_prompt refuses a row object", True)

    print("\n3 · response validation")
    good = '{"review_id":"r1","tone":-2,"seller_or_shipping_only":false,' \
           '"defect":true,"price_complaint":false}'
    check("valid object parses", parse_response(good, "r1")["tone"] == -2)
    for name, bad in (
        ("prose rejected", "The tone is -2."),
        ("code fence rejected", "```json\n" + good + "\n```"),
        ("missing key rejected", '{"review_id":"r1","tone":0,"defect":false}'),
        ("extra key rejected", good[:-1] + ',"confidence":0.9}'),
        ("tone 3 rejected", good.replace("-2", "3")),
        ("tone as string rejected", good.replace("-2", '"-2"')),
        ("tone as bool rejected", good.replace("-2", "true")),
        ("tag as string rejected", good.replace("false", '"false"', 1)),
        ("wrong review_id rejected", good.replace('"r1"', '"r2"', 1)),
    ):
        try:
            parse_response(bad, "r1")
            check(name, False, "accepted")
        except Malformed:
            check(name, True)

    print("\n4 · calibration arithmetic against known answers")
    perfect = [(t, t) for t in TONE_VALUES for _ in range(20)]
    check("perfect agreement -> kappa 1.0",
          abs(quadratic_weighted_kappa(perfect) - 1.0) < 1e-12)
    check("perfect agreement -> exact 1.0, adjacent 1.0",
          agreement_rates(perfect) == (1.0, 1.0))
    off = [(t, min(t + 1, 2)) for t in TONE_VALUES for _ in range(20)]
    e, a = agreement_rates(off)
    check("one-step-high rater -> adjacent 1.0", abs(a - 1.0) < 1e-12)
    check("one-step-high rater -> exact 0.2", abs(e - 0.2) < 1e-12)
    check("both raters constant -> kappa undefined, not 1.0",
          quadratic_weighted_kappa([(0, 0)] * 100) is None)
    check("exact agreement alone does not define kappa",
          agreement_rates([(0, 0)] * 100) == (1.0, 1.0))
    const = [(t, 0) for t in TONE_VALUES for _ in range(20)]
    check("model always 0 against a spread human -> kappa 0.0",
          abs(quadratic_weighted_kappa(const)) < 1e-12)
    # Hand-checked 3x3-style case embedded in the 5-category frame.
    pairs = [(-2, -2)] * 10 + [(-2, 2)] * 10 + [(2, -2)] * 10 + [(2, 2)] * 10
    check("symmetric half-wrong extremes -> kappa 0.0",
          abs(quadratic_weighted_kappa(pairs)) < 1e-12)
    check("adjacent counts |h-m|<=1 only",
          agreement_rates([(0, 1), (0, -1), (0, 2)])[1] == 2 / 3.0)
    check("gate rejects kappa just under the line", 0.599 < GATE_KAPPA)
    check("pearson identical series = 1.0",
          abs(pearson([1, 2, 3], [1, 2, 3]) - 1.0) < 1e-12)
    check("pearson constant series = None", pearson([1, 1, 1], [1, 2, 3]) is None)

    print("\n5 · F13 · failures are recorded, never dropped")
    reviews = read_reviews(paths["reviews"])
    # Enough reviews that all three simulated failure classes appear; the stub
    # fails on prompt hash, so a small sample can miss a class entirely.
    want = max(args.limit or SELFTEST_MIN, SELFTEST_MIN)
    sample = reviews[:want]
    if len(sample) < SELFTEST_MIN:
        print("  !! only %d reviews available; %d are needed to hit every"
              % (len(sample), SELFTEST_MIN))
        print("  !! simulated failure class, so section 5 may under-report.")
    backend = StubBackend()
    out, notes = [], {}
    for r in sample:
        res, note = score_one(backend, r)
        res["rubric_version"] = rubric_version
        res["scored_at"] = utcnow()
        out.append({k: res[k] for k in SCORE_FIELDS})
        notes[r["review_id"]] = note
    nfail = sum(1 for r in out if r["scoring_failed"])
    check("one row written per attempted review", len(out) == len(sample))
    check("failures present and counted", nfail > 0, "no failures simulated")
    check("failed rows carry empty tone",
          all(r["tone"] == "" for r in out if r["scoring_failed"]))
    check("scored rows carry an integer tone",
          all(isinstance(r["tone"], int) for r in out if not r["scoring_failed"]))
    guard_stub_path(paths["scores"])
    # The self-test owns this fixture file; append-only protects real scorer
    # vintages, not a synthetic scratch file re-created on every run.
    if os.path.exists(paths["scores"]):
        os.remove(paths["scores"])
    append_scores(paths["scores"], out)
    written = select_scores(load_scores(paths["scores"]), rubric_version,
                            STUB_MODEL_ID)
    check("rows survive the csv round-trip", len(written) == len(out))
    check("every contract column present",
          all(set(r) == set(SCORE_FIELDS) for r in written))
    check("append-only: re-reading finds no other scorer vintage",
          all(r["scorer_model_id"] == STUB_MODEL_ID for r in written))
    rep = build_report(sample, out, notes, rubric_version, STUB_MODEL_ID,
                       backend, utcnow(), paths)
    with open(paths["report"], "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, sort_keys=True)
    check("report failure count matches rows", rep["n_failed"] == nfail)
    check("malformed and transport failures counted apart",
          rep["n_failed_malformed"] > 0 and rep["n_failed_transport"] > 0,
          "malformed=%d transport=%d" % (rep["n_failed_malformed"],
                                         rep["n_failed_transport"]))
    check("a one-off malformed response recovers on the single retry",
          rep["n_ok_after_retry"] > 0)
    check("a systematically malformed review is not retried into existence",
          rep["n_failed_malformed"] + rep["n_failed_transport"] == rep["n_failed"])
    check("scored + failed = attempted",
          rep["n_scored"] + rep["n_failed"] == rep["n_attempted"])
    check("seller-only rows counted but still in the file",
          rep["n_in_tone_aggregate"] + rep["n_seller_or_shipping_only"]
          == rep["n_scored"])

    print("\n6 · stub containment")
    check("stub rows stamped as stub",
          all(r["scorer_model_id"] == STUB_MODEL_ID for r in out))
    try:
        guard_stub_path("data/HELE/scores.csv")
        check("stub refuses to write under data/", False, "allowed")
    except SystemExit:
        check("stub refuses to write under data/", True)

    # ------------------------------------------------------------------
    # The three cost changes. CONTRACTS.md s5: every refusal below has a case
    # here that demonstrably trips it. A guard that cannot fire is
    # indistinguishable from one that passes, and this project has shipped
    # three of those.
    # ------------------------------------------------------------------

    def refuses(name, fn, detail=""):
        try:
            fn()
        except SystemExit as e:
            check(name, e.code != 0, "exited 0")
            return
        except Malformed:
            check(name, False, "raised Malformed, not a refusal")
            return
        check(name, False, detail or "did not refuse")

    print("\n7 · the request body is one body, and it is validated")
    blocks = build_system_blocks(text, cache=True)
    probe = build_prompt("r1", "t", "body")
    params = request_params(STUB_MODEL_ID, blocks, probe, 256)
    check("temperature is 0", params["temperature"] == 0)
    check("exactly one user message",
          len(params["messages"]) == 1
          and params["messages"][0]["role"] == "user")
    check("system carries the rubric, messages carry the review",
          params["system"][0]["text"] == sysp
          and params["messages"][0]["content"] == probe)
    check("cache breakpoint closes the stable prefix",
          "cache_control" in params["system"][-1])
    check("--no-cache omits the breakpoint entirely",
          "cache_control" not in build_system_blocks(text, cache=False)[0])
    # The decisive structural check: caching changes nothing the model reads.
    check("cached and uncached bodies differ ONLY by cache_control",
          _strip_cache(request_params(STUB_MODEL_ID, blocks, probe, 256))
          == _strip_cache(request_params(
              STUB_MODEL_ID, build_system_blocks(text, cache=False), probe, 256)))
    # And that the sync and batch paths send the same body, which is what makes
    # the agreement re-score meaningful rather than a check on one of two paths.
    shim = _StubApiShim(StubBackend())
    check("batch path builds the identical body to the sync path",
          shim.params_for(probe)
          == request_params(STUB_MODEL_ID, blocks, probe, 256))

    refuses("refuses temperature != 0",
            lambda: validate_request_params(dict(params, temperature=0.7)))
    refuses("refuses a second message",
            lambda: validate_request_params(dict(
                params, messages=params["messages"]
                + [{"role": "assistant", "content": "ok"}])))
    refuses("refuses a non-user message",
            lambda: validate_request_params(dict(
                params, messages=[{"role": "assistant", "content": probe}])))
    refuses("refuses an empty system field",
            lambda: validate_request_params(dict(params, system=[])))
    refuses("refuses system text that is not the frozen extraction",
            lambda: validate_request_params(dict(
                params, system=[{"type": "text",
                                 "text": sysp + "\nAlso be lenient."}])))
    refuses("refuses the review text inside the system block",
            lambda: validate_request_params(dict(
                params, system=[{"type": "text", "text": sysp + probe}])))
    refuses("refuses a cache breakpoint before the end of the prefix",
            lambda: validate_request_params(dict(
                params, system=[dict(blocks[0]),
                                {"type": "text", "text": ""}])))
    refuses("refuses a changed output schema",
            lambda: validate_request_params(dict(
                params, output_config={"format": {
                    "type": "json_schema",
                    "schema": {"type": "object"}}})))
    # The guard behind the guard: validating against a rubric that was never
    # hash-checked would make every check above vacuous.
    saved = list(_EXPECTED_SYSTEM)
    del _EXPECTED_SYSTEM[:]
    refuses("refuses to validate before the rubric is hash-checked",
            lambda: validate_request_params(params))
    _EXPECTED_SYSTEM.extend(saved)

    print("\n8 · per-quarter sampling is a method, not a truncation")
    rows = [{"review_id": "r%04d" % i, "title": "", "text": "x",
             "review_date": "2024-%02d-15" % (1 + (i % 12))}
            for i in range(600)]
    seed = env_stamp.stage_seed("scoring_sample")
    check("the stage seed comes from env_stamp's convention",
          seed == env_stamp.DEFAULT_SEED
          + env_stamp.STAGE_OFFSETS["scoring_sample"])
    kept, blk = sample_per_quarter(rows, "T", 30, seed)
    again, blk2 = sample_per_quarter(rows, "T", 30, seed)
    check("same corpus and seed draw the same sample twice",
          [r["review_id"] for r in kept] == [r["review_id"] for r in again])
    check("the record reproduces the draw exactly", blk == blk2)
    other, _ = sample_per_quarter(rows, "T", 30, seed + 1)
    check("a different seed draws a different sample",
          [r["review_id"] for r in kept] != [r["review_id"] for r in other])
    check("no quarter exceeds N",
          all(d["drawn"] <= 30 for d in blk["per_quarter"].values()))
    check("every quarter in the input is represented",
          sorted(blk["per_quarter"]) == ["FY2024Q1", "FY2024Q2",
                                         "FY2024Q3", "FY2024Q4"])
    # Order independence: the draw is a function of the ID SET, not of the file.
    shuffled = list(reversed(rows))
    kept_rev, _ = sample_per_quarter(shuffled, "T", 30, seed)
    check("row order in reviews.csv cannot move the draw",
          sorted(r["review_id"] for r in kept_rev)
          == sorted(r["review_id"] for r in kept))
    # Nested in n: raising N deepens a quarter instead of redrawing it.
    _, deep = sample_per_quarter(rows, "T", 40, seed)
    check("the N=30 draw is a subset of the N=40 draw",
          all(set(blk["per_quarter"][q]["drawn_review_ids"])
              <= set(deep["per_quarter"][q]["drawn_review_ids"])
              for q in blk["per_quarter"]))
    check("the draw never reads a score",
          blk["draw_inputs"] == ["review_ids", "quarter", "seed"])
    # A short quarter is taken whole, and a thin one is reported, not dropped.
    thin = ([{"review_id": "a%d" % i, "title": "", "text": "x",
              "review_date": "2024-02-02"} for i in range(5)]
            + [{"review_id": "b%d" % i, "title": "", "text": "x",
                "review_date": "2024-05-05"} for i in range(400)])
    tkept, tblk = sample_per_quarter(thin, "T", 50, seed)
    check("a quarter shorter than N is taken whole",
          tblk["per_quarter"]["FY2024Q1"]["drawn"] == 5
          and tblk["per_quarter"]["FY2024Q1"]["took_all"])
    check("a quarter under the floor is flagged, not dropped",
          tblk["per_quarter"]["FY2024Q1"]["below_floor"] is True
          and "FY2024Q1" in tblk["per_quarter"])
    check("a quarter over the floor is NOT flagged below it",
          tblk["per_quarter"]["FY2024Q2"]["below_floor"] is False)
    check("the sample keeps exactly what the record says it drew",
          len(tkept) == tblk["drawn_total"] == 55)
    # The requirement that 150-from-509 cannot read as a genuine 150.
    check("the population drawn from travels with the sample",
          tblk["per_quarter"]["FY2024Q2"]["population"] == 400
          and tblk["per_quarter"]["FY2024Q2"]["drawn"] == 50)
    check("the population's id set is hashed, so a changed population shows",
          tblk["per_quarter"]["FY2024Q2"]["population_sha256"]
          != sample_per_quarter(thin[:-1], "T", 50, seed)[1]
          ["per_quarter"]["FY2024Q2"]["population_sha256"])
    refuses("refuses N below the per-quarter floor",
            lambda: sample_per_quarter(rows, "T",
                                       aggmod.MIN_REVIEWS_PER_QUARTER - 1, seed))
    refuses("refuses a review with no assignable quarter",
            lambda: sample_per_quarter(
                rows + [{"review_id": "nq", "title": "", "text": "x",
                         "review_date": ""}], "T", 30, seed))
    check("the quarter labeller is the aggregator's own",
          quarter_of({"review_date": "2024-05-17"})
          == aggmod.period_label({"review_date": "2024-05-17"}))

    print("\n9 · the batch path, against shapes the live endpoint cannot be asked for")
    bsample = reviews[:60]
    bids = [r["review_id"] for r in bsample]
    sb = StubBackend()
    bb = BatchBackend(_StubApiShim(sb), StubBatchTransport(sb, reverse=True),
                      poll_seconds=0, quiet=True)
    got = bb.score_rows(bsample)
    check("one result per submitted review", set(got) == set(bids))
    check("results arriving reversed are still keyed correctly",
          all(got[rid][0]["review_id"] == rid for rid in bids))
    # The decisive one: batch and sync must agree review-for-review.
    sync = dict((r["review_id"], score_one(StubBackend(), r)[0])
                for r in bsample)
    check("batch and synchronous agree on every tone",
          all(got[rid][0]["tone"] == sync[rid]["tone"] for rid in bids))
    check("batch and synchronous agree on every tag",
          all(all(got[rid][0][t] == sync[rid][t] for t in TAGS)
              for rid in bids))
    check("a malformed batch response recovers on the single retry",
          any(n[0] == "ok_after_retry" for _, n in got.values()))
    # errored / canceled / expired are transport, NOT malformed.
    sb2 = StubBackend(fail_transport=0, fail_malformed=0, retry_ok=0)
    bb2 = BatchBackend(_StubApiShim(sb2),
                       StubBatchTransport(sb2, error_ids=bids[:1],
                                          canceled_ids=bids[1:2],
                                          expired_ids=bids[2:3]),
                       poll_seconds=0, quiet=True)
    got2 = bb2.score_rows(bsample)
    check("errored/canceled/expired count as transport, not malformed",
          all(got2[rid][1][0] == "transport" for rid in bids[:3]))
    check("an errored result is written as a failure, never dropped",
          all(got2[rid][0]["scoring_failed"] for rid in bids[:3])
          and set(got2) == set(bids))
    check("the rest of the batch still scores around the failures",
          sum(1 for rid in bids[3:] if not got2[rid][0]["scoring_failed"])
          == len(bids) - 3)
    # A submitted id with no result line is a loss and is recorded as one.
    sb3 = StubBackend(fail_transport=0, fail_malformed=0, retry_ok=0)
    bb3 = BatchBackend(_StubApiShim(sb3),
                       StubBatchTransport(sb3, drop_ids=bids[:2]),
                       poll_seconds=0, quiet=True)
    got3 = bb3.score_rows(bsample)
    check("a submitted id with no result line is a recorded failure",
          set(got3) == set(bids)
          and all(got3[rid][0]["scoring_failed"] for rid in bids[:2])
          and all(got3[rid][1][0] == "transport" for rid in bids[:2]))

    sb4 = StubBackend(fail_transport=0, fail_malformed=0, retry_ok=0)
    refuses("refuses a result for a custom_id it never submitted",
            lambda: BatchBackend(
                _StubApiShim(sb4),
                StubBatchTransport(sb4, extra_custom_id="not-ours"),
                poll_seconds=0, quiet=True).score_rows(bsample))
    refuses("refuses two requests sharing one custom_id",
            lambda: BatchBackend(
                _StubApiShim(sb4), StubBatchTransport(sb4),
                poll_seconds=0, quiet=True).score_rows(bsample + bsample[:1]))
    refuses("refuses a batch that never ends within the wait",
            lambda: BatchBackend(
                _StubApiShim(sb4), StubBatchTransport(sb4, stall_rounds=10**6),
                poll_seconds=0, max_wait=-1, quiet=True).score_rows(bsample[:2]))
    refuses("refuses a submission that returns no batch id",
            lambda: BatchBackend(_StubApiShim(sb4), _NoIdTransport(),
                                 poll_seconds=0, quiet=True
                                 ).score_rows(bsample[:2]))
    refuses("refuses an ended batch with no results_url",
            lambda: BatchBackend(_StubApiShim(sb4), _NoUrlTransport(),
                                 poll_seconds=0, quiet=True
                                 ).score_rows(bsample[:2]))

    print("\n10 · caching and cost are measured, never assumed")
    u = UsageTally()
    u.add({"input_tokens": 940, "output_tokens": 40,
           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})
    v = cache_verdict(u, "claude-haiku-4-5-20251001", True, 1)
    check("cache_control sent + zero reads reads as NOT engaged",
          v["engaged"] is False)
    check("the non-engaging case says the saving is zero, in words",
          "ZERO" in (v.get("note") or ""))
    check("this model's minimum prefix is recorded beside the verdict",
          v["min_cacheable_prefix_tokens"] == 4096)
    u2 = UsageTally()
    u2.add({"input_tokens": 107, "output_tokens": 40,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 833})
    check("a non-zero cache read reads as engaged",
          cache_verdict(u2, "claude-haiku-4-5-20251001", True, 1)["engaged"]
          is True)
    u3 = UsageTally()
    u3.add(None)
    check("no usage at all reads as unknown, never as engaged",
          cache_verdict(u3, "claude-haiku-4-5-20251001", True, 1)["engaged"]
          is None)
    check("caching not requested reads as not engaged",
          cache_verdict(u2, "claude-haiku-4-5-20251001", False, 1)["engaged"]
          is False)
    cb = cost_block(u, "claude-haiku-4-5-20251001", batched=False)
    cbb = cost_block(u, "claude-haiku-4-5-20251001", batched=True)
    check("cost comes out of measured tokens",
          abs(cb["usd_total"] - (940 * 1.0 + 40 * 5.0) / 1e6) < 1e-12)
    check("the batch tier is exactly half",
          abs(cbb["usd_total"] - cb["usd_total"] / 2.0) < 1e-15)
    check("an unpriced model yields null cost, not a guess",
          cost_block(u, "some-future-model", False)["usd_total"] is None)
    check("the stub reports no usage, so no cost can be quoted from it",
          StubBackend().usage.totals["input_tokens"] == 0)

    print("\n11 · refusals that need the whole script, run as a subprocess")
    here = os.path.abspath(__file__)
    sub_out = os.path.join(os.path.dirname(paths["scores"]), "..", "_refuse")

    def run_cli(extra):
        env = dict(os.environ)
        r = subprocess.run(
            [sys.executable, here, "SAMPLE", "--outdir", sub_out] + extra,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        return r.returncode, r.stdout.decode("utf-8", "replace")

    os.makedirs(os.path.join(sub_out, "SAMPLE"), exist_ok=True)
    import shutil
    shutil.copy(paths["reviews"],
                os.path.join(sub_out, "SAMPLE", "reviews.csv"))
    for name, extra in (
        ("refuses --sample-per-quarter with --limit",
         ["--backend", "stub", "--sample-per-quarter", "30", "--limit", "10"]),
        ("refuses --batch with --calibrate",
         ["--batch", "--calibrate"]),
        ("refuses --require-cache when caching did not engage",
         ["--backend", "stub", "--require-cache", "--limit", "5"]),
    ):
        rc, outp = run_cli(extra)
        check(name, rc != 0, "exited 0:\n%s" % outp[-400:])

    # ------------------------------------------------------------------
    # The refusals that were already here and had never fired. The refusal
    # audit found 18 of them when it was first pointed at this file - the same
    # finding it made on the peer-group component, where 24 of 70 had never
    # fired. They are covered here because §5's rule is about the file, not
    # about whichever refusal was added most recently.
    # ------------------------------------------------------------------
    print("\n12 · refusals that were already here and nothing tripped")
    tmpd = tempfile.mkdtemp(prefix="score-selftest-")

    refuses("refuses a rubric version with no file",
            lambda: load_rubric("v0-does-not-exist"))
    saved_pins = dict(RUBRIC_SHA256)
    try:
        RUBRIC_SHA256["vtest"] = None
        del RUBRIC_SHA256["vtest"]
        # An unpinned version: the file must exist, so point the loader at a
        # copy under a version label carrying no pin.
        shutil.copy(rubric_path(rubric_version),
                    os.path.join(RUBRIC_DIR, "tone-rubric-vunpinned.md"))
        refuses("refuses a rubric version with no pinned hash",
                lambda: load_rubric("vunpinned"))
        # An edited frozen rubric: same label, different bytes.
        with open(os.path.join(RUBRIC_DIR, "tone-rubric-vedited.md"), "w",
                  encoding="utf-8") as f:
            f.write(text + "\n6. Be a bit more generous.\n")
        RUBRIC_SHA256["vedited"] = RUBRIC_SHA256[rubric_version]
        refuses("refuses a frozen rubric that was edited in place",
                lambda: load_rubric("vedited"))
    finally:
        RUBRIC_SHA256.clear()
        RUBRIC_SHA256.update(saved_pins)
        for v in ("vunpinned", "vedited"):
            p = os.path.join(RUBRIC_DIR, "tone-rubric-%s.md" % v)
            if os.path.exists(p):
                os.remove(p)
    refuses("refuses a rubric missing a section the prompt needs",
            lambda: extract_sections("## Scale\n\nonly this one\n",
                                     PROMPT_SECTIONS))

    def write_csv(name, rows, header=None):
        p = os.path.join(tmpd, name)
        with open(p, "w", newline="", encoding="utf-8") as f:
            if rows:
                w = csv.DictWriter(f, fieldnames=header or list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            elif header:
                csv.writer(f).writerow(header)
        return p

    refuses("refuses a reviews.csv that is not there",
            lambda: read_reviews(os.path.join(tmpd, "nope.csv")))
    refuses("refuses a reviews.csv with a header and no rows",
            lambda: read_reviews(write_csv(
                "empty.csv", [], ["review_id", "title", "text"])))
    refuses("refuses a reviews.csv missing a required column",
            lambda: read_reviews(write_csv(
                "nocols.csv", [{"review_id": "a", "title": "t"}])))
    # review_id is a hash of the fields that identify a review, so one id
    # keying two different texts would merge two reviews into one score.
    refuses("refuses a review_id that keys two different reviews",
            lambda: read_reviews(write_csv("collide.csv", [
                {"review_id": "dup", "title": "a", "text": "one"},
                {"review_id": "dup", "title": "b", "text": "two"}])))
    # And the benign twin of that case must NOT refuse: the 2023 corpus holds
    # exact duplicate records, which are dropped and counted. A guard that
    # fired on these would be switched off and then guard nothing (§5).
    dupes_ok = read_reviews(write_csv("exactdup.csv", [
        {"review_id": "dup", "title": "a", "text": "one"},
        {"review_id": "dup", "title": "a", "text": "one"}]))
    check("an EXACT duplicate row is dropped, not refused",
          len(dupes_ok) == 1)

    # The two window refusals. window.py restricts nothing in the live design
    # and says so, so these can only be reached through a window that does -
    # which is the state the module is kept in its current shape to return to.
    class _RestrictingWindow(object):
        mode, restricts = "dev", True

        def __init__(self, allowed):
            self._allowed = allowed

        def allows(self, label):
            return label in self._allowed

        def describe(self):
            return "test window allowing %s" % sorted(self._allowed)

    refuses("refuses a windowed run over reviews with no fiscal_quarter",
            lambda: read_reviews(write_csv("noq.csv", [
                {"review_id": "a", "title": "t", "text": "x"}]),
                _RestrictingWindow({"FY2024Q1"})))
    refuses("refuses a window that excludes every review",
            lambda: read_reviews(write_csv("outq.csv", [
                {"review_id": "a", "title": "t", "text": "x",
                 "fiscal_quarter": "FY2030Q1"}]),
                _RestrictingWindow({"FY2024Q1"})))
    kept_win = read_reviews(write_csv("inq.csv", [
        {"review_id": "a", "title": "t", "text": "x",
         "fiscal_quarter": "FY2024Q1"},
        {"review_id": "b", "title": "t", "text": "y",
         "fiscal_quarter": "FY2030Q1"}]), _RestrictingWindow({"FY2024Q1"}))
    check("a window that allows some reviews keeps exactly those",
          [r["review_id"] for r in kept_win] == ["a"])

    # No API key. Checked by constructing the backend with the variable blank.
    saved_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        os.environ["ANTHROPIC_API_KEY"] = ""
        refuses("refuses to score with no ANTHROPIC_API_KEY",
                lambda: ApiBackend("claude-haiku-4-5-20251001", sysp))
    finally:
        if saved_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved_key

    # The calibration refusals.
    cal_paths = dict(paths)
    for k in ("scores", "cal_key", "cal_sample", "cal_result"):
        cal_paths[k] = os.path.join(tmpd, os.path.basename(paths[k]))
    cal_paths["reviews"] = write_csv("calrev.csv", [
        {"review_id": "a", "title": "t", "text": "x"}])

    class _A(object):
        n, seed, boot, labels = 100, 20261011, 50, None

    refuses("refuses a calibration draw with no scores to stratify on",
            lambda: cmd_calibrate_draw(_A(), cal_paths, rubric_version,
                                       STUB_MODEL_ID))
    append_scores(cal_paths["scores"], [{
        "review_id": "a", "tone": 1, "seller_or_shipping_only": False,
        "defect": False, "price_complaint": False, "scoring_failed": False,
        "rubric_version": rubric_version, "scorer_model_id": STUB_MODEL_ID,
        "scored_at": utcnow()}])
    refuses("refuses a calibration sample smaller than the gate's 100",
            lambda: cmd_calibrate_draw(_A(), cal_paths, rubric_version,
                                       STUB_MODEL_ID))
    refuses("refuses a calibration gate computed against stub scores",
            lambda: cmd_calibrate_report(_A(), cal_paths, rubric_version,
                                         STUB_MODEL_ID))

    class _AL(_A):
        labels = None

    al = _AL()
    al.labels = write_csv("nohuman.csv", [{"review_id": "a", "tone": "1"}])
    refuses("refuses a labels file with no human_tone column",
            lambda: cmd_calibrate_report(al, cal_paths, rubric_version,
                                         "claude-haiku-4-5-20251001"))
    with open(cal_paths["cal_key"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["review_id", "model_tone", "stratum"])
        w.writeheader()
        w.writerow({"review_id": "a", "model_tone": 1, "stratum": 1})
    al2 = _AL()
    al2.labels = write_csv("otherdraw.csv",
                           [{"review_id": "zzz", "human_tone": "1"}])
    refuses("refuses labels that came from a different draw",
            lambda: cmd_calibrate_report(al2, cal_paths, rubric_version,
                                         "claude-haiku-4-5-20251001"))
    al3 = _AL()
    al3.labels = write_csv("badtone.csv",
                           [{"review_id": "a", "human_tone": "lovely"}])
    refuses("refuses a human_tone that is not an integer in -2..+2",
            lambda: cmd_calibrate_report(al3, cal_paths, rubric_version,
                                         "claude-haiku-4-5-20251001"))

    refuses("refuses a downstream stage when B3 is unmet",
            lambda: require_calibration(tmpd, "NOCAL", rubric_version,
                                        "claude-haiku-4-5-20251001"))
    shutil.rmtree(tmpd, ignore_errors=True)

    print("\n--- %d checks failed ---" % len(fails) if fails
          else "\n--- all checks passed ---")
    return 1 if fails else 0


def _strip_cache(params):
    """A request body with every cache_control removed, for comparison."""
    out = json.loads(json.dumps(params))
    for b in out.get("system") or []:
        b.pop("cache_control", None)
    return out


class _NoIdTransport(object):
    """Accepts a submission and returns no batch id. Requests in flight, no handle."""

    kind = "stub"

    def create(self, requests_):
        return {"processing_status": "in_progress"}

    def retrieve(self, batch_id):            # pragma: no cover - never reached
        raise AssertionError("retrieve must not be reached without an id")

    def results(self, url):                  # pragma: no cover
        raise AssertionError("results must not be reached without an id")


class _NoUrlTransport(object):
    """Ends the batch with no results_url. Counts, but nothing to read."""

    kind = "stub"

    def create(self, requests_):
        return {"id": "msgbatch_nourl", "processing_status": "in_progress"}

    def retrieve(self, batch_id):
        return {"id": batch_id, "processing_status": "ended",
                "request_counts": {"succeeded": 2}}

    def results(self, url):                  # pragma: no cover
        raise AssertionError("results must not be reached without a url")


def data_root(outdir):
    """The `data/` directory, however deep `--outdir` points inside it."""
    norm = os.path.normpath(os.path.abspath(outdir))
    parts = norm.split(os.sep)
    if "data" in parts:
        return os.sep.join(parts[:len(parts) - parts[::-1].index("data")])
    return norm


def promote_calibration(outdir, tickers):
    """Publish the per-company calibration result at the contract path.

    `references/data-contracts.md` and `pipeline_state.detect_calibration` both
    name `data/calibration.json` as the B3 gate file, and the gate is read from
    nowhere else. This script computes the result per company, into
    `data/{TICKER}/calibration.json`, so without this step the gate could never
    be met no matter how good the agreement was - the file it reads would simply
    not exist.

    Calibration is a property of the *scorer*, not of a company, so there is one
    contract file. With several companies calibrated in one run it is only
    written when they agree on the gate, the rubric and the model; a
    disagreement is reported rather than resolved by picking one, because which
    one got picked would then silently decide whether the pipeline is blocked.
    """
    found = []
    for tic in tickers:
        path = os.path.join(outdir, tic, "calibration.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                found.append((tic, json.load(f)))
        except (OSError, ValueError) as e:
            print("!! cannot read %s: %s" % (path, e))
    if not found:
        return 0

    keyed = set((r.get("gate"), r.get("rubric_version"), r.get("scorer_model_id"))
                for _, r in found)
    # The contract path is `data/calibration.json` at the DATA ROOT, not inside
    # whatever `--outdir` this run happened to use. Calibration is keyed on
    # (rubric_version, rubric_sha256, scorer_model_id) and applies to every
    # group scored with that pair; `calibrated_on` records which company's
    # reviews supplied the sample, which is provenance, not scope.
    #
    # Measured 2026-10-15: with the live layout `--outdir data/<group>` put the
    # gate file at `data/<group>/calibration.json`, the runner read
    # `data/calibration.json`, and a PASSED gate still blocked the pipeline.
    # That is the same shape as the bug this function's docstring describes -
    # a gate reading a file nothing writes - reintroduced one directory up.
    dest = os.path.join(data_root(outdir), "calibration.json")
    if len(keyed) > 1:
        print("\n!! NOT writing %s: the %d calibrations in this run disagree on"
              % (dest, len(found)))
        print("!! gate, rubric or model:")
        for tic, r in found:
            print("!!   %-6s gate=%s rubric=%s model=%s"
                  % (tic, r.get("gate"), r.get("rubric_version"),
                     r.get("scorer_model_id")))
        print("!! B3 is a property of the scorer, so one of these is wrong about")
        print("!! it. The gate stays unmet until they agree (CORE-PROMPT s7).")
        return 1

    tic, result = found[0]
    out = dict(result)
    out["calibrated_on"] = sorted(t for t, _ in found)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    print("\nB3 gate file  -> %s (from %s)" % (dest, ", ".join(out["calibrated_on"])))
    print("This is the file pipeline_state reads for B3.calibration.")
    return 0


def guard_stub_path(path):
    norm = os.path.normpath(os.path.abspath(path))
    parts = norm.split(os.sep)
    if "data" in parts:
        die("refusing to write synthetic stub scores to %s.\n"
            "The stub is a plumbing check, not a scorer. Use --outdir to send it\n"
            "somewhere outside data/." % path)


# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tickers_pos", nargs="*", default=[], metavar="TICKER",
                   help="one or more tickers; --tickers is the orchestrator's "
                        "spelling of the same thing")
    windowmod.add_tickers_argument(
        p, "the orchestrator's spelling of the positional tickers "
           "(.claude/commands/review-tone-run.md); equivalent, not additional")
    windowmod.add_argument(p)
    p.add_argument("--outdir", default="data")
    p.add_argument("--reviews", help="override data/{TICKER}/reviews.csv")
    p.add_argument("--scores", help="override data/{TICKER}/scores.csv")
    p.add_argument("--rubric-version", default="v1")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help="exact dated model ID (default %s)" % DEFAULT_MODEL)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL,
                   help="ANTHROPIC_BASE_URL in the environment is ignored")
    p.add_argument("--workers", type=int, default=1,
                   help="independent concurrent calls; never batches reviews")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--sample-per-quarter", type=int, default=0, metavar="N",
                   help="score at most N reviews per quarter, drawn from the "
                        "full quarter population on a seed from env_stamp's "
                        "convention, before any score is read. Recorded in "
                        "manifest.json and the scoring report with N, the seed, "
                        "the drawn ids and the population drawn from. Refused "
                        "below the %d-review floor."
                        % aggmod.MIN_REVIEWS_PER_QUARTER)
    p.add_argument("--batch", action="store_true",
                   help="route scoring through the Batches API: same one-review "
                        "requests, 50%% off, results keyed on custom_id. Not for "
                        "a calibration draw, which should not wait on a batch.")
    p.add_argument("--batch-poll-seconds", type=int, default=20)
    p.add_argument("--batch-max-wait", type=int, default=24 * 3600,
                   help="refuse rather than poll forever; the batch is not lost "
                        "and results are kept for 29 days, so re-run to collect")
    p.add_argument("--no-cache", action="store_true",
                   help="omit cache_control from the system block")
    p.add_argument("--require-cache", action="store_true",
                   help="exit non-zero if cache_read_input_tokens stays 0. The "
                        "minimum cacheable prefix is model-dependent and a "
                        "prefix below it caches nothing silently.")
    p.add_argument("--verify-prompt-identity", action="store_true",
                   help="count_tokens the string and single-block system forms "
                        "and report whether they are the same prompt")
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--max-failure-rate", type=float, default=0.10,
                   help="above this share of scoring failures the run is blocked")
    p.add_argument("--calibrate", action="store_true", help="B3 calibration gate")
    p.add_argument("--labels", help="completed labelling sheet; computes the gate")
    p.add_argument("-n", type=int, default=100, dest="n",
                   help="calibration sample size (the rubric specifies 100)")
    p.add_argument("--seed", type=int, default=20261011)
    p.add_argument("--boot", type=int, default=2000, help="bootstrap replicates")
    p.add_argument("--backend", choices=("api", "stub"), default="api",
                   help="stub runs the whole scoring path offline with synthetic "
                        "tone - refused anywhere under data/")
    p.add_argument("--self-test", action="store_true",
                   help="offline verification; writes synthetic tone, not scores")
    args = p.parse_args()

    tickers, seen_t = [], set()
    for x in list(args.tickers or []) + list(args.tickers_pos):
        u = x.upper()
        if u not in seen_t:
            seen_t.add(u)
            tickers.append(u)
    if not tickers:
        p.error("no tickers given; pass them positionally or with --tickers")
    if (args.reviews or args.scores) and len(tickers) > 1:
        p.error("--reviews/--scores override a single company's paths; they "
                "cannot stand for %d companies" % len(tickers))

    win = windowmod.resolve(args, "score_reviews.py (B3/B4)")
    print(win.describe())

    rc = 0
    for ticker in tickers:
        if len(tickers) > 1:
            print("\n=== %s ===" % ticker)
        rc = run_one(args, p, ticker, win) or rc
    if args.calibrate and not args.labels:
        return rc
    if args.calibrate:
        rc = promote_calibration(args.outdir, tickers) or rc
    return rc


def run_one(args, p, ticker, win):
    tdir = os.path.join(args.outdir, ticker)
    paths = {
        "ticker": ticker,
        "reviews": args.reviews or os.path.join(tdir, "reviews.csv"),
        "scores": args.scores or os.path.join(tdir, "scores.csv"),
        "report": os.path.join(tdir, "scoring_report.json"),
        "cal_sample": os.path.join(tdir, "calibration_sample.csv"),
        "cal_key": os.path.join(tdir, "calibration_key.csv"),
        "cal_result": os.path.join(tdir, "calibration.json"),
        "manifest": os.path.join(tdir, "manifest.json"),
    }
    os.makedirs(tdir, exist_ok=True)

    rv = args.rubric_version
    rubric_text = load_rubric(rv)
    # Pin the only system text this process may send, from the text whose
    # sha256 load_rubric has just checked against the pin.
    set_expected_system(rubric_text, rv)

    if args.self_test:
        return cmd_selftest(args, paths, rv)

    if args.calibrate:
        if args.batch:
            die("--batch and --calibrate cannot be combined.\n\n"
                "A calibration draw is a hundred reviews that someone is about\n"
                "to sit down and label by hand; making it wait on a batch that\n"
                "may take an hour buys a saving of cents. Score the calibration\n"
                "sample synchronously and batch the bulk run.")
        if args.labels:
            return cmd_calibrate_report(args, paths, rv, args.model)
        return cmd_calibrate_draw(args, paths, rv, args.model, win)

    if args.backend == "stub":
        guard_stub_path(paths["scores"])
        print("!! STUB BACKEND - synthetic tone, no API calls. This verifies the")
        print("!! scoring path, not the scores. Rows are stamped %s.\n"
              % STUB_MODEL_ID)
        backend = StubBackend()
        if args.batch:
            backend = BatchBackend(
                _StubApiShim(backend), StubBatchTransport(backend),
                poll_seconds=0, max_wait=args.batch_max_wait)
    else:
        api = ApiBackend(args.model,
                         build_system_blocks(rubric_text, cache=not args.no_cache),
                         base_url=args.base_url, max_tokens=args.max_tokens)
        if args.verify_prompt_identity:
            verify_prompt_identity(api, rubric_text)
        backend = api
        if args.batch:
            backend = BatchBackend(api, HttpBatchTransport(api),
                                   poll_seconds=args.batch_poll_seconds,
                                   max_wait=args.batch_max_wait)
    rc = cmd_score(args, paths, rv, backend, win)
    if rc == 0:
        ok, detail = calibration_status(args.outdir, ticker, rv,
                                        backend.resolved_models.most_common(1)[0][0]
                                        if backend.resolved_models else args.model)
        if not ok:
            print("\n!! Scores written, but B3 is NOT met: %s" % detail)
            print("!! Scores are raw material; a RESULT built on an uncalibrated")
            print("!! scorer is blocked (CORE-PROMPT s7). Next: --calibrate")
    return rc


if __name__ == "__main__":
    sys.exit(main())
