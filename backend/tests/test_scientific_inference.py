"""Independent numerical oracles for Scientific Workbench classical inference.

NIST one-way ANOVA input rows and certified values are from the StRD ASCII
files: https://www.itl.nist.gov/div898/strd/anova/ (15 significant digits).
The high-difficulty SmLs07 data is transcribed in lossless run-length form.
The other worked examples are published and linked in the fitting reference.
No external numerical packages are needed to run these tests.
"""

import itertools
import math

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import run_operation, readable_result
from graphlink_plugins.scientific.packs import inferential


def _run(name, **kwargs):
    op = next(item for item in operations("fitting") if item.id == name)
    return run_operation("fitting", op, kwargs)


def _nist_groups(name):
    if name == "SiRstv":
        # https://www.itl.nist.gov/div898/strd/anova/SiRstv.dat
        return [
            [196.3052, 196.1240, 196.1890, 196.2569, 196.3403],
            [196.3042, 196.3825, 196.1669, 196.3257, 196.0422],
            [196.1303, 196.2005, 196.2889, 196.0343, 196.1811],
            [196.2795, 196.1748, 196.1494, 196.1485, 195.9885],
            [196.2119, 196.1051, 196.1850, 196.0052, 196.2090],
        ]
    if name == "AtmWtAg":
        # https://www.itl.nist.gov/div898/strd/anova/AtmWtAg.dat
        return [
            [107.8681568, 107.8681465, 107.8681572, 107.8681785,
             107.8681446, 107.8681903, 107.8681526, 107.8681494,
             107.8681616, 107.8681587, 107.8681519, 107.8681486,
             107.8681419, 107.8681569, 107.8681508, 107.8681672,
             107.8681385, 107.8681518, 107.8681662, 107.8681424,
             107.8681360, 107.8681333, 107.8681610, 107.8681477],
            [107.8681079, 107.8681344, 107.8681513, 107.8681197,
             107.8681604, 107.8681385, 107.8681642, 107.8681365,
             107.8681151, 107.8681082, 107.8681517, 107.8681448,
             107.8681198, 107.8681482, 107.8681334, 107.8681609,
             107.8681101, 107.8681512, 107.8681469, 107.8681360,
             107.8681254, 107.8681261, 107.8681450, 107.8681368],
        ]
    if name == "SmLs07":
        # https://www.itl.nist.gov/div898/strd/anova/SmLs07.dat
        # NIST: nine groups of 21, each a singleton followed by 10 pairs.
        # All cells have the common leading 13 digits "1000000000000".
        patterns = [(.4, .3, .5), (.3, .2, .4), (.5, .4, .6),
                    (.3, .2, .4), (.5, .4, .6), (.3, .2, .4),
                    (.5, .4, .6), (.3, .2, .4), (.5, .4, .6)]
        return [[1000000000000 + first] +
                [1000000000000 + item for _ in range(10) for item in (low, high)]
                for first, low, high in patterns]
    raise ValueError(name)


NIST = {
    # df_between, df_within, SS_between, SS_within, MS_between, MS_within,
    # F, R², residual SD (all 15 published significant digits)
    "SiRstv": (4, 20, .0511462616000000, .216636560000000,
               .0127865654000000, .0108318280000000, 1.18046237440255,
               .190999039051129, .104076068334656),
    "AtmWtAg": (1, 46, 3.63834187500000e-9, 1.04951729166667e-8,
                3.63834187500000e-9, 2.28155932971014e-10, 15.9467335677930,
                .257426544538321, 1.51048314446410e-5),
    "SmLs07": (8, 180, 1.68000000000000, 1.80000000000000,
               .210000000000000, .0100000000000000, 21.0000000000000,
               .482758620689655, .100000000000000),
}


