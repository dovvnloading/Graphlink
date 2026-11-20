"""Environmental measurement, air quality, water quality and catchment models."""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from statistics import median
from fractions import Fraction
from itertools import accumulate

from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.reference_data import Dataset

REFERENCE_DATASETS = (
    Dataset("environment.epa_aqi_breakpoints", "2024.0.0", "environment",
            ("_AQI_INDEX_BANDS", "_AQI_BREAKPOINTS", "_AQI_UNITS", "_AQI_CATEGORIES"),
            "US EPA-454/B-24-002 (May 2024), Table 6"),
    Dataset("environment.briggs_pg_rural", "1.0.0", "environment", ("_PG_RURAL",),
            "Briggs rural Pasquill–Gifford coefficients"),
    Dataset("environment.briggs_pg_urban", "1.0.0", "environment", ("_PG_URBAN",),
            "Briggs urban Pasquill–Gifford coefficients"),
)


def _plume_reference_datasets(inputs: dict) -> tuple[str, ...]:
    terrain = inputs.get("terrain", "")
    if isinstance(terrain, str) and terrain.strip().lower() in ("rural", "urban"):
        return ("environment.briggs_pg_" + terrain.strip().lower(),)
    return ()



def flow_weighted_concentration(data):
    flows, times = data["flow_m3_s"], data["duration_s"]
    concentrations = data["concentration_mg_l"]
    if not flows or not len(flows) == len(times) == len(concentrations):
        raise ValueError("Provide flow, duration, and concentration for every interval.")
    if any(x < 0 for x in flows + concentrations) or any(t <= 0 for t in times):
        raise ValueError("Flows and concentrations must be nonnegative; durations must be positive.")
    volumes = [Fraction(q) * Fraction(t) for q, t in zip(flows, times)]
    total_volume = sum(volumes)
    if total_volume == 0:
        raise ValueError("At least one interval must contribute nonzero water volume.")
    # mg/L × m³ = g; convert grams to kg for the reported integrated mass.
    masses = [v * Fraction(c) / 1000 for v, c in zip(volumes, concentrations)]
    total_mass = sum(masses)
    mean = float(1000 * total_mass / total_volume)
    total_volume = float(total_volume)
    return {
        "summary": f"Flow-weighted mean concentration is {mean:.6g} mg/L over {total_volume:.6g} m³.",
        "values": {
            "mean_concentration_mg_l": mean,
            "total_water_m3": total_volume,
            "total_constituent_kg": float(total_mass),
        },
        "table": {
            "columns": ["Interval", "Water volume (m³)", "Constituent mass (kg)"],
            "rows": [[i, float(v), float(mass)] for i, (v, mass) in enumerate(zip(volumes, masses), 1)],
        },
    }


def rainfall_runoff(data):
    rain, area, runoff = data["rainfall_mm"], data["area_km2"], data["runoff_volume_m3"]
    if rain <= 0 or area <= 0 or runoff < 0:
        raise ValueError("Rainfall and catchment area must be positive, and runoff volume must be nonnegative.")
    rainfall_volume = Fraction(rain) * Fraction(area) * 1000
    coefficient = float(Fraction(runoff) / rainfall_volume)
    summary = f"Observed runoff coefficient is {coefficient:.6g} for the supplied event."
    if coefficient > 1:
        summary += " Runoff exceeds rainfall for this event boundary; check inflows, storage, and measurements."
    return {
        "summary": summary,
        "values": {
            "rainfall_volume_m3": float(rainfall_volume),
            "runoff_depth_mm": float(Fraction(runoff) / Fraction(area) / 1000),
            "runoff_coefficient": coefficient,
        },
    }


def degree_days(data):
    lows, highs, base = data["daily_min_c"], data["daily_max_c"], data["base_c"]
    if not lows or len(lows) != len(highs):
        raise ValueError("Enter paired daily minimum and maximum temperatures.")
    if base < -273.15 or any(lo < -273.15 or lo > hi for lo, hi in zip(lows, highs)):
        raise ValueError("Each minimum must be at or above absolute zero and at most its daily maximum.")
    means = [(Fraction(lo) + Fraction(hi)) / 2 for lo, hi in zip(lows, highs)]
    gains = [max(0, mean - Fraction(base)) for mean in means]
    cumulative = [float(subtotal) for subtotal in accumulate(gains)]
    days = list(range(1, len(lows) + 1))
    total = cumulative[-1]
    return {
        "summary": f"The series accumulates {total:.6g} °C·days above {base:g} °C.",
        "values": {"growing_degree_days_c": total, "days_observed": len(lows)},
        "table": {
            "columns": ["Day", "Daily mean (°C)", "Degree-days (°C·day)", "Cumulative (°C·day)"],
            "rows": [
                [day, float(mean), float(gain), subtotal]
                for day, mean, gain, subtotal in zip(days, means, gains, cumulative)
            ],
        },
        "plot": {
            "title": "Cumulative growing degree-days",
            "xLabel": "Day",
            "yLabel": "Degree-days (°C·day)",
            "x": days,
            "y": cumulative,
        },
    }


