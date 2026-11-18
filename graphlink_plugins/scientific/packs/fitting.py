"""Reproducible least-squares fits and frequentist tests, without external solvers."""

from __future__ import annotations

import math
import statistics
from decimal import Decimal, InvalidOperation, localcontext

from graphlink_plugins.scientific.core import Field, Operation

# Working precision protects strongly correlated regressors (NIST Longley) and
# the covariance/uncertainty calculation after nonlinear convergence.
_PRECISION = 76
_PIVOT_FLOOR = Decimal("1e-36")


def _d(value):
    # Scientific inputs arrive as validated binary64. str(float) recovers the
    # decimal measurements entered by the user, including the NIST data.
    return Decimal(str(value))


def _as_float(value, description):
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{description} is outside the supported numeric range.") from None
    if not math.isfinite(result) or (value != 0 and result == 0):
        raise ValueError(f"{description} is outside the supported numeric range.")
    return result


def _normal_solve(gram, rhs, *, inverse=False):
    """Equilibrated, pivoted Gauss-Jordan solve; optionally recover G inverse.

    Normal equations are evaluated in high-precision decimal, *not* in
    binary64. Scaling by sqrt(G_jj) prevents differing parameter units from
    deciding which pivot is numerically detectable.
    """
    p = len(rhs)
    scales = [gram[j][j].sqrt() for j in range(p)]
    if any(v <= 0 for v in scales):
        raise ValueError("The fit has a non-identifiable parameter (zero derivative).")
    rows = [
        [gram[i][j] / (scales[i] * scales[j]) for j in range(p)]
        + [rhs[i] / scales[i]]
        + ([Decimal(int(i == j)) for j in range(p)] if inverse else [])
        for i in range(p)
    ]
    for k in range(p):
        pivot = max(range(k, p), key=lambda i: abs(rows[i][k]))
        if abs(rows[pivot][k]) < _PIVOT_FLOOR:
            raise ValueError("The fit is rank deficient or numerically unidentifiable.")
        rows[pivot], rows[k] = rows[k], rows[pivot]
        divisor = rows[k][k]
        rows[k] = [element / divisor for element in rows[k]]
        for i in range(p):
            if i == k:
                continue
            factor = rows[i][k]
            if factor:
                rows[i] = [a - factor * b for a, b in zip(rows[i], rows[k])]
    solution = [rows[i][p] / scales[i] for i in range(p)]
    if not inverse:
        return solution
    covariance_base = [[rows[i][p + 1 + j] / (scales[i] * scales[j]) for j in range(p)] for i in range(p)]
    # Use the symmetric estimate to suppress insignificant elimination asymmetry.
    covariance_base = [[(covariance_base[i][j] + covariance_base[j][i]) / 2 for j in range(p)] for i in range(p)]
    return solution, covariance_base


