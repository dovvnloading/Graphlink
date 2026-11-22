"""Adaptive IVP, root, quadrature and spline methods for the Scientific Workbench.

All user-supplied formulae are parsed and evaluated by the uncertainty pack's
bounded arithmetic grammar. No Python expression evaluation is used.
"""

from __future__ import annotations

import bisect
import heapq
import math
import sys

from graphlink_plugins.scientific.core import Field, Operation, finite
from graphlink_plugins.scientific.packs.uncertainty import (
    _FUNCTIONS, _NAME, _Parser, _differentiate, _evaluate, _json,
)

_EPS = sys.float_info.epsilon
_GAMMA = 1.0 - 1.0 / math.sqrt(2.0)


def _tolerances(data):
    rtol, atol = data["relative_tolerance"], data["absolute_tolerance"]
    if rtol < 0 or atol < 0 or (rtol == 0 and atol == 0):
        raise ValueError("Relative and absolute tolerances must be nonnegative, with at least one positive.")
    if 0 < rtol < 100 * _EPS:
        raise ValueError("Relative tolerance cannot be met below 100 machine epsilons.")
    return rtol, atol


def _budget(value, name, maximum):
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}.")
    return value


def _parameters(raw, forbidden):
    items = _json(raw, "Parameters")
    if not isinstance(items, dict) or len(items) > 8:
        raise ValueError("Parameters must be a JSON object of at most eight numeric constants.")
    result = {}
    for name, value in items.items():
        if (not isinstance(name, str) or not _NAME.fullmatch(name) or len(name) > 48
                or name in forbidden or name in _FUNCTIONS):
            raise ValueError("Parameter names must be short, distinct identifiers, not model variables or functions.")
        result[name] = finite(value, f"Parameter {name}")
    return result


def _tree(expression, names):
    # The parser performs all lexical, size, grammar and nesting checks.
    # Supply only identifiers actually referenced: its measurement-specific
    # contract rejects *unused* input names, which are common in ODE systems.
    probe = _Parser(expression, {})
    used = {name: 0.0 for name in names if name in probe.tokens and name not in _FUNCTIONS}
    return _Parser(expression, used).parse()


def _real(value, label):
    return finite(value, label)


def _scalar(expression, parameters, independent):
    names = {independent, *parameters}
    if independent in parameters:
        raise ValueError("Parameters cannot shadow the independent variable.")
    tree = _tree(expression, names)
    calls = [0]

    def function(x):
        calls[0] += 1
        return _evaluate(tree, {independent: x, **parameters})

    return function, calls


class _System:
    def __init__(self, data):
        y0 = data["initial_values"]
        if not 1 <= len(y0) <= 8:
            raise ValueError("ODE systems require between one and eight state variables.")
        expressions = _json(data["rhs_expressions"], "ODE right-hand sides")
        if (not isinstance(expressions, list) or len(expressions) != len(y0)
                or any(not isinstance(item, str) for item in expressions)):
            raise ValueError("Right-hand sides must be a JSON array with one expression per state variable.")
        names = {"t", *(f"y{i}" for i in range(len(y0)))}
        self.params = _parameters(data["parameters"], names)
        self.trees = [_tree(expr, names | self.params.keys()) for expr in expressions]
        self.dimension = len(y0)
        self.evaluations = 0
        self.jacobians = 0
        self.linear_solves = 0

    def _variables(self, t, y):
        return {"t": t, **{f"y{i}": v for i, v in enumerate(y)}, **self.params}

    def rhs(self, t, y):
        self.evaluations += 1
        variables = self._variables(t, y)
        return [_evaluate(tree, variables) for tree in self.trees]

    def jacobian(self, t, y):
        self.jacobians += 1
        variables = self._variables(t, y)
        return [[_differentiate(tree, variables, f"y{j}")[1]
                 for j in range(self.dimension)] for tree in self.trees]


def _scale_error(error, old, new, rtol, atol):
    scales = [atol + rtol * max(abs(a), abs(b)) for a, b in zip(old, new)]
    return max((abs(e) / s if s else (0.0 if e == 0 else math.inf))
               for e, s in zip(error, scales))


