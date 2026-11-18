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


OPERATIONS: tuple[Operation, ...] = (
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

# Models below are append-only: the original three operation definitions and
# their result envelopes must remain byte-for-byte compatible with saved runs.


def _strictly_increasing_nonnegative(samples, name):
    if not samples or samples[0] < 0 or any(a >= b for a, b in zip(samples, samples[1:])):
        raise ValueError(f"{name} must be nonnegative and strictly increasing.")


def _require_positive(data, *keys):
    for key in keys:
        if data[key] <= 0:
            raise ValueError(f"{key} must be positive in the stated units.")


def beam_response(data):
    """Euler-Bernoulli beam; positive loads/deflections point downwards."""
    support = data["support"]
    if support not in ("simply_supported", "cantilever"):
        raise ValueError("Support must be simply_supported or cantilever (fixed at x = 0).")
    _require_positive(data, "length_m", "young_modulus_pa", "second_moment_m4")
    length, load, at, distributed = (data[key] for key in
        ("length_m", "point_load_n", "point_location_m", "uniform_load_n_m"))
    if load < 0 or distributed < 0 or not 0 <= at <= length:
        raise ValueError("Use downward nonnegative loads and a point location within the span [0, L].")
    xs = data["position_m"]
    _strictly_increasing_nonnegative(xs, "Sample positions (m)")
    if len(xs) > 819:
        raise ValueError("At most 819 beam positions fit the five-column result table.")
    if xs[-1] > length:
        raise ValueError("Sample positions (m) must lie within the beam length.")
    ei = data["young_modulus_pa"] * data["second_moment_m4"]
    if not math.isfinite(ei) or ei <= 0:
        raise ValueError("Flexural rigidity E I is outside the supported numerical range.")

    if support == "simply_supported":
        left = load * (length - at) / length + distributed * length / 2
        right = load * at / length + distributed * length / 2
        constant = (left * length**2 / 6 - distributed * length**3 / 24
                    - load * (length - at)**3 / (6 * length))

        def response(x):
            h = max(x - at, 0.0)
            shear = left - distributed * x - (load if x >= at else 0)
            moment = left * x - distributed * x*x/2 - load * h
            slope = (constant - left*x*x/2 + distributed*x**3/6 + load*h*h/2) / ei
            deflection = ((constant*x - left*x**3/6 + distributed*x**4/24
                           + load*h**3/6) / ei)
            if x == 0 or x == length:
                deflection = 0.0
            if x == length:
                moment = 0.0
            return deflection, slope, shear, moment

        if load == 0 and distributed == 0 or (distributed == 0 and at in (0, length)):
            extreme = 0.0
        else:
            low, high = 0.0, length
            # With downward loads M >= 0, hence d(slope)/dx <= 0. There
            # is a unique maximum where the slope changes sign.
            for _ in range(85):
                middle = (low + high) / 2
                if response(middle)[1] > 0:
                    low = middle
                else:
                    high = middle
            extreme = (low + high) / 2
        candidates = [0.0, length, at]
        if distributed:
            candidates += [max(0.0, min(length, left / distributed)),
                           max(0.0, min(length, (left - load) / distributed))]
        max_moment_at = max(candidates, key=lambda x: abs(response(x)[3]))
        max_slope = max(abs(response(x)[1]) for x in (0.0, length))
        fixed_end_moment = 0.0
    else:
        left, right = load + distributed*length, 0.0
        fixed_end_moment = -load*at - distributed*length*length/2

        def response(x):
            h = max(at-x, 0.0)
            shear = distributed*(length-x) + (load if x < at else 0.0)
            moment = -load*h - distributed*(length-x)**2/2
            if x <= at:
                point_y = load*x*x*(3*at-x)/6
                point_slope = load*x*(2*at-x)/2
            else:
                point_y = load*at*at*(3*x-at)/6
                point_slope = load*at*at/2
            y = (point_y + distributed*x*x*(6*length**2-4*length*x+x*x)/24)/ei
            slope = (point_slope + distributed*x*(3*length**2-3*length*x+x*x)/6)/ei
            return y, slope, shear, moment

        extreme = length
        max_moment_at = 0.0
        max_slope = abs(response(length)[1])

    rows = [[x, *response(x)] for x in xs]
    return {
        "summary": ("Euler–Bernoulli small-deflection beam with downward point and whole-span uniform "
                    "loads; shear is dM/dx (right side of a point load). Positive deflection is down."),
        "values": {
            "reaction_left_n": left, "reaction_right_n": right,
            "fixed_end_internal_moment_nm": fixed_end_moment,
            "max_deflection_m": response(extreme)[0], "max_deflection_at_m": extreme,
            "max_abs_slope_rad": max_slope,
            "max_abs_bending_moment_nm": abs(response(max_moment_at)[3]),
            "max_abs_bending_moment_at_m": max_moment_at,
        },
        "table": {"columns": ["Position (m)", "Downward deflection (m)", "Downward slope (rad)",
                              "Shear (N)", "Sagging moment (N·m)"], "rows": rows},
        "plot": {"title": "Beam deflection (down positive)", "xLabel": "Position (m)",
                 "yLabel": "Downward deflection (m)", "x": xs, "y": [row[1] for row in rows]},
    }


def combined_stress(data):
    """Signed extreme-fiber normal stress and a supplied mean shear stress."""
    _require_positive(data, "area_m2", "section_modulus_m3", "shear_area_m2", "yield_strength_pa")
    if data["safety_factor_1"] < 1:
        raise ValueError("The required factor of safety must be at least 1 (dimensionless).")
    sigma_axial = data["axial_force_n"] / data["area_m2"]
    sigma_bending = data["bending_moment_nm"] / data["section_modulus_m3"]
    tau = data["shear_force_n"] / data["shear_area_m2"]
    side_a, side_b = sigma_axial + sigma_bending, sigma_axial - sigma_bending
    # Plane-stress von Mises at the two extreme fibers. The shear input is a
    # design-estimate average V/A_s, not a section-specific distribution.
    vm_a = math.hypot(side_a, math.sqrt(3)*tau)
    vm_b = math.hypot(side_b, math.sqrt(3)*tau)
    vm_max = max(vm_a, vm_b)
    allowable = data["yield_strength_pa"] / data["safety_factor_1"]
    utilization = vm_max / allowable
    return {
        "summary": ("Plane-stress von Mises yield screening of both extreme fibers (axial ± bending "
                    "normal stress and approximated transverse shear V/A_s). "
                    + ("PASS" if utilization <= 1 else "FAIL") + " at the requested safety factor."),
        "values": {
            "axial_stress_pa": sigma_axial, "bending_stress_pa": sigma_bending,
            "shear_stress_pa": tau, "normal_stress_face_a_pa": side_a,
            "normal_stress_face_b_pa": side_b, "von_mises_face_a_pa": vm_a,
            "von_mises_face_b_pa": vm_b, "max_von_mises_pa": vm_max,
            "allowable_von_mises_pa": allowable, "utilization_ratio_1": utilization,
            "yield_check": "PASS" if utilization <= 1 else "FAIL",
        },
        "table": {"columns": ["Extreme fiber (1)", "Normal stress (Pa)",
                              "Shear estimate (Pa)", "Von Mises stress (Pa)"],
                  "rows": [["A", side_a, tau, vm_a], ["B", side_b, tau, vm_b]]},
    }


def pipe_flow(data):
    """Fully developed incompressible pipe flow, Darcy (not Fanning) factor."""
    _require_positive(data, "diameter_m", "length_m", "density_kg_m3", "viscosity_pa_s", "gravity_m_s2")
    diameter = data["diameter_m"]
    roughness, rate = data["roughness_m"], data["volume_flow_m3_s"]
    if roughness < 0 or roughness / diameter > 0.05 or rate < 0:
        raise ValueError("Use 0 ≤ roughness/D ≤ 0.05 and nonnegative volume flow (m³/s).")
    speed = 4*rate/(math.pi*diameter**2)
    re = data["density_kg_m3"] * speed * diameter / data["viscosity_pa_s"]
    if not math.isfinite(re):
        raise ValueError("Reynolds number is outside the supported numerical range.")
    iterations, residual = 0, 0.0
    if rate == 0:
        regime, friction = "stagnant", 0.0
    elif re < 2300:
        regime, friction = "laminar", 64/re
    elif re < 4000:
        raise ValueError("Reynolds number 2300–3999 is transitional; no reliable single friction factor.")
    else:
        regime = "turbulent"
        # Colebrook–White in x = 1/sqrt(f); fixed-point iteration avoids
        # intermediate division by a small friction factor.
        inverse_root = 7.0
        for iterations in range(1, 101):
            updated = -2*math.log10(roughness/(3.7*diameter) + 2.51*inverse_root/re)
            if not math.isfinite(updated) or updated <= 0:
                raise ValueError("Colebrook iteration left the physical turbulent branch.")
            if abs(updated-inverse_root) <= 2e-13*max(1.0, updated):
                inverse_root = updated
                break
            inverse_root = updated
        else:
            raise ValueError("Colebrook friction factor did not converge within 100 iterations.")
        friction = 1/(inverse_root*inverse_root)
        residual = abs(inverse_root + 2*math.log10(roughness/(3.7*diameter) + 2.51*inverse_root/re))
    head = friction * (data["length_m"]/diameter) * speed**2 / (2*data["gravity_m_s2"])
    pressure = data["density_kg_m3"] * data["gravity_m_s2"] * head
    return {
        "summary": ("Full, straight, steady circular pipe. Darcy–Weisbach major loss; "
                    f"{regime} flow, " + ("iterated Colebrook–White friction." if iterations else
                                            "analytical laminar or stagnant limit.")),
        "values": {"velocity_m_s": speed, "reynolds_number_1": re,
                   "relative_roughness_1": roughness/diameter,
                   "darcy_friction_factor_1": friction, "colebrook_iterations_count": iterations,
                   "colebrook_residual_1": residual, "head_loss_m": head,
                   "pressure_drop_pa": pressure, "flow_regime": regime},
    }


def _lmtd(delta_a, delta_b):
    if delta_a <= 0 or delta_b <= 0:
        raise ValueError("A required terminal temperature difference is zero or reversed (pinch).")
    if delta_a == delta_b:
        return delta_a
    # (a-b)/ln(a/b), using log1p for nearly equal terminal differences.
    difference = (delta_a - delta_b) / delta_b
    return delta_b*difference/math.log1p(difference)


def heat_exchanger_sizing(data):
    """Rate-driven area design; invert epsilon–NTU independently of LMTD."""
    mode = data["flow_arrangement"]
    if mode not in ("parallel", "counterflow"):
        raise ValueError("Flow arrangement must be parallel or counterflow.")
    _require_positive(data, "hot_mass_flow_kg_s", "cold_mass_flow_kg_s", "hot_cp_j_kg_k",
                      "cold_cp_j_kg_k", "overall_u_w_m2_k", "target_heat_w")
    hot_in, cold_in = data["hot_inlet_c"], data["cold_inlet_c"]
    if not (-273.15 <= cold_in < hot_in) or hot_in < -273.15:
        raise ValueError("Inlet temperatures must be physically valid (≥ −273.15 °C), with hot > cold.")
    hot_c = data["hot_mass_flow_kg_s"] * data["hot_cp_j_kg_k"]
    cold_c = data["cold_mass_flow_kg_s"] * data["cold_cp_j_kg_k"]
    c_min, c_max = min(hot_c, cold_c), max(hot_c, cold_c)
    duty = data["target_heat_w"]
    q_max = c_min*(hot_in-cold_in)
    if not (0 < duty < q_max):
        raise ValueError("Target heat transfer must be between zero and C_min (T_h,in − T_c,in).")
    hot_out = hot_in - duty/hot_c
    cold_out = cold_in + duty/cold_c
    ratio = c_min/c_max
    effectiveness = duty/q_max
    if mode == "parallel":
        first, second = hot_in-cold_in, hot_out-cold_out
        if effectiveness*(1+ratio) >= 1:
            raise ValueError("Target effectiveness exceeds the parallel-flow thermodynamic limit.")
        ntu = -math.log1p(-effectiveness*(1+ratio))/(1+ratio)
    else:
        first, second = hot_in-cold_out, hot_out-cold_in
        if ratio == 1:
            ntu = effectiveness/(1-effectiveness)
        else:
            # Stable even for nearly balanced heat-capacity rates.
            d = 1-ratio
            ntu = -math.log1p(-effectiveness*d/(1-effectiveness*ratio))/d
    temperature_mean = _lmtd(first, second)
    area_lmtd = duty/(data["overall_u_w_m2_k"]*temperature_mean)
    area_ntu = ntu*c_min/data["overall_u_w_m2_k"]
    return {
        "summary": (f"Ideal {mode} single-pass exchanger, constant properties and U, no heat loss. "
                    "Independently sized by LMTD and ε–NTU; terminal differences must remain positive."),
        "values": {
            "hot_outlet_c": hot_out, "cold_outlet_c": cold_out,
            "hot_capacity_rate_w_k": hot_c, "cold_capacity_rate_w_k": cold_c,
            "capacity_rate_ratio_1": ratio, "max_possible_heat_w": q_max,
            "target_heat_w": duty, "effectiveness_1": effectiveness, "ntu_1": ntu,
            "terminal_difference_1_k": first, "terminal_difference_2_k": second,
            "lmtd_k": temperature_mean, "required_area_lmtd_m2": area_lmtd,
            "required_area_ntu_m2": area_ntu,
            "area_method_difference_percent": 100*abs(area_lmtd-area_ntu)/area_lmtd,
        },
        "table": {"columns": ["Design method (1)", "Required area (m²)"],
                  "rows": [["LMTD", area_lmtd], ["effectiveness–NTU", area_ntu]]},
    }


def _second_step_fraction(zeta, u):
    if zeta == 1:
        return -math.expm1(-u) - u*math.exp(-u)
    if zeta < 1:
        beta = math.sqrt((1-zeta)*(1+zeta))
        return 1 - math.exp(-zeta*u)*(math.cos(beta*u) + zeta*math.sin(beta*u)/beta)
    root = math.sqrt((zeta-1)*(zeta+1))
    fast = zeta+root
    slow = 1/fast  # avoid catastrophic cancellation in zeta - sqrt(zeta² - 1)
    return 1 - (fast*math.exp(-slow*u)-slow*math.exp(-fast*u))/(fast-slow)


def _bisect_threshold(f, target, left, right):
    """First crossing on a known monotone-increasing interval."""
    for _ in range(85):
        middle = (left+right)/2
        if f(middle) < target:
            left = middle
        else:
            right = middle
    return (left+right)/2


def _second_metrics(zeta, omega):
    response = lambda u: _second_step_fraction(zeta, u)
    if zeta < 1:
        beta = math.sqrt((1-zeta)*(1+zeta))
        peak_u = math.pi/beta
        upper = peak_u
        overshoot = 100*math.exp(-math.pi*zeta/beta)
        peak_time = peak_u/omega
    else:
        upper = 1.0
        while response(upper) < 0.9:
            upper *= 2
            if not math.isfinite(upper):
                raise ValueError("The response time scale is outside the supported numerical range.")
        overshoot, peak_time = 0.0, "none (monotonic)"
    rise = (_bisect_threshold(response, 0.9, 0, upper)
            - _bisect_threshold(response, 0.1, 0, upper))/omega
    if zeta < 1:
        # Step-error extrema lie at k*pi/omega_d, with magnitudes
        # exp(-k*pi*zeta/sqrt(1-zeta²)). Find the LAST 2%-band exit,
        # not the first entry or an approximate envelope bound.
        k = max(0, math.floor(math.log(50)*beta/(math.pi*zeta)))
        while k and math.exp(-math.pi*zeta*k/beta) < 0.02:
            k -= 1
        while math.exp(-math.pi*zeta*(k+1)/beta) >= 0.02:
            k += 1
        base = k*math.pi/beta
        left, right = base, (k+1)*math.pi/beta
        # Multiplying by (-1)**k removes sign; local-phase evaluation
        # avoids loss of trig precision for many oscillations.
        def signed_error(u):
            phase = beta*(u-base)
            return math.exp(-zeta*u)*(math.cos(phase) + zeta*math.sin(phase)/beta)
        for _ in range(85):
            middle = (left+right)/2
            if signed_error(middle) > 0.02:
                left = middle
            else:
                right = middle
        settling = ((left+right)/2)/omega
    else:
        top = max(upper, 1.0)
        while response(top) < 0.98:
            top *= 2
            if not math.isfinite(top):
                raise ValueError("The settling time exceeds the supported numerical range.")
        settling = _bisect_threshold(response, 0.98, 0, top)/omega
    return rise, overshoot, settling, peak_time


def _frequency_response(order, gain, tau, natural, zeta, w):
    if order == 1:
        phase = -math.degrees(math.atan(tau*w))
        amplitude = gain/math.hypot(1, tau*w)
    else:
        ratio = w/natural
        real, imag = 1-ratio*ratio, 2*zeta*ratio
        phase = -math.degrees(math.atan2(imag, real))
        amplitude = gain/math.hypot(real, imag)
    if amplitude <= 0 or not math.isfinite(amplitude):
        raise ValueError("Frequency response magnitude is outside the supported numerical range.")
    return amplitude, 20*math.log10(amplitude), phase


def transfer_function_response(data):
    """Stable, no-delay first/second-order low-pass open-loop models."""
    order, gain = data["order"], data["dc_gain_1"]
    if order not in (1, 2):
        raise ValueError("Transfer-function order must be 1 or 2.")
    _require_positive(data, "dc_gain_1")
    tau, natural, zeta = (data[key] for key in ("time_constant_s", "natural_frequency_rad_s", "damping_ratio_1"))
    if order == 1 and tau <= 0 or order == 2 and (natural <= 0 or zeta <= 0):
        raise ValueError("First order requires τ > 0 s; second order requires ωn > 0 rad/s and ζ > 0.")
    times, frequencies = data["time_s"], data["angular_frequency_rad_s"]
    _strictly_increasing_nonnegative(times, "Step sample times (s)")
    _strictly_increasing_nonnegative(frequencies, "Angular frequencies (rad/s)")
    step = data["step_amplitude_1"]
    if order == 1:
        ys = [-gain*step*math.expm1(-t/tau) for t in times]
        rise, overshoot, settling, peak = tau*math.log(9), 0.0, tau*math.log(50), "none (monotonic)"
        gc = [math.sqrt(gain*gain-1)/tau] if gain > 1 else []
    else:
        ys = [gain*step*_second_step_fraction(zeta, natural*t) for t in times]
        rise, overshoot, settling, peak = _second_metrics(zeta, natural)
        # Unity-gain roots of (1-r²)² + 4ζ²r² = K², r = ω/ωn.
        coefficient = 4*zeta*zeta-2
        discriminant = coefficient*coefficient + 4*(gain*gain-1)
        roots = [] if discriminant < 0 else [(-coefficient+math.sqrt(discriminant))/2,
                                              (-coefficient-math.sqrt(discriminant))/2]
        gc = sorted({natural*math.sqrt(y) for y in roots if y > 0})
    frequency_rows = [[w, *_frequency_response(order, gain, tau, natural, zeta, w)] for w in frequencies]
    margins = [(180 + _frequency_response(order, gain, tau, natural, zeta, w)[2], w) for w in gc]
    if margins:
        phase_margin, crossover = min(margins)
    else:
        phase_margin, crossover = "undefined (no finite unity-gain crossing)", "none (rad/s)"
    return {
        "summary": ("Stable ideal low-pass transfer function without zeros or delay. 10–90% rise, "
                    "exact last-exit 2% settling and open-loop Bode margins. "
                    "No finite −180° phase crossover, so gain margin is infinite."),
        "values": {
            "final_step_output_1": gain*step, "rise_time_10_90_s": rise,
            "peak_overshoot_percent": overshoot, "settling_time_2_percent_s": settling,
            "first_peak_time_s": peak, "gain_margin_ratio_1": "∞ (no finite phase crossover)",
            "gain_margin_db": "∞ (no finite phase crossover)",
            "phase_margin_deg": phase_margin, "gain_crossover_rad_s": crossover,
            "unity_gain_crossings_count": len(gc),
        },
        "table": {"columns": ["Angular frequency (rad/s)", "Magnitude (1)",
                              "Magnitude (dB)", "Phase (deg)"], "rows": frequency_rows},
        "plot": {"title": "Transfer-function unit step response", "xLabel": "Time (s)",
                 "yLabel": "Step output (1)", "x": times, "y": ys},
    }


def pid_closed_loop(data):
    """Exact step of a first-order plant with PID derivative on measurement."""
    _require_positive(data, "plant_gain_1", "plant_tau_s")
    for key in ("kp_1", "ki_per_s", "kd_s"):
        if data[key] < 0:
            raise ValueError(f"{key} must be nonnegative.")
    times = data["time_s"]
    _strictly_increasing_nonnegative(times, "Closed-loop sample times (s)")
    plant, tau = data["plant_gain_1"], data["plant_tau_s"]
    kp, ki, kd, target = (data[key] for key in ("kp_1", "ki_per_s", "kd_s", "setpoint_1"))
    a, b, c = tau+plant*kd, 1+plant*kp, plant*ki
    if not all(math.isfinite(x) and x > 0 for x in (a, b)) or not math.isfinite(c):
        raise ValueError("The PID closed-loop coefficients are outside the supported numerical range.")
    values = {}
    if ki == 0:
        steady = target*plant*kp/b
        tau_cl = a/b
        ys = [-steady*math.expm1(-t/tau_cl) for t in times]
        values["closed_loop_time_constant_s"] = tau_cl
    else:
        steady = target
        alpha = b/(2*a)
        omega_sq = c/a
        derivative0 = target*plant*kp/a
        initial = -steady
        if alpha*alpha < omega_sq:
            beta = math.sqrt(omega_sq-alpha*alpha)
            ys = [steady + math.exp(-alpha*t)*(initial*math.cos(beta*t)
                  + (derivative0+alpha*initial)*math.sin(beta*t)/beta) for t in times]
        elif alpha*alpha == omega_sq:
            ys = [steady + math.exp(-alpha*t)*(initial+(derivative0+alpha*initial)*t)
                  for t in times]
        else:
            root = math.sqrt(alpha*alpha-omega_sq)
            fast, slow = -alpha-root, -omega_sq/(alpha+root)
            coefficient_slow = (derivative0-fast*initial)/(slow-fast)
            coefficient_fast = initial-coefficient_slow
            ys = [steady + coefficient_slow*math.exp(slow*t)
                  + coefficient_fast*math.exp(fast*t) for t in times]
        values["closed_loop_natural_frequency_rad_s"] = math.sqrt(omega_sq)
        values["closed_loop_damping_ratio_1"] = alpha/math.sqrt(omega_sq)
    errors = [target-y for y in ys]
    values.update({"steady_state_output_1": steady, "steady_state_error_1": target-steady,
                   "last_sample_output_1": ys[-1], "last_sample_error_1": errors[-1]})
    return {
        "summary": ("Stable unity-negative-feedback PID on a first-order plant, with derivative on "
                    "measurement (no setpoint derivative kick), zero initial conditions and ideal continuous time. "
                    "All signals and plant/controller gains are normalized dimensionless."),
        "values": values,
        "table": {"columns": ["Time (s)", "Output (1)", "Error (1)"],
                  "rows": [[t, y, e] for t, y, e in zip(times, ys, errors)]},
        "plot": {"title": "PID closed-loop setpoint step", "xLabel": "Time (s)",
                 "yLabel": "Controlled output (1)", "x": times, "y": ys},
    }


# These additions deliberately do not change or reorder the three legacy IDs,
# their field definitions, examples, functions, or serialized results.
OPERATIONS += (
    Operation(
        "beam_response", "Beam deflection, slope, shear and moment",
        "Euler–Bernoulli prismatic beam, simple supports or a fixed-left cantilever. "
        "A single downward point load and/or a whole-span downward UDL; small deflections, linear elastic EI.",
        (
            Field("support", "Support: simply_supported or cantilever", "text", unit="1 (choice)"),
            Field("length_m", "Beam length", "number", unit="m"),
            Field("young_modulus_pa", "Young's modulus E", "number", unit="Pa"),
            Field("second_moment_m4", "Second moment of area I", "number", unit="m⁴"),
            Field("point_load_n", "Downward point load P (zero allowed)", "number", unit="N"),
            Field("point_location_m", "Point position from left", "number", unit="m"),
            Field("uniform_load_n_m", "Downward uniform load w (zero allowed)", "number", unit="N/m"),
            Field("position_m", "Ascending span sample positions", "vector", unit="m"),
        ),
        {"support": "simply_supported", "length_m": 4, "young_modulus_pa": 200e9,
         "second_moment_m4": 8e-6, "point_load_n": 1000, "point_location_m": 2,
         "uniform_load_n_m": 100, "position_m": [0, 1, 2, 3, 4]},
        beam_response,
    ),
    Operation(
        "combined_stress", "Axial, bending and von Mises yield check",
        "Check both extreme fibers using sigma=F/A±M/Z and von Mises=sqrt(sigma²+3 tau²), "
        "with tau approximated by V/A_s. Yield allowables include the specified safety factor.",
        (
            Field("axial_force_n", "Signed axial force (tension positive)", "number", unit="N"),
            Field("bending_moment_nm", "Signed bending moment", "number", unit="N·m"),
            Field("area_m2", "Axial section area", "number", unit="m²"),
            Field("section_modulus_m3", "Elastic section modulus", "number", unit="m³"),
            Field("shear_force_n", "Signed transverse shear force", "number", unit="N"),
            Field("shear_area_m2", "Effective shear area for V/A_s", "number", unit="m²"),
            Field("yield_strength_pa", "Material tensile yield strength", "number", unit="Pa"),
            Field("safety_factor_1", "Required factor of safety", "number", unit="1"),
        ),
        {"axial_force_n": 50000, "bending_moment_nm": 2000, "area_m2": 0.005,
         "section_modulus_m3": 1e-4, "shear_force_n": 10000, "shear_area_m2": 0.004,
         "yield_strength_pa": 250e6, "safety_factor_1": 2},
        combined_stress,
    ),
    Operation(
        "pipe_flow", "Pipe Reynolds number and Darcy–Weisbach loss",
        "Incompressible, full, straight circular pipe: laminar f=64/Re or turbulent Darcy factor "
        "iterated from Colebrook–White. Transitional 2300≤Re<4000 is rejected; no minor losses.",
        (
            Field("diameter_m", "Inside diameter", "number", unit="m"),
            Field("length_m", "Straight pipe length", "number", unit="m"),
            Field("roughness_m", "Absolute inner-wall roughness", "number", unit="m"),
            Field("volume_flow_m3_s", "Volumetric flow rate", "number", unit="m³/s"),
            Field("density_kg_m3", "Fluid density", "number", unit="kg/m³"),
            Field("viscosity_pa_s", "Dynamic viscosity", "number", unit="Pa·s"),
            Field("gravity_m_s2", "Gravitational acceleration", "number", unit="m/s²"),
        ),
        {"diameter_m": 0.1, "length_m": 30, "roughness_m": 0.00002,
         "volume_flow_m3_s": 0.01, "density_kg_m3": 1000,
         "viscosity_pa_s": 0.001, "gravity_m_s2": 9.80665},
        pipe_flow,
    ),
    Operation(
        "heat_exchanger_sizing", "Size parallel/counterflow exchanger: LMTD and ε–NTU",
        "Size the area for a specified heat duty from both LMTD and effectiveness–NTU, "
        "using a single-pass ideal exchanger with constant heat capacities and overall U.",
        (
            Field("flow_arrangement", "Arrangement: parallel or counterflow", "text", unit="1 (choice)"),
            Field("hot_inlet_c", "Hot fluid inlet temperature", "number", unit="°C"),
            Field("cold_inlet_c", "Cold fluid inlet temperature", "number", unit="°C"),
            Field("hot_mass_flow_kg_s", "Hot mass flow rate", "number", unit="kg/s"),
            Field("cold_mass_flow_kg_s", "Cold mass flow rate", "number", unit="kg/s"),
            Field("hot_cp_j_kg_k", "Hot specific heat", "number", unit="J/(kg·K)"),
            Field("cold_cp_j_kg_k", "Cold specific heat", "number", unit="J/(kg·K)"),
            Field("overall_u_w_m2_k", "Overall heat transfer coefficient", "number", unit="W/(m²·K)"),
            Field("target_heat_w", "Required heat transfer rate", "number", unit="W"),
        ),
        {"flow_arrangement": "counterflow", "hot_inlet_c": 150, "cold_inlet_c": 40,
         "hot_mass_flow_kg_s": 2.5, "cold_mass_flow_kg_s": 5,
         "hot_cp_j_kg_k": 4000, "cold_cp_j_kg_k": 4000,
         "overall_u_w_m2_k": 500, "target_heat_w": 650000},
        heat_exchanger_sizing,
    ),
    Operation(
        "transfer_function_response", "First/second-order steps, Bode and stability margins",
        "Stable low-pass G(s)=K/(τs+1) (order 1) or K ωn²/(s²+2ζωn s+ωn²) "
        "(order 2), no time delay. Exact 10–90% rise and last-exit 2% settling; "
        "open-loop gain/phase margins of G(s), not of unity-feedback closed loop.",
        (
            Field("order", "Transfer function order (1 or 2)", "integer", unit="1"),
            Field("dc_gain_1", "Positive DC gain K", "number", unit="1"),
            Field("time_constant_s", "First-order time constant τ (unused for order 2)", "number", unit="s"),
            Field("natural_frequency_rad_s", "Second-order natural frequency ωn (unused for order 1)", "number", unit="rad/s"),
            Field("damping_ratio_1", "Second-order damping ζ (unused for order 1)", "number", unit="1"),
            Field("step_amplitude_1", "Step input amplitude", "number", unit="1"),
            Field("time_s", "Ascending step sample times", "vector", unit="s"),
            Field("angular_frequency_rad_s", "Ascending Bode angular frequencies", "vector", unit="rad/s"),
        ),
        {"order": 2, "dc_gain_1": 2, "time_constant_s": 1,
         "natural_frequency_rad_s": 3, "damping_ratio_1": 0.5, "step_amplitude_1": 1,
         "time_s": [0, 0.25, 0.5, 1, 2, 3, 4, 6],
         "angular_frequency_rad_s": [0, 0.5, 1, 2, 3, 4, 6, 10, 30]},
        transfer_function_response,
    ),
    Operation(
        "pid_closed_loop", "PID-controlled first-order plant closed-loop step",
        "Exact continuous-time response for P(s)=K/(τs+1), unity negative feedback, "
        "u=Kp e+Ki integral(e)−Kd dy/dt (derivative on measurement). "
        "Gains and signals normalized; no saturation, delay or derivative filter.",
        (
            Field("plant_gain_1", "Positive plant static gain", "number", unit="1"),
            Field("plant_tau_s", "Plant time constant", "number", unit="s"),
            Field("kp_1", "Proportional gain Kp", "number", unit="1"),
            Field("ki_per_s", "Integral gain Ki", "number", unit="1/s"),
            Field("kd_s", "Derivative gain Kd", "number", unit="s"),
            Field("setpoint_1", "Setpoint step amplitude", "number", unit="1"),
            Field("time_s", "Ascending simulation times", "vector", unit="s"),
        ),
        {"plant_gain_1": 1, "plant_tau_s": 2, "kp_1": 2, "ki_per_s": 1,
         "kd_s": 0.2, "setpoint_1": 1, "time_s": [0, 0.2, 0.5, 1, 2, 3, 5, 8, 12]},
        pid_closed_loop,
    ),
)
