#!/usr/bin/env python3
"""Project state and phase gates - read-only.

The orchestrator must never decide for itself which phase the project is in.
Phase is a property of what is on disk and in git, and it is read, not judged.
This module does the reading and the gate arithmetic so that no gate decision
is ever made in prose (CORE-PROMPT s1.3).

Everything here is read-only. Nothing in this module writes, moves or mutates a
file. Gate evaluation that could rewrite the thing it is evaluating is how a
frozen universe quietly stops being frozen.

Gates, in order (CORE-PROMPT s8, SKILL.md phase map, tone-rubric-v1 calibration
gate). Each is the ENTRY condition for the work that follows it:

    A1  universe exists, hashed, hash matches, scan not truncated
    A1b the pre-filter has cut the panel to a hashed shortlist per formation
    A2  >= 8 structurally eligible companies IN ONE FORMATION, every rejection
        carrying a reason, the file not partial
    A3  >= 8 of those have usable, untruncated review coverage   <- Gate A
    B1  prereg frozen: hash matches, both files committed, no placeholders
    B3  calibration kappa >= 0.60 AND adjacent agreement >= 0.85 <- Gate B
    B7  a phase-B held-out row with verdict `signal`             <- Gate C
    C   a phase-C row with verdict `signal`                      <- Gate D

Three integrity checks run on every pass, independent of phase:

    held-out contamination  quarter-level output inside the held-out window
                            while the prereg is still unfrozen (s1.4)
    append-only             results.jsonl / MISSES.md shortened or rewritten
                            relative to git HEAD (s1.6)
    dead prereg             a prereg hash that has already failed on held-out
                            data. That hypothesis is finished; it is never
                            re-specified and re-run against the same held-out
                            quarters (prereg TEMPLATE s10).

Standard library only.
"""

import csv
import hashlib
import json
import os
import re
import subprocess

# Gate thresholds. These come from the locked core prompt and the frozen
# rubric; they are not tuning parameters and are not exposed as CLI flags.
MIN_ELIGIBLE = 8              # CORE-PROMPT s8, phase A gate
MIN_COVERAGE = 8              # CORE-PROMPT s8, phase A gate
MIN_QUARTERS = 12             # CORE-PROMPT s5, per-company floor

# The review corpus is a fixed snapshot ending September 2023 and will never
# grow (README; weekly-routine.md, "Why weekly"). extract_reviews.py holds the
# authoritative constant as CORPUS_SNAPSHOT; this is a second copy because
# pipeline_state imports nothing from the pipeline it inspects, and the test
# suite checks the two agree.
CORPUS_SNAPSHOT = (2023, 3)   # 2023Q3


def quarters_to_corpus_end(year):
    """Quarters from a 1 January formation to the end of the corpus.

    A formation that leaves fewer than MIN_QUARTERS cannot produce a company
    that clears the s5 floor, however eligible its companies are. That is
    reported at the A2 gate rather than enforced, because s5 drops companies
    and this is a property of a cohort - a rule about cohorts is the user's to
    add, not this module's to invent.
    """
    return (CORPUS_SNAPSHOT[0] - year) * 4 + CORPUS_SNAPSHOT[1]
MIN_KAPPA = 0.60              # tone-rubric-v1, calibration gate
MIN_ADJACENT = 0.85           # tone-rubric-v1, calibration gate
HELDOUT_QUARTERS = 8          # CORE-PROMPT s6

PLACEHOLDER = re.compile(r"<paste>|<N>|<exact id>|<value>|<\.\.\.>|_pending_|"
                         r"<YYYY-MM-DD>|<RUN NAME>|<e\.g\.|<one-sided")
