"""Cell-culture arithmetic for the grant-gated Biology Workbench.

Plans are theoretical volume calculations, not instructions for sterile handling,
centrifuge safety, or organism-specific culture conditions. Standard library only.
"""

from __future__ import annotations

import json
import math

from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.result_views import check_result_views


_MAX_EXACT_COUNT = 2**53 - 1
_RCF_COEFFICIENT = 1.118e-5  # RCF (multiples of g) = coefficient * radius (cm) * rpm²


def _positive(data: dict, key: str) -> float:
    value = data[key]
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be a finite, positive number.")
    return value


def _checked(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} exceeds the supported numeric range.")
    return value


def _counts(values: list, label: str) -> list[int]:
    if not values or any(not math.isfinite(n) or n < 0 or n != int(n) for n in values):
        raise ValueError(f"{label} must contain nonnegative whole-cell counts.")
    result = [int(n) for n in values]
    if sum(result) > _MAX_EXACT_COUNT:
        raise ValueError(f"{label} exceeds the exact-count limit (2^53 - 1).")
    return result


def cell_count_viability(data: dict) -> dict:
    """Count equal-volume hemocytometer regions, correcting for sample dilution."""
    live = _counts(data["live_counts"], "Live counts")
    dead = _counts(data["dead_counts"], "Nonviable counts")
    if len(live) != len(dead):
        raise ValueError("Live and nonviable counts must describe the same regions.")
    live_total, dead_total = sum(live), sum(dead)
    total = live_total + dead_total
    if total == 0:
        raise ValueError("Viability is undefined when no cells were counted.")
    if total > _MAX_EXACT_COUNT:
        raise ValueError("Combined cell counts exceed the exact-count limit (2^53 - 1).")

    dilution = _positive(data, "dilution_factor")
    if dilution < 1:
        raise ValueError("The dilution factor must be at least 1 (undiluted).")
    volume_ul = _positive(data, "counted_volume_ul")
    sample_ml = _positive(data, "sample_volume_ml")
    multiplier = _checked(dilution * (1000 / volume_ul), "Dilution/volume multiplier")
    live_per_ml = _checked((live_total / len(live)) * multiplier, "Viable concentration")
    dead_per_ml = _checked((dead_total / len(dead)) * multiplier, "Nonviable concentration")
    total_per_ml = _checked((total / len(live)) * multiplier, "Total concentration")
    available = _checked(live_per_ml * sample_ml, "Viable cells in sample")
    viability = 100 * live_total / total

    return {
        "summary": (
            f"{viability:.4g}% viable cells; {live_per_ml:.6g} viable cells/mL "
            f"after a {dilution:g}-fold counting dilution. Counts are observations, "
            "not a measurement of culture health beyond dye exclusion."
        ),
        "values": {
            "regions_counted": len(live),
            "live_cells_counted": live_total,
            "nonviable_cells_counted": dead_total,
            "total_cells_counted": total,
            "viability_percent": viability,
            "viability_unit": "%",
            "viable_cells_per_ml": live_per_ml,
            "nonviable_cells_per_ml": dead_per_ml,
            "total_cells_per_ml": total_per_ml,
            "concentration_unit": "cells/mL",
            "sample_volume_ml": sample_ml,
            "sample_volume_unit": "mL",
            "viable_cells_in_sample": available,
            "viable_cells_in_sample_unit": "cells",
        },
        "table": {
            "columns": ["Counted region", "Live (cells)", "Nonviable (cells)", "Total (cells)"],
            "rows": [[i, a, b, a + b] for i, (a, b) in enumerate(zip(live, dead), 1)],
        },
    }


