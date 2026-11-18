"""Independent analytic and high-accuracy reference cases for Numerical Methods."""

import copy
import math
import tomllib
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACKS, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation


OPS = {op.id: op for op in operations("numerics")}


def run(name, **updates):
    args = copy.deepcopy(OPS[name].example)
    args.update(updates)
    return run_operation("numerics", OPS[name], args)


def value(name, **updates):
    return run(name, **updates)["values"]


def test_catalog_plugin_manifest_examples_and_receipts():
    assert PACKS["numerics"][0] == "Numerical Methods"
    manifest = Path(__file__).resolve().parents[2] / "plugins" / "science_numerics" / "plugin.toml"
    with manifest.open("rb") as file:
        data = tomllib.load(file)
    assert data["plugin"]["id"] == "science_numerics"
    assert data["plugin"]["version"] == VERSION
    assert data["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert data["persistence"]["passive_node_kinds"] == ["workbench"]
    assert set(OPS) == {"solve_ivp_rk45", "solve_ivp_implicit", "brent_root",
                        "gauss_kronrod_integral", "cubic_spline"}
    for name in OPS:
        result = run(name)
        assert result["provenance"]["packId"] == "numerics"
        assert readable_result({"version": 1, "latest": result}, "numerics")
        broken = copy.deepcopy(result)
        broken["values"][next(iter(broken["values"]))] = "tampered"
        assert not readable_result({"version": 1, "latest": broken}, "numerics")


def test_rk45_exponential_decay_to_stated_tolerance():
    # y'=-0.5*y, y(0)=1, y(8)=exp(-4) exactly.
    v = value("solve_ivp_rk45", end_time=8.0,
              relative_tolerance=1e-9, absolute_tolerance=1e-12)
    assert v["final_y0"] == pytest.approx(math.exp(-4), abs=2e-10)
    assert v["accepted_steps"] > 3
    assert v["rhs_evaluations"] >= 7*v["accepted_steps"]
    assert 0 <= v["max_accepted_error_ratio"] <= 1
    assert v["estimated_max_local_error"] >= 0


def test_rk45_harmonic_oscillator_multiple_periods_and_backward_time():
    # x'=v, v'=-x, x(t)=cos(t), v(t)=-sin(t), at ten periods.
    v = value("solve_ivp_rk45", rhs_expressions='["y1", "-y0"]',
              initial_values=[1.0, 0.0], end_time=20*math.pi,
              relative_tolerance=2e-9, absolute_tolerance=2e-11)
    assert v["final_y0"] == pytest.approx(1, abs=2e-7)
    assert v["final_y1"] == pytest.approx(0, abs=2e-7)
    reverse = value("solve_ivp_rk45", start_time=4, end_time=0,
                    initial_values=[math.exp(-2)], relative_tolerance=1e-9,
                    absolute_tolerance=1e-12)
    assert reverse["final_y0"] == pytest.approx(1, abs=1e-8)


def test_van_der_pol_against_high_accuracy_radau_oracles():
    # SciPy Radau, rtol=2e-13, atol=1e-15 independent oracle at mu=3, t=10.
    expressions = '["y1", "mu*(1-y0^2)*y1-y0"]'
    v = value("solve_ivp_rk45", rhs_expressions=expressions, parameters='{"mu":3}',
              initial_values=[2, 0], end_time=10,
              relative_tolerance=1e-8, absolute_tolerance=1e-10)
    assert v["final_y0"] == pytest.approx(1.7508147160418, abs=2e-6)
    assert v["final_y1"] == pytest.approx(-0.270994439452338, abs=2e-6)

    # mu=1000, t=10 is stiff; a fully implicit integrator must progress.
    stiff = value("solve_ivp_implicit", rhs_expressions=expressions,
                  parameters='{"mu":1000}', initial_values=[2, 0],
                  end_time=10, relative_tolerance=1e-5,
                  absolute_tolerance=1e-8)
    assert stiff["final_y0"] == pytest.approx(1.99331492756978, abs=8e-4)
    assert stiff["final_y1"] == pytest.approx(-0.000670403793877684, abs=8e-6)
    assert stiff["jacobian_evaluations"] > 0
    assert stiff["linear_solves"] > 0
    assert 0 <= stiff["max_accepted_error_ratio"] <= 1


def test_robertson_stiff_chemistry_against_high_accuracy_radau_oracle():
    # Robertson's stiff 3-species benchmark; Radau rtol=2e-13 atol=1e-15
    # at t=10000: [0.10730042853784,4.80016697257274e-7,0.892699091445467].
    rhs = ('["-0.04*y0+1e4*y1*y2",'
           '"0.04*y0-1e4*y1*y2-3e7*y1^2","3e7*y1^2"]')
    args = dict(rhs_expressions=rhs, initial_values=[1, 0, 0], end_time=10000,
                relative_tolerance=1e-5, absolute_tolerance=1e-9)
    v = value("solve_ivp_implicit", **args)
    for i, expected in enumerate((0.10730042853784, 4.80016697257274e-7,
                                  0.892699091445467)):
        assert v[f"final_y{i}"] == pytest.approx(expected, abs=1e-3 if i != 1 else 2e-7)
    assert sum(v[f"final_y{i}"] for i in range(3)) == pytest.approx(1, abs=3e-7)
    assert v["jacobian_evaluations"] > 0
    with pytest.raises(ValueError, match="ODE tolerance not met"):
        value("solve_ivp_rk45", **{**args, "max_steps": 30})


def test_brent_convergence_counts_and_failure_modes():
    v = value("brent_root", relative_tolerance=1e-12, absolute_tolerance=1e-12)
    assert v["root"] == pytest.approx(0.7390851332151607, abs=2e-12)
    assert v["estimated_absolute_error"] <= 1e-12 + 1e-12*abs(v["root"])
    assert abs(v["absolute_residual"]) < 1e-11
    assert 2 < v["function_evaluations"] <= 100
    assert value("brent_root", expression="x-1", lower=1, upper=3)["root"] == 1
    left = value("brent_root", expression="x-1", lower=1, upper=3, max_evaluations=1)
    assert left["root"] == 1 and left["function_evaluations"] == 1
    with pytest.raises(ValueError, match="Root tolerance not met"):
        run("brent_root", expression="x-1", lower=0, upper=3, max_evaluations=1)
    # Convergence on precisely the final allowed evaluation must count as success.
    last = value("brent_root", expression="exp(x)-1000", lower=-1, upper=10,
                 max_evaluations=14)
    assert last["function_evaluations"] == 14
    assert last["estimated_absolute_error"] <= 1e-12 + 1e-12*abs(last["root"])
    with pytest.raises(ValueError, match="opposite endpoint signs"):
        run("brent_root", expression="x^2+1", lower=-1, upper=1)
    with pytest.raises(ValueError, match="tolerance not met"):
        run("brent_root", max_evaluations=2)


def test_adaptive_gauss_kronrod_analytic_and_error_estimates():
    v = value("gauss_kronrod_integral", relative_tolerance=1e-10,
              absolute_tolerance=1e-11)
    assert v["integral"] == pytest.approx(2, abs=1e-10)
    assert v["estimated_absolute_error"] <= 1e-11+1e-10*abs(v["integral"])
    assert v["function_evaluations"] == 15+30*v["subdivisions"]
    p = value("gauss_kronrod_integral", expression="x^4", lower=-1, upper=2,
              relative_tolerance=1e-11, absolute_tolerance=1e-12)
    assert p["integral"] == pytest.approx(33/5, abs=1e-11)
    e = value("gauss_kronrod_integral", expression="exp(-k*x)",
              parameters='{"k":2}', lower=0, upper=3,
              relative_tolerance=1e-10, absolute_tolerance=1e-12)
    assert e["integral"] == pytest.approx((1-math.exp(-6))/2, abs=1e-10)
    reverse = value("gauss_kronrod_integral", lower=math.pi, upper=0)
    assert reverse["integral"] == pytest.approx(-2, abs=1e-10)
    with pytest.raises(ValueError, match="Quadrature tolerance not met"):
        run("gauss_kronrod_integral", expression="exp(-x^2)", lower=-4, upper=4,
            relative_tolerance=1e-12, absolute_tolerance=1e-13, max_evaluations=15)


def test_natural_and_clamped_splines_exact_benchmarks():
    linear = value("cubic_spline", x=[0, 1, 2, 3], y=[1, 3, 5, 7],
                   queries=[0, .25, .5, 1.5, 2.75, 3], boundary="natural")
    assert linear["max_knot_residual"] <= 1e-14
    for q, y, d in run("cubic_spline", x=[0, 1, 2, 3], y=[1, 3, 5, 7],
                       queries=[0, .25, .5, 1.5, 2.75, 3])["table"]["rows"]:
        assert y == pytest.approx(1+2*q, abs=1e-13)
        assert d == pytest.approx(2, abs=1e-12)
    # Exact cubic recovery from clamped endpoints: f=x^3-2x^2+x.
    poly = lambda x: x**3-2*x*x+x
    result = run("cubic_spline", x=[0, 1, 2, 3], y=[poly(i) for i in range(4)],
                 queries=[0, .3, .8, 1.5, 2.7, 3], boundary="clamped",
                 left_slope=1, right_slope=16)
    for q, actual, derivative in result["table"]["rows"]:
        assert actual == pytest.approx(poly(q), abs=1e-11)
        assert derivative == pytest.approx(3*q*q-4*q+1, abs=2e-11)
    assert result["values"]["estimated_roundoff_error"] >= result["values"]["max_knot_residual"]
    assert "unknown" in result["values"]["underlying_function_error"]


@pytest.mark.parametrize("changes,kind", [
    ({"boundary": "periodic"}, "boundary"),
    ({"x": [1, 0]}, "increasing"),
    ({"x": [1, 2]}, "matching"),
    ({"queries": [-1]}, "extrapolation"),
])
def test_spline_rejects_invalid_boundary_or_knots(changes, kind):
    with pytest.raises(ValueError):
        run("cubic_spline", **changes)


@pytest.mark.parametrize("expression", [
    "__import__('os').system('id')", "x.__class__", "x[0]", "x**2",
    "x or 1", "lambda x: x", "sin(x,2)", "2x", "x + (", "x;0",
])
def test_all_scalar_models_reuse_the_safe_parser(expression):
    with pytest.raises(ValueError):
        run("brent_root", expression=expression)
    with pytest.raises(ValueError):
        run("gauss_kronrod_integral", expression=expression)


def test_invalid_ode_models_parameters_limits_and_tolerances():
    with pytest.raises(ValueError):
        run("solve_ivp_rk45", rhs_expressions='["y0.__class__"]')
    with pytest.raises(ValueError):
        run("solve_ivp_implicit", rhs_expressions='["__import__(1)"]')
    with pytest.raises(ValueError, match="Right-hand sides"):
        run("solve_ivp_rk45", rhs_expressions='["y0", "y1"]')
    with pytest.raises(ValueError, match="Parameter"):
        run("solve_ivp_rk45", parameters='{"y0":2}')
    with pytest.raises(ValueError):
        run("solve_ivp_rk45", parameters='{"a":1,"a":2}')
    with pytest.raises(ValueError, match="tolerance"):
        run("solve_ivp_rk45", relative_tolerance=0, absolute_tolerance=0)
    with pytest.raises(ValueError, match="100 machine epsilons"):
        run("solve_ivp_rk45", relative_tolerance=1e-16)
    with pytest.raises(ValueError, match="ODE tolerance not met"):
        run("solve_ivp_rk45", max_steps=1, relative_tolerance=1e-13,
            absolute_tolerance=1e-15)
