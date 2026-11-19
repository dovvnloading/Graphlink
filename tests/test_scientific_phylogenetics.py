"""Independent tree references, invariants and invalid-input checks for Phylogenetics."""

import json
import math

import pytest

from graphlink_plugins.scientific.packs import phylogenetics as phy
from graphlink_plugins.scientific.core import check_artifact, validated_inputs


TREE = "((A:1,B:1):2,(C:2,D:2):1);"
MATRIX = [[0, 2, 6, 6], [2, 0, 6, 6], [6, 6, 0, 4], [6, 6, 4, 0]]


def pairs(tree):
    result = phy.patristic_distances({"tree": tree})
    return {(a, b): d for a, b, d in result["table"]["rows"]}


def test_catalog_discovery_and_all_examples_are_workbench_safe():
    from graphlink_plugins.scientific.catalog import PACKS, PACK_FAMILIES, operations

    assert "phylogenetics" in PACKS
    assert PACK_FAMILIES["phylogenetics"] == "biology"
    assert {op.id for op in operations("phylogenetics")} == {
        "upgma", "neighbor_joining", "parse_newick", "reroot_tree",
        "compare_trees", "patristic_distances", "consensus_tree",
    }
    for operation in operations("phylogenetics"):
        original = json.dumps(operation.example)
        inputs = validated_inputs(operation, operation.example)
        result = operation.calculate(inputs)
        check_artifact(result)
        assert json.dumps(result, allow_nan=False)
        assert result == operation.calculate(inputs)
        assert original == json.dumps(operation.example)
        assert operation.methods and operation.citations


def test_upgma_reference_heights_and_matrix_order_invariance():
    output = phy.upgma({"taxa": '["A", "B", "C", "D"]', "distances": MATRIX})
    assert pairs(output["values"]["newick"]) == pytest.approx({
        ("A", "B"): 2, ("A", "C"): 6, ("A", "D"): 6,
        ("B", "C"): 6, ("B", "D"): 6, ("C", "D"): 4,
    })
    depths = phy.parse_newick({"tree": output["values"]["newick"]})["table"]["rows"]
    assert [distance for _, distance in depths] == pytest.approx([3, 3, 3, 3])
    assert output["values"]["total_branch_length"] == pytest.approx(9)
    order = [3, 1, 0, 2]
    shuffled = phy.upgma({"taxa": json.dumps([["A", "B", "C", "D"][i] for i in order]),
                          "distances": [[MATRIX[i][j] for j in order] for i in order]})
    assert shuffled["values"]["newick"] == output["values"]["newick"]


def test_neighbor_joining_additive_quartet_recovers_tip_paths():
    output = phy.neighbor_joining({"taxa": '["A", "B", "C", "D"]', "distances": MATRIX})
    assert output["values"]["negative_limbs_clipped"] == 0
    observed = pairs(output["values"]["newick"])
    expected = {(a, b): MATRIX[i][j] for i, a in enumerate("ABCD")
                for j, b in enumerate("ABCD") if i < j}
    assert observed == pytest.approx(expected)
    assert phy.compare_trees({"tree_a": TREE, "tree_b": output["values"]["newick"],
                              "mode": "unrooted"})["values"]["robinson_foulds"] == 0


def test_two_taxa_and_nonadditive_neighbor_joining_are_defined():
    for function in (phy.upgma, phy.neighbor_joining):
        tree = function({"taxa": '["X", "Y"]', "distances": [[0, 7], [7, 0]]})["values"]["newick"]
        assert pairs(tree) == {("X", "Y"): 7}
    # Violating the triangle inequality can make estimated limbs negative;
    # the emitted tree still has nonnegative edges and discloses clipping.
    nonmetric = phy.neighbor_joining({"taxa": '["A", "B", "C"]',
                                       "distances": [[0, 1, 1], [1, 0, 10], [1, 10, 0]]})
    assert nonmetric["values"]["negative_limbs_clipped"] > 0
    phy.patristic_distances({"tree": nonmetric["values"]["newick"]})


def test_newick_quoted_labels_comments_internal_labels_and_polytomies():
    text = "[comment] (('A B':0.1,'O''Brien':0.2)95:0.3, C:0.4);"
    output = phy.parse_newick({"tree": text})
    assert output["values"]["taxa_count"] == 3
    assert output["values"]["internal_nodes"] == 2
    normalized = output["values"]["newick"]
    assert "'A B'" in normalized and "'O''Brien'" in normalized and "95" in normalized
    assert phy.parse_newick({"tree": normalized})["values"]["newick"] == normalized
    assert phy.parse_newick({"tree": "(A,B,C,D);"})["values"]["branch_lengths_complete"] == "no"


def test_reroot_outgroup_and_midpoint_keep_tip_distances():
    tree = "(((A:1,B:2):3,C:6):1,D:4);"
    baseline = pairs(tree)
    for method, outgroup in (("outgroup", "B"), ("midpoint", "ignored")):
        output = phy.reroot_tree({"tree": tree, "method": method, "outgroup": outgroup})
        rerooted = output["values"]["newick"]
        assert pairs(rerooted) == pytest.approx(baseline)
        assert output["values"]["total_branch_length"] == pytest.approx(17)
        assert phy.compare_trees({"tree_a": tree, "tree_b": rerooted,
                                  "mode": "unrooted"})["values"]["robinson_foulds"] == 0


