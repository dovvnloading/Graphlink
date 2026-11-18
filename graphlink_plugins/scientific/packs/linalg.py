"""Small, dependency-free real linear algebra for the Scientific Workbench.

Decompositions retain deficient ranks; operations requiring a unique stable
solution refuse deficient or severely ill-conditioned matrices. All reported
errors are *achieved* a posteriori diagnostics, not forward-error guarantees.
"""

from __future__ import annotations

import json
import math
import sys

from graphlink_plugins.scientific.core import Field, Operation, finite

_EPS = sys.float_info.epsilon
_RANK_FACTOR = 8.0
_CONDITION_LIMIT = 1e10
_WARNING_LIMIT = 1e8


def _encoded(value):
    """Matrix and vector artifacts travel as JSON strings: workbench values are scalar."""
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _number(value, label="Linear algebra result"):
    return finite(value, label)


def _shape(a, *, square=False, tall=False, observations=False):
    m, n = len(a), len(a[0])
    if square and m != n:
        raise ValueError("A square matrix is required (at most 32 by 32).")
    if tall and m < n:
        raise ValueError("Least squares requires at least as many rows as columns (m >= n).")
    if observations and m < 2:
        raise ValueError("PCA requires at least two observations.")
    return m, n


def _scaled(a):
    scale = max(abs(x) for row in a for x in row)
    if scale == 0.0:
        return [[0.0] * len(a[0]) for _ in a], 0.0
    return [[x / scale for x in row] for row in a], scale


def _fro(a):
    return math.sqrt(math.fsum(x*x for row in a for x in row))


def _error(a, b):
    return math.sqrt(math.fsum((x-y)**2 for row, compare in zip(a, b)
                               for x, y in zip(row, compare)))


def _orthogonality(columns):
    if not columns:
        return 0.0
    return max(abs(math.fsum(x*y for x, y in zip(columns[i], columns[j])) - (i == j))
               for i in range(len(columns)) for j in range(len(columns)))


def _table(matrix):
    if not matrix:
        raise ValueError("Empty linear algebra table.")
    return {"columns": [f"c{j+1}" for j in range(len(matrix[0]))], "rows": matrix}


def _spectral_info(singular, m, n):
    largest = singular[0] if singular else 0.0
    threshold = _RANK_FACTOR * max(m, n) * _EPS * largest
    rank = sum(s > threshold for s in singular)
    if rank < min(m, n) or not largest:
        return rank, math.inf, threshold
    return rank, largest / singular[-1], threshold


def _condition_text(condition):
    return condition if math.isfinite(condition) else "infinite (rank deficient)"


def _warning(condition):
    if not math.isfinite(condition):
        return "WARNING: rank deficient; a unique stable solution is not available."
    if condition >= _WARNING_LIMIT:
        return (f"WARNING: condition number {condition:.6g}; input roundoff can be "
                "amplified and the reported residual does not certify forward accuracy.")
    return "none"


def _require_stable(singular, m, n, operation):
    rank, condition, _ = _spectral_info(singular, m, n)
    if rank != n:
        raise ValueError(f"{operation}: singular or rank-deficient matrix (rank {rank}/{n}); no unique stable result.")
    if not math.isfinite(condition) or condition > _CONDITION_LIMIT:
        raise ValueError(f"{operation}: badly ill-conditioned matrix (2-norm condition "
                         f"{condition:.6g} > {_CONDITION_LIMIT:.0e}); result refused.")
    return rank, condition


def _complete_orthonormal(existing, size):
    """Complete zero-singular-value columns without pretending they are measured."""
    candidates = [[float(i == j) for i in range(size)] for j in range(size)]
    best = None
    norm_best = 0.0
    for candidate in candidates:
        for _ in range(2):
            for vector in existing:
                projection = math.fsum(x*y for x, y in zip(candidate, vector))
                candidate = [x-projection*y for x, y in zip(candidate, vector)]
        norm = math.hypot(*candidate)
        if norm > norm_best:
            best, norm_best = candidate, norm
    if norm_best < 0.5:
        raise ValueError("SVD orthonormal completion lost numerical precision.")
    return [x / norm_best for x in best]


