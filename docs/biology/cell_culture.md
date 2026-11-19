# Cell Culture — Biology Workbench reference

**Pack:** `cell_culture` · **Plugin:** `science_cell_culture` · **Family:** Biology Workbench

Enable **Cell Culture** in Settings → Plugins and select **Plugins → Biology Workbench**. This is an independent, grant-gated pack: it does not change any existing `science_biology` operation, result, or saved receipt. All nine operations use the Python standard library, and results use the Workbench's verified summary, scalar values, optional tables, and typed result views.

## Operations at a glance

| Operation ID | Inputs and units | Outputs |
| --- | --- | --- |
| `cell_count_viability` | `live_counts`, `dead_counts` (cells per equal-volume counted region); `dilution_factor` (dimensionless); `counted_volume_ul` (µL per region); `sample_volume_ml` (mL of *original* suspension) | Total and live/nonviable counts (cells), viability (%), viable/nonviable/total density (cells/mL), estimated viable cells in original sample, per-region table |
| `seeding_plan` | `viable_cells_per_ml` (cells/mL); `target_cells_per_vessel` (cells); `vessels` (count); `final_volume_ml_per_vessel` (mL); `overage_percent` (%) | Suspension and medium per vessel and in a bulk mixture including overage (mL), bulk viable cells (cells), preparation table |
| `serial_dilution_plan` | `initial_concentration` (chosen `concentration_unit`); `concentration_unit` (text label, e.g. `cells/mL`); `step_dilution_factor` (dimensionless); `steps` (count); `retained_volume_ml` (mL per tube **after** downstream transfer) | Concentration at each step, volume taken from previous solution and diluent added (mL), initially mixed and finally retained volumes (mL), total stock and diluent requirements |
| `centrifuge_rcf_rpm` | `input_kind` (`rpm` or `rcf`); `input_value` (rpm or multiple of gravity ×g); `rotor_radius_cm` (cm from rotation axis to the sample position) | Both rpm and RCF (×g), with radius and units |

| `plate_count_cfu` | `colony_counts` (CFU/plate); `reciprocal_dilutions` (factor/plate); `plated_volume_ml` (mL/plate) | Exposure-weighted pooled CFU/mL; per-plate table and countability flags |
| `microplate_map` | `plate_format` choice (`96` or `384`); `wells_json` (JSON well-ID → number/text/null); `measurement_label` (text) | Scrollable 96/384-well grid and sparse data table for export; empty wells are not zero |
| `most_probable_number` | `positive_tubes`, `total_tubes` (tubes/level); `reciprocal_dilutions` (factor/level); `inoculum_volume_ml` (mL/tube) | Poisson-likelihood MPN/mL; observed/fitted positive tubes by level |
| `growth_curve_parameters` | `time`, `abundance` (numeric vectors with selected `time_unit`, `abundance_unit` labels) | Modified Gompertz lag, maximum specific growth rate, asymptote, fastest instantaneous doubling time, diagnostics, table/plot |
| `doubling_time` | `initial_abundance`, `final_abundance`, `elapsed_time` (numeric; selected abundance/time units) | Two-point exponential growth rate, doubling time and observed doublings |

### 1. Count cells and estimate viability

For **n** equal-volume counted regions, with live counts `L_i`, nonviable counts `D_i`, total dilution factor `f` of the *original* sample, and counted volume `v` in µL **per region**:

- Viable cells/mL = `(sum(L_i) / n) × f × (1000 / v)`.
- Nonviable cells/mL = `(sum(D_i) / n) × f × (1000 / v)`; total concentration is their sum.
- Viability (%) = `100 × sum(L_i) / (sum(L_i) + sum(D_i))`.
- Estimated viable cells in sample = `viable_cells_per_ml × sample_volume_ml`.

For a standard Neubauer large square (area 1 mm², depth 0.1 mm), `v = 0.1 µL`, hence the conventional `10,000` cells/mL multiplier **before** sample-dilution correction. A 1:1 sample/trypan-blue mix uses `dilution_factor = 2`; include any additional dilution in this *total* factor. Do not enter the full chamber loading volume in place of the small region volume.

