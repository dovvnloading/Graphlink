"""Statistical regression oracles, boundary cases and signed-result checks."""

import copy
import math

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import check_artifact, readable_result, run_operation


def _run(name, **inputs):
    operation = next(op for op in operations("epidemiology") if op.id == name)
    return run_operation("epidemiology", operation, inputs)


def test_advanced_operation_catalog_examples_and_receipts():
    advanced = operations("epidemiology")[-4:]
    assert [op.id for op in advanced] == [
        "mantel_haenszel", "meta_analysis", "multi_group_kaplan_meier",
        "cox_proportional_hazards",
    ]
    for op in advanced:
        assert op.methods and op.citations and all(field.unit for field in op.fields)
        result = run_operation("epidemiology", op, op.example)
        check_artifact(result)
        assert result["operationId"] == op.id
        assert readable_result({"version": 1, "latest": result}, "epidemiology")
        if "views" in result:
            tampered = copy.deepcopy(result)
            tampered["views"][0]["title"] = "modified after signing"
            assert not readable_result({"version": 1, "latest": tampered}, "epidemiology")


def test_mantel_haenszel_stratified_or_rbg_interval_and_cmh_oracles():
    # Cross-checked with statsmodels StratifiedTable (without continuity correction).
    raw = {"exposed_events": [12, 8, 15], "exposed_nonevents": [88, 72, 85],
           "unexposed_events": [20, 16, 24], "unexposed_nonevents": [80, 64, 76]}
    result = _run("mantel_haenszel", **raw)
    values = result["values"]
    assert values["common_odds_ratio"] == pytest.approx(.5229007633587787)
    assert values["log_common_odds_ratio_se"] == pytest.approx(.23244072388646614)
    assert values["common_odds_ratio_ci95_lower"] == pytest.approx(.3315624750940107)
    assert values["common_odds_ratio_ci95_upper"] == pytest.approx(.8246566751670763)
    assert values["cmh_chi_square"] == pytest.approx(7.901776135272534)
    assert values["cmh_p_value"] == pytest.approx(.0049386280565036165)
    assert values["corrected_strata"] == 0
    forest, = result["views"]
    assert forest["type"] == "forest_plot" and forest["scale"] == "log"
    assert forest["reference"] == 1 and len(forest["studies"]) == 3
    assert forest["pooled"]["estimate"] == values["common_odds_ratio"]
    permuted = _run("mantel_haenszel", **{
        name: list(reversed(series)) for name, series in raw.items()})
    assert permuted["values"]["common_odds_ratio"] == pytest.approx(values["common_odds_ratio"])
    assert permuted["values"]["cmh_chi_square"] == pytest.approx(values["cmh_chi_square"])


def test_mantel_haenszel_sparse_correction_and_invalid_strata():
    raw = {"exposed_events": [0, 4], "exposed_nonevents": [10, 16],
           "unexposed_events": [5, 6], "unexposed_nonevents": [15, 14]}
    result = _run("mantel_haenszel", **raw)
    assert result["values"]["corrected_strata"] == 1
    assert result["table"]["rows"][0][-1] == .5
    assert result["views"][0]["studies"][0]["lower"] > 0
    with pytest.raises(ValueError, match="2–64"):
        _run("mantel_haenszel", **{**raw, "exposed_events": [0]})
    with pytest.raises(ValueError, match="whole-number"):
        _run("mantel_haenszel", **{**raw, "exposed_events": [.4, 4]})
    with pytest.raises(ValueError, match="at least one"):
        _run("mantel_haenszel", **{**raw, "exposed_nonevents": [0, 16]})


