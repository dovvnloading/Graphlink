"""Equilibrium binding, tight-binding inhibition, Schild and thermal-shift fits.

Unweighted response-space fitting; positive scales are fitted in log space.
Numerics share the workbench's dependency-free, pivoted least-squares solver.
"""

from __future__ import annotations

import math
import statistics

from graphlink_plugins.scientific.core import Citation, Field, Operation
from graphlink_plugins.scientific.packs.fitting import _d, _normal_solve
from graphlink_plugins.scientific.units import convert_decimal_measurement


def _label(value, name):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 40 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name} must be a unit label of 1-40 characters.")
    return value.strip()


def _molar_unit(value):
    unit = _label(value, "Concentration")
    try:
        convert_decimal_measurement("1", unit, "M")
    except ValueError:
        raise ValueError("Concentration unit must be convertible to molarity (M).") from None
    return unit


def _paired(x, y, *, minimum, distinct, name, positive=False):
    if len(x) != len(y) or len(x) < minimum:
        raise ValueError(f"{name} requires at least {minimum} aligned observations.")
    if any(t < 0 or (positive and t == 0) for t in x) or len(set(x)) < distinct:
        raise ValueError(f"{name} requires at least {distinct} distinct "
                         f"{'positive' if positive else 'nonnegative'} concentrations/temperatures.")
    spread = max(y) - min(y)
    if spread <= 1e-12 * max(abs(max(y)), abs(min(y)), 1e-15):
        raise ValueError(f"{name} requires a varying response with measurable dynamic range.")


def _solve(gram, rhs, *, inverse=False):
    result = _normal_solve([[_d(v) for v in row] for row in gram],
                           [_d(v) for v in rhs], inverse=inverse)
    if inverse:
        _, covariance = result
        return [[float(value) for value in row] for row in covariance]
    return [float(v) for v in result]


def _terms(predict, x, y, theta, *, derivatives=False):
    npar = len(theta)
    gram = [[0.0] * npar for _ in theta]
    grad = [0.0] * npar
    predictions, residuals = [], []
    for xx, yy in zip(x, y):
        estimated, jac = predict(xx, theta)
        if not math.isfinite(estimated) or any(not math.isfinite(v) for v in jac):
            raise ValueError("Nonfinite fit prediction or sensitivity.")
        residual = yy - estimated
        predictions.append(estimated)
        residuals.append(residual * residual)
        if derivatives:
            for i in range(npar):
                grad[i] += jac[i] * residual
                for j in range(i + 1):
                    gram[i][j] += jac[i] * jac[j]
    sse = math.fsum(residuals)
    if not derivatives:
        return sse
    for i in range(npar):
        for j in range(i):
            gram[j][i] = gram[i][j]
    return sse, gram, grad, predictions


def _lm(predict, x, y, initial, bounds):
    theta = [max(lo, min(hi, value)) for value, (lo, hi) in zip(initial, bounds)]
    damping = 0.01
    for iteration in range(1, 241):
        sse, gram, grad, _ = _terms(predict, x, y, theta, derivatives=True)
        if max(map(abs, grad)) <= 1e-12:
            return theta, iteration, sse
        accepted = False
        for _ in range(28):
            matrix = [row.copy() for row in gram]
            for j in range(len(theta)):
                matrix[j][j] += damping * max(gram[j][j], 1e-12)
            try:
                step = _solve(matrix, grad)
                trial = [max(lo, min(hi, val + step[j]))
                         for j, (val, (lo, hi)) in enumerate(zip(theta, bounds))]
                trial_sse = _terms(predict, x, y, trial)
            except (ValueError, ArithmeticError, OverflowError):
                trial_sse = math.inf
            if math.isfinite(trial_sse) and trial_sse < sse:
                accepted = True
                break
            damping *= 10
        if not accepted:
            if max(map(abs, grad)) < 1e-7:
                return theta, iteration, sse
            raise ValueError("Nonlinear fit did not converge or reached a parameter boundary.")
        change = max(abs(a - b) for a, b in zip(trial, theta))
        relative_improvement = (sse - trial_sse) / max(sse, 1e-12)
        theta = trial
        damping = max(1e-12, damping / 3)
        if change < 1e-9 or relative_improvement < 1e-12:
            return theta, iteration, trial_sse
    raise ValueError("Nonlinear fit exceeded 240 iterations.")


