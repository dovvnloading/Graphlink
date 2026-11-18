"""Linear Algebra pack: analytic identities and independently published test cases.

Reference matrices: Hilbert condition numbers (Higham, Accuracy and Stability
of Numerical Algorithms), Wilkinson W21+ (MathWorks/Cleve Moler), and the
NIST StRD Longley linear-regression observations/certified coefficients.
No NumPy, SciPy or external numerical package is used by the implementation.
"""

import copy
import json
import math
import tomllib
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACKS, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation


OPS = {op.id: op for op in operations("linalg")}


def run(name, **updates):
    inputs = copy.deepcopy(OPS[name].example)
    inputs.update(updates)
    return run_operation("linalg", OPS[name], inputs)


def values(name, **updates):
    return run(name, **updates)["values"]


def unpack(result, key):
    return json.loads(result[key])


def dot(a, b):
    return math.fsum(x*y for x, y in zip(a, b))


def multiply(a, b):
    return [[dot(row, column) for column in zip(*b)] for row in a]


def transpose(a):
    return [list(row) for row in zip(*a)]


def hilbert(n):
    return [[1.0/(i+j+1) for j in range(n)] for i in range(n)]


# NIST/ITL StRD Longley.dat: https://www.itl.nist.gov/div898/strd/lls/data/LINKS/DATA/Longley.dat
# Columns are response y and predictors x1, x2, x3, x4, x5, x6.
_LONGLEY = """
60323 83.0 234289 2356 1590 107608 1947
61122 88.5 259426 2325 1456 108632 1948
60171 88.2 258054 3682 1616 109773 1949
61187 89.5 284599 3351 1650 110929 1950
63221 96.2 328975 2099 3099 112075 1951
63639 98.1 346999 1932 3594 113270 1952
64989 99.0 365385 1870 3547 115094 1953
63761 100.0 363112 3578 3350 116219 1954
66019 101.2 397469 2904 3048 117388 1955
67857 104.6 419180 2822 2857 118734 1956
68169 108.4 442769 2936 2798 120445 1957
66513 110.8 444546 4681 2637 121950 1958
68655 112.6 482704 3813 2552 123366 1959
69564 114.2 502601 3931 2514 125368 1960
69331 115.7 518173 4806 2572 127852 1961
70551 116.9 554894 4007 2827 130081 1962
"""
_LONGLEY_ROWS = [[float(v) for v in line.split()] for line in _LONGLEY.strip().splitlines()]
_LONGLEY_X = [[1.0, *row[1:]] for row in _LONGLEY_ROWS]
_LONGLEY_Y = [row[0] for row in _LONGLEY_ROWS]
_LONGLEY_BETA = [-3482258.63459582, 15.0618722713733, -0.035819179292591,
                 -2.02022980381683, -1.03322686717359, -0.0511041056535807,
                 1829.15146461355]