# US EPA, AQI Technical Assistance Document EPA-454/B-24-002 (May 2024),
# Table 6. A None band is undefined by EPA for that averaging period.
# Decimal endpoints retain exact regulatory breakpoints and avoid float ties.
_AQI_INDEX_BANDS = ((0, 50), (51, 100), (101, 150), (151, 200), (201, 300), (301, 500))
_AQI_BREAKPOINTS = {
    "o3_8h_ppm": (("0", ".054"), (".055", ".070"), (".071", ".085"),
                   (".086", ".105"), (".106", ".200"), None),
    "o3_1h_ppm": (None, None, (".125", ".164"), (".165", ".204"),
                   (".205", ".404"), (".405", ".604")),
    "pm25_24h_ug_m3": (("0", "9.0"), ("9.1", "35.4"), ("35.5", "55.4"),
                        ("55.5", "125.4"), ("125.5", "225.4"), ("225.5", "325.4")),
    "pm10_24h_ug_m3": (("0", "54"), ("55", "154"), ("155", "254"),
                        ("255", "354"), ("355", "424"), ("425", "604")),
    "co_8h_ppm": (("0", "4.4"), ("4.5", "9.4"), ("9.5", "12.4"),
                   ("12.5", "15.4"), ("15.5", "30.4"), ("30.5", "50.4")),
    "so2_1h_ppb": (("0", "35"), ("36", "75"), ("76", "185"),
                    ("186", "304"), None, None),
    "so2_24h_ppb": (None, None, None, None, ("305", "604"), ("605", "1004")),
    "no2_1h_ppb": (("0", "53"), ("54", "100"), ("101", "360"),
                    ("361", "649"), ("650", "1249"), ("1250", "2049")),
}
_AQI_UNITS = {
    "o3_8h_ppm": ("O₃ 8-h", "ppm", 3),
    "o3_1h_ppm": ("O₃ 1-h", "ppm", 3),
    "pm25_24h_ug_m3": ("PM₂.₅ 24-h", "µg/m³", 1),
    "pm10_24h_ug_m3": ("PM₁₀ 24-h", "µg/m³", 0),
    "co_8h_ppm": ("CO 8-h", "ppm", 1),
    "so2_1h_ppb": ("SO₂ 1-h", "ppb", 0),
    "so2_24h_ppb": ("SO₂ 24-h", "ppb", 0),
    "no2_1h_ppb": ("NO₂ 1-h", "ppb", 0),
}
_AQI_CATEGORIES = ("Good", "Moderate", "Unhealthy for Sensitive Groups", "Unhealthy",
                   "Very Unhealthy", "Hazardous")


def _aqi_subindex(key: str, concentration: Decimal) -> int:
    bands = _AQI_BREAKPOINTS[key]
    for band, (low_index, high_index) in zip(bands, _AQI_INDEX_BANDS):
        if band is None:
            continue
        low, high = map(Decimal, band)
        if low <= concentration <= high:
            interpolated = Decimal(low_index) + (concentration - low) * (high_index - low_index) / (high - low)
            return int(interpolated.to_integral_value(rounding=ROUND_HALF_UP))
    # EPA extends the 301–500 interval's slope above 500, not a new 501+ slope.
    last = bands[-1]
    if last is not None and concentration > Decimal(last[1]):
        low, high = map(Decimal, last)
        interpolated = Decimal(301) + (concentration - low) * 199 / (high - low)
        return int(interpolated.to_integral_value(rounding=ROUND_HALF_UP))
    raise ValueError(f"No EPA AQI breakpoint applies to {key} at {concentration}.")


def _aqi_category(index: int) -> str:
    return next((name for (_, upper), name in zip(_AQI_INDEX_BANDS, _AQI_CATEGORIES)
                 if index <= upper), "Hazardous")


def air_quality_index(data):
    """Daily, multi-pollutant AQI; -1 alone represents an unmeasured input."""
    if not any(value >= 0 for value in data.values()):
        raise ValueError("Supply at least one measured concentration; -1 means unavailable.")
    if any(value < 0 and value != -1 for value in data.values()):
        raise ValueError("Concentrations must be nonnegative, or exactly -1 when unmeasured.")
    rows = []
    for key, (name, unit, decimals) in _AQI_UNITS.items():
        observed = data[key]
        if observed == -1:
            continue
        concentration = Decimal(str(observed)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)
        if key == "o3_1h_ppm" and concentration < Decimal("0.125"):
            continue  # The 1-hour O3 sub-index is undefined below 0.125 ppm.
        if key == "o3_8h_ppm" and concentration > Decimal("0.200"):
            continue  # AQI >= 301 needs 1-hour ozone, not 8-hour extrapolation.
        if key == "so2_24h_ppb" and concentration < 305:
            continue  # 24-hour SO2 is only used for the high AQI bands.
        if key == "so2_1h_ppb" and concentration >= 305:
            # EPA TAD: if 24-hour SO2 does not reach 305 ppb, report 200.
            if data["so2_24h_ppb"] >= 305:
                continue
            index = 200
        else:
            index = _aqi_subindex(key, concentration)
        rows.append((name, observed, float(concentration), unit, index, _aqi_category(index)))
    if not rows:
        raise ValueError("Supplied concentrations are outside their EPA-defined averaging-period breakpoints.")
    leader = max(rows, key=lambda row: row[4])  # first wins a tie deterministically
    index = leader[4]
    return {
        "summary": f"US EPA daily AQI {index} ({_aqi_category(index)}), driven by {leader[0]}; "
                   "concentrations use their stated averaging periods.",
        "values": {"aqi": index, "aqi_category": _aqi_category(index),
                   "main_pollutant": leader[0], "subindices_calculated": len(rows)},
        "table": {"columns": ["Pollutant / averaging period", "Observed", "EPA-truncated",
                              "Concentration unit", "AQI (1)", "Category (1)"],
                  "rows": [list(row) for row in rows]},
    }


