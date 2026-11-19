"""Idealized oscillator and thermal models in SI units."""

import math
from fractions import Fraction

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
    # Divide length by both conductivity and area before rounding. The
    # intermediate L/k alone can vanish even with a representable resistance.
    exact = [Fraction(length) / Fraction(k) / Fraction(area) for length, k in zip(thickness, conductivities)]
    resistances = [float(r) for r in exact]
    if min(resistances) == 0:
        raise ValueError("A layer resistance is below the supported numerical range.")
    total = sum(exact)
    heat_rate = (Fraction(t1) - Fraction(t2)) / total
    heat_flux = heat_rate / Fraction(area)
    if heat_rate and (float(heat_rate) == 0 or float(heat_flux) == 0):
        raise ValueError("The nonzero heat rate or heat flux is below the supported numerical range.")
    return {
        "summary": "Steady one-dimensional conduction through layers in perfect contact. Positive flow is left to right.",
        "values": {"resistance_k_w": float(total), "heat_rate_w": float(heat_rate), "heat_flux_w_m2": float(heat_flux)},
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
    heat_capacity = Fraction(mass) * Fraction(capacity)
    if float(heat_capacity) == 0:
        raise ValueError("Heat capacity is below the supported numerical range.")
    return {
        "summary": "Sensible heat at constant specific heat, with no phase change. Positive heat enters the sample.",
        "values": {
            "temperature_change_k": delta,
            "heat_capacity_j_k": float(heat_capacity),
            "heat_j": float(heat_capacity * (Fraction(final) - Fraction(initial))),
        },
    }


OPERATIONS: tuple[Operation, ...] = (
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
        headline="heat_j",
    ),
)

# Extended mechanics, thermal, wave, optical and circuit models.  These
# additions follow the original three operations so their IDs, defaults,
# numerical outputs and saved-result receipts remain byte-for-byte stable.
_C_LIGHT_M_S = 299_792_458  # exact SI definition


def _positive_samples(samples, name, *, zero=True):
    if not samples or (min(samples) < 0 if zero else min(samples) <= 0):
        raise ValueError(f"{name} must be nonnegative and nonempty." if zero else f"{name} must be positive and nonempty.")
    if any(b <= a for a, b in zip(samples, samples[1:])):
        raise ValueError(f"{name} must be strictly increasing.")
    return samples


def _second_order_basis(natural_rad_s, damping_per_s, time_s):
    """Fundamental solutions u(0)=1,u'(0)=0 and v(0)=0,v'(0)=1.

    Thus x = y0*u + vy0*v and x' = -omega0**2*y0*v +
    vy0*(u-2*gamma*v), valid for under/critical/overdamping.
    """
    omega2 = natural_rad_s * natural_rad_s
    gamma = damping_per_s
    discriminant = gamma * gamma - omega2
    if abs(discriminant) <= 1e-12 * max(gamma * gamma, omega2):
        decay = math.exp(-gamma * time_s)
        u = decay * (1 + gamma * time_s)
        v = decay * time_s
    elif discriminant < 0:
        omega_d = math.sqrt(-discriminant)
        decay = math.exp(-gamma * time_s)
        sine = math.sin(omega_d * time_s)
        v = decay * sine / omega_d
        u = decay * math.cos(omega_d * time_s) + gamma * v
    else:
        delta = math.sqrt(discriminant)
        slow = -gamma + delta
        fast = -gamma - delta
        # Both roots are nonpositive. This avoids overflowing cosh/sinh.
        e_slow, e_fast = math.exp(slow * time_s), math.exp(fast * time_s)
        v = (e_slow - e_fast) / (2 * delta)
        u = (-fast * e_slow + slow * e_fast) / (2 * delta)
    return u, v, -omega2 * v, u - 2 * gamma * v