def _svd_tall(a):
    """One-sided cyclic Jacobi SVD of an m-by-n matrix (m>=n)."""
    m, n = len(a), len(a[0])
    cols = [[a[i][j] for i in range(m)] for j in range(n)]
    right = [[float(i == j) for i in range(n)] for j in range(n)]  # columns
    converged = False
    for _ in range(100):
        rotated = False
        for p in range(n):
            for q in range(p+1, n):
                u, v = cols[p], cols[q]
                alpha = math.fsum(x*x for x in u)
                beta = math.fsum(x*x for x in v)
                if not alpha or not beta:
                    continue
                gamma = math.fsum(x*y for x, y in zip(u, v))
                if abs(gamma) <= 4*_EPS*math.sqrt(alpha)*math.sqrt(beta):
                    continue
                rotated = True
                zeta = (beta-alpha) / (2*gamma)
                t = (1 if zeta >= 0 else -1) / (abs(zeta) + math.hypot(1, zeta))
                c = 1 / math.hypot(1, t)
                s = t*c
                cols[p] = [c*x-s*y for x, y in zip(u, v)]
                cols[q] = [s*x+c*y for x, y in zip(u, v)]
                up, vq = right[p], right[q]
                right[p] = [c*x-s*y for x, y in zip(up, vq)]
                right[q] = [s*x+c*y for x, y in zip(up, vq)]
        if not rotated:
            converged = True
            break
    if not converged:
        raise ValueError("SVD Jacobi iteration did not converge within 100 sweeps.")
    entries = sorted(((math.hypot(*col), col, v) for col, v in zip(cols, right)),
                     key=lambda item: item[0], reverse=True)
    sigma = [item[0] for item in entries]
    rank, _, threshold = _spectral_info(sigma, m, n)
    left_cols = []
    # Very small singular vectors are numerical null-space vectors, not resolved data.
    for s, col, _ in entries:
        left_cols.append([x/s for x in col] if s > threshold else
                         _complete_orthonormal(left_cols, m))
    # Re-orthogonalize just the explicitly null-space columns, never the
    # resolved singular vectors (which could change a close singular triplet).
    sigma = [s if s > threshold else 0.0 for s in sigma]
    u = [[left_cols[j][i] for j in range(n)] for i in range(m)]
    vt = [item[2] for item in entries]
    return u, sigma, vt


def _svd_core(matrix):
    """Economy SVD; internally normalize global magnitude to prevent overflow."""
    m, n = len(matrix), len(matrix[0])
    a, scale = _scaled(matrix)
    if m >= n:
        u, sigma, vt = _svd_tall(a)
    else:
        ut, sigma, vtt = _svd_tall([list(col) for col in zip(*a)])
        u = [list(row) for row in zip(*vtt)]
        vt = [list(row) for row in zip(*ut)]
    if scale == 0:
        sigma = [0.0]*len(sigma)
    else:
        sigma = [_number(s*scale, "Singular value") for s in sigma]
    # Compute diagnostics on the normalized input, not its potentially
    # overflowing physical reconstruction.
    factor = scale if scale else 1.0
    scaled_sigma = [s/factor for s in sigma]
    reconstructed = [[math.fsum(u[i][k]*scaled_sigma[k]*vt[k][j]
                                for k in range(len(sigma))) for j in range(n)] for i in range(m)]
    residual = _error(a, reconstructed) / max(_fro(a), 1e-300)
    u_cols = [[u[i][j] for i in range(m)] for j in range(len(sigma))]
    v_cols = vt
    orth = max(_orthogonality(u_cols), _orthogonality(v_cols))
    return u, sigma, vt, _number(residual), _number(orth)


