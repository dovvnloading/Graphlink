"""Typed result-view validation, persistence and biological operation regressions."""

import copy
import json

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import (
    VERSION, check_artifact, fingerprint, readable_result, run_operation,
)
from graphlink_plugins.scientific.result_views import check_result_views


def _run(pack, op_id, **inputs):
    operation = next(op for op in operations(pack) if op.id == op_id)
    return run_operation(pack, operation, inputs)


def _plate(size=96, wells=None):
    return {"type": "plate_map", "title": "Assay", "format": size, "unit": "RFU",
            "wells": wells if wells is not None else [{"well": "A1", "value": 0.0}]}


def _survival():
    return {"type": "survival_curve", "title": "Survival", "xLabel": "Time (days)",
            "groups": [
                {"label": "Control", "times": [0, 1, 2], "survival": [1, .5, .5], "censored": [2]},
                {"label": "Treatment", "times": [0, 1, 3], "survival": [1, 1, .75], "censored": [1]},
                {"label": "Reference", "times": [0, 2], "survival": [1, .75]},
            ]}


def test_typed_views_accept_both_plate_sizes_and_three_survival_groups():
    for view in [_plate(), _plate(384, [{"well": "P24", "value": "control"}]), _survival()]:
        check_result_views([view])
        check_artifact({"summary": "ok", "values": {"n": 1}, "views": [view]})
    assert check_result_views([_plate(), _survival()]) is None


@pytest.mark.parametrize("change", [
    {"format": 48},
    {"format": True},
    {"wells": [{"well": "A13", "value": 1}]},
    {"wells": [{"well": "P1", "value": 1}]},
    {"wells": [{"well": "A1", "value": 1}, {"well": "A1", "value": 2}]},
    {"wells": [{"well": "A1", "value": float("nan")}]},
    {"wells": [{"well": "A1", "value": True}]},
    {"wells": [{"well": "A1", "value": "x" * 129}]},
    {"wells": [{"well": "A1", "value": [1]}]},
    {"wells": [{"well": "A1"}]},
    {"wells": [{"well": "A1", "value": 1, "url": "javascript:alert(1)"}]},
    {"wells": [{"well": "A1", "value": 1}] * 97},
])
def test_plate_views_fail_closed(change):
    view = {**_plate(), **change}
    with pytest.raises(ValueError):
        check_result_views([view])


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(xLabel=""),
    lambda v: v["groups"][1].update(label="Control"),
    lambda v: v["groups"][0].update(times=[0, -1, 2]),
    lambda v: v["groups"][0].update(times=[0, 2, 1]),
    lambda v: v["groups"][0].update(survival=[1, .5, .7]),
    lambda v: v["groups"][0].update(survival=[1, 1.1, .5]),
    lambda v: v["groups"][0].update(censored=[5]),
    lambda v: v["groups"][0].update(censored=[2, 2]),
    lambda v: v["groups"][0].update(survival=[1, .5]),
    lambda v: v["groups"].append({"label": "Other", "times": [1], "survival": [.5]}),
    lambda v: v.update(groups=v["groups"] * 5),
])
def test_survival_views_reject_invalid_shape_probability_or_censor_times(mutate):
    view = _survival()
    mutate(view)
    with pytest.raises(ValueError):
        check_result_views([view])


def test_view_limits_and_legacy_outputs():
    with pytest.raises(ValueError, match="four"):
        check_result_views([_plate()] * 5)
    with pytest.raises(ValueError, match="4,096"):
        check_result_views([{
            **_survival(), "groups": [
                {"label": "A", "times": list(range(2050)),
                 "survival": [1.0] * 2050},
                {"label": "B", "times": list(range(2050)),
                 "survival": [1.0] * 2050},
            ],
        }])
    check_artifact({"summary": "Legacy result", "values": {"n": 1},
                    "plot": {"title": "P", "xLabel": "x", "yLabel": "y",
                             "x": [0], "y": [1]}})


