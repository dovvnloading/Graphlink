"""Specimen measurements and reproducible engineering materials calculations.

Methods use only the standard library. Validity limits, reference conventions and
units are documented in docs/SCIENTIFIC_MATERIALS_REFERENCE.md.
"""

import math
from fractions import Fraction

from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.reference_data import Dataset

REFERENCE_DATASETS = (
    Dataset("materials.astm_e140_table_1", "2019.0.0", "materials",
            ("_E140_TABLE_1",), "ASTM E140-12b (2019 reapproval), Table 1"),
    Dataset("materials.astm_e140_table_2", "2019.0.0", "materials",
            ("_E140_TABLE_2",), "ASTM E140-12b (2019 reapproval), Table 2"),
)


def _hardness_reference_datasets(inputs: dict) -> tuple[str, ...]:
    table = inputs.get("e140_table")
    selected = ({
        "table1_non_austenitic_hrc": "materials.astm_e140_table_1",
        "table2_non_austenitic_hrb": "materials.astm_e140_table_2",
    }.get(table) if isinstance(table, str) else None)
    return (selected,) if selected else ()



def bulk_density(data):
    mass, volume = data["dry_mass_g"], data["bulk_volume_cm3"]
    if mass <= 0 or volume <= 0:
        raise ValueError("Dry mass and external specimen volume must be positive.")
    ratio = Fraction(mass) / Fraction(volume)
    density = float(ratio)
    return {
        "summary": f"Dry bulk density is {density:.6g} g/cm³.",
        "values": {"bulk_density_g_cm3": density, "bulk_density_kg_m3": float(ratio * 1000)},
    }


def tensile_response(data):
    forces, elongations = data["force_n"], data["extension_mm"]
    area, length = data["original_area_mm2"], data["gauge_length_mm"]
    if area <= 0 or length <= 0:
        raise ValueError("Original area and gauge length must be positive.")
    if not forces or len(forces) != len(elongations):
        raise ValueError("Supply one force and extension pair per reading.")
    if any(value < 0 for value in forces + elongations):
        raise ValueError("This tensile series accepts nonnegative forces and extensions.")
    strain = [d / length for d in elongations]
    stress = [f / area for f in forces]  # 1 N/mm² = 1 MPa.
    peak = max(stress)
    peak_strain = strain[stress.index(peak)]
    return {
        "summary": f"Peak measured engineering stress is {peak:.6g} MPa at strain {peak_strain:.6g}.",
        "values": {"peak_measured_stress_mpa": peak, "engineering_strain_at_first_peak": peak_strain},
        "table": {
            "columns": ["Engineering strain (mm/mm)", "Engineering stress (MPa)"],
            "rows": [list(row) for row in zip(strain, stress)],
        },
        "plot": {
            "title": "Engineering stress–strain",
            "xLabel": "Engineering strain (mm/mm)",
            "yLabel": "Engineering stress (MPa)",
            "x": strain,
            "y": stress,
        },
    }


def apparent_porosity(data):
    dry = data["dry_mass_g"]
    saturated = data["saturated_mass_g"]
    submerged = data["suspended_mass_g"]
    if dry <= 0 or saturated < dry or submerged >= dry:
        raise ValueError("Require a positive dry mass, saturated mass ≥ dry mass, and suspended mass < dry mass.")
    absorbed = Fraction(saturated) - Fraction(dry)
    displaced = Fraction(saturated) - Fraction(submerged)
    percent = float(100 * absorbed / displaced)
    return {
        "summary": f"Liquid-accessible open pores occupy {percent:.6g}% of the specimen bulk volume.",
        "values": {"apparent_open_porosity_percent": percent, "absorbed_liquid_g": saturated - dry},
    }


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "bulk_density",
        "Dry bulk density",
        "Divide dry mass by measured external volume, which includes both open and closed pores.",
        (
            Field("dry_mass_g", "Dry specimen mass", "number", unit="g"),
            Field("bulk_volume_cm3", "External specimen volume", "number", unit="cm³"),
        ),
        {"dry_mass_g": 37.5, "bulk_volume_cm3": 15},
        bulk_density,
    ),
    Operation(
        "tensile_response",
        "Engineering stress–strain",
        "Convert axial tension and extension readings to engineering stress and strain using "
        "the original area and gauge length. Readings retain their acquisition order.",
        (
            Field("force_n", "Measured tensile forces", "vector", unit="N"),
            Field("extension_mm", "Gauge extensions", "vector", unit="mm"),
            Field("original_area_mm2", "Original cross-sectional area", "number", unit="mm²"),
            Field("gauge_length_mm", "Original gauge length", "number", unit="mm"),
        ),
        {
            "force_n": [0, 100, 180, 160],
            "extension_mm": [0, 0.25, 0.6, 1],
            "original_area_mm2": 5,
            "gauge_length_mm": 50,
        },
        tensile_response,
    ),
    Operation(
        "apparent_porosity",
        "Apparent open porosity",
        "Estimate liquid-accessible porosity from dry, saturated surface-dry, and suspended mass "
        "readings. Assume complete pore saturation, a nonreactive liquid, and corrected holder tare.",
        (
            Field("dry_mass_g", "Dry mass in air", "number", unit="g"),
            Field("saturated_mass_g", "Saturated surface-dry mass in air", "number", unit="g"),
            Field("suspended_mass_g", "Apparent saturated mass submerged in the same liquid", "number", unit="g"),
        ),
        {"dry_mass_g": 80, "saturated_mass_g": 86, "suspended_mass_g": 56},
        apparent_porosity,
    ),
)


# Calculations below do not alter the three original operations or the shared
# result/receipt serialization contract.


def _positive(value, label):
    if value <= 0:
        raise ValueError(f"{label} must be positive.")
    return value