def _linear(data):
    matrix, y, weights = data["predictors"], data["response"], data["weights"]
    n = len(y)
    if len(matrix) != n or len(weights) != n:
        raise ValueError("Provide one predictor row, response and weight per observation.")
    p = len(matrix[0]) + 1
    if p > 16:
        raise ValueError("At most 15 predictors plus an intercept are supported.")
    if any(w < 0 for w in weights):
        raise ValueError("Least-squares weights must be nonnegative.")
    positive = sum(w > 0 for w in weights)
    if positive <= p:
        raise ValueError("More positive-weight observations than fitted parameters are required.")
    try:
        with localcontext() as context:
            context.prec = _PRECISION
            x = [[Decimal(1)] + list(map(_d, row)) for row in matrix]
            ys, ws = list(map(_d, y)), list(map(_d, weights))
            gram = [[sum((w * row[i] * row[j] for row, w in zip(x, ws)), Decimal(0))
                     for j in range(p)] for i in range(p)]
            rhs = [sum((w * row[i] * yy for row, yy, w in zip(x, ys, ws)), Decimal(0)) for i in range(p)]
            beta, inverse = _normal_solve(gram, rhs, inverse=True)
            residuals = [yy - sum((v * b for v, b in zip(row, beta)), Decimal(0))
                         for row, yy in zip(x, ys)]
            sse = sum((w * r * r for w, r in zip(ws, residuals)), Decimal(0))
            dof = positive - p
            reduced = sse / dof
            covariance = [[reduced * value for value in row] for row in inverse]
            errors = [max(covariance[j][j], Decimal(0)).sqrt() for j in range(p)]
            mean = sum((w * yy for w, yy in zip(ws, ys)), Decimal(0)) / sum(ws)
            total = sum((w * (yy - mean) ** 2 for yy, w in zip(ys, ws)), Decimal(0))
            values = {
                "observations": n, "positive_weight_observations": positive,
                "parameters": p, "degrees_of_freedom": dof,
                "weighted_residual_sum_squares": _as_float(sse, "Weighted residual sum of squares"),
                "reduced_chi_square": _as_float(reduced, "Reduced chi-square"),
                "residual_standard_deviation": _as_float(reduced.sqrt(), "Residual standard deviation"),
            }
            if total:
                values["r_squared"] = _as_float(Decimal(1) - sse / total, "R squared")
            for i, (value, error) in enumerate(zip(beta, errors)):
                values[f"coefficient_{i}"] = _as_float(value, "Coefficient")
                values[f"standard_error_{i}"] = _as_float(error, "Standard error")
            for i, residual in enumerate(residuals):
                values[f"residual_{i}"] = _as_float(residual, "Residual")
            return {
                "summary": "Weighted linear least squares with an intercept; weights are relative inverse variances. "
                           "Covariance is scaled by weighted residual sum of squares / residual degrees of freedom.",
                "values": values,
                "table": {"columns": ["parameter"] + [f"beta_{i}" for i in range(p)],
                          "rows": [[f"beta_{i}"] + [_as_float(v, "Covariance") for v in row]
                                   for i, row in enumerate(covariance)]},
                "plot": {"title": "Weighted linear fit residuals", "xLabel": "Observation index (zero-based)",
                         "yLabel": "Observed minus fitted", "x": list(range(n)),
                         "y": [_as_float(r, "Residual") for r in residuals]},
            }
    except (InvalidOperation, OverflowError, ZeroDivisionError):
        raise ValueError("The regression exceeds the supported numerical range.") from None



def _one_minus_exp_neg(z):
    """Compute 1 - exp(-z) without losing a small nonzero z to cancellation."""
    if abs(z) > Decimal("1e-8"):
        return Decimal(1) - (-z).exp()
    term = total = z
    for k in range(2, 128):
        term *= -z / k
        updated = total + term
        if updated == total:
            return total
        total = updated
    return total


def _model(name, x, b):
    """Return model value and its analytic derivative w.r.t. parameters."""
    one = Decimal(1)
    if name == "exponential":             # a exp(b x) + c
        a, rate, baseline = b
        e = (rate * x).exp()
        return a * e + baseline, [e, a * x * e, one]
    if name == "saturating_exponential":  # NIST Misra1a
        a, rate = b
        z = rate * x
        e = (-z).exp()
        difference = _one_minus_exp_neg(z)
        return a * difference, [difference, a * x * e]
    if name == "power_law":               # a x^b, x positive
        if x <= 0:
            raise ValueError("Power-law fitting requires strictly positive x values.")
        a, exponent = b
        lx = x.ln()
        power = (exponent * lx).exp()
        return a * power, [power, a * power * lx]
    if name == "gaussian":                # baseline + amplitude exp(-(x-center)^2/(2 width^2))
        baseline, amplitude, center, width = b
        if not width:
            raise ValueError("Gaussian width must be nonzero.")
        z = (x - center) / width
        e = (-z * z / 2).exp()
        f = baseline + amplitude * e
        return f, [one, e, amplitude * e * z / width, amplitude * e * z * z / width]
    if name == "polynomial":
        terms = [one]
        for _ in range(1, len(b)):
            terms.append(terms[-1] * x)
        return sum((v * t for v, t in zip(b, terms)), Decimal(0)), terms
    if name == "thurber":                 # NIST rational cubic / cubic
        powers = [one, x, x * x, x * x * x]
        numerator = sum((b[j] * powers[j] for j in range(4)), Decimal(0))
        denominator = one + sum((b[j + 3] * powers[j] for j in range(1, 4)), Decimal(0))
        if not denominator:
            raise ValueError("Rational denominator is zero at an observation.")
        f = numerator / denominator
        return f, [v / denominator for v in powers] + [-f * v / denominator for v in powers[1:]]
    if name == "mgh09":                  # NIST Kowalik-Osborne rational model
        xx = x * x
        denominator = xx + x * b[2] + b[3]
        if not denominator:
            raise ValueError("Rational denominator is zero at an observation.")
        numerator = xx + x * b[1]
        f = b[0] * numerator / denominator
        return f, [numerator / denominator, b[0] * x / denominator,
                   -f * x / denominator, -f / denominator]
    raise ValueError("Unknown nonlinear fitting model.")


