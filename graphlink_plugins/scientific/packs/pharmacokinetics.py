"""Pharmacokinetics: single/repeated doses, BSA scaling, and drug combinations.

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
    # Center the time origin and the logarithms: subtracting two large log(C)
    # values erases a shallow but resolvable terminal slope.
    x = [t - times[-count] for t in times[-count:]]
    y = concentrations[-count:]
    if any(v <= 0 for v in y):
        raise ValueError("Terminal concentrations must be strictly positive; exclude BLQ/zero tail points.")
    reference = y[0]
    reference_log = math.log(reference)
    logs = [(math.log1p((v - reference) / reference)
             if abs(v - reference) <= reference / 2 else math.log(v) - reference_log)
            for v in y]
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
        # The quotient C0/C1 may overflow and the difference log(C0)-log(C1)
        # may round to zero for almost-flat yet distinct positive samples.
        q = (-math.log1p((c1 - c0) / c0) if c0 - c1 <= c0 / 2
             else math.log(c0) - math.log(c1))
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




def multiple_dose_prediction(data):
    """Equal, instantaneous IV boluses with linear one-compartment elimination."""
    dose, volume = data["dose"], data["central_volume"]
    rate, interval, count = data["elimination_rate"], data["dosing_interval"], data["number_of_doses"]
    if any(not math.isfinite(v) or v <= 0 for v in (dose, volume, rate, interval)):
        raise ValueError("Dose, central volume, elimination rate and dosing interval must be positive.")
    if type(count) is not int or not 1 <= count <= 200:
        raise ValueError("Number of doses must be an integer from 1 to 200.")

    exponent = rate * interval
    start_concentration = dose / volume
    last_time = count * interval
    if not all(math.isfinite(v) and v > 0 for v in (exponent, start_concentration, last_time)):
        raise ValueError("Multiple-dose parameters exceed the supported numeric range.")

    # expm1 avoids cancellation for intervals much shorter than a half-life.
    one_minus_decay = -math.expm1(-exponent)
    decay = math.exp(-exponent)
    accumulation_ss = 1 / one_minus_decay
    peak_ss = start_concentration * accumulation_ss
    trough_ss = peak_ss * decay
    average_ss = start_concentration / exponent
    half_life = math.log(2) / rate
    time_95_envelope = math.log(20) / rate
    fraction_last = -math.expm1(-count * exponent)
    accumulation_last = fraction_last / one_minus_decay
    peak_last = start_concentration * accumulation_last
    trough_last = peak_last * decay
    scalars = (accumulation_ss, peak_ss, trough_ss, average_ss, half_life,
               time_95_envelope, fraction_last, accumulation_last, peak_last, trough_last)
    if not all(math.isfinite(v) for v in scalars):
        raise ValueError("Multiple-dose predictions exceed the supported numeric range.")

    rows, plot_x, plot_y = [], [], []
    for number in range(1, count + 1):
        time = (number - 1) * interval
        accumulation = -math.expm1(-number * exponent) / one_minus_decay
        peak = start_concentration * accumulation
        pre_dose = max(0.0, peak - start_concentration)
        trough = peak * decay
        rows.append([number, time, pre_dose, peak, trough, accumulation])
        # Duplicate dose timestamps model the actual bolus concentration jump.
        plot_x.extend([time, time, time + interval])
        plot_y.extend([pre_dose, peak, trough])
    if not all(math.isfinite(value) for value in plot_x + plot_y):
        raise ValueError("Multiple-dose profile exceeds the supported numeric range.")

    return {
        "summary": (
            f"After {count} equal IV boluses every {interval:.6g} h, predicted peak "
            f"{peak_last:.6g} mg/L and end-interval trough {trough_last:.6g} mg/L; "
            f"steady-state peak {peak_ss:.6g} mg/L, trough {trough_ss:.6g} mg/L "
            f"(accumulation factor {accumulation_ss:.6g}). "
            "One-compartment, instantaneous IV dosing and linear elimination are assumed."
        ),
        "values": {
            "dose": dose, "dose_unit": "mg", "central_volume": volume, "central_volume_unit": "L",
            "elimination_rate": rate, "elimination_rate_unit": "1/h",
            "dosing_interval": interval, "dosing_interval_unit": "h",
            "number_of_doses": count,
            "terminal_half_life": half_life, "terminal_half_life_unit": "h",
            "accumulation_ratio_after_n_doses": accumulation_last,
            "steady_state_accumulation_ratio": accumulation_ss,
            "peak_after_last_dose": peak_last, "trough_after_last_interval": trough_last,
            "steady_state_peak": peak_ss, "steady_state_trough": trough_ss,
            "steady_state_average": average_ss, "concentration_unit": "mg/L",
            "fraction_of_steady_state_peak_after_n_doses": fraction_last,
            "time_to_95_percent_envelope": time_95_envelope, "time_to_95_percent_envelope_unit": "h",
            "warnings": "Idealized IV bolus; the continuous 95% envelope is not a scheduled dose time",
        },
        "table": {
            "columns": ["Dose number", "Administration time (h)", "Predose (mg/L)",
                        "Postdose peak (mg/L)", "End-interval trough (mg/L)", "Accumulation factor"],
            "rows": rows,
        },
        "plot": {
            "title": "Repeated IV bolus concentration profile (including dose jumps)",
            "xLabel": "Time (h)", "yLabel": "Plasma concentration (mg/L)",
            "x": plot_x, "y": plot_y,
        },
    }


# Fixed reference factors from FDA (2005) Table 3; adult-human Km=37 at 60 kg.
_ANIMAL_KM = {
    "mouse": 3, "hamster": 5, "rat": 6, "guinea_pig": 8,
    "rabbit": 12, "dog": 20, "monkey": 12, "mini_pig": 35,
}
_HUMAN_KM = 37


def human_equivalent_dose(data):
    """BSA-normalized animal-to-adult-human dose equivalence, not a safe dose."""
    species = data["species"]
    if species not in _ANIMAL_KM:
        raise ValueError("Unsupported species for reference Km conversion.")
    dose, human_weight = data["animal_dose"], data["human_weight"]
    if not math.isfinite(dose) or dose <= 0 or not math.isfinite(human_weight) or human_weight <= 0:
        raise ValueError("Animal dose and human weight must be positive.")
    factor = _ANIMAL_KM[species] / _HUMAN_KM
    hed = dose * factor
    total = hed * human_weight
    if not all(math.isfinite(v) and v > 0 for v in (hed, total)):
        raise ValueError("Scaled dose exceeds the supported numeric range.")
    sample_doses = [dose * fraction for fraction in (0, 0.25, 0.5, 0.75, 1)]
    sample_heds = [value * factor for value in sample_doses]
    warnings = (
        "HED is a surface-area conversion, not a human starting dose: "
        "no NOAEL, safety factor, exposure, route or species relevance was assessed"
    )
    if human_weight != 60:
        warnings += "; fixed adult-human Km=37 is referenced to 60 kg, not individualized BSA"
    return {
        "summary": (
            f"{species.replace('_', ' ').title()} {dose:.6g} mg/kg at Km={_ANIMAL_KM[species]} "
            f"corresponds to adult-reference HED {hed:.6g} mg/kg (Km=37); "
            f"{total:.6g} mg is the arithmetic equivalent for {human_weight:.6g} kg, "
            "not a clinical dose recommendation."
        ),
        "values": {
            "species": species, "animal_dose": dose, "animal_dose_unit": "mg/kg",
            "animal_km": _ANIMAL_KM[species], "human_km": _HUMAN_KM,
            "bsa_conversion_factor": factor,
            "human_equivalent_dose": hed, "human_equivalent_dose_unit": "mg/kg",
            "human_weight": human_weight, "human_weight_unit": "kg",
            "human_total_equivalent": total, "human_total_equivalent_unit": "mg",
            "warnings": warnings,
        },
        "table": {
            "columns": ["Animal dose (mg/kg)", "Adult-reference HED (mg/kg)",
                        f"Arithmetic total for {human_weight:g} kg (mg)"],
            "rows": [[animal, scaled, scaled * human_weight]
                     for animal, scaled in zip(sample_doses, sample_heds)],
        },
        "plot": {
            "title": f"FDA Km-based BSA scaling: {species.replace('_', ' ')} to adult",
            "xLabel": "Animal dose (mg/kg)", "yLabel": "Adult-reference HED (mg/kg)",
            "x": sample_doses, "y": sample_heds,
        },
    }


def _median_effect_fraction(dose, median_dose, slope):
    """Stable rising median-effect response f/(1-f)=(dose/Dm)**m."""
    if dose == 0:
        return 0.0
    log_odds = slope * (math.log(dose) - math.log(median_dose))
    if log_odds >= 0:
        return 1 / (1 + math.exp(-log_odds))
    odds = math.exp(log_odds)
    return odds / (1 + odds)


def _single_agent_equieffective_dose(median_dose, slope, fraction):
    log_dose = math.log(median_dose) + (math.log(fraction) - math.log1p(-fraction)) / slope
    try:
        amount = math.exp(log_dose)
    except OverflowError:
        amount = math.inf
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("Single-agent dose at the observed effect exceeds the supported numeric range.")
    return amount


def drug_combination_analysis(data):
    """Bliss excess and Chou-Talalay CI from independently fitted median-effect curves."""
    unit = _label(data["concentration_unit"], "Concentration")
    try:
        convert_decimal_measurement("1", unit, "mg/L")
    except ValueError:
        try:
            convert_decimal_measurement("1", unit, "mol/L")
        except ValueError:
            raise ValueError("Concentration unit must represent mass/volume or amount/volume.") from None
    dose_a, dose_b = data["dose_a"], data["dose_b"]
    median_a, median_b = data["median_effect_dose_a"], data["median_effect_dose_b"]
    slope_a, slope_b = data["slope_a"], data["slope_b"]
    effect = data["observed_combination_effect"]
    if any(not math.isfinite(v) or v <= 0 for v in
           (dose_a, dose_b, median_a, median_b, slope_a, slope_b)):
        raise ValueError("Doses, median-effect doses and slopes must be positive.")
    if not math.isfinite(effect) or not 0 < effect < 1:
        raise ValueError("Observed combination effect must be strictly between zero and one for CI.")
    model = data["interaction_model"]
    if model not in ("exclusive", "nonexclusive"):
        raise ValueError("Interaction model must be exclusive or nonexclusive.")

    single_a = _median_effect_fraction(dose_a, median_a, slope_a)
    single_b = _median_effect_fraction(dose_b, median_b, slope_b)
    bliss = single_a + single_b - single_a * single_b
    excess = effect - bliss

    # Dx is the standalone concentration required to reproduce the observed
    # combination effect, not the standalone response at its combination dose.
    dx_a = _single_agent_equieffective_dose(median_a, slope_a, effect)
    dx_b = _single_agent_equieffective_dose(median_b, slope_b, effect)
    fraction_a, fraction_b = dose_a / dx_a, dose_b / dx_b
    exclusive_ci = fraction_a + fraction_b
    nonexclusive_ci = exclusive_ci + fraction_a * fraction_b
    ci = exclusive_ci if model == "exclusive" else nonexclusive_ci
    if not all(math.isfinite(v) for v in
               (single_a, single_b, bliss, excess, fraction_a, fraction_b,
                exclusive_ci, nonexclusive_ci, ci)):
        raise ValueError("Combination predictions exceed the supported numeric range.")

    scales = [0, 0.25, 0.5, 0.75, 1]
    rows = []
    for scale in scales:
        a = _median_effect_fraction(dose_a * scale, median_a, slope_a)
        b = _median_effect_fraction(dose_b * scale, median_b, slope_b)
        rows.append([scale, dose_a * scale, dose_b * scale, a, b, a + b - a * b])
    if not all(math.isfinite(v) for row in rows for v in row):
        raise ValueError("Combination response profile exceeds the supported numeric range.")

    return {
        "summary": (
            f"Bliss independent-action expectation {bliss:.6g} versus observed affected "
            f"fraction {effect:.6g} (excess {excess:+.6g}); Chou-Talalay CI "
            f"{ci:.6g} ({model} model) at the observed effect. "
            "References are model-based, not statistical tests of synergy."
        ),
        "values": {
            "concentration_unit": unit, "observed_combination_effect": effect,
            "single_agent_effect_a": single_a, "single_agent_effect_b": single_b,
            "bliss_expected_effect": bliss, "bliss_excess_effect": excess,
            "bliss_direction": "above" if excess > 0 else "below" if excess < 0 else "equal",
            "equieffective_single_agent_dose_a": dx_a,
            "equieffective_single_agent_dose_b": dx_b, "equieffective_dose_unit": unit,
            "dose_fraction_a": fraction_a, "dose_fraction_b": fraction_b,
            "ci_exclusive": exclusive_ci, "ci_nonexclusive": nonexclusive_ci,
            "combination_index": ci, "interaction_model": model,
            "ci_direction": "below 1" if ci < 1 else "above 1" if ci > 1 else "equal to 1",
            "warnings": ("Interpretation depends on independently fitted single-agent median-effect "
                         "curves and a matched endpoint/time; no replicate uncertainty or clinical "
                         "significance was assessed"),
        },
        "table": {
            "columns": ["Fixed-ratio scale", f"Drug A ({unit})", f"Drug B ({unit})",
                        "Single A effect", "Single B effect", "Bliss expected effect"],
            "rows": rows,
        },
        "plot": {
            "title": "Modeled Bliss expectation along the fixed-ratio dose ray",
            "xLabel": "Fraction of tested combination doses", "yLabel": "Expected affected fraction",
            "x": scales, "y": [row[-1] for row in rows],
        },
    }




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
        headline="auc_inf",
        paired_fields=(("time", "concentration"),),
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
        headline="clearance",
        paired_fields=(("time", "concentration"),),
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
        headline="clearance",
        paired_fields=(("time", "concentration"),),
    ),

    Operation(
        "multiple_dose_prediction", "Predict repeated IV-bolus concentrations",
        "Accumulation through repeated one-compartment doses and asymptotic peak, trough and average steady state.",
        (Field("dose", "Dose per IV bolus", "number", unit="mg"),
         Field("central_volume", "Central distribution volume", "number", unit="L"),
         Field("elimination_rate", "First-order elimination rate", "number", unit="1/h"),
         Field("dosing_interval", "Interval between doses", "number", unit="h"),
         Field("number_of_doses", "Number of doses to simulate", "integer", unit="1")),
        {"dose": 100, "central_volume": 10, "elimination_rate": 0.2,
         "dosing_interval": 8, "number_of_doses": 8},
        multiple_dose_prediction,
        methods="One-compartment equal IV boluses: Cmax,n=(Dose/V)*(1-exp(-n*k*tau))/(1-exp(-k*tau)); "
                "Cmin,n=Cmax,n*exp(-k*tau). Rss=1/(1-exp(-k*tau)); "
                "Css,avg=Dose/(V*k*tau). Numerically stable expm1 for short intervals; "
                "continuous 95% accumulation envelope occurs at ln(20)/k, distinct from actual dose times. "
                "Assumes linear, time-invariant, first-order IV-bolus elimination.",
        citations=(Citation("McGraw Hill: multiple-dose pharmacokinetic models",
                            "https://accesspharmacy.mhmedical.com/content.aspx?bookid=2577&sectionid=234135883"),),
        headline="steady_state_average",
    ),
    Operation(
        "human_equivalent_dose", "Scale animal dose to adult human by BSA",
        "FDA reference Km-factor animal-to-human equivalence in mg/kg and arithmetic adult total.",
        (Field("species", "Animal species (FDA reference factor)", "choice", unit="category (dimensionless)",
               choices=tuple(_ANIMAL_KM)),
         Field("animal_dose", "Animal administered dose", "number", unit="mg/kg"),
         Field("human_weight", "Illustrative adult human body weight", "number", unit="kg")),
        {"species": "mouse", "animal_dose": 100, "human_weight": 60},
        human_equivalent_dose,
        methods="Adult-reference HED(mg/kg)=animal dose(mg/kg)*animal Km/37, using FDA (2005) "
                "Table 3 species reference Km factors, with adult human Km=37 at 60 kg. "
                "The total is HED times supplied adult weight, not a safe starting dose, "
                "MRSD or recommendation; no toxicity, exposure, route, species selection or "
                "safety factor is considered.",
        citations=(Citation("FDA 2005 guidance: BSA scaling, Table 3",
                            "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/estimating-maximum-safe-starting-dose-initial-clinical-trials-therapeutics-adult-healthy-volunteers"),),
        headline="human_equivalent_dose",
    ),
    Operation(
        "drug_combination_analysis", "Bliss and Chou-Talalay drug combination analysis",
        "Compare combined effect with Bliss independence and compute Chou-Talalay CI from fitted single-agent median-effect curves.",
        (Field("concentration_unit", "Shared dose/concentration unit (e.g. mg/L or uM)",
               "text", unit="unit label"),
         Field("dose_a", "Drug A combination concentration", "number", unit="selected concentration unit", default_unit="mg/L"),
         Field("median_effect_dose_a", "Drug A fitted median-effect concentration (Dm)", "number",
               unit="selected concentration unit", default_unit="mg/L"),
         Field("slope_a", "Drug A fitted median-effect slope (m)", "number", unit="1"),
         Field("dose_b", "Drug B combination concentration", "number", unit="selected concentration unit", default_unit="mg/L"),
         Field("median_effect_dose_b", "Drug B fitted median-effect concentration (Dm)", "number",
               unit="selected concentration unit", default_unit="mg/L"),
         Field("slope_b", "Drug B fitted median-effect slope (m)", "number", unit="1"),
         Field("observed_combination_effect", "Observed affected fraction (0–1)", "number", unit="1"),
         Field("interaction_model", "Chou-Talalay interaction model", "choice",
               unit="category (dimensionless)", choices=("exclusive", "nonexclusive"))),
        {"concentration_unit": "mg/L", "dose_a": 1, "median_effect_dose_a": 2,
         "slope_a": 1, "dose_b": 2, "median_effect_dose_b": 4, "slope_b": 1,
         "observed_combination_effect": 0.75, "interaction_model": "exclusive"},
        drug_combination_analysis,
        methods="From independently fitted single-agent median-effect curves fa/(1-fa)=(D/Dm)^m, "
                "compute single-agent responses at the tested doses and Bliss E_A+E_B-E_A*E_B. "
                "For observed 0<fa<1, Dx_j=Dm_j*(fa/(1-fa))^(1/m_j). "
                "Chou-Talalay exclusive CI=D_A/Dx_A+D_B/Dx_B; nonexclusive CI additionally "
                "includes (D_A/Dx_A)*(D_B/Dx_B). Effects require the same endpoint and time; "
                "CI and Bliss excess are descriptive, without uncertainty testing.",
        citations=(Citation("Chou (2006), Pharmacological Reviews",
                            "https://pubmed.ncbi.nlm.nih.gov/16968952/"),
                   Citation("Review: drug combination reference models and Bliss independence",
                            "https://pmc.ncbi.nlm.nih.gov/articles/PMC9127325/")),
        headline="combination_index",
    ),
)