def _curve(rows, *, minimum=2, positive_stress=False):
    if len(rows) < minimum or any(len(row) != 2 for row in rows):
        raise ValueError(f"Supply at least {minimum} paired strain/stress readings (two columns).")
    strains = [row[0] for row in rows]
    stresses = [row[1] for row in rows]
    if any(x < 0 for x in strains) or any(y < 0 for y in stresses):
        raise ValueError("Tensile strain and stress must be nonnegative.")
    if positive_stress and any(y <= 0 for y in stresses):
        raise ValueError("Fitting stress readings must be positive.")
    if any(next_x <= x for x, next_x in zip(strains, strains[1:])):
        raise ValueError("Strain readings must increase strictly in acquisition order.")
    return strains, stresses


def _trapezoid(x, y, end=None):
    """Integrate sampled y dx to end; linearly interpolate the final ordinate."""
    last = x[-1] if end is None else end
    if last < x[0] or last > x[-1]:
        raise ValueError("Integration limit must lie in the measured strain range.")
    total = 0.0
    for left, right, yl, yr in zip(x, x[1:], y, y[1:]):
        if left >= last:
            break
        stop = min(right, last)
        y_stop = yl + (yr - yl) * (stop - left) / (right - left)
        total += (stop - left) * (yl + y_stop) / 2
    return total


def _linear_fit(x, y):
    if len(x) < 2 or len(x) != len(y):
        raise ValueError("At least two paired readings are required for a fit.")
    x_bar = math.fsum(x) / len(x)
    y_bar = math.fsum(y) / len(y)
    xx = math.fsum((v - x_bar) ** 2 for v in x)
    if xx <= 0:
        raise ValueError("The independent variable must vary for a linear fit.")
    slope = math.fsum((a - x_bar) * (b - y_bar) for a, b in zip(x, y)) / xx
    intercept = y_bar - slope * x_bar
    residual = math.fsum((b - (slope * a + intercept)) ** 2 for a, b in zip(x, y))
    variance = math.fsum((b - y_bar) ** 2 for b in y)
    r_squared = 1 - residual / variance if variance else (1.0 if not residual else 0.0)
    return slope, intercept, r_squared


def stress_strain_analysis(data):
    """Fit elastic slope, locate 0.2%-proof intersection, integrate measured work."""
    strain, stress = _curve(data["engineering_curve"], minimum=5)
    limit = data["elastic_limit_strain_mm_mm"]
    if limit <= strain[0] or limit >= strain[-1]:
        raise ValueError("Elastic fit limit must be inside the measured strain range.")
    elastic = [(e, s) for e, s in zip(strain, stress) if e <= limit]
    if len(elastic) < 3:
        raise ValueError("Elastic fit needs at least three samples up to the selected limit.")
    modulus, intercept, r2 = _linear_fit([v[0] for v in elastic], [v[1] for v in elastic])
    if modulus <= 0:
        raise ValueError("The fitted Young modulus must be positive.")
    if r2 < 0.98:
        raise ValueError("The selected elastic region is not sufficiently linear (R² < 0.98).")
    peak_index = stress.index(max(stress))
    # Require the 0.2%-offset intersection on the rising curve, rather than
    # misidentifying the descending post-necking portion as the yield point.
    offset = [s - (modulus * (e - 0.002) + intercept) for e, s in zip(strain, stress)]
    crossing = None
    for i in range(1, peak_index + 1):
        if strain[i] < 0.002 or offset[i - 1] < 0 or offset[i] > 0:
            continue
        fraction = offset[i - 1] / (offset[i - 1] - offset[i]) if offset[i - 1] != offset[i] else 0
        crossing = (strain[i - 1] + fraction * (strain[i] - strain[i - 1]),
                    stress[i - 1] + fraction * (stress[i] - stress[i - 1]))
        break
    if crossing is None:
        raise ValueError("No 0.2% offset-yield intersection on the pre-UTS curve; add measured points.")
    if strain[0] != 0 or stress[0] != 0:
        raise ValueError("For total energy densities the measured curve must start at (0 mm/mm, 0 MPa).")
    toughness = _trapezoid(strain, stress)  # MPa times dimensionless = MJ/m³.
    resilience = _trapezoid(strain, stress, end=limit)
    return {
        "summary": "Elastic least-squares fit and 0.2% offset yield on the pre-UTS branch; energies integrate measured points.",
        "values": {
            "young_modulus_mpa": modulus,
            "elastic_intercept_mpa": intercept,
            "elastic_fit_r_squared_dimensionless": r2,
            "offset_yield_strength_mpa": crossing[1],
            "offset_yield_strain_mm_mm": crossing[0],
            "ultimate_tensile_strength_mpa": stress[peak_index],
            "elongation_at_break_percent": strain[-1] * 100,
            "toughness_mj_m3": toughness,
            "resilience_to_elastic_limit_mj_m3": resilience,
        },
        "table": {"columns": ["Engineering strain (mm/mm)", "Engineering stress (MPa)"],
                  "rows": data["engineering_curve"]},
        "plot": {"title": "Measured engineering tensile curve", "xLabel": "Engineering strain (mm/mm)",
                 "yLabel": "Engineering stress (MPa)", "x": strain, "y": stress},
    }