# Briggs' rural and urban interpolation of Pasquill–Gifford A–F curves.
# Each entry is (lateral coefficient, lateral denominator factor,
# vertical coefficient, vertical denominator factor, vertical exponent).
_PG_RURAL = {
    "A": (.22, .0001, .20, 0, 0), "B": (.16, .0001, .12, 0, 0),
    "C": (.11, .0001, .08, .0002, -.5),
    "D": (.08, .0001, .06, .0015, -.5),
    "E": (.06, .0001, .03, .0003, -1),
    "F": (.04, .0001, .016, .0003, -1),
}
_PG_URBAN = {
    "A": (.32, .0004, .24, .001, .5),
    "B": (.32, .0004, .24, .001, .5),
    "C": (.22, .0004, .20, 0, 0),
    "D": (.16, .0004, .14, .0003, -.5),
    "E": (.11, .0004, .08, .00015, -.5),
    "F": (.11, .0004, .08, .00015, -.5),
}


def _plume_sigmas(x: float, stability: str, terrain: str) -> tuple[float, float]:
    cy, by, cz, bz, exponent = (_PG_RURAL if terrain == "rural" else _PG_URBAN)[stability]
    return cy * x / math.sqrt(1 + by * x), cz * x * (1 + bz * x) ** exponent


def gaussian_plume(data):
    emission, wind = data["emission_rate_g_s"], data["wind_speed_m_s"]
    x, y, z = data["downwind_m"], data["crosswind_m"], data["receptor_height_m"]
    height = data["effective_stack_height_m"]
    stability = data["stability_class"].strip().upper()
    terrain = data["terrain"].strip().lower()
    if stability not in _PG_RURAL or terrain not in ("rural", "urban"):
        raise ValueError("Use stability class A–F and terrain rural or urban.")
    if not (100 <= x <= 10000) or wind <= 0 or emission < 0 or z < 0 or height < 0:
        raise ValueError("Use 100–10000 m downwind, positive wind and nonnegative release, height and receptor altitude.")

    def concentration(distance: float, crosswind: float, elevation: float) -> float:
        sy, sz = _plume_sigmas(distance, stability, terrain)
        return (1e6 * emission / (2 * math.pi * wind * sy * sz)
                * math.exp(-.5 * (crosswind / sy) ** 2)
                * (math.exp(-.5 * ((elevation - height) / sz) ** 2)
                   + math.exp(-.5 * ((elevation + height) / sz) ** 2)))

    sy, sz = _plume_sigmas(x, stability, terrain)
    receptor = concentration(x, y, z)
    centerline = concentration(x, 0, 0)
    distances = [100 * 100 ** (i / 60) for i in range(61)]
    return {
        "summary": f"Class {stability} {terrain} Gaussian plume: receptor {receptor:.6g} µg/m³ "
                   f"at {x:g} m downwind, {y:g} m crosswind and {z:g} m elevation.",
        "values": {"sigma_y_m": sy, "sigma_z_m": sz,
                   "receptor_concentration_ug_m3": receptor,
                   "ground_centerline_concentration_ug_m3": centerline},
        "plot": {"title": f"Class {stability} ground-level plume centerline ({terrain})",
                 "xLabel": "Downwind distance (m)", "yLabel": "Concentration (µg/m³)",
                 "x": distances, "y": [concentration(d, 0, 0) for d in distances]},
    }


