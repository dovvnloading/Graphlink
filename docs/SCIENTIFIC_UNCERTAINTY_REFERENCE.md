# Measurement Uncertainty: GUM propagation reference

The independent **Measurement Uncertainty** pack (`science_uncertainty`) adds one Scientific Workbench operation, `propagate_uncertainty`. Enable it in **Settings → Plugins**, then choose **Plugins → Scientific Workbench → Measurement Uncertainty**. It uses the standard Scientific Workbench operation/result/receipt protocol (version 1.1.0), requires `graph.read` and `graph.mutate`, and performs all work locally using Python's standard library. The Student *t* quantile is reused from `graphlink_plugins.scientific.packs.fitting` rather than importing a new statistics library.

## Entering a measurement model

The operation exposes **four exact fields**; the common workbench validator requires each one:

- `expression`: a real-valued measurement model as a text expression, for example `lS + d - lS*(deltaAlpha*theta + alphaS*deltaTheta)`.
- `measurements`: a JSON **object** mapping each identifier in the model to an object with required numeric `value` and `uncertainty` (one **standard** uncertainty, not an expanded uncertainty). `degrees_of_freedom` is an optional **positive finite number**; omission means infinite degrees of freedom. No other properties are accepted. All identifiers in the model must appear in this object, and all declared measurements must appear in the model.
- `correlations`: a JSON **array** of objects `{"a":"name1","b":"name2","coefficient":rho}`. Names are distinct input identifiers; the coefficient is a real number in `[-1, 1]`. Each unordered pair can occur at most once; omitted pairs have correlation zero. Use `[]` for independent inputs. Nonzero correlation with a zero-uncertainty input is rejected. The resulting full correlation matrix must be positive semidefinite, not just have individually in-range coefficients.
- `coverage_probability`: a numeric, strictly between 0 and 1, two-sided coverage probability (e.g. `0.95`). This is a *probability*, not a percentage.

A minimal example, with measurements and lengths in **mm**, is:

```json
{
  "expression": "L + d",
  "measurements": "{\"L\":{\"value\":50.000623,\"uncertainty\":0.000025,\"degrees_of_freedom\":18},\"d\":{\"value\":0.000215,\"uncertainty\":0.0000097,\"degrees_of_freedom\":25.6}}",
  "correlations": "[]",
  "coverage_probability": 0.95
}
```

The `measurements` and `correlations` field values above are **JSON-encoded text**, because the existing workbench operation schema has no nested-object input field kind. The UI displays those two fields as editable text; do not supply native objects for them. Numeric inputs are finite binary64; booleans, null, nonfinite numbers, negative standard uncertainties, invalid/duplicate JSON keys and unspecified fields are rejected. At most **16 inputs**, **120 correlation pairs**, **2,048 expression characters** and **256 expression tokens** are allowed. Standard units are supplied by the user; no automatic dimensional analysis or unit conversion occurs.

## Expression grammar and security

The parser in `graphlink_plugins/scientific/packs/uncertainty.py` is a small recursive-descent **arithmetic parser**, not Python's parser. It never calls `eval`, `exec`, interprets code, or accesses the filesystem, imports, objects, or attributes. Supported syntax is:

- Decimal/scientific numeric literals, named ASCII identifiers (`[A-Za-z_][A-Za-z_0-9]*`), parentheses, binary `+ - * / ^` (where `^` is exponentiation) and unary `+ -`.
- Unary functions **only** `sqrt(expr)`, `exp(expr)`, `ln(expr)`, `log10(expr)`, `sin(expr)` and `cos(expr)`. Trigonometric arguments are in **radians**. Function names are reserved and cannot name input variables.
- Powers are right-associative (`2^3^2` is 512). Exponentiation binds more tightly than unary negation (`-2^2` is -4); `2^-2` is 0.25. Multiplication must be written explicitly (`2*x`, not `2x`).

The parser rejects extra tokens, identifiers not declared as measurements, malformed function calls, strings, indexing, attributes, comprehensions, assignments, comments, `**`, and unsupported operators. Nested parentheses/functions are limited to 48 levels. Each model evaluation and each analytic sensitivity must produce a finite **real** result: division by zero, invalid logarithm/square-root domains, complex powers, undefined derivatives and overflow fail validation. A failed calculation cannot replace the last successful workbench result.

## Propagation and analytic sensitivity coefficients

