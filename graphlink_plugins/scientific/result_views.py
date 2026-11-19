"""Bounded, receipt-protected scientific result views.

Views are presentations of deterministic results, not commands or linked input
selectors. They coexist with the legacy single-series plot and table fields.
"""

from __future__ import annotations

import math
import re

MAX_VIEWS = 4
MAX_SURVIVAL_GROUPS = 12
MAX_SURVIVAL_POINTS = 4096


def _label(value, field: str, limit: int = 160) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be nonempty text of at most {limit} characters.")


def _number(value) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _plate_map(view: dict) -> None:
    if set(view) - {"type", "title", "format", "wells", "unit"}:
        raise ValueError("Plate-map view contains an unknown field.")
    size = view.get("format")
    if type(size) is not int or size not in (96, 384):
        raise ValueError("A plate-map view requires a 96- or 384-well format.")
    unit = view.get("unit")
    if unit is not None and (not isinstance(unit, str) or len(unit) > 80):
        raise ValueError("Plate-map unit must be a short text label.")
    wells = view.get("wells")
    if not isinstance(wells, list) or len(wells) > size:
        raise ValueError("Plate-map wells must be a list within the selected plate size.")
    rows, cols = (8, 12) if size == 96 else (16, 24)
    seen: set[str] = set()
    for entry in wells:
        if not isinstance(entry, dict) or set(entry) != {"well", "value"}:
            raise ValueError("Each plate-map well needs a well ID and a value.")
        well = entry["well"]
        match = re.fullmatch(r"([A-P])([1-9][0-9]?)", well) if isinstance(well, str) else None
        if not match or ord(match[1]) - ord("A") >= rows or int(match[2]) > cols or well in seen:
            raise ValueError("Plate-map well IDs must be unique and inside the selected format.")
        seen.add(well)
        value = entry["value"]
        if value is not None and not _number(value) and not (
            isinstance(value, str) and 0 < len(value) <= 128
        ):
            raise ValueError("Plate-map values must be finite numbers, short text, or null.")


def _survival(view: dict) -> None:
    if set(view) != {"type", "title", "xLabel", "groups"}:
        raise ValueError("Survival-curve view requires a title, x axis and groups.")
    _label(view["xLabel"], "Survival x-axis label")
    groups = view["groups"]
    if not isinstance(groups, list) or not 2 <= len(groups) <= MAX_SURVIVAL_GROUPS:
        raise ValueError("A survival-curve view requires 2 to 12 groups.")
    labels: set[str] = set()
    points = 0
    for group in groups:
        if not isinstance(group, dict) or set(group) - {"label", "times", "survival", "censored"}:
            raise ValueError("Survival groups have an invalid shape.")
        _label(group.get("label"), "Survival group label", 80)
        if group["label"] in labels:
            raise ValueError("Survival group labels must be unique.")
        labels.add(group["label"])
        times, values = group.get("times"), group.get("survival")
        if (not isinstance(times, list) or not isinstance(values, list)
                or not times or len(times) != len(values)):
            raise ValueError("Survival groups need equally sized time and probability arrays.")
        points += len(times)
        if points > MAX_SURVIVAL_POINTS:
            raise ValueError("Survival-curve views support at most 4,096 points in total.")
        if not all(_number(t) and t >= 0 for t in times):
            raise ValueError("Survival times must be nonnegative finite numbers.")
        if not all(_number(p) and 0 <= p <= 1 for p in values):
            raise ValueError("Survival probabilities must be between zero and one.")
        if times[0] != 0 or values[0] != 1:
            raise ValueError("Each survival curve must begin at time zero with probability one.")
        if any(t < previous or (i > 1 and t == previous)
               for i, (previous, t) in enumerate(zip(times, times[1:]), 1)):
            raise ValueError("Survival time points must increase (except an event at time zero).")
        if any(current > previous for previous, current in zip(values, values[1:])):
            raise ValueError("Survival curves must be nonincreasing.")
        censored = group.get("censored", [])
        if (not isinstance(censored, list) or len(censored) > len(times)
                or not all(_number(t) and t in times for t in censored)
                or any(b <= a for a, b in zip(censored, censored[1:]))):
            raise ValueError("Censor markers must be distinct ordered observed time points.")


def check_result_views(views) -> None:
    """Validate every supported typed view before storing or trusting a receipt."""
    if not isinstance(views, list) or not 1 <= len(views) <= MAX_VIEWS:
        raise ValueError("Scientific results support between one and four typed views.")
    for view in views:
        if not isinstance(view, dict):
            raise ValueError("Typed result views must be objects.")
        _label(view.get("title"), "Result view title")
        kind = view.get("type")
        if kind == "plate_map":
            _plate_map(view)
        elif kind == "survival_curve":
            _survival(view)
        else:
            raise ValueError("Unsupported typed scientific result view.")
