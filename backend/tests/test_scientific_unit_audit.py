"""Independent correctness regressions for Scientific Workbench SI inputs.

BIPM SI Brochure (9th ed.) §§2.3.4, 4.1 and NIST SP 811 §§4–6:
prefixes and derived units are exact, angles are physically distinct from
pure ratios, repeated solidi are not acceptable ambiguous notation, and
absolute temperatures differ from temperature intervals.
"""

from copy import deepcopy
from fractions import Fraction

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import (
    Field, Operation, fingerprint, readable_result, run_operation,
    validated_inputs, validated_inputs_with_units,
)
from graphlink_plugins.scientific.units import (
    convert_decimal_measurement, convert_measurement, dimension_name, parse_unit,
)


def _operation(pack, name):
    return next(op for op in operations(pack) if op.id == name)


@pytest.mark.parametrize("unit,expected", [
    ("Qm", Fraction(10**30)),
    ("qm", Fraction(1, 10**30)),
    ("µm", Fraction(1, 10**6)),
    ("ug", Fraction(1, 10**9)),
    ("mm²", Fraction(1, 10**6)),
    ("m³", Fraction(1)),
    ("mL", Fraction(1, 10**6)),
    ("kWh", Fraction(3_600_000)),
    ("N", Fraction(1)),
    ("Pa", Fraction(1)),
    ("bar", Fraction(100_000)),
    ("eV", Fraction(1_602_176_634, 10**28)),
    ("mM", Fraction(1)),
    ("mg/L", Fraction(1, 1000)),
    ("MPa√m", Fraction(10**6)),
])
def test_si_and_derived_factors_have_exact_rational_definitions(unit, expected):
    assert parse_unit(unit).scale == expected
    assert isinstance(parse_unit(unit).scale, Fraction)


def test_derived_unit_dimensions_match_the_si_defining_equations():
    for derived, expression in [
        ("Pa", "N/m²"), ("J", "N*m"), ("W", "J/s"),
        ("V", "W/A"), ("Ω", "V/A"), ("F", "C/V"),
        ("mM", "mol/m³"), ("kg/m³", "kg/(m³)"),
        ("lm", "cd*sr"), ("lx", "lm/m²"),
        ("MPa√m", "Pa√m"),
    ]:
        left, right = parse_unit(derived), parse_unit(expression)
        assert left.dimensions == right.dimensions
        assert left.angle_power == right.angle_power
        assert left.scale == right.scale or derived == "MPa√m"


@pytest.mark.parametrize("bad", [
    "m/s/s", "kg/m*s", "km/s/Mpc", "m/Hz/s", "m/s/Hz",
    "furlong", "MegaPa", "kpa", "ms^-1/x", "k°C",
])
def test_unknown_or_ambiguous_unit_expressions_are_rejected(bad):
    with pytest.raises(ValueError, match="Ambiguous|Unknown|absolute|Invalid"):
        parse_unit(bad)


def test_parenthesized_denominators_are_not_ambiguously_interpreted():
    assert parse_unit("km/(s·Mpc)").dimensions == parse_unit("1/s").dimensions
    assert parse_unit("kg/(m*s)").dimensions == parse_unit("kg/(m*s)").dimensions


def test_angle_frequency_and_dimensionless_ratio_are_distinct_kinds():
    assert dimension_name(parse_unit("deg").dimensions, parse_unit("deg").angle_power) == "plane angle (rad)"
    assert dimension_name(parse_unit("rad/s").dimensions, parse_unit("rad/s").angle_power) == "angular frequency (rad/s)"
    assert dimension_name(parse_unit("sr").dimensions, parse_unit("sr").angle_power) == "solid angle (sr)"
    assert convert_decimal_measurement("180", "deg", "rad") == pytest.approx(3.141592653589793)
    assert convert_decimal_measurement("90", "deg/s", "rad/s") == pytest.approx(1.5707963267948966)
    assert convert_decimal_measurement("20", "%", "1") == pytest.approx(0.2)
    for source, target in [
        ("deg", "1"), ("1", "rad"), ("rad", "%"),
        ("rad/s", "Hz"), ("Hz", "rad/s"), ("sr", "rad"), ("cd", "lm"),
    ]:
        with pytest.raises(ValueError, match="Incompatible quantities"):
            convert_decimal_measurement("1", source, target)
        with pytest.raises(ValueError, match="Incompatible quantities"):
            convert_measurement(1, source, target)
    op = _operation("engineering", "transfer_function_response")
    invalid = deepcopy(op.example)
    invalid["natural_frequency_rad_s"] = "1 Hz"
    with pytest.raises(ValueError, match="Incompatible quantities"):
        validated_inputs(op, invalid)
    valid = deepcopy(op.example)
    valid["natural_frequency_rad_s"] = "180 deg/s"
    assert validated_inputs(op, valid)["natural_frequency_rad_s"] == pytest.approx(3.141592653589793)