def true_stress_strain(data):
    """Bidirectional conversion, valid only for uniform volume-conserving tension."""
    strain, stress = _curve(data["uniform_curve"])
    direction = data["direction"]
    if direction == "engineering_to_true":
        engineering_strain, engineering_stress = strain, stress
        true_strain = [math.log1p(e) for e in strain]
        true_stress = [s * (1 + e) for e, s in zip(strain, stress)]
        x, y, xlabel, ylabel = true_strain, true_stress, "True strain (mm/mm)", "True stress (MPa)"
    elif direction == "true_to_engineering":
        true_strain, true_stress = strain, stress
        engineering_strain = [math.expm1(e) for e in strain]
        engineering_stress = [s / math.exp(e) for e, s in zip(strain, stress)]
        x, y, xlabel, ylabel = engineering_strain, engineering_stress, "Engineering strain (mm/mm)", "Engineering stress (MPa)"
    else:
        raise ValueError("Choose engineering_to_true or true_to_engineering.")
    if any(not math.isfinite(value) for value in x + y):
        raise ValueError("Converted strain or stress exceeds the supported numerical range.")
    return {
        "summary": "Bidirectional engineering/true conversion for uniform pre-necking uniaxial deformation only.",
        "values": {"last_true_strain_mm_mm": true_strain[-1], "last_true_stress_mpa": true_stress[-1],
                   "last_engineering_strain_mm_mm": engineering_strain[-1],
                   "last_engineering_stress_mpa": engineering_stress[-1]},
        "table": {"columns": ["Engineering strain (mm/mm)", "Engineering stress (MPa)",
                              "True strain (mm/mm)", "True stress (MPa)"],
                  "rows": [[a, b, c, d] for a, b, c, d in zip(engineering_strain, engineering_stress,
                                                               true_strain, true_stress)]},
        "plot": {"title": "Converted uniform tensile curve", "xLabel": xlabel,
                 "yLabel": ylabel, "x": x, "y": y},
    }


def _hardening_fit(plastic_strain, true_stress):
    if len(plastic_strain) < 3 or any(v <= 0 for v in plastic_strain) or any(s <= 0 for s in true_stress):
        raise ValueError("A hardening fit needs at least three strictly positive plastic strain/stress pairs.")
    n, log_k, r2 = _linear_fit([math.log(v) for v in plastic_strain], [math.log(s) for s in true_stress])
    if not 0 < n <= 1:
        raise ValueError("The log-log fit must give 0 < n ≤ 1 for strain-hardening behavior.")
    if r2 < 0.90:
        raise ValueError("The selected hardening region is not well described by a power law (R² < 0.90).")
    strength = math.exp(log_k)
    if not math.isfinite(strength) or strength <= 0:
        raise ValueError("Fitted strength coefficient is outside the numeric range.")
    return strength, n, r2


def hollomon_fit(data):
    plastic, stress = _curve(data["true_plastic_curve"], minimum=3, positive_stress=True)
    if plastic[0] <= 0:
        raise ValueError("All true plastic strains must be positive for log-log fitting.")
    k, n, r2 = _hardening_fit(plastic, stress)
    return {
        "summary": "Hollomon fit σ_true = K ε_plastic^n, based on the supplied uniform-flow plastic region.",
        "values": {"strength_coefficient_k_mpa": k, "strain_hardening_exponent_n_dimensionless": n,
                   "log_log_r_squared_dimensionless": r2},
        "table": {"columns": ["True plastic strain (mm/mm)", "True stress observed (MPa)",
                              "Hollomon stress fitted (MPa)"],
                  "rows": [[p, s, k * p ** n] for p, s in zip(plastic, stress)]},
        "plot": {"title": "Hollomon hardening data", "xLabel": "True plastic strain (mm/mm)",
                 "yLabel": "True stress (MPa)", "x": plastic, "y": stress},
    }


def ramberg_osgood_fit(data):
    stress, strain = [], []
    for row in data["total_strain_stress_curve"]:
        if len(row) != 2:
            raise ValueError("Ramberg–Osgood data require two columns: total strain and true stress.")
        strain.append(row[0])
        stress.append(row[1])
    if len(strain) < 3 or any(e <= 0 for e in strain) or any(s <= 0 for s in stress):
        raise ValueError("At least three strictly positive post-yield strain/stress pairs are required.")
    if any(b <= a for a, b in zip(strain, strain[1:])):
        raise ValueError("Total strains must increase strictly.")
    modulus = _positive(data["young_modulus_mpa"], "Young modulus")
    plastic = [e - s / modulus for e, s in zip(strain, stress)]
    if any(p <= 0 for p in plastic):
        raise ValueError("Each fitted point must have positive plastic strain ε_total − σ/E.")
    k, n, r2 = _hardening_fit(plastic, stress)
    return {
        "summary": "Ramberg–Osgood plastic power-law fit ε_total = σ/E + (σ/K)^(1/n).",
        "values": {"strength_coefficient_k_mpa": k, "ramberg_osgood_n_dimensionless": n,
                   "log_log_r_squared_dimensionless": r2},
        "table": {"columns": ["Total strain observed (mm/mm)", "True stress (MPa)",
                              "Total strain fitted (mm/mm)"],
                  "rows": [[e, s, s / modulus + (s / k) ** (1 / n)] for e, s in zip(strain, stress)]},
        "plot": {"title": "Ramberg–Osgood observations", "xLabel": "Total strain (mm/mm)",
                 "yLabel": "True stress (MPa)", "x": strain, "y": stress},
    }


def _fatigue_factor(method, mean_stress, ultimate, yield_strength):
    if method not in {"none", "goodman", "gerber", "soderberg"}:
        raise ValueError("Mean-stress correction must be none, goodman, gerber, or soderberg.")
    tensile_mean = max(0.0, mean_stress)  # Conservative: no life credit for compression.
    if method == "none":
        return 1.0
    strength = ultimate if method in {"goodman", "gerber"} else yield_strength
    _positive(strength, "Mean-stress reference strength")
    ratio = tensile_mean / strength
    if ratio >= 1:
        raise ValueError("Tensile mean stress must be below the chosen mean-stress reference strength.")
    return 1 - (ratio ** 2 if method == "gerber" else ratio)


