"""Epidemiology and biostatistics operations for the Biology Workbench.

Counts are independent, nonnegative observations. Rates use independent
Poisson counts; confidence intervals are two-sided 95% unless stated otherwise.
Only Python's standard library is required.
"""

from __future__ import annotations

import math

from graphlink_plugins.scientific.core import Citation, Field, Operation


_Z95 = 1.959963984540054
_TAIL = 0.025
_MAX_COUNT = 2**53 - 1
_MAX_POISSON_COUNT = 1_000_000


def _count(value, name):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < 0 or value > _MAX_COUNT or value != int(value)):
        raise ValueError(f"{name} must be a nonnegative whole-number count below 2^53.")
    return int(value)


def _counts(values, name):
    counts = [_count(value, name) for value in values]
    if sum(counts) > _MAX_COUNT:
        raise ValueError(f"{name} total exceeds the exact-count limit (2^53 - 1).")
    return counts


def _positive(value, name):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be strictly positive and finite.")
    return value


def _unit(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 40
            or any(ord(char) < 32 for char in value)):
        raise ValueError("Person-time unit must be a label of 1–40 printable characters.")
    return value.strip()


def _finite(value, name):
    if not math.isfinite(value):
        raise ValueError(f"{name} exceeds the supported numeric range.")
    return value