For a scalar measurement model `y = f(x_1, ..., x_N)` evaluated at the supplied best estimates, the operation uses the **first-order** GUM law of propagation of uncertainty, JCGM 100:2008, clauses **5.1.2** and **5.2.2**, Equations (10) and (13):

\[
 c_i = \left.\frac{\partial f}{\partial x_i}\right|_x,\quad
 a_i = c_i u(x_i),\quad
 u_c^2(y) = \sum_i a_i^2 + 2\sum_{i<j}a_i a_j r_{ij}.
\]

The **signed** input contribution is `a_i = c_i*u_i`; its magnitude is the standard-uncertainty contribution. Each input row in the result table reports its value, standard uncertainty, degrees of freedom, sensitivity, signed contribution and diagonal variance term `a_i²`. Each supplied pair adds a row containing the signed covariance term `2*a_i*a_j*r_ij`. These variance terms sum to the total propagated variance. The result `values` also includes `estimate`, `sensitivity_<name>`, `contribution_<name>`, `combined_standard_uncertainty` and input/correlation counts.

Sensitivities are computed **analytically using the chain rule on the bounded arithmetic expression tree**, not by finite differences or subtracting rounded model outputs. For example, `f(x)=x+1e20` has sensitivity 1 even though binary64 evaluation of `f(x+h)-f(x-h)` could round to zero. The derivative rules include the arithmetic operators, powers, and each documented function (`sqrt`, `exp`, `ln`, `log10`, `sin`, `cos`), using radians for trigonometry. Each differentiated path must remain in the finite real domain. Where a derivative is undefined (for example `sqrt(x)` at `x=0`), the operation rejects the calculation rather than reporting an unreliable uncertainty. No arbitrary expressions or Python evaluation are introduced.

Analytic derivatives remove finite-difference step-size and output-subtraction errors, **not** the GUM's assumption of local differentiability and adequate *first-order* linearization. Floating-point overflow/underflow and badly conditioned algebra may still limit the numerical answer; variance accumulation uses scaled signed contributions to reduce avoidable overflow and cancellation. The pack does not automatically establish that a first-order approximation is adequate for the measurement model.

## Degrees of freedom and coverage

For **independent** estimated variance contributions with positive finite degrees of freedom `ν_i`, the operation uses the Welch–Satterthwaite formula, GUM Annex G.4.1:

\[
 \nu_\mathrm{eff} = \frac{u_c^4(y)}{\sum_i (c_i u_i)^4/\nu_i}.
\]

Omitted degrees of freedom are treated as **infinite** and contribute zero to the denominator. When **correlated** inputs are supplied, the general independent-component equation is **not** valid for arbitrary finite-degree-of-freedom covariances. This pack supports a **restricted joint-estimation convention**: inputs connected by nonzero correlations form a block. Within each block, either all degrees of freedom are omitted or each input supplies **the same** finite degrees of freedom, representing **one jointly estimated block variance** (e.g. the five simultaneous observation sets in H.2 imply `ν=4`). The block's full correlated variance is used as a single component in the Welch–Satterthwaite denominator. Distinct blocks are assumed to have independently estimated variances. Conflicting/mixed degrees of freedom within one correlated block are rejected. This convention is only defensible when the user's data really have that joint-sampling structure; for arbitrary correlated Type B/Type A sources it does **not** supply a general coverage theorem. The estimate and combined standard uncertainty can still be computed with omitted degrees of freedom, but the resulting infinite-`ν` coverage model is then a modelling assumption.

The output reports **fractional** `effective_degrees_of_freedom` (or the string `"infinite"` if no finite-variance contribution is present). For finite degrees of freedom, the two-sided coverage factor is `k = t^(-1)((1+p)/2, ν_eff)`, using the fitting pack's Student *t* quantile; the interval is `estimate ± U`, where `U = k*u_c`. For large finite `ν_eff > 10^7`, a first-order large-`ν` Student *t* expansion avoids numerical loss of accuracy; infinite `ν` uses the normal limit. The returned `coverage_factor`, `expanded_uncertainty`, `coverage_lower` and `coverage_upper` all use the caller's selected `coverage_probability`. This is an **approximate symmetric coverage interval**, not a guarantee of physical coverage. GUM Annex G often **truncates** `ν_eff` when selecting tabled coverage factors; this pack reports and uses its untruncated numerical value so callers can reproduce or conservatively floor it themselves.

## Published reference checks: JCGM 100 Annex H

