"""Stratified, pooled and time-to-event epidemiology (standard library only).

The Workbench limits inputs to 1,024 numeric samples. Every operation returns
finite, receipt-verifiable values and bounded native forest/survival views.
"""

from __future__ import annotations

import json
import math
from collections import Counter

from graphlink_plugins.scientific.core import Citation, Field, Operation
from graphlink_plugins.scientific.packs.fitting import _gamma_q

_Z = 1.959963984540054
_MAX_COUNT = 2**53 - 1


def _counts(items, label):
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
           or v > _MAX_COUNT or v != int(v) for v in items):
        raise ValueError(f"{label} must contain nonnegative exact whole-number counts.")
    return [int(v) for v in items]


def _labels(raw, count):
    try:
        labels = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Study labels must be a JSON array of distinct short strings.") from exc
    if (not isinstance(labels, list) or len(labels) != count
            or any(not isinstance(label, str) or not label.strip()
                   or label != label.strip() or len(label) > 80 or any(ord(ch) < 32 for ch in label) for label in labels)
            or len(set(labels)) != count):
        raise ValueError("Study labels must be a JSON array of distinct, nonempty strings (at most 80 characters each).")
    return labels


def _number(value, description):
    if not math.isfinite(value):
        raise ValueError(f"{description} exceeds the supported numeric range.")
    return value


def _ratio(log_estimate):
    if not -700 < log_estimate < 700:
        raise ValueError("Ratio estimate or confidence limit exceeds the supported log scale.")
    return math.exp(log_estimate)


def mantel_haenszel(data):
    """MH common odds ratio, Robins–Breslow–Greenland CI, CMH test."""
    a, b, c, d = [_counts(data[key], key) for key in (
        "exposed_events", "exposed_nonevents", "unexposed_events", "unexposed_nonevents")]
    k = len(a)
    if not 2 <= k <= 64 or any(len(x) != k for x in (b, c, d)):
        raise ValueError("Mantel–Haenszel requires 2–64 matched strata of four cell counts.")
    r = s = pr = ps_qr = qs = observed_minus_expected = variance = 0.0
    correction_count = 0
    rows, studies = [], []
    for i, (aa0, bb0, cc0, dd0) in enumerate(zip(a, b, c, d), 1):
        if aa0 + bb0 == 0 or cc0 + dd0 == 0:
            raise ValueError("Each stratum needs at least one exposed and one unexposed subject.")
        n0 = aa0 + bb0 + cc0 + dd0
        if n0 > _MAX_COUNT:
            raise ValueError("A stratum exceeds the exact-count limit (2^53 - 1).")
        exposed = aa0 + bb0
        unexposed = cc0 + dd0
        cases = aa0 + cc0
        controls = bb0 + dd0
        if n0 > 1:
            observed_minus_expected += aa0 - exposed * cases / n0
            variance += exposed * unexposed * cases * controls / (n0 * n0 * (n0 - 1))
        correction = .5 if 0 in (aa0, bb0, cc0, dd0) else 0.0
        correction_count += bool(correction)
        aa, bb, cc, dd = (v + correction for v in (aa0, bb0, cc0, dd0))
        n = aa + bb + cc + dd
        ri, si = aa * dd / n, bb * cc / n
        pi, qi = (aa + dd) / n, (bb + cc) / n
        r += ri
        s += si
        pr += pi * ri
        ps_qr += pi * si + qi * ri
        qs += qi * si
        log_or = math.log(aa) + math.log(dd) - math.log(bb) - math.log(cc)
        log_se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
        estimate = _ratio(log_or)
        lo, hi = _ratio(log_or - _Z * log_se), _ratio(log_or + _Z * log_se)
        rows.append([i, aa0, bb0, cc0, dd0, estimate, lo, hi, correction])
        studies.append({"label": f"Stratum {i}", "estimate": estimate,
                        "lower": lo, "upper": hi, "weight": 1 / (log_se*log_se)})
    if r <= 0 or s <= 0:
        raise ValueError("The stratified odds ratio has no estimable numerator or denominator.")
    common_log_or = math.log(r) - math.log(s)
    log_variance = pr / (2 * r * r) + ps_qr / (2 * r * s) + qs / (2 * s * s)
    if not math.isfinite(log_variance) or log_variance <= 0:
        raise ValueError("The stratified odds ratio variance is unidentifiable.")
    se = math.sqrt(log_variance)
    common_or = _ratio(common_log_or)
    lower, upper = _ratio(common_log_or - _Z * se), _ratio(common_log_or + _Z * se)
    if variance > 0:
        chi = observed_minus_expected**2 / variance
        p = math.erfc(math.sqrt(chi / 2))
    else:
        chi = p = "undefined (zero CMH variance)"
    return {
        "summary": (f"Mantel–Haenszel common odds ratio {common_or:.6g} "
                    f"(RBG 95% CI {lower:.6g}–{upper:.6g}) across {k} strata; "
                    f"CMH association test without continuity correction. "
                    f"A 0.5 all-cell correction was applied to {correction_count} sparse strata "
                    "for odds-ratio estimation only; assumes a common stratum odds ratio."),
        "values": {"strata": k, "common_odds_ratio": common_or,
                   "common_odds_ratio_ci95_lower": lower, "common_odds_ratio_ci95_upper": upper,
                   "log_common_odds_ratio_se": se, "cmh_chi_square": chi, "cmh_p_value": p,
                   "cmh_degrees_of_freedom": 1 if variance > 0 else 0,
                   "corrected_strata": correction_count, "odds_ratio_unit": "1 (dimensionless)",
                   "p_value_unit": "1 (probability)", "chi_square_unit": "1 (dimensionless)"},
        "table": {"columns": ["Stratum", "Exposed cases", "Exposed noncases", "Unexposed cases",
                              "Unexposed noncases", "OR", "95% lower", "95% upper", "0.5 correction"],
                  "rows": rows},
        "views": [{"type": "forest_plot", "title": "Stratified odds ratios and MH common effect",
                   "xLabel": "Odds ratio (95% CI)", "scale": "log", "reference": 1,
                   "studies": studies,
                   "pooled": {"label": "MH pooled", "estimate": common_or,
                              "lower": lower, "upper": upper}}],
    }