# Dormand-Prince 5(4), seven stages with the fifth-order advance and fourth-order error estimator.
_C = (0, 1/5, 3/10, 4/5, 8/9, 1, 1)
_A = ((), (1/5,), (3/40, 9/40), (44/45, -56/15, 32/9),
      (19372/6561, -25360/2187, 64448/6561, -212/729),
      (9017/3168, -355/33, 46732/5247, 49/176, -5103/18656),
      (35/384, 0, 500/1113, 125/192, -2187/6784, 11/84))
_B5 = (35/384, 0, 500/1113, 125/192, -2187/6784, 11/84, 0)
_B4 = (5179/57600, 0, 7571/16695, 393/640, -92097/339200, 187/2100, 1/40)


def _rk45_step(system, t, y, h):
    k = []
    for stage, coeffs in enumerate(_A):
        z = [v + h * math.fsum(a * k[j][i] for j, a in enumerate(coeffs))
             for i, v in enumerate(y)]
        k.append(system.rhs(t + _C[stage] * h, z))
    newer = [_real(v + h * math.fsum(b * k[j][i] for j, b in enumerate(_B5)), "ODE state")
             for i, v in enumerate(y)]
    error = [_real(h * math.fsum((b - c) * k[j][i]
                               for j, (b, c) in enumerate(zip(_B5, _B4))), "ODE error")
             for i in range(len(y))]
    return newer, error


def _linear_solve(matrix, rhs):
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for j in range(n):
        pivot = max(range(j, n), key=lambda i: abs(a[i][j]))
        if not math.isfinite(a[pivot][j]) or abs(a[pivot][j]) < 1e-290:
            raise ValueError("Implicit Newton matrix is singular.")
        a[j], a[pivot] = a[pivot], a[j]
        for i in range(j + 1, n):
            ratio = a[i][j] / a[j][j]
            for k in range(j + 1, n + 1):
                a[i][k] -= ratio * a[j][k]
            a[i][j] = 0.0
    x = [0.0] * n
    for j in range(n - 1, -1, -1):
        x[j] = _real((a[j][n] - math.fsum(a[j][k] * x[k] for k in range(j + 1, n))) / a[j][j],
                     "Implicit Newton update")
    return x


def _newton(system, t, base, hgamma, guess, rtol, atol):
    """Solve z = base + hgamma*f(t,z), with analytic AST Jacobians."""
    z = guess[:]

    def residual(values):
        f = system.rhs(t, values)
        r = [values[i] - base[i] - hgamma * f[i] for i in range(len(z))]
        # Use the requested scale even for tiny states; an unrelated 1e-30
        # floor can falsely declare convergence while the state is unchanged.
        norm = _scale_error(r, values, base, rtol, atol)
        return r, norm

    for _ in range(12):
        residuals, norm = residual(z)
        if norm <= 0.02:
            return z
        jac = system.jacobian(t, z)
        m = [[float(i == j) - hgamma * jac[i][j] for j in range(len(z))]
             for i in range(len(z))]
        system.linear_solves += 1
        change = _linear_solve(m, [-value for value in residuals])
        improved = False
        for damping in (1, .5, .25, .125, .0625, .03125):
            attempt = [_real(z[i] + damping * change[i], "Implicit Newton state")
                       for i in range(len(z))]
            try:
                _, candidate_norm = residual(attempt)
            except ValueError:
                continue
            if candidate_norm < norm:
                z = attempt
                improved = True
                break
        if not improved:
            break
    raise ValueError("Implicit Newton iteration did not converge at this step size.")


def _sdirk_step(system, t, y, h, rtol, atol):
    """Two-stage L-stable second-order SDIRK (Alexander gamma=1-1/sqrt(2))."""
    z1 = _newton(system, t + _GAMMA*h, y, _GAMMA*h, y, rtol, atol)
    k1 = system.rhs(t + _GAMMA*h, z1)
    base2 = [_real(v + h*(1-_GAMMA)*k, "Implicit stage") for v, k in zip(y, k1)]
    return _newton(system, t + h, base2, _GAMMA*h, z1, rtol, atol)


