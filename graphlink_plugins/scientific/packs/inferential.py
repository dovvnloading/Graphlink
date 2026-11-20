"""Classical inference for the Scientific Workbench; Python standard library only.

Exact permutation distributions are used for small rank samples. Approximation
thresholds, tie handling, confidence intervals, and model assumptions are
specified in docs/SCIENTIFIC_FITTING_REFERENCE.md.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from collections import Counter
from decimal import Decimal, localcontext
from functools import lru_cache

from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.packs.fitting import (
    _as_float, _beta_i, _gamma_q, _normal_solve, _student_t_cdf, _student_t_ppf,
)

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
    # Subtract before scaling whenever the span fits in binary64: scaling
    # first can discard low bits even for small integer rank examples.
    amin, bmin = min(a), min(b)
    ax = [x - amin for x in a]
    by = [y - bmin for y in b]
    sx, sy = max(ax), max(by)
    # Preserve support for finite observations whose span overflows binary64
    # (e.g. -1e308 to +1e308) by normalizing only that variable first.
    if not math.isfinite(sx):
        ascale = max(map(abs, a)) or 1.0
        amin_scaled = amin / ascale
        ax = [x / ascale - amin_scaled for x in a]
        sx = max(ax)
    if not math.isfinite(sy):
        bscale = max(map(abs, b)) or 1.0
        bmin_scaled = bmin / bscale
        by = [y / bscale - bmin_scaled for y in b]
        sy = max(by)
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
    # Avoid overflow in max-min for finite observations of opposite sign.
    magnitude = max(map(abs, xs)) or 1.0
    xs = [x / magnitude for x in xs]
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


# Additional standard-library inferential procedures. Integrated in inferential.py.


def _two_way_anova(data):
    ys, aa, bb = data["values"], data["factor_a"], data["factor_b"]
    if len(ys) != len(aa) or len(ys) != len(bb):
        raise ValueError("Two-way ANOVA requires one level of each factor per response.")
    a_levels, b_levels = sorted(set(aa)), sorted(set(bb))
    na, nb, n = len(a_levels), len(b_levels), len(ys)
    if na < 2 or nb < 2:
        raise ValueError("Two-way ANOVA requires at least two levels of each factor.")
    if na * nb > 64:
        raise ValueError("Two-way ANOVA supports at most 64 factor combinations.")
    cells = {(a, b): [] for a in a_levels for b in b_levels}
    for y, a, b in zip(ys, aa, bb):
        cells[(a, b)].append(y)
    if any(not sample for sample in cells.values()):
        raise ValueError("Two-way ANOVA with interaction requires every factor combination to be observed.")
    if n <= na * nb:
        raise ValueError("Two-way ANOVA requires residual degrees of freedom (replicate measurements).")
    with localcontext() as ctx:
        ctx.prec = 76
        dy = [Decimal(str(v)) for v in ys]
        by_a = {a: [] for a in a_levels}
        by_b = {b: [] for b in b_levels}
        dcell = {(a, b): [] for a in a_levels for b in b_levels}
        for y, a, b in zip(dy, aa, bb):
            by_a[a].append(y)
            by_b[b].append(y)
            dcell[a, b].append(y)
        def within(groups):
            return sum((sum(((v - sum(group) / len(group)) ** 2 for v in group), Decimal(0))
                        for group in groups), Decimal(0))
        ss_a_only = within(by_a.values())
        ss_b_only = within(by_b.values())
        ss_full = within(dcell.values())
        # Type-II main effects: compare additive fits, never a type-III
        # main effect conditional on the higher-order interaction.
        x = [[Decimal(1)] + [Decimal(int(a == level)) for level in a_levels[1:]]
             + [Decimal(int(b == level)) for level in b_levels[1:]] for a, b in zip(aa, bb)]
        p = 1 + na - 1 + nb - 1
        gram = [[sum((row[i] * row[j] for row in x), Decimal(0)) for j in range(p)]
                for i in range(p)]
        rhs = [sum((row[i] * y for row, y in zip(x, dy)), Decimal(0)) for i in range(p)]
        beta = _normal_solve(gram, rhs)
        ss_add = sum(((y - sum((v * b for v, b in zip(row, beta)), Decimal(0))) ** 2
                      for y, row in zip(dy, x)), Decimal(0))
        ss = [ss_b_only - ss_add, ss_a_only - ss_add, ss_add - ss_full]
        if any(v < 0 for v in ss):
            raise ValueError("Two-way ANOVA sums of squares are numerically inconsistent.")
        if ss_full <= 0:
            raise ValueError("Two-way ANOVA residual variance is zero; F tests are undefined.")
        df_error = n - na * nb
        mse = ss_full / df_error
        if float(mse) == 0.0:
            raise ValueError("Two-way ANOVA residual mean square underflows the numeric range.")
        dfs = [na - 1, nb - 1, (na - 1) * (nb - 1)]
        rows = []
        for label, s, df in zip(("factor A (Type II)", "factor B (Type II)", "interaction A:B"), ss, dfs):
            ms = s / df
            f = float(ms / mse)
            pvalue = _beta_i(df_error / 2, df / 2, df_error / (df_error + df * f)) if math.isfinite(f) else 0.0
            rows.append([label, df, _as_float(s, "ANOVA sum of squares") if s else 0.0,
                         _as_float(ms, "ANOVA mean square") if ms else 0.0,
                         _as_float(Decimal(str(f)), "ANOVA F statistic"), _probability(pvalue)])
        rows.append(["residual", df_error, _as_float(ss_full, "ANOVA residual sum of squares"),
                     _as_float(mse, "ANOVA residual mean square"), "", ""])
        overall = sum(dy) / n
        total = sum(((y - overall) ** 2 for y in dy), Decimal(0))
        values = {"observations": n, "levels_a": na, "levels_b": nb,
                  "degrees_of_freedom_residual": df_error, "residual_sum_squares": _as_float(ss_full, "Residual sum of squares"),
                  "residual_mean_square": _as_float(mse, "Residual mean square"),
                  "r_squared_full": _as_float(1 - ss_full / total, "R squared"),
                  "ss_factor_a_type_ii": rows[0][2], "ss_factor_b_type_ii": rows[1][2],
                  "ss_interaction": rows[2][2], "f_factor_a": rows[0][4], "f_factor_b": rows[1][4],
                  "f_interaction": rows[2][4], "p_factor_a": rows[0][5], "p_factor_b": rows[1][5],
                  "p_interaction": rows[2][5]}
    return {"summary": "Two-way fixed-effects ANOVA with interaction. Type-II main-effect SS use additive model comparisons; all F tests use full-model residual error. Interpret interaction first.",
            "values": values,
            "table": {"columns": ["source", "df", "sum of squares", "mean square", "F", "p"], "rows": rows}}


def _kruskal_wallis(data):
    ys, labels = data["values"], data["groups"]
    if len(ys) != len(labels):
        raise ValueError("Kruskal-Wallis requires one group per measurement.")
    groups = {label: [] for label in sorted(set(labels))}
    for y, label in zip(ys, labels):
        groups[label].append(y)
    if not 2 <= len(groups) <= 32 or any(len(g) < 2 for g in groups.values()):
        raise ValueError("Kruskal-Wallis requires 2-32 independent groups with at least two observations each.")
    ranks, ties = _rank(ys)
    n = len(ys)
    correction = 1 - math.fsum(t**3 - t for t in ties) / (n**3 - n)
    if correction <= 0:
        raise ValueError("Kruskal-Wallis is undefined when all measurements are tied.")
    sums = {label: 0.0 for label in groups}
    for rank, label in zip(ranks, labels):
        sums[label] += rank
    h = max(0.0, (12 / (n * (n + 1)) *
                  math.fsum(sums[label]**2 / len(group) for label, group in groups.items())
                  - 3 * (n + 1)) / correction)
    df = len(groups) - 1
    pvalue = _gamma_q(df / 2, h / 2)
    variance = (n * (n + 1) - math.fsum(t**3 - t for t in ties) / (n - 1)) / 12
    labels = sorted(groups)
    rows = []
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            diff = sums[a] / len(groups[a]) - sums[b] / len(groups[b])
            se = math.sqrt(variance * (1 / len(groups[a]) + 1 / len(groups[b])))
            z = diff / se
            rows.append([a, b, diff, z, math.erfc(abs(z) / math.sqrt(2)), 0.0])
    m = len(rows)
    last = 0.0
    for rank, idx in enumerate(sorted(range(m), key=lambda i: rows[i][4])):
        last = max(last, min(1.0, (m - rank) * rows[idx][4]))
        rows[idx][5] = last
    return {"summary": "Kruskal-Wallis rank test with tie-corrected chi-square approximation; two-sided Dunn pairwise z tests, Holm familywise adjustment.",
            "values": {"observations": n, "group_count": len(groups), "degrees_of_freedom": df,
                       "h_statistic": h, "tie_correction": correction, "p_value": pvalue},
            "table": {"columns": ["group A", "group B", "mean rank A - B", "Dunn z", "raw p", "Holm adjusted p"], "rows": rows}}


def _count_table(raw, *, fisher=False):
    if len(raw) < 2 or len(raw[0]) < 2:
        raise ValueError("Contingency tables require at least two rows and two columns.")
    if fisher and (len(raw) != 2 or len(raw[0]) != 2):
        raise ValueError("Fisher's exact test requires a 2x2 contingency table.")
    if any(v < 0 or not v.is_integer() or v > 2**53 - 1 for row in raw for v in row):
        raise ValueError("Contingency counts must be nonnegative, exactly representable integers.")
    table = [[int(v) for v in row] for row in raw]
    row_totals = [sum(row) for row in table]
    col_totals = [sum(row[j] for row in table) for j in range(len(table[0]))]
    if any(v == 0 for v in row_totals + col_totals):
        raise ValueError("Contingency tables require nonzero row and column margins.")
    return table, row_totals, col_totals, sum(row_totals)


def _chi_square_independence(data):
    table, rt, ct, n = _count_table(data["table"])
    r, c = len(rt), len(ct)
    contributions = []
    cells_below_5 = 0
    rows = []
    for i in range(r):
        for j in range(c):
            expected = rt[i] * ct[j] / n
            observed = table[i][j]
            residual = (observed - expected) / math.sqrt(expected)
            contributions.append(residual**2)
            cells_below_5 += expected < 5
            rows.append([f"{i},{j}", observed, expected, residual])
    chi = math.fsum(contributions)
    df = (r - 1) * (c - 1)
    return {"summary": "Pearson chi-square independence test (uncorrected) using an asymptotic chi-square tail; inspect cells with expected counts below five.",
            "values": {"rows": r, "columns": c, "observations": n, "degrees_of_freedom": df,
                       "chi_square": chi, "p_value": _gamma_q(df / 2, chi / 2),
                       "expected_cells_below_5": cells_below_5,
                       "cramers_v": math.sqrt(chi / (n * min(r - 1, c - 1)))},
            "table": {"columns": ["row,column (zero-based)", "observed", "expected", "Pearson residual"], "rows": rows}}


def _fisher_exact(data):
    table, rt, ct, n = _count_table(data["table"], fisher=True)
    a, b = table[0]
    c, d = table[1]
    lo, hi = max(0, ct[0] - rt[1]), min(ct[0], rt[0])
    if hi - lo > 100000:
        raise ValueError("Fisher's exact test supports at most 100001 feasible tables for fixed margins.")
    # lgamma subtraction loses all low-tail precision when margins are large.
    # Recurrences of adjacent hypergeometric weights use only exact counts
    # before division and are normalized around the distribution mode.
    mode = max(lo, min(hi, ((rt[0] + 1) * (ct[0] + 1)) // (n + 2)))
    weights = [0.0] * (hi - lo + 1)
    weights[mode - lo] = 1.0
    for v in range(mode + 1, hi + 1):
        previous = v - 1
        weights[v - lo] = (weights[previous - lo] * ((rt[0] - previous) / v)
                           * ((ct[0] - previous) / (rt[1] - ct[0] + v)))
    for v in range(mode - 1, lo - 1, -1):
        weights[v - lo] = (weights[v + 1 - lo] * ((v + 1) / (rt[0] - v))
                           * ((rt[1] - ct[0] + v + 1) / (ct[0] - v)))
    total = math.fsum(weights)
    observed_weight = weights[a - lo]
    # Probability-order two-sided convention (R fisher.test / SciPy).
    p_two = math.fsum(w for w in weights if w <= observed_weight * (1 + 1e-10)) / total
    p_less = math.fsum(weights[:a - lo + 1]) / total
    p_greater = math.fsum(weights[a - lo:]) / total
    odds = "infinite" if b * c == 0 and a * d else (a * d / (b * c) if b * c else "undefined (zero margins)")
    return {"summary": "Fisher conditional exact test for a 2x2 table; two-sided p sums all fixed-margin tables no more probable than observed. Odds ratio is the sample cross-product estimate.",
            "values": {"observations": n, "odds_ratio": odds, "p_two_sided": _probability(p_two),
                       "p_less": _probability(p_less), "p_greater": _probability(p_greater),
                       "feasible_tables": hi - lo + 1}}


def _ols_model(data):
    matrix, response = data["predictors"], data["response"]
    n = len(response)
    if len(matrix) != n:
        raise ValueError("Multiple regression needs one predictor row per response.")
    p = len(matrix[0]) + 1
    if p > 16:
        raise ValueError("Multiple regression supports at most 15 predictors plus an intercept.")
    if n <= p:
        raise ValueError("Multiple regression requires positive residual degrees of freedom.")
    with localcontext() as ctx:
        ctx.prec = 76
        x = [[Decimal(1)] + [Decimal(str(v)) for v in row] for row in matrix]
        y = [Decimal(str(v)) for v in response]
        gram = [[sum((row[i] * row[j] for row in x), Decimal(0)) for j in range(p)] for i in range(p)]
        rhs = [sum((row[i] * yy for row, yy in zip(x, y)), Decimal(0)) for i in range(p)]
        beta, inverse = _normal_solve(gram, rhs, inverse=True)
        predicted = [sum((v * b for v, b in zip(row, beta)), Decimal(0)) for row in x]
        residual = [yy - yh for yy, yh in zip(y, predicted)]
        sse = sum((v * v for v in residual), Decimal(0))
        mean = sum(y) / n
        sst = sum(((v - mean)**2 for v in y), Decimal(0))
        if sst <= 0:
            raise ValueError("Multiple regression has a constant response (R squared is undefined).")
        if sse <= 0:
            raise ValueError("Multiple regression has zero residual variance; inferential standard errors are undefined.")
        df = n - p
        mse = sse / df
        stdev = mse.sqrt()
        crit = Decimal(str(_student_t_ppf(.975, df)))
        rows = []
        for j, coef in enumerate(beta):
            se = (mse * inverse[j][j]).sqrt()
            t = _as_float(coef / se, "Regression t statistic")
            pvalue = 2 * _student_t_cdf(-abs(t), df)
            if j:
                xmean = sum((row[j] for row in x), Decimal(0)) / n
                ssx = sum(((row[j] - xmean)**2 for row in x), Decimal(0))
                vif = _as_float(ssx * inverse[j][j], "Variance inflation factor")
            else:
                vif = "not applicable"
            rows.append(["intercept" if j == 0 else f"predictor_{j}",
                         _as_float(coef, "Coefficient"), _as_float(se, "Standard error"),
                         t, _probability(pvalue), _as_float(coef - crit * se, "95% CI"),
                         _as_float(coef + crit * se, "95% CI"), vif])
        diag_rows = []
        leverage_max = Decimal(0)
        max_std = max_cook = Decimal(0)
        for i, (row, yhat, res) in enumerate(zip(x, predicted, residual)):
            lever = sum((row[j] * inverse[j][k] * row[k] for j in range(p) for k in range(p)), Decimal(0))
            leverage_max = max(leverage_max, lever)
            if lever >= 1 - Decimal("1e-25"):
                standardized, cooks = "undefined (unit leverage)", "undefined (unit leverage)"
            else:
                standardized_d = res / (stdev * (1 - lever).sqrt())
                cook_d = res**2 / (p * mse) * lever / (1 - lever)**2
                max_std = max(max_std, abs(standardized_d))
                max_cook = max(max_cook, cook_d)
                standardized = _as_float(standardized_d, "Standardized residual")
                cooks = _as_float(cook_d, "Cook distance") if cook_d else 0.0
            diag_rows.append([i, _as_float(yhat, "Fitted value"), _as_float(res, "Residual"),
                              _as_float(lever, "Leverage"), standardized, cooks])
        dw = sum(((residual[i] - residual[i-1])**2 for i in range(1, n)), Decimal(0)) / sse
        m2 = sum((v**2 for v in residual), Decimal(0)) / n
        m3 = sum((v**3 for v in residual), Decimal(0)) / n
        m4 = sum((v**4 for v in residual), Decimal(0)) / n
        jb = Decimal(n) / 6 * ((m3 / m2**Decimal("1.5"))**2 + (m4 / m2**2 - 3)**2 / 4)
        r2 = 1 - sse / sst
        adj = 1 - (sse / df) / (sst / (n - 1))
        values = {"observations": n, "parameters": p, "degrees_of_freedom": df,
                  "residual_sum_squares": _as_float(sse, "Residual SS"),
                  "residual_standard_deviation": _as_float(stdev, "Residual SD"),
                  "r_squared": _as_float(r2, "R squared"),
                  "adjusted_r_squared": _as_float(adj, "Adjusted R squared"),
                  "durbin_watson": _as_float(dw, "Durbin-Watson"),
                  "jarque_bera": _as_float(jb, "Jarque-Bera"),
                  "jarque_bera_p_value": math.exp(-_as_float(jb, "Jarque-Bera") / 2),
                  "max_leverage": _as_float(leverage_max, "Leverage"),
                  "max_abs_standardized_residual": _as_float(max_std, "Standardized residual") if max_std else 0.0,
                  "max_cooks_distance": _as_float(max_cook, "Cook distance") if max_cook else 0.0}
        for i, row in enumerate(diag_rows):
            values[f"fitted_{i}"] = row[1]
            values[f"residual_{i}"] = row[2]
            values[f"leverage_{i}"] = row[3]
            values[f"standardized_residual_{i}"] = row[4]
            values[f"cooks_distance_{i}"] = row[5]
        for j, row in enumerate(rows):
            values[f"coefficient_{j}"] = row[1]
            values[f"standard_error_{j}"] = row[2]
            values[f"ci95_lower_{j}"] = row[5]
            values[f"ci95_upper_{j}"] = row[6]
            if j:
                values[f"vif_{j}"] = row[7]
    return {"summary": "Unweighted OLS with intercept; two-sided t/Wald 95% coefficient intervals, VIF and in-sample residual diagnostics. Durbin-Watson depends on observation order; Jarque-Bera is asymptotic.",
            "values": values,
            "table": {"columns": ["term", "estimate", "standard error", "t", "two-sided p", "95% lower", "95% upper", "VIF"], "rows": rows},
            "plot": {"title": "OLS residuals versus fitted values", "xLabel": "Fitted response", "yLabel": "Observed minus fitted",
                     "x": [row[1] for row in diag_rows], "y": [row[2] for row in diag_rows]}}


def _sigmoid(z):
    if z >= 0:
        e = math.exp(-z)
        return 1 / (1 + e)
    e = math.exp(z)
    return e / (1 + e)


def _logistic_loglike(x, y, beta):
    return math.fsum((yy * eta - (max(eta, 0.0) + math.log1p(math.exp(-abs(eta))))
                      for row, yy in zip(x, y) for eta in (math.fsum(v * b for v, b in zip(row, beta)),)))


def _logistic(data):
    matrix, outcomes = data["predictors"], data["outcome"]
    n = len(outcomes)
    if n != len(matrix):
        raise ValueError("Binary logistic regression requires one predictor row per outcome.")
    if any(y not in (0, 1) for y in outcomes):
        raise ValueError("Binary logistic regression outcomes must be exactly 0 or 1.")
    if len(set(outcomes)) < 2:
        raise ValueError("Binary logistic regression requires both outcome classes (degenerate outcome).")
    p = len(matrix[0]) + 1
    if p > 13 or n <= p:
        raise ValueError("Binary logistic regression requires n>parameters and at most 12 predictors.")
    with localcontext() as ctx:
        ctx.prec = 76
        raw = [[Decimal(str(v)) for v in row] for row in matrix]
        means = [sum((row[j] for row in raw), Decimal(0)) / n for j in range(p - 1)]
        scales = [(sum(((row[j] - means[j])**2 for row in raw), Decimal(0)) / n).sqrt()
                  for j in range(p - 1)]
        if any(v == 0 for v in scales):
            raise ValueError("Binary logistic regression has a constant predictor (rank deficient).")
        x = [[1.0] + [float((row[j] - means[j]) / scales[j]) for j in range(p - 1)] for row in raw]
        # Check the unweighted design before IRLS; separation must not be
        # disguised as a generic linear solve failure.
        g = [[sum((Decimal(str(row[j])) * Decimal(str(row[k])) for row in x), Decimal(0))
              for k in range(p)] for j in range(p)]
        _normal_solve(g, [Decimal(0)] * p)
        fraction = math.fsum(outcomes) / n
        beta = [math.log(fraction / (1 - fraction))] + [0.] * (p - 1)
        converged = False
        for iteration in range(1, 101):
            predicted = [_sigmoid(math.fsum(v * b for v, b in zip(row, beta))) for row in x]
            weights = [pr * (1 - pr) for pr in predicted]
            score = [math.fsum(row[j] * (yy - pr) for row, yy, pr in zip(x, outcomes, predicted))
                     for j in range(p)]
            gram = [[sum((Decimal(str(w)) * Decimal(str(row[j])) * Decimal(str(row[k]))
                          for row, w in zip(x, weights)), Decimal(0))
                     for k in range(p)] for j in range(p)]
            try:
                delta = _normal_solve(gram, [Decimal(str(v)) for v in score])
            except ValueError:
                raise ValueError("Binary logistic regression has complete/quasi separation or singular IRLS information.") from None
            step = [float(v) for v in delta]
            old_ll = _logistic_loglike(x, outcomes, beta)
            multiplier = 1.
            for _ in range(32):
                candidate = [b + multiplier * s for b, s in zip(beta, step)]
                new_ll = _logistic_loglike(x, outcomes, candidate)
                if new_ll >= old_ll - 1e-12:
                    break
                multiplier /= 2
            else:
                raise ValueError("Binary logistic regression IRLS line search failed to converge.")
            beta = candidate
            if max(map(abs, beta)) > 40:
                raise ValueError("Binary logistic regression has complete or quasi separation (diverging coefficients).")
            if max(abs(multiplier * s) for s in step) < 1e-9:
                converged = True
                break
        if not converged:
            raise ValueError("Binary logistic regression did not converge; check for complete/quasi separation.")
        predicted = [_sigmoid(math.fsum(v * b for v, b in zip(row, beta))) for row in x]
        weights = [v * (1 - v) for v in predicted]
        gram = [[sum((Decimal(str(w)) * Decimal(str(row[j])) * Decimal(str(row[k]))
                      for row, w in zip(x, weights)), Decimal(0))
                 for k in range(p)] for j in range(p)]
        try:
            _, inv = _normal_solve(gram, [Decimal(0)] * p, inverse=True)
        except ValueError:
            raise ValueError("Binary logistic regression has separation or singular fitted information.") from None
        # Map coefficients and covariance from standardized predictors back
        # to the supplied predictor units, including intercept covariances.
        transform = [[Decimal(1)] + [-means[j] / scales[j] for j in range(p - 1)]]
        transform.extend([[Decimal(0)] + [Decimal(int(j == k)) / scales[j] for k in range(p - 1)]
                          for j in range(p - 1)])
        coef = [sum((transform[i][j] * Decimal(str(beta[j])) for j in range(p)), Decimal(0)) for i in range(p)]
        covariance = [[sum((transform[i][k] * inv[k][l] * transform[j][l]
                            for k in range(p) for l in range(p)), Decimal(0))
                       for j in range(p)] for i in range(p)]
        rows = []
        for j in range(p):
            se = covariance[j][j].sqrt()
            z = _as_float(coef[j] / se, "Logistic Wald z")
            b = _as_float(coef[j], "Logistic coefficient")
            err = _as_float(se, "Logistic standard error")
            lo = _as_float(coef[j] - Decimal(str(_Z95)) * se, "Logistic CI")
            hi = _as_float(coef[j] + Decimal(str(_Z95)) * se, "Logistic CI")
            if not -700 < lo <= hi < 700:
                raise ValueError("Logistic odds ratios or their confidence limits exceed the finite numeric range.")
            rows.append(["intercept" if j == 0 else f"predictor_{j}", b, err, z,
                         math.erfc(abs(z) / math.sqrt(2)), lo, hi,
                         math.exp(b), math.exp(lo), math.exp(hi)])
        loglike = _logistic_loglike(x, outcomes, beta)
        null_ll = sum(outcomes) * math.log(fraction) + (n - sum(outcomes)) * math.log1p(-fraction)
        lr = max(0.0, 2 * (loglike - null_ll))
        values = {"observations": n, "parameters": p, "iterations": iteration,
                  "log_likelihood": loglike, "null_log_likelihood": null_ll,
                  "likelihood_ratio_chi_square": lr, "likelihood_ratio_p_value": _gamma_q((p - 1) / 2, lr / 2) if p > 1 else 1.0,
                  "aic": 2 * p - 2 * loglike, "mcfadden_pseudo_r_squared": 1 - loglike / null_ll}
        for j, row in enumerate(rows):
            values[f"coefficient_{j}"] = row[1]
            values[f"standard_error_{j}"] = row[2]
            values[f"odds_ratio_{j}"] = row[7]
            values[f"odds_ratio_ci95_lower_{j}"] = row[8]
            values[f"odds_ratio_ci95_upper_{j}"] = row[9]
    return {"summary": "Binary logistic maximum-likelihood regression by damped IRLS; two-sided Wald 95% odds-ratio intervals, likelihood ratio test and McFadden pseudo-R². Separation is rejected.",
            "values": values,
            "table": {"columns": ["term", "log-odds coefficient", "standard error", "z", "two-sided p", "log-odds lower", "log-odds upper", "odds ratio", "OR 95% lower", "OR 95% upper"], "rows": rows},
            "plot": {"title": "Binary logistic fitted probabilities", "xLabel": "Observation index (zero-based)",
                     "yLabel": "Fitted probability of outcome 1", "x": list(range(n)), "y": predicted}}


def _sample_statistic(xs, name):
    if name == "mean":
        return math.fsum(xs) / len(xs)
    if name == "median":
        return statistics.median(xs)
    if name == "standard_deviation":
        return statistics.stdev(xs)
    raise ValueError("Bootstrap statistic must be 'mean', 'median', or 'standard_deviation'.")


def _quantile_sorted(xs, q):
    at = (len(xs) - 1) * q
    lo = math.floor(at)
    hi = math.ceil(at)
    return xs[lo] + (xs[hi] - xs[lo]) * (at - lo)


def _bootstrap_ci(data):
    sample, name = data["sample"], data["statistic"].strip().lower()
    reps, seed, confidence = data["replicates"], data["seed"], data["confidence_level"]
    n = len(sample)
    if n < 3:
        raise ValueError("BCa bootstrap needs at least three independent observations.")
    if not 200 <= reps <= 20000:
        raise ValueError("Bootstrap replicate count must lie between 200 and 20000.")
    if not 0 < confidence < 1:
        raise ValueError("Bootstrap confidence level must lie strictly between 0 and 1.")
    observed = _sample_statistic(sample, name)
    rng = random.Random(seed)
    distribution = sorted(_sample_statistic([sample[rng.randrange(n)] for _ in range(n)], name)
                          for _ in range(reps))
    if not all(map(math.isfinite, distribution)):
        raise ValueError("Bootstrap statistics exceed the finite numerical range.")
    if distribution[0] == distribution[-1]:
        raise ValueError("Bootstrap replicates are degenerate (all estimates identical).")
    jack = [_sample_statistic(sample[:i] + sample[i+1:], name) for i in range(n)]
    mean_j = math.fsum(jack) / n
    influences = [mean_j - j for j in jack]
    sumsq = math.fsum(v**2 for v in influences)
    if sumsq <= 0:
        raise ValueError("BCa bootstrap jackknife acceleration is undefined for degenerate data.")
    acceleration = math.fsum(v**3 for v in influences) / (6 * sumsq**1.5)
    count_less = sum(x < observed for x in distribution)
    count_equal = sum(x == observed for x in distribution)
    fraction = (count_less + count_equal / 2) / reps
    if not 0 < fraction < 1:
        raise ValueError("BCa bootstrap bias correction is undefined (all estimates on one side).")
    bias = _NORMAL.inv_cdf(fraction)
    alpha = (1 - confidence) / 2
    def corrected(p):
        z = _NORMAL.inv_cdf(p)
        if 1 - acceleration * (bias + z) == 0:
            raise ValueError("BCa bootstrap acceleration correction is singular.")
        return _NORMAL.cdf(bias + (bias + z) / (1 - acceleration * (bias + z)))
    lower, upper = corrected(alpha), corrected(1 - alpha)
    if not 0 < lower < upper < 1:
        raise ValueError("BCa bootstrap adjusted tail probabilities are degenerate.")
    return {"summary": "Reproducible nonparametric iid bootstrap; linearly interpolated empirical percentile and bias-corrected accelerated (BCa) intervals using leave-one-out jackknife.",
            "values": {"observations": n, "statistic": name, "estimate": observed,
                       "replicates": reps, "seed": seed, "confidence_level": confidence,
                       "percentile_lower": _quantile_sorted(distribution, alpha),
                       "percentile_upper": _quantile_sorted(distribution, 1 - alpha),
                       "bca_lower": _quantile_sorted(distribution, lower),
                       "bca_upper": _quantile_sorted(distribution, upper),
                       "bias_correction_z0": bias, "jackknife_acceleration": acceleration}}


def _power_design(data):
    if data["design"] not in ("one_sample", "paired", "two_sample"):
        raise ValueError("t-test design must be 'one_sample', 'paired', or 'two_sample'.")
    if data["tails"] not in ("one_sided", "two_sided"):
        raise ValueError("Test tails must be 'one_sided' or 'two_sided'.")
    if not 0 < data["alpha"] < 1 or not 0 < data["target_power"] < 1:
        raise ValueError("Alpha and target power must lie strictly between 0 and 1.")
    if not 3 <= data["sample_size"] <= 1000000:
        raise ValueError("Power requires 3 to 1,000,000 observations per group/pair.")


def _t_test_power_at(n, effect, alpha, tails, design):
    df = n - 1 if design in ("one_sample", "paired") else 2 * n - 2
    delta = effect * math.sqrt(n if design != "two_sample" else n / 2)
    critical = -_student_t_ppf(alpha / (2 if tails == "two_sided" else 1), df)
    sqrt2df = math.sqrt(2 * df)
    tlo, thi = -min(sqrt2df, 12.0), 12.0
    const = math.log(2) + df / 2 * math.log(df / 2) - math.lgamma(df / 2) - math.log(sqrt2df)
    area = 0.0
    for xi, wi in _legendre_nodes(128):
        t = (thi + tlo) / 2 + (thi - tlo) / 2 * xi
        scale = 1 + t / sqrt2df
        if scale <= 0:
            continue
        density = math.exp(const + (df - 1) * math.log(scale) - df * scale**2 / 2)
        if density < 1e-25:
            continue
        p = _NORMAL.cdf(delta - critical * scale)
        if tails == "two_sided":
            p += _NORMAL.cdf(-delta - critical * scale)
        area += wi * density * p
    return _probability(area * (thi - tlo) / 2)


def _required_size(target, method):
    low, high = 3, 3
    if method(low) >= target:
        return low
    while method(high) < target:
        low = high
        high *= 2
        if high > 1000000:
            raise ValueError("Target power needs more than 1,000,000 observations per group.")
    while high - low > 1:
        mid = (low + high) // 2
        if method(mid) >= target:
            high = mid
        else:
            low = mid
    return high


def _t_test_power(data):
    _power_design(data)
    effect = abs(data["effect_size"])
    if effect <= 0 or not math.isfinite(effect):
        raise ValueError("Power calculation requires a nonzero standardized effect size.")
    alpha, target = data["alpha"], data["target_power"]
    tails, design, n = data["tails"], data["design"], data["sample_size"]
    method = lambda size: _t_test_power_at(size, effect, alpha, tails, design)
    required = _required_size(target, method)
    return {"summary": "Exact noncentral-t power by deterministic chi-square mixing; minimum integer sample size for the target power, independent normal measurements and a standardized effect.",
            "values": {"effect_size": effect, "alpha": alpha, "tails": tails, "design": design,
                       "sample_size_per_group": n, "achieved_power": method(n),
                       "target_power": target, "required_sample_size_per_group": required,
                       "power_at_required_sample_size": method(required)}}


def _proportion_power_at(n, a, b, alpha, tails, design):
    difference = abs(a - b)
    if design == "one_sample":
        null_variance = b * (1 - b)
        alternative_variance = a * (1 - a)
    else:
        pooled = (a + b) / 2
        null_variance = 2 * pooled * (1 - pooled)
        alternative_variance = a * (1 - a) + b * (1 - b)
    z = _NORMAL.inv_cdf(1 - alpha / (2 if tails == "two_sided" else 1))
    scale = math.sqrt(alternative_variance)
    p = _NORMAL.cdf((difference * math.sqrt(n) - z * math.sqrt(null_variance)) / scale)
    if tails == "two_sided":
        p += _NORMAL.cdf((-difference * math.sqrt(n) - z * math.sqrt(null_variance)) / scale)
    return _probability(p)


def _proportion_power(data):
    a, b = data["proportion_a"], data["proportion_b"]
    if not 0 < a < 1 or not 0 < b < 1 or a == b:
        raise ValueError("Power calculations require distinct interior probabilities (0 < p < 1).")
    if data["design"] not in ("one_sample", "two_sample"):
        raise ValueError("Proportion design must be 'one_sample' or 'two_sample'.")
    _power_design(data)
    alpha, target = data["alpha"], data["target_power"]
    tails, design, n = data["tails"], data["design"], data["sample_size"]
    method = lambda size: _proportion_power_at(size, a, b, alpha, tails, design)
    required = _required_size(target, method)
    return {"summary": "Normal-approximation z-test power and minimum integer sample size: one sample versus null proportion b, or equal-size independent two-sample proportions a/b (pooled-null variance).",
            "values": {"proportion_a": a, "proportion_b": b, "alpha": alpha, "tails": tails,
                       "design": design, "sample_size_per_group": n,
                       "achieved_power": method(n), "target_power": target,
                       "required_sample_size_per_group": required,
                       "power_at_required_sample_size": method(required)}}

OPERATIONS = (
    Operation("two_way_anova", "Two-way ANOVA (Type II, interaction)",
              "Full factorial fixed-effects ANOVA; Type-II main-effect SS from additive reduced models, interaction from full model.",
              (Field("values", "Responses", "vector"), Field("factor_a", "Factor A numeric codes", "vector"),
               Field("factor_b", "Factor B numeric codes", "vector")),
              {"values": [10, 12, 13, 15, 16, 16, 20, 21, 25, 25, 26, 28],
               "factor_a": [0]*6 + [1]*6, "factor_b": [0]*3+[1]*3+[0]*3+[1]*3}, _two_way_anova, headline="p_interaction"),
    Operation("kruskal_wallis", "Kruskal-Wallis with Dunn post-hoc",
              "Tie-corrected omnibus rank test with all pairwise Dunn z comparisons and Holm familywise p adjustment.",
              (Field("values", "Measurements", "vector"), Field("groups", "Numeric group codes", "vector")),
              {"values": [1, 1, 1, 1, 2, 2, 2, 4, 3, 3, 5, 6],
               "groups": [1]*4+[2]*4+[3]*4}, _kruskal_wallis, headline="p_value"),
    Operation("chi_square_independence", "Chi-square test of independence",
              "Pearson contingency-table test, expected counts, Pearson residuals and Cramer's V.",
              (Field("table", "Observed contingency counts", "matrix"),),
              {"table": [[9, 1], [2, 8]]}, _chi_square_independence, headline="p_value"),
    Operation("fisher_exact", "Fisher's exact test (2x2)",
              "Fixed-margin hypergeometric test; probability-order two-sided and directional exact p-values.",
              (Field("table", "2x2 observed counts", "matrix"),),
              {"table": [[3, 1], [1, 3]]}, _fisher_exact, headline="p_two_sided"),
    Operation("multiple_linear_regression", "Multiple linear regression and diagnostics",
              "Intercept OLS; Student-t coefficient limits, adjusted R squared, VIF, residuals, leverage, Cook's D and diagnostics.",
              (Field("predictors", "Predictor rows (without intercept)", "matrix"),
               Field("response", "Response measurements", "vector")),
              {"predictors": [[i, i*i] for i in range(1, 12)],
               "response": [i + .2*i*i + (-1)**i for i in range(1, 12)]}, _ols_model, headline="adjusted_r_squared"),
    Operation("binary_logistic_regression", "Binary logistic regression (IRLS)",
              "Binomial maximum likelihood by damped IRLS with Wald odds ratios/95% CI and separation detection.",
              (Field("predictors", "Predictor rows (without intercept)", "matrix"),
               Field("outcome", "Binary responses (0 or 1)", "vector")),
              {"predictors": [[i] for i in range(1, 11)],
               "outcome": [0, 0, 1, 0, 1, 0, 1, 1, 0, 1]}, _logistic, headline="odds_ratio_1"),
    Operation("bootstrap_confidence_interval", "Bootstrap percentile and BCa intervals",
              "Seeded iid nonparametric bootstrap of the mean, median or sample standard deviation; jackknife BCa.",
              (Field("sample", "Independent observations", "vector"),
               Field("statistic", "Statistic: mean, median, standard_deviation", "text"),
               Field("replicates", "Number of bootstrap replicates (200-20000)", "integer"),
               Field("seed", "Pseudorandom generator seed", "integer"),
               Field("confidence_level", "Confidence level in (0,1)", "number")),
              {"sample": [1, 2, 3, 4, 5, 7, 10, 11, 16, 21], "statistic": "mean",
               "replicates": 2000, "seed": 42, "confidence_level": .95}, _bootstrap_ci, headline="estimate"),
    Operation("t_test_power", "t-test power and sample size",
              "Noncentral-t power for one-sample, paired or equal-group two-sample t tests; minimal target-power sample size.",
              (Field("effect_size", "Standardized effect magnitude (Cohen's d)", "number"),
               Field("alpha", "Significance level", "number"),
               Field("target_power", "Desired power", "number"),
               Field("sample_size", "Observations per group/pairs", "integer"),
               Field("design", "one_sample, paired, or two_sample", "text"),
               Field("tails", "one_sided or two_sided", "text")),
              {"effect_size": .5, "alpha": .05, "target_power": .8,
               "sample_size": 30, "design": "two_sample", "tails": "two_sided"}, _t_test_power, headline="achieved_power"),
    Operation("proportion_power", "Proportion z-test power and sample size",
              "Normal-approximation power for one-sample proportion against p0 or two independent equal-sized proportions.",
              (Field("proportion_a", "Alternative proportion p1", "number"),
               Field("proportion_b", "Null p0 or second proportion p2", "number"),
               Field("alpha", "Significance level", "number"),
               Field("target_power", "Desired power", "number"),
               Field("sample_size", "Observations per group", "integer"),
               Field("design", "one_sample or two_sample", "text"),
               Field("tails", "one_sided or two_sided", "text")),
              {"proportion_a": .6, "proportion_b": .4, "alpha": .05,
               "target_power": .8, "sample_size": 100, "design": "two_sample", "tails": "two_sided"}, _proportion_power, headline="achieved_power"),
    Operation("one_way_anova", "One-way ANOVA with Tukey HSD",
              "Fixed-effects one-way ANOVA with Tukey-Kramer pairwise familywise-adjusted tests and simultaneous confidence limits.",
              (Field("values", "Response measurements", "vector"), Field("groups", "Numeric group codes", "vector")),
              {"values": [7, 8, 9, 10, 11, 12, 9, 11, 13], "groups": [1, 1, 1, 2, 2, 2, 3, 3, 3]}, _one_way_anova, headline="p_value"),
    Operation("mann_whitney_u", "Mann-Whitney U",
              "Independent two-sample two-sided rank test; exact conditional distribution through 30 pooled observations.",
              (Field("sample_a", "Independent sample A", "vector"), Field("sample_b", "Independent sample B", "vector")),
              {"sample_a": [1, 3, 5, 7], "sample_b": [2, 4, 6, 8]}, _mann_whitney, headline="p_value"),
    Operation("wilcoxon_signed_rank", "Wilcoxon signed-rank",
              "Paired two-sided signed-rank test, excluding zero differences; exact conditional distribution through 30 nonzero pairs.",
              (Field("before", "Before measurements", "vector"), Field("after", "After measurements", "vector")),
              {"before": [2, 4, 6, 8, 10, 12], "after": [3, 6, 9, 12, 15, 18]}, _wilcoxon, headline="p_value"),
    Operation("shapiro_wilk", "Shapiro-Wilk normality",
              "Royston's AS R94 approximation to Shapiro-Wilk W; 3 to 1024 observations (workbench input limit).",
              (Field("sample", "Sample measurements", "vector"),),
              {"sample": [148, 154, 158, 160, 161, 162, 166, 170, 182, 195, 236]}, _shapiro, headline="p_value"),
    Operation("pearson_correlation", "Pearson correlation and 95% CI",
              "Two-sided Pearson r and Fisher-z 95% confidence interval.",
              (Field("x", "Paired x values", "vector"), Field("y", "Paired y values", "vector")),
              {"x": [1, 2, 3, 4, 5, 6], "y": [2, 5, 3, 8, 6, 9]}, _pearson, headline="r"),
    Operation("spearman_correlation", "Spearman correlation and 95% CI",
              "Two-sided Spearman rho with exact permutation p through 8 pairs and approximate Fisher-z 95% CI.",
              (Field("x", "Paired x values", "vector"), Field("y", "Paired y values", "vector")),
              {"x": [1, 2, 3, 4, 5, 6], "y": [2, 1, 4, 3, 6, 5]}, _spearman, headline="rho"),
    Operation("bonferroni_correction", "Bonferroni p-value correction",
              "Controls familywise error by multiplying each p-value by the hypothesis count.",
              (Field("p_values", "Raw p-values", "vector"), Field("alpha", "Significance threshold", "number")),
              {"p_values": [.001, .02, .04, .3], "alpha": .05}, _bonferroni, headline="rejections"),
    Operation("benjamini_hochberg_correction", "Benjamini-Hochberg FDR correction",
              "Step-up monotone adjusted p-values for FDR control under independence or PRDS dependence.",
              (Field("p_values", "Raw p-values", "vector"), Field("alpha", "Significance threshold", "number")),
              {"p_values": [.001, .02, .04, .3], "alpha": .05}, _benjamini_hochberg, headline="rejections"),
)
