"""Classical inference for the Scientific Workbench; Python standard library only.

Exact permutation distributions are used for small rank samples. Approximation
thresholds, tie handling, confidence intervals, and model assumptions are
specified in docs/SCIENTIFIC_FITTING_REFERENCE.md.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter
from decimal import Decimal, localcontext
from functools import lru_cache

from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.packs.fitting import _beta_i, _student_t_cdf

_NORMAL = statistics.NormalDist()
_Z95 = _NORMAL.inv_cdf(0.975)
_RANK_EXACT_LIMIT = 30
_SPEARMAN_EXACT_LIMIT = 8


def _probability(p):
    return max(0.0, min(1.0, p))


def _rank(values):
    """Midranks and tie multiplicities, including singleton groups."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    ties = []
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[start]] == values[order[stop]]:
            stop += 1
        mid = (start + 1 + stop) / 2
        for j in range(start, stop):
            ranks[order[j]] = mid
        ties.append(stop - start)
        start = stop
    return ranks, ties


def _conditional_rank_distribution(ranks, choose):
    """Exact distribution of rank sums by selection without replacement.

    Twice the midranks are integers, so ties remain exact. Every selection of
    labeled observations is equally likely under the permutation null.
    """
    ways = [Counter() for _ in range(choose + 1)]
    ways[0][0] = 1
    for position, rank in enumerate(ranks):
        doubled = int(round(2 * rank))
        for k in range(min(position + 1, choose), 0, -1):
            for total, count in list(ways[k - 1].items()):
                ways[k][total + doubled] += count
    return ways[choose]


def _two_sided_exact(distribution, observed):
    denominator = sum(distribution.values())
    left = sum(count for value, count in distribution.items() if value <= observed) / denominator
    right = sum(count for value, count in distribution.items() if value >= observed) / denominator
    return _probability(2 * min(left, right))


def _normal_two_sided(statistic, mean, variance, *, continuity=0.5):
    if variance <= 0:
        raise ValueError("The rank distribution is degenerate (zero null variance).")
    z = max(0.0, abs(statistic - mean) - continuity) / math.sqrt(variance)
    return _probability(math.erfc(z / math.sqrt(2))), z


def _mann_whitney(data):
    a, b = data["sample_a"], data["sample_b"]
    if min(len(a), len(b)) < 2:
        raise ValueError("Mann-Whitney U requires at least two observations in each independent sample.")
    n1, n2 = len(a), len(b)
    ranks, ties = _rank(a + b)
    u1 = math.fsum(ranks[:n1]) - n1 * (n1 + 1) / 2
    u2 = n1 * n2 - u1
    if len(ties) == 1:
        raise ValueError("Mann-Whitney U is undefined when all pooled observations are tied.")
    if n1 + n2 <= _RANK_EXACT_LIMIT:
        distribution = _conditional_rank_distribution(ranks, n1)
        observed = int(round(2 * math.fsum(ranks[:n1])))
        p = _two_sided_exact(distribution, observed)
        method, z = "exact conditional permutation", None
    else:
        n = n1 + n2
        variance = n1 * n2 / 12 * (n + 1 - sum(t**3 - t for t in ties) / (n * (n - 1)))
        p, z = _normal_two_sided(u1, n1 * n2 / 2, variance)
        method = "tie-corrected normal with 0.5 continuity correction"
    result = {
        "sample_a_size": n1, "sample_b_size": n2, "u_a": u1, "u_b": u2,
        "u_statistic": min(u1, u2), "p_value": p,
        "method": method, "ties_present": str(any(t > 1 for t in ties)).lower(),
    }
    if z is not None:
        result["absolute_z_statistic"] = z
    return {"summary": "Two-sided Mann-Whitney U test (independent samples); " + method + ".",
            "values": result}


