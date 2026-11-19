"""Independent JCGM 100/101 regression checks for connected measurement propagation.

The numerical oracles are JCGM 100:2008 Annex H.2 and JCGM 101:2008
sections 7.2, 7.7 and 9.2.2. Tests marked "regression" expose behavior
that was wrong in the implementation merged by PR #626.
"""

from __future__ import annotations

import asyncio
import math
import statistics
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from backend.domain.node_states import PluginStateSnapshot
from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import (
    Field, Operation, fingerprint, readable_result, run_operation,
    validated_inputs_with_units,
)
from graphlink_plugins.scientific.execution import execute_in_process
from graphlink_plugins.scientific.propagation import (
    _budget, _mc_trial_count, connected_measurement,
)
from graphlink_plugins.scientific.workbench import _verify_sources

_NORMAL_95 = 1.959963984540054


def _op(name: str, fields: tuple[str, ...],
        formula: Callable[[dict[str, float]], float]) -> Operation:
    return Operation(
        name, name, "",
        tuple(Field(field, field, "number") for field in fields),
        {field: 0.0 for field in fields},
        lambda x: {"summary": "Calculated", "values": {"y": formula(x)}},
    )


def _item(value: float, sigma: float, origin: str, unit: str = "") -> dict[str, Any]:
    return {
        "value": value, "standardUncertainty": sigma,
        "coverageFactor": _NORMAL_95, "coverageProbability": 0.95,
        "unit": unit, "components": {origin: sigma},
    }


def test_zero_standard_uncertainty_does_not_zero_a_nonzero_sensitivity() -> None:
    """Regression: PR #626 returned c_x=0 whenever u(x)=0, even for y=2*x."""
    op = _op("double_exact_input", ("x",), lambda x: 2 * x["x"])
    result = run_operation(
        "audit", op, {"x": 0.0},
        uncertainty_inputs={"x": _item(0.0, 0.0, "a" * 64)},
    )
    output = result["uncertainty"]["outputs"]["y"]
    assert output["standardUncertainty"] == 0.0
    assert output["budget"][0]["sensitivity"] == pytest.approx(2.0, rel=1e-10)
    assert output["budget"][0]["signedContribution"] == 0.0
    assert readable_result({"version": 1, "latest": result}, "audit")


def test_h2_connected_resistance_budget_matches_jcgm100_sensitivities() -> None:
    """Annex H.2: five paired observations, shared roots encode measured covariance."""
    observations = {
        "V": [5.007, 4.994, 5.005, 4.990, 4.999],
        "I": [19.663, 19.639, 19.640, 19.685, 19.678],
        "phi": [1.0456, 1.0438, 1.0468, 1.0428, 1.0433],
    }
    n = 5
    means = {key: statistics.mean(rows) for key, rows in observations.items()}
    connected: dict[str, dict[str, Any]] = {}
    for name, rows in observations.items():
        # u(mean)^2 = sum((x_j - mean)^2) / (n*(n-1)). The same five
        # source roots reproduce the sample covariances of all three means.
        components = {
            fingerprint(["JCGM100-H2-observation", j]):
                (value - means[name]) / math.sqrt(n * (n - 1))
            for j, value in enumerate(rows)
        }
        connected[name] = {
            "value": means[name], "standardUncertainty": math.hypot(*components.values()),
            "coverageFactor": _NORMAL_95, "coverageProbability": 0.95,
            "unit": {"V": "V", "I": "mA", "phi": "rad"}[name],
            "components": components,
        }
    op = Operation(
        "h2_resistance", "H.2 resistance", "",
        (Field("V", "Voltage", "number", "V"),
         Field("I", "Current", "number", "mA"),
         Field("phi", "Phase", "number", "rad")),
        means,
        lambda x: {"summary": "H.2", "values": {
            "resistance_ohm": 1000 * x["V"] / x["I"] * math.cos(x["phi"]),
        }},
    )
    output = run_operation(
        "audit", op, means, uncertainty_inputs=connected,
    )["uncertainty"]["outputs"]["resistance_ohm"]
    slopes = {row["input"]: row["sensitivity"] for row in output["budget"]
              if row["kind"] == "input"}
    assert slopes["V"] == pytest.approx(
        1000 * math.cos(means["phi"]) / means["I"], rel=1e-7)
    assert slopes["I"] == pytest.approx(
        -1000 * means["V"] * math.cos(means["phi"]) / means["I"] ** 2, rel=1e-7)
    assert slopes["phi"] == pytest.approx(
        -1000 * means["V"] * math.sin(means["phi"]) / means["I"], rel=1e-7)
    assert output["value"] == pytest.approx(127.732, abs=0.001)
    assert output["standardUncertainty"] == pytest.approx(0.071, abs=0.001)
    assert output["unit"] == "ohm"
    assert len([row for row in output["budget"] if row["kind"] == "covariance"]) == 3