def dissolved_oxygen_sag(data):
    """Streeter–Phelps oxygen balance with one mixed initial load."""
    do0, saturation = data["initial_do_mg_l"], data["saturation_do_mg_l"]
    bod0 = data["ultimate_bod_mg_l"]
    kd, kr = data["deoxygenation_per_day"], data["reaeration_per_day"]
    velocity, reach = data["velocity_m_s"], data["reach_length_km"]
    if not (0 <= do0 <= saturation and saturation > 0 and bod0 >= 0
            and kd > 0 and kr > 0 and velocity > 0 and reach > 0):
        raise ValueError("Require 0 ≤ initial DO ≤ positive saturation DO, nonnegative BOD, "
                         "and positive rates, velocity and reach length.")
    initial_deficit = saturation - do0
    time_end = reach * 1000 / (velocity * 86400)

    def deficit(t: float) -> float:
        difference = kr - kd
        if abs(difference) <= 1e-8 * max(kd, kr):
            load = kd * bod0 * t * math.exp(-kd * t)
        else:
            load = kd * bod0 * (math.exp(-kd * t) - math.exp(-kr * t)) / difference
        return initial_deficit * math.exp(-kr * t) + load

    candidates = [(0.0, do0), (time_end, saturation - deficit(time_end))]
    if bod0:
        difference = kr - kd
        if abs(difference) <= 1e-8 * max(kd, kr):
            critical = (bod0 - initial_deficit) / (kd * bod0)
        else:
            ratio = kr * (kd * bod0 - difference * initial_deficit) / (kd * kd * bod0)
            critical = math.log(ratio) / difference if ratio > 0 else -1
        if 0 < critical < time_end:
            candidates.append((critical, saturation - deficit(critical)))
    time_min, do_min = min(candidates, key=lambda item: item[1])
    times = [time_end * i / 40 for i in range(41)]
    positions = [reach * i / 40 for i in range(41)]
    oxygen = [saturation - deficit(t) for t in times]
    return {
        "summary": f"DO reaches {do_min:.6g} mg/L at {time_min * velocity * 86.4:.6g} km "
                   f"within a {reach:g} km reach (Streeter–Phelps steady-flow approximation)."
                   + (" Predicted oxygen is below zero: the model assumptions fail."
                      if do_min < 0 else ""),
        "values": {"initial_deficit_mg_l": initial_deficit,
                   "outlet_do_mg_l": oxygen[-1], "minimum_do_mg_l": do_min,
                   "minimum_do_distance_km": time_min * velocity * 86.4,
                   "travel_time_day": time_end},
        "table": {"columns": ["Distance (km)", "Travel time (day)",
                              "Remaining ultimate BOD (mg/L)", "Oxygen deficit (mg/L)", "DO (mg/L)"],
                  "rows": [[x, t, bod0 * math.exp(-kd * t), saturation - o, o]
                           for x, t, o in zip(positions[::4], times[::4], oxygen[::4])]},
        "plot": {"title": "Streeter–Phelps dissolved oxygen sag", "xLabel": "Distance (km)",
                 "yLabel": "Dissolved oxygen (mg/L)", "x": positions, "y": oxygen},
    }


def first_order_decay_fit(data):
    """Unweighted log-linear least squares; measurement errors assumed log-scale."""
    times, observed = data["time_day"], data["concentration_mg_l"]
    prediction_time = data["prediction_time_day"]
    n = len(times)
    if n < 3 or n != len(observed) or any(t < 0 for t in times) or any(c <= 0 for c in observed):
        raise ValueError("Enter at least three paired, nonnegative times (day) and positive concentrations (mg/L).")
    if any(b <= a for a, b in zip(times, times[1:])) or prediction_time < 0:
        raise ValueError("Times must be strictly increasing and prediction time nonnegative.")
    logs = [math.log(c) for c in observed]
    mx, my = math.fsum(times) / n, math.fsum(logs) / n
    dx = [t - mx for t in times]
    sxx = math.fsum(v * v for v in dx)
    slope = math.fsum(v * (w - my) for v, w in zip(dx, logs)) / sxx
    if slope >= 0:
        raise ValueError("Measurements do not support positive first-order decay (the log slope must be negative).")
    intercept = my - slope * mx
    rate = -slope
    fitted = [math.exp(intercept + slope * t) for t in times]
    residual_log = [y - (intercept + slope * t) for t, y in zip(times, logs)]
    sse = math.fsum(e * e for e in residual_log)
    syy = math.fsum((y - my) ** 2 for y in logs)
    r_squared = max(0.0, min(1.0, 1 - sse / syy)) if syy > 0 else 1.0
    predicted = math.exp(intercept + slope * prediction_time)
    return {
        "summary": f"Fitted first-order k = {rate:.6g} day⁻¹, half-life "
                   f"{math.log(2) / rate:.6g} day (unweighted ln-concentration fit).",
        "values": {"decay_constant_per_day": rate, "half_life_day": math.log(2) / rate,
                   "initial_concentration_mg_l": math.exp(intercept),
                   "rate_standard_error_per_day": math.sqrt(sse / (n - 2) / sxx),
                   "r_squared_log": r_squared, "predicted_concentration_mg_l": predicted},
        "table": {"columns": ["Time (day)", "Measured (mg/L)", "Fitted (mg/L)",
                              "Residual measured − fitted (mg/L)"],
                  "rows": [[t, c, fit, c - fit] for t, c, fit in zip(times, observed, fitted)]},
        "plot": {"title": "First-order decay: log-linear fit", "xLabel": "Time (day)",
                 "yLabel": "Predicted concentration (mg/L)", "x": times, "y": fitted},
    }


def rational_runoff(data):
    coefficient, intensity, area = data["runoff_coefficient"], data["rainfall_intensity_mm_h"], data["area_ha"]
    duration, time_of_concentration = data["storm_duration_min"], data["time_of_concentration_min"]
    if not 0 <= coefficient <= 1 or intensity < 0 or area <= 0 or time_of_concentration <= 0 or duration <= 0:
        raise ValueError("Use a runoff coefficient in [0, 1], nonnegative intensity, positive area and durations.")
    if duration < time_of_concentration:
        raise ValueError("The design rainfall duration must be at least the catchment time of concentration.")
    peak = coefficient * intensity * area / 360  # mm/h × ha -> m³/s
    return {
        "summary": f"Rational-method peak runoff is {peak:.6g} m³/s for a "
                   f"{duration:g} min design storm of {intensity:g} mm/h.",
        "values": {"peak_runoff_m3_s": peak, "runoff_coefficient": coefficient,
                   "design_rainfall_depth_mm": intensity * duration / 60},
    }