_MODEL_PARAMS = {"exponential": 3, "saturating_exponential": 2, "power_law": 2,
                 "gaussian": 4, "thurber": 7, "mgh09": 4}


def _objective(model, x, y, w, b, *, jacobian=False):
    predictions, derivatives = [], []
    for xx in x:
        prediction, derivative = _model(model, xx, b)
        predictions.append(prediction)
        if jacobian:
            derivatives.append(derivative)
    residuals = [yy - prediction for yy, prediction in zip(y, predictions)]
    sse = sum((ww * r * r for ww, r in zip(w, residuals)), Decimal(0))
    if not jacobian:
        return sse
    p = len(b)
    gram = [[sum((ww * row[i] * row[j] for ww, row in zip(w, derivatives)), Decimal(0))
             for j in range(p)] for i in range(p)]
    grad = [sum((ww * row[i] * r for ww, row, r in zip(w, derivatives, residuals)), Decimal(0))
            for i in range(p)]
    return sse, gram, grad, residuals


def _nonlinear(data):
    model = data["model"].strip().lower()
    initial = data["initial_parameters"]
    p = len(initial)
    if model not in _MODEL_PARAMS and model != "polynomial":
        raise ValueError("Choose exponential, saturating_exponential, power_law, gaussian, polynomial, thurber or mgh09.")
    if (model in _MODEL_PARAMS and p != _MODEL_PARAMS[model]) or (model == "polynomial" and not 1 <= p <= 12):
        raise ValueError("The initial parameter count does not match the selected model.")
    xs, ys, weights = data["x"], data["y"], data["weights"]
    n = len(xs)
    if n != len(ys) or n != len(weights):
        raise ValueError("Provide equally many x, y and weight observations.")
    if any(w < 0 for w in weights):
        raise ValueError("Least-squares weights must be nonnegative.")
    positive = sum(w > 0 for w in weights)
    if positive <= p:
        raise ValueError("More positive-weight observations than parameters are required.")
    if model == "power_law" and any(x <= 0 for x in xs):
        raise ValueError("Power-law fitting requires strictly positive x values.")
    if model == "gaussian" and initial[3] == 0:
        raise ValueError("Gaussian width must be nonzero.")
    try:
        with localcontext() as context:
            context.prec = _PRECISION
            x, y, w, beta = list(map(_d, xs)), list(map(_d, ys)), list(map(_d, weights)), list(map(_d, initial))
            damping = Decimal("0.001")
            accepted = rejected = 0
            converged = False
            for iteration in range(1, 1201):
                sse, gram, grad, residuals = _objective(model, x, y, w, beta, jacobian=True)
                # Stop only when the predicted change is negligible at a
                # precision much higher than the certified 10-11 digits.
                step = None
                next_sse = None
                for _ in range(55):
                    modified = [row.copy() for row in gram]
                    for j in range(p):
                        modified[j][j] += damping * max(gram[j][j], Decimal("1e-70"))
                    try:
                        step = _normal_solve(modified, grad)
                        candidate = [b + db for b, db in zip(beta, step)]
                        next_sse = _objective(model, x, y, w, candidate)
                    except (ValueError, InvalidOperation, OverflowError, ZeroDivisionError):
                        next_sse = None
                    if next_sse is not None and next_sse < sse:
                        break
                    rejected += 1
                    damping *= 10
                else:
                    # If no decrease is representable even with high working
                    # precision, this is convergence only for a tiny step.
                    if step is not None and max(abs(db) / max(abs(b), Decimal(1))
                                                for b, db in zip(beta, step)) < Decimal("1e-22"):
                        converged = True
                        break
                    raise ValueError("Levenberg–Marquardt failed to find a decreasing step.")
                accepted += 1
                relative_step = max(abs(db) / max(abs(b), Decimal(1)) for b, db in zip(beta, step))
                relative_improvement = (sse - next_sse) / max(sse, Decimal(1))
                beta = candidate
                damping = max(damping / 3, Decimal("1e-35"))
                if relative_step < Decimal("1e-21") and relative_improvement < Decimal("1e-30"):
                    converged = True
                    break
            if not converged:
                raise ValueError("Levenberg–Marquardt did not converge within 1200 iterations.")
            sse, gram, grad, residuals = _objective(model, x, y, w, beta, jacobian=True)
            _, inverse = _normal_solve(gram, [Decimal(0)] * p, inverse=True)
            dof = positive - p
            reduced = sse / dof
            errors = [(max(inverse[j][j] * reduced, Decimal(0))).sqrt() for j in range(p)]
            # Local linearized Wald intervals use Student t for the residual
            # degrees of freedom, not a large-sample Gaussian quantile.
            critical95 = _d(_student_t_ppf(0.975, dof))
            values = {
                "observations": n, "parameters": p, "degrees_of_freedom": dof,
                "iterations": iteration, "accepted_steps": accepted, "rejected_steps": rejected,
                "converged": "true", "final_damping": _as_float(damping, "Damping"),
                "weighted_residual_sum_squares": _as_float(sse, "Residual sum of squares"),
                "reduced_chi_square": _as_float(reduced, "Reduced chi-square"),
                "residual_standard_deviation": _as_float(reduced.sqrt(), "Residual standard deviation"),
                "gradient_infinity_norm": _as_float(max(map(abs, grad)), "Gradient"),
            }
            rows = []
            for j, (b, error) in enumerate(zip(beta, errors)):
                values[f"parameter_{j}"] = _as_float(b, "Parameter")
                values[f"standard_error_{j}"] = _as_float(error, "Standard error")
                low, high = b - critical95 * error, b + critical95 * error
                values[f"ci95_lower_{j}"] = _as_float(low, "Confidence limit")
                values[f"ci95_upper_{j}"] = _as_float(high, "Confidence limit")
                rows.append([f"beta_{j}", values[f"parameter_{j}"], values[f"standard_error_{j}"],
                             values[f"ci95_lower_{j}"], values[f"ci95_upper_{j}"]])
            return {
                "summary": f"Levenberg–Marquardt {model} fit converged in {iteration} iterations. "
                           "95% intervals use the local linearized covariance and Student t quantile.",
                "values": values,
                "table": {"columns": ["parameter", "estimate", "standard error", "95% lower", "95% upper"], "rows": rows},
                "plot": {"title": "Nonlinear fit residuals", "xLabel": "Predictor x", "yLabel": "Observed minus fitted",
                         "x": xs, "y": [_as_float(r, "Residual") for r in residuals]},
            }
    except (InvalidOperation, OverflowError, ZeroDivisionError):
        raise ValueError("The nonlinear fit exceeds the supported numerical range.") from None