def test_affine_points_and_delta_temperatures_use_exact_si_offsets():
    assert convert_decimal_measurement("32", "degF", "K") == pytest.approx(273.15)
    assert convert_decimal_measurement("-459.67", "degF", "K") == 0.0
    assert convert_decimal_measurement("-273.15", "degC", "K") == 0.0
    assert convert_decimal_measurement("18", "delta_degF", "delta_degC") == 10.0
    assert convert_decimal_measurement("10", "delta_degC", "delta_K") == 10.0
    with pytest.raises(ValueError, match="temperature kinds"):
        convert_decimal_measurement("18", "degF", "delta_K")
    with pytest.raises(ValueError, match="temperature kinds"):
        convert_decimal_measurement("18", "delta_degF", "K")


@pytest.mark.parametrize("unit,value", [
    ("K", -0.01), ("°C", -273.16), ("degF", -459.68),
])
def test_bare_numeric_absolute_temperatures_below_zero_kelvin_are_rejected(unit, value):
    op = Operation("absolute_probe", "Absolute probe", "", (
        Field("temperature", "Absolute temperature", "number", unit=unit),
    ), {"temperature": 0}, lambda data: {
        "summary": "temperature", "values": {"temperature": data["temperature"]},
    })
    with pytest.raises(ValueError, match="Absolute temperature must be at least 0 K"):
        validated_inputs(op, {"temperature": value})


def test_prefixed_kelvins_are_intervals_only_for_delta_fields():
    op = _operation("materials", "thermal_expansion_stress")
    assert next(field for field in op.fields if field.key == "temperature_change_k").default_unit == "delta_K"
    raw = deepcopy(op.example)
    raw["temperature_change_k"] = "90 delta_degF"
    result = run_operation("materials", op, raw)
    expected = run_operation("materials", op, op.example)
    assert result["inputs"]["temperature_change_k"] == 50
    assert result["values"] == expected["values"]
    assert result["values"]["free_length_change_mm"] == pytest.approx(0.6)
    assert result["provenance"]["inputUnits"]["temperature_change_k"] == "delta_degF"
    assert readable_result({"version": 1, "latest": result}, "materials")

    raw["temperature_change_k"] = "50000 mK"
    assert validated_inputs(op, raw)["temperature_change_k"] == 50
    raw["temperature_change_k"] = "-18 delta_degF"
    assert validated_inputs(op, raw)["temperature_change_k"] == -10
    for invalid in ("90 degF", "10 degC"):
        raw["temperature_change_k"] = invalid
        with pytest.raises(ValueError, match="temperature kinds"):
            validated_inputs(op, raw)


def test_declared_engineering_temperature_difference_matches_formula():
    op = _operation("engineering", "linear_expansion")
    assert next(field for field in op.fields if field.key == "temperature_change_k").default_unit == "delta_K"
    raw = deepcopy(op.example)
    raw["temperature_change_k"] = "72 delta_degF"
    normalized = run_operation("engineering", op, raw)
    bare = run_operation("engineering", op, op.example)
    assert normalized["values"] == bare["values"]


def test_hubble_constant_has_a_grouped_time_inverse_unit():
    op = _operation("astronomy", "flat_lcdm_distances")
    field = next(field for field in op.fields if field.key == "h0_km_s_mpc")
    assert field.default_unit == "km/(s·Mpc)"
    assert parse_unit(field.default_unit).dimensions == parse_unit("1/s").dimensions
    result = run_operation("astronomy", op, op.example)
    assert result["provenance"]["inputUnits"]["h0_km_s_mpc"] == "km/(s·Mpc)"
    assert result["values"]["comoving_distance_mpc"] > 0


def test_paris_exponent_dependent_coefficient_is_not_mislabeled_dimensionless():
    op = _operation("materials", "paris_crack_growth")
    field = next(f for f in op.fields if f.key == "paris_c_m_cycle_inv_mpa_sqrt_m_neg_m")
    assert field.default_unit == "m/cycle/(MPa√m)^m"
    assert field.dimension.startswith("exponent-dependent:")
    bare = run_operation("materials", op, op.example)
    assert bare["provenance"]["inputUnits"][field.key] == field.default_unit
    assert bare["values"]["paris_growth_cycles"] > 0
    assert bare["values"]["initial_delta_k_mpa_sqrt_m"] > 0
    assert readable_result({"version": 1, "latest": bare}, "materials")
    raw = deepcopy(op.example)
    raw[field.key] = "1e-12 m"
    with pytest.raises(ValueError, match="cannot be converted"):
        validated_inputs(op, raw)


