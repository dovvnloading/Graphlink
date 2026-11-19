# Scientific Fitting and Inference: quantitative reference

The **Fitting and Inference** pack is a standalone Scientific Workbench plugin (`science_fitting`). Enable it in **Settings → Plugins**, then choose **Plugins → Scientific Workbench → Fitting and Inference**. It uses only Python's standard library, runs locally, requires `graph.read` and `graph.mutate` grants, and uses the existing version **1.2.0** Scientific Workbench result and receipt protocol. The operation catalog and worked examples are available from the enabled plugin. The host validates exact field names, finite measurements, numeric dimensions and immutable source citations before calculation; failed runs do not overwrite the last successful result.

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

The built-in names are case-insensitive. The two rational models are retained for compatibility and certified NIST fits. The saturating-exponential model uses a small-argument series to protect against cancellation. Zero rational denominators are rejected at observed points. You can now also enter a **safe mathematical expression**, never Python code; see the custom-expression and bound interfaces below.

At convergence, the local linearized covariance estimate is *C ≈ [S/(n₊−p)] (JᵀWJ)⁻¹*. Each `standard_error_j` is `sqrt(C[j,j])`; `ci95_lower_j` and `ci95_upper_j` equal *β̂ⱼ ± t₀.₉₇₅,ν × standard_error_j*. The fitted parameter table and residual plot are included in the artifact. **These are approximate local Wald confidence intervals**, not profile-likelihood intervals, bootstrap intervals, simultaneous intervals, or rigorous coverage guarantees for nonlinear or non-Gaussian data. Strong correlations, boundary parameters, non-identifiability, heteroscedasticity, narrow samples and nonnormal errors can render them unreliable. Reduced chi-square assumes inverse-variance weighting and a valid model. LM is a **local** method sensitive to starting parameters; failure to find a global minimum must not be reported as a verified global optimum. For polynomial fits, the linear solver is usually preferable.

## Custom nonlinear models, safe expressions and optional bounds

The original `nonlinear_least_squares` operation still accepts the exact five legacy fields `model`, `x`, `y`, `weights`, and `initial_parameters`. All built-in names, fitted values, uncertainty estimates, and existing solver settings remain valid. `model` also accepts a *safe expression in x*, e.g. `a*(1-exp(-b*x))`. Parameter names are inferred **in first-appearance order** (`a`, then `b` in this example), and `initial_parameters: [250,0.0005]` follows that order. Names are case-sensitive. When an explicit order or optional bounds are needed, `model` can instead be a JSON **text** specification:

```json
{
  "expression": "a*(1-exp(-b*x))",
  "parameter_names": ["a", "b"],
  "bounds": {"a": [0, null], "b": [0, 0.01]}
}
```

This JSON text is supplied as the `model` field; the separate `initial_parameters` vector remains mandatory for the legacy operation. The alternative JSON form `{"builtin":"exponential","bounds":[[0,null],[null,0],[null,null]]}` allows bounds on a built-in family without changing its equation. For an easier explicit-name interface, use `custom_nonlinear_least_squares` with `expression` (text), `parameters` (JSON text), and the same `x`, `y` and `weights` vectors:

```json
{
  "expression": "a*(1-exp(-b*x))",
  "parameters": "{\"a\":{\"start\":250,\"lower\":0},\"b\":{\"start\":0.0005,\"lower\":0,\"upper\":0.01}}",
  "x": [77.6, 114.9, 141.1, 190.8, 239.9],
  "y": [10.07, 14.73, 17.94, 23.93, 29.61],
  "weights": [1, 1, 1, 1, 1]
}
```

Each entry in the parameters JSON can be a number (its starting value) or an object with required `start` and optional `lower` and `upper`; omitted bounds are unrestricted. Bounds are inclusive and must satisfy lower strictly less than upper, when both are present. Initial values must lie inside the bounds. Candidate LM steps are projected to the valid interval, and convergence on a boundary produces a clear warning. A boundary fit still reports the **unconstrained local linearized** standard errors and Wald intervals, which are generally unreliable at an active bound; they are *not* constrained-confidence intervals.

