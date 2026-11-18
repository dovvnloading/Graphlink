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

def test_studentized_range_32_group_cdf_independent_high_precision_reference():
    # Independent high-accuracy integration also agrees with SciPy's CDF.
    # 80 normal-range nodes miss this reference by ~1.15e-7.
    assert inferential._studentized_range_cdf(5.0, 32, 40) == pytest.approx(
        0.828224730257006, abs=2e-10)


def test_anova_rejects_unrepresentable_nonzero_variances():
    # The F statistic is finite, but SS_W ~ 1e-640 cannot be stored as
    # binary64; reporting all sums as zero would contradict the raw data.
    with pytest.raises(ValueError, match="underflow"):
        _run("one_way_anova", values=[0.0, 1e-320, 0.0, 1e-320],
             groups=[1, 1, 2, 2])

"""Reference checks for Scientific Workbench advanced inference.

Only stdlib and pytest are imported. Published NIST, Minitab, Penn State,
Spector and R worked examples are identified in the scientific reference.
"""


def test_two_way_type_ii_unbalanced_published_reduced_model_oracle():
    # Unbalanced 2x2 fixed-effect cells: independent Type-II reduced model
    # calculations, cross-checked with statsmodels anova_lm(typ=2).
    vals = [8, 10, 12, 13, 14, 18, 19, 21, 23, 25, 27, 26]
    a = [0]*5 + [1]*7
    b = [0]*3 + [1]*2 + [0]*4 + [1]*3
    v = _run("two_way_anova", values=vals, factor_a=a, factor_b=b)["values"]
    assert v["degrees_of_freedom_residual"] == 8
    assert v["ss_factor_a_type_ii"] == pytest.approx(364.03361344537814, abs=1e-11)
    assert v["ss_factor_b_type_ii"] == pytest.approx(67.80504201680672, abs=1e-11)
    assert v["ss_interaction"] == pytest.approx(3.573529411764706, abs=1e-12)
    assert v["residual_sum_squares"] == 25.25
    assert v["f_factor_a"] == pytest.approx(115.33738247774357, abs=1e-11)
    assert v["p_interaction"] == pytest.approx(.3183647046405231, abs=1e-12)


def test_kruskal_wallis_published_tied_example_and_dunn_holm():
    # Clinical Biostats published 12-value tied-rank example:
    # https://www.clinicalbiostats.com/tutorials/undergraduate-statistics-introduction-to-statistics/the-kruskal-wallis-test-for-independent-groups/
    vals = [1]*4 + [2,2,2,4] + [3,3,5,6]
    result = _run("kruskal_wallis", values=vals, groups=[1]*4 + [2]*4 + [3]*4)
    v = result["values"]
    assert v["h_statistic"] == pytest.approx(9.254612546125466, abs=1e-12)
    assert v["p_value"] == pytest.approx(.009781071182572683, abs=1e-12)
    rows = result["table"]["rows"]
    # Independent tie-corrected Dunn variance: N(N+1)/12 - 90/(12*(N-1)).
    variance = 12*13/12 - 90/(12*11)
    assert rows[0][2] == -4.5
    assert rows[0][3] == pytest.approx(-4.5 / math.sqrt(variance/2))
    assert [row[5] for row in rows] == pytest.approx([.13959102183824423, .007531845259897691, .22673060424832295], abs=1e-12)
    assert all(row[4] <= row[5] <= 1 for row in rows)


def test_chi_square_and_fisher_published_contingency_examples():
    # Minitab cookie preference 2x2 example, Pearson no Yates correction.
    c = _run("chi_square_independence", table=[[9, 1], [2, 8]])
    assert c["values"]["chi_square"] == pytest.approx(9.8989898989899, abs=1e-12)
    assert c["values"]["p_value"] == pytest.approx(.0016536951637003395, abs=1e-12)
    f = _run("fisher_exact", table=[[9, 1], [2, 8]])["values"]
    assert f["p_two_sided"] == pytest.approx(.005477494641581329, abs=1e-11)
    # Fisher tea tasting: 2-sided probability-order 34/70 and right-tail 17/70.
    tea = _run("fisher_exact", table=[[3, 1], [1, 3]])["values"]
    assert tea["odds_ratio"] == 9.0
    assert tea["p_two_sided"] == pytest.approx(34/70, abs=1e-13)
    assert tea["p_greater"] == pytest.approx(17/70, abs=1e-13)
    assert c["table"]["rows"][0][2] == 5.5


