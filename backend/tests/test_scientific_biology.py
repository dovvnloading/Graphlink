"""Published numeric oracles, physical constraints and persisted Biology results.

The reference readings and their stated precision are recorded in
``docs/SCIENTIFIC_BIOLOGY_REFERENCE.md``. No scientific third-party runtime
package is needed by the workbench or this test module.
"""

import copy
import math
import tomllib
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation
from graphlink_plugins.scientific.packs import biology


def _run(op_id, **fields):
    operation = next(o for o in operations("biology") if o.id == op_id)
    return run_operation("biology", operation, fields)


def _published(actual, expected, decimals):
    """Match the decimal digits actually printed by the reference, not looser tolerances."""
    assert f"{actual:.{decimals}f}" == f"{expected:.{decimals}f}", (actual, expected)


def test_manifest_examples_receipts_and_legacy_operations():
    repo = Path(__file__).resolve().parents[2]
    with (repo / "plugins" / "science_biology" / "plugin.toml").open("rb") as fd:
        manifest = tomllib.load(fd)
    assert manifest["plugin"]["id"] == "science_biology"
    assert manifest["plugin"]["version"] == VERSION
    assert manifest["persistence"]["passive_node_kinds"] == ["workbench"]
    assert manifest["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert [op.id for op in operations("biology")] == [
        "community_diversity", "quadrat_density", "community_overlap", "logistic_growth_fit",
        "gompertz_growth_fit", "michaelis_menten_fit", "dose_response_4pl_fit",
        "lotka_volterra", "hardy_weinberg", "kaplan_meier_logrank", "richness_rarefaction",
    ]
    for op in operations("biology"):
        assert all(f.unit for f in op.fields), (op.id, "unlabeled input")
        artifact = _run(op.id, **op.example)
        assert readable_result({"version": 1, "latest": artifact}, "biology"), op.id
        assert artifact["provenance"]["packVersion"] == VERSION
        changed = copy.deepcopy(artifact)
        changed["summary"] += " changed"
        assert not readable_result({"version": 1, "latest": changed}, "biology")
        if op.id not in ("community_diversity", "quadrat_density", "community_overlap"):
            assert artifact["plot"]["xLabel"] and artifact["plot"]["yLabel"]
            assert any("(" in col and ")" in col for col in artifact["table"]["columns"])

    assert biology.community_diversity({"counts": [12, 6, 2, 0]})["values"] == {
        "individuals": 20, "richness": 3,
        "shannon_nats": pytest.approx(.8979457248567797),
        "gini_simpson": pytest.approx(.54),
    }
    assert biology.quadrat_density({"counts": [4, 9, 5], "area_m2": [1, 2, 1]})["values"] == {
        "pooled_density_per_m2": 4.5, "individuals": 18, "surveyed_area_m2": 4.0,
    }
    assert biology.community_overlap({"site_a_counts": [3, 2, 0, 0], "site_b_counts": [1, 0, 4, 0]})["values"] == {
        "shared_taxa": 1, "union_taxa": 3, "jaccard_similarity": pytest.approx(1/3),
        "sorensen_similarity": .5,
    }


def test_published_michaelis_menten_nls_not_reciprocal_regression():
    # Freddie Buck, UW Madison Biochemistry (2017), base R nls():
    # Vmax 73.261, Km 3.437 and SSE 156.4 (precision printed by author).
    r = _run("michaelis_menten_fit", substrate=[0, 1, 2, 5, 8, 12, 30, 50],
             initial_rate=[0, 11.1, 25.4, 44.8, 54.5, 58.2, 72, 60.1],
             substrate_unit="mM", rate_unit="mM/s")
    v = r["values"]
    _published(v["vmax"], 73.261, 3)
    _published(v["km"], 3.437, 3)
    _published(v["residual_sum_squares"], 156.4, 1)
    assert 0 < v["vmax_standard_error"] < 15
    assert 0 < v["km_standard_error"] < 5
    assert v["vmax"] != pytest.approx(v["lineweaver_burk_vmax_comparison"], abs=.1)
    assert v["km"] != pytest.approx(v["lineweaver_burk_km_comparison"], abs=.1)
    assert "comparison only" in r["summary"]
    assert v["vmax_unit"] == "mM/s" and v["km_unit"] == "mM"
    assert r["table"]["rows"][0][:2] == [0, 0]