def test_empirical_hardness_is_recorded_in_selected_scale_without_si_conversion():
    op = _operation("materials", "hardness_e140")
    field = next(f for f in op.fields if f.key == "hardness_reading")
    assert field.default_unit == "HRC"
    assert field.dimension == "empirical hardness scale (selected)"
    raw = deepcopy(op.example)
    normalized, symbols = validated_inputs_with_units(op, raw)
    assert normalized["hardness_reading"] == 38
    assert symbols["hardness_reading"] == "HRC"
    raw["hardness_reading"] = "38 HRC"
    assert validated_inputs_with_units(op, raw)[1]["hardness_reading"] == "HRC"
    raw["hardness_reading"] = "38 HV"
    with pytest.raises(ValueError, match="cannot be converted"):
        validated_inputs(op, raw)
    raw["from_scale"] = "HV"
    assert validated_inputs_with_units(op, raw)[1]["hardness_reading"] == "HV"


def test_suffixed_values_are_scaled_before_range_and_integer_rounding():
    op = Operation("decimal_probe", "Decimal probe", "", (
        Field("distance", "Distance", "number", unit="m"),
        Field("pressure", "Pressure", "number", unit="Pa"),
        Field("samples", "Samples", "vector", unit="m"),
    ), {"distance": 1, "pressure": 1, "samples": [1]}, lambda data: {
        "summary": "Converted", "values": {"distance_m": data["distance"]},
    })
    raw = {"distance": "1e310 qm", "pressure": "1e310 qPa",
           "samples": ["9007199254740993 nm", "0.01 m"]}
    result = run_operation("core", op, raw)
    assert result["inputs"]["distance"] == pytest.approx(1e280)
    assert result["inputs"]["pressure"] == pytest.approx(1e280)
    assert result["inputs"]["samples"][0] == pytest.approx(9007199.254740993)
    assert result["provenance"]["inputUnits"]["samples"] == ["nm", "m"]


@pytest.mark.parametrize("source,target", [
    ("%", "mag"), ("1", "mag AB"), ("mag", "mag AB"),
    ("%", "Mw (1)"), ("1", "pKa"), ("mag", "mag/airmass"),
])
def test_logarithmic_and_linear_quantities_cannot_be_interchanged(source, target):
    with pytest.raises(ValueError, match="Incompatible quantity kinds"):
        convert_decimal_measurement("1", source, target)
    with pytest.raises(ValueError, match="logarithmic quantities"):
        convert_measurement(1, source, target)
    for unit in ("mag*1", "pKa/1", "mag^2"):
        with pytest.raises(ValueError, match="Nonlinear"):
            parse_unit(unit)


def test_pka_inputs_use_logarithmic_units_without_changing_bare_results():
    op = _operation("chemistry", "buffer_ph_capacity")
    field = next(f for f in op.fields if f.key == "pka")
    assert field.default_unit == "pKa"
    assert field.dimension == "logarithmic acid-dissociation index (pKa)"
    old = run_operation("chemistry", op, op.example)
    assert old["provenance"]["inputUnits"]["pka"] == "pKa"
    raw = deepcopy(op.example)
    raw["pka"] = f'{raw["pka"]} pKa'
    assert run_operation("chemistry", op, raw)["values"] == old["values"]
    raw["pka"] = "4.7 %"
    with pytest.raises(ValueError, match="Incompatible quantity kinds"):
        validated_inputs(op, raw)
    for name in ("acid_base_speciation", "titration_curve"):
        pack_op = _operation("chemistry", name)
        assert next(f for f in pack_op.fields if f.key == "pka_values").default_unit == "pKa"


def test_legacy_bare_receipts_keep_their_hashes_and_units():
    op = _operation("engineering", "linear_expansion")
    old = run_operation("engineering", op, op.example)
    saved = deepcopy(old)
    receipt = saved["provenance"]
    receipt.pop("inputUnits")
    receipt["receiptHash"] = fingerprint({k: v for k, v in receipt.items() if k != "receiptHash"})
    assert readable_result({"version": 1, "latest": saved}, "engineering")
    assert saved["provenance"]["inputHash"] == old["provenance"]["inputHash"]
    assert saved["provenance"]["resultHash"] == old["provenance"]["resultHash"]