def _fit(predict, x, y, seeds, bounds):
    """Multi-start fit to normalized observations; covariance is on fitted coordinates."""
    best = None
    for seed in seeds:
        try:
            theta, iterations, sse = _lm(predict, x, y, seed, bounds)
        except (ValueError, ArithmeticError, OverflowError):
            continue
        if best is None or sse < best[2]:
            best = theta, iterations, sse
    if best is None:
        raise ValueError("Nonlinear fit failed; sample both sides of the transition and add replicates.")
    theta, iterations, sse = best
    _, gram, _, predictions = _terms(predict, x, y, theta, derivatives=True)
    covariance = None
    warnings = []
    if any(abs(value - lo) < 1e-7 or abs(value - hi) < 1e-7
           for value, (lo, hi) in zip(theta, bounds)):
        warnings.append("A fit parameter reached a bound; estimates may be unidentifiable")
    try:
        inverse = _solve(gram, [0] * len(theta), inverse=True)
        covariance = [[sse / (len(y) - len(theta)) * value for value in row] for row in inverse]
        if not all(math.isfinite(v) for row in covariance for v in row):
            raise ValueError("Nonfinite covariance")
    except (ValueError, ArithmeticError, OverflowError):
        warnings.append("Local covariance is rank deficient")
    return theta, predictions, covariance, iterations, warnings


def _se(covariance, derivative):
    if covariance is None:
        return "unavailable (singular covariance)"
    variance = math.fsum(d * derivative[j] * covariance[i][j]
                         for i, d in enumerate(derivative) for j in range(len(derivative)))
    return math.sqrt(max(0.0, variance)) if math.isfinite(variance) else "unavailable (nonfinite covariance)"


def _metrics(y, predictions, count, unit, iterations, warnings):
    rss = math.fsum((a - b) ** 2 for a, b in zip(y, predictions))
    total = math.fsum((value - statistics.mean(y)) ** 2 for value in y)
    return {"observations": len(y), "degrees_of_freedom": len(y) - count,
            "iterations": iterations, "converged": "true",
            "residual_sum_squares": rss, "residual_sum_squares_unit": f"({unit})²",
            "residual_standard_deviation": math.sqrt(rss / (len(y) - count)),
            "residual_standard_deviation_unit": unit,
            "r_squared": 1 - rss / total, "r_squared_unit": "1 (dimensionless)",
            "warnings": "; ".join(warnings) if warnings else "none"}


def _fit_table(x, y, fitted, xlabel, ylabel, title):
    return {"table": {"columns": [xlabel, f"Observed ({ylabel})", f"Fitted ({ylabel})",
                                  f"Residual ({ylabel})"],
                      "rows": [[xx, yy, ff, yy - ff] for xx, yy, ff in zip(x, y, fitted)]},
            "plot": {"title": title, "xLabel": xlabel, "yLabel": f"Fitted ({ylabel})",
                     "x": x, "y": fitted}}


def _binding_prediction(model, sign):
    def predict(x, theta):
        baseline, logb, logk = theta[:3]
        b, k = math.exp(logb), math.exp(logk)
        frac = x / (k + x)
        one = sign * b * frac
        value, jac = baseline + one, [1.0, one, -one * k / (k + x)]
        if model == "one_site_nonspecific":
            slope = sign * math.exp(theta[3]) * x
            value += slope
            jac.append(slope)
        elif model == "two_site":
            b2, k2 = math.exp(theta[3]), math.exp(theta[4])
            two = sign * b2 * x / (k2 + x)
            value += two
            jac.extend((two, -two * k2 / (k2 + x)))
        return value, jac
    return predict


