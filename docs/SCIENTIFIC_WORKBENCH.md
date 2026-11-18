# Scientific Workbench

Enable the desired packs in **Settings → Plugins**, then open **Plugins → Scientific Workbench** on the canvas. Each enabled pack can start a new node or branch from a selected node. Choose an operation, inspect the example, enter measurements in the labeled units, and press **Run calculation**. A grant applies to one pack. Every pack runs locally with Python's standard library.

## Feature plan and acceptance criteria

| Stage | Feature | Acceptance criterion |
| --- | --- | --- |
| 1 | Operation catalog and Scientific Core | Every operation declares its inputs, units, assumptions and an executable example. Import a numeric CSV, convert a measurement or plot paired data. |
| 2 | Domain packs | Each pack has an independent manifest and three implemented operations. Reference cases check calculations and invalid domains. |
| 3 | Interactive graph nodes | Add a node on an empty canvas or branch it. Run explicitly, inspect summaries, values, tables and charts, and export the result. Failed validation preserves the previous result. |
| 4 | Reproducibility | Record exact inputs, method and version, result and input fingerprints, UTC time and any linked source. A run is one undoable graph edit. |
| 5 | Storage and verification | Save and reopen a workspace with the same results. Carry verified result assets through archive export/import, preserve missing asset references, and exercise grants and frontend controls. |

## Pack specifications

| Pack | Operations | Output and assumptions |
| --- | --- | --- |
| Scientific Core | Import numeric CSV, convert a unit, plot paired samples | Rectangular numeric table with unique headers, explicitly dimensioned conversions, labeled XY trace |
| Mathematics | Sample a polynomial, integrate samples, solve a linear system | Horner evaluation, signed trapezoidal integral, row-scaled pivoted solution and residual |
| Statistics | Describe a sample, fit a line, summarize weighted observations | Sample SD, least-squares coefficients and correlation where defined, weighted population SD |
| Physics | Ideal spring oscillator, steady slab conduction, calorimetry | Undamped oscillator, layers in series, constant heat capacity |
| Astronomy | Parallax distance, distance modulus, blackbody peak | Positive parallax, extinction-corrected magnitude, wavelength-form Wien peak |
| Engineering | Equivalent resistance, RC step response, thermal expansion | Ideal resistors, first-order capacitor response, constant linear expansion coefficient |
| Chemistry | Beer–Lambert concentration, transmission spectrum, isotope average | Single absorber, positive percent transmission, abundance-weighted mass |
| Biology and Ecology | Community diversity, quadrat density, community overlap | Count-based richness and diversity, density over surveyed area, presence/absence similarity |
| Earth Science | Great-circle distance, geothermal gradient, seismic distance | Mean spherical Earth, two-point gradient, constant P and S wave speeds |
| Environmental Science | Flow-weighted concentration, event runoff, growing degree days | Volume-weighted mass balance, rainfall over a catchment, daily mean above base |
| Materials Science | Bulk density, tensile response, apparent porosity | Mass per volume, engineering stress and strain, displacement-derived open pore fraction |
| Signal Processing | Moving average, amplitude spectrum, linear resampling | Full centered odd windows, one-sided DFT of uniformly sampled data, interpolation within the supplied interval |

## Worked graph

1. Enable **Scientific Core** and **Statistics**. Add a Scientific Core node. Choose **Import numeric CSV** and paste this data.

   ```csv
   Station,Temperature (°C)
   1,8
   2,10
   3,18
   ```

2. Run the import, select its node and add **Statistics**. In the sample description operation, choose **Scientific Core · Temperature (°C)** from the connected results under **Observations**. Run the calculation. The mean is 12 °C, the median is 10 °C, and the sample standard deviation is approximately 5.292 °C.
3. Expand **Inputs and provenance** to inspect the three measurements, UTC timestamp, pack version and source column. **Export JSON** downloads the backend’s canonical result and receipt, preserving the numeric representation used to calculate the hashes. **Export CSV** saves the result table, plot coordinates or scalar metrics depending on the operation.
4. Editing a copied field detaches its source. A change to an upstream result requires selecting its current output before running the downstream node with that link. Each node retains its last successful run. If another view changes the result while you edit, the form keeps the draft and lets you review the change. Undo and redo work for individual calculations. Duplicate submissions of an identical calculation keep the original receipt.

## SI dimensional analysis in Scientific Core

