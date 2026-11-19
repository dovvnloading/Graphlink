"""Physiology calculations for the consent-gated Biology Workbench.

Voltages are intracellular minus extracellular. This educational, deterministic
pack uses SI constants and the original 1952 Hodgkin--Huxley squid-axon model.
No numerical dependencies beyond the Python standard library are required.
"""

from __future__ import annotations

import math

from graphlink_plugins.scientific.core import Citation, Field, Operation


R_GAS = 8.31446261815324  # J / (mol K), CODATA
FARADAY = 96485.33212  # C / mol, CODATA


def _positive(data: dict, key: str) -> float:
    value = data[key]
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be finite and greater than zero.")
    return value


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} is outside the supported numerical range.")
    return value


def _log_positive_ratio(numerator: float, denominator: float) -> float:
    """Log ratio of positive values without losing small ionic gradients."""
    if numerator >= denominator:
        difference = numerator - denominator
        if difference <= denominator:
            return math.log1p(difference / denominator)
    else:
        difference = denominator - numerator
        if difference <= numerator:
            return -math.log1p(difference / numerator)
    # Logs separately avoid overflow/underflow of a very large/small quotient.
    return math.log(numerator) - math.log(denominator)


def nernst_potential(data: dict) -> dict:
    """Equilibrium potential E_inside - E_outside for one ionic species."""
    inside = _positive(data, "inside_mm")
    outside = _positive(data, "outside_mm")
    temperature = _positive(data, "temperature_k")
    z = data["valence"]
    if type(z) is not int or z == 0 or not -3 <= z <= 3:
        raise ValueError("Ionic valence must be a nonzero integer between -3 and 3.")
    log_ratio = _log_positive_ratio(outside, inside)
    potential = _finite(1000 * R_GAS * temperature / (z * FARADAY) * log_ratio,
                        "Nernst potential")
    return {
        "summary": (
            f"Nernst equilibrium potential (inside minus outside) = {potential:.6g} mV "
            f"for a {z:+d} ion at {temperature:.6g} K. Activities are approximated "
            "by concentrations; this is not a whole-membrane resting potential."
        ),
        "values": {
            "equilibrium_potential_mv": potential,
            "log_outside_inside_ratio": log_ratio,
            "ionic_valence": z,
            "temperature_k": temperature,
        },
    }


def ghk_potential(data: dict) -> dict:
    """Constant-field GHK voltage with the chloride concentration reversed."""
    temperature = _positive(data, "temperature_k")
    species = (
        ("K+", "p_k", "k_inside_mm", "k_outside_mm"),
        ("Na+", "p_na", "na_inside_mm", "na_outside_mm"),
        ("Cl-", "p_cl", "cl_inside_mm", "cl_outside_mm"),
    )
    rows = []
    numerator_terms, denominator_terms = [], []
    for name, permeability_key, inside_key, outside_key in species:
        permeability = data[permeability_key]
        inside, outside = data[inside_key], data[outside_key]
        if permeability < 0 or inside < 0 or outside < 0:
            raise ValueError("GHK permeabilities and concentrations must be nonnegative.")
        numerator = permeability * (inside if name == "Cl-" else outside)
        denominator = permeability * (outside if name == "Cl-" else inside)
        numerator_terms.append(_finite(numerator, "GHK numerator term"))
        denominator_terms.append(_finite(denominator, "GHK denominator term"))
        rows.append([name, permeability, inside, outside, numerator, denominator])
    if not any(data[key] > 0 for _, key, _, _ in species):
        raise ValueError("At least one GHK permeability must be positive.")
    numerator = _finite(math.fsum(numerator_terms), "GHK numerator")
    denominator = _finite(math.fsum(denominator_terms), "GHK denominator")
    if numerator <= 0 or denominator <= 0:
        raise ValueError("GHK weighted intracellular and extracellular sums must both be positive.")
    log_ratio = math.log(numerator) - math.log(denominator)
    potential = _finite(1000 * R_GAS * temperature / FARADAY * log_ratio,
                        "GHK potential")
    return {
        "summary": (
            f"GHK constant-field voltage (inside minus outside) = {potential:.6g} mV "
            "for monovalent K+, Na+ and Cl-. The chloride gradient is reversed "
            "in the permeability-weighted ratio."
        ),
        "values": {
            "membrane_potential_mv": potential,
            "ghk_numerator_mm": numerator,
            "ghk_denominator_mm": denominator,
            "log_ghk_ratio": log_ratio,
        },
        "table": {
            "columns": ["Ion", "Relative permeability", "Inside (mM)", "Outside (mM)",
                        "Numerator contribution (mM)", "Denominator contribution (mM)"],
            "rows": rows,
        },
    }


