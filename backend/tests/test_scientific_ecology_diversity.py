"""Numeric and validation regression tests for Biology diversity additions.

The accumulation oracle enumerates sampling orders independently of the
closed-form incidence hypergeometric estimator used by the implementation.
"""

import itertools
import math

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import readable_result, run_operation


def _run(op_id, **fields):
    op = next(op for op in operations("biology") if op.id == op_id)
    return run_operation("biology", op, fields)


def test_new_operations_are_discoverable_and_receipts_are_readable():
    for op_id in ("hill_numbers", "beta_diversity", "species_accumulation"):
        op = next(op for op in operations("biology") if op.id == op_id)
        assert op.methods and op.citations
        assert all(field.unit for field in op.fields)
        result = _run(op_id, **op.example)
        assert readable_result({"version": 1, "latest": result}, "biology")
        assert result["plot"]["xLabel"] and result["plot"]["yLabel"]
        assert len(result["table"]["rows"]) == len(result["plot"]["x"])


def test_hill_numbers_closed_form_orders_and_profile():
    # p=(1/2, 1/4, 1/4), with one unobserved taxon.
    result = _run("hill_numbers", counts=[4, 2, 2, 0], order_q=3)
    values = result["values"]
    assert values["hill_q0"] == 3
    assert values["hill_q1"] == pytest.approx(2 * math.sqrt(2), rel=1e-14)
    assert values["hill_q2"] == pytest.approx(8 / 3, rel=1e-14)
    assert values["hill_selected"] == pytest.approx(math.sqrt(32 / 5), rel=1e-14)
    assert result["plot"]["x"] == sorted(result["plot"]["x"])
    assert all(a >= b - 1e-12 for a, b in
               zip(result["plot"]["y"], result["plot"]["y"][1:]))
    assert values["hill_numbers_unit"] == "effective taxa"


def test_hill_numbers_single_taxon_equal_taxa_and_shannon_limit():
    for order in (0, .5, 1, 1 + 1e-9, 2, 10):
        mono = _run("hill_numbers", counts=[0, 9, 0], order_q=order)
        assert mono["values"]["hill_selected"] == pytest.approx(1)
        even = _run("hill_numbers", counts=[3, 3, 3, 0], order_q=order)
        assert even["values"]["hill_selected"] == pytest.approx(3, rel=1e-12)
    near = _run("hill_numbers", counts=[4, 2, 2], order_q=1 + 1e-9)["values"]
    assert near["hill_selected"] == pytest.approx(near["hill_q1"], rel=1e-9)
    extreme = _run("hill_numbers", counts=[2**53 - 2, 1], order_q=2)["values"]
    assert extreme["hill_q0"] == 2
    assert 1 <= extreme["hill_selected"] <= 2


def test_beta_diversity_two_site_analytic_oracle():
    result = _run("beta_diversity", site_abundances=[[3, 1, 0], [1, 0, 2]])
    v = result["values"]
    assert v["gamma_richness"] == 3
    assert v["mean_alpha_richness"] == 2
    assert v["additive_beta"] == 1
    assert v["whittaker_beta"] == pytest.approx(1.5)
    assert v["mean_jaccard_dissimilarity"] == pytest.approx(2 / 3)
    assert v["mean_sorensen_dissimilarity"] == pytest.approx(.5)
    assert v["mean_bray_curtis_dissimilarity"] == pytest.approx(5 / 7)
    assert result["table"]["rows"] == [[1, 2, pytest.approx(2 / 3), .5, pytest.approx(5 / 7)]]


def test_beta_diversity_multi_site_extremes_and_bounds():
    distinct = _run("beta_diversity", site_abundances=[
        [1, 0, 0], [0, 1, 0], [0, 0, 1],
    ])
    values = distinct["values"]
    assert values["whittaker_beta"] == 3
    assert values["mean_jaccard_dissimilarity"] == 1
    assert values["mean_sorensen_dissimilarity"] == 1
    assert values["mean_bray_curtis_dissimilarity"] == 1
    assert len(distinct["table"]["rows"]) == 3
    identical = _run("beta_diversity", site_abundances=[[1, 2], [1, 2], [1, 2]])
    assert identical["values"]["whittaker_beta"] == 1
    assert identical["values"]["mean_bray_curtis_dissimilarity"] == 0
    bounded = _run("beta_diversity", site_abundances=[[1]] * 32)
    assert len(bounded["table"]["rows"]) == 496


def test_accumulation_exact_mean_over_every_sample_permutation():
    sites = [[1, 0, 0], [0, 2, 0], [1, 0, 3], [0, 1, 0]]
    result = _run("species_accumulation", site_abundances=sites)
    all_orderings = list(itertools.permutations(sites))
    oracle = []
    for m in range(1, len(sites) + 1):
        richnesses = []
        for ordering in all_orderings:
            richnesses.append(sum(
                any(row[t] > 0 for row in ordering[:m]) for t in range(3)
            ))
        oracle.append(sum(richnesses) / len(richnesses))
    assert result["plot"]["y"] == pytest.approx(oracle, abs=1e-13)
    assert [row[1] for row in result["table"]["rows"]] == [1, 2, 3, 3]
    assert [row[2] for row in result["table"]["rows"]] == pytest.approx(oracle)
    assert result["values"]["observed_richness"] == 3


def test_accumulation_incidence_not_individuals_and_empty_samples():
    result = _run("species_accumulation", site_abundances=[
        [0, 0], [100, 0], [0, 0],
    ])
    assert [row[1] for row in result["table"]["rows"]] == [0, 1, 1]
    assert result["plot"]["y"] == pytest.approx([1/3, 2/3, 1])
    assert result["values"]["individuals"] == 100
    assert result["values"]["expected_richness_all_samples"] == 1
    long = _run("species_accumulation", site_abundances=[[1]] + [[0]] * 255)
    assert long["plot"]["y"][0] == pytest.approx(1/256)
    assert long["plot"]["y"][-1] == 1
    assert len(long["table"]["rows"]) == 256


@pytest.mark.parametrize(("operation", "data"), [
    ("hill_numbers", {"counts": [0, 0], "order_q": 1}),
    ("hill_numbers", {"counts": [1, -1], "order_q": 1}),
    ("hill_numbers", {"counts": [1.5, 2], "order_q": 1}),
    ("hill_numbers", {"counts": [2**53 - 1, 1], "order_q": 1}),
    ("hill_numbers", {"counts": [1], "order_q": -0.1}),
    ("hill_numbers", {"counts": [1], "order_q": 11}),
    ("beta_diversity", {"site_abundances": [[0, 0], [1, 2]]}),
    ("beta_diversity", {"site_abundances": [[0], [0]]}),
    ("beta_diversity", {"site_abundances": [[1, 2], [3]]}),
    ("beta_diversity", {"site_abundances": [[1], [2.5]]}),
    ("beta_diversity", {"site_abundances": [[1]] * 33}),
    ("species_accumulation", {"site_abundances": [[0, 0], [0, 0]]}),
    ("species_accumulation", {"site_abundances": [[1, 2], [3]]}),
    ("species_accumulation", {"site_abundances": [[1]] * 257}),
])
def test_invalid_diversity_inputs_rejected(operation, data):
    with pytest.raises(ValueError):
        _run(operation, **data)