def _qr_core(matrix, *, balance=False):
    """Householder QR with column pivoting, optionally equilibrating columns."""
    m, n = len(matrix), len(matrix[0])
    a, global_scale = _scaled(matrix)
    if not global_scale:
        global_scale = 1.0
    column_scales = ([max(abs(a[i][j]) for i in range(m)) or 1.0 for j in range(n)]
                     if balance else [1.0]*n)
    if balance:
        a = [[a[i][j]/column_scales[j] for j in range(n)] for i in range(m)]
    perm = list(range(n))
    reflectors = []
    k = min(m, n)
    for col in range(k):
        pivot = max(range(col, n), key=lambda j: math.fsum(a[i][j]**2 for i in range(col, m)))
        if pivot != col:
            for row in a:
                row[col], row[pivot] = row[pivot], row[col]
            perm[col], perm[pivot] = perm[pivot], perm[col]
        vec = [a[i][col] for i in range(col, m)]
        length = math.hypot(*vec)
        if not length:
            reflectors.append((col, []))
            continue
        vec[0] += math.copysign(length, vec[0])
        vnorm = math.hypot(*vec)
        vec = [x/vnorm for x in vec]
        for j in range(col, n):
            projection = 2*math.fsum(vec[i-col]*a[i][j] for i in range(col, m))
            for i in range(col, m):
                a[i][j] -= projection*vec[i-col]
        reflectors.append((col, vec))
    q = [[float(i == j) for j in range(k)] for i in range(m)]
    for col, vec in reversed(reflectors):
        if not vec:
            continue
        for j in range(k):
            projection = 2*math.fsum(vec[i-col]*q[i][j] for i in range(col, m))
            for i in range(col, m):
                q[i][j] -= projection*vec[i-col]
    # R is in the original matrix's physical units, including the permutation.
    # For the solve return the normalized upper triangle separately, to avoid
    # unnecessary overflow when the original matrix is very large.
    r_scaled = [[a[i][j] if i <= j else 0.0 for j in range(n)] for i in range(k)]
    r = [[_number(r_scaled[i][j] * column_scales[perm[j]] * global_scale,
                  "QR upper triangle") for j in range(n)] for i in range(k)]
    initial, scale = _scaled(matrix)
    reconstructed = [[math.fsum(q[i][h] * r_scaled[h][j] * column_scales[perm[j]]
                               for h in range(k)) for j in range(n)] for i in range(m)]
    # Original pivoted matrix, in the same global scaling as reconstructed.
    target = [[initial[i][perm[j]] for j in range(n)] for i in range(m)]
    residual = _error(target, reconstructed) / max(_fro(initial), 1e-300)
    orth = _orthogonality([[q[i][j] for i in range(m)] for j in range(k)])
    return q, r, perm, _number(residual), _number(orth), r_scaled, column_scales, global_scale


def _jacobi_symmetric(matrix):
    """Cyclic two-sided Jacobi for real symmetric matrices."""
    n = len(matrix)
    a, scale = _scaled(matrix)
    for i in range(n):
        for j in range(i+1, n):
            if abs(a[i][j]-a[j][i]) > 1e-12:
                raise ValueError("Symmetric eigen/Cholesky operation requires a symmetric matrix.")
            a[i][j] = a[j][i] = (a[i][j]+a[j][i])/2
    original = [row[:] for row in a]
    v = [[float(i == j) for j in range(n)] for i in range(n)]
    converged = False
    for _ in range(100):
        if max((abs(a[i][j]) for i in range(n) for j in range(i+1, n)), default=0.0) <= 8*_EPS:
            converged = True
            break
        for p in range(n):
            for q in range(p+1, n):
                off = a[p][q]
                if abs(off) <= _EPS:
                    continue
                tau = (a[q][q]-a[p][p])/(2*off)
                t = (1 if tau >= 0 else -1)/(abs(tau)+math.hypot(1, tau))
                c = 1/math.hypot(1, t)
                s = t*c
                a[p][p] -= t*off
                a[q][q] += t*off
                a[p][q] = a[q][p] = 0.0
                for r in range(n):
                    if r not in (p, q):
                        x, y = a[r][p], a[r][q]
                        a[r][p] = a[p][r] = c*x-s*y
                        a[r][q] = a[q][r] = s*x+c*y
                    x, y = v[r][p], v[r][q]
                    v[r][p], v[r][q] = c*x-s*y, s*x+c*y
    if not converged:
        raise ValueError("Symmetric Jacobi eigen iteration failed to converge.")
    order = sorted(range(n), key=lambda i: a[i][i], reverse=True)
    eigen_scaled = [a[i][i] for i in order]
    eigenvalues = [_number(value*scale, "Eigenvalue") for value in eigen_scaled]
    vectors = [[v[i][j] for j in order] for i in range(n)]
    residual = math.sqrt(math.fsum((math.fsum(original[i][h]*vectors[h][j]
                                               for h in range(n)) - eigen_scaled[j]*vectors[i][j])**2
                                    for i in range(n) for j in range(n))) / max(_fro(original), 1e-300)
    orth = _orthogonality([[vectors[i][j] for i in range(n)] for j in range(n)])
    return eigenvalues, vectors, _number(residual), _number(orth)