def test_correlated_lineage_uses_compensated_sum_for_large_cancellation() -> None:
    """Regression: 1e16 + 1 - 1e16 was accumulated as 0 instead of 1."""
    root = "b" * 64
    attached = {
        "a": _item(1.0, 1e16, root),
        "b": _item(1.0, 1.0, root),
        "c": {**_item(1.0, 1e16, root), "components": {root: -1e16}},
    }
    budget, signed = _budget({"a": 1.0, "b": 1.0, "c": 1.0}, attached)
    assert signed[root] == 1.0
    assert math.hypot(*signed.values()) == 1.0
    assert len([row for row in budget if row["kind"] == "covariance"]) == 3


def test_mc_trial_counts_resolve_requested_coverage_tails() -> None:
    """Regression: fixed 16,384 draws misses the JCGM 101 7.2.2 guideline."""
    assert _mc_trial_count(0.95) == 200_000
    assert _mc_trial_count(0.99) == 1_000_000
    with pytest.raises(ValueError, match="more than 1,000,000"):
        _mc_trial_count(0.999)


def test_mc_seed_is_reproducible_and_coverage_matches_jcgm101_table_2() -> None:
    """Four independent N(0,1) sums: JCGM 101 Table 2 has u=2, 95% ±3.92."""
    keys = ("x1", "x2", "x3", "x4")
    op = _op("jcgm101_repeatable_sum", keys, lambda x: math.fsum(x.values()))
    connected = {key: _item(0.0, 1.0, fingerprint(["mc-table2", key]))
                 for key in keys}
    inputs = {key: 0.0 for key in keys}
    first = run_operation("audit", op, inputs, uncertainty_inputs=connected,
                          uncertainty_method="monte_carlo")
    second = run_operation("audit", op, inputs, uncertainty_inputs=connected,
                           uncertainty_method="monte_carlo")
    a = first["uncertainty"]["outputs"]["y"]
    b = second["uncertainty"]["outputs"]["y"]
    assert a == b
    assert a["monteCarloTrials"] == 200_000
    assert a["standardUncertainty"] == pytest.approx(2.0, abs=0.04)
    assert a["interval"] == pytest.approx([-3.92, 3.92], abs=0.12)
    assert first["provenance"]["resultHash"] == second["provenance"]["resultHash"]


def test_skewed_monte_carlo_output_cannot_be_reused_as_gaussian_without_pdf() -> None:
    """Regression: x² at x=0 has mean 1, not nominal 0, and no Gaussian output PDF."""
    op = _op("square_gaussian", ("x",), lambda x: x["x"] ** 2)
    result = run_operation(
        "audit", op, {"x": 0.0},
        uncertainty_inputs={"x": _item(0.0, 1.0, "c" * 64)},
    )
    output = result["uncertainty"]["outputs"]["y"]
    assert output["method"] == "monte_carlo"
    assert output["value"] == 0.0
    assert output["monteCarloMean"] == pytest.approx(1.0, abs=0.04)
    assert output["interval"][0] >= 0
    with pytest.raises(ValueError, match="joint output distribution"):
        connected_measurement(result, ["values", "y"], "source-node")


