# Biology Workbench — Enzyme Kinetics

**Plugin ID:** `science_enzyme_kinetics`. This independent, consent-gated Biology Workbench pack exposes five operations without altering the legacy `science_biology` operation identifiers, result formats, or plugin grants. It uses Graphlink's shared scalar/vector validation, result receipts, and existing fitting linear algebra; NumPy, SciPy, and network calls are **not** required. Enable **Enzyme Kinetics** in Settings → Plugins to access its operations from **Biology Workbench**.

## Operations and numerical conventions

| Operation ID | Inputs | Primary outputs | Computation |
| --- | --- | --- | --- |
| `inhibition_model_fit` | Matched `[S]`, `[I]`, initial-rate vectors; independent substrate, inhibitor and rate units; inhibition model choice | `vmax`, `km`, `ki` and/or `ki_prime` with local SEs; residuals, R², AIC/AICc/BIC | Unweighted joint nonlinear least-squares fit to one of four reversible inhibition rate laws |
| `inhibition_model_compare` | The same matched vectors and units | Four refitted candidate models, ranked AICc, ΔAICc, Akaike weights, SSE | Compare **identical observations** with a common Gaussian residual model; do not compare precomputed mismatched scores |
| `catalytic_constants` | `vmax`, `km`, active-site concentration `[E]`, concentration/rate/time units | `kcat` in selected and SI time units; `kcat/Km` in selected and M⁻¹ s⁻¹ units | Unit-checked ratios, including explicit min-to-second and micromolar-to-molar conversions |
| `hill_fit` | Positive `[S]` vector, nonnegative initial-rate vector; substrate/rate units | `vmax`, `k_half`, `hill_coefficient` with local SEs, residual table and plot | Three-parameter Hill saturation, multi-start bounded nonlinear least squares |
| `cheng_prusoff_ki` | `ic50` (positive), assay `[S]` (nonnegative), `km` (positive), common concentration unit | `ki`, correction factor and input audit values | Classical competitive-equilibrium Cheng–Prusoff identity |

Each operation declares **Methods and citations** in its Workbench card. Inputs are paired by index; replicate observations stay separate. Concentrations may use different compatible SI units by including the unit in a numeric input, e.g. `"1 mM"` when the selected default unit is `uM`. `[S]` and `[I]` can also use separate default units in inhibition fits. Only `catalytic_constants` performs physical rate/concentration/time conversion; the fitting operations consistently label their own units without silently converting arbitrary rate scales. All scalar results use companion `*_unit` keys, plots have labeled axes, and fits return observed/fitted/residual tables.

### Reversible inhibition

The common mixed-inhibition rate equation is

\[
v = \frac{V_{\max}[S]}{\alpha K_m + \alpha'[S]},\qquad
\alpha=1+\frac{[I]}{K_i},\quad \alpha'=1+\frac{[I]}{K_i'}.
\]

- **Competitive:** `alpha_prime = 1`; estimate `Vmax`, `Km`, `Ki` (inhibitor binding to free enzyme).
- **Uncompetitive:** `alpha = 1`; estimate `Vmax`, `Km`, `Ki_prime` (binding to ES).
- **Pure noncompetitive:** `alpha = alpha_prime`, equivalently `Ki = Ki_prime`; estimate one shared inhibition constant and report both aliases.
- **Mixed:** estimate distinct `Ki` and `Ki_prime` in addition to `Vmax` and `Km`.

Each model fits rates **directly**, not using Lineweaver–Burk reciprocal transformation. It optimizes the logarithms of positive parameters using multistart damped Levenberg–Marquardt and analytic derivatives. Inputs are internally scaled to stable substrate, inhibitor and rate ranges; recovered parameters carry the user-selected units. The fit requires at least 12 observations, at least four positive substrate levels, at least three inhibitor levels including zero, and at least three substrate levels in each of the inhibitor-free and inhibitor-treated groups. Zero-substrate controls are accepted **only with a zero background-corrected rate**. No fitted baseline is estimated; subtract appropriate assay blanks before analysis. The workbench is intended for initial rates, constant active enzyme, reversible inhibition and an appropriate Michaelis–Menten regime. Product inhibition, time-dependent inhibition, substrate depletion, cooperativity, noncompetitive non-equilibrium effects, and tight-binding require other models.

The local linearized covariance is

\[
\operatorname{Cov}(\widehat{\log\theta})\approx\frac{\mathrm{SSE}}{n-p}(J^\top J)^{-1},
\qquad \mathrm{SE}(\hat\theta_j)\approx \hat\theta_j\sqrt{\operatorname{Cov}(\widehat{\log\theta})_{jj}}.
\]

