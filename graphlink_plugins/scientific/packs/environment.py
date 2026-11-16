"""Water monitoring, catchment observations, and temperature accounting."""

from fractions import Fraction
from itertools import accumulate

from graphlink_plugins.scientific.core import Field, Operation


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


OPERATIONS = (
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
    ),
)
