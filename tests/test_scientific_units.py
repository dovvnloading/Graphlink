"""Exact SI definitions, dimensional compatibility, and saved-result regressions."""

import json
import math
import random
from decimal import Decimal
from fractions import Fraction

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation
from graphlink_plugins.scientific.units import parse_unit


_CONVERT = next(op for op in operations("core") if op.id == "convert_unit")


def convert(value, source, target):
    return run_operation("core", _CONVERT, {"value": value, "from_unit": source, "to_unit": target})


@pytest.mark.parametrize(("symbol", "dimensions", "scale"), [
    ("m", (1, 0, 0, 0, 0, 0, 0), Fraction(1)),
    ("kg", (0, 1, 0, 0, 0, 0, 0), Fraction(1)),
    ("s", (0, 0, 1, 0, 0, 0, 0), Fraction(1)),
    ("A", (0, 0, 0, 1, 0, 0, 0), Fraction(1)),
    ("K", (0, 0, 0, 0, 1, 0, 0), Fraction(1)),
    ("mol", (0, 0, 0, 0, 0, 1, 0), Fraction(1)),
    ("cd", (0, 0, 0, 0, 0, 0, 1), Fraction(1)),
    ("N", (1, 1, -2, 0, 0, 0, 0), Fraction(1)),
    ("Pa", (-1, 1, -2, 0, 0, 0, 0), Fraction(1)),
    ("J", (2, 1, -2, 0, 0, 0, 0), Fraction(1)),
    ("W", (2, 1, -3, 0, 0, 0, 0), Fraction(1)),
    ("Hz", (0, 0, -1, 0, 0, 0, 0), Fraction(1)),
    ("C", (0, 0, 1, 1, 0, 0, 0), Fraction(1)),
    ("V", (2, 1, -3, -1, 0, 0, 0), Fraction(1)),
    ("ohm", (2, 1, -3, -2, 0, 0, 0), Fraction(1)),
    ("Wb", (2, 1, -2, -1, 0, 0, 0), Fraction(1)),
    ("T", (0, 1, -2, -1, 0, 0, 0), Fraction(1)),
    ("L", (3, 0, 0, 0, 0, 0, 0), Fraction(1, 1000)),
    ("min", (0, 0, 1, 0, 0, 0, 0), Fraction(60)),
    ("h", (0, 0, 1, 0, 0, 0, 0), Fraction(3600)),
    ("eV", (2, 1, -2, 0, 0, 0, 0), Fraction(1602176634, 10**28)),
    ("km/h", (1, 0, -1, 0, 0, 0, 0), Fraction(5, 18)),
    ("kWh", (2, 1, -2, 0, 0, 0, 0), Fraction(3600000)),
    ("m³", (3, 0, 0, 0, 0, 0, 0), Fraction(1)),
    ("m/s²", (1, 0, -2, 0, 0, 0, 0), Fraction(1)),
    ("µm", (1, 0, 0, 0, 0, 0, 0), Fraction(1, 10**6)),
    ("qg", (0, 1, 0, 0, 0, 0, 0), Fraction(1, 10**33)),
    ("Qm", (1, 0, 0, 0, 0, 0, 0), Fraction(10**30)),
])
def test_exact_dimensions_and_factors(symbol, dimensions, scale):
    unit = parse_unit(symbol)
    assert unit.dimensions == dimensions
    assert unit.scale == scale