def test_published_four_parameter_ic50_with_zero_dose_controls():
    # ToolFoundry 4PL worked example: IC50 3.058, Hill 2.982,
    # low plateau 7.793, high plateau 0.4814, residual SD 0.5196.
    x = [0] * 6 + [.94] * 3 + [1.88] * 3 + [3.75] * 3 + [7.5] * 3 + [15] * 3 + [30] * 3
    y = [7.58, 8, 8.328571429, 7.25, 7.375, 7.9625,
         8.355555556, 6.914285714, 7.75, 6.871428571, 6.45, 5.922222222,
         1.925, 2.885714286, 4.233333333, 1.1875, .857142857, 1.057142857,
         .6875, .525, .825, .25, .22, .44]
    r = _run("dose_response_4pl_fit", concentration=x, response=y,
             concentration_unit="nM", response_unit="% activity", potency_metric="IC50")
    v = r["values"]
    _published(v["potency_concentration"], 3.058, 3)
    _published(v["hill_slope"], 2.982, 3)
    _published(v["low_dose_response"], 7.793, 3)
    _published(v["high_dose_response"], .4814, 4)
    _published(v["residual_standard_deviation"], .5196, 4)
    assert v["potency_ci95_lower"] < v["potency_concentration"] < v["potency_ci95_upper"]
    assert v["potency_unit"] == "nM" and v["plateau_unit"] == "% activity"
    assert v["hill_slope_standard_error"] > 0
    # The label is semantic; the same model also supports an ascending EC50.
    asc = _run("dose_response_4pl_fit", concentration=x, response=[10-z for z in y],
               concentration_unit="nM", response_unit="% activity", potency_metric="EC50")
    assert asc["values"]["potency_concentration"] == pytest.approx(v["potency_concentration"], rel=2e-8)
    assert asc["values"]["hill_slope"] == pytest.approx(v["hill_slope"], rel=2e-8)


def test_published_hardy_weinberg_chi_square_correct_df():
    # PunnettSquares.com: AA=30, AB=60, BB=10, p=0.6, expected 36/48/16,
    # chi-square 6.25 and 1 df (unlike the erroneous naive df=2).
    r = _run("hardy_weinberg", homozygote_aa=30, heterozygote_ab=60, homozygote_bb=10)
    v = r["values"]
    assert v["allele_a_frequency"] == pytest.approx(.6)
    assert [row[2] for row in r["table"]["rows"]] == pytest.approx([36, 48, 16])
    assert v["chi_square"] == pytest.approx(6.25, abs=1e-12)
    assert v["degrees_of_freedom"] == 1
    assert v["p_value"] == pytest.approx(.01241933065155, rel=1e-12)
    assert v["warning"] == "none"
    monomorphic = _run("hardy_weinberg", homozygote_aa=100, heterozygote_ab=0, homozygote_bb=0)
    assert "undefined" in monomorphic["values"]["p_value"]
    rare = _run("hardy_weinberg", homozygote_aa=5, heterozygote_ab=1, homozygote_bb=0)
    assert rare["values"]["expected_count_below_5"] > 0


