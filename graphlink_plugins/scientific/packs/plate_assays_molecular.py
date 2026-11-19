"""Molecular quantification methods for Biology Workbench plate assays.

All inputs are supplied measurements; calculations neither call an instrument
nor infer that a quantitative PCR or spectrophotometry assay passed QC.
"""

from __future__ import annotations

import math
import statistics


def _checked(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} is outside the supported numeric range.")
    return value


def _label(value: str, name: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 40
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{name} must be a 1–40 character label.")
    return value.strip()


def _qpcr_ct(value: float) -> bool:
    """A detected threshold cycle must not be a negative cycle number."""
    return 0 <= value <= 100


def qpcr_standard_curve(data: dict) -> dict:
    """Fit Ct = intercept + slope*log10(starting amount); E = 10**(-1/slope)."""
    amounts, cts = data["standard_quantities"], data["standard_cts"]
    unit = _label(data["quantity_unit"], "Quantity unit")
    if len(amounts) != len(cts) or len(amounts) < 3 or len(set(amounts)) < 3:
        raise ValueError("qPCR standard curves need at least three paired standards at distinct quantities.")
    if any(value <= 0 for value in amounts) or any(not _qpcr_ct(ct) for ct in cts):
        raise ValueError("Standard quantities must be positive and Ct must be between 0 and 100 cycles.")
    xs = [math.log10(value) for value in amounts]
    x_bar, y_bar = statistics.mean(xs), statistics.mean(cts)
    sxx = math.fsum((x - x_bar)**2 for x in xs)
    if sxx <= 0:
        raise ValueError("Standard quantities do not span a log10 range.")
    slope = _checked(math.fsum((x - x_bar) * (ct - y_bar)
                               for x, ct in zip(xs, cts)) / sxx, "qPCR curve slope")
    if slope >= 0:
        raise ValueError("qPCR threshold cycles must decrease as starting quantity increases.")
    intercept = _checked(y_bar - slope * x_bar, "qPCR curve intercept")
    predictions = [intercept + slope * x for x in xs]
    sst = math.fsum((ct - y_bar)**2 for ct in cts)
    if sst <= 0:
        raise ValueError("Ct values must vary for a qPCR standard curve.")
    sse = math.fsum((ct - predicted)**2 for ct, predicted in zip(cts, predictions))
    r2 = _checked(1 - sse / sst, "qPCR R squared")
    factor = _checked(math.exp(-math.log(10) / slope), "qPCR amplification factor")
    efficiency_percent = _checked(100 * (factor - 1), "qPCR amplification efficiency")
    low, high = min(xs), max(xs)
    grid = [low + (high - low) * i / 63 for i in range(64)]
    return {
        "summary": (f"Ct versus log10(starting {unit}) calibration from {len(cts)} standards; "
                    f"amplification efficiency {efficiency_percent:.4g}% (E={factor:.4g} per cycle). "
                    "Efficiency is estimated from the observed slope, not assumed to be 100%."),
        "values": {
            "standards": len(cts), "quantity_unit": unit, "ct_intercept": intercept,
            "ct_slope_per_log10_quantity": slope, "r_squared": r2,
            "amplification_factor": factor, "amplification_efficiency_percent": efficiency_percent,
            "minimum_standard_quantity": min(amounts), "maximum_standard_quantity": max(amounts),
        },
        "table": {
            "columns": [f"Starting quantity ({unit})", "log10(quantity)", "Observed Ct",
                        "Fitted Ct", "Ct residual"],
            "rows": [[amount, x, ct, predicted, _checked(ct - predicted, "Ct residual")]
                     for amount, x, ct, predicted in zip(amounts, xs, cts, predictions)],
        },
        "plot": {"title": "qPCR Ct standard curve", "xLabel": f"log10(starting {unit})",
                 "yLabel": "Ct (cycles)", "x": grid,
                 "y": [_checked(intercept + slope * x, "Fitted Ct") for x in grid]},
    }


def qpcr_relative_expression(data: dict) -> dict:
    """Return both 2^-ddCt and multi-reference Pfaffl efficiency-corrected ratios."""
    sample, control = data["target_ct_sample"], data["target_ct_calibrator"]
    sample_refs = data["reference_cts_sample"]
    control_refs = data["reference_cts_calibrator"]
    efficiencies = data["reference_efficiencies"]
    target_efficiency = data["target_efficiency"]
    count = len(sample_refs)
    if len(control_refs) != count or len(efficiencies) != count:
        raise ValueError("Each reference gene needs paired sample/calibrator Cts and one efficiency.")
    if any(not _qpcr_ct(ct) for ct in [sample, control, *sample_refs, *control_refs]):
        raise ValueError("Detected qPCR Cts must be between 0 and 100 cycles.")
    if any(not 1 < factor <= 3 for factor in [target_efficiency, *efficiencies]):
        raise ValueError("Amplification factors must be >1 and <=3 (e.g. 2.0, not 100%).")
    mean_sample = statistics.mean(sample_refs)
    mean_control = statistics.mean(control_refs)
    delta_sample = sample - mean_sample
    delta_control = control - mean_control
    ddct = _checked(delta_sample - delta_control, "Delta-delta Ct")
    conventional_ratio = _checked(math.exp(-math.log(2) * ddct), "Delta-delta Ct fold change")
    target_log_ratio = (control - sample) * math.log(target_efficiency)
    reference_log_ratios = [(cal - measured) * math.log(factor)
                            for measured, cal, factor in zip(sample_refs, control_refs, efficiencies)]
    ref_mean_log_ratio = math.fsum(reference_log_ratios) / count
    reference_normalizer = _checked(math.exp(ref_mean_log_ratio), "Reference geometric mean")
    target_ratio = _checked(math.exp(target_log_ratio), "Target relative quantity")
    pfaffl_ratio = _checked(math.exp(target_log_ratio - ref_mean_log_ratio), "Pfaffl fold change")
    rows = [["Target", sample, control, target_efficiency, control - sample, target_ratio]]
    rows.extend([f"Reference {i}", measured, cal, factor, cal - measured,
                 _checked(math.exp(log_ratio), "Reference relative quantity")]
                for i, (measured, cal, factor, log_ratio) in enumerate(
                    zip(sample_refs, control_refs, efficiencies, reference_log_ratios), 1))
    return {
        "summary": (f"Sample versus calibrator normalized to the geometric mean of {count} "
                    f"reference gene relative quantities. 2^-ΔΔCt={conventional_ratio:.5g}; "
                    f"efficiency-corrected Pfaffl ratio={pfaffl_ratio:.5g}. "
                    "2^-ΔΔCt assumes E=2 for every assay."),
        "values": {
            "reference_genes": count, "reference_mean_ct_sample": mean_sample,
            "reference_mean_ct_calibrator": mean_control,
            "delta_ct_sample": delta_sample, "delta_ct_calibrator": delta_control,
            "delta_delta_ct": ddct, "ddct_fold_change": conventional_ratio,
            "target_amplification_factor": target_efficiency,
            "target_sample_to_calibrator_ratio": target_ratio,
            "reference_geometric_mean_sample_to_calibrator_ratio": reference_normalizer,
            "pfaffl_fold_change": pfaffl_ratio,
        },
        "table": {"columns": ["Assay", "Sample Ct", "Calibrator Ct", "Amplification factor E",
                              "Calibrator − sample Ct", "Sample/calibrator relative quantity"],
                  "rows": rows},
    }


def digital_pcr(data: dict) -> dict:
    """Poisson occupancy inversion with Wilson 95% interval on the partition fraction."""
    positive, total = data["positive_partitions"], data["total_partitions"]
    volume_nl, dilution = data["partition_volume_nl"], data["dilution_factor"]
    if total <= 0 or positive < 0 or positive >= total:
        raise ValueError("Provide total > 0 partitions and 0 <= positive < total; all-positive is saturated.")
    if volume_nl <= 0 or dilution < 1:
        raise ValueError("Partition volume must be positive and reciprocal dilution at least one.")
    negative = total - positive
    p = positive / total
    q = negative / total
    # Use the smaller fraction for log1p when positivity is low, and the
    # negative fraction directly near saturation to avoid rounding p to 1.
    lam = -math.log1p(-p) if p <= 0.5 else -math.log(q)
    # Wilson interval on the negative fraction q, transformed by -ln(q).
    # q_high uses addition; obtain q_low from the product of the Wilson
    # roots, q**2 / den, to avoid cancellation (especially when q == 1,
    # i.e. zero positive partitions). Both bounds remain finite for q > 0.
    z = 1.959963984540054
    z2 = z * z
    den = 1 + z2 / total
    middle = (q + z2 / (2 * total)) / den
    half = z * math.sqrt(q * (1 - q) / total + z2 / (4 * total * total)) / den
    q_high = min(1.0, middle + half)
    q_low = q * q / (den * q_high)
    volume_ul = volume_nl / 1000
    measured = _checked(lam / volume_ul, "dPCR concentration")
    corrected = _checked(measured * dilution, "Original-sample dPCR concentration")
    lower = _checked(-math.log(q_high) / volume_ul * dilution, "dPCR interval lower")
    upper = _checked(-math.log(q_low) / volume_ul * dilution, "dPCR interval upper")
    return {
        "summary": (f"{positive} of {total} partitions positive; Poisson loading "
                    f"λ={lam:.5g} copies/partition; corrected concentration "
                    f"{corrected:.6g} copies/µL of original sample. "
                    "95% interval is Wilson-score occupancy transformed through Poisson."),
        "values": {
            "positive_partitions": positive, "negative_partitions": total - positive,
            "total_partitions": total, "positive_fraction": p,
            "copies_per_partition": lam, "partition_volume_nl": volume_nl,
            "reciprocal_dilution_factor": dilution, "copies_per_ul_measured": measured,
            "copies_per_ul_original": corrected, "ci95_lower_copies_per_ul_original": lower,
            "ci95_upper_copies_per_ul_original": upper,
        },
        "table": {"columns": ["Positive", "Negative", "Total", "Fraction positive",
                              "Copies/partition", "Measured copies/µL", "Original copies/µL"],
                  "rows": [[positive, total - positive, total, p, lam, measured, corrected]]},
    }


def a260_quantification(data: dict) -> dict:
    """Blank-correct A260, Beer–Lambert mass concentration and purity ratios."""
    kind = data["nucleic_acid_type"]
    length, dilution = data["path_length_cm"], data["dilution_factor"]
    if length <= 0 or dilution < 1:
        raise ValueError("Optical path length must be positive and dilution factor at least one.")
    corrected = {w: _checked(data[f"a{w}"] - data[f"blank_a{w}"], f"Blank-corrected A{w}")
                 for w in (260, 280, 230)}
    if corrected[260] < 0:
        raise ValueError("Blank-corrected A260 must be nonnegative for mass quantification.")
    conversion = {"dsDNA": 50.0, "ssDNA": 33.0, "RNA": 40.0}[kind]
    absorbance_1cm = _checked(corrected[260] / length, "Path-normalized A260")
    measured = _checked(absorbance_1cm * conversion, "Measured nucleic-acid concentration")
    original = _checked(measured * dilution, "Original nucleic-acid concentration")

    def purity(denominator: int) -> float | str:
        if corrected[denominator] <= 0:
            return f"undefined (nonpositive blank-corrected A{denominator})"
        return _checked(corrected[260] / corrected[denominator], f"A260/A{denominator}")

    return {
        "summary": (f"Blank-corrected {kind} A260 at {length:g} cm path length gives "
                    f"{measured:.6g} ng/µL in the measured solution and "
                    f"{original:.6g} ng/µL in the original sample. "
                    "Purity ratios are indicators, not guarantees of sample identity or quality."),
        "values": {
            "nucleic_acid_type": kind, "path_length_cm": length,
            "reciprocal_dilution_factor": dilution,
            "a260_blank_corrected": corrected[260],
            "a280_blank_corrected": corrected[280],
            "a230_blank_corrected": corrected[230],
            "a260_at_1cm": absorbance_1cm,
            "conversion_factor_ng_ul_per_a260_1cm": conversion,
            "concentration_ng_per_ul_measured": measured,
            "concentration_ng_per_ul_original": original,
            "a260_a280_ratio": purity(280), "a260_a230_ratio": purity(230),
        },
        "table": {"columns": ["Wavelength (nm)", "Sample absorbance", "Blank absorbance",
                              "Corrected absorbance"],
                  "rows": [[w, data[f"a{w}"], data[f"blank_a{w}"], corrected[w]]
                           for w in (230, 260, 280)]},
    }
