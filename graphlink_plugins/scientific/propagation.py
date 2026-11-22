"""Provenance-aware uncertainty transfer across Scientific Workbench connections.

Legacy measurements remain plain numbers. This optional, receipt-protected
sidecar carries signed 1-sigma components through subsequent calculations so
reusing the *same* measurement does not create independent random variables.
JCGM 100:2008 clauses 5.1/5.2 govern the linearized budget; JCGM 101:2008
clauses 7.4–7.7 govern the explicitly requested/nonlinear Monte Carlo path.
"""

from __future__ import annotations

import math
import random
import re
import statistics
from array import array
from statistics import NormalDist

from .core import Operation, _effective_unit, finite, fingerprint
from .units import parse_unit

_NORMAL_95 = NormalDist().inv_cdf(0.975)
_MAX_ROOTS = 64
_MC_TRIALS = 200_000  # JCGM 101:2008 7.2.2, illustrative minimum at p = 0.95.
_MC_MAX_TRIALS = 1_000_000  # Bound worker memory/time at higher coverage levels.
_UNIT_SUFFIX = re.compile(r"\(([^()]{1,64})\)\s*$")
_UNIT_NAMES = {
    "ma": "Ma", "m": "m", "mm": "mm", "cm": "cm", "km": "km",
    "s": "s", "ms": "ms", "kg": "kg", "g": "g", "j": "J", "k": "K",
    "pa": "Pa", "hz": "Hz", "v": "V", "a": "A", "ohm": "ohm",
    "rad": "rad", "deg": "deg", "mol": "mol", "l": "L",
}


def result_unit(name: str, declared: dict | None = None) -> str:
    """Use only an explicitly labeled result unit, never guess dimensions."""
    if declared and name in declared:
        return declared[name]
    # Legacy oscillator receipts predate explicit output units. A rate's
    # trailing "_s" is its denominator, not a declaration of seconds.
    if name == "angular_frequency_rad_s":
        return "rad/s"
    label = _UNIT_SUFFIX.search(name)
    if label:
        return label.group(1).strip()
    if "_per_" in name or re.search(r"_(?:rad|m|cm|mm|kg|mol|j|w|pa)_[a-z]+$", name, re.I):
        return ""
    # Scientific packs also label scalar output quantities with suffixes.
    for suffix, symbol in sorted(_UNIT_NAMES.items(), key=lambda item: -len(item[0])):
        if name.lower().endswith("_" + suffix):
            return symbol
    return ""


