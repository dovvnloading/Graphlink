"""Biology and ecology: surveys, nonlinear fits, population and survival analysis.

The original three operations and their output contracts are deliberately kept
byte-for-byte compatible with saved scientific workbench results. All new work
uses Python's standard library (including the workbench's shared NLS solver).
"""

from __future__ import annotations

import json
import math
from collections import Counter

from graphlink_plugins.scientific.core import Citation, Field, Operation
from graphlink_plugins.scientific.packs.population_dynamics import OPERATIONS as _POPULATION_OPERATIONS
from graphlink_plugins.scientific.packs.ecology_diversity import OPERATIONS as _ECOLOGY_DIVERSITY_OPERATIONS


def _counts(values):
    if not values or any(v < 0 or int(v) != v or v > 2**53 - 1 for v in values):
        raise ValueError("Counts must be nonnegative whole numbers below 2^53 in a nonempty series.")
    return [int(v) for v in values]


def _total_count(counts):
    total = sum(counts)
    if total > 2**53 - 1:
        raise ValueError("Total individuals must be below 2^53 for exact export and graph reuse.")
    return total


def community_diversity(data):
    counts = _counts(data["counts"])
    total = _total_count(counts)
    if total == 0:
        raise ValueError("At least one individual must have been observed.")
    observed = [n for n in counts if n]
    richness = len(observed)
    # log1p also resolves the entropy contribution of a nearly dominant taxon.
    shannon = math.fsum((n / total) * math.log1p((total - n) / n) for n in observed)
    # One minus sum(p_i^2), using exact integer counts to avoid cancellation.
    simpson = sum(n * (total - n) for n in counts) / (total * total)
    return {
        "summary": f"{richness} observed taxa among {total} individuals. Shannon diversity is {shannon:.6g} nats.",
        "values": {"individuals": total, "richness": richness, "shannon_nats": shannon, "gini_simpson": simpson},
    }


def quadrat_density(data):
    counts = _counts(data["counts"])
    areas = data["area_m2"]
    if len(counts) != len(areas) or any(area <= 0 for area in areas):
        raise ValueError("Provide one positive surveyed area per quadrat count.")
    total_area = math.fsum(areas)
    total_count = _total_count(counts)
    density = total_count / total_area
    return {
        "summary": f"Pooled density is {density:.6g} individuals/m² over {total_area:.6g} m².",
        "values": {"pooled_density_per_m2": density, "individuals": total_count, "surveyed_area_m2": total_area},
        "table": {
            "columns": ["Quadrat", "Individuals", "Area (m²)", "Density (individuals/m²)"],
            "rows": [[i, n, area, n / area] for i, (n, area) in enumerate(zip(counts, areas), 1)],
        },
    }


def community_overlap(data):
    a = _counts(data["site_a_counts"])
    b = _counts(data["site_b_counts"])
    if len(a) != len(b):
        raise ValueError("Each site must report the same taxa in the same order.")
    shared = sum(x > 0 and y > 0 for x, y in zip(a, b))
    union = sum(x > 0 or y > 0 for x, y in zip(a, b))
    if union == 0:
        raise ValueError("Presence/absence similarity is undefined for two empty communities.")
    return {
        "summary": f"The sites share {shared} of {union} observed taxa.",
        "values": {
            "shared_taxa": shared,
            "union_taxa": union,
            "jaccard_similarity": shared / union,
            "sorensen_similarity": 2 * shared / (union + shared),
        },
    }


