"""Data preparation and inspection shared across scientific disciplines."""

import csv
import io
from decimal import Decimal, InvalidOperation

from graphlink_plugins.scientific.core import MAX_CELLS, MAX_COLUMNS, MAX_POINTS, Field, Operation, finite
from graphlink_plugins.scientific.units import convert_measurement


def _numeric_csv(data):
    reader = csv.reader(io.StringIO(data["csv"].removeprefix("\ufeff"), newline=""), strict=True)
    try:
        header = next((row for row in reader if row), [])
        columns = [v.strip() for v in header]
        if not 1 <= len(columns) <= MAX_COLUMNS or not all(columns) or len(set(columns)) != len(columns):
            raise ValueError("Supply a header with 1–32 distinct, nonempty column names.")
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(columns):
                raise ValueError(f"CSV line {reader.line_num} has the wrong number of columns.")
            if len(rows) >= MAX_POINTS or (len(rows) + 1) * len(columns) > MAX_CELLS:
                raise ValueError("CSV exceeds 1024 rows or 4096 cells.")
            try:
                parsed = []
                for cell in row:
                    number = finite(float(cell), "CSV cell")
                    exact = Decimal(cell)
                    if number == 0 and exact != 0:
                        raise ValueError
                    if exact == exact.to_integral_value() and Decimal.from_float(number) != exact:
                        raise ValueError
                    parsed.append(number)
                rows.append(parsed)
            except (ValueError, InvalidOperation):
                raise ValueError(f"CSV line {reader.line_num} requires finite numeric cells in range and exactly representable integer counts.") from None
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV at line {reader.line_num}: {exc}") from None
    if not rows:
        raise ValueError("The CSV needs at least one measurement row.")
    return {
        "summary": f"Imported {len(rows)} measurement rows and {len(columns)} columns.",
        "values": {"rows": len(rows), "columns": len(columns)},
        "table": {"columns": columns, "rows": rows},
    }


# Preserve the wire-visible summaries of every previously supported conversion.
# Existing receipts reference those summaries and must not be recomputed.
_LEGACY_GROUPS = (
    ("length", frozenset(("mm", "cm", "m", "km"))),
    ("mass", frozenset(("mg", "g", "kg"))),
    ("time", frozenset(("ms", "s", "min", "h"))),
    ("volume", frozenset(("mL", "L", "m³"))),
    ("temperature", frozenset(("K", "°C"))),
)


def _convert(data):
    source = data["from_unit"].strip()
    target = data["to_unit"].strip()
    converted = convert_measurement(data["value"], source, target)
    legacy = next((kind for kind, symbols in _LEGACY_GROUPS if source in symbols and target in symbols), None)
    summary = (f"Converted {legacy} from {source} to {target}." if legacy
               else f"Converted {source} to {target}.")
    return {"summary": summary, "values": {f"value ({target})": converted}}


def _plot(data):
    x, y = data["x"], data["y"]
    if len(x) != len(y) or any(len(data[key].strip()) == 0 or len(data[key]) > 96 for key in ("x_label", "y_label")):
        raise ValueError("Use equal-length coordinates and axis labels of 1–96 characters.")
    return {
        "summary": f"Plotted {len(x)} paired samples in the supplied order.", "values": {"samples": len(x)},
        "table": {"columns": [data["x_label"], data["y_label"]], "rows": [[a, b] for a, b in zip(x, y)]},
        "plot": {"title": "Measured samples", "x": x, "y": y, "xLabel": data["x_label"], "yLabel": data["y_label"]},
    }


_SYMBOLS = (
    "Case-sensitive SI base units (m, kg, s, A, K, mol, cd), SI prefixes, "
    "derived units (N, Pa, J, W, Hz, C, V, ohm/Ω, Wb, T, F, H, L, eV), "
    "and min, h, bar, Wh. Examples: km/h, kWh, MJ, m³, W/(m*K). "
    "Use *, /, ^, parentheses and superscript powers. Absolute temperatures: "
    "K, degC/°C, degF/°F. Temperature differences: delta_K, delta_degC, "
    "delta_degF (or ΔK, Δ°C, Δ°F)."
)
OPERATIONS = (
    Operation("numeric_csv", "Import numeric CSV", "Read comma-separated measurements with a header row.",
              (Field("csv", "CSV measurements", "text", description="Quoted headers and blank lines are accepted. Integer counts must be exactly representable."),),
              {"csv": "Time (s),Distance (m)\n0,0\n1,2\n2,4"}, _numeric_csv),
    Operation("convert_unit", "Convert a measurement", "Convert compatible SI dimensions, compound and prefixed units, and affine temperatures.",
              (Field("value", "Measured value", "number"), Field("from_unit", "Source unit", "text", description=_SYMBOLS),
               Field("to_unit", "Destination unit", "text", description=_SYMBOLS)),
              {"value": 25, "from_unit": "°C", "to_unit": "K"}, _convert),
    Operation("plot_xy", "Plot paired samples", "Connect measured samples in their original sequence.",
              (Field("x", "Horizontal coordinates", "vector"), Field("y", "Vertical coordinates", "vector"),
               Field("x_label", "Horizontal axis with units", "text"), Field("y_label", "Vertical axis with units", "text")),
              {"x": [0, 1, 2], "y": [0, 2, 4], "x_label": "Time (s)", "y_label": "Distance (m)"}, _plot),
)