def test_robinson_foulds_rooted_and_unrooted_with_polytomies():
    left = "((A,B),(C,D));"
    right = "((A,C),(B,D));"
    assert phy.compare_trees({"tree_a": left, "tree_b": right,
                              "mode": "unrooted"})["values"]["robinson_foulds"] == 2
    rooted = phy.compare_trees({"tree_a": left, "tree_b": right, "mode": "rooted"})["values"]
    assert rooted["robinson_foulds"] == 4
    assert rooted["normalized_rf"] == 1
    assert phy.compare_trees({"tree_a": left, "tree_b": "(A,B,C,D);",
                              "mode": "unrooted"})["values"]["robinson_foulds"] == 1
    assert phy.compare_trees({"tree_a": "(A,B,C);", "tree_b": "((A,B),C);",
                              "mode": "unrooted"})["values"]["normalized_rf"] == 0


def test_patristic_distances_accept_zero_branches_and_reject_missing_ones():
    assert pairs("(A:0,B:2,C:3);") == {("A", "B"): 2, ("A", "C"): 3, ("B", "C"): 5}
    result = phy.patristic_distances({"tree": TREE})
    assert result["values"]["pair_count"] == 6
    assert result["values"]["mean_distance"] == pytest.approx(5)
    with pytest.raises(ValueError, match="branch lengths"):
        phy.patristic_distances({"tree": "((A:1,B):2,C:3);"})


def test_rooted_majority_and_strict_consensus_support():
    trees = "((A,B),(C,D)); ((A,B),(C,D)); ((A,C),(B,D));"
    majority = phy.consensus_tree({"trees": trees, "method": "majority"})
    assert majority["values"]["tree_count"] == 3
    assert majority["values"]["retained_clades"] == 2
    assert majority["values"]["newick"] == "((A,B),(C,D));"
    assert {row[1] for row in majority["table"]["rows"]} == {2}
    assert all(row[2] == pytest.approx(2 / 3) for row in majority["table"]["rows"])
    strict = phy.consensus_tree({"trees": trees, "method": "strict"})
    assert strict["values"]["newick"] == "(A,B,C,D);"
    assert strict["values"]["retained_clades"] == 0
    assert "table" not in strict
    tie = phy.consensus_tree({"trees": "((A,B),C);((A,C),B);", "method": "majority"})
    assert tie["values"]["newick"] == "(A,B,C);"


@pytest.mark.parametrize("newick", [
    "", "(A,B)", "(A,B));", "(A,,B);", "(A);", "(A,A);", "(A,B:);",
    "(A:-1,B:1);", "(A:NaN,B:1);", "(A:1e999,B:1);", "('A,B);",
    "(A:2,B:1);(C,D);", "(A[unclosed,B);", "(A,B,C):1;extra",
])
def test_bad_newick_is_rejected(newick):
    with pytest.raises(ValueError):
        phy.parse_newick({"tree": newick})


@pytest.mark.parametrize("taxa, matrix", [
    ('["A", "A"]', [[0, 1], [1, 0]]),
    ('["A", "B"]', [[0, 1]]),
    ('["A", "B"]', [[0, 1], [2, 0]]),
    ('["A", "B"]', [[1, 1], [1, 0]]),
    ('["A", "B"]', [[0, -1], [-1, 0]]),
    ('["A", "B"]', [[0, math.inf], [math.inf, 0]]),
    ('["A", "B"]', [[0, math.nan], [math.nan, 0]]),
    ('["A", "B"]', [[0, 1e13], [1e13, 0]]),
    ("invalid JSON", [[0, 1], [1, 0]]),
])
def test_bad_matrices_are_rejected(taxa, matrix):
    with pytest.raises(ValueError):
        phy.upgma({"taxa": taxa, "distances": matrix})
    with pytest.raises(ValueError):
        phy.neighbor_joining({"taxa": taxa, "distances": matrix})


def test_cross_tree_taxa_and_missing_midpoint_lengths_rejected():
    with pytest.raises(ValueError, match="same taxa"):
        phy.compare_trees({"tree_a": "(A,B,C);", "tree_b": "(A,B,D);", "mode": "rooted"})
    with pytest.raises(ValueError, match="same taxa"):
        phy.consensus_tree({"trees": "(A,B,C);(A,B,D);", "method": "majority"})
    with pytest.raises(ValueError, match="outgroup"):
        phy.reroot_tree({"tree": TREE, "method": "outgroup", "outgroup": "absent"})
    with pytest.raises(ValueError, match="branch length"):
        phy.reroot_tree({"tree": "(A,B,C);", "method": "midpoint", "outgroup": "A"})


def test_bounds_on_taxa_forest_and_label_lengths():
    too_many = "(" + ",".join(f"T{i}" for i in range(33)) + ");"
    with pytest.raises(ValueError):
        phy.parse_newick({"tree": too_many})
    with pytest.raises(ValueError):
        phy.parse_newick({"tree": f"({'X' * 129},B);"})
    with pytest.raises(ValueError):
        phy.consensus_tree({"trees": "(A,B);" * 101, "method": "majority"})