def _qr(data):
    a = data["matrix"]
    m, n = _shape(a)
    q, r, permutation, residual, orth, *_ = _qr_core(a)
    singular = _svd_core(a)[1]
    rank, condition, _ = _spectral_info(singular, m, n)
    return {"summary": "Column-pivoted economy Householder QR: A[:, permutation] = Q R.",
            "values": {"rows": m, "columns": n, "numerical_rank": rank,
                       "condition_number": _condition_text(condition), "warning": _warning(condition),
                       "column_permutation_zero_based": _encoded(permutation),
                       "Q": _encoded(q), "R": _encoded(r),
                       "relative_reconstruction_residual": residual,
                       "max_orthogonality_error": orth}, "table": _table(q)}


def _cholesky(data):
    matrix = data["matrix"]
    n, _ = _shape(matrix, square=True)
    eig, _, _, _ = _jacobi_symmetric(matrix)
    if eig[-1] <= 0:
        raise ValueError("Cholesky requires a strictly positive-definite symmetric matrix.")
    singular = _svd_core(matrix)[1]
    _, condition = _require_stable(singular, n, n, "Cholesky")
    a, scale = _scaled(matrix)
    lower = [[0.0]*n for _ in range(n)]
    for i in range(n):
        for j in range(i+1):
            entry = a[i][j] - math.fsum(lower[i][h]*lower[j][h] for h in range(j))
            if i == j:
                if entry <= 0:
                    raise ValueError("Cholesky requires positive pivots; matrix is not numerically positive definite.")
                lower[i][j] = math.sqrt(entry)
            else:
                lower[i][j] = entry / lower[j][j]
    recon = [[math.fsum(lower[i][h]*lower[j][h] for h in range(n)) for j in range(n)] for i in range(n)]
    residual = _error(a, recon) / max(_fro(a), 1e-300)
    physical = [[_number(x*math.sqrt(scale)) for x in row] for row in lower]
    return {"summary": "Lower-triangular Cholesky factor L of A = L L^T.",
            "values": {"order": n, "L": _encoded(physical), "condition_number": condition,
                       "warning": _warning(condition), "relative_reconstruction_residual": _number(residual)},
            "table": _table(physical)}