def test_published_km_and_mantel_cox_hypergeometric_variance():
    # BenchFig: A 2,4,5+,7,9 vs B 6,8+,11,13,15+ (+ censored).
    # Reported χ² 4.30 and p = 0.038, NOT the shortcut χ² = 3.56.
    r = _run("kaplan_meier_logrank", time_a=[2, 4, 5, 7, 9], event_a=[1, 1, 0, 1, 1],
             time_b=[6, 8, 11, 13, 15], event_b=[1, 0, 1, 1, 0], time_unit="days")
    v = r["values"]
    _published(v["logrank_chi_square"], 4.30, 2)
    _published(v["logrank_p_value"], .038, 3)
    assert v["logrank_degrees_of_freedom"] == 1
    assert v["median_survival_a"] == 7
    assert v["median_survival_b"] == 13
    assert r["table"]["rows"][0][1:5] == [5, 1, 0, pytest.approx(.8)]
    assert len(r["table"]["rows"]) == 10
    tied = _run("kaplan_meier_logrank", time_a=[1, 1, 2], event_a=[1, 0, 0],
                time_b=[1, 2, 3], event_b=[1, 1, 0], time_unit="months")
    assert tied["table"]["rows"][0][:6] == [1, 3, 1, 1, pytest.approx(2/3), 3]
    no_events = _run("kaplan_meier_logrank", time_a=[1, 2], event_a=[0, 0],
                     time_b=[1, 2], event_b=[0, 0], time_unit="days")
    assert "undefined" in no_events["values"]["logrank_p_value"]
    assert no_events["values"]["final_survival_a"] == 1


def test_published_hurlbert_rarefaction_and_chao1():
    # Published rarefaction example: [5,3,2], m=5: E[S_5]=113/42
    # (=2.690476 to six decimals), N=10, Sobs=3.
    r = _run("richness_rarefaction", counts=[5, 3, 2], subsample_individuals=5)
    v = r["values"]
    assert v["rarefied_expected_richness"] == pytest.approx(113/42, rel=3e-15)
    assert round(v["rarefied_expected_richness"], 6) == 2.690476
    assert _run("richness_rarefaction", counts=[5,3,2], subsample_individuals=10)["values"][
        "rarefied_expected_richness"] == 3
    assert _run("richness_rarefaction", counts=[5,3,2], subsample_individuals=1)["values"][
        "rarefied_expected_richness"] == pytest.approx(1)
    # Chao (1984) f1²/(2f2): 8 observed, 4 singleton, 2 doubleton => 12 taxa.
    chao = _run("richness_rarefaction", counts=[1, 1, 1, 1, 2, 2, 3, 4], subsample_individuals=5)
    assert chao["values"]["chao1_estimated_richness"] == 12
    corrected = _run("richness_rarefaction", counts=[1, 1, 1, 3], subsample_individuals=2)
    assert corrected["values"]["chao1_estimated_richness"] == 7
    assert "fallback" in corrected["values"]["chao1_method"]
    assert r["plot"]["x"] == sorted(r["plot"]["x"])
    assert all(0 <= y <= 3 for y in r["plot"]["y"])


def test_logistic_and_gompertz_reference_models_and_doubling_time():
    # GraphPad published model definitions; sample values generated directly
    # from those equations with deterministic rounding/noise (no fitter used).
    x = list(range(11))
    logistic = [100 / (1 + math.exp(-.65 * (t - 4))) + (t % 3 - 1) * .02 for t in x]
    g = _run("logistic_growth_fit", time=x, abundance=logistic,
             time_unit="days", abundance_unit="cells/mL")
    gv = g["values"]
    assert gv["carrying_capacity"] == pytest.approx(100, rel=.002)
    assert gv["growth_rate_per_time_unit"] == pytest.approx(.65, rel=.002)
    initial = gv["initial_fitted_abundance"]
    target = 2*initial
    assert target < gv["carrying_capacity"]
    r = gv["growth_rate_per_time_unit"]
    K = gv["carrying_capacity"]
    assert gv["doubling_time_from_first_sample"] == pytest.approx(
        math.log(2*(K-initial)/(K-2*initial))/r, rel=1e-12)
    assert gv["growth_rate_unit"] == "1/days" and gv["doubling_time_unit"] == "days"
    gompertz = [120 * math.exp(-math.exp(-.52 * (t - 4))) + (t % 3 - 1) * .01 for t in x]
    h = _run("gompertz_growth_fit", time=x, abundance=gompertz,
             time_unit="hours", abundance_unit="OD600")
    hv = h["values"]
    assert hv["carrying_capacity"] == pytest.approx(120, rel=.003)
    assert hv["growth_rate_per_time_unit"] == pytest.approx(.52, rel=.003)
    assert isinstance(hv["doubling_time_from_first_sample"], float)
    assert hv["growth_rate_unit"] == "1/hours"
    assert "Gompertz" in h["summary"]


