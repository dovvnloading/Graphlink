"""Single-dose pharmacokinetics: NCA and IV-bolus compartment fits.

All methods are local, deterministic, and use the Workbench's existing
bounded nonlinear least-squares solver. No numerical third-party dependency.
Concentrations must be mass/volume and dose is converted to milligrams.
"""

from __future__ import annotations

import math
import statistics

from graphlink_plugins.scientific.core import Citation, Field, Operation
from graphlink_plugins.scientific.packs.enzyme_advanced import _fit, _se
from graphlink_plugins.scientific.units import convert_decimal_measurement


def _label(value, name):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 40 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name} must be a unit label of 1–40 characters.")
    return value.strip()


def _data(data, minimum, *, strictly_positive=False):
    time_unit = _label(data["time_unit"], "Time")
    concentration_unit = _label(data["response_unit"], "Concentration")
    try:
        convert_decimal_measurement("1", time_unit, "h")
        mass_per_liter = convert_decimal_measurement("1", concentration_unit, "mg/L")
    except ValueError:
        raise ValueError("Time must use a time unit and concentration must use a mass/volume unit (e.g. mg/L).") from None
    times, concentrations = data["time"], data["concentration"]
    if len(times) != len(concentrations) or len(times) < minimum:
        raise ValueError(f"At least {minimum} aligned time/concentration observations are required.")
    if times[0] != 0 or any(t < 0 for t in times) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Times must start at zero and be strictly increasing and nonnegative.")
    if any(c < 0 or (strictly_positive and c == 0) for c in concentrations):
        raise ValueError("Concentrations must be nonnegative (strictly positive for compartment fitting).")
    if max(concentrations) <= 0 or len(set(concentrations)) < 3:
        raise ValueError("A varying, positive concentration response is required.")
    if data["dose"] <= 0:
        raise ValueError("Administered dose must be positive.")
    return times, concentrations, time_unit, concentration_unit, mass_per_liter


def _terminal(times, concentrations, count):
    if not 3 <= count <= len(times) - 1:
        raise ValueError("Terminal fitting requires 3 or more points, leaving an earlier observation.")
    peak_index = max(range(len(concentrations)), key=concentrations.__getitem__)
    if len(times) - count <= peak_index:
        raise ValueError("Terminal points must start after the observed peak concentration.")
    x, y = times[-count:], concentrations[-count:]
    if any(v <= 0 for v in y):
        raise ValueError("Terminal concentrations must be strictly positive; exclude BLQ/zero tail points.")
    logs = [math.log(v) for v in y]
    mean_t, mean_log = statistics.mean(x), statistics.mean(logs)
    variance = math.fsum((t - mean_t) ** 2 for t in x)
    slope = math.fsum((t - mean_t) * (v - mean_log) for t, v in zip(x, logs)) / variance
    if slope >= 0 or not math.isfinite(slope):
        raise ValueError("Terminal concentrations must have a negative log-linear slope.")
    predicted = [mean_log + slope * (t - mean_t) for t in x]
    residual = math.fsum((v - p) ** 2 for v, p in zip(logs, predicted))
    total = math.fsum((v - mean_log) ** 2 for v in logs)
    return -slope, max(0.0, 1 - residual / total)


def _interval_integrals(t0, t1, c0, c1):
    """Linear-up/log-down interval AUC and AUMC (including stable near-flat tail)."""
    dt = t1 - t0
    if c0 > 0 and c1 > 0 and c1 < c0:
        q = math.log(c0) - math.log(c1)
        area = c0 * dt * (-math.expm1(-q)) / q
        if q < 0.01:
            # Integral_0^1 u exp(-q*u) du, Taylor expanded near q=0.
            moment = c0 * dt * dt * (0.5 - q / 3 + q**2 / 8 - q**3 / 30 + q**4 / 144)
        else:
            moment = c0 * dt * dt * (1 - (1 + q) * math.exp(-q)) / (q * q)
        return area, t0 * area + moment
    area = dt * (c0 + c1) / 2
    # Interpolate C linearly; integrate t*C exactly (not a trapezoid on t*C).
    moment = dt * dt * (c0 / 6 + c1 / 3)
    return area, t0 * area + moment