def membrane_properties(data: dict) -> dict:
    """Passive isopotential RC membrane responding to a maintained current step."""
    area = _positive(data, "area_cm2")
    specific_c = _positive(data, "specific_capacitance_uf_cm2")
    specific_r = _positive(data, "specific_resistance_ohm_cm2")
    times = data["time_ms"]
    if times[0] != 0 or any(t < 0 for t in times) or any(
        current <= previous for previous, current in zip(times, times[1:])
    ):
        raise ValueError("Times must start at zero and be strictly increasing.")
    if len(times) < 2:
        raise ValueError("At least two sample times are required.")
    # MΩ times nF = ms; nA times MΩ = mV.
    capacitance_nf = _finite(specific_c * area * 1000, "Membrane capacitance")
    resistance_mohm = _finite(specific_r / area / 1e6, "Input resistance")
    tau_ms = _finite(resistance_mohm * capacitance_nf, "Membrane time constant")
    if min(capacitance_nf, resistance_mohm, tau_ms) <= 0:
        raise ValueError("Membrane resistance, capacitance and tau must be representable.")
    voltage_change_mv = _finite(data["current_na"] * resistance_mohm,
                                "Steady-state voltage change")
    rows = []
    for time in times:
        delta = _finite(voltage_change_mv * -math.expm1(-time / tau_ms),
                        "Passive voltage change")
        voltage = _finite(data["resting_mv"] + delta, "Membrane voltage")
        rows.append([time, voltage, delta])
    return {
        "summary": (
            f"Passive, spatially uniform membrane: R={resistance_mohm:.6g} MΩ, "
            f"C={capacitance_nf:.6g} nF, tau={tau_ms:.6g} ms; a maintained "
            f"{data['current_na']:.6g} nA current has a steady-state change "
            f"of {voltage_change_mv:.6g} mV."
        ),
        "values": {
            "capacitance_nf": capacitance_nf,
            "input_resistance_mohm": resistance_mohm,
            "time_constant_ms": tau_ms,
            "steady_state_delta_mv": voltage_change_mv,
            "final_voltage_mv": rows[-1][1],
        },
        "table": {
            "columns": ["Time (ms)", "Voltage (mV)", "Change from rest (mV)"],
            "rows": rows,
        },
        "plot": {
            "title": "Passive membrane response to maintained current",
            "xLabel": "Time (ms)", "yLabel": "Membrane potential (mV)",
            "x": times, "y": [row[1] for row in rows],
        },
    }


def _vtrap(x: float, scale: float) -> float:
    """x/(1-exp(-x/scale)), including the removable singularity at x=0."""
    ratio = x / scale
    if abs(ratio) < 1e-6:
        return scale * (1 + ratio / 2 + ratio * ratio / 12)
    return -x / math.expm1(-ratio)


def _hh_rates(voltage: float) -> tuple[float, float, float, float, float, float]:
    """Gate rates in inverse milliseconds, with modern -65 mV voltage offset."""
    return (
        0.1 * _vtrap(voltage + 40, 10),
        4 * math.exp(-(voltage + 65) / 18),
        0.07 * math.exp(-(voltage + 65) / 20),
        1 / (1 + math.exp(-(voltage + 35) / 10)),
        0.01 * _vtrap(voltage + 55, 10),
        0.125 * math.exp(-(voltage + 65) / 80),
    )


