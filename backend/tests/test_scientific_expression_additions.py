"""Numerical regressions and receipt-contract checks for new expression operations."""

import json
import math

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import readable_result, run_operation
from graphlink_plugins.scientific.packs.expression_additions import _hypergeometric_right_tail


def _run(name, **changes):
    op = next(op for op in operations("expression") if op.id == name)
    return run_operation("expression", op, {**op.example, **changes})


def test_extra_operations_have_examples_citations_and_verified_receipts():
    new = {"ma_values", "sample_correlation", "expression_pca", "over_representation_enrichment"}
    assert new <= {op.id for op in operations("expression")}
    for op in operations("expression"):
        if op.id not in new:
            continue
        assert op.methods and op.citations
        result = run_operation("expression", op, op.example)
        assert readable_result({"version": 1, "latest": result}, "expression")
        assert result["operationId"] == op.id


def test_ma_values_use_log_of_group_arithmetic_means_and_preserve_direction():
    result = _run("ma_values", gene_ids='["A", "B"]',
                  counts=[[10, 10, 40, 40], [30, 30, 10, 10]],
                  sample_groups='["control", "control", "treated", "treated"]',
                  method="cpm", pseudocount=1)
    rows = result["table"]["rows"]
    assert [r[0] for r in rows] == ["A", "B"]
    expected_a = math.log2(250000 + 1)
    expected_b = math.log2(800000 + 1)
    assert rows[0][1:3] == pytest.approx([250000, 800000])
    assert rows[0][3] == pytest.approx((expected_a + expected_b) / 2)
    assert rows[0][4] == pytest.approx(expected_b - expected_a)
    assert rows[0][4] > 0 and rows[1][4] < 0
    assert result["values"]["group_a"] == "control"
    assert result["values"]["group_b"] == "treated"
    switched = _run("ma_values", gene_ids='["A", "B"]',
                    counts=[[40, 40, 10, 10], [10, 10, 30, 30]],
                    sample_groups='["treated", "treated", "control", "control"]',
                    method="cpm", pseudocount=1)
    assert switched["table"]["rows"][0][4] == pytest.approx(-rows[0][4])
    assert switched["table"]["rows"][0][3] == pytest.approx(rows[0][3])


def test_correlation_average_linkage_recovers_depth_equivalent_samples():
    result = _run("sample_correlation", gene_ids='["G1", "G2", "G3"]',
                  counts=[[10, 20, 1], [20, 40, 10], [30, 60, 100]],
                  sample_ids='["A", "B", "C"]', method="cpm", pseudocount=1)
    matrix = result["table"]
    assert matrix["columns"] == ["sample_id", "correlation_to_A", "correlation_to_B", "correlation_to_C"]
    assert [row[0] for row in matrix["rows"]] == ["A", "B", "C"]
    assert matrix["rows"][0][2] == pytest.approx(1.0, abs=1e-12)
    assert matrix["rows"][1][1] == pytest.approx(1.0, abs=1e-12)
    assert matrix["rows"][0][3] == pytest.approx(matrix["rows"][2][1])
    assert all(matrix["rows"][i][i+1] == 1 for i in range(3))
    linkage = json.loads(result["values"]["linkage"])
    assert linkage[0][:2] == [0, 1]
    assert linkage[0][2] == pytest.approx(0, abs=1e-12)
    assert linkage[0][3] == 2
    assert json.loads(result["values"]["cluster_order"]) == ["A", "B", "C"]


def test_correlation_rejects_undefined_variance_and_bad_sample_labels():
    with pytest.raises(ValueError, match="constant"):
        _run("sample_correlation", gene_ids='["G1", "G2"]',
             counts=[[10, 20], [10, 20]], sample_ids='["A", "B"]')
    with pytest.raises(ValueError, match="distinct"):
        _run("sample_correlation", sample_ids=json.dumps(['same'] * 6))
    with pytest.raises(ValueError, match="per matrix column"):
        _run("sample_correlation", sample_ids='["A", "B"]')


def test_expression_pca_scores_centered_orthogonal_and_depth_invariant():
    kwargs = {"gene_ids": '["A", "B", "C"]',
              "counts": [[10, 20, 20, 40], [10, 20, 40, 20], [40, 80, 40, 40]],
              "sample_ids": '["s1", "s2", "s3", "s4"]', "method": "cpm", "pseudocount": 1}
    result = _run("expression_pca", **kwargs)
    rows = result["table"]["rows"]
    assert [r[0] for r in rows] == ["s1", "s2", "s3", "s4"]
    assert math.fsum(r[1] for r in rows) == pytest.approx(0, abs=1e-10)
    assert math.fsum(r[2] for r in rows) == pytest.approx(0, abs=1e-10)
    assert math.fsum(r[1]*r[2] for r in rows) == pytest.approx(0, abs=1e-8)
    values = result["values"]
    assert 0 < values["pc1_explained_fraction"] <= 1
    assert 0 <= values["pc2_explained_fraction"] <= 1
    assert values["pc1_explained_fraction"] + values["pc2_explained_fraction"] == pytest.approx(1)
    depth_scaled = [[row[0], row[1] * 5, row[2], row[3] * 3] for row in kwargs["counts"]]
    scaled = _run("expression_pca", **{**kwargs, "counts": depth_scaled})
    for actual, original in zip(scaled["table"]["rows"], rows):
        assert actual[1:] == pytest.approx(original[1:])
    with pytest.raises(ValueError, match="variation"):
        _run("expression_pca", gene_ids='["A", "B"]',
             counts=[[10, 20, 30], [10, 20, 30]], sample_ids='["x", "y", "z"]')


def test_enrichment_exact_hypergeometric_tail_bh_and_universe_filter():
    result = _run("over_representation_enrichment")
    assert result["values"]["tested_sets"] == 2
    assert result["values"]["out_of_universe_sets"] == 1
    rows = result["table"]["rows"]
    alpha, beta, outside = rows
    assert alpha[:3] == ["pathway_alpha", 3, 2]
    assert alpha[3] == pytest.approx(9/8)
    assert alpha[4] == pytest.approx(16/9)
    assert alpha[5] == pytest.approx(2/7)  # (3*5+1) / C(8,3)
    assert alpha[6] == pytest.approx(4/7)
    assert json.loads(alpha[7]) == ["A", "B"]
    assert beta[5] == pytest.approx(13/14)
    assert beta[6] == pytest.approx(13/14)
    assert outside[5:7] == ["not tested", "not tested"]
    assert outside[-1] == "not_in_universe"
    assert _hypergeometric_right_tail(1024, 512, 512, 512) == pytest.approx(
        1 / math.comb(1024, 512), rel=1e-12)
    assert _hypergeometric_right_tail(10, 4, 3, 0) == 1


def test_enrichment_rejects_out_of_universe_selection_duplicates_and_bad_sets():
    for change in (
        {"selected_gene_ids": '["A", "unknown"]'},
        {"selected_gene_ids": '["A", "A"]'},
        {"selected_gene_ids": '["A", "B", "C", "D", "E", "F", "G", "H"]'},
        {"gene_sets": '{"bad": ["A", "A"]}'},
        {"gene_sets": '{"bad": []}'},
        {"gene_sets": '["not", "an", "object"]'},
        {"gene_sets": '{"bad": ["A", 3]}'},
        {"gene_sets": '__import__("os")'},
        {"fdr_cutoff": 0}, {"fdr_cutoff": 1},
    ):
        with pytest.raises(ValueError):
            _run("over_representation_enrichment", **change)