def _wilcoxon(data):
    a, b = data["before"], data["after"]
    if len(a) != len(b):
        raise ValueError("Wilcoxon signed-rank requires matched samples of equal length.")
    if len(a) < 2:
        raise ValueError("Wilcoxon signed-rank requires at least two matched pairs.")
    diffs = [y - x for x, y in zip(a, b) if y != x]
    if not all(math.isfinite(d) for d in diffs):
        raise ValueError("Paired differences exceed the supported numeric range.")
    n = len(diffs)
    if n < 2:
        raise ValueError("Wilcoxon signed-rank requires at least two nonzero paired differences.")
    ranks, ties = _rank([abs(d) for d in diffs])
    wplus = math.fsum(r for r, d in zip(ranks, diffs) if d > 0)
    wminus = n * (n + 1) / 2 - wplus
    if n <= _RANK_EXACT_LIMIT:
        # Conditional on observed absolute ranks, all 2**n sign assignments.
        doubled = [int(round(2 * rank)) for rank in ranks]
        ways = Counter({0: 1})
        for r in doubled:
            new = Counter(ways)
            for total, count in ways.items():
                new[total + r] += count
            ways = new
        p = _two_sided_exact(ways, int(round(2 * wplus)))
        method, z = "exact conditional sign permutation", None
    else:
        variance = n * (n + 1) * (2 * n + 1) / 24 - sum(t**3 - t for t in ties) / 48
        p, z = _normal_two_sided(wplus, n * (n + 1) / 4, variance)
        method = "tie-corrected normal with 0.5 continuity correction"
    values = {
        "pairs": len(a), "nonzero_pairs": n, "zero_pairs_dropped": len(a) - n,
        "w_plus": wplus, "w_minus": wminus, "w_statistic": min(wplus, wminus),
        "p_value": p, "method": method,
        "ties_present": str(any(t > 1 for t in ties)).lower(),
    }
    if z is not None:
        values["absolute_z_statistic"] = z
    return {"summary": "Two-sided Wilcoxon signed-rank test (after minus before); zeros omitted; " + method + ".",
            "values": values}


def _safe_correlation(a, b):
    """Centered and scaled Pearson correlation, avoiding cancellation."""
    if len(a) != len(b):
        raise ValueError("Correlation requires the same number of paired x and y values.")
    if len(a) < 4:
        raise ValueError("A 95% correlation confidence interval requires at least four pairs.")
    # Subtract before scaling, then normalize to protect the sums of squares.
    amin, bmin = min(a), min(b)
    ax = [x - amin for x in a]
    by = [y - bmin for y in b]
    sx, sy = max(ax), max(by)
    if not math.isfinite(sx) or not math.isfinite(sy):
        raise ValueError("Correlation differences exceed the supported numeric range.")
    if sx == 0 or sy == 0:
        raise ValueError("Correlation is undefined for a constant (degenerate) variable.")
    ax = [x / sx for x in ax]
    by = [y / sy for y in by]
    mx, my = math.fsum(ax) / len(ax), math.fsum(by) / len(by)
    dx = [x - mx for x in ax]
    dy = [y - my for y in by]
    xx = math.fsum(x * x for x in dx)
    yy = math.fsum(y * y for y in dy)
    if xx == 0 or yy == 0:
        raise ValueError("Correlation is undefined for a constant (degenerate) variable.")
    return max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(dx, dy)) / math.sqrt(xx * yy)))


def _correlation_ci(r, n, *, spearman=False):
    if abs(r) >= 1:
        return r, r
    # Bonett-Wright approximation to the Spearman Fisher-z variance.
    factor = 1 + r * r / 2 if spearman else 1
    width = _Z95 * math.sqrt(factor / (n - 3))
    center = math.atanh(r)
    return math.tanh(center - width), math.tanh(center + width)


def _pearson(data):
    a, b = data["x"], data["y"]
    r = _safe_correlation(a, b)
    df = len(a) - 2
    p = 0.0 if abs(r) == 1 else _probability(2 * _student_t_cdf(-abs(r) * math.sqrt(df / ((1 - r) * (1 + r))), df))
    lo, hi = _correlation_ci(r, len(a))
    return {"summary": "Two-sided Pearson product-moment correlation with 95% Fisher-z confidence limits.",
            "values": {"observations": len(a), "r": r, "degrees_of_freedom": df,
                       "p_value": p, "ci95_lower": lo, "ci95_upper": hi}}


