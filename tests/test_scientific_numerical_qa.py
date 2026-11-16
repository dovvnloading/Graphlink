"""Independent analytical oracles and scale checks for the 18 quantitative/core operations."""

import math
import random
import time
from decimal import Decimal, localcontext

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import run_operation


PACK_NAMES = ("core", "mathematics", "statistics", "physics", "astronomy", "engineering")
CATALOG = {op.id: (pack, op) for pack in PACK_NAMES for op in operations(pack)}


def run(name, **inputs):
    pack, operation = CATALOG[name]
    return run_operation(pack, operation, inputs)


def test_seeded_csv_import_and_plot_preserve_pairing_and_roundtrip():
    rng = random.Random(62531)
    x = [rng.randrange(-300, 300) / 8 for _ in range(32)]
    y = [rng.randrange(-300, 300) / 16 for _ in x]
    lines = [f'{a:.5f},"{b:.6f}"' for a, b in zip(x, y)]
    csv_data = '\ufeff\r\n"Position, x",Velocity\r\n\r\n' + "\r\n".join(lines)
    table = run("numeric_csv", csv=csv_data)["table"]
    assert table["columns"] == ["Position, x", "Velocity"]
    assert table["rows"] == [[a, b] for a, b in zip(x, y)]
    result = run("plot_xy", x=x, y=y, x_label="Position, x", y_label="Velocity")
    assert result["table"] == table
    assert list(zip(result["plot"]["x"], result["plot"]["y"])) == list(zip(x, y))


@pytest.mark.parametrize("cell", ["1e-999", "9007199254740993", "18446744073709551617", "NaN"])
def test_csv_refuses_unrepresentable_counts_or_nonzero_values_lost_to_zero(cell):
    with pytest.raises(ValueError, match="line 2"):
        run("numeric_csv", csv=f"v\n{cell}")


def test_unit_conversion_against_exact_decimal_unit_definitions():
    units = [
        {"s": "1", "ms": ".001", "min": "60", "h": "3600"},
        {"mm": ".001", "cm": ".01", "m": "1", "km": "1000"},
        {"g": ".001", "kg": "1", "mg": ".000001"},
        {"mL": ".000001", "L": ".001", "m³": "1"},
    ]
    rng = random.Random(28209)
    for group in units:
        for source, a in group.items():
            for target, b in group.items():
                value = rng.randrange(-50000, 50000) / 8
                expected = float(Decimal(value) * Decimal(a) / Decimal(b))
                result = run("convert_unit", value=value, from_unit=source, to_unit=target)
                assert result["values"][f"value ({target})"] == expected
    assert run("convert_unit", value=0.0, from_unit="K", to_unit="°C")["values"]["value (°C)"] == -273.15
    assert run("convert_unit", value=-273.15, from_unit="°C", to_unit="K")["values"]["value (K)"] == 0.0
    with pytest.raises(ValueError, match="numerical range"):
        run("convert_unit", value=math.ulp(0.0), from_unit="mL", to_unit="L")


def test_polynomials_match_binomial_oracle_near_multiple_roots():
    for degree in (3, 6, 12):
        power_coefficients = [math.comb(degree, k) * (-1) ** (degree - k) for k in range(degree + 1)]
        points = [1 + 2**-i for i in (8, 12, 20)]
        result = run("polynomial", coefficients=power_coefficients, points=points)
        assert [row[1] for row in result["table"]["rows"]] == [(x - 1) ** degree for x in points]


def test_integration_avoids_premature_underflow_and_overflow():
    tiny = math.ulp(0.0)
    value = run("trapezoid", x=[0, 1e300], y=[tiny, 0])["values"]["integral"]
    assert math.isclose(value, (tiny * 1e300) / 2, rel_tol=1e-15)
    assert run("trapezoid", x=[-1e308, 1e308], y=[1e-308] * 2)["values"]["integral"] == pytest.approx(2)
    assert run("trapezoid", x=[-1e308, 0, 1e308], y=[-1e308, 0, 1e308])["values"]["integral"] == 0


