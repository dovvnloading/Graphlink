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


def _forest(scale="linear"):
    return {
        "type": "forest_plot", "title": "Treatment effects", "xLabel": "Mean difference",
        "scale": scale, "reference": 0 if scale == "linear" else 1,
        "studies": [
            {"label": "Study A", "estimate": -.2 if scale == "linear" else .8,
             "lower": -.6 if scale == "linear" else .6,
             "upper": .3 if scale == "linear" else 1.2, "weight": 2.5},
            {"label": "Study B", "estimate": .4 if scale == "linear" else 1.3,
             "lower": .2 if scale == "linear" else 1.1,
             "upper": .7 if scale == "linear" else 1.6, "weight": 1},
        ],
        "pooled": {"label": "Overall", "estimate": .2 if scale == "linear" else 1.04,
                   "lower": 0 if scale == "linear" else .91,
                   "upper": .5 if scale == "linear" else 1.22},
    }


def _tree(layout="rectangular", mode="phylogram"):
    return {
        "type": "phylogenetic_tree", "title": "Rooted clades",
        "layout": layout, "mode": mode,
        "tree": {"name": "Root", "children": [
            {"name": "Clade A", "length": .4, "support": 98, "children": [
                {"name": "Taxon A", "length": .2},
                {"name": "Taxon B", "length": .1},
            ]},
            {"name": "Taxon C", "length": 1.2, "support": 85},
        ]},
    }


@pytest.mark.parametrize("scale", ["linear", "log"])
def test_forest_views_validate_studies_pooled_confidence_intervals_and_scale(scale):
    view = _forest(scale)
    check_result_views([view])
    check_artifact({"summary": "ok", "values": {"n": 2}, "views": [view]})
    assert check_result_views([_plate(), view, _survival(), _tree()]) is None


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(scale="symlog"),
    lambda v: v.update(reference=float("inf")),
    lambda v: v.update(studies=[]),
    lambda v: v.update(studies=v["studies"] * 33),
    lambda v: v["studies"][1].update(label="Study A"),
    lambda v: v["studies"][0].update(lower=.95),
    lambda v: v["studies"][0].update(upper=-.3),
    lambda v: v["studies"][0].update(estimate=float("nan")),
    lambda v: v["studies"][0].update(weight=-1),
    lambda v: v["studies"][0].update(weight=True),
    lambda v: v["studies"][0].update(href="javascript:alert(1)"),
    lambda v: v.update(pooled={"estimate": 1, "lower": 1}),
    lambda v: v.update(pooled={"estimate": .3, "lower": .4, "upper": .5}),
    lambda v: v.update(studies=[{"label": "A", "estimate": 1, "lower": 0, "upper": 2}]),
    lambda v: v.update(reference=0),
])
def test_log_forest_views_fail_closed_for_invalid_intervals_and_data(mutate):
    view = _forest("log")
    mutate(view)
    with pytest.raises(ValueError):
        check_result_views([view])


@pytest.mark.parametrize("layout", ["rectangular", "circular"])
@pytest.mark.parametrize("mode", ["phylogram", "cladogram"])
def test_phylogenetic_views_validate_layouts_and_branch_annotations(layout, mode):
    view = _tree(layout, mode)
    check_result_views([view])
    check_artifact({"summary": "ok", "values": {}, "views": [view]})
    if mode == "cladogram":
        view["tree"]["children"][0].pop("length")
        check_result_views([view])
        view["tree"]["children"][0]["length"] = None
        with pytest.raises(ValueError):
            check_result_views([view])


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(layout="radial"),
    lambda v: v.update(mode="chronogram"),
    lambda v: v["tree"].update(length=2),
    lambda v: v["tree"].update(support=95),
    lambda v: v["tree"].update(children=[]),
    lambda v: v["tree"]["children"][0].update(length=-.1),
    lambda v: v["tree"]["children"][0].update(length=float("nan")),
    lambda v: v["tree"]["children"][0].update(length=None),
    lambda v: v["tree"]["children"][0].update(support=101),
    lambda v: v["tree"]["children"][0].update(support=True),
    lambda v: v["tree"]["children"][0].update(children=[{"name": "A", "length": 1}]),
    lambda v: v["tree"]["children"][1].update(name="Taxon A"),
    lambda v: v["tree"]["children"][1].pop("length"),
    lambda v: v["tree"]["children"][0].update(children=[
        {"length": 1}, {"name": "A", "length": 2}]),
    lambda v: v["tree"]["children"][0].update(onload="unsafe"),
    lambda v: v["tree"]["children"][0].update(children=[]),
])
def test_phylograms_fail_closed_for_invalid_topology_lengths_and_support(mutate):
    view = _tree()
    mutate(view)
    with pytest.raises(ValueError):
        check_result_views([view])


def test_phylogenetic_view_rejects_cycles_and_oversized_or_depth_bomb_trees():
    root = {"children": [{"name": "A", "length": 1}]}
    root["children"].append(root)
    with pytest.raises(ValueError):
        check_result_views([{**_tree(), "tree": root}])

    def balanced(names):
        if len(names) == 1:
            return {"name": names[0], "length": 1}
        middle = len(names) // 2
        return {"length": 1, "children": [balanced(names[:middle]), balanced(names[middle:])]}

    permitted = _tree()
    permitted["tree"] = {"children": [balanced([f"Taxon {i}" for i in range(32)]),
                                    balanced([f"Taxon {i}" for i in range(32, 64)])]}
    check_result_views([permitted])
    prohibited = copy.deepcopy(permitted)
    prohibited["tree"] = {"children": [balanced([f"Taxon {i}" for i in range(33)]),
                                     balanced([f"Taxon {i}" for i in range(33, 65)])]}
    with pytest.raises(ValueError):
        check_result_views([prohibited])

    long_branch = {"name": "Bottom"}
    for depth in range(34):
        long_branch = {"children": [
            {"name": f"Side {depth}"}, long_branch,
        ]}
    with pytest.raises(ValueError):
        check_result_views([{**_tree(mode="cladogram"), "tree": long_branch}])