def seeding_plan(data: dict) -> dict:
    """Plan suspension and medium volumes with explicit preparation overage."""
    concentration = _positive(data, "viable_cells_per_ml")
    cells_per_vessel = data["target_cells_per_vessel"]
    vessels = data["vessels"]
    if not 1 <= cells_per_vessel <= _MAX_EXACT_COUNT:
        raise ValueError("Target cells per vessel must be a positive whole number.")
    if not 1 <= vessels <= 1_000_000:
        raise ValueError("Vessel count must be between 1 and 1,000,000.")
    final_ml = _positive(data, "final_volume_ml_per_vessel")
    overage = data["overage_percent"]
    if not math.isfinite(overage) or not 0 <= overage <= 100:
        raise ValueError("Preparation overage must be between 0 and 100 percent.")

    suspension_ml = _checked(cells_per_vessel / concentration, "Suspension per vessel")
    if suspension_ml > final_ml:
        raise ValueError(
            "The required suspension volume exceeds the final vessel volume; "
            "concentrate the suspension or revise the seeding design."
        )
    medium_ml = final_ml - suspension_ml
    prepared_vessels = vessels * (1 + overage / 100)
    bulk_suspension = _checked(suspension_ml * prepared_vessels, "Bulk suspension")
    bulk_medium = _checked(medium_ml * prepared_vessels, "Bulk medium")
    bulk_volume = _checked(final_ml * prepared_vessels, "Bulk final volume")
    bulk_cells = _checked(cells_per_vessel * prepared_vessels, "Bulk viable cells")

    return {
        "summary": (
            f"Seed {vessels} vessels at {cells_per_vessel:g} viable cells each "
            f"in {final_ml:g} mL per vessel. Prepare {bulk_volume:.6g} mL "
            f"total including {overage:g}% overage."
        ),
        "values": {
            "viable_cells_per_ml": concentration,
            "concentration_unit": "cells/mL",
            "target_cells_per_vessel": cells_per_vessel,
            "vessels": vessels,
            "overage_percent": overage,
            "suspension_ml_per_vessel": suspension_ml,
            "medium_ml_per_vessel": medium_ml,
            "final_volume_ml_per_vessel": final_ml,
            "bulk_suspension_ml": bulk_suspension,
            "bulk_medium_ml": bulk_medium,
            "bulk_volume_ml": bulk_volume,
            "bulk_viable_cells": bulk_cells,
            "volume_unit": "mL",
            "cell_count_unit": "cells",
        },
        "table": {
            "columns": [
                "Preparation", "Suspension (mL)", "Medium (mL)",
                "Total (mL)", "Viable cells (cells)",
            ],
            "rows": [
                ["Per vessel", suspension_ml, medium_ml, final_ml, cells_per_vessel],
                ["Bulk with overage", bulk_suspension, bulk_medium, bulk_volume, bulk_cells],
            ],
        },
    }


def _unit_label(label: str) -> str:
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40 or any(ord(c) < 32 for c in label):
        raise ValueError("Concentration unit must be a 1–40 character label.")
    return label.strip()


def serial_dilution_plan(data: dict) -> dict:
    """Backward-plan a serial dilution so EVERY tube retains the target volume.

    The volume mixed at an intermediate step is its desired retained volume
    plus the aliquot that the following tube will take from it.
    """
    initial = _positive(data, "initial_concentration")
    unit = _unit_label(data["concentration_unit"])
    factor = _positive(data, "step_dilution_factor")
    if factor <= 1:
        raise ValueError("The per-step dilution factor must be greater than 1.")
    steps = data["steps"]
    if not 1 <= steps <= 100:
        raise ValueError("Serial dilution supports 1–100 steps.")
    retained_ml = _positive(data, "retained_volume_ml")

    prepared = [0.0] * steps
    required_ml = retained_ml
    for i in range(steps - 1, -1, -1):
        prepared[i] = _checked(required_ml, "Prepared tube volume")
        required_ml = _checked(retained_ml + required_ml / factor, "Required upstream volume")

    stock_ml = _checked(prepared[0] / factor, "Starting solution aliquot")
    if stock_ml <= 0:
        raise ValueError("The starting aliquot underflows the numeric range.")

    rows = []
    concentration = initial
    for i, prepared_ml in enumerate(prepared, 1):
        concentration = _checked(concentration / factor, "Dilution concentration")
        if concentration <= 0:
            raise ValueError("The diluted concentration underflows the numeric range.")
        transfer_ml = _checked(prepared_ml / factor, "Transfer volume")
        diluent_ml = _checked(prepared_ml - transfer_ml, "Diluent volume")
        if transfer_ml <= 0 or diluent_ml <= 0:
            raise ValueError("Transfer or diluent volume underflows the numeric range.")
        rows.append([i, concentration, transfer_ml, diluent_ml, prepared_ml, retained_ml])

    total_diluent = _checked(math.fsum(row[3] for row in rows), "Total diluent")
    total_retained = _checked(retained_ml * steps, "Total retained volume")
    return {
        "summary": (
            f"{steps} successive {factor:g}-fold dilutions: {initial:.6g} to "
            f"{concentration:.6g} {unit}. Back-calculated transfers leave "
            f"{retained_ml:g} mL in each tube after downstream transfers."
        ),
        "values": {
            "initial_concentration": initial,
            "final_concentration": concentration,
            "concentration_unit": unit,
            "step_dilution_factor": factor,
            "steps": steps,
            "retained_volume_ml_per_tube": retained_ml,
            "starting_solution_ml": stock_ml,
            "total_diluent_ml": total_diluent,
            "total_retained_ml": total_retained,
            "volume_unit": "mL",
        },
        "table": {
            "columns": [
                "Tube", f"Concentration ({unit})", "Transfer from previous (mL)",
                "Add diluent (mL)", "Volume mixed (mL)", "Volume left (mL)",
            ],
            "rows": rows,
        },
    }