@pytest.mark.parametrize(("source", "target", "value", "expected"), [
    ("km/h", "m/s", 72, 20),
    ("m/s", "km/h", 1, 3.6),
    ("kWh", "MJ", 2, 7.2),
    ("N", "kg*m/s^2", 5, 5),
    ("Pa", "N/m²", 3, 3),
    ("J", "N·m", 2, 2),
    ("kΩ", "ohm", 3, 3000),
    ("mA", "A", 50, 0.05),
    ("mL", "cm³", 100, 100),
    ("bar", "Pa", 1, 100000),
    ("MWh", "GJ", 1, 3.6),
    ("eV", "J", 1, 1.602176634e-19),
    ("1/s", "Hz", 4, 4),
    ("W/(m*K)", "J/(s*m*delta_K)", 5, 5),
    ("°C", "K", 25, 298.15),
    ("degC", "degF", 100, 212),
    ("°F", "°C", 32, 0),
    ("°F", "K", -459.67, 0),
    ("delta_degF", "delta_K", 18, 10),
    ("Δ°F", "Δ°C", 9, 5),
    ("delta_degC", "delta_K", -8, -8),
    ("mK", "K", 273150, 273.15),
    ("delta_mK", "delta_K", 1000, 1),
])
def test_conversions(source, target, value, expected):
    result = convert(value, source, target)
    assert result["values"][f"value ({target})"] == pytest.approx(expected, rel=1e-14, abs=0)


@pytest.mark.parametrize(("source", "target", "error"), [
    ("m", "s", "Incompatible SI dimensions"),
    ("km/h", "J", "Incompatible SI dimensions"),
    ("W", "V", "Incompatible SI dimensions"),
    ("K", "delta_K", "absolute temperatures and temperature differences"),
    ("°C", "delta_degC", "absolute temperatures and temperature differences"),
    ("°F", "delta_degF", "absolute temperatures and temperature differences"),
    ("mK", "delta_K", "absolute temperatures and temperature differences"),
    ("°C/s", "K/s", "absolute temperature"),
    ("degF*m", "delta_K*m", "absolute temperature"),
    ("nonsense", "m", "Unknown unit symbol"),
    ("m//s", "m/s", "Ambiguous unit expression"),
    ("m^(2)", "m²", "Unit powers"),
    ("m^999", "m", "Unit powers"),
    ("µkg", "kg", "Unknown unit symbol"),
])
def test_rejects_bad_or_incompatible_units(source, target, error):
    with pytest.raises(ValueError, match=error):
        convert(1, source, target)


@pytest.mark.parametrize("source,value", [("K", -0.01), ("°C", -273.16), ("degF", -459.68)])
def test_absolute_zero_and_signed_intervals(source, value):
    with pytest.raises(ValueError, match="Absolute temperature"):
        convert(value, source, "K")
    assert convert(-4, "delta_degF", "delta_K")["values"]["value (delta_K)"] < 0


def test_numerical_underflow_is_not_silent():
    with pytest.raises(ValueError, match="numerical range"):
        convert(math.ulp(0), "mL", "L")


def test_original_results_and_readable_receipts_are_unchanged():
    groups = {
        "time": {"s": 1., "ms": 0.001, "min": 60., "h": 3600.},
        "length": {"mm": 0.001, "cm": 0.01, "m": 1., "km": 1000.},
        "mass": {"g": 0.001, "kg": 1., "mg": 1e-6},
        "volume": {"mL": 1e-6, "L": 0.001, "m³": 1.},
    }
    rng = random.Random(28209)
    for dimension, group in groups.items():
        for source, a in group.items():
            for target, b in group.items():
                value = rng.randrange(-50000, 50000) / 8
                expected = value if source == target else float(Fraction(value) * Fraction(str(a)) / Fraction(str(b)))
                result = convert(value, source, target)
                assert result["values"] == {f"value ({target})": expected}
                assert result["summary"] == f"Converted {dimension} from {source} to {target}."
                assert result["provenance"]["packVersion"] == VERSION == "1.2.0"
    for source, target, value in [("K", "°C", 0), ("°C", "K", 25), ("°C", "°C", -273.15)]:
        original_offset = {"K": 0.0, "°C": 273.15}
        expected = value if source == target else float(
            Decimal(str(value)) + Decimal(str(original_offset[source])) - Decimal(str(original_offset[target]))
        )
        result = convert(value, source, target)
        assert result["values"] == {f"value ({target})": expected}
        assert result["summary"] == f"Converted temperature from {source} to {target}."
        stored = json.loads(json.dumps({"version": 1, "latest": result}))
        assert readable_result(stored, "core")
