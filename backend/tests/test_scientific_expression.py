"""Independent numerical and contract regressions for the expression pack."""

import json
import math
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACK_FAMILIES, operations
from graphlink_plugins.scientific.core import readable_result, run_operation
from graphlink_plugins.scientific.result_views import check_result_views


def _run(name, **changes):
    op = next(item for item in operations("expression") if item.id == name)
    return run_operation("expression", op, {**op.example, **changes})


def test_manifest_auto_discovery_examples_and_receipts():
    assert PACK_FAMILIES["expression"] == "biology"
    assert {op.id for op in operations("expression")} == {
        "normalize_counts", "differential_expression",
        "ma_values", "sample_correlation", "expression_pca",
        "over_representation_enrichment",
    }
    for op in operations("expression"):
        assert op.methods and op.citations
        result = run_operation("expression", op, op.example)
        assert readable_result({"version": 1, "latest": result}, "expression")
        assert result["operationId"] == op.id
    docs = (Path(__file__).resolve().parents[2] / "docs/biology/expression.md").read_text(encoding="utf-8")
    assert "normalize_counts" in docs and "differential_expression" in docs
    assert "negative-binomial" in docs


def test_cpm_exact_library_scaling_and_zero_gene():
    result = _run("normalize_counts", gene_ids='["A", "B", "C"]',
                  counts=[[10, 20], [30, 60], [0, 0]], method="cpm")
    values = result["values"]
    assert values["sample_1_library_total"] == 40
    assert values["sample_2_library_total"] == 80
    assert values["sample_1_size_factor"] == pytest.approx(4e-5)
    rows = result["table"]["rows"]
    for row, expected in zip(rows, [[250000, 250000], [750000, 750000], [0, 0]]):
        assert row[1:] == pytest.approx(expected)
    assert math.fsum(row[1] for row in rows) == pytest.approx(1e6)
    assert math.fsum(row[2] for row in rows) == pytest.approx(1e6)


def test_median_ratio_corrects_depth_without_using_all_zero_genes():
    result = _run("normalize_counts", method="median_ratio")
    assert result["values"]["median_ratio_reference_genes"] == 3
    factors = [result["values"][f"sample_{i}_size_factor"] for i in range(1, 5)]
    assert math.prod(factors) == pytest.approx(1)
    assert [factors[i + 1] / factors[i] for i in range(3)] == pytest.approx([2, 2, 2])
    for row in result["table"]["rows"]:
        assert row[1:] == pytest.approx([row[1]] * 4)
    with_zeros = _run("normalize_counts", gene_ids='["A", "B", "C"]',
                      counts=[[1, 2], [0, 5], [10, 0]], method="median_ratio")
    assert with_zeros["values"]["median_ratio_reference_genes"] == 1
    assert with_zeros["table"]["rows"][1][1] == 0


@pytest.mark.parametrize("changes", [
    {"counts": [[0, 1], [0, 3], [0, 1]]},  # empty library
    {"counts": [[1, 2], [-1, 3], [1, 4]]},
    {"counts": [[1.5, 2], [3, 4], [5, 6]]},
    {"counts": [[True, 2], [3, 4], [5, 6]]},
    {"counts": [[2**53, 2], [3, 4], [5, 6]]},
    {"counts": [[1], [2], [3]]},
    {"gene_ids": '["A", "A", "C"]'},
    {"gene_ids": '["A", "B"]'},
    {"gene_ids": 'not JSON'},
    {"gene_ids": '[["nested"], "B", "C"]'},
    {"method": "TPM"},
])
def test_normalization_rejects_invalid_inputs(changes):
    with pytest.raises(ValueError):
        _run("normalize_counts", **changes)


def test_median_ratio_rejects_when_no_gene_is_positive_in_all_samples():
    with pytest.raises(ValueError, match="positive counts in every sample"):
        _run("normalize_counts", gene_ids='["A", "B"]',
             counts=[[1, 0], [0, 2]], method="median_ratio")


def test_default_de_independent_p_values_bh_order_and_volcano():
    result = _run("differential_expression")
    values, rows = result["values"], result["table"]["rows"]
    assert values["tested_genes"] == 4
    assert values["low_count_genes"] == 1
    assert values["significant_up_in_group_b"] == 1
    assert values["significant_down_in_group_b"] == 1
    assert rows[0][3] == pytest.approx(2.95429305700608, abs=1e-9)
    assert rows[1][3] == pytest.approx(-2.76806802864469, abs=1e-9)
    # Reference Welch p-values checked independently with scipy.stats.ttest_ind.
    assert rows[0][6] == pytest.approx(0.00025364184486265, rel=1e-5)
    assert rows[1][6] == pytest.approx(0.00101743581391482, rel=1e-5)
    assert rows[0][7] == pytest.approx(rows[0][6] * 4)
    assert rows[1][7] == pytest.approx(rows[1][6] * 2)
    assert rows[3][6:8] == ["not tested", "not tested"]
    volcano = result["views"][0]
    assert volcano["type"] == "volcano_plot"
    assert volcano["xLabel"] == "log2 fold change (treated / control)"
    assert [point["gene"] for point in volcano["points"]] == ["G1", "G2", "G3", "G5"]
    assert volcano["points"][0]["qValue"] == pytest.approx(rows[0][7])
    check_result_views(result["views"])