def test_lotka_volterra_rk4_equilibrium_and_positive_phase_plane():
    # Analytic coexistence equilibrium: x*=gamma/delta, y*=alpha/beta.
    r = _run("lotka_volterra", initial_prey=40, initial_predators=20,
             prey_growth_rate=.5, predation_rate=.025, conversion_rate=.01,
             predator_death_rate=.4, step_size=.05, steps=200, time_unit="days")
    assert r["values"]["final_prey"] == pytest.approx(40, abs=1e-11)
    assert r["values"]["final_predators"] == pytest.approx(20, abs=1e-11)
    assert r["values"]["duration"] == pytest.approx(10)
    assert len(r["table"]["rows"]) == 201
    assert r["plot"]["x"] == pytest.approx([40] * 201, abs=1e-11)
    other = _run("lotka_volterra", initial_prey=35, initial_predators=12,
                 prey_growth_rate=.5, predation_rate=.025, conversion_rate=.01,
                 predator_death_rate=.4, step_size=.1, steps=50, time_unit="days")
    assert all(row[1] >= 0 and row[2] >= 0 for row in other["table"]["rows"])


@pytest.mark.parametrize("op,inputs", [
    ("michaelis_menten_fit", {"substrate": [0, 0, 0, 0], "initial_rate": [0, 0, 0, 1],
                              "substrate_unit": "mM", "rate_unit": "µmol/min"}),
    ("michaelis_menten_fit", {"substrate": [1, 2, 3, 4], "initial_rate": [1, 2, 3],
                              "substrate_unit": "mM", "rate_unit": "µmol/min"}),
    ("dose_response_4pl_fit", {"concentration": [0, 0, 1, 1, 2, 2],
                               "response": [1, 1, 1, 2, 2, 2],
                               "concentration_unit": "nM", "response_unit": "%", "potency_metric": "IC50"}),
    ("dose_response_4pl_fit", {"concentration": [0, 1, 2, 3, 4, 5],
                               "response": [1, 2, 3, 4, 5, 6],
                               "concentration_unit": "nM", "response_unit": "%", "potency_metric": "EC90"}),
    ("logistic_growth_fit", {"time": [0,1,2,3,4], "abundance": [1,1,1,1,1],
                             "time_unit": "days", "abundance_unit": "cells"}),
    ("gompertz_growth_fit", {"time": [0,1,2,3,4], "abundance": [10,8,6,4,2],
                             "time_unit": "days", "abundance_unit": "cells"}),
    ("lotka_volterra", {"initial_prey": 10, "initial_predators": 4,
                        "prey_growth_rate": .5, "predation_rate": .02, "conversion_rate": .01,
                        "predator_death_rate": .4, "step_size": -1, "steps": 20, "time_unit": "days"}),
    ("hardy_weinberg", {"homozygote_aa": 0, "heterozygote_ab": 0, "homozygote_bb": 0}),
    ("kaplan_meier_logrank", {"time_a": [1,2], "event_a": [1,2],
                              "time_b": [2,3], "event_b": [1,0], "time_unit": "days"}),
    ("richness_rarefaction", {"counts": [5,3,2], "subsample_individuals": 11}),
    ("richness_rarefaction", {"counts": [5,0,2.1], "subsample_individuals": 5}),
])
def test_invalid_biological_designs_are_rejected(op, inputs):
    with pytest.raises(ValueError):
        _run(op, **inputs)


def test_survival_export_limit_enforced_before_giant_table():
    with pytest.raises(ValueError, match="400 distinct"):
        _run("kaplan_meier_logrank", time_a=list(range(220)), event_a=[0] * 220,
             time_b=list(range(220, 450)), event_b=[1] * 230, time_unit="days")