def _hh_derivative(state: tuple[float, float, float, float], current: float
                   ) -> tuple[float, float, float, float]:
    voltage, m, h, n = state
    am, bm, ah, bh, an, bn = _hh_rates(voltage)
    sodium = 120 * m**3 * h * (voltage - 50)
    potassium = 36 * n**4 * (voltage + 77)
    leak = 0.3 * (voltage + 54.387)
    return (current - sodium - potassium - leak,
            am * (1 - m) - bm * m,
            ah * (1 - h) - bh * h,
            an * (1 - n) - bn * n)


def _hh_shift(state: tuple[float, float, float, float],
              slope: tuple[float, float, float, float],
              scale: float) -> tuple[float, float, float, float]:
    """Preserve the fixed four-variable state shape for the RK4 stages."""
    return (
        state[0] + scale * slope[0],
        state[1] + scale * slope[1],
        state[2] + scale * slope[2],
        state[3] + scale * slope[3],
    )


def _hh_step(state: tuple[float, float, float, float], current: float,
             dt: float) -> tuple[float, float, float, float]:
    """Classical fourth-order Runge--Kutta with piecewise-constant step current."""
    k1 = _hh_derivative(state, current)
    k2 = _hh_derivative(_hh_shift(state, k1, dt / 2), current)
    k3 = _hh_derivative(_hh_shift(state, k2, dt / 2), current)
    k4 = _hh_derivative(_hh_shift(state, k3, dt), current)
    next_state = [
        x + dt * (a + 2 * b + 2 * c + d) / 6
        for x, a, b, c, d in zip(state, k1, k2, k3, k4)
    ]
    return next_state[0], next_state[1], next_state[2], next_state[3]


def hodgkin_huxley(data: dict) -> dict:
    """Space-clamped original HH model at its reference 6.3 C temperature."""
    duration = _positive(data, "duration_ms")
    requested_dt = _positive(data, "time_step_ms")
    stimulus_start = data["stimulus_start_ms"]
    stimulus_stop = data["stimulus_stop_ms"]
    stimulus = data["current_ua_cm2"]
    initial = data["initial_voltage_mv"]
    if not 1 <= duration <= 200 or not 0.005 <= requested_dt <= 0.05:
        raise ValueError("HH requires 1–200 ms duration and 0.005–0.05 ms step size.")
    steps = math.ceil(duration / requested_dt)
    if steps > 20000:
        raise ValueError("HH simulation may use at most 20,000 integration steps.")
    dt = duration / steps
    if not 0 <= stimulus_start < stimulus_stop <= duration or stimulus_stop - stimulus_start < dt:
        raise ValueError("Stimulus must be a positive-duration interval within the simulation, at least one step long.")
    if not -100 <= stimulus <= 100 or not -100 <= initial <= 80:
        raise ValueError("HH current must be in [-100,100] µA/cm² and initial voltage in [-100,80] mV.")

    am, bm, ah, bh, an, bn = _hh_rates(initial)
    state = (initial, am / (am + bm), ah / (ah + bh), an / (an + bn))
    maximum, minimum, peak_time, crossings = initial, initial, 0.0, 0
    # Eight columns times at most 500 rows remain below the inline 4096-cell cap.
    stride = max(1, math.ceil(steps / 498))
    rows = [[0.0, *state, 0.0,
             120 * state[1]**3 * state[2] * (initial - 50),
             36 * state[3]**4 * (initial + 77)]]
    for index in range(steps):
        midpoint = (index + 0.5) * dt
        injected = stimulus if stimulus_start <= midpoint < stimulus_stop else 0.0
        previous_v = state[0]
        state = _hh_step(state, injected, dt)
        voltage, m, h, n = state
        if (not all(math.isfinite(x) for x in state) or not -200 <= voltage <= 200
                or not all(-1e-8 <= gate <= 1 + 1e-8 for gate in (m, h, n))):
            raise ValueError("HH integration left its supported voltage/gating range; reduce current or step.")
        if previous_v < 0 <= voltage:
            crossings += 1
        if voltage > maximum:
            maximum, peak_time = voltage, (index + 1) * dt
        minimum = min(minimum, voltage)
        if (index + 1) % stride == 0 or index + 1 == steps:
            t = (index + 1) * dt
            ina = 120 * m**3 * h * (voltage - 50)
            ik = 36 * n**4 * (voltage + 77)
            rows.append([t, voltage, m, h, n, injected, ina, ik])
    return {
        "summary": (
            f"Space-clamped Hodgkin–Huxley squid axon at 6.3 °C: "
            f"{crossings} upward zero-mV crossings over {duration:g} ms; "
            f"peak {maximum:.6g} mV. Fourth-order RK4 at {dt:.6g} ms; "
            "currents are densities (µA/cm²), not whole-cell currents."
        ),
        "values": {
            "integration_steps": steps,
            "integration_step_ms": dt,
            "zero_mv_upcrossings": crossings,
            "peak_voltage_mv": maximum,
            "peak_time_ms": peak_time,
            "minimum_voltage_mv": minimum,
            "final_voltage_mv": state[0],
            "reference_temperature_c": 6.3,
        },
        "table": {
            "columns": ["Time (ms)", "Voltage (mV)", "m", "h", "n",
                        "Applied current (µA/cm²)", "Na current (µA/cm²)", "K current (µA/cm²)"],
            "rows": rows,
        },
        "plot": {
            "title": "Hodgkin–Huxley action potential",
            "xLabel": "Time (ms)", "yLabel": "Membrane potential (mV)",
            "x": [row[0] for row in rows], "y": [row[1] for row in rows],
        },
    }