def binding_isotherm_fit(data):
    unit, response_unit = _molar_unit(data["concentration_unit"]), _label(data["response_unit"], "Response")
    ligand, response, model = data["ligand"], data["response"], data["model"]
    p = {"one_site": 3, "one_site_nonspecific": 4, "two_site": 5}[model]
    minimum = 12 if model == "two_site" else 9
    distinct = 10 if model == "two_site" else 7
    _paired(ligand, response, minimum=minimum, distinct=distinct, name="Binding isotherm")
    if len({v for v in ligand if v > 0}) < distinct - 1:
        raise ValueError("Binding isotherm needs more distinct positive ligand levels.")
    order = sorted(range(len(ligand)), key=ligand.__getitem__)
    sign = 1 if statistics.mean(response[i] for i in order[-2:]) > statistics.mean(response[i] for i in order[:2]) else -1
    x_scale = statistics.median(v for v in ligand if v > 0)
    xs = [v / x_scale for v in ligand]
    y_origin = min(response)
    y_scale = max(response) - y_origin
    ys = [(v - y_origin) / y_scale for v in response]
    positive = [v for v in xs if v > 0]
    log_limits = (math.log(min(positive) / 100), math.log(max(positive) * 100))
    base = ys[order[0]]
    amplitude = max(0.2, abs(ys[order[-1]] - base))
    bounds = [(-5, 5), (-20, 6), log_limits]
    starts = [[base, math.log(amplitude), math.log(k)] for k in (.25, 1, 4, 12)]
    if model == "one_site_nonspecific":
        bounds.append((-22, 6))
        starts = [start + [math.log(max(amplitude * portion / max(xs), 1e-6))]
                  for start, portion in zip(starts, (.02, .1, .3, .05))]
    elif model == "two_site":
        bounds += [(-20, 6), log_limits]
        starts = [[base, math.log(max(amplitude * f, 1e-5)), math.log(k1),
                   math.log(max(amplitude * (1 - f), 1e-5)), math.log(k2)]
                  for f, k1, k2 in ((.5, .1, 3), (.3, .25, 8), (.7, .5, 5), (.5, 1, 15), (.5, .05, 1))]
    theta, predictions, cov, iterations, warnings = _fit(
        _binding_prediction(model, sign), xs, ys, starts, bounds)
    fitted = [y_origin + y_scale * v for v in predictions]
    dim = len(theta)
    def metric(value, gradient, name, measure):
        return {name: value, name + "_standard_error": _se(cov, gradient), name + "_unit": measure}
    values = metric(y_origin + y_scale * theta[0], [y_scale] + [0] * (dim - 1),
                    "baseline", response_unit)
    b1 = sign * y_scale * math.exp(theta[1])
    k1 = x_scale * math.exp(theta[2])
    pairs = [(k1, b1, 1, 2)]
    if model == "two_site":
        pairs.append((x_scale * math.exp(theta[4]), sign * y_scale * math.exp(theta[3]), 3, 4))
        pairs.sort(key=lambda item: item[0])
        if pairs[1][0] / pairs[0][0] < 1.5:
            warnings.append("Two affinities overlap; two-site decomposition may be unidentifiable")
    for j, (kd, capacity, capacity_index, kd_index) in enumerate(pairs):
        postfix = str(j + 1) if model == "two_site" else ""
        derivative = [0] * dim
        derivative[capacity_index] = capacity
        values.update(metric(capacity, derivative, "bmax" + postfix, response_unit))
        derivative = [0] * dim
        derivative[kd_index] = kd
        values.update(metric(kd, derivative, "kd" + postfix, unit))
    if model == "one_site_nonspecific":
        slope = sign * y_scale / x_scale * math.exp(theta[3])
        derivative = [0] * dim
        derivative[3] = slope
        values.update(metric(slope, derivative, "nonspecific_slope", f"{response_unit}/{unit}"))
    values.update({"model": model, **_metrics(response, fitted, p, response_unit, iterations, warnings)})
    return {"summary": f"{model.replace('_', ' ')} binding isotherm: unweighted equilibrium fit. "
                       "Amplitudes may be negative for a decreasing signal; affinities are apparent Kd.",
            "values": values,
            **_fit_table(ligand, response, fitted, f"Ligand ({unit})", response_unit,
                         f"Binding isotherm ({model.replace('_', ' ')})")}


def _morrison_prediction(x, theta):
    bottom, log_delta, log_ki = theta
    delta, ki = math.exp(log_delta), math.exp(log_ki)
    total = 1 + x + ki
    # Equivalent to sqrt((1+x+Ki)^2 - 4*x), without cancellation when
    # inhibitor and active enzyme are nearly equal and Ki is very small.
    disc = math.sqrt((1 - x) ** 2 + ki * (2 * (1 + x) + ki))
    bound = 2 * x / (total + disc)
    free = 1 - bound
    return bottom + delta * free, [1, delta * free, delta * bound * ki / disc]