def test_irregular_linear_curve_and_scaled_row_systems_have_analytical_solutions():
    rng = random.Random(81901)
    for _ in range(20):
        x = sorted(rng.sample(range(-100, 101), 5))
        slope, intercept = rng.randrange(-50, 51), rng.randrange(-30, 31)
        y = [slope * v + intercept for v in x]
        analytic = (x[-1] - x[0]) * (slope * (x[-1] + x[0]) / 2 + intercept)
        assert run("trapezoid", x=x, y=y)["values"]["integral"] == analytic
        matrix = [[rng.randrange(-4, 5) for _ in range(4)] for _ in range(4)]
        for i, row in enumerate(matrix):
            row[i] = 1 + sum(abs(v) for v in row)
        solution = [rng.randrange(-8, 9) for _ in matrix]
        scales = [2.0 ** rng.randrange(-240, 240) for _ in matrix]
        scaled = [[a * s for a in row] for row, s in zip(matrix, scales)]
        rhs = [sum(a * v for a, v in zip(row, solution)) for row in scaled]
        result = run("linear_system", matrix=scaled, rhs=rhs)
        output = [row[1] for row in result["table"]["rows"]]
        assert output == pytest.approx(solution, rel=1e-12, abs=1e-12)
        with localcontext() as ctx:
            ctx.prec = 180
            residual = max(
                abs(sum(Decimal(a) * Decimal(v) for a, v in zip(row, output)) - Decimal(b))
                for row, b in zip(scaled, rhs)
            )
        assert result["values"]["max_absolute_residual"] == float(residual)


