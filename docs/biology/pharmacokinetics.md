# Biology Workbench — Pharmacokinetics

**Pack:** pharmacokinetics · **Plugin:** science_pharmacokinetics · **Family:** Biology Workbench

Enable **Pharmacokinetics** in Settings → Plugins, then open Plugins → Biology Workbench. This is a standalone, explicitly grant-gated pack using Graphlink's existing reproducible result receipts, numeric input validation, plots, and tables. No NumPy, SciPy, or network requests are required, and the legacy Biology and Ecology pack is unchanged.

## Operations

| Operation ID | Method | Results |
| --- | --- | --- |
| noncompartmental_analysis | Observed IV-bolus or extravascular concentration/time profile; linear-up/log-down integration and selected terminal regression | Cmax, Tmax, AUC0-last/AUC infinity, AUMC, terminal lambda-z and half-life, MRT; IV CL/Vz/Vss, or apparent extravascular CL/F and Vz/F |
| one_compartment_fit | Unweighted nonlinear IV-bolus C(t) = A exp(-k t) | A, k, local SEs, AUC, terminal half-life, CL, Vc/Vz/Vss, MRT, fit diagnostics and residuals |
| two_compartment_fit | Unweighted nonlinear IV-bolus C(t) = A exp(-alpha t) + B exp(-beta t), alpha > beta | A, B, alpha, beta, local SEs, k10/k12/k21, AUC, half-life, CL, Vc/Vz/Vss, MRT, fit diagnostics and residuals |
| multiple_dose_prediction | One-compartment equal IV boluses with first-order elimination | Accumulation after n doses, predicted peak/trough each interval, steady-state peak/trough/mean, 95%-envelope time and concentration-time profile |
| human_equivalent_dose | FDA (2005) animal-to-adult-human body-surface-area Km factors | Animal-to-human equivalent mg/kg and illustrative total mg at adult body weight, **not** a starting-dose recommendation |
| drug_combination_analysis | Bliss independence and Chou–Talalay CI from independently fitted single-agent median-effect curves | Single-agent effects, Bliss expectation/excess, matched-effect single-agent doses, exclusive/nonexclusive CI and descriptive comparisons |

## Inputs, units, and scope

**The three single-dose operations** take aligned **time** and **concentration** vectors, **dose**, **time_unit**, and **response_unit**. Times must start at zero and increase strictly; single-dose concentrations must be nonnegative, and positive throughout for model fits. Dose defaults to mg but accepts explicitly unit-tagged masses such as "100000 ug". Concentrations must be convertible to mg/L, e.g. mg/L, ug/mL or ng/mL. Time units must be convertible to hours, e.g. h or min. Each numeric entry can carry its own convertible unit. Molar concentration without molecular weight is rejected rather than being treated as mass per volume.

AUC has units concentration × time; AUMC has units concentration × time squared. Clearance is expressed in L per selected time unit; volumes are in L. Results include a labeled plot and observation table. Compartment fits also return fitted concentration and signed residual for every observation.

These are research calculations, **not dose recommendations, safety assessments, or clinical therapeutic targets**. A high R-squared or converged solver does not establish that the pharmacokinetic assumptions hold.

## Noncompartmental analysis

The additional inputs are route (iv_bolus or extravascular) and terminal_points (at least three final samples). The chosen terminal samples must be positive and occur *after* the observed peak. The last selected log concentrations are linearly regressed on measured time; minus their slope is lambda-z. For nearly equal concentrations, log ratios are computed with `log1p` relative to the first terminal concentration, and terminal times are recentered before regression; near-flat log-down trapezoids use the same stable log-ratio strategy. These preserve representable shallow decays without changing the linear-up/log-down NCA model. A non-negative terminal slope, an invalid time sequence, or zero/BLQ terminal measurements cause an error. Select the terminal segment deliberately; no automated terminal-window selection or BLQ imputation is implemented.

