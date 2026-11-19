# Independent scientific correctness audit — Biology Workbench

**Date:** 2026-11-19  
**Repository/base:** `dovvnloading/Graphlink`, `main` at `a9978058423749a72c912bc131f3c32c77cb547f` (before these changes).  
**Scope:** 37 operations in five Biology Workbench packs, including the enzyme-advanced and molecular plate-assay implementations: `enzyme_kinetics.py` + `enzyme_advanced.py`; `plate_assays.py` + `plate_assays_molecular.py`; `cell_culture.py`; `pharmacokinetics.py`; and `physiology.py`.

## Method and interpretation

This is a **code-level scientific correctness audit**, not a validation of any wet-lab protocol, animal-to-human dose, drug efficacy, electrophysiology recording, or clinical decision. I independently checked model equations and the relationship of fitted parameters to reported scientific quantities, units and signs, the input domain, loss of significance and special numerical cases, physical interpretation of results, and available test oracles. Analytic identities and independently synthesized curves were used where possible; the new tests avoid relying only on each pack's sample results.

**A finding of no identified issue does not mean proven correct for all measurements or model regimes.** The workbench does not propagate instrument error, batch effects, calibration uncertainty, pharmacokinetic population variation, or every model-identifiability failure. Model suitability and experimental controls remain the investigator's responsibility.

## Findings and remediation

| ID | Pack / severity | Reproducible pre-fix problem | Fix and independent regression |
| --- | --- | --- | --- |
| **B1** | Enzyme kinetics / numerical correctness | Morrison tight-binding quadratic formed `(1+x+k)^2-4x`. At `x=1` (inhibitor = active enzyme) and `k=Ki/[E]=10⁻¹⁶`, binary64 rounds the radicand to zero and the Ki Jacobian divides by zero, although the true discriminant is positive. | Compute the algebraically identical noncancelling radicand `(1-x)^2+k(2(1+x)+k)`. Test the equimolar limit against the analytic mass-balance root and finite derivative. |
| **B2** | Plate assays / scientific interpretation | 5PL's `c` was labeled a **midpoint** in the fitting implementation. In `y=d+(a-d)/(1+(x/c)^b)^g`, `c` is **not** the concentration at half the asymptote difference unless `g=1`. Users could mistake the scale for half-response potency. | Label `c` as a scale and expose a separate, unit-labeled `half_response_concentration=c(2^(1/g)-1)^(1/b)`, plus an in-range/extrapolated status. Check a manufactured asymmetric 5PL curve and symmetric 4PL limit. |
| **B3** | Cell culture / numerical correctness | The two-point growth estimator subtracted `log(N₂)-log(N₁)`, which can become exactly zero for *distinct finite* large abundances, erroneously rejecting valid increasing measurements. | Use `log1p((N₂-N₁)/N₁)` for near-equal abundances and separate logs for wide ratios; test adjacent representable positive floats. |
| **B4** | Pharmacokinetics / numerical correctness | Near-flat descending concentration intervals could produce `q=log(C₀)-log(C₁)=0` and a division-by-zero in the logarithmic trapezoid; a shallow terminal elimination slope could be lost when logarithms were nearly equal. | Stable `-log1p((C₁-C₀)/C₀)` for near-flat log-down AUC/AUMC, and log ratios relative to the first terminal concentration with recentered times for terminal λz regression. Test adjacent binary64 concentrations and independently manufactured slow exponential decay. |
| **B5** | Physiology / numerical correctness | Nernst and GHK differences of separately rounded logarithms could return exactly zero voltage for two distinct finite, near-equal concentrations; GHK should reduce to Nernst when one monovalent cation is permeable. | Evaluate near-unity logarithmic ratios with `log1p`, retaining separate logs for widely different inputs. Test exact Nernst expression and GHK-to-Nernst single-ion limit. |
| **B6** | Plate assays / input-domain hardening | The direct qPCR relative-expression function did not explicitly reject an empty reference-gene set; it failed downstream in a generic mean-of-empty-input error. | Require at least one reference gene inside the function, with a clear error, in addition to the workbench's vector-level validation. |