def _spearman(data):
    a, b = data["x"], data["y"]
    _safe_correlation(a, b)  # paired length / degenerate input validation
    ar, at = _rank(a)
    br, bt = _rank(b)
    r = _safe_correlation(ar, br)
    n = len(a)
    if n <= _SPEARMAN_EXACT_LIMIT:
        permutations = itertools.permutations(br)
        # Two-sided permutation p-value conditional on tied margins.
        extreme = total = 0
        for perm in permutations:
            rr = _safe_correlation(ar, perm)
            extreme += abs(rr) >= abs(r) - 1e-12
            total += 1
        p, method = extreme / total, "exact permutation (conditional on ranks)"
    else:
        df = n - 2
        p = 0.0 if abs(r) == 1 else 2 * _student_t_cdf(-abs(r) * math.sqrt(df / ((1 - r) * (1 + r))), df)
        method = "large-sample t approximation"
    lo, hi = _correlation_ci(r, n, spearman=True)
    return {"summary": "Two-sided Spearman midrank correlation; " + method + "; approximate 95% Fisher-z confidence limits.",
            "values": {"observations": n, "rho": r, "p_value": _probability(p), "method": method,
                       "ties_present": str(any(t > 1 for t in at + bt)).lower(),
                       "ci95_lower": lo, "ci95_upper": hi}}


def _poly(coefficients, x):
    return math.fsum(c * x**j for j, c in enumerate(coefficients))


def _shapiro(data):
    xs = sorted(data["sample"])
    n = len(xs)
    if n < 3:
        raise ValueError("Shapiro-Wilk requires at least 3 observations.")
    if n > 5000:
        raise ValueError("Royston's Shapiro-Wilk approximation supports at most 5000 observations.")
    baseline = xs[0]
    centered = [x - baseline for x in xs]
    scale = centered[-1]
    if scale == 0:
        raise ValueError("Shapiro-Wilk is undefined when all observations are identical.")
    if not math.isfinite(scale):
        raise ValueError("Shapiro-Wilk measurement range exceeds supported precision.")
    centered = [x / scale for x in centered]
    mean = math.fsum(centered) / n
    ss = math.fsum((x - mean)**2 for x in centered)
    half = n // 2
    if n == 3:
        coeff = [1 / math.sqrt(2)]
    else:
        m = [_NORMAL.inv_cdf((i + 1 - 0.375) / (n + 0.25)) for i in range(half)]
        norm = math.sqrt(2 * math.fsum(v * v for v in m))
        u = 1 / math.sqrt(n)
        a1 = -m[0] / norm + _poly((0, .221157, -.147981, -2.07119, 4.434685, -2.706056), u)
        coeff = [a1]
        if n > 5:
            a2 = -m[1] / norm + _poly((0, .042981, -.293762, -1.752461, 5.682633, -3.582633), u)
            coeff.append(a2)
        rem = 1 - 2 * math.fsum(a * a for a in coeff)
        if rem <= 0:
            raise ValueError("Shapiro-Wilk weight normalization failed.")
        start = len(coeff)
        other = 2 * math.fsum(v * v for v in m[start:])
        coeff.extend([-v * math.sqrt(rem / other) for v in m[start:]])
    weighted = math.fsum(a * (centered[n - 1 - i] - centered[i]) for i, a in enumerate(coeff))
    w = max(0, min(1, weighted * weighted / ss))
    if n == 3 and 1 - w < 1e-14:
        w = 1.0
    if n == 3:
        p = _probability(6 / math.pi * (math.asin(math.sqrt(w)) - math.pi / 3))
    else:
        logw = math.log1p(-w) if w < 1 else -math.inf
        if n <= 11:
            gamma = -2.273 + 0.459 * n
            if logw >= gamma:
                p = 0.0
            else:
                t = -math.log(gamma - logw)
                mu = _poly((.544, -.39978, .025054, -.0006714), n)
                sig = math.exp(_poly((1.3822, -.77857, .062767, -.0020322), n))
                p = _NORMAL.cdf(-(t - mu) / sig)
        else:
            t = logw
            ln = math.log(n)
            mu = _poly((-1.5861, -.31082, -.083751, .0038915), ln)
            sig = math.exp(_poly((-.4803, -.082676, .0030302), ln))
            p = _NORMAL.cdf(-(t - mu) / sig)
    return {"summary": "Shapiro-Wilk normality test using Royston's AS R94 W coefficients and p-value approximation (exact p for n=3).",
            "values": {"observations": n, "w_statistic": w, "p_value": _probability(p),
                       "p_value_method": "exact" if n == 3 else "Royston AS R94 approximation"}}


