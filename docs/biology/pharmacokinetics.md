# Biology Workbench — Pharmacokinetics

**Pack:** pharmacokinetics · **Plugin:** science_pharmacokinetics · **Family:** Biology Workbench

Enable **Pharmacokinetics** in Settings → Plugins, then open Plugins → Biology Workbench. This is a standalone, explicitly grant-gated pack using Graphlink's existing reproducible result receipts, numeric input validation, plots, and tables. No NumPy, SciPy, or network requests are required, and the legacy Biology and Ecology pack is unchanged.

## Operations

| Operation ID | Method | Results |
| --- | --- | --- |
| noncompartmental_analysis | Observed IV-bolus or extravascular concentration/time profile; linear-up/log-down integration and selected terminal regression | Cmax, Tmax, AUC0-last/AUC infinity, AUMC, terminal lambda-z and half-life, MRT; IV CL/Vz/Vss, or apparent extravascular CL/F and Vz/F |
| one_compartment_fit | Unweighted nonlinear IV-bolus C(t) = A exp(-k t) | A, k, local SEs, AUC, terminal half-life, CL, Vc/Vz/Vss, MRT, fit diagnostics and residuals |
| two_compartment_fit | Unweighted nonlinear IV-bolus C(t) = A exp(-alpha t) + B exp(-beta t), alpha > beta | A, B, alpha, beta, local SEs, k10/k12/k21, AUC, half-life, CL, Vc/Vz/Vss, MRT, fit diagnostics and residuals |

## Inputs, units, and scope

Each operation takes aligned **time** and **concentration** vectors, **dose**, **time_unit**, and **response_unit**. Times must start at zero and increase strictly; single-dose concentrations must be nonnegative, and positive throughout for model fits. Dose defaults to mg but accepts explicitly unit-tagged masses such as "100000 ug". Concentrations must be convertible to mg/L, e.g. mg/L, ug/mL or ng/mL. Time units must be convertible to hours, e.g. h or min. Each numeric entry can carry its own convertible unit. Molar concentration without molecular weight is rejected rather than being treated as mass per volume.

AUC has units concentration × time; AUMC has units concentration × time squared. Clearance is expressed in L per selected time unit; volumes are in L. Results include a labeled plot and observation table. Compartment fits also return fitted concentration and signed residual for every observation.

These methods estimate **single-dose pharmacokinetics**, not a dose recommendation or a clinical therapeutic target. A high R-squared or converged solver does not establish that the pharmacokinetic assumptions hold.

## Noncompartmental analysis

The additional inputs are route (iv_bolus or extravascular) and terminal_points (at least three final samples). The chosen terminal samples must be positive and occur *after* the observed peak. The last selected log concentrations are linearly regressed on measured time; minus their slope is lambda-z. A non-negative terminal slope, an invalid time sequence, or zero/BLQ terminal measurements cause an error. Select the terminal segment deliberately; no automated terminal-window selection or BLQ imputation is implemented.

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

## Sampling and model limitations

NCA requires at least five observations; one-compartment fitting requires at least six strictly positive observations and two-compartment fitting at least eight. Both compartment models assume instantaneous single IV-bolus delivery, central-compartment sampling, and first-order, time-invariant exchange and elimination. They do not model oral absorption, IV infusion, repeated doses, saturable clearance, time-dependent kinetics or random effects across subjects. Absence of early distribution points or sufficient late observations can make a two-compartment solution unidentifiable even with a low residual.

Regression coverage in backend/tests/test_scientific_pharmacokinetics.py checks synthetic analytic profiles, unit conversions, mass balance, model parameter recovery, invalid data and result receipt validity. Synthetic correctness is **not** independent clinical validation.

## References

- [pkpdutils noncompartmental analysis](https://matthiaskoenig.github.io/pkpdutils/nca/) — trapezoid integration, terminal regression, AUMC and extrapolation.
- [ASHP Concepts in Clinical Pharmacokinetics: Two-Compartment Models](https://ashp.mhmedical.com/content.aspx?bookid=3751&sectionid=311145061) — IV bolus macro- and micro-rate conventions.
- [Certara Phoenix WinNonlin User's Guide](https://onlinehelp.certara.com/phoenix/8.3/responsive_html5_%21MasterPage%21/WinNonlin%20User%27s%20Guide.pdf) — exponential compartment parameterization and NCA conventions.
