"""Connected GUM uncertainty: JCGM 100 Annex H and JCGM 101 Table 2 oracles."""

from __future__ import annotations

import copy
import json
import math
from types import SimpleNamespace

import pytest

from backend.domain.node_states import PluginStateSnapshot
from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import (
    Field, Operation, calculate_result, fingerprint, readable_result, run_operation,
    validated_inputs_with_units,
)
from graphlink_plugins.scientific.propagation import connected_measurement
from graphlink_plugins.scientific.workbench import _verify_sources


def _op(id, names, fn):
    return Operation(id, id, "", tuple(Field(k, k, "number") for k in names),
                     {k: 0 for k in names},
                     lambda inp: {"summary": "Scalar model", "values": {"y": fn(inp)}})


def _measurement(x=2.0, u=0.1):
    op = next(op for op in operations("uncertainty") if op.id == "propagate_uncertainty")
    return run_operation("uncertainty", op, {
        "expression": "x", "measurements": json.dumps({"x": {"value": x, "uncertainty": u}}),
        "correlations": "[]", "coverage_probability": 0.95,
    })


def _connected(result, source_key="a" * 32):
    """Exercise real receipt/edge verification rather than trusting client metadata."""
    parent = SimpleNamespace(
        kind="science_uncertainty.workbench",
        state=PluginStateSnapshot.from_data({"version": 1, "nodeKey": source_key, "latest": result}),
    )
    target = SimpleNamespace(id="target")
    document = SimpleNamespace(
        nodes={"parent": parent, "target": target},
        edges={"edge": SimpleNamespace(source="parent", target="target")},
    )
    receipt = result["provenance"]
    reference = {"nodeId": "obsolete-session-id", "sourceKey": source_key,
                 "resultHash": receipt["resultHash"], "receiptHash": receipt["receiptHash"],
                 "selector": ["values", "estimate"]}
    return document, target, reference


def test_ja100_h1_end_gauge_follows_a_connection_with_coverage_and_units():
    """JCGM 100:2008, H.1: 50.000838 mm, first-order u ~32 nm."""
    source_op = next(op for op in operations("uncertainty") if op.id == "propagate_uncertainty")
    source = run_operation("uncertainty", source_op, {
        "expression": "lS+d-lS*(deltaAlpha*theta+alphaS*deltaTheta)",
        "measurements": json.dumps({
            "lS": {"value": 50.000623, "uncertainty": 25e-6, "degrees_of_freedom": 18},
            "d": {"value": 0.000215, "uncertainty": 9.7e-6, "degrees_of_freedom": 25.6},
            "alphaS": {"value": 11.5e-6, "uncertainty": 1.2e-6},
            "theta": {"value": -0.1, "uncertainty": 0.41},
            "deltaAlpha": {"value": 0, "uncertainty": 1e-6 / math.sqrt(3), "degrees_of_freedom": 50},
            "deltaTheta": {"value": 0, "uncertainty": 0.05 / math.sqrt(3), "degrees_of_freedom": 2},
        }), "correlations": "[]", "coverage_probability": 0.95,
    })
    values = source["values"]
    assert values["estimate"] == pytest.approx(50.000838, abs=1e-10)
    assert round(values["combined_standard_uncertainty"] * 1e6) == 32
    document, target, ref = _connected(source)
    attached = {}
    inputs = {"value": values["estimate"], "from_unit": "mm", "to_unit": "m"}
    sources = _verify_sources(document, target, inputs, {"value": ref}, lambda _: True, attached)
    assert sources["value"]["sourceKey"] == "a" * 32
    assert attached["value"]["coverageFactor"] == values["coverage_factor"]
    assert attached["value"]["standardUncertainty"] == values["combined_standard_uncertainty"]

    convert = next(op for op in operations("core") if op.id == "convert_unit")
    output = run_operation("core", convert, inputs, sources, uncertainty_inputs=attached)
    measured = output["uncertainty"]["outputs"]["value (m)"]
    assert measured["value"] == pytest.approx(0.050000838, abs=1e-12)
    assert measured["standardUncertainty"] == pytest.approx(values["combined_standard_uncertainty"] / 1000, rel=2e-8)
    assert measured["unit"] == "m"
    assert measured["coverageFactor"] == values["coverage_factor"]
    assert [row["input"] for row in measured["budget"]] == ["value"]
    assert output["uncertainty"]["inputs"]["value"]["value"] == values["estimate"]
    assert readable_result({"version": 1, "latest": output}, "core")
    damaged = copy.deepcopy(output)
    damaged["uncertainty"]["outputs"]["value (m)"]["standardUncertainty"] *= 2
    assert not readable_result({"version": 1, "latest": damaged}, "core")