**Notes on severity:** B1, B3, B4 and B5 concern attainable floating-point inputs for which the original computation can report a wrong/undefined result; the extreme examples are *numerical stress tests*, not claims of physical measurement precision. B2 is a scientific label/interpretation error and adds explicit reporting without changing fitted curves or unknown-sample inversion. B6 improves a domain guard rather than changing numerical output on valid input.

**Compatibility:** Operation IDs, required fields, calibrated-unknown inverse rules, physical units and existing result keys are preserved. 4PL/5PL values gain the additive `c_unit`, `half_response_concentration`, `half_response_concentration_unit` and `half_response_status` keys. The half-response value is a **model descriptor**, not a calibrated unknown. It may lie outside the standards and is explicitly marked when it does; nonrepresentable values are reported as text, not nonfinite JSON.

## Coverage ledger — all 37 operations

“Reviewed; no specific defect reproduced” means no code change was justified, **not** full scientific validation.

| Pack | Operation | Independent model/quantity check | Outcome |
| --- | --- | --- | --- |
| Enzyme | `inhibition_model_fit` | `v=Vmax[S]/(αKm+α'[S])`; competitive/uncompetitive/pure noncompetitive/mixed constraints, units of Ki and Km, local SE and residuals | Reviewed; existing synthetic-mechanism oracles |
| Enzyme | `inhibition_model_compare` | Same-data Gaussian AICc including residual variance, Akaike weight normalization and meaning | Reviewed; existing synthetic-model oracles |
| Enzyme | `catalytic_constants` | `kcat=Vmax/[E]active`, efficiency M⁻¹s⁻¹ after rate/concentration conversion | Reviewed; existing SI conversion checks |
| Enzyme | `hill_fit` | `v=Vmax[S]^h/(K0.5^h+[S]^h)`; Hill coefficient is descriptive | Reviewed; existing manufactured saturation cases |
| Enzyme | `cheng_prusoff_ki` | Competitive rapid-equilibrium `Ki=IC50/(1+[S]/Km)` | Reviewed; independent identity in new suite |
| Enzyme | `binding_isotherm_fit` | One/two-site saturation, positive Kd, optional nonspecific component | Reviewed; existing manufactured curves |
| Enzyme | `morrison_tight_binding_fit` | Reversible one-site inhibitor/enzyme mass balance and positive discriminant | **B1 fixed** |
| Enzyme | `schild_analysis` | Free-slope log₁₀(DR−1) regression; pA₂ conditional on competitive equilibrium | Reviewed; existing fitted-slope oracles |
| Enzyme | `thermal_shift_tm` | Logistic transition midpoint, signal direction, temperature interval vs absolute units | Reviewed; existing increasing/decreasing cases |
| Plate | `plate_layout` | 96/384 well addresses, missing/null distinct from measured zero | Reviewed; existing map tests |
| Plate | `standard_curve` | Linear/quadratic and asymmetrical 4/5PL response; monotone in-range inversion; reciprocal dilution | **B2 fixed** |
| Plate | `assay_quality` | `Z'=1-3(σ+ + σ−)/Δμ`, sample SD, window and CV denominator conventions | Reviewed; independent zero-SD identity in new suite |
| Plate | `qpcr_standard_curve` | Ct vs log₁₀(starting amount); `E=10^(-1/slope)` per cycle | Reviewed; existing independent Ct slope oracles |
| Plate | `qpcr_relative_expression` | `2^(-ΔΔCt)` and Pfaffl geometric mean of matched reference ratios | **B6 hardened**; existing Pfaffl oracle |
| Plate | `digital_pcr` | Poisson occupancy `−ln(negative/total)`, dilution, Wilson occupancy interval | Reviewed; existing zero/saturated-boundary tests |
| Plate | `a260_quantification` | Beer–Lambert 1 cm concentration coefficients, blanks, A260/280/230, dilution | Reviewed; existing mass-conversion oracles |
| Culture | `cell_count_viability` | Equal-volume region counts, counting dilution, dye-exclusion fraction | Reviewed; independent viability in new suite |
| Culture | `seeding_plan` | Viable cells = concentration × suspension volume; bulk overage | Reviewed; existing mass balance |
| Culture | `serial_dilution_plan` | `C1V1=C2V2` at each step; retain volume for downstream aliquots | Reviewed; existing stepwise mass balance |
| Culture | `centrifuge_rcf_rpm` | `RCF=1.118×10⁻⁵·r(cm)·rpm²`, invert in rpm | Reviewed; existing bidirectional conversion |
| Culture | `plate_count_cfu` | Pooled CFU/total original-sample volume rather than mean of per-plate titers | Reviewed; existing dilution-volume oracle |
| Culture | `microplate_map` | 96/384 plate geometry and categorical/nonmeasured distinction | Reviewed; existing sparse plate tests |
| Culture | `most_probable_number` | Poisson presence/absence maximum-likelihood root, boundary all-positive/all-negative | Reviewed; existing independent likelihood oracle |
| Culture | `growth_curve_parameters` | Modified Gompertz fit in log abundance, lag tangent, max growth rate | Reviewed; existing manufactured Gompertz |
| Culture | `doubling_time` | `ln(2)·Δt/ln(N₂/N₁)`; near-equality precision | **B3 fixed** |
| PK | `noncompartmental_analysis` | Linear-up/log-down AUC/AUMC, terminal λz, CL, Vz, MRT and route-specific F | **B4 fixed** |
| PK | `one_compartment_fit` | IV bolus `C=A·exp(-kt)`, CL=k·V, covariance | Reviewed; existing independent exponential |
| PK | `two_compartment_fit` | Positive ordered biexponential, biexponential AUC, micro-rates and central volume | Reviewed; existing independent biexponential |
| PK | `multiple_dose_prediction` | Bolus superposition; `Css,avg=Dose/(V·k·τ)` and accumulation | Reviewed; independent average identity in new suite |
| PK | `human_equivalent_dose` | FDA reference `HED(mg/kg)=animal(mg/kg)·Km_animal/37`, not safe dose | Reviewed; existing FDA reference-factor tests |
| PK | `drug_combination_analysis` | Bliss `Ea+Eb−EaEb` and exclusive/nonexclusive Chou–Talalay CI | Reviewed; existing independent effect oracles |
| Physiology | `nernst_potential` | `E=RT/(zF)·ln(out/in)`, voltage inside minus outside | **B5 fixed** |
| Physiology | `ghk_potential` | Permeability-weighted monovalent ion ratio; Cl⁻ reversed; single-cation Nernst limit | **B5 fixed** |
| Physiology | `membrane_properties` | Specific resistance/capacitance to whole-cell RC, time constant, current step | Reviewed; existing passive response oracle |
| Physiology | `hodgkin_huxley` | Original 1952 squid-axon gate rates, RK4, µA/cm² current, reversible-rate singularities | Reviewed; existing spike and refinement checks |
| Physiology | `spike_detection` | Interpolated rising threshold, refractory-onset logic, sampled peaks | Reviewed; existing crossing/peak tests |
| Physiology | `fick_diffusion` | Steady `J=−D·ΔC/L`, net mol `J·A·t`, sign | Reviewed; independent mass-transfer identity in new suite |