def _least_squares(data):
    matrix, b = data["matrix"], data["rhs"]
    m, n = _shape(matrix, tall=True)
    if len(b) != m:
        raise ValueError("Least squares RHS length must match the matrix row count.")
    singular = _svd_core(matrix)[1]
    rank, condition = _require_stable(singular, m, n, "Least squares")
    q, _, perm, qr_res, orth, r, colscales, global_scale = _qr_core(matrix, balance=True)
    rhs_scale = max(map(abs, b)) or 1.0
    normalized_b = [v/rhs_scale for v in b]
    projected = [math.fsum(q[i][j]*normalized_b[i] for i in range(m)) for j in range(n)]
    z = [0.0]*n
    for i in range(n-1, -1, -1):
        if abs(r[i][i]) < _RANK_FACTOR*max(m, n)*_EPS:
            raise ValueError("Least squares: QR diagonal is numerically zero (rank deficient).")
        z[i] = (projected[i] - math.fsum(r[i][j]*z[j] for j in range(i+1, n))) / r[i][i]
    coefficients = [0.0]*n
    for j in range(n):
        original_col = perm[j]
        # global_scale * colscales[original_col] is the physical column scale.
        coefficients[original_col] = _number((z[j] * rhs_scale / global_scale) /
                                             colscales[original_col], "Least-squares coefficient")
    # Achieved residual in input units, computed with correctly rounded fsum.
    residuals = [_number(math.fsum(matrix[i][j]*coefficients[j] for j in range(n)) - b[i],
                         "Least-squares residual") for i in range(m)]
    residual_norm = math.hypot(*residuals)
    if not math.isfinite(residual_norm):
        raise ValueError("Least-squares residual norm exceeds the numerical range.")
    # Squaring the response scale overflows even for a perfect fit with
    # exactly zero residuals. Square only the achieved residual norm.
    sum_squares = _number(residual_norm * residual_norm, "Residual sum of squares")
    if residual_norm and sum_squares == 0:
        raise ValueError("Least-squares residual sum of squares underflows the numerical range.")
    a_norm = _fro(_scaled(matrix)[0]) * global_scale
    x_norm = math.hypot(*coefficients)
    b_norm = math.hypot(*b)
    denominator = a_norm*x_norm + b_norm
    if not math.isfinite(denominator):
        raise ValueError("Least squares residual scale is outside finite numeric range.")
    relative = residual_norm/denominator if denominator else 0.0
    result = {"summary": "Full-column-rank least-squares solution by column-scaled, pivoted Householder QR.",
              "values": {"rows": m, "columns": n, "numerical_rank": rank,
                         "condition_number": condition, "warning": _warning(condition),
                         "coefficients": _encoded(coefficients), "residuals": _encoded(residuals),
                         "residual_l2_norm": _number(residual_norm),
                         "residual_sum_squares": _number(sum_squares),
                         "relative_backward_residual": _number(relative),
                         "qr_relative_reconstruction_residual": qr_res,
                         "qr_max_orthogonality_error": orth,
                         "degrees_of_freedom": m-n},
              "table": {"columns": ["coefficient", "value"],
                        "rows": [[f"x{j+1}", val] for j, val in enumerate(coefficients)]}}
    return result


def _eigen(data):
    matrix = data["matrix"]
    n, _ = _shape(matrix, square=True)
    eig, vec, residual, orth = _jacobi_symmetric(matrix)
    return {"summary": "Symmetric Jacobi eigenpairs, eigenvalues in descending order.",
            "values": {"order": n, "eigenvalues": _encoded(eig), "eigenvectors_columns": _encoded(vec),
                       "relative_eigenpair_residual": residual, "max_orthogonality_error": orth},
            "table": {"columns": ["component", "eigenvalue"],
                      "rows": [[str(j+1), eig[j]] for j in range(n)]}}


def _svd(data):
    a = data["matrix"]
    m, n = _shape(a)
    u, sigma, vt, residual, orth = _svd_core(a)
    rank, condition, threshold = _spectral_info(sigma, m, n)
    return {"summary": "Economy one-sided Jacobi SVD, A = U diag(s) V^T; rank-deficient factors remain inspectable.",
            "values": {"rows": m, "columns": n, "U": _encoded(u), "singular_values": _encoded(sigma),
                       "V_transpose": _encoded(vt), "numerical_rank": rank,
                       "numerical_rank_cutoff": threshold, "condition_number": _condition_text(condition),
                       "warning": _warning(condition), "relative_reconstruction_residual": residual,
                       "max_orthogonality_error": orth},
            "table": {"columns": ["component", "singular value"],
                      "rows": [[str(j+1), sigma[j]] for j in range(len(sigma))]}}


def _lu(a):
    """Pivoted LU of the globally scaled matrix, with original-row permutation."""
    n = len(a)
    scaled, scale = _scaled(a)
    if scale == 0:
        raise ValueError("Matrix is singular: every entry is zero.")
    lu = [row[:] for row in scaled]
    perm = list(range(n))
    parity = 1
    for j in range(n):
        pivot = max(range(j, n), key=lambda i: abs(lu[i][j]))
        if abs(lu[pivot][j]) <= _EPS:
            raise ValueError("LU factorization encountered a singular pivot.")
        if pivot != j:
            lu[pivot], lu[j] = lu[j], lu[pivot]
            perm[pivot], perm[j] = perm[j], perm[pivot]
            parity *= -1
        for i in range(j+1, n):
            lu[i][j] /= lu[j][j]
            for k in range(j+1, n):
                lu[i][k] -= lu[i][j]*lu[j][k]
    # L has unit diagonal and stores entries below the diagonal; U is above.
    # The factorization residual is evaluated without rounding the original scale.
    reconstructed = [[math.fsum((1.0 if i == h else lu[i][h]) * lu[h][j]
                                      for h in range(min(i, j)+1))
                      for j in range(n)] for i in range(n)]
    residual = _error([scaled[i] for i in perm], reconstructed)/max(_fro(scaled),1e-300)
    return lu, perm, parity, scale, _number(residual)


