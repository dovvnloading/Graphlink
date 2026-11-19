"""Independent numeric oracles and boundaries for the Genetics population extensions."""

import math
import random

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import run_operation


def _run(operation_id, **inputs):
    operation = next(op for op in operations("genetics") if op.id == operation_id)
    return run_operation("genetics", operation, inputs)


def test_f_statistics_unequal_size_weighting_and_identity():
    # Two unequal-size populations: (AA, AB, BB)=(10,0,0) and (0,30,0).
    # H_I=30/40, H_S=(10*0+30*.5)/40, pbar=50/80.
    r = _run("f_statistics", genotype_counts=[[10, 0, 0], [0, 30, 0]])
    v = r["values"]
    assert v["individuals"] == 40
    assert v["allele_a_frequency"] == pytest.approx(0.625)
    assert v["observed_heterozygosity_hi"] == pytest.approx(0.75)
    assert v["expected_within_hs"] == pytest.approx(0.375)
    assert v["expected_total_ht"] == pytest.approx(0.46875)
    assert v["f_is"] == pytest.approx(-1)
    assert v["f_st"] == pytest.approx(0.2)
    assert v["f_it"] == pytest.approx(-0.6)
    assert 1 - v["f_it"] == pytest.approx((1 - v["f_is"]) * (1 - v["f_st"]))
    assert r["plot"]["y"] == pytest.approx([1, 0.5])
    assert r["table"]["rows"][1][6:] == pytest.approx([1, 0.5])


def test_f_statistics_identical_and_fixed_subpopulations():
    same = _run("f_statistics", genotype_counts=[[20, 10, 20], [10, 30, 10]])["values"]
    assert same["f_st"] == pytest.approx(0)
    assert same["f_is"] == pytest.approx(0.2)
    fully_differentiated = _run("f_statistics", genotype_counts=[[10, 0, 0], [0, 0, 30]])["values"]
    assert fully_differentiated["f_st"] == pytest.approx(1)
    assert fully_differentiated["f_it"] == pytest.approx(1)
    assert "undefined" in fully_differentiated["f_is"]
    monomorphic = _run("f_statistics", genotype_counts=[[10, 0, 0], [20, 0, 0]])["values"]
    assert all("undefined" in monomorphic[key] for key in ("f_is", "f_st", "f_it"))


def test_ld_coupling_repulsion_independence_and_complete_association():
    coupled = _run("linkage_disequilibrium", haplotype_counts=[40, 10, 10, 40])
    v = coupled["values"]
    assert v["allele_a_frequency"] == 0.5
    assert v["allele_b_frequency"] == 0.5
    assert v["d"] == pytest.approx(0.15)
    assert v["d_prime_signed"] == pytest.approx(0.6)
    assert v["r_squared"] == pytest.approx(0.36)
    assert [row[3] for row in coupled["table"]["rows"]] == pytest.approx([25] * 4)
    repulsion = _run("linkage_disequilibrium", haplotype_counts=[10, 40, 40, 10])["values"]
    assert repulsion["d"] == pytest.approx(-0.15)
    assert repulsion["d_prime_signed"] == pytest.approx(-0.6)
    assert repulsion["r_squared"] == pytest.approx(0.36)
    independent = _run("linkage_disequilibrium", haplotype_counts=[25, 25, 25, 25])["values"]
    assert (independent["d"], independent["d_prime_signed"], independent["r_squared"]) == (0, 0, 0)
    complete = _run("linkage_disequilibrium", haplotype_counts=[50, 0, 0, 50])["values"]
    assert complete["d_prime_signed"] == 1
    assert complete["r_squared"] == 1
    fixed = _run("linkage_disequilibrium", haplotype_counts=[100, 0, 0, 0])["values"]
    assert fixed["d"] == 0
    assert "undefined" in fixed["d_prime_signed"] and "undefined" in fixed["r_squared"]


def test_wright_fisher_viability_selection_equation_and_seeded_sampling():
    inputs = dict(population_size=10, initial_a_copies=10, generations=4,
                  fitness_aa=1.2, fitness_ab=1.1, fitness_bb=1, seed=7)
    a = _run("wright_fisher_drift_selection", **inputs)
    b = _run("wright_fisher_drift_selection", **inputs)
    assert a["table"] == b["table"]
    assert a["plot"] == b["plot"]
    # p=.5: weighted numerator .25*1.2 + .25*1.1 = .575,
    # denominator .25*1.2 + .5*1.1 + .25*1 = 1.1.
    q = .575 / 1.1
    assert a["table"]["rows"][0][1] == pytest.approx(q)
    draw = random.Random(7).random
    first_generation = sum(draw() < q for _ in range(20))
    assert a["table"]["rows"][0][2] == first_generation
    assert a["table"]["rows"][0][3] == pytest.approx(first_generation / 20)
    assert a["plot"]["y"][0] == 0.5
    assert len(a["plot"]["x"]) == 5
    assert a["values"]["initial_a_copies"] == 10


def test_wright_fisher_neutral_drift_and_absorbing_boundaries():
    neutral = dict(population_size=3, initial_a_copies=3, generations=8,
                   fitness_aa=1, fitness_ab=1, fitness_bb=1, seed=11)
    a = _run("wright_fisher_drift_selection", **neutral)
    b = _run("wright_fisher_drift_selection", **{**neutral, "fitness_aa": 5,
                                                 "fitness_ab": 5, "fitness_bb": 5})
    assert a["plot"]["y"] == b["plot"]["y"]
    for row in a["table"]["rows"]:
        assert 0 <= row[2] <= 6
        assert row[3] == row[2] / 6
    for copies in (0, 6):
        result = _run("wright_fisher_drift_selection", **{**neutral, "initial_a_copies": copies})
        assert result["values"]["fixation_generation"] == 0
        assert result["plot"]["y"] == [copies / 6] * 9


