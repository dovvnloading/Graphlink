"""Idealized oscillator and thermal models in SI units."""

import math

from graphlink_plugins.scientific.core import Field, Operation


def _oscillator(data):
    mass, stiffness, amplitude = data["mass_kg"], data["stiffness_n_m"], data["amplitude_m"]
    if mass <= 0 or stiffness <= 0 or amplitude < 0:
        raise ValueError("Mass and spring stiffness must be positive, and amplitude must be nonnegative.")
    omega = math.sqrt(stiffness) / math.sqrt(mass)
    period = math.tau / omega
    return {
        "summary": "Ideal undamped oscillator released from rest at its maximum displacement.",
        "values": {"period_s": period, "frequency_hz": omega / math.tau, "angular_frequency_rad_s": omega},
        "plot": {
            "title": "One period of harmonic motion",
            "xLabel": "Time (s)",
            "yLabel": "Displacement (m)",
            "x": [period * (i / 64) for i in range(65)],
            "y": [amplitude * math.cos(math.tau * i / 64) for i in range(65)],
        },
    }


def _conduction(data):
    thickness, conductivities = data["thickness_m"], data["conductivity_w_mk"]
    area, t1, t2 = data["area_m2"], data["left_k"], data["right_k"]
    if not thickness or len(thickness) != len(conductivities):
        raise ValueError("Supply a thickness and conductivity for each layer.")
    if min(thickness) <= 0 or min(conductivities) <= 0 or area <= 0:
        raise ValueError("Thicknesses, conductivities and cross-sectional area must be positive.")
    if min(t1, t2) < 0:
        raise ValueError("Boundary temperatures must be at or above zero kelvin.")
    resistances = [length / k / area for length, k in zip(thickness, conductivities)]
    total = math.fsum(resistances)
    if total == 0:
        raise ValueError("Thermal resistance is below the supported numerical range.")
    heat_rate = (t1 - t2) / total
    return {
        "summary": "Steady one-dimensional conduction through layers in perfect contact. Positive flow is left to right.",
        "values": {"resistance_k_w": total, "heat_rate_w": heat_rate, "heat_flux_w_m2": heat_rate / area},
        "table": {
            "columns": ["Layer", "Thermal resistance (K/W)"],
            "rows": [[i + 1, r] for i, r in enumerate(resistances)],
        },
    }


def _calorimetry(data):
    mass, capacity = data["mass_kg"], data["specific_heat_j_kgk"]
    initial, final = data["initial_k"], data["final_k"]
    if mass <= 0 or capacity <= 0:
        raise ValueError("Mass and specific heat must be positive.")
    if min(initial, final) < 0:
        raise ValueError("Temperatures must be at or above zero kelvin.")
    delta = final - initial
    return {
        "summary": "Sensible heat at constant specific heat, with no phase change. Positive heat enters the sample.",
        "values": {
            "temperature_change_k": delta,
            "heat_capacity_j_k": mass * capacity,
            "heat_j": mass * capacity * delta,
        },
    }


OPERATIONS = (
    Operation(
        "spring_oscillator",
        "Model a spring oscillator",
        "Calculate the period and frequency and plot one cycle of an ideal linear mass–spring oscillator.",
        (
            Field("mass_kg", "Mass", "number", unit="kg"),
            Field("stiffness_n_m", "Spring stiffness", "number", unit="N/m"),
            Field("amplitude_m", "Initial displacement amplitude", "number", unit="m"),
        ),
        {"mass_kg": 1.0, "stiffness_n_m": 4.0, "amplitude_m": 0.1},
        _oscillator,
    ),
    Operation(
        "thermal_layers",
        "Calculate heat flow through layers",
        "Sum series thermal resistances of planar layers with common area and fixed face temperatures.",
        (
            Field("thickness_m", "Layer thicknesses", "vector", unit="m"),
            Field("conductivity_w_mk", "Thermal conductivities", "vector", unit="W/(m·K)"),
            Field("area_m2", "Cross-sectional area", "number", unit="m²"),
            Field("left_k", "Left face temperature", "number", unit="K"),
            Field("right_k", "Right face temperature", "number", unit="K"),
        ),
        {"thickness_m": [0.1, 0.02], "conductivity_w_mk": [0.2, 0.04], "area_m2": 2, "left_k": 295, "right_k": 275},
        _conduction,
    ),
    Operation(
        "sensible_heat",
        "Calculate sensible heat",
        "Compute the signed heat transfer for a known mass and constant specific heat over a temperature change.",
        (
            Field("mass_kg", "Mass", "number", unit="kg"),
            Field("specific_heat_j_kgk", "Specific heat", "number", unit="J/(kg·K)"),
            Field("initial_k", "Initial temperature", "number", unit="K"),
            Field("final_k", "Final temperature", "number", unit="K"),
        ),
        {"mass_kg": 0.5, "specific_heat_j_kgk": 4180, "initial_k": 293.15, "final_k": 303.15},
        _calorimetry,
    ),
)