The **Convert a measurement** operation resolves units to exponent vectors in the seven SI base dimensions, in the order **m, kg, s, A, K, mol, cd**. It performs conversion only when these vectors match, and rejects incompatible units with both dimensions in the error. Scales are stored as exact rational numbers, following SI definitions; no approximate conversion table is used. This is dimensional compatibility, not a guarantee that two quantities with equal dimensions represent the same physical concept (for example, Hz and Bq).

- **Base, named and prefixed units:** m, kg, s, A, K, mol and cd; g; rad, sr, Hz, N, Pa, J, W, C, V, F, Ω / ohm, S, Wb, T, H, lm, lx, Bq, Gy, Sv and kat. Non-SI units accepted alongside the SI include L, min, h, d, t, ha, au and eV; common exactly defined units include Wh, bar and atm. SI prefixes are case-sensitive (for example, mm, µm, km, mA, kPa, MJ, GWh, MeV). For mass, prefixes attach to **g**, not kg.
- **Compound expressions:** use `*`, `·`, `/`, parentheses and integer exponents, including `m²`, `m³`, `s⁻¹` and `m^2/s^2`. Spaces may separate multiplied symbols. `km/h → m/s` converts 72 to 20; `kWh → MJ` converts 2 to 7.2; `N → kg*m/s^2` converts 1 to 1. `kg → m` is rejected as an incompatible dimension.
- **Absolute temperatures:** `K`, `°C` / `degC` and `°F` / `degF` are temperature *points*, not unit scale factors. Their exact affine definitions are `K = °C + 273.15` and `K = (°F + 459.67) × 5/9`. Absolute temperatures below 0 K are rejected. A prefixed kelvin such as `mK` is also an absolute point.
- **Temperature differences:** use `delta_K`, `delta_degC`, `delta_degF` (also `ΔK`, `Δ°C`, `Δ°F`), with scale factors 1, 1 and 5/9 respectively, **without offsets**. For example, a difference of 18 `delta_degF` is 10 `delta_K`. Absolute `°C → delta_degC` and `K → delta_K` are explicitly rejected. In composites such as `W/(m*K)`, K means a temperature *interval*; affine °C and °F cannot appear in products or quotients. `delta_degC` and `delta_degF` can appear in composites.
- **Compatibility and persistence:** The `convert_unit` ID, input field names, metric outputs, existing summaries, pack version `1.1.0`, fingerprints and saved-workspace receipt schema are unchanged. Already saved results are verified against their original receipt, not recalculated. Out-of-range or underflowed conversions, including prefixed absolute-temperature conversions, are rejected.