def scs_curve_number(data):
    rainfall, area = data["rainfall_mm"], data["area_km2"]
    curve_number, ratio = data["curve_number"], data["initial_abstraction_ratio"]
    if rainfall < 0 or area <= 0 or not (0 < curve_number <= 100) or not (0 <= ratio <= 1):
        raise ValueError("Rainfall must be nonnegative, area positive, CN in (0, 100], "
                         "and initial abstraction ratio in [0, 1].")
    storage = 25400 / curve_number - 254  # SI version of NRCS S=1000/CN−10 in.
    abstraction = ratio * storage
    # Evaluate as x*(x/(x+S)), without squaring x: for CN=100 and
    # very small rainfall x**2 underflows; for large events it overflows.
    excess = rainfall - abstraction
    runoff_depth = excess * (excess / (excess + storage)) if excess > 0 else 0.0
    volume = runoff_depth * area * 1000  # mm × km² to m³
    return {
        "summary": f"NRCS curve-number direct runoff is {runoff_depth:.6g} mm "
                   f"({volume:.6g} m³) using CN={curve_number:g} and Ia/S={ratio:g}.",
        "values": {"potential_retention_mm": storage, "initial_abstraction_mm": abstraction,
                   "direct_runoff_depth_mm": runoff_depth, "direct_runoff_volume_m3": volume,
                   "event_runoff_coefficient": runoff_depth / rainfall if rainfall else 0.0},
    }


def _saturation_vapour_pressure(temp_c: float) -> float:
    return .6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def fao56_evapotranspiration(data):
    """FAO-56 daily short-grass reference ET0, eq. 6; Rn and G supplied."""
    low, high = data["tmin_c"], data["tmax_c"]
    vapour = data["actual_vapour_pressure_kpa"]
    wind, elevation = data["wind_speed_2m_m_s"], data["elevation_m"]
    net, soil = data["net_radiation_mj_m2_day"], data["soil_heat_flux_mj_m2_day"]
    if not (-60 <= low <= high <= 70 and -500 <= elevation <= 9000 and wind >= 0 and vapour >= 0):
        raise ValueError("Use physical daily temperatures (−60 to 70 °C), elevation −500–9000 m, "
                         "and nonnegative 2-m wind and actual vapour pressure.")
    mean = (low + high) / 2
    saturation = (_saturation_vapour_pressure(low) + _saturation_vapour_pressure(high)) / 2
    if vapour > saturation:
        raise ValueError("Actual vapour pressure must not exceed mean saturation vapour pressure.")
    delta = 4098 * _saturation_vapour_pressure(mean) / (mean + 237.3) ** 2
    pressure = 101.3 * ((293 - .0065 * elevation) / 293) ** 5.26
    gamma = .000665 * pressure
    denominator = delta + gamma * (1 + .34 * wind)
    radiative = .408 * delta * (net - soil) / denominator
    aerodynamic = gamma * (900 / (mean + 273)) * wind * (saturation - vapour) / denominator
    et0 = radiative + aerodynamic
    return {
        "summary": f"FAO-56 short-grass reference evapotranspiration ET₀ = {et0:.6g} mm/day "
                   "from daily net radiation, vapour deficit, temperature and 2-m wind.",
        "values": {"reference_et0_mm_day": et0, "radiation_term_mm_day": radiative,
                   "aerodynamic_term_mm_day": aerodynamic, "mean_temperature_c": mean,
                   "saturation_vapour_pressure_kpa": saturation,
                   "vapour_pressure_deficit_kpa": saturation - vapour,
                   "slope_saturation_curve_kpa_per_c": delta,
                   "psychrometric_constant_kpa_per_c": gamma,
                   "atmospheric_pressure_kpa": pressure},
    }


