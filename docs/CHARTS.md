# Chart data contract

Graphlink chart nodes use the version-1 canonical JSON object validated by
`graphlink_chart_data.canonicalize_chart_data`. The server is the authority;
`web_ui/src/app/canvas/chartSpec.ts` performs the same defensive validation.
The version remains **1** because the scientific and new chart-type fields are additive.

**New-type transport compatibility:** generation input uses `"type": "scatter"`,
`"boxplot"`, or `"heatmap"`, but canonical *stored* data for those types leaves
out its optional nested `type` field; the enclosing node's `chartType`
is authoritative. This preserves Graphlink's existing generated version-1
scene validator, whose type enum does not enumerate those types and is out
of scope. Pass the intended chart type explicitly when canonicalizing
that stored payload. Legacy chart types continue to store their nested
`type` normally.

## Existing charts

The five existing shapes remain unchanged. A bar, line, or pie chart has
`type`, `labels: string[]`, `values: number[]`, `title`, `xAxis`, and
`yAxis`. Histogram uses `values` and optional `bins`. Sankey uses a
`flows` array containing `{source, target, value}`. Existing default
titles, axis labels, category ordering, validations, and visual renderers
are preserved without adding optional fields to legacy payloads.

## Scientific line and scatter charts

Both accept optional `xValues: number[]`, `series`, `xScale`,
`yScale`, `xUnit`, and `yUnit`. The two scale values are
`"linear"` (default) or `"log10"`. Units are plain strings; renderers
display them as `Axis label (unit)` without altering numeric data. If
`series` is present it is an array of named series, each with `name`,
`values: number[]`, and optional `xError` and `yError` arrays. The
arrays use the same shared x positions and have the same length. The
series names appear in the legend.

An error array has one entry per data point. An entry is either a
nonnegative number (symmetric), or a two-element array
`[negativeMagnitude, positiveMagnitude]` (asymmetric). These describe
**magnitudes**, not signed lower/upper endpoints. Each point is rendered
at its original value with the corresponding interval; the value is never
moved or rounded to obtain its error bars.

For one-series line charts, `xError` and `yError` may be specified
at the top level along with the historical `labels` and `values`.
When `series` is present, errors belong to the individual series.
A scatter chart requires numeric `xValues` and either a
`series` array or one top-level `values` array. A scientific line
chart can supply numeric `xValues`; without them it retains its
historical evenly spaced, categorically labeled x positions. A log10
x-axis therefore requires `xValues`.

Example:

```json
{
  "type": "scatter",
  "title": "Calibration",
  "xAxis": "Concentration",
  "yAxis": "Response",
  "xUnit": "mg/L",
  "yUnit": "mV",
  "xScale": "log10",
  "yScale": "linear",
  "xValues": [1, 10, 100],
  "series": [
    {"name": "Control", "values": [0.9, 1.8, 3.2],
     "xError": [0.05, [1, 2], 10],
     "yError": [0.1, [0.1, 0.3], 0.2]},
    {"name": "Treatment", "values": [1.1, 2.1, 3.5]}
  ]
}
```

## Validation rules

* All coordinates, values, and error magnitudes must be finite numbers.
  NaN, positive/negative infinity, and numeric overflow are rejected.
* Series must be nonempty and named. Every series, coordinate array, and
  error array must have the same number of points, within the existing
  300-point chart limit. The validator never truncates or drops points.
* Error magnitudes must be nonnegative. The two magnitudes of an
  asymmetric error must both be present and valid.
* A log10 axis requires **strictly positive** coordinates, including the
  lower endpoint of every error interval on that axis. A coordinate of
  zero, a negative coordinate, or a zero/negative lower endpoint is an
  error; it is never silently omitted or clipped.
* The full span across all coordinates **and** all error endpoints must
  also be finite, not merely its individual endpoints. This avoids
  overflowing a plotting domain or compressing unrelated points together.
* Existing bar, pie, histogram, and Sankey validation rules are unchanged.
  An invalid scientific chart is rejected with a field/index-specific
  message; a render request with invalid data continues to show the
  existing chart-error placeholder.

Interactive SVG and the matplotlib PNG/SVG exporter use the original
numeric coordinates and error magnitudes. Hover tooltips use unrounded
number strings with units (not the shortened tick-label formatter).

## Box plots

Use `"type": "boxplot"` when generating data and pass `"boxplot"` as
`chart_type` when canonicalizing a stored chart. Each entry in `groups`
is a `{label: string, values: number[]}` object containing **raw observed
samples**, not precomputed quartiles. The chart keeps the group order and
the observations unchanged. It supports 1–12 uniquely named groups, with
at least two observations per group and no more than **300 observations
total**. All observations must be finite, and their combined range must
also be finite. The default axis labels are `Group` and `Value`.

```json
{
  "type": "boxplot",
  "title": "Response times",
  "xAxis": "Condition",
  "yAxis": "Milliseconds",
  "groups": [
    {"label": "Control", "values": [1, 2, 2, 3, 3, 4, 100]},
    {"label": "Treatment", "values": [2, 3, 3, 4, 5]}
  ]
}
```

Quartiles use the median-of-halves (Tukey) definition: for odd samples,
the overall median is excluded from the two halves. Whiskers stop at the
most extreme observations inside **1.5 × IQR** from Q1/Q3; every remaining
point appears as an outlier marker. This is the same algorithm for live
SVG, overview, and matplotlib PNG/SVG exports. Tooltips show the sample
size, quartiles, median, whiskers, and outlier count.

## Heatmaps