def test_meta_analysis_random_dl_fixed_and_forest_plot_oracles():
    # DL estimates independently cross-checked with statsmodels combine_effects.
    raw = {"estimates": [-.22, .10, -.11], "standard_errors": [.12, .15, .10],
           "study_labels_json": '["Trial A", "Trial B", "Trial C"]',
           "effect_measure": "log_odds_ratio", "model": "random_dl"}
    result = _run("meta_analysis", **raw)
    values = result["values"]
    assert values["cochran_q"] == pytest.approx(2.786854256854258)
    assert values["tau_squared"] == pytest.approx(.005813326226012798)
    assert values["i_squared_percent"] == pytest.approx(28.23449611399637)
    assert values["pooled_analysis_scale_effect"] == pytest.approx(-.09665771322216613)
    assert values["pooled_effect"] == pytest.approx(.9078667037251211)
    assert values["pooled_effect_ci95_lower"] == pytest.approx(.7727884944741438)
    assert values["pooled_effect_ci95_upper"] == pytest.approx(1.066555671605297)
    assert sum(row[-1] for row in result["table"]["rows"]) == pytest.approx(100)
    forest, = result["views"]
    assert forest["scale"] == "log"
    assert [study["label"] for study in forest["studies"]] == ["Trial A", "Trial B", "Trial C"]
    assert forest["pooled"]["estimate"] == values["pooled_effect"]

    fixed = _run("meta_analysis", **{**raw, "model": "fixed"})
    weights = [1/se**2 for se in raw["standard_errors"]]
    expected = sum(w*e for w, e in zip(weights, raw["estimates"])) / sum(weights)
    assert fixed["values"]["pooled_analysis_scale_effect"] == pytest.approx(expected)
    assert fixed["values"]["pooled_standard_error"] == pytest.approx(1/math.sqrt(sum(weights)))
    linear = _run("meta_analysis", **{**raw, "effect_measure": "mean_difference"})
    assert linear["views"][0]["scale"] == "linear"
    assert linear["values"]["pooled_effect"] == pytest.approx(values["pooled_analysis_scale_effect"])


def test_meta_analysis_rejects_invalid_scales_and_labels():
    raw = {"estimates": [-.22, .1, -.11], "standard_errors": [.12, .15, .1],
           "study_labels_json": '["Trial A", "Trial B", "Trial C"]',
           "effect_measure": "log_odds_ratio", "model": "random_dl"}
    with pytest.raises(ValueError, match="paired"):
        _run("meta_analysis", **{**raw, "standard_errors": [.1, .2]})
    with pytest.raises(ValueError, match="Standard errors"):
        _run("meta_analysis", **{**raw, "standard_errors": [0, .1, .2]})
    with pytest.raises(ValueError, match="Study labels"):
        _run("meta_analysis", **{**raw, "study_labels_json": '["x", "x", "z"]'})
    with pytest.raises(ValueError, match="log scale"):
        _run("meta_analysis", **{**raw, "estimates": [800, 800, 800]})


def test_multigroup_kaplan_meier_and_hypergeometric_logrank_oracle():
    # Three-group log-rank statistic independently checked with statsmodels survdiff.
    raw = {"follow_up_time": [1,2,4,6, 1,3,5,7, 2,3,6,8],
           "event_observed": [1,1,0,1, 1,0,1,0, 1,0,1,0],
           "group": [1]*4 + [2]*4 + [3]*4, "time_unit": "days"}
    result = _run("multi_group_kaplan_meier", **raw)
    values = result["values"]
    assert values["logrank_chi_square"] == pytest.approx(.9808365993691908)
    assert values["logrank_p_value"] == pytest.approx(.6123701863465185)
    assert values["logrank_degrees_of_freedom"] == 2
    assert values["group_1_median_survival"] == 2
    assert values["group_2_final_survival"] == pytest.approx(.375)
    assert values["group_2_greenwood_se"] == pytest.approx(.28641098093474004)
    view, = result["views"]
    assert view["type"] == "survival_curve" and len(view["groups"]) == 3
    assert view["groups"][0]["censored"] == [4]
    assert all(curve["times"][0] == 0 and curve["survival"][0] == 1 for curve in view["groups"])
    relabeled = _run("multi_group_kaplan_meier", **{**raw, "group": [
        3 if g == 1 else 1 if g == 3 else 2 for g in raw["group"]]})
    assert relabeled["values"]["logrank_chi_square"] == pytest.approx(values["logrank_chi_square"])


def test_multigroup_km_time_zero_events_censoring_and_degenerate_test():
    result = _run("multi_group_kaplan_meier", follow_up_time=[0,0,1,0,2],
                  event_observed=[1,0,0,1,0], group=[1,1,1,2,2], time_unit="days")
    first = result["views"][0]["groups"][0]
    assert first["times"] == [0,0,1]
    assert first["survival"] == pytest.approx([1, 2/3, 2/3])
    assert first["censored"] == [0,1]
    empty = _run("multi_group_kaplan_meier", follow_up_time=[1,2,3,4],
                 event_observed=[0,0,0,0], group=[1,1,2,2], time_unit="days")
    assert empty["values"]["logrank_p_value"].startswith("undefined")
    with pytest.raises(ValueError, match="0/1"):
        _run("multi_group_kaplan_meier", follow_up_time=[1,2,3,4],
             event_observed=[0,2,1,0], group=[1,1,2,2], time_unit="days")
    with pytest.raises(ValueError, match="Group codes"):
        _run("multi_group_kaplan_meier", follow_up_time=[1,2,3,4],
             event_observed=[0,1,1,0], group=[1,1.5,2,2], time_unit="days")