The oracle data and exact input observation rows are in `backend/tests/test_scientific_uncertainty_gum.json`; tests are in `backend/tests/test_scientific_uncertainty.py`.

**H.1, end-gauge calibration (Annex H.1.2–H.1.6):** With lengths in mm, the first-order model is `lS + d - lS*(deltaAlpha*theta + alphaS*deltaTheta)`. Use `lS=50.000623`, `d=0.000215`, `alphaS=11.5e-6`, `theta=-0.1`, `deltaAlpha=0`, `deltaTheta=0`; standard uncertainties are `25e-6`, `9.7e-6`, `1.2e-6`, `0.41`, `1e-6/sqrt(3)` and `0.05/sqrt(3)` in the corresponding input units. Finite degrees of freedom are 18 (standard), 25.6 (length difference), 50 (expansion-coefficient difference), and 2 (temperature difference); the other two are treated as infinite. The test checks **50.000838 mm**, **32 nm** combined standard uncertainty and floor(`ν_eff`) **16**, as reported in H.1.5/H.1.6, as well as approximately **25, 9.7, 2.9 and 16.6 nm** individual contributions in Table H.1. The `sqrt(3)` formulas deliberately retain the extra digits underlying the GUM's rounded `0.58 × 10^-6` and `0.029` values. **Important:** H.1.7 explicitly adds second-order effects which increase the total to **34 nm**. The pack intentionally returns the **first-order 32 nm** value; it does *not* implement the H.1.7 correction.

**H.2, simultaneous resistance/reactance/impedance (Annex H.2.2–H.2.4):** The fixture retains all five simultaneous observation sets from Table H.2. Means, standard uncertainties of the means and **sample correlation coefficients** are independently computed from those observation rows (not taken from the pack's output). With `V` in volts, `I` in milliamperes and `phi` in radians, use `R = 1000*V/I*cos(phi)`, `X = 1000*V/I*sin(phi)` and `Z = 1000*V/I`, which yield ohms. The three pairwise correlations are approximately **−0.36**, **+0.86** and **−0.65**; each participating input has joint degrees of freedom **4**. The published Table H.3 values are **R = 127.732 Ω, u(R) = 0.071 Ω; X = 219.847 Ω, u(X) = 0.295 Ω; Z = 254.260 Ω, u(Z) = 0.236 Ω**. The numerical first-order operation agrees to the stated precision for the estimates and within **0.001 Ω** for the standard uncertainties. For `X`, direct first-order propagation of the *printed input rows* gives about **0.2956 Ω**, while the H.3/H.4 printed figure is **0.295 Ω**; the GUM explicitly warns at the start of Annex H that intermediate digits retained for published calculations can differ from those recoverable from rounded figures. The tests also compare results with correlations removed against Table H.5 (**0.195, 0.201, 0.204 Ω**) to prevent accidentally omitting covariance terms.

Source: [JCGM 100:2008, *Evaluation of measurement data — Guide to the expression of uncertainty in measurement*](https://doi.org/10.59161/JCGM100-2008E), Sections 5–6 and Annexes G and H (Tables H.1–H.5).

## Assumptions and limits

The model must represent a **scalar measurand** with consistent units and input estimates whose standard uncertainties and correlations have already been evaluated. The operation does **not** estimate those input distributions, correct systematic effects, compute correlations from raw observations, provide a full output covariance matrix for multiple measurands, or perform second-order/Monte Carlo propagation. Run separate operations for multiple outputs. No guarantees are made for nonlinear, non-Gaussian, skewed, heavy-tailed, discontinuous or badly scaled models; symmetric Student *t* intervals are approximations requiring suitable statistical justification. A nondifferentiable model at the estimate should trigger a reconsideration of the model or a more appropriate propagation method; no finite-difference step can repair the first-order assumption. Physical measurement claims should report model assumptions, input units, coverage probability, degrees of freedom and the method used to determine each input's standard uncertainty.

## Reproduce

From a checkout with the backend's Python dependencies installed:

```sh
python -m pytest -n0 backend/tests/test_scientific_uncertainty.py
python -m pytest -n0 backend/tests/test_scientific_workbench.py backend/tests/test_scientific_fitting.py
```

The general `test_scientific_workbench.py` suite discovers packs from the same catalog and plugin directory conventions, so it checks the new manifest, grants, example run, provenance and saved result integration without needing changes to existing tests.