def morrison_tight_binding_fit(data):
    unit, response_unit = _molar_unit(data["concentration_unit"]), _label(data["response_unit"], "Activity")
    inhibitor, activity, enzyme = data["inhibitor"], data["activity"], data["enzyme_concentration"]
    _paired(inhibitor, activity, minimum=9, distinct=7, name="Morrison tight-binding fit")
    if enzyme <= 0 or not math.isfinite(enzyme):
        raise ValueError("Total active enzyme concentration must be positive.")
    if 0 not in inhibitor or max(inhibitor) < enzyme:
        raise ValueError("Morrison fitting requires zero-inhibitor controls and doses at/above total enzyme.")
    if any(v < 0 for v in activity):
        raise ValueError("Morrison activity must be nonnegative and background-corrected.")
    x = [v / enzyme for v in inhibitor]
    if not all(math.isfinite(v) and v < 1e12 for v in x):
        raise ValueError("Inhibitor/enzyme ratios exceed the supported range.")
    positions = sorted(range(len(x)), key=x.__getitem__)
    if statistics.mean(activity[i] for i in positions[:2]) <= statistics.mean(activity[i] for i in positions[-2:]):
        raise ValueError("Morrison inhibition requires activity decreasing with inhibitor.")
    y_origin = min(activity)
    y_scale = max(activity) - y_origin
    y = [(v - y_origin) / y_scale for v in activity]
    positive = [v for v in x if v > 0]
    bounds = [(-2, 2), (-18, 5), (math.log(min(positive) / 100), math.log(max(positive) * 100))]
    seeds = [[0.0, 0.0, math.log(k)] for k in (.02, .1, .5, 2, 10)]
    theta, predictions, cov, iterations, warnings = _fit(_morrison_prediction, x, y, seeds, bounds)
    fitted = [y_origin + y_scale * v for v in predictions]
    ki = enzyme * math.exp(theta[2])
    bottom = y_origin + y_scale * theta[0]
    top = bottom + y_scale * math.exp(theta[1])
    values = {"ki_app": ki, "ki_app_standard_error": _se(cov, [0, 0, ki]),
              "ki_app_unit": unit, "enzyme_concentration": enzyme, "enzyme_concentration_unit": unit,
              "bottom": bottom, "bottom_standard_error": _se(cov, [y_scale, 0, 0]),
              "bottom_unit": response_unit,
              "top": top, "top_standard_error": _se(cov, [y_scale, y_scale * math.exp(theta[1]), 0]),
              "top_unit": response_unit,
              **_metrics(activity, fitted, 3, response_unit, iterations, warnings)}
    return {"summary": f"Morrison quadratic tight-binding fit: apparent Ki={ki:.6g} {unit}. "
                       "Total active enzyme is fixed; Ki_app is assay-dependent and is not automatically intrinsic Ki.",
            "values": values,
            **_fit_table(inhibitor, activity, fitted, f"Total inhibitor ({unit})", response_unit,
                         "Morrison tight-binding activity")}


