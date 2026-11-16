"""Quantitative interpretation of optical and isotope measurements."""

import math

from graphlink_plugins.scientific.core import Field, Operation


def beer_lambert(data):
    absorbance = data["absorbance"]
    coefficient = data["molar_absorptivity"]
    path = data["path_length_cm"]
    if absorbance < 0 or coefficient <= 0 or path <= 0:
        raise ValueError("Enter nonnegative absorbance and positive absorptivity and path length.")
    concentration = absorbance / coefficient / path
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
    # log10(100/T) written as a difference also handles subnormal positive T.
    absorbances = [2 - math.log10(t) for t in transmission]
    peak = max(absorbances)
    peak_nm = wavelengths[absorbances.index(peak)]
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
    average = math.fsum(mass * (percent / total) for mass, percent in zip(masses, abundance))
    return {
        "summary": f"Mean mass per atom in this sample is {average:.8g} u.",
        "values": {"mean_atomic_mass_u": average, "abundance_total_percent": total},
    }


OPERATIONS = (
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
