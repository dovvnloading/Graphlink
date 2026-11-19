"""Published-formula regression oracles and boundary tests for epidemiology."""

import math
import tomllib
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACKS, PACK_FAMILIES, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation
from graphlink_plugins.scientific.packs import epidemiology


def _run(identifier, **inputs):
    operation = next(op for op in operations("epidemiology") if op.id == identifier)
    return run_operation("epidemiology", operation, inputs)


def test_pack_discovery_grants_documentation_and_reproducible_example_receipts():
    root = Path(__file__).resolve().parents[2]
    with (root / "plugins/science_epidemiology/plugin.toml").open("rb") as stream:
        manifest = tomllib.load(stream)
    assert manifest["plugin"]["id"] == "science_epidemiology"
    assert manifest["plugin"]["version"] == VERSION
    assert manifest["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert manifest["scientific"]["family"] == "biology"
    assert manifest["persistence"]["passive_node_kinds"] == ["workbench"]
    assert PACKS["epidemiology"][0] == "Epidemiology and Biostatistics"
    assert PACK_FAMILIES["epidemiology"] == "biology"
    expected_ids = ["two_by_two_measures", "diagnostic_test_accuracy", "roc_curve_auc",
                    "incidence_rate", "direct_age_standardization", "mantel_haenszel",
                    "meta_analysis", "multi_group_kaplan_meier", "cox_proportional_hazards"]
    assert [op.id for op in operations("epidemiology")] == expected_ids
    documentation = (root / "docs/biology/epidemiology.md").read_text(encoding="utf-8")
    assert "science_epidemiology" in documentation
    for op in operations("epidemiology"):
        assert op.methods and op.citations
        assert all(field.unit for field in op.fields)
        assert f"`{op.id}`" in documentation
        artifact = run_operation("epidemiology", op, op.example)
        assert artifact["operationId"] == op.id
        assert artifact["provenance"]["packVersion"] == VERSION
        assert artifact["summary"] and artifact["values"] and artifact["table"]["rows"]
        assert readable_result({"version": 1, "latest": artifact}, "epidemiology")
        assert artifact["inputs"].keys() == op.example.keys()


def test_two_by_two_katz_woolf_and_newcombe_numeric_oracles():
    # 12/200 in treated vs 30/200 controls; reference calculations use
    # 1.95996398454 and unpooled Wilson-score difference limits.
    v = _run("two_by_two_measures", treated_events=12, treated_nonevents=188,
             control_events=30, control_nonevents=170)["values"]
    assert v["treated_risk"] == .06
    assert v["control_risk"] == .15
    assert v["risk_ratio"] == pytest.approx(.4)
    assert v["odds_ratio"] == pytest.approx(12 * 170 / (188 * 30))
    assert v["risk_difference"] == pytest.approx(-.09)
    assert v["absolute_risk_reduction"] == pytest.approx(.09)
    assert v["risk_ratio_ci95_lower"] == pytest.approx(.21089133277353345)
    assert v["risk_ratio_ci95_upper"] == pytest.approx(.7586845694214313)
    assert v["odds_ratio_ci95_lower"] == pytest.approx(.17946212552009838)
    assert v["odds_ratio_ci95_upper"] == pytest.approx(.7290030070373335)
    assert v["risk_difference_ci95_lower"] == pytest.approx(-.15152042886089112)
    assert v["risk_difference_ci95_upper"] == pytest.approx(-.030036720492779688)
    assert v["number_needed_kind"] == "NNT"
    assert v["number_needed"] == pytest.approx(100 / 9)
    assert v["number_needed_ci95_lower"] == pytest.approx(6.599770126826177)
    assert v["number_needed_ci95_upper"] == pytest.approx(33.292582665287405)
    assert v["continuity_correction_per_cell"] == 0


def test_two_by_two_zero_cells_harm_and_unbounded_reciprocal_ci():
    zero = _run("two_by_two_measures", treated_events=0, treated_nonevents=20,
                control_events=5, control_nonevents=15)["values"]
    assert zero["risk_difference"] == -.25  # Observed RD is not corrected.
    assert zero["continuity_correction_per_cell"] == .5
    assert zero["risk_ratio"] == pytest.approx(1 / 11)
    assert zero["odds_ratio"] == pytest.approx((.5 * 15.5) / (20.5 * 5.5))
    assert 0 < zero["risk_ratio_ci95_lower"] < zero["risk_ratio_ci95_upper"]
    assert 0 <= zero["treated_risk_ci95_lower"] < zero["treated_risk_ci95_upper"]
    harm = _run("two_by_two_measures", treated_events=30, treated_nonevents=170,
                control_events=12, control_nonevents=188)["values"]
    assert harm["number_needed_kind"] == "NNH"
    assert harm["number_needed"] == pytest.approx(100 / 9)
    assert harm["number_needed_ci95_lower"] == pytest.approx(6.599770126826177)
    assert harm["number_needed_ci95_upper"] == pytest.approx(33.292582665287405)
    equal = _run("two_by_two_measures", treated_events=1, treated_nonevents=9,
                 control_events=1, control_nonevents=9)["values"]
    assert equal["risk_difference"] == 0
    assert equal["number_needed_kind"] == "undefined"
    assert "unbounded" in equal["number_needed_ci95_lower"]
    assert "unbounded" in equal["number_needed_ci95_upper"]
    with pytest.raises(ValueError, match="Both treated"):
        _run("two_by_two_measures", treated_events=0, treated_nonevents=0,
             control_events=1, control_nonevents=2)
    with pytest.raises(ValueError, match="integer"):
        _run("two_by_two_measures", treated_events=2.5, treated_nonevents=10,
             control_events=1, control_nonevents=10)


def test_two_by_two_double_zero_or_all_events_do_not_invent_relative_effect():
    # The Cochrane Handbook identifies tables with no events (or all events)
    # in both arms as uninformative for relative effects. With unequal arm
    # sizes, a universal 0.5 correction falsely invents an apparent benefit.
    for a, b, c, d in ((0, 200, 0, 20), (200, 0, 20, 0)):
        result = _run("two_by_two_measures", treated_events=a, treated_nonevents=b,
                      control_events=c, control_nonevents=d)
        values = result["values"]
        assert values["risk_difference"] == 0
        assert values["absolute_risk_reduction"] == 0
        assert values["number_needed_kind"] == "undefined"
        assert values["continuity_correction_per_cell"] == 0
        for key in ("risk_ratio", "odds_ratio",
                    "risk_ratio_ci95_lower", "risk_ratio_ci95_upper",
                    "odds_ratio_ci95_lower", "odds_ratio_ci95_upper"):
            assert values[key].startswith("undefined")
        assert "undefined" in result["summary"]
        assert values["risk_difference_ci95_lower"] < 0 < values["risk_difference_ci95_upper"]



def test_diagnostic_accuracy_wilson_intervals_likelihood_ratios_boundaries():
    v = _run("diagnostic_test_accuracy", true_positive=80, false_positive=10,
             false_negative=20, true_negative=90)["values"]
    assert v["sensitivity"] == .8
    assert v["specificity"] == .9
    assert v["positive_predictive_value"] == pytest.approx(8 / 9)
    assert v["negative_predictive_value"] == pytest.approx(9 / 11)
    assert v["accuracy"] == .85
    assert v["prevalence"] == .5
    assert v["positive_likelihood_ratio"] == pytest.approx(8)
    assert v["negative_likelihood_ratio"] == pytest.approx(2 / 9)
    assert v["sensitivity_ci95_lower"] == pytest.approx(.7111708344068411)
    assert v["sensitivity_ci95_upper"] == pytest.approx(.8666330666689676)
    all_negative_tests = _run("diagnostic_test_accuracy", true_positive=0, false_positive=0,
                              false_negative=1, true_negative=9)["values"]
    assert all_negative_tests["positive_predictive_value"].startswith("undefined")
    assert all_negative_tests["specificity"] == 1
    assert all_negative_tests["positive_likelihood_ratio"].startswith("undefined")
    assert all_negative_tests["negative_likelihood_ratio"] == 1
    no_disease = _run("diagnostic_test_accuracy", true_positive=0, false_positive=1,
                      false_negative=0, true_negative=9)["values"]
    assert no_disease["sensitivity"].startswith("undefined")
    assert no_disease["positive_likelihood_ratio"].startswith("undefined")
    with pytest.raises(ValueError, match="Diagnostic table"):
        _run("diagnostic_test_accuracy", true_positive=0, false_positive=0,
             false_negative=0, true_negative=0)


def test_empirical_roc_auc_independent_pairwise_oracle_ties_direction_and_thresholds():
    # Mann–Whitney pair enumeration, including 0.5 credit for ties, is an
    # independent oracle for the trapezoidal ROC implementation.
    scores = [.91, .8, .8, .55, .4, .1]
    labels = [1, 1, 0, 1, 0, 0]
    v = _run("roc_curve_auc", scores=scores, disease_status=labels,
             positive_direction="higher")
    cases = [score for score, label in zip(scores, labels) if label]
    controls = [score for score, label in zip(scores, labels) if not label]
    oracle = sum((case > control) + .5 * (case == control)
                 for case in cases for control in controls) / (len(cases) * len(controls))
    assert oracle == pytest.approx(5 / 6)
    assert v["values"]["auc"] == pytest.approx(oracle)
    rows = v["table"]["rows"]
    assert rows[0][3:5] == [0, 0]
    assert rows[-1][3:5] == [1, 1]
    assert len(rows) == len(set(scores)) + 1
    assert v["values"]["best_youden_threshold"] == .55
    assert v["plot"]["x"] == [row[4] for row in rows]
    assert v["plot"]["y"] == [row[3] for row in rows]
    reversed_scores = [-score for score in scores]
    negative_direction = _run("roc_curve_auc", scores=reversed_scores, disease_status=labels,
                              positive_direction="lower")
    assert negative_direction["values"]["auc"] == pytest.approx(oracle)
    all_ties = _run("roc_curve_auc", scores=[3, 3, 3, 3], disease_status=[0, 1, 0, 1],
                    positive_direction="higher")
    assert all_ties["values"]["auc"] == .5
    assert len(all_ties["table"]["rows"]) == 2
    # A non-discriminating ROC has no positive J; the (0,0) origin is the
    # first/most stringent maximizer, not an arbitrary observed-score cutoff.
    assert all_ties["values"]["best_youden_j"] == 0
    assert all_ties["values"]["best_youden_threshold"] == "No predicted positives"
    inverted = _run("roc_curve_auc", scores=[4, 3, 2, 1],
                    disease_status=[0, 0, 1, 1], positive_direction="higher")
    assert inverted["values"]["auc"] == 0
    assert inverted["values"]["best_youden_j"] == 0
    assert inverted["values"]["best_youden_threshold"] == "No predicted positives"
    perfect = _run("roc_curve_auc", scores=[9, 8, 2, 1], disease_status=[1, 1, 0, 0],
                   positive_direction="higher")
    assert perfect["values"]["auc"] == 1
    with pytest.raises(ValueError, match="at least one case"):
        _run("roc_curve_auc", scores=[1, 2], disease_status=[0, 0], positive_direction="higher")
    with pytest.raises(ValueError, match="0/1"):
        _run("roc_curve_auc", scores=[1, 2], disease_status=[1, 2], positive_direction="higher")
    with pytest.raises(ValueError, match="matched scores"):
        _run("roc_curve_auc", scores=[1, 2], disease_status=[1], positive_direction="higher")


def test_roc_1024_unique_thresholds_stay_within_plot_and_table_limits():
    values = {"scores": list(range(1024)), "disease_status": [i % 2 for i in range(1024)],
              "positive_direction": "higher"}
    result = epidemiology.roc_curve_auc(values)
    assert len(result["table"]["rows"]) == 1024
    assert len(result["plot"]["x"]) == 1024
    assert result["plot"]["x"][0] == result["plot"]["y"][0] == 0
    assert result["plot"]["x"][-1] == result["plot"]["y"][-1] == 1
    assert result["values"]["auc"] == pytest.approx(sum(
        (i > j) + .5 * (i == j) for i in range(1, 1024, 2)
        for j in range(0, 1024, 2)) / (512 * 512))


def test_garwood_exact_poisson_chi_square_oracles_and_rate_scale():
    # Chi-square quantiles from independent published Garwood definition:
    # k=0 upper=-ln(.025)/T; k=1 lower=-ln(.975)/T.
    zero = _run("incidence_rate", events=0, person_time=100,
                person_time_unit="person-years", rate_scale=1000)["values"]
    assert zero["incidence_rate"] == 0
    assert zero["incidence_rate_ci95_lower"] == 0
    assert zero["incidence_rate_ci95_upper"] == pytest.approx(-10 * math.log(.025))
    one = _run("incidence_rate", events=1, person_time=100,
               person_time_unit="person-years", rate_scale=1000)["values"]
    assert one["incidence_rate"] == 10
    assert one["incidence_rate_ci95_lower"] == pytest.approx(-10 * math.log(.975))
    regular = _run("incidence_rate", events=15, person_time=2500,
                   person_time_unit="person-years", rate_scale=1000)["values"]
    assert regular["incidence_rate"] == 6
    assert regular["incidence_rate_ci95_lower"] == pytest.approx(3.3581544531133263, rel=2e-10)
    assert regular["incidence_rate_ci95_upper"] == pytest.approx(9.896087548594338, rel=2e-10)
    for k in (1, 2, 15, 100, 10_000):
        low, upper = epidemiology._garwood(k, 100)
        assert epidemiology._gamma_cdf(k, low * 100) == pytest.approx(.025, abs=2e-10)
        assert epidemiology._gamma_cdf(k + 1, upper * 100) == pytest.approx(.975, abs=2e-10)
    with pytest.raises(ValueError, match="positive"):
        _run("incidence_rate", events=1, person_time=0,
             person_time_unit="person-years", rate_scale=1000)
    with pytest.raises(ValueError, match="1,000,000"):
        _run("incidence_rate", events=1_000_001, person_time=1,
             person_time_unit="person-years", rate_scale=1000)


def test_direct_standardization_fay_feuer_ci_and_exact_poisson_reduction():
    actual = _run("direct_age_standardization", events_by_age=[5, 15, 30],
                  person_time_by_age=[1000, 2000, 1500], standard_population=[4000, 3500, 2500],
                  person_time_unit="person-years", rate_scale=100000)
    v = actual["values"]
    assert v["standardized_rate"] == pytest.approx(962.5)
    assert v["crude_rate"] == pytest.approx(50 / 4500 * 100000)
    assert v["standardized_rate_se"] == pytest.approx(144.66196228910118)
    assert v["standardized_rate_ci95_lower"] == pytest.approx(700.0868238090118, rel=1e-10)
    assert v["standardized_rate_ci95_upper"] == pytest.approx(1317.344392924383, rel=1e-10)
    assert sum(row[3] for row in actual["table"]["rows"]) == pytest.approx(1)
    assert sum(row[5] for row in actual["table"]["rows"]) == pytest.approx(v["standardized_rate"])

    # With standard-population shares equal to study person-time shares,
    # all w_i/T_i are equal and Fay–Feuer *exactly* reduces to Garwood.
    equal = _run("direct_age_standardization", events_by_age=[2, 3],
                 person_time_by_age=[100, 300], standard_population=[100, 300],
                 person_time_unit="person-years", rate_scale=1000)["values"]
    poisson = _run("incidence_rate", events=5, person_time=400,
                   person_time_unit="person-years", rate_scale=1000)["values"]
    assert equal["standardized_rate"] == pytest.approx(poisson["incidence_rate"])
    assert equal["standardized_rate_ci95_lower"] == pytest.approx(poisson["incidence_rate_ci95_lower"])
    assert equal["standardized_rate_ci95_upper"] == pytest.approx(poisson["incidence_rate_ci95_upper"])

    zero = _run("direct_age_standardization", events_by_age=[0, 0],
                person_time_by_age=[100, 200], standard_population=[1, 1],
                person_time_unit="person-years", rate_scale=1000)["values"]
    assert zero["standardized_rate_ci95_lower"] == 0
    assert zero["standardized_rate_ci95_upper"] == pytest.approx(-math.log(.025) * .005 * 1000)


def test_standardization_validation_and_permutation_invariance():
    original = _run("direct_age_standardization", events_by_age=[5, 15, 30],
                    person_time_by_age=[1000, 2000, 1500], standard_population=[4000, 3500, 2500],
                    person_time_unit="person-years", rate_scale=100000)["values"]
    permuted = _run("direct_age_standardization", events_by_age=[30, 5, 15],
                    person_time_by_age=[1500, 1000, 2000], standard_population=[2500, 4000, 3500],
                    person_time_unit="person-years", rate_scale=100000)["values"]
    for key in ("standardized_rate", "standardized_rate_ci95_lower", "standardized_rate_ci95_upper"):
        assert original[key] == pytest.approx(permuted[key])
    with pytest.raises(ValueError, match="matching age strata"):
        _run("direct_age_standardization", events_by_age=[1, 2],
             person_time_by_age=[100], standard_population=[1, 1],
             person_time_unit="person-years", rate_scale=1000)
    with pytest.raises(ValueError, match="positive finite"):
        _run("direct_age_standardization", events_by_age=[1, 2],
             person_time_by_age=[0, 100], standard_population=[1, 1],
             person_time_unit="person-years", rate_scale=1000)
    with pytest.raises(ValueError, match="positive total"):
        _run("direct_age_standardization", events_by_age=[1, 2],
             person_time_by_age=[100, 100], standard_population=[0, 0],
             person_time_unit="person-years", rate_scale=1000)
    with pytest.raises(ValueError, match="whole-number"):
        _run("direct_age_standardization", events_by_age=[1.1, 2],
             person_time_by_age=[100, 100], standard_population=[1, 1],
             person_time_unit="person-years", rate_scale=1000)
