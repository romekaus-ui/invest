---
description: Run the review-tone workflow end to end, stopping at the first unmet stage, and report where it stands
argument-hint: "[group-id] [--dry-run]"
allowed-tools: Bash, Read, Glob, Grep
---

# /tone-run

Walks the stages in `CONTRACTS.md` §4 — declare → collect → calibrate → score →
aggregate → render — stops at the first one that is not satisfied, and reports
what it found.

Arguments: `$ARGUMENTS`

---

## Before anything else

**`CORE-PROMPT.md` governs and this command does not override one word of it.**
Read it, then `PLAN.md` for where the project actually stands.

```bash
cat CORE-PROMPT.md
ls groups/ && tail -40 CHANGELOG.md
```

The rules most easily broken while relaying a result:

1. **Never output a buy, sell, hold, target, size or allocation** (§1.1). Not
   hedged, not as an example. Give the measurement and decline the call.
2. **Never compute, adjust or estimate a number yourself** (§1.2). Every figure
   comes from a script and is read from its output. If one looks wrong, say it
   looks wrong; do not fix it in prose.
3. **Never present a tone number without its coverage ratio** (§4). A mean over
   0.1% of a product's reviews is not that product's tone.

## Run it

```bash
python3 .claude/skills/review-tone-signal/scripts/run.py
```

One group, or a rehearsal that writes nothing:

```bash
python3 .claude/skills/review-tone-signal/scripts/run.py --group energy-drinks
python3 .claude/skills/review-tone-signal/scripts/run.py --dry-run
```

Exit codes: **0** ran or reported a clean stop — including a stage block, which
is the system working · **2** an integrity violation, which needs a person ·
**3** a stage is not built yet.

## Your job after it runs

The runner does the work. You relay it.

- **Say which stage it stopped at and why.** That is the answer, not a
  disappointment to apologise for.
- **If a stage reports `not built`**, say which and what it waits on. That is the
  honest state of the project.
- **If exit code is 2, stop.** Do not re-run, do not pass a flag to get past it,
  do not commit. It means an append-only file was altered or a hash no longer
  holds.
- **Report `recency-verified: NO` loudly if it appears.** Stage C can be
  satisfied while the data is still not a time series. Those are different
  conditions and a chart must never imply the stronger one.

## What this command refuses to do

**It never declares a peer group.** Stage G *verifies* existing declarations; it
does not create them. A group is declared deliberately, by a person, before any
review is collected, and is never revised after tone is seen — which is the
whole reason the declaration is hashed. Use the `review-tone-peer-group` skill
(`declare_group.py declare <id>`) when a new group is genuinely wanted.

**It never invents a stage's output or skips a missing stage.** A missing script
stops the run and is reported as missing.

**It never spends money without a ceiling.** Collection through a provider costs
per review. Every provider run carries a hard `maxTotalChargeUsd`, and the
budget state is `curl -H "Authorization: Bearer $APIFY_TOKEN"
https://api.apify.com/v2/users/me/limits`.

**It never calibrates the scorer.** That is a human labelling ~100 reviews, and
until `data/calibration.json` exists the scorer produces numbers rather than
measurements. A run reports its absence and stops.

## Known gap, recorded rather than hidden

Stages **S, A and D predate the `--group` contract** and still take `--tickers`.
The runner translates — it reads the group's members and invokes each script the
way it actually works. That translation is wiring, not design: those scripts
should take `--group`, and until they do the gap is here in writing rather than
discovered by someone wondering why a flag has no effect.
