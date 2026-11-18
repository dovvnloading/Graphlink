# Scientific Fitting and Inference: quantitative reference

The **Fitting and Inference** pack is a standalone Scientific Workbench plugin (`science_fitting`). Enable it in **Settings → Plugins**, then choose **Plugins → Scientific Workbench → Fitting and Inference**. It uses only Python's standard library, runs locally, requires `graph.read` and `graph.mutate` grants, and uses the existing version **1.1.0** Scientific Workbench result and receipt protocol. The operation catalog and worked examples are available from the enabled plugin. The host validates exact field names, finite measurements, numeric dimensions and immutable source citations before calculation; failed runs do not overwrite the last successful result.

All measurements use the caller's consistent units. Quantitative answers are conditional on the stated statistical model and data collection assumptions. An exact p-value or numerical confidence interval is **not** evidence that those assumptions hold.

## Weighted linear least squares (`weighted_linear_least_squares`)

Inputs: `predictors` is an *n × k* rectangular matrix (one observation per row), `response` contains *n* values, and `weights` contains *n* nonnegative **relative inverse-variance weights**. A constant intercept is added implicitly: the coefficient vector has *p = k + 1* elements, indexed `0` through `k`. Up to 15 supplied predictors are accepted. The design matrix must have full column rank and more positive-weight rows than parameters; zero-weight rows do not contribute to the fit or its residual degrees of freedom. For unweighted least squares, use all weights equal to 1.

With *W = diag(w)*, solve

\[\hat\beta = (X^\mathsf{T} W X)^{-1} X^\mathsf{T}Wy,\quad r=y-X\hat\beta,\quad S=\sum_i w_i r_i^2.\]

The implementation evaluates and equilibrates the normal equations in **76-digit decimal arithmetic**, followed by pivoted elimination. That avoids the ordinary binary64 loss of precision from squaring the condition number for the tested inputs, but does **not** make a nearly unidentifiable design statistically meaningful. Rank-deficient and zero-information designs are rejected; there is no coefficient regularization or automatic predictor selection. Inputs are decimalized from the normalized, finite numbers in the shared workbench validator. Very extreme scales may still exceed finite output range.

Let *n₊* denote rows with positive weights, *ν = n₊ − p*, and *s² = S/ν*. The estimated **coefficient covariance matrix** is *s²(XᵀWX)⁻¹*; `standard_error_j = sqrt(covariance[j,j])`. Thus a common scale factor applied to all weights does not change estimates or their standard errors. This assumes the weight ratios are proportional to inverse observation variances and estimates the unknown common noise scale from residuals. These are **not** known-variance standard errors. `r_squared = 1 − S/Σwᵢ(yᵢ−ȳw)²` uses a weighted, centered total sum of squares and is omitted if the response is constant (undefined denominator). The operation returns coefficient and error scalars, a covariance **table** (`parameter`, `beta_0`, ...), each `residual_i` scalar, and the ordered residual trace as a **plot**. `weighted_residual_sum_squares`, `reduced_chi_square` and `residual_standard_deviation` are also reported. Reduced chi-square is an estimated weighted residual variance; its reference value is not expected to be 1 when weights are only relative.

Do not interpret the fit causally. Correlated errors, omitted variables, nonlinear responses, outliers, highly correlated predictors and incorrectly specified weight ratios invalidate or distort conventional uncertainty estimates.

## Nonlinear Levenberg–Marquardt (`nonlinear_least_squares`)

Inputs: `model`, `x`, `y`, `weights` (one per paired observation), and `initial_parameters` (in the exact order below). Use **positive** or **zero** weights with more positive-weight points than unknown parameters. The objective is *S(β) = Σᵢwᵢ[yᵢ − f(xᵢ,β)]²*. Each model provides analytic derivatives. The solver constructs *JᵀWJ* and *JᵀWr* using 76-digit decimal arithmetic, and solves the diagonally damped and column-equilibrated LM system with pivoted elimination:

\[(J^\mathsf{T}WJ + \lambda\operatorname{diag}(J^\mathsf{T}WJ))\Delta\beta = J^\mathsf{T}Wr.\]