def spike_detection(data: dict) -> dict:
    """Upward crossing of voltage threshold, followed by segment-local peak."""
    times, voltages = data["time_ms"], data["voltage_mv"]
    if len(times) != len(voltages) or len(times) < 3:
        raise ValueError("Spike detection requires at least three aligned time/voltage samples.")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Spike times must be strictly increasing.")
    refractory = data["refractory_ms"]
    if refractory < 0:
        raise ValueError("Refractory period must be nonnegative.")
    threshold = data["threshold_mv"]
    rows: list[list[float]] = []
    last_onset = -math.inf
    for i in range(1, len(times)):
        if not (voltages[i - 1] < threshold <= voltages[i]):
            continue
        fraction = (threshold - voltages[i - 1]) / (voltages[i] - voltages[i - 1])
        onset = times[i - 1] + fraction * (times[i] - times[i - 1])
        if onset - last_onset < refractory:
            continue
        j = i
        while j + 1 < len(times) and voltages[j + 1] >= threshold:
            j += 1
        peak = max(range(i, j + 1), key=voltages.__getitem__)
        rows.append([len(rows) + 1, _finite(onset, "Spike onset"),
                     times[peak], voltages[peak]])
        last_onset = onset
    count = len(rows)
    duration = times[-1] - times[0]
    rate = _finite(1000 * count / duration, "Spike rate")
    values = {
        "spike_count": count,
        "recording_duration_ms": duration,
        "window_firing_rate_hz": rate,
        "mean_interspike_interval_ms": (
            sum(rows[i][1] - rows[i - 1][1] for i in range(1, count)) / (count - 1)
            if count > 1 else "undefined (fewer than two spikes)"
        ),
    }
    if count:
        values.update({"first_onset_ms": rows[0][1],
                       "maximum_detected_peak_mv": max(row[3] for row in rows)})
    return {
        "summary": (
            f"{count} upward {threshold:g} mV threshold crossings passing a "
            f"{refractory:g} ms onset-to-onset refractory filter over "
            f"{duration:.6g} ms. Peaks are sampled maxima in each suprathreshold interval."
        ),
        "values": values,
        "table": {
            "columns": ["Spike #", "Threshold onset (ms)", "Peak time (ms)", "Peak voltage (mV)"],
            "rows": rows,
        },
        "plot": {
            "title": "Spike detection input voltage",
            "xLabel": "Time (ms)", "yLabel": "Membrane potential (mV)",
            "x": times, "y": voltages,
        },
    }