def meta_analysis(data):
    """Inverse-variance common-effect and DerSimonian–Laird random-effects meta-analysis."""
    effect, errors = data["estimates"], data["standard_errors"]
    k = len(effect)
    if not 2 <= k <= 64 or len(errors) != k:
        raise ValueError("Meta-analysis requires 2–64 paired estimates and standard errors.")
    if any(not 1e-12 <= se <= 1e12 for se in errors):
        raise ValueError("Standard errors must be positive, finite and between 1e-12 and 1e12.")
    labels = _labels(data["study_labels_json"], k)
    measure = data["effect_measure"]
    ratio = measure != "mean_difference"
    method = data["model"]
    base = min(errors)
    weights = [(base / se)**2 for se in errors]
    weight_total = math.fsum(weights)
    fixed = math.fsum(w * x for w, x in zip(weights, effect)) / weight_total
    q = _number(math.fsum(((x - fixed) / se)**2 for x, se in zip(effect, errors)), "Cochran Q")
    df = k - 1
    denominator = weight_total - math.fsum(w*w for w in weights) / weight_total
    tau2 = max(0.0, (q - df) * base**2 / denominator) if denominator > 1e-14 else 0.0
    if denominator <= 1e-14 and method == "random_dl":
        raise ValueError("Random-effects heterogeneity is unidentifiable from these study precisions.")
    total_errors = [math.hypot(se, math.sqrt(tau2)) if method == "random_dl" else se
                    for se in errors]
    minimum = min(total_errors)
    relative = [(minimum / se)**2 for se in total_errors]
    total = math.fsum(relative)
    pooled = _number(math.fsum(w * x for w, x in zip(relative, effect)) / total, "Pooled effect")
    pooled_se = _number(minimum / math.sqrt(total), "Pooled standard error")
    low, high = pooled - _Z * pooled_se, pooled + _Z * pooled_se
    transform = _ratio if ratio else lambda x: _number(x, "Effect estimate")
    pooled_display, low_display, high_display = transform(pooled), transform(low), transform(high)
    studies, rows = [], []
    for name, estimate, se, weight in zip(labels, effect, errors, relative):
        shown, lower, upper = transform(estimate), transform(estimate - _Z*se), transform(estimate + _Z*se)
        weight_pct = 100 * weight / total
        studies.append({"label": name, "estimate": shown, "lower": lower,
                        "upper": upper, "weight": weight_pct})
        rows.append([name, estimate, se, shown, lower, upper, weight_pct])
    unit = "ratio (dimensionless)" if ratio else "effect units (user supplied)"
    return {
        "summary": (f"{k}-study inverse-variance meta-analysis ({method}): pooled "
                    f"{pooled_display:.6g} (95% CI {low_display:.6g}–{high_display:.6g}); "
                    f"Q={q:.6g}, I²={max(0., (q-df)/q)*100 if q else 0:.4g}%, "
                    f"DerSimonian–Laird τ²={tau2:.6g}. "
                    "Confidence intervals are normal approximations; random effects use DL, not REML/HK."),
        "values": {"studies": k, "model": method, "effect_measure": measure,
                   "pooled_effect": pooled_display, "pooled_effect_ci95_lower": low_display,
                   "pooled_effect_ci95_upper": high_display, "pooled_analysis_scale_effect": pooled,
                   "pooled_standard_error": pooled_se, "cochran_q": q,
                   "heterogeneity_degrees_of_freedom": df,
                   "i_squared_percent": max(0.0, (q-df)/q) * 100 if q else 0.0,
                   "tau_squared": tau2, "effect_unit": unit,
                   "standard_error_unit": "log-ratio or mean difference", "weight_unit": "%"},
        "table": {"columns": ["Study", "Input effect", "Input SE", "Display effect",
                              "95% lower", "95% upper", "Weight (%)"], "rows": rows},
        "views": [{"type": "forest_plot", "title": f"{method} meta-analysis",
                   "xLabel": unit, "scale": "log" if ratio else "linear",
                   "reference": 1 if ratio else 0, "studies": studies,
                   "pooled": {"label": "Pooled", "estimate": pooled_display,
                              "lower": low_display, "upper": high_display}}],
    }