def test_nist_longley_multiple_regression_certified_digits():
    # NIST StRD https://www.itl.nist.gov/div898/strd/lls/data/LINKS/v-Longley.shtml
    rows = [
        [60323,83.,234289,2356,1590,107608,1947],
        [61122,88.5,259426,2325,1456,108632,1948],
        [60171,88.2,258054,3682,1616,109773,1949],
        [61187,89.5,284599,3351,1650,110929,1950],
        [63221,96.2,328975,2099,3099,112075,1951],
        [63639,98.1,346999,1932,3594,113270,1952],
        [64989,99.0,365385,1870,3547,115094,1953],
        [63761,100.0,363112,3578,3350,116219,1954],
        [66019,101.2,397469,2904,3048,117388,1955],
        [67857,104.6,419180,2822,2857,118734,1956],
        [68169,108.4,442769,2936,2798,120445,1957],
        [66513,110.8,444546,4681,2637,121950,1958],
        [68655,112.6,482704,3813,2552,123366,1959],
        [69564,114.2,502601,3931,2514,125368,1960],
        [69331,115.7,518173,4806,2572,127852,1961],
        [70551,116.9,554894,4007,2827,130081,1962],
    ]
    v = _run("multiple_linear_regression", predictors=[row[1:] for row in rows],
             response=[row[0] for row in rows])["values"]
    certified_coef = [-3482258.63459582,15.0618722713733,-.0358191792925910,
                      -2.02022980381683,-1.03322686717359,-.0511041056535807,1829.15146461355]
    certified_se = [890420.383607373,84.9149257747669,.0334910077722432,
                    .488399681651699,.214274163161675,.226073200069370,455.478499142212]
    for i, (coef, se) in enumerate(zip(certified_coef, certified_se)):
        assert format(v[f"coefficient_{i}"], ".14e") == format(coef, ".14e")
        assert format(v[f"standard_error_{i}"], ".14e") == format(se, ".14e")
        assert v[f"ci95_lower_{i}"] < coef < v[f"ci95_upper_{i}"]
    assert format(v["r_squared"], ".14e") == format(.995479004577296, ".14e")
    assert format(v["residual_standard_deviation"], ".14e") == format(304.854073561965, ".14e")
    assert v["vif_1"] > 1
    assert len([k for k in v if k.startswith("cooks_distance_")]) == 16


def test_spector_logistic_irls_matches_published_statsmodels_output():
    # statsmodels discrete-choice Spector example (GRADE 1 indicates improvement).
    gpa = [2.66,2.89,3.28,2.92,4,2.86,2.76,2.87,3.03,3.92,2.63,3.32,
           3.57,3.26,3.53,2.74,2.75,2.83,3.12,3.16,2.06,3.62,2.89,3.51,
           3.54,2.83,3.39,2.67,3.65,4,3.10,2.39]
    tuce = [20,22,24,12,21,17,17,21,25,29,20,23,23,25,26,19,
            25,19,23,25,22,28,14,26,24,27,17,24,21,23,21,19]
    grade = [0,0,0,0,1,0,0,0,0,1,0,0,0,1,0,0,
             0,0,0,1,0,1,0,0,1,1,1,0,1,1,0,1]
    design = [[v, t, int(i >= 18)] for i, (v, t) in enumerate(zip(gpa, tuce))]
    v = _run("binary_logistic_regression", predictors=design, outcome=grade)["values"]
    published = [-13.0213468581,2.8261125949,.0951576613,2.3786876551]
    errors = [4.9313242136,1.2629410756,.1415542057,1.0645642545]
    for i, (coef, se) in enumerate(zip(published, errors)):
        assert v[f"coefficient_{i}"] == pytest.approx(coef, abs=2e-9)
        assert v[f"standard_error_{i}"] == pytest.approx(se, abs=2e-9)
    assert v["mcfadden_pseudo_r_squared"] == pytest.approx(.374, abs=.0002)
    assert v["odds_ratio_3"] == pytest.approx(10.7907, abs=.0001)


