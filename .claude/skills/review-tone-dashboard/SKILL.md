---
name: review-tone-dashboard
description: Render review-tone pipeline results for one company as a published Artifact dashboard — quarterly tone with bootstrap intervals and review counts, volume against the sample floor, hit rate beside its base rate, and the signal / no_signal / insufficient_data verdict. Use when asked to visualise, chart, publish or share the output of the review-tone-signal pipeline for a ticker. Renders measured numbers only; never produces investment recommendations.
---

# Review Tone Dashboard

Turns one company's `quarters.csv` and `fundamentals.csv` into a published
Artifact. It is the presentation layer for `review-tone-signal`, and it inherits
every rule that skill is bound by.

**Read `REVIEW-TONE-CORE-PROMPT.md` in the project root in full before using
this.** It is locked and binding. The four rules this skill is most able to
break, because a chart breaks them silently:

- §1.1 Never a buy, sell, hold, target, size, entry, exit or allocation — not as
  an example, not hedged, not hypothetical.
- §1.3 Never compute a result number. This skill **reads** tone means, deltas,
  intervals and hit rates from pipeline output. It does not derive them.
- §5 Floors are render gates, not captions.
- §6 Every hit rate appears beside the base rate of the same event.

A dashboard is more dangerous than a table. A table with six reviews in it looks
like six reviews; a line chart of the same six looks like a trend. Everything
below exists because of that difference.

## Run it

```bash
python3 .claude/skills/review-tone-dashboard/scripts/build_dashboard.py HELE \
  --company "Helen of Troy" --formation-year 2016
```

Writes `hele-review-tone.html` plus `hele-review-tone.payload.json`, prints the
verdict, the floor result, and every check that flagged. Then publish the HTML
with the Artifact tool.

| flag | default | notes |
|---|---|---|
| `--formation-year` | — | universe formation year, for the scope line |
| `--data-dir` | `data` | where `{TICKER}/quarters.csv` lives |
| `--results` | `results/results.jsonl` | supplies verdict and hit/base rates |
| `--min-reviews` | 30 | per-quarter floor, CORE-PROMPT §5 |
| `--min-quarters` | 12 | per-company floor, CORE-PROMPT §5 |
| `--data-source` | `pipeline` | `synthetic_fixture` stamps a permanent banner |

Floors are overridable because the prereg fixes them per run, never to get a
chart out of thin data. Whatever is used is written into the payload and printed
on the dashboard face.

## What it reads

Per `references/data-contracts.md` in the sibling skill:

- `data/{TICKER}/quarters.csv` — **required**. Missing columns stop the run
  rather than rendering a partial frame.
- `data/{TICKER}/fundamentals.csv` — optional. Point-in-time revenue, shown with
  both `period_end` and `filed_date` so the non-public window is visible (F2).
- `data/{TICKER}/manifest.json` — optional. Provenance: hashes, rubric version,
  scorer model ID, coverage ratio.
- `results/results.jsonl` — optional. Verdict, hit rate, base rate, null run,
  specifications tested. Last matching row wins; the file is append-only.

If `quarters.csv` does not exist, Phase B5 has not run. The script says so and
exits. It does not substitute, estimate or interpolate.

## What it refuses to draw

Full detail in `references/render-contract.md`. Read it before changing any
chart code. In short:

- A tone line is never drawn without review counts welded into the same figure.
- A quarter under the review floor is a hollow ring and a **break in the line**,
  never a point on it.
- A tone mean without a bootstrap interval is not rendered at all.
- A company under the quarter floor gets **no trend chart** — a refusal panel
  replaces it. The chart is not drawn and captioned; it is not drawn.
- A hit rate without its base rate is dropped. A hit rate below its base rate is
  labelled a failure, never "58% accurate" (F7).
- Verdicts are downgrade-only. A `signal` row for a company under the floors
  still renders `insufficient_data`.
- The verdict is not a traffic light. `signal` is blue, never green, because
  green reads as "go" and "go" is a recommendation.

## Verifying a change

Three fixtures cover the gates. They are synthetic and labelled as such at every
layer; never run one through the `pipeline` data source.

```bash
python3 .claude/skills/review-tone-dashboard/scripts/make_fixture.py \
  --ticker PILOT --scenario below-floors --outdir /tmp/fx/data
```

| scenario | shape | the dashboard must |
|---|---|---|
| `below-floors` | 8 quarters, 1–28 reviews — the Logitech M510 pilot's shape | refuse to chart; `insufficient_data` |
| `mixed` | 16 quarters, three under the review floor | draw the line **broken** at those three |
| `above-floors` | 16 clean quarters, hit rate 58% vs base rate 65% | read that as a failure, not a result |

If `below-floors` ever produces a tone line, this skill is broken.

## Charts

Load the `dataviz` and `artifact-design` skills before touching chart code.
Colours are slots 1 and 2 of the data-viz reference palette, validated against
this page's own surfaces in both modes — the run and its numbers are recorded in
`references/render-contract.md`. `node` is not installed here; the dataviz skill
ships `scripts/validate_palette.py`, which takes the same arguments. Re-run it
for both modes if a series colour or a surface changes, and do not eyeball it.

## Publishing

The template is a complete standalone HTML file. Publish it with the Artifact
tool, passing `icon: "chart"` on the first publish for a ticker and omitting
`icon` on every redeploy. Reuse that artifact's `url` for later runs of the same
company so the link stays stable; a new path creates a second artifact.

Give the artifact the ticker's own name — `HELE Review Tone` — not a description
of what the page does.

## When the user asks what to do about it

They will, because a dashboard invites it. Decline the call and give them what
the instrument produces: the measured effect, its interval, the sample behind
it, the baseline it is compared against, and how often it has been wrong. Then
stop. This skill renders measurements; it does not advise, and it does not
produce a hedged or hypothetical recommendation either.