def fick_diffusion(data: dict) -> dict:
    """Steady one-dimensional transport between fixed-concentration boundaries."""
    diffusion = _positive(data, "diffusivity_m2_s")
    thickness = _positive(data, "thickness_m")
    area = _positive(data, "area_m2")
    duration = _positive(data, "duration_s")
    left = data["left_mol_m3"]
    right = data["right_mol_m3"]
    if left < 0 or right < 0:
        raise ValueError("Fick concentrations must be nonnegative.")
    gradient = _finite((right - left) / thickness, "Concentration gradient")
    flux = _finite(-diffusion * gradient, "Diffusive flux")
    rate = _finite(flux * area, "Molar transfer rate")
    amount = _finite(rate * duration, "Integrated molar transfer")
    rows = [
        [thickness * i / 8, left + (right - left) * i / 8]
        for i in range(9)
    ]
    return {
        "summary": (
            f"Steady Fick flux = {flux:.6g} mol/(m²·s), positive from left to right; "
            f"net transfer over {duration:g} s = {amount:.6g} mol. Boundary "
            "concentrations and diffusivity are held constant."
        ),
        "values": {
            "gradient_mol_m4": gradient,
            "flux_mol_m2_s": flux,
            "transfer_rate_mol_s": rate,
            "net_transfer_mol": amount,
            "direction": "left to right" if flux > 0 else "right to left" if flux < 0 else "no net flux",
        },
        "table": {"columns": ["Position (m)", "Concentration (mol/m³)"], "rows": rows},
        "plot": {
            "title": "Steady linear concentration profile",
            "xLabel": "Position (m)", "yLabel": "Concentration (mol/m³)",
            "x": [row[0] for row in rows], "y": [row[1] for row in rows],
        },
    }