def test_shared_source_reuse_is_fully_correlated_even_when_node_id_changes():
    source = _measurement(x=5.0, u=0.25)
    document, target, ref = _connected(source)
    inputs = {"a": 5.0, "b": 5.0}
    selected = {}
    _verify_sources(document, target, inputs, {"a": ref, "b": ref}, lambda _: True, selected)
    assert selected["a"]["components"] == selected["b"]["components"]

    additive = _op("sum", ("a", "b"), lambda p: p["a"] + p["b"])
    subtractive = _op("difference", ("a", "b"), lambda p: p["a"] - p["b"])
    plus = run_operation("test", additive, inputs, uncertainty_inputs=selected)["uncertainty"]["outputs"]["y"]
    minus = run_operation("test", subtractive, inputs, uncertainty_inputs=selected)["uncertainty"]["outputs"]["y"]
    assert plus["standardUncertainty"] == pytest.approx(0.5, abs=2e-9)
    assert minus["standardUncertainty"] == pytest.approx(0, abs=2e-9)
    assert {row["input"] for row in plus["budget"]} == {"a", "b", "a × b"}
    assert next(row["variance"] for row in plus["budget"] if row["kind"] == "covariance") == pytest.approx(0.125)
    assert next(row["variance"] for row in minus["budget"] if row["kind"] == "covariance") == pytest.approx(-0.125)

    # Two downstream branches still refer to the same physical source, not
    # two artificially independent standard deviations.
    first = run_operation("test", _op("double", ("a",), lambda p: 2 * p["a"]),
                          {"a": 5.0}, uncertainty_inputs={"a": selected["a"]})
    second = run_operation("test", _op("identity", ("b",), lambda p: p["b"]),
                           {"b": 5.0}, uncertainty_inputs={"b": selected["b"]})
    a = connected_measurement(first, ["values", "y"], "branch1")
    b = connected_measurement(second, ["values", "y"], "branch2")
    chained = run_operation("test", additive, {"a": 10.0, "b": 5.0},
                            uncertainty_inputs={"a": a, "b": b})
    assert chained["uncertainty"]["outputs"]["y"]["standardUncertainty"] == pytest.approx(.75, abs=3e-9)


def test_jcgm101_gaussian_sum_table2_first_order_and_requested_monte_carlo():
    """JCGM 101:2008, §9.2.2, Table 2: four N(0, 1), u(y)=2, 95% ±3.92."""
    fields = ("x1", "x2", "x3", "x4")
    op = _op("jcgm101_gaussian_sum", fields, lambda p: sum(p.values()))
    inputs = {key: 0.0 for key in fields}
    connected = {key: {"value": 0.0, "standardUncertainty": 1.0,
                       "coverageFactor": 1.959963984540054, "unit": "",
                       "components": {fingerprint(["jcgm101", key]): 1.0}}
                 for key in fields}
    first = run_operation("test", op, inputs, uncertainty_inputs=connected)
    gum = first["uncertainty"]["outputs"]["y"]
    assert gum["method"] == "gum"
    assert gum["value"] == 0
    assert gum["standardUncertainty"] == pytest.approx(2, abs=1e-9)
    assert gum["interval"] == pytest.approx([-3.919927969, 3.919927969], abs=1e-7)
    assert len(gum["budget"]) == 4

    mc = run_operation("test", op, inputs, uncertainty_inputs=connected,
                       uncertainty_method="monte_carlo")["uncertainty"]["outputs"]["y"]
    assert mc["method"] == "monte_carlo"
    assert mc["monteCarloTrials"] == 200_000
    assert mc["standardUncertainty"] == pytest.approx(2, abs=.07)
    assert mc["interval"] == pytest.approx([-3.92, 3.92], abs=.18)
    assert mc["monteCarloMean"] == pytest.approx(0, abs=.08)
    assert len(mc["budget"]) == 4