def _lu_solve(lu, perm, b):
    n = len(b)
    y = [0.0]*n
    for i in range(n):
        y[i] = b[perm[i]] - math.fsum(lu[i][j]*y[j] for j in range(i))
    x = [0.0]*n
    for i in range(n-1,-1,-1):
        x[i] = (y[i] - math.fsum(lu[i][j]*x[j] for j in range(i+1,n))) / lu[i][i]
    return x


def _inverse(data):
    a = data["matrix"]
    n, _ = _shape(a, square=True)
    singular = _svd_core(a)[1]
    _, condition = _require_stable(singular, n, n, "Inverse")
    lu, perm, _, scale, lu_res = _lu(a)
    columns = [_lu_solve(lu, perm, [float(i == j) for i in range(n)]) for j in range(n)]
    inverse = [[_number(columns[j][i]/scale, "Inverse entry") for j in range(n)] for i in range(n)]
    scaled, _ = _scaled(a)
    check = [[math.fsum(scaled[i][k]*columns[j][k] for k in range(n)) for j in range(n)]
             for i in range(n)]
    residual = math.sqrt(math.fsum((check[i][j]-(i==j))**2 for i in range(n) for j in range(n)))/math.sqrt(n)
    return {"summary": "Matrix inverse by pivoted LU, refused if condition exceeds the safety limit.",
            "values": {"order": n, "inverse": _encoded(inverse), "condition_number": condition,
                       "warning": _warning(condition), "relative_identity_residual": _number(residual),
                       "relative_lu_factor_residual": lu_res}, "table": _table(inverse)}


def _determinant(data):
    a = data["matrix"]
    n, _ = _shape(a, square=True)
    singular = _svd_core(a)[1]
    _, condition = _require_stable(singular, n, n, "Determinant")
    lu, _, parity, scale, residual = _lu(a)
    sign = parity
    logabs = n*math.log(scale)
    for i in range(n):
        sign *= -1 if lu[i][i] < 0 else 1
        logabs += math.log(abs(lu[i][i]))
    # Retain sign + log magnitude even when the determinant is not a binary64 number.
    if math.log(sys.float_info.min) - 40 < logabs < math.log(sys.float_info.max):
        det = _number(sign*math.exp(logabs), "Determinant")
    else:
        det = "outside finite binary64 range (use sign and log_absolute_determinant)"
    return {"summary": "Pivoted LU determinant with sign and natural-log magnitude.",
            "values": {"order": n, "determinant": det, "sign": sign,
                       "log_absolute_determinant": _number(logabs),
                       "condition_number": condition, "warning": _warning(condition),
                       "relative_lu_factor_residual": residual}}