def test_sample_weighted_and_regression_against_independent_moments():
    rng = random.Random(38532)
    for _ in range(16):
        x = rng.sample(range(-100, 101), 9)
        y = [rng.randrange(-150, 150) for _ in x]
        weights = [rng.randrange(1, 6) for _ in x]
        n = len(y)
        sum_x, sum_y = sum(x), sum(y)
        xx = n * sum(v * v for v in x) - sum_x**2
        yy = n * sum(v * v for v in y) - sum_y**2
        xy = n * sum(a * b for a, b in zip(x, y)) - sum_x * sum_y
        described = run("describe", observations=y)["values"]
        assert described["sample_sd"] == pytest.approx(math.sqrt(yy / (n * (n - 1))))
        assert described["mean"] == pytest.approx(sum_y / n)
        assert described["median"] == sorted(y)[n // 2]
        reg = run("linear_regression", x=x, y=y)["values"]
        assert reg["slope"] == pytest.approx(xy / xx)
        assert reg["intercept"] == pytest.approx((sum_y - (xy / xx) * sum_x) / n)
        assert reg["pearson_r"] == pytest.approx(xy / math.sqrt(xx * yy))
        expanded = [v for v, w in zip(y, weights) for _ in range(w)]
        mean = sum(expanded) / len(expanded)
        sd = math.sqrt(sum((v - mean) ** 2 for v in expanded) / len(expanded))
        weighted = run("weighted_summary", observations=y, weights=weights)["values"]
        assert weighted["weighted_mean"] == pytest.approx(mean)
        assert weighted["population_sd"] == pytest.approx(sd)


def test_thermal_models_and_oscillator_over_signs_and_scales():
    rng = random.Random(97103)
    for _ in range(16):
        m, f, a = rng.randrange(1, 10), rng.randrange(1, 20), rng.randrange(1, 10) / 10
        osc = run("spring_oscillator", mass_kg=m, stiffness_n_m=m * (math.tau * f) ** 2, amplitude_m=a)
        assert osc["values"]["frequency_hz"] == pytest.approx(f)
        assert osc["values"]["period_s"] == pytest.approx(1 / f)
        assert osc["plot"]["y"][16] == pytest.approx(0, abs=1e-15)
        q, area, left = rng.randrange(-50, 51), rng.randrange(1, 10), 300
        lengths = [rng.randrange(1, 8) / 8 for _ in range(3)]
        right = left - q * sum(lengths) / area
        layers = run(
            "thermal_layers", thickness_m=lengths, conductivity_w_mk=[1] * 3, area_m2=area, left_k=left, right_k=right
        )["values"]
        assert layers["heat_rate_w"] == pytest.approx(q, abs=1e-12)
        assert layers["heat_flux_w_m2"] == pytest.approx(q / area, abs=1e-12)
        c, change = rng.randrange(1, 200), rng.randrange(-80, 81)
        calorimetry = run("sensible_heat", mass_kg=m, specific_heat_j_kgk=c, initial_k=300, final_k=300 + change)
        assert calorimetry["values"]["heat_j"] == m * c * change
    conduction = run(
        "thermal_layers", thickness_m=[1e-290], conductivity_w_mk=[1e100], area_m2=1e-90, left_k=1e-300, right_k=0
    )["values"]
    assert math.isclose(conduction["resistance_k_w"], 1e-300, rel_tol=1e-15)
    assert math.isclose(conduction["heat_rate_w"], 1.0, rel_tol=1e-15)
    with pytest.raises(ValueError, match="capacity"):
        run("sensible_heat", mass_kg=1e-200, specific_heat_j_kgk=1e-200, initial_k=0, final_k=1e200)


def test_astronomy_roundtrips_and_a_magnitude_lost_by_pairwise_subtraction():
    rng = random.Random(7952)
    for _ in range(24):
        pc, absolute, extinction = 10 ** rng.uniform(-2, 6), rng.uniform(-9, 7), rng.random()
        parallax = run("parallax_distance", parallax_mas=1000 / pc)["values"]
        assert parallax["distance_pc"] == pytest.approx(pc, rel=1e-14)
        assert parallax["distance_ly"] / pc == pytest.approx(3.26156377717, rel=1e-10)
        apparent = 5 * math.log10(pc / 10) + extinction + absolute
        photometric = run("distance_modulus", apparent_mag=apparent, absolute_mag=absolute, extinction_mag=extinction)
        assert photometric["values"]["distance_pc"] == pytest.approx(pc, rel=1e-12)
        temperature = 10 ** rng.uniform(1, 8)
        peak = run("blackbody_peak", temperature_k=temperature)["values"]["peak_wavelength_nm"]
        assert peak * temperature == pytest.approx(2.897771955e6, rel=1e-14)
    values = run("distance_modulus", apparent_mag=1e16, absolute_mag=-1, extinction_mag=1e16)["values"]
    assert values["true_distance_modulus_mag"] == 1
    assert values["distance_pc"] == pytest.approx(15.8489319246)


def test_engineering_models_and_tiny_step_at_large_tau():
    rng = random.Random(39125)
    for _ in range(16):
        r, c, voltage = 2.0 ** rng.randrange(-20, 20), 2.0 ** rng.randrange(-20, 20), rng.uniform(-100, 100)
        n = rng.randrange(2, 15)
        values = run("resistor_network", resistance_ohm=[r] * n)["values"]
        assert values["series_ohm"] == n * r
        assert values["parallel_ohm"] == pytest.approx(r / n)
        tau = r * c
        response = run("rc_step", resistance_ohm=r, capacitance_f=c, step_v=voltage, time_s=[0, tau * math.log(2)])
        assert response["values"]["final_sample_v"] == pytest.approx(voltage / 2)
        length, percent, delta = rng.randrange(1, 100), rng.randrange(-10, 11), rng.randrange(1, 10)
        expansion = run(
            "linear_expansion", length_m=length, coefficient_per_k=percent / 10000, temperature_change_k=delta
        )
        assert expansion["values"]["final_length_m"] == pytest.approx(length * (10000 + percent * delta) / 10000)
    early = run("rc_step", resistance_ohm=1e150, capacitance_f=1e150, step_v=1e300, time_s=[1e-100])
    assert math.isclose(early["values"]["final_sample_v"], 1e-100, rel_tol=1e-14)
    with pytest.raises(ValueError, match="strain"):
        run("linear_expansion", length_m=1e300, coefficient_per_k=1e-200, temperature_change_k=1e-200)
    with pytest.raises(ValueError, match="parallel resistance"):
        run("resistor_network", resistance_ohm=[math.ulp(0.0)] * 3)


def test_rc_with_subnormal_time_constant_uses_unrounded_rc_in_the_exponential():
    capacitance, resistance, time = 2e-162, 3e-162, math.ulp(0.0)
    with localcontext() as ctx:
        ctx.prec = 60
        expected = float(1 - (-Decimal(time) / (Decimal(resistance) * Decimal(capacitance))).exp())
    result = run("rc_step", resistance_ohm=resistance, capacitance_f=capacitance, step_v=1, time_s=[time])
    assert math.isclose(result["values"]["final_sample_v"], expected, rel_tol=1e-15)


def test_thermal_flux_does_not_conflate_underflow_with_equilibrium():
    with pytest.raises(ValueError, match="heat rate or heat flux"):
        run("thermal_layers", thickness_m=[1e-100], conductivity_w_mk=[1e-50], area_m2=1e-300, left_k=1e-100, right_k=0)


@pytest.mark.parametrize("exponent", [1, 1020])
def test_maximum_degree_near_root_matches_decimal_oracle_with_bounded_cost(exponent):
    rng = random.Random(4031)
    linear = math.ldexp(1.0, exponent)
    root = 1 / linear
    points = [root * (1 + 0.7e-8 * rng.random()) for _ in range(256)]
    coefficients = [-1.0, linear] + [math.ldexp(rng.uniform(-1, 1), -900) for _ in range(126)]
    started = time.process_time()
    output = run("polynomial", coefficients=coefficients, points=points)["plot"]["y"]
    elapsed = time.process_time() - started
    with localcontext() as ctx:
        ctx.prec = 130
        decimal_coefficients = list(map(Decimal, coefficients))
        expected = []
        for point in sorted(points):
            x = Decimal(point)
            value = decimal_coefficients[-1]
            for coefficient in reversed(decimal_coefficients[:-1]):
                value = value * x + coefficient
            expected.append(float(value))
    assert output == expected
    # Process CPU time excludes CI scheduling and other concurrently executing
    # suites. Generic rational GCDs at this degree took over five CPU seconds.
    assert elapsed < 3.0
