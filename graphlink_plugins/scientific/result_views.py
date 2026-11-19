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
MAX_FOREST_STUDIES = 64
MAX_TREE_NODES = 127
MAX_TREE_TIPS = 64
MAX_TREE_DEPTH = 32


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



def _forest_interval(entry: dict, *, pooled: bool, log_scale: bool) -> None:
    allowed = {"estimate", "lower", "upper", "label"} if pooled else {
        "label", "estimate", "lower", "upper", "weight",
    }
    if not isinstance(entry, dict) or set(entry) - allowed or not {
        "estimate", "lower", "upper",
    } <= set(entry):
        raise ValueError("Forest estimates need an estimate and confidence interval.")
    if not pooled or "label" in entry:
        _label(entry.get("label"), "Forest estimate label", 80)
    numbers = (entry["lower"], entry["estimate"], entry["upper"])
    if not all(_number(value) for value in numbers):
        raise ValueError("Forest confidence intervals must contain finite numbers.")
    if not numbers[0] <= numbers[1] <= numbers[2]:
        raise ValueError("Forest confidence intervals must enclose their estimates.")
    if log_scale and numbers[0] <= 0:
        raise ValueError("Log-scale forest estimates and intervals must be positive.")
    if not pooled and "weight" in entry and (
        not _number(entry["weight"]) or entry["weight"] < 0
    ):
        raise ValueError("Forest weights must be nonnegative finite numbers.")


def _forest_plot(view: dict) -> None:
    if set(view) - {"type", "title", "xLabel", "scale", "reference", "studies", "pooled"} or not {
        "type", "title", "xLabel", "scale", "studies", "pooled",
    } <= set(view):
        raise ValueError("Forest plots need an axis, studies, and a pooled estimate.")
    _label(view["xLabel"], "Forest x-axis label")
    if view["scale"] not in ("linear", "log"):
        raise ValueError("Forest scale must be linear or log.")
    log_scale = view["scale"] == "log"
    reference = view.get("reference", 1 if log_scale else 0)
    if not _number(reference) or (log_scale and reference <= 0):
        raise ValueError("Forest reference must be finite and positive on a log axis.")
    studies = view["studies"]
    if not isinstance(studies, list) or not 1 <= len(studies) <= MAX_FOREST_STUDIES:
        raise ValueError("Forest plots require 1 to 64 studies.")
    labels: set[str] = set()
    for study in studies:
        _forest_interval(study, pooled=False, log_scale=log_scale)
        if study["label"] in labels:
            raise ValueError("Forest study labels must be unique.")
        labels.add(study["label"])
    _forest_interval(view["pooled"], pooled=True, log_scale=log_scale)


def _phylogenetic_tree(view: dict) -> None:
    if set(view) != {"type", "title", "layout", "mode", "tree"}:
        raise ValueError("Phylogenetic trees need a layout, mode, and rooted tree.")
    if view["layout"] not in ("rectangular", "circular"):
        raise ValueError("Phylogenetic layout must be rectangular or circular.")
    if view["mode"] not in ("phylogram", "cladogram"):
        raise ValueError("Phylogenetic mode must be phylogram or cladogram.")
    seen: set[int] = set()
    taxa: set[str] = set()
    count = 0
    positive_length = False

    def visit(node, depth: int) -> None:
        nonlocal count, positive_length
        if not isinstance(node, dict) or set(node) - {"name", "length", "support", "children"}:
            raise ValueError("Phylogenetic nodes contain an invalid field.")
        # Reject cycles and reused mutable subtrees, including non-JSON Python inputs.
        if id(node) in seen:
            raise ValueError("Phylogenetic trees must not reuse nodes or contain cycles.")
        seen.add(id(node))
        count += 1
        if count > MAX_TREE_NODES or depth > MAX_TREE_DEPTH:
            raise ValueError("Phylogenetic trees exceed the node or depth limit.")
        if "name" in node:
            _label(node["name"], "Phylogenetic node name", 80)
        if depth == 0:
            if "length" in node or "support" in node:
                raise ValueError("Root nodes cannot have incoming branch annotations.")
        else:
            length = node.get("length")
            if length is not None:
                if not _number(length) or length < 0:
                    raise ValueError("Phylogenetic branch lengths must be finite and nonnegative.")
                positive_length |= length > 0
            if view["mode"] == "phylogram" and "length" not in node:
                raise ValueError("Phylograms require a length on every branch.")
            if "support" in node and (
                not _number(node["support"]) or not 0 <= node["support"] <= 100
            ):
                raise ValueError("Phylogenetic branch support must be between 0 and 100.")
        children = node.get("children")
        if children is None:
            if "children" in node or "name" not in node:
                raise ValueError("Phylogenetic leaves need names and no children field.")
            if node["name"] in taxa:
                raise ValueError("Phylogenetic tip names must be unique.")
            taxa.add(node["name"])
            if len(taxa) > MAX_TREE_TIPS:
                raise ValueError("Phylogenetic trees support at most 64 tips.")
        elif not isinstance(children, list) or not 2 <= len(children) <= 16:
            raise ValueError("Phylogenetic internal nodes need 2 to 16 children.")
        else:
            for child in children:
                visit(child, depth + 1)

    root = view["tree"]
    visit(root, 0)
    if not isinstance(root, dict) or "children" not in root or len(taxa) < 2:
        raise ValueError("Phylogenetic views require a rooted tree with at least two tips.")
    if view["mode"] == "phylogram" and not positive_length:
        raise ValueError("Phylograms need at least one positive branch length.")


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
        elif kind == "forest_plot":
            _forest_plot(view)
        elif kind == "phylogenetic_tree":
            _phylogenetic_tree(view)
        else:
            raise ValueError("Unsupported typed scientific result view.")
