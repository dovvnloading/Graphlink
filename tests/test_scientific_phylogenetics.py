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


def test_newick_unquoted_underscores_are_spaces_but_quoted_underscores_are_literal():
    # Olsen/Newick 8:45: underscores in unquoted names represent blanks.
    original = "(A_B,'A_B','A,B','O''Brien');"
    normalized = phy.parse_newick({"tree": original})["values"]["newick"]
    assert "'A B'" in normalized
    assert "'A_B'" in normalized
    assert "'A,B'" in normalized
    assert "'O''Brien'" in normalized
    assert phy._leaves(phy._parse(normalized)) == ("A B", "A,B", "A_B", "O'Brien")
    assert phy.parse_newick({"tree": normalized})["values"]["newick"] == normalized
    assert phy.compare_trees({
        "tree_a": "((A_B,C),(D,E));",
        "tree_b": "(('A B',C),(D,E));",
        "mode": "rooted",
    })["values"]["robinson_foulds"] == 0


@pytest.mark.parametrize("tree", ["(A]B,C);", "(A'B,C);"])
def test_newick_rejects_unquoted_reserved_punctuation(tree):
    with pytest.raises(ValueError):
        phy.parse_newick({"tree": tree})
    assert phy.parse_newick({"tree": "('A]B',C);"})["values"]["taxa_count"] == 2


def test_newick_branch_lengths_roundtrip_without_double_precision_loss():
    length = 1.2345678901234567
    normalized = phy.parse_newick({"tree": f"(A:{length!r},B:0);"})["values"]["newick"]
    assert pairs(normalized) == {("A", "B"): length}
    with pytest.raises(ValueError, match="underflows"):
        phy.parse_newick({"tree": "(A:1e-999,B:1);"})
    # A literal zero, including an exponent, remains a valid zero-length edge.
    assert pairs("(A:0e-999,B:1);") == {("A", "B"): 1.0}


@pytest.mark.parametrize("a, b", [(0, 1e-12), (1e-12, 1.1e-12)])
def test_tiny_asymmetric_distances_are_not_accepted_by_absolute_floor(a, b):
    for infer in (phy.upgma, phy.neighbor_joining):
        with pytest.raises(ValueError, match="symmetric"):
            infer({"taxa": '["A", "B"]', "distances": [[0, a], [b, 0]]})
    # A genuine relative difference below 1e-10 is allowed at the same scale.
    phy.upgma({
        "taxa": '["A", "B"]',
        "distances": [[0, 1e-12], [1e-12 * (1 + 5e-11), 0]],
    })


def test_inferred_tree_fit_reports_independent_pairwise_distance_residuals():
    # UPGMA averages cross-cluster distances: fitted AC and BC are both 4.5.
    input_upgma = [[0, 2, 4], [2, 0, 5], [4, 5, 0]]
    upgma = phy.upgma({"taxa": '["A", "B", "C"]', "distances": input_upgma})
    assert upgma["values"]["maximum_absolute_distance_residual"] == pytest.approx(0.5)
    assert upgma["values"]["root_mean_square_distance_residual"] == pytest.approx(
        math.sqrt((0.5 ** 2 + 0.5 ** 2) / 3)
    )
    assert upgma["values"]["maximum_absolute_distance_residual"] == pytest.approx(
        max(abs(pairs(upgma["values"]["newick"])[(a, b)] - input_upgma[i][j])
            for i, a in enumerate("ABC") for j, b in enumerate("ABC") if i < j)
    )

    # Nonadditive quartet with all *positive* NJ limbs: clipping alone cannot
    # diagnose incomplete distance reproduction.
    input_nj = [[0, 3, 4, 5], [3, 0, 6, 6], [4, 6, 0, 2], [5, 6, 2, 0]]
    nj = phy.neighbor_joining({"taxa": '["A", "B", "C", "D"]', "distances": input_nj})
    assert nj["values"]["negative_limbs_clipped"] == 0
    assert nj["values"]["maximum_absolute_distance_residual"] == pytest.approx(0.25)
    residuals = [
        pairs(nj["values"]["newick"])[(a, b)] - input_nj[i][j]
        for i, a in enumerate("ABCD") for j, b in enumerate("ABCD") if i < j
    ]
    assert nj["values"]["root_mean_square_distance_residual"] == pytest.approx(
        math.sqrt(sum(residual ** 2 for residual in residuals) / len(residuals))
    )
    for infer in (phy.upgma, phy.neighbor_joining):
        exact = infer({"taxa": '["A", "B", "C", "D"]', "distances": MATRIX})
        assert exact["values"]["maximum_absolute_distance_residual"] == pytest.approx(0, abs=1e-12)


def test_every_negative_neighbor_joining_limb_is_counted_even_at_tiny_scale():
    scale = 1e-12
    nonmetric = [[0, scale, scale], [scale, 0, 10 * scale], [scale, 10 * scale, 0]]
    result = phy.neighbor_joining({"taxa": '["A", "B", "C"]', "distances": nonmetric})
    assert result["values"]["negative_limbs_clipped"] == 1
    assert result["values"]["maximum_absolute_distance_residual"] > 0
    assert all(distance >= 0 for distance in pairs(result["values"]["newick"]).values())


def test_clade_and_split_tables_disambiguate_punctuation_in_taxon_names():
    tree = "(('A,B',C),(A,'B,C'));"
    comparison = phy.compare_trees({"tree_a": tree, "tree_b": tree, "mode": "rooted"})
    names = [row[0] for row in comparison["table"]["rows"]]
    assert len(names) == len(set(names)) == 2
    assert any("'A,B'" in name for name in names)
    assert any("'B,C'" in name for name in names)
    consensus = phy.consensus_tree({"trees": tree + tree, "method": "strict"})
    assert len({row[0] for row in consensus["table"]["rows"]}) == 2