@pytest.mark.parametrize("name", NIST)
def test_nist_strd_one_way_anova_all_certified_digits(name):
    groups = _nist_groups(name)
    values = [value for sample in groups for value in sample]
    labels = [i for i, sample in enumerate(groups, 1) for _ in sample]
    artifact = _run("one_way_anova", values=values, groups=labels)
    v = artifact["values"]
    ref = NIST[name]
    assert (v["df_between"], v["df_within"]) == ref[:2]
    for key, expected in zip(("ss_between", "ss_within", "ms_between", "ms_within",
                              "f_statistic", "r_squared", "residual_standard_deviation"), ref[2:]):
        assert format(v[key], ".14e") == format(expected, ".14e"), (name, key, v[key], expected)
    assert len(artifact["table"]["rows"]) == len(groups) * (len(groups) - 1) // 2
    assert all(0 <= row[5] <= 1 and row[6] <= row[2] <= row[7]
               for row in artifact["table"]["rows"])
    assert readable_result({"version": 1, "latest": artifact}, "fitting")


def test_tukey_studentized_range_nist_handbook_critical_value_and_two_groups():
    # NIST ESH 7.4.7.1: q_(.95;5,10) = 4.65 (two published decimal digits).
    assert inferential._studentized_range_cdf(4.65, 5, 10) == pytest.approx(.95, abs=.0003)
    assert inferential._studentized_range_ppf95(5, 10) == pytest.approx(4.65, abs=.01)
    # With two treatments, q/sqrt(2) has |Student t| distribution.
    assert inferential._studentized_range_cdf(3, 2, 12) == pytest.approx(
        2 * inferential._student_t_cdf(3 / math.sqrt(2), 12) - 1, abs=1e-13)


def test_tukey_kramer_unequal_sample_sizes_and_indistinguishable_means():
    v = _run("one_way_anova", values=[1, 2, 3, 4, 6, 7, 8, 9, 10],
             groups=[1, 1, 1, 2, 2, 3, 3, 3, 3])
    assert v["values"]["df_within"] == 6
    rows = v["table"]["rows"]
    assert len(rows) == 3
    assert all(row[3] > 0 and 0 <= row[5] <= 1 for row in rows)
    assert rows[0][3] != rows[2][3]  # unequal n, Tukey-Kramer SE differs
    same = _run("one_way_anova", values=[1, 2, 3] * 3, groups=[1]*3+[2]*3+[3]*3)
    assert same["values"]["f_statistic"] == 0
    assert all(row[5] == 1 for row in same["table"]["rows"])


def test_mann_whitney_published_exact_rank_table_and_tied_conditional_permutation():
    # Complete separation with n_A=n_B=4: U=0 and exact two-sided 2/C(8,4).
    v = _run("mann_whitney_u", sample_a=[1, 2, 3, 4], sample_b=[5, 6, 7, 8])["values"]
    assert (v["u_a"], v["u_b"]) == (0, 16)
    assert v["p_value"] == pytest.approx(2 / math.comb(8, 4))
    a, b = [1, 2, 2, 3], [2, 3, 4, 4]
    tied = _run("mann_whitney_u", sample_a=a, sample_b=b)["values"]
    # Independent brute-force enumeration of all C(8,4) label placements.
    ranks = inferential._rank(a + b)[0]
    observed = sum(ranks[:4])
    all_sums = [sum(ranks[i] for i in positions) for positions in itertools.combinations(range(8), 4)]
    oracle = min(1, 2 * min(sum(x <= observed for x in all_sums),
                            sum(x >= observed for x in all_sums)) / len(all_sums))
    assert tied["p_value"] == pytest.approx(oracle)
    assert tied["ties_present"] == "true"