The expression grammar is exactly the Measurement Uncertainty pack's existing `_Parser` (recursive-descent tokenizer, length/token/depth limits, and allowed functions). It accepts numeric literals, `x`, up to **15 named fitted parameters**, parentheses, unary signs, arithmetic `+ - * /`, exponentiation `^` (not Python `**`), and the functions `sqrt`, `exp`, `ln`, `log10`, `sin`, and `cos`. The expression may use `x` (a parameter-only constant model is also valid) and must use **all named parameters**. Parameter names cannot be `x`, a function name, or an invalid identifier. The parser rejects attribute access, indexing, comprehensions, method calls, Python expressions, and arbitrary function names; no `eval`, `exec`, imports, file access or network calls are involved. The parsed AST is evaluated in 76-digit Decimal arithmetic with chain-rule analytic partial derivatives, except `sin` and `cos` values (and their analytic derivative factors) which use the finite binary64 standard-library trigonometric functions. Real-domain violations, undefined derivatives, nonfinite results and nondecreasing LM trials are rejected. As with all LM methods, **starting values matter**; the optimizer cannot guarantee a global optimum.

### Fit diagnostics added to both linear and nonlinear fits

Every successful weighted linear, built-in nonlinear, or custom nonlinear fit retains its previous outputs and additionally supplies:

- `aic`, `aicc`, `bic`: **full weighted Gaussian log-likelihood** information criteria with an unknown common residual variance. They use the **positive-weight observations** `n₊` and `k = p + 1` fitted likelihood parameters (the regression parameters plus the noise variance), so `AIC = -2log(Lmax)+2k`, `BIC = -2log(Lmax)+k log(n₊)`, and `AICc = AIC + 2k(k+1)/(n₊-k-1)`. The common Gaussian weight normalization `-sum(log(weight_i))` is included. If `n₊ <= k+1` or the weighted residual sum of squares is exactly zero, an affected criterion is marked `"undefined"` with a warning rather than emitting an infinite or misleading value. Comparisons require defined AICc for *all* candidates.
- `residual_0`, `residual_1`, etc., ordered exactly as the input (including zero-weight rows), plus the existing residual plot. The **runs test** uses only positive-weight residuals, discards exact zeros, counts sign runs about zero, and reports `runs_count`, `runs_test_z_absolute` and `runs_test_p_value` (two-sided normal approximation with continuity correction). The **Shapiro–Wilk** normality check reuses the existing Inference pack `_shapiro` implementation and reports `residual_shapiro_w` and `residual_shapiro_p_value`. Tests return `"undefined"` with an explanatory warning if the sample is degenerate/insufficient. The tests are *descriptive screening checks*: fitted residuals are not independent unmodified observations, and their p-values should not be treated as exact fit-validity probabilities.
- `parameter_correlation_i_j` for each off-diagonal pair in parameter order, with `max_absolute_parameter_correlation`. Correlations come from the local fit covariance. `warnings` is the string `"none"` or an explicit semicolon-separated explanation when any pair has absolute correlation at least **0.99**, a standard error exceeds the absolute corresponding estimate, a fitted parameter meets a bound within the numerical tolerance, a diagnostic is undefined, or residual screening reports `p < 0.05`. Large correlations and wide errors flag poorly determined parameters; they do not prove global unidentifiability.

All candidate models must use the **same positive-weight data**, same response transformation and the same interpretation of relative weights for information-criterion comparisons to be meaningful. AIC/AICc/BIC measure relative predictive tradeoffs under the Gaussian error model, *not* the probability that a model is scientifically true.

### Compare models (`compare_models`)

Inputs are shared `x`, `y`, `weights` vectors and `models` (a **JSON text array of 2–12 candidate specifications**). Each candidate is independently fitted by the same nonlinear solver; comparison never trusts a caller's precomputed AIC or purported fitted values. A candidate can be a built-in/legacy model with `{"name":"linear","model":"polynomial","initial_parameters":[1,2]}`, a raw safe expression in `model`, or an explicitly named bounded custom expression with `{"name":"saturating","expression":"a*(1-exp(-b*x))","parameters":{"a":{"start":250,"lower":0},"b":{"start":0.0005,"lower":0}}}`. Names must be unique. The operation refuses candidates for which fitting fails or AICc is undefined, rather than silently omitting them.

The output table is ranked by ascending **AICc** and includes AIC, AICc, BIC, `delta AICc`, normalized **Akaike weights** `exp(-delta/2) / sum_j exp(-delta_j/2)`, weighted SSE, number of regression parameters and per-fit warnings. `values.best_model` and `akaike_weight_i` use the ranked order. These weights are conditional on the *candidate set*, not absolute posterior model probabilities; equal prior probabilities, Gaussian errors, and comparable observations/weights are implicit. The table preserves numerical diagnostics without replacing any existing result table.

### Independent certification of user expressions