def multi_group_kaplan_meier(data):
    """KM by 2–12 coded groups and multivariate hypergeometric log-rank test."""
    times, events, codes = data["follow_up_time"], data["event_observed"], data["group"]
    n = len(times)
    if len(events) != n or len(codes) != n or n < 2:
        raise ValueError("Survival analysis needs matching follow-up, event and group observations.")
    if any(t < 0 for t in times) or any(e not in (0, 1) for e in events):
        raise ValueError("Follow-up must be nonnegative; event indicators must be 0/1.")
    if any(g != int(g) or not 1 <= g <= 12 for g in codes):
        raise ValueError("Group codes must be whole numbers from 1 to 12.")
    ids = sorted(set(map(int, codes)))
    k = len(ids)
    if not 2 <= k <= 12:
        raise ValueError("Kaplan–Meier comparison needs 2–12 observed groups.")
    unit = data["time_unit"].strip()
    if not 1 <= len(unit) <= 40 or any(ord(ch) < 32 for ch in unit):
        raise ValueError("Time unit must have 1–40 printable characters.")
    lookup = {code: i for i, code in enumerate(ids)}
    samples = [Counter() for _ in ids]
    sizes = [0]*k
    for t, e, code in zip(times, events, codes):
        g = lookup[int(code)]
        samples[g][(t, int(e))] += 1
        sizes[g] += 1
    at_risk = sizes[:]
    survival = [1.0]*k
    greenwood = [0.0]*k
    median = ["not reached"]*k
    observed, expected = [0.0]*k, [0.0]*k
    covariance = [[0.0]*k for _ in ids]
    curves = [{"label": f"Group {code}", "times": [0.0],
               "survival": [1.0], "censored": []} for code in ids]
    rows = []
    for t in sorted({t for sample in samples for t, _ in sample}):
        deaths = [sample[(t, 1)] for sample in samples]
        censored = [sample[(t, 0)] for sample in samples]
        n_risk, d = sum(at_risk), sum(deaths)
        if d:
            for g in range(k):
                observed[g] += deaths[g]
                expected[g] += d * at_risk[g] / n_risk
            if n_risk > 1:
                factor = d * (n_risk - d) / (n_risk*n_risk*(n_risk - 1))
                for g in range(k):
                    for h in range(k):
                        covariance[g][h] += factor * at_risk[g] * (
                            (n_risk - at_risk[g]) if g == h else -at_risk[h])
        for g in range(k):
            if deaths[g]:
                survivors = at_risk[g] - deaths[g]
                survival[g] *= survivors / at_risk[g]
                if survivors:
                    greenwood[g] += deaths[g] / (at_risk[g] * survivors)
                if median[g] == "not reached" and survival[g] <= .5:
                    median[g] = t
            if deaths[g] or censored[g]:
                curves[g]["times"].append(t)
                curves[g]["survival"].append(survival[g])
                if censored[g]:
                    curves[g]["censored"].append(t)
                rows.append([t, ids[g], at_risk[g], deaths[g], censored[g], survival[g]])
            at_risk[g] -= deaths[g] + censored[g]
    # Drop the last group's contrast, since sum(O-E)=0 and V is singular.
    contrast = [observed[i] - expected[i] for i in range(k-1)]
    try:
        solution, _ = _positive_solve([row[:k-1] for row in covariance[:k-1]], contrast)
        chi = max(0.0, _number(math.fsum(c*v for c, v in zip(contrast, solution)), "Log-rank chi-square"))
        p = _gamma_q((k-1)/2, chi/2)
        df = k - 1
    except ValueError:
        chi = p = "undefined (singular log-rank covariance)"
        df = 0
    summary = (f"Kaplan–Meier product-limit survival for {k} groups ({n} subjects); "
               f"events precede censoring at tied times. "
               f"Multigroup log-rank chi-square uses hypergeometric covariance "
               f"({df} estimable degrees of freedom).")
    group_values = {}
    for i, group_id in enumerate(ids):
        group_values[f"group_{group_id}_subjects"] = sizes[i]
        group_values[f"group_{group_id}_median_survival"] = median[i]
        group_values[f"group_{group_id}_final_survival"] = survival[i]
        group_values[f"group_{group_id}_greenwood_se"] = (
            survival[i] * math.sqrt(greenwood[i]) if survival[i] else 0.0)
    return {
        "summary": summary,
        "values": {**group_values, "median_survival_unit": unit,
                   "greenwood_se_unit": "1 (probability)",
                   "subjects": n, "groups": k, "events": int(sum(events)),
                   "subject_unit": "subjects", "event_unit": "events",
                   "logrank_chi_square": chi, "logrank_p_value": p,
                   "logrank_degrees_of_freedom": df,
                   "chi_square_unit": "1 (dimensionless)", "p_value_unit": "1 (probability)",
                   "survival_unit": "1 (probability)", "time_unit": unit},
        "table": {"columns": [f"Follow-up time ({unit})", "Group code", "At risk", "Events",
                              "Censored", "KM survival"], "rows": rows},
        "views": [{"type": "survival_curve", "title": "Multi-group Kaplan–Meier survival",
                   "xLabel": f"Follow-up time ({unit})", "groups": curves}],
    }