Cmax is the **largest observed concentration**; Tmax is the **first observed time** at that value. Logarithmic integration is used between two strictly positive declining observations, with linear interpolation for rising, equal, or zero-ended intervals. AUC and AUMC use consistent integrations; AUMC includes the exact first moment of each log-down interval. The extrapolation beyond the last time uses the **observed** final concentration:

- AUCinf = AUClast + Clast / lambda-z
- AUMCinf = AUMClast + tlast * Clast / lambda-z + Clast / lambda-z²
- terminal half-life = ln(2) / lambda-z
- MRT = AUMCinf / AUCinf

For IV bolus, CL = dose / AUCinf, Vz = CL / lambda-z, and Vss = CL × MRT. For extravascular administration with unknown bioavailability F, only apparent CL/F and Vz/F are returned, because MRT can include absorption time and cannot be used to derive IV Vss. The reported AUC percentage extrapolated and terminal log-linear R-squared help diagnose the suitability of the terminal window; extrapolation above 20% and R-squared below 0.90 emit warnings.

**Numerical oracle:** With C(t) = 12 exp(-ln(2)t/4) mg/L and a 120 mg IV bolus, lambda-z = 0.1732868 h^-1, half-life = 4 h, AUCinf = 69.24936 mg·h/L, MRT = 5.77078 h, CL = 1.732868 L/h, and Vz = Vss = 10 L. Exact log-down segments and extrapolation reconstruct these analytic values.

## One- and two-compartment IV bolus models

The optimization minimizes **unweighted squared errors in concentration units** (not logarithmic errors). Parameters are constrained positive by logarithmic optimization coordinates. The two-compartment parameterization alpha = beta + delta, delta > 0, enforces ordering and prevents exchanging the labels of fast and slow phases. Fits use Graphlink's existing deterministic multistart damped least-squares solver with normalized time and concentration. Local Jacobian-based standard errors are reported when covariance is nonsingular.

For one compartment:

- C(t) = A exp(-kt), A,k > 0
- AUCinf = A/k; MRT = 1/k; half-life = ln(2)/k
- Vc = Vz = Vss = dose/A; CL = k * Vc

For two compartments:

- C(t) = A exp(-alpha t) + B exp(-beta t), A,B > 0 and alpha > beta > 0
- AUCinf = A/alpha + B/beta
- MRT = (A/alpha² + B/beta²) / AUCinf
- Vc = dose/(A+B); CL = dose/AUCinf
- k21 = (A*beta + B*alpha)/(A+B)
- k10 = alpha*beta/k21; k12 = alpha + beta - k21 - k10
- Vz = CL/beta; Vss = CL*MRT; terminal half-life = ln(2)/beta

**Numerical oracle:** A=8 mg/L, B=2 mg/L, alpha=1 h^-1, beta=0.12 h^-1 and dose=100 mg imply AUCinf = 24.666667 mg·h/L, CL = 4.054054 L/h, Vc = 10 L, k21 = 0.296 h^-1, k10 = 0.405405 h^-1, k12 = 0.418595 h^-1, and terminal half-life ≈ 5.776227 h.

Diagnostics include observed/fitted/residual rows, residual sum of squares, residual standard deviation, R-squared, AIC, AICc and BIC. Information criteria count the estimated Gaussian noise variance as an additional parameter, with undefined criteria for effectively exact fits. Small amplitudes and overlapping phases produce identifiability warnings; local SEs are *not* confidence intervals or evidence of biological compartment identity.

## Repeated-dose IV bolus prediction

**Operation:** `multiple_dose_prediction`. Inputs are a positive **dose per IV bolus** (mg), **central volume** (L), **first-order elimination rate** (1/h), **dosing interval** (h) and **number of doses** (1–200). The numeric fields accept compatible explicit unit suffixes such as "100000 ug", "10000 mL", "0.2 1/min", or "60 min". All reported concentrations are mg/L and times hours.