In addition to all existing built-in NIST StRD cases, the **same published data, starting vectors and 11 published significant digits** are tested through safe *custom* equations for [Misra1a](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/v-Misra1a.shtml), [Thurber](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/v-Thurber.shtml), and [MGH09](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/v-MGH09.shtml). Assertions cover every certified coefficient, every certified standard error and the residual sum of squares from **both** published initial vectors. The linear Longley certification, original nonlinear operations and other inference tests remain in place.

## Welch two-sample t test (`welch_t_test`)

Inputs: two independent numeric samples, each of size at least 2; unequal population variances are allowed. Sample means and unbiased sample variances (*s₁²*, *s₂²*, with divisors *n−1*) give

\[t=\frac{\bar x_1-\bar x_2}{\sqrt{s_1^2/n_1+s_2^2/n_2}},\quad
\nu=\frac{(v_1+v_2)^2}{v_1^2/(n_1-1)+v_2^2/(n_2-1)},\quad v_j=s_j^2/n_j.\]

The **two-sided** p-value is *I_{ν/(ν+t²)}(ν/2, 1/2)*, where *I* is the regularized incomplete beta function. The implementation evaluates this as twice the stable negative-*t* tail, including the logarithmic small-beta-argument limit when `t²` overflows; a representable p-value must not silently become zero. Degrees of freedom use the Welch–Satterthwaite approximation and need not be integers. If both samples have zero variance, the test is undefined and rejected. Population independence and a meaningful sample distribution approximation are necessary; this Welch operation does not perform pairing or effect-size adjustment; the pack also provides paired-rank, normality and multiple-test procedures. `t_statistic`, `degrees_of_freedom`, `p_value`, means and sample sizes are reported.

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


## One-way ANOVA with Tukey–Kramer HSD (`one_way_anova`)

Inputs: `values` contains the numeric responses and `groups` contains one numeric group code for **each** response in the same order. Codes are arbitrary finite numbers (e.g., `[1,1,2,2,3,3]`), and **unequal** group sizes are supported. There must be 2–32 distinct groups, with at least two observations in each group. A zero pooled within-group variance is an **error**: the F statistic and studentized-range post-hoc probabilities are undefined in that case.

For \(k\) groups, \(n\) observations, group means \(\bar x_i\), grand mean \(\bar x\), and within-group values \(x_{ij}\), calculate \(SS_B=\sum_i n_i(\bar x_i-\bar x)^2\), \(SS_W=\sum_{ij}(x_{ij}-\bar x_i)^2\). The F statistic is \(F=[SS_B/(k-1)]/[SS_W/(n-k)]\); the upper-tail p-value uses the **regularized incomplete beta** calculation in the existing fitting module. The output includes both sums and mean squares, between/within degrees of freedom, F, p-value, \(R^2=SS_B/(SS_B+SS_W)\) and the residual standard deviation. Summation, centering and mean calculation use **76-digit decimal arithmetic** on the normalized finite inputs to preserve small residuals near large baselines, with the result converted to binary64 only at the artifact boundary.

The post-hoc table contains **every pair** of numeric group codes in ascending order, observed difference \(\bar x_i-\bar x_j\), Tukey–Kramer standard error \(s_{ij}=\sqrt{MS_W(1/n_i+1/n_j)/2}\), studentized-range \(q=|\bar x_i-\bar x_j|/s_{ij}\), Tukey-adjusted two-sided p-value, and **simultaneous familywise 95% confidence limits** \((\bar x_i-\bar x_j)\pm q_{0.95;k,n-k}s_{ij}\). For unequal sizes this is the conventional conservative Tukey–Kramer extension. The distribution is evaluated by deterministic Gauss–Legendre quadrature of the **normal-range and chi-square scale mixture** (Hartley integral), rather than a t-distribution substitution or Bonferroni approximation; for two groups the exact relation \(q=\sqrt2|t|\) is used.

Assumptions: independent observations, independent normally distributed errors with equal population variance across groups, and a fixed one-way treatment model. Tukey HSD controls **familywise** pairwise error under the same assumptions; it does not correct for data-dependent subgroup selection, heterogeneous variances or dependent repeated measurements. The pairwise confidence intervals are not the marginal per-comparison intervals.

## Mann–Whitney U (`mann_whitney_u`)

Inputs: `sample_a`, `sample_b` (independent samples, at least **two** observations in each). The method assigns **midranks** for equal pooled measurements, then reports \(U_A=R_A-n_A(n_A+1)/2\), \(U_B=n_An_B-U_A\), the smaller U, both sample sizes and the **two-sided** p-value.