Candidate steps that fail to reduce the weighted objective are rejected and damping increases; accepted steps reduce damping. A small-step/high-precision objective-reduction criterion or a sufficiently small rejected step stops the iterations. At most **1,200** outer iterations and 55 damping trials per iteration are allowed. A failed, singular or non-convergent fit produces a validation error, **not** a misleading success artifact. Diagnostics: `converged`, `iterations`, `accepted_steps`, `rejected_steps`, `final_damping`, `gradient_infinity_norm`, weighted residual sum of squares, reduced chi-square, and residual standard deviation.

| Model name | Model equation | Starting parameter order / restrictions |
| --- | --- | --- |
| `exponential` | *a exp(bx) + c* | `[a, b, c]` (amplitude, rate, baseline) |
| `saturating_exponential` | *a [1 − exp(−bx)]* | `[a, b]`; NIST Misra1a equation |
| `power_law` | *a xᵇ* | `[a, b]`; **all x must be strictly positive** |
| `gaussian` | *c + a exp(−(x−μ)²/(2σ²))* | `[c, a, μ, σ]`; σ must be nonzero (sign is not identifiable, so choose positive σ) |
| `polynomial` | *Σⱼβⱼxʲ* | `[β₀, β₁, ...]` in ascending power order; 1–12 parameters |
| `thurber` | *(β₁+β₂x+β₃x²+β₄x³)/(1+β₅x+β₆x²+β₇x³)* | Seven parameters; NIST StRD rational cubic/cubic |
| `mgh09` | *β₁(x²+β₂x)/(x²+β₃x+β₄)* | Four parameters; NIST StRD rational model |

The strings are case-insensitive. The two rational models are included because the specified NIST StRD certification cases **cannot** be represented by the other four model families. The saturating-exponential model evaluates `1 − exp(−bx)` using a small-argument series when needed; directly subtracting two nearly equal decimal exponentials would lose meaningful predicted values even at 76-digit precision when the amplitude is large. Rational denominators equal to zero at observed points are rejected for that trial. Models are not arbitrary Python expressions; no expression evaluator, network access or arbitrary code execution is involved.

At convergence, the local linearized covariance estimate is *C ≈ [S/(n₊−p)] (JᵀWJ)⁻¹*. Each `standard_error_j` is `sqrt(C[j,j])`; `ci95_lower_j` and `ci95_upper_j` equal *β̂ⱼ ± t₀.₉₇₅,ν × standard_error_j*. The fitted parameter table and residual plot are included in the artifact. **These are approximate local Wald confidence intervals**, not profile-likelihood intervals, bootstrap intervals, simultaneous intervals, or rigorous coverage guarantees for nonlinear or non-Gaussian data. Strong correlations, boundary parameters, non-identifiability, heteroscedasticity, narrow samples and nonnormal errors can render them unreliable. Reduced chi-square assumes inverse-variance weighting and a valid model. LM is a **local** method sensitive to starting parameters; failure to find a global minimum must not be reported as a verified global optimum. For polynomial fits, the linear solver is usually preferable.

## Welch two-sample t test (`welch_t_test`)

Inputs: two independent numeric samples, each of size at least 2; unequal population variances are allowed. Sample means and unbiased sample variances (*s₁²*, *s₂²*, with divisors *n−1*) give

\[t=\frac{\bar x_1-\bar x_2}{\sqrt{s_1^2/n_1+s_2^2/n_2}},\quad
\nu=\frac{(v_1+v_2)^2}{v_1^2/(n_1-1)+v_2^2/(n_2-1)},\quad v_j=s_j^2/n_j.\]

The **two-sided** p-value is *I_{ν/(ν+t²)}(ν/2, 1/2)*, where *I* is the regularized incomplete beta function. Degrees of freedom use the Welch–Satterthwaite approximation and need not be integers. If both samples have zero variance, the test is undefined and rejected. Population independence and a meaningful sample distribution approximation are necessary; the pack does not perform pairing, multiple-test correction, normality tests or effect-size adjustment. `t_statistic`, `degrees_of_freedom`, `p_value`, means and sample sizes are reported.