def test_registration_manifest_examples_receipts_and_diagnostics():
    assert PACKS["linalg"][0] == "Linear Algebra"
    manifest = Path(__file__).resolve().parents[2] / "plugins" / "science_linalg" / "plugin.toml"
    with manifest.open("rb") as handle:
        data = tomllib.load(handle)
    assert data["plugin"]["id"] == "science_linalg"
    assert data["plugin"]["version"] == VERSION
    assert data["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert data["persistence"]["passive_node_kinds"] == ["workbench"]
    assert set(OPS) == {"qr_decomposition", "cholesky_decomposition", "least_squares_qr",
                        "symmetric_eigen", "svd", "matrix_inverse", "matrix_determinant", "pca"}
    for name in OPS:
        result = run(name)
        assert result["provenance"]["packId"] == "linalg"
        assert readable_result({"version": 1, "latest": result}, "linalg")
        assert any("residual" in field or "orthogonality" in field
                   for field in result["values"]), name
        tampered = copy.deepcopy(result)
        field = next(iter(result["values"]))
        tampered["values"][field] = "modified"
        assert not readable_result({"version": 1, "latest": tampered}, "linalg")


def test_qr_rectangular_pivoted_factorization_and_orthogonality():
    matrix = [[1.0, 100.0, 3.0], [2.0, 110.0, -2.0],
              [5.0, 120.0, 1.0], [7.0, 130.0, 0.0]]
    v = values("qr_decomposition", matrix=matrix)
    q, r, perm = (unpack(v, key) for key in ("Q", "R", "column_permutation_zero_based"))
    assert sorted(perm) == [0, 1, 2]
    assert perm[0] == 1
    assert len(q) == 4 and len(q[0]) == 3
    assert v["numerical_rank"] == 3
    assert v["relative_reconstruction_residual"] < 1e-13
    assert v["max_orthogonality_error"] < 1e-13
    for original, recovered in zip([[row[i] for i in perm] for row in matrix], multiply(q, r)):
        assert recovered == pytest.approx(original, abs=2e-12)
    wide = values("qr_decomposition", matrix=[[1., 2., 3.], [4., 5., 6.]])
    assert len(unpack(wide, "Q")) == 2
    assert len(unpack(wide, "R")) == 2
    assert wide["relative_reconstruction_residual"] < 1e-13
    deficient = values("qr_decomposition", matrix=[[1., 2.], [2., 4.], [3., 6.]])
    assert deficient["numerical_rank"] == 1
    assert "rank deficient" in deficient["warning"]


def test_cholesky_analytic_spd_and_reject_non_spd():
    matrix = [[4., 2.], [2., 3.]]
    v = values("cholesky_decomposition", matrix=matrix)
    lower = unpack(v, "L")
    assert lower[0] == pytest.approx([2.0, 0.0])
    assert lower[1][1] == pytest.approx(math.sqrt(2))
    for actual, expected in zip(multiply(lower, transpose(lower)), matrix):
        assert actual == pytest.approx(expected, abs=1e-14)
    assert v["relative_reconstruction_residual"] < 1e-14
    for bad in ([[1., 2.], [2., 1.]], [[1., 2.], [3., 4.]],
                [[1., 1.], [1., 1.]]):
        with pytest.raises(ValueError, match="Cholesky|symmetric|positive|singular"):
            run("cholesky_decomposition", matrix=bad)


def test_qr_least_squares_analytic_full_rank_and_explicit_rank_failure():
    a = [[1., 0.], [1., 1.], [1., 2.]]
    v = values("least_squares_qr", matrix=a, rhs=[1., 2., 2.])
    assert unpack(v, "coefficients") == pytest.approx([7/6, .5], abs=1e-13)
    assert v["residual_sum_squares"] == pytest.approx(1/6, abs=1e-14)
    assert v["residual_l2_norm"] == pytest.approx(math.sqrt(1/6), abs=1e-14)
    assert v["numerical_rank"] == 2
    assert v["degrees_of_freedom"] == 1
    assert v["qr_relative_reconstruction_residual"] < 1e-13
    assert v["qr_max_orthogonality_error"] < 1e-13
    exact = values("least_squares_qr", matrix=[[1., 0.], [1., 1.], [1., 2.]],
                   rhs=[2., 5., 8.])
    assert unpack(exact, "coefficients") == pytest.approx([2., 3.])
    assert exact["residual_l2_norm"] < 1e-13
    with pytest.raises(ValueError, match="rank-deficient|singular"):
        run("least_squares_qr", matrix=[[1., 2.], [2., 4.], [3., 6.]], rhs=[1., 2., 3.])
    with pytest.raises(ValueError, match="at least as many"):
        run("least_squares_qr", matrix=[[1., 2., 3.]], rhs=[1.])
    with pytest.raises(ValueError, match="RHS length"):
        run("least_squares_qr", matrix=[[1., 0.], [1., 1.]], rhs=[1.])


def test_nist_strd_longley_16_observations_six_predictors_certified_values():
    # NIST: 7 parameters, 9 residual degrees of freedom; certified SSE
    # 836424.055505915; intentionally ill-scaled and highly collinear.
    v = values("least_squares_qr", matrix=_LONGLEY_X, rhs=_LONGLEY_Y)
    coefficients = unpack(v, "coefficients")
    assert v["numerical_rank"] == 7 and v["degrees_of_freedom"] == 9
    assert v["condition_number"] > 1e8
    assert "WARNING" in v["warning"]
    for actual, certified in zip(coefficients, _LONGLEY_BETA):
        assert actual == pytest.approx(certified, rel=2e-9, abs=3e-6)
    assert v["residual_sum_squares"] == pytest.approx(836424.055505915, rel=2e-9)
    assert v["residual_l2_norm"] == pytest.approx(math.sqrt(836424.055505915), rel=2e-9)
    assert v["qr_relative_reconstruction_residual"] < 1e-12
    assert v["qr_max_orthogonality_error"] < 1e-12


def test_symmetric_eigen_analytic_and_wilkinson_w21_published_pairs():
    v = values("symmetric_eigen", matrix=[[2., 1.], [1., 2.]])
    assert unpack(v, "eigenvalues") == pytest.approx([3., 1.], abs=1e-14)
    assert v["relative_eigenpair_residual"] < 1e-14
    assert v["max_orthogonality_error"] < 1e-14
    # Wilkinson W_21^+: diagonal 10..0..10, both off-diagonals 1.
    w = [[float(abs(i-10)) if i == j else (1. if abs(i-j) == 1 else 0.)
          for j in range(21)] for i in range(21)]
    eig = values("symmetric_eigen", matrix=w)
    lam = unpack(eig, "eigenvalues")
    # Cleve Moler, Wilkinson's Matrices (2013): two near-coincident top pairs.
    assert lam[:4] == pytest.approx([10.746194182903393, 10.746194182903322,
                                    9.210678647361332, 9.210678647304919], abs=2e-11)
    assert eig["relative_eigenpair_residual"] < 1e-12
    assert eig["max_orthogonality_error"] < 1e-12
    with pytest.raises(ValueError, match="symmetric"):
        run("symmetric_eigen", matrix=[[1., 2.], [0., 1.]])


@pytest.mark.parametrize("matrix,expected", [
    ([[3., 0.], [0., 2.], [0., 0.]], [3., 2.]),
    ([[3., 0., 0.], [0., 2., 0.]], [3., 2.]),
    ([[1., 2.], [2., 4.], [3., 6.]], [math.sqrt(70), 0.]),
])
def test_svd_rectangular_analytic_and_rank_deficient(matrix, expected):
    v = values("svd", matrix=matrix)
    assert unpack(v, "singular_values") == pytest.approx(expected, abs=2e-13)
    assert v["relative_reconstruction_residual"] < 1e-13
    assert v["max_orthogonality_error"] < 1e-12
    if expected[-1] == 0:
        assert v["numerical_rank"] == 1
        assert "infinite" in str(v["condition_number"])
    else:
        assert v["numerical_rank"] == 2
        assert v["condition_number"] == pytest.approx(1.5)
    u, s, vt = (unpack(v, key) for key in ("U", "singular_values", "V_transpose"))
    reconstructed = multiply([[row[j]*s[j] for j in range(len(s))] for row in u], vt)
    for orig, recovered in zip(matrix, reconstructed):
        assert recovered == pytest.approx(orig, abs=2e-12)


def test_inverse_determinant_analytic_and_warnings():
    a = [[4., 7.], [2., 6.]]
    v = values("matrix_inverse", matrix=a)
    inv = unpack(v, "inverse")
    for actual, expected in zip(inv, [[.6, -.7], [-.2, .4]]):
        assert actual == pytest.approx(expected, abs=1e-14)
    assert v["relative_identity_residual"] < 1e-14
    assert v["relative_lu_factor_residual"] < 1e-14
    d = values("matrix_determinant", matrix=a)
    assert d["determinant"] == pytest.approx(10., abs=1e-13)
    assert d["log_absolute_determinant"] == pytest.approx(math.log(10.), abs=1e-14)
    assert d["sign"] == 1 and d["relative_lu_factor_residual"] < 1e-14
    assert values("matrix_determinant", matrix=[[-2.]])["determinant"] == pytest.approx(-2.)
    for bad in ([[1., 2.], [2., 4.]], [[0., 0.], [0., 0.]]):
        with pytest.raises(ValueError, match="singular|rank-deficient"):
            run("matrix_inverse", matrix=bad)
        with pytest.raises(ValueError, match="singular|rank-deficient"):
            run("matrix_determinant", matrix=bad)
    # Non-representable finite determinants retain sign + log rather than NaN/Inf.
    tiny = values("matrix_determinant", matrix=[[1e-200, 0.], [0., 1e-200]])
    assert isinstance(tiny["determinant"], str)
    assert tiny["sign"] == 1
    assert tiny["log_absolute_determinant"] == pytest.approx(2*math.log(1e-200))
    assert tiny["relative_lu_factor_residual"] < 1e-14


def test_hilbert_published_condition_numbers_and_refusal_safety():
    # Higham (2002), Table 28.2: kappa2(H6) ~=1.4951e7, H8 ~=1.5258e10.
    v6 = values("svd", matrix=hilbert(6))
    assert v6["condition_number"] == pytest.approx(1.4951e7, rel=5e-4)
    i6 = values("matrix_inverse", matrix=hilbert(6))
    assert "WARNING" in i6["warning"] or i6["condition_number"] < 1e8
    assert i6["relative_identity_residual"] < 1e-7
    for n in (8, 10):
        decomposition = values("svd", matrix=hilbert(n))
        assert decomposition["relative_reconstruction_residual"] < 1e-12
        assert decomposition["condition_number"] > 1e10
        with pytest.raises(ValueError, match="ill-conditioned"):
            run("matrix_inverse", matrix=hilbert(n))
        with pytest.raises(ValueError, match="ill-conditioned"):
            run("matrix_determinant", matrix=hilbert(n))
        with pytest.raises(ValueError, match="ill-conditioned"):
            run("least_squares_qr", matrix=hilbert(n), rhs=[1.]*n)


def test_pca_center_loadings_scores_explained_variance_and_standardize():
    # One factor exactly: centered first variable [-1,0,1], second twice it.
    a = [[1., 2.], [2., 4.], [3., 6.]]
    v = values("pca", observations=a, scaling="center")
    assert v["numerical_rank"] == 1
    assert unpack(v, "means") == pytest.approx([2., 4.])
    assert unpack(v, "explained_variance") == pytest.approx([5., 0.], abs=2e-13)
    assert unpack(v, "explained_variance_ratio") == pytest.approx([1., 0.], abs=2e-13)
    loadings, scores = unpack(v, "loadings"), unpack(v, "scores")
    assert len(loadings) == 2 and len(scores) == 3
    reconstruction = multiply(scores, transpose(loadings))
    for original, estimated in zip([[-1., -2.], [0., 0.], [1., 2.]], reconstruction):
        assert estimated == pytest.approx(original, abs=1e-13)
    assert v["relative_reconstruction_residual"] < 1e-13
    assert v["max_orthogonality_error"] < 1e-12
    standardized = values("pca", observations=a, scaling="standardize")
    assert unpack(standardized, "explained_variance") == pytest.approx([2., 0.], abs=2e-13)
    assert unpack(standardized, "explained_variance_ratio") == pytest.approx([1., 0.])
    with pytest.raises(ValueError, match="constant"):
        run("pca", observations=[[1., 2.], [3., 2.]], scaling="standardize")
    with pytest.raises(ValueError, match="variation"):
        run("pca", observations=[[0., 0.], [0., 0.]])
    with pytest.raises(ValueError, match="scaling"):
        run("pca", scaling="covariance")


def test_malformed_inputs_finite_bounds_and_32_column_limit():
    for op in ("qr_decomposition", "svd", "matrix_inverse", "cholesky_decomposition"):
        with pytest.raises(ValueError, match="finite"):
            run(op, matrix=[[float("nan")]])
        with pytest.raises(ValueError, match="finite"):
            run(op, matrix=[[True]])
        with pytest.raises(ValueError, match="dimensions"):
            run(op, matrix=[[1.] * 33])
    with pytest.raises(ValueError, match="square"):
        run("matrix_determinant", matrix=[[1., 2.]])
    with pytest.raises(ValueError, match="equal-length"):
        run("qr_decomposition", matrix=[[1.], [1., 2.]])
    with pytest.raises(ValueError, match="exactly"):
        run_operation("linalg", OPS["svd"], {"matrix": [[1.]], "other": "x"})


def test_near_condition_safety_boundary_and_large_32_matrix():
    # Permutation/inverse stress: order 32 within the bounded input domain.
    n = 32
    a = [[3.0 if i == j else (0.25 if abs(i-j) == 1 else 0.0)
          for j in range(n)] for i in range(n)]
    qr = values("qr_decomposition", matrix=a)
    svd = values("svd", matrix=a)
    assert qr["relative_reconstruction_residual"] < 1e-12
    assert qr["max_orthogonality_error"] < 1e-12
    assert svd["relative_reconstruction_residual"] < 1e-12
    assert svd["max_orthogonality_error"] < 1e-12
    assert svd["numerical_rank"] == n
    inverse = values("matrix_inverse", matrix=a)
    assert inverse["relative_identity_residual"] < 1e-12
    for op in ("matrix_inverse", "matrix_determinant", "cholesky_decomposition"):
        with pytest.raises(ValueError, match="ill-conditioned"):
            run(op, matrix=[[1.0, 0.0], [0.0, 1e-12]])
    with pytest.raises(ValueError, match="ill-conditioned"):
        run("least_squares_qr", matrix=[[1.0, 0.0], [0.0, 1e-12]], rhs=[1., 2.])