QUARTER = re.compile(r"`(?:FY)?(\d{4})Q([1-4])`")
QLABEL = re.compile(r"^(?:FY)?(\d{4})Q([1-4])$")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git(root, *args):
    """Run a git command; return (ok, stdout). Never raises."""
    try:
        out = subprocess.run(("git",) + args, cwd=root, capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return out.returncode == 0, out.stdout


def is_tracked(root, relpath):
    ok, _ = git(root, "ls-files", "--error-unmatch", relpath)
    return ok


def committed_blob(root, relpath):
    """File contents at HEAD, or None if absent there."""
    ok, out = git(root, "show", "HEAD:%s" % relpath)
    return out if ok else None


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes", "y")


def qkey(label):
    """`FY2021Q3` / `2021Q3` -> (2021, 3). None when unparseable."""
    m = QLABEL.match((label or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def quarter_seq(start, end):
    """Inclusive list of (year, q) from start to end."""
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = (cur[0] + 1, 1) if cur[1] == 4 else (cur[0], cur[1] + 1)
    return out


# --------------------------------------------------------------------------
# stage A1 - universe
# --------------------------------------------------------------------------

def detect_universe(root):
    """Locate the point-in-time universe, verify its hash, report staleness.

    Two shapes exist in this project's history: the rolling annual panel
    mandated by company-selection.md s1 (`universe_panel.csv`), and the
    single-formation screens that preceded it (`universe_<year>.csv`). Both are
    accepted; the absence of the panel is reported as staleness rather than
    silently treated as equivalent, because a single formation date excludes
    every company that listed later.
    """
    d = os.path.join(root, "data")
    st = {"files": [], "hash": None, "problems": [], "notes": [],
          "formation_dates": {}, "tickers": {}, "ciks": {}, "shape": None}
    if not os.path.isdir(d):
        st["problems"].append("data/ does not exist")
        return st

    panel = os.path.join(d, "universe_panel.csv")
    if os.path.exists(panel):
        found, st["shape"] = [panel], "rolling panel"
    else:
        found = sorted(os.path.join(d, f) for f in os.listdir(d)
                       if re.fullmatch(r"universe(_\d{4})?\.csv", f))
        if found:
            st["shape"] = "single-formation screen(s)"
            st["notes"].append(
                "universe_panel.csv absent: the universe is %d single-formation "
                "screen(s), not the rolling annual panel that "
                "company-selection.md s1 now mandates. Every company that "
                "listed after a formation date is missing from that date's "
                "screen." % len(found))

    if not found:
        st["problems"].append("no universe file in data/ (stage A1 not run)")
        return st

    per_file = []
    for path in found:
        rel = os.path.relpath(path, root)
        st["files"].append(rel)
        hp = path[:-4] + ".hash"
        actual = sha256_file(path)
        if not os.path.exists(hp):
            st["problems"].append("%s has no .hash file: CORE-PROMPT s2 requires "
                                  "the universe hashed before review data is "
                                  "touched" % rel)
            continue
        with open(hp) as f:
            meta = f.read().strip()
        recorded = meta.split()[0] if meta else ""
        if recorded != actual:
            st["problems"].append(
                "%s does not match its recorded hash (recorded %s, actual %s): "
                "the universe changed after being frozen"
                % (rel, recorded[:12], actual[:12]))
            continue
        if "truncated=True" in meta:
            st["problems"].append("%s was produced by a truncated scan: "
                                  "estimation only, not a universe" % rel)
            continue
        if not is_tracked(root, rel):
            st["problems"].append("%s is not committed: an uncommitted universe "
                                  "is not frozen" % rel)
        per_file.append((os.path.basename(path), actual))

    for path in found:
        try:
            for r in read_csv(path):
                tic = (r.get("ticker") or "").strip()
                if not tic:
                    continue
                fd = (r.get("formation_date") or "").strip()
                st["tickers"].setdefault(tic, set()).add(fd)
                cik = (r.get("cik") or "").strip()
                if cik:
                    st["ciks"][tic] = cik
                prev = st["formation_dates"].get(tic)
                if fd and (prev is None or fd < prev):
                    st["formation_dates"][tic] = fd
        except (OSError, csv.Error) as e:
            st["problems"].append("cannot read %s: %s"
                                  % (os.path.relpath(path, root), e))

    if per_file:
        # One screen -> its own hash. Several -> a deterministic composite over
        # "name:sha" lines, so the result row pins the exact set that was used.
        if len(per_file) == 1:
            st["hash"] = per_file[0][1]
        else:
            blob = "\n".join("%s:%s" % p for p in sorted(per_file))
            st["hash"] = hashlib.sha256(blob.encode()).hexdigest()
            st["composite_of"] = sorted(n for n, _ in per_file)
    return st


# --------------------------------------------------------------------------
# stage A1b - mechanical pre-filter
# --------------------------------------------------------------------------

def detect_shortlist(root):
    """Read the pre-filter's cut record and its per-formation shortlists.

    A1b sits between the universe and A2: it cuts the in_band panel down to the
    set A2 is handed, on disclosed structure read from the filing in force at
    each formation. `prefilter_panel.csv` carries every in_band company-year
    with a reason per cut, and is what bounds the population - the eligibility
    file does not. Both are hashed and checked like the universe, because a
    shortlist edited after its hash was recorded decides which company-years
    may be scored, which is the same defect as a universe edited after freezing.
    """
    d = os.path.join(root, "data")
    st = {"present": False, "years": {}, "problems": [], "notes": [],
          "panel_hash": None}
    if not os.path.isdir(d):
        return st

    panel = os.path.join(d, "prefilter_panel.csv")
    if os.path.exists(panel):
        st["panel_hash"] = sha256_file(panel)
        hp = panel[:-4] + ".hash"
        if not os.path.exists(hp):
            st["problems"].append("data/prefilter_panel.csv has no .hash file")
        else:
            with open(hp) as f:
                rec = (f.read().strip().split() or [""])[0]
            if rec != st["panel_hash"]:
                st["problems"].append(
                    "data/prefilter_panel.csv does not match its recorded hash "
                    "(recorded %s, actual %s): the cut record changed after it "
                    "was written" % (rec[:12], st["panel_hash"][:12]))
        if not is_tracked(root, "data/prefilter_panel.csv"):
            st["problems"].append(
                "data/prefilter_panel.csv is not committed: the cut list is "
                "what bounds the population, and an uncommitted one is not a "
                "record")

    for fn in sorted(os.listdir(d)):
        m = re.fullmatch(r"shortlist_(\d{4})\.csv", fn)
        if not m:
            continue
        year, path = int(m.group(1)), os.path.join(d, fn)
        rel = os.path.relpath(path, root)
        try:
            tickers = [(r.get("ticker") or "").strip() for r in read_csv(path)
                       if (r.get("ticker") or "").strip()]
        except (OSError, csv.Error) as e:
            st["problems"].append("cannot read %s: %s" % (rel, e))
            continue
        actual = sha256_file(path)
        hp = path[:-4] + ".hash"
        if not os.path.exists(hp):
            st["problems"].append("%s has no .hash file" % rel)
        else:
            with open(hp) as fh:
                rec = (fh.read().strip().split() or [""])[0]
            if rec != actual:
                st["problems"].append(
                    "%s does not match its recorded hash (recorded %s, actual "
                    "%s): a shortlist edited after the fact silently changes "
                    "which company-years A2 was allowed to score"
                    % (rel, rec[:12], actual[:12]))
        if not is_tracked(root, rel):
            st["problems"].append("%s is not committed" % rel)
        st["years"][year] = {"n": len(tickers), "hash": actual,
                             "tickers": tickers}

    st["present"] = bool(st["years"])
    if st["present"] and not os.path.exists(panel):
        st["problems"].append(
            "shortlists exist but data/prefilter_panel.csv does not: the cut "
            "company-years and their reasons are the half of the record that "
            "company-selection.md s2 asks for, and without it the shortlist is "
            "a set of names with no account of what was excluded")
    return st


# --------------------------------------------------------------------------
# stage A2 - structural eligibility
# --------------------------------------------------------------------------

def eligibility_files(root):
    """Per-formation scorecards, newest naming first.

    A2 writes one file per formation - data/eligibility_<YEAR>.csv - because
    the universe re-forms annually and a company is scored once per formation,
    against the filing in force then. The single data/eligibility.csv that
    predates the rolling panel is still read when present, under year None.
    """
    d = os.path.join(root, "data")
    out = []
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        m = re.fullmatch(r"eligibility_(\d{4})\.csv", fn)
        if m:
            out.append((int(m.group(1)), os.path.join(d, fn)))
    flat = os.path.join(d, "eligibility.csv")
    if os.path.exists(flat):
        out.append((None, flat))
    return out


def detect_eligibility(root, universe):
    """Read every per-formation scorecard and check it against its contract.

    This stage is scored by a human from filings (company-selection.md s2). It
    is never automated and never re-scored by a scheduled run: "Never revise a
    score after seeing an outcome."

    The gate is satisfied by one formation, not by a total swept across all of
    them, so the per-year counts are kept separate and a cohort is chosen from
    them rather than summed. `eligible` is that cohort's list: the set the rest
    of the pipeline is entitled to act on.
    """
    st = {"present": False, "eligible": [], "ineligible": [], "problems": [],
          "notes": [], "hash": None, "by_year": {}, "cohort_year": None,
          "partial": {}}
    found = eligibility_files(root)
    if not found:
        return st
    st["present"] = True

    for year, path in found:
        rel = os.path.relpath(path, root)
        per = {"eligible": [], "ineligible": [], "hash": sha256_file(path),
               "partial": False, "scope": None}
        hp = path[:-4] + ".hash"
        if os.path.exists(hp):
            with open(hp) as f:
                meta = f.read().strip()
            rec = (meta.split() or [""])[0]
            if rec != per["hash"]:
                st["problems"].append(
                    "%s does not match its recorded hash (recorded %s, actual "
                    "%s): a scorecard edited after it was written is not a "
                    "record of what was scored" % (rel, rec[:12],
                                                   per["hash"][:12]))
            # A partial file is a verification sample, not a scored formation.
            # score_eligibility.py marks it; without this the gate could be met
            # by eight companies scored out of a scope of eighty.
            per["partial"] = "partial=True" in meta
        else:
            # Recorded, not blocked. Requiring a hash file here would be a new
            # rule: s2 hashes the universe, and the scorecard contract does not
            # ask for one. The cost is that completeness cannot be read, so the
            # file is taken at face value - which is what the gate did before.
            st["notes"].append(
                "%s has no .hash file, so whether it is a complete formation "
                "or a verification sample cannot be read from disk" % rel)

        try:
            rows = read_csv(path)
        except (OSError, csv.Error) as e:
            st["problems"].append("cannot read %s: %s" % (rel, e))
            continue

        for r in rows:
            tic = (r.get("ticker") or "").strip()
            if not tic:
                continue
            reason = (r.get("reason") or "").strip()
            if truthy(r.get("eligible")):
                per["eligible"].append(tic)
            else:
                per["ineligible"].append({"ticker": tic,
                                          "reason": reason or "(none recorded)",
                                          "score": r.get("weighted_score", "")})
                if not reason:
                    st["problems"].append(
                        "%s in %s is ineligible with no reason recorded: the "
                        "contract requires a binding reason" % (tic, rel))
            if universe["tickers"] and tic not in universe["tickers"]:
                st["problems"].append(
                    "%s is scored for eligibility but is not in the universe: "
                    "eligibility applies to universe members only, and scoring "
                    "a non-member is how a hand-picked name enters the test"
                    % tic)
            asof = (r.get("scored_asof") or "").strip()
            form = universe["formation_dates"].get(tic)
            if asof and form and asof > form:
                st["problems"].append(
                    "%s scored from evidence dated %s, after its formation date "
                    "%s: the scorecard may only use evidence knowable at "
                    "formation" % (tic, asof, form))
        st["by_year"][year] = per
        st["partial"][year] = per["partial"]

    # One formation carries the gate. The earliest qualifying one is chosen:
    # a formation is a cohort followed forward, and the earliest leaves the
    # longest run of quarters against a corpus that ends at a fixed date.
    qualifying = [y for y, v in st["by_year"].items()
                  if y is not None and not v["partial"]
                  and len(v["eligible"]) >= MIN_ELIGIBLE]
    if None in st["by_year"] and not st["by_year"][None]["partial"] \
            and len(st["by_year"][None]["eligible"]) >= MIN_ELIGIBLE:
        qualifying.append(None)
    if qualifying:
        st["cohort_year"] = min(y for y in qualifying if y is not None) \
            if any(y is not None for y in qualifying) else None
        coh = st["by_year"][st["cohort_year"]]
        st["eligible"] = list(coh["eligible"])
        st["ineligible"] = list(coh["ineligible"])
        st["hash"] = coh["hash"]
    else:
        # No cohort: report everything, entitle nothing. `eligible` stays empty
        # so no later stage can act on a set that has not met the gate.
        st["ineligible"] = [x for v in st["by_year"].values()
                            for x in v["ineligible"]]
        st["hash"] = hashlib.sha256(
            "|".join("%s:%s" % (y, v["hash"])
                     for y, v in sorted(st["by_year"].items(),
                                        key=lambda kv: (kv[0] is None,
                                                        kv[0]))).encode()
        ).hexdigest()
    return st


# --------------------------------------------------------------------------
# stage A3 - coverage
# --------------------------------------------------------------------------

def detect_coverage(root, eligibility):
    path = os.path.join(root, "data", "coverage.csv")
    st = {"present": False, "usable": [], "unusable": [], "problems": [],
          "truncated": False, "hash": None}
    if not os.path.exists(path):
        return st
    st["present"] = True
    st["hash"] = sha256_file(path)
    try:
        rows = read_csv(path)
    except (OSError, csv.Error) as e:
        st["problems"].append("cannot read data/coverage.csv: %s" % e)
        return st

    elig = set(eligibility["eligible"])
    for r in rows:
        tic = (r.get("ticker") or "").strip()
        if not tic:
            continue
        if truthy(r.get("truncated_scan")):
            st["truncated"] = True
        if truthy(r.get("usable")):
            st["usable"].append(tic)
        else:
            st["unusable"].append({
                "ticker": tic,
                "reason": (r.get("reason") or "").strip() or "(none recorded)",
                "matched_products": r.get("matched_products", ""),
            })
        if elig and tic not in elig:
            st["problems"].append(
                "%s appears in coverage.csv but is not structurally eligible: "
                "coverage runs last and may never add a company" % tic)
    if st["truncated"]:
        st["problems"].append(
            "coverage.csv was produced by a truncated corpus scan: coverage.py "
            "marks this estimation-only, and an estimate does not pass a gate")
    return st


# --------------------------------------------------------------------------
# stage B1 - prereg
# --------------------------------------------------------------------------

def parse_window(text, source):
    """Pull the window table out of a prereg (s3). All three rows required."""
    win = {"source": source}
    for key, label in (("full", "Full window"), ("development", "Development"),
                       ("heldout", "Held-out")):
        for line in text.splitlines():
            if label.lower() in line.lower() and "|" in line:
                qs = QUARTER.findall(line)
                if len(qs) >= 2:
                    win[key] = ((int(qs[0][0]), int(qs[0][1])),
                                (int(qs[-1][0]), int(qs[-1][1])))
                break
    if "heldout" in win:
        n = len(quarter_seq(*win["heldout"]))
        win["heldout_len"] = n
        if n != HELDOUT_QUARTERS:
            win["problem"] = ("held-out window spans %d quarters; CORE-PROMPT s6 "
                              "fixes it at the final %d"
                              % (n, HELDOUT_QUARTERS))
    return win


def detect_prereg(root):
    """Find the active prereg and decide whether it is genuinely frozen.

    Frozen means all four of: a .hash file exists, it matches the document,
    both files are committed, and no template placeholder survives in the
    document. A prereg with `<paste>` still in it fixes nothing, and a prereg
    that is not committed can be edited after the result is seen.
    """
    d = os.path.join(root, "prereg")
    st = {"frozen": False, "path": None, "hash": None, "problems": [],
          "candidates": [], "historical": [], "window": None, "fields": {}}
    if not os.path.isdir(d):
        st["problems"].append("prereg/ does not exist")
        return st

    cands = sorted(f for f in os.listdir(d)
                   if f.endswith(".md") and f != "TEMPLATE.md")
    st["candidates"] = cands
    if not cands:
        tmpl = os.path.join(d, "TEMPLATE.md")
        if os.path.exists(tmpl):
            with open(tmpl, encoding="utf-8") as f:
                st["window"] = parse_window(f.read(), "prereg/TEMPLATE.md "
                                                      "(NOT frozen)")
        return st

    active = cands[-1]           # dated filenames sort chronologically
    st["historical"] = cands[:-1]
    rel = "prereg/" + active
    st["path"] = rel
    path = os.path.join(d, active)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    actual = sha256_file(path)
    st["document_hash"] = actual
    st["window"] = parse_window(text, rel)

    hp = path[:-3] + ".hash"
    problems = []
    if not os.path.exists(hp):
        problems.append("%s has no .hash file (freeze it: shasum -a 256 %s | "
                        "tee %s)" % (rel, rel, rel[:-3] + ".hash"))
    else:
        with open(hp) as f:
            recorded = (f.read().strip().split() or [""])[0]
        if recorded != actual:
            problems.append("%s does not match its recorded hash: it was edited "
                            "after being frozen" % rel)
        if not is_tracked(root, rel[:-3] + ".hash"):
            problems.append("%s is not committed" % (rel[:-3] + ".hash"))
    if not is_tracked(root, rel):
        problems.append("%s is not committed: an uncommitted prereg is not "
                        "frozen" % rel)
    else:
        blob = committed_blob(root, rel)
        if blob is not None and hashlib.sha256(blob.encode()).hexdigest() != actual:
            problems.append("%s differs from its committed version" % rel)
    left = sorted(set(PLACEHOLDER.findall(text)))
    if left:
        problems.append("%s still contains unfilled template placeholders (%s): "
                        "a prereg that fixes nothing is not frozen"
                        % (rel, ", ".join(left[:4])))
    if st["window"] and "problem" in st["window"]:
        problems.append(st["window"]["problem"])

    st["problems"] = problems
    st["frozen"] = not problems
    if st["frozen"]:
        st["hash"] = actual
    st["fields"] = parse_prereg_fields(text)
    return st


def parse_prereg_fields(text):
    """Scorer model ID, rubric version, claimed kappa, specification count."""
    out = {}
    m = re.search(r"Tone rubric version:\s*`([^`]+)`", text)
    if m:
        out["rubric_version"] = m.group(1)
    m = re.search(r"Scorer model ID:\s*`([^`]+)`", text)
    if m:
        out["scorer_model_id"] = m.group(1)
    m = re.search(r"Calibration kappa achieved:\s*`([^`]+)`", text)
    if m:
        try:
            out["kappa_claimed"] = float(m.group(1))
        except ValueError:
            pass
    # s7 specification table: count data rows, excluding header and separator.
    sec = re.split(r"\n##\s*7\.", text)
    if len(sec) > 1:
        body = re.split(r"\n##\s", sec[1])[0]
        rows = [l for l in body.splitlines()
                if l.strip().startswith("|") and not re.match(r"^\|[\s\-|:]+\|$",
                                                              l.strip())]
        data = [l for l in rows[1:] if re.search(r"\|\s*\d+\s*\|", l)]
        if data:
            out["specifications_tested"] = len(data)
    return out


# --------------------------------------------------------------------------
# stage B3 - calibration
# --------------------------------------------------------------------------

def detect_calibration(root, prereg):
    """Read data/calibration.json (contract in references/data-contracts.md).

    The gate is the rubric's, not ours: quadratic-weighted kappa >= 0.60 AND
    adjacent agreement >= 0.85. An unvalidated scorer makes every downstream
    statistic decorative, so below either threshold the pipeline is blocked.
    """
    path = os.path.join(root, "data", "calibration.json")
    st = {"present": False, "passed": False, "problems": [], "data": {}}
    if not os.path.exists(path):
        return st
    st["present"] = True
    try:
        with open(path) as f:
            cal = json.load(f)
    except (OSError, ValueError) as e:
        st["problems"].append("cannot read data/calibration.json: %s" % e)
        return st
    st["data"] = cal

    required = ("n", "exact_agreement", "adjacent_agreement",
                "quadratic_weighted_kappa", "rubric_version",
                "scorer_model_id", "scored_at")
    missing = [k for k in required if cal.get(k) is None]
    if missing:
        st["problems"].append("calibration.json missing %s"
                              % ", ".join(missing))
        return st

    if cal["n"] < 100:
        st["problems"].append("calibration used %s reviews; the rubric requires "
                              "at least 100" % cal["n"])
    if cal["quadratic_weighted_kappa"] < MIN_KAPPA:
        st["problems"].append("quadratic-weighted kappa %.3f < %.2f"
                              % (cal["quadratic_weighted_kappa"], MIN_KAPPA))
    if cal["adjacent_agreement"] < MIN_ADJACENT:
        st["problems"].append("adjacent agreement %.3f < %.2f"
                              % (cal["adjacent_agreement"], MIN_ADJACENT))

    claimed = prereg["fields"].get("kappa_claimed")
    if claimed is not None:
        if abs(claimed - cal["quadratic_weighted_kappa"]) > 0.005:
            st["problems"].append(
                "the prereg records kappa %.3f but calibration.json reports "
                "%.3f: one of them is wrong and the result row cannot carry "
                "both" % (claimed, cal["quadratic_weighted_kappa"]))
    want = prereg["fields"].get("scorer_model_id")
    if want and cal.get("scorer_model_id") != want:
        st["problems"].append(
            "calibration scored with %s but the prereg fixes %s: calibration "
            "must be re-run when the scorer changes (F10)"
            % (cal.get("scorer_model_id"), want))
    want = prereg["fields"].get("rubric_version")
    if want and cal.get("rubric_version") != want:
        st["problems"].append("calibration used rubric %s but the prereg fixes "
                              "%s" % (cal.get("rubric_version"), want))

    st["passed"] = not st["problems"]
    return st


# --------------------------------------------------------------------------
# results log and misses
# --------------------------------------------------------------------------

def read_results(root):
    path = os.path.join(root, "results", "results.jsonl")
    st = {"path": "results/results.jsonl", "rows": [], "problems": []}
    if not os.path.exists(path):
        return st
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                st["rows"].append(json.loads(line))
            except ValueError:
                st["problems"].append("results.jsonl line %d is not JSON" % i)
    return st


def read_misses(root):
    path = os.path.join(root, "MISSES.md")
    st = {"entries": [], "hashes": set()}
    if not os.path.exists(path):
        return st
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # Strip fenced blocks first: the file documents its own entry format in a
    # fence, and that example is not a miss.
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    for block in re.split(r"\n(?=## )", text):
        m = re.match(r"##\s+(\d{4}-\d{2}-\d{2})\s+.\s+(\S+)", block)
        if not m:
            continue
        h = re.search(r"\*\*Prereg hash:\*\*\s*`?([0-9a-f]{8,64})`?", block)
        entry = {"date": m.group(1), "run_id": m.group(2),
                 "prereg_hash": h.group(1) if h else None}
        st["entries"].append(entry)
        if entry["prereg_hash"]:
            st["hashes"].add(entry["prereg_hash"])
    return st


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


def dead_prereg_hashes(results, misses):
    """Prereg hashes whose hypothesis is finished.

    A held-out failure ends that hypothesis. It is recorded with verdict
    `no_signal` and is never re-specified and re-run against the same held-out
    quarters (prereg TEMPLATE s10, failure mode F6). This is the rule a weekly
    automated loop is most likely to break by quietly "trying again", so the
    set of spent hypotheses is computed on every pass and the orchestrator
    refuses to re-enter the backtest for any of them.
    """
    dead = set(misses["hashes"])
    for r in results["rows"]:
        if (r.get("verdict") == "no_signal" and r.get("heldout_evaluated")
                and r.get("prereg_hash")):
            dead.add(r["prereg_hash"])
    return dead


def heldout_rows_for(results, prereg_hash):
    return [r for r in results["rows"]
            if r.get("heldout_evaluated") and r.get("prereg_hash") == prereg_hash]


# --------------------------------------------------------------------------
# held-out contamination
# --------------------------------------------------------------------------

def heldout_contamination(root, window, prereg_frozen):
    """Quarter-level output inside the held-out window while development is open.

    CORE-PROMPT s1.4: held-out quarters are not inspected, plotted, described or
    reasoned about until the prereg is frozen and development is closed. If an
    upstream stage has already written aggregates for those quarters, the
    contamination has happened and no later discipline undoes it.

    Caveat recorded rather than hidden: company fiscal quarters are labelled
    FY<year>Q<n> on the company's own calendar, while the prereg window is
    written in calendar quarters. The comparison here is on the (year, quarter)
    pair, so a company whose fiscal year is offset can be judged up to one
    quarter early or late. Where that matters the finding is reported as
    `boundary_uncertain` instead of being asserted.
    """
    out = {"violations": [], "boundary_uncertain": [], "checked": 0}
    if prereg_frozen or not window or "heldout" not in window:
        return out
    start = window["heldout"][0]
    d = os.path.join(root, "data")
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        qpath = os.path.join(d, name, "quarters.csv")
        if not os.path.exists(qpath):
            continue
        out["checked"] += 1
        try:
            rows = read_csv(qpath)
        except (OSError, csv.Error):
            continue
        for r in rows:
            label = (r.get("fiscal_quarter") or "").strip()
            k = qkey(label)
            if k is None:
                out["boundary_uncertain"].append("%s: unparseable fiscal "
                                                 "quarter %r" % (name, label))
                continue
            if k >= start:
                out["violations"].append(
                    "data/%s/quarters.csv contains %s, inside the held-out "
                    "window (>= %dQ%d), while the prereg is not frozen"
                    % (name, label, start[0], start[1]))
    return out


# --------------------------------------------------------------------------
# full state and gates
# --------------------------------------------------------------------------

def collect(root):
    st = {"root": root}
    st["universe"] = detect_universe(root)
    st["shortlist"] = detect_shortlist(root)
    st["eligibility"] = detect_eligibility(root, st["universe"])
    st["coverage"] = detect_coverage(root, st["eligibility"])
    st["prereg"] = detect_prereg(root)
    st["calibration"] = detect_calibration(root, st["prereg"])
    st["results"] = read_results(root)
    st["misses"] = read_misses(root)
    st["window"] = st["prereg"]["window"]
    st["append_only"] = append_only_violations(root)
    st["dead_hashes"] = dead_prereg_hashes(st["results"], st["misses"])
    st["contamination"] = heldout_contamination(root, st["window"],
                                                st["prereg"]["frozen"])
    return st


def gate(gid, phase, met, detail, blocking=True):
    return {"id": gid, "phase": phase, "met": met, "detail": detail,
            "blocking": blocking}


def evaluate_gates(st):
    """Ordered entry gates. The first unmet one is where the run stops."""
    g = []
    u, e, c = st["universe"], st["eligibility"], st["coverage"]
    p, cal = st["prereg"], st["calibration"]
    sl = st.get("shortlist") or {"present": False, "years": {}, "problems": []}

    # A1
    if u["problems"]:
        g.append(gate("A1.universe", "A", False, "; ".join(u["problems"])))
    else:
        g.append(gate("A1.universe", "A", True,
                      "%s (%s), hash %s verified"
                      % (", ".join(u["files"]), u["shape"], (u["hash"] or "")[:12])))

    # A1b
    if not sl["present"]:
        g.append(gate("A1b.shortlist", "A", False,
                      "no data/shortlist_<YEAR>.csv: the mechanical pre-filter "
                      "(prefilter_panel.py) has not been run. A2 scores the set "
                      "it is handed, so until the panel is cut there is nothing "
                      "for it to score"))
    elif sl["problems"]:
        g.append(gate("A1b.shortlist", "A", False, "; ".join(sl["problems"])))
    else:
        sizes = ", ".join("%d:%d" % (y, v["n"])
                          for y, v in sorted(sl["years"].items()))
        g.append(gate("A1b.shortlist", "A", True,
                      "%d formation(s) shortlisted (%s), panel hash %s verified"
                      % (len(sl["years"]), sizes,
                         (sl["panel_hash"] or "")[:12])))

    # A2
    if not e["present"]:
        g.append(gate("A2.eligibility", "A", False,
                      "no data/eligibility_<YEAR>.csv. This stage is scored by "
                      "hand from filings against company-selection.md s2; it is "
                      "not a missing script and a scheduled run must not invent "
                      "it"))
    elif e["problems"]:
        g.append(gate("A2.eligibility", "A", False, "; ".join(e["problems"])))
    elif e["cohort_year"] is not None or e["eligible"]:
        y = e["cohort_year"]
        n = len(e["eligible"])
        detail = ("%d structurally eligible at the %s formation (needs >= %d in "
                  "one formation), %d recorded ineligible"
                  % (n, y if y is not None else "single-file",
                     MIN_ELIGIBLE, len(e["ineligible"])))
        if y is not None:
            q = quarters_to_corpus_end(y)
            if q < MIN_QUARTERS:
                detail += ("; NOTE: %d quarters from that formation to the end "
                           "of the corpus, below the s5 floor of %d, so every "
                           "company in this cohort will be dropped"
                           % (q, MIN_QUARTERS))
        g.append(gate("A2.eligibility", "A", True, detail))
    else:
        bits = []
        for y, v in sorted(e["by_year"].items(),
                           key=lambda kv: (kv[0] is None, kv[0])):
            bits.append("%s: %d eligible of %d scored%s"
                        % (y if y is not None else "eligibility.csv",
                           len(v["eligible"]),
                           len(v["eligible"]) + len(v["ineligible"]),
                           " (PARTIAL, not a scored formation)"
                           if v["partial"] else ""))
        g.append(gate("A2.eligibility", "A", False,
                      "no formation has >= %d structurally eligible companies. "
                      "%s" % (MIN_ELIGIBLE, "; ".join(bits))))

    # A3 -> Gate A
    if not c["present"]:
        g.append(gate("A3.coverage", "A", False,
                      "data/coverage.csv absent (coverage.py not yet run)"))
    elif c["problems"]:
        g.append(gate("A3.coverage", "A", False, "; ".join(c["problems"])))
    else:
        n = len(c["usable"])
        g.append(gate("A3.coverage", "A", n >= MIN_COVERAGE,
                      "%d of %d eligible companies have usable coverage "
                      "(needs >= %d)"
                      % (n, n + len(c["unusable"]), MIN_COVERAGE)))

    # B1
    if not p["candidates"]:
        g.append(gate("B1.prereg_frozen", "B", False,
                      "no prereg in prereg/ (only TEMPLATE.md). Copy the "
                      "template, fill every field, hash it and commit it"))
    elif p["frozen"]:
        g.append(gate("B1.prereg_frozen", "B", True,
                      "%s frozen, hash %s committed"
                      % (p["path"], p["hash"][:12])))
    else:
        g.append(gate("B1.prereg_frozen", "B", False, "; ".join(p["problems"])))

    # B3 -> Gate B
    if not cal["present"]:
        g.append(gate("B3.calibration", "B", False,
                      "data/calibration.json absent: the scorer has not been "
                      "calibrated against human labels, so kappa is unknown"))
    elif cal["passed"]:
        d = cal["data"]
        g.append(gate("B3.calibration", "B", True,
                      "kappa %.3f >= %.2f, adjacent %.3f >= %.2f, n=%s, scorer %s"
                      % (d["quadratic_weighted_kappa"], MIN_KAPPA,
                         d["adjacent_agreement"], MIN_ADJACENT, d["n"],
                         d["scorer_model_id"])))
    else:
        g.append(gate("B3.calibration", "B", False, "; ".join(cal["problems"])))

    # B7 -> Gate C
    ph = p["hash"]
    if ph and ph in st["dead_hashes"]:
        g.append(gate("B7.heldout", "C", False,
                      "prereg %s has already failed on held-out data. That "
                      "hypothesis is finished: it is recorded in MISSES.md with "
                      "verdict no_signal and is never re-specified and re-run "
                      "against the same held-out quarters" % ph[:12]))
    else:
        rows = heldout_rows_for(st["results"], ph) if ph else []
        won = [r for r in rows if r.get("verdict") == "signal"]
        if won:
            g.append(gate("B7.heldout", "C", True,
                          "held-out run %s: verdict signal, estimate %s "
                          "(CI %s..%s) vs baseline %s, base rate %s, null %s"
                          % (won[-1].get("run_id"), won[-1].get("point_estimate"),
                             won[-1].get("ci_low"), won[-1].get("ci_high"),
                             won[-1].get("baseline_value"),
                             won[-1].get("base_rate"),
                             won[-1].get("null_run_result"))))
        else:
            g.append(gate("B7.heldout", "C", False,
                          "no held-out phase-B result under the active prereg"))

    # C -> Gate D
    crows = [r for r in st["results"]["rows"]
             if str(r.get("phase", "")).upper().startswith("C")
             and r.get("prereg_hash") == ph]
    cwon = [r for r in crows if r.get("verdict") == "signal"]
    g.append(gate("C.degradation", "D", bool(cwon),
                  "degradation test passed under the ~50-review "
                  "helpfulness-sorted regime" if cwon else
                  "no phase-C result: Phase D is unreachable (CORE-PROMPT s8)"))
    return g


def first_unmet(gates):
    for g in gates:
        if not g["met"]:
            return g
    return None


def phase_now(gates):
    """The phase whose entry gates are all met: the furthest legal phase."""
    ok = {g["id"]: g["met"] for g in gates}
    if ok.get("C.degradation"):
        return "D"
    if ok.get("B7.heldout"):
        return "C"
    if ok.get("B3.calibration") and ok.get("B1.prereg_frozen"):
        return "B"
    if ok.get("A3.coverage") and ok.get("A2.eligibility"):
        return "B-prep"
    return "A"


def fingerprint(st):
    """What the run saw, as content hashes and counts.

    Written into every result row so that "what changed since last run" is a
    diff of two append-only rows rather than a mutable state file that could
    disagree with the log.
    """
    fp = {
        "universe_hash": st["universe"]["hash"],
        "universe_files": sorted(st["universe"]["files"]),
        "eligibility_hash": st["eligibility"]["hash"],
        "n_eligible": len(st["eligibility"]["eligible"]),
        "eligibility_cohort": st["eligibility"].get("cohort_year"),
        "shortlist_hashes": {
            str(y): v["hash"][:12] for y, v
            in (st.get("shortlist") or {"years": {}})["years"].items()},
        "coverage_hash": st["coverage"]["hash"],
        "n_usable_coverage": len(st["coverage"]["usable"]),
        "prereg_hash": st["prereg"]["hash"],
        "prereg_frozen": st["prereg"]["frozen"],
        "calibration_kappa": st["calibration"]["data"].get(
            "quadratic_weighted_kappa"),
        "stages_built": {},
    }
    d = os.path.join(st["root"], "data")
    if os.path.isdir(d):
        per = {}
        for name in sorted(os.listdir(d)):
            sub = os.path.join(d, name)
            if not os.path.isdir(sub):
                continue
            per[name] = {f: sha256_file(os.path.join(sub, f))[:12]
                         for f in ("products.csv", "quarters.csv",
                                   "fundamentals.csv", "scores.csv")
                         if os.path.exists(os.path.join(sub, f))}
        fp["per_company"] = {k: v for k, v in per.items() if v}
    return fp


def diff_fingerprints(old, new):
    """Human-readable changes between two run fingerprints."""
    if not old:
        return ["first recorded run: nothing to compare against"]
    out = []
    labels = {
        "universe_hash": "universe", "eligibility_hash": "eligibility scorecard",
        "coverage_hash": "coverage", "prereg_hash": "active prereg",
        "calibration_kappa": "calibration kappa",
        "prereg_frozen": "prereg frozen",
        "eligibility_cohort": "A2 cohort formation",
    }
    for k, label in labels.items():
        a, b = old.get(k), new.get(k)
        if a != b:
            fmt = (lambda v: "none" if v is None
                   else (v[:12] if isinstance(v, str) and len(v) > 12 else str(v)))
            out.append("%s: %s -> %s" % (label, fmt(a), fmt(b)))
    for k, label in (("n_eligible", "eligible companies"),
                     ("n_usable_coverage", "companies with usable coverage")):
        if old.get(k) != new.get(k):
            out.append("%s: %s -> %s" % (label, old.get(k), new.get(k)))
    os_, ns_ = old.get("shortlist_hashes") or {}, new.get("shortlist_hashes") or {}
    if os_ != ns_:
        added = sorted(set(ns_) - set(os_))
        moved = sorted(y for y in set(ns_) & set(os_) if ns_[y] != os_[y])
        if added:
            out.append("shortlist added for: %s" % ", ".join(added))
        if moved:
            out.append("shortlist changed for: %s" % ", ".join(moved))

    oc, nc = old.get("per_company") or {}, new.get("per_company") or {}
    added = sorted(set(nc) - set(oc))
    changed = sorted(t for t in set(nc) & set(oc) if nc[t] != oc[t])
    if added:
        out.append("new company data: %s" % ", ".join(added))
    if changed:
        out.append("company data changed: %s" % ", ".join(changed))
    return out or ["no change in any tracked artefact since the last run"]