def _positive_solve(matrix, rhs):
    """Cholesky solve and inverse; fail closed on (nearly) singular information."""
    p = len(rhs)
    if not p:
        raise ValueError("A covariance matrix needs at least one dimension.")
    diagonal = max(matrix[i][i] for i in range(p))
    if diagonal <= 0 or not math.isfinite(diagonal):
        raise ValueError("The information matrix is singular.")
    floor = 1e-10 * diagonal
    lower = [[0.0]*p for _ in range(p)]
    for i in range(p):
        for j in range(i+1):
            residual = matrix[i][j] - math.fsum(lower[i][h]*lower[j][h] for h in range(j))
            if i == j:
                if residual <= floor:
                    raise ValueError("The information matrix is singular or rank deficient.")
                lower[i][j] = math.sqrt(residual)
            else:
                lower[i][j] = residual / lower[j][j]

    def solve(y):
        forward = [0.0]*p
        for i in range(p):
            forward[i] = (y[i] - math.fsum(lower[i][j]*forward[j] for j in range(i))) / lower[i][i]
        result = [0.0]*p
        for i in reversed(range(p)):
            result[i] = (forward[i] - math.fsum(lower[j][i]*result[j] for j in range(i+1,p))) / lower[i][i]
        return result
    result = solve(rhs)
    inverse = [solve([float(i==j) for i in range(p)]) for j in range(p)]
    # inverse[j][i] contains column j, so transpose to get rows.
    return result, [[inverse[j][i] for j in range(p)] for i in range(p)]


