"""Quantitative interpretation of optical and isotope measurements."""

import math
from fractions import Fraction

from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.reference_data import Dataset

REFERENCE_DATASETS = (
    Dataset("chemistry.ciaaw_abridged_atomic_weights", "2024.0.0", "chemistry",
            ("_ATOMIC_WEIGHTS",), "https://ciaaw.org/abridged-atomic-weights.htm"),
)


def beer_lambert(data):
    absorbance = data["absorbance"]
    coefficient = data["molar_absorptivity"]
    path = data["path_length_cm"]
    if absorbance < 0 or coefficient <= 0 or path <= 0:
        raise ValueError("Enter nonnegative absorbance and positive absorptivity and path length.")
    concentration = float(Fraction(absorbance) / (Fraction(coefficient) * Fraction(path)))
    return {
        "summary": f"The absorbing species has a concentration of {concentration:.6g} mol/L.",
        "values": {"concentration_mol_l": concentration},
    }


def transmission_spectrum(data):
    wavelengths = data["wavelength_nm"]
    transmission = data["transmission_percent"]
    if not wavelengths or len(wavelengths) != len(transmission):
        raise ValueError("Provide one percent transmission for each wavelength.")
    if any(w <= 0 for w in wavelengths) or any(a >= b for a, b in zip(wavelengths, wavelengths[1:])):
        raise ValueError("Wavelengths must be positive and strictly increasing.")
    if any(t <= 0 or t > 100 for t in transmission):
        raise ValueError("Percent transmission must be greater than zero and at most 100.")
    # Near the blank, subtracting two nearly equal logarithms erases the absorption.
    # At low transmission, taking the logs separately avoids underflow of T/100.
    absorbances = [-math.log1p((t - 100) / 100) / math.log(10) if t >= 50 else 2 - math.log10(t) for t in transmission]
    peak = max(absorbances)
    peak_nm = wavelengths[transmission.index(min(transmission))]
    return {
        "summary": f"Maximum measured absorbance {peak:.6g} at {peak_nm:g} nm (first peak if tied).",
        "values": {"peak_absorbance": peak, "peak_wavelength_nm": peak_nm},
        "table": {
            "columns": ["Wavelength (nm)", "Transmission (%)", "Absorbance"],
            "rows": [list(row) for row in zip(wavelengths, transmission, absorbances)],
        },
        "plot": {
            "title": "Absorbance spectrum",
            "xLabel": "Wavelength (nm)",
            "yLabel": "Absorbance",
            "x": wavelengths,
            "y": absorbances,
        },
    }


def isotope_average(data):
    masses = data["isotope_mass_u"]
    abundance = data["abundance_percent"]
    if not masses or len(masses) != len(abundance):
        raise ValueError("Enter one abundance for each isotope mass.")
    if any(mass <= 0 for mass in masses) or any(p < 0 or p > 100 for p in abundance):
        raise ValueError("Masses must be positive and abundances must be between 0 and 100 percent.")
    total = math.fsum(abundance)
    if not math.isclose(total, 100, rel_tol=0, abs_tol=0.01 + 1e-12):
        raise ValueError("Isotopic abundances must total 100% within 0.01 percentage point.")
    weights = [Fraction(percent) for percent in abundance]
    average = float(sum(Fraction(mass) * weight for mass, weight in zip(masses, weights)) / sum(weights))
    return {
        "summary": f"Mean mass per atom in this sample is {average:.8g} u.",
        "values": {"mean_atomic_mass_u": average, "abundance_total_percent": total},
    }


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "beer_lambert",
        "Beer–Lambert concentration",
        "Infer concentration from blank-corrected decadic absorbance for one absorbing species "
        "in the linear range at a fixed wavelength.",
        (
            Field("absorbance", "Absorbance", "number"),
            Field("molar_absorptivity", "Molar absorption coefficient", "number", unit="L/(mol·cm)"),
            Field("path_length_cm", "Optical path length", "number", unit="cm"),
        ),
        {"absorbance": 0.36, "molar_absorptivity": 12000, "path_length_cm": 1},
        beer_lambert,
    ),
    Operation(
        "transmission_spectrum",
        "Convert transmission to absorbance",
        "Convert a blank-referenced transmission spectrum to decadic absorbance, A = −log₁₀(T/100).",
        (
            Field("wavelength_nm", "Wavelengths, increasing", "vector", unit="nm"),
            Field("transmission_percent", "Transmission at each wavelength", "vector", unit="%"),
        ),
        {"wavelength_nm": [400, 450, 500, 550], "transmission_percent": [80, 50, 10, 70]},
        transmission_spectrum,
    ),
    Operation(
        "isotope_average",
        "Isotope-weighted atomic mass",
        "Calculate the number-abundance-weighted mean isotope mass. Rounded percent abundances "
        "within 0.01 percentage point of 100% are normalized to their total.",
        (
            Field("isotope_mass_u", "Measured isotope masses", "vector", unit="u"),
            Field("abundance_percent", "Number abundances", "vector", unit="%"),
        ),
        {"isotope_mass_u": [34.96885, 36.96590], "abundance_percent": [75.76, 24.24]},
        isotope_average,
    ),
)