Use `"type": "heatmap"` and a **dense, rectangular** matrix. `rowLabels`
and `columnLabels` are nonempty arrays of text labels; `matrix` is a
row-major `number[][]` with exactly one numeric cell per row/column pair.
An optional `valueUnit` labels the colorbar and exact-value tooltips.
`xAxis` and `yAxis` default to `Columns` and `Rows`. A heatmap supports
at most **300 cells**; ragged, missing, NaN, infinite, and overflowing
value-range data are rejected rather than silently padded or discarded.

```json
{
  "type": "heatmap",
  "title": "Assay response",
  "xAxis": "Time",
  "yAxis": "Group",
  "valueUnit": "AU",
  "rowLabels": ["Control", "Treatment"],
  "columnLabels": ["0h", "24h", "48h"],
  "matrix": [[-2, 0, 1], [1.5, 3, 5]]
}
```

Each matrix has one shared color domain across all rows. Signed matrices
spanning zero use a blue–neutral–red diverging palette centered at zero;
all-positive, all-negative, or constant matrices use a sequential palette.
The interactive SVG exposes each original cell value and labels on hover,
and both export formats include a colorbar. Row/column order is preserved.

## Publication-quality export

Open a chart node and click **Publication export…** in its toolbar. Select
**SVG**, **PDF**, or **PNG**, then choose a physical width:

* **Journal single column:** 85 mm wide;
* **Journal double column:** 180 mm wide;
* **Custom:** enter explicit width and height in millimeters.

The journal presets preserve the chart node's saved width-to-height ratio.
A custom size defines both dimensions. PNG offers **300** and **600 DPI**;
these determine the actual raster pixel dimensions (mm / 25.4 × DPI) and
are embedded as PNG resolution metadata. SVG and PDF are resolution
independent, sized in physical points (72 pt/inch), so their DPI picker
does not rasterize their content.

Publication rendering is performed server-side by
`graphlink_chart_rendering.render_chart_publication`. All eight chart types
(bar, line, scatter including asymmetric error bars, box plot, heatmap,
histogram, pie, Sankey) run through the **same canonical data and per-type
artists** as Graphlink's existing PNG/SVG exporter. Axis names, scientific
units, row and category order, values, error endpoints, and named-series
legends come from the saved chart spec; source measurements are never
inferred or modified for printing. Heatmap cells are vector quadrilaterals
in publication SVG/PDF, with the same row/column layout and a colorbar.
Matplotlib uses 8 pt axis ticks and legend labels and 9 pt axis titles
in physical point units rather than shrinking fonts with the raster DPI.

SVG contains actual `<text>` elements rather than paths for glyphs.
PDF embeds TrueType fonts with text mappings (not a whole-page PNG and
not Type 3 outlines). Both formats support selectable/searchable text.
The export uses a light print background. Check **Colorblind-safe palette**
for the Okabe–Ito categorical colors; check **Grayscale-safe style** for
distinct line markers and dashes, hatched categories, and a grayscale
heatmap. Grayscale style takes precedence if both options are checked.
The interactive canvas's theme is intentionally unaffected.

These are per-export options: they do not change the version-1 chart
JSON or persist new fields. The backend validates all options; the UI provides constrained numeric form fields.
Physical dimensions must be 35–300 mm wide and 30–300 mm high;
DPI is restricted to 300 or 600 and the export is capped at 30 million
raster-equivalent pixels and a 24 MB wire response. Invalid settings and
non-chart node IDs are rejected.

The existing `GET /api/assets/chart/{node_id}/export?fmt=png|svg`
endpoint remains supported for legacy display-sized downloads; its frontend
helper lives in `web_ui/src/app/canvas/charts/legacyExport.ts`. The chart
node now uses one consolidated publication-export control instead of the
old per-format shortcuts. Publication
exports instead use the authenticated `scene.exportPublicationChart`
WebSocket intent with arguments `[nodeId, options]`, returning a bounded
base64 file with its filename, MIME type and resolved physical dimensions.
The frontend uses a temporary one-shot authenticated socket and saves a
local Blob: it does not navigate to a URL containing the session capability
token.

Parity tests live in `backend/tests/test_chart_publication_export.py`
and the chart frontend test files. They cover all eight chart types across
SVG/PDF/PNG, physical page/raster dimensions, selectable text, axes and
units, original scatter/error-bar coordinates, box-plot quartiles/outliers,
heatmap cell arrays, series legends, and authenticated download behavior.

## Generation and testing

The existing `scene.generateChart` intent accepts `"boxplot"` and
`"heatmap"` as its chart type argument (for example, arguments
`[sourceNodeId, "heatmap"]`). The model extraction schemas require the
fields described above and do not invent missing observations. Invalid
model output still creates a valid error-state chart node, just as with
scatter in PR #586. The existing chart-creation submenu is defined outside
this change's permitted file scope and does not yet expose these two
choices. Programmatic intent calls and live scene chart nodes are supported.
The legacy session-save/load path is also outside this scope: it serializes
only the canonical nested chart data, while its loader looks for `data.type`.
Because the version-1 wire format omits that nested discriminator for these
new types (as for scatter in PR #586), legacy session reload currently drops
them. Persisted-session round trips require a separate scoped migration to
carry the enclosing `chartType` through save/load.

Regression fixtures live under `backend/evals/fixtures/chart/`, with
Python canonicalizer/export tests in `backend/tests/` and TypeScript
validator, interactive rendering, and overview tests under
`web_ui/src/app/canvas/`. Legacy bar, line, pie, histogram, Sankey and
scientific line/scatter payloads keep their existing shapes and behavior.