**Worked example:** Live counts `[90,110,100,100]`, nonviable counts `[10,10,20,0]`, factor `2`, region volume `0.1 µL`, original sample `5 mL`: **2,000,000 viable cells/mL**, **200,000 nonviable cells/mL**, **90.9091% viability**, and **10,000,000 estimated viable cells** in the sample. The output table preserves each region's counts. Empty-total counts are rejected because viability would be undefined; unequal-length series, negative/fractional counts, and totals exceeding 2⁵³−1 are also rejected.

This uses dye-exclusion observations; it does not assess metabolic fitness, contamination, growth behavior, or the accuracy of the original sample mix. Use a consistent counting-boundary rule and equivalent region volumes.

### 2. Calculate viable-cell seeding volumes

Let `C` be viable cells/mL, `N` target viable cells per vessel, `V` final vessel volume in mL, `k` vessel count, and `p` preparation overage (%) assumed to apply equally to both components.

- Cell suspension per vessel = `N / C` mL.
- Medium per vessel = `V − N / C` mL.
- Bulk suspension = `(N / C) × k × (1 + p/100)` mL.
- Bulk medium = `(V − N / C) × k × (1 + p/100)` mL.

**Worked example:** `C=2,000,000 cells/mL`, `N=100,000 cells`, `k=12`, `V=2 mL`, `p=10%`: **0.05 mL (50 µL)** suspension + **1.95 mL** medium per vessel; prepare **0.66 mL** suspension + **25.74 mL** medium = **26.4 mL** bulk. Overage is for preparing extra *mixture*, not for increasing each vessel's target cell count. Plans requiring more cell suspension than the final vessel volume are rejected; the pack does not silently concentrate cells.

### 3. Prepare serial dilutions with enough volume left in every tube

A basic repeated transfer into `V` mL can leave earlier tubes short of the desired final amount. This planner instead works backward to provide **V mL remaining in each of the n tubes**, including the ones used to make subsequent dilutions.

For step factor `D > 1`, `C_i = C_0 / D^i`. Let `P_i` be the volume to mix initially in tube `i`. Starting at the last tube, `P_n = V`, and for earlier tubes `P_i = V + P_(i+1)/D`. Each tube takes `P_i / D` mL of the preceding solution and adds `P_i × (1 − 1/D)` mL diluent. The first tube takes its aliquot from the original stock. The table lists all these amounts.

**Worked example:** `C_0=1,000,000 cells/mL`, `D=10`, `n=3`, `V=1 mL`:

| Tube | Concentration (cells/mL) | Take from previous (mL) | Add diluent (mL) | Mix initially (mL) | Remains after transfer (mL) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 100,000 | 0.111 | 0.999 | 1.110 | 1.000 |
| 2 | 10,000 | 0.110 | 0.990 | 1.100 | 1.000 |
| 3 | 1,000 | 0.100 | 0.900 | 1.000 | 1.000 |

Total original stock used: **0.111 mL**; total diluent: **2.889 mL**; final retained volume: **3.000 mL**. The operation supports 1–100 steps and refuses underflow/overflow instead of printing zero concentrations. Numeric concentrations are assumed to be expressed in the supplied label; arbitrary labels are **not** converted to each other. This is an ideal volumetric plan; pipetting dead volume, instrument resolution, mixing error, and any desired excess must be accounted for separately.

### 4. Convert centrifuge RCF and rpm

For radius `r` in cm from the rotation axis to the selected sample position:

`RCF (×g) = 1.118 × 10^-5 × r(cm) × rpm²`

`rpm = sqrt(RCF / (1.118 × 10^-5 × r(cm)))`

Select `input_kind = "rpm"` or `"rcf"`; enter its numeric value and the applicable radius. **10 cm and 3,000 rpm → 1,006.2 ×g**; conversely 1,006.2 ×g at 10 cm → **3,000 rpm**. At 5 cm the same rpm generates only 503.1 ×g. Zero speed/RCF is supported; a zero/negative radius or negative speed/force is not.

RCF changes with radius: `r_max` often describes the **bottom of the tube**, but `r_avg` and `r_min` produce different forces. Consult the rotor specification for which point and radius a protocol uses. This conversion does not provide a centrifugation duration, biological recommendation, rotor limits, or approval to operate above instrument/tube ratings.

### 5. Count colonies and estimate CFU/mL

