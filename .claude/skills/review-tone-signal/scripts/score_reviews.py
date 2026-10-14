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
ordering cannot leak one review's context into the next.

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
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import window as windowmod  # noqa: E402

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

DEFAULT_MODEL = "claude-haiku-4-5"
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

class ApiBackend(object):
    """One independent Messages API call per review, temperature 0, no history.

    ANTHROPIC_BASE_URL in the environment is deliberately ignored. In a Claude
    Code session that variable points at a gateway that does not accept
    `x-api-key`, and silently inheriting it produces confusing auth failures.
    Override with --base-url when a proxy is genuinely wanted.
    """

    kind = "api"

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
        self.system = system
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.max_tokens = max_tokens
        self.transport_retries = transport_retries
        self.timeout = timeout
        self.resolved_models = Counter()

    def __call__(self, prompt):
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "system": self.system,
            # Exactly one user message. No assistant turns, no prior reviews.
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        assert json.loads(body)["messages"].__len__() == 1

        req = urllib.request.Request(self.url, data=body, headers={
            "x-api-key": self._key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "user-agent": "review-tone-signal/1.0 (research; tone scorer)",
        })

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

        # The model that actually answered, not the alias that was asked for.
        resolved = payload.get("model") or self.model
        self.resolved_models[resolved] += 1

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

    def __init__(self, fail_transport=29, fail_malformed=23, retry_ok=17):
        self.model = STUB_MODEL_ID
        self.resolved_models = Counter()
        # Keyed on the prompt, not on a call counter: at temperature 0 a retry
        # sends an identical prompt and gets an identical answer, so a
        # systematically malformed review stays malformed on the second attempt.
        self.fail_transport = fail_transport
        self.fail_malformed = fail_malformed
        self.retry_ok = retry_ok
        self._seen = set()

    def __call__(self, prompt):
        self.resolved_models[STUB_MODEL_ID] += 1
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
        if "fiscal_quarter" not in rows[0]:
            die("%s has no fiscal_quarter column, so --window %s cannot be\n"
                "honoured. Scoring it would put held-out reviews through the\n"
                "scorer with nothing able to tell afterwards (CORE-PROMPT s1.4).\n"
                "Re-extract it with extract_reviews.py." % (path, win.mode))
        kept = [r for r in rows if win.allows(r.get("fiscal_quarter"))]
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
    existing = load_scores(paths["scores"])
    already = set(r["review_id"] for r in
                  select_scores(existing, rubric_version, backend.model))

    # For an API backend the exact model is only known once a response arrives,
    # so resume-by-model uses the requested id; a mid-run change is caught below.
    todo = [r for r in reviews if r["review_id"] not in already]
    if args.limit:
        todo = todo[:args.limit]

    print("reviews.csv        %d rows" % len(reviews))
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
    if not todo:
        print("\nnothing to do.")
        return 0

    results = {}
    notes = {}
    t0 = time.time()
    done = 0

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
                          backend, stamped_at, paths)
    with open(paths["report"], "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print_report(report, paths)
    if report["failure_rate"] > args.max_failure_rate:
        print("\n!! BLOCKED: %.1f%% of reviews failed to score, over the %.1f%% limit."
              % (100 * report["failure_rate"], 100 * args.max_failure_rate))
        print("!! The rows are in scores.csv marked scoring_failed - inspect them.")
        print("!! A quarter that lost this many reviews is not a clean quarter (F13).")
        return 1
    return 0


def build_report(todo, out_rows, notes, rubric_version, resolved, backend,
                 stamped_at, paths):
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
        "temperature": 0,
        "reviews_per_call": 1,
        "conversation_history": False,
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

    print("\n--- %d checks failed ---" % len(fails) if fails
          else "\n--- all checks passed ---")
    return 1 if fails else 0


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
    dest = os.path.join(outdir, "calibration.json")
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
    }
    os.makedirs(tdir, exist_ok=True)

    rv = args.rubric_version
    rubric_text = load_rubric(rv)

    if args.self_test:
        return cmd_selftest(args, paths, rv)

    if args.calibrate:
        if args.labels:
            return cmd_calibrate_report(args, paths, rv, args.model)
        return cmd_calibrate_draw(args, paths, rv, args.model, win)

    if args.backend == "stub":
        guard_stub_path(paths["scores"])
        print("!! STUB BACKEND - synthetic tone, no API calls. This verifies the")
        print("!! scoring path, not the scores. Rows are stamped %s.\n"
              % STUB_MODEL_ID)
        backend = StubBackend()
    else:
        backend = ApiBackend(args.model, build_system_prompt(rubric_text),
                             base_url=args.base_url, max_tokens=args.max_tokens)
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
