"""Finite polynomial sampling, quadrature and small dense linear systems."""

import math
import sys

from graphlink_plugins.scientific.core import Field, Operation


def _polynomial(data):
    coefficients, points = data["coefficients"], data["points"]
    if not 1 <= len(coefficients) <= 128 or not 1 <= len(points) <= 256:
        raise ValueError("Supply 1–128 coefficients and 1–256 sample coordinates.")
    values = []
    for x in points:
        value = coefficients[-1]
        for coefficient in reversed(coefficients[:-1]):
            value = value * x + coefficient
        values.append(value)
    points_and_values = sorted(zip(points, values), key=lambda pair: pair[0])
    return {
        "summary": f"Evaluated the polynomial at {len(points)} coordinates using Horner’s method.",
        "values": {"samples": len(points)},
        "table": {"columns": ["x", "p(x)"], "rows": [[x, y] for x, y in zip(points, values)]},
        "plot": {
            "title": "Polynomial samples",
            "xLabel": "x",
            "yLabel": "p(x)",
            "x": [x for x, _ in points_and_values],
            "y": [y for _, y in points_and_values],
        },
    }


def _integrate(data):
    x, y = data["x"], data["y"]
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("Supply at least two paired x and y observations.")
    if any(left >= right for left, right in zip(x, x[1:])):
        raise ValueError("Sample coordinates must increase strictly.")
    areas = []
    for i in range(1, len(x)):
        left, right = y[i - 1], y[i]
        total = left + right
        # Half the sum keeps subnormal ordinates; halve each term only if
        # their sum overflowed. A true zero-height interval always cancels.
        height = 0.5 * total if math.isfinite(total) else 0.5 * left + 0.5 * right
        areas.append((x[i] - x[i - 1]) * height if height else 0.0)
    return {
        "summary": "Signed integral of the piecewise-linear interpolant on the supplied interval.",
        "values": {"integral": math.fsum(areas), "intervals": len(areas)},
        "plot": {"title": "Integrated samples", "xLabel": "x", "yLabel": "y", "x": x, "y": y},
    }


def _solve(data):
    matrix, rhs = data["matrix"], data["rhs"]
    n = len(rhs)
    if not 1 <= n <= 32 or len(matrix) != n or any(len(row) != n for row in matrix):
        raise ValueError("Supply a square matrix of order 1–32 and one right-hand value per row.")
    # Equilibrating each equation gives scaled pivoting and preserves tiny
    # but well-conditioned equations when units differ between rows.
    rows = []
    for row, value in zip(matrix, rhs):
        scale = max(map(abs, row))
        if scale == 0:
            raise ValueError("A zero row prevents a unique solution.")
        rows.append([entry / scale for entry in row] + [value / scale])
    for j in range(n):
        pivot = max(range(j, n), key=lambda k: abs(rows[k][j]))
        if abs(rows[pivot][j]) <= n * sys.float_info.epsilon:
            raise ValueError("The matrix is singular or too ill-conditioned for this solver.")
        rows[j], rows[pivot] = rows[pivot], rows[j]
        for i in range(j + 1, n):
            factor = rows[i][j] / rows[j][j]
            for k in range(j + 1, n + 1):
                rows[i][k] -= factor * rows[j][k]

    solution = [0.0] * n
    for j in reversed(range(n)):
        products = (rows[j][k] * solution[k] for k in range(j + 1, n))
        solution[j] = (rows[j][-1] - math.fsum(products)) / rows[j][j]
    residual = max(abs(math.fsum(a * x for a, x in zip(row, solution)) - value) for row, value in zip(matrix, rhs))
    return {
        "summary": f"Solved {n} equations with row scaling and partial pivoting.",
        "values": {"order": n, "max_absolute_residual": residual},
        "table": {"columns": ["Variable", "Solution"], "rows": [[f"x{i + 1}", v] for i, v in enumerate(solution)]},
    }


OPERATIONS = (
    Operation(
        "polynomial",
        "Evaluate a polynomial",
        "Evaluate real coefficients at specified coordinates. The table keeps the original input order.",
        (
            Field(
                "coefficients",
                "Polynomial coefficients",
                "vector",
                description="Constant term first, up to 128 coefficients.",
            ),
            Field("points", "Sample coordinates", "vector", description="Between 1 and 256 coordinates."),
        ),
        {"coefficients": [1, -2, 1], "points": [-2, -1, 0, 1, 2, 3]},
        _polynomial,
    ),
    Operation(
        "trapezoid",
        "Integrate sampled data",
        "Use the composite trapezoid rule on paired observations with strictly increasing coordinates.",
        (Field("x", "Sample coordinates", "vector"), Field("y", "Measured values", "vector")),
        {"x": [0, 0.5, 1, 2], "y": [1, 2, 3, 5]},
        _integrate,
    ),
    Operation(
        "linear_system",
        "Solve a linear system",
        "Solve Ax = b for a square nonsingular real matrix with up to 32 rows.",
        (Field("matrix", "Coefficient matrix A", "matrix"), Field("rhs", "Right-hand side b", "vector")),
        {"matrix": [[2, 1], [1, -1]], "rhs": [5, 1]},
        _solve,
    ),
)