@lru_cache(maxsize=4)
def _legendre_nodes(count):
    """Nodes/weights on [-1,1] from Newton iterations on Legendre polynomials."""
    nodes, weights = [0.0] * count, [0.0] * count
    for i in range((count + 1) // 2):
        z = math.cos(math.pi * (i + 0.75) / (count + 0.5))
        for _ in range(20):
            p0, p1 = 1.0, z
            for j in range(2, count + 1):
                p0, p1 = p1, ((2 * j - 1) * z * p1 - (j - 1) * p0) / j
            p = p1 if count > 1 else z
            old = p0 if count > 1 else 1.0
            derivative = count * (z * p - old) / (z * z - 1)
            step = p / derivative
            z -= step
            if abs(step) < 2e-16:
                break
        w = 2 / ((1 - z * z) * derivative**2)
        nodes[i] = -z
        nodes[count - 1 - i] = z
        weights[i] = weights[count - 1 - i] = w
    return tuple(zip(nodes, weights))


@lru_cache(maxsize=4096)
def _studentized_range_cdf(q, k, df):
    """Studentized-range CDF by quadrature over normal range and chi-squared scale.

    k=2 uses the exact Student t identity. For k>=3, integrate the Hartley
    range integral, mixed over sqrt(chi^2_df/df); Legendre quadrature uses
    finite tails with negligible missing mass (<~1e-12).
    """
    if q <= 0:
        return 0.0
    if k == 2:
        return _probability(2 * _student_t_cdf(q / math.sqrt(2), df) - 1)
    # More normal-range nodes are necessary for the steeper k>=16 integrand.
    nodes_z = _legendre_nodes(128 if k >= 16 else 80)
    z_data = [(9 * z, 9 * weight / math.sqrt(2 * math.pi) * math.exp(-.5 * (9 * z)**2),
               _NORMAL.cdf(9 * z)) for z, weight in nodes_z]
    sqrt2df = math.sqrt(2 * df)
    tlo, thi = -min(sqrt2df, 12), 12.0
    log_constant = (math.log(2) + df / 2 * math.log(df / 2)
                    - math.lgamma(df / 2) - math.log(sqrt2df))
    area = 0.0
    for xi, wi in _legendre_nodes(128):
        t = (thi + tlo) / 2 + (thi - tlo) / 2 * xi
        scale = 1 + t / sqrt2df
        if scale <= 0:
            continue
        log_density = log_constant + (df - 1) * math.log(scale) - df * scale**2 / 2
        density = math.exp(log_density)
        if density < 1e-22:
            continue
        width = q * scale
        inner = math.fsum(wz * max(0., _NORMAL.cdf(z + width) - cdfz) ** (k - 1)
                          for z, wz, cdfz in z_data)
        area += wi * density * k * inner
    return _probability(area * (thi - tlo) / 2)


@lru_cache(maxsize=256)
def _studentized_range_ppf95(k, df):
    low, high = 0.0, 5.0
    while _studentized_range_cdf(high, k, df) < 0.95:
        high *= 2
        if high > 1e7:
            raise ValueError("Studentized-range quantile exceeds the supported range.")
    for _ in range(37):
        mid = (low + high) / 2
        if _studentized_range_cdf(mid, k, df) < 0.95:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _one_way_anova(data):
    ys, labels = data["values"], data["groups"]
    if len(ys) != len(labels):
        raise ValueError("One-way ANOVA requires one group code per response value.")
    groups = {}
    for value, label in zip(ys, labels):
        groups.setdefault(label, []).append(value)
    if len(groups) < 2:
        raise ValueError("One-way ANOVA requires at least two distinct groups.")
    if len(groups) > 32:
        raise ValueError("One-way ANOVA supports at most 32 groups (pairwise table limit).")
    if any(len(v) < 2 for v in groups.values()):
        raise ValueError("One-way ANOVA requires at least two observations per group.")
    n, k = len(ys), len(groups)
    with localcontext() as ctx:
        ctx.prec = 76
        d_groups = {label: [Decimal(str(v)) for v in sample] for label, sample in groups.items()}
        means = {label: sum(sample) / len(sample) for label, sample in d_groups.items()}
        overall = sum(sum(sample) for sample in d_groups.values()) / n
        ss_between = sum((len(sample) * (means[label] - overall)**2
                          for label, sample in d_groups.items()), Decimal(0))
        ss_within = sum(((x - means[label])**2 for label, sample in d_groups.items()
                         for x in sample), Decimal(0))
        if not ss_within:
            raise ValueError("ANOVA pooled within-group variance is zero; F and Tukey HSD are undefined.")
        df1, df2 = k - 1, n - k
        ms_between, ms_within = ss_between / df1, ss_within / df2
        if any(value and float(value) == 0.0 for value in
               (ss_between, ss_within, ms_between, ms_within)):
            raise ValueError("ANOVA sums or mean squares underflow the supported floating-point range.")
        f = float(ms_between / ms_within)
        total = ss_between + ss_within
        vals = {
            "observations": n, "group_count": k, "df_between": df1, "df_within": df2,
            "ss_between": float(ss_between), "ss_within": float(ss_within),
            "ss_total": float(total), "ms_between": float(ms_between),
            "ms_within": float(ms_within), "f_statistic": f,
            "p_value": _beta_i(df2 / 2, df1 / 2, df2 / (df2 + df1 * f)) if math.isfinite(f) else 0.0,
            "r_squared": float(ss_between / total),
            "residual_standard_deviation": float(ms_within.sqrt()),
        }
        critical = _studentized_range_ppf95(k, df2)
        vals["tukey_q_critical_95"] = critical
        sorted_labels = sorted(d_groups)
        rows = []
        for i, a in enumerate(sorted_labels):
            for b in sorted_labels[i + 1:]:
                diff = float(means[a] - means[b])
                se = float((ms_within * (Decimal(1) / len(groups[a]) + Decimal(1) / len(groups[b])) / 2).sqrt())
                q = abs(diff) / se
                p = _probability(1 - _studentized_range_cdf(q, k, df2))
                margin = critical * se
                rows.append([a, b, diff, se, q, p, diff - margin, diff + margin])
    if not all(math.isfinite(v) for v in vals.values() if type(v) is float):
        raise ValueError("ANOVA results exceed supported floating-point range.")
    return {"summary": "One-way fixed-effects ANOVA and two-sided Tukey-Kramer HSD pairwise comparisons; "
                       "95% familywise confidence intervals assume independent normal errors and common variance.",
            "values": vals,
            "table": {"columns": ["group A", "group B", "mean A - mean B", "standard error", "q", "Tukey adjusted p", "95% lower", "95% upper"],
                      "rows": rows}}


def _adjust_p_values(data, method):
    ps, alpha = data["p_values"], data["alpha"]
    if not 0 < alpha < 1:
        raise ValueError("The familywise/FDR significance threshold alpha must lie strictly between 0 and 1.")
    if any(p < 0 or p > 1 for p in ps):
        raise ValueError("P-values must lie in the closed interval [0, 1].")
    m = len(ps)
    if method == "bonferroni":
        adjusted = [min(1., p * m) for p in ps]
        meaning = "familywise error rate"
    else:
        adjusted = [0.] * m
        limit = 1.
        for rank, i in reversed(list(enumerate(sorted(range(m), key=lambda j: (ps[j], j)), 1))):
            limit = min(limit, ps[i] * m / rank)
            adjusted[i] = limit
        meaning = "false discovery rate under independence or positive dependence"
    return {"summary": f"{method.replace('_', '-').title()} adjusted p-values at alpha={alpha:g}; controls {meaning}.",
            "values": {"hypotheses": m, "alpha": alpha,
                       "rejections": sum(p <= alpha for p in adjusted), "method": method},
            "table": {"columns": ["hypothesis (zero-based)", "raw p", "adjusted p", "reject"],
                      "rows": [[i, p, adjusted[i], str(adjusted[i] <= alpha).lower()] for i, p in enumerate(ps)]}}


def _bonferroni(data):
    return _adjust_p_values(data, "bonferroni")


def _benjamini_hochberg(data):
    return _adjust_p_values(data, "benjamini_hochberg")


OPERATIONS = (
    Operation("one_way_anova", "One-way ANOVA with Tukey HSD",
              "Fixed-effects one-way ANOVA with Tukey-Kramer pairwise familywise-adjusted tests and simultaneous confidence limits.",
              (Field("values", "Response measurements", "vector"), Field("groups", "Numeric group codes", "vector")),
              {"values": [7, 8, 9, 10, 11, 12, 9, 11, 13], "groups": [1, 1, 1, 2, 2, 2, 3, 3, 3]}, _one_way_anova),
    Operation("mann_whitney_u", "Mann-Whitney U",
              "Independent two-sample two-sided rank test; exact conditional distribution through 30 pooled observations.",
              (Field("sample_a", "Independent sample A", "vector"), Field("sample_b", "Independent sample B", "vector")),
              {"sample_a": [1, 3, 5, 7], "sample_b": [2, 4, 6, 8]}, _mann_whitney),
    Operation("wilcoxon_signed_rank", "Wilcoxon signed-rank",
              "Paired two-sided signed-rank test, excluding zero differences; exact conditional distribution through 30 nonzero pairs.",
              (Field("before", "Before measurements", "vector"), Field("after", "After measurements", "vector")),
              {"before": [2, 4, 6, 8, 10, 12], "after": [3, 6, 9, 12, 15, 18]}, _wilcoxon),
    Operation("shapiro_wilk", "Shapiro-Wilk normality",
              "Royston's AS R94 approximation to Shapiro-Wilk W; 3 to 1024 observations (workbench input limit).",
              (Field("sample", "Sample measurements", "vector"),),
              {"sample": [148, 154, 158, 160, 161, 162, 166, 170, 182, 195, 236]}, _shapiro),
    Operation("pearson_correlation", "Pearson correlation and 95% CI",
              "Two-sided Pearson r and Fisher-z 95% confidence interval.",
              (Field("x", "Paired x values", "vector"), Field("y", "Paired y values", "vector")),
              {"x": [1, 2, 3, 4, 5, 6], "y": [2, 5, 3, 8, 6, 9]}, _pearson),
    Operation("spearman_correlation", "Spearman correlation and 95% CI",
              "Two-sided Spearman rho with exact permutation p through 8 pairs and approximate Fisher-z 95% CI.",
              (Field("x", "Paired x values", "vector"), Field("y", "Paired y values", "vector")),
              {"x": [1, 2, 3, 4, 5, 6], "y": [2, 1, 4, 3, 6, 5]}, _spearman),
    Operation("bonferroni_correction", "Bonferroni p-value correction",
              "Controls familywise error by multiplying each p-value by the hypothesis count.",
              (Field("p_values", "Raw p-values", "vector"), Field("alpha", "Significance threshold", "number")),
              {"p_values": [.001, .02, .04, .3], "alpha": .05}, _bonferroni),
    Operation("benjamini_hochberg_correction", "Benjamini-Hochberg FDR correction",
              "Step-up monotone adjusted p-values for FDR control under independence or PRDS dependence.",
              (Field("p_values", "Raw p-values", "vector"), Field("alpha", "Significance threshold", "number")),
              {"p_values": [.001, .02, .04, .3], "alpha": .05}, _benjamini_hochberg),
)