def noncompartmental_analysis(data):
    times, concentrations, time_unit, unit, mass_per_liter = _data(data, 5)
    route, count = data["route"], data["terminal_points"]
    if route not in ("iv_bolus", "extravascular"):
        raise ValueError("Route must be iv_bolus or extravascular.")
    if route == "iv_bolus" and concentrations[0] <= 0:
        raise ValueError("IV bolus NCA requires a positive time-zero concentration.")
    elimination, terminal_r2 = _terminal(times, concentrations, count)
    integrals = [_interval_integrals(t0, t1, c0, c1) for t0, t1, c0, c1 in
                 zip(times, times[1:], concentrations, concentrations[1:])]
    auc_last = math.fsum(part[0] for part in integrals)
    aumc_last = math.fsum(part[1] for part in integrals)
    last_t, last_c = times[-1], concentrations[-1]
    auc_extra = last_c / elimination
    aumc_extra = last_t * auc_extra + last_c / (elimination * elimination)
    auc_inf = auc_last + auc_extra
    aumc_inf = aumc_last + aumc_extra
    mrt = aumc_inf / auc_inf
    clearance = data["dose"] / (auc_inf * mass_per_liter)
    volume = clearance / elimination
    extrapolated_pct = 100 * auc_extra / auc_inf
    if not all(math.isfinite(v) and v > 0 for v in (auc_inf, aumc_inf, mrt, clearance, volume)):
        raise ValueError("NCA parameters exceed the supported numeric range.")
    maximum = max(concentrations)
    tmax = times[concentrations.index(maximum)]
    warnings = []
    if extrapolated_pct > 20:
        warnings.append("AUC extrapolation exceeds 20%; extend terminal sampling")
    if terminal_r2 < 0.9:
        warnings.append("Terminal log-linear fit has R² below 0.90")
    if route == "extravascular":
        warnings.append("Unknown bioavailability: clearance and terminal volume are CL/F and Vz/F")
    values = {
        "route": route, "observations": len(times), "dose": data["dose"], "dose_unit": "mg",
        "cmax": maximum, "cmax_unit": unit, "tmax": tmax, "tmax_unit": time_unit,
        "auc_last": auc_last, "auc_inf": auc_inf, "auc_extrapolated": auc_extra,
        "auc_unit": f"({unit})*{time_unit}",
        "auc_extrapolated_percent": extrapolated_pct,
        "aumc_last": aumc_last, "aumc_inf": aumc_inf,
        "aumc_unit": f"({unit})*{time_unit}²",
        "terminal_points": count, "terminal_r_squared": terminal_r2,
        "lambda_z": elimination, "lambda_z_unit": f"1/{time_unit}",
        "terminal_half_life": math.log(2) / elimination, "terminal_half_life_unit": time_unit,
        "mrt": mrt, "mrt_unit": time_unit,
        "warnings": "; ".join(warnings) if warnings else "none",
    }
    if route == "iv_bolus":
        values.update({"clearance": clearance, "clearance_unit": f"L/{time_unit}",
                       "volume_z": volume, "volume_z_unit": "L",
                       "volume_ss": clearance * mrt, "volume_ss_unit": "L"})
    else:
        values.update({"clearance_over_f": clearance, "clearance_over_f_unit": f"L/{time_unit}",
                       "volume_z_over_f": volume, "volume_z_over_f_unit": "L"})
    return {
        "summary": (
            f"Observed Cmax={maximum:.6g} {unit} at Tmax={tmax:.6g} {time_unit}; "
            f"AUC∞={auc_inf:.6g} {unit}·{time_unit}, terminal t½={math.log(2)/elimination:.6g} "
            f"{time_unit}. {'CL and Vz' if route == 'iv_bolus' else 'Apparent CL/F and Vz/F'} "
            "assume a single dose and linear, time-invariant elimination."
        ),
        "values": values,
        "table": {"columns": [f"Time ({time_unit})", f"Observed concentration ({unit})",
                               f"Interval AUC ({unit}*{time_unit})"],
                  "rows": [[t, c, integrals[i-1][0] if i else 0.0]
                           for i, (t, c) in enumerate(zip(times, concentrations))]},
        "plot": {"title": "Noncompartmental concentration-time profile",
                 "xLabel": f"Time ({time_unit})", "yLabel": f"Concentration ({unit})",
                 "x": times, "y": concentrations},
    }