def _gamma_q(shape, x):
    """Regularized upper incomplete gamma, with series/continued fractions."""
    if not math.isfinite(shape) or not math.isfinite(x) or shape <= 0 or x < 0:
        raise ValueError("Gamma distribution requires positive shape and nonnegative argument.")
    if x == 0:
        return 1.0
    pref_log = shape * math.log(x) - x - math.lgamma(shape)
    epsilon = 4e-15
    if x < shape + 1:
        term = total = 1.0 / shape
        for k in range(1, 10001):
            term *= x / (shape + k)
            total += term
            if abs(term) <= abs(total) * epsilon:
                result_log = pref_log + math.log(total)
                return max(0.0, min(1.0, -math.expm1(min(0.0, result_log))))
    else:
        tiny = 1e-300
        b = x + 1 - shape
        c = 1 / tiny
        d = 1 / (b if abs(b) > tiny else tiny)
        h = d
        for k in range(1, 10001):
            a = -k * (k - shape)
            b += 2
            d = a * d + b
            if abs(d) < tiny:
                d = tiny
            c = b + a / c
            if abs(c) < tiny:
                c = tiny
            d = 1 / d
            factor = d * c
            h *= factor
            if abs(factor - 1) <= epsilon:
                return max(0.0, min(1.0, math.exp(pref_log) * h))
    raise ValueError("The incomplete gamma computation did not converge.")


def _beta_cf(a, b, x):
    """Modified Lentz continued fraction for incomplete beta."""
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1)
    if abs(d) < tiny:
        d = tiny
    d = 1 / d
    h = d
    for m in range(1, 10001):
        aa = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        d = 1 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        h *= d * c
        aa = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        factor = d * c
        h *= factor
        if abs(factor - 1) < 4e-15:
            return h
    raise ValueError("The incomplete beta computation did not converge.")


