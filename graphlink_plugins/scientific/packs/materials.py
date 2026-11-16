"""Specimen-level density, tensile measurements, and liquid-accessible porosity."""

from fractions import Fraction

from graphlink_plugins.scientific.core import Field, Operation


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


OPERATIONS = (
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