def _oscillator_extended(data):
    mass = data["mass_kg"]
    k = data["stiffness_n_m"]
    damping = data["damping_n_s_m"]
    force = data["drive_force_n"]
    frequency = data["drive_frequency_hz"]
    times = _positive_samples(data["time_s"], "Time samples")
    scans = _positive_samples(data["scan_frequency_hz"], "Scan frequencies")
    if mass <= 0 or k <= 0 or damping < 0 or frequency < 0 or force < 0:
        raise ValueError("Mass and stiffness must be positive; damping, driving force and frequency must be nonnegative.")
    omega0 = math.sqrt(k / mass)
    gamma = damping / (2 * mass)
    omega = math.tau * frequency
    detuning = k - mass * omega * omega
    quadrature = damping * omega
    impedance = math.hypot(detuning, quadrature)
    resonant_drive = force > 0 and damping == 0 and abs(omega - omega0) <= 1e-14 * omega0
    if resonant_drive:
        # At exact undamped resonance the steady-state ansatz fails; a secular
        # t*sin(omega0*t) term is the exact forced response instead.
        a = b = 0.0
        amplitude = "unbounded (m)"
        phase = "undefined at resonance (rad)"
    else:
        if impedance == 0:
            a = b = 0.0  # no forcing; the response is entirely homogeneous
        else:
            a = force / impedance * (detuning / impedance)
            b = force / impedance * (quadrature / impedance)
        amplitude = math.hypot(a, b)
        phase = math.atan2(quadrature, detuning) if force else 0.0
    x0, v0 = data["initial_displacement_m"], data["initial_velocity_m_s"]
    if resonant_drive:
        y0, vy0 = x0, v0
    else:
        y0, vy0 = x0 - a, v0 - b * omega
    rows = []
    for time in times:
        u, v, du, dv = _second_order_basis(omega0, gamma, time)
        if resonant_drive:
            secular = force / (2 * mass * omega0)
            xp = secular * time * math.sin(omega0 * time)
            vp = secular * (math.sin(omega0 * time) + omega0 * time * math.cos(omega0 * time))
        else:
            xp = a * math.cos(omega * time) + b * math.sin(omega * time)
            vp = omega * (-a * math.sin(omega * time) + b * math.cos(omega * time))
        displacement = xp + y0 * u + vy0 * v
        velocity = vp + y0 * du + vy0 * dv
        acceleration = (force * math.cos(omega * time) - damping * velocity - k * displacement) / mass
        rows.append([time, displacement, velocity, acceleration])
    if damping > 0:
        peak_rad = math.sqrt(max(0.0, omega0 * omega0 - 2 * gamma * gamma))
        quality = mass * omega0 / damping
        peak_amplitude = force / math.hypot(k - mass * peak_rad**2, damping * peak_rad)
    else:
        peak_rad = omega0
        quality = "infinite (1; undamped)"
        peak_amplitude = "unbounded (m)" if force else 0.0
    curve = []
    for scan_hz in scans:
        w = math.tau * scan_hz
        z = math.hypot(k - mass * w * w, damping * w)
        if z == 0 and force:
            raise ValueError("Undamped resonance has no finite steady-state amplitude; omit the resonant scan frequency.")
        curve.append(force / z if z else 0.0)
    return {
        "summary": "Forced mass–spring–damper response including initial-condition transients; time table gives x, v and a, and plot gives steady-state amplitude versus frequency. Forcing is F cos(2πft).",
        "values": {
            "natural_frequency_hz": omega0 / math.tau,
            "damping_ratio_1": gamma / omega0,
            "quality_factor_1": quality,
            "resonance_peak_frequency_hz": peak_rad / math.tau,
            "resonance_peak_amplitude_m": peak_amplitude,
            "drive_amplitude_m": amplitude,
            "drive_phase_lag_rad": phase,
            "last_displacement_m": rows[-1][1],
        },
        "plot": {"title": "Steady-state mechanical resonance", "xLabel": "Drive frequency (Hz)",
                 "yLabel": "Displacement amplitude (m)", "x": scans, "y": curve},
        "table": {"columns": ["Time (s)", "Displacement (m)", "Velocity (m/s)", "Acceleration (m/s²)"], "rows": rows},
    }


