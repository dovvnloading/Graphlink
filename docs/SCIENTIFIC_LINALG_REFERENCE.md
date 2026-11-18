# Linear Algebra — Scientific Workbench reference

**Plugin:** `science_linalg` · **Pack:** `linalg` · **Workbench version:** `1.1.0` · **Dependencies:** Python standard library only.

Enable **Linear Algebra** as its own Scientific Workbench plugin in Settings → Plugins. Like the Numerical Methods (`science_numerics`) plugin, it uses `register_pack`, the generic workbench frontend, `graph.read` / `graph.mutate` grants, passive `workbench` node persistence, and the existing validated-input, provenance-hash, undo, and JSON export framework. The old `mathematics.linear_system` solve remains unchanged.

## Data contract, numerical policy, and limitations

Inputs are finite real binary64 numbers. Matrix fields are rectangular arrays of **1–1,024 rows**, **1–32 columns**, and at most **4,096 cells**; square operations are limited to order **32**. Only the least-squares response vector must match the number of matrix rows. PCA needs at least two observations. The workbench requires every declared field and refuses nonfinite entries, booleans in numeric fields, ragged matrices, and unexpected fields.

The shared artifact schema permits one table and scalar/text `values`, **not nested arrays as direct values**. Factor matrices, coefficient vectors, components and scores are therefore serialized as **compact JSON strings** in the named `values` keys; `json.loads` reconstructs their numerical arrays. Where useful, the table also shows the primary factor or tabular coefficients/scores. Matrices use row-major layout and **zero-based** column permutation indices. Returned `Q` is economy-sized; `R` has `min(m,n)` rows, and `A[:, permutation] = Q @ R`. SVD uses `U` (m × k), `singular_values` (k), and `V_transpose` (k × n), where `k = min(m,n)`. Eigenvectors and PCA loadings are stored as **columns** in matrices.

All sensitive solve operations (**least squares, Cholesky, inverse, determinant**) refuse a rank-deficient matrix or a computed 2-norm condition number **greater than 1e10**. The numerical rank cutoff is `8 * max(m,n) * machine_epsilon * largest_singular_value` (machine epsilon ≈ 2.22e-16). Above condition **1e8**, accepted results carry an explicit `WARNING` about roundoff amplification. A zero-row or exactly singular input yields a clear error rather than a fabricated result. Decompositions (QR, SVD, eigenpairs) and PCA remain usable for rank-deficient matrices **when the decomposition is meaningful**; they report numerical rank, infinite condition when appropriate, and still check reconstruction/orthogonality. A rank-deficient *decomposition* is not an attempted unstable solve.

Residuals are calculated **after** factorization with normalized Frobenius norms where appropriate. They are observed backward diagnostics, **not certified forward-error bounds**. Relative reconstruction residuals take the form `||A - reconstructed_A||_F / ||A||_F`, using permutation where relevant. Orthogonality error is the largest absolute entry of `QᵀQ − I` or the analogous `UᵀU−I`, `VVᵀ−I`. A relative eigenpair residual is `||AV − V diag(lambda)||_F / ||A||_F`. An inverse's relative identity residual is `||AA_inverse − I||_F / sqrt(n)`. The least-squares solver reports the achieved `||Ax-b||_2`, its squared sum and `||Ax-b||_2 / (||A||_F ||x||_2 + ||b||_2)`. A determinant reports **LU reconstruction residual** (not a fictitious scalar residual); all results carry at least one achieved residual or orthogonality error. No routine claims a user-requested tolerance was satisfied unless it was actually checked. Iterations and arithmetic failures raise `ValueError` and do not replace the previous successful workbench result.

## Available operations

| Operation ID | Inputs | Outputs and numerical method |
| --- | --- | --- |
| `qr_decomposition` | `matrix` | Column-pivoted **Householder** economy `Q`, `R`, `column_permutation_zero_based`, numerical rank, condition warning, reconstruction residual, orthogonality error. Accepts tall, square, or wide matrices. |
| `cholesky_decomposition` | square `matrix` | Symmetry and positive-definiteness checks; lower triangular `L` with `A = LLᵀ`; 2-norm condition, LU-free reconstruction residual. Refuses non-SPD and ill-conditioned matrices. |
| `least_squares_qr` | design `matrix`, `rhs` vector | **Column-scaled, column-pivoted Householder QR** without normal equations; unscaled `coefficients`, row `residuals`, numerical rank, degrees of freedom, residual L2 and RSS, backward residual, QR factor/orthogonality errors. Requires `m >= n`, full column rank and condition ≤ 1e10. |
| `symmetric_eigen` | square symmetric `matrix` | Cyclic **Jacobi** eigenvalues (descending) and eigenvectors as columns, eigenpair residual and eigenvector orthogonality error. Rejects materially nonsymmetric input and failure to converge. |
| `svd` | `matrix` | Economy **one-sided Jacobi** singular value decomposition, largest first, `U`, `singular_values`, `V_transpose`, 2-norm condition number, numerical rank and cutoff, reconstruction/orthogonality errors. Handles wide matrices by transposition and returns an orthonormal null-space completion for zero singular values. |
| `matrix_inverse` | square `matrix` | Partial-pivoting LU inverse, 2-norm condition warning, identity residual and LU factor residual. Ill-conditioned or singular inputs refused. |
| `matrix_determinant` | square `matrix` | Partial-pivoting LU signed determinant, `sign`, `log_absolute_determinant`, condition warning and LU factor residual. If determinant underflows or overflows binary64, `determinant` is explanatory text and the sign/log remain available. Singular or unsafe matrices are refused. |
| `pca` | `observations` matrix, `scaling` text | **SVD of centered data**, with optional `"standardize"` sample-standard-deviation scaling (otherwise `"center"`). Returns `means`, `feature_standard_deviations`, **loadings** (columns), **scores** (observations × components), **explained variance**, **explained variance ratio**, numerical rank, condition warning, reconstruction/orthogonality errors. Constant features cannot be standardized. |