def _prediction(model):
    def predict(x, theta):
        if model == "one":
            amplitude, rate = math.exp(theta[0]), math.exp(theta[1])
            estimate = amplitude * math.exp(-rate * x)
            return estimate, [estimate, -estimate * rate * x]
        fast_a, slow_b = math.exp(theta[0]), math.exp(theta[1])
        beta, delta = math.exp(theta[2]), math.exp(theta[3])
        fast = fast_a * math.exp(-(beta + delta) * x)
        slow = slow_b * math.exp(-beta * x)
        return fast + slow, [fast, slow, -beta * x * (fast + slow), -delta * x * fast]
    return predict


def _fit_compartment(data, model):
    minimum = 8 if model == "two" else 6
    times, concentrations, time_unit, unit, mass_per_liter = _data(data, minimum, strictly_positive=True)
    if concentrations[-1] >= concentrations[0]:
        raise ValueError("IV bolus compartment fitting needs an overall declining concentration profile.")
    time_scale = statistics.median(times[1:])
    response_scale = max(concentrations)
    xs = [t / time_scale for t in times]
    ys = [c / response_scale for c in concentrations]
    if any(not math.isfinite(x) for x in xs):
        raise ValueError("Time sampling spans an unsupported numeric range.")
    # The last observations seed the slow disposition rate. Wider seeds cover
    # incomplete terminal sampling without silently using transformed OLS as a fit.
    tail = min(4, len(times) - 1)
    tail_logs = [math.log(c) for c in concentrations[-tail:]]
    tail_times = times[-tail:]
    t_mean, y_mean = statistics.mean(tail_times), statistics.mean(tail_logs)
    slope = math.fsum((t - t_mean) * (y - y_mean) for t, y in zip(tail_times, tail_logs)) / math.fsum(
        (t - t_mean) ** 2 for t in tail_times)
    rate_guess = -slope if slope < 0 else math.log(concentrations[0] / concentrations[-1]) / times[-1]
    rate_guess = max(1e-9, min(1e6, rate_guess * time_scale))
    if model == "one":
        starts = [[math.log(a), math.log(max(1e-9, rate_guess * k))]
                  for a in (1.0, 1.3) for k in (0.5, 1, 2, 4)]
        bounds = [(-16, 5), (-16, 9)]
    else:
        starts = [
            [math.log(frac), math.log(1 - frac),
             math.log(max(1e-9, rate_guess * slow)),
             math.log(max(1e-9, rate_guess * slow * separation))]
            for frac in (0.2, 0.5, 0.8)
            for slow in (0.4, 1, 1.6)
            for separation in (2, 8, 20)
        ]
        bounds = [(-16, 5), (-16, 5), (-16, 9), (-16, 9)]
    theta, predictions, covariance, iterations, warnings = _fit(
        _prediction(model), xs, ys, starts, bounds)
    fitted = [y * response_scale for y in predictions]
    sse = math.fsum((a - b) ** 2 for a, b in zip(concentrations, fitted))
    total = math.fsum((c - statistics.mean(concentrations)) ** 2 for c in concentrations)
    p = len(theta)
    k = p + 1  # Include estimated Gaussian residual variance.
    if math.fsum((a - b) ** 2 for a, b in zip(ys, predictions)) <= 1e-26:
        aic = aicc = bic = "undefined (zero residual variance)"
    else:
        likelihood = len(times) * (math.log(2 * math.pi) + 1 + math.log(sse / len(times)))
        aic = likelihood + 2 * k
        aicc = aic + 2 * k * (k + 1) / (len(times) - k - 1)
        bic = likelihood + k * math.log(len(times))
    values = {
        "model": model, "observations": len(times), "parameters": p,
        "degrees_of_freedom": len(times) - p, "iterations": iterations, "converged": "true",
        "residual_sum_squares": sse, "residual_sum_squares_unit": f"({unit})²",
        "residual_standard_deviation": math.sqrt(sse / (len(times) - p)),
        "residual_standard_deviation_unit": unit,
        "r_squared": 1 - sse / total, "r_squared_unit": "1 (dimensionless)",
        "aic": aic, "aicc": aicc, "bic": bic,
        "dose": data["dose"], "dose_unit": "mg",
    }
    if model == "one":
        amp = math.exp(theta[0]) * response_scale
        rate = math.exp(theta[1]) / time_scale
        auc = amp / rate
        mrt = 1 / rate
        volume = data["dose"] / (amp * mass_per_liter)
        clearance = rate * volume
        values.update({
            "initial_concentration": amp, "initial_concentration_unit": unit,
            "initial_concentration_standard_error": _se(covariance, [amp, 0]),
            "elimination_rate": rate, "elimination_rate_unit": f"1/{time_unit}",
            "elimination_rate_standard_error": _se(covariance, [0, rate]),
            "terminal_half_life": math.log(2) / rate, "terminal_half_life_unit": time_unit,
            "central_volume": volume, "central_volume_unit": "L",
            "volume_z": volume, "volume_z_unit": "L",
            "volume_ss": volume, "volume_ss_unit": "L",
        })
    else:
        a, b = math.exp(theta[0]) * response_scale, math.exp(theta[1]) * response_scale
        beta, delta = math.exp(theta[2]) / time_scale, math.exp(theta[3]) / time_scale
        alpha = beta + delta
        auc = a / alpha + b / beta
        mrt = (a / alpha**2 + b / beta**2) / auc
        volume = data["dose"] / ((a + b) * mass_per_liter)
        clearance = data["dose"] / (auc * mass_per_liter)
        k21 = (a * beta + b * alpha) / (a + b)
        k10 = alpha * beta / k21
        k12 = alpha + beta - k21 - k10
        if alpha / beta < 1.5 or min(a, b) / (a + b) < 0.03:
            warnings.append("Disposition phases overlap or one amplitude is small; two compartments may be unidentifiable")
        values.update({
            "fast_amplitude": a, "fast_amplitude_unit": unit,
            "fast_amplitude_standard_error": _se(covariance, [a, 0, 0, 0]),
            "slow_amplitude": b, "slow_amplitude_unit": unit,
            "slow_amplitude_standard_error": _se(covariance, [0, b, 0, 0]),
            "alpha": alpha, "alpha_unit": f"1/{time_unit}",
            "alpha_standard_error": _se(covariance, [0, 0, beta, delta]),
            "beta": beta, "beta_unit": f"1/{time_unit}",
            "beta_standard_error": _se(covariance, [0, 0, beta, 0]),
            "k10": k10, "k12": k12, "k21": k21, "micro_rate_unit": f"1/{time_unit}",
            "terminal_half_life": math.log(2) / beta, "terminal_half_life_unit": time_unit,
            "central_volume": volume, "central_volume_unit": "L",
            "volume_z": clearance / beta, "volume_z_unit": "L",
            "volume_ss": clearance * mrt, "volume_ss_unit": "L",
        })
    values.update({
        "auc_inf": auc, "auc_unit": f"({unit})*{time_unit}",
        "mrt": mrt, "mrt_unit": time_unit,
        "clearance": clearance, "clearance_unit": f"L/{time_unit}",
        "warnings": "; ".join(warnings) if warnings else "none",
    })
    if not all(math.isfinite(v) for v in values.values() if type(v) in (int, float)):
        raise ValueError("Fitted pharmacokinetic parameters exceed the supported numerical range.")
    label = "One" if model == "one" else "Two"
    return {
        "summary": f"{label}-compartment IV bolus nonlinear concentration-space fit: "
                   f"AUC∞={auc:.6g} {unit}·{time_unit}, CL={clearance:.6g} L/{time_unit}, "
                   f"terminal t½={values['terminal_half_life']:.6g} {time_unit}. "
                   "Fits assume linear, time-invariant distribution/elimination after a single IV bolus.",
        "values": values,
        "table": {"columns": [f"Time ({time_unit})", f"Observed ({unit})", f"Fitted ({unit})",
                               f"Residual ({unit})"],
                  "rows": [[t, c, f, c - f] for t, c, f in zip(times, concentrations, fitted)]},
        "plot": {"title": f"{label}-compartment IV bolus fit", "xLabel": f"Time ({time_unit})",
                 "yLabel": f"Fitted concentration ({unit})", "x": times, "y": fitted},
    }