def _strain_life_reversals(strain_amplitude, sigma_f, modulus, b, epsilon_f, c):
    """Monotone root in logarithmic reversal count, avoiding overflow."""
    def equation(log_reversals):
        # exp terms underflow harmlessly for very long lives.
        return (sigma_f / modulus * math.exp(b * log_reversals)
                + epsilon_f * math.exp(c * log_reversals) - strain_amplitude)

    # Fatigue life models are valid here for N >= 0.5 cycles (>= 1 reversal).
    if equation(0) < 0:
        raise ValueError("Strain amplitude exceeds model capacity at one reversal (N < 0.5 cycles).")
    hi = 1.0
    while equation(hi) > 0 and hi < 700:
        hi = min(2 * hi, 700)
    if equation(hi) > 0:
        raise ValueError("Predicted fatigue life exceeds the supported numeric range.")
    low = 0.0
    for _ in range(90):
        mid = (low + hi) / 2
        if equation(mid) > 0:
            low = mid
        else:
            hi = mid
    return math.exp((low + hi) / 2)


def fatigue_life(data):
    amplitude = _positive(data["stress_amplitude_mpa"], "Alternating stress amplitude")
    strain_amp = _positive(data["total_strain_amplitude_mm_mm"], "Total strain amplitude")
    strength = _positive(data["fatigue_strength_coefficient_mpa"], "Fatigue strength coefficient")
    ductility = _positive(data["fatigue_ductility_coefficient_mm_mm"], "Fatigue ductility coefficient")
    modulus = _positive(data["young_modulus_mpa"], "Young modulus")
    ultimate = _positive(data["ultimate_strength_mpa"], "Ultimate strength")
    yield_strength = _positive(data["yield_strength_mpa"], "Yield strength")
    b, c = data["basquin_exponent_b"], data["coffin_manson_exponent_c"]
    if b >= 0 or c >= 0:
        raise ValueError("Basquin b and Coffin–Manson c must be negative.")
    factor = _fatigue_factor(data["mean_stress_correction"], data["mean_stress_mpa"],
                             ultimate, yield_strength)
    equivalent = amplitude / factor
    log_reversals = math.log(equivalent / strength) / b
    if log_reversals < 0 or log_reversals > 700:
        raise ValueError("Basquin life is outside the supported N ≥ 0.5-cycle numeric range.")
    basquin_cycles = math.exp(log_reversals) / 2
    # Engineering approximation: correct the elastic Basquin coefficient using
    # the same mean-stress reduction; the plastic Coffin–Manson term is unchanged.
    strain_cycles = _strain_life_reversals(strain_amp, factor * strength, modulus, b, ductility, c) / 2
    return {
        "summary": "Stress-life Basquin and combined strain-life Basquin–Coffin–Manson lives in cycles; "
                   "tensile mean-stress correction is an engineering estimate, not a design allowables curve.",
        "values": {"mean_stress_factor_dimensionless": factor,
                   "corrected_alternating_stress_mpa": equivalent,
                   "basquin_fatigue_life_cycles": basquin_cycles,
                   "basquin_coffin_manson_fatigue_life_cycles": strain_cycles},
    }


def _geometry_factor(geometry, a, width):
    if geometry == "infinite_center":
        return 1.0
    if geometry == "semi_infinite_edge":
        return 1.12
    if geometry == "finite_center":
        if not a < width / 2:
            raise ValueError("Finite-center half-crack length must be less than half the plate width.")
        return math.sqrt(1 / math.cos(math.pi * a / width))
    if geometry == "finite_edge":
        ratio = a / width
        if not 0 < ratio <= 0.6:
            raise ValueError("Finite-edge geometry requires 0 < a/width ≤ 0.6.")
        return 1.12 - 0.231 * ratio + 10.55 * ratio ** 2 - 21.72 * ratio ** 3 + 30.39 * ratio ** 4
    raise ValueError("Geometry must be infinite_center, semi_infinite_edge, finite_center, or finite_edge.")


def fracture_stress_intensity(data):
    stress = _positive(data["applied_tensile_stress_mpa"], "Applied tensile stress")
    a = _positive(data["crack_length_a_m"], "Half-center or edge crack length")
    width = _positive(data["plate_width_m"], "Plate width")
    factor = _geometry_factor(data["geometry"], a, width)
    intensity = factor * stress * math.sqrt(math.pi * a)
    return {
        "summary": "Mode-I K = Y σ √(πa); for center cracks, a is HALF the full through-crack length.",
        "values": {"geometry_factor_y_dimensionless": factor,
                   "mode_i_stress_intensity_mpa_sqrt_m": intensity},
    }


def critical_crack_length(data):
    toughness = _positive(data["fracture_toughness_mpa_sqrt_m"], "Fracture toughness")
    stress = _positive(data["applied_tensile_stress_mpa"], "Applied tensile stress")
    factor = _positive(data["constant_geometry_factor_y"], "Constant geometry factor")
    a = (toughness / (factor * stress)) ** 2 / math.pi
    if a <= 0 or not math.isfinite(a):
        raise ValueError("Critical crack length is outside the supported numeric range.")
    return {
        "summary": "Critical half-center or edge crack length from K_IC = Y σ √(πa), assuming Y stays constant.",
        "values": {"critical_crack_length_a_m": a, "critical_crack_length_a_mm": a * 1000,
                   "full_center_crack_length_2a_m": 2 * a},
    }