def _cox_statistics(beta, ordered, x, times, events, ties):
    """Log partial likelihood, score and observed information, descending risk sweep."""
    p = len(beta)
    n = len(times)
    eta = [math.fsum(b*v for b, v in zip(beta, row)) for row in x]
    if any(not math.isfinite(v) for v in eta):
        raise ValueError("Cox linear predictor overflows.")
    score, info = [0.0]*p, [[0.0]*p for _ in range(p)]
    risk1, risk2 = [0.0]*p, [[0.0]*p for _ in range(p)]
    risk0, shift, log_likelihood = 0.0, -math.inf, 0.0
    cursor = 0
    while cursor < n:
        stop = cursor + 1
        t = times[ordered[cursor]]
        while stop < n and times[ordered[stop]] == t:
            stop += 1
        at_time = ordered[cursor:stop]
        for i in at_time:
            if eta[i] > shift:
                factor = math.exp(shift - eta[i])
                risk0 *= factor
                risk1 = [v*factor for v in risk1]
                risk2 = [[v*factor for v in row] for row in risk2]
                shift = eta[i]
            w = math.exp(eta[i] - shift)
            risk0 += w
            for j in range(p):
                risk1[j] += w*x[i][j]
                for h in range(p):
                    risk2[j][h] += w*x[i][j]*x[i][h]
        dying = [i for i in at_time if events[i] == 1]
        d = len(dying)
        if d:
            event0 = math.fsum(math.exp(eta[i] - shift) for i in dying)
            event1 = [math.fsum(math.exp(eta[i] - shift)*x[i][j] for i in dying) for j in range(p)]
            event2 = [[math.fsum(math.exp(eta[i] - shift)*x[i][j]*x[i][h] for i in dying)
                       for h in range(p)] for j in range(p)]
            log_likelihood += math.fsum(eta[i] for i in dying)
            for j in range(p):
                score[j] += math.fsum(x[i][j] for i in dying)
            for tie_index in range(d if ties == "efron" else 1):
                fraction = tie_index/d if ties == "efron" else 0.0
                multiplicity = 1 if ties == "efron" else d
                denom = risk0 - fraction*event0
                if denom <= 0 or not math.isfinite(denom):
                    raise ValueError("Cox risk-set denominator is numerically degenerate.")
                log_likelihood -= multiplicity * (shift + math.log(denom))
                means = [(risk1[j] - fraction*event1[j])/denom for j in range(p)]
                for j in range(p):
                    score[j] -= multiplicity * means[j]
                    for h in range(p):
                        info[j][h] += multiplicity * (
                            (risk2[j][h] - fraction*event2[j][h])/denom - means[j]*means[h])
        cursor = stop
    return _number(log_likelihood, "Cox log likelihood"), score, info