Use colony counts per plate, reciprocal dilution factors per plate (1000 means a 1:1000 dilution), and the volume of **diluted** material plated per plate in mL. All plates share a plated volume, but their dilutions may differ. For plate i, the volume of *original* sample represented is vᵢ = V/Dᵢ. The pooled Poisson maximum-likelihood estimate is:

\[
\widehat{\mathrm{CFU/mL}}=\frac{\sum_i c_i}{\sum_i v_i}.
\]

This exposure-weighted estimate is **not** the unweighted mean of plate-specific CFU/mL values when dilutions differ.

**Worked example:** Colony counts [50,20], reciprocal dilutions [100,1000], and 0.1 mL plated on each plate represent 0.001 and 0.0001 mL of original sample. Thus 70/0.0011 = **63,636.36 CFU/mL**. The table reports counts, dilutions, exposures, per-plate CFU/mL and the indicative 30–300 countability flag.

The operation reports but does not silently exclude plates outside 30–300. That range is indicative and varies by assay; sparsity, crowding and counting conventions matter. A zero-colony result gives a zero *point estimate*, not proof of sterility. CFU may represent viable clumps rather than individual cells. Censored or too-numerous-to-count plates cannot be entered as exact counts.

### 6. Estimate most probable number (MPN) from dilution tubes

Enter positive-tube counts, total tube counts and **strictly increasing reciprocal dilution factors** (one entry per dilution level). The inoculum volume in mL is the amount of **diluted** sample tested *per tube*, shared across dilution levels. At dilution Dᵢ and inoculum V, a tube receives vᵢ = V/Dᵢ mL of original material.

Assuming independent homogeneous Poisson sampling, the probability a tube is positive at original-sample concentration C (MPN/mL) is pᵢ = 1 − exp(−Cvᵢ). With kᵢ positives of nᵢ tubes, the maximum-likelihood estimate maximizes:

\[
\log L(C)=\sum_i k_i\log(1-e^{-Cv_i})-(n_i-k_i)Cv_i.
\]

The positive finite maximum is found by a bracketed scalar root; the output table shows fitted positive probabilities and expected positives.

**Independent numerical check:** For positive counts [5,3,0] out of [5,5,5] tubes, reciprocal dilutions [1,10,100] and 1 mL inoculum, the MLE is **7.92427 MPN/mL**. One positive of two tubes with 1 mL undiluted exposure gives exactly ln(2) MPN/mL. All-negative observations yield a boundary estimate of **0 MPN/mL**, a nondetection, not proof of absence. All-positive tubes have **no finite MLE** and must include higher dilutions containing negative tubes. This operation reports a point estimate, not confidence intervals or a literal counted organism total.

### 7. Fit culture lag time, maximum growth rate and asymptote

Enter paired vectors of time and strictly positive viable counts (or abundance proportional to viable counts), with consistent time and abundance unit labels. The operation fits the **modified Gompertz** growth model to the *natural logarithm* of the population ratio:

\[
y(t)=\ln\!\left(\frac{N(t)}{N_0}\right)
=A\exp\left\{-\exp\left[\frac{\mu_{\max}e}{A}(\lambda-t)+1\right]\right\}.
\]

Here N₀ is the first observed abundance; A is the asymptotic **log abundance ratio**, so the predicted upper asymptote is N₀ exp(A). The parameter μmax is the **maximum specific growth rate** in inverse time units: the maximum slope of ln(N). The lag time λ is the inflection tangent's baseline intercept, **not a directly observed onset of growth**.

The fit uses unweighted nonlinear least squares on log ratios and the Workbench's existing bounded Levenberg–Marquardt engine. Time is internally offset and scaled, with outputs restored to the selected units. Results include fitted parameters, local linearized standard errors, log-space R², an observed-versus-fitted table and a plot of predicted abundance. The shortest **instantaneous** doubling time implied by the fitted maximum growth rate is ln(2)/μmax, not necessarily the time for a finite culture to double.

**Worked example:** A synthetic culture with starting abundance approximately 10,000 CFU/mL, A=4, μmax=0.5 h⁻¹, and λ=4 h, sampled every two hours through hour 20, approaches **546,000 CFU/mL**, with fitted lag near **4 h**, maximum growth rate near **0.5 h⁻¹**, and minimum instantaneous doubling time near **1.386 h**.