def schild_analysis(data):
    unit = _molar_unit(data["concentration_unit"])
    inhibitor, ratios = data["antagonist"], data["dose_ratio"]
    _paired(inhibitor, ratios, minimum=5, distinct=5, name="Schild analysis", positive=True)
    if any(dr <= 1 for dr in ratios):
        raise ValueError("Schild dose ratios must be greater than one.")
    scale = convert_decimal_measurement("1", unit, "M")
    x = [math.log10(b * scale) for b in inhibitor]
    y = [math.log10(dr - 1) for dr in ratios]
    n = len(x)
    xbar, ybar = statistics.mean(x), statistics.mean(y)
    sxx = math.fsum((v - xbar) ** 2 for v in x)
    syy = math.fsum((v - ybar) ** 2 for v in y)
    if sxx <= 0 or syy <= 0:
        raise ValueError("Schild analysis requires variation in antagonist and dose ratio.")
    slope = math.fsum((xx - xbar) * (yy - ybar) for xx, yy in zip(x, y)) / sxx
    if slope <= 0:
        raise ValueError("Schild analysis requires a positive slope; inspect antagonist mechanism and data.")
    intercept = ybar - slope * xbar
    predicted = [intercept + slope * xx for xx in x]
    rss = math.fsum((yy - ff) ** 2 for yy, ff in zip(y, predicted))
    variance = rss / (n - 2)
    slope_error = math.sqrt(variance / sxx)
    intercept_error = math.sqrt(variance * (1 / n + xbar * xbar / sxx))
    covariance = -xbar * variance / sxx
    pa2 = intercept / slope  # DR=2 => log10(DR-1)=0; x is log10([B] in M).
    dpda, dpdm = 1 / slope, -intercept / (slope * slope)
    pa2_error = math.sqrt(max(0, dpda * dpda * intercept_error ** 2 +
                              dpdm * dpdm * slope_error ** 2 + 2 * dpda * dpdm * covariance))
    warnings = []
    if abs(slope - 1) > max(0.2, 2 * slope_error):
        warnings.append("Schild slope differs from unity; pA2 does not imply a competitive Kb")
    if min(x) > -pa2 or max(x) < -pa2:
        warnings.append("DR=2 intercept is extrapolated outside the sampled antagonist range")
    values = {"schild_slope": slope, "schild_slope_standard_error": slope_error,
              "schild_slope_unit": "1 (dimensionless)",
              "schild_intercept": intercept, "schild_intercept_standard_error": intercept_error,
              "schild_intercept_unit": "1 (dimensionless; log10 molar scale)",
              "pa2": pa2, "pa2_standard_error": pa2_error,
              "pa2_unit": "1 (dimensionless; -log10 molar)",
              "observations": n, "degrees_of_freedom": n - 2,
              "r_squared": 1 - rss / syy, "r_squared_unit": "1 (dimensionless)",
              "residual_sum_squares": rss, "residual_sum_squares_unit": "(log10(DR-1))²",
              "residual_standard_deviation": math.sqrt(variance),
              "residual_standard_deviation_unit": "log10(DR-1)",
              "warnings": "; ".join(warnings) if warnings else "none"}
    return {"summary": f"Free-slope Schild regression: slope={slope:.5g}, pA2={pa2:.5g} "
                       "(negative log10 molar antagonist concentration at dose ratio 2). "
                       "A unit slope is needed to interpret pA2 as pKb under competitive equilibrium assumptions.",
            "values": values,
            "table": {"columns": [f"Antagonist ({unit})", "Dose ratio (1)", "log10([B]/M)",
                                  "log10(DR-1)", "Schild fitted log10(DR-1)", "Residual log10(DR-1)"],
                      "rows": [[b, dr, xx, yy, fitted, yy - fitted] for b, dr, xx, yy, fitted in
                               zip(inhibitor, ratios, x, y, predicted)]},
            "plot": {"title": "Schild regression", "xLabel": "log10([B]/M)",
                     "yLabel": "Fitted log10(DR-1)", "x": x, "y": predicted}}


def _thermal_prediction(x, theta):
    baseline, amplitude, midpoint, logwidth = theta
    width = math.exp(logwidth)
    z = (midpoint - x) / width
    if z >= 0:
        ez = math.exp(-z)
        frac = ez / (1 + ez)
    else:
        ez = math.exp(z)
        frac = 1 / (1 + ez)
    factor = amplitude * frac * (1 - frac)
    return baseline + amplitude * frac, [1, frac, -factor / width, factor * z]


