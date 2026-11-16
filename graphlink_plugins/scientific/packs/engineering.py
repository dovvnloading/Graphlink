"""Lumped circuit models and uniform thermal expansion."""

import math
import sys
from fractions import Fraction

from graphlink_plugins.scientific.core import Field, Operation


def _resistance(data):
    resistances = data["resistance_ohm"]
    if not resistances or min(resistances) < 0:
        raise ValueError("Supply at least one nonnegative resistance.")
    minimum = min(resistances)
    # Scaling by the smallest resistance avoids overflow in 1/R.
    parallel = minimum / math.fsum(minimum / r for r in resistances) if minimum else 0.0
    if minimum > 0 and parallel == 0:
        raise ValueError("The positive parallel resistance is below the supported numerical range.")
    return {
        "summary": "Equivalent resistance for all elements in series and all elements in parallel.",
        "values": {"series_ohm": math.fsum(resistances), "parallel_ohm": parallel},
    }


def _rc_response(data):
    resistance, capacitance, voltage = data["resistance_ohm"], data["capacitance_f"], data["step_v"]
    times = data["time_s"]
    if resistance <= 0 or capacitance <= 0:
        raise ValueError("Resistance and capacitance must be positive.")
    if not times or min(times) < 0 or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("Supply strictly increasing, nonnegative sample times.")
    tau = resistance * capacitance
    if not math.isfinite(tau) or tau == 0:
        raise ValueError("The RC time constant is outside the supported numerical range.")
    # A subnormal time constant may have only one significant bit. The
    # displayed value rounds to binary64; use the unrounded product to model it.
    exact_tau = Fraction(resistance) * Fraction(capacitance) if tau < sys.float_info.min else None
    voltages = []
    for time in times:
        if exact_tau is not None:
            scaled = Fraction(time) / exact_tau
            ratio = float(scaled) if scaled < 750 else math.inf
        else:
            ratio = time / tau
        if time and ratio < sys.float_info.min:
            # In this regime the quadratic term is below binary64 precision.
            # Compute the linear term before rounding the extremely small ratio.
            volts = float(Fraction(voltage) * Fraction(time) / Fraction(resistance) / Fraction(capacitance))
        else:
            volts = -voltage * math.expm1(-ratio)
        voltages.append(volts)
    return {
        "summary": "Capacitor voltage following an ideal DC step from an initially uncharged state.",
        "values": {"time_constant_s": tau, "final_sample_v": voltages[-1]},
        "plot": {
            "title": "Capacitor step response",
            "xLabel": "Time (s)",
            "yLabel": "Voltage (V)",
            "x": times,
            "y": voltages,
        },
        "table": {"columns": ["Time (s)", "Voltage (V)"], "rows": [[t, v] for t, v in zip(times, voltages)]},
    }


def _expansion(data):
    length, alpha, delta = data["length_m"], data["coefficient_per_k"], data["temperature_change_k"]
    if length <= 0:
        raise ValueError("Initial length must be positive.")
    strain = Fraction(alpha) * Fraction(delta)
    if strain and float(strain) == 0:
        raise ValueError("The nonzero thermal strain is below the supported numerical range.")
    if strain <= -1:
        raise ValueError("The predicted length must remain positive. Check the linear-model inputs.")
    return {
        "summary": "Free, uniform one-dimensional expansion with a constant linear expansion coefficient.",
        "values": {
            "strain": float(strain),
            "length_change_m": float(Fraction(length) * strain),
            "final_length_m": float(Fraction(length) * (1 + strain)),
        },
    }


OPERATIONS = (
    Operation(
        "resistor_network",
        "Combine ideal resistors",
        "Calculate the equivalent resistance of a list of resistors connected entirely in series or entirely in parallel.",
        (Field("resistance_ohm", "Resistances", "vector", unit="Ω", description="Zero represents an ideal short."),),
        {"resistance_ohm": [100, 200, 300]},
        _resistance,
    ),
    Operation(
        "rc_step",
        "Plot an RC step response",
        "Compute the time constant and capacitor voltage at chosen times in an ideal series RC circuit.",
        (
            Field("resistance_ohm", "Resistance", "number", unit="Ω"),
            Field("capacitance_f", "Capacitance", "number", unit="F"),
            Field("step_v", "Input voltage step", "number", unit="V"),
            Field("time_s", "Sample times", "vector", unit="s", description="Increasing nonnegative times."),
        ),
        {"resistance_ohm": 1000, "capacitance_f": 0.001, "step_v": 5, "time_s": [0, 0.25, 0.5, 1, 2, 3, 5]},
        _rc_response,
    ),
    Operation(
        "linear_expansion",
        "Calculate thermal expansion",
        "Compute the unconstrained length change for a material with uniform temperature change in the linear regime.",
        (
            Field("length_m", "Initial length", "number", unit="m"),
            Field("coefficient_per_k", "Linear expansion coefficient", "number", unit="1/K"),
            Field("temperature_change_k", "Signed temperature change", "number", unit="K"),
        ),
        {"length_m": 2, "coefficient_per_k": 0.000023, "temperature_change_k": 40},
        _expansion,
    ),
)