def test_cox_regression_efron_and_breslow_tied_failure_oracles():
    # Fitted coefficients, SEs and log partial likelihood checked with statsmodels PHReg.
    raw = {"follow_up_time": [1,1,2,2,3,4,4,5,6,7],
           "event_observed": [1,1,1,0,1,1,1,0,1,0],
           "covariates": [[0],[1],[1],[0],[1],[0],[0],[1],[1],[0]],
           "covariate_labels_json": '["Exposure"]', "time_unit": "days"}
    for ties, coefficient, se, likelihood in [
        ("efron", .3465735902799692, .7656986954155408, -11.956263474594772),
        ("breslow", .4054651081081641, .7669649888473704, -12.246894640050428),
    ]:
        result = _run("cox_proportional_hazards", **{**raw, "ties": ties})
        values = result["values"]
        assert values["coefficient_1"] == pytest.approx(coefficient, rel=1e-8)
        assert values["standard_error_1"] == pytest.approx(se, rel=1e-8)
        assert values["log_partial_likelihood"] == pytest.approx(likelihood, rel=1e-10)
        assert values["hazard_ratio_1"] == pytest.approx(math.exp(coefficient))
        assert values["hazard_ratio_ci95_lower_1"] < values["hazard_ratio_1"] < values["hazard_ratio_ci95_upper_1"]
        assert result["table"]["rows"][0][0] == "Exposure"


def test_cox_multivariable_and_row_permutation_invariance():
    raw = {"follow_up_time": [1,1,2,2,3,4,4,5,6,7,8,10,11,12],
           "event_observed": [1,1,1,0,1,1,1,0,1,1,0,1,1,0],
           "covariates": [[0,0],[1,1],[1,0],[0,1],[1,0],[0,0],[0,1],
                          [1,1],[1,0],[1,1],[0,0],[1,0],[1,1],[0,0]],
           "covariate_labels_json": '["a", "b"]', "ties": "efron", "time_unit": "days"}
    result = _run("cox_proportional_hazards", **raw)
    expected = ((.5703678200353367, .7435313805490642),
                (-.25923489842151926, .698330395206393))
    for i, (coefficient, se) in enumerate(expected, 1):
        assert result["values"][f"coefficient_{i}"] == pytest.approx(coefficient, rel=1e-8)
        assert result["values"][f"standard_error_{i}"] == pytest.approx(se, rel=1e-8)
    indices = list(reversed(range(len(raw["follow_up_time"]))))
    permuted = {**raw, **{
        key: [raw[key][index] for index in indices]
        for key in ("follow_up_time", "event_observed", "covariates")
    }}
    reordered = _run("cox_proportional_hazards", **permuted)
    for i in (1, 2):
        assert reordered["values"][f"coefficient_{i}"] == pytest.approx(result["values"][f"coefficient_{i}"])


def test_cox_rejects_no_events_collinear_and_constant_predictors():
    raw = {"follow_up_time": [1,2,3,4,5,6,7,8],
           "event_observed": [1,1,1,0,1,1,0,1],
           "covariates": [[0],[1],[0],[1],[1],[0],[0],[1]],
           "covariate_labels_json": '["Exposure"]', "ties": "efron", "time_unit": "days"}
    with pytest.raises(ValueError, match="more events"):
        _run("cox_proportional_hazards", **{**raw, "event_observed": [0]*8})
    with pytest.raises(ValueError, match="vary"):
        _run("cox_proportional_hazards", **{**raw, "covariates": [[1]]*8})
    with pytest.raises(ValueError, match="singular|rank deficient"):
        _run("cox_proportional_hazards", **{
            **raw, "covariates": [[row[0], row[0]] for row in raw["covariates"]],
            "covariate_labels_json": '["a", "a copy"]',
        })
    with pytest.raises(ValueError, match="Study labels"):
        _run("cox_proportional_hazards", **{**raw, "covariate_labels_json": '["a", "b"]'})
