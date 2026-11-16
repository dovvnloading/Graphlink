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

## Runtime and storage contract

- A manifest grants access to read and mutate the graph. Pack activation, node creation, execution and reading linked outputs follow the existing plugin grant checks. Run intents are available through the user interface. Builder tool discovery and automatic graph execution exclude them.
- Each operation declares a pack-local ID, a description, labeled input fields, expected measurement units and example values. The common validator enforces exact field names, finite numbers, safe integers, rectangular matrices, and operation-specific domains. Vectors accept 1–1024 values, matrices accept up to 32 columns and 4096 cells, and CSV/text inputs accept up to 65,536 characters. Individual operations may impose smaller bounds, including the DFT and dense linear solver.
- Outputs have a summary and named scalar metrics, plus an optional rectangular table and optional paired XY trace. Scientific Core accepts comma-separated numeric data with unique headers. Unit conversion supports metric length, mass and volume, common time units, Kelvin and Celsius. The XY operation preserves sample order and requires both axis labels.
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
