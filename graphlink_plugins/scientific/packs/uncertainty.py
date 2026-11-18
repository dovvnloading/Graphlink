"""First-order GUM measurement uncertainty, with a deliberately small expression grammar."""

from __future__ import annotations

import json
import math
import re
from statistics import NormalDist

from graphlink_plugins.scientific.core import Field, Operation, finite
from graphlink_plugins.scientific.packs.fitting import _student_t_ppf

_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
_TOKEN = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[A-Za-z_][A-Za-z_0-9]*|[+*/^(),-]")
_FUNCTIONS = {"sqrt": math.sqrt, "exp": math.exp, "ln": math.log,
              "log10": math.log10, "sin": math.sin, "cos": math.cos}
_MAX_INPUTS = 16


def _json_object(pairs):
    """Reject repeated JSON names instead of silently replacing measurements."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}.")
        result[key] = value
    return result


def _json(text, label):
    try:
        return json.loads(text, object_pairs_hook=_json_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON number.")))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError(f"{label} must be valid JSON without duplicate keys or nonfinite numbers.") from exc


def _number(value, label):
    return finite(value, label)


class _Parser:
    """Recursive-descent grammar; tokens represent no executable Python syntax."""

    def __init__(self, expression, inputs):
        if len(expression) > 2048:
            raise ValueError("The measurement expression exceeds 2048 characters.")
        tokens = []
        position = 0
        while position < len(expression):
            if expression[position].isspace():
                position += 1
                continue
            token = _TOKEN.match(expression, position)
            if token is None:
                raise ValueError("Expression contains an unsupported character or token.")
            tokens.append(token.group())
            position = token.end()
            if len(tokens) > 256:
                raise ValueError("Expression exceeds 256 tokens.")
        self.tokens = tokens + [None]
        self.position = 0
        self.depth = 0
        self.inputs = inputs
        self.used = set()

    def _peek(self):
        return self.tokens[self.position]

    def _take(self, expected=None):
        token = self._peek()
        if expected is not None and token != expected:
            raise ValueError(f"Expression requires '{expected}'.")
        if token is None:
            raise ValueError("Unexpected end of measurement expression.")
        self.position += 1
        return token

    def parse(self):
        if not self.tokens[0]:
            raise ValueError("Measurement expression cannot be empty.")
        root = self._sum()
        if self._peek() is not None:
            raise ValueError("Unexpected trailing expression token.")
        if self.used != set(self.inputs):
            raise ValueError("Model names must match the supplied inputs exactly.")
        return root

    def _nested(self):
        self.depth += 1
        if self.depth > 48:
            raise ValueError("Measurement expression is nested too deeply.")
        try:
            return self._sum()
        finally:
            self.depth -= 1

    def _sum(self):
        node = self._product()
        while self._peek() in ("+", "-"):
            operator = self._take()
            node = (operator, node, self._product())
        return node

    def _product(self):
        node = self._unary()
        while self._peek() in ("*", "/"):
            operator = self._take()
            node = (operator, node, self._unary())
        return node

    def _unary(self):
        if self._peek() in ("+", "-"):
            operator = self._take()
            return ("positive" if operator == "+" else "negative", self._unary())
        return self._power()

    def _power(self):
        node = self._atom()
        if self._peek() == "^":
            self._take("^")
            node = ("^", node, self._unary())  # right associative, above unary minus
        return node

    def _atom(self):
        token = self._take()
        if token == "(":
            node = self._nested()
            self._take(")")
            return node
        if token in _FUNCTIONS:
            self._take("(")
            node = self._nested()
            self._take(")")
            return ("function", token, node)
        if token in self.inputs:
            self.used.add(token)
            return ("input", token)
        if token[0].isdigit() or token[0] == ".":
            number = float(token)
            if not math.isfinite(number) or (number == 0 and any(c in "123456789" for c in token.split("e")[0].split("E")[0])):
                raise ValueError("Expression literal exceeds the supported numeric range.")
            return ("literal", number)
        raise ValueError("Unknown input, function or malformed expression.")


def _evaluate(node, variables):
    kind = node[0]
    try:
        if kind == "literal":
            value = node[1]
        elif kind == "input":
            value = variables[node[1]]
        elif kind == "function":
            value = _FUNCTIONS[node[1]](_evaluate(node[2], variables))
        elif kind == "positive":
            value = _evaluate(node[1], variables)
        elif kind == "negative":
            value = -_evaluate(node[1], variables)
        else:
            left, right = _evaluate(node[1], variables), _evaluate(node[2], variables)
            if kind == "+":
                value = left + right
            elif kind == "-":
                value = left - right
            elif kind == "*":
                value = left * right
            elif kind == "/":
                value = left / right
            else:
                value = left ** right
        if isinstance(value, complex) or not math.isfinite(value):
            raise ValueError("Model evaluation is outside the finite real domain.")
        return value
    except (ArithmeticError, ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Model evaluation is undefined or outside the finite real domain.") from exc



def _differentiate(node, variables, name):
    """Propagate one input derivative through the bounded expression AST.

    Apply the ordinary chain rule at the supplied estimates instead of
    subtracting rounded, potentially much larger output values.
    """
    kind = node[0]
    try:
        if kind == "literal":
            return node[1], 0.0
        if kind == "input":
            return variables[node[1]], float(node[1] == name)
        if kind in ("positive", "negative"):
            value, derivative = _differentiate(node[1], variables, name)
            return (value, derivative) if kind == "positive" else (-value, -derivative)
        if kind == "function":
            argument, delta = _differentiate(node[2], variables, name)
            function = node[1]
            value = _FUNCTIONS[function](argument)
            if function == "sqrt":
                if value == 0 and delta:
                    raise ValueError("Square root sensitivity is undefined at zero.")
                derivative = delta / (2 * value) if delta else 0.0
            elif function == "exp":
                derivative = value * delta
            elif function == "ln":
                derivative = delta / argument
            elif function == "log10":
                # Divide before multiplying by ln(10): argument*ln(10)
                # can overflow even while the sensitivity is representable.
                ratio = delta / argument
                derivative = (ratio / math.log(10) if math.isfinite(ratio)
                              else (delta / math.log(10)) / argument)
            elif function == "sin":
                derivative = math.cos(argument) * delta
            else:
                derivative = -math.sin(argument) * delta
        else:
            left, dl = _differentiate(node[1], variables, name)
            right, dr = _differentiate(node[2], variables, name)
            if kind == "+":
                value, derivative = left + right, dl + dr
            elif kind == "-":
                value, derivative = left - right, dl - dr
            elif kind == "*":
                value, derivative = left * right, dl * right + left * dr
            elif kind == "/":
                value = left / right
                derivative = dl / right - value * (dr / right)
            else:
                value = left ** right
                if left > 0:
                    if dr:
                        derivative = value * (dr * math.log(left) + right * dl / left)
                    else:
                        derivative = right * left ** (right - 1) * dl if dl and right else 0.0
                elif dr:
                    # The real-valued power is not differentiable with
                    # respect to its exponent at a nonpositive base.
                    raise ValueError("Power sensitivity is undefined for a nonpositive base and variable exponent.")
                elif left < 0:
                    if not right.is_integer():
                        raise ValueError("A negative base requires an integer exponent.")
                    derivative = right * left ** (right - 1) * dl if dl else 0.0
                elif not dl or right == 0:
                    derivative = 0.0
                elif right == 1:
                    derivative = dl
                elif right > 1 and right.is_integer():
                    derivative = 0.0
                else:
                    raise ValueError("Power sensitivity is undefined at zero.")
        if isinstance(value, complex) or not math.isfinite(value) or not math.isfinite(derivative):
            raise ValueError("The model or sensitivity exceeds the finite real range.")
        return value, derivative
    except (ArithmeticError, ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Model sensitivity is undefined or outside the finite real domain.") from exc


def _measurements(text):
    data = _json(text, "Measurements")
    if not isinstance(data, dict) or not 1 <= len(data) <= _MAX_INPUTS:
        raise ValueError("Measurements require 1–16 named inputs in a JSON object.")
    result = {}
    for name, item in data.items():
        if not _NAME.fullmatch(name) or name in _FUNCTIONS or len(name) > 48:
            raise ValueError("Input names must be short identifiers distinct from function names.")
        if not isinstance(item, dict) or not {"value", "uncertainty"} <= item.keys() or item.keys() - {
                "value", "uncertainty", "degrees_of_freedom"}:
            raise ValueError(f"Input {name} requires value and uncertainty, with optional degrees_of_freedom.")
        value = _number(item["value"], f"Value of {name}")
        uncertainty = _number(item["uncertainty"], f"Uncertainty of {name}")
        if uncertainty < 0:
            raise ValueError("Standard uncertainties cannot be negative.")
        df = None
        if "degrees_of_freedom" in item:
            df = _number(item["degrees_of_freedom"], f"Degrees of freedom of {name}")
            if df <= 0:
                raise ValueError("Degrees of freedom must be positive; omit for infinity.")
        result[name] = (value, uncertainty, df)
    return result


def _correlation_matrix(text, measurements):
    pairs = _json(text, "Correlations")
    if not isinstance(pairs, list) or len(pairs) > _MAX_INPUTS * (_MAX_INPUTS - 1) // 2:
        raise ValueError("Correlations must be an array of at most 120 input pairs.")
    names = list(measurements)
    index = {name: i for i, name in enumerate(names)}
    matrix = [[float(i == j) for j in range(len(names))] for i in range(len(names))]
    seen = set()
    entries = []
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {"a", "b", "coefficient"}:
            raise ValueError("Each correlation needs exactly a, b and coefficient.")
        a, b = pair["a"], pair["b"]
        if not isinstance(a, str) or not isinstance(b, str) or a not in index or b not in index or a == b:
            raise ValueError("Correlations need two distinct named measurement inputs.")
        key = frozenset((a, b))
        if key in seen:
            raise ValueError("Repeated pairwise correlation.")
        seen.add(key)
        rho = _number(pair["coefficient"], "Correlation coefficient")
        if not -1 <= rho <= 1:
            raise ValueError("Correlation coefficients must lie in [-1, 1].")
        if (measurements[a][1] == 0 or measurements[b][1] == 0) and rho:
            raise ValueError("A nonzero correlation requires nonzero standard uncertainties.")
        i, j = index[a], index[b]
        matrix[i][j] = matrix[j][i] = rho
        entries.append((a, b, rho))
    # A collection of individually valid coefficients need not be a possible
    # joint correlation matrix. Check positive semidefiniteness by elimination.
    remainder = [row[:] for row in matrix]
    for k in range(len(names)):
        pivot = remainder[k][k]
        if pivot < -1e-10:
            raise ValueError("The correlation matrix is not positive semidefinite.")
        if pivot <= 1e-12:
            if any(abs(remainder[k][j]) > 1e-10 for j in range(k + 1, len(names))):
                raise ValueError("The correlation matrix is not positive semidefinite.")
            continue
        for i in range(k + 1, len(names)):
            for j in range(i, len(names)):
                remainder[i][j] -= remainder[k][i] * remainder[k][j] / pivot
                remainder[j][i] = remainder[i][j]
    return names, matrix, entries


def _calculate(data):
    measurements = _measurements(data["measurements"])
    names, correlation, pairs = _correlation_matrix(data["correlations"], measurements)
    probability = data["coverage_probability"]
    if not 0 < probability < 1:
        raise ValueError("Coverage probability must be strictly between zero and one.")
    tree = _Parser(data["expression"], measurements).parse()
    values = {name: measurements[name][0] for name in names}
    estimate = _evaluate(tree, values)
    sensitivities, signed = [], []
    for name in names:
        _, sensitivity = _differentiate(tree, values, name)
        sensitivities.append(sensitivity)
        uncertainty = measurements[name][1]
        contribution = sensitivity * uncertainty
        if sensitivity and uncertainty and contribution == 0:
            raise ValueError("Signed uncertainty contribution is below the numerical range.")
        signed.append(_number(contribution, "Signed uncertainty contribution"))
    scale = max(map(abs, signed))
    q = [v / scale for v in signed] if scale else [0.0] * len(names)

    def variance(indices):
        terms = [q[i] * q[i] for i in indices]
        terms.extend(2 * q[i] * q[j] * correlation[i][j]
                     for position, i in enumerate(indices) for j in indices[position + 1:])
        result = math.fsum(terms)
        if result < -1e-10:
            raise ValueError("Correlations yield a negative propagated variance.")
        return max(0.0, result)

    # A correlated group with finite df is only meaningful here if those
    # inputs share a single jointly estimated variance with the same df.
    unseen = set(range(len(names)))
    groups = []
    while unseen:
        group, frontier = set(), [min(unseen)]
        while frontier:
            i = frontier.pop()
            if i in group:
                continue
            group.add(i)
            frontier.extend(j for j in unseen if correlation[i][j] != 0 and j != i)
        unseen -= group
        groups.append(sorted(group))
    components = [variance(group) for group in groups]
    relative_variance = math.fsum(components)
    combined = _number(scale * math.sqrt(relative_variance), "Combined standard uncertainty")
    denominator = 0.0
    for group, component in zip(groups, components):
        dfs = [measurements[names[i]][2] for i in group]
        if len(group) > 1 and not (all(df is None for df in dfs) or
                                   all(df is not None and df == dfs[0] for df in dfs)):
            raise ValueError("Correlated inputs require a common joint degrees of freedom or all omitted.")
        if dfs[0] is not None:
            denominator += component * component / dfs[0]
    effective_df = relative_variance**2 / denominator if denominator and relative_variance else None
    if effective_df is not None and (not math.isfinite(effective_df) or effective_df <= 0):
        raise ValueError("Effective degrees of freedom exceeds the supported range.")
    quantile = (1 + probability) / 2
    if quantile >= 1:
        raise ValueError("Coverage probability is too close to one for binary64 precision.")
    if effective_df is None:
        factor = NormalDist().inv_cdf(quantile)  # Student t with infinite df
    elif effective_df > 1e7:
        z = NormalDist().inv_cdf(quantile)
        factor = z + (z**3 + z) / (4 * effective_df)  # t asymptotic expansion
    else:
        factor = _student_t_ppf(quantile, effective_df)
    expanded = _number(combined * factor, "Expanded uncertainty")
    output = {
        "estimate": estimate, "combined_standard_uncertainty": combined,
        "effective_degrees_of_freedom": effective_df if effective_df is not None else "infinite",
        "coverage_probability": probability, "coverage_factor": factor,
        "expanded_uncertainty": expanded, "coverage_lower": _number(estimate - expanded, "Coverage limit"),
        "coverage_upper": _number(estimate + expanded, "Coverage limit"),
        "input_count": len(names), "correlation_count": len(pairs),
    }
    rows = []
    for j, name in enumerate(names):
        x, u, df = measurements[name]
        output[f"sensitivity_{name}"] = sensitivities[j]
        output[f"contribution_{name}"] = signed[j]  # signed: c_i * u_i
        rows.append([name, x, u, df if df is not None else "infinite", sensitivities[j],
                     signed[j], _number(signed[j] ** 2, "Variance contribution")])
    indexes = {name: i for i, name in enumerate(names)}
    for a, b, rho in pairs:
        cross = _number(2 * signed[indexes[a]] * signed[indexes[b]] * rho, "Covariance term")
        rows.append([f"{a} × {b} (r={rho:g})", "", "", "", "", "", cross])
    return {
        "summary": "First-order GUM law of propagation with analytic chain-rule sensitivities, "
                   "correlated covariance terms and Student t coverage (normal limit for infinite df).",
        "values": output,
        "table": {"columns": ["input or covariance pair", "value", "standard uncertainty", "degrees of freedom",
                              "sensitivity", "signed contribution", "variance term"], "rows": rows},
    }


OPERATIONS = (
    Operation(
        "propagate_uncertainty", "GUM measurement uncertainty propagation",
        "Combine input standard uncertainties by the first-order GUM law, including pairwise correlations; "
        "use a safe arithmetic expression, analytic chain-rule sensitivities and Student t coverage.",
        (Field("expression", "Measurement model (safe expression)", "text"),
         Field("measurements", "Named inputs (JSON: value, uncertainty, optional degrees_of_freedom)", "text"),
         Field("correlations", "Pairwise correlations (JSON array, [] for none)", "text"),
         Field("coverage_probability", "Two-sided coverage probability", "number")),
        {"expression": "L + d", "measurements": '{"L":{"value":50.000623,"uncertainty":0.000025,"degrees_of_freedom":18},'
         '"d":{"value":0.000215,"uncertainty":0.0000097,"degrees_of_freedom":25.6}}',
         "correlations": "[]", "coverage_probability": 0.95},
        _calculate,
    ),
)
