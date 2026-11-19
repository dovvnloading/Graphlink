# Numerical Methods — Scientific Workbench reference

The **Numerical Methods** pack (plugin ID: science_numerics) is a separate, explicitly granted Scientific Workbench plugin, like Fitting and Measurement Uncertainty. Enable it in Settings → Plugins and select it in Plugins → Scientific Workbench. Its manifest uses SDK API 1, generic frontend, passive workbench node persistence and graph.read / graph.mutate grants. The pack uses the existing scientific workbench receipt, undo and export framework (pack version 1.2.0). It adds **no external runtime dependencies**.

## Model input and security

All scalar root expressions, quadrature integrands, and vector ODE right-hand sides use the *existing bounded recursive-descent parser and arithmetic AST evaluator* from graphlink_plugins.scientific.packs.uncertainty. There is no Python eval, exec, import from model input, or new expression language. The grammar supports finite real numeric literals, named identifiers, parentheses, +, -, *, / and ^ (exponentiation), plus sqrt, exp, ln, log10, sin and cos (radian arguments). Python syntax, attribute access, indexing, unrecognized functions, undeclared variables and malformed expressions are rejected. Existing limits include 2,048 characters, 256 tokens and 48 levels of nesting per expression; evaluation must remain in the finite real domain.

The shared workbench validator requires **every listed field**. Nested data such as ODE expressions and parameters must be provided as JSON-encoded **text strings**, not as JSON objects in the outer operation inputs. Reuse of the uncertainty pack's strict JSON decoder rejects duplicate keys and nonfinite numbers. The parameters field accepts a JSON object of up to eight finite real numeric constants (for example, {"mu":1000} or {}); their names may not shadow independent variables, state variables or reserved functions. Each individual ODE component expression may reference only the variables it needs.

## Initial-value ODE solvers

The operations **solve_ivp_rk45** and **solve_ivp_implicit** take identical fields:

| Field | Input |
| --- | --- |
| rhs_expressions | JSON array **text** containing one expression for each state derivative; variables t, y0 through y7 and named parameters |
| initial_values | Numeric vector of 1–8 initial states |
| parameters | JSON object **text** of constant parameters, e.g. {} |
| start_time, end_time | Distinct finite endpoints; forward and backward integration supported |
| relative_tolerance, absolute_tolerance | Nonnegative finite numbers, at least one strictly positive |
| max_steps | Integer from 1 to 20,000, counting accepted and rejected attempted steps |

Component local error is normalized using absolute_tolerance + relative_tolerance × max(abs(old_state), abs(new_state)); the maximum normalized component error must be ≤ 1 before a step is accepted. Positive relative tolerances below 100 binary64 machine epsilons (~2.22e-14) are rejected; solver RHS evaluations are also capped at 200,000. A failed tolerance request, step underflow, nonfinite model evaluation, unsolved Newton stage or exhausted budget raises a descriptive failure rather than storing a successful result. The workbench preserves the previous successful artifact on failure.

**solve_ivp_rk45** implements the seven-stage embedded **Dormand–Prince fifth-order / fourth-order (5(4))** pair and adapts its step size based on the embedded local error (controller exponent 1/5). This is intended for nonstiff problems.

**solve_ivp_implicit** implements the **two-stage L-stable second-order SDIRK** method, gamma = 1 − 1/sqrt(2), with adaptive step doubling. Each implicit stage uses a damped Newton solve with the safe expression AST's **analytic state Jacobian**, and a pivoted dense solver for at most eight components. The estimated local error of the two-half-step solution is (two_half_steps − full_step) / 3; its controller exponent is 1/3. This method handles stiff problems but does not automatically switch algorithms or promise convergence for all models.

Both successful operations return final_y0, final_y1, etc.; accepted_steps, rejected_steps, rhs_evaluations, jacobian_evaluations, linear_solves, estimated_max_local_error, estimated_last_local_error, max_accepted_error_ratio and tolerance_met. A bounded trajectory table and plot sample the solution; decimation affects only display, never the computed final state. The error estimates are **local truncation diagnostics, not guaranteed global error bounds**.

Example outer workbench inputs, exponential decay from time 0 to 8 (exact final state exp(-4) = 0.01831563888873418):

| Field | Value |
| --- | --- |
| rhs_expressions | JSON text array containing the single expression -0.5*y0 |
| initial_values | [1] |
| parameters | {} (as text) |
| start_time / end_time | 0 / 8 |
| relative_tolerance / absolute_tolerance | 1e-9 / 1e-12 |
| max_steps | 10000 |

## Brent root finding

**brent_root** accepts expression (in x), parameters (JSON object text), lower and upper finite bounds with lower < upper, relative_tolerance, absolute_tolerance, and max_evaluations (integer 1–5,000). Endpoint values must be of opposite sign, unless one is exactly zero. Safeguarded inverse-quadratic or secant interpolation falls back to bisection. Success requires either an exactly zero model evaluation or a final bracket width ≤ absolute_tolerance + relative_tolerance × abs(root).

