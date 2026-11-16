"""Photometric, astrometric and ideal blackbody relations."""

import math

from graphlink_plugins.scientific.core import Field, Operation

# IAU fixed au (m), Julian year (s), definition of the parsec and exact c (m/s).
_LIGHT_YEARS_PER_PARSEC = (648000 / math.pi) * 149597870700 / (31557600 * 299792458)
# NIST CODATA wavelength displacement constant b (m K), rounded to 10 figures.
# https://physics.nist.gov/cgi-bin/cuu/Value?bwien
_WIEN_M_K = 2.897771955e-3


def _parallax(data):
    parallax = data["parallax_mas"]
    if parallax <= 0:
        raise ValueError("A geometric inverse-parallax distance requires a positive parallax.")
    distance_pc = 1000 / parallax
    return {
        "summary": "Geometric inverse-parallax distance, intended for a precise positive stellar parallax.",
        "values": {"distance_pc": distance_pc, "distance_ly": distance_pc * _LIGHT_YEARS_PER_PARSEC},
    }


def _modulus(data):
    apparent, absolute, extinction = data["apparent_mag"], data["absolute_mag"], data["extinction_mag"]
    if extinction < 0:
        raise ValueError("Passband extinction must be nonnegative.")
    modulus = math.fsum([apparent, -absolute, -extinction])
    distance_pc = 10 ** (1 + modulus / 5)
    if distance_pc == 0:
        raise ValueError("The implied distance is below the supported numerical range.")
    return {
        "summary": "Photometric distance from apparent and absolute magnitudes corrected for extinction in the same band.",
        "values": {"true_distance_modulus_mag": modulus, "distance_pc": distance_pc},
    }


def _blackbody(data):
    temperature = data["temperature_k"]
    if temperature <= 0:
        raise ValueError("An emitting blackbody must have a positive absolute temperature.")
    wavelength_nm = (_WIEN_M_K * 1e9) / temperature
    return {
        "summary": "Peak of blackbody spectral radiance per unit wavelength by Wien’s displacement law.",
        "values": {"peak_wavelength_nm": wavelength_nm},
    }


OPERATIONS = (
    Operation(
        "parallax_distance",
        "Estimate distance from parallax",
        "Invert a precise positive stellar parallax to estimate distance in parsecs and light-years.",
        (Field("parallax_mas", "Annual parallax", "number", unit="mas"),),
        {"parallax_mas": 100},
        _parallax,
    ),
    Operation(
        "distance_modulus",
        "Calculate a photometric distance",
        "Convert the extinction-corrected distance modulus to parsecs using magnitudes from a common passband.",
        (
            Field("apparent_mag", "Apparent magnitude", "number", unit="mag"),
            Field("absolute_mag", "Absolute magnitude", "number", unit="mag"),
            Field("extinction_mag", "Passband extinction", "number", unit="mag"),
        ),
        {"apparent_mag": 10.3, "absolute_mag": 5.0, "extinction_mag": 0.3},
        _modulus,
    ),
    Operation(
        "blackbody_peak",
        "Find a blackbody spectral peak",
        "Compute the wavelength of maximum spectral radiance for an ideal blackbody at the specified temperature.",
        (Field("temperature_k", "Blackbody temperature", "number", unit="K"),),
        {"temperature_k": 5772},
        _blackbody,
    ),
)
