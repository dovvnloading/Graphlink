# Plate Assays — Biology Workbench reference

**Pack:** plate_assays · **Plugin:** science_plate_assays · **Family:** Biology Workbench

Enable **Plate Assays** in Settings → Plugins, then choose **Plugins → Biology Workbench → Plate Assays**. This pack is independently consent-gated and uses existing scientific result receipts and typed plate-map views. It does not change the older Cell Culture microplate_map operation or saved results.

## Operations

| Operation ID | Inputs | Outputs |
| --- | --- | --- |
| **plate_layout** | plate_format (96/384); wells_json (well → number, short string or null); reading_label | Labeled 8×12 or 16×24 plate-map view, sparse well table, recorded/empty counts |
| **standard_curve** | model (linear, quadratic, 4pl, 5pl); paired standard_concentrations / standard_signals; unknown_signals and matching dilution_factors; concentration_unit and signal_unit | Fitted curve plot, coefficients, standard recovery, unknown concentration and dilution-corrected original sample concentration, status table |
| **assay_quality** | positive_controls and negative_controls signal vectors, signal_unit | Sample mean, SD, %CV, Z′-factor, absolute and normalized signal window, replicate table |

Concentrations and signals are supplied in **consistent chosen units** (the two unit fields are labels, not automatic conversions). Dilution factors are **reciprocal**: a 1:10 dilution uses 10; undiluted material uses 1.

### 1. Plate layout

Well addresses use uppercase letters, **A1–H12** for 96 wells or **A1–P24** for 384. Example wells_json:

    {"A1":"blank","A2":"standard 1","B1":0,"B2":1.24}

The existing scientific plate_map view shows each well in its physical position. Numeric **zero is measured zero**, not an empty well. Explicit JSON null and omitted addresses are unmeasured. A well cannot appear twice; boolean, object/array, nonfinite numeric, out-of-plate and malformed entries are rejected. Cell values are limited by the typed-view validator to finite numbers, labels up to 128 characters, or null. Export the well table as CSV or the complete result and receipt as JSON.

This operation accepts *raw readings or layout annotations*, rather than inventing sample identity from the numeric measurements.

### 2. Standard curves and back-calculation

Given known concentrations \(x_i\) and observed signals \(y_i\), the models minimize unweighted \(\sum_i(y_i-f(x_i))^2\). The nonlinear fits use the shared bounded Levenberg–Marquardt solver after scaling predictors and responses.

| Model | Equation | Minimum paired standards |
| --- | --- | --- |
| linear | \(y=a+bx\) | 2, at least 2 distinct concentrations |
| quadratic | \(y=a+bx+cx^2\) | 3, at least 3 distinct concentrations, monotone in range |
| 4pl | \(y=d+(a-d)/(1+(x/c)^b)\) | 6, at least 5 distinct **positive** concentrations |
| 5pl | \(y=d+(a-d)/(1+(x/c)^b)^g\) | 7, at least 6 distinct **positive** concentrations |

For logistic curves, \(a\) is the low-concentration limiting signal, \(d\) is the high-concentration limiting signal, \(c>0\) is the concentration scale, \(b>0\) is the Hill slope, and \(g>0\) is the asymmetry (fixed at 1 for 4PL). Increasing and decreasing responses are supported via the ordering of the asymptotes. Logistic calibration spans at most 12 orders of magnitude. Calibration needs varying responses.

**Inverse prediction:** Every observed standard and unknown signal is inverted over the **measured** concentration interval \([x_{\min},x_{\max}]\) using monotone bisection. Responses outside the fitted endpoint signals receive an **outside calibration** status, rather than extrapolated concentrations. The table includes nominal concentration, observed signal, fitted signal, back-calculated concentration and, when nominal concentration is positive, recovery \(100\hat{x}/x\).

For each in-range unknown:

\[
C_{\mathrm{original}}=f^{-1}(y_{\mathrm{unknown}})\times D
\]

where \(D\ge1\) is its corresponding reciprocal sample dilution. Out-of-range unknowns receive blank concentration cells with an explicit status.

**Worked linear example:** standards [0,1,2,3,4] ng/mL and responses [0.1,2.1,4.1,6.1,8.1] RFU yield \(y=0.1+2x\). An unknown reading of 3.1 RFU gives 1.5 ng/mL in the diluted sample; with dilution factor 2, the original sample contains 3 ng/mL. An unknown reading of 5.1 RFU at dilution factor 10 gives 25 ng/mL. Both are in the runnable example.

### 3. Assay quality

Use at least two positive and two negative controls to compute means \(\mu_+,\mu_-\) and **unbiased sample standard deviations** \(s_+,s_-\) (denominator \(n-1\)). The positive control need not be the higher-signal group. Their means must differ.

\[
Z'=1-\frac{3(s_++s_-)}{|\mu_+-\mu_-|}
\]

Absolute signal window in the signal units:

\[
W_{\mathrm{absolute}}=|\mu_+-\mu_-|-3(s_++s_-)
\]

The dimensionless signal window is \(W_{\mathrm{absolute}}/s_{\mathrm{high}}\) with \(s_{\mathrm{high}}\) the SD of the higher-mean control (the **n=1 per-test-well** convention). Some publications use \(s_{\mathrm{high}}/\sqrt{n}\) for production assays with n replicate test wells; this pack does not invent a replicate count.

\[
\%CV=100\,s/|\mu|
\]

When a mean is exactly zero, %CV is reported as **undefined (zero mean)**. When high-control SD is zero, normalized signal window is **undefined (zero high-control SD)**; absolute window and Z′ remain available. There are **no automatic pass/fail labels**: thresholds depend on the assay and screening program.

**Worked example:** positive [98,100,102,100], negative [9,10,11,10] RFU. Their means are 100 and 10; sample SDs are approximately 1.633 and 0.816. Z′ ≈ 0.91835; positive %CV ≈ 1.633%, negative %CV ≈ 8.165%, and absolute signal window ≈ 82.652 RFU.

## Boundaries and references

- All three operations require the usual plugin grant and explicit Run action. The nonlinear calibration operation allows a 45-second isolated worker budget. Standard Workbench input and 512-KiB receipt limits apply.
- Invalid well addresses or duplicates, nonfinite inputs, ambiguous quadratic turning points, unequal vectors, invalid dilution factors, and insufficient calibration/control data fail closed. No nonfinite or out-of-range quantitative value is exported.
- Tables, plots and plate-map views are included in the tamper-evident scientific result receipt. Results can be reopened and validated without rerunning measurements.
- **Sources:** [Bio-Rad Bio-Plex 4PL/5PL guide](https://www.bio-rad.com/webroot/web/pdf/lsr/literature/10022815.pdf); [NCBI HTS Assay Validation](https://www.ncbi.nlm.nih.gov/books/NBK83783/); [Guidelines for Reporting Non-Clinical Data](https://www.ncbi.nlm.nih.gov/books/NBK550206/).