The operation assumes identical instantaneous IV boluses, linear and time-invariant one-compartment kinetics, complete IV bioavailability, and no loading dose. It superposes residual drug after each dose, **not** extrapolating a terminal NCA slope to models with absorption or multiple disposition phases. For the n-th dose and interval τ, k > 0, A = dose/V, and q = exp(−kτ):

- **Accumulation factor after n doses:** Rn = (1 − qⁿ)/(1 − q).
- **Postdose peak:** Cmax,n = A × Rn; **end-interval trough:** Cmin,n = Cmax,n × q.
- **Steady-state accumulation factor:** Rss = 1/(1 − q); Css,max = A × Rss; Css,min = Css,max × q.
- **Time-averaged steady-state concentration:** Css,avg = A/(kτ) = dose/(CL×τ).
- **Fraction of the steady-state peak attained by dose n:** 1 − qⁿ. The continuous 95% accumulation **envelope** occurs at ln(20)/k hours; this is not necessarily an administration time.

`expm1` is used to preserve precision when kτ is small. The output contains a per-dose predose/postdose/trough table and a concentration-time plot with duplicate timestamps showing bolus discontinuities. Large, ill-conditioned numeric inputs and more than 200 doses are rejected.

**Numerical oracle:** 100 mg IV boluses, V=10 L, k=ln(2) h⁻¹, τ=1 h and n=5 yield R5=1.9375, postdose peak 19.375 mg/L, trough 9.6875 mg/L, steady-state accumulation 2, Css,max 20 mg/L and Css,min 10 mg/L. This operation does **not** model missed doses, variable intervals, infusions, oral absorption, nonlinear clearance, or clinical target concentrations.

## Human-equivalent dose by body surface area

**Operation:** `human_equivalent_dose`. Enter the animal species, its administered dose (mg/kg), and an illustrative adult human body weight (kg). The calculation follows **FDA 2005 guidance, Table 3**, using the reference 60-kg adult-human Km of 37:

HED (mg/kg) = animal dose (mg/kg) × animal Km / 37

| Species | FDA reference Km |
| --- | ---: |
| Mouse | 3 |
| Hamster | 5 |
| Rat | 6 |
| Guinea pig | 8 |
| Rabbit | 12 |
| Dog | 20 |
| Monkey (reference primates) | 12 |
| Mini-pig | 35 |
| Adult human (reference 60 kg) | 37 |

**Numerical oracle:** Mouse 100 mg/kg scales to HED = 100 × 3/37 = **8.108108 mg/kg**. At the reference 60 kg, the arithmetic total is 486.4865 mg. The dose axis plot shows this linear conversion.

These are **reference species factors** based on typical animal weights. Actual species/strain and animal weight, pharmacokinetics, pharmacodynamics, route, formulation, metabolism, toxicity, and organ-specific sensitivity can change the appropriate scaling method. Using a non-60-kg adult weight for an illustrative total does **not** recompute the fixed human Km or individualized body surface area, and emits a warning. **An HED is not a maximum recommended starting dose**: the calculation does not choose a relevant species or NOAEL, apply a safety factor, or validate human exposure. It must not be used for human dosing decisions.

## Drug combinations: Bliss and Chou–Talalay

**Operation:** `drug_combination_analysis`. Enter the **tested concentration of each agent** (A and B), its independently fitted single-agent **median-effect dose** Dm and positive **slope** m, the **observed combination affected fraction** fa, and whether the Chou–Talalay interaction assumption is **exclusive** or **nonexclusive**. Use a common concentration unit such as mg/L, ug/mL, or uM; concentration inputs are converted consistently, but molar and mass concentration cannot be interconverted without each compound's molecular weight. Inputs require **0 < fa < 1** for a finite equieffective single-agent concentration and positive doses, Dm values, and slopes.

The operation evaluates each single-agent model at its *combination concentration*:

- Median-effect relationship: fa/(1−fa) = (D/Dm)ᵐ, with an increasing effect as dose increases.
- Single-agent effects E_A and E_B imply **Bliss-independent expected** combined effect E_Bliss = E_A + E_B − E_AE_B.
- **Bliss excess** = observed combined effect − E_Bliss; positive means above this reference model, negative below.
- The single-agent concentration required to achieve the **observed combined** effect fa is Dx,j = Dm,j × (fa/(1−fa))^(1/m_j).
- **Chou–Talalay CI (mutually exclusive assumption):** CIex = D_A/Dx,A + D_B/Dx,B.
- **Chou–Talalay CI (nonexclusive assumption):** CInonex = CIex + (D_A/Dx,A)(D_B/Dx,B). The selected model is reported as CI; both variants remain in the values.

A CI below 1 is below the selected additivity reference, and a CI above 1 exceeds it; **Bliss and CI have different reference assumptions**, so their directions need not agree. The output includes the expected-response profile along the fixed-ratio dose ray from zero to the tested pair, not a measured combination-response curve. No new single-agent dose-response fitting, confidence intervals, replicate variance estimation, or formal significance test occurs inside this operation. Dm and m **must be independently estimated from quality-controlled single-agent assays with the same biological endpoint and exposure time**; a single measured fraction is insufficient to infer them.

**Numerical oracle:** For A at 1 mg/L, Dm,A=2 mg/L, m_A=1; B at 2 mg/L, Dm,B=4 mg/L, m_B=1; and observed fa=0.75, both single-agent effects at the tested doses are 1/3. Bliss expectation is 5/9, excess is 0.194444; Dx,A=6 and Dx,B=12 mg/L, CIex=1/3, and CInonex=13/36. These are **descriptive comparisons**, not clinical efficacy claims or statistical evidence of interaction.

## Sampling and model limitations

NCA requires at least five observations; one-compartment fitting requires at least six strictly positive observations and two-compartment fitting at least eight. Both compartment models assume instantaneous single IV-bolus delivery, central-compartment sampling, and first-order, time-invariant exchange and elimination. These three **single-dose** operations do not model oral absorption, IV infusion, repeated doses, saturable clearance, time-dependent kinetics or random effects across subjects. Absence of early distribution points or sufficient late observations can make a two-compartment solution unidentifiable even with a low residual.

Regression coverage in backend/tests/test_scientific_pharmacokinetics.py checks synthetic analytic profiles, unit conversions, mass balance, model parameter recovery, repeated-dose accumulation and steady state, FDA species factors, Bliss/CI oracles, invalid data and result receipt validity. Synthetic correctness is **not** independent clinical validation.

## References

- [pkpdutils noncompartmental analysis](https://matthiaskoenig.github.io/pkpdutils/nca/) — trapezoid integration, terminal regression, AUMC and extrapolation.
- [ASHP Concepts in Clinical Pharmacokinetics: Two-Compartment Models](https://ashp.mhmedical.com/content.aspx?bookid=3751&sectionid=311145061) — IV bolus macro- and micro-rate conventions.
- [McGraw Hill: multiple-dose pharmacokinetic models](https://accesspharmacy.mhmedical.com/content.aspx?bookid=2577&sectionid=234135883) — accumulation and steady-state IV bolus equations.
- [FDA 2005 HED guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/estimating-maximum-safe-starting-dose-initial-clinical-trials-therapeutics-adult-healthy-volunteers) — adult and species Km factors and limits of HED translation.
- [Chou (2006), Pharmacological Reviews](https://pubmed.ncbi.nlm.nih.gov/16968952/) — median-effect and Chou–Talalay CI.
- [Review of drug-combination reference models](https://pmc.ncbi.nlm.nih.gov/articles/PMC9127325/) — Bliss independence and model interpretation.
- [Certara Phoenix WinNonlin User's Guide](https://onlinehelp.certara.com/phoenix/8.3/responsive_html5_%21MasterPage%21/WinNonlin%20User%27s%20Guide.pdf) — exponential compartment parameterization and NCA conventions.