def one_compartment_fit(data):
    return _fit_compartment(data, "one")


def two_compartment_fit(data):
    return _fit_compartment(data, "two")


_FIELDS = (
    Field("time", "Time since dose", "vector", unit="selected time unit"),
    Field("concentration", "Plasma concentration", "vector", unit="selected response unit"),
    Field("dose", "Administered dose (mass)", "number", unit="mg"),
    Field("time_unit", "Time unit (e.g. h)", "text", unit="unit label"),
    Field("response_unit", "Concentration unit (e.g. mg/L)", "text", unit="unit label"),
)
_TIMES = [0, 0.25, 0.5, 1, 2, 3, 4, 6, 8, 10, 14, 18]
_EXAMPLE = {"time": _TIMES,
            "concentration": [round(8 * math.exp(-t) + 2 * math.exp(-0.12 * t), 7) for t in _TIMES],
            "dose": 100, "time_unit": "h", "response_unit": "mg/L"}
_NCA_EXAMPLE = {"time": list(range(9)),
                "concentration": [round(10 * math.exp(-0.25 * t), 7) for t in range(9)],
                "dose": 100, "time_unit": "h", "response_unit": "mg/L",
                "route": "iv_bolus", "terminal_points": 4}
_NCA_CITES = (Citation("pkpdutils: Noncompartmental analysis and trapezoidal rules",
                       "https://matthiaskoenig.github.io/pkpdutils/nca/"),)
