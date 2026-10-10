# Render Contract

What the dashboard is allowed to draw, and what it must refuse to draw.

These are not styling preferences. Each one is a specific way a chart can make a
null result look like a finding, and each is enforced in code — in
`scripts/build_dashboard.py` where the data is gated, or in
`templates/dashboard.html` where the geometry is drawn. A rule that lives only
in a reviewer's memory is a rule that fails on the day it matters.

---

## R1 · A tone number never appears without its count

**Enforced:** `toneChart()` draws the n-strip *inside the chart SVG*, below the
axis, as part of the same figure.

It is welded into the chart geometry on purpose. If the counts were a separate
HTML row beneath the figure, some later layout change could drop them and the
chart would still render — a tone line with no sample sizes, which is the exact
artefact this harness exists to avoid. To remove the counts now, someone has to
edit the chart function itself.

The volume chart labels the **scored product-review count**, not the stack
total, because the floor applies to scored reviews. Labelling the total puts a
number next to the floor line that is not the number the floor tests.

## R2 · A below-floor quarter is never a point on the line

**Enforced:** `build_dashboard.py` assigns each quarter a `state`; the template
switches on it.

| state | meaning | how it draws |
|---|---|---|
| `renderable` | clears the floor, has a mean and an interval | filled marker on the line |
| `insufficient` | under `MIN_REVIEWS_PER_QUARTER` | hollow amber ring at the axis; **the line breaks** |
| `no_estimate` | no usable mean, or a mean with no interval | hollow dashed ring; **the line breaks** |

The line breaking matters as much as the marker. A line drawn *through* a thin
quarter asserts a continuity the data does not have; interpolating across a
six-review quarter invents the quarter.

The floor is re-derived from `n_reviews` rather than trusted from the
`sufficient` column. When the two disagree, the floor wins and the disagreement
is surfaced as a warning rather than smoothed over.

## R3 · A point estimate without an interval is not rendered

**Enforced:** `load_quarters()` sets `state = "no_estimate"` when `tone_ci_low`
or `tone_ci_high` is missing, and emits a warning.

CORE-PROMPT §5 calls an interval-less estimate an incomplete result. The
dashboard treats "incomplete" as "not renderable", not as "renderable with a
caveat". The CI band is drawn from the same per-quarter record as the marker, so
there is no code path that produces one without the other.

An empty CSV cell parses to `None`, never to `0.0`. A missing tone mean coerced
to zero would place a fabricated point on the line at exactly the spot where the
pipeline had nothing to say.

## R4 · Below the quarter floor, the trend chart is not drawn at all

**Enforced:** `render_trend` is false when usable quarters < `MIN_QUARTERS`; the
template substitutes a refusal panel for the chart.

This is the strongest rule here and the easiest to water down. The tempting
version is to draw the line and caption it "small sample — interpret with
caution". That version loses: the caption is read once, the shape is read every
time, and the shape is what gets screenshotted into a conversation.

So the chart is replaced, not annotated. The refusal panel states the floor, the
shortfall, and why small quarters swing. The review counts still render
underneath it — counts are facts about what was retrieved, not estimates.

The Logitech M510 pilot (8 quarters, 1–28 reviews each) is the canonical input
for this path, and `make_fixture.py --scenario below-floors` reproduces its
shape. If that input ever produces a tone line, this skill is broken.

## R5 · A hit rate always appears beside the base rate of the same event

**Enforced:** `build_dashboard.py` drops a hit rate that arrives without a
`base_rate` and logs it; the template renders the pair or renders nothing.

When the hit rate is below the base rate, the panel says
`BELOW THE BASE RATE — THIS IS A FAILURE` in the refusal style, names F7, and
states plainly that always guessing the common outcome would have scored better.
It is never phrased as "58% accurate".

This is the failure mode most likely to be laundered into something
authoritative, because a lone percentage reads as a result to anyone who does
not know the base rate.

## R6 · The verdict is not a traffic light

**Enforced:** the `VERDICT` map in the template.

| verdict | colour | why |
|---|---|---|
| `insufficient_data` | amber | the instrument declining to answer |
| `no_signal` | neutral | a legitimate, expected, publishable result |
| `signal` | accent blue | measured — deliberately **not** green |

A green `signal` pill reads as "go", and "go" is a recommendation. The three
verdicts are measurement outcomes, not desirability grades. Each ships with an
icon *and* the word, so colour never carries the meaning alone.

Verdicts are **downgrade-only**. `results.jsonl` can supply `signal` or
`no_signal`, but only when the floors have already passed. A results row
claiming `signal` for a company under the floors still renders as
`insufficient_data`. Floors passing with no backtest row is also
`insufficient_data` — tone was measured, prediction was not tested, so there is
nothing to claim.

## R7 · Scope, limits and the no-advice framing are structural

**Enforced:** `closing()` renders them unconditionally, outside the tab system.

They are not a tab, because a tab can go unopened, and not a footer note,
because a footer note gets skimmed. They are full-width panels in the page's
main column, after the charts, every time.

The closing panels also carry a **Checks** list that reports each gate's state —
including the ones that FLAG, such as a missing prereg hash (F6) or unquantified
coverage (F3). A dashboard that hides its own failed checks is worse than one
that has none.

## R8 · No buy, sell, hold, target, size, entry, exit or allocation

**Enforced:** by absence, and by the closing panel that says so.

Nowhere in the template, the scripts, the fixtures or this document does such a
term appear — not as an example, not hedged, not hypothetical. The page states
what was measured, how much data stood behind it, how wide the uncertainty is,
and how often the method has been wrong. Then it stops.

If someone asks this skill to add a recommendation, the answer is the one in
CORE-PROMPT §1.1: decline the call and give them the measurement.

---

## Colour

The two series colours are slots 1 and 2 of the data-viz reference palette, and
the pair was run through `validate_palette.py` against this page's actual
surfaces rather than the defaults:

| | light | dark |
|---|---|---|
| surface | `#fbfbfc` | `#17181a` |
| product reviews / mean tone | `#2a78d6` | `#3987e5` |
| seller-only (excluded) | `#eb6834` | `#d95926` |

All checks pass in both modes (worst adjacent CVD ΔE 24.7 light / 26.8 dark
against a target of 8; normal-vision ΔE 33.6 / 31.8 against a floor of 15).

Grey was the first choice for seller-only, since those records are excluded, but
it fails the chroma floor as a categorical series (OKLCH C 0.009) and reads as
chart furniture rather than as data. The exclusion is carried by the legend and
the stacking order instead.

Amber `#fab219` marks below-floor state. It sits under 3:1 on the light surface
by design, so it is never alone: every amber mark is paired with a shape (hollow
ring, dashed border) and a label.

## Running the validator

`node` is not installed on this machine; the dataviz skill ships a Python twin
that takes the same arguments.

```bash
python3 <dataviz-skill>/scripts/validate_palette.py "#2a78d6,#eb6834" --mode light --surface "#fbfbfc"
python3 <dataviz-skill>/scripts/validate_palette.py "#3987e5,#d95926" --mode dark  --surface "#17181a"
```

Re-run both if either series colour or either surface changes. Do not eyeball it.