The operation needs 6–120 observations at distinct times with a net increase and evidence of an approaching plateau. Constant, decreasing, exponential-only or nonpositive-abundance series cannot identify all of these parameters and are rejected. Estimates depend on covering the lag, rapid-growth and saturation phases and on reasonable log-space residuals.

### 8. Calculate a two-point doubling time

For positive abundances N₁ and N₂ with N₂ > N₁, and positive elapsed time Δt, assume approximate exponential growth during the interval. For near-equal observed abundances the implementation evaluates \(\ln(1+(N_2-N_1)/N_1)\), avoiding a subtraction of large, nearly identical logarithms. This is a numerical precision improvement, not evidence that such small biological changes are experimentally resolvable:

\[
\mu=\frac{\ln N_2-\ln N_1}{\Delta t},
\qquad t_d=\frac{\ln 2}{\mu}.
\]

**Worked example:** 100,000 to 800,000 CFU/mL over 6 hours is three doublings, a specific growth rate ln(2)/2 h⁻¹ and **2 h doubling time**. Both abundance inputs must use the same units or proportional measurement, and the time field accepts compatible time measurements (for example 360 min with a chosen time unit of h). No increase or zero/negative values are rejected.

Unlike the growth fit's minimum instantaneous doubling time, the two-point result is an **average exponential-phase estimate over the observed interval**. Lag phases and plateaus invalidate its constant-rate assumption.

### 9. Display measurements on a 96/384-well plate

Select **96** or **384** in the microplate-map operation, then supply a JSON object whose keys are well addresses and whose values are finite numbers, short labels, or `null`. For example: `{"A1":"control","A2":0,"B3":0.65,"H12":null}`. Well IDs are uppercase and range from **A1–H12** (96-well) or **A1–P24** (384-well); every address must be unique and inside the selected format. The reading label supplies a display unit (for example, `Signal (RFU)`).

The resulting typed plate map presents all wells in row/column order, with numeric intensity shading, a tooltip for each measurement, and horizontal scrolling for 384-well plates. The accompanying sparse table and exported JSON retain input readings exactly; unspecified and explicit `null` wells remain unmeasured, distinct from an observed zero. This is a layout of observations, **not** a spatial interpolation or normalization, and no plate-position corrections are inferred.

## Method references and limitations

- [FDA, *BAM Appendix 2*](https://www.fda.gov/food/laboratory-methods-food/bam-appendix-2-most-probable-number-serial-dilutions) explains MPN from serial-dilution positive/negative tube patterns.
- [Zwietering et al., *Modeling of the Bacterial Growth Curve* (1990)](https://doi.org/10.1128/aem.56.6.1875-1881.1990) defines the modified Gompertz parameters used here.
- [FDA, *BAM Chapter 3: Aerobic Plate Count (2025)*](https://www.fda.gov/media/191248/download) describes exposure-weighted estimates for exact plate counts.
- [Thermo Fisher Scientific — manual hemocytometer counts](https://www.thermofisher.com/us/en/home/references/gibco-cell-culture-basics/cell-culture-protocols/counting-cells-in-a-hemacytometer.html) explains the 0.1 µL square and 10⁴ multiplier.
- [UK Culture Collections — cell culture counting](https://www.culturecollections.org.uk/training-and-support/technical-support/cell-culture-protocols/) describes the dilution correction and live/(live+dead) viability.
- [Abcam — counting cells using a hemocytometer](https://www.abcam.com/en-us/technical-resources/protocols/counting-cells-using-a-haemocytometer) supplies the independent four-count, 1:5 dilution oracle used in the tests.
- [Eppendorf, *Start Separation at Ease*, §1.1](https://www.fishersci.pt/content/dam/fssite/eu/brands/e/eppendorf/28974_new_product_showcase_march_eppendorf/28974_Eppendorf_Essential_Tips_for_Good_Results.pdf) supplies the 1.118 × 10⁻⁵ rotor conversion and radius convention.

All outputs are deterministic computations from the supplied observations, with model-dependent statistical assumptions. The Workbench rejects invalid fields, nonfinite inputs/outputs, and oversized artifacts. Use measured viable counts, real vessel capacity, equipment guidance, and appropriate laboratory procedures when making practical decisions.