def thermal_shift_tm(data):
    unit = _label(data["temperature_unit"], "Temperature")
    if unit not in ("°C", "degC", "K", "°F", "degF"):
        raise ValueError("Thermal-shift temperature unit must be °C, degC, K, °F or degF.")
    response_unit = _label(data["response_unit"], "Fluorescence")
    temperature, signal = data["temperature"], data["signal"]
    if len(temperature) != len(signal) or len(temperature) < 10 or len(set(temperature)) < 9:
        raise ValueError("Thermal shift requires at least ten paired readings at nine distinct temperatures.")
    spread = max(signal) - min(signal)
    if spread <= 1e-12 * max(abs(max(signal)), abs(min(signal)), 1e-15):
        raise ValueError("Thermal shift requires a varying fluorescence response.")
    minimum, maximum = min(temperature), max(temperature)
    span = maximum - minimum
    xs = [(t - minimum) / span for t in temperature]
    y_origin, y_scale = min(signal), spread
    ys = [(v - y_origin) / y_scale for v in signal]
    order = sorted(range(len(xs)), key=xs.__getitem__)
    left = statistics.mean(ys[i] for i in order[:2])
    right = statistics.mean(ys[i] for i in order[-2:])
    amplitude = right - left
    if abs(amplitude) < 0.3:
        raise ValueError("Thermal shift needs a clear single transition across its temperature range.")
    middle = left + amplitude / 2
    closest = min(range(len(ys)), key=lambda j: abs(ys[j] - middle))
    bounds = [(-5, 5), (-8, 8), (0, 1), (math.log(.005), math.log(1))]
    seeds = [[left, amplitude, tm, math.log(width)]
             for tm, width in ((xs[closest], .08), (.5, .15), (xs[closest], .2), (.5, .04))]
    theta, predictions, cov, iterations, warnings = _fit(_thermal_prediction, xs, ys, seeds, bounds)
    fitted = [y_origin + y_scale * v for v in predictions]
    tm = minimum + span * theta[2]
    width = span * math.exp(theta[3])
    lower = y_origin + y_scale * theta[0]
    upper = lower + y_scale * theta[1]
    interval = {"°C": "delta_degC", "degC": "delta_degC", "K": "delta_K",
                "°F": "delta_degF", "degF": "delta_degF"}[unit]
    values = {"tm": tm, "tm_standard_error": _se(cov, [0, 0, span, 0]),
              "tm_unit": unit, "transition_width": width,
              "transition_width_standard_error": _se(cov, [0, 0, 0, width]),
              "transition_width_unit": interval,
              "low_temperature_signal": lower,
              "low_temperature_signal_standard_error": _se(cov, [y_scale, 0, 0, 0]),
              "low_temperature_signal_unit": response_unit,
              "high_temperature_signal": upper,
              "high_temperature_signal_standard_error": _se(cov, [y_scale, y_scale, 0, 0]),
              "high_temperature_signal_unit": response_unit,
              "direction": "increasing" if theta[1] > 0 else "decreasing",
              **_metrics(signal, fitted, 4, response_unit, iterations, warnings)}
    return {"summary": f"Thermal-shift single-transition sigmoid: Tm={tm:.6g} {unit} "
                       f"({values['direction']} signal). Tm is the fluorescence midpoint, "
                       "not proof of two-state unfolding or binding affinity.",
            "values": values,
            **_fit_table(temperature, signal, fitted, f"Temperature ({unit})", response_unit,
                         "Thermal-shift melting transition")}


_BINDING_FIELDS = (
    Field("ligand", "Total ligand concentration", "vector", unit="selected concentration unit"),
    Field("response", "Equilibrium binding signal", "vector", unit="selected response unit"),
    Field("concentration_unit", "Ligand concentration unit (e.g. uM)", "text", unit="unit label"),
    Field("response_unit", "Binding signal unit (e.g. RFU)", "text", unit="unit label"),
    Field("model", "Binding isotherm", "choice", unit="category (dimensionless)",
          choices=("one_site", "one_site_nonspecific", "two_site")),
)
_BINDING_EXAMPLE = {"ligand": [0, .05, .1, .2, .5, 1, 2, 4, 8, 16, 32, 64],
                    "response": [5, 7.4, 9.6, 13.4, 21.7, 30, 38.3, 45, 49.4, 52.1, 53.5, 54.2],
                    "concentration_unit": "uM", "response_unit": "RFU", "model": "one_site"}
_MORRISON_EXAMPLE = {"inhibitor": [0, .01, .03, .06, .1, .2, .4, .8, 1.2, 2, 4, 8],
                     "activity": [101, 99, 96, 92, 87, 76, 60, 41, 30, 20, 12, 7],
                     "enzyme_concentration": .5, "concentration_unit": "uM", "response_unit": "uM/s"}
_SCHILD_EXAMPLE = {"antagonist": [.001, .003, .01, .03, .1, .3, 1, 3],
                   "dose_ratio": [1.1, 1.3, 2.0, 4.0, 11, 31, 101, 301],
                   "concentration_unit": "uM"}
_THERMAL_EXAMPLE = {"temperature": [30, 35, 40, 45, 48, 50, 52, 54, 56, 58, 60, 62, 65, 70, 75, 80],
                    "signal": [100, 102, 106, 115, 128, 148, 181, 231, 310, 410, 510, 595, 680, 730, 745, 749],
                    "temperature_unit": "°C", "response_unit": "RFU"}