def test_wilcoxon_published_diuretic_example_and_conditional_ties():
    # ACCP biosampl.pdf example 4: -110, -550, +100, -90, -50, -10;
    # positive rank sum 4 and two-sided exact p=14/64=0.21875.
    vals = _run("wilcoxon_signed_rank", before=[0] * 6,
                after=[-110, -550, 100, -90, -50, -10])["values"]
    assert (vals["w_plus"], vals["w_minus"], vals["p_value"]) == (4, 17, .21875)
    # Published 8-change worked example: all eight decreasing -> p=2/256.
    v = _run("wilcoxon_signed_rank", before=[7, 6, 8, 5, 9, 7, 6, 8, 7, 5],
             after=[4, 5, 6, 5, 6, 3, 6, 5, 4, 4])["values"]
    assert v["zero_pairs_dropped"] == 2
    assert v["p_value"] == 2 / 256
    tied = _run("wilcoxon_signed_rank", before=[0] * 5,
                after=[1, 1, 2, 2, -3])["values"]
    ranks = inferential._rank([1, 1, 2, 2, 3])[0]
    observed = sum(ranks[:4])
    sums = [sum(r for r, s in zip(ranks, signs) if s)
            for signs in itertools.product([False, True], repeat=5)]
    oracle = min(1, 2 * min(sum(s <= observed for s in sums),
                            sum(s >= observed for s in sums)) / len(sums))
    assert tied["p_value"] == pytest.approx(oracle)
    assert tied["ties_present"] == "true"


def test_both_rank_normal_approximations_use_tie_and_continuity_corrections():
    a, b = [0] * 18 + [1] * 2, [0] * 17 + [2] * 3
    u = _run("mann_whitney_u", sample_a=a, sample_b=b)["values"]
    n1, n2, n = len(a), len(b), len(a) + len(b)
    ties = (35, 2, 3)
    var = n1 * n2 / 12 * (n + 1 - sum(t**3-t for t in ties) / (n * (n - 1)))
    z = max(0, abs(u["u_a"] - n1 * n2 / 2) - .5) / math.sqrt(var)
    assert u["p_value"] == pytest.approx(math.erfc(z / math.sqrt(2)))
    assert "normal" in u["method"]
    changes = [1] * 15 + [-2] * 16
    w = _run("wilcoxon_signed_rank", before=[0] * 31, after=changes)["values"]
    var = 31 * 32 * 63 / 24 - ((15**3-15) + (16**3-16)) / 48
    z = max(0, abs(w["w_plus"] - 31 * 32 / 4) - .5) / math.sqrt(var)
    assert w["p_value"] == pytest.approx(math.erfc(z / math.sqrt(2)))
    assert "normal" in w["method"]


def test_shapiro_royston_published_eleven_observation_example():
    x = [148, 154, 158, 160, 161, 162, 166, 170, 182, 195, 236]
    v = _run("shapiro_wilk", sample=x)["values"]
    # Worked AS R94 example: W=0.78881, p=0.006704.
    assert v["w_statistic"] == pytest.approx(.78881, abs=.00001)
    assert v["p_value"] == pytest.approx(.006704, abs=1e-6)
    assert _run("shapiro_wilk", sample=[1, 2, 3])["values"]["p_value"] == 1
    assert _run("shapiro_wilk", sample=[1, 2, 3, 4, 5, 6, 7, 8])["values"]["p_value"] == pytest.approx(
        .9331651921, abs=2e-7)


def test_pearson_and_spearman_published_examples_with_95pct_intervals():
    x = [16, 24, 18, 17, 23, 27, 32]
    y = [3, 2, 5, 4, 0, 6, 1]
    # Emory mathematics worked example: Spearman rho=-0.1785714.
    v = _run("spearman_correlation", x=x, y=y)["values"]
    assert v["rho"] == pytest.approx(-.17857142857142858)
    assert v["p_value"] == pytest.approx(.7130952380952381)
    assert v["ci95_lower"] < v["rho"] < v["ci95_upper"]
    # Five-case worked example: rho=.8; exact two-sided permutation p=16/120.
    v2 = _run("spearman_correlation", x=[1,2,3,4,5], y=[2,1,4,3,5])["values"]
    assert v2["rho"] == .8
    assert v2["p_value"] == pytest.approx(16 / 120)
    # Eight values with adjacent swaps; Pearson r=19/21.
    pear = _run("pearson_correlation", x=list(range(1, 9)), y=[2, 1, 4, 3, 6, 5, 8, 7])["values"]
    assert pear["r"] == pytest.approx(19 / 21)
    assert pear["ci95_lower"] < pear["r"] < pear["ci95_upper"]
    assert 0 < pear["p_value"] < .01
    # Perfect monotone non-linear correlation is always Spearman rho=1.
    rank = _run("spearman_correlation", x=list(range(1, 9)), y=[2, 3, 5, 8, 13, 21, 34, 55])["values"]
    assert rank["rho"] == 1
    assert rank["p_value"] == pytest.approx(2 / math.factorial(8))