## Testing and reproducibility

New tests: `backend/tests/test_scientific_biology_independent_audit.py`. Includes deliberately adversarial float values, an independent stoichiometric Morrison root, an independently synthesized asymmetric 5PL curve, and ordinary equation identities spanning all five packs. Baseline pack tests:

- `backend/tests/test_scientific_enzyme_kinetics.py`
- `backend/tests/test_scientific_enzyme_advanced.py`
- `backend/tests/test_scientific_plate_assays.py`
- `backend/tests/test_scientific_plate_assays_molecular.py`
- `backend/tests/test_scientific_cell_culture.py`
- `backend/tests/test_scientific_pharmacokinetics.py`
- `backend/tests/test_scientific_physiology.py`

Run from the repository root:

```bash
python -m pytest -q backend/tests/test_scientific_biology_independent_audit.py \
  backend/tests/test_scientific_enzyme_kinetics.py \
  backend/tests/test_scientific_enzyme_advanced.py \
  backend/tests/test_scientific_plate_assays.py \
  backend/tests/test_scientific_plate_assays_molecular.py \
  backend/tests/test_scientific_cell_culture.py \
  backend/tests/test_scientific_pharmacokinetics.py \
  backend/tests/test_scientific_physiology.py
```

The tests check *software arithmetic* and output contracts, not analytic accuracy of laboratory instruments. Verify CI status separately on the PR; a checked-in test is not evidence of a passing test run.