_COMP_CITES = (Citation("ASHP, Concepts in Clinical Pharmacokinetics: Two-Compartment Models",
                        "https://ashp.mhmedical.com/content.aspx?bookid=3751&sectionid=311145061"),)

OPERATIONS = (
    Operation(
        "noncompartmental_analysis", "Noncompartmental pharmacokinetic analysis",
        "Observed Cmax/Tmax, linear-up/log-down AUC and AUMC, terminal half-life, MRT, clearance and volume.",
        _FIELDS + (Field("route", "Dose route", "choice", unit="category (dimensionless)",
                         choices=("iv_bolus", "extravascular")),
                   Field("terminal_points", "Number of final terminal samples", "integer", unit="1")),
        _NCA_EXAMPLE, noncompartmental_analysis,
        methods="Single-dose NCA with zero-time baseline and linear-up/log-down integration. "
                "Regress log(concentration) on the last selected positive samples after Cmax; "
                "lambda_z=-slope, t1/2=ln(2)/lambda_z. Extrapolate AUC by Clast/lambda_z "
                "and AUMC by tlast*Clast/lambda_z+Clast/lambda_z²; MRT=AUMCinf/AUCinf. "
                "IV bolus: CL=Dose/AUCinf, Vz=CL/lambda_z, Vss=CL*MRT; "
                "extravascular: report only CL/F and Vz/F because F is not known. "
                "Concentrations must be mass/volume, dose mass, time a time dimension.",
        citations=_NCA_CITES,
    ),
    Operation(
        "one_compartment_fit", "Fit one-compartment IV bolus PK",
        "Fit C(t)=A exp(-kt) in untransformed concentration space and derive PK parameters.",
        _FIELDS, {key: _NCA_EXAMPLE[key] for key in ("time", "concentration", "dose", "time_unit", "response_unit")}, one_compartment_fit,
        methods="Unweighted nonlinear least squares with log-positive amplitude and elimination rate. "
                "One-compartment IV bolus C(t)=A exp(-kt), CL=k Dose/A, V=Dose/A, "
                "AUC=A/k, MRT=1/k. Local standard errors are Jacobian-linearized. "
                "Use only single-dose, linear time-invariant IV bolus profiles.",
        citations=_COMP_CITES,
    ),
    Operation(
        "two_compartment_fit", "Fit two-compartment IV bolus PK",
        "Fit a positive, ordered biexponential C(t)=A exp(-alpha t)+B exp(-beta t), alpha>beta.",
        _FIELDS, _EXAMPLE, two_compartment_fit,
        methods="Unweighted nonlinear least squares with positive amplitudes and ordered rates "
                "alpha=beta+delta, beta,delta>0. CL=Dose/(A/alpha+B/beta); "
                "k21=(A beta+B alpha)/(A+B), k10=alpha beta/k21, k12=alpha+beta-k21-k10; "
                "Vc=Dose/(A+B), Vss=CL*MRT, Vz=CL/beta, MRT=(A/alpha²+B/beta²)/AUC. "
                "Local standard errors are Jacobian-linearized, and weak phase separation is flagged.",
        citations=_COMP_CITES,
    ),
)