def centrifuge_rcf_rpm(data: dict) -> dict:
    """RCF at the specified radius, with rpm and RCF as alternate input kinds."""
    radius_cm = _positive(data, "rotor_radius_cm")
    kind = data["input_kind"].strip().lower()
    if kind not in ("rpm", "rcf"):
        raise ValueError("Input kind must be 'rpm' or 'rcf'.")
    value = data["input_value"]
    if not math.isfinite(value) or value < 0:
        raise ValueError("Speed or RCF must be nonnegative and finite.")
    coefficient = _checked(_RCF_COEFFICIENT * radius_cm, "RCF radius coefficient")
    if coefficient <= 0:
        raise ValueError("The radius is too small for a representable conversion.")

    if kind == "rpm":
        rpm = value
        rcf = _checked(coefficient * rpm * rpm, "RCF")
    else:
        rcf = value
        rpm = _checked(math.sqrt(rcf / coefficient), "RPM")

    return {
        "summary": (
            f"At rotor radius {radius_cm:g} cm, {rpm:.6g} rpm corresponds "
            f"to {rcf:.6g} × g. Use the rotor manufacturer's radius definition "
            "and do not exceed instrument or vessel limits."
        ),
        "values": {
            "rotor_radius_cm": radius_cm,
            "radius_unit": "cm",
            "rpm": rpm,
            "rpm_unit": "rev/min",
            "rcf_x_g": rcf,
            "rcf_unit": "× g (relative to standard gravity)",
            "input_kind": kind,
        },
    }


def plate_count_cfu(data: dict) -> dict:
    """Pooled CFU/mL estimate from colony counts and original-sample exposure."""
    counts = _counts(data["colony_counts"], "Colony counts")
    dilutions = data["reciprocal_dilutions"]
    if len(counts) != len(dilutions):
        raise ValueError("Colony counts and reciprocal dilutions must have equal lengths.")
    if any(not math.isfinite(d) or d < 1 for d in dilutions):
        raise ValueError("Reciprocal dilution factors must be finite and at least 1.")
    volume = _positive(data, "plated_volume_ml")

    # A plate at reciprocal dilution D samples volume/D mL of original material.
    # Pool exposures rather than averaging per-plate CFU/mL, which would give
    # tiny exposures the same weight as plates with much greater exposure.
    exposures = [_checked(volume / d, "Original-sample plate exposure") for d in dilutions]
    if any(v <= 0 for v in exposures):
        raise ValueError("A plate's original-sample exposure underflows the numeric range.")
    total_exposure = _checked(sum(exposures), "Total original-sample exposure")
    total = sum(counts)
    estimate = _checked(total / total_exposure, "CFU concentration")
    resolution = _checked(1 / total_exposure, "Single-colony concentration increment")
    countable = sum(30 <= count <= 300 for count in counts)
    rows = [
        [i, count, factor, volume, exposure,
         _checked(count / exposure, "Per-plate CFU concentration"),
         "30–300" if 30 <= count <= 300 else "outside 30–300"]
        for i, (count, factor, exposure) in enumerate(zip(counts, dilutions, exposures), 1)
    ]
    note = ("Zero colonies observed; 0 is the estimate, not proof of absence."
            if not total else
            f"{countable} of {len(counts)} plates are within the indicative 30–300 colony range.")
    return {
        "summary": (
            f"Pooled plate-count estimate: {estimate:.6g} CFU/mL from {total} colonies "
            f"across {len(counts)} plates (equivalent to {total_exposure:.6g} mL "
            f"of undiluted sample). {note} Colony counting assumes independent "
            "recoverable colony-forming units, not a count of individual cells."
        ),
        "values": {
            "plates_counted": len(counts),
            "colonies_counted": total,
            "plates_in_30_to_300_range": countable,
            "original_sample_exposure_ml": total_exposure,
            "plated_volume_ml_per_plate": volume,
            "cfu_per_ml": estimate,
            "single_colony_increment_cfu_per_ml": resolution,
            "concentration_unit": "CFU/mL",
            "volume_unit": "mL",
            "method": "pooled colony count / total original-sample exposure",
        },
        "table": {
            "columns": [
                "Plate", "Colonies (CFU)", "Reciprocal dilution",
                "Plated (mL)", "Original sample (mL)",
                "Estimated CFU/mL", "Indicative countable range",
            ],
            "rows": rows,
        },
    }