# All new acid/base calculations assume ideal concentration activities at 25 °C.
# These are conditional pKa values; temperature/ionic-strength corrections are
# deliberately not inferred from a pKa without additional measurements.
_PKW = 14.0
_KW = 1e-14  # (mol/L)^2 at 25 °C
_LN10 = math.log(10)
_R_J_MOL_K = 8.31446261815324  # exact SI-defined molar gas constant
_R_L_BAR_MOL_K = _R_J_MOL_K / 100  # 1 L·bar = 100 J


def _number(value, label, *, positive=False, nonnegative=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative'}.")
    return value


def _pkas(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 8:
        raise ValueError("Supply 1–8 successive conjugate-acid pKa values.")
    for value in values:
        _number(value, "pKa")
        if not -20 <= value <= 35:
            raise ValueError("pKa must lie between −20 and 35 for this aqueous model.")
    if any(a >= b for a, b in zip(values, values[1:])):
        raise ValueError("Successive pKa values must be strictly increasing.")
    return values


def _family(value, pkas):
    if value not in ("acid", "base"):
        raise ValueError("Family must be 'acid' or 'base' (pKa of the conjugate acid).")
    # Acid: neutral fully protonated acid, charges 0, -1, -2, ...
    # Base: neutral fully deprotonated base, charges n, n-1, ..., 0.
    return 0 if value == "acid" else len(pkas)


def _fractions(ph, pkas):
    # Normalize logarithmic weights instead of multiplying Ka/H terms, which
    # can overflow across the many decades spanned by polyprotic equilibria.
    log_weights = [0.0]
    for pka in pkas:
        log_weights.append(log_weights[-1] + _LN10 * (ph - pka))
    largest = max(log_weights)
    weights = [math.exp(weight - largest) for weight in log_weights]
    total = math.fsum(weights)
    return [weight / total for weight in weights]


def _charge_ph(pkas, formal_mol_l, first_charge, spectator_mol_l=0):
    """Solve [H+] - [OH-] + counterions + sum(z_j [species_j]) = 0."""
    _number(formal_mol_l, "Analytical concentration (mol/L)", positive=True)
    _number(spectator_mol_l, "Net spectator-ion charge (mol/L)")
    if formal_mol_l > 1000 or abs(spectator_mol_l) > 1000:
        raise ValueError("Concentrations above 1000 mol/L are outside this aqueous model.")

    def residual(ph):
        h = 10.0 ** (-ph)
        oh = _KW / h
        charge = math.fsum((first_charge - i) * fraction for i, fraction in enumerate(_fractions(ph, pkas)))
        return math.fsum((h, -oh, spectator_mol_l, formal_mol_l * charge))

    low, high = -30.0, 50.0
    if residual(low) < 0 or residual(high) > 0:
        raise ValueError("Charge balance has no solution within the aqueous model range.")
    for _ in range(180):
        middle = (low + high) / 2
        if residual(middle) > 0:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def acid_base_speciation(data):
    pkas = _pkas(data["pka_values"])
    charge = _family(data["family"], pkas)
    total = data["formal_concentration_mol_l"]
    spectator = data["spectator_charge_mol_l"]
    ph = _charge_ph(pkas, total, charge, spectator)
    fractions = _fractions(ph, pkas)
    species = [total * fraction for fraction in fractions]
    dominant = max(range(len(species)), key=lambda i: species[i])
    return {
        "summary": f"Ideal aqueous {data['family']} system at 25 °C: pH {ph:.4f}, pOH {_PKW - ph:.4f}; "
                   f"dominant form has lost {dominant} proton(s).",
        "values": {
            "ph": ph, "poh": _PKW - ph, "hydrogen_mol_l": 10 ** (-ph),
            "hydroxide_mol_l": _KW / (10 ** (-ph)),
            "dominant_form_protons_lost": dominant,
            **{f"fraction_{i}": fraction for i, fraction in enumerate(fractions)},
            **{f"species_{i}_mol_l": concentration for i, concentration in enumerate(species)},
        },
        "table": {
            "columns": ["Protons lost (count)", "Ion charge (e)", "Mole fraction (1)", "Concentration (mol/L)"],
            "rows": [[i, charge - i, fractions[i], species[i]] for i in range(len(species))],
        },
        "plot": {
            "title": "Acid–base species at equilibrium (25 °C)",
            "xLabel": "Protons lost (count)", "yLabel": "Species fraction (1)",
            "x": list(range(len(species))), "y": fractions,
        },
    }


def buffer_ph_capacity(data):
    pka = _pkas([data["pka"]])[0]
    acid = _number(data["acid_concentration_mol_l"], "Undissociated acid (mol/L)", positive=True)
    base = _number(data["base_concentration_mol_l"], "Conjugate-base salt (mol/L)", positive=True)
    total = math.fsum((acid, base))
    if total > 1000:
        raise ValueError("Total buffer concentration must not exceed 1000 mol/L.")
    hh = pka + math.log10(base) - math.log10(acid)
    # The salt supplies one spectator cation per mole of A−. Water autoprotolysis
    # and acid dissociation are both retained in the exact electroneutrality root.
    exact = _charge_ph([pka], total, 0, base)
    fraction = _fractions(exact, [pka])[1]
    hydrogen = 10 ** (-exact)
    hydroxide = _KW / hydrogen
    approx_capacity = _LN10 * total * (acid / total) * (base / total)
    exact_capacity = _LN10 * (total * fraction * (1 - fraction) + hydrogen + hydroxide)
    return {
        "summary": f"Henderson–Hasselbalch pH {hh:.4f}; exact charge-balance pH {exact:.4f} "
                   "(ideal solution, 25 °C). Capacities are per pH unit.",
        "values": {
            "ph_henderson_hasselbalch": hh, "ph_charge_balance": exact,
            "ph_exact_minus_hh": exact - hh, "poh_charge_balance": _PKW - exact,
            "buffer_capacity_hh_mol_l_per_ph": approx_capacity,
            "buffer_capacity_exact_mol_l_per_ph": exact_capacity,
            "hydrogen_mol_l": hydrogen, "hydroxide_mol_l": hydroxide,
            "equilibrium_base_fraction": fraction,
        },
        "table": {
            "columns": ["Method", "pH (1)", "Buffer capacity (mol/(L·pH))"],
            "rows": [["Henderson–Hasselbalch", hh, approx_capacity],
                     ["Exact charge balance", exact, exact_capacity]],
        },
    }


def titration_curve(data):
    pkas = _pkas(data["pka_values"])
    charge = _family(data["family"], pkas)
    analyte = _number(data["analyte_concentration_mol_l"], "Analyte concentration (mol/L)", positive=True)
    volume = _number(data["analyte_volume_ml"], "Analyte volume (mL)", positive=True)
    titrant = _number(data["titrant_concentration_mol_l"], "Titrant concentration (mol/L)", positive=True)
    maximum = _number(data["max_titrant_volume_ml"], "Maximum titrant volume (mL)", positive=True)
    samples = data["samples"]
    if type(samples) is not int or not 3 <= samples <= 512:
        raise ValueError("Choose 3–512 curve samples.")
    if analyte > 1000 or titrant > 1000:
        raise ValueError("Concentrations above 1000 mol/L are outside this aqueous model.")
    first_eq = analyte * volume / titrant
    eq_volumes = [first_eq * (i + 1) for i in range(len(pkas))]
    if not math.isfinite(eq_volumes[-1]) or maximum < eq_volumes[-1]:
        raise ValueError("Maximum titrant volume must include every equivalence point.")
    # This displays all true stoichiometric equivalence points even if none
    # coincides with a uniformly sampled curve position.
    volumes = sorted(set([maximum * i / (samples - 1) for i in range(samples)] + eq_volumes))

    def ph_at(v):
        mixed_volume = volume + v
        concentration = analyte * volume / mixed_volume
        salt = (1 if charge == 0 else -1) * titrant * v / mixed_volume
        return _charge_ph(pkas, concentration, charge, salt)

    phs = [ph_at(v) for v in volumes]
    values = {"initial_ph": ph_at(0), "final_ph": ph_at(maximum),
              "equivalence_count": len(pkas)}
    for i, eq in enumerate(eq_volumes, 1):
        values[f"equivalence_{i}_volume_ml"] = eq
        values[f"equivalence_{i}_ph"] = ph_at(eq)
        previous = eq_volumes[i - 2] if i > 1 else 0.0
        values[f"half_equivalence_{i}_ph"] = ph_at((previous + eq) / 2)
    return {
        "summary": f"{len(pkas)} stoichiometric equivalence point(s) for strong "
                   f"{'base' if charge == 0 else 'acid'} titrant (25 °C, ideal activities).",
        "values": values,
        "table": {"columns": ["Titrant added (mL)", "Calculated pH (1)"],
                  "rows": [[v, ph] for v, ph in zip(volumes, phs)]},
        "plot": {"title": "Charge-balance titration curve", "xLabel": "Titrant added (mL)",
                 "yLabel": "pH (1)", "x": volumes, "y": phs},
    }


def _linear_fit(xs, ys):
    if len(xs) != len(ys) or len(xs) < 3:
        raise ValueError("Fit requires at least three paired measurements for uncertainty.")
    n = len(xs)
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    centered_x = [x - mx for x in xs]
    centered_y = [y - my for y in ys]
    xx = math.fsum(v * v for v in centered_x)
    if xx <= 0 or not math.isfinite(xx):
        raise ValueError("The independent measurements must vary enough for a fit.")
    slope = math.fsum(x * y for x, y in zip(centered_x, centered_y)) / xx
    intercept = my - slope * mx
    residuals = [y - slope * x for x, y in zip(centered_x, centered_y)]
    sse = math.fsum(v * v for v in residuals)
    yy = math.fsum(v * v for v in centered_y)
    stderr = math.sqrt((sse / (n - 2)) / xx)
    r2 = max(0.0, min(1.0, 1 - sse / yy)) if yy else (1.0 if sse == 0 else 0.0)
    if not all(math.isfinite(v) for v in (slope, intercept, sse, stderr, r2)):
        raise ValueError("Fit parameters exceed the numerical range.")
    return slope, intercept, stderr, r2


def arrhenius_fit(data):
    temperatures = data["temperature_k"]
    rates = data["rate_constant"]
    unit = data["rate_constant_unit"].strip() if isinstance(data["rate_constant_unit"], str) else ""
    if not unit or len(unit) > 48:
        raise ValueError("Specify the shared rate-constant unit in 1–48 characters.")
    if len(temperatures) != len(rates) or not 3 <= len(rates) <= 800:
        raise ValueError("Supply 3–800 matching temperatures and rate constants (table size limit).")
    for t, k in zip(temperatures, rates):
        _number(t, "Absolute temperature (K)", positive=True)
        _number(k, "Rate constant", positive=True)
    xs = [1000 / t for t in temperatures]  # K^−1 × 1000
    ys = [math.log(k) for k in rates]      # log(k / 1 declared unit)
    slope, intercept, se, r2 = _linear_fit(xs, ys)
    if intercept > math.log(1e308) or intercept < math.log(1e-308):
        raise ValueError("Fitted Arrhenius prefactor is outside the numerical range.")
    prefactor = math.exp(intercept)
    ea = -_R_J_MOL_K * slope  # kJ/mol because slope is against 1000/T
    return {
        "summary": f"Arrhenius fit: Ea {ea:.6g} ± {_R_J_MOL_K * se:.3g} kJ/mol "
                   f"(1σ regression standard error, n={len(xs)}).",
        "values": {
            "activation_energy_kj_mol": ea,
            "activation_energy_standard_error_kj_mol": _R_J_MOL_K * se,
            "ln_prefactor": intercept, "prefactor": prefactor,
            "prefactor_unit": unit, "r_squared": r2, "residual_degrees_of_freedom": len(xs) - 2,
        },
        "table": {"columns": ["Temperature (K)", f"Rate constant ({unit})", "1000/T (1/K × 1000)",
                              "ln(k / 1 unit) (1)", "Fitted ln(k / 1 unit) (1)"],
                  "rows": [[t, k, x, y, intercept + slope * x] for t, k, x, y in zip(temperatures, rates, xs, ys)]},
        "plot": {"title": "Arrhenius regression", "xLabel": "1000/T (1/K × 1000)",
                 "yLabel": "ln(k / 1 declared unit) (1)", "x": xs, "y": ys},
    }


def rate_law_fit(data):
    order = data["order"]
    if order not in ("first", "second"):
        raise ValueError("Rate law order must be 'first' or 'second'.")
    times, concentrations = data["time_s"], data["concentration_mol_l"]
    if len(times) != len(concentrations) or len(times) < 3:
        raise ValueError("Supply at least three paired times and reactant concentrations.")
    for t, c in zip(times, concentrations):
        _number(t, "Elapsed time (s)", nonnegative=True)
        _number(c, "Reactant concentration (mol/L)", positive=True)
    if any(t2 <= t1 for t1, t2 in zip(times, times[1:])):
        raise ValueError("Measurement times must be strictly increasing.")
    responses = [math.log(c) if order == "first" else 1 / c for c in concentrations]
    slope, intercept, se, r2 = _linear_fit(times, responses)
    k = -slope if order == "first" else slope
    if k <= 0:
        raise ValueError("The fitted rate constant must be positive (reactant consumption).")
    if order == "second" and intercept <= 0:
        raise ValueError("The fitted reciprocal initial concentration must be positive.")
    initial = math.exp(intercept) if order == "first" else 1 / intercept
    half_life = math.log(2) / k if order == "first" else 1 / (k * initial)
    if order == "first":
        modeled = [math.exp(intercept + slope * t) for t in times]
        units = "s⁻¹"
        key, se_key = "rate_constant_per_s", "rate_constant_standard_error_per_s"
    else:
        modeled = [1 / (intercept + slope * t) for t in times]
        units = "L/(mol·s)"
        key, se_key = "rate_constant_l_mol_s", "rate_constant_standard_error_l_mol_s"
    if any(not math.isfinite(v) or v <= 0 for v in modeled + [initial, half_life]):
        raise ValueError("Fitted concentrations or half-life exceed the numerical range.")
    return {
        "summary": f"{order.title()}-order integrated rate-law fit: k={k:.6g} {units}; "
                   f"half-life={half_life:.6g} s (from fitted initial concentration).",
        "values": {key: k, se_key: se, "initial_concentration_mol_l": initial,
                   "half_life_s": half_life, "r_squared_transformed": r2,
                   "residual_degrees_of_freedom": len(times) - 2},
        "table": {"columns": ["Time (s)", "Measured [A] (mol/L)",
                              "Fitted [A] (mol/L)", "Residual [A] (mol/L)"],
                  "rows": [[t, c, fit, c - fit] for t, c, fit in zip(times, concentrations, modeled)]},
        "plot": {"title": f"{order.title()}-order concentration fit", "xLabel": "Time (s)",
                 "yLabel": "Reactant concentration (mol/L)", "x": times, "y": modeled},
    }


def _gas_parameters(data):
    n = _number(data["amount_mol"], "Amount (mol)", positive=True)
    t = _number(data["temperature_k"], "Temperature (K)", positive=True)
    a = _number(data["a_l2_bar_mol2"], "van der Waals a (L²·bar/mol²)", nonnegative=True)
    b = _number(data["b_l_mol"], "van der Waals b (L/mol)", nonnegative=True)
    return n, t, a, b


def gas_pressure(data):
    n, t, a, b = _gas_parameters(data)
    v = _number(data["volume_l"], "Volume (L)", positive=True)
    if v <= n * b:
        raise ValueError("Gas volume must exceed n·b (excluded molar volume).")
    ideal = n * _R_L_BAR_MOL_K * t / v
    vdw = n * _R_L_BAR_MOL_K * t / (v - n * b) - a * (n / v) ** 2
    if not math.isfinite(vdw) or vdw <= 0:
        raise ValueError("van der Waals pressure is nonpositive or outside the numerical range.")
    return {
        "summary": f"At {t:g} K, ideal pressure {ideal:.6g} bar; van der Waals pressure {vdw:.6g} bar.",
        "values": {"ideal_pressure_bar": ideal, "van_der_waals_pressure_bar": vdw,
                   "compressibility_factor_vdw": vdw / ideal,
                   "vdw_minus_ideal_bar": vdw - ideal},
    }


def gas_volume(data):
    n, t, a, b = _gas_parameters(data)
    p = _number(data["pressure_bar"], "Target pressure (bar)", positive=True)
    rt = _R_L_BAR_MOL_K * t
    ideal_molar = rt / p
    if a == 0:
        vdw_molar = ideal_molar + b
    else:
        # Search from the dilute-gas region inward, taking the LARGEST
        # admissible root when the subcritical vdW isotherm is multivalued.
        def difference(molar_volume):
            return rt / (molar_volume - b) - a / (molar_volume * molar_volume) - p

        upper = max(ideal_molar + b, 2 * b, 1e-12) * 2
        while difference(upper) >= 0:
            upper *= 2
            if not math.isfinite(upper):
                raise ValueError("Cannot bracket a finite gas-phase volume.")
        high_log = math.log(upper - b)
        low_log = math.log(max(math.ulp(b), (upper - b) * 1e-14))
        prev = upper
        root_pair = None
        for i in range(1, 2049):
            candidate = b + math.exp(high_log + (low_log - high_log) * i / 2048)
            if candidate <= b:
                break
            if difference(candidate) >= 0:
                root_pair = (candidate, prev)
                break
            prev = candidate
        if root_pair is None:
            raise ValueError("No resolvable positive gas-phase van der Waals root at this pressure.")
        lo, hi = root_pair
        for _ in range(120):
            mid = (lo + hi) / 2
            if difference(mid) > 0:
                lo = mid
            else:
                hi = mid
        vdw_molar = (lo + hi) / 2
    ideal = n * ideal_molar
    vdw = n * vdw_molar
    if not all(math.isfinite(x) and x > 0 for x in (ideal, vdw)):
        raise ValueError("Calculated gas volumes exceed the numerical range.")
    return {
        "summary": f"At {p:g} bar, ideal gas volume {ideal:.6g} L; largest van der Waals root {vdw:.6g} L.",
        "values": {"ideal_volume_l": ideal, "van_der_waals_gas_volume_l": vdw,
                   "compressibility_factor_vdw": p * vdw / (n * rt),
                   "vdw_minus_ideal_l": vdw - ideal},
    }


# CIAAW 2024 *abridged standard atomic weights* (representative conventional
# values, not isotope-resolved mass; elements lacking a standard weight omitted).
# https://ciaaw.org/abridged-atomic-weights.htm
_ATOMIC_WEIGHTS = {
    row.split()[0]: float(row.split()[1]) for row in """
H 1.0080
He 4.0026
Li 6.94
Be 9.0122
B 10.81
C 12.011
N 14.007
O 15.999
F 18.998
Ne 20.180
Na 22.990
Mg 24.305
Al 26.982
Si 28.085
P 30.974
S 32.06
Cl 35.45
Ar 39.95
K 39.098
Ca 40.078
Sc 44.956
Ti 47.867
V 50.942
Cr 51.996
Mn 54.938
Fe 55.845
Co 58.933
Ni 58.693
Cu 63.546
Zn 65.38
Ga 69.723
Ge 72.630
As 74.922
Se 78.971
Br 79.904
Kr 83.798
Rb 85.468
Sr 87.62
Y 88.906
Zr 91.222
Nb 92.906
Mo 95.95
Ru 101.07
Rh 102.91
Pd 106.42
Ag 107.87
Cd 112.41
In 114.82
Sn 118.71
Sb 121.76
Te 127.60
I 126.90
Xe 131.29
Cs 132.91
Ba 137.33
La 138.91
Ce 140.12
Pr 140.91
Nd 144.24
Sm 150.36
Eu 151.96
Gd 157.25
Tb 158.93
Dy 162.50
Ho 164.93
Er 167.26
Tm 168.93
Yb 173.05
Lu 174.97
Hf 178.49
Ta 180.95
W 183.84
Re 186.21
Os 190.23
Ir 192.22
Pt 195.08
Au 196.97
Hg 200.59
Tl 204.38
Pb 207.2
Bi 208.98
Th 232.04
Pa 231.04
U 238.03
""".splitlines() if row.strip()
}
_ATOMIC_ORDER = {symbol: i for i, symbol in enumerate(_ATOMIC_WEIGHTS)}


def _formula_counts(formula):
    if not isinstance(formula, str) or not formula.strip() or len(formula) > 512:
        raise ValueError("Supply a chemical formula of at most 512 characters.")
    formula = formula.replace("·", ".").replace("∙", ".").replace("⋅", ".").replace(" ", "")
    parts = formula.split(".")
    if any(not part for part in parts):
        raise ValueError("Hydrate components must be separated by a single dot (· or .).")

    def integer(part, i):
        start = i
        while i < len(part) and part[i].isdigit():
            i += 1
        if i == start:
            return 1, i
        value = int(part[start:i])
        if not 1 <= value <= 1_000_000:
            raise ValueError("Formula subscripts and hydrate coefficients must be 1–1,000,000.")
        return value, i

    def parse_group(part, i, close=""):
        counts = {}
        while i < len(part) and (not close or part[i] != close):
            ch = part[i]
            if ch in "([":
                inner, i = parse_group(part, i + 1, ")" if ch == "(" else "]")
                if i >= len(part) or part[i] != (")" if ch == "(" else "]"):
                    raise ValueError("Unclosed group in chemical formula.")
                i += 1
            elif ch.isascii() and ch.isupper():
                symbol = ch
                i += 1
                if i < len(part) and part[i].isascii() and part[i].islower():
                    symbol += part[i]
                    i += 1
                if symbol not in _ATOMIC_WEIGHTS:
                    raise ValueError(f"No CIAAW standard atomic weight for element '{symbol}'.")
                inner = {symbol: 1}
            else:
                raise ValueError(f"Unexpected character '{ch}' in chemical formula.")
            multiplier, i = integer(part, i)
            for symbol, count in inner.items():
                counts[symbol] = counts.get(symbol, 0) + count * multiplier
                if counts[symbol] > 2**53 - 1:
                    raise ValueError("Element count exceeds exact integer range.")
        if not counts:
            raise ValueError("Empty groups are not valid chemical formulas.")
        return counts, i

    total = {}
    for component in parts:
        coefficient, i = integer(component, 0)
        if i == len(component):
            raise ValueError("Hydrate coefficient must precede a formula.")
        counts, last = parse_group(component, i)
        if last != len(component):
            raise ValueError("Unmatched closing group in chemical formula.")
        for symbol, count in counts.items():
            total[symbol] = total.get(symbol, 0) + count * coefficient
            if total[symbol] > 2**53 - 1:
                raise ValueError("Element count exceeds exact integer range.")
    if sum(total.values()) > 2**53 - 1:
        raise ValueError("Formula-unit atom count exceeds exact integer range.")
    return total


def molar_mass(data):
    counts = _formula_counts(data["formula"])
    rows = [[symbol, n, _ATOMIC_WEIGHTS[symbol], n * _ATOMIC_WEIGHTS[symbol]]
            for symbol, n in sorted(counts.items(), key=lambda item: _ATOMIC_ORDER[item[0]])]
    mass = math.fsum(row[3] for row in rows)
    return {
        "summary": f"Molar mass of {data['formula'].strip()} = {mass:.6g} g/mol "
                   "(CIAAW 2024 abridged standard atomic weights).",
        "values": {"molar_mass_g_mol": mass, "element_count": len(counts),
                   "atoms_per_formula_unit": sum(counts.values())},
        "table": {"columns": ["Element", "Atoms (count)", "Atomic weight (g/mol)",
                              "Contribution (g/mol)"], "rows": rows},
    }


def solution_dilution(data):
    c = _number(data["stock_concentration_mol_l"], "Stock concentration (mol/L)", positive=True)
    aliquot = _number(data["aliquot_volume_ml"], "Stock aliquot (mL)", positive=True)
    final = _number(data["final_volume_ml"], "Final solution volume (mL)", positive=True)
    if final < aliquot:
        raise ValueError("Final volume must be at least the stock aliquot volume.")
    concentration = c * (aliquot / final)
    if not math.isfinite(concentration) or concentration <= 0:
        raise ValueError("Diluted concentration is outside the numerical range.")
    return {
        "summary": f"Dilute {aliquot:g} mL of {c:g} mol/L stock to {final:g} mL: "
                   f"{concentration:.6g} mol/L (ideal additive volumes).",
        "values": {"final_concentration_mol_l": concentration,
                   "dilution_factor": final / aliquot,
                   "solute_amount_mmol": c * aliquot,
                   "diluent_volume_ml": final - aliquot},
    }


def reaction_yield(data):
    coefficients, masses, molar_masses = (data[key] for key in
        ("reactant_coefficients", "reactant_mass_g", "reactant_molar_mass_g_mol"))
    if not 2 <= len(coefficients) <= 16 or len(coefficients) != len(masses) or len(masses) != len(molar_masses):
        raise ValueError("Supply 2–16 reactants with matching coefficients, masses and molar masses.")
    for coeff in coefficients:
        _number(coeff, "Stoichiometric coefficient", positive=True)
        if int(coeff) != coeff or coeff > 1_000_000:
            raise ValueError("Stoichiometric coefficients must be positive whole numbers ≤ 1,000,000.")
    for mass, mm in zip(masses, molar_masses):
        _number(mass, "Reactant mass (g)", nonnegative=True)
        _number(mm, "Reactant molar mass (g/mol)", positive=True)
    product_coeff = _number(data["product_coefficient"], "Product coefficient", positive=True)
    if int(product_coeff) != product_coeff or product_coeff > 1_000_000:
        raise ValueError("Product coefficient must be a positive whole number ≤ 1,000,000.")
    product_mm = _number(data["product_molar_mass_g_mol"], "Product molar mass (g/mol)", positive=True)
    actual = _number(data["actual_product_mass_g"], "Actual product mass (g)", nonnegative=True)
    amounts = [mass / mm for mass, mm in zip(masses, molar_masses)]
    extents = [amount / coeff for amount, coeff in zip(amounts, coefficients)]
    limiting = min(range(len(extents)), key=lambda i: extents[i])
    extent = extents[limiting]
    theoretical_mol = extent * product_coeff
    theoretical_g = theoretical_mol * product_mm
    if not math.isfinite(theoretical_g):
        raise ValueError("Theoretical product yield exceeds the numerical range.")
    if theoretical_g == 0:
        if actual:
            raise ValueError("Positive actual product mass is inconsistent with zero theoretical yield.")
        yield_percent = "undefined (zero theoretical yield)"
        yield_text = "undefined (zero theoretical yield)"
        warning = ""
    else:
        yield_percent = 100 * (actual / theoretical_g)
        if not math.isfinite(yield_percent):
            raise ValueError("Yield percentage exceeds the numerical range.")
        yield_text = f"{yield_percent:.4g}%"
        warning = (" Yield above 100% suggests product impurities or measurement error."
                   if yield_percent > 100 else "")
    return {
        "summary": f"Reactant {limiting + 1} limits the reaction; theoretical product "
                   f"{theoretical_g:.6g} g, observed yield {yield_text}." + warning,
        "values": {"limiting_reactant_index": limiting + 1, "reaction_extent_mol": extent,
                   "theoretical_product_mol": theoretical_mol,
                   "theoretical_product_mass_g": theoretical_g,
                   "actual_product_mass_g": actual, "percent_yield": yield_percent},
        "table": {"columns": ["Reactant (1-based index)", "Coefficient (1)", "Initial (mol)",
                              "Consumed (mol)", "Remaining (mol)", "Remaining (g)"],
                  "rows": [[i + 1, coeff, amount, extent * coeff,
                            max(0.0, amount - extent * coeff),
                            max(0.0, amount - extent * coeff) * mm]
                           for i, (coeff, amount, mm) in enumerate(zip(coefficients, amounts, molar_masses))]},
    }


# Append only: do not change the IDs, examples, outputs, schema, or receipts
# for the original three operations (including saved Scientific Workbench nodes).
OPERATIONS += (
    Operation(
        "acid_base_speciation", "Acid/base pH and polyprotic speciation",
        "Ideal 25 °C charge-balance pH, pOH and species fractions for neutral acids or neutral bases. "
        "Enter successive ascending pKa values of the fully protonated species; spectator charge is "
        "positive for fixed cations and negative for fixed anions (mol charge/L).",
        (Field("family", "Starting neutral compound", "text", unit="category: acid | base"),
         Field("pka_values", "Successive conjugate-acid pKa values", "vector", unit="pKa"),
         Field("formal_concentration_mol_l", "Analytical concentration", "number", unit="mol/L"),
         Field("spectator_charge_mol_l", "Fixed spectator-ion charge (cations minus anions)", "number", unit="mol charge/L")),
        {"family": "acid", "pka_values": [2.15, 7.20, 12.35],
         "formal_concentration_mol_l": 0.1, "spectator_charge_mol_l": 0},
        acid_base_speciation,
        headline="ph",
    ),
    Operation(
        "buffer_ph_capacity", "Buffer pH and capacity: Henderson–Hasselbalch vs exact",
        "For a monoprotic HA/NaA (or BH+/B with the corresponding counterion) ideal aqueous "
        "buffer at 25 °C, compare Henderson–Hasselbalch with full charge balance and water, "
        "including buffer capacity d(strong base)/d(pH) per litre.",
        (Field("pka", "Conjugate-acid pKa", "number", unit="pKa"),
         Field("acid_concentration_mol_l", "Weak acid (or conjugate acid) analytical concentration", "number", unit="mol/L"),
         Field("base_concentration_mol_l", "Conjugate-base salt analytical concentration", "number", unit="mol/L")),
        {"pka": 4.7447274948966935, "acid_concentration_mol_l": 0.1, "base_concentration_mol_l": 0.1},
        buffer_ph_capacity,
        headline="ph_charge_balance",
    ),
    Operation(
        "titration_curve", "Polyprotic acid/base titration with equivalence points",
        "At 25 °C solve exact mass/charge balance at every strong-base (acid analyte) or "
        "strong-acid (base analyte) addition, including each stoichiometric equivalence point; "
        "assumes ideal, additive solution volumes and neutral initial analyte.",
        (Field("family", "Neutral analyte type", "text", unit="category: acid | base"),
         Field("pka_values", "Successive conjugate-acid pKa values", "vector", unit="pKa"),
         Field("analyte_concentration_mol_l", "Initial analyte concentration", "number", unit="mol/L"),
         Field("analyte_volume_ml", "Initial analyte volume", "number", unit="mL"),
         Field("titrant_concentration_mol_l", "Strong titrant concentration", "number", unit="mol/L"),
         Field("max_titrant_volume_ml", "Last titrant addition (must cover all equivalences)", "number", unit="mL"),
         Field("samples", "Uniformly spaced curve samples", "integer", unit="count")),
        {"family": "acid", "pka_values": [-math.log10(1.8e-5)],
         "analyte_concentration_mol_l": 0.1, "analyte_volume_ml": 25,
         "titrant_concentration_mol_l": 0.1, "max_titrant_volume_ml": 50, "samples": 51},
        titration_curve,
        headline="equivalence_1_volume_ml",
    ),
    Operation(
        "arrhenius_fit", "Arrhenius activation-energy regression",
        "Unweighted linear least squares for ln(k / one declared unit) versus 1000/T, "
        "with residual-based 1σ standard error on Ea (requires at least 3 temperatures).",
        (Field("temperature_k", "Absolute measurement temperatures", "vector", unit="K"),
         Field("rate_constant", "Measured rate constants", "vector", unit="see rate_constant_unit"),
         Field("rate_constant_unit", "Unit shared by all k values", "text", unit="unit label")),
        {"temperature_k": [290, 300, 310, 320],
         "rate_constant": [0.00095, 0.00164, 0.00274, 0.00447], "rate_constant_unit": "s^-1"},
        arrhenius_fit,
    ),
    Operation(
        "rate_law_fit", "First/second-order integrated rate-law fit",
        "Unweighted ln(concentration) (first order) or reciprocal concentration (second order) "
        "against elapsed time, returning rate constant, 1σ slope error, half-life and fitted trace.",
        (Field("order", "Integrated rate law", "text", unit="category: first | second"),
         Field("time_s", "Elapsed time since start", "vector", unit="s"),
         Field("concentration_mol_l", "Reactant concentrations", "vector", unit="mol/L")),
        {"order": "first", "time_s": [0, 60, 120, 180],
         "concentration_mol_l": [0.2, 0.14816, 0.10976, 0.08131]},
        rate_law_fit,
        headline="rate_constant_per_s",
    ),
    Operation(
        "gas_pressure", "Ideal and van der Waals gas pressure",
        "Compute P=nRT/V and P=nRT/(V−nb)−a(n/V)². Use bar, L, K, mol consistently; "
        "supply nonnegative vdW constants for the gas at the studied conditions.",
        (Field("amount_mol", "Gas amount", "number", unit="mol"),
         Field("temperature_k", "Absolute temperature", "number", unit="K"),
         Field("volume_l", "Container volume", "number", unit="L"),
         Field("a_l2_bar_mol2", "vdW attractive constant a", "number", unit="L²·bar/mol²"),
         Field("b_l_mol", "vdW covolume constant b", "number", unit="L/mol")),
        {"amount_mol": 1, "temperature_k": 273.15, "volume_l": 22.41396954,
         "a_l2_bar_mol2": 3.59 * 1.01325, "b_l_mol": 0.0427},
        gas_pressure,
        headline="van_der_waals_pressure_bar",
    ),
    Operation(
        "gas_volume", "Ideal and gas-branch van der Waals volume",
        "At a specified pressure solve ideal volume and the largest positive vdW molar-volume "
        "root. The liquid-like roots of subcritical vdW isotherms are not selected.",
        (Field("amount_mol", "Gas amount", "number", unit="mol"),
         Field("temperature_k", "Absolute temperature", "number", unit="K"),
         Field("pressure_bar", "Target absolute pressure", "number", unit="bar"),
         Field("a_l2_bar_mol2", "vdW attractive constant a", "number", unit="L²·bar/mol²"),
         Field("b_l_mol", "vdW covolume constant b", "number", unit="L/mol")),
        {"amount_mol": 1, "temperature_k": 300, "pressure_bar": 1,
         "a_l2_bar_mol2": 3.59 * 1.01325, "b_l_mol": 0.0427},
        gas_volume,
        headline="van_der_waals_gas_volume_l",
    ),
    Operation(
        "molar_mass", "Molar mass of grouped and hydrated formulas",
        "Parse nested round/square parentheses and integer hydrate prefixes separated by · or .; "
        "sum CIAAW 2024 abridged atomic weights in g/mol (not isotope-specific).",
        (Field("formula", "Neutral formula unit, e.g. CuSO4·5H2O", "text", unit="formula string"),),
        {"formula": "CuSO4·5H2O"},
        molar_mass,
        reference_datasets=("chemistry.ciaaw_abridged_atomic_weights",),
        headline="molar_mass_g_mol",
    ),
    Operation(
        "solution_dilution", "Prepare a dilution from stock solution",
        "Conservation of solute: C1 V1 = C2 V2 for an aliquot brought to final solution volume.",
        (Field("stock_concentration_mol_l", "Stock molarity", "number", unit="mol/L"),
         Field("aliquot_volume_ml", "Stock aliquot", "number", unit="mL"),
         Field("final_volume_ml", "Final solution volume", "number", unit="mL")),
        {"stock_concentration_mol_l": 2, "aliquot_volume_ml": 10, "final_volume_ml": 100},
        solution_dilution,
    ),
    Operation(
        "reaction_yield", "Limiting reagent and percent product yield",
        "Given coefficients from a separately balanced reaction and reactant masses/molar masses, "
        "compute limiting extent, excess remaining, theoretical mass and actual/theoretical percent yield.",
        (Field("reactant_coefficients", "Balanced reactant coefficients", "vector", unit="1"),
         Field("reactant_mass_g", "Reactant starting masses", "vector", unit="g"),
         Field("reactant_molar_mass_g_mol", "Corresponding reactant molar masses", "vector", unit="g/mol"),
         Field("product_coefficient", "Balanced desired product coefficient", "number", unit="1"),
         Field("product_molar_mass_g_mol", "Desired product molar mass", "number", unit="g/mol"),
         Field("actual_product_mass_g", "Isolated dry product mass", "number", unit="g")),
        {"reactant_coefficients": [2, 1], "reactant_mass_g": [4.032, 32],
         "reactant_molar_mass_g_mol": [2.016, 31.998], "product_coefficient": 2,
         "product_molar_mass_g_mol": 18.015, "actual_product_mass_g": 30},
        reaction_yield,
        headline="percent_yield",
    ),
)