def test_de_group_assignment_permutations_and_zero_variance_are_explicit():
    forward = _run("differential_expression")
    # Simply renaming both groups does not change the first-seen A/B ordering.
    renamed = _run("differential_expression", sample_groups=json.dumps(
        ["treated", "treated", "treated", "control", "control", "control"]))
    assert renamed["table"]["rows"][0][3] == pytest.approx(forward["table"]["rows"][0][3])

    # Reorder columns so the treated samples appear first: group A/B really
    # reverse, and the B-vs-A effect changes sign without changing the p-value.
    op = next(item for item in operations("expression") if item.id == "differential_expression")
    reordered_counts = [row[3:] + row[:3] for row in op.example["counts"]]
    reversed_groups = _run("differential_expression", counts=reordered_counts,
                           sample_groups=json.dumps(["treated"] * 3 + ["control"] * 3))
    assert reversed_groups["table"]["rows"][0][3] == pytest.approx(
        -forward["table"]["rows"][0][3])
    assert reversed_groups["table"]["rows"][0][7] == pytest.approx(
        forward["table"]["rows"][0][7])
    zero = _run("differential_expression", gene_ids='["A", "B"]',
                counts=[[10, 10, 10, 20, 20, 20], [20, 20, 20, 10, 10, 10]],
                method="median_ratio", min_total_count=0)
    assert zero["values"]["tested_genes"] == 0
    assert zero["values"]["zero_variance_genes"] == 2
    assert zero["views"][0]["points"] == []
    assert zero["table"]["rows"][0][6] == "not tested"


def test_de_low_count_filter_adjusts_only_tested_and_handles_empty_volcano():
    result = _run("differential_expression", min_total_count=10_000)
    assert result["values"]["tested_genes"] == 0
    assert result["views"][0]["points"] == []
    check_result_views(result["views"])


@pytest.mark.parametrize("changes", [
    {"sample_groups": '["A", "A", "A", "A", "A", "A"]'},
    {"sample_groups": '["A", "B", "B", "B", "B", "B"]'},
    {"sample_groups": '["A", "B", "A", "B", "C", "C"]'},
    {"sample_groups": '["A", "B", "A"]'},
    {"sample_groups": '{"bad": "object"}'},
    {"pseudocount": 0}, {"pseudocount": -1},
    {"fdr_cutoff": 0}, {"fdr_cutoff": 1},
    {"log2_fc_cutoff": -0.1}, {"log2_fc_cutoff": 101},
    {"min_total_count": -1},
])
def test_de_rejects_invalid_groups_and_thresholds(changes):
    with pytest.raises(ValueError):
        _run("differential_expression", **changes)


def test_volcano_view_contract_rejects_forged_significance_and_unbounded_points():
    view = _run("differential_expression")["views"][0]
    for bad in (
        {**view, "fdrThreshold": 1},
        {**view, "log2FoldChangeThreshold": -1},
        {**view, "points": [{"gene": "bad", "log2FoldChange": 2, "qValue": -0.1}]},
        {**view, "points": [{"gene": "A", "log2FoldChange": 1, "qValue": 0.2},
                             {"gene": "A", "log2FoldChange": 2, "qValue": 0.4}]},
        {**view, "points": [{"gene": "a", "log2FoldChange": float("nan"), "qValue": 0.2}]},
        {**view, "points": view["points"] * 300},
        {**view, "danger": "<script>"},
    ):
        with pytest.raises(ValueError):
            check_result_views([bad])


def test_cpm_denominator_is_supplied_gene_panel_not_full_library():
    partial = _run("normalize_counts", gene_ids='["A","B"]',
                   counts=[[5, 10], [5, 10]], method="cpm")
    expanded = _run("normalize_counts", gene_ids='["A","B","background"]',
                    counts=[[5, 10], [5, 10], [90, 180]], method="cpm")
    assert partial["values"]["sample_1_library_total"] == 10
    assert expanded["values"]["sample_1_library_total"] == 100
    assert partial["table"]["rows"][0][1:] == pytest.approx([500_000, 500_000])
    assert expanded["table"]["rows"][0][1:] == pytest.approx([50_000, 50_000])
    assert "input matrix" in partial["summary"]
