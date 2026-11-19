"""Exact combinatorial oracles and edge cases for the Genetics Biology pack."""

import math
import tomllib
from fractions import Fraction
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACKS, PACK_FAMILIES, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation
from graphlink_plugins.scientific.packs import genetics


def _run(operation_id, **inputs):
    operation = next(op for op in operations("genetics") if op.id == operation_id)
    return run_operation("genetics", operation, inputs)


def _hwe_brute(aa, ab, bb):
    """Independent integer-rational genotype configurations (Wigginton eq. 1)."""
    n = aa + ab + bb
    rare = min(2 * aa + ab, 2 * bb + ab)
    common = 2 * n - rare
    values = {}
    for h in range(rare % 2, rare + 1, 2):
        values[h] = Fraction(
            2**h * math.factorial(n) * math.factorial(rare) * math.factorial(common),
            math.factorial((rare - h)//2) * math.factorial(h)
            * math.factorial((common - h)//2) * math.factorial(2*n),
        )
    probability = values[ab]
    return float(sum((value for value in values.values() if value <= probability), Fraction()))


def test_manifest_grant_discovery_docs_and_example_receipts():
    root = Path(__file__).resolve().parents[2]
    with (root / "plugins/science_genetics/plugin.toml").open("rb") as stream:
        manifest = tomllib.load(stream)
    assert manifest["plugin"]["id"] == "science_genetics"
    assert manifest["plugin"]["version"] == VERSION
    assert manifest["scientific"]["family"] == "biology"
    assert manifest["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert manifest["persistence"]["passive_node_kinds"] == ["workbench"]
    assert PACK_FAMILIES["genetics"] == "biology"
    assert PACKS["genetics"][0] == "Genetics"
    assert [operation.id for operation in operations("genetics")] == [
        "segregation_chi_square", "segregation_exact_binomial", "hardy_weinberg_exact",
        "map_distances", "three_point_cross", "two_point_lod",
    ]
    document = (root / "docs/biology/genetics.md").read_text(encoding="utf-8")
    assert "science_genetics" in document
    for operation in operations("genetics"):
        assert operation.methods and operation.citations
        assert all(field.unit for field in operation.fields)
        assert f"`{operation.id}`" in document
        result = run_operation("genetics", operation, operation.example)
        assert readable_result({"version": 1, "latest": result}, "genetics")
        assert result["provenance"]["packVersion"] == VERSION
        assert result["summary"] and result["values"]
        assert len(result["table"]["rows"]) > 0
        assert result["plot"]["xLabel"] and result["plot"]["yLabel"]


def test_mendelian_pearson_k_minus_one_and_small_expected_warning():
    result = _run("segregation_chi_square", observed_counts=[90, 30], expected_ratio=[3, 1])
    assert result["values"]["chi_square"] == 0
    assert result["values"]["degrees_of_freedom"] == 1
    assert result["values"]["p_value"] == 1
    result = _run("segregation_chi_square", observed_counts=[50, 50], expected_ratio=[3, 1])
    assert result["values"]["chi_square"] == pytest.approx(100/3)
    assert result["values"]["p_value"] == pytest.approx(
        math.erfc(math.sqrt((100/3)/2)), rel=1e-12)
    result = _run("segregation_chi_square", observed_counts=[315, 108, 101, 32],
                  expected_ratio=[9, 3, 3, 1])
    expected = [556 * x / 16 for x in (9, 3, 3, 1)]
    assert [row[2] for row in result["table"]["rows"]] == expected
    chi = sum((o-e)**2/e for o, e in zip([315,108,101,32], expected))
    assert result["values"]["chi_square"] == pytest.approx(chi)
    assert result["values"]["degrees_of_freedom"] == 3
    assert result["values"]["p_value"] == pytest.approx(
        math.erfc(math.sqrt(chi/2)) + math.sqrt(2*chi/math.pi) * math.exp(-chi/2), rel=1e-12)
    sparse = _run("segregation_chi_square", observed_counts=[7,1,1,1], expected_ratio=[9,3,3,1])
    assert sparse["values"]["expected_count_below_5"] == 3
    assert "may be unreliable" in sparse["summary"]


def test_exact_binomial_probability_order_not_doubled_tail():
    # At p=0.75, observing zero of four is the unique least-likely result:
    # two-sided probability-order p = (1/4)^4 = 1/256.
    result = _run("segregation_exact_binomial", observed_counts=[0,4], expected_ratio=[3,1])
    assert result["values"]["two_sided_p_value"] == pytest.approx(1/256, rel=1e-12)
    assert result["values"]["log10_two_sided_p_value"] == pytest.approx(math.log10(1/256))
    symmetric = _run("segregation_exact_binomial", observed_counts=[1,3], expected_ratio=[1,1])
    assert symmetric["values"]["two_sided_p_value"] == pytest.approx(10/16)
    equal = _run("segregation_exact_binomial", observed_counts=[3,1], expected_ratio=[3,1])
    assert equal["values"]["two_sided_p_value"] == 1
    # Extreme tails are still reported as a meaningful log10 p even if p underflows.
    tiny = _run("segregation_exact_binomial", observed_counts=[0, 5000], expected_ratio=[3, 1])
    assert tiny["values"]["two_sided_p_value"] == 0
    assert tiny["values"]["log10_two_sided_p_value"] == pytest.approx(5000 * math.log10(.25), rel=1e-10)


@pytest.mark.parametrize("genotypes", [(1,0,1), (2,1,1), (30,60,10), (298,489,213), (5,1,0)])
def test_hwe_exact_probability_order_matches_independent_factorial_oracle(genotypes):
    aa, ab, bb = genotypes
    result = _run("hardy_weinberg_exact", homozygote_aa=aa,
                  heterozygote_ab=ab, homozygote_bb=bb)
    assert result["values"]["two_sided_p_value"] == pytest.approx(
        _hwe_brute(aa, ab, bb), abs=2e-12)
    swapped = _run("hardy_weinberg_exact", homozygote_aa=bb,
                   heterozygote_ab=ab, homozygote_bb=aa)
    assert swapped["values"]["two_sided_p_value"] == pytest.approx(
        result["values"]["two_sided_p_value"], abs=2e-12)
    assert sum(row[2] for row in result["table"]["rows"]) == pytest.approx(aa+ab+bb)


def test_hwe_boundary_monomorphic_and_deficit_tail():
    monomorphic = _run("hardy_weinberg_exact", homozygote_aa=100,
                       heterozygote_ab=0, homozygote_bb=0)
    assert monomorphic["values"]["two_sided_p_value"] == 1
    assert monomorphic["values"]["mid_p_value"] == .5
    heterozygote_deficit = _run("hardy_weinberg_exact", homozygote_aa=1,
                               heterozygote_ab=0, homozygote_bb=1)
    assert heterozygote_deficit["values"]["two_sided_p_value"] == pytest.approx(1/3)
    assert heterozygote_deficit["values"]["heterozygote_deficit_p_value"] == pytest.approx(1/3)
    assert heterozygote_deficit["values"]["heterozygote_excess_p_value"] == pytest.approx(1)


def test_haldane_kosambi_numeric_oracles_and_boundaries():
    result = _run("map_distances", recombinants=35, total_offspring=100)
    assert result["values"]["haldane_distance_cm"] == pytest.approx(60.198640216)
    assert result["values"]["kosambi_distance_cm"] == pytest.approx(43.365029)
    unlinked_zero = _run("map_distances", recombinants=0, total_offspring=100)
    assert unlinked_zero["values"]["haldane_distance_cm"] == 0
    assert unlinked_zero["values"]["kosambi_distance_cm"] == 0
    with pytest.raises(ValueError, match="below 0.5"):
        _run("map_distances", recombinants=50, total_offspring=100)
    with pytest.raises(ValueError, match="below 0.5"):
        _run("map_distances", recombinants=70, total_offspring=100)
    with pytest.raises(ValueError, match="Total offspring"):
        _run("map_distances", recombinants=2, total_offspring=1)


def test_three_point_order_sco_dco_interference_and_outer_correction():
    counts = [350, 95, 10, 45, 45, 10, 95, 350]
    result = _run("three_point_cross", progeny_counts=counts)
    v = result["values"]
    assert v["gene_order"] == "A-B-C"
    assert v["middle_gene"] == "B"
    assert v["parental_offspring"] == 700
    assert v["single_crossovers_left"] == 90
    assert v["single_crossovers_right"] == 190
    assert v["double_crossovers"] == 20
    assert v["left_recombination_fraction"] == pytest.approx(.11)
    assert v["right_recombination_fraction"] == pytest.approx(.21)
    assert v["outer_recombination_fraction"] == pytest.approx(.28)  # DCO restores outer phase
    assert v["expected_double_crossovers"] == pytest.approx(23.1)
    assert v["coefficient_of_coincidence"] == pytest.approx(20/23.1)
    assert v["interference"] == pytest.approx(1-20/23.1)
    assert v["haldane_left_cm"] == pytest.approx(-50*math.log(.78))
    assert v["kosambi_right_cm"] == pytest.approx(25*math.log(1.42/.58))
    assert [row[2] for row in result["table"]["rows"]] == [
        "parental", "single right", "double", "single left",
        "single left", "double", "single right", "parental",
    ]
    # Relabel A and B: the middle locus changes to A, intervals remain 11/21 cM.
    swapped = [0]*8
    for i, count in enumerate(counts):
        mask = 7-i
        a, b, c = (mask >> 2)&1, (mask >> 1)&1, mask&1
        new_mask = (b << 2) | (a << 1) | c
        swapped[7-new_mask] = count
    reanalyzed = _run("three_point_cross", progeny_counts=swapped)["values"]
    assert reanalyzed["gene_order"] == "B-A-C"
    assert reanalyzed["left_distance_cm"] == pytest.approx(11)
    assert reanalyzed["right_distance_cm"] == pytest.approx(21)
    no_dco = _run("three_point_cross", progeny_counts=[450, 40, 0, 10, 10, 0, 40, 450])
    assert no_dco["values"]["double_crossovers"] == 0
    assert no_dco["values"]["interference"] == 1


def test_two_point_lod_profile_boundary_theta_and_unlinked_mle():
    result = _run("two_point_lod", recombinants=3, nonrecombinants=7, theta=.3)
    v = result["values"]
    expected = 3*math.log10(.6) + 7*math.log10(1.4)
    assert v["lod_at_theta"] == pytest.approx(expected)
    assert v["maximum_lod"] == pytest.approx(expected)
    assert v["theta_mle"] == pytest.approx(.3)
    assert result["plot"]["x"][-1] == .5
    assert result["plot"]["y"][-1] == pytest.approx(0)
    no_recombinants = _run("two_point_lod", recombinants=0, nonrecombinants=10, theta=0)
    assert no_recombinants["values"]["theta_mle"] == 0
    assert no_recombinants["values"]["maximum_lod"] == pytest.approx(10*math.log10(2))
    zero_theta_with_recombinants = _run("two_point_lod", recombinants=2,
                                       nonrecombinants=8, theta=0)
    assert zero_theta_with_recombinants["values"]["lod_at_theta"] == "minus infinity"
    unlinked = _run("two_point_lod", recombinants=7, nonrecombinants=3, theta=.5)
    assert unlinked["values"]["theta_mle"] == .5
    assert unlinked["values"]["maximum_lod"] == 0


@pytest.mark.parametrize("op,inputs,pattern", [
    ("segregation_chi_square", {"observed_counts": [1,2], "expected_ratio": [3,-1]}, "strictly positive"),
    ("segregation_chi_square", {"observed_counts": [1,2,3], "expected_ratio": [3,1]}, "one strictly positive"),
    ("segregation_chi_square", {"observed_counts": [0,0], "expected_ratio": [3,1]}, "At least one"),
    ("segregation_exact_binomial", {"observed_counts": [10001,0], "expected_ratio": [1,1]}, "10,000"),
    ("segregation_exact_binomial", {"observed_counts": [1,2,3], "expected_ratio": [1,1,1]}, "between 2 and 2"),
    ("hardy_weinberg_exact", {"homozygote_aa": 0, "heterozygote_ab": 0, "homozygote_bb": 0}, "at least one"),
    ("hardy_weinberg_exact", {"homozygote_aa": 100001, "heterozygote_ab": 0, "homozygote_bb": 0}, "100,000"),
    ("map_distances", {"recombinants": 0, "total_offspring": 0}, "positive"),
    ("three_point_cross", {"progeny_counts": [1]*8}, "ambiguous"),
    ("three_point_cross", {"progeny_counts": [1]*7}, "8 classes"),
    ("two_point_lod", {"recombinants": 0, "nonrecombinants": 0, "theta": .1}, "at least one"),
    ("two_point_lod", {"recombinants": 3, "nonrecombinants": 7, "theta": .51}, "between 0 and 0.5"),
])
def test_rejects_malformed_or_unidentifiable_inputs(op, inputs, pattern):
    with pytest.raises(ValueError, match=pattern):
        _run(op, **inputs)


def test_rejects_fractional_counts_even_when_core_vector_is_numeric():
    with pytest.raises(ValueError, match="whole-number"):
        _run("three_point_cross", progeny_counts=[1.5, 2, 0, 1, 1, 0, 2, 3])