**References:** [BIPM SI Brochure, 9th edition (updated 2026)](https://www.bipm.org/en/publications/si-brochure/); [NIST SP 811, chapter 4 (derived units and prefixes)](https://www.nist.gov/pml/special-publication-811/nist-guide-si-chapter-4-two-classes-si-units-and-si-prefixes); [NIST SP 811, appendix B (conversion factors)](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b9); [NIST SP 330, section 4](https://www.nist.gov/pml/special-publication-330/sp-330-section-4). NIST SP 811 predates the 2019 SI redefinition; the value of the electronvolt is now **exactly** 1.602176634 × 10⁻¹⁹ J because the elementary charge has an exact defining value.


## Chart a result directly

Run a Scientific Workbench operation, then use **Chart type → Chart this** in
the result card. This creates a connected, ordinary Graphlink **Chart** node.
It does not ask an AI to extract measurements: the chart adapter reads only the
current, grant-checked and checksum-verified scientific result. The chart is an
undoable graph change and uses the existing scatter, line, box-plot and heatmap
renderers (including existing SVG zoom, export and resize controls).

- **XY trace:** Scatter preserves numerical x/y coordinates, sample ordering
  and axis labels. A trailing unit in parentheses (e.g. Time (s)) is shown
  as the axis's physical unit. If the result table contains the exact plotted
  x/y measurements plus a nonnegative uncertainty column, the scatter chart
  uses its values as vertical error bars. A standalone GUM measurement
  uncertainty result plots the estimate with its reported asymmetric coverage
  limits (or standard uncertainty if there is no coverage interval).
- **Fit:** The *Fitted line with 95% confidence envelope* option uses measured
  x/y pairs and the saved parameters of a statistical linear regression, a
  single-predictor weighted linear fit, or a nonlinear least-squares model.
  It overlays the fitted values, pointwise 95% mean-response confidence
  limits and error bars around the fitted curve. The existing multi-series
  **line** chart draws the upper and lower limits as boundary traces, rather
  than adding a new filled-band renderer. This is **not** a prediction or
  simultaneous confidence interval. Weighted fits use their stored
  covariance; nonlinear fits reconstruct the local linearized covariance
  from the stored optimum and weights, consistent with the fitting pack's
  first-order approximation. Other model shapes are not mislabeled as fits.
- **Tables:** A table with repeated text group labels and numeric observations,
  or columns containing enough raw numeric observations, can produce a
  **box plot**. A rectangular numeric matrix, with optional unique text row
  names, can produce a **heatmap**. Neither fabricates missing cells or
  converts aggregated summary statistics into fake raw observations.

The available chart types respect the same underlying limits as ordinary
charts: 300 total plotted points/observations/cells, 12 box-plot groups and,
for a four-series fitted line, 75 paired measurements. The server checks
these bounds again before adding a node and reports invalid/unsupported
shapes without creating an invented placeholder.

**Freshness and reproducibility:** The chart's canonical data stores a
scienceReceipt containing the source's durable sourceKey, its
resultHash and the exact receiptHash from which the chart was drawn. The
source-to-chart connection and node ids follow normal scene save/reopen.
On a new successful source run, dependent charts retain their last rendered
values and gain the existing chart warning badge. The result card lists
**Linked charts**, marks each as **Source changed · refresh to update**, and
offers **Refresh chart** to recalculate the *same* chart node from the
current source receipt. Refresh is undoable. Comparing receipt hashes instead
of numerical result hashes alone also catches changes to input lineage where
the displayed numbers happen to match. Revoked/damaged scientific packs
cannot be used as chart-generation sources. Regular model-generated charts
and CSV-table-driven charts keep their existing flows.

## Runtime and storage contract

- A manifest grants access to read and mutate the graph. Pack activation, node creation, execution and reading linked outputs follow the existing plugin grant checks. Run intents are available through the user interface. Builder tool discovery and automatic graph execution exclude them.
- Each operation declares a pack-local ID, a description, labeled input fields, expected measurement units and example values. The common validator enforces exact field names, finite numbers, safe integers, rectangular matrices, and operation-specific domains. Vectors accept 1–1024 values, matrices accept up to 32 columns and 4096 cells, and CSV/text inputs accept up to 65,536 characters. Individual operations may impose smaller bounds, including the DFT and dense linear solver.
- Outputs have a summary and named scalar metrics, plus an optional rectangular table and optional paired XY trace. Scientific Core accepts comma-separated numeric data with unique headers. Unit conversion accepts case-sensitive SI base symbols, prefixes (including the 2022 prefixes), named derived units, and compatible products, quotients and integer powers. Temperature points and temperature differences have distinct kinds. Incompatible dimensions and mixed temperature kinds are errors, not silently scaled values. The XY operation preserves sample order and requires both axis labels.
- Every receipt records a UTC timestamp, stable operation ID, pack version, SHA-256 input and result hashes, and field-level input sources where supplied. Source selections carry a stable node key, a result hash and a selector. The host verifies a direct incoming connection, the source grant, the current result and the copied values. The result fingerprint excludes the timestamp so repeat runs can be compared. Pack version 1.1.0 adds a separate receipt hash covering time and provenance. A connected input now pins that receipt as well, so changing the source of the same numerical answer can be detected. Saved 1.0.0 results still open with their original input and result hashes. Their original format did not checksum the timestamp or source citations.
- Each completed result is limited to 512 KiB. When a workspace has an asset store, compact state of at least 8 KiB is saved as content-addressed JSON and travels with workspace archives. Smaller states stay in the graph. Catalog descriptions and example data come from the installed pack. A missing asset or invalid receipt is displayed on the node so the saved work can be recovered. A disabled pack retains its nodes, edges and passive saved data after a restart. Re-enabling it restores access to the result.
- Methods and reference calculations for all eleven domain packs are in the [quantitative reference](SCIENTIFIC_QUANTITATIVE_REFERENCE.md) and [natural sciences and signal processing reference](SCIENTIFIC_NATURAL_REFERENCE.md).


## Reproduce the workbench checks

The operation tests use seeded inputs, independent formulas and worked reference values. The workflow tests cover grant changes, source changes during calculation, conflicting edits, canonical exports, cold restart with a disabled pack, undo, and recovery of missing assets.

From a checkout with the repository’s Python test dependencies installed, run:

```sh
python -m pytest -n0 backend/tests/test_scientific*.py tests/test_scientific*.py backend/tests/test_plugin_state_assets.py backend/tests/test_packaging_bundled_resources.py
```

From `web_ui`, run `npm run check` for the frontend suite and build. With the Python backend available and Playwright Chromium installed, run `npx playwright test e2e/scientific-workbench.spec.ts` for the real browser workflows.