def _ode(data, implicit=False):
    rtol, atol = _tolerances(data)
    max_steps = _budget(data["max_steps"], "Maximum ODE steps", 20000)
    t, end = data["start_time"], data["end_time"]
    if t == end:
        raise ValueError("Initial and final times must differ.")
    system = _System(data)
    y = data["initial_values"][:]
    direction = 1 if end > t else -1
    span = abs(end - t)
    if not math.isfinite(span):
        raise ValueError("ODE time span exceeds the supported numeric range.")
    initial_rhs = system.rhs(t, y)
    rate = max(abs(f) / max(atol + rtol * max(1, abs(v)), 1e-300)
               for f, v in zip(initial_rhs, y))
    h = min(span, max(1e-7 * max(1, abs(t)), .01 / max(rate, 1e-30)))
    accepted = rejected = 0
    maximum_estimate = maximum_ratio = last_estimate = 0.0
    points = [[t, *y]]
    row_limit = min(1000, 4096 // (len(y) + 1))
    for attempt in range(max_steps):
        if direction * (end - t) <= 0:
            break
        h = min(h, abs(end - t))
        if t + direction*h == t:
            raise ValueError(f"ODE tolerance not met: time step underflow after {accepted} accepted, "
                             f"{rejected} rejected steps and {system.evaluations} RHS evaluations.")
        step = direction * h
        try:
            if implicit:
                full = _sdirk_step(system, t, y, step, rtol, atol)
                midway = _sdirk_step(system, t, y, step/2, rtol, atol)
                new = _sdirk_step(system, t + step/2, midway, step/2, rtol, atol)
                # Difference is ~3 times the two-half-step local error for order 2.
                error = [(a-b)/3 for a, b in zip(new, full)]
            else:
                new, error = _rk45_step(system, t, y, step)
            ratio = _scale_error(error, y, new, rtol, atol)
        except ValueError:
            ratio = math.inf
        if ratio <= 1:
            next_t = end if h == abs(end-t) else t + step
            if next_t == t:
                raise ValueError("ODE tolerance not met: time step cannot advance in binary64.")
            t, y = next_t, new
            accepted += 1
            last_estimate = max(map(abs, error))
            maximum_estimate = max(maximum_estimate, last_estimate)
            maximum_ratio = max(maximum_ratio, ratio)
            # Decimate only the display trajectory; never discard solver state.
            if len(points) >= row_limit:
                points = points[::2]
            points.append([t, *y])
        else:
            rejected += 1
        power = 1/3 if implicit else 1/5
        factor = 3 if ratio == 0 else max(.1, min(3, .9 * ratio ** (-power)))
        h *= factor if ratio <= 1 else min(.5, factor)
        if system.evaluations > 200000:
            raise ValueError(f"ODE tolerance not met: RHS evaluation budget exhausted "
                             f"after {accepted} accepted and {rejected} rejected steps.")
    if direction * (end - t) > 0:
        raise ValueError(f"ODE tolerance not met after {max_steps} attempted steps "
                         f"({accepted} accepted, {rejected} rejected; {system.evaluations} RHS evaluations).")
    values = {
        "method": "L-stable SDIRK2 (step doubling)" if implicit else "Dormand-Prince RK45 (5/4)",
        "start_time": data["start_time"], "end_time": t,
        "accepted_steps": accepted, "rejected_steps": rejected,
        "rhs_evaluations": system.evaluations, "jacobian_evaluations": system.jacobians,
        "linear_solves": system.linear_solves, "estimated_max_local_error": maximum_estimate,
        "estimated_last_local_error": last_estimate,
        "max_accepted_error_ratio": maximum_ratio, "tolerance_met": "true",
    }
    values.update({f"final_y{i}": v for i, v in enumerate(y)})
    return {
        "summary": ("Adaptive implicit L-stable SDIRK2" if implicit else "Adaptive Dormand-Prince RK45")
                   + "; reported errors are estimated LOCAL truncation errors, not certified global bounds.",
        "values": values,
        "table": {"columns": ["time", *(f"y{i}" for i in range(len(y)))], "rows": points},
        "plot": {"title": "Initial-value solution (first component)", "xLabel": "time",
                 "yLabel": "y0", "x": [row[0] for row in points], "y": [row[1] for row in points]},
    }


def _root(data):
    rtol, atol = _tolerances(data)
    limit = _budget(data["max_evaluations"], "Maximum root evaluations", 5000)
    a, b = data["lower"], data["upper"]
    if not a < b:
        raise ValueError("Root bracket requires lower < upper.")
    params = _parameters(data["parameters"], {"x"})
    f, calls = _scalar(data["expression"], params, "x")
    fa = f(a)
    if fa == 0:
        return _root_output(a, 0.0, calls[0], 0, abs(b-a), 0.0)
    if calls[0] >= limit:
        raise ValueError("Root tolerance not met: checking the bracket needs two evaluations.")
    fb = f(b)
    if fb == 0:
        return _root_output(b, 0.0, calls[0], 0, abs(b-a), 0.0)
    if (fa > 0) == (fb > 0):
        raise ValueError(f"Root bracket does not contain opposite endpoint signs ({calls[0]} evaluations).")
    if abs(fa) < abs(fb):
        a, b, fa, fb = b, a, fb, fa
    c, d, fc, bisected = a, a, fa, True
    iterations = 0
    while True:
        width = abs(b-a)
        tol = atol + rtol * abs(b)
        if width <= tol:
            return _root_output(b, width, calls[0], iterations, width, abs(fb))
        if calls[0] >= limit:
            raise ValueError(f"Root tolerance not met: maximum {limit} evaluations exhausted "
                             f"with bracket width {width:.8g}.")
        if fa != fc and fb != fc:
            s = (a * fb * fc / ((fa-fb)*(fa-fc))
                 + b * fa * fc / ((fb-fa)*(fb-fc))
                 + c * fa * fb / ((fc-fa)*(fc-fb)))
        else:
            s = b - fb * (b-a) / (fb-fa)
        pivot = (3*a+b)/4
        bad = (not math.isfinite(s) or not min(pivot, b) < s < max(pivot, b)
               or (bisected and abs(s-b) >= abs(b-c)/2)
               or (not bisected and abs(s-b) >= abs(c-d)/2)
               or (bisected and abs(b-c) < tol)
               or (not bisected and abs(c-d) < tol))
        if bad:
            s, bisected = a + (b-a)/2, True
        else:
            bisected = False
        if s in (a, b):
            raise ValueError(f"Root tolerance not met: bracket cannot shrink in binary64 "
                             f"after {calls[0]} evaluations.")
        fs = f(s)
        iterations += 1
        d, c, fc = c, b, fb
        if fs == 0:
            return _root_output(s, 0, calls[0], iterations, 0, 0)
        if (fa > 0) != (fs > 0):
            b, fb = s, fs
        else:
            a, fa = s, fs
        if abs(fa) < abs(fb):
            a, b, fa, fb = b, a, fb, fa


def _root_output(root, error, evaluations, iterations, width, residual):
    return {"summary": "Brent bracketed root; error estimate is the final bracket width "
                        "(zero when the function evaluates to exactly zero).",
            "values": {"root": root, "estimated_absolute_error": error, "bracket_width": width,
                       "absolute_residual": residual, "iterations": iterations,
                       "function_evaluations": evaluations, "tolerance_met": "true"}}


# Standard embedded Gauss(7)/Kronrod(15) nodes and weights, positive half only.
_XGK = (.9914553711208126, .9491079123427585, .8648644233597691,
        .7415311855993944, .5860872354676911, .4058451513773972,
        .2077849550078985, 0.0)
_WGK = (.02293532201052922, .06309209262997855, .10479001032225018,
        .14065325971552592, .1690047266392679, .1903505780647854,
        .2044329400752989, .20948214108472782)
_WG = (.1294849661688697, .27970539148927667, .3818300505051189, .4179591836734694)


def _gk15(f, a, b):
    midpoint, half = a + (b-a)/2, (b-a)/2
    center = f(midpoint)
    pairs = [(f(midpoint-half*x), f(midpoint+half*x)) for x in _XGK[:-1]]
    # Scale each sample before summation. Unscaled symmetric sums such as
    # f(left)+f(right) can overflow even when the integral is representable.
    kronrod = math.fsum([half*_WGK[-1]*center] +
                        [half*w*v for w, pair in zip(_WGK, pairs) for v in pair])
    gauss = math.fsum([half*_WG[-1]*center] +
                      [half*_WG[j]*v for j, i in enumerate((1, 3, 5)) for v in pairs[i]])
    absolute = math.fsum([abs(half)*_WGK[-1]*abs(center)] +
                         [abs(half)*w*abs(v) for w, pair in zip(_WGK, pairs) for v in pair])
    mean = kronrod / (b-a)
    asc = math.fsum([abs(half)*_WGK[-1]*abs(center-mean)] +
                    [abs(half)*w*abs(v-mean) for w, pair in zip(_WGK, pairs) for v in pair])
    value = _real(kronrod, "Quadrature integral")
    error = abs(kronrod-gauss)
    if asc and error:
        error = asc*min(1, (200*(error/asc))**1.5)
    error = _real(max(error, 50*_EPS*absolute), "Quadrature error estimate")
    return value, error

def _quadrature(data):
    rtol, atol = _tolerances(data)
    limit = _budget(data["max_evaluations"], "Maximum quadrature evaluations", 100000)
    lo, hi = data["lower"], data["upper"]
    parameters = _parameters(data["parameters"], {"x"})
    f, calls = _scalar(data["expression"], parameters, "x")
    if lo == hi:
        return _quadrature_output(0, 0, 0, 0, 0)
    sign = 1 if hi > lo else -1
    a, b = (lo, hi) if sign == 1 else (hi, lo)
    if limit < 15:
        raise ValueError("Quadrature tolerance not met: a 15-point rule requires at least 15 evaluations.")
    value, error = _gk15(f, a, b)
    heap = [(-error, a, b, value, error)]
    total, total_error, splits = value, error, 0
    while total_error > atol + rtol*abs(total):
        if calls[0] + 30 > limit:
            raise ValueError(f"Quadrature tolerance not met: {total_error:.8g} estimated error "
                             f"after {calls[0]} evaluations and {splits} subdivisions.")
        _, left, right, old_value, old_error = heapq.heappop(heap)
        middle = left + (right-left)/2
        if middle in (left, right):
            raise ValueError(f"Quadrature tolerance not met: subdivision cannot advance in binary64 "
                             f"after {calls[0]} evaluations.")
        lv, le = _gk15(f, left, middle)
        rv, re = _gk15(f, middle, right)
        heapq.heappush(heap, (-le, left, middle, lv, le))
        heapq.heappush(heap, (-re, middle, right, rv, re))
        total = _real(total + (lv+rv-old_value), "Quadrature accumulated integral")
        total_error = _real(max(0, total_error + (le+re-old_error)), "Quadrature accumulated error")
        splits += 1
    # Re-sum final intervals to protect the last tolerance check from cancellation.
    total = _real(math.fsum(item[3] for item in heap), "Quadrature integral")
    total_error = _real(math.fsum(item[4] for item in heap), "Quadrature error estimate")
    if total_error > atol + rtol*abs(total):
        raise ValueError("Quadrature tolerance not met after interval re-summation.")
    return _quadrature_output(sign*total, total_error, calls[0], splits, len(heap))


def _quadrature_output(value, error, evaluations, subdivisions, intervals):
    return {"summary": "Adaptive 15-point Gauss-Kronrod / 7-point Gauss embedded quadrature; "
                        "error is an a posteriori heuristic, not a rigorous bound for arbitrary functions.",
            "values": {"integral": value, "estimated_absolute_error": error,
                       "function_evaluations": evaluations, "subdivisions": subdivisions,
                       "intervals": intervals, "tolerance_met": "true"}}


def _spline(data):
    x, y, queries = data["x"], data["y"], data["queries"]
    n = len(x)
    if n < 2 or len(y) != n or n > 1024:
        raise ValueError("Spline needs matching x and y arrays with at least two knots.")
    if any(x[i] >= x[i+1] for i in range(n-1)):
        raise ValueError("Spline knots must be strictly increasing and distinct.")
    if data["boundary"] not in ("natural", "clamped"):
        raise ValueError("Spline boundary must be 'natural' or 'clamped'.")
    if any(q < x[0] or q > x[-1] for q in queries):
        raise ValueError("Spline queries must lie within the knot interval; extrapolation is disabled.")
    h = [x[i+1]-x[i] for i in range(n-1)]
    if any(not math.isfinite(v) or v <= 0 for v in h):
        raise ValueError("Spline knot spacing is outside the finite numeric range.")
    slopes = [_real((y[i+1]-y[i])/h[i], "Spline secant") for i in range(n-1)]
    lower, diagonal, upper, rhs = [0.0]*n, [0.0]*n, [0.0]*n, [0.0]*n
    if data["boundary"] == "natural":
        diagonal[0] = diagonal[-1] = 1.0
    else:
        diagonal[0], upper[0] = 2*h[0], h[0]
        rhs[0] = 6*(slopes[0] - data["left_slope"])
        lower[-1], diagonal[-1] = h[-1], 2*h[-1]
        rhs[-1] = 6*(data["right_slope"] - slopes[-1])
    for i in range(1, n-1):
        lower[i], diagonal[i], upper[i] = h[i-1], 2*(h[i-1]+h[i]), h[i]
        rhs[i] = 6*(slopes[i]-slopes[i-1])
    # Thomas tridiagonal elimination; diagonal dominance for ordered knots.
    for i in range(1, n):
        if diagonal[i-1] == 0:
            raise ValueError("Spline coefficient system is singular.")
        factor = lower[i]/diagonal[i-1]
        diagonal[i] = _real(diagonal[i] - factor*upper[i-1], "Spline pivot")
        rhs[i] = _real(rhs[i]-factor*rhs[i-1], "Spline coefficient")
    if diagonal[-1] == 0:
        raise ValueError("Spline coefficient system is singular.")
    second = [0.0]*n
    second[-1] = _real(rhs[-1]/diagonal[-1], "Spline second derivative")
    for i in range(n-2, -1, -1):
        second[i] = _real((rhs[i]-upper[i]*second[i+1])/diagonal[i], "Spline second derivative")

    def interpolate(q):
        i = min(n-2, bisect.bisect_right(x, q)-1)
        a, b = (x[i+1]-q)/h[i], (q-x[i])/h[i]
        # Avoid squaring a potentially huge interval (including 0 * infinity
        # for a perfectly linear spline with zero second derivatives).
        value = a*y[i] + b*y[i+1] + (h[i]/6)*((a**3-a)*(h[i]*second[i]) + (b**3-b)*(h[i]*second[i+1]))
        gradient = slopes[i] + h[i]/6*((1-3*a*a)*second[i] + (3*b*b-1)*second[i+1])
        return _real(value, "Spline interpolated value"), _real(gradient, "Spline slope")

    rows = [[q, *interpolate(q)] for q in queries]
    residual = max(abs(interpolate(xx)[0]-yy) for xx, yy in zip(x, y))
    roundoff = _real(residual + 32*_EPS*max(1, *(abs(v) for v in y),
                                           *(abs(row[1]) for row in rows)), "Spline roundoff estimate")
    return {
        "summary": "Piecewise cubic C2 spline; knot residual and roundoff estimate do not bound "
                   "interpolation error against an unknown underlying function.",
        "values": {"boundary": data["boundary"], "knots": n, "intervals": n-1,
                   "evaluations": n+len(queries), "tridiagonal_solves": 1,
                   "max_knot_residual": residual, "estimated_roundoff_error": roundoff,
                   "underlying_function_error": "unknown (requires a fourth-derivative bound)",
                   "tolerance_met": "not applicable: no interpolation tolerance requested"},
        "table": {"columns": ["x", "spline", "first derivative"], "rows": rows},
        "plot": {"title": "Cubic spline at requested points", "xLabel": "x", "yLabel": "spline",
                 "x": [row[0] for row in rows], "y": [row[1] for row in rows]},
    }


_ODE_FIELDS = (
    Field("rhs_expressions", "Right-hand sides (JSON array of safe expressions)", "text"),
    Field("initial_values", "Initial state y0, y1, ...", "vector"),
    Field("parameters", "Constant parameters (JSON object)", "text"),
    Field("start_time", "Initial time", "number"),
    Field("end_time", "Final time", "number"),
    Field("relative_tolerance", "Relative tolerance", "number"),
    Field("absolute_tolerance", "Absolute tolerance", "number"),
    Field("max_steps", "Maximum attempted steps", "integer"),
)
_EXAMPLE = {"rhs_expressions": '["-0.5*y0"]', "initial_values": [1.0], "parameters": "{}",
            "start_time": 0.0, "end_time": 4.0, "relative_tolerance": 1e-7,
            "absolute_tolerance": 1e-10, "max_steps": 10000}

OPERATIONS = (
    Operation("solve_ivp_rk45", "Adaptive Dormand-Prince RK45 IVP",
              "Integrate an ODE system with embedded fifth/fourth-order local error control.",
              _ODE_FIELDS, _EXAMPLE, lambda data: _ode(data, implicit=False), headline="final_y0"),
    Operation("solve_ivp_implicit", "Adaptive implicit stiff IVP",
              "Integrate a stiff ODE system with an L-stable, two-stage SDIRK2 and step-doubling estimator.",
              _ODE_FIELDS, _EXAMPLE, lambda data: _ode(data, implicit=True), headline="final_y0"),
    Operation("brent_root", "Brent bracketed root",
              "Find a sign-changing scalar root using safeguarded Brent interpolation and bisection.",
              (Field("expression", "Function (safe expression in x)", "text"),
               Field("parameters", "Constant parameters (JSON object)", "text"),
               Field("lower", "Lower bracket", "number"), Field("upper", "Upper bracket", "number"),
               Field("relative_tolerance", "Relative tolerance", "number"),
               Field("absolute_tolerance", "Absolute tolerance", "number"),
               Field("max_evaluations", "Maximum evaluations", "integer")),
              {"expression": "cos(x)-x", "parameters": "{}", "lower": 0.0, "upper": 1.0,
               "relative_tolerance": 1e-12, "absolute_tolerance": 1e-12, "max_evaluations": 100}, _root, headline="root"),
    Operation("gauss_kronrod_integral", "Adaptive Gauss-Kronrod 15(7) quadrature",
              "Integrate a safe scalar expression with a summed embedded-rule error estimate.",
              (Field("expression", "Integrand (safe expression in x)", "text"),
               Field("parameters", "Constant parameters (JSON object)", "text"),
               Field("lower", "Lower integration bound", "number"),
               Field("upper", "Upper integration bound", "number"),
               Field("relative_tolerance", "Relative tolerance", "number"),
               Field("absolute_tolerance", "Absolute tolerance", "number"),
               Field("max_evaluations", "Maximum evaluations", "integer")),
              {"expression": "sin(x)", "parameters": "{}", "lower": 0.0, "upper": math.pi,
               "relative_tolerance": 1e-10, "absolute_tolerance": 1e-11,
               "max_evaluations": 15000}, _quadrature, headline="integral"),
    Operation("cubic_spline", "Natural / clamped cubic spline",
              "Construct a C2 cubic spline over increasing knots and evaluate inside the knot range.",
              (Field("x", "Strictly increasing knot coordinates", "vector"),
               Field("y", "Knot values", "vector"),
               Field("queries", "Interpolation points", "vector"),
               Field("boundary", "Boundary type: natural or clamped", "text"),
               Field("left_slope", "Left endpoint first derivative (clamped)", "number"),
               Field("right_slope", "Right endpoint first derivative (clamped)", "number")),
              {"x": [0, 1, 2], "y": [0, 1, 2], "queries": [.5, 1.5],
               "boundary": "natural", "left_slope": 0.0, "right_slope": 0.0}, _spline, headline="estimated_roundoff_error",
        paired_fields=(("x", "y"),)),
)