def test_benjamini_hochberg_1995_fifteen_published_p_values_and_bonferroni():
    # Original BH(1995) example, as reproduced in the paper referenced in docs.
    ps = [.0001, .0004, .0019, .0095, .0201, .0278, .0298, .0344, .0459,
          .3240, .4262, .5719, .6528, .7590, 1.0]
    bh = _run("benjamini_hochberg_correction", p_values=ps, alpha=.05)
    bonf = _run("bonferroni_correction", p_values=ps, alpha=.05)
    assert bh["values"]["rejections"] == 4
    assert bonf["values"]["rejections"] == 3
    assert [row[2] for row in bh["table"]["rows"][:4]] == pytest.approx(
        [.0015, .0030, .0095, .035625])
    assert bh["table"]["rows"][5][2] == pytest.approx(.06385714285714286)
    shuffled = _run("benjamini_hochberg_correction", p_values=[.9, .03, .03, .9, 0], alpha=.05)
    assert [row[2] for row in shuffled["table"]["rows"]] == pytest.approx([.9, .05, .05, .9, 0])


@pytest.mark.parametrize("op,inputs,fragment", [
    ("one_way_anova", {"values": [1, 2], "groups": [1, 1]}, "at least two distinct"),
    ("one_way_anova", {"values": [1, 1, 3, 3], "groups": [1, 1, 2, 2]}, "variance is zero"),
    ("one_way_anova", {"values": [1, 2, 3], "groups": [1, 1]}, "one group code"),
    ("one_way_anova", {"values": [1, 2, 3], "groups": [1, 1, 2]}, "at least two observations"),
    ("mann_whitney_u", {"sample_a": [1], "sample_b": [2, 3]}, "at least two"),
    ("mann_whitney_u", {"sample_a": [1, 1], "sample_b": [1, 1]}, "all pooled"),
    ("wilcoxon_signed_rank", {"before": [1, 2], "after": [1, 2]}, "two nonzero"),
    ("wilcoxon_signed_rank", {"before": [1, 2], "after": [2, 3, 4]}, "equal length"),
    ("shapiro_wilk", {"sample": [1, 2]}, "at least 3"),
    ("shapiro_wilk", {"sample": [1, 1, 1]}, "identical"),
    ("pearson_correlation", {"x": [1, 2, 3], "y": [1, 2, 3]}, "four pairs"),
    ("pearson_correlation", {"x": [1, 1, 1, 1], "y": [1, 2, 3, 4]}, "constant"),
    ("spearman_correlation", {"x": [1, 2, 3, 4], "y": [2, 2, 2, 2]}, "constant"),
    ("bonferroni_correction", {"p_values": [1.01], "alpha": .05}, "closed interval"),
    ("benjamini_hochberg_correction", {"p_values": [.1, .2], "alpha": 1}, "strictly between"),
])
def test_invalid_and_degenerate_input_diagnostics(op, inputs, fragment):
    with pytest.raises(ValueError, match=fragment):
        _run(op, **inputs)


def test_registered_examples_are_receipted_and_no_scientific_external_dependency():
    ids = {op.id for op in operations("fitting")}
    assert {"one_way_anova", "mann_whitney_u", "wilcoxon_signed_rank", "shapiro_wilk",
            "pearson_correlation", "spearman_correlation", "bonferroni_correction",
            "benjamini_hochberg_correction"} <= ids
    for op in inferential.OPERATIONS:
        r = _run(op.id, **op.example)
        assert readable_result({"version": 1, "latest": r}, "fitting")