For **\(n_A+n_B\le30\)**, p-values are **exact permutation probabilities conditional on the observed ranks**. Dynamic programming counts all \(\binom{n_A+n_B}{n_A}\) label assignments, with ranks multiplied by two to represent half-integer tied midranks without floating-point ambiguity. The two-sided probability is \(2\min\{P(U_A\le u),P(U_A\ge u)\}\), capped at 1. Thus exact small-sample p-values **also support ties**, but can differ from programs that use a tie-ignorant exact null distribution.

For pooled sizes **greater than 30**, a normal approximation uses mean \(n_An_B/2\), tie-corrected variance
\[
\operatorname{Var}(U_A)=\frac{n_An_B}{12}
\left[N+1-\frac{\sum_t(t^3-t)}{N(N-1)}\right],
\quad N=n_A+n_B,
\]
where \(t\) are pooled tie-block sizes. A **0.5 continuity correction** is applied toward the null mean; the resulting two-sided normal tail is reported, together with the method. A sample in which all pooled observations are identical is rejected.

The test is about **distributions/stochastic ordering**; interpret it as a median shift only when the groups have comparable shapes. Independence and exchangeability under the null are necessary for the exact permutation p-value.

## Wilcoxon signed-rank (`wilcoxon_signed_rank`)

Inputs: `before`, `after` matched by position and of **equal length**. The operation ranks the absolute **after minus before** differences, assigns midranks for tied magnitudes, and uses the **Wilcoxon zero convention**: exact zeros are dropped *before* ranking. At least **two nonzero differences** are required. The result includes total/nonzero/dropped pairs, \(W^+\) (positive rank sum), \(W^-\) (negative), \(\min(W^+,W^-)\), and a two-sided p-value. A paired-difference magnitude outside finite range is rejected.

For **up to 30 nonzero pairs**, exact p-values count all \(2^n\) sign assignments of the observed doubled midranks using a subset-sum dynamic program (conditional exact test even **with ties**). Above 30, the tie-corrected normal variance is
\[
\operatorname{Var}(W^+) =
\frac{n(n+1)(2n+1)}{24}-\frac{\sum_t(t^3-t)}{48},
\quad E[W^+]=n(n+1)/4,
\]
with a **0.5 continuity correction** toward the null mean. Do not interpret this as a generic paired median test unless the paired-difference distribution is **symmetric**; pairs should be independent and matched meaningfully.

## Shapiro–Wilk normality (`shapiro_wilk`)