def test_seeded_bootstrap_percentile_bca_and_t_and_proportion_power():
    args = {"sample": [1,2,3,4,5,7,10,11,16,21], "statistic": "mean",
            "replicates": 3000, "seed": 42, "confidence_level": .95}
    first = _run("bootstrap_confidence_interval", **args)["values"]
    second = _run("bootstrap_confidence_interval", **args)["values"]
    assert first == second
    assert first["percentile_lower"] == pytest.approx(4.4)
    assert first["percentile_upper"] == pytest.approx(12.2)
    assert first["bca_lower"] == pytest.approx(4.845876946545298, abs=1e-11)
    assert first["bca_upper"] == pytest.approx(13.1)
    # Noncentral-t independent worked example: d=0.5, two equal groups,
    # two-sided alpha=.05, 80% target -> n=64 per group.
    t = _run("t_test_power", effect_size=.5, alpha=.05, target_power=.8,
             sample_size=30, design="two_sample", tails="two_sided")["values"]
    assert t["required_sample_size_per_group"] == 64
    assert t["achieved_power"] == pytest.approx(.4778965207601648, abs=2e-10)
    assert t["power_at_required_sample_size"] == pytest.approx(.8014595579222752, abs=2e-10)
    pr = _run("proportion_power", proportion_a=.6, proportion_b=.4,
              alpha=.05, target_power=.8, sample_size=100,
              design="two_sample", tails="two_sided")["values"]
    assert pr["required_sample_size_per_group"] == 97
    assert pr["achieved_power"] == pytest.approx(.8122918205911362, abs=1e-12)


@pytest.mark.parametrize("name,args,diagnostic", [
    ("two_way_anova", {"values": [1, 2, 3, 4], "factor_a": [0,0,1,1], "factor_b": [0,0,1,1]}, "every factor combination"),
    ("two_way_anova", {"values": [1,1,3,3,5,5,7,7], "factor_a": [0]*4+[1]*4,
                        "factor_b": [0,0,1,1]*2}, "residual variance is zero"),
    ("kruskal_wallis", {"values": [1]*6, "groups": [0]*3+[1]*3}, "all measurements are tied"),
    ("chi_square_independence", {"table": [[0,0],[1,2]]}, "nonzero row and column"),
    ("fisher_exact", {"table": [[1,2,3],[4,5,6]]}, "2x2"),
    ("multiple_linear_regression", {"predictors": [[i,2*i] for i in range(6)], "response": [2,3,4,2,5,6]}, "rank deficient"),
    ("multiple_linear_regression", {"predictors": [[i] for i in range(5)], "response": [1]*5}, "constant response"),
    ("binary_logistic_regression", {"predictors": [[i] for i in range(20)], "outcome": [0]*10+[1]*10}, "separation"),
    ("binary_logistic_regression", {"predictors": [[i] for i in range(6)], "outcome": [1]*6}, "both outcome classes"),
    ("bootstrap_confidence_interval", {"sample": [1]*6, "statistic": "mean", "replicates": 500, "seed": 0, "confidence_level": .95}, "degenerate"),
    ("t_test_power", {"effect_size": 0, "alpha": .05, "target_power": .8,
                       "sample_size": 20, "design": "two_sample", "tails": "two_sided"}, "nonzero"),
    ("proportion_power", {"proportion_a": .5, "proportion_b": .5, "alpha": .05, "target_power": .8,
                         "sample_size": 20, "design": "two_sample", "tails": "two_sided"}, "distinct"),
])
def test_advanced_inference_invalid_or_separable_inputs(name, args, diagnostic):
    with pytest.raises(ValueError, match=diagnostic):
        _run(name, **args)


def test_advanced_ops_registered_receipted_and_stdlib_only():
    expected = {"two_way_anova", "kruskal_wallis", "chi_square_independence", "fisher_exact",
                "multiple_linear_regression", "binary_logistic_regression",
                "bootstrap_confidence_interval", "t_test_power", "proportion_power"}
    assert expected <= {op.id for op in operations("fitting")}
    for op in inferential.OPERATIONS:
        assert readable_result({"version": 1, "latest": _run(op.id, **op.example)}, "fitting")