def paris_crack_growth(data):
    initial = _positive(data["initial_crack_a_m"], "Initial crack length")
    final = _positive(data["final_crack_a_m"], "Final crack length")
    if final <= initial:
        raise ValueError("Final crack length must exceed initial crack length.")
    delta_stress = _positive(data["stress_range_mpa"], "Cyclic stress range")
    y = _positive(data["constant_geometry_factor_y"], "Geometry factor")
    c = _positive(data["paris_c_m_cycle_inv_mpa_sqrt_m_neg_m"], "Paris C coefficient")
    m = _positive(data["paris_exponent_m"], "Paris exponent")
    ratio = data["load_ratio_r"]
    if not 0 <= ratio < 1:
        raise ValueError("Load ratio R = σ_min/σ_max must be in [0, 1).")
    delta_k0 = y * delta_stress * math.sqrt(math.pi * initial)
    delta_kf = y * delta_stress * math.sqrt(math.pi * final)
    toughness = _positive(data["fracture_toughness_mpa_sqrt_m"], "Fracture toughness")
    if delta_kf / (1 - ratio) > toughness * (1 + 1e-12):
        raise ValueError("The final crack would exceed K_IC before the requested end length.")
    # Analytic integral of da/[C (Y Δσ sqrt(πa))^m]. Use expm1
    # to avoid cancellation when m is close to 2.
    p = 1 - m / 2
    # log(final / initial) loses precision for nearly identical cracks;
    # log1p also avoids overflowing the ratio for widely separated sizes.
    difference = final - initial
    log_ratio = (math.log1p(difference / initial) if difference <= initial
                 else math.log(final) - math.log(initial))
    span = (log_ratio if p == 0 else
            initial ** p * math.expm1(p * log_ratio) / p)
    cycles = span / (c * (y * delta_stress * math.sqrt(math.pi)) ** m)
    if not math.isfinite(cycles) or cycles <= 0:
        raise ValueError("Predicted Paris-law cycles are outside the supported numeric range.")
    return {
        "summary": "Constant-Y Paris law integrated between the stated crack sizes; valid only in the Paris regime.",
        "values": {"paris_growth_cycles": cycles, "initial_delta_k_mpa_sqrt_m": delta_k0,
                   "final_delta_k_mpa_sqrt_m": delta_kf,
                   "final_k_max_mpa_sqrt_m": delta_kf / (1 - ratio)},
    }


def thermal_expansion_stress(data):
    alpha = data["linear_expansion_per_k"]
    length = _positive(data["initial_length_mm"], "Original length")
    modulus = _positive(data["young_modulus_mpa"], "Young modulus")
    restraint = data["axial_restraint_fraction"]
    if not 0 <= restraint <= 1:
        raise ValueError("Axial restraint fraction must be in [0, 1].")
    delta = data["temperature_change_k"]
    thermal_strain = alpha * delta
    return {
        "summary": "1D uniform linear thermoelastic expansion; heating under axial restraint produces compressive stress.",
        "values": {"free_length_change_mm": length * thermal_strain,
                   "free_thermal_strain_mm_mm": thermal_strain,
                   "restrained_axial_stress_mpa": -modulus * thermal_strain * restraint,
                   "actual_length_change_mm": length * thermal_strain * (1 - restraint)},
    }


def composite_moduli(data):
    vf = data["fiber_volume_fraction"]
    if not 0 <= vf <= 1:
        raise ValueError("Fiber volume fraction must lie within [0, 1].")
    ef_axial = _positive(data["fiber_axial_modulus_gpa"], "Axial fiber modulus")
    ef_transverse = _positive(data["fiber_transverse_modulus_gpa"], "Transverse fiber modulus")
    em = _positive(data["matrix_modulus_gpa"], "Matrix modulus")
    xi = _positive(data["halpin_tsai_shape_xi"], "Halpin–Tsai shape parameter")
    longitudinal = vf * ef_axial + (1 - vf) * em
    transverse = 1 / (vf / ef_transverse + (1 - vf) / em)
    eta = (ef_transverse / em - 1) / (ef_transverse / em + xi)
    halpin = em * (1 + xi * eta * vf) / (1 - eta * vf)
    return {
        "summary": "Ideal aligned iso-strain and iso-stress bounds with a morphology-dependent Halpin–Tsai estimate.",
        "values": {"longitudinal_rule_mixtures_modulus_gpa": longitudinal,
                   "transverse_inverse_rule_modulus_gpa": transverse,
                   "halpin_tsai_modulus_gpa": halpin,
                   "halpin_tsai_eta_dimensionless": eta},
    }


# Selected knots from ASTM E140-12b (2019 reapproval), Tables 1 and 2, for
# homogeneous NON-AUSTENITIC steels. Each tuple is (HRC, HV, HBW) or
# (HRB, HV, HBS). Untabulated HBW cells are intentionally None. Other
# alloys, indenters, heat treatments and standards require their own tables.
# Values at knots reproduce the cited published numbers; between knots the
# result is a piecewise-linear estimate, NOT a substitute for a test.
_E140_TABLE_1 = (
    (20, 238, 226), (21, 243, 231), (22, 248, 237), (23, 254, 243),
    (24, 260, 247), (25, 266, 253), (26, 272, 258), (27, 279, 264),
    (28, 286, 271), (29, 294, 279), (30, 302, 286), (31, 310, 294),
    (32, 318, 301), (33, 327, 311), (34, 336, 319), (35, 345, 327),
    (36, 354, 336), (37, 363, 344), (38, 372, 353), (39, 382, 362),
    (40, 392, 371), (41, 402, 381), (42, 412, 390), (43, 423, 400),
    (44, 434, 409), (45, 446, 421), (46, 458, 432), (47, 471, 443),
    (48, 484, 455), (49, 498, 469), (50, 513, 481), (51, 528, 496),
    (52, 544, 512), (53, 560, 525), (54, 577, 543), (55, 595, 560),
    (56, 613, 577), (57, 633, 595), (58, 653, 615), (59, 674, 634),
    (60, 697, None), (61, 720, None), (62, 746, None), (63, 772, None),
    (64, 800, None), (65, 832, None), (66, 865, None), (67, 900, None),
    (68, 940, None),
)
_E140_TABLE_2 = (
    (55, 100, 100), (56, 101, 101), (57, 103, 103), (58, 104, 104),
    (59, 106, 106), (60, 107, 107), (61, 108, 108), (62, 110, 110),
    (63, 112, 112), (64, 114, 114), (65, 116, 116), (66, 117, 117),
    (67, 119, 119), (68, 121, 121), (69, 123, 123), (70, 125, 125),
    (71, 127, 127), (72, 130, 130), (73, 132, 132), (74, 135, 135),
    (75, 137, 137), (76, 139, 139), (77, 141, 141), (78, 144, 144),
    (79, 147, 147), (80, 150, 150), (81, 153, 153), (82, 156, 156),
    (83, 159, 159), (84, 162, 162), (85, 165, 165), (86, 169, 169),
    (87, 172, 172), (88, 176, 176), (89, 180, 180), (90, 185, 185),
    (91, 190, 190), (92, 195, 195), (93, 200, 200), (94, 205, 205),
    (95, 210, 210), (96, 216, 216), (97, 222, 222), (98, 228, 228),
    (99, 234, 234), (100, 240, 240),
)


