"""Exact SI dimensional algebra for Scientific Core measurement conversion.

Dimensions are (length, mass, time, electric current, thermodynamic
 temperature, amount of substance, luminous intensity).  Linear factors and
 affine offsets are Fractions so prefix and derived-unit definitions stay exact.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

_DIMENSION_SYMBOLS = ("m", "kg", "s", "A", "K", "mol", "cd")
_ZERO = (0,) * len(_DIMENSION_SYMBOLS)
_TEMPERATURE = (0, 0, 0, 0, 1, 0, 0)


@dataclass(frozen=True)
class Unit:
    dimensions: tuple[int | Fraction, ...]
    scale: Fraction = Fraction(1)
    offset: Fraction = Fraction(0)
    # A temperature point (absolute) is not a temperature interval (difference).
    kind: str = "linear"
    # Auxiliary quantity kind: rad and sr are SI dimensionless, but their
    # physical angle powers must not be confused with ratios or cycles/s.
    angle_power: int = 0

    def __mul__(self, other: Unit) -> Unit:
        if self.kind != "linear" or other.kind != "linear":
            raise ValueError("Nonlinear unit quantities cannot be multiplied.")
        return Unit(tuple(a + b for a, b in zip(self.dimensions, other.dimensions)),
                    self.scale * other.scale, angle_power=self.angle_power + other.angle_power)

    def __truediv__(self, other: Unit) -> Unit:
        if self.kind != "linear" or other.kind != "linear":
            raise ValueError("Nonlinear unit quantities cannot be divided.")
        return Unit(tuple(a - b for a, b in zip(self.dimensions, other.dimensions)),
                    self.scale / other.scale, angle_power=self.angle_power - other.angle_power)

    def __pow__(self, power: int) -> Unit:
        if self.kind != "linear":
            raise ValueError("Nonlinear unit quantities cannot be exponentiated.")
        return Unit(tuple(a * power for a in self.dimensions), self.scale ** power,
                    angle_power=self.angle_power * power)


def _base(index: int) -> Unit:
    return Unit(tuple(int(i == index) for i in range(7)))


m, kg, s, A, K, mol, cd = (_base(i) for i in range(7))
N = kg * m / s**2
J = N * m
W = J / s
Pa = N / m**2
C = A * s
V = W / A
F = C / V
Ohm = V / A
S = A / V
Wb = V * s
T = Wb / m**2
H = Wb / A

# Non-SI units accepted for use with the SI and additional exactly defined
# units. Names are case-sensitive; 'm' and 'M' are not interchangeable.
_UNITS = {
    "1": Unit(_ZERO), "m": m, "kg": kg, "s": s, "A": A,
    "K": K, "mol": mol, "cd": cd,
    "g": Unit(kg.dimensions, Fraction(1, 1000)),
    "rad": Unit(_ZERO, angle_power=1), "sr": Unit(_ZERO, angle_power=2),
    "Hz": s**-1, "N": N, "Pa": Pa, "J": J, "W": W,
    "C": C, "V": V, "F": F, "ohm": Ohm, "Ω": Ohm, "Ω": Ohm,
    "S": S, "Wb": Wb, "T": T, "H": H,
    "lm": cd * Unit(_ZERO, angle_power=2),
    "lx": cd * Unit(_ZERO, angle_power=2) / m**2, "Bq": s**-1,
    "Gy": J / kg, "Sv": J / kg, "kat": mol / s,
    "L": Unit((3, 0, 0, 0, 0, 0, 0), Fraction(1, 1000)),
    "l": Unit((3, 0, 0, 0, 0, 0, 0), Fraction(1, 1000)),
    "min": Unit(s.dimensions, Fraction(60)),
    "h": Unit(s.dimensions, Fraction(3600)),
    "d": Unit(s.dimensions, Fraction(86400)),
    "t": Unit(kg.dimensions, Fraction(1000)),
    "ha": Unit((2, 0, 0, 0, 0, 0, 0), Fraction(10000)),
    "au": Unit(m.dimensions, Fraction(149597870700)),
    "eV": Unit(J.dimensions, Fraction(1602176634, 10**28)),
    "Wh": Unit(J.dimensions, Fraction(3600)),
    "bar": Unit(Pa.dimensions, Fraction(100000)),
    "atm": Unit(Pa.dimensions, Fraction(101325)),
}

# Legacy prefix symbols and powers follow NIST SP 811, Table 5;
# the 2022 ronna/quetta and ronto/quecto additions follow the updated
# BIPM SI Brochure (SP 811 predates these additions). Use g, not kg, for mass prefixes.
_PREFIX_POWERS = {
    "Q": 30, "R": 27, "Y": 24, "Z": 21, "E": 18, "P": 15,
    "T": 12, "G": 9, "M": 6, "k": 3, "h": 2, "da": 1,
    "d": -1, "c": -2, "m": -3, "µ": -6, "μ": -6,
    "n": -9, "p": -12, "f": -15, "a": -18,
    "z": -21, "y": -24, "r": -27, "q": -30,
}
# ASCII 'u' is a convenient alias for the micro prefix in typed inputs.
_PREFIX_POWERS["u"] = -6
_PREFIXABLE = frozenset({
    "m", "s", "A", "mol", "cd", "K", "g", "rad", "sr", "Hz", "N", "Pa", "J", "W",
    "C", "V", "F", "ohm", "Ω", "Ω", "S", "Wb", "T", "H", "lm", "lx", "Bq", "Gy", "Sv", "kat",
    "L", "l", "eV", "Wh", "bar",
})

# Scientific pack notation beyond SI: nominal astronomy constants, conventional
# angles, concentrations, counts and calendar durations. All factors are exact
# rational definitions of the stated decimal conventions (angles use a
# high-precision rational approximation to pi, which is irrational).
_DEGREE = Unit(_ZERO, Fraction(Decimal("0.017453292519943295769236907684886127134428718885417")), angle_power=1)
_YEAR = Unit(s.dimensions, Fraction(31_557_600))  # Julian year (365.25 d)
_PC = Unit(m.dimensions, Fraction(30_856_775_814_913_673))
_UNITS.update({
    "deg": _DEGREE, "°": _DEGREE, "°N": _DEGREE, "°E": _DEGREE,
    "arcmin": Unit(_ZERO, _DEGREE.scale / 60, angle_power=1),
    "arcsec": Unit(_ZERO, _DEGREE.scale / 3600, angle_power=1),
    "mas": Unit(_ZERO, _DEGREE.scale / 3_600_000, angle_power=1),
    "%": Unit(_ZERO, Fraction(1, 100)), "wt%": Unit(_ZERO, Fraction(1, 100)),
    "ppm": Unit(_ZERO, Fraction(1, 10**6)), "ppb": Unit(_ZERO, Fraction(1, 10**9)),
    "day": Unit(s.dimensions, Fraction(86400)), "days": Unit(s.dimensions, Fraction(86400)),
    "yr": _YEAR, "year": _YEAR, "years": _YEAR,
    "Ma": Unit(s.dimensions, _YEAR.scale * 10**6),
    "pc": _PC, "AU": _UNITS["au"],
    "M": mol / _UNITS["L"],  # molarity, e.g. mM and nM
    "u": Unit(kg.dimensions, Fraction(Decimal("1.66053906660e-27"))),
    "Da": Unit(kg.dimensions, Fraction(Decimal("1.66053906660e-27"))),  # Dalton = unified atomic mass unit
    "U": Unit((mol / s).dimensions, Fraction(1, 60_000_000)),  # enzyme unit: 1 µmol/min
    "Jy": Unit((0, 1, -2, 0, 0, 0, 0), Fraction(1, 10**26)),
    "solar radii": Unit(m.dimensions, Fraction(695_700_000)),
    "solar masses": Unit(kg.dimensions, Fraction(Decimal("1.98847e30"))),
    "cells": Unit(_ZERO), "cell": Unit(_ZERO),
    "count": Unit(_ZERO), "samples": Unit(_ZERO), "individuals": Unit(_ZERO),
    "individuals per taxon": Unit(_ZERO), "steps (dimensionless)": Unit(_ZERO),
    "stellar radii": Unit(_ZERO), "relative flux": Unit(_ZERO),
    "mag": Unit(_ZERO, kind="magnitude"),
    "mag AB": Unit(_ZERO, kind="ab_magnitude"),
    "mag/airmass": Unit(_ZERO, kind="extinction_magnitude"),
    "Mw (1)": Unit(_ZERO, kind="moment_magnitude"),
    "pKa": Unit(_ZERO, kind="pKa"),
    "1 (zone)": Unit(_ZERO),
    "0/1 (dimensionless)": Unit(_ZERO), "JD (days)": Unit(s.dimensions, Fraction(86400)),
    "mol charge/L": mol / _UNITS["L"],
    "% activity": Unit(_ZERO, Fraction(1, 100)),
    "HV, HBW, HBS, HRC or HRB (as selected)": Unit(_ZERO),
    # Fracture mechanics uses stress times sqrt(length); these dimensions
    # require half-integer exponents, which Unit algebra already supports.
    "Pa√m": Unit(tuple(Fraction(a) + Fraction(b, 2) for a, b in zip(Pa.dimensions, m.dimensions))),
    "kPa√m": Unit(tuple(Fraction(a) + Fraction(b, 2) for a, b in zip(Pa.dimensions, m.dimensions)), Fraction(1000)),
    "MPa√m": Unit(tuple(Fraction(a) + Fraction(b, 2) for a, b in zip(Pa.dimensions, m.dimensions)), Fraction(10**6)),
    "GPa√m": Unit(tuple(Fraction(a) + Fraction(b, 2) for a, b in zip(Pa.dimensions, m.dimensions)), Fraction(10**9)),
})
_PREFIXABLE = _PREFIXABLE | {"M", "Jy", "pc", "yr", "year", "Da", "U"}

# Standalone temperature points are affine; intervals have explicit delta
# names. Composites use interval K (or delta_degC/F), never affine points.
_ABSOLUTE = {
    "K": (Fraction(1), Fraction(0)),
    "°C": (Fraction(1), Fraction(27315, 100)),
    "degC": (Fraction(1), Fraction(27315, 100)),
    "°F": (Fraction(5, 9), Fraction(45967, 180)),
    "degF": (Fraction(5, 9), Fraction(45967, 180)),
}
_DIFFERENCES = {
    "delta_K": Fraction(1), "ΔK": Fraction(1),
    "delta_degC": Fraction(1), "Δ°C": Fraction(1),
    "delta_degF": Fraction(5, 9), "Δ°F": Fraction(5, 9),
}
_TOKEN = re.compile(r"[A-Za-zµμΩΩ°Δ_]+|[0-9]+|[()+*/^\-]")
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")


def _unit_atom(symbol: str) -> Unit:
    if symbol in _DIFFERENCES:
        return Unit(_TEMPERATURE, _DIFFERENCES[symbol])
    if symbol.startswith("delta_") and symbol[6:].endswith("K") and symbol[6:-1] in _PREFIX_POWERS:
        return Unit(_TEMPERATURE, Fraction(10) ** _PREFIX_POWERS[symbol[6:-1]])
    if symbol in _ABSOLUTE and symbol != "K":
        raise ValueError(f"'{symbol}' is an absolute temperature. Use delta_degC or delta_degF for products and quotients.")
    if symbol in _UNITS:
        return _UNITS[symbol]
    for prefix in sorted(_PREFIX_POWERS, key=len, reverse=True):
        suffix = symbol.removeprefix(prefix)
        if suffix != symbol and suffix in _PREFIXABLE:
            factor = Fraction(10) ** _PREFIX_POWERS[prefix]
            unit = _UNITS[suffix]
            return Unit(unit.dimensions, factor * unit.scale, angle_power=unit.angle_power)
    raise ValueError(f"Unknown unit symbol '{symbol}' (unit symbols are case-sensitive).")


def _tokens(symbol: str) -> list[str]:
    expression = symbol.strip().replace("·", "*").replace("⋅", "*").replace("×", "*").replace("**", "^").replace("−", "-")
    # A superscript group such as m² or s⁻¹ is an integer exponent.
    expression = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+", lambda match: "^" + match.group().translate(_SUPERSCRIPT), expression)
    expression = re.sub(r"(?<=[A-Za-zµμΩΩ°Δ_0-9)])\s+(?=[A-Za-zµμΩΩ°Δ_(])", "*", expression)
    expression = re.sub(r"\s+", "", expression)
    tokens = _TOKEN.findall(expression)
    if not tokens or "".join(tokens) != expression or len(tokens) > 96:
        raise ValueError(f"Invalid unit expression '{symbol}'. Use symbols, *, /, parentheses and integer powers.")
    # NIST SP 811 §6.1.7: a repeated solidus or an ungrouped denominator
    # followed by multiplication is ambiguous, not an alternative notation.
    depth, divisor_grouped = 0, {}
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif token == "/":
            if depth in divisor_grouped:
                raise ValueError(f"Ambiguous unit expression '{symbol}': group the denominator with parentheses.")
            divisor_grouped[depth] = index + 1 < len(tokens) and tokens[index + 1] == "("
        elif token == "*" and depth in divisor_grouped and not divisor_grouped[depth]:
            raise ValueError(f"Ambiguous unit expression '{symbol}': group the denominator with parentheses.")
    return tokens


def parse_unit(symbol: str) -> Unit:
    """Read a unit expression into exact SI dimensions, factor and kind."""
    name = symbol.strip()
    if not name or len(name) > 128:
        raise ValueError("Supply a unit symbol or expression of at most 128 characters.")
    if name in _ABSOLUTE:
        scale, offset = _ABSOLUTE[name]
        return Unit(_TEMPERATURE, scale, offset, "absolute")
    if name in _DIFFERENCES:
        return Unit(_TEMPERATURE, _DIFFERENCES[name], kind="difference")
    if name in _UNITS:
        return _UNITS[name]
    # A prefixed kelvin alone is also an absolute temperature point; inside
    # an expression K always represents a linear temperature interval.
    if name.endswith("K") and name[:-1] in _PREFIX_POWERS:
        return Unit(_TEMPERATURE, Fraction(10) ** _PREFIX_POWERS[name[:-1]], kind="absolute")
    if name.startswith("delta_") and name[6:].endswith("K") and name[6:-1] in _PREFIX_POWERS:
        return Unit(_TEMPERATURE, Fraction(10) ** _PREFIX_POWERS[name[6:-1]], kind="difference")
    tokens = _tokens(name)
    position = 0

    def factor(depth: int) -> Unit:
        nonlocal position
        if depth > 12 or position >= len(tokens):
            raise ValueError(f"Invalid unit expression '{symbol}'.")
        token = tokens[position]
        position += 1
        if token == "(":
            result = expression(depth + 1)
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError(f"Unbalanced parentheses in unit expression '{symbol}'.")
            position += 1
        else:
            if token in ("*", "/", ")", "^", "-") or (token.isdigit() and token != "1"):
                raise ValueError(f"Invalid unit expression '{symbol}'.")
            result = _unit_atom(token)
        if position < len(tokens) and tokens[position] == "^":
            position += 1
            sign = -1 if position < len(tokens) and tokens[position] == "-" else 1
            if sign == -1:
                position += 1
            if position >= len(tokens) or not tokens[position].isdigit() or len(tokens[position]) > 2:
                raise ValueError("Unit powers must be integers between -24 and 24.")
            exponent = sign * int(tokens[position])
            if abs(exponent) > 24:
                raise ValueError("Unit powers must be integers between -24 and 24.")
            position += 1
            result = result ** exponent
        return result

    def expression(depth: int) -> Unit:
        nonlocal position
        result = factor(depth)
        while position < len(tokens) and tokens[position] in ("*", "/"):
            operation = tokens[position]
            position += 1
            other = factor(depth)
            result = result * other if operation == "*" else result / other
        return result

    parsed = expression(0)
    if position != len(tokens):
        raise ValueError(f"Invalid unit expression '{symbol}'. Use explicit multiplication and division.")
    if parsed.dimensions == _TEMPERATURE:
        return Unit(parsed.dimensions, parsed.scale, kind="difference", angle_power=parsed.angle_power)
    return parsed


def dimension_string(dimensions: tuple[int | Fraction, ...]) -> str:
    """Format an SI base-dimension vector for diagnostics."""
    items = [f"{symbol}^{exponent}" if exponent != 1 else symbol
             for symbol, exponent in zip(_DIMENSION_SYMBOLS, dimensions) if exponent]
    return " ".join(items) if items else "1 (dimensionless)"


def convert_measurement(value: float, source: str, target: str) -> float:
    """Convert only like SI dimensions and like temperature point/interval kinds."""
    from_unit = parse_unit(source)
    to_unit = parse_unit(target)
    if from_unit.dimensions != to_unit.dimensions:
        raise ValueError(
            f"Incompatible SI dimensions: {source} ({dimension_string(from_unit.dimensions)}) "
            f"cannot be converted to {target} ({dimension_string(to_unit.dimensions)})."
        )
    if from_unit.angle_power != to_unit.angle_power:
        raise ValueError("Incompatible quantities: plane/solid angles and angular frequencies "
                         "cannot be interchanged with ratios or cyclic frequencies.")
    if from_unit.kind != to_unit.kind:
        raise ValueError(
            f"Cannot convert {source} ({from_unit.kind}) to {target} ({to_unit.kind}): "
            "absolute temperatures and temperature differences are distinct; "
            "logarithmic quantities and linear ratios are also distinct."
        )
    if from_unit.kind == "absolute":
        # Written decimal measurements are converted using exact defined
        # affine offsets; Celsius/Kelvin follows the historical decimal path.
        if source in ("K", "°C") and target in ("K", "°C"):
            if value < -float(from_unit.offset):
                raise ValueError("Absolute temperature must be at least 0 K.")
            if source == target:
                return value
            result = float(Decimal(str(value)) + (Decimal(from_unit.offset.numerator) / Decimal(from_unit.offset.denominator)) - (Decimal(to_unit.offset.numerator) / Decimal(to_unit.offset.denominator)))
        else:
            kelvin = Fraction(str(value)) * from_unit.scale + from_unit.offset
            if kelvin < 0:
                raise ValueError("Absolute temperature must be at least 0 K.")
            exact_result = (kelvin - to_unit.offset) / to_unit.scale
            result = value if source == target else float(exact_result)
            if source != target and exact_result != 0 and result == 0:
                raise ValueError("The converted measurement is below the numerical range.")
    else:
        result = value if source == target else float(Fraction(value) * from_unit.scale / to_unit.scale)
        if value and result == 0:
            raise ValueError("The converted measurement is below the numerical range.")
    if not math.isfinite(result):
        raise ValueError("The converted measurement exceeds the numerical range.")
    return result



# User-visible dimensions are more informative than bare exponents for errors.
_DIMENSION_NAMES = {
    _ZERO: "dimensionless (1)",
    m.dimensions: "length (m)", kg.dimensions: "mass (kg)",
    s.dimensions: "time (s)", A.dimensions: "electric current (A)",
    K.dimensions: "temperature (K)", mol.dimensions: "amount of substance (mol)",
    cd.dimensions: "luminous intensity (cd)", Pa.dimensions: "pressure (Pa)",
    N.dimensions: "force (N)", J.dimensions: "energy (J)",
    W.dimensions: "power (W)", V.dimensions: "voltage (V)",
    Ohm.dimensions: "electrical resistance (ohm)",
}


def dimension_name(dimensions: tuple[int | Fraction, ...], angle_power: int = 0,
                   kind: str = "linear") -> str:
    """Human-readable SI dimensions, distinguishing angular and nonlinear quantities."""
    nonlinear = {
        "magnitude": "logarithmic magnitude (mag)",
        "ab_magnitude": "AB logarithmic magnitude (mag AB)",
        "extinction_magnitude": "logarithmic extinction per airmass (mag/airmass)",
        "moment_magnitude": "logarithmic moment magnitude (Mw)",
        "pKa": "logarithmic acid-dissociation index (pKa)",
    }
    if kind in nonlinear:
        return nonlinear[kind]
    if angle_power:
        if dimensions == _ZERO and angle_power == 1:
            return "plane angle (rad)"
        if dimensions == _ZERO and angle_power == 2:
            return "solid angle (sr)"
        if dimensions == (s**-1).dimensions and angle_power == 1:
            return "angular frequency (rad/s)"
        if dimensions == cd.dimensions and angle_power == 2:
            return "luminous flux (lm)"
        if dimensions == (cd / m**2).dimensions and angle_power == 2:
            return "illuminance (lx)"
        return f"SI dimensions {dimension_string(dimensions)}, angular exponent {angle_power}"
    return _DIMENSION_NAMES.get(dimensions, f"SI dimensions {dimension_string(dimensions)}")


def convert_decimal_measurement(text: str, source: str, target: str) -> float:
    """Convert a typed decimal directly with exact rational factors and offsets.

    Unlike converting an intermediate binary64 float, this preserves the
    written decimal until the final result is rounded once for the solver.
    """
    from_unit, to_unit = parse_unit(source), parse_unit(target)
    if from_unit.dimensions != to_unit.dimensions:
        raise ValueError(
            f"Incompatible units: {source} ({dimension_name(from_unit.dimensions)}) "
            f"cannot be converted to {target} ({dimension_name(to_unit.dimensions)})."
        )
    if from_unit.angle_power != to_unit.angle_power:
        raise ValueError("Incompatible quantities: plane/solid angles and angular frequencies "
                         "cannot be interchanged with ratios or cyclic frequencies.")
    if from_unit.kind != to_unit.kind:
        category = ("temperature" if {from_unit.kind, to_unit.kind}
                    <= {"absolute", "difference", "linear"} else "quantity")
        raise ValueError(
            f"Incompatible {category} kinds: {source} is {from_unit.kind}, "
            f"but {target} is {to_unit.kind}; affine temperatures, differences "
            "and logarithmic quantities cannot be mixed with other kinds."
        )
    try:
        decimal = Decimal(text)
    except (ValueError, ArithmeticError):
        raise ValueError("Supply a finite decimal measurement.") from None
    if not decimal.is_finite() or (decimal and abs(decimal.adjusted()) > 1000):
        raise ValueError("The converted measurement exceeds the numerical range.")
    amount = Fraction(decimal)
    kelvin_or_si = amount * from_unit.scale + (from_unit.offset if from_unit.kind == "absolute" else 0)
    if from_unit.kind == "absolute" and kelvin_or_si < 0:
        raise ValueError("Absolute temperature must be at least 0 K.")
    exact = (kelvin_or_si - (to_unit.offset if to_unit.kind == "absolute" else 0)) / to_unit.scale
    try:
        result = float(exact)
    except OverflowError:
        raise ValueError("The converted measurement exceeds the numerical range.") from None
    if not math.isfinite(result):
        raise ValueError("The converted measurement exceeds the numerical range.")
    if exact and result == 0:
        raise ValueError("The converted measurement is below the numerical range.")
    return result


def interval_temperature_unit(symbol: str) -> str:
    """Kelvins (including prefixed kelvins) denote intervals on an interval field."""
    if symbol == "K" or (symbol.endswith("K") and symbol[:-1] in _PREFIX_POWERS):
        return "delta_" + symbol
    return symbol


_HEADER_UNIT = re.compile(r"^(.+?)\s+\(([^()\r\n]+)\)$")


def split_column_header(header: str) -> tuple[str, str]:
    """Read optional units from a typed-table heading without converting data.

    Preserve the original label in table metadata; unknown domain-specific
    units must remain usable even when the SI converter cannot parse them.
    Existing unit parsing and conversion behavior is intentionally unchanged.
    """
    clean = header.strip()
    match = _HEADER_UNIT.fullmatch(clean)
    if match and match.group(1).strip() and match.group(2).strip():
        return match.group(1).strip(), match.group(2).strip()
    return clean, ""