For centered PCA, the explained variance is `sigma_j**2 / (m-1)` in the original feature units squared; for standardized PCA it is the variance of unit-standard-deviation features. Signs of eigenvectors, singular vectors, and PCA axes are arbitrary; comparing them requires allowing a consistent per-component sign flip. Closely clustered eigenvalues also admit rotated eigenspaces. Retained zero singular values do not denote measured structure.

### Example: least squares

Input values (as ordinary, **not** JSON-encoded, workbench fields):

```json
{"matrix": [[1,0], [1,1], [1,2]], "rhs": [1,2,2]}
```

Output `coefficients` (JSON string parsing to a vector) is approximately `[1.16666666666667, 0.5]`, numerical rank `2`, achieved residual L2 norm `sqrt(1/6)` ≈ `0.40824829046`, and residual sum of squares `1/6`. This is a legitimate nonzero least-squares residual, **not** a convergence failure.

### Failure behaviour

- A non-symmetric matrix cannot be used by Cholesky or symmetric eigenpairs; a symmetric matrix with a nonpositive Cholesky pivot is not positive definite.
- A singular design matrix, or a square matrix with dependent rows/columns, cannot produce a unique least-squares/inverse/determinant answer. These operations fail with an explicit rank/condition message.
- Hilbert matrices of order 8 or greater have 2-norm condition numbers above `1e10`: inverse, determinant and full-rank least-squares are **refused**; an SVD may still report their singular spectrum and warning.
- A numerically constant column prevents PCA standardization. PCA on centered data with deficient rank still reports the variance explained by the nonzero components, with the redundant component having zero explained variance.
- Outputs outside finite binary64 range are rejected or, for the determinant alone, represented by sign and log magnitude rather than infinities/NaNs.

## Published and analytic validation

The regression suite `backend/tests/test_scientific_linalg.py` checks all operation examples, workbench receipt tamper detection and plugin metadata; analytically known QR, SPD/Cholesky, eigenvalue, SVD, inverse/determinant and PCA results; near-singular refusals; rectangular/wide cases; and a 32 × 32 bound case. It embeds these **independent** reference problems (without adding runtime dependencies):

| Reference | Check |
| --- | --- |
| **Hilbert matrices** | H6 published 2-norm condition ≈ **1.4951×10⁷**, H8 ≈ **1.5258×10¹⁰**; H6 inverse residual is small; H8/H10 are refused by inverse, determinant, and least squares. See Higham, *Accuracy and Stability of Numerical Algorithms* (2nd ed.), Table 28.2. [Reference table](https://pages.stat.wisc.edu/~bwu62/771/hingham2002.pdf). |
| **Wilkinson symmetric W21+** | Tridiagonal diagonal entries 10,9,…,0,…,9,10 and adjacent off-diagonal entries 1. The two largest eigenvalues are approximately **10.746194182903393** and **10.746194182903322**; Jacobi reports eigenpair residual/orthogonality and checks four clustered leading eigenvalues. [MathWorks: Wilkinson's Matrices](https://blogs.mathworks.com/cleve/2013/04/15/wilkinsons-matrices-2/). |
| **NIST StRD Longley** | All **16 observations**, six predictors and an intercept (seven parameters) from [NIST Longley.dat](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/DATA/Longley.dat). QR least squares estimates intercept **−3,482,258.63459582**, year coefficient **1,829.15146461355**, six other certified coefficients, residual sum of squares **836,424.055505915**, and nine residual degrees of freedom; condition warning expected. [NIST certified results](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/v-Longley.shtml). |

Reference values are independently published and embedded in the tests so they run offline. Their comparison tolerances recognize binary64 input representation and conditioning. The NIST raw coefficients are particularly sensitive to numerical stability; the implementation scales columns and avoids normal equations.

## Running locally

```sh
python -m pytest -n0 backend/tests/test_scientific_linalg.py
python -m pytest -n0 backend/tests/test_scientific_workbench.py backend/tests/test_scientific_numerics.py
```

The `-n0` flag is useful in repository setups with pytest-xdist; it is not a runtime requirement of this pack.

### Independent correctness audit: large exact fits and PCA feature units

**Least squares:** The residual sum of squares is `RSS = Σ(y_i−(Ax)_i)²` (squared response units), and is exactly zero for an exact fit. Forming `(max(|y|))²` before multiplying by a normalized zero RSS incorrectly overflowed for otherwise representable 1e200-scale measurements. RSS is now obtained from the achieved residual L2 norm and squared **only after** checking the finite range. Thus the fit `A=[[1],[0]]`, `y=[1e200,0]` returns coefficient `1e200`, residual L2 norm `0` (response units) and RSS `0` (response units squared). A genuinely unrepresentable nonzero RSS still raises `ValueError` rather than serializing infinity. [NIST StRD least-squares glossary](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/c-NoInt2.shtml).

**PCA:** `feature_standard_deviations` now means the **measured sample standard deviation of each input feature**, in original feature units, in *both* `center` and `standardize` modes. Previously centered PCA incorrectly returned an array of ones, confusing its scale factors (used only for standardizing) with measured standard deviations. For observations `[[1,2],[2,4],[3,6],[4,8]]`, the values are `[sqrt(5/3), sqrt(20/3)]`, approximately `[1.29099445, 2.58198890]` in their respective feature units, not `[1,1]`. Centered scores still have original input units and standardized scores remain dimensionless. The SVD algorithm, rank threshold and condition-number policy are unchanged.