## Limitations and remaining scientific risks

- **Identifiability and model suitability:** AICc inhibition ranking, one-vs-two-compartment PK, Morrison Ki_app, apparent Kd, GHK assumptions, thermal-shift transitions and culture lag extrapolation depend on the experimental design. Local linearized SEs can substantially understate uncertainty in poorly identified fits.
- **Plate calibration and qPCR:** no blank-subtraction, heteroscedastic weighted 4/5PL, batch effects, limits of detection/quantitation, replicate uncertainty or biological reference-gene stability assessment is inferred. A numeric half-response outside calibration is an extrapolation, not validated potency.
- **Culture:** dye exclusion ≠ long-term viability; CFU ≠ single living-cell count; MPN has a Poisson occupancy model but no confidence interval here; theoretical seeding does not prescribe sterile handling.
- **PK and human dose:** NCA assumes a correctly selected terminal phase and linear time-invariant exposure. IV bolus Vss is not inferred from extravascular MRT. FDA Km BSA equivalence is not a clinically safe starting dose. Bliss/Chou–Talalay effects are descriptive and have no replicate uncertainty or mechanistic proof.
- **Physiology:** Nernst uses concentration in place of activity; GHK assumes monovalent passive independent ion fluxes; HH simulates the reference squid axon at 6.3 °C and is not patient-specific. Nearly adjacent floats are stress inputs, not plausible assay resolution.
- **Out-of-scope checks:** No laboratory data, external instrument hardware, independent expert review, or full exhaustive numerical parameter search was available. This audit did not change other Biology Workbench packs.

## Independent references

1. Cornish-Bowden, *Fundamentals of Enzyme Kinetics*, 4th ed. (2012), reversible inhibition rate laws. [Publisher](https://www.wiley.com/en-us/Fundamentals+of+Enzyme+Kinetics%2C+4th+Edition-p-9783527330744).
2. Morrison (1969), quadratic treatment of tight-binding inhibitors. [DOI](https://doi.org/10.1016/0005-2744(69)90420-3).
3. GraphPad, *Asymmetrical five-parameter dose-response curves* (5PL asymmetry and midpoint interpretation). [Guide](https://www.graphpad.com/support/faq/asymmetrical-five-parameter-logistic-dose-responsecurves/). Parameterization conventions differ across software: the formula here is derived from **this repository's** explicitly stated 5PL equation.
4. Bustin et al. (2009), *The MIQE Guidelines*, *Clinical Chemistry* 55:611–622, qPCR reference/assay reporting. [DOI](https://doi.org/10.1373/clinchem.2008.112797). Pfaffl (2001), real-time RT-PCR relative quantification. [DOI](https://doi.org/10.1093/nar/29.9.e45).
5. pkpdutils NCA documentation: linear-up/log-down integration, terminal elimination and extrapolation. [Reference](https://matthiaskoenig.github.io/pkpdutils/nca/).
6. FDA (2005), *Estimating the Maximum Safe Starting Dose in Initial Clinical Trials for Therapeutics in Adult Healthy Volunteers*, Table 3 species Km values; the guidance does **not** equate HED to a safe starting dose. [FDA](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/estimating-maximum-safe-starting-dose-initial-clinical-trials-therapeutics-adult-healthy-volunteers).
7. NIST CODATA, gas constant and Faraday constant for electrochemical potentials. [Constants](https://physics.nist.gov/cuu/Constants/). Hodgkin & Huxley (1952), original membrane model. [Original paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC1392413/).

See also the detailed [Enzyme](enzyme_kinetics.md), [Plate](plate_assays.md), [Culture](cell_culture.md), [PK](pharmacokinetics.md) and [Physiology](physiology.md) pack references for assumptions and existing numeric examples.