The result includes root, estimated_absolute_error (conservative final bracket width), bracket_width, absolute_residual, iterations, function_evaluations (including endpoints) and tolerance_met. Nonbracketing input, evaluation budget exhaustion or a bracket too small to shrink in binary64 is a clear error. For example, cos(x)-x on [0,1] has a root near 0.7390851332151607. An even-multiplicity root without sign change is not guaranteed to be found.

## Adaptive Gauss–Kronrod quadrature

**gauss_kronrod_integral** accepts expression in x, parameters (JSON object text), lower, upper, relative_tolerance, absolute_tolerance and max_evaluations (integer 1–100,000). It evaluates a Kronrod-15 / embedded Gauss-7 pair, repeatedly bisecting the interval with largest error estimate. The algorithm sums the local error estimates and stops once the sum ≤ absolute_tolerance + relative_tolerance × abs(integral). Its QUADPACK-style error estimate rescales the embedded difference and includes a floating-point roundoff floor. Each initial interval costs 15 evaluations, and each subdivision 30 more. Integration with reversed bounds returns the negative integral; equal endpoints give zero integral, zero error and zero evaluations.

Results include integral, estimated_absolute_error, function_evaluations, subdivisions, intervals and tolerance_met. If a requested tolerance cannot be met before the budget or precision limit, the operation fails. **An embedded-rule error estimate is heuristic**, not a rigorous error bound for arbitrary integrands. Singularities, discontinuities, cancellation and unresolved oscillations need special care. A known example is the integral of sin(x) from zero to pi, equal to 2.

## Natural and clamped cubic splines

**cubic_spline** takes x and y numeric vectors of equal size (2–1,024 knots), numeric queries, a boundary text field ("natural" or "clamped"), and numeric left_slope and right_slope (both required; ignored for natural splines). Knots must be strictly increasing; queries outside the knot interval are rejected, not extrapolated.

Natural splines set zero second derivative at each endpoint; clamped splines enforce the supplied endpoint first derivatives. A tridiagonal system produces piecewise cubic C2 interpolation. The output table gives each requested position, interpolated value and first derivative. Diagnostics include knots, intervals, evaluations, tridiagonal_solves, max_knot_residual and estimated_roundoff_error. For an **unknown underlying function**, the actual interpolation error cannot be established without further assumptions (e.g. a fourth-derivative bound), so underlying_function_error explicitly reports **unknown**; no interpolation accuracy tolerance is falsely claimed. A clamped cubic reproduces a cubic polynomial when its endpoint slopes are exact.

## Known-answer validation

The new tests at backend/tests/test_scientific_numerics.py validate the plugin manifest, registration, examples and checkable workbench receipts, the safe expression restrictions, explicit failure cases and independent reference values:

| Problem | Specified tolerances | Reference and comparison |
| --- | --- | --- |
| Exponential decay y'=-0.5y, t=8 | RK45 rtol=1e-9, atol=1e-12 | exp(-4), absolute difference ≤ 2e-10 |
| Harmonic oscillator x'=v, v'=-x, t=20π | RK45 rtol=2e-9, atol=2e-11 | (1,0), absolute difference ≤ 2e-7 |
| Van der Pol, mu=3, t=10 | RK45 rtol=1e-8, atol=1e-10 | (1.7508147160418,-0.270994439452338), absolute difference ≤ 2e-6 |
| Stiff Van der Pol, mu=1000, t=10 | SDIRK2 rtol=1e-5, atol=1e-8 | (1.99331492756978,-0.000670403793877684), within 8e-4 / 8e-6 |
| Robertson stiff kinetics, t=10000 | SDIRK2 rtol=1e-5, atol=1e-9 | (0.10730042853784,4.80016697257274e-7,0.892699091445467), main species within 1e-3 and conserved sum ≈1 |
| cos(x)-x root on [0,1] | Brent rtol=atol=1e-12 | 0.7390851332151607 |
| Integrals of sin(x) on [0,pi], x^4 on [-1,2] | For sine rtol=1e-10, atol=1e-11 | 2 and 33/5 |
| Natural linear and clamped cubic interpolation | Knot/roundoff residuals | Exact linear results and cubic x^3-2x^2+x with exact endpoint slopes |

The Van der Pol and Robertson reference states are independently calculated high-precision Radau values embedded as constants; SciPy is **not** imported by this pack or its tests. Actual accuracy relative to these references is checked separately from the adaptive estimators.

Reproduce from the repo root after installing development test dependencies:

    python -m pytest -n0 backend/tests/test_scientific_numerics.py
    python -m pytest -n0 backend/tests/test_scientific_workbench.py backend/tests/test_scientific_uncertainty.py backend/tests/test_scientific_fitting.py


## Numerical-range audit cases (2026-11-18)

Implicit Newton convergence uses each component's **actual requested**
absolute-plus-relative tolerance, without imposing a separate 1e-30
residual floor. In particular, y'=-y with y(0)=1e-40 and zero-sized absolute
tolerance relative to the state should decay rather than silently remain
unchanged; the test suite uses a positive 1e-60 absolute tolerance.
The Gauss–Kronrod rule scales each term before accumulation so the finite
integral of f(x)=1e308 over [0,1] does not overflow on intermediate paired
sums. Spline evaluation likewise avoids squaring a very wide knot interval
when the final interpolated value is finite. Non-representable outputs
remain outside the supported binary64 result range.