def _unit(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 40 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be a unit label of 1–40 characters.")
    return value.strip()


def _paired(x, y, minimum, label, *, positive_x=False, positive_y=False):
    if len(x) != len(y) or len(x) < minimum:
        raise ValueError(f"{label} needs at least {minimum} paired observations.")
    if any(v < 0 for v in x) or (positive_x and any(v <= 0 for v in x)):
        raise ValueError(f"{label} requires {'positive' if positive_x else 'nonnegative'} x values.")
    if positive_y and any(v <= 0 for v in y):
        raise ValueError(f"{label} requires strictly positive responses.")
    if len(set(x)) < minimum - 1:
        raise ValueError(f"{label} needs at least {minimum - 1} distinct x values.")
    return list(zip(x, y))


def _nls(expression, names, starts, bounds, x, y):
    """Reuse the scientific pack's Decimal LM + local covariance calculation.

    The binding is intentionally to the established numerical core, rather
    than importing an optional numerical package or maintaining a second LM.
    """
    from graphlink_plugins.scientific.packs.fitting import _original_nonlinear

    specification = json.dumps({
        "expression": expression, "parameter_names": names,
        "bounds": {name: list(bound) for name, bound in zip(names, bounds)},
    }, allow_nan=False)
    result = _original_nonlinear({
        "model": specification, "x": x, "y": y, "weights": [1] * len(x),
        "initial_parameters": starts,
    })
    return result, {name: result["values"][f"parameter_{i}"] for i, name in enumerate(names)}, {
        name: result["values"][f"standard_error_{i}"] for i, name in enumerate(names)
    }


def _multi_nls(expression, names, guesses, bounds, x, y):
    """Different plateau/rate guesses keep sigmoid fitting from a poor basin."""
    best = None
    errors = []
    for starts in guesses:
        try:
            fitted = _nls(expression, names, starts, bounds, x, y)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if best is None or fitted[0]["values"]["weighted_residual_sum_squares"] < best[0]["values"]["weighted_residual_sum_squares"]:
            best = fitted
    if best is None:
        raise ValueError("Nonlinear fit did not converge to identifiable parameters. "
                         "Include both plateaus / a wider range of x values. " + (errors[0] if errors else ""))
    return best


def _sigmoid(z):
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def _fit_diagnostics(x, y, predictions, fit):
    sse = fit["values"]["weighted_residual_sum_squares"]
    mean = math.fsum(y) / len(y)
    total = math.fsum((v - mean) ** 2 for v in y)
    metrics = {"observations": len(x), "residual_sum_squares": sse,
               "residual_standard_deviation": fit["values"]["residual_standard_deviation"],
               "degrees_of_freedom": fit["values"]["degrees_of_freedom"],
               "observations_unit": "observations", "degrees_of_freedom_unit": "1 (dimensionless)",
               "converged": fit["values"]["converged"]}
    if total > 0:
        metrics["r_squared"] = 1 - sse / total
    if any(not math.isfinite(v) for v in predictions):
        raise ValueError("Model predictions exceed the numeric range.")
    return metrics


def _growth(data, model):
    time_unit = _unit(data["time_unit"], "Time")
    quantity_unit = _unit(data["abundance_unit"], "Abundance")
    pairs = sorted(_paired(data["time"], data["abundance"], 5, "Growth fitting", positive_y=True))
    time, observed = [p[0] for p in pairs], [p[1] for p in pairs]
    if time[-1] <= time[0] or observed[-1] <= observed[0] or max(observed) == min(observed):
        raise ValueError("Growth fitting needs an increasing population across distinct sampling times.")
    relative = [t - time[0] for t in time]
    span = relative[-1]
    maximum = max(observed)
    rate = max(1e-6 / span, min(20 / span, math.log(observed[-1] / observed[0]) / span))
    if model == "logistic":
        formula = "K/(1+exp(-r*(x-mid)))"
        target = maximum * .5
    else:
        formula = "K*exp(-exp(-r*(x-mid)))"
        target = maximum / math.e
    initial_mid = relative[min(range(len(observed)), key=lambda i: abs(observed[i] - target))]
    candidates = [
        [maximum * factor, max(rate * rate_factor, 1e-8 / span), initial_mid]
        for factor, rate_factor in ((1.05, 1), (1.5, 1), (1.1, 2))
    ]
    fit, params, errors = _multi_nls(formula, ["K", "r", "mid"], candidates,
                                     [(1e-100, None), (1e-100, None), (None, None)], relative, observed)
    K, r, mid = params["K"], params["r"], params["mid"]
    if model == "logistic":
        predict = lambda t: K * _sigmoid(r * (t - mid))
        specific_rate = lambda n: r * (1 - n / K)
    else:
        def predict(t):
            z = -r * (t - mid)
            return K * math.exp(-math.exp(z)) if z < 700 else 0.0
        specific_rate = lambda n: r * math.log(K / n)
    fitted = [predict(t) for t in relative]
    initial = predict(0)
    if not 0 < initial < K or not 0 < r or not all(v > 0 for v in fitted):
        raise ValueError("Growth curve is not identifiable in the observed range.")
    doubling = "not reached (2 × initial abundance ≥ carrying capacity)"
    if 2 * initial < K:
        if model == "logistic":
            doubling = math.log(2 * (K - initial) / (K - 2 * initial)) / r
        else:
            doubling = math.log(math.log(K / initial) / math.log(K / (2 * initial))) / r
    values = {
        "model": model, "carrying_capacity": K, "carrying_capacity_unit": quantity_unit,
        "growth_rate_per_time_unit": r, "growth_rate_unit": f"1/{time_unit}",
        "growth_rate_standard_error": errors["r"], "growth_rate_standard_error_unit": f"1/{time_unit}",
        "carrying_capacity_standard_error": errors["K"], "carrying_capacity_standard_error_unit": quantity_unit,
        "inflection_time": time[0] + mid, "inflection_time_unit": time_unit,
        "initial_fitted_abundance": initial, "initial_fitted_abundance_unit": quantity_unit,
        "initial_specific_growth_rate_per_time_unit": specific_rate(initial),
        "initial_specific_growth_rate_unit": f"1/{time_unit}",
        "doubling_time_from_first_sample": doubling, "doubling_time_unit": time_unit,
        **_fit_diagnostics(time, observed, fitted, fit),
        "residual_standard_deviation_unit": quantity_unit,
        "residual_sum_squares_unit": f"({quantity_unit})²",
        "r_squared_unit": "1 (dimensionless)",
    }
    return {
        "summary": f"{model.title()} nonlinear least-squares fit: K={K:.6g} {quantity_unit}, "
                   f"r={r:.6g} 1/{time_unit}. Doubling time is the fitted time "
                   "from first-sample abundance to twice that abundance, when reachable; "
                   "r is the intrinsic rate (logistic) or decay rate of per-capita growth (Gompertz).",
        "values": values,
        "table": {"columns": [f"Time ({time_unit})", f"Observed ({quantity_unit})",
                              f"Fitted ({quantity_unit})", f"Residual ({quantity_unit})"],
                  "rows": [[t, v, z, v - z] for t, v, z in zip(time, observed, fitted)]},
        "plot": {"title": f"{model.title()} fitted growth curve", "xLabel": f"Time ({time_unit})",
                 "yLabel": f"Fitted abundance ({quantity_unit})", "x": time, "y": fitted},
    }


def logistic_growth_fit(data):
    return _growth(data, "logistic")


def gompertz_growth_fit(data):
    return _growth(data, "gompertz")


def _lineweaver_burk(x, y):
    valid = [(1 / s, 1 / v) for s, v in zip(x, y) if s > 0 and v > 0]
    if len(valid) < 3 or len({p[0] for p in valid}) < 3:
        return "unavailable", "unavailable"
    mean_x = math.fsum(t[0] for t in valid) / len(valid)
    mean_y = math.fsum(t[1] for t in valid) / len(valid)
    var = math.fsum((a - mean_x) ** 2 for a, b in valid)
    slope = math.fsum((a - mean_x) * (b - mean_y) for a, b in valid) / var
    intercept = mean_y - slope * mean_x
    if slope <= 0 or intercept <= 0 or not all(math.isfinite(v) for v in (slope, intercept)):
        return "unavailable", "unavailable"
    return 1 / intercept, slope / intercept


def michaelis_menten_fit(data):
    su = _unit(data["substrate_unit"], "Substrate concentration")
    vu = _unit(data["rate_unit"], "Initial rate")
    pairs = _paired(data["substrate"], data["initial_rate"], 4, "Enzyme kinetics")
    x, y = [p[0] for p in pairs], [p[1] for p in pairs]
    if max(y) <= 0:
        raise ValueError("At least one measured initial rate must be positive.")
    positives = sorted(v for v in x if v > 0)
    if not positives:
        raise ValueError("Supply positive substrate concentrations.")
    maximum = max(y)
    midpoint = positives[len(positives) // 2]
    fit, p, se = _multi_nls("Vmax*x/(Km+x)", ["Vmax", "Km"],
                             [[maximum * f, midpoint * g] for f, g in ((1.2, 1), (1.05, .25), (2, 2))],
                             [(1e-100, None), (1e-100, None)], x, y)
    vmax, km = p["Vmax"], p["Km"]
    preds = [vmax * v / (km + v) for v in x]
    lb_vmax, lb_km = _lineweaver_burk(x, y)
    return {
        "summary": f"Unweighted nonlinear least-squares Michaelis–Menten fit: Vmax={vmax:.6g} {vu}, "
                   f"Km={km:.6g} {su}; standard errors use local (JᵀJ)⁻¹ × SSE/(n−2). "
                   "Lineweaver–Burk is a reciprocal-plot comparison only, not the fitted estimate.",
        "values": {"vmax": vmax, "vmax_standard_error": se["Vmax"], "vmax_unit": vu,
                   "km": km, "km_standard_error": se["Km"], "km_unit": su,
                   "lineweaver_burk_vmax_comparison": lb_vmax,
                   "lineweaver_burk_vmax_unit": vu,
                   "lineweaver_burk_km_comparison": lb_km,
                   "lineweaver_burk_km_unit": su,
                   **_fit_diagnostics(x, y, preds, fit),
                   "residual_standard_deviation_unit": vu, "residual_sum_squares_unit": f"({vu})²",
                   "r_squared_unit": "1 (dimensionless)"},
        "table": {"columns": [f"[S] ({su})", f"Initial rate ({vu})", f"NLS fitted rate ({vu})",
                              f"Residual ({vu})"],
                  "rows": [[s, v, pred, v - pred] for s, v, pred in zip(x, y, preds)]},
        "plot": {"title": "Michaelis–Menten nonlinear fit", "xLabel": f"[S] ({su})",
                 "yLabel": f"Initial rate ({vu})", "x": sorted(x),
                 "y": [vmax * s / (km + s) for s in sorted(x)]},
    }


def dose_response_4pl_fit(data):
    xu = _unit(data["concentration_unit"], "Concentration")
    yu = _unit(data["response_unit"], "Response")
    mode = data["potency_metric"].strip().upper()
    if mode not in ("EC50", "IC50"):
        raise ValueError("Potency metric must be EC50 or IC50.")
    pairs = sorted(_paired(data["concentration"], data["response"], 6, "Dose-response fitting"))
    concentration, response = [p[0] for p in pairs], [p[1] for p in pairs]
    positive = sorted(set(c for c in concentration if c > 0))
    if len(positive) < 4 or max(response) == min(response):
        raise ValueError("4PL needs four distinct positive doses and a varying response.")
    lower_log, upper_log = math.log(positive[0]), math.log(positive[-1])
    # At a true zero dose the limiting response is exactly the low-dose
    # asymptote. A log-dose 500 natural-log units below the smallest measured
    # positive dose evaluates that limit to below floating-point precision
    # for Hill >= 0.1, without removing zero-dose controls from fitting.
    log_doses = [math.log(c) if c > 0 else lower_log - 500 for c in concentration]
    low_start = math.fsum(response[:min(3, len(response))]) / min(3, len(response))
    high_start = math.fsum(response[-min(3, len(response)):]) / min(3, len(response))
    initial_log = math.log(positive[len(positive) // 2])
    guesses = [[low_start, high_start, initial_log, h] for h in (1.0, 2.0, .5)]
    fit, p, se = _multi_nls("low+(high-low)/(1+exp(-hill*(x-log_mid)))",
                             ["low", "high", "log_mid", "hill"], guesses,
                             [(None, None), (None, None), (lower_log - 10, upper_log + 10), (.1, 20)],
                             log_doses, response)
    try:
        c50 = math.exp(p["log_mid"])
        def prediction(c):
            return p["low"] if c == 0 else p["low"] + (p["high"] - p["low"]) * _sigmoid(
                p["hill"] * (math.log(c) - p["log_mid"]))
        calculated = [prediction(c) for c in concentration]
        from graphlink_plugins.scientific.packs.fitting import _student_t_ppf
        critical = _student_t_ppf(.975, fit["values"]["degrees_of_freedom"])
        c50_lo = math.exp(p["log_mid"] - critical * se["log_mid"])
        c50_hi = math.exp(p["log_mid"] + critical * se["log_mid"])
    except OverflowError:
        raise ValueError("Dose-response parameters or confidence interval overflowed.") from None
    return {
        "summary": f"Four-parameter logistic nonlinear least-squares fit ({mode}): {c50:.6g} {xu}, "
                   f"Hill slope {p['hill']:.5g} (dimensionless). Low/high are low-dose/high-dose "
                   "response plateaus; positive Hill allows either stimulation or inhibition.",
        "values": {"potency_metric": mode, "potency_concentration": c50, "potency_unit": xu,
                   "potency_standard_error": c50 * se["log_mid"],
                   "potency_ci95_lower": c50_lo, "potency_ci95_upper": c50_hi,
                   "hill_slope": p["hill"], "hill_slope_standard_error": se["hill"],
                   "hill_slope_unit": "1 (dimensionless)",
                   "low_dose_response": p["low"], "low_dose_response_standard_error": se["low"],
                   "high_dose_response": p["high"], "high_dose_response_standard_error": se["high"],
                   "plateau_unit": yu, "log_mid": p["log_mid"], "log_mid_unit": f"ln(concentration / 1 {xu})",
                   **_fit_diagnostics(concentration, response, calculated, fit),
                   "residual_standard_deviation_unit": yu, "residual_sum_squares_unit": f"({yu})²",
                   "r_squared_unit": "1 (dimensionless)"},
        "table": {"columns": [f"Concentration ({xu})", f"Response ({yu})",
                              f"4PL fitted ({yu})", f"Residual ({yu})"],
                  "rows": [[c, y, z, y - z] for c, y, z in zip(concentration, response, calculated)]},
        "plot": {"title": f"4PL fitted {mode} curve", "xLabel": f"Concentration ({xu})",
                 "yLabel": f"Response ({yu})", "x": concentration, "y": calculated},
    }


def lotka_volterra(data):
    tu = _unit(data["time_unit"], "Time")
    prey, predator = data["initial_prey"], data["initial_predators"]
    a, b, d, g = data["prey_growth_rate"], data["predation_rate"], data["conversion_rate"], data["predator_death_rate"]
    dt, steps = data["step_size"], data["steps"]
    if prey <= 0 or predator <= 0 or min(a, b, d, g, dt) <= 0 or not 1 <= steps <= 1000:
        raise ValueError("Initial populations and coefficients must be positive; step size > 0 and steps 1–1000.")

    def deriv(x, y):
        return (a * x - b * x * y, d * x * y - g * y)

    def advance(x, y, h):
        p1, q1 = deriv(x, y)
        p2, q2 = deriv(x + h * p1 / 2, y + h * q1 / 2)
        p3, q3 = deriv(x + h * p2 / 2, y + h * q2 / 2)
        p4, q4 = deriv(x + h * p3, y + h * q3)
        new = (x + h * (p1 + 2 * p2 + 2 * p3 + p4) / 6,
               y + h * (q1 + 2 * q2 + 2 * q3 + q4) / 6)
        if not all(math.isfinite(v) and v >= 0 for v in new):
            raise ValueError("Numerically unstable predator–prey trajectory. Reduce the time step or rates.")
        return new

    rows = [[0.0, prey, predator]]
    for i in range(1, steps + 1):
        for _ in range(8):  # Eight RK4 substeps per reported interval.
            prey, predator = advance(prey, predator, dt / 8)
        rows.append([i * dt, prey, predator])
    return {
        "summary": f"Lotka–Volterra predator–prey phase trajectory using RK4 (8 substeps per {dt:g} {tu}). "
                   "No environmental stochasticity, carrying capacity or demographic noise.",
        "values": {"initial_prey": data["initial_prey"], "initial_predators": data["initial_predators"],
                   "final_prey": prey, "final_predators": predator,
                   "population_unit": "individuals", "duration": steps * dt, "time_unit": tu,
                   "prey_growth_rate": a, "prey_growth_rate_unit": f"1/{tu}",
                   "predation_rate": b, "predation_rate_unit": f"1/(predator·{tu})",
                   "conversion_rate": d, "conversion_rate_unit": f"1/(prey·{tu})",
                   "predator_death_rate": g, "predator_death_rate_unit": f"1/{tu}"},
        "table": {"columns": [f"Time ({tu})", "Prey (individuals)", "Predators (individuals)"], "rows": rows},
        "plot": {"title": "Lotka–Volterra phase portrait", "xLabel": "Prey (individuals)",
                 "yLabel": "Predators (individuals)", "x": [r[1] for r in rows], "y": [r[2] for r in rows]},
    }


def hardy_weinberg(data):
    obs = [data["homozygote_aa"], data["heterozygote_ab"], data["homozygote_bb"]]
    if any(v < 0 for v in obs) or (n := sum(obs)) < 1:
        raise ValueError("HWE needs nonnegative genotype counts and at least one diploid individual.")
    p = (2 * obs[0] + obs[1]) / (2 * n)
    q = 1 - p
    exp = [n * p * p, n * 2 * p * q, n * q * q]
    if p in (0, 1):
        chi, pv, test = "undefined (monomorphic locus)", "undefined (monomorphic locus)", "not testable"
        contributions = [0.0, 0.0, 0.0]
    else:
        contributions = [(a - e) ** 2 / e for a, e in zip(obs, exp)]
        chi = math.fsum(contributions)
        pv = math.erfc(math.sqrt(chi / 2))  # df = 3 - 1 sum constraint - 1 fitted p
        test = "Pearson chi-square; df = 1 (p estimated)"
    warning = "Expected genotype count < 5; asymptotic chi-square may be unreliable." if min(exp) < 5 else "none"
    return {
        "summary": f"Hardy–Weinberg equilibrium at a biallelic diploid locus: p={p:.6g}, q={q:.6g}; "
                   f"{test}. {warning}",
        "values": {"individuals": n, "individuals_unit": "individuals",
                   "allele_a_frequency": p, "allele_b_frequency": q,
                   "allele_frequency_unit": "1 (dimensionless)",
                   "chi_square": chi, "chi_square_unit": "1 (dimensionless)",
                   "degrees_of_freedom": 1 if p not in (0, 1) else 0,
                   "p_value": pv, "p_value_unit": "probability (dimensionless)",
                   "expected_count_below_5": sum(e < 5 for e in exp),
                   "expected_count_below_5_unit": "genotypes",
                   "degrees_of_freedom_unit": "1 (dimensionless)", "warning": warning},
        "table": {"columns": ["Genotype (categorical)", "Observed (individuals)", "Expected (individuals)",
                              "χ² contribution (dimensionless)"],
                  "rows": [[label, actual, expected, part] for label, actual, expected, part in
                           zip(("AA", "AB", "BB"), obs, exp, contributions)]},
        "plot": {"title": "HWE expected genotype counts", "xLabel": "Genotype code (0=AA,1=AB,2=BB)",
                 "yLabel": "Expected (individuals)", "x": [0, 1, 2], "y": exp},
    }


def kaplan_meier_logrank(data):
    tu = _unit(data["time_unit"], "Survival time")
    groups = []
    for label, time_key, event_key in (("A", "time_a", "event_a"), ("B", "time_b", "event_b")):
        ts, es = data[time_key], data[event_key]
        if not ts or len(ts) != len(es) or any(t < 0 for t in ts) or any(e not in (0, 1) for e in es):
            raise ValueError(f"Group {label} needs nonnegative times and matching 0=censored, 1=event indicators.")
        groups.append((Counter((t, int(e)) for t, e in zip(ts, es)), len(ts)))
    times = sorted({t for records, _ in groups for t, _ in records})
    if len(times) > 400:
        raise ValueError("For a readable two-curve workbench result, use at most 400 distinct follow-up times.")
    risk = [groups[0][1], groups[1][1]]
    survival = [1.0, 1.0]
    greenwood = [0.0, 0.0]
    median = ["not reached", "not reached"]
    obs_a = expected_a = variance = 0.0
    rows, xs, ys = [], [0.0], [1.0]
    curve_times = [[0.0], [0.0]]
    curve_survival = [[1.0], [1.0]]
    censor_times = [[], []]
    for t in times:
        deaths = [groups[i][0][(t, 1)] for i in (0, 1)]
        censored = [groups[i][0][(t, 0)] for i in (0, 1)]
        total, events = sum(risk), sum(deaths)
        if events:
            obs_a += deaths[0]
            expected_a += events * risk[0] / total
            if total > 1:
                variance += risk[0] * risk[1] * events * (total - events) / (total**2 * (total - 1))
        for i in (0, 1):
            if deaths[i]:
                old_risk = risk[i]
                survival[i] *= 1 - deaths[i] / old_risk
                if deaths[i] < old_risk:
                    greenwood[i] += deaths[i] / (old_risk * (old_risk - deaths[i]))
                if median[i] == "not reached" and survival[i] <= .5:
                    median[i] = t
            if deaths[i] or censored[i]:
                curve_times[i].append(t)
                curve_survival[i].append(survival[i])
            if censored[i]:
                censor_times[i].append(t)
        rows.append([t, risk[0], deaths[0], censored[0], survival[0],
                     risk[1], deaths[1], censored[1], survival[1]])
        xs.append(t)
        ys.append(survival[0])
        risk = [risk[i] - deaths[i] - censored[i] for i in (0, 1)]
    if variance > 0:
        chi = (obs_a - expected_a) ** 2 / variance
        pv = math.erfc(math.sqrt(chi / 2))
    else:
        chi, pv = "undefined (zero log-rank variance)", "undefined (zero log-rank variance)"
    return {
        "summary": "Two Kaplan–Meier product-limit survival curves (events before censoring at tied times); "
                   "two-sided Mantel–Cox log-rank chi-square, 1 df with hypergeometric tie variance. "
                   "Both curves appear in the survival view and table; the legacy plot shows A.",
        "values": {"group_a_size": groups[0][1], "group_b_size": groups[1][1],
                   "group_size_unit": "subjects", "observed_events_a": obs_a,
                   "expected_events_a": expected_a, "event_count_unit": "events",
                   "logrank_variance": variance, "logrank_variance_unit": "events²",
                   "logrank_chi_square": chi, "logrank_chi_square_unit": "1 (dimensionless)",
                   "logrank_degrees_of_freedom": 1 if variance > 0 else 0,
                   "logrank_degrees_of_freedom_unit": "1 (dimensionless)",
                   "logrank_p_value": pv, "logrank_p_value_unit": "probability (dimensionless)",
                   "median_survival_a": median[0], "median_survival_b": median[1],
                   "median_survival_unit": tu, "final_survival_a": survival[0],
                   "final_survival_b": survival[1], "survival_probability_unit": "probability (dimensionless)",
                   "greenwood_se_a": (survival[0] * math.sqrt(greenwood[0]) if survival[0] else 0.0),
                   "greenwood_se_b": (survival[1] * math.sqrt(greenwood[1]) if survival[1] else 0.0),
                   "greenwood_se_unit": "probability (dimensionless)"},
        "table": {"columns": [f"Time ({tu})", "A at risk (subjects)", "A events (events)",
                              "A censored (subjects)", "A survival (probability)",
                              "B at risk (subjects)", "B events (events)",
                              "B censored (subjects)", "B survival (probability)"], "rows": rows},
        "plot": {"title": "Kaplan–Meier curve A (compare B in table)", "xLabel": f"Time ({tu})",
                 "yLabel": "Survival (probability)", "x": xs, "y": ys},
        "views": [{
            "type": "survival_curve", "title": "Kaplan–Meier survival by group",
            "xLabel": f"Follow-up time ({tu})",
            "groups": [
                {"label": label, "times": curve_times[i], "survival": curve_survival[i],
                 "censored": censor_times[i]}
                for i, label in enumerate(("A", "B"))
            ],
        }],
    }


def _rarefied(counts, n):
    """Hurlbert exact E[S_n], using stable hypergeometric survival products."""
    N = sum(counts)
    if n == N:
        return sum(c > 0 for c in counts)
    total = 0.0
    for count in counts:
        if count == 0:
            continue
        if count > N - n:
            total += 1
            continue
        k = min(count, n)
        # The absent probability is the shorter product
        # prod_j(1 - max(count,n)/(N-j)). Subtracting lgamma values near
        # 2**53 erases small but scientifically resolvable contributions.
        if count * n > 40 * N:
            total += 1.0  # probability of absence < exp(-40)
            continue
        removed = max(count, n)
        if k <= 8192:
            log_absent = math.fsum(math.log1p(-removed / (N - j)) for j in range(k))
        else:
            # Midpoint expansion of the same log product. Small expected
            # occupancy implies removed/N <= 40/k, so the curvature term
            # controls the approximation even for huge survey counts.
            mid = (k - 1) / 2
            remaining = N - mid
            log_absent = (k * math.log1p(-removed / remaining)
                          - removed * (2 * remaining - removed) * k * (k*k - 1)
                          / (24 * remaining**2 * (remaining - removed)**2))
        total -= math.expm1(min(0.0, log_absent))
    return total


def richness_rarefaction(data):
    counts = _counts(data["counts"])
    N = _total_count(counts)
    m = data["subsample_individuals"]
    if N == 0 or not 1 <= m <= N:
        raise ValueError("Rarefaction requires individuals and a subsample size in [1, total individuals].")
    observed = sum(c > 0 for c in counts)
    singletons = sum(c == 1 for c in counts)
    doubletons = sum(c == 2 for c in counts)
    if doubletons:
        chao1 = observed + singletons**2 / (2 * doubletons)
        method = "classic Chao1, f1²/(2 f2)"
    else:
        chao1 = observed + singletons * (singletons - 1) / 2
        method = "Chao1 f2=0 bias-corrected fallback"
    grid = sorted({1, m, N, *[max(1, round(1 + j * (N - 1) / 24)) for j in range(25)]})
    rows = [[n, _rarefied(counts, n)] for n in grid]
    requested = next(y for x, y in rows if x == m)
    return {
        "summary": f"{observed} observed taxa from {N} individuals; Chao1 = {chao1:.6g} taxa "
                   f"({method}); without-replacement rarefaction at {m} individuals "
                   f"expects {requested:.6g} taxa.",
        "values": {"observed_richness": observed, "chao1_estimated_richness": chao1,
                   "richness_unit": "taxa", "singleton_taxa": singletons, "doubleton_taxa": doubletons,
                   "singleton_doubleton_unit": "taxa", "individuals": N, "individuals_unit": "individuals",
                   "subsample_individuals": m, "subsample_unit": "individuals",
                   "rarefied_expected_richness": requested, "rarefied_expected_richness_unit": "taxa",
                   "chao1_method": method},
        "table": {"columns": ["Subsample (individuals)", "Expected richness (taxa)"], "rows": rows},
        "plot": {"title": "Individual-based rarefaction (without replacement)",
                 "xLabel": "Subsample size (individuals)", "yLabel": "Expected richness (taxa)",
                 "x": [row[0] for row in rows], "y": [row[1] for row in rows]},
    }


# The original Operation entries, ids, fields, examples and functions are
# unchanged, preserving existing saved results and graph source bindings.
OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "community_diversity",
        "Community diversity",
        "Calculate observed richness, Shannon entropy (natural logarithm), and Gini–Simpson "
        "diversity (1 − Σp²) from counts by taxon. Zero-count taxa contribute no weight.",
        (Field("counts", "Individuals observed per taxon", "vector", unit="individuals"),),
        {"counts": [12, 6, 2, 0]},
        community_diversity,
        methods=(
            "Count observed taxa with nᵢ > 0. From pᵢ = nᵢ/N for nonzero taxa, calculate Shannon "
            "H = −Σpᵢ ln(pᵢ) (nats) and the Gini–Simpson index 1−Σpᵢ². This uses empirical "
            "population proportions, without a finite-sample correction."
        ),
        citations=(
            Citation(
                "Shannon (1948), A Mathematical Theory of Communication, Bell System Technical Journal 27:379–423",
                "https://doi.org/10.1002/j.1538-7305.1948.tb01338.x",
            ),
            Citation(
                "Simpson (1949), Measurement of Diversity, Nature 163:688",
                "https://doi.org/10.1038/163688a0",
            ),
        ),
        headline="gini_simpson",
    ),
    Operation(
        "quadrat_density",
        "Quadrat survey density",
        "Calculate per-quadrat and pooled density for non-overlapping survey quadrats. "
        "Pooled density divides the total count by the total surveyed area.",
        (
            Field("counts", "Counts by quadrat", "vector", unit="individuals"),
            Field("area_m2", "Areas in matching quadrat order", "vector", unit="m²"),
        ),
        {"counts": [4, 9, 5], "area_m2": [1, 2, 1]},
        quadrat_density,
        methods=(
            "For nonoverlapping quadrats with matched counts nᵢ and positive areas Aᵢ, compute "
            "individual densities nᵢ/Aᵢ and pooled density Σnᵢ/ΣAᵢ (individuals/m²). Unequal-area "
            "quadrats are weighted by sampled area, not averaged equally; habitat-wide "
            "extrapolation needs representative sampling."
        ),
        citations=(
            Citation(
                "OpenStax Biology 2e, 45.1 Population Demography — population density and quadrat sampling",
                "https://openstax.org/books/biology-2e/pages/45-1-population-demography",
            ),
        ),
        headline="pooled_density_per_m2",
        paired_fields=(("counts", "area_m2"),),
    ),
    Operation(
        "community_overlap",
        "Community overlap",
        "Calculate Jaccard and Sørensen presence/absence similarity for surveys using the same "
        "taxon order. Any positive count indicates presence.",
        (
            Field("site_a_counts", "Site A counts by taxon", "vector", unit="individuals"),
            Field("site_b_counts", "Site B counts in the same order", "vector", unit="individuals"),
        ),
        {"site_a_counts": [3, 2, 0, 0], "site_b_counts": [1, 0, 4, 0]},
        community_overlap,
        methods=(
            "Turn positive counts into species presence at each site, using the same taxon "
            "ordering. Let a be taxa shared and u be taxa present at either site; Jaccard = a/u "
            "and Sørensen = 2a/(u+a). Joint absences do not contribute; two empty sites yield "
            "undefined similarity."
        ),
        citations=(
            Citation(
                "An improved asymptotic test for the Jaccard similarity index for binary data (2022) — set-overlap definition",
                "https://www.sciencedirect.com/science/article/pii/S0167715222000074",
            ),
            Citation(
                "Sørensen (1948), A method of establishing groups of equal amplitude in plant sociology, Biologiske Skrifter 5:1–34",
                "https://refs.drheap.org/sorensen1948method/",
            ),
        ),
        headline="jaccard_similarity",
        paired_fields=(("site_a_counts", "site_b_counts"),),
    ),
    Operation(
        "logistic_growth_fit", "Fit logistic population growth",
        "Nonlinear 3-parameter logistic growth, K and r, observed/fitted residuals and first-sample doubling time.",
        (Field("time", "Sampling times", "vector", unit="selected time unit"),
         Field("abundance", "Observed abundances", "vector", unit="selected abundance unit"),
         Field("time_unit", "Time unit (e.g. days)", "text", unit="unit label"),
         Field("abundance_unit", "Abundance unit (e.g. cells/mL)", "text", unit="unit label")),
        {"time": [0, 1, 2, 3, 4, 5, 6, 7, 8],
         "abundance": [10.1, 15.9, 25.0, 37.7, 53.4, 67.7, 79.3, 87.6, 92.8],
         "time_unit": "days", "abundance_unit": "cells/mL"}, logistic_growth_fit,
        methods=(
            "Fit N(t) = K/[1+exp(−r(t−t_mid))] with unweighted multi-start Levenberg–Marquardt "
            "nonlinear least squares (K,r > 0). Report fitted residuals, inflection time and "
            "local linearized standard errors. Doubling time starts at fitted abundance at the "
            "first sample and is defined only when twice that abundance is below K."
        ),
        citations=(
            Citation(
                "GraphPad Prism Curve Fitting Guide, Logistic growth — model and parameter interpretation",
                "https://www.graphpad.com/guides/prism/latest/curve-fitting/reg_logistic-growth.htm",
            ),
        ),
        headline="carrying_capacity",
        paired_fields=(("time", "abundance"),),
    ),
    Operation(
        "gompertz_growth_fit", "Fit Gompertz population growth",
        "Nonlinear 3-parameter Gompertz growth, K and rate, and first-sample doubling time.",
        (Field("time", "Sampling times", "vector", unit="selected time unit"),
         Field("abundance", "Observed abundances", "vector", unit="selected abundance unit"),
         Field("time_unit", "Time unit (e.g. days)", "text", unit="unit label"),
         Field("abundance_unit", "Abundance unit (e.g. cells/mL)", "text", unit="unit label")),
        {"time": [0, 1, 2, 3, 4, 5, 6, 7, 8],
         "abundance": [2.4, 8.2, 18.3, 33.1, 50.1, 65.7, 77.5, 86.1, 91.5],
         "time_unit": "days", "abundance_unit": "cells/mL"}, gompertz_growth_fit,
        methods=(
            "Fit N(t) = K exp[−exp(−r(t−t_mid))] by unweighted multi-start Levenberg–Marquardt "
            "nonlinear least squares (K,r > 0). Here r controls the exponential decline of "
            "specific growth rate, not a constant per-capita rate. Return local linearized "
            "standard errors and first-sample doubling time only when 2N₀ < K."
        ),
        citations=(
            Citation(
                "GraphPad Prism Curve Fitting Guide, Gompertz growth — model and parameter interpretation",
                "https://www.graphpad.com/guides/prism/latest/curve-fitting/reg_gompertz-growth.htm",
            ),
        ),
        headline="carrying_capacity",
        paired_fields=(("time", "abundance"),),
    ),
    Operation(
        "michaelis_menten_fit", "Fit Michaelis–Menten enzyme kinetics",
        "Nonlinear least squares Vmax and Km with local standard errors; reciprocal Lineweaver–Burk comparison only.",
        (Field("substrate", "Substrate concentrations [S]", "vector", unit="selected concentration unit"),
         Field("initial_rate", "Initial reaction rates", "vector", unit="selected rate unit"),
         Field("substrate_unit", "Concentration unit (e.g. mM)", "text", unit="unit label"),
         Field("rate_unit", "Rate unit (e.g. mM/s)", "text", unit="unit label")),
        {"substrate": [0, 1, 2, 5, 8, 12, 30, 50],
         "initial_rate": [0, 11.1, 25.4, 44.8, 54.5, 58.2, 72, 60.1],
         "substrate_unit": "mM", "rate_unit": "mM/s"}, michaelis_menten_fit,
        methods=(
            "Fit v = Vmax[S]/(Km+[S]) for Vmax,Km > 0 by unweighted, multi-start nonlinear least "
            "squares. Parameter standard errors use local SSE/(n−2) × (JᵀJ)⁻¹ covariance. A "
            "separate Lineweaver–Burk reciprocal regression is provided for comparison only; it "
            "does not determine Vmax, Km or their uncertainties."
        ),
        citations=(
            Citation(
                "Michaelis & Menten (1913), translated and annotated by Johnson & Goody (2011), Biochemistry 50:8264–8269",
                "https://doi.org/10.1021/bi201284u",
            ),
        ),
        headline="km",
        paired_fields=(("substrate", "initial_rate"),),
    ),
    Operation(
        "dose_response_4pl_fit", "Fit dose–response (4PL)",
        "Fit low/high plateaus, EC50 or IC50, and Hill slope by nonlinear least squares; zero-dose controls supported.",
        (Field("concentration", "Dose concentrations", "vector", unit="selected concentration unit"),
         Field("response", "Measured responses", "vector", unit="selected response unit"),
         Field("concentration_unit", "Dose unit (e.g. nM)", "text", unit="unit label"),
         Field("response_unit", "Response unit (e.g. % activity)", "text", unit="unit label"),
         Field("potency_metric", "Potency label: EC50 or IC50", "choice",
               unit="category (dimensionless)", choices=("EC50", "IC50"))),
        {"concentration": [0, 0.1, 0.3, 1, 3, 10, 30, 100, 300],
         "response": [1, 2, 5, 18, 42, 75, 92, 97, 99],
         "concentration_unit": "nM", "response_unit": "% activity", "potency_metric": "EC50"},
        dose_response_4pl_fit,
        methods=(
            "Use unweighted multi-start nonlinear least squares for y = "
            "low+(high−low)/[1+exp(−h(ln(c)−ln(c50)))]. The positive Hill slope h is bounded "
            "0.1–20; plateau ordering permits both ascending and descending responses. Zero-dose "
            "controls approach the low-dose limit. Report relative EC50/IC50; its 95% interval is "
            "a transformed Student-t interval on ln(c50) and its SE uses the delta method."
        ),
        citations=(
            Citation(
                "Sebaugh (2011), Guidelines for accurate EC50/IC50 estimation, Pharmaceutical Statistics 10:128–134",
                "https://doi.org/10.1002/pst.426",
            ),
        ),
        headline="potency_concentration",
        paired_fields=(("concentration", "response"),),
    ),
    Operation(
        "lotka_volterra", "Simulate Lotka–Volterra predator–prey",
        "Fourth-order Runge–Kutta deterministic predator–prey system with labeled rates and phase portrait.",
        (Field("initial_prey", "Prey initially", "number", unit="individuals"),
         Field("initial_predators", "Predators initially", "number", unit="individuals"),
         Field("prey_growth_rate", "Prey intrinsic growth α", "number", unit="1 / selected time unit"),
         Field("predation_rate", "Predation β", "number", unit="1 / (predator × time unit)"),
         Field("conversion_rate", "Predator conversion δ", "number", unit="1 / (prey × time unit)"),
         Field("predator_death_rate", "Predator mortality γ", "number", unit="1 / selected time unit"),
         Field("step_size", "Output time step", "number", unit="selected time unit"),
         Field("steps", "Number of output steps", "integer", unit="steps (dimensionless)"),
         Field("time_unit", "Time unit (e.g. days)", "text", unit="unit label")),
        {"initial_prey": 40, "initial_predators": 9, "prey_growth_rate": .5,
         "predation_rate": .025, "conversion_rate": .01, "predator_death_rate": .4,
         "step_size": .1, "steps": 100, "time_unit": "days"}, lotka_volterra,
        methods=(
            "Integrate dX/dt = αX−βXY and dY/dt = δXY−γY from positive initial populations and "
            "rates using classical fourth-order Runge–Kutta with eight internal substeps per "
            "reported time interval. This deterministic predator–prey model omits carrying "
            "capacity and demographic/environmental stochasticity; negative or nonfinite "
            "trajectories are rejected."
        ),
        citations=(
            Citation(
                "Wolfram MathWorld, Lotka–Volterra Equations — model and equilibria",
                "https://mathworld.wolfram.com/Lotka-VolterraEquations.html",
            ),
            Citation(
                "Kingsland (2015), Alfred J. Lotka and the origins of theoretical population ecology, PNAS",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC4534218/",
            ),
        ),
        headline="final_prey",
    ),
    Operation(
        "hardy_weinberg", "Hardy–Weinberg equilibrium and chi-square",
        "Allele frequencies from observed AA/AB/BB genotypes; Pearson χ² 1 df after estimating p.",
        (Field("homozygote_aa", "AA homozygotes", "integer", unit="individuals"),
         Field("heterozygote_ab", "AB heterozygotes", "integer", unit="individuals"),
         Field("homozygote_bb", "BB homozygotes", "integer", unit="individuals")),
        {"homozygote_aa": 30, "heterozygote_ab": 60, "homozygote_bb": 10}, hardy_weinberg,
        methods=(
            "Estimate allele frequency p = (2AA+AB)/(2N), q = 1−p; the Hardy–Weinberg expected "
            "genotype counts are Np², 2Npq and Nq². Compute Pearson χ² with one degree of freedom "
            "after fitting p, and its asymptotic upper-tail p-value. Warn on expected genotype "
            "counts below five and avoid χ² inference at a monomorphic locus."
        ),
        citations=(
            Citation(
                "Hardy (1908), Mendelian Proportions in a Mixed Population, Science 28:49–50",
                "https://doi.org/10.1126/science.28.706.49",
            ),
            Citation(
                "Pearson (1900), On the criterion that a given system of deviations..., Philosophical Magazine 50:157–175",
                "https://doi.org/10.1080/14786440009463897",
            ),
        ),
        headline="p_value",
    ),
    Operation(
        "kaplan_meier_logrank", "Kaplan–Meier and log-rank (two groups)",
        "Two survival curves with risk sets, censoring, Greenwood uncertainty, median and tie-aware log-rank test.",
        (Field("time_a", "Group A follow-up times", "vector", unit="selected time unit"),
         Field("event_a", "A event status (1 event, 0 censored)", "vector", unit="0/1 (dimensionless)"),
         Field("time_b", "Group B follow-up times", "vector", unit="selected time unit"),
         Field("event_b", "B event status (1 event, 0 censored)", "vector", unit="0/1 (dimensionless)"),
         Field("time_unit", "Follow-up unit (e.g. days)", "text", unit="unit label")),
        {"time_a": [2, 4, 5, 7, 9], "event_a": [1, 1, 0, 1, 1],
         "time_b": [6, 8, 11, 13, 15], "event_b": [1, 0, 1, 1, 0], "time_unit": "days"},
        kaplan_meier_logrank,
        methods=(
            "Calculate two right-censored Kaplan–Meier product-limit step curves, processing "
            "events before censoring at tied times; Greenwood's variance gives final survival SEs "
            "and medians are first crossings of 0.5. Use observed-minus-expected events with "
            "hypergeometric tie variance for a Mantel–Cox log-rank χ² (one degree of freedom when "
            "variance > 0). Both curves appear in the typed survival view and table; "
            "the legacy single-series plot displays group A."
        ),
        citations=(
            Citation(
                "Kaplan & Meier (1958), Nonparametric Estimation from Incomplete Observations, JASA 53:457–481",
                "https://doi.org/10.1080/01621459.1958.10501452",
            ),
            Citation(
                "Mantel (1966), Evaluation of Survival Data and Two New Rank Order Statistics, Cancer Chemotherapy Reports 50:163–170",
                "https://pubmed.ncbi.nlm.nih.gov/5910392/",
            ),
        ),
        headline="logrank_p_value",
        paired_fields=(("time_a", "event_a"), ("time_b", "event_b")),
    ),
    Operation(
        "richness_rarefaction", "Species richness: Chao1 and rarefaction",
        "Abundance Chao1 richness and exact Hurlbert individual-based rarefaction without replacement.",
        (Field("counts", "Abundances in taxon order", "vector", unit="individuals per taxon"),
         Field("subsample_individuals", "Rarefaction subsample size", "integer", unit="individuals")),
        {"counts": [5, 3, 2], "subsample_individuals": 5}, richness_rarefaction,
        methods=(
            "Calculate Chao1 = Sobs+f₁²/(2f₂), using the bias-corrected Sobs+f₁(f₁−1)/2 when f₂ = "
            "0. Expected richness at sample size m ≤ N is the individual-based "
            "without-replacement Hurlbert sum Σᵢ[1−C(N−nᵢ,m)/C(N,m)]. Use stable log-absence "
            "probabilities (and a long-product approximation); rarefaction is an expectation, not "
            "extrapolation or a confidence interval."
        ),
        citations=(
            Citation(
                "Chao (1984), Nonparametric Estimation of the Number of Classes in a Population, Scandinavian Journal of Statistics 11:265–270",
                "https://www.jstor.org/stable/4615964",
            ),
            Citation(
                "Hurlbert (1971), The Nonconcept of Species Diversity, Ecology 52:577–586",
                "https://doi.org/10.2307/1934145",
            ),
            Citation(
                "spacc (CRAN), Richness Estimation and Completeness — Chao1 conventions",
                "https://cran.rstudio.org/web/packages/spacc/vignettes/richness-estimation.html",
            ),
        ),
        headline="chao1_estimated_richness",
    ),
)

# Extend without modifying the three original operation contracts or existing IDs.
OPERATIONS += _POPULATION_OPERATIONS
OPERATIONS += _ECOLOGY_DIVERSITY_OPERATIONS