def _beta_i(a, b, x):
    if not all(math.isfinite(v) for v in (a, b, x)) or a <= 0 or b <= 0 or not 0 <= x <= 1:
        raise ValueError("Incomplete beta requires positive shapes and x in [0, 1].")
    if x == 0:
        return 0.0
    if x == 1:
        return 1.0
    if x > (a + 1) / (a + b + 2):
        return max(0.0, min(1.0, 1 - _beta_i(b, a, 1 - x)))
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    return max(0.0, min(1.0, front * _beta_cf(a, b, x) / a))


def _normal_cdf(x):
    return math.erfc(-x / math.sqrt(2)) / 2


def _student_t_cdf(t, df):
    if not math.isfinite(t) or not math.isfinite(df) or df <= 0:
        raise ValueError("Student t distribution requires finite t and positive degrees of freedom.")
    # Near t=0, df/(df+t*t) rounds to 1 and destroys all digits
    # of the central probability. Evaluate the complementary beta
    # parameterization directly rather than subtracting nearly equal values.
    if abs(t) <= 1:
        x = t * t / (df + t * t)
        half = _beta_i(0.5, df / 2, x) / 2
        return 0.5 - half if t < 0 else 0.5 + half
    squared = t * t
    if math.isfinite(squared):
        # Ratios avoid overflowing the denominator df + t².
        x = 1 / (1 + squared / df) if df >= squared else (df / squared) / (1 + df / squared)
    else:
        # t² can overflow although the Student-t tail is representable.
        # Evaluate the beta argument via ratios before falling back to its
        # small-x leading term in log space.
        magnitude = abs(t)
        scaled = df / magnitude
        x = scaled / (magnitude + scaled)
    if x:
        half = _beta_i(df / 2, 0.5, x) / 2
    else:
        a = df / 2
        # For x too small for binary64, I_x(a, 1/2) ~
        # x^a / (a * B(a, 1/2)). The next term is negligible.
        log_x = math.log(df) - 2 * math.log(abs(t))
        log_half = (math.lgamma(a + 0.5) - math.lgamma(a) - math.lgamma(0.5)
                    - math.log(2 * a) + a * log_x)
        half = math.exp(log_half)
    return half if t < 0 else 1 - half


def _student_t_ppf(probability, df):
    """Monotone inversion of the stable t CDF for two-sided intervals."""
    if not 0 < probability < 1 or not math.isfinite(df) or df <= 0:
        raise ValueError("A Student t quantile needs probability in (0, 1) and positive df.")
    if probability == 0.5:
        return 0.0
    # Never form 1 - p for an extreme lower-tail probability: e.g.
    # 1 - 1e-20 rounds to 1 in binary64, although the quantile is finite.
    # Invert the negative-t tail directly, whose CDF uses the stable beta tail.
    lower_tail = min(probability, 1 - probability)
    lo, hi = 0.0, 1.0
    while _student_t_cdf(-hi, df) > lower_tail:
        hi *= 2
        if not math.isfinite(hi):
            raise ValueError("Student t quantile exceeds the supported range.")
    for _ in range(90):
        midpoint = (lo + hi) / 2
        if _student_t_cdf(-midpoint, df) > lower_tail:
            lo = midpoint
        else:
            hi = midpoint
    magnitude = (lo + hi) / 2
    return -magnitude if probability < 0.5 else magnitude


def _chi_square_cdf(x, df):
    return 1 - _gamma_q(df / 2, x / 2)


def _welch(data):
    a, b = data["sample_a"], data["sample_b"]
    if len(a) < 2 or len(b) < 2:
        raise ValueError("Welch's test requires at least two observations in each sample.")
    mean_a, mean_b = statistics.mean(a), statistics.mean(b)
    v_a = statistics.variance(a) / len(a)
    v_b = statistics.variance(b) / len(b)
    scale = v_a + v_b
    if scale <= 0 or not math.isfinite(scale):
        raise ValueError("Welch's test needs at least one sample with positive variance.")
    t = (mean_a - mean_b) / math.sqrt(scale)
    if not math.isfinite(t):
        raise ValueError("The test statistic exceeds the supported numeric range.")
    # Normalized variance contributions avoid underflow of (small variance)^2.
    wa, wb = v_a / scale, v_b / scale
    df = 1 / (wa * wa / (len(a) - 1) + wb * wb / (len(b) - 1))
    if not math.isfinite(df) or df <= 0:
        raise ValueError("Welch degrees of freedom cannot be evaluated for these samples.")
    p = (1 - _beta_i(0.5, df / 2, t * t / (df + t * t))) if abs(t) <= 1 else (
        _beta_i(df / 2, 0.5, df / (df + t * t)) if math.isfinite(t * t) else 0.0)
    return {
        "summary": "Two-sided Welch independent-samples t test with unequal variances and Satterthwaite degrees of freedom.",
        "values": {"mean_a": mean_a, "mean_b": mean_b, "t_statistic": t, "degrees_of_freedom": df,
                   "p_value": p, "sample_size_a": len(a), "sample_size_b": len(b)},
    }