def _wilson(successes, total):
    """Wilson 95% binomial limits, including 0/n and n/n boundaries."""
    if not total:
        return None
    p = successes / total
    z2 = _Z95 * _Z95
    denominator = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denominator
    margin = _Z95 * math.sqrt(p * (1 - p) / total + z2 / (4 * total**2)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _proportion(successes, total):
    if not total:
        return ("undefined (empty denominator)",) * 3
    return successes / total, *_wilson(successes, total)


def two_by_two_measures(data):
    """Comparative risk, RR, OR, RD and reciprocal absolute risk difference."""
    a, b, c, d = (_count(data[key], key) for key in (
        "treated_events", "treated_nonevents", "control_events", "control_nonevents"))
    if a + b + c + d > _MAX_COUNT:
        raise ValueError("The combined table count exceeds 2^53 - 1.")
    n_t, n_c = a + b, c + d
    if not n_t or not n_c:
        raise ValueError("Both treated/exposed and control/unexposed groups need observations.")
    p_t, p_c = a / n_t, c / n_c
    rd = p_t - p_c
    t_lo, t_hi = _wilson(a, n_t)
    c_lo, c_hi = _wilson(c, n_c)
    # Newcombe's unpooled Wilson-score (method 10) difference interval.
    rd_lo = max(-1.0, rd - math.hypot(p_t - t_lo, c_hi - p_c))
    rd_hi = min(1.0, rd + math.hypot(t_hi - p_t, p_c - c_lo))

    # Double-zero or double-all-event tables give no direction of relative
    # effect. A fixed 0.5 correction would create a spurious RR/OR merely
    # from unequal group sizes; retain estimable absolute-risk measures.
    if (a == c == 0) or (b == d == 0):
        reason = ("zero events in both groups" if a == c == 0
                  else "all subjects had events in both groups")
        rr = odds_ratio = f"undefined ({reason})"
        rr_ci = or_ci = (rr, rr)
        correction = 0.0
    else:
        # For other zero-cell tables, show the all-cell 0.5 correction on
        # both the effect estimate and its Katz/Woolf interval.
        correction = 0.5 if 0 in (a, b, c, d) else 0.0
        aa, bb, cc, dd = (value + correction for value in (a, b, c, d))
        rr = (aa / (aa + bb)) / (cc / (cc + dd))
        odds_ratio = (aa / bb) / (cc / dd)
        rr_se = math.sqrt(1 / aa - 1 / (aa + bb) + 1 / cc - 1 / (cc + dd))
        or_se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
        log_rr, log_or = math.log(rr), math.log(odds_ratio)
        rr_ci = (math.exp(log_rr - _Z95 * rr_se), math.exp(log_rr + _Z95 * rr_se))
        or_ci = (math.exp(log_or - _Z95 * or_se), math.exp(log_or + _Z95 * or_se))

    reduction = -rd  # Control risk minus treatment risk.
    kind = "NNT" if reduction > 0 else "NNH" if reduction < 0 else "undefined"
    needed = 1 / abs(reduction) if reduction else "undefined (equal risks)"
    reduction_lo, reduction_hi = -rd_hi, -rd_lo
    if reduction_lo > 0:  # Benefit throughout the interval.
        n_lo, n_hi = 1 / reduction_hi, 1 / reduction_lo
    elif reduction_hi < 0:  # Harm throughout the interval (report NNH magnitude).
        n_lo, n_hi = 1 / -reduction_lo, 1 / -reduction_hi
    else:
        n_lo = n_hi = "unbounded (risk-difference CI includes zero)"
    rr_text = rr if isinstance(rr, str) else f"{rr:.6g}"
    or_text = odds_ratio if isinstance(odds_ratio, str) else f"{odds_ratio:.6g}"
    return {
        "summary": (f"Treated risk {p_t:.6g}; control risk {p_c:.6g}; "
                    f"RR {rr_text}, OR {or_text}, risk difference {rd:.6g}; "
                    f"{kind} {needed if isinstance(needed, str) else f'{needed:.6g}'}. "
                    "RR/OR use a 0.5 all-cell correction for other zero-cell tables; "
                    "both are undefined when both arms have no events or all events. "
                    "NNT/NNH is interpreted for a treatment comparison, not a causal claim."),
        "values": {
            "treated_risk": p_t, "control_risk": p_c,
            "treated_risk_ci95_lower": t_lo, "treated_risk_ci95_upper": t_hi,
            "control_risk_ci95_lower": c_lo, "control_risk_ci95_upper": c_hi,
            "risk_ratio": rr, "risk_ratio_ci95_lower": rr_ci[0], "risk_ratio_ci95_upper": rr_ci[1],
            "odds_ratio": odds_ratio, "odds_ratio_ci95_lower": or_ci[0], "odds_ratio_ci95_upper": or_ci[1],
            "risk_difference": rd, "risk_difference_ci95_lower": rd_lo,
            "risk_difference_ci95_upper": rd_hi,
            "absolute_risk_reduction": reduction,
            "number_needed_kind": kind, "number_needed": needed,
            "number_needed_ci95_lower": n_lo, "number_needed_ci95_upper": n_hi,
            "continuity_correction_per_cell": correction,
            "risk_unit": "1 (probability)", "risk_difference_unit": "1 (probability)",
            "ratio_unit": "1 (dimensionless)", "number_needed_unit": "persons",
        },
        "table": {
            "columns": ["Group", "Events", "Non-events", "Total", "Observed risk"],
            "rows": [["Treated/exposed", a, b, n_t, p_t], ["Control/unexposed", c, d, n_c, p_c]],
        },
        "plot": {"title": "Observed event risks", "xLabel": "Group (1 treated, 2 control)",
                 "yLabel": "Event risk (probability)", "x": [1, 2], "y": [p_t, p_c]},
    }


def diagnostic_test_accuracy(data):
    tp, fp, fn, tn = (_count(data[key], key) for key in (
        "true_positive", "false_positive", "false_negative", "true_negative"))
    total = tp + fp + fn + tn
    if total == 0 or total > _MAX_COUNT:
        raise ValueError("Diagnostic table must have 1–(2^53 - 1) observations.")
    entries = [
        ("sensitivity", tp, tp + fn),
        ("specificity", tn, tn + fp),
        ("positive_predictive_value", tp, tp + fp),
        ("negative_predictive_value", tn, tn + fn),
        ("accuracy", tp + tn, total),
        ("prevalence", tp + fn, total),
    ]
    values = {"subjects": total, "true_positive": tp, "false_positive": fp,
              "false_negative": fn, "true_negative": tn, "proportion_unit": "1 (probability)",
              "likelihood_ratio_unit": "1 (dimensionless)"}
    rows = []
    for name, successes, denominator in entries:
        estimate, lower, upper = _proportion(successes, denominator)
        values[name] = estimate
        values[name + "_ci95_lower"] = lower
        values[name + "_ci95_upper"] = upper
        rows.append([name, successes, denominator, estimate, lower, upper])
    sensitivity, specificity = values["sensitivity"], values["specificity"]
    if isinstance(sensitivity, str) or isinstance(specificity, str):
        lr_positive = lr_negative = "undefined (missing disease stratum)"
    else:
        lr_positive = (sensitivity / (1 - specificity) if specificity < 1
                       else "infinite (no false positives)" if sensitivity > 0 else "undefined (0/0)")
        lr_negative = ((1 - sensitivity) / specificity if specificity > 0
                       else "infinite (no true negatives)" if sensitivity < 1 else "undefined (0/0)")
    values["positive_likelihood_ratio"] = lr_positive
    values["negative_likelihood_ratio"] = lr_negative
    return {
        "summary": (f"Diagnostic accuracy {(tp + tn) / total:.6g} (n={total}); "
                    "Wilson-score 95% intervals for proportions. Empty subgroups have "
                    "undefined metrics; likelihood-ratio boundaries are not smoothed."),
        "values": values,
        "table": {"columns": ["Measure", "Numerator", "Denominator", "Estimate", "95% low", "95% high"],
                  "rows": rows},
    }


def roc_curve_auc(data):
    labels = _counts(data["disease_status"], "Disease status")
    scores = data["scores"]
    if len(labels) != len(scores) or len(labels) < 2 or any(label not in (0, 1) for label in labels):
        raise ValueError("ROC requires matched scores and 0/1 disease status for at least two subjects.")
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        raise ValueError("ROC AUC needs at least one case and one control.")
    direction = data["positive_direction"]
    if direction not in ("higher", "lower"):
        raise ValueError("Positive direction must be higher or lower.")
    observations = sorted((score if direction == "higher" else -score, label)
                          for score, label in zip(scores, labels))
    observations.reverse()
    tp = fp = 0
    prior_fpr = prior_tpr = area = 0.0
    rows = [["No predicted positives", 0, 0, 0.0, 0.0, 0.0]]
    # The (0, 0) ROC origin has J = 0 and is an eligible threshold. Without
    # it, a non-discriminating test can return a misleading numeric cutoff.
    best_j, best_threshold = 0.0, "No predicted positives"
    index = 0
    while index < len(observations):
        threshold = observations[index][0]
        while index < len(observations) and observations[index][0] == threshold:
            if observations[index][1]:
                tp += 1
            else:
                fp += 1
            index += 1
        tpr, fpr = tp / positives, fp / negatives
        area += (fpr - prior_fpr) * (tpr + prior_tpr) / 2
        cutoff = threshold if direction == "higher" else -threshold
        rows.append([cutoff, tp, fp, tpr, fpr, tpr - fpr])
        if tpr - fpr > best_j + 1e-14:
            best_j, best_threshold = tpr - fpr, cutoff
        prior_fpr, prior_tpr = fpr, tpr
    auc = min(1.0, max(0.0, area))
    # The plot/result inline limits permit 1024 points. AUC is still computed
    # from *all* distinct thresholds before any deterministic view sampling.
    if len(rows) > 1024:
        last = len(rows) - 1
        rows = [rows[round(i * last / 1023)] for i in range(1024)]
    return {
        "summary": (f"Empirical ROC AUC {auc:.6g} from {positives} cases and {negatives} controls "
                    f"(higher-risk scores are {direction}); tied scores cross together. "
                    "AUC is trapezoidal/Mann–Whitney with half credit for ties."),
        "values": {
            "auc": auc, "cases": positives, "controls": negatives,
            "positive_direction": direction, "best_youden_j": best_j,
            "best_youden_threshold": best_threshold,
            "auc_unit": "1 (dimensionless)", "roc_rate_unit": "1 (probability)",
            "threshold_unit": "score (user supplied)",
        },
        "table": {"columns": ["Threshold (inclusive)", "True positives", "False positives",
                              "Sensitivity", "False positive rate", "Youden J"],
                  "rows": rows},
        "plot": {"title": "Empirical ROC curve", "xLabel": "False positive rate (1 - specificity)",
                 "yLabel": "True positive rate (sensitivity)",
                 "x": [row[4] for row in rows], "y": [row[3] for row in rows]},
    }


def _gamma_cdf(shape, x):
    """Regularized incomplete gamma P(shape,x) by series / continued fraction."""
    if x <= 0:
        return 0.0
    factor = math.exp(shape * math.log(x) - x - math.lgamma(shape))
    if x < shape + 1:
        term = summation = 1 / shape
        for i in range(1, 20001):
            term *= x / (shape + i)
            summation += term
            if abs(term) < abs(summation) * 2e-15:
                return min(1.0, max(0.0, factor * summation))
    else:
        b, c = x + 1 - shape, 1e300
        d = 1 / b
        h = d
        for i in range(1, 20001):
            an = -i * (i - shape)
            b += 2
            d = an * d + b
            c = b + an / c
            if abs(d) < 1e-300:
                d = 1e-300
            if abs(c) < 1e-300:
                c = 1e-300
            d = 1 / d
            delta = d * c
            h *= delta
            if abs(delta - 1) < 2e-15:
                return max(0.0, min(1.0, 1 - factor * h))
    raise ValueError("Gamma tail calculation did not converge at these counts.")


def _gamma_quantile(probability, shape):
    """Inverse unit-scale gamma CDF; bisection avoids an external SciPy dependency."""
    if not 0 < probability < 1 or not math.isfinite(shape) or shape <= 0:
        raise ValueError("Gamma quantile needs 0 < p < 1 and a positive finite shape.")
    low, high = 0.0, max(1.0, shape + 12 * math.sqrt(shape))
    while _gamma_cdf(shape, high) < probability:
        high *= 2
        _finite(high, "Gamma upper bracket")
    for _ in range(75):
        mid = (low + high) / 2
        if _gamma_cdf(shape, mid) < probability:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _garwood(count, person_time):
    """Central exact 95% Poisson interval for events per one person-time unit."""
    if count > _MAX_POISSON_COUNT:
        raise ValueError("Exact Poisson CI supports at most 1,000,000 events per calculation.")
    lower = _gamma_quantile(_TAIL, count) / person_time if count else 0.0
    upper = _gamma_quantile(1 - _TAIL, count + 1) / person_time
    return lower, upper


def incidence_rate(data):
    count = _count(data["events"], "Events")
    time = _positive(data["person_time"], "Person-time")
    unit = _unit(data["person_time_unit"])
    scale = _positive(data["rate_scale"], "Rate multiplier")
    if scale > 1e9:
        raise ValueError("Rate multiplier must be at most 10^9.")
    rate = _finite(count / time * scale, "Incidence rate")
    low, high = _garwood(count, time)
    low = _finite(low * scale, "Lower incidence bound")
    high = _finite(high * scale, "Upper incidence bound")
    return {
        "summary": (f"{count} events in {time:.6g} {unit}: {rate:.6g} per "
                    f"{scale:g} {unit} (exact central Garwood 95% CI {low:.6g}–{high:.6g}). "
                    "Assumes independent Poisson events and fixed observed person-time."),
        "values": {"events": count, "person_time": time, "person_time_unit": unit,
                   "rate_scale": scale, "incidence_rate": rate,
                   "incidence_rate_ci95_lower": low, "incidence_rate_ci95_upper": high,
                   "rate_unit": f"events per {scale:g} {unit}"},
        "table": {"columns": ["Events", f"Person-time ({unit})", "Rate", "Exact 95% lower", "Exact 95% upper"],
                  "rows": [[count, time, rate, low, high]]},
    }


def direct_age_standardization(data):
    cases = _counts(data["events_by_age"], "Age-stratum events")
    time = data["person_time_by_age"]
    reference = data["standard_population"]
    if len(cases) != len(time) or len(cases) != len(reference):
        raise ValueError("Cases, person-time and standard population must have matching age strata.")
    if not all(math.isfinite(t) and t > 0 for t in time):
        raise ValueError("Each age stratum needs positive finite person-time.")
    if not all(math.isfinite(w) and w >= 0 for w in reference) or max(reference) <= 0:
        raise ValueError("Reference population weights must be nonnegative, with a positive total.")
    if sum(cases) > _MAX_POISSON_COUNT:
        raise ValueError("Fay–Feuer CI supports at most 1,000,000 total events.")
    unit = _unit(data["person_time_unit"])
    scale = _positive(data["rate_scale"], "Rate multiplier")
    if scale > 1e9:
        raise ValueError("Rate multiplier must be at most 10^9.")
    # Normalize first so very large reference-population counts do not overflow.
    largest = max(reference)
    scaled_weights = [item / largest for item in reference]
    weight_total = math.fsum(scaled_weights)
    weights = [item / weight_total for item in scaled_weights]
    rates = [cases_i / time_i for cases_i, time_i in zip(cases, time)]
    coefficients = [w / t for w, t in zip(weights, time)]
    rate = math.fsum(k * coefficient for k, coefficient in zip(cases, coefficients))
    variance = math.fsum(k * coefficient**2 for k, coefficient in zip(cases, coefficients))
    maximum_coefficient = max(coefficients)
    if not maximum_coefficient or (rate > 0 and variance == 0):
        raise ValueError("Age-standardization weights exceed the supported numeric precision.")
    augmented_rate = rate + maximum_coefficient
    augmented_variance = variance + maximum_coefficient**2
    if not augmented_variance:
        raise ValueError("Age-standardization variance exceeds the supported numeric precision.")
    # Fay–Feuer gamma limits. When all coefficients are equal they reduce to
    # the exact Garwood Poisson limits for the pooled event count.
    low = (_gamma_quantile(_TAIL, rate * rate / variance) * variance / rate
           if rate else 0.0)
    high = (_gamma_quantile(1 - _TAIL, augmented_rate**2 / augmented_variance)
            * augmented_variance / augmented_rate)
    person_time = _finite(math.fsum(time), "Total person-time")
    crude = _finite(sum(cases) / person_time * scale, "Crude incidence rate")
    standardized = _finite(rate * scale, "Age-standardized incidence rate")
    standard_error = _finite(math.sqrt(variance) * scale, "Standardized rate SE")
    low = _finite(low * scale, "Age-standardized lower limit")
    high = _finite(high * scale, "Age-standardized upper limit")
    rows = [[i, k, t, w, r * scale, k * coefficient * scale]
            for i, (k, t, w, r, coefficient) in enumerate(
                zip(cases, time, weights, rates, coefficients), start=1)]
    return {
        "summary": (f"Directly standardized rate {standardized:.6g} events per {scale:g} {unit} "
                    f"(Fay–Feuer gamma 95% CI {low:.6g}–{high:.6g}); "
                    f"crude pooled rate {crude:.6g}. Age strata must align with the reference distribution; "
                    "Poisson events and fixed independent denominators are assumed."),
        "values": {"age_strata": len(cases), "events": sum(cases),
                   "person_time": person_time, "person_time_unit": unit, "rate_scale": scale,
                   "crude_rate": crude, "standardized_rate": standardized,
                   "standardized_rate_se": standard_error,
                   "standardized_rate_ci95_lower": low, "standardized_rate_ci95_upper": high,
                   "rate_unit": f"events per {scale:g} {unit}"},
        "table": {"columns": ["Age stratum (input order)", "Events", f"Person-time ({unit})",
                              "Standard share", "Age-specific rate", "Weighted contribution"],
                  "rows": rows},
        "plot": {"title": "Age-specific incidence rates", "xLabel": "Age stratum (input order)",
                 "yLabel": f"Events per {scale:g} {unit}",
                 "x": list(range(1, len(cases) + 1)),
                 "y": [r * scale for r in rates]},
    }


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "two_by_two_measures", "Two-by-two treatment/exposure effects",
        "Compute RR, OR, risk difference, absolute risk reduction, and NNT/NNH with 95% CIs.",
        (Field("treated_events", "Treated / exposed events", "integer", unit="people"),
         Field("treated_nonevents", "Treated / exposed non-events", "integer", unit="people"),
         Field("control_events", "Control / unexposed events", "integer", unit="people"),
         Field("control_nonevents", "Control / unexposed non-events", "integer", unit="people")),
        {"treated_events": 12, "treated_nonevents": 188,
         "control_events": 30, "control_nonevents": 170}, two_by_two_measures,
        methods=("For independent cohorts, risk = events/total and RD = treated risk − control risk. "
                 "RR uses Katz and OR uses Woolf log-normal 95% intervals; other zero-cell "
                 "tables use a stated Haldane–Anscombe 0.5 correction for RR/OR only. "
                 "Both relative effects are undefined for double-zero or double-all-event tables. "
                 "RD uses Newcombe's unpooled Wilson-score interval. Absolute risk reduction is "
                 "−RD; its reciprocal is NNT (benefit) or NNH (harm). A confidence interval "
                 "crossing zero in RD gives an unbounded/disjoint reciprocal interval."),
        citations=(Citation("Newcombe (1998), Interval estimation for the difference between independent proportions",
                            "https://pubmed.ncbi.nlm.nih.gov/9595617/"),
                   Citation("Altman (1998), Confidence intervals for the number needed to treat",
                            "https://www.bmj.com/content/317/7168/1309"),
                   Citation("Cochrane Handbook, Choosing effect measures (double-zero studies)",
                            "https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-06")),
        headline="risk_ratio",
    ),
    Operation(
        "diagnostic_test_accuracy", "Diagnostic test accuracy",
        "Sensitivity, specificity, predictive values, overall accuracy, prevalence and likelihood ratios.",
        (Field("true_positive", "True positives", "integer", unit="people"),
         Field("false_positive", "False positives", "integer", unit="people"),
         Field("false_negative", "False negatives", "integer", unit="people"),
         Field("true_negative", "True negatives", "integer", unit="people")),
        {"true_positive": 80, "false_positive": 10, "false_negative": 20, "true_negative": 90},
        diagnostic_test_accuracy,
        methods=("Given disease status as reference, sensitivity=TP/(TP+FN), specificity=TN/(TN+FP), "
                 "PPV=TP/(TP+FP), NPV=TN/(TN+FN), accuracy=(TP+TN)/N; binomial Wilson 95% "
                 "intervals. LR+=sensitivity/(1−specificity); LR−=(1−sensitivity)/specificity. "
                 "Undefined denominators and infinite likelihood ratios are reported as text, not NaN."),
        citations=(Citation("Wilson (1927), Probable inference, the law of succession, and statistical inference",
                            "https://doi.org/10.1080/01621459.1927.10502953"),),
        headline="accuracy",
    ),
    Operation(
        "roc_curve_auc", "Empirical ROC curve and AUC",
        "Compute tie-aware sensitivity versus false-positive rate at observed score thresholds and ROC AUC.",
        (Field("scores", "Diagnostic scores", "vector", unit="1 (score, dimensionless)"),
         Field("disease_status", "Reference disease status (0/1)", "vector", unit="1 (binary)"),
         Field("positive_direction", "Higher or lower score indicates disease", "choice",
               unit="category (dimensionless)", choices=("higher", "lower"))),
        {"scores": [0.91, 0.8, 0.8, 0.55, 0.4, 0.1], "disease_status": [1, 1, 0, 1, 0, 0],
         "positive_direction": "higher"}, roc_curve_auc,
        methods=("Sort scores from highest predicted risk to lowest, updating tied observations "
                 "together at each inclusive threshold, with (0,0) and (1,1) endpoints. "
                 "Include the no-predicted-positive (0,0) point when maximizing Youden J. "
                 "AUC uses trapezoidal integration, equivalent to P(score_case > score_control) "
                 "+ 0.5 P(tie). Output the maximizing Youden J threshold as descriptive only; "
                 "no train/test separation or bootstrap CI is inferred."),
        citations=(Citation("Hanley & McNeil (1982), Meaning and use of the area under an ROC curve",
                            "https://pubmed.ncbi.nlm.nih.gov/7063747/"),),
        headline="auc",
        paired_fields=(("scores", "disease_status"),),
    ),
    Operation(
        "incidence_rate", "Incidence rate with exact Poisson CI",
        "Rate per unit person-time and central exact Garwood 95% confidence bounds, including zero events.",
        (Field("events", "Incident events", "integer", unit="events"),
         Field("person_time", "Observed person-time at risk", "number", unit="selected person-time unit"),
         Field("person_time_unit", "Person-time unit (e.g. person-years)", "text", unit="unit label"),
         Field("rate_scale", "Report per how many person-time units", "number", unit="1 (dimensionless)")),
        {"events": 15, "person_time": 2500, "person_time_unit": "person-years", "rate_scale": 1000},
        incidence_rate,
        methods=("Incidence density is count/person-time, multiplied by the chosen scale. "
                 "For Poisson count k, exact central Garwood limits are χ²(2k,0.025)/(2T) "
                 "(zero if k=0) and χ²(2(k+1),0.975)/(2T), evaluated by numerical gamma "
                 "CDF inversion without a normal approximation."),
        citations=(Citation("Garwood (1936), Fiducial limits for the Poisson distribution",
                            "https://doi.org/10.1093/biomet/28.3-4.437"),),
        headline="incidence_rate",
    ),
    Operation(
        "direct_age_standardization", "Direct age standardization",
        "Apply reference-population age shares to age-specific incidence rates with Fay–Feuer gamma CIs.",
        (Field("events_by_age", "Incident events in age-stratum order", "vector", unit="events"),
         Field("person_time_by_age", "Person-time in matching age order", "vector",
               unit="selected person-time unit"),
         Field("standard_population", "Reference-population counts in matching order", "vector", unit="people"),
         Field("person_time_unit", "Person-time unit (e.g. person-years)", "text", unit="unit label"),
         Field("rate_scale", "Report per how many person-time units", "number", unit="1 (dimensionless)")),
        {"events_by_age": [5, 15, 30], "person_time_by_age": [1000, 2000, 1500],
         "standard_population": [4000, 3500, 2500], "person_time_unit": "person-years",
         "rate_scale": 100000}, direct_age_standardization,
        methods=("For each age stratum i, rᵢ=eventsᵢ/person-timeᵢ; standard share "
                 "wᵢ=standardᵢ/Σstandard. DSR=Σwᵢrᵢ; variance=Σeventsᵢ(wᵢ/Tᵢ)². "
                 "Fay–Feuer gamma 95% intervals use gamma quantiles and the maximum wᵢ/Tᵢ "
                 "for the upper bound. Equal coefficients reduce to exact Garwood intervals. "
                 "Independent Poisson counts, aligned strata and fixed denominators are assumed."),
        citations=(Citation("Fay & Feuer (1997), Confidence intervals for directly standardized rates",
                            "https://pubmed.ncbi.nlm.nih.gov/9131766/"),
                   Citation("SAS/STAT, Direct standardization gamma confidence interval",
                            "https://support.sas.com/documentation/cdl/en/statug/67523/HTML/default/statug_stdrate_details14.htm")),
        headline="standardized_rate",
        paired_fields=(("events_by_age", "person_time_by_age", "standard_population"),),
    ),
)


# Advanced epidemiology extends (rather than renumbers) existing operations.
from graphlink_plugins.scientific.packs.epidemiology_advanced import OPERATIONS as _ADVANCED_OPERATIONS

OPERATIONS += _ADVANCED_OPERATIONS