Input: `sample`, **3–1024** finite observations (the shared workbench vector limit; Royston's algorithm itself supports up to 5000). All-equal data are rejected. The operation calculates the classical \(W=[\sum_i a_i x_{(i)}]^2/\sum_i(x_i-\bar x)^2\) with antisymmetric weights. The weight construction uses **Royston's AS R94** approximation: Blom normal-order scores \(m_i=\Phi^{-1}[(i-3/8)/(n+1/4)]\), polynomial corrections to the outer one/two weights and normalization of the remaining weights. The ordered observations are centered and scaled before taking squares, reducing cancellation and overflow.

For **\(n=3\)** the significance probability is **exact**, \(p=(6/\pi)[\arcsin(\sqrt W)-\pi/3]\); for \(4\le n\le11\), the short-sample Royston transform and fitted normal parameters are used; for \(n\ge12\), the log-\(n\) Royston approximation applies. This is an **approximate** null p-value for all \(n\ge4\); even a p-value above 0.05 does not prove the measurements are normal. The method assumes an independent continuous sample.

## Pearson and Spearman correlations (`pearson_correlation`, `spearman_correlation`)

Both accept position-matched numeric vectors `x` and `y`, with **at least four pairs** to define the requested confidence intervals; unequal lengths or constant variables are rejected. Computation uses centered and range-scaled values and stable sums. All p-values are **two-sided**.

**Pearson** returns the product-moment \(r\), degrees of freedom \(n-2\), and a Student-t null test \(t=r\sqrt{(n-2)/(1-r^2)}\). Its **95% Fisher-z confidence interval** is \(\tanh[\operatorname{atanh}(r)\pm z_{0.975}/\sqrt{n-3}]\). This interval and t test assume independent, approximately bivariate-normal paired observations; extreme outliers and nonlinearity can invalidate interpretation.

**Spearman** returns Pearson correlation computed **on midranks** (correctly incorporating ties). For **up to 8 pairs**, its two-sided p-value is the **exact permutation** proportion conditional on the observed ranks, including ties. For larger paired samples, it uses the usual **Student-t approximation** with \(n-2\) degrees of freedom. Its **approximate 95% confidence interval** uses the *Bonett–Wright (2000)* modified Fisher-z variance \((1+\rho^2/2)/(n-3)\). This interval is a **large-sample approximation**, not an exact small-sample confidence interval; it is least reliable for \(n<10\), heavy ties or nearly perfect correlations. At \(r=\pm1\), the output takes the limiting endpoint convention \([\pm1,\pm1]\), not a finite-sample coverage guarantee. Spearman correlation is about **monotone association**, not a causal relationship.

## Multiplicity correction (`bonferroni_correction`, `benjamini_hochberg_correction`)

Both operations accept `p_values` (one or more valid numbers in **[0, 1]**) and a desired threshold `alpha` **strictly between 0 and 1**. Output rows preserve the **input order** and show zero-based hypothesis index, raw p, adjusted p and rejection at the supplied alpha. A count of rejected hypotheses is also returned.

- **Bonferroni:** \(p^{adj}_i=\min(1,mp_i)\). Familywise error control does not require independent tests, provided the input p-values are valid.
- **Benjamini–Hochberg:** sort p-values to \(p_{(1)}\le\cdots\le p_{(m)}\) and calculate \(p^{adj}_{(i)}=\min_{j\ge i}\min(1,mp_{(j)}/j)\), working backward to ensure **monotonic adjusted probabilities**; unsort into the original order. BH controls **false discovery rate** under independent or suitably **positive-regression-dependent** p-values, not arbitrary dependence.

These corrections apply to the **full family of planned tests**; submitting only a selected subset of significant p-values invalidates the declared multiplicity control. They do not repair biased sampling, inappropriate test statistics or violations of assumptions.

## Independent certification cases for the additional tests

`backend/tests/test_scientific_inference.py` uses exact NIST StRD data and **15 published significant digits** for each published ANOVA sum of squares, mean square, F, \(R^2\) and residual standard deviation (where NIST supplies that result). The hard-case `SmLs07` rows preserve the 13 leading common digits. All three tests compare **formatted published significant digits** (not a loose absolute epsilon):

| NIST dataset | Between df / within df | Between SS | Within SS | Certified F |
| --- | --- | --- | --- | --- |
| [SiRstv](https://www.itl.nist.gov/div898/strd/anova/SiRstv.dat) | 4 / 20 | 0.0511462616000000 | 0.216636560000000 | 1.18046237440255 |
| [AtmWtAg](https://www.itl.nist.gov/div898/strd/anova/AtmWtAg.dat) | 1 / 46 | 3.63834187500000e-9 | 1.04951729166667e-8 | 15.9467335677930 |
| [SmLs07](https://www.itl.nist.gov/div898/strd/anova/SmLs07.dat) | 8 / 180 | 1.68000000000000 | 1.80000000000000 | 21.0000000000000 |

Other independent worked-example tests cover:

- [NIST Tukey critical value](https://www.itl.nist.gov/div898/handbook/prc/section4/prc471.htm), \(q_{0.95;5,10}\approx4.65\), plus the two-group exact t identity.
- [ACCP published Wilcoxon diuretic example](https://www.accp.com/docs/bookstore/biosampl.pdf), \(W^+=4\), exact two-sided \(p=0.21875\), and an [8-pair all-decreasing example](https://binarydecimal.com/tools/statistics-calculator/wilcoxon-signed-rank-test), exact \(p=2/256\).
- [Royston/Shapiro 11-value worked example](https://algorithm.tamnd.com/shapiro-wilk-test/), \(W\approx0.78881\), \(p\approx0.006704\).
- [Emory rank correlation example](https://mathcenter.oxford.emory.edu/site/math117/rankCorrelation/), \(\rho=-0.1785714\), plus an exact 5-case permutation example.
- [Benjamini–Hochberg's original 15 p-values](https://algorithm.tamnd.com/benjamini-hochberg-procedure/) (four BH and three Bonferroni rejections at 0.05), including adjusted probabilities and monotonicity.
- Exact tied-rank permutation probabilities checked by **independent brute-force enumeration** and large-sample tie/continuity corrections checked against independently written null-variance formulas, as well as degenerate-input errors and verifiable workbench receipts.

Run the new tests along with the original numerical certification suite:

```sh
python -m pytest -n0 backend/tests/test_scientific_inference.py backend/tests/test_scientific_fitting.py
```

### Binary64 limits and quadrature resolution (2026-11-18 audit)

The certified StRD sums and mean squares are computed in Decimal, but these
diagnostics are published as binary64 numbers. If a nonzero variance diagnostic
underflows to zero at that boundary, ANOVA now raises an explicit range error
rather than producing mutually inconsistent zero sums and a finite F statistic.
For large Tukey families (16–32 groups), the normal-range quadrature uses 128
rather than 80 nodes to resolve the more sharply concentrated kernel; for
example, the independently checked CDF at q=5, k=32, df=40 is
0.828224730257006. This refinement does not change the test's assumptions.

## Additional inferential methods (advanced scientific statistics)

These operations are in the **Fitting and Inference** Workbench pack; they use the
same receipt hashes, numeric-vector/matrix constraints and Python standard library
as the existing methods. Numeric factor codes are categories, not covariates. The
Workbench limits input vectors to 1,024 values, matrix widths to 32 columns and
matrix cells to 4,096. Results are **unadjusted for selection** unless explicitly
noted. No operation substitutes for appropriate experimental design.

### Two-way factorial ANOVA (`two_way_anova`)

Supply `values`, `factor_a` and `factor_b` as aligned numeric vectors. Each
factor must have at least two levels; the Cartesian product of levels must
contain no empty cells and may have at most 64 combinations. Independent
replicates must leave positive error df `n - (a*b)`. The full model includes
main effects and interaction and estimates an individual mean per cell. The
pooled error mean square is `SSE(full) / (n-a*b)`; a zero error variance is
rejected. The **Type II** main-effect sums of squares are partial reductions in
SSE when adding A to B and B to A in the **additive** model:

- `SS(A | B) = SSE(B-only) - SSE(A+B)`.
- `SS(B | A) = SSE(A-only) - SSE(A+B)`.
- `SS(A:B) = SSE(A+B) - SSE(full)`.

The corresponding degrees of freedom are `a-1`, `b-1` and `(a-1)(b-1)`.
Reported F ratios divide each term's mean square by **full-model** error mean
square and use the incomplete-beta F tail. In unbalanced data these partial
sums are generally **not additive**. Evaluate the interaction first: when it
is important, a single averaged main effect can obscure cell-specific effects.
Fixed factors, independent normal errors and common residual variance are
assumed. Summations/linear solves use 76-digit decimal precision. The
unbalanced reference test verifies `SS_A=364.03361344537814`,
`SS_B=67.80504201680672`, `SS_AB=3.573529411764706` and `SSE=25.25` on
an independently checked 2×2 factorial design. The definition follows
[Langsrud, *ANOVA for unbalanced data: Use Type II instead of Type III sums of
squares*](https://www.stat.yale.edu/~jtc5/312_612/readings/unbalanced_two-way_anova_and_interactions/unbalanced-anova-use-type-II-SS_Stat-Comput_03.pdf).

### Kruskal–Wallis with Dunn comparisons (`kruskal_wallis`)

Supply aligned `values` and `groups` vectors, for 2–32 groups with at least two
observations per group. All pooled observations receive **midranks**; the
omnibus H is divided by `C=1-Σ(t³-t)/(N³-N)` for ties, with an asymptotic
upper chi-square tail on `k-1` df. Dunn's pairwise comparisons use differences
of average midranks, variance
`[N(N+1)-Σ(t³-t)/(N-1)]/12 × (1/n_i+1/n_j)`, two-sided normal tails and
**Holm step-down** adjusted probabilities over *all* pairs. They are not
Mann–Whitney permutation tests. The [published tied example](https://www.clinicalbiostats.com/tutorials/undergraduate-statistics-introduction-to-statistics/the-kruskal-wallis-test-for-independent-groups/)
(4 ones; 2,2,2,4; 3,3,5,6) yields `H=9.254612546125466` and
`p=0.009781071182573`. A pooled all-ties sample is invalid. These
rank-based procedures assume independent observations and do not demonstrate
identical medians unless group distribution shapes are comparable.

### Contingency tables (`chi_square_independence`, `fisher_exact`)

Each accepts `table` as a rectangular matrix of **nonnegative integer counts**.
At least 2 rows and columns with all row and column margins positive are
required. `chi_square_independence` calculates expected cell counts from
`row_total * column_total / N`, Pearson's uncorrected statistic
`Σ(O-E)²/E`, `(r-1)(c-1)` degrees of freedom, its upper chi-square tail,
Cramér's V, and cellwise Pearson residuals. It flags expected counts below 5;
small samples, dependent observations and structural zeros may invalidate the
asymptotic test. No Yates correction or estimated-parameter adjustment is
applied. On [Minitab's cookie preference example](https://support.minitab.com/en-us/minitab/help-and-how-to/statistics/tables/how-to/cross-tabulation-and-chi-square/methods-and-formulas/test-for-2x2-tables/)
`[[9,1],[2,8]]`, chi-square is `9.8989898989899`, p is
`0.00165369516370034` and Fisher p is `0.00547749464158133`.

`fisher_exact` requires exactly 2×2, enumerates fixed-margin hypergeometric
tables using logarithmic combinatorial weights, and reports one-sided
`p_less`/`p_greater` and **two-sided probability-order**
`p_two_sided` (sum of all table probabilities at most as likely as the
observed table). It is not simply double the smaller tail. To bound execution,
no more than 100,001 feasible tables are supported; larger enumeration raises
an error. `odds_ratio` is the *sample cross-product ratio* `ad/bc`, not R's
conditional-MLE odds ratio. For [Fisher's tea tasting](https://online.stat.psu.edu/stat504/Lesson04)
`[[3,1],[1,3]]`, this yields `p_two_sided=34/70`, `p_greater=17/70`
and sample odds ratio 9. Infinite odds ratios are represented as text rather
than an invalid floating-point infinity. All tests require mutually exclusive
categories and independent experimental units.

### Multiple linear regression (`multiple_linear_regression`)

Supply `predictors` as an `n × p` matrix **without** an intercept column and
`response` as an aligned length-n vector. Up to 15 predictors plus a fitted
intercept are supported. The full-rank unweighted OLS fit requires `n > p+1`,
nonconstant response and strictly positive residual SSE. It reports each
`coefficient_j`, `standard_error_j`, `ci95_lower_j`, `ci95_upper_j`,
two-sided Student-t p-values and predictor `vif_j`, as well as the coefficient
table. Here `j=0` identifies the intercept. The covariance estimate is
`[SSE/(n-p-1)] (X'X)^-1`; VIF is `SST(X_j) × (X'X)^-1[j,j]`, with an
intercept in X. Both calculations use equilibrated pivoted high-precision
normal equations, avoiding binary64 normal-equation cancellation on difficult
data. `r_squared` and `adjusted_r_squared` use centered total SS. Rank
failure and zero error variance produce explicit errors, never fictitious
confidence intervals.

Individual `fitted_i`, `residual_i`, `leverage_i`, `standardized_residual_i`
and `cooks_distance_i` values (zero-based `i`) are available, with a
residual-versus-fitted plot. Additional aggregates are residual SD,
Durbin–Watson, maximum absolute standardized residual/leverage/Cook's D,
and the asymptotic Jarque–Bera statistic/p-value. A unit-leverage row has
undefined standardized residual and Cook's D (reported as text). Durbin–Watson
uses the *supplied row order*, and its usual interpretation requires a time
ordering; Jarque–Bera is not reliable for very small samples. OLS t intervals
assume independent homoscedastic normal errors with a correct linear model.
The [NIST StRD Longley certification](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/v-Longley.shtml)
is asserted to **15 published significant digits** for all seven coefficients
and SEs, residual SD and R².

### Binary logistic IRLS (`binary_logistic_regression`)

Supply `predictors` as rows (without the intercept) and an aligned binary
`outcome` vector of **exactly 0/1** observations. Both classes, positive
degrees of freedom, full predictor rank and at most 12 predictors are
required. The binomial log-likelihood is maximized by a **damped iteratively
reweighted least squares** Newton solver; features are standardized for the
iteration and coefficients/covariances are transformed back to original units.
The final information-matrix inverse gives conventional nonrobust Wald SE,
95% normal intervals on log odds, odds ratios `exp(beta)`, and odds ratio
intervals obtained by exponentiating the log-odds interval endpoints. AIC,
log likelihood, the LR test, McFadden pseudo-R² and fitted probability plot
are also provided. Nonconvergence, near-singular information or diverging
coefficients (complete/quasi separation) are **errors**; this is not Firth or
penalized regression, and the code does not silently report finite estimates
under separation. Avoid interpreting Wald intervals when data are sparse or
separation is plausible. The published [Spector example](https://www.statsmodels.org/v0.14.4/discretemod.html)
(32 observations, predictors GPA/TUCE/PSI) yields beta
`[-13.02134686, 2.82611259, 0.09515766, 2.37868766]` and SE
`[4.93132421, 1.26294108, 0.14155421, 1.06456425]`.

### Seeded percentile/BCa bootstrap (`bootstrap_confidence_interval`)

Input `sample`, a `statistic` text selector (`mean`, `median`, or
`standard_deviation`), integer `replicates` (200–20,000), integer `seed` and
`confidence_level` strictly between 0 and 1. Bootstrap resampling is *iid
with replacement* using a **local** `random.Random(seed)`; the seed and
replicate count are receipted, and identical inputs produce exactly identical
bounds. Both intervals use linearly interpolated sorted empirical quantiles.
The **percentile interval** takes the alpha/2 and 1-alpha/2 quantiles;
**BCa** applies `z0=Phi^-1(P(t*<t_hat))` (half weight for ties) and the
leave-one-out jackknife acceleration
`a=Σ(mean(jack)-jack_i)^3 / (6[Σ(mean(jack)-jack_i)^2]^(3/2))` to change
quantile levels. An undefined jackknife acceleration, constant resampling
statistic or an impossible BCa quantile gives an explicit error, rather than
fabricating a BCa interval. Monte Carlo uncertainty decreases with the
number of replicates; specify and preserve the seed, and use at least a few
thousand replicates for reporting. This is **not** a cluster, block or
parametric bootstrap. See [Efron/Narasimhan's BCa discussion](https://cran.wustl.edu/web/packages/bcaboot/vignettes/bcaboot.html).

### Power and minimum sample size (`t_test_power`, `proportion_power`)

Both return `achieved_power` for input `sample_size` and the smallest integer
`required_sample_size_per_group` achieving `target_power` (plus power at that
size), searching up to one million observations per group. `alpha` and
`target_power` must each lie in `(0,1)`; `tails` is `one_sided` or
`two_sided`. The one-sided calculation assumes the effect direction is
**specified before sampling**. All calculations use known effect inputs,
not effects estimated from the same study being tested.

`t_test_power` takes a **nonzero positive magnitude** of Cohen's `effect_size`
(absolute value of signed input). `design` is `one_sample`, `paired` (the
effect is the mean paired difference divided by its SD), or `two_sample`
(equal n in independent groups and Cohen's d based on common SD). Degrees of
freedom are `n-1` for the first two, and `2n-2` for two samples;
noncentrality is `d*sqrt(n)` or `d*sqrt(n/2)` respectively. Exact noncentral
t probabilities are calculated deterministically by Gauss–Legendre
quadrature over the chi-square scale mixture, not by the normal approximation.
The independently checked example `d=0.5`, two-sided alpha `0.05`, desired
power `0.8`, two equal groups requires **64 per group** (power
`0.801459557922275`).

`proportion_power` accepts interior probabilities `proportion_a` and
`proportion_b` which must be unequal. `design=one_sample` compares an
alternative `p1=a` against null `p0=b`; `design=two_sample` compares
independent equal-n groups with proportions `a,b`. It uses the usual **normal
z-test approximation** with null variance at the pooled proportion (two
samples) or p0 (one sample), and alternative variance at the proposed
proportion(s). It does **not** apply continuity correction, exact/binomial
small-sample power or account for attrition, unequal allocation or clustering.
For `a=0.6,b=0.4`, two-sided alpha `0.05`, target power `0.8` the normal
approximation recommends **97 per group**. These are prospective planning
calculations; realized sample sizes, missingness and model assumptions affect
actual power.

Run the additional certification and regression suites after a full checkout:

```sh
python -m pytest -n0 backend/tests/test_scientific_inference.py backend/tests/test_scientific_fitting.py
```

### Independent correctness audit: magnitude extremes and exact-test tails

The **Pearson/Spearman correlation** helper and **Shapiro–Wilk** implementation now scale each finite input vector by its largest absolute magnitude **before subtracting** its baseline. A vector that spans values such as `−1e308` to `+1e308` is finite even though the unscaled subtraction overflows; normalization preserves the dimensionless Pearson `r`, Spearman `rho`, and Shapiro–Wilk `W` and their p-values, rather than rejecting those legitimate measurements. The input size and constant-data validations remain unchanged. Confidence intervals and p-values retain their previous methods and precision limitations.

**Fisher's 2×2 exact test:** The null distribution is hypergeometric conditional on fixed row and column margins. Subtracting `lgamma` values for margins near `10¹⁵` loses extreme-tail accuracy and can inflate a true `~10⁻¹⁵` probability by more than an order of magnitude. The implementation now calculates unnormalized probabilities by adjacent hypergeometric probability ratios, starting at the exact integer mode, then normalizes their sum. The two-sided convention is unchanged: sum all tables whose probability does not exceed the observed table's; directional p-values are the inclusive upper/lower tails. For `[[10**15+1,0],[0,1]]`, the one-sided greater and two-sided p-values are both **`1/(10**15+2)`**, to binary64 precision; for `[[3,1],[1,3]]`, the two-sided p-value is **`34/70`**. The sample odds ratio remains an unconditional cross-product estimate. [SciPy Fisher exact convention and hypergeometric PMF](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.fisher_exact.html), Fisher (1935).