def _numeric(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _native_uncertainty(result: dict, key: str) -> dict | None:
    """Recognize declared standard uncertainty/error pairs in legacy outputs.

    A confidence/percentile interval alone does NOT justify assuming a normal
    distribution. It is marked non-propagatable, not silently treated as exact.
    """
    values = result.get("values", {})
    value = values.get(key)
    if not _numeric(value):
        return None
    sigma, factor = None, _NORMAL_95
    probability = 0.95
    if key == "estimate" and _numeric(values.get("combined_standard_uncertainty")):
        sigma = values["combined_standard_uncertainty"]
        factor = values.get("coverage_factor", _NORMAL_95)
        probability = values.get("coverage_probability", 0.95)
    else:
        index = key.rsplit("_", 1)[-1] if "_" in key else ""
        candidates = (
            key + "_standard_uncertainty", key + "_standard_error",
            key.replace("_per_", "_standard_error_per_"),
            key.replace("_per_", "_standard_uncertainty_per_"),
            key.replace("_", "_standard_uncertainty_", 1) if "_" in key else "",
            key.replace("_", "_standard_error_", 1) if "_" in key else "",
            f"standard_error_{index}" if key.startswith(("coefficient_", "parameter_")) else "",
            "b_approx_standard_error_per_mw" if key == "b_value_per_mw" else "",
            "rate_standard_error_per_day" if key == "decay_constant_per_day" else "",
        )
        for candidate in candidates:
            if candidate and candidate != key and _numeric(values.get(candidate)):
                sigma = values[candidate]
                break
    if sigma is not None:
        if (sigma < 0 or not _numeric(factor) or factor <= 0
                or not _numeric(probability) or not 0 < probability < 1):
            raise ValueError("The connected result reports invalid uncertainty or coverage.")
        interval = None
        if key == "estimate" and _numeric(values.get("coverage_lower")) and _numeric(values.get("coverage_upper")):
            interval = [values["coverage_lower"], values["coverage_upper"]]
        return {"value": value, "standardUncertainty": sigma,
                "coverageFactor": factor, "coverageProbability": probability, "unit": result_unit(key, result.get("provenance", {}).get("outputUnits")),
                **({"interval": interval} if interval else {})}

    # A confidence or bootstrap interval is not a standard uncertainty.
    # It may be asymmetric or use a non-Gaussian sampling distribution.
    interval_pairs = [
        ("ci95_lower", "ci95_upper"),
        ("percentile_lower", "percentile_upper"),
        ("bca_lower", "bca_upper"),
        ("confidence_lower", "confidence_upper"),
    ]
    if key.startswith("odds_ratio_"):
        index = key[len("odds_ratio_"):]
        interval_pairs.append((f"odds_ratio_ci95_lower_{index}", f"odds_ratio_ci95_upper_{index}"))
    if key in ("estimate", "mean", "median", "statistic", "r", "rho") or key.startswith("odds_ratio_"):
        has_interval = any(_numeric(values.get(lower)) and _numeric(values.get(upper))
                           for lower, upper in interval_pairs)
    else:
        has_interval = False
    if has_interval:
        raise ValueError(
            "The connected estimate has only a confidence/bootstrap interval, not a "
            "standard uncertainty or a specified sampling distribution; propagation "
            "is unsupported. Supply an evaluated standard uncertainty instead."
        )
    return None


def connected_measurement(result: dict, selector: list, source_identity: str) -> dict | None:
    """Find the selected metric's uncertainty on a VERIFIED source artifact."""
    if len(selector) != 2 or selector[0] != "values" or not isinstance(selector[1], str):
        return None
    key = selector[1]
    attached = result.get("uncertainty")
    if isinstance(attached, dict):
        error = attached.get("unsupportedOutputs", {}).get(key)
        if error:
            raise ValueError(f"Connected result '{key}': {error}")
        item = attached.get("outputs", {}).get(key)
        if item is not None:
            if item["value"] != result["values"][key]:
                raise ValueError("The connected measurement metadata disagrees with the result.")
            if item.get("method") == "monte_carlo":
                # A marginal mean, standard deviation and interval are not the
                # output PDF (nor its joint PDF with sibling outputs). Sampling
                # a new Gaussian here would lose skew and shared correlations.
                raise ValueError(
                    "The connected Monte Carlo output has no saved joint output "
                    "distribution; downstream uncertainty propagation is unsupported."
                )
            return item
    measurement = _native_uncertainty(result, key)
    if measurement is None:
        return None
    # Same immutable output/selector => same random variable across any number
    # of downstream edges, even after node IDs are reminted on reopen.
    origin = fingerprint(["scientific-uncertainty", source_identity,
                          result["provenance"]["resultHash"], selector])
    return {**measurement, "components": {origin: measurement["standardUncertainty"]},
            "sourceArtifact": fingerprint(["scientific-source-artifact", source_identity,
                                            result["provenance"]["resultHash"]])}


def _checked_components(item: dict) -> dict[str, float]:
    components = item.get("components")
    if not isinstance(components, dict) or not components or len(components) > _MAX_ROOTS:
        raise ValueError("A connected uncertainty has missing or excessive source lineage.")
    if any(not isinstance(k, str) or len(k) != 64 or not _numeric(v) for k, v in components.items()):
        raise ValueError("The connected uncertainty lineage is invalid.")
    u = item.get("standardUncertainty")
    if u is None or not _numeric(u) or u < 0:
        raise ValueError("The connected standard uncertainty must be finite and nonnegative.")
    if not math.isclose(math.hypot(*components.values()), u, rel_tol=1e-9,
                        abs_tol=1e-300):
        # MC nonlinear outputs may have a separate residual component, but the
        # entire component vector must still reconstruct their standard u.
        raise ValueError("The connected uncertainty lineage does not reproduce its standard uncertainty.")
    return components


def _measure_field_units(operation: Operation, inputs: dict, attached: dict) -> None:
    for field in operation.fields:
        if field.key not in attached:
            continue
        unit = attached[field.key].get("unit", "")
        # The catalog's canonical unit takes precedence over a legacy label.
        expected = _effective_unit(field, inputs)
        if not unit:
            continue  # Historic GUM outputs need not declare an output unit.
        try:
            measured, target = parse_unit(unit), parse_unit(expected)
        except ValueError as exc:
            raise ValueError(f"Connected input '{field.key}' has invalid units: {exc}") from None
        if measured.dimensions != target.dimensions or measured.kind != target.kind:
            raise ValueError(
                f"Connected input '{field.key}' carries incompatible units {unit}; "
                f"the operation expects {expected}."
            )
        if unit != expected:
            # Source verification must scale both the value and the lineage.
            # Compatible dimensions alone are not enough for a GUM budget.
            raise ValueError(
                f"Connected input '{field.key}' still carries units {unit}; "
                f"normalize its value and standard uncertainty to {expected} first."
            )


def _output_values(operation: Operation, inputs: dict, names: set[str]) -> dict:
    try:
        result = operation.calculate(inputs)
        values = result["values"]
        if not isinstance(values, dict):
            raise ValueError("The operation has no scalar result values.")
        selected = {name: values[name] for name in names}
        if any(not _numeric(value) for value in selected.values()):
            raise ValueError("A perturbed output is not a finite scalar.")
        return selected
    except (ValueError, TypeError, ArithmeticError, KeyError, OverflowError) as exc:
        raise ValueError("The operation is undefined for the uncertainty perturbations.") from exc


def _derivatives(operation: Operation, inputs: dict, base: dict[str, float],
                 attached: dict) -> tuple[dict[str, dict[str, float]], set[str]]:
    """Central difference sensitivities with scale/ULP-aware perturbations.

    This is an operation-agnostic fallback: the GUM expression pack retains its
    separate analytic derivatives. Undefined or discontinuous sensitivities are
    explicit unsupported outputs, never zeros invented from a failed model.
    """
    names = set(base)
    derivatives: dict[str, dict[str, float]] = {key: {} for key in base}
    curved: set[str] = set()
    for field, measurement in attached.items():
        value = inputs[field]
        if not _numeric(value):
            raise ValueError(f"Connected input '{field}' is not a scalar measurement.")
        u = measurement["standardUncertainty"]
        # A zero input uncertainty makes its contribution zero, but does not
        # make its sensitivity coefficient zero (JCGM 100, Eq. 10).
        # Use an absolute scale at the origin, where the ULP is subnormal.
        h = max(abs(value) * 1e-5, abs(u) * 1e-3,
                1e-8 if value == 0 else 0.0, math.ulp(float(value)) * 16)
        if not math.isfinite(h):
            raise ValueError("A sensitivity perturbation exceeds the numerical range.")
        try:
            plus = _output_values(operation, {**inputs, field: value + h}, names)
            minus = _output_values(operation, {**inputs, field: value - h}, names)
        except ValueError as exc:
            raise ValueError(f"Cannot propagate connected input '{field}': {exc}") from exc
        span = (value + h) - (value - h)
        if span == 0 or not math.isfinite(span):
            raise ValueError(f"Cannot resolve the sensitivity of connected input '{field}'.")
        for key, center in base.items():
            slope = (plus[key] - minus[key]) / span
            finite(slope, f"Sensitivity for {key}")
            derivatives[key][field] = slope
            # Compare the curvature at one standard uncertainty (not h).
            # Detect x^2 around zero, nonlinear ratios, exponential response.
            if u and math.isfinite(value + u) and math.isfinite(value - u):
                try:
                    upper = _output_values(operation, {**inputs, field: value + u}, {key})[key]
                    lower = _output_values(operation, {**inputs, field: value - u}, {key})[key]
                except ValueError:
                    curved.add(key)
                    continue
                linear_span = abs(slope * u)
                bend = abs((upper - center) + (lower - center)) / 2
                # Unlike a derivative at a nominal stationary point, the
                # second-order response can be significant (x² at x=0).
                # Ignore curvature smaller than floating-point evaluation
                # noise; compare the nonlinear bend to the 1-sigma linear span.
                if bend > max(0.1 * linear_span, 64 * math.ulp(center)):
                    curved.add(key)
    return derivatives, curved


def _budget(derivatives: dict[str, float], attached: dict) -> tuple[list[dict[str, str | float]], dict[str, float]]:
    rows: list[dict[str, str | float]] = []
    components_by_root: dict[str, list[float]] = {}
    signed_by_field: dict[str, dict[str, float]] = {}
    for field, slope in derivatives.items():
        item = attached[field]
        signed = finite(slope * item["standardUncertainty"], f"Contribution from {field}")
        vector = {origin: finite(slope * value, "Signed lineage contribution")
                  for origin, value in item["components"].items()}
        signed_by_field[field] = vector
        rows.append({"input": field, "kind": "input", "unit": item.get("unit", ""),
                     **({"sourceUnit": item["sourceUnit"]} if item.get("sourceUnit") else {}),
                     "standardUncertainty": item["standardUncertainty"], "sensitivity": slope,
                     "signedContribution": signed, "variance": signed * signed})
        for origin, value in vector.items():
            components_by_root.setdefault(origin, []).append(value)
    keys = list(derivatives)
    for i, first in enumerate(keys):
        for second in keys[i + 1:]:
            a, b = signed_by_field[first], signed_by_field[second]
            common = a.keys() & b.keys()
            if not common:
                continue
            covariance = 2 * math.fsum(a[root] * b[root] for root in common)
            rows.append({"input": f"{first} × {second}", "kind": "covariance",
                         "variance": finite(covariance, "Shared-source covariance")})
    if len(components_by_root) > _MAX_ROOTS:
        raise ValueError("The calculation exceeds the 64-source uncertainty lineage limit.")
    # fsum once per root: incremental addition loses the small middle term in
    # large + small - large even when the combined uncertainty is resolvable.
    vectors = {root: finite(math.fsum(parts), "Correlated contribution")
               for root, parts in components_by_root.items()}
    return rows, vectors


def _quantile(values: list[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = int(position)
    blend = position - lower
    return values[lower] * (1 - blend) + values[min(lower + 1, len(values) - 1)] * blend


def _mc_trial_count(probability: float) -> int:
    """JCGM 101:2008 7.2.2: enough draws to resolve coverage tails.

    The example recommendation is M >= 10^4/(1-p); a bounded worker cannot
    support arbitrary p, so reject unsupported tail probabilities explicitly.
    """
    recommended = 10_000 / (1 - probability)
    if recommended > _MC_MAX_TRIALS:
        raise ValueError(
            "Monte Carlo coverage probability requires more than 1,000,000 "
            "trials to resolve the tails; use first-order GUM or lower coverage."
        )
    return max(_MC_TRIALS, math.ceil(recommended))


def _monte_carlo(operation: Operation, inputs: dict, attached: dict,
                 keys: set[str], trials: int) -> dict:
    """Seeded Gaussian input PDFs, sampled jointly by *root*, per JCGM 101.

    Reject nonphysical domain draws, rather than silently discarding them
    (which would bias the reported coverage). No PDF is inferred from a bare
    confidence/bootstrap interval.
    """
    seed = int(fingerprint(["mc", operation.id, inputs, attached])[:16], 16)
    rng = random.Random(seed)
    roots = sorted({root for item in attached.values() for root in item["components"]})
    # Compact numeric arrays keep a million draws bounded for typical models.
    samples = {name: array("d") for name in keys}
    for _ in range(trials):
        z = {root: rng.gauss(0.0, 1.0) for root in roots}
        trial = dict(inputs)
        for name, item in attached.items():
            trial[name] = finite(inputs[name] + math.fsum(
                weight * z[root] for root, weight in item["components"].items()), f"Sampled {name}")
        try:
            values = _output_values(operation, trial, keys)
        except ValueError as exc:
            raise ValueError(
                "Monte Carlo cannot propagate this operation: the assumed Gaussian "
                "inputs reach an invalid model domain. Specify a supported input distribution."
            ) from exc
        for name in keys:
            samples[name].append(values[name])
    return samples


def propagate(operation: Operation, inputs: dict, result: dict,
              attached: dict, method: str = "auto") -> dict:
    """Propagate verified connected scalar uncertainties without altering values."""
    if method not in ("auto", "gum", "monte_carlo"):
        raise ValueError("Uncertainty method must be auto, gum or monte_carlo.")
    if len(attached) > 16:
        raise ValueError("At most 16 uncertain connected inputs may be combined.")
    _measure_field_units(operation, inputs, attached)
    field_kinds = {field.key: field.kind for field in operation.fields}
    for name, item in attached.items():
        if field_kinds.get(name) != "number":
            raise ValueError(
                f"Cannot propagate uncertainty through discrete or non-scalar input '{name}'. "
                "Use a continuous numerical measurement input."
            )
        if name not in inputs or not _numeric(inputs[name]) or item.get("value") != inputs[name]:
            raise ValueError(f"Connected input '{name}' has inconsistent measurement values.")
        if not _numeric(item.get("coverageFactor")) or item["coverageFactor"] <= 0:
            raise ValueError(f"Connected input '{name}' has an invalid coverage factor.")
        if not isinstance(item.get("unit"), str) or len(item["unit"]) > 128:
            raise ValueError("Connected measurement units are invalid.")
        _checked_components(item)
    # Two DIFFERENT native metrics from one fitted model can share an unknown
    # covariance (e.g. regression coefficients). Only identical selections or
    # measurements with explicit shared root components are safely composable.
    items = list(attached.items())
    for position, (left_name, left) in enumerate(items):
        for right_name, right in items[position + 1:]:
            if (left.get("sourceArtifact") and left.get("sourceArtifact") == right.get("sourceArtifact")
                    and left["standardUncertainty"] and right["standardUncertainty"]
                    and not (left["components"].keys() & right["components"].keys())):
                raise ValueError(
                    f"Cannot propagate '{left_name}' and '{right_name}' from the same "
                    "scientific result: their mutual covariance is not reported. "
                    "Supply a joint covariance model instead."
                )
    numbers = {key: value for key, value in result["values"].items() if _numeric(value)}
    if not numbers:
        raise ValueError(
            "This operation cannot propagate connected uncertainty: it has no numeric scalar outputs."
        )
    probabilities = {item.get("coverageProbability", 0.95) for item in attached.values()}
    if len(probabilities) != 1 or not all(_numeric(p) and 0 < p < 1 for p in probabilities):
        raise ValueError("Connected uncertainties use different coverage probabilities; "
                         "select results with matching coverage probabilities.")
    probability = next(iter(probabilities))
    derivatives, curved = _derivatives(operation, inputs, numbers, attached)
    use_mc = method == "monte_carlo" or (method == "auto" and bool(curved))
    trial_count = _mc_trial_count(probability) if use_mc else 0
    trials = _monte_carlo(operation, inputs, attached, set(numbers), trial_count) if use_mc else {}
    # A conservative factor for mixed effective degrees of freedom; never
    # silently downgrade a finite-DoF upstream factor to the normal limit.
    factor = max(NormalDist().inv_cdf((1 + probability) / 2),
                 *(item["coverageFactor"] for item in attached.values()))
    outputs, unsupported = {}, {}
    for key, value in numbers.items():
        try:
            native = _native_uncertainty(result, key)
            if native is not None and native["standardUncertainty"] > 0:
                # e.g. radiometric_age already assigns a half-life-derived
                # uncertainty; adding the linked input a second time could
                # double-count it. Without its full covariance model we must
                # not overwrite that native standard uncertainty.
                raise ValueError(
                    "This operation already reports an internal standard uncertainty "
                    "whose covariance with the connected inputs is not specified. "
                    "Propagation cannot safely combine both uncertainty budgets."
                )
            rows, vector = _budget(derivatives[key], attached)
            gum = finite(math.hypot(*vector.values()), f"Combined standard uncertainty of {key}")
            if use_mc:
                series = trials[key]
                estimate = finite(statistics.fmean(series), "Monte Carlo mean")
                std = finite(statistics.stdev(series), "Monte Carlo standard uncertainty")
                ordered = sorted(series)
                alpha = (1 - probability) / 2
                low = finite(_quantile(ordered, alpha), "Monte Carlo coverage lower")
                high = finite(_quantile(ordered, 1 - alpha), "Monte Carlo coverage upper")
                # A nonlinear residual cannot be represented by first derivatives
                # alone. Keep the shared linear components and one independent
                # residual term, preserving their total 1-sigma variance.
                if std < gum:
                    vector = {root: weight * std / gum for root, weight in vector.items()} if gum else {}
                elif std > gum:
                    residual = std * math.sqrt(max(0.0, (1 - gum / std) * (1 + gum / std)))
                    vector[fingerprint(["mc-residual", operation.id, inputs, attached, key])] = residual
            else:
                std, estimate = gum, value
                low, high = finite(value - factor * std, "Coverage lower"), finite(value + factor * std, "Coverage upper")
            unit = result_unit(key, dict(operation.output_units))
            if operation.id == "convert_unit" and key.startswith("value ("):
                unit = inputs["to_unit"]
            if not unit and len(attached) == 1 and key in ("estimate", next(iter(attached))):
                unit = next(iter(attached.values())).get("unit", "")
            outputs[key] = {
                "value": value, "standardUncertainty": std,
                "coverageFactor": factor, "coverageProbability": probability,
                "unit": unit, "interval": [low, high],
                "method": "monte_carlo" if use_mc else "gum",
                "components": vector,
                "budget": rows,
                **({"monteCarloMean": estimate, "monteCarloTrials": trial_count,
                    "budgetNote": "First-order input attribution; Monte Carlo uncertainty and interval."} if use_mc else {}),
            }
        except ValueError as exc:
            unsupported[key] = str(exc)
    if not outputs:
        raise ValueError(
            "This operation cannot propagate the connected uncertainty: "
            + "; ".join(f"{name}: {problem}" for name, problem in unsupported.items())
        )
    return {
        "method": "monte_carlo" if use_mc else "gum",
        "inputs": attached, "outputs": outputs,
        **({"unsupportedOutputs": unsupported} if unsupported else {}),
    }


def check_uncertainty(data: dict, values: dict) -> None:
    """Validate the optional artifact sidecar before accepting a saved receipt."""
    if not isinstance(data, dict) or data.get("method") not in ("gum", "monte_carlo"):
        raise ValueError("Invalid propagated uncertainty method.")
    ins, outs = data.get("inputs"), data.get("outputs")
    if not isinstance(ins, dict) or not isinstance(outs, dict) or not ins or not outs or len(ins) > 16 or len(outs) > 256:
        raise ValueError("Invalid propagated uncertainty inputs or outputs.")
    for key, item in [*ins.items(), *outs.items()]:
        if not isinstance(key, str) or not isinstance(item, dict):
            raise ValueError("Invalid propagated measurement record.")
        for name in ("value", "standardUncertainty", "coverageFactor"):
            if not _numeric(item.get(name)):
                raise ValueError("A propagated measurement must be finite.")
        if (item["standardUncertainty"] < 0 or item["coverageFactor"] <= 0
                or ("coverageProbability" in item and (
                    not _numeric(item["coverageProbability"]) or not 0 < item["coverageProbability"] < 1))):
            raise ValueError("Invalid propagated standard uncertainty or coverage.")
        if not isinstance(item.get("unit"), str) or len(item["unit"]) > 128:
            raise ValueError("Invalid propagated units.")
        if ("sourceUnit" in item and
                (not isinstance(item["sourceUnit"], str) or len(item["sourceUnit"]) > 128)):
            raise ValueError("Invalid connected source units.")
        if "sourceValue" in item and not _numeric(item["sourceValue"]):
            raise ValueError("Invalid connected source measurement.")
        if ("sourceArtifact" in item and (not isinstance(item["sourceArtifact"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sourceArtifact"]))):
            raise ValueError("Invalid native uncertainty source identity.")
        _checked_components(item)
    for key, item in outs.items():
        if key not in values or item["value"] != values[key]:
            raise ValueError("Propagated measurements must match the scalar outputs.")
        if not isinstance(item.get("interval"), list) or len(item["interval"]) != 2 or not all(map(_numeric, item["interval"])):
            raise ValueError("Invalid propagated coverage interval.")
        if not isinstance(item.get("budget"), list) or len(item["budget"]) > 160:
            raise ValueError("Invalid uncertainty budget.")
        for row in item["budget"]:
            if (not isinstance(row, dict) or not isinstance(row.get("input"), str)
                    or row.get("kind") not in ("input", "covariance") or not _numeric(row.get("variance"))):
                raise ValueError("Invalid uncertainty budget contribution.")
    if any(k not in values or not isinstance(v, str)
           for k, v in data.get("unsupportedOutputs", {}).items()):
        raise ValueError("Invalid unpropagatable output notes.")