def _projectile(data):
    speed = data["initial_speed_m_s"]
    angle = math.radians(data["launch_angle_deg"])
    height = data["release_height_m"]
    gravity = data["gravity_m_s2"]
    mass = data["mass_kg"]
    rho = data["air_density_kg_m3"]
    cd = data["drag_coefficient_1"]
    area = data["cross_section_m2"]
    step = data["time_step_s"]
    limit = data["max_duration_s"]
    if (speed < 0 or height < 0 or gravity <= 0 or mass <= 0 or rho < 0
            or cd < 0 or area < 0 or step <= 0 or limit <= 0 or not -90 <= data["launch_angle_deg"] <= 90):
        raise ValueError("Use |launch angle| <= 90°, positive gravity, mass, step and duration, and nonnegative speed, height and drag parameters.")
    vx0, vy0 = speed * math.cos(angle), speed * math.sin(angle)
    drag = 0.5 * rho * cd * area / mass
    if height == 0 and vy0 <= 0:
        drag = 0.0  # already at the ground, so no flight or drag integration
    if drag == 0:
        # Exactly solve the quadratic ground event when no air resistance is present.
        root = math.hypot(vy0, math.sqrt(2 * gravity * height))
        flight = 2 * height / (root - vy0) if vy0 < 0 else (vy0 + root) / gravity
        if flight > limit:
            raise ValueError("The projectile does not reach the ground within max duration (s).")
        count = min(256, max(2, math.ceil(flight / step) + 1)) if flight else 1
        times = [flight * i / (count - 1) for i in range(count)] if flight else [0.0]
        states = [(vx0 * t, max(0.0, height + vy0*t - 0.5*gravity*t*t), vx0, vy0-gravity*t) for t in times]
        max_height = height + max(vy0, 0.0)**2 / (2*gravity)
    else:
        def rhs(s):
            _, _, vx, vy = s
            velocity = math.hypot(vx, vy)
            return (vx, vy, -drag*velocity*vx, -gravity-drag*velocity*vy)

        def rk4(s, dt):
            k1 = rhs(s)
            k2 = rhs(tuple(x + dt*a/2 for x, a in zip(s, k1)))
            k3 = rhs(tuple(x + dt*a/2 for x, a in zip(s, k2)))
            k4 = rhs(tuple(x + dt*a for x, a in zip(s, k3)))
            return tuple(x + dt*(a+2*b+2*c+d)/6 for x,a,b,c,d in zip(s,k1,k2,k3,k4))

        state = (0.0, height, vx0, vy0)
        path = [(0.0, state)]
        max_height = height
        t = 0.0
        for _ in range(100000):
            if t >= limit:
                raise ValueError("The projectile does not reach the ground within max duration (s).")
            dt = min(step, limit-t, 0.25 / (drag * max(math.hypot(state[2], state[3]), 1e-15)))
            next_state = rk4(state, dt)
            if not all(math.isfinite(v) for v in next_state):
                raise ValueError("Trajectory exceeded the numerical range; reduce the time step (s).")
            if state[3] > 0 and next_state[3] <= 0:
                low, high = 0.0, dt
                for _ in range(36):
                    middle = (low+high)/2
                    if rk4(state, middle)[3] > 0:
                        low = middle
                    else:
                        high = middle
                max_height = max(max_height, rk4(state, (low+high)/2)[1])
            else:
                max_height = max(max_height, next_state[1])
            if next_state[1] <= 0 and (state[1] > 0 or t > 0):
                low, high = 0.0, dt
                for _ in range(40):
                    middle = (low+high)/2
                    if rk4(state, middle)[1] > 0:
                        low = middle
                    else:
                        high = middle
                impact_dt = (low+high)/2
                impact = rk4(state, impact_dt)
                flight = t + impact_dt
                path.append((flight, (impact[0], 0.0, impact[2], impact[3])))
                break
            t += dt
            state = next_state
            path.append((t, state))
        else:
            raise ValueError("Trajectory exceeds the 100000-step safety limit; increase time step (s).")
        if height == 0 and vy0 <= 0:
            path = [(0.0, (0.0, 0.0, vx0, vy0))]
            flight = 0.0
        # Preserve exact event and samples while bounding saved payloads.
        indices = sorted(set([round(i*(len(path)-1)/min(255,len(path)-1)) for i in range(min(255,len(path)-1)+1)]))
        times = [path[i][0] for i in indices]
        states = [path[i][1] for i in indices]
    rows = [[t, x, y, vx, vy] for t,(x,y,vx,vy) in zip(times, states)]
    x,y,vx,vy = states[-1]
    return {
        "summary": "Two-dimensional trajectory to ground level with constant g; quadratic drag Fd = -(ρ Cd A/2)|v|v uses RK4 and a refined impact event (or an exact vacuum parabola).",
        "values": {"flight_time_s": times[-1], "horizontal_range_m": x,
                   "maximum_height_m": max_height, "impact_speed_m_s": math.hypot(vx,vy)},
        "plot": {"title": "Projectile path", "xLabel": "Horizontal distance (m)",
                 "yLabel": "Height above ground (m)", "x": [row[1] for row in rows], "y": [row[2] for row in rows]},
        "table": {"columns": ["Time (s)", "Horizontal position (m)", "Height (m)",
                              "Horizontal velocity (m/s)", "Vertical velocity (m/s)"], "rows": rows},
    }


def _rigid_body_inertia(data):
    shape = data["shape"]
    mass, radius = data["mass_kg"], data["radius_m"]
    length, width = data["length_m"], data["width_m"]
    offset = data["parallel_axis_offset_m"]
    factors = {
        "solid_sphere": (2/5, radius**2, "Sphere, any central diameter"),
        "thin_spherical_shell": (2/3, radius**2, "Thin spherical shell, central diameter"),
        "solid_cylinder": (1/2, radius**2, "Solid cylinder, symmetry axis"),
        "solid_disk": (1/2, radius**2, "Thin disk, symmetry axis"),
        "thin_hoop": (1, radius**2, "Thin hoop, symmetry axis"),
        "slender_rod": (1/12, length**2, "Slender rod, center, perpendicular to length"),
        "rectangular_plate": (1/12, length**2+width**2, "Thin plate, center, normal to face"),
    }
    if shape not in factors:
        raise ValueError("Shape must be solid_sphere, thin_spherical_shell, solid_cylinder, solid_disk, thin_hoop, slender_rod or rectangular_plate.")
    if mass <= 0 or radius <= 0 or length <= 0 or width <= 0 or offset < 0:
        raise ValueError("Mass and dimensions must be positive, and parallel-axis distance must be nonnegative.")
    coefficient, dimension_squared, axis = factors[shape]
    centered = coefficient * mass * dimension_squared
    shifted = centered + mass * offset * offset
    return {
        "summary": f"{axis}. Parallel-axis shift is along a parallel line displaced by d: I = I_cm + m d²; uniform density assumed.",
        "values": {"centroidal_inertia_kg_m2": centered, "parallel_axis_inertia_kg_m2": shifted,
                   "radius_of_gyration_m": math.sqrt(shifted / mass)},
        "table": {"columns": ["Axis (1)", "Offset (m)", "Moment of inertia (kg·m²)"],
                  "rows": [["Centroidal", 0, centered], ["Parallel displaced", offset, shifted]]},
    }


