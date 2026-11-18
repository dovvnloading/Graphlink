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

The **two-sided** p-value is *I_{ν/(ν+t²)}(ν/2, 1/2)*, where *I* is the regularized incomplete beta function. The implementation evaluates this as twice the stable negative-*t* tail, including the logarithmic small-beta-argument limit when `t²` overflows; a representable p-value must not silently become zero. Degrees of freedom use the Welch–Satterthwaite approximation and need not be integers. If both samples have zero variance, the test is undefined and rejected. Population independence and a meaningful sample distribution approximation are necessary; the pack does not perform pairing, multiple-test correction, normality tests or effect-size adjustment. `t_statistic`, `degrees_of_freedom`, `p_value`, means and sample sizes are reported.

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


## One-way ANOVA with Tukey–Kramer HSD (\`one_way_anova\`)

Inputs: \`values\` contains the numeric responses and \`groups\` contains one numeric group code for **each** response in the same order. Codes are arbitrary finite numbers (e.g., \`[1,1,2,2,3,3]\`), and **unequal** group sizes are supported. There must be 2–32 distinct groups, with at least two observations in each group. A zero pooled within-group variance is an **error**: the F statistic and studentized-range post-hoc probabilities are undefined in that case.

For \(k\) groups, \(n\) observations, group means \(\bar x_i\), grand mean \(\bar x\), and within-group values \(x_{ij}\), calculate \(SS_B=\sum_i n_i(\bar x_i-\bar x)^2\), \(SS_W=\sum_{ij}(x_{ij}-\bar x_i)^2\). The F statistic is \(F=[SS_B/(k-1)]/[SS_W/(n-k)]\); the upper-tail p-value uses the **regularized incomplete beta** calculation in the existing fitting module. The output includes both sums and mean squares, between/within degrees of freedom, F, p-value, \(R^2=SS_B/(SS_B+SS_W)\) and the residual standard deviation. Summation, centering and mean calculation use **76-digit decimal arithmetic** on the normalized finite inputs to preserve small residuals near large baselines, with the result converted to binary64 only at the artifact boundary.

The post-hoc table contains **every pair** of numeric group codes in ascending order, observed difference \(\bar x_i-\bar x_j\), Tukey–Kramer standard error \(s_{ij}=\sqrt{MS_W(1/n_i+1/n_j)/2}\), studentized-range \(q=|\bar x_i-\bar x_j|/s_{ij}\), Tukey-adjusted two-sided p-value, and **simultaneous familywise 95% confidence limits** \((\bar x_i-\bar x_j)\pm q_{0.95;k,n-k}s_{ij}\). For unequal sizes this is the conventional conservative Tukey–Kramer extension. The distribution is evaluated by deterministic Gauss–Legendre quadrature of the **normal-range and chi-square scale mixture** (Hartley integral), rather than a t-distribution substitution or Bonferroni approximation; for two groups the exact relation \(q=\sqrt2|t|\) is used.

Assumptions: independent observations, independent normally distributed errors with equal population variance across groups, and a fixed one-way treatment model. Tukey HSD controls **familywise** pairwise error under the same assumptions; it does not correct for data-dependent subgroup selection, heterogeneous variances or dependent repeated measurements. The pairwise confidence intervals are not the marginal per-comparison intervals.

## Mann–Whitney U (\`mann_whitney_u\`)

Inputs: \`sample_a\`, \`sample_b\` (independent samples, at least **two** observations in each). The method assigns **midranks** for equal pooled measurements, then reports \(U_A=R_A-n_A(n_A+1)/2\), \(U_B=n_An_B-U_A\), the smaller U, both sample sizes and the **two-sided** p-value.

For **\(n_A+n_B\le30\)**, p-values are **exact permutation probabilities conditional on the observed ranks**. Dynamic programming counts all \(\binom{n_A+n_B}{n_A}\) label assignments, with ranks multiplied by two to represent half-integer tied midranks without floating-point ambiguity. The two-sided probability is \(2\min\{P(U_A\le u),P(U_A\ge u)\}\), capped at 1. Thus exact small-sample p-values **also support ties**, but can differ from programs that use a tie-ignorant exact null distribution.

For pooled sizes **greater than 30**, a normal approximation uses mean \(n_An_B/2\), tie-corrected variance
\[
\operatorname{Var}(U_A)=\frac{n_An_B}{12}
\left[N+1-\frac{\sum_t(t^3-t)}{N(N-1)}\right],
\quad N=n_A+n_B,
\]
where \(t\) are pooled tie-block sizes. A **0.5 continuity correction** is applied toward the null mean; the resulting two-sided normal tail is reported, together with the method. A sample in which all pooled observations are identical is rejected.

The test is about **distributions/stochastic ordering**; interpret it as a median shift only when the groups have comparable shapes. Independence and exchangeability under the null are necessary for the exact permutation p-value.

## Wilcoxon signed-rank (\`wilcoxon_signed_rank\`)

Inputs: \`before\`, \`after\` matched by position and of **equal length**. The operation ranks the absolute **after minus before** differences, assigns midranks for tied magnitudes, and uses the **Wilcoxon zero convention**: exact zeros are dropped *before* ranking. At least **two nonzero differences** are required. The result includes total/nonzero/dropped pairs, \(W^+\) (positive rank sum), \(W^-\) (negative), \(\min(W^+,W^-)\), and a two-sided p-value. A paired-difference magnitude outside finite range is rejected.

For **up to 30 nonzero pairs**, exact p-values count all \(2^n\) sign assignments of the observed doubled midranks using a subset-sum dynamic program (conditional exact test even **with ties**). Above 30, the tie-corrected normal variance is
\[
\operatorname{Var}(W^+) =
\frac{n(n+1)(2n+1)}{24}-\frac{\sum_t(t^3-t)}{48},
\quad E[W^+]=n(n+1)/4,
\]
with a **0.5 continuity correction** toward the null mean. Do not interpret this as a generic paired median test unless the paired-difference distribution is **symmetric**; pairs should be independent and matched meaningfully.

## Shapiro–Wilk normality (\`shapiro_wilk\`)

Input: \`sample\`, **3–1024** finite observations (the shared workbench vector limit; Royston's algorithm itself supports up to 5000). All-equal data are rejected. The operation calculates the classical \(W=[\sum_i a_i x_{(i)}]^2/\sum_i(x_i-\bar x)^2\) with antisymmetric weights. The weight construction uses **Royston's AS R94** approximation: Blom normal-order scores \(m_i=\Phi^{-1}[(i-3/8)/(n+1/4)]\), polynomial corrections to the outer one/two weights and normalization of the remaining weights. The ordered observations are centered and scaled before taking squares, reducing cancellation and overflow.

For **\(n=3\)** the significance probability is **exact**, \(p=(6/\pi)[\arcsin(\sqrt W)-\pi/3]\); for \(4\le n\le11\), the short-sample Royston transform and fitted normal parameters are used; for \(n\ge12\), the log-\(n\) Royston approximation applies. This is an **approximate** null p-value for all \(n\ge4\); even a p-value above 0.05 does not prove the measurements are normal. The method assumes an independent continuous sample.

## Pearson and Spearman correlations (\`pearson_correlation\`, \`spearman_correlation\`)

Both accept position-matched numeric vectors \`x\` and \`y\`, with **at least four pairs** to define the requested confidence intervals; unequal lengths or constant variables are rejected. Computation uses centered and range-scaled values and stable sums. All p-values are **two-sided**.

**Pearson** returns the product-moment \(r\), degrees of freedom \(n-2\), and a Student-t null test \(t=r\sqrt{(n-2)/(1-r^2)}\). Its **95% Fisher-z confidence interval** is \(\tanh[\operatorname{atanh}(r)\pm z_{0.975}/\sqrt{n-3}]\). This interval and t test assume independent, approximately bivariate-normal paired observations; extreme outliers and nonlinearity can invalidate interpretation.

**Spearman** returns Pearson correlation computed **on midranks** (correctly incorporating ties). For **up to 8 pairs**, its two-sided p-value is the **exact permutation** proportion conditional on the observed ranks, including ties. For larger paired samples, it uses the usual **Student-t approximation** with \(n-2\) degrees of freedom. Its **approximate 95% confidence interval** uses the *Bonett–Wright (2000)* modified Fisher-z variance \((1+\rho^2/2)/(n-3)\). This interval is a **large-sample approximation**, not an exact small-sample confidence interval; it is least reliable for \(n<10\), heavy ties or nearly perfect correlations. At \(r=\pm1\), the output takes the limiting endpoint convention \([\pm1,\pm1]\), not a finite-sample coverage guarantee. Spearman correlation is about **monotone association**, not a causal relationship.

## Multiplicity correction (\`bonferroni_correction\`, \`benjamini_hochberg_correction\`)

Both operations accept \`p_values\` (one or more valid numbers in **[0, 1]**) and a desired threshold \`alpha\` **strictly between 0 and 1**. Output rows preserve the **input order** and show zero-based hypothesis index, raw p, adjusted p and rejection at the supplied alpha. A count of rejected hypotheses is also returned.

- **Bonferroni:** \(p^{adj}_i=\min(1,mp_i)\). Familywise error control does not require independent tests, provided the input p-values are valid.
- **Benjamini–Hochberg:** sort p-values to \(p_{(1)}\le\cdots\le p_{(m)}\) and calculate \(p^{adj}_{(i)}=\min_{j\ge i}\min(1,mp_{(j)}/j)\), working backward to ensure **monotonic adjusted probabilities**; unsort into the original order. BH controls **false discovery rate** under independent or suitably **positive-regression-dependent** p-values, not arbitrary dependence.

These corrections apply to the **full family of planned tests**; submitting only a selected subset of significant p-values invalidates the declared multiplicity control. They do not repair biased sampling, inappropriate test statistics or violations of assumptions.

## Independent certification cases for the additional tests

\`backend/tests/test_scientific_inference.py\` uses exact NIST StRD data and **15 published significant digits** for each published ANOVA sum of squares, mean square, F, \(R^2\) and residual standard deviation (where NIST supplies that result). The hard-case \`SmLs07\` rows preserve the 13 leading common digits. All three tests compare **formatted published significant digits** (not a loose absolute epsilon):

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

\`\`\`sh
python -m pytest -n0 backend/tests/test_scientific_inference.py backend/tests/test_scientific_fitting.py
\`\`\`