def test_microplate_operation_96_and_384_preserves_zeros_categories_and_empty_wells():
    small = _run("cell_culture", "microplate_map", plate_format="96",
                 wells_json='{"B2":0,"A1":"control","H12":null,"C3":1.25}',
                 measurement_label="Signal (RFU)")
    view = small["views"][0]
    assert [cell["well"] for cell in view["wells"]] == ["A1", "B2", "C3", "H12"]
    assert view["wells"][1]["value"] == 0
    assert view["wells"][-1]["value"] is None
    assert small["values"]["recorded_wells"] == 3
    assert small["values"]["empty_wells"] == 93
    assert small["table"]["rows"][0] == ["A1", "control"]
    assert readable_result({"version": 1, "latest": small}, "cell_culture")

    readings = {f"{chr(65 + row)}{col}": row * 24 + col
                for row in range(16) for col in range(1, 25)}
    large = _run("cell_culture", "microplate_map", plate_format="384",
                 wells_json=json.dumps(readings), measurement_label="Absorbance")
    assert len(large["views"][0]["wells"]) == 384
    assert large["views"][0]["wells"][-1] == {"well": "P24", "value": 384}
    assert large["values"]["empty_wells"] == 0
    assert readable_result({"version": 1, "latest": large}, "cell_culture")


@pytest.mark.parametrize("raw", [
    '{"A1":1,"A1":2}', '{"A13":1}', '{"P1":0}', '{"A1":NaN}',
    '{"A1":true}', '{"A1":[2]}', '{"A1":1e999}', '[1,2]', '{"A1":""}',
])
def test_microplate_operation_rejects_bad_well_data(raw):
    with pytest.raises(ValueError):
        _run("cell_culture", "microplate_map", plate_format="96",
             wells_json=raw, measurement_label="RFU")


def test_two_group_survival_view_displays_both_groups_and_survives_a_receipt_roundtrip():
    result = _run("biology", "kaplan_meier_logrank",
                  time_a=[2, 4, 5, 7, 9], event_a=[1, 1, 0, 1, 1],
                  time_b=[6, 8, 11, 13, 15], event_b=[1, 0, 1, 1, 0], time_unit="days")
    view, = result["views"]
    assert view["type"] == "survival_curve"
    assert [group["label"] for group in view["groups"]] == ["A", "B"]
    assert view["groups"][0] == {
        "label": "A", "times": [0, 2, 4, 5, 7, 9],
        "survival": pytest.approx([1, .8, .6, .6, .3, 0]), "censored": [5],
    }
    assert view["groups"][1]["times"] == [0, 6, 8, 11, 13, 15]
    assert view["groups"][1]["censored"] == [8, 15]
    assert result["plot"]["y"][0] == 1  # Existing single-series contract preserved.
    assert readable_result({"version": 1, "latest": result}, "biology")
    tampered = copy.deepcopy(result)
    tampered["views"][0]["groups"][1]["survival"][-1] = .1
    assert not readable_result({"version": 1, "latest": tampered}, "biology")


def test_legacy_two_group_receipt_without_views_remains_readable():
    operation = next(op for op in operations("biology") if op.id == "kaplan_meier_logrank")
    result = run_operation("biology", operation, operation.example)
    result.pop("views")
    receipt = result["provenance"]
    output = {key: result[key] for key in ("summary", "values", "table", "plot")}
    receipt["resultHash"] = fingerprint(["biology", VERSION, operation.id, receipt["inputHash"], output])
    receipt["receiptHash"] = fingerprint({key: value for key, value in receipt.items() if key != "receiptHash"})
    assert readable_result({"version": 1, "latest": result}, "biology")


def test_zero_time_events_and_tied_censoring_remain_step_curve_valid():
    result = _run("biology", "kaplan_meier_logrank",
                  time_a=[0, 0, 1], event_a=[1, 0, 0],
                  time_b=[0, 2], event_b=[1, 0], time_unit="days")
    view = result["views"][0]
    assert view["groups"][0]["times"] == [0, 0, 1]
    assert view["groups"][0]["survival"] == pytest.approx([1, 2/3, 2/3])
    assert view["groups"][0]["censored"] == [0, 1]
    assert readable_result({"version": 1, "latest": result}, "biology")
