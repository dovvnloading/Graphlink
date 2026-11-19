"""Plate assay layouts, calibration and control quality for Biology Workbench.

Calculations use bounded, grant-gated scientific operations. A result is an
analysis of supplied observations, not a claim that an assay passed validation.
"""

from __future__ import annotations

import json
import math
import statistics
from decimal import Decimal, localcontext

from graphlink_plugins.scientific.core import Citation, Field, Operation
from graphlink_plugins.scientific.packs.fitting import _normal_solve, _original_nonlinear
from graphlink_plugins.scientific.result_views import check_result_views


def _label(value: str, name: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 40
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{name} must be a 1–40 character label.")
    return value.strip()


def _checked(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} is outside the supported numeric range.")
    return value


def plate_layout(data: dict) -> dict:
    """Preserve categorical assignments, actual zero readings and missing wells."""
    def unique_wells(pairs):
        result = {}
        for address, value in pairs:
            if address in result:
                raise ValueError(f"Duplicate plate well {address!r}.")
            result[address] = value
        return result

    try:
        readings = json.loads(
            data["wells_json"], object_pairs_hook=unique_wells,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite well reading.")),
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Supply a JSON object mapping well IDs to readings or assignments.") from exc
    if not isinstance(readings, dict):
        raise ValueError("Plate readings must be a JSON object.")
    size = int(data["plate_format"])
    unit = _label(data["reading_label"], "Reading label")
    wells = [{"well": address, "value": value} for address, value in readings.items()]
    view = {"type": "plate_map", "title": f"{size}-well assay plate", "format": size,
            "unit": unit, "wells": wells}
    check_result_views([view])
    wells.sort(key=lambda item: (item["well"][0], int(item["well"][1:])))
    observed = sum(item["value"] is not None for item in wells)
    return {
        "summary": f"{size}-well assay layout: {observed} assigned/measured wells, {size - observed} unmeasured.",
        "values": {"plate_format": str(size), "recorded_wells": observed,
                   "empty_wells": size - observed, "reading_label": unit},
        "table": {"columns": ["Well", unit],
                  "rows": [[item["well"], item["value"] if item["value"] is not None else ""]
                           for item in wells]},
        "views": [view],
    }


def _polynomial_fit(xs: list[float], ys: list[float], degree: int) -> list[float]:
    """High-precision least squares for linear/quadratic standard curves."""
    with localcontext() as context:
        context.prec = 76
        x = [Decimal(str(value)) for value in xs]
        y = [Decimal(str(value)) for value in ys]
        rows = [[Decimal(1), value, value * value][:degree + 1] for value in x]
        gram = [[sum((row[i] * row[j] for row in rows), Decimal(0))
                 for j in range(degree + 1)] for i in range(degree + 1)]
        rhs = [sum((row[i] * observed for row, observed in zip(rows, y)), Decimal(0))
               for i in range(degree + 1)]
        try:
            coefficients = _normal_solve(gram, rhs)
        except ValueError as exc:
            raise ValueError("Standard concentrations do not identify the polynomial fit.") from exc
    return [_checked(float(value), "Calibration coefficient") for value in coefficients]


def _logistic_fit(xs: list[float], ys: list[float], model: str) -> dict:
    """Scale concentrations/signals before the shared bounded LM optimizer."""
    if any(x <= 0 for x in xs):
        raise ValueError("4PL/5PL concentrations must all be strictly positive.")
    lo_x, hi_x = min(xs), max(xs)
    if hi_x / lo_x > 1e12:
        raise ValueError("4PL/5PL calibration spans more than 12 orders of magnitude.")
    x_scale = math.exp((math.log(lo_x) + math.log(hi_x)) / 2)
    y_floor, y_span = min(ys), max(ys) - min(ys)
    x = [value / x_scale for value in xs]
    y = [(value - y_floor) / y_span for value in ys]
    ordered = sorted(zip(x, y))
    mid = ordered[len(ordered) // 2][0]
    names = ["a", "d", "c", "b"]
    expression = "d+(a-d)/(1+(x/c)^b)"
    bounds = [[-4, 5], [-4, 5], [min(x) / 10, max(x) * 10], [0.05, 20]]
    starts = [ordered[0][1], ordered[-1][1], mid, 1.2]
    if model == "5pl":
        names.append("g")
        expression = "d+(a-d)/(1+(x/c)^b)^g"
        bounds.append([0.1, 10])
        starts.append(1.0)
    fit = _original_nonlinear({
        "model": json.dumps({"expression": expression, "parameter_names": names, "bounds": bounds}),
        "x": x, "y": y, "weights": [1.0] * len(x), "initial_parameters": starts,
    })
    estimates = [fit["values"][f"parameter_{i}"] for i in range(len(names))]
    a, d, c, b = estimates[:4]
    g = estimates[4] if model == "5pl" else 1.0
    parameters = {"a": _checked(y_floor + y_span * a, "4PL/5PL asymptote"),
                  "d": _checked(y_floor + y_span * d, "4PL/5PL asymptote"),
                  "c": _checked(x_scale * c, "4PL/5PL midpoint"),
                  "b": _checked(b, "4PL/5PL slope"), "g": _checked(g, "5PL asymmetry")}
    if parameters["c"] <= 0 or parameters["b"] <= 0 or parameters["g"] <= 0:
        raise ValueError("The fitted 4PL/5PL parameters are not physically invertible.")
    return parameters


def _evaluate(model: str, parameters: dict, x: float) -> float:
    if model in ("linear", "quadratic"):
        return _checked(sum(parameters[f"coefficient_{j}"] * x**j
                            for j in range(2 if model == "linear" else 3)), "Predicted signal")
    a, d, c, b, g = (parameters[key] for key in ("a", "d", "c", "b", "g"))
    z = b * math.log(x / c)
    # softplus(z) avoids overflow from (x/c)**b at steep endpoints.
    log_denominator = z + math.log1p(math.exp(-z)) if z > 0 else math.log1p(math.exp(z))
    return _checked(d + (a - d) * math.exp(-g * log_denominator), "Predicted signal")


def _inverse(model: str, parameters: dict, response: float,
             lo: float, hi: float) -> float | None:
    """Invert only within the *observed* calibration interval; never extrapolate."""
    left, right = _evaluate(model, parameters, lo), _evaluate(model, parameters, hi)
    if left == right or response < min(left, right) or response > max(left, right):
        return None
    increasing = right > left
    lower, upper = (math.log(lo), math.log(hi)) if model in ("4pl", "5pl") else (lo, hi)
    for _ in range(80):
        center = lower / 2 + upper / 2
        x = math.exp(center) if model in ("4pl", "5pl") else center
        candidate = _evaluate(model, parameters, x)
        if (candidate < response) == increasing:
            lower = center
        else:
            upper = center
    return _checked(math.exp(lower / 2 + upper / 2) if model in ("4pl", "5pl")
                    else lower / 2 + upper / 2, "Back-calculated concentration")


def standard_curve(data: dict) -> dict:
    """Fit a calibration curve, invert standards/unknowns and undo dilution."""
    model = data["model"]
    xs, ys = data["standard_concentrations"], data["standard_signals"]
    unknowns, dilutions = data["unknown_signals"], data["dilution_factors"]
    unit = _label(data["concentration_unit"], "Concentration unit")
    signal_unit = _label(data["signal_unit"], "Signal unit")
    required = {"linear": 2, "quadratic": 3, "4pl": 6, "5pl": 7}[model]
    if len(xs) != len(ys) or len(xs) < required or len(set(xs)) < {"linear": 2, "quadratic": 3, "4pl": 5, "5pl": 6}[model]:
        raise ValueError(f"{model.upper()} requires at least {required} paired standards at distinct concentrations.")
    if len(unknowns) != len(dilutions):
        raise ValueError("Each unknown signal requires one matching dilution factor.")
    if any(x < 0 for x in xs) or any(factor < 1 for factor in dilutions):
        raise ValueError("Concentrations must be nonnegative; dilution factors must be at least one.")
    if max(ys) == min(ys):
        raise ValueError("Standard responses must vary across concentrations.")
    if model in ("linear", "quadratic"):
        fitted = _polynomial_fit(xs, ys, 1 if model == "linear" else 2)
        params = {f"coefficient_{j}": value for j, value in enumerate(fitted)}
        if model == "quadratic":
            low_slope = fitted[1] + 2 * fitted[2] * min(xs)
            high_slope = fitted[1] + 2 * fitted[2] * max(xs)
            if low_slope * high_slope <= 0:
                raise ValueError("Quadratic calibration is not monotonic over the standards.")
        elif fitted[1] == 0:
            raise ValueError("A flat linear calibration cannot be inverted.")
    else:
        if len(set(xs)) < (4 if model == "4pl" else 5):
            raise ValueError("4PL/5PL needs enough distinct positive concentrations.")
        params = _logistic_fit(xs, ys, model)
    lo, hi = min(xs), max(xs)
    pred = [_evaluate(model, params, x) for x in xs]
    sse = _checked(math.fsum((observed - fitted) ** 2
                             for observed, fitted in zip(ys, pred)), "Residual sum of squares")
    mean = statistics.mean(ys)
    sst = _checked(math.fsum((value - mean) ** 2 for value in ys), "Response variance")
    r_squared = _checked(1 - sse / sst, "Calibration R squared")

    rows = []
    for index, (x, y, prediction) in enumerate(zip(xs, ys, pred), 1):
        back = _inverse(model, params, y, lo, hi)
        rows.append(["Standard", index, x, y, prediction,
                     back if back is not None else "", 1,
                     back if back is not None else "",
                     100 * back / x if back is not None and x > 0 else "",
                     "within range" if back is not None else "outside calibration"])
    quantified = 0
    for index, (signal, factor) in enumerate(zip(unknowns, dilutions), 1):
        back = _inverse(model, params, signal, lo, hi)
        corrected = _checked(back * factor, "Dilution-corrected concentration") if back is not None else None
        rows.append(["Unknown", index, "", signal, "", back if back is not None else "",
                     factor, corrected if corrected is not None else "", "",
                     "within range" if back is not None else "outside calibration"])
        quantified += back is not None
    if model in ("4pl", "5pl"):
        points = [math.exp(math.log(lo) + i * (math.log(hi) - math.log(lo)) / 95)
                  for i in range(96)]
    else:
        points = [lo + i * (hi - lo) / 95 for i in range(96)]
    fit_curve = [_evaluate(model, params, x) for x in points]
    return {
        "summary": (f"{model.upper()} standard curve fitted to {len(xs)} standards; "
                    f"{quantified} of {len(unknowns)} unknowns quantified within the calibration interval. "
                    "Concentrations for unknowns include their reciprocal sample dilution factors."),
        "values": {
            "model": model, "standards": len(xs), "unknowns": len(unknowns),
            "unknowns_quantified": quantified, "calibration_min": lo, "calibration_max": hi,
            "concentration_unit": unit, "signal_unit": signal_unit,
            "residual_sum_squares": sse, "r_squared": r_squared, **params,
        },
        "table": {"columns": ["Type", "Index", f"Nominal ({unit})", f"Signal ({signal_unit})",
                              f"Fitted signal ({signal_unit})", f"Back-calculated ({unit})",
                              "Reciprocal dilution", f"Original sample ({unit})", "Recovery (%)", "Status"],
                  "rows": rows},
        "plot": {"title": f"{model.upper()} standard curve", "xLabel": f"Concentration ({unit})",
                 "yLabel": f"Signal ({signal_unit})", "x": points, "y": fit_curve},
    }


def assay_quality(data: dict) -> dict:
    """Unbiased replicate SDs, Z′ and high-control-SD-normalized signal window."""
    positive, negative = data["positive_controls"], data["negative_controls"]
    unit = _label(data["signal_unit"], "Signal unit")
    if len(positive) < 2 or len(negative) < 2:
        raise ValueError("Provide at least two replicates of each control.")
    pos_mean, neg_mean = statistics.mean(positive), statistics.mean(negative)
    pos_sd, neg_sd = statistics.stdev(positive), statistics.stdev(negative)
    delta = abs(pos_mean - neg_mean)
    if delta == 0:
        raise ValueError("Z′ is undefined when positive and negative control means coincide.")
    z_prime = _checked(1 - 3 * (pos_sd + neg_sd) / delta, "Z′-factor")
    high_sd = pos_sd if pos_mean > neg_mean else neg_sd
    raw_window = _checked(delta - 3 * (pos_sd + neg_sd), "Absolute signal window")
    window = (_checked(raw_window / high_sd, "Signal window") if high_sd
              else "undefined (zero high-control SD)")
    def cv(mean: float, sd: float) -> float | str:
        return _checked(100 * sd / abs(mean), "%CV") if mean else "undefined (zero mean)"

    return {
        "summary": (f"Z′-factor {z_prime:.4g}; separation {delta:.6g} {unit}; "
                    "signal window is normalized by the high-signal control SD (n=1). "
                    "No pass/fail threshold is assumed."),
        "values": {
            "positive_n": len(positive), "negative_n": len(negative),
            "positive_mean": pos_mean, "negative_mean": neg_mean,
            "positive_sd": pos_sd, "negative_sd": neg_sd,
            "positive_cv_percent": cv(pos_mean, pos_sd), "negative_cv_percent": cv(neg_mean, neg_sd),
            "z_prime_factor": z_prime, "signal_separation": delta,
            "absolute_signal_window": raw_window, "signal_window": window, "signal_unit": unit,
        },
        "table": {"columns": ["Control", "Replicate", f"Signal ({unit})"],
                  "rows": ([["Positive", i, value] for i, value in enumerate(positive, 1)] +
                           [["Negative", i, value] for i, value in enumerate(negative, 1)])},
    }


OPERATIONS = (
    Operation(
        "plate_layout", "96/384-well assay layout",
        "Display labeled assignments or measured values as a plate-map view; unmeasured wells are not zero.",
        (Field("plate_format", "Plate format", "choice", choices=("96", "384"), unit="wells"),
         Field("wells_json", "Well ID to reading/assignment (JSON)", "text", unit="JSON"),
         Field("reading_label", "Reading label or signal unit", "text", unit="label")),
        {"plate_format": "96", "wells_json": '{"A1":"blank","A2":"standard 1","B1":0,"B2":1.24}',
         "reading_label": "RFU or role"}, plate_layout,
        methods="Row-major 8×12 or 16×24 grids; addresses A1–H12 or A1–P24. Explicit null and omitted wells remain unmeasured.",
    ),
    Operation(
        "standard_curve", "Standard curves and diluted unknowns",
        "Linear, quadratic, symmetric 4PL or asymmetric 5PL calibration with in-range inverse prediction.",
        (Field("model", "Calibration model", "choice", choices=("linear", "quadratic", "4pl", "5pl")),
         Field("standard_concentrations", "Known standard concentrations", "vector", unit="1"),
         Field("standard_signals", "Corresponding standard signals", "vector", unit="1"),
         Field("unknown_signals", "Unknown sample signals", "vector", unit="1"),
         Field("dilution_factors", "Reciprocal sample dilution factors", "vector", unit="1"),
         Field("concentration_unit", "Concentration unit label", "text", unit="label"),
         Field("signal_unit", "Signal unit label", "text", unit="label")),
        {"model": "linear", "standard_concentrations": [0, 1, 2, 3, 4],
         "standard_signals": [0.1, 2.1, 4.1, 6.1, 8.1],
         "unknown_signals": [3.1, 5.1], "dilution_factors": [2, 10],
         "concentration_unit": "ng/mL", "signal_unit": "RFU"},
        standard_curve, timeout_s=45.0,
        methods=("Ordinary unweighted least squares. Linear y=a+bx; quadratic y=a+bx+cx²; "
                 "4PL/5PL y=d+(a-d)/(1+(x/c)^b)^g, g=1 for 4PL. Nonlinear fits use the shared "
                 "bounded Levenberg–Marquardt solver. Inversion is restricted to the measured standard "
                 "concentration range; outside results are flagged, never extrapolated. "
                 "Original-sample concentration = inverse(signal) × reciprocal dilution."),
        citations=(Citation("Bio-Rad five-parameter logistic calibration",
                            "https://www.bio-rad.com/webroot/web/pdf/lsr/literature/10022815.pdf"),),
    ),
    Operation(
        "assay_quality", "Assay quality: Z′, signal window and %CV",
        "Compute replicate-based separation, window and variation for two control populations.",
        (Field("positive_controls", "Positive-control signals", "vector", unit="1"),
         Field("negative_controls", "Negative-control signals", "vector", unit="1"),
         Field("signal_unit", "Signal unit label", "text", unit="label")),
        {"positive_controls": [98, 100, 102, 100], "negative_controls": [9, 10, 11, 10],
         "signal_unit": "RFU"},
        assay_quality,
        methods=("Use sample SD (n−1) for each control. Z′=1−3(SDpos+SDneg)/|meanpos−meanneg|; "
                 "absolute signal window=|meanpos−meanneg|−3(SDpos+SDneg); "
                 "normalized signal window=absolute window/SD of the higher-mean control (n=1); "
                 "%CV=100×SD/|mean|. Undefined denominators are reported as text, not NaN."),
        citations=(Citation("NCBI Assay Validation: Z′ and signal window",
                            "https://www.ncbi.nlm.nih.gov/books/NBK83783/"),),
    ),
)