def _chi_square(data):
    observed, expected = data["observed"], data["expected"]
    if len(observed) != len(expected) or len(observed) < 2:
        raise ValueError("Provide at least two corresponding observed and expected categories.")
    if any(v < 0 or not v.is_integer() for v in observed) or any(v <= 0 for v in expected):
        raise ValueError("Observed counts must be nonnegative integers and expectations strictly positive.")
    observed_total = math.fsum(observed)
    if observed_total <= 0:
        raise ValueError("Pearson goodness of fit requires a positive observed total.")
    if not math.isclose(observed_total, math.fsum(expected), rel_tol=1e-12, abs_tol=1e-10):
        raise ValueError("Observed and expected category totals must agree; no normalization is performed.")
    chi2 = math.fsum((o - e) ** 2 / e for o, e in zip(observed, expected))
    if not math.isfinite(chi2):
        raise ValueError("Chi-square statistic exceeds the supported numeric range.")
    df = len(observed) - 1  # No fitted parameters; expected probabilities specified a priori.
    p = _gamma_q(df / 2, chi2 / 2)
    return {
        "summary": "Pearson chi-square goodness-of-fit test; expected counts fixed a priori, no fitted parameters.",
        "values": {"chi_square": chi2, "degrees_of_freedom": df, "p_value": p,
                   "categories": len(observed), "expected_count_below_5": sum(e < 5 for e in expected)},
        "table": {"columns": ["category (zero-based)", "observed", "expected", "chi-square contribution"],
                  "rows": [[i, o, e, (o - e) ** 2 / e] for i, (o, e) in enumerate(zip(observed, expected))]},
    }


OPERATIONS = (
    Operation(
        "weighted_linear_least_squares", "Weighted linear least squares",
        "Fit an intercept and up to 15 predictors with nonnegative relative inverse-variance weights; "
        "return coefficients, errors, covariance, weighted R² and per-observation residuals.",
        (Field("predictors", "Predictors (rows × columns; intercept implicit)", "matrix"),
         Field("response", "Response y", "vector"),
         Field("weights", "Relative inverse-variance weights", "vector")),
        {"predictors": [[1], [2], [3], [4], [5]], "response": [2.1, 4.2, 5.9, 8.0, 10.2],
         "weights": [1, 2, 1, 2, 1]}, _linear,
    ),
    Operation(
        "nonlinear_least_squares", "Levenberg–Marquardt nonlinear fit",
        "Fit exponential, saturating_exponential, power_law, gaussian, polynomial, thurber "
        "or mgh09 models with analytic Jacobians, convergence diagnostics, errors and 95% Wald intervals.",
        (Field("model", "Model (case-insensitive names in description)", "text"),
         Field("x", "Predictor x", "vector"), Field("y", "Observed response", "vector"),
         Field("weights", "Relative inverse-variance weights", "vector"),
         Field("initial_parameters", "Starting parameters in documented order", "vector")),
        {"model": "saturating_exponential", "x": [1, 2, 3, 4, 5],
         "y": [1.8, 3.2, 4.3, 5.1, 5.6], "weights": [1, 1, 1, 1, 1],
         "initial_parameters": [8, 0.25]}, _nonlinear,
    ),
    Operation(
        "welch_t_test", "Welch two-sample t test", "Two-sided Welch t test; independent samples and unequal population variances.",
        (Field("sample_a", "Sample A", "vector"), Field("sample_b", "Sample B", "vector")),
        {"sample_a": [6, 8, 10, 7], "sample_b": [9, 11, 12, 10, 13]}, _welch,
    ),
    Operation(
        "chi_square_gof", "Chi-square goodness of fit",
        "Pearson goodness-of-fit test for categories with fixed expected counts and matching totals.",
        (Field("observed", "Observed category counts", "vector"),
         Field("expected", "Expected counts (fixed in advance)", "vector")),
        {"observed": [25, 30, 45], "expected": [30, 30, 40]}, _chi_square,
    ),
)