def _interpolate_hardness(source, target, rows, reading, names):
    source_index, target_index = names.index(source), names.index(target)
    points = [(row[source_index], row[target_index]) for row in rows
              if row[source_index] is not None and row[target_index] is not None]
    points.sort()
    if reading < points[0][0] or reading > points[-1][0]:
        raise ValueError(f"Not tabulated in {source} → {target} for this ASTM E140 table; no extrapolation.")
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= reading <= x1:
            return y0 + (y1 - y0) * (reading - x0) / (x1 - x0)
    if reading == points[-1][0]:
        return points[-1][1]
    raise ValueError("No valid interval exists for this hardness conversion.")


def hardness_e140(data):
    table = data["e140_table"]
    source = data["from_scale"].upper()
    target = data["to_scale"].upper()
    if table == "table1_non_austenitic_hrc":
        names, rows = ("HRC", "HV", "HBW"), _E140_TABLE_1
    elif table == "table2_non_austenitic_hrb":
        names, rows = ("HRB", "HV", "HBS"), _E140_TABLE_2
    else:
        raise ValueError("Choose ASTM E140 Table 1 (HRC) or Table 2 (HRB) for non-austenitic steels.")
    if source not in names or target not in names or source == target:
        raise ValueError("Select two distinct, tabulated hardness scales for the chosen table.")
    reading = data["hardness_reading"]
    # E140 §7.3: do not report more significant digits than the integer
    # values in these selected columns. Python's round uses ties-to-even.
    converted = round(_interpolate_hardness(source, target, rows, reading, names))
    return {
        "summary": f"Approximate ASTM E140-12b {source} → {target} for homogeneous non-austenitic steel; "
                   "piecewise-linear interpolation, not an acceptance-test substitute.",
        "values": {f"converted_hardness_{target.lower()}": converted,
                   f"original_hardness_{source.lower()}": reading},
    }


def arrhenius_diffusion_profile(data):
    d0 = _positive(data["diffusion_prefactor_m2_s"], "Diffusion pre-exponential factor")
    activation = data["activation_energy_kj_mol"]
    if activation < 0:
        raise ValueError("Activation energy must be nonnegative.")
    kelvin = _positive(data["temperature_k"], "Absolute temperature")
    seconds = _positive(data["elapsed_time_s"], "Diffusion time")
    depths = data["depth_mm"]
    if not depths or any(x < 0 for x in depths) or any(b <= a for a, b in zip(depths, depths[1:])):
        raise ValueError("Depths must be nonnegative and strictly increasing in millimetres.")
    initial = data["initial_solute_wt_percent"]
    surface = data["surface_solute_wt_percent"]
    if not 0 <= initial <= 100 or not 0 <= surface <= 100:
        raise ValueError("Solute concentrations must be in [0, 100] wt%.")
    # NIST 2018 CODATA molar gas constant (J mol^-1 K^-1).
    coefficient = d0 * math.exp(-(activation * 1000) / (8.31446261815324 * kelvin))
    if coefficient <= 0 or not math.isfinite(coefficient):
        raise ValueError("Diffusivity underflows or overflows at this temperature.")
    penetration = 2 * math.sqrt(coefficient * seconds)
    if penetration <= 0 or not math.isfinite(penetration):
        raise ValueError("Diffusion length is outside the supported numerical range.")
    # erfc avoids cancellation for large x/(2 sqrt(Dt)); C(0,t) = Cs.
    concentration = [initial + (surface - initial) * math.erfc((x * 1e-3) / penetration)
                     for x in depths]
    return {
        "summary": "Arrhenius diffusivity with Fick's second-law erfc profile for a constant-surface, "
                   "semi-infinite solid with uniform initial concentration.",
        "values": {"diffusivity_m2_s": coefficient, "two_sqrt_dt_diffusion_length_mm": 1000 * penetration,
                   "concentration_at_last_depth_wt_percent": concentration[-1]},
        "table": {"columns": ["Depth (mm)", "Solute concentration (wt%)"],
                  "rows": [[x, c] for x, c in zip(depths, concentration)]},
        "plot": {"title": "Fick II semi-infinite concentration profile", "xLabel": "Depth (mm)",
                 "yLabel": "Solute concentration (wt%)", "x": depths, "y": concentration},
    }