## Pearson chi-square goodness of fit (`chi_square_gof`)

Inputs: matched vectors of at least two categories, `observed` **nonnegative integer counts with a strictly positive total** and `expected` **strictly positive** expected frequencies. An empty sample (all observed counts zero) is rejected even if an absolute total-matching tolerance would otherwise accept the inputs. Expected totals must equal observed totals (within 1e−12 relative or 1e−10 absolute rounding tolerance). The plugin **never silently normalizes** probabilities, so expected frequencies must be supplied as frequencies, not probabilities. Expected frequencies are assumed specified **before** observing the sample, with no fitted nuisance parameters. The statistic is *χ² = Σ(Oᵢ−Eᵢ)²/Eᵢ*, degrees of freedom are *k−1*, and the **upper-tail** p-value is the regularized incomplete gamma *Q((k−1)/2, χ²/2)*. The result includes category contributions and `expected_count_below_5` as a validity warning. The usual asymptotic chi-square approximation can be poor when expected counts are small (commonly, below 5), observations are dependent, or parameters were estimated from the data. Bin merging and adjustment of degrees of freedom must be performed deliberately outside this operation.

## Numerical probability functions

`_normal_cdf(x)` uses `math.erfc` rather than subtracting nearly equal probabilities in the tails. Student *t* probabilities and quantiles use a **regularized incomplete beta** calculation with a modified-Lentz continued fraction and symmetry transformations, plus monotone bisection of the negative-*t* lower tail for the critical quantile. Direct lower-tail inversion avoids evaluating `1 - p` when a small probability would round that difference to 1 in binary64. For Student-*t* tails with `t²` beyond binary64 range, a small-argument beta asymptotic is evaluated in log space so a representable tail is not silently returned as zero. The chi-square survival function uses **regularized upper incomplete gamma**: a convergent power series below *x < a+1* (with `expm1` complement evaluation) and a continued fraction otherwise. `math.lgamma` supplies logarithmic normalization to reduce overflow and underflow. The functions have explicit domain checks, finite iteration limits, and may correctly return **0** for tails too small for binary64. All reported p-values are bounded to `[0,1]`; **no** external numerical libraries (`numpy`, `scipy`) are imported. A p-value is a probability of data at least as extreme under the null model, not the probability the null model is true.

## Certified tests and reproducibility

The independent numerical oracles live in `backend/tests/test_scientific_fitting_nist.json`, with certification assertions and adversarial cases in `backend/tests/test_scientific_fitting.py`. The data rows, both prescribed starting vectors and published certified coefficients/standard errors are taken from the NIST StRD ASCII datasets, not generated by the implementation under test. Assertions compare all **15 published significant digits** for the Longley linear estimates/standard errors and all **11 published significant digits** for Misra1a, Thurber and MGH09 estimates/standard errors (from *both* starts), along with residual diagnostics. This is a certification against those inputs, **not** a guarantee of arbitrary input accuracy. Other tests cover synthetic exponential, power-law, Gaussian and polynomial data, analytic Jacobians, exact distribution identities, NIST published Student-t and chi-square critical values, invalid domains and workbench receipt tampering. The existing scientific test suites are unchanged.

Official reference inputs and results:

- [NIST Longley ASCII data](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/DATA/Longley.dat) and [certified results](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/v-Longley.shtml).
- [NIST Misra1a ASCII data](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/Misra1a.dat), [NIST Thurber ASCII data](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/Thurber.dat), and [NIST MGH09 ASCII data](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/MGH09.dat).
- [NIST Student-t critical values](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3672.htm) and [chi-square critical values](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3674.htm).

From a complete checkout with test dependencies installed, run:

```sh
python -m pytest -n0 backend/tests/test_scientific_fitting.py
python -m pytest -n0 backend/tests/test_scientific*.py tests/test_scientific*.py
```

Result artifacts and receipts are part of the existing Scientific Workbench graph node; their hashes include the pack ID, operation ID, validated input fingerprint, pack version and output. A separate receipt hash covers UTC time and input provenance. Export JSON preserves those canonical measurements and hashes for later verification.
