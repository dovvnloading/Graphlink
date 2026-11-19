# Plate Assays — Biology Workbench reference

**Pack:** plate_assays · **Plugin:** science_plate_assays · **Family:** Biology Workbench

Enable **Plate Assays** in Settings → Plugins, then choose **Plugins → Biology Workbench → Plate Assays**. This pack is independently consent-gated and uses existing scientific result receipts and typed plate-map views. It does not change the older Cell Culture microplate_map operation or saved results.

## Operations

| Operation ID | Inputs | Outputs |
| --- | --- | --- |
| **plate_layout** | plate_format (96/384); wells_json (well → number, short string or null); reading_label | Labeled 8×12 or 16×24 plate-map view, sparse well table, recorded/empty counts |
| **standard_curve** | model (linear, quadratic, 4pl, 5pl); paired standard_concentrations / standard_signals; unknown_signals and matching dilution_factors; concentration_unit and signal_unit | Fitted curve plot, coefficients, standard recovery, unknown concentration and dilution-corrected original sample concentration, status table |
| **assay_quality** | positive_controls and negative_controls signal vectors, signal_unit | Sample mean, SD, %CV, Z′-factor, absolute and normalized signal window, replicate table |
| **qpcr_standard_curve** | standard_quantities, standard_cts, quantity_unit | Ct/log10(quantity) regression, slope, R², amplification factor and efficiency (%) |
| **qpcr_relative_expression** | target Cts, aligned reference Cts, target and reference amplification factors | ΔCt, ΔΔCt, 2^-ΔΔCt, Pfaffl ratio, geometric mean reference normalization |
| **digital_pcr** | positive/total partitions, volume per partition (nL), dilution | Poisson copies/partition, copies/µL, Wilson-based approximate 95% interval |
| **a260_quantification** | type, sample/blank A260/A280/A230, optical path, dilution | Blank-corrected concentration (ng/µL), A260/A280 and A260/A230 purity ratios |

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

**Important 5PL distinction:** the fitted scale parameter \(c\) is the half-response concentration *only if* \(g=1\). For the equation above, the concentration halfway between the fitted asymptotes is

\[
x_{1/2}=c\big(2^{1/g}-1\big)^{1/b}.
\]

The result now includes `half_response_concentration`, `half_response_concentration_unit`, and `half_response_status`. This descriptive half-asymptote concentration is explicitly marked as within or outside the sampled concentration range; an out-of-range value is **not** assay-validated. Unlike unknown-sample concentrations, this model-derived descriptor may be extrapolated and is never used for quantifying unknowns. See [independent correctness audit](SCIENTIFIC_CORRECTNESS_AUDIT_2026.md).

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

### 4. qPCR Ct standard curve and efficiency (qpcr_standard_curve)

Inputs are paired `standard_quantities` (strictly positive, in one consistent chosen unit), `standard_cts` (detected Ct/Cq values), and a free-form `quantity_unit` label. At least **three observations at three distinct quantities** are required. The ordinary least-squares model fits **Ct against log10(starting quantity)**:

\[
Ct_i=a+b\log_{10}(Q_i),\qquad E=10^{-1/b},\qquad \mathrm{efficiency}_{\%}=100(E-1).
\]

A valid curve has a **negative slope** (more starting template gives fewer cycles). Here \(E\) is the amplification **factor per cycle**; ideal doubling is \(E=2\), or **100% amplification efficiency**. The operation reports intercept, slope, R², standard range, Ct residual table and fitted-Ct plot. Efficiency outside an expected assay-specific range is still reported instead of being silently clamped or certified. This estimates efficiency of the supplied calibration; it does not establish that the assay passes MIQE-style controls or determine limits of detection.

**Worked example:** starting quantities [1, 10, 100, 1000] copies/reaction with Cts [30, 26.6780719051, 23.3561438102, 20.0342157153] give \(b\approx-3.321928\), \(E\approx2\), efficiency ≈100%, and R²≈1. A nondecreasing Ct calibration, zero quantity, missing pair or constant Ct fails validation.

### 5. qPCR relative expression (qpcr_relative_expression)

Provide detected `target_ct_sample` and `target_ct_calibrator`, aligned vectors `reference_cts_sample` and `reference_cts_calibrator` (**one position per reference gene**), `target_efficiency`, and aligned `reference_efficiencies`. At least one reference gene is required. Each efficiency is the **per-cycle factor E** (e.g., 1.95 for 95% amplification efficiency), **not** the percentage 95. The target and references may have distinct efficiencies. Factors must be >1 and ≤3; Cts must be between 0 and 100.

Conventional relative expression assumes \(E=2\) for **all** assays. Define a reference mean Ct over the supplied genes in each condition, equivalent to the **geometric mean of their \(2^{-Ct}\) relative quantities**:

\[
\begin{aligned}
\Delta Ct_s &= Ct_{t,s}-\frac{1}{n}\sum_i Ct_{r_i,s},\\
\Delta Ct_c &= Ct_{t,c}-\frac{1}{n}\sum_i Ct_{r_i,c},\\
\Delta\Delta Ct &=\Delta Ct_s-\Delta Ct_c,\\
R_{\Delta\Delta Ct}&=2^{-\Delta\Delta Ct}.
\end{aligned}
\]

The efficiency-corrected **Pfaffl sample/calibrator ratio** instead normalizes the target relative quantity by the **geometric mean of the reference relative quantities**:

\[
R_{\mathrm{Pfaffl}}=
\frac{E_t^{Ct_{t,c}-Ct_{t,s}}}
{\left[\prod_{i=1}^{n}E_{r_i}^{Ct_{r_i,c}-Ct_{r_i,s}}\right]^{1/n}}.
\]