def test_source_verification_rejects_zero_substituted_for_tiny_quantity() -> None:
    """Regression: the former absolute 1e-14 tolerance accepted zero for 1e-15 m."""
    source_op = Operation(
        "tiny_length", "Tiny length", "",
        (Field("x", "Measured length", "number", "mm"),), {"x": 1e-12},
        lambda x: {"summary": "A tiny length", "values": {
            "length_mm": x["x"], "length_standard_uncertainty_mm": 1e-14,
        }},
    )
    source = run_operation("core", source_op, {"x": 1e-12})
    key = "d" * 32
    target = SimpleNamespace(id="target")
    document = SimpleNamespace(
        nodes={
            "parent": SimpleNamespace(
                kind="science_core.workbench",
                state=PluginStateSnapshot.from_data(
                    {"version": 1, "nodeKey": key, "latest": source}),
            ),
            "target": target,
        },
        edges={"edge": SimpleNamespace(source="parent", target="target")},
    )
    receipt = source["provenance"]
    ref = {
        "nodeId": "parent", "sourceKey": key,
        "resultHash": receipt["resultHash"], "receiptHash": receipt["receiptHash"],
        "selector": ["values", "length_mm"],
    }
    target_op = Operation(
        "take_length", "Take length", "",
        (Field("length", "Length", "number", "m"),), {"length": 1e-15},
        lambda x: {"summary": "Length", "values": {"distance_m": x["length"]}},
    )
    with pytest.raises(ValueError, match="differs"):
        _verify_sources(document, target, {"length": 0.0},
                        {"length": ref}, lambda _: True, {},
                        operation=target_op)
    # A relative 1e-13 tolerance also let a deliberately modified value
    # through; a two-ULP allowance accepts rounding, not changed measurements.
    with pytest.raises(ValueError, match="differs"):
        _verify_sources(document, target, {"length": 1e-15 * (1 + 5e-14)},
                        {"length": ref}, lambda _: True, {},
                        operation=target_op)
    attached: dict[str, Any] = {}
    _verify_sources(document, target, {"length": 1e-15},
                    {"length": ref}, lambda _: True, attached,
                    operation=target_op)
    assert attached["length"]["value"] == pytest.approx(1e-15)
    assert attached["length"]["sourceUnit"] == "mm"
    assert attached["length"]["unit"] == "m"
    assert attached["length"]["standardUncertainty"] == pytest.approx(1e-17)
    assert attached["length"]["coverageFactor"] == pytest.approx(_NORMAL_95, rel=1e-12)


def test_huge_uncertainty_fails_explicitly_not_as_nan_or_infinity() -> None:
    op = _op("huge_uncertainty", ("x",), lambda x: x["x"])
    with pytest.raises(ValueError, match="cannot propagate|finite|numerical range"):
        run_operation("audit", op, {"x": 0.0},
                      uncertainty_inputs={"x": _item(0.0, 1e308, "e" * 64)})


def test_exact_saved_result_without_uncertainty_remains_readable() -> None:
    op = _op("exact_saved", ("x",), lambda x: x["x"] + 1.0)
    legacy = run_operation("audit", op, {"x": 2.0})
    assert "uncertainty" not in legacy
    assert readable_result({"version": 1, "latest": legacy}, "audit")
    assert readable_result({"version": 1, "nodeKey": "f" * 32,
                            "latest": legacy}, "audit")


def test_unsupported_discrete_inputs_and_mixed_coverage_are_explicit() -> None:
    count_op = Operation(
        "count", "Count", "", (Field("n", "Count", "integer"),), {"n": 3},
        lambda x: {"summary": "Count", "values": {"number": x["n"]}},
    )
    with pytest.raises(ValueError, match="discrete or non-scalar"):
        run_operation("audit", count_op, {"n": 3},
                      uncertainty_inputs={"n": _item(3.0, 0.25, "a" * 64)})
    op = _op("mix_coverage", ("a", "b"), lambda x: x["a"] + x["b"])
    a = _item(1.0, 0.1, "a" * 64)
    b = {**_item(2.0, 0.2, "b" * 64), "coverageProbability": 0.99}
    with pytest.raises(ValueError, match="different coverage probabilities"):
        run_operation("audit", op, {"a": 1.0, "b": 2.0},
                      uncertainty_inputs={"a": a, "b": b})


def test_spawned_worker_carries_unit_aware_sidecar_and_validates_receipt() -> None:
    """Execution boundary: detached uncertainty and units reach the worker on Windows."""
    op = next(op for op in operations("core") if op.id == "convert_unit")
    inputs, input_units = validated_inputs_with_units(
        op, {"value": 5.0, "from_unit": "mm", "to_unit": "m"})

    async def calculate() -> dict[str, Any]:
        return await execute_in_process(
            "core", op, inputs, None, timeout_s=20.0,
            cancel_event=asyncio.Event(), input_units=input_units,
            uncertainty_inputs={"value": _item(5.0, 0.2, "e" * 64, "mm")},
        )

    result = asyncio.run(calculate())
    measured = result["uncertainty"]["outputs"]["value (m)"]
    assert measured["value"] == pytest.approx(0.005)
    assert measured["unit"] == "m"
    assert measured["standardUncertainty"] == pytest.approx(0.0002)
    assert result["provenance"]["inputUnits"] == input_units
    assert readable_result({"version": 1, "latest": result}, "core")