def test_heritability_from_variance_components_is_not_a_regression_estimate():
    r = _run("heritability_variance", additive_variance=4, dominance_variance=2,
             epistatic_variance=1, environmental_variance=3, variance_unit="cm²")
    v = r["values"]
    assert v["genetic_variance"] == 7
    assert v["phenotypic_variance"] == 10
    assert v["narrow_sense_h2"] == pytest.approx(0.4)
    assert v["broad_sense_h2"] == pytest.approx(0.7)
    assert sum(row[2] for row in r["table"]["rows"]) == pytest.approx(1)
    environmental_only = _run("heritability_variance", additive_variance=0, dominance_variance=0,
                              epistatic_variance=0, environmental_variance=5, variance_unit="kg²")
    assert environmental_only["values"]["narrow_sense_h2"] == 0
    assert environmental_only["values"]["broad_sense_h2"] == 0


def test_sex_ratio_effective_size_and_heterozygosity_decay():
    balanced = _run("effective_population_size", breeding_males=10,
                    breeding_females=10, generations=10)
    v = balanced["values"]
    assert v["effective_population_size"] == pytest.approx(20)
    assert v["ne_to_breeders_ratio"] == pytest.approx(1)
    assert v["inbreeding_rate_per_generation"] == pytest.approx(1 / 40)
    assert v["relative_heterozygosity_remaining"] == pytest.approx((39 / 40)**10)
    assert v["projected_inbreeding_from_zero"] == pytest.approx(1 - (39 / 40)**10)
    assert balanced["table"]["rows"][0] == [0, 1, 0]
    unequal = _run("effective_population_size", breeding_males=1,
                   breeding_females=9, generations=10)["values"]
    assert unequal["effective_population_size"] == pytest.approx(3.6)
    assert unequal["ne_to_breeders_ratio"] == pytest.approx(.36)
    assert unequal["relative_heterozygosity_remaining"] < v["relative_heterozygosity_remaining"]
    large = _run("effective_population_size", breeding_males=1_000_000_000,
                 breeding_females=1_000_000_000, generations=1)["values"]
    assert large["projected_inbreeding_from_zero"] == pytest.approx(2.5e-10)


@pytest.mark.parametrize("op,inputs,pattern", [
    ("f_statistics", {"genotype_counts": [[1, 0, 0]]}, "2–200"),
    ("f_statistics", {"genotype_counts": [[1, 0, 0], [0, 0, 0]]}, "at least one"),
    ("f_statistics", {"genotype_counts": [[1, 0, 0], [-1, 0, 2]]}, "whole-number"),
    ("f_statistics", {"genotype_counts": [[1, 0, 0], [1, 2]]}, "equal-length|3 classes"),
    ("linkage_disequilibrium", {"haplotype_counts": [1, 2, 3]}, "4 classes"),
    ("linkage_disequilibrium", {"haplotype_counts": [0, 0, 0, 0]}, "at least one"),
    ("linkage_disequilibrium", {"haplotype_counts": [1, 2.5, 3, 4]}, "whole-number"),
    ("wright_fisher_drift_selection", dict(population_size=5001, initial_a_copies=1,
                                           generations=1, fitness_aa=1, fitness_ab=1,
                                           fitness_bb=1, seed=1), "1–5000"),
    ("wright_fisher_drift_selection", dict(population_size=10, initial_a_copies=21,
                                           generations=1, fitness_aa=1, fitness_ab=1,
                                           fitness_bb=1, seed=1), "0–2N"),
    ("wright_fisher_drift_selection", dict(population_size=10, initial_a_copies=10,
                                           generations=401, fitness_aa=1, fitness_ab=1,
                                           fitness_bb=1, seed=1), "1–400"),
    ("wright_fisher_drift_selection", dict(population_size=10, initial_a_copies=10,
                                           generations=1, fitness_aa=1, fitness_ab=0,
                                           fitness_bb=1, seed=1), "strictly positive"),
    ("wright_fisher_drift_selection", dict(population_size=10, initial_a_copies=10,
                                           generations=1, fitness_aa=1, fitness_ab=1,
                                           fitness_bb=1, seed=-1), "seed"),
    ("heritability_variance", dict(additive_variance=0, dominance_variance=0,
                                   epistatic_variance=0, environmental_variance=0,
                                   variance_unit="cm²"), "positive"),
    ("heritability_variance", dict(additive_variance=-1, dominance_variance=1,
                                   epistatic_variance=0, environmental_variance=0,
                                   variance_unit="cm²"), "nonnegative"),
    ("heritability_variance", dict(additive_variance=1, dominance_variance=0,
                                   epistatic_variance=0, environmental_variance=0,
                                   variance_unit=" "), "Variance unit"),
    ("effective_population_size", dict(breeding_males=0, breeding_females=2,
                                       generations=10), "positive"),
    ("effective_population_size", dict(breeding_males=1, breeding_females=2,
                                       generations=401), "1–400"),
])
def test_invalid_population_genetics_inputs(op, inputs, pattern):
    with pytest.raises(ValueError, match=pattern):
        _run(op, **inputs)