OPERATIONS += (
    Operation(
        "stress_strain_analysis", "Analyze a tensile curve", "Fit elastic data; estimate 0.2% proof stress, "
        "ultimate strength, break elongation, toughness and measured elastic resilience.",
        (Field("engineering_curve", "Engineering [strain, stress] rows", "matrix",
               unit="col 1: mm/mm; col 2: MPa"),
         Field("elastic_limit_strain_mm_mm", "End of linear elastic fit/integration", "number", unit="mm/mm")),
        {"engineering_curve": [[0, 0], [0.0005, 100], [0.001, 200], [0.0015, 300],
                               [0.002, 400], [0.003, 430], [0.004, 455], [0.006, 475],
                               [0.01, 490], [0.03, 500], [0.06, 480], [0.1, 440], [0.14, 400]],
         "elastic_limit_strain_mm_mm": 0.002}, stress_strain_analysis,
    ),
    Operation(
        "true_stress_strain", "Convert engineering and true stress–strain",
        "Two-way conversion before necking: σ_true=σ_eng(1+ε_eng) and ε_true=ln(1+ε_eng).",
        (Field("uniform_curve", "Input [strain, stress] rows, as selected by direction", "matrix",
               unit="col 1: mm/mm; col 2: MPa"),
         Field("direction", "engineering_to_true | true_to_engineering", "text", unit="unitless code")),
        {"uniform_curve": [[0, 0], [0.02, 250], [0.1, 320]],
         "direction": "engineering_to_true"}, true_stress_strain,
    ),
    Operation(
        "hollomon_fit", "Fit Hollomon work hardening", "Log-linear least-squares fit of σ_true=K ε_plastic^n.",
        (Field("true_plastic_curve", "[true plastic strain, true stress] rows", "matrix",
               unit="col 1: mm/mm; col 2: MPa"),),
        {"true_plastic_curve": [[0.01, 200], [0.04, 303.143313302], [0.09, 381.01817609]]}, hollomon_fit,
    ),
    Operation(
        "ramberg_osgood_fit", "Fit Ramberg–Osgood plasticity",
        "Fit ε=σ/E+(σ/K)^(1/n) in post-yield uniform flow; subtract σ/E before log fit.",
        (Field("total_strain_stress_curve", "[true total strain, true stress] rows", "matrix",
               unit="col 1: mm/mm; col 2: MPa"),
         Field("young_modulus_mpa", "Measured Young modulus", "number", unit="MPa")),
        {"total_strain_stress_curve": [[0.002, 200], [0.005, 300], [0.01, 360], [0.02, 420]],
         "young_modulus_mpa": 200000}, ramberg_osgood_fit,
    ),
    Operation(
        "fatigue_life", "Estimate Basquin and Coffin–Manson life",
        "Stress-life and strain-life estimates with optional tensile mean-stress correction; return cycles, not reversals.",
        (Field("stress_amplitude_mpa", "Alternating stress amplitude", "number", unit="MPa"),
         Field("total_strain_amplitude_mm_mm", "Total strain amplitude", "number", unit="mm/mm"),
         Field("mean_stress_mpa", "Algebraic mean stress", "number", unit="MPa"),
         Field("ultimate_strength_mpa", "Ultimate tensile strength", "number", unit="MPa"),
         Field("yield_strength_mpa", "Tensile yield strength", "number", unit="MPa"),
         Field("young_modulus_mpa", "Young modulus", "number", unit="MPa"),
         Field("fatigue_strength_coefficient_mpa", "Basquin fatigue strength σ'f", "number", unit="MPa"),
         Field("basquin_exponent_b", "Basquin exponent b (negative)", "number", unit="1"),
         Field("fatigue_ductility_coefficient_mm_mm", "Coffin–Manson ductility ε'f", "number", unit="mm/mm"),
         Field("coffin_manson_exponent_c", "Coffin–Manson exponent c (negative)", "number", unit="1"),
         Field("mean_stress_correction", "none | goodman | gerber | soderberg", "text", unit="unitless code")),
        {"stress_amplitude_mpa": 400, "total_strain_amplitude_mm_mm": 0.004,
         "mean_stress_mpa": 50, "ultimate_strength_mpa": 1000, "yield_strength_mpa": 700,
         "young_modulus_mpa": 200000, "fatigue_strength_coefficient_mpa": 1000, "basquin_exponent_b": -0.1,
         "fatigue_ductility_coefficient_mm_mm": 0.5, "coffin_manson_exponent_c": -0.5,
         "mean_stress_correction": "goodman"}, fatigue_life,
    ),
    Operation(
        "fracture_stress_intensity", "Compute mode-I stress intensity",
        "Infinite plate center/edge, finite-width center secant or finite-edge geometry factor.",
        (Field("applied_tensile_stress_mpa", "Nominal tensile stress", "number", unit="MPa"),
         Field("crack_length_a_m", "Center half-crack or edge crack length a", "number", unit="m"),
         Field("plate_width_m", "Plate full width (unused for infinite geometry)", "number", unit="m"),
         Field("geometry", "infinite_center | semi_infinite_edge | finite_center | finite_edge", "text",
               unit="unitless code")),
        {"applied_tensile_stress_mpa": 200, "crack_length_a_m": 0.005,
         "plate_width_m": 0.1, "geometry": "finite_edge"}, fracture_stress_intensity,
    ),
    Operation(
        "fracture_critical_length", "Compute critical crack length from toughness",
        "Invert K_IC=Y σ√(πa) with constant user-set Y. Center crack full length is 2a.",
        (Field("fracture_toughness_mpa_sqrt_m", "Plane-strain fracture toughness K_IC", "number",
               unit="MPa√m"),
         Field("applied_tensile_stress_mpa", "Applied tensile stress", "number", unit="MPa"),
         Field("constant_geometry_factor_y", "Constant geometry factor Y", "number", unit="1")),
        {"fracture_toughness_mpa_sqrt_m": 40, "applied_tensile_stress_mpa": 300,
         "constant_geometry_factor_y": 1}, critical_crack_length,
    ),
    Operation(
        "paris_crack_growth", "Integrate Paris-law fatigue crack growth",
        "Integrate da/dN=C(ΔK)^m between measured cracks for constant Y and 0≤R<1.",
        (Field("initial_crack_a_m", "Initial crack length a0", "number", unit="m"),
         Field("final_crack_a_m", "Final crack length af", "number", unit="m"),
         Field("stress_range_mpa", "Cyclic nominal stress range Δσ", "number", unit="MPa"),
         Field("constant_geometry_factor_y", "Constant geometry factor Y", "number", unit="1"),
         Field("paris_c_m_cycle_inv_mpa_sqrt_m_neg_m", "Paris C in da/dN=C (ΔK)^m", "number",
               unit="m/cycle/(MPa√m)^m"),
         Field("paris_exponent_m", "Paris exponent m", "number", unit="1"),
         Field("load_ratio_r", "Stress ratio R=σmin/σmax", "number", unit="1"),
         Field("fracture_toughness_mpa_sqrt_m", "Fracture toughness K_IC (endpoint limit)", "number",
               unit="MPa√m")),
        {"initial_crack_a_m": 0.001, "final_crack_a_m": 0.005,
         "stress_range_mpa": 100, "constant_geometry_factor_y": 1,
         "paris_c_m_cycle_inv_mpa_sqrt_m_neg_m": 1e-12, "paris_exponent_m": 3,
         "load_ratio_r": 0.1, "fracture_toughness_mpa_sqrt_m": 50}, paris_crack_growth,
    ),
    Operation(
        "thermal_expansion_stress", "Thermal expansion and restrained stress",
        "One-dimensional isotropic, uniform-temperature thermoelastic calculation; restraint=0 free, 1 fixed.",
        (Field("linear_expansion_per_k", "Linear thermal expansion coefficient α", "number", unit="K⁻¹"),
         Field("initial_length_mm", "Initial specimen length", "number", unit="mm"),
         Field("temperature_change_k", "Temperature change (final − initial)", "number", unit="K", default_unit="delta_K"),
         Field("young_modulus_mpa", "Young modulus", "number", unit="MPa"),
         Field("axial_restraint_fraction", "Axial restraint fraction", "number", unit="1")),
        {"linear_expansion_per_k": 12e-6, "initial_length_mm": 1000,
         "temperature_change_k": 50, "young_modulus_mpa": 200000,
         "axial_restraint_fraction": 1}, thermal_expansion_stress,
    ),
    Operation(
        "composite_moduli", "Estimate aligned composite moduli",
        "Longitudinal rule, inverse transverse rule and Halpin–Tsai from separate axial/transverse fiber moduli.",
        (Field("fiber_volume_fraction", "Fiber volume fraction", "number", unit="1"),
         Field("fiber_axial_modulus_gpa", "Fiber axial Young modulus", "number", unit="GPa"),
         Field("fiber_transverse_modulus_gpa", "Fiber transverse Young modulus", "number", unit="GPa"),
         Field("matrix_modulus_gpa", "Matrix Young modulus", "number", unit="GPa"),
         Field("halpin_tsai_shape_xi", "Halpin–Tsai reinforcement shape ξ", "number", unit="1")),
        {"fiber_volume_fraction": 0.6, "fiber_axial_modulus_gpa": 230, "fiber_transverse_modulus_gpa": 15,
         "matrix_modulus_gpa": 3.5, "halpin_tsai_shape_xi": 2}, composite_moduli,
    ),
    Operation(
        "hardness_e140", "Convert ASTM E140 steel hardness",
        "Approximate HV, Brinell (HBW or HBS), and HRC/HRB conversion for homogeneous non-austenitic steels.",
        (Field("e140_table", "table1_non_austenitic_hrc | table2_non_austenitic_hrb", "text",
               unit="unitless code"),
         Field("from_scale", "Measured hardness scale HV | HBW | HBS | HRC | HRB", "text", unit="unitless code"),
         Field("to_scale", "Target hardness scale HV | HBW | HBS | HRC | HRB", "text", unit="unitless code"),
         Field("hardness_reading", "Measured hardness reading (selected scale)", "number",
               unit="HV, HBW, HBS, HRC or HRB (as selected)")),
        {"e140_table": "table1_non_austenitic_hrc", "from_scale": "HRC", "to_scale": "HV",
         "hardness_reading": 38}, hardness_e140,
        reference_datasets=_hardness_reference_datasets,
    ),
    Operation(
        "arrhenius_diffusion_profile", "Compute Arrhenius diffusion and Fick II profile",
        "D=D0 exp(−Q/RT); semi-infinite constant-surface concentration profile via erfc.",
        (Field("diffusion_prefactor_m2_s", "Diffusivity pre-exponential factor D0", "number", unit="m²/s"),
         Field("activation_energy_kj_mol", "Diffusion activation energy Q", "number", unit="kJ/mol"),
         Field("temperature_k", "Absolute treatment temperature", "number", unit="K"),
         Field("elapsed_time_s", "Treatment time", "number", unit="s"),
         Field("depth_mm", "Depth from surface (increasing)", "vector", unit="mm"),
         Field("initial_solute_wt_percent", "Initial uniform solute concentration", "number", unit="wt%"),
         Field("surface_solute_wt_percent", "Constant surface solute concentration", "number", unit="wt%")),
        {"diffusion_prefactor_m2_s": 3.35e-5, "activation_energy_kj_mol": 148,
         "temperature_k": 1223.15, "elapsed_time_s": 25400,
         "depth_mm": [0, 0.25, 0.5, 0.75, 1],
         "initial_solute_wt_percent": 0.25, "surface_solute_wt_percent": 1.2}, arrhenius_diffusion_profile,
    ),
)