def _mk_exact_two_sided_p(n: int, s: int) -> float:
    """Mahonian inversion counts give exact permutation p for untied small samples."""
    counts = [1]
    for length in range(2, n + 1):
        updated = [0] * (len(counts) + length - 1)
        for i, count in enumerate(counts):
            for j in range(length):
                updated[i + j] += count
        counts = updated
    inversions = (n * (n - 1) // 2 - abs(s)) // 2
    return min(1.0, 2 * sum(counts[:inversions + 1]) / math.factorial(n))


def _mk_exact_tied_two_sided_p(measurements: list[float], observed_s: int) -> float:
    """Exact conditional permutation p-value among distinct tied orderings."""
    frequencies = Counter(measurements)
    multiplicities = tuple(frequencies[value] for value in sorted(frequencies))
    states = {(0,) * len(multiplicities): {0: 1}}
    for _ in measurements:
        next_states: dict[tuple[int, ...], dict[int, int]] = {}
        for used, distribution in states.items():
            for index, limit in enumerate(multiplicities):
                if used[index] == limit:
                    continue
                updated = used[:index] + (used[index] + 1,) + used[index + 1:]
                step_s = sum(used[:index]) - sum(used[index + 1:])
                target_distribution = next_states.setdefault(updated, {})
                for previous_s, ways in distribution.items():
                    new_s = previous_s + step_s
                    target_distribution[new_s] = target_distribution.get(new_s, 0) + ways
        states = next_states
    distribution = states[multiplicities]
    return (sum(ways for score, ways in distribution.items() if abs(score) >= abs(observed_s))
            / sum(distribution.values()))


def mann_kendall_sen(data):
    """Two-sided seasonal-unadjusted MK with tie variance and Sen median slope."""
    times, measurements = data["time_year"], data["observations"]
    unit, alpha = data["observation_unit"].strip(), data["significance_level"]
    n = len(times)
    if n < 3 or n != len(measurements) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Provide at least three observations at strictly increasing times (year).")
    if not (unit and len(unit) <= 32 and not any(c in unit for c in "\r\n<>")):
        raise ValueError("Enter a measurement unit (for example mg/L, °C or mm), up to 32 characters.")
    if not 0 < alpha < 1:
        raise ValueError("Significance level must be strictly between 0 and 1.")
    slopes = [(measurements[j] - measurements[i]) / (times[j] - times[i])
              for i in range(n) for j in range(i + 1, n)]
    s = sum(1 if v > 0 else -1 if v < 0 else 0
            for i in range(n) for v in (measurements[j] - measurements[i] for j in range(i + 1, n)))
    counts = Counter(measurements)
    ties = sum(count * (count - 1) // 2 for count in counts.values())
    variance = (n * (n - 1) * (2 * n + 5)
                - sum(t * (t - 1) * (2 * t + 5) for t in counts.values())) / 18
    z = (s - 1 if s > 0 else s + 1 if s < 0 else 0) / math.sqrt(variance) if variance else 0.0
    # For small samples use the exact conditional permutation distribution:
    # including ties avoids misclassifying weak trends at a significance edge.
    exact = n <= 10
    p = (_mk_exact_two_sided_p(n, s) if exact and ties == 0 else
         _mk_exact_tied_two_sided_p(measurements, s) if exact else
         math.erfc(abs(z) / math.sqrt(2)) if variance else 1.0)
    method = ("exact permutation (ties)" if ties else "exact permutation") if exact else (
        "normal approximation (continuity/tie corrected)")
    tau_b = s / math.sqrt((n * (n - 1) // 2) * (n * (n - 1) // 2 - ties)) if ties < n * (n - 1) // 2 else 0.0
    slope = median(slopes)
    intercept = median([v - slope * t for t, v in zip(times, measurements)])
    predicted = [intercept + slope * t for t in times]
    trend = ("increasing" if s > 0 else "decreasing") if p < alpha else "not significant"
    return {
        "summary": f"Mann–Kendall {trend} trend (two-sided p={p:.4g}, τb={tau_b:.4g}); "
                   f"Sen slope {slope:.6g} {unit}/year.",
        "values": {"mann_kendall_s": s, "variance_s": variance, "z_score": z,
                   "two_sided_p_value": p, "p_value_method": method, "kendall_tau_b": tau_b,
                   "sen_slope_per_year": slope, "sen_slope_unit": f"{unit}/year",
                   "sen_intercept": intercept, "intercept_unit": unit,
                   "trend": trend, "significance_level": alpha},
        "table": {"columns": ["Time (year)", f"Observed ({unit})", f"Sen fitted ({unit})"],
                  "rows": [[t, m, fit] for t, m, fit in zip(times, measurements, predicted)]},
        "plot": {"title": "Sen median-slope trend estimate", "xLabel": "Time (year)",
                 "yLabel": f"Sen fitted value ({unit})", "x": times, "y": predicted},
    }


# Explicit annotation avoids inferring a fixed-length tuple containing only the
# three original operations; these IDs and their result payloads are preserved.
OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "flow_weighted_concentration",
        "Flow-weighted concentration",
        "Integrate interval-average stream flow and constituent concentration over each measured "
        "duration. Values are treated as constant within each interval.",
        (
            Field("flow_m3_s", "Flow by interval", "vector", unit="m³/s"),
            Field("duration_s", "Interval durations", "vector", unit="s"),
            Field("concentration_mg_l", "Concentrations by interval", "vector", unit="mg/L"),
        ),
        {"flow_m3_s": [1, 2], "duration_s": [600, 600], "concentration_mg_l": [3, 6]},
        flow_weighted_concentration,
        headline="mean_concentration_mg_l",
    ),
    Operation(
        "rainfall_runoff",
        "Observed event runoff coefficient",
        "Compare measured direct runoff to area-average rainfall on a catchment. Use one event "
        "boundary for rainfall and baseflow-separated runoff.",
        (
            Field("rainfall_mm", "Event rainfall depth", "number", unit="mm"),
            Field("area_km2", "Catchment area", "number", unit="km²"),
            Field("runoff_volume_m3", "Observed direct runoff volume", "number", unit="m³"),
        ),
        {"rainfall_mm": 20, "area_km2": 1.5, "runoff_volume_m3": 9000},
        rainfall_runoff,
        headline="runoff_depth_mm",
    ),
    Operation(
        "degree_days",
        "Growing degree-days",
        "Accumulate max(0, (daily minimum + maximum)/2 − base) over consecutive days. "
        "This daily-mean method uses no upper temperature cutoff.",
        (
            Field("daily_min_c", "Daily minimum temperatures", "vector", unit="°C"),
            Field("daily_max_c", "Daily maximum temperatures", "vector", unit="°C"),
            Field("base_c", "Base temperature", "number", unit="°C"),
        ),
        {"daily_min_c": [6, 10, 12], "daily_max_c": [12, 18, 20], "base_c": 10},
        degree_days,
        headline="growing_degree_days_c",
    ),
    Operation(
        "air_quality_index",
        "US EPA daily Air Quality Index",
        "Compute EPA 2024 breakpoint-based subindices from daily/period-averaged concentrations "
        "and report their maximum. Enter -1 only for unavailable pollutant measurements; "
        "never substitute an hourly observation for a 24-h or 8-h mean. O₃ 1-h and "
        "SO₂ 24-h apply only to their EPA-defined higher AQI ranges.",
        (
            Field("o3_8h_ppm", "Daily maximum 8-h O₃ (-1 if missing)", "number", unit="ppm"),
            Field("o3_1h_ppm", "Optional daily maximum 1-h O₃ (-1 if missing)", "number", unit="ppm"),
            Field("pm25_24h_ug_m3", "24-h mean PM₂.₅ (-1 if missing)", "number", unit="µg/m³"),
            Field("pm10_24h_ug_m3", "24-h mean PM₁₀ (-1 if missing)", "number", unit="µg/m³"),
            Field("co_8h_ppm", "Daily maximum 8-h CO (-1 if missing)", "number", unit="ppm"),
            Field("so2_1h_ppb", "Daily maximum 1-h SO₂ (-1 if missing)", "number", unit="ppb"),
            Field("so2_24h_ppb", "24-h mean SO₂ for AQI 201+ (-1 if missing)", "number", unit="ppb"),
            Field("no2_1h_ppb", "Daily maximum 1-h NO₂ (-1 if missing)", "number", unit="ppb"),
        ),
        {"o3_8h_ppm": .07853333, "o3_1h_ppm": -1, "pm25_24h_ug_m3": 35.9,
         "pm10_24h_ug_m3": -1, "co_8h_ppm": 8.4, "so2_1h_ppb": -1,
         "so2_24h_ppb": -1, "no2_1h_ppb": -1},
        air_quality_index,
        reference_datasets=("environment.epa_aqi_breakpoints",),
        headline="aqi",
    ),
    Operation(
        "gaussian_plume",
        "Gaussian plume, Pasquill–Gifford A–F",
        "Steady-state ground-reflected plume with Briggs rural/urban Pasquill–Gifford "
        "dispersion coefficients (roughly 100 m–10 km). Supply effective release height "
        "including plume rise; wind is constant. No deposition, chemistry or downwash.",
        (
            Field("emission_rate_g_s", "Continuous emission rate", "number", unit="g/s"),
            Field("wind_speed_m_s", "Wind at effective plume height", "number", unit="m/s"),
            Field("downwind_m", "Receptor downwind distance", "number", unit="m"),
            Field("crosswind_m", "Signed receptor crosswind offset", "number", unit="m"),
            Field("receptor_height_m", "Receptor height above ground", "number", unit="m"),
            Field("effective_stack_height_m", "Effective plume centerline height", "number", unit="m"),
            Field("stability_class", "Pasquill–Gifford stability class A–F", "text", unit="category (1)"),
            Field("terrain", "Dispersion terrain: rural or urban", "text", unit="category (1)"),
        ),
        {"emission_rate_g_s": 10, "wind_speed_m_s": 3, "downwind_m": 1000,
         "crosswind_m": 0, "receptor_height_m": 0, "effective_stack_height_m": 50,
         "stability_class": "D", "terrain": "rural"},
        gaussian_plume,
        reference_datasets=_plume_reference_datasets,
        headline="receptor_concentration_ug_m3",
    ),
    Operation(
        "dissolved_oxygen_sag",
        "Streeter–Phelps river oxygen sag",
        "Predict DO, ultimate BOD and deficit through a uniformly mixed steady-flow reach "
        "with constant first-order deoxygenation and reaeration coefficients at site temperature. "
        "Reports the analytical minimum within the selected reach; no benthic demand or lateral loads.",
        (
            Field("initial_do_mg_l", "Mixed initial dissolved oxygen", "number", unit="mg/L"),
            Field("saturation_do_mg_l", "Oxygen saturation at river temperature", "number", unit="mg/L"),
            Field("ultimate_bod_mg_l", "Initial ultimate carbonaceous BOD", "number", unit="mg/L"),
            Field("deoxygenation_per_day", "BOD deoxygenation rate kd", "number", unit="day⁻¹"),
            Field("reaeration_per_day", "Atmospheric reaeration rate kr", "number", unit="day⁻¹"),
            Field("velocity_m_s", "Average river velocity", "number", unit="m/s"),
            Field("reach_length_km", "Reach length", "number", unit="km"),
        ),
        {"initial_do_mg_l": 8, "saturation_do_mg_l": 9, "ultimate_bod_mg_l": 15,
         "deoxygenation_per_day": .2, "reaeration_per_day": .4,
         "velocity_m_s": .35, "reach_length_km": 45},
        dissolved_oxygen_sag,
        headline="minimum_do_mg_l",
    ),
    Operation(
        "first_order_decay_fit",
        "Fit first-order pollutant decay and half-life",
        "Fit ln(concentration) = ln(C₀) − kt by unweighted least squares; "
        "log-scale residuals estimate k uncertainty and half-life ln(2)/k. "
        "Measurements must be positive and support decay; ignore censored non-detects.",
        (
            Field("time_day", "Elapsed measurement time", "vector", unit="day"),
            Field("concentration_mg_l", "Measured pollutant concentrations", "vector", unit="mg/L"),
            Field("prediction_time_day", "Time for a fitted concentration estimate", "number", unit="day"),
        ),
        {"time_day": [0, 1, 2, 4, 6], "concentration_mg_l": [10, 7.4, 5.5, 3.0, 1.65],
         "prediction_time_day": 7},
        first_order_decay_fit,
        headline="decay_constant_per_day",
    ),
    Operation(
        "rational_runoff",
        "Rational-method peak discharge",
        "For a small catchment, estimate Qp = C i A / 360 (m³/s) using i in mm/h "
        "and A in hectares. The design-rainfall duration must meet or exceed time of concentration; "
        "runoff coefficient and intensity are externally selected, not fitted here.",
        (
            Field("runoff_coefficient", "Design runoff coefficient C", "number", unit="1"),
            Field("rainfall_intensity_mm_h", "Design rainfall intensity", "number", unit="mm/h"),
            Field("area_ha", "Contributing area", "number", unit="ha"),
            Field("storm_duration_min", "Intensity averaging / storm duration", "number", unit="min"),
            Field("time_of_concentration_min", "Catchment time of concentration", "number", unit="min"),
        ),
        {"runoff_coefficient": .6, "rainfall_intensity_mm_h": 50,
         "area_ha": 20, "storm_duration_min": 30, "time_of_concentration_min": 20},
        rational_runoff,
        headline="peak_runoff_m3_s",
    ),
    Operation(
        "scs_curve_number",
        "NRCS/SCS event curve-number runoff",
        "Runoff depth (P − Ia)²/(P − Ia + S) for P > Ia; S=25400/CN−254 mm, "
        "Ia=λS. Uses the traditional λ=0.20 by default (editable); requires an "
        "appropriately selected composite CN and event rainfall, with no routing.",
        (
            Field("rainfall_mm", "Event rainfall depth", "number", unit="mm"),
            Field("area_km2", "Catchment area", "number", unit="km²"),
            Field("curve_number", "Composite runoff curve number", "number", unit="1"),
            Field("initial_abstraction_ratio", "Initial abstraction ratio λ=Ia/S", "number", unit="1"),
        ),
        {"rainfall_mm": 80, "area_km2": 2, "curve_number": 75,
         "initial_abstraction_ratio": .2},
        scs_curve_number,
        headline="direct_runoff_depth_mm",
    ),
    Operation(
        "fao56_evapotranspiration",
        "FAO-56 Penman–Monteith reference ET₀",
        "Daily grass-reference ET₀ from FAO-56 equation 6. Enter measured/derived "
        "net radiation Rn and soil heat flux G (MJ/m²/day), Tmax/Tmin (°C), "
        "actual vapour pressure (kPa), wind at 2 m (m/s), and elevation (m). "
        "Includes radiative and aerodynamic contributions; not crop ETc.",
        (
            Field("tmax_c", "Daily maximum air temperature", "number", unit="°C"),
            Field("tmin_c", "Daily minimum air temperature", "number", unit="°C"),
            Field("actual_vapour_pressure_kpa", "Actual atmospheric vapour pressure ea", "number", unit="kPa"),
            Field("wind_speed_2m_m_s", "Wind speed at 2 m above grass", "number", unit="m/s"),
            Field("elevation_m", "Station elevation", "number", unit="m"),
            Field("net_radiation_mj_m2_day", "Daily net radiation Rn", "number", unit="MJ/m²/day"),
            Field("soil_heat_flux_mj_m2_day", "Daily soil heat flux G", "number", unit="MJ/m²/day"),
        ),
        {"tmax_c": 34.8, "tmin_c": 25.6, "actual_vapour_pressure_kpa": 2.85,
         "wind_speed_2m_m_s": 2, "elevation_m": 2,
         "net_radiation_mj_m2_day": 14.33, "soil_heat_flux_mj_m2_day": .14},
        fao56_evapotranspiration,
        headline="reference_et0_mm_day",
    ),
    Operation(
        "mann_kendall_sen",
        "Mann–Kendall trend and Sen slope",
        "Two-sided nonseasonal Mann–Kendall test with tie correction, exact p-values "
        "for up to 10 observations, including ties (otherwise continuity-corrected normal), "
        "and median pairwise Sen slope. "
        "Times must increase in years; choose the unit of the observed environmental variable. "
        "Assumes independent observations; serial autocorrelation alters p-values.",
        (
            Field("time_year", "Observation time", "vector", unit="year"),
            Field("observations", "Measured environmental variable", "vector", unit="observation_unit"),
            Field("observation_unit", "Measurement unit (e.g. mg/L, °C, mm)", "text", unit="unit label"),
            Field("significance_level", "Two-sided significance threshold α", "number", unit="1"),
        ),
        {"time_year": [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025],
         "observations": [4.5, 4.3, 3.8, 3.9, 3.4, 3.1, 2.8, 2.7],
         "observation_unit": "mg/L", "significance_level": .05},
        mann_kendall_sen,
        headline="sen_slope_per_year",
    ),
)