def test_strong_nonlinearity_auto_selects_monte_carlo_and_gum_is_opt_in():
    op = _op("square", ("x",), lambda p: p["x"] ** 2)
    item = {"value": 0.0, "standardUncertainty": 1.0, "coverageFactor": 1.96,
            "unit": "", "components": {"f" * 64: 1.0}}
    auto = run_operation("test", op, {"x": 0.0}, uncertainty_inputs={"x": item})
    out = auto["uncertainty"]["outputs"]["y"]
    assert out["method"] == "monte_carlo"
    assert out["monteCarloMean"] == pytest.approx(1, abs=.09)
    assert out["standardUncertainty"] == pytest.approx(math.sqrt(2), abs=.12)
    assert out["interval"][0] >= 0
    assert out["interval"][1] == pytest.approx(5.02, abs=.45)
    first = run_operation("test", op, {"x": 0.0}, uncertainty_inputs={"x": item},
                          uncertainty_method="gum")
    assert first["uncertainty"]["outputs"]["y"]["standardUncertainty"] == 0


def test_native_non_gum_standard_errors_and_non_gaussian_intervals_fail_explicitly():
    fake = {"values": {"coefficient_0": 4, "standard_error_0": .5,
                       "r": 0.8, "ci95_lower": .65, "ci95_upper": .9,
                       "estimate": 3, "bca_lower": 1, "bca_upper": 6},
            "provenance": {"resultHash": "f" * 64}}
    coef = connected_measurement(fake, ["values", "coefficient_0"], "a" * 32)
    assert coef["standardUncertainty"] == 0.5
    for name in ("r", "estimate"):
        with pytest.raises(ValueError, match="interval|unsupported"):
            connected_measurement(fake, ["values", name], "a" * 32)

    x = {"value": 1.0, "standardUncertainty": 0.1, "coverageFactor": 1.0,
         "unit": "m", "components": {"a" * 64: .1}}
    measured_op = Operation("needs_seconds", "needs_seconds", "", (Field("x", "x", "number", "s"),),
                            {"x": 1}, lambda inp: {"summary": "q", "values": {"y": inp["x"]}})
    with pytest.raises(ValueError, match="units"):
        run_operation("test", measured_op, {"x": 1}, uncertainty_inputs={"x": x})

    text_only = Operation("text_only", "text_only", "", (Field("x", "x", "number"),),
                          {"x": 1}, lambda inp: {"summary": "text", "values": {"label": "ok"}})
    with pytest.raises(ValueError, match="cannot propagate|no numeric"):
        run_operation("test", text_only, {"x": 1}, uncertainty_inputs={"x": {**x, "unit": ""}})


def test_fitted_coefficients_require_reported_covariance_and_intrinsic_u_is_not_doubled():
    fitted = {"values": {"coefficient_0": 1.0, "coefficient_1": 2.0,
                         "standard_error_0": 0.1, "standard_error_1": 0.2,
                         "age_ma": 12.0, "age_standard_uncertainty_ma": 0.3},
              "provenance": {"resultHash": "a" * 64}}
    a = connected_measurement(fitted, ["values", "coefficient_0"], "fitted-model")
    b = connected_measurement(fitted, ["values", "coefficient_1"], "fitted-model")
    assert a["sourceArtifact"] == b["sourceArtifact"]
    assert connected_measurement(fitted, ["values", "age_ma"], "fitted-model")["unit"] == "Ma"
    with pytest.raises(ValueError, match="mutual covariance"):
        run_operation("test", _op("model_sum", ("a", "b"), lambda p: p["a"] + p["b"]),
                      {"a": 1.0, "b": 2.0}, uncertainty_inputs={"a": a, "b": b})

    intrinsic = Operation("internal_u", "internal_u", "",
                          (Field("x", "x", "number"),), {"x": 1},
                          lambda p: {"summary": "internal σ",
                                     "values": {"y": 2 * p["x"], "y_standard_uncertainty": .4}})
    result = run_operation("test", intrinsic, {"x": 1}, uncertainty_inputs={
        "x": {"value": 1., "standardUncertainty": .1, "coverageFactor": 1.96,
              "unit": "", "components": {"f" * 64: .1}},
    })
    assert "covariance" in result["uncertainty"]["unsupportedOutputs"]["y"]
    with pytest.raises(ValueError, match="cannot safely combine"):
        connected_measurement(result, ["values", "y"], "downstream")