def microplate_map(data: dict) -> dict:
    """Lay out sparse, user-labeled numeric or categorical microplate readings."""
    def unique_object(pairs):
        values = {}
        for well, value in pairs:
            if well in values:
                raise ValueError(f"Well {well!r} appears more than once.")
            values[well] = value
        return values

    try:
        readings = json.loads(
            data["wells_json"], object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite well value.")),
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Wells must be a JSON object mapping well IDs to readings.") from exc
    if not isinstance(readings, dict):
        raise ValueError("Wells must be a JSON object mapping well IDs to readings.")
    size = int(data["plate_format"])
    wells = [{"well": well, "value": value} for well, value in readings.items()]
    view = {
        "type": "plate_map", "title": f"{size}-well microplate map",
        "format": size, "unit": data["measurement_label"], "wells": wells,
    }
    check_result_views([view])
    wells.sort(key=lambda entry: (entry["well"][0], int(entry["well"][1:])))
    occupied = sum(entry["value"] is not None for entry in wells)
    return {
        "summary": f"{size}-well plate with {occupied} recorded readings and {size - occupied} empty wells.",
        "values": {
            "plate_format": str(size), "recorded_wells": occupied,
            "empty_wells": size - occupied, "measurement_label": data["measurement_label"],
        },
        "table": {
            "columns": ["Well", data["measurement_label"]],
            "rows": [[entry["well"], entry["value"] if entry["value"] is not None else ""] for entry in wells],
        },
        "views": [view],
    }


def _mpn_score(relative_exposures: list[float], positives: list[int],
               totals: list[int], scaled_concentration: float) -> float:
    """Derivative of Poisson presence/absence log-likelihood, scaled by max exposure."""
    terms = []
    for exposure, positive, total in zip(relative_exposures, positives, totals):
        z = scaled_concentration * exposure
        # exposure/expm1(z) avoids catastrophic cancellation near zero. The
        # Taylor limit avoids underflow when scaled dilutions are extremely small.
        if not positive:
            pos_term = 0.0
        elif z < 1e-6:
            pos_term = positive * (
                1 / scaled_concentration - exposure / 2
                + scaled_concentration * exposure * exposure / 12
            )
        elif z > 700:
            pos_term = 0.0
        else:
            pos_term = positive * exposure / math.expm1(z)
        terms.append(pos_term - (total - positive) * exposure)
    return math.fsum(terms)


def most_probable_number(data: dict) -> dict:
    """Poisson MPN/mL from positive/total tubes at specified reciprocal dilutions."""
    positives = _counts(data["positive_tubes"], "Positive tubes")
    totals = _counts(data["total_tubes"], "Total tubes")
    dilutions = data["reciprocal_dilutions"]
    if len(positives) != len(totals) or len(positives) != len(dilutions):
        raise ValueError("MPN positive, total and dilution vectors must have equal lengths.")
    if any(total == 0 or positive > total for positive, total in zip(positives, totals)):
        raise ValueError("Each dilution needs at least one tube and no more positives than total tubes.")
    if sum(totals) > _MAX_EXACT_COUNT:
        raise ValueError("Total MPN tubes exceed the exact-count limit (2^53 - 1).")
    if any(not math.isfinite(d) or d < 1 for d in dilutions):
        raise ValueError("Reciprocal dilutions must be finite and at least 1.")
    if any(a >= b for a, b in zip(dilutions, dilutions[1:])):
        raise ValueError("MPN reciprocal dilutions must increase strictly from level to level.")
    volume = _positive(data, "inoculum_volume_ml")
    exposures = [_checked(volume / d, "MPN original-sample exposure") for d in dilutions]
    if any(exposure <= 0 for exposure in exposures):
        raise ValueError("MPN tube exposure underflows the numeric range.")

    observed_positive, observed_total = sum(positives), sum(totals)
    if observed_positive == observed_total:
        raise ValueError(
            "Every MPN tube is positive; the Poisson estimate has no finite maximum. "
            "Include a higher dilution with negative tubes."
        )

    if observed_positive == 0:
        estimate = scaled = 0.0
        probabilities = [0.0] * len(exposures)
        note = "All tubes were negative: zero is the maximum-likelihood estimate, not proof of absence."
    else:
        # Write theta = concentration * max(original-sample exposure). Solving
        # in theta keeps the likelihood stable for small inocula/dilutions.
        maximum = max(exposures)
        relative = [exposure / maximum for exposure in exposures]
        if any(exposure <= 0 for exposure in relative):
            raise ValueError("MPN dilution range is too wide for finite-precision fitting.")
        lo, hi = 0.0, 1.0
        while _mpn_score(relative, positives, totals, hi) > 0:
            hi *= 2
            if not math.isfinite(hi):
                raise ValueError("MPN estimate exceeds the supported numeric range.")
        for _ in range(180):
            midpoint = lo + (hi - lo) / 2
            if midpoint == lo or midpoint == hi:
                break
            if _mpn_score(relative, positives, totals, midpoint) > 0:
                lo = midpoint
            else:
                hi = midpoint
        scaled = lo + (hi - lo) / 2
        estimate = _checked(scaled / maximum, "MPN concentration")
        probabilities = [-math.expm1(-scaled * exposure) for exposure in relative]
        note = "Poisson maximum-likelihood estimate; uncertainty and sampling assumptions matter."

    rows = [
        [i, factor, positive, total, exposure, probability,
         _checked(total * probability, "Expected positive tubes")]
        for i, (factor, positive, total, exposure, probability) in enumerate(
            zip(dilutions, positives, totals, exposures, probabilities), 1
        )
    ]
    return {
        "summary": (
            f"Most probable number: {estimate:.6g} MPN/mL from "
            f"{observed_positive}/{observed_total} positive tubes across "
            f"{len(dilutions)} dilution levels. {note}"
        ),
        "values": {
            "mpn_per_ml": estimate,
            "concentration_unit": "MPN/mL",
            "dilution_levels": len(dilutions),
            "positive_tubes": observed_positive,
            "negative_tubes": observed_total - observed_positive,
            "total_tubes": observed_total,
            "inoculum_volume_ml_per_tube": volume,
            "volume_unit": "mL",
            "model": "independent Poisson presence/absence maximum likelihood",
        },
        "table": {
            "columns": [
                "Level", "Reciprocal dilution", "Positive tubes", "Total tubes",
                "Original sample per tube (mL)", "Fitted positive probability",
                "Expected positive tubes",
            ],
            "rows": rows,
        },
    }


def growth_curve_parameters(data: dict) -> dict:
    """Fit a three-parameter modified Gompertz curve to ln(N/N0)."""
    time_unit = _unit_label(data["time_unit"])
    abundance_unit = _unit_label(data["abundance_unit"])
    time, abundance = data["time"], data["abundance"]
    if len(time) != len(abundance) or not 6 <= len(time) <= 120:
        raise ValueError("Growth-curve fitting requires 6–120 paired measurements.")
    if any(value <= 0 for value in abundance):
        raise ValueError("Growth-curve abundance must be strictly positive for natural logarithms.")
    pairs = sorted(zip(time, abundance))
    if any(b[0] <= a[0] for a, b in zip(pairs, pairs[1:])):
        raise ValueError("Growth-curve measurement times must be distinct.")
    times, counts = [p[0] for p in pairs], [p[1] for p in pairs]
    start = times[0]
    span = _checked(times[-1] - start, "Growth-curve time range")
    if span <= 0:
        raise ValueError("Growth-curve time range must be positive.")
    normalized_time = [(t - start) / span for t in times]
    log_counts = [math.log(n) for n in counts]
    response = [value - log_counts[0] for value in log_counts]
    amplitude = max(response)
    if amplitude <= 0 or response[-1] <= 0:
        raise ValueError("Growth fitting needs a net increase in abundance.")
    slopes = [
        (b - a) / (tx2 - tx1)
        for a, b, tx1, tx2 in zip(response, response[1:], normalized_time, normalized_time[1:])
    ]
    peak_rate = max(slopes)
    if peak_rate <= 0 or sum(slopes[-2:]) / 2 >= 0.9 * peak_rate:
        raise ValueError(
            "A lag/asymptote fit needs data approaching a plateau, "
            "not only an increasing exponential-phase series."
        )

    # The modified Gompertz parameterization makes mu the maximum derivative
    # of ln(N/N0), and lag the intersection of its inflection tangent with
    # the zero-growth baseline. Normalizing the time axis aids conditioning.
    peak_index = max(range(len(slopes)), key=lambda i: slopes[i])
    inflection_guess = (normalized_time[peak_index] + normalized_time[peak_index + 1]) / 2
    guesses = [
        [amplitude * a, peak_rate * rate,
         inflection_guess - amplitude * a / (math.e * peak_rate * rate)]
        for a, rate in ((1.05, 1), (1.3, 0.7), (1.02, 1.6))
    ]
    from graphlink_plugins.scientific.packs.biology import _multi_nls

    fitted, parameters, errors = _multi_nls(
        "A*exp(-exp(1+mu*exp(1)*(lag-x)/A))",
        ["A", "mu", "lag"], guesses,
        [(1e-10, None), (1e-10, None), (None, None)],
        normalized_time, response,
    )
    a, mu, lag = parameters["A"], parameters["mu"], parameters["lag"]
    maximum_rate = _checked(mu / span, "Maximum specific growth rate")
    lag_time = _checked(start + lag * span, "Lag time")
    lag_duration = _checked(lag * span, "Lag duration")
    def abundance_from_log(log_value: float, label: str) -> float:
        try:
            number = math.exp(log_value)
        except OverflowError:
            raise ValueError(f"{label} exceeds the supported numeric range.") from None
        if number == 0:
            raise ValueError(f"{label} underflows the supported numeric range.")
        return _checked(number, label)

    asymptote = abundance_from_log(log_counts[0] + a, "Asymptotic abundance")
    doubling = _checked(math.log(2) / maximum_rate, "Fastest instantaneous doubling time")

    def predicted_log(x: float) -> float:
        exponent = 1 + mu * math.e * (lag - x) / a
        return a * math.exp(-math.exp(exponent)) if exponent < 700 else 0.0

    predicted = [
        abundance_from_log(log_counts[0] + predicted_log(x), "Fitted abundance")
        for x in normalized_time
    ]
    residuals = [obs - model for obs, model in zip(log_counts,
                                                     (math.log(n) for n in predicted))]
    sse = fitted["values"]["weighted_residual_sum_squares"]
    average_log = math.fsum(response) / len(response)
    total_ss = math.fsum((v - average_log) ** 2 for v in response)
    r_squared = 1 - sse / total_ss
    note = ("The lag-time tangent intercept is extrapolated before the first sample."
            if lag < 0 else
            "Lag is the fitted tangent intercept, not an observed onset of growth.")
    return {
        "summary": (
            f"Modified Gompertz fit: lag={lag_time:.6g} {time_unit}, "
            f"maximum specific growth rate={maximum_rate:.6g} 1/{time_unit}, "
            f"asymptote={asymptote:.6g} {abundance_unit}; fastest instantaneous "
            f"doubling time={doubling:.6g} {time_unit}. {note}"
        ),
        "values": {
            "model": "modified Gompertz of ln(N/N0), unweighted least squares",
            "observations": len(times),
            "baseline_abundance": counts[0],
            "abundance_unit": abundance_unit,
            "lag_time": lag_time,
            "lag_duration_from_first_sample": lag_duration,
            "lag_time_standard_error": _checked(errors["lag"] * span, "Lag standard error"),
            "lag_time_unit": time_unit,
            "maximum_specific_growth_rate": maximum_rate,
            "maximum_specific_growth_rate_standard_error": _checked(
                errors["mu"] / span, "Growth-rate standard error"
            ),
            "growth_rate_unit": f"1/{time_unit}",
            "asymptote_abundance": asymptote,
            "asymptote_log_ratio": a,
            "asymptote_log_ratio_standard_error": errors["A"],
            "asymptote_unit": abundance_unit,
            "doubling_time_at_max_growth": doubling,
            "doubling_time_unit": time_unit,
            "residual_sum_squares_log": sse,
            "r_squared_log": r_squared,
            "degrees_of_freedom": fitted["values"]["degrees_of_freedom"],
        },
        "table": {
            "columns": [
                f"Time ({time_unit})", f"Observed ({abundance_unit})",
                f"Fitted ({abundance_unit})", "Ln(observed / fitted)",
            ],
            "rows": [
                [t, observed, model, residual]
                for t, observed, model, residual in zip(times, counts, predicted, residuals)
            ],
        },
        "plot": {
            "title": "Modified Gompertz growth curve",
            "xLabel": f"Time ({time_unit})", "yLabel": f"Abundance ({abundance_unit})",
            "x": times, "y": predicted,
        },
    }


def doubling_time(data: dict) -> dict:
    """Two-point exponential-phase doubling time from the log growth ratio."""
    before = _positive(data, "initial_abundance")
    after = _positive(data, "final_abundance")
    elapsed = _positive(data, "elapsed_time")
    time_unit = _unit_label(data["time_unit"])
    abundance_unit = _unit_label(data["abundance_unit"])
    if after <= before:
        raise ValueError("Doubling time requires a measurable increase in abundance.")
    # log(N2) - log(N1) loses the entire growth signal when two large
    # abundances differ by only a few ULPs. Use log1p for near-unity ratios
    # without forming N2/N1 (which could overflow for widely separated data).
    increase = after - before
    log_increase = (math.log1p(increase / before) if increase <= before
                    else math.log(after) - math.log(before))
    rate = _checked(log_increase / elapsed, "Specific growth rate")
    estimate = _checked(math.log(2) / rate, "Doubling time")
    return {
        "summary": (
            f"Exponential-phase doubling time: {estimate:.6g} {time_unit} "
            f"({rate:.6g} 1/{time_unit}) from {before:.6g} to {after:.6g} "
            f"{abundance_unit} over {elapsed:.6g} {time_unit}. "
            "Assumes exponential growth between the two measurements."
        ),
        "values": {
            "initial_abundance": before,
            "final_abundance": after,
            "abundance_unit": abundance_unit,
            "elapsed_time": elapsed,
            "time_unit": time_unit,
            "specific_growth_rate": rate,
            "specific_growth_rate_unit": f"1/{time_unit}",
            "doublings_observed": log_increase / math.log(2),
            "doubling_time": estimate,
            "doubling_time_unit": time_unit,
            "method": "two-point exponential growth; ln(2) * elapsed / ln(N2/N1)",
        },
    }



OPERATIONS = (
    Operation(
        "cell_count_viability", "Hemocytometer count and viability",
        "Live/nonviable counts across equal-volume regions; correct by counting dilution, "
        "estimate cells/mL and dye-exclusion viability.",
        (
            Field("live_counts", "Live cells in each counted region", "vector", unit="cells"),
            Field("dead_counts", "Nonviable cells in each counted region", "vector", unit="cells"),
            Field("dilution_factor", "Total dilution of original sample for counting", "number", unit="1"),
            Field("counted_volume_ul", "Volume of each counted region", "number", unit="uL"),
            Field("sample_volume_ml", "Volume of original cell suspension", "number", unit="mL"),
        ),
        {
            "live_counts": [90, 110, 100, 100], "dead_counts": [10, 10, 20, 0],
            "dilution_factor": 2, "counted_volume_ul": 0.1, "sample_volume_ml": 5,
        },
        cell_count_viability,
    ),
    Operation(
        "seeding_plan", "Viable-cell seeding preparation",
        "Plan per-vessel and bulk cell suspension/medium volumes with a preparation overage.",
        (
            Field("viable_cells_per_ml", "Live cell suspension concentration", "number", unit="cells/mL"),
            Field("target_cells_per_vessel", "Target viable cells per vessel", "integer", unit="cells"),
            Field("vessels", "Number of vessels", "integer", unit="1"),
            Field("final_volume_ml_per_vessel", "Final culture volume per vessel", "number", unit="mL"),
            Field("overage_percent", "Extra preparation volume", "number", unit="%"),
        ),
        {
            "viable_cells_per_ml": 2_000_000, "target_cells_per_vessel": 100_000,
            "vessels": 12, "final_volume_ml_per_vessel": 2, "overage_percent": 10,
        },
        seeding_plan,
    ),
    Operation(
        "serial_dilution_plan", "Serial dilution with retained tube volumes",
        "Plan each successive transfer and diluent addition so all tubes retain the "
        "requested volume, including intermediate tubes.",
        (
            Field("initial_concentration", "Starting solution concentration", "number",
                  unit="selected concentration unit"),
            Field("concentration_unit", "Concentration unit label (e.g. cells/mL)", "text",
                  unit="unit label"),
            Field("step_dilution_factor", "Dilution factor at each step", "number", unit="1"),
            Field("steps", "Number of serial dilution steps", "integer", unit="1"),
            Field("retained_volume_ml", "Required remaining volume in each tube", "number", unit="mL"),
        ),
        {
            "initial_concentration": 1_000_000, "concentration_unit": "cells/mL",
            "step_dilution_factor": 10, "steps": 3, "retained_volume_ml": 1,
        },
        serial_dilution_plan,
    ),
    Operation(
        "centrifuge_rcf_rpm", "Centrifuge RCF ↔ rpm",
        "Convert rotor speed and relative centrifugal force at a user-supplied radius.",
        (
            Field("input_kind", "Input kind: rpm or rcf (× g)", "text", unit="choice"),
            Field("input_value", "Speed (rpm) or relative force (× g)", "number", unit="1"),
            Field("rotor_radius_cm", "Radius from axis to sample (often r_max)", "number", unit="cm"),
        ),
        {"input_kind": "rpm", "input_value": 3000, "rotor_radius_cm": 10},
        centrifuge_rcf_rpm,
    ),
    Operation(
        "plate_count_cfu", "Plate counts and CFU/mL",
        "Pool colony counts from plates with known reciprocal dilutions and plated volume.",
        (
            Field("colony_counts", "Colonies on each plate", "vector", unit="CFU"),
            Field("reciprocal_dilutions", "Reciprocal sample dilution for each plate", "vector", unit="1"),
            Field("plated_volume_ml", "Sample plated on each plate", "number", unit="mL"),
        ),
        {"colony_counts": [45, 55], "reciprocal_dilutions": [1000, 1000],
         "plated_volume_ml": 0.1},
        plate_count_cfu,
    ),
    Operation(
        "microplate_map", "96/384-well microplate map",
        "Display measured or categorical readings in a labeled well grid. Empty wells remain distinct from zero.",
        (
            Field("plate_format", "Microplate format", "choice", unit="wells",
                  choices=("96", "384")),
            Field("wells_json", "Well readings (JSON object)", "text", unit="JSON",
                  description='Use addresses such as A1 and H12 (96) or P24 (384), mapped to numbers, labels or null.'),
            Field("measurement_label", "Reading label / unit", "text", unit="label"),
        ),
        {"plate_format": "96", "wells_json": '{"A1":"control","A2":0,"A3":0.42,"B3":1.25}',
         "measurement_label": "Signal (RFU)"},
        microplate_map,
        methods=(
            "This view preserves each supplied numeric or categorical reading without interpolation or "
            "normalization. The 96-well grid has rows A–H and columns 1–12; the 384-well grid has "
            "rows A–P and columns 1–24. Omitted wells and explicit nulls are unmeasured, not zero."
        ),
    ),
    Operation(
        "most_probable_number", "Most probable number (MPN/mL)",
        "Estimate viable units from positive/negative dilution tubes using Poisson likelihood.",
        (
            Field("positive_tubes", "Positive tubes at each dilution", "vector", unit="tubes"),
            Field("total_tubes", "Tubes tested at each dilution", "vector", unit="tubes"),
            Field("reciprocal_dilutions", "Reciprocal dilution per tube group", "vector", unit="1"),
            Field("inoculum_volume_ml", "Diluted sample inoculated into each tube", "number", unit="mL"),
        ),
        {"positive_tubes": [5, 3, 0], "total_tubes": [5, 5, 5],
         "reciprocal_dilutions": [1, 10, 100], "inoculum_volume_ml": 1},
        most_probable_number,
    ),
    Operation(
        "growth_curve_parameters", "Culture growth-curve parameters",
        "Fit modified Gompertz ln-abundance kinetics for lag, maximum specific growth rate, "
        "asymptotic abundance and fastest instantaneous doubling time.",
        (
            Field("time", "Measurement times", "vector", unit="selected time unit"),
            Field("abundance", "Viable counts or proportional culture abundance", "vector",
                  unit="selected abundance unit"),
            Field("time_unit", "Time unit label (e.g. h)", "text", unit="unit label"),
            Field("abundance_unit", "Abundance unit label (e.g. CFU/mL)", "text", unit="unit label"),
        ),
        {
            "time": list(range(0, 22, 2)),
            "abundance": [
                10_000 * math.exp(4 * math.exp(-math.exp(1 + (0.5 * math.e / 4) * (4 - t))))
                for t in range(0, 22, 2)
            ],
            "time_unit": "h", "abundance_unit": "CFU/mL",
        },
        growth_curve_parameters,
    ),
    Operation(
        "doubling_time", "Two-point culture doubling time",
        "Estimate the exponential-phase specific growth rate and doubling time between two observations.",
        (
            Field("initial_abundance", "Initial count or culture abundance", "number",
                  unit="selected abundance unit"),
            Field("final_abundance", "Final count or culture abundance", "number",
                  unit="selected abundance unit"),
            Field("elapsed_time", "Elapsed time between observations", "number",
                  unit="selected time unit"),
            Field("time_unit", "Time unit label (e.g. h)", "text", unit="unit label"),
            Field("abundance_unit", "Abundance unit label (e.g. CFU/mL)", "text", unit="unit label"),
        ),
        {"initial_abundance": 100_000, "final_abundance": 800_000,
         "elapsed_time": 6, "time_unit": "h", "abundance_unit": "CFU/mL"},
        doubling_time,
    ),
)