def _pca(data):
    a, scaling = data["observations"], data["scaling"]
    m, n = _shape(a, observations=True)
    if scaling not in ("center", "standardize"):
        raise ValueError("PCA scaling must be 'center' or 'standardize'.")
    matrix, global_scale = _scaled(a)
    if not global_scale:
        raise ValueError("PCA needs variation; the all-zero data matrix is constant.")
    means_scaled = [math.fsum(matrix[i][j]/m for i in range(m)) for j in range(n)]
    centered = [[matrix[i][j]-means_scaled[j] for j in range(n)] for i in range(m)]
    stdev = [math.sqrt(math.fsum(row[j]**2 for row in centered)/(m-1)) for j in range(n)]
    if all(s == 0 for s in stdev):
        raise ValueError("PCA needs at least one varying column.")
    if scaling == "standardize" and any(s <= _RANK_FACTOR*m*_EPS for s in stdev):
        raise ValueError("PCA standardization refused: constant or numerically constant feature.")
    factors = stdev if scaling == "standardize" else [1.0]*n
    work = [[centered[i][j]/factors[j] for j in range(n)] for i in range(m)]
    u, singular, vt, residual, orth = _svd_core(work)
    k = len(singular)
    loadings = [[vt[c][j] for c in range(k)] for j in range(n)]
    scores_scaled = [[u[i][j]*singular[j] for j in range(k)] for i in range(m)]
    # Center mode returns original data units; standardize returns z-scores.
    unit = global_scale if scaling == "center" else 1.0
    scores = [[_number(x*unit, "PCA score") for x in row] for row in scores_scaled]
    variances = [_number(((s*unit)**2)/(m-1), "Explained variance") for s in singular]
    total = math.fsum(variances)
    explained = [v/total for v in variances]
    means = [_number(x*global_scale, "PCA mean") for x in means_scaled]
    # Feature standard deviations describe the original measurements in both
    # modes; only the PCA score normalization depends on the chosen scaling.
    feature_scales = [_number(s*global_scale, "PCA standard deviation") for s in stdev]
    rank, condition, _ = _spectral_info(singular, m, n)
    return {"summary": "PCA by economy SVD of column-centered data (optional sample-standard-deviation scaling).",
            "values": {"observations": m, "features": n, "components": k, "numerical_rank": rank,
                       "condition_number": _condition_text(condition), "warning": _warning(condition),
                       "scaling": scaling, "means": _encoded(means),
                       "feature_standard_deviations": _encoded(feature_scales),
                       "loadings": _encoded(loadings), "scores": _encoded(scores),
                       "explained_variance": _encoded(variances),
                       "explained_variance_ratio": _encoded(explained),
                       "relative_reconstruction_residual": residual,
                       "max_orthogonality_error": orth}, "table": _table(scores)}


_M = (Field("matrix", "Real numeric matrix", "matrix"),)
_S = (Field("matrix", "Square real matrix", "matrix"),)

OPERATIONS = (
    Operation("qr_decomposition", "Pivoted QR decomposition",
              "Economy Householder QR with column permutation, rank and reconstruction/orthogonality diagnostics.",
              _M, {"matrix": [[1., 2.], [3., 4.], [5., 6.]]}, _qr),
    Operation("cholesky_decomposition", "Cholesky decomposition",
              "Lower-triangular factor of a symmetric positive-definite matrix.",
              _S, {"matrix": [[4., 2.], [2., 3.]]}, _cholesky),
    Operation("least_squares_qr", "Least squares (QR)",
              "Full-rank overdetermined linear least squares, reporting achieved residual and rank.",
              (Field("matrix", "Design matrix", "matrix"),
               Field("rhs", "Response vector", "vector")),
              {"matrix": [[1., 0.], [1., 1.], [1., 2.]], "rhs": [1., 2., 2.]},
              _least_squares),
    Operation("symmetric_eigen", "Symmetric eigenvalues and eigenvectors",
              "Real symmetric Jacobi eigenvalue iteration with orthogonality diagnostics.",
              _S, {"matrix": [[2., 1.], [1., 2.]]}, _eigen),
    Operation("svd", "Singular value decomposition",
              "Economy one-sided Jacobi SVD, 2-norm condition number, numerical rank and residual.",
              _M, {"matrix": [[3., 0.], [0., 2.], [0., 0.]]}, _svd),
    Operation("matrix_inverse", "Matrix inverse",
              "Inverse from pivoted LU, refusing singular or dangerously ill-conditioned matrices.",
              _S, {"matrix": [[2., 1.], [1., 3.]]}, _inverse),
    Operation("matrix_determinant", "Matrix determinant",
              "Signed determinant and log magnitude with condition warnings and LU residual.",
              _S, {"matrix": [[2., 1.], [1., 3.]]}, _determinant),
    Operation("pca", "Principal component analysis",
              "Centered or standardized PCA with scores, loadings and explained variance.",
              (Field("observations", "Rows: observations, columns: features", "matrix"),
               Field("scaling", "Scaling: center or standardize", "text")),
              {"observations": [[1., 2.], [2., 4.], [3., 6.], [4., 8.]], "scaling": "center"},
              _pca),
)