def test_exact_inputs_keep_original_receipt_and_source_not_reinterpreted():
    op = next(op for op in operations("core") if op.id == "convert_unit")
    inputs = {"value": 25, "from_unit": "degC", "to_unit": "K"}
    exact = run_operation("core", op, inputs)
    also_exact = run_operation("core", op, inputs, uncertainty_inputs={})
    assert "uncertainty" not in exact and "uncertainty" not in also_exact
    assert exact["values"] == also_exact["values"]
    assert exact["provenance"]["resultHash"] == also_exact["provenance"]["resultHash"]
    assert readable_result({"version": 1, "latest": exact}, "core")
    # A quoted value mismatch is still rejected before any propagation runs.
    document, target, ref = _connected(_measurement(x=5.0))
    with pytest.raises(ValueError, match="differs"):
        _verify_sources(document, target, {"a": 6.0}, {"a": ref}, lambda _: True, {})
    with pytest.raises(ValueError, match="changed"):
        _verify_sources(document, target, {"a": 5.0}, {"a": {**ref, "resultHash": "0" * 64}},
                        lambda _: True, {})

def test_unit_aware_connected_metric_keeps_converted_value_and_shared_uncertainty_budget():
    """PR #625 + #626: one source's mm, sigma and lineage survive an SI-bound input."""
    source_op = Operation(
        "measured_length", "Measured length", "", (Field("x", "Length", "number", "mm"),),
        {"x": 25}, lambda p: {
            "summary": "Measured length",
            "values": {"length_mm": p["x"], "length_standard_uncertainty_mm": 0.5},
        },
    )
    source = run_operation("uncertainty", source_op, {"x": 25})
    document, target, ref = _connected(source)
    ref["selector"] = ["values", "length_mm"]
    target_op = Operation(
        "scale_length", "Scale length", "", (Field("distance", "Distance", "number", "m"),),
        {"distance": 0.025}, lambda p: {
            "summary": "Scaled length", "values": {"y_m": 2 * p["distance"]},
        },
    )
    inputs, input_units = validated_inputs_with_units(target_op, {"distance": "25 mm"})
    assert inputs == {"distance": pytest.approx(0.025)}
    assert input_units == {"distance": "mm"}
    attached = {}
    sources = _verify_sources(
        document, target, inputs, {"distance": ref}, lambda _: True, attached,
        operation=target_op,
    )
    assert attached["distance"]["value"] == pytest.approx(0.025)
    assert attached["distance"]["sourceValue"] == 25
    assert attached["distance"]["sourceUnit"] == "mm"
    assert attached["distance"]["unit"] == "m"
    assert attached["distance"]["standardUncertainty"] == pytest.approx(0.0005)
    assert list(attached["distance"]["components"].values()) == pytest.approx([0.0005])

    result = calculate_result(
        "core", target_op, inputs, sources, input_units=input_units,
        uncertainty_inputs=attached,
    )
    measured = result["uncertainty"]["outputs"]["y_m"]
    assert measured["value"] == pytest.approx(0.05)
    assert measured["unit"] == "m"
    assert measured["standardUncertainty"] == pytest.approx(0.001, rel=1e-8)
    assert measured["budget"][0]["unit"] == "m"
    assert measured["budget"][0]["sourceUnit"] == "mm"
    assert result["provenance"]["inputUnits"]["distance"] == "mm"
    assert readable_result({"version": 1, "latest": result}, "core")

    with pytest.raises(ValueError, match="differs"):
        _verify_sources(
            document, target, {"distance": 0.03}, {"distance": ref},
            lambda _: True, {}, operation=target_op,
        )
    seconds = Operation(
        "incompatible_seconds", "Seconds", "",
        (Field("distance", "Distance", "number", "s"),), {"distance": 25},
        lambda p: {"summary": "Duration", "values": {"duration_s": p["distance"]}},
    )
    with pytest.raises(ValueError, match="incompatible units"):
        _verify_sources(
            document, target, {"distance": 25}, {"distance": ref}, lambda _: True,
            {}, operation=seconds,
        )

def test_legacy_run_intent_can_omit_propagation_method():
    """The optional method must not silently reject existing plugin wire calls."""
    from graphlink_wire_schema import validate_payload
    from graphlink_plugins.scientific.workbench import RunArgs

    assert validate_payload({
        "node_id": "n0", "operation_id": "convert_unit", "inputs": {"value": 1},
    }, RunArgs) == []
    assert RunArgs(node_id="n0", operation_id="convert_unit", inputs={"value": 1}).uncertainty_method == "auto"