def _transient_heat(data):
    length, alpha = data["length_m"], data["diffusivity_m2_s"]
    init, left, right = data["initial_temperature_k"], data["left_boundary_k"], data["right_boundary_k"]
    count, time, dt = data["nodes"], data["duration_s"], data["time_step_s"]
    scheme = data["scheme"]
    if scheme not in ("explicit", "implicit"):
        raise ValueError("Scheme must be explicit or implicit.")
    if length <= 0 or alpha <= 0 or min(init,left,right) < 0 or count < 3 or count > 256 or time <= 0 or dt <= 0:
        raise ValueError("Use positive slab length, diffusivity, duration and step; nonnegative Kelvin temperatures and 3–256 nodes.")
    intervals = math.ceil(time / dt)
    if not math.isfinite(intervals) or intervals > 20000:
        raise ValueError("At most 20000 time steps are supported; increase the step (s) or shorten duration (s).")
    dx = length / (count - 1)
    max_step = min(dt, time)
    courant = alpha * max_step / (dx * dx)
    if not math.isfinite(courant):
        raise ValueError("The Fourier number is outside the supported numerical range.")
    if scheme == "explicit" and courant > 0.5:
        raise ValueError("Unstable explicit scheme: Fourier number α Δt/Δx² must be <= 0.5; reduce time step (s).")
    temperatures = [left] + [init]*(count-2) + [right]
    initial = list(temperatures)
    for idx in range(intervals):
        h = min(dt, time-idx*dt)
        fo = alpha * h / (dx*dx)
        if scheme == "explicit":
            temperatures = [left] + [temperatures[i] + fo*(temperatures[i-1]-2*temperatures[i]+temperatures[i+1])
                                     for i in range(1,count-1)] + [right]
        else:
            # Backward Euler Dirichlet tridiagonal system, Thomas algorithm.
            n = count - 2
            diagonal = 1+2*fo
            modified = [0.0]*n
            rhs = [0.0]*n
            for i in range(n):
                denom = diagonal + (fo*modified[i-1] if i else 0.0)
                modified[i] = -fo/denom if i < n-1 else 0.0
                source = temperatures[i+1] + (fo*left if i == 0 else 0) + (fo*right if i == n-1 else 0)
                rhs[i] = (source + (fo*rhs[i-1] if i else 0.0))/denom
            interior = [0.0]*n
            interior[-1] = rhs[-1]
            for i in range(n-2,-1,-1):
                interior[i] = rhs[i] - modified[i]*interior[i+1]
            temperatures = [left] + interior + [right]
    xs = [length*i/(count-1) for i in range(count)]
    return {
        "summary": f"One-dimensional homogeneous slab, fixed Dirichlet endpoint temperatures, uniform initial interior, {scheme} finite differences (backward Euler if implicit). Explicit stability requires Fo <= 0.5.",
        "values": {"spatial_step_m": dx, "fourier_number_1": courant,
                   "explicit_stability_limit_1": 0.5, "time_steps_1": intervals,
                   "midpoint_temperature_k": temperatures[count//2],
                   "minimum_temperature_k": min(temperatures), "maximum_temperature_k": max(temperatures)},
        "plot": {"title": f"Slab profile at t = {time:g} s", "xLabel": "Position (m)",
                 "yLabel": "Temperature (K)", "x": xs, "y": temperatures},
        "table": {"columns": ["Position (m)", "Initial temperature (K)", "Final temperature (K)"],
                  "rows": [[x, a, b] for x,a,b in zip(xs,initial,temperatures)]},
    }


def _doppler(data):
    mode = data["wave_type"]
    emitted = data["emitted_frequency_hz"]
    wave_speed = data["medium_wave_speed_m_s"]
    source = data["source_toward_receiver_m_s"]
    receiver = data["receiver_toward_source_m_s"]
    if mode not in ("sound", "light"):
        raise ValueError("Wave type must be sound or light.")
    if emitted <= 0 or wave_speed <= 0:
        raise ValueError("Emission frequency and medium wave speed must be positive.")
    limit = wave_speed if mode == "sound" else _C_LIGHT_M_S
    if abs(source) >= limit or abs(receiver) >= limit:
        raise ValueError("Source and receiver speeds must be below wave propagation speed in magnitude.")
    if mode == "sound":
        ratio = (wave_speed + receiver) / (wave_speed-source)
        wavelength = (wave_speed-source) / emitted
    else:
        beta = (source/_C_LIGHT_M_S + receiver/_C_LIGHT_M_S) / (1+source*receiver/_C_LIGHT_M_S**2)
        ratio = math.sqrt((1+beta)/(1-beta))
        wavelength = _C_LIGHT_M_S / (emitted*ratio)
    received = emitted * ratio
    return {
        "summary": ("Collinear classical medium-wave Doppler shift f' = f(c+v_receiver)/(c-v_source)."
                    if mode == "sound" else "Collinear longitudinal relativistic Doppler shift, using Einstein velocity addition and exact vacuum light speed."),
        "values": {"received_frequency_hz": received, "frequency_ratio_1": ratio,
                   "fractional_frequency_shift_1": ratio-1, "wavelength_m": wavelength},
    }


def _relativity(data):
    mass, speed, proper = data["rest_mass_kg"], data["velocity_m_s"], data["proper_interval_s"]
    if mass <= 0 or proper < 0 or abs(speed) >= _C_LIGHT_M_S:
        raise ValueError("Rest mass must be positive, proper interval nonnegative and |velocity| below c = 299792458 m/s.")
    beta = speed / _C_LIGHT_M_S
    root = math.sqrt((1-beta)*(1+beta))
    gamma = 1/root
    rest_energy = mass * _C_LIGHT_M_S**2
    kinetic = (beta*beta/(root*(1+root))) * rest_energy
    return {
        "summary": "Special-relativistic collinear inertial-frame quantities with c = 299792458 m/s (exact); interval is lab time for the moving clock's proper interval.",
        "values": {"beta_1": beta, "lorentz_factor_1": gamma,
                   "dilated_interval_s": gamma*proper,
                   "relativistic_momentum_kg_m_s": gamma*mass*speed,
                   "rest_energy_j": rest_energy, "kinetic_energy_j": kinetic,
                   "total_energy_j": gamma*rest_energy},
    }


def _geometric_optics(data):
    mode = data["calculation"]
    focal, obj = data["focal_length_m"], data["object_distance_m"]
    n1,n2,incident = data["index_incident_1"], data["index_transmitted_1"], data["incidence_angle_deg"]
    if mode not in ("thin_lens", "spherical_mirror", "snell"):
        raise ValueError("Calculation must be thin_lens, spherical_mirror or snell.")
    if n1 <= 0 or n2 <= 0 or not 0 <= incident <= 90 or (mode != "snell" and obj <= 0):
        raise ValueError("Indices and lens/mirror object distance must be positive; incidence angle must be between 0 and 90 degrees.")
    if mode == "snell":
        ratio = n1*math.sin(math.radians(incident))/n2
        critical = math.degrees(math.asin(n2/n1)) if n1 > n2 else "not applicable (deg)"
        tir = n1 > n2 and incident > critical + 1e-12
        transmitted = "no transmitted ray (deg)" if tir else math.degrees(math.asin(min(1.0,ratio)))
        return {
            "summary": "Snell refraction n₁ sin θ₁ = n₂ sin θ₂; angles are measured from the surface normal. Beyond the critical angle, total internal reflection occurs.",
            "values": {"refracted_angle_deg": transmitted, "reflected_angle_deg": incident,
                       "critical_angle_deg": critical,
                       "total_internal_reflection_1": "yes (1)" if tir else "no (1)"},
        }
    if focal == 0:
        raise ValueError("Focal length must be nonzero for lens or mirror equations.")
    # Only an object exactly on the focal plane has an infinite image.
    # Nearby representable distances have finite (possibly very large) images.
    if obj == focal:
        image = "at infinity (m)"
        magnification = "unbounded (1)"
        nature = "image at infinity (1)"
    else:
        image = focal * (obj / (obj - focal))
        if not math.isfinite(image):
            raise ValueError("Image distance exceeds the supported numerical range (m).")
        magnification = -image / obj
        nature = "real/inverted (1)" if image > 0 else "virtual/upright (1)"
    return {
        "summary": ("Paraxial thin lens: 1/f = 1/do + 1/di; positive f converging and positive di real."
                    if mode == "thin_lens" else "Paraxial spherical mirror: 1/f = 1/do + 1/di; positive f concave and positive di real."),
        "values": {"image_distance_m": image, "lateral_magnification_1": magnification, "image_type_1": nature},
    }


def _slit_intensity(data):
    mode = data["pattern"]
    wavelength, width = data["wavelength_m"], data["slit_width_m"]
    separation, distance = data["slit_separation_m"], data["screen_distance_m"]
    peak = data["central_intensity_w_m2"]
    positions = data["screen_position_m"]
    if mode not in ("single", "double"):
        raise ValueError("Pattern must be single or double.")
    if (wavelength <= 0 or width <= 0 or separation <= 0 or distance <= 0 or peak < 0
            or not positions or any(b <= a for a,b in zip(positions,positions[1:]))):
        raise ValueError("Use positive wavelength, width, separation and screen distance; nonnegative central intensity and strictly increasing screen coordinates (m).")
    intensities = []
    for x in positions:
        sine = x / math.hypot(x, distance)
        beta = math.pi * width * sine / wavelength
        envelope = (math.sin(beta) / beta)**2 if abs(beta) > 1e-8 else (1-beta*beta/6)**2
        interference = math.cos(math.pi*separation*sine/wavelength)**2 if mode == "double" else 1.0
        intensities.append(peak*envelope*interference)
    first_min = math.degrees(math.asin(wavelength / width)) if wavelength <= width else "none in visible angular domain (deg)"
    return {
        "summary": "Fraunhofer far-field intensity on a flat screen, with finite slit-width sinc² envelope; double slits add coherent cos² fringes. No polarization or coherence losses.",
        "values": {"central_intensity_w_m2": peak,
                   "single_slit_first_minimum_deg": first_min,
                   "paraxial_fringe_spacing_m": wavelength*distance/separation if mode == "double" else "not applicable (m)"},
        "plot": {"title": f"{mode.title()}-slit far-field pattern", "xLabel": "Screen position (m)",
                 "yLabel": "Intensity (W/m²)", "x": positions, "y": intensities},
        "table": {"columns": ["Screen position (m)", "Intensity (W/m²)", "Relative intensity (1)"],
                  "rows": [[x,intensity,intensity/peak if peak else 0.0]
                           for x,intensity in zip(positions,intensities)]},
    }


def _rlc_series(data):
    resistance, inductance, capacitance = data["resistance_ohm"],data["inductance_h"],data["capacitance_f"]
    step_voltage, initial_v, initial_i = data["step_voltage_v"], data["initial_capacitor_voltage_v"],data["initial_current_a"]
    drive_voltage = data["drive_amplitude_v"]
    times = _positive_samples(data["time_s"], "Time samples")
    scans = _positive_samples(data["scan_frequency_hz"], "Frequency samples")
    if resistance < 0 or inductance <= 0 or capacitance <= 0 or drive_voltage < 0:
        raise ValueError("R must be nonnegative, L and C positive, and AC driving amplitude nonnegative.")
    omega0 = 1/math.sqrt(inductance*capacitance)
    gamma = resistance/(2*inductance)
    yc0, dyc0 = initial_v-step_voltage, initial_i/capacitance
    rows = []
    for t in times:
        u,v,du,dv = _second_order_basis(omega0,gamma,t)
        vc = step_voltage + yc0*u + dyc0*v
        current = capacitance*(yc0*du+dyc0*dv)
        energy = 0.5*capacitance*vc*vc + 0.5*inductance*current*current
        rows.append([t,vc,current,energy])
    currents = []
    for frequency in scans:
        if frequency == 0:
            current = 0.0
        else:
            w = math.tau*frequency
            reactance = w*inductance - 1/(w*capacitance)
            impedance = math.hypot(resistance,reactance)
            if impedance == 0 and drive_voltage:
                raise ValueError("Lossless resonant series RLC has no finite AC current amplitude; omit that frequency.")
            current = drive_voltage/impedance if impedance else 0.0
        currents.append(current)
    return {
        "summary": "Series RLC: exact second-order capacitor voltage and current after an ideal DC step, including initial state; AC plot shows sinusoidal steady-state current magnitude |V/Z|.",
        "values": {"natural_frequency_hz": omega0/math.tau,
                   "quality_factor_1": omega0*inductance/resistance if resistance else "infinite (1; lossless)",
                   "half_power_bandwidth_hz": resistance/(math.tau*inductance),
                   "last_capacitor_voltage_v": rows[-1][1], "last_current_a": rows[-1][2]},
        "plot": {"title": "Series RLC AC current response", "xLabel": "Frequency (Hz)",
                 "yLabel": "Current amplitude (A)", "x": scans, "y": currents},
        "table": {"columns": ["Time (s)", "Capacitor voltage (V)", "Series current (A)", "Stored energy (J)"],
                  "rows": rows},
    }


# Only append: legacy operation objects and their persisted payloads are untouched.
OPERATIONS += (
    Operation("damped_driven_oscillator", "Solve damped and driven oscillator",
              "Full x/v/a transient and driven resonance curve with damping ratio, resonance and Q.",
              (Field("mass_kg", "Mass", "number", unit="kg"),
               Field("stiffness_n_m", "Stiffness", "number", unit="N/m"),
               Field("damping_n_s_m", "Viscous damping", "number", unit="N·s/m"),
               Field("drive_force_n", "Drive-force amplitude", "number", unit="N"),
               Field("drive_frequency_hz", "Drive frequency", "number", unit="Hz"),
               Field("initial_displacement_m", "Initial displacement", "number", unit="m"),
               Field("initial_velocity_m_s", "Initial velocity", "number", unit="m/s"),
               Field("time_s", "Time samples", "vector", unit="s"),
               Field("scan_frequency_hz", "Resonance scan frequencies", "vector", unit="Hz")),
              {"mass_kg": 1.0,"stiffness_n_m": 100.0,"damping_n_s_m": 1.0,
               "drive_force_n": 1.0,"drive_frequency_hz": 1.0,
               "initial_displacement_m": 0.1,"initial_velocity_m_s": 0.0,
               "time_s": [0.1*i for i in range(101)],
               "scan_frequency_hz": [0.1*i for i in range(1,51)]}, _oscillator_extended, headline="drive_amplitude_m"),
    Operation("projectile_motion", "Model projectile with quadratic air drag",
              "Numerically integrate quadratic air resistance (or exact drag-free flight), locating impact and apex.",
              (Field("initial_speed_m_s","Launch speed","number",unit="m/s"),
               Field("launch_angle_deg","Launch elevation","number",unit="deg"),
               Field("release_height_m","Release height","number",unit="m"),
               Field("gravity_m_s2","Gravity","number",unit="m/s²"),
               Field("mass_kg","Projectile mass","number",unit="kg"),
               Field("air_density_kg_m3","Air density","number",unit="kg/m³"),
               Field("drag_coefficient_1","Drag coefficient","number",unit="1"),
               Field("cross_section_m2","Reference area","number",unit="m²"),
               Field("time_step_s","RK4 maximum time step","number",unit="s"),
               Field("max_duration_s","Maximum flight duration","number",unit="s")),
              {"initial_speed_m_s":30.0,"launch_angle_deg":45.0,"release_height_m":1.0,
               "gravity_m_s2":9.80665,"mass_kg":0.145,"air_density_kg_m3":1.225,
               "drag_coefficient_1":0.47,"cross_section_m2":0.0042,
               "time_step_s":0.01,"max_duration_s":60.0}, _projectile, headline="horizontal_range_m"),
    Operation("rigid_body_inertia", "Calculate rigid-body moment of inertia",
              "Uniform common shapes with center-axis inertia, parallel-axis shift and radius of gyration.",
              (Field("shape","Shape (solid_sphere, thin_spherical_shell, solid_cylinder, solid_disk, thin_hoop, slender_rod, rectangular_plate)","text",unit="1"),
               Field("mass_kg","Mass","number",unit="kg"),
               Field("radius_m","Radius (sphere/cylinder/disk/hoop)","number",unit="m"),
               Field("length_m","Length (rod/plate)","number",unit="m"),
               Field("width_m","Plate width","number",unit="m"),
               Field("parallel_axis_offset_m","Parallel-axis offset","number",unit="m")),
              {"shape":"solid_cylinder","mass_kg":2.0,"radius_m":0.3,
               "length_m":1.0,"width_m":0.4,"parallel_axis_offset_m":0.2},_rigid_body_inertia),
    Operation("transient_heat_1d","Solve transient 1D heat conduction",
              "Explicit central-space finite differences with Fo stability limit, or unconditionally stable implicit backward Euler.",
              (Field("scheme","Method (explicit or implicit)","text",unit="1"),
               Field("length_m","Slab length","number",unit="m"),
               Field("diffusivity_m2_s","Thermal diffusivity","number",unit="m²/s"),
               Field("initial_temperature_k","Initial interior temperature","number",unit="K"),
               Field("left_boundary_k","Left fixed boundary","number",unit="K"),
               Field("right_boundary_k","Right fixed boundary","number",unit="K"),
               Field("nodes","Spatial grid nodes","integer",unit="1"),
               Field("duration_s","Simulation time","number",unit="s"),
               Field("time_step_s","Maximum time step","number",unit="s")),
              {"scheme":"explicit","length_m":0.1,"diffusivity_m2_s":1e-5,
               "initial_temperature_k":293.15,"left_boundary_k":373.15,"right_boundary_k":293.15,
               "nodes":21,"duration_s":10.0,"time_step_s":0.5},_transient_heat, headline="midpoint_temperature_k"),
    Operation("doppler_shift","Calculate Doppler shift",
              "Collinear classical sound Doppler or relativistic longitudinal light Doppler; positive velocities approach.",
              (Field("wave_type","Wave type (sound or light)","text",unit="1"),
               Field("emitted_frequency_hz","Emission frequency","number",unit="Hz"),
               Field("medium_wave_speed_m_s","Wave speed in medium (sound)","number",unit="m/s"),
               Field("source_toward_receiver_m_s","Source velocity toward receiver","number",unit="m/s"),
               Field("receiver_toward_source_m_s","Receiver velocity toward source","number",unit="m/s")),
              {"wave_type":"sound","emitted_frequency_hz":440.0,
               "medium_wave_speed_m_s":343.0,"source_toward_receiver_m_s":20.0,
               "receiver_toward_source_m_s":0.0},_doppler, headline="received_frequency_hz"),
    Operation("relativistic_kinematics","Calculate relativistic energy and time",
              "Lorentz gamma, dilated clock interval, relativistic signed momentum, rest/kinetic/total energy.",
              (Field("rest_mass_kg","Rest mass","number",unit="kg"),
               Field("velocity_m_s","Signed speed","number",unit="m/s"),
               Field("proper_interval_s","Clock proper interval","number",unit="s")),
              {"rest_mass_kg":1.0,"velocity_m_s":0.6*_C_LIGHT_M_S,
               "proper_interval_s":1.0},_relativity, headline="lorentz_factor_1"),
    Operation("geometric_optics","Solve lenses, mirrors and Snell refraction",
              "Thin-lens and spherical-mirror signed image distance, or Snell's law with critical angle and TIR.",
              (Field("calculation","Mode (thin_lens, spherical_mirror, snell)","text",unit="1"),
               Field("focal_length_m","Focal length (lens/mirror)","number",unit="m"),
               Field("object_distance_m","Object distance (lens/mirror)","number",unit="m"),
               Field("index_incident_1","Incident refractive index","number",unit="1"),
               Field("index_transmitted_1","Transmitted refractive index","number",unit="1"),
               Field("incidence_angle_deg","Incidence from normal","number",unit="deg")),
              {"calculation":"thin_lens","focal_length_m":0.1,"object_distance_m":0.3,
               "index_incident_1":1.5,"index_transmitted_1":1.0,"incidence_angle_deg":30.0},_geometric_optics),
    Operation("slit_diffraction","Model single/double slit intensity",
              "Fraunhofer sinc-squared aperture envelope and optional coherent two-slit fringes on a flat screen.",
              (Field("pattern","Pattern (single or double)","text",unit="1"),
               Field("wavelength_m","Wavelength","number",unit="m"),
               Field("slit_width_m","Each slit width","number",unit="m"),
               Field("slit_separation_m","Slit center separation (double)","number",unit="m"),
               Field("screen_distance_m","Distance to screen","number",unit="m"),
               Field("central_intensity_w_m2","Central intensity","number",unit="W/m²"),
               Field("screen_position_m","Screen coordinates","vector",unit="m")),
              {"pattern":"double","wavelength_m":500e-9,"slit_width_m":10e-6,
               "slit_separation_m":100e-6,"screen_distance_m":2.0,
               "central_intensity_w_m2":1.0,
               "screen_position_m":[0.0005*i for i in range(-100,101)]},_slit_intensity, headline="single_slit_first_minimum_deg"),
    Operation("series_rlc_response","Solve series RLC transient and AC response",
              "Exact step-driven capacitor voltage, current, stored energy, AC current resonance, Q and bandwidth.",
              (Field("resistance_ohm","Series resistance","number",unit="Ω"),
               Field("inductance_h","Inductance","number",unit="H"),
               Field("capacitance_f","Capacitance","number",unit="F"),
               Field("step_voltage_v","DC source voltage after t=0","number",unit="V"),
               Field("initial_capacitor_voltage_v","Initial capacitor voltage","number",unit="V"),
               Field("initial_current_a","Initial inductor current","number",unit="A"),
               Field("drive_amplitude_v","AC voltage amplitude","number",unit="V"),
               Field("time_s","Time samples","vector",unit="s"),
               Field("scan_frequency_hz","AC scan frequencies","vector",unit="Hz")),
              {"resistance_ohm":10.0,"inductance_h":0.1,"capacitance_f":0.001,
               "step_voltage_v":5.0,"initial_capacitor_voltage_v":0.0,
               "initial_current_a":0.0,"drive_amplitude_v":1.0,
               "time_s":[i*0.001 for i in range(201)],
               "scan_frequency_hz":[i*0.25 for i in range(1,201)]},_rlc_series, headline="natural_frequency_hz"),
)


def _rlc_frequency(data):
    """Complex-impedance Bode samples, including phase, in an ordinary table."""
    resistance = data["resistance_ohm"]
    inductance = data["inductance_h"]
    capacitance = data["capacitance_f"]
    voltage = data["drive_amplitude_v"]
    scan = _positive_samples(data["frequency_hz"], "AC frequencies")
    if resistance < 0 or inductance <= 0 or capacitance <= 0 or voltage < 0:
        raise ValueError("R and source amplitude must be nonnegative; L and C must be positive.")
    omega0 = 1 / math.sqrt(inductance * capacitance)
    rows = []
    current_amplitudes = []
    for frequency in scan:
        if frequency == 0:
            impedance = "infinite (Ω)"
            phase = "undefined at DC (rad)"
            current = 0.0
        else:
            w = math.tau * frequency
            reactance = w * inductance - 1 / (w * capacitance)
            impedance = math.hypot(resistance, reactance)
            if impedance == 0 and voltage > 0:
                raise ValueError("Lossless RLC at resonant frequency has an unbounded steady-state AC response.")
            phase = math.atan2(reactance, resistance)
            current = voltage / impedance if impedance else 0.0
        rows.append([frequency, impedance, phase, current])
        current_amplitudes.append(current)
    return {
        "summary": "Series RLC phasor response to sinusoidal voltage: Z = R + j(ωL − 1/ωC). Positive impedance phase means inductive; current phase relative to voltage is its negative. DC capacitor is an open circuit.",
        "values": {"natural_frequency_hz": omega0 / math.tau,
                   "quality_factor_1": omega0 * inductance / resistance if resistance else "infinite (1; lossless)",
                   "half_power_bandwidth_hz": resistance / (math.tau * inductance),
                   "peak_current_amplitude_a": voltage / resistance if resistance else ("unbounded (A)" if voltage else 0.0)},
        "plot": {"title": "Series RLC current amplitude", "xLabel": "AC frequency (Hz)",
                 "yLabel": "Current amplitude (A)", "x": scan, "y": current_amplitudes},
        "table": {"columns": ["Frequency (Hz)", "Impedance amplitude (Ω)",
                              "Impedance phase (rad)", "Current amplitude (A)"], "rows": rows},
    }


OPERATIONS += (
    Operation("series_rlc_frequency", "Analyze series RLC complex impedance",
              "Frequency-sampled impedance magnitude and phase, current resonance, quality and bandwidth.",
              (Field("resistance_ohm", "Series resistance", "number", unit="Ω"),
               Field("inductance_h", "Inductance", "number", unit="H"),
               Field("capacitance_f", "Capacitance", "number", unit="F"),
               Field("drive_amplitude_v", "Sinusoidal source voltage amplitude", "number", unit="V"),
               Field("frequency_hz", "Increasing AC sample frequencies", "vector", unit="Hz")),
              {"resistance_ohm": 10.0, "inductance_h": 0.1, "capacitance_f": 0.001,
               "drive_amplitude_v": 1.0, "frequency_hz": [0.25*i for i in range(201)]},
              _rlc_frequency, headline="peak_current_amplitude_a"),
)
