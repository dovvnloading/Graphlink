"""Enzyme kinetics: inhibition, turnover, Hill saturation, and IC50 conversion.

Inhibition is fitted jointly in substrate and inhibitor concentration using
unweighted, bounded log-parameter Levenberg--Marquardt.  No reciprocal-rate
transformation or optional numerical dependency is used.
"""

from __future__ import annotations

import json
import math
import statistics

from graphlink_plugins.scientific.core import Citation, Field, Operation
from graphlink_plugins.scientific.packs.fitting import _d, _normal_solve, _original_nonlinear
from graphlink_plugins.scientific.units import convert_decimal_measurement
from graphlink_plugins.scientific.packs.enzyme_advanced import OPERATIONS as ADVANCED_OPERATIONS


_MODELS = ("competitive", "uncompetitive", "noncompetitive", "mixed")
_NAMES = {
    "competitive": ("vmax", "km", "ki"),
    "uncompetitive": ("vmax", "km", "ki_prime"),
    "noncompetitive": ("vmax", "km", "ki"),
    "mixed": ("vmax", "km", "ki", "ki_prime"),
}


def _unit(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 40 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be a unit label of 1-40 characters.")
    return value.strip()


def _inhibition_data(data):
    su = _unit(data["substrate_unit"], "Substrate concentration")
    iu = _unit(data["concentration_unit"], "Inhibitor concentration")
    vu = _unit(data["rate_unit"], "Initial rate")
    substrate, inhibitor, rate = (data[key] for key in ("substrate", "inhibitor", "initial_rate"))
    if not len(substrate) == len(inhibitor) == len(rate) or len(rate) < 12:
        raise ValueError("Inhibition fits need at least 12 aligned substrate, inhibitor and rate observations.")
    if any(s < 0 for s in substrate) or any(i < 0 for i in inhibitor) or any(v < 0 for v in rate):
        raise ValueError("Substrate, inhibitor and initial rates must be nonnegative.")
    positives = sorted({s for s in substrate if s > 0})
    doses = sorted(set(inhibitor))
    controls = {s for s, i in zip(substrate, inhibitor) if i == 0 and s > 0}
    treated = {s for s, i in zip(substrate, inhibitor) if i > 0 and s > 0}
    if len(positives) < 4 or len(doses) < 3 or doses[0] != 0 or len(controls) < 3 or len(treated) < 3:
        raise ValueError("Use >=4 positive substrate levels and >=3 inhibitor levels (including zero), "
                         "with >=3 substrate levels in both control and treated groups.")
    if max(rate) <= 0 or len(set(rate)) == 1:
        raise ValueError("Inhibition fitting needs a varying, positive initial-rate response.")
    # A zero-substrate blank has an exactly zero model prediction.  Reject a
    # nonzero blank rather than folding a missing background offset into Ki.
    if any(s == 0 and v != 0 for s, v in zip(substrate, rate)):
        raise ValueError("Zero-substrate controls require zero background-corrected rate.")
    return substrate, inhibitor, rate, su, iu, vu


def _inhibition_prediction(model, parameters, substrate, inhibitor, *, jacobian=False):
    """Prediction and analytic derivatives with respect to LOG parameters."""
    vmax, km, ki = parameters[:3]
    ratio = inhibitor / ki
    alpha = 1 + ratio if model in ("competitive", "noncompetitive", "mixed") else 1.0
    alpha_prime = (1 + inhibitor / parameters[3] if model == "mixed" else
                   1 + ratio if model in ("uncompetitive", "noncompetitive") else 1.0)
    denominator = alpha * km + alpha_prime * substrate
    prediction = vmax * (substrate / denominator)
    if not jacobian:
        return prediction
    response = [prediction, -prediction * alpha * km / denominator]
    if model == "competitive" or model == "mixed":
        influence = km
    elif model == "uncompetitive":
        influence = substrate
    else:
        influence = km + substrate
    response.append(prediction * ratio * influence / denominator)
    if model == "mixed":
        response.append(prediction * (inhibitor / parameters[3]) * substrate / denominator)
    return prediction, response


def _objective(model, xs, inhibitors, ys, logs, *, derivatives=False):
    parameters = [math.exp(value) for value in logs]
    p = len(parameters)
    if derivatives:
        gram, gradient = [[0.0] * p for _ in range(p)], [0.0] * p
    error_terms = []
    for substrate, inhibitor, observed in zip(xs, inhibitors, ys):
        if derivatives:
            fitted, jac = _inhibition_prediction(model, parameters, substrate, inhibitor, jacobian=True)
        else:
            fitted = _inhibition_prediction(model, parameters, substrate, inhibitor)
        residual = observed - fitted
        error_terms.append(residual * residual)
        if derivatives:
            for j in range(p):
                gradient[j] += jac[j] * residual
                for k in range(j + 1):
                    gram[j][k] += jac[j] * jac[k]
    sse = math.fsum(error_terms)
    if not derivatives:
        return sse
    for j in range(p):
        for k in range(j):
            gram[k][j] = gram[j][k]
    return sse, gram, gradient


def _linear_step(matrix, rhs, *, inverse=False):
    # Reuse the workbench's equilibrated/pivoted numerical linear solve.
    scaled = [[_d(value) for value in row] for row in matrix]
    result = _normal_solve(scaled, [_d(value) for value in rhs], inverse=inverse)
    if inverse:
        solution, covariance = result
        return [float(value) for value in solution], [[float(v) for v in row] for row in covariance]
    return [float(value) for value in result]


def _lm(model, xs, inhibitors, ys, starts):
    logs = [math.log(max(1e-11, value)) for value in starts]
    damping = 0.01
    for iteration in range(1, 241):
        sse, gram, gradient = _objective(model, xs, inhibitors, ys, logs, derivatives=True)
        if max(map(abs, gradient)) < 1e-13:
            return logs, iteration, sse
        accepted = False
        for _ in range(28):
            modified = [row.copy() for row in gram]
            for j in range(len(logs)):
                modified[j][j] += damping * max(gram[j][j], 1e-12)
            try:
                step = _linear_step(modified, gradient)
                trial = [max(-27.0, min(27.0, b + delta)) for b, delta in zip(logs, step)]
                trial_sse = _objective(model, xs, inhibitors, ys, trial)
            except (ValueError, ArithmeticError, OverflowError):
                trial_sse = math.inf
            if math.isfinite(trial_sse) and trial_sse < sse:
                accepted = True
                break
            damping *= 10
        if not accepted:
            if max(map(abs, gradient)) < 1e-8:
                return logs, iteration, sse
            raise ValueError("Inhibition nonlinear fit did not converge.")
        relative_step = max(abs(a - b) for a, b in zip(trial, logs))
        improvement = sse - trial_sse
        logs = trial
        damping = max(1e-12, damping / 3)
        if relative_step < 1e-9 or improvement < 1e-14 * max(sse, 1e-10):
            return logs, iteration, trial_sse
    raise ValueError("Inhibition nonlinear fit exceeded 240 iterations.")


def _inhibition_estimate(model, substrate, inhibitor, rate):
    s_scale = statistics.median(s for s in substrate if s > 0)
    i_scale = statistics.median(i for i in inhibitor if i > 0)
    v_scale = max(rate)
    xs = [s / s_scale for s in substrate]
    zs = [i / i_scale for i in inhibitor]
    ys = [v / v_scale for v in rate]
    p = len(_NAMES[model])
    untreated = [v for v, i in zip(ys, zs) if i == 0]
    base_vmax = max(1.05, max(untreated, default=1.0) * 1.5)
    seeds = (
        (base_vmax, 1.0, 1.0, 1.0),
        (base_vmax * 1.5, 0.3, 0.3, 3.0),
        (base_vmax, 3.0, 3.0, 0.3),
        (base_vmax * 2, 0.8, 0.15, 0.15),
        (base_vmax, 0.2, 5.0, 5.0),
    )
    best = None
    for seed in seeds:
        try:
            logs, iterations, sse = _lm(model, xs, zs, ys, seed[:p])
            if best is None or sse < best[2]:
                best = logs, iterations, sse
        except (ValueError, ArithmeticError, OverflowError):
            continue
    if best is None:
        raise ValueError(f"{model} inhibition fit is unidentifiable or did not converge.")
    logs, iterations, sse = best
    normalized = [math.exp(value) for value in logs]
    physical = [value * factor for value, factor in zip(normalized,
                (v_scale, s_scale, i_scale, i_scale))]
    predictions = [_inhibition_prediction(model, normalized, s, i) * v_scale
                   for s, i in zip(xs, zs)]
    if not all(math.isfinite(y) for y in predictions):
        raise ValueError("Inhibition predictions exceed the supported numerical range.")
    _, gram, _ = _objective(model, xs, zs, ys, logs, derivatives=True)
    warnings = []
    if any(abs(v) > 26.99 for v in logs):
        warnings.append("A parameter reached the optimization range; the mechanism may be unidentifiable")
    try:
        _, inv = _linear_step(gram, [0.0] * p, inverse=True)
        reduced = sse / (len(rate) - p)
        errors = [v * math.sqrt(max(0.0, inv[j][j] * reduced)) for j, v in enumerate(physical)]
        if any(not math.isfinite(v) for v in errors):
            raise ValueError("Nonfinite local covariance")
    except (ValueError, ArithmeticError):
        errors = ["unavailable (singular covariance)"] * p
        warnings.append("Local covariance is rank deficient")
    rss = math.fsum((observed - fitted) ** 2 for observed, fitted in zip(rate, predictions))
    total = math.fsum((observed - statistics.mean(rate)) ** 2 for observed in rate)
    k = p + 1  # Estimated Gaussian noise variance is a model parameter.
    if sse <= 1e-26:
        aic = aicc = bic = "undefined (zero residual variance)"
    else:
        base = len(rate) * (math.log(sse / len(rate)) + 2 * math.log(v_scale) +
                            1 + math.log(2 * math.pi))
        aic = base + 2 * k
        aicc = aic + 2 * k * (k + 1) / (len(rate) - k - 1) if len(rate) > k + 1 else "undefined"
        bic = base + k * math.log(len(rate))
    return {
        "model": model, "names": _NAMES[model], "parameters": physical, "errors": errors,
        "predictions": predictions, "residual_sum_squares": rss,
        "residual_standard_deviation": math.sqrt(rss / (len(rate) - p)),
        "r_squared": 1 - rss / total, "degrees_of_freedom": len(rate) - p,
        "observations": len(rate), "iterations": iterations,
        "aic": aic, "aicc": aicc, "bic": bic,
        "warnings": "; ".join(warnings) if warnings else "none",
    }


def _inhibition_values(result, su, iu, vu):
    values = {
        "model": result["model"], "observations": result["observations"],
        "degrees_of_freedom": result["degrees_of_freedom"], "iterations": result["iterations"],
        "converged": "true", "residual_sum_squares": result["residual_sum_squares"],
        "residual_sum_squares_unit": f"({vu})²",
        "residual_standard_deviation": result["residual_standard_deviation"],
        "residual_standard_deviation_unit": vu, "r_squared": result["r_squared"],
        "r_squared_unit": "1 (dimensionless)",
        "aic": result["aic"], "aicc": result["aicc"], "bic": result["bic"],
        "warnings": result["warnings"],
    }
    for name, estimate, error in zip(result["names"], result["parameters"], result["errors"]):
        unit = vu if name == "vmax" else su if name == "km" else iu
        values[name] = estimate
        values[f"{name}_standard_error"] = error
        values[f"{name}_unit"] = unit
    if result["model"] == "noncompetitive":
        values["ki_prime"] = values["ki"]
        values["ki_prime_standard_error"] = values["ki_standard_error"]
        values["ki_prime_unit"] = iu
    return values


def inhibition_model_fit(data):
    substrate, inhibitor, rate, su, iu, vu = _inhibition_data(data)
    model = data["model"]
    fit = _inhibition_estimate(model, substrate, inhibitor, rate)
    params = ", ".join(f"{name}={value:.6g}" for name, value in zip(fit["names"], fit["parameters"]))
    return {
        "summary": f"{model.title()} inhibition: unweighted joint nonlinear fit ({params}). "
                   "Local linearized standard errors and AICc assume independent equal-variance rate errors.",
        "values": _inhibition_values(fit, su, iu, vu),
        "table": {"columns": [f"[S] ({su})", f"[I] ({iu})", f"Observed ({vu})",
                              f"Fitted ({vu})", f"Residual ({vu})"],
                  "rows": [[s, i, y, fitted, y - fitted] for s, i, y, fitted in
                           zip(substrate, inhibitor, rate, fit["predictions"])]},
        "plot": {"title": f"{model.title()} inhibition: observed vs fitted",
                 "xLabel": f"Observed initial rate ({vu})", "yLabel": f"Fitted initial rate ({vu})",
                 "x": rate, "y": fit["predictions"]},
    }


def inhibition_model_compare(data):
    substrate, inhibitor, rate, su, iu, vu = _inhibition_data(data)
    fits, rejected = [], []
    for model in _MODELS:
        try:
            fit = _inhibition_estimate(model, substrate, inhibitor, rate)
            if not isinstance(fit["aicc"], float):
                raise ValueError("AICc undefined for an exact or undersampled fit")
            fits.append(fit)
        except (ValueError, ArithmeticError, OverflowError) as exc:
            rejected.append((model, str(exc)))
    if len(fits) < 2:
        raise ValueError("At least two identifiable models with defined AICc are required. "
                         "Collect replicate rates with nonzero residual variance and a wider substrate/inhibitor grid.")
    fits.sort(key=lambda result: result["aicc"])
    best = fits[0]["aicc"]
    likelihood = [math.exp(-0.5 * (fit["aicc"] - best)) for fit in fits]
    denominator = math.fsum(likelihood)
    rows, values = [], {"best_model": fits[0]["model"], "models_compared": len(fits),
                        "models_rejected": len(rejected), "observations": len(rate)}
    for rank, (fit, weight) in enumerate(zip(fits, likelihood), 1):
        normalized_weight = weight / denominator
        values[f"akaike_weight_{fit['model']}"] = normalized_weight
        values[f"delta_aicc_{fit['model']}"] = fit["aicc"] - best
        descriptor = ", ".join(f"{name}={p:.6g}" for name, p in zip(fit["names"], fit["parameters"]))
        rows.append([rank, fit["model"], fit["aic"], fit["aicc"], fit["bic"],
                     fit["aicc"] - best, normalized_weight, fit["residual_sum_squares"],
                     descriptor, fit["warnings"]])
    for model, reason in rejected:
        rows.append(["unranked", model, "undefined", "undefined", "undefined",
                     "undefined", 0, "undefined", "unavailable", reason])
    return {
        "summary": f"Four inhibition mechanisms fitted to the same {len(rate)} rates; "
                   f"lowest AICc: {fits[0]['model']}. Akaike weights are relative evidence, "
                   "not probabilities that the mechanism is physically true.",
        "values": values,
        "table": {"columns": ["Rank", "Inhibition model", "AIC", "AICc", "BIC",
                              "ΔAICc", "Akaike weight (1)", f"SSE ({vu})²",
                              "Parameters", "Warnings"], "rows": rows},
        "plot": {"title": "Relative inhibition-model Akaike weights",
                 "xLabel": "Rank (1 = lowest AICc)", "yLabel": "Akaike weight (1)",
                 "x": list(range(1, len(fits) + 1)),
                 "y": [w / denominator for w in likelihood]},
    }


def catalytic_constants(data):
    cu = _unit(data["concentration_unit"], "Concentration")
    ru = _unit(data["rate_unit"], "Initial rate")
    tu = _unit(data["time_unit"], "Time")
    vmax, km, enzyme = (data[key] for key in ("vmax", "km", "enzyme_concentration"))
    if min(vmax, km, enzyme) <= 0:
        raise ValueError("Vmax, Km and total active enzyme concentration must be positive.")
    try:
        normalized_rate = convert_decimal_measurement(str(vmax), ru, f"{cu}/{tu}")
        seconds_per_time = convert_decimal_measurement("1", tu, "s")
        km_molar = convert_decimal_measurement(str(km), cu, "M")
    except ValueError as exc:
        raise ValueError(f"Rate must have concentration/time units and concentration must be molar: {exc}") from None
    turnover = normalized_rate / enzyme
    turnover_si = turnover / seconds_per_time
    efficiency = turnover / km
    efficiency_si = turnover_si / km_molar
    if not all(math.isfinite(value) and value > 0 for value in
               (turnover, turnover_si, efficiency, efficiency_si)):
        raise ValueError("Catalytic constants exceed the supported numerical range.")
    return {
        "summary": f"Active-site turnover kcat={turnover:.6g} 1/{tu}; "
                   f"catalytic efficiency kcat/Km={efficiency_si:.6g} M⁻¹ s⁻¹. "
                   "Total active enzyme concentration (not enzyme mass) is required.",
        "values": {
            "kcat": turnover, "kcat_unit": f"1/{tu}", "kcat_per_second": turnover_si,
            "kcat_per_second_unit": "1/s",
            "kcat_over_km": efficiency, "kcat_over_km_unit": f"1/({cu}*{tu})",
            "kcat_over_km_si": efficiency_si, "kcat_over_km_si_unit": "1/(M*s)",
            "enzyme_concentration": enzyme, "enzyme_concentration_unit": cu,
            "vmax": vmax, "vmax_unit": ru, "km": km, "km_unit": cu,
        },
    }


def hill_fit(data):
    su = _unit(data["substrate_unit"], "Substrate concentration")
    vu = _unit(data["rate_unit"], "Initial rate")
    xs, ys = data["substrate"], data["initial_rate"]
    if len(xs) != len(ys) or len(xs) < 7:
        raise ValueError("Hill fitting requires at least seven paired measurements.")
    if any(x <= 0 for x in xs) or any(y < 0 for y in ys):
        raise ValueError("Hill fitting requires positive substrate and nonnegative rate observations.")
    if len(set(xs)) < 6 or max(ys) <= 0 or len(set(ys)) < 4:
        raise ValueError("Hill fitting needs at least six distinct positive substrates and varying rates.")
    log_x = [math.log(x) for x in xs]
    lmin, lmax = min(log_x), max(log_x)
    log_middle = statistics.median(log_x)
    midpoint = min(range(len(ys)), key=lambda j: abs(ys[j] - max(ys) / 2))
    start_logs = [log_x[midpoint], log_middle]
    spec = json.dumps({"expression": "Vmax/(1+exp(h*(logK-x)))",
                       "parameter_names": ["Vmax", "logK", "h"],
                       "bounds": {"Vmax": [1e-100, None],
                                  "logK": [lmin - 5, lmax + 5], "h": [0.1, 8.0]}})
    best = None
    for log_k in start_logs:
        for h in (1, 2, 0.5):
            try:
                fitted = _original_nonlinear({"model": spec, "x": log_x, "y": ys,
                                               "weights": [1] * len(xs),
                                               "initial_parameters": [max(ys) * 1.15, log_k, h]})
                if best is None or fitted["values"]["weighted_residual_sum_squares"] < best["values"]["weighted_residual_sum_squares"]:
                    best = fitted
            except (ValueError, ArithmeticError, OverflowError):
                continue
    if best is None:
        raise ValueError("Hill curve is unidentifiable; sample below and above the half-saturation plateau.")
    vals = best["values"]
    vmax, log_k, h = (vals[f"parameter_{j}"] for j in range(3))
    khalf = math.exp(log_k)
    fitted_rate = [vmax / (1 + math.exp(h * (log_k - x)))
                   if h * (log_k - x) < 700 else 0.0 for x in log_x]
    sse = vals["weighted_residual_sum_squares"]
    total = math.fsum((v - statistics.mean(ys)) ** 2 for v in ys)
    return {
        "summary": f"Three-parameter Hill saturation: Vmax={vmax:.6g} {vu}, "
                   f"K0.5={khalf:.6g} {su}, Hill exponent={h:.5g}. "
                   "The exponent is descriptive, not proof of mechanistic cooperativity.",
        "values": {
            "vmax": vmax, "vmax_standard_error": vals["standard_error_0"], "vmax_unit": vu,
            "k_half": khalf, "k_half_standard_error": khalf * vals["standard_error_1"],
            "k_half_unit": su,
            "hill_coefficient": h, "hill_coefficient_standard_error": vals["standard_error_2"],
            "hill_coefficient_unit": "1 (dimensionless)",
            "observations": len(xs), "degrees_of_freedom": len(xs) - 3,
            "residual_sum_squares": sse, "residual_sum_squares_unit": f"({vu})²",
            "residual_standard_deviation": vals["residual_standard_deviation"],
            "residual_standard_deviation_unit": vu,
            "r_squared": 1 - sse / total, "r_squared_unit": "1 (dimensionless)",
            "converged": vals["converged"],
        },
        "table": {"columns": [f"[S] ({su})", f"Initial rate ({vu})", f"Hill fitted ({vu})",
                              f"Residual ({vu})"],
                  "rows": [[s, v, f, v - f] for s, v, f in zip(xs, ys, fitted_rate)]},
        "plot": {"title": "Hill saturation nonlinear fit", "xLabel": f"[S] ({su})",
                 "yLabel": f"Fitted initial rate ({vu})", "x": xs, "y": fitted_rate},
    }


def cheng_prusoff_ki(data):
    unit = _unit(data["concentration_unit"], "Concentration")
    ic50, substrate, km = (data[key] for key in ("ic50", "substrate_concentration", "km"))
    if ic50 <= 0 or substrate < 0 or km <= 0:
        raise ValueError("IC50 and Km must be positive and substrate concentration nonnegative.")
    ki = ic50 / (1 + substrate / km)
    if not math.isfinite(ki) or ki <= 0:
        raise ValueError("The derived Ki is outside the supported numerical range.")
    return {
        "summary": f"Competitive equilibrium Cheng–Prusoff Ki={ki:.6g} {unit} "
                   "from IC50/(1+[S]/Km). Not valid for tight-binding, irreversible, "
                   "noncompetitive or substrate-depletion assays.",
        "values": {"ki": ki, "ki_unit": unit, "ic50": ic50, "ic50_unit": unit,
                   "substrate_concentration": substrate, "substrate_concentration_unit": unit,
                   "km": km, "km_unit": unit, "cheng_prusoff_factor": 1 + substrate / km,
                   "cheng_prusoff_factor_unit": "1 (dimensionless)"},
    }


_INHIBITION_FIELDS = (
    Field("substrate", "Substrate concentration [S]", "vector", unit="selected concentration unit"),
    Field("inhibitor", "Inhibitor concentration [I]", "vector", unit="selected concentration unit"),
    Field("initial_rate", "Initial reaction rate", "vector", unit="selected rate unit"),
    Field("substrate_unit", "Substrate unit (e.g. mM)", "text", unit="unit label"),
    Field("concentration_unit", "Inhibitor unit (e.g. uM)", "text", unit="unit label"),
    Field("rate_unit", "Rate unit (e.g. uM/s)", "text", unit="unit label"),
)
_INHIBITION_EXAMPLE = {
    "substrate": [0.5, 1, 2, 4, 8, 16] * 3,
    "inhibitor": [0] * 6 + [1.5] * 6 + [4.5] * 6,
    "initial_rate": [20.1, 33.2, 50.4, 66.8, 79.6, 89.1,
                     11.3, 19.8, 33.6, 50.2, 66.5, 79.8,
                     5.9, 11.2, 20.2, 33.1, 49.9, 66.9],
    "substrate_unit": "mM", "concentration_unit": "uM", "rate_unit": "uM/s",
}
_INHIBITION_METHOD = (
    "Joint unweighted nonlinear least squares on the initial-rate surface "
    "v=Vmax[S]/(alpha Km+alpha' [S]), with alpha=1+[I]/Ki and "
    "alpha'=1+[I]/Ki'. Competitive fixes alpha'=1; uncompetitive fixes alpha=1; "
    "pure noncompetitive sets Ki=Ki'; mixed fits each independently. "
    "Optimize positive parameters in log space with multi-start damped least squares. "
    "Standard errors use local Jacobian covariance SSE/(n-p) times inverse(J^T J). "
    "AIC/AICc/BIC include an estimated common Gaussian error variance."
)
_INHIBITION_CITATION = Citation(
    "Cornish-Bowden, Fundamentals of Enzyme Kinetics, 4th ed. (2012), reversible inhibition models",
    "https://www.wiley.com/en-us/Fundamentals+of+Enzyme+Kinetics%2C+4th+Edition-p-9783527330744",
)

OPERATIONS = (
    Operation(
        "inhibition_model_fit", "Fit enzyme inhibition mechanism",
        "Global nonlinear initial-rate fit: competitive, uncompetitive, pure noncompetitive or mixed inhibition.",
        _INHIBITION_FIELDS + (Field("model", "Inhibition mechanism", "choice",
                                    unit="category (dimensionless)", choices=_MODELS),),
        {**_INHIBITION_EXAMPLE, "model": "competitive"}, inhibition_model_fit,
        methods=_INHIBITION_METHOD, citations=(_INHIBITION_CITATION,),
    ),
    Operation(
        "inhibition_model_compare", "Compare enzyme inhibition models",
        "Refit four reversible inhibition mechanisms to the same paired rates and rank by AICc.",
        _INHIBITION_FIELDS, _INHIBITION_EXAMPLE, inhibition_model_compare,
        methods=_INHIBITION_METHOD + " Rank on finite AICc, with delta AICc and normalized Akaike weights.",
        citations=(_INHIBITION_CITATION,),
    ),
    Operation(
        "catalytic_constants", "Catalytic turnover and efficiency",
        "Calculate kcat = Vmax/[E]active and kcat/Km, including SI molar-second efficiency.",
        (Field("vmax", "Maximum initial rate Vmax", "number", unit="selected rate unit"),
         Field("km", "Michaelis constant Km", "number", unit="selected concentration unit"),
         Field("enzyme_concentration", "Total active-site concentration [E]", "number",
               unit="selected concentration unit"),
         Field("concentration_unit", "Concentration unit (e.g. uM)", "text", unit="unit label"),
         Field("rate_unit", "Rate unit (e.g. uM/min)", "text", unit="unit label"),
         Field("time_unit", "Time unit (e.g. min)", "text", unit="unit label")),
        {"vmax": 120, "km": 20, "enzyme_concentration": 0.1,
         "concentration_unit": "uM", "rate_unit": "uM/min", "time_unit": "min"},
        catalytic_constants,
        methods="Convert Vmax to concentration/time; kcat=Vmax/[E]active; efficiency=kcat/Km. "
                "Return both selected units and M^-1 s^-1. The enzyme input is concentration of "
                "active catalytic sites, assuming one turnover is counted per active site.",
        citations=(Citation("IUPAC Gold Book, catalytic constant (turnover number)",
                            "https://goldbook.iupac.org/terms/view/C00846"),),
    ),
    Operation(
        "hill_fit", "Fit Hill saturation",
        "Unweighted nonlinear three-parameter Hill fit: Vmax, K0.5 and Hill exponent.",
        (Field("substrate", "Positive substrate concentrations [S]", "vector",
               unit="selected concentration unit"),
         Field("initial_rate", "Initial reaction rates", "vector", unit="selected rate unit"),
         Field("substrate_unit", "Substrate unit (e.g. uM)", "text", unit="unit label"),
         Field("rate_unit", "Rate unit (e.g. uM/s)", "text", unit="unit label")),
        {"substrate": [0.25, 0.5, 1, 2, 4, 8, 16, 32, 64],
         "initial_rate": [1.2, 3.6, 9.9, 25.1, 50.2, 75.0, 90.1, 96.4, 98.9],
         "substrate_unit": "uM", "rate_unit": "uM/s"},
        hill_fit,
        methods="Nonlinear unweighted least squares of v=Vmax[S]^h/(K0.5^h+[S]^h) "
                "on positive substrate concentrations; multi-start Decimal LM fits Vmax>0, "
                "0.1<=h<=8 and log(K0.5). Report delta-method SE for K0.5 and local covariance SE. "
                "The Hill exponent is phenomenological, not a count of binding sites.",
        citations=(Citation("Hill (1910), The possible effects of aggregation of the molecules of haemoglobin",
                            "https://doi.org/10.1113/jphysiol.1910.sp001386"),),
    ),
    Operation(
        "cheng_prusoff_ki", "Convert IC50 to competitive Ki",
        "Equilibrium competitive Cheng-Prusoff correction for substrate concentration and Km.",
        (Field("ic50", "Measured IC50", "number", unit="selected concentration unit"),
         Field("substrate_concentration", "Assay substrate concentration [S]", "number",
               unit="selected concentration unit"),
         Field("km", "Michaelis constant Km", "number", unit="selected concentration unit"),
         Field("concentration_unit", "Common concentration unit (e.g. uM)", "text", unit="unit label")),
        {"ic50": 15, "substrate_concentration": 8, "km": 4, "concentration_unit": "uM"},
        cheng_prusoff_ki,
        methods="For reversible, rapid-equilibrium competitive inhibition in a Michaelis-Menten "
                "assay, Ki=IC50/(1+[S]/Km). This correction is not a general IC50 to Ki "
                "conversion for mixed, irreversible, tight-binding or non-equilibrium inhibitors.",
        citations=(Citation("Cheng and Prusoff (1973), Relationship between Ki and IC50",
                            "https://doi.org/10.1016/0006-2952(73)90196-2"),),
    ),
) + ADVANCED_OPERATIONS