Subscripts \(s\) and \(c\) mean sample and calibrator. Calculations use log-space products for numerical stability and report both fold changes, ΔCt, ΔΔCt, each gene's sample/calibrator ratio and the geometric-mean reference normalizer. **Example:** target Ct 23 (sample), 25 (calibrator), two reference genes 18→20 and 20→19 (sample→calibrator), and factors \(E_t=1.9\), \(E_{r_1}=2\), \(E_{r_2}=1.8\). Then ΔΔCt = −1.5 and conventional fold change is \(2^{1.5}\approx2.8284\); the corrected Pfaffl ratio is \(1.9^2/\sqrt{4/1.8}\approx2.4217\). Reference stability must be established experimentally; the operation does **not** rank reference genes or impute undetected Cts.

### 6. Digital PCR Poisson quantification (digital_pcr)

Inputs: exact integer `positive_partitions`, `total_partitions` (accepted partitions), `partition_volume_nl` (volume of **each** partition in nanoliters), and `dilution_factor` (reciprocal sample dilution, ≥1). Let \(p=k/N\) be the positive fraction, \(v\) the partition volume in µL and \(D\) the reciprocal dilution. Assuming random independent template occupancy and correctly classified partitions:

\[
\lambda=-\ln(1-p)=-\ln(N_{negative}/N),\qquad
C_{measured}=\frac{\lambda}{v},\qquad C_{original}=D C_{measured}.
\]

Outputs include positive and negative counts, positive fraction, copies per partition, copies/µL measured, copies/µL original and a **95% approximate interval** from the Wilson binomial interval on \(p\), transformed monotonically through the same Poisson equation. This interval represents **partition counting uncertainty only**, not volume calibration, false positives, template linkage or other biases. Zero positives yield a zero point estimate with a positive upper confidence bound. All-positive partitions are **saturated** and rejected because no finite Poisson concentration can be inferred. Counts must be nonnegative exact integers with \(0\le k<N\), and the volume must be positive.

**Worked example:** 250 positive of 1000 accepted partitions, 0.5 nL per partition, 1:4 sample dilution gives \(\lambda=-\ln(0.75)\approx0.28768\) and \(C_{original}\approx2301.46\) copies/µL.

### 7. A260 nucleic-acid quantification (a260_quantification)

Inputs: `nucleic_acid_type` (`dsDNA`, `ssDNA`, `RNA`), sample absorbances `a260`, `a280`, `a230`, matching blanks `blank_a260`, `blank_a280`, `blank_a230`, `path_length_cm` and reciprocal `dilution_factor`. All absorbances must be measured on the **same path-length basis**. Subtract blanks independently at each wavelength. For \(A_{260,c}=A_{260,sample}-A_{260,blank}\) and optical path \(l\) cm:

\[
A_{260,1cm}=\frac{A_{260,c}}{l},\qquad
C_{measured}\;(\mathrm{ng/\mu L})=f A_{260,1cm},\qquad
C_{original}=D C_{measured}.
\]

Conventional nucleic-acid conversion factor \(f\) is **50** for dsDNA, **33** for ssDNA and **40** for RNA (ng/µL for A260=1 at 1 cm). Purity ratios are **blank-corrected** \(A_{260,c}/A_{280,c}\) and \(A_{260,c}/A_{230,c}\); path length cancels. If a corrected denominator is zero/negative, the ratio is returned as **undefined**, never infinity, NaN or a misleading pass. Negative corrected A260, zero/negative path and dilution factors below one are rejected. If the instrument **already reports 1-cm-normalized absorbance**, use `path_length_cm=1`, not its physical short path a second time.

**Worked example:** dsDNA A260/A280/A230 = 0.55/0.30/0.25, wavelength-matched blank = 0.05 each, 1-cm normalized reading, 1:10 dilution gives corrected A260=0.50, measured concentration 25 ng/µL, original concentration **250 ng/µL**, A260/A280=2.0 and A260/A230=2.5. UV absorbance may include other nucleic acids and contaminants: purity ratios are screening indicators and are **not** a sample-identity or assay-quality certificate.

**Primary method references:** [Pfaffl, Nucleic Acids Research (2001)](https://doi.org/10.1093/nar/29.9.e45); [Vandesompele et al., Genome Biology (2002)](https://doi.org/10.1186/gb-2002-3-7-research0034); [Bio-Rad Droplet Digital PCR Applications Guide, p. 55](https://www.bio-rad.com/sites/default/files/webroot/web/pdf/lsr/literature/Bulletin_6407.pdf); [Thermo Scientific NanoDrop Lite Plus User Guide, p. 12](https://assets.thermofisher.com/TFS-Assets/MSD/Product-Guides/nanodrop-lite-plus-user-guide-en-s120.pdf).

## Boundaries and references

- All seven operations require the usual plugin grant and explicit Run action. The nonlinear calibration operation allows a 45-second isolated worker budget. Standard Workbench input and 512-KiB receipt limits apply.
- Invalid well addresses or duplicates, nonfinite inputs, ambiguous quadratic turning points, unequal vectors, invalid dilution factors, and insufficient calibration/control data fail closed. No nonfinite or out-of-range quantitative value is exported.
- Tables, plots and plate-map views are included in the tamper-evident scientific result receipt. Results can be reopened and validated without rerunning measurements.
- **Sources:** [Bio-Rad Bio-Plex 4PL/5PL guide](https://www.bio-rad.com/webroot/web/pdf/lsr/literature/10022815.pdf); [NCBI HTS Assay Validation](https://www.ncbi.nlm.nih.gov/books/NBK83783/); [Guidelines for Reporting Non-Clinical Data](https://www.ncbi.nlm.nih.gov/books/NBK550206/).