`J` is the response Jacobian with respect to **log parameters**. SEs are local (not bootstrap or biological-replicate error), and can be unavailable for rank-deficient data. Bounded or highly correlated parameters are not evidence of precise Ki. The comparison treats all measurements as independent with a **common unweighted Gaussian rate-error variance**, and counts that estimated variance as an additional parameter `k=p+1`:

\[
\mathrm{AIC}=n[\log(2\pi)+1+\log(\mathrm{SSE}/n)]+2k,
\quad \mathrm{AICc}=\mathrm{AIC}+\frac{2k(k+1)}{n-k-1},\quad
\mathrm{BIC}=n[\log(2\pi)+1+\log(\mathrm{SSE}/n)]+k\log n.
\]

For AIC, the actual-rate-unit SSE is used (not the internal normalized SSE). Only **differences** in the scores are meaningful when units are changed consistently. `ΔAICc = AICc − min(AICc)` and normalized weights are `exp(-ΔAICc/2)/sum(exp(-ΔAICc/2))`. Exact-zero residual variance and datasets with `n <= k+1` cannot yield a finite AICc. Failed or undefined candidate fits are shown as unranked; comparisons require at least two ranked models. AICc ranks empirical fit/parsimony under the stated assumptions; it does **not** establish a molecular mechanism or constitute a formal hypothesis test.

### Catalytic constants

\[
k_{cat}=V_{\max}/[E]_{\rm active},\qquad
\mathrm{catalytic\ efficiency}=k_{cat}/K_m.
\]

Example: `vmax=120 uM/min`, `[E]active=0.1 uM`, `km=20 uM` gives `kcat=1200 min^-1 = 20 s^-1` and `kcat/Km=60 uM^-1 min^-1 = 1,000,000 M^-1 s^-1`. The calculator requires rate dimensions of concentration/time and molar concentration units; it raises an error instead of silently dividing incompatible quantities. `[E]active` must be **the total concentration of catalytically active sites**; using total protein mass, inactive enzyme, or an unknown number of active sites changes the meaning of `kcat`. `Km` is not automatically a dissociation constant.

### Hill fit

\[
v=\frac{V_{\max}[S]^h}{K_{0.5}^h+[S]^h}
=\frac{V_{\max}}{1+\exp[h(\ln K_{0.5}-\ln[S])]}.
\]

Use at least seven observations at six distinct **strictly positive** substrate concentrations; zero-dose controls should be omitted because the log fit is defined only for positive substrate. The fit constrains `0.1 <= h <= 8` and `Vmax > 0`, and reports `K0.5` in substrate units, `h` as dimensionless, and approximate SE of `K0.5` by the delta method from `SE(logK0.5)`. The Hill coefficient is descriptive of curve steepness: `h>1` alone does not prove cooperative binding or count active sites. This is a **saturation** fit, not the four-parameter agonist/antagonist dose-response fit in `science_biology`.

### Cheng–Prusoff conversion

\[
K_i = \frac{IC_{50}}{1+[S]/K_m}.
\]

Example: `IC50=15 uM`, `[S]=8 uM`, `Km=4 uM` yields `Ki=5 uM`. The classic correction assumes **reversible, equilibrium, competitive inhibition** of a Michaelis–Menten reaction at a fixed substrate concentration; it must not be applied indiscriminately to Hill-fit IC50s, irreversible/time-dependent or mixed/noncompetitive inhibition, tight-binding relative to enzyme abundance, or substrate depletion. All three inputs are converted to the same declared concentration unit before calculation.

## Numerical validation and references

The regression tests in `backend/tests/test_scientific_enzyme_kinetics.py` exercise synthetic (independently evaluated) competitive, uncompetitive, pure noncompetitive and mixed rate surfaces, selection by AICc on reproducible small-noise data, turnover/efficiency unit conversions, Hill parameter recovery, Cheng–Prusoff identities, invalid experimental designs and saved-result integrity. Synthetic truths are numerical method checks, **not** independent biological validation.

- Cornish-Bowden, *Fundamentals of Enzyme Kinetics*, 4th edition (2012), reversible inhibition rate laws: https://www.wiley.com/en-us/Fundamentals+of+Enzyme+Kinetics%2C+4th+Edition-p-9783527330744
- Hill (1910), *The possible effects of aggregation of the molecules of haemoglobin*: https://doi.org/10.1113/jphysiol.1910.sp001386
- Cheng and Prusoff (1973), *Relationship between Ki and IC50*: https://doi.org/10.1016/0006-2952(73)90196-2
- IUPAC Gold Book, catalytic constant / turnover number: https://goldbook.iupac.org/terms/view/C00846
- Burnham and Anderson, *Model Selection and Multimodel Inference*, 2nd edition (2002), AICc and Akaike weights.