OPERATIONS = (
    Operation("binding_isotherm_fit", "Fit equilibrium binding isotherms",
              "One-site, one-site plus nonspecific, or two-site saturable binding with fitted background.",
              _BINDING_FIELDS, _BINDING_EXAMPLE, binding_isotherm_fit,
              methods="Unweighted nonlinear least squares: background + sum(Bmax_i*[L]/(Kd_i+[L])) "
                      "+ optional linear nonspecific slope*[L]. Positive Kd and amplitude magnitudes "
                      "are optimized in log coordinates; negative signal directions are supported. "
                      "Two-site Kd values are reported in ascending order. Local Jacobian covariance SEs.",
              citations=(Citation("Langmuir (1918), adsorption and single-site saturation",
                                  "https://doi.org/10.1021/ja02242a004"),)),
    Operation("morrison_tight_binding_fit", "Fit Morrison tight-binding inhibition",
              "Quadratic tight-binding depletion of free enzyme with fixed active-enzyme concentration.",
              (Field("inhibitor", "Total inhibitor concentration", "vector", unit="selected concentration unit"),
               Field("activity", "Observed residual activity", "vector", unit="selected response unit"),
               Field("enzyme_concentration", "Total active enzyme concentration", "number",
                     unit="selected concentration unit"),
               Field("concentration_unit", "Concentration unit (e.g. uM)", "text", unit="unit label"),
               Field("response_unit", "Activity unit (e.g. uM/s)", "text", unit="unit label")),
              _MORRISON_EXAMPLE, morrison_tight_binding_fit,
              methods="Unweighted nonlinear Morrison quadratic: bound/E=2[I]total/(Etotal+[I]total+Ki_app+"
                      "sqrt((Etotal+[I]total+Ki_app)^2-4*Etotal*[I]total)). Fit Ki_app, "
                      "top and bottom with known Etotal. Assumes reversible 1:1 tight binding, "
                      "equilibrium and concentration of active sites; Ki_app is condition-dependent.",
              citations=(Citation("Morrison (1969), reversible tight-binding inhibition",
                                  "https://doi.org/10.1016/0005-2744(69)90420-3"),)),
    Operation("schild_analysis", "Schild antagonist analysis",
              "Free-slope Schild regression, pA2 and departure from unit slope.",
              (Field("antagonist", "Antagonist concentration", "vector", unit="selected concentration unit"),
               Field("dose_ratio", "Agonist EC50 dose ratio (>1)", "vector", unit="1"),
               Field("concentration_unit", "Antagonist unit (e.g. uM)", "text", unit="unit label")),
              _SCHILD_EXAMPLE, schild_analysis,
              methods="OLS of log10(DR-1) on log10([antagonist]/M) with freely fitted slope. "
                      "pA2=intercept/slope; standard errors from OLS covariance and delta method. "
                      "Only with equilibrium, surmountable competitive antagonism and slope near 1 "
                      "does pA2 approximate pKb; dose ratios need common agonist response level.",
              citations=(Citation("Schild (1947), pA scale for drug antagonism",
                                  "https://doi.org/10.1111/j.1476-5381.1947.tb00336.x"),)),
    Operation("thermal_shift_tm", "Thermal-shift melting temperature",
              "Fit an increasing or decreasing single-transition fluorescence sigmoid to estimate Tm.",
              (Field("temperature", "Absolute temperature series", "vector", unit="selected temperature unit"),
               Field("signal", "Thermal fluorescence signal", "vector", unit="selected response unit"),
               Field("temperature_unit", "Temperature unit (°C, K or °F)", "text", unit="unit label"),
               Field("response_unit", "Fluorescence unit (e.g. RFU)", "text", unit="unit label")),
              _THERMAL_EXAMPLE, thermal_shift_tm,
              methods="Unweighted four-parameter Boltzmann sigmoid signal=low+delta/"
                      "(1+exp((Tm-T)/width)), with positive width and Tm inside the measured range. "
                      "Temperatures are absolute points (K, Celsius or Fahrenheit); width is an interval. "
                      "One transition and approximately flat end-state baselines are assumed.",
              citations=(Citation("Niesen, Berglund and Vedadi (2007), differential scanning fluorimetry",
                                  "https://doi.org/10.1038/nprot.2007.321"),)),
)