_CITES = (
    Citation("Hodgkin and Huxley (1952), Journal of Physiology",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC1392413/"),
    Citation("Goldman (1943), Journal of General Physiology",
             "https://rupress.org/jgp/article/27/1/37/12329/Potential-Impedance-and-Rectification-in"),
    Citation("NIST CODATA physical constants", "https://physics.nist.gov/cuu/Constants/"),
    Citation("Fick's laws of diffusion, overview", "https://en.wikipedia.org/wiki/Fick%27s_laws_of_diffusion"),
)


OPERATIONS = (
    Operation(
        "nernst_potential", "Nernst equilibrium potential",
        "Compute an ion's inside-minus-outside equilibrium voltage from concentrations and valence.",
        (
            Field("inside_mm", "Intracellular concentration", "number", unit="mM"),
            Field("outside_mm", "Extracellular concentration", "number", unit="mM"),
            Field("valence", "Ionic valence", "integer", unit="1"),
            Field("temperature_k", "Absolute temperature", "number", unit="K"),
        ),
        {"inside_mm": 140, "outside_mm": 5, "valence": 1, "temperature_k": 310.15},
        nernst_potential,
        methods="Nernst E = RT/(zF) ln(Cout/Cin); ideal activities; voltage is inside minus outside.",
        citations=_CITES[2:3],
    ),
    Operation(
        "ghk_potential", "Goldman–Hodgkin–Katz potential",
        "Constant-field membrane voltage for monovalent potassium, sodium and chloride.",
        (
            Field("k_inside_mm", "K+ inside", "number", unit="mM"),
            Field("k_outside_mm", "K+ outside", "number", unit="mM"),
            Field("na_inside_mm", "Na+ inside", "number", unit="mM"),
            Field("na_outside_mm", "Na+ outside", "number", unit="mM"),
            Field("cl_inside_mm", "Cl- inside", "number", unit="mM"),
            Field("cl_outside_mm", "Cl- outside", "number", unit="mM"),
            Field("p_k", "Relative permeability to K+", "number", unit="1"),
            Field("p_na", "Relative permeability to Na+", "number", unit="1"),
            Field("p_cl", "Relative permeability to Cl-", "number", unit="1"),
            Field("temperature_k", "Absolute temperature", "number", unit="K"),
        ),
        {"k_inside_mm": 140, "k_outside_mm": 5, "na_inside_mm": 12, "na_outside_mm": 145,
         "cl_inside_mm": 10, "cl_outside_mm": 110,
         "p_k": 1, "p_na": 0.04, "p_cl": 0.45, "temperature_k": 310.15},
        ghk_potential,
        methods="GHK ln((PK Kout + PNa Naout + PCl Clin)/(PK Kin + PNa Nain + PCl Clout)).",
        citations=_CITES[1:3],
    ),
    Operation(
        "membrane_properties", "Passive membrane properties",
        "Compute membrane capacitance, input resistance, RC time constant and current-step voltage.",
        (
            Field("area_cm2", "Area", "number", unit="cm²"),
            Field("specific_capacitance_uf_cm2", "Specific capacitance", "number", unit="µF/cm²"),
            Field("specific_resistance_ohm_cm2", "Specific membrane resistance", "number", unit="Ω*cm²"),
            Field("resting_mv", "Resting voltage", "number", unit="mV"),
            Field("current_na", "Maintained current", "number", unit="nA"),
            Field("time_ms", "Times", "vector", unit="ms"),
        ),
        {"area_cm2": 0.01, "specific_capacitance_uf_cm2": 1,
         "specific_resistance_ohm_cm2": 10000, "resting_mv": -65,
         "current_na": 0.1, "time_ms": [0, 5, 10, 20, 40]},
        membrane_properties,
        methods="C = cm A, R = rm/A, tau = RC, V(t) = Vrest + IR(1-exp(-t/tau)).",
        citations=_CITES[:1],
    ),
    Operation(
        "hodgkin_huxley", "Hodgkin–Huxley action potential",
        "Simulate a space-clamped 1952 HH axon with fourth-order Runge–Kutta and a square current pulse.",
        (
            Field("duration_ms", "Simulation duration", "number", unit="ms"),
            Field("time_step_ms", "Maximum integration step", "number", unit="ms"),
            Field("stimulus_start_ms", "Pulse start", "number", unit="ms"),
            Field("stimulus_stop_ms", "Pulse stop", "number", unit="ms"),
            Field("current_ua_cm2", "Injected current density", "number", unit="µA/cm²"),
            Field("initial_voltage_mv", "Initial membrane voltage", "number", unit="mV"),
        ),
        {"duration_ms": 50, "time_step_ms": 0.01, "stimulus_start_ms": 5,
         "stimulus_stop_ms": 35, "current_ua_cm2": 10, "initial_voltage_mv": -65},
        hodgkin_huxley,
        methods="Original HH alpha/beta gate rates, C=1 uF/cm2, gNa=120, gK=36, gL=0.3 mS/cm2; "
                "ENa=50, EK=-77, EL=-54.387 mV; RK4 at 6.3 C.",
        citations=_CITES[:1],
    ),
    Operation(
        "spike_detection", "Threshold spike detection",
        "Detect rising threshold crossings and subsequent sampled peaks with a refractory filter.",
        (
            Field("time_ms", "Sample times", "vector", unit="ms"),
            Field("voltage_mv", "Voltages", "vector", unit="mV"),
            Field("threshold_mv", "Threshold", "number", unit="mV"),
            Field("refractory_ms", "Minimum onset separation", "number", unit="ms"),
        ),
        {"time_ms": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
         "voltage_mv": [-65, -30, 30, -50, -65, -25, 35, -45, -65, -60, -65],
         "threshold_mv": 0, "refractory_ms": 2},
        spike_detection,
        methods="Linear threshold crossing interpolation; maximum sample before return below threshold; "
                "onset-to-onset refractory exclusion.",
        citations=_CITES[:1],
    ),
    Operation(
        "fick_diffusion", "Fick steady diffusion",
        "Compute one-dimensional flux, transfer rate and amount through a planar layer.",
        (
            Field("diffusivity_m2_s", "Diffusivity", "number", unit="m²/s"),
            Field("thickness_m", "Layer thickness", "number", unit="m"),
            Field("area_m2", "Cross-sectional area", "number", unit="m²"),
            Field("left_mol_m3", "Left concentration", "number", unit="mol/m³"),
            Field("right_mol_m3", "Right concentration", "number", unit="mol/m³"),
            Field("duration_s", "Steady interval", "number", unit="s"),
        ),
        {"diffusivity_m2_s": 1e-9, "thickness_m": 0.001, "area_m2": 0.0002,
         "left_mol_m3": 3, "right_mol_m3": 1, "duration_s": 100},
        fick_diffusion,
        methods="Steady Fick J=-D(Cright-Cleft)/L; transferred mol=J A duration.",
        citations=_CITES[3:],
    ),
)
