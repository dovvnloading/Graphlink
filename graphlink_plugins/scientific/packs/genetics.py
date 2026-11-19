"""Mendelian genetics and linkage calculations for the Biology Workbench.

All calculations use the standard library. Counts represent independent, fully
classified offspring/genotypes; no missing-data or penetrance model is assumed.
"""

from __future__ import annotations

import math

from graphlink_plugins.scientific.core import Citation, Field, Operation


_MAX_COUNT = 2**53 - 1
_MAX_EXACT_BINOMIAL = 10_000
_MAX_EXACT_HWE = 100_000
_HAPLOTYPES = ("ABC", "ABc", "AbC", "Abc", "aBC", "aBc", "abC", "abc")


def _counts(values, *, label, size=None):
    if (not values or (size is not None and len(values) != size)
            or any(not math.isfinite(x) or x < 0 or x > _MAX_COUNT or x != int(x) for x in values)):
        raise ValueError(f"{label} requires nonnegative whole-number counts"
                         + (f" in {size} classes." if size is not None else "."))
    result = [int(x) for x in values]
    if sum(result) > _MAX_COUNT:
        raise ValueError(f"{label} exceeds the exact-count limit (2^53 - 1).")
    return result


def _observations(observed, minimum=2, maximum=16):
    if not minimum <= len(observed) <= maximum:
        raise ValueError(f"Expected between {minimum} and {maximum} offspring classes.")
    counts = _counts(observed, label="Observed offspring")
    if not sum(counts):
        raise ValueError("At least one offspring must be observed.")
    return counts


def _ratios(ratios, count):
    if (len(ratios) != count or any(not math.isfinite(x) or x <= 0 for x in ratios)):
        raise ValueError("Expected ratios must contain one strictly positive weight per class.")
    scale = max(ratios)
    weights = [x / scale for x in ratios]
    total = math.fsum(weights)
    if any(w == 0 for w in weights) or not math.isfinite(total):
        raise ValueError("Expected ratios cannot be resolved at numeric precision.")
    return [w / total for w in weights]


def _chi_square_sf(statistic, df):
    """Regularized upper incomplete gamma Q(df/2, chi2/2), no SciPy dependency."""
    if statistic == 0:
        return 1.0
    if df == 1:
        return math.erfc(math.sqrt(statistic / 2))
    a, x = df / 2, statistic / 2
    factor = math.exp(a * math.log(x) - x - math.lgamma(a))
    if x < a + 1:
        term = summation = 1 / a
        for i in range(1, 10_001):
            term *= x / (a + i)
            summation += term
            if abs(term) <= 1e-14 * summation:
                break
        return max(0.0, min(1.0, 1 - summation * factor))
    b, c = x + 1 - a, 1e300
    d = 1 / b
    h = d
    for i in range(1, 10_001):
        b += 2
        an = -i * (i - a)
        d = an * d + b
        c = b + an / c
        if abs(d) < 1e-300:
            d = 1e-300
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-14:
            break
    return max(0.0, min(1.0, factor * h))


def _logsumexp(weights):
    values = list(weights)
    largest = max(values)
    return largest + math.log(math.fsum(math.exp(v - largest) for v in values))


def _probability_ordered(log_weights, observed):
    """Two-sided exact tail: probability of every outcome at most as likely."""
    normalizer = _logsumexp(log_weights.values())
    threshold = log_weights[observed] + 1e-12  # include numerical ties
    log_tail = _logsumexp(w for w in log_weights.values() if w <= threshold) - normalizer
    return (min(1.0, math.exp(log_tail)), min(0.0, log_tail / math.log(10)),
            math.exp(log_weights[observed] - normalizer), normalizer)


def _observed_expected_table(counts, expected):
    return {
        "columns": ["Class", "Observed (offspring)", "Expected (offspring)"],
        "rows": [[i + 1, count, expectation]
                 for i, (count, expectation) in enumerate(zip(counts, expected))],
    }


def _expected_plot(expected, name):
    return {"title": name, "xLabel": "Class index", "yLabel": "Expected (offspring)",
            "x": list(range(1, len(expected) + 1)), "y": expected}


def segregation_chi_square(data):
    """Pearson's goodness-of-fit test with a user-specified Mendelian ratio."""
    observed = _observations(data["observed_counts"])
    probabilities = _ratios(data["expected_ratio"], len(observed))
    n = sum(observed)
    expected = [n * p for p in probabilities]
    chi_square = math.fsum((o - e)**2 / e for o, e in zip(observed, expected))
    if not math.isfinite(chi_square):
        raise ValueError("Segregation statistic exceeds the numeric range.")
    df = len(observed) - 1
    p_value = _chi_square_sf(chi_square, df)
    low = sum(e < 5 for e in expected)
    return {
        "summary": f"Pearson segregation test: χ²={chi_square:.6g}, df={df}, p={p_value:.6g}. "
                   + (f"Warning: {low} expected class count(s) below 5; the χ² approximation may be unreliable."
                      if low else "Expected counts meet the conventional 5-per-class guideline."),
        "values": {"offspring": n, "classes": len(observed), "chi_square": chi_square,
                   "degrees_of_freedom": df, "p_value": p_value,
                   "expected_count_below_5": low, "method": "Pearson chi-square, fixed ratio"},
        "table": _observed_expected_table(observed, expected),
        "plot": _expected_plot(expected, "Mendelian expected offspring counts"),
    }


def segregation_exact_binomial(data):
    """Probability-ordered two-sided binomial test for a two-class cross."""
    observed = _observations(data["observed_counts"], maximum=2)
    proportions = _ratios(data["expected_ratio"], 2)
    n = sum(observed)
    if n > _MAX_EXACT_BINOMIAL:
        raise ValueError("Exact segregation binomial tests support at most 10,000 offspring.")
    p = proportions[0]
    log_p, log_q = math.log(p), math.log1p(-p)
    # Evaluate in log-space so rare, strongly imbalanced results do not overflow.
    log_weights = {
        k: (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * log_p + (n - k) * log_q)
        for k in range(n + 1)
    }
    exact, log10_exact, observed_probability, _ = _probability_ordered(log_weights, observed[0])
    expected = [n * value for value in proportions]
    return {
        "summary": f"Exact two-sided binomial segregation test: {observed[0]}/{n} in class 1 "
                   f"(expected {p:.6g}); p={exact:.6g} (probability-ordered tail).",
        "values": {"offspring": n, "class_1_observed": observed[0],
                   "class_1_expected_probability": p, "two_sided_p_value": exact,
                   "log10_two_sided_p_value": log10_exact,
                   "observed_outcome_probability": observed_probability,
                   "method": "two-sided probability-ordered exact binomial"},
        "table": _observed_expected_table(observed, expected),
        "plot": _expected_plot(expected, "Expected offspring under the two-class ratio"),
    }


def hardy_weinberg_exact(data):
    """Wigginton-style recurrence conditional on minor-allele count and sample N."""
    aa, ab, bb = _counts([data["homozygote_aa"], data["heterozygote_ab"],
                          data["homozygote_bb"]], label="Genotypes", size=3)
    n = aa + ab + bb
    if not n:
        raise ValueError("Hardy–Weinberg testing requires at least one diploid genotype.")
    if n > _MAX_EXACT_HWE:
        raise ValueError("Hardy–Weinberg exact testing supports at most 100,000 genotypes.")
    a_count = 2 * aa + ab
    rare = min(a_count, 2 * n - a_count)
    common = 2 * n - rare
    mid = rare * common // (2 * n)
    if mid % 2 != rare % 2:
        mid += 1
    log_weights = {mid: 0.0}
    for h in range(mid, 1, -2):
        minor_hom, major_hom = (rare - h) // 2, (common - h) // 2
        log_weights[h - 2] = (log_weights[h] + math.log(h) + math.log(h - 1)
                              - math.log(4) - math.log(minor_hom + 1) - math.log(major_hom + 1))
    for h in range(mid, rare - 1, 2):
        minor_hom, major_hom = (rare - h) // 2, (common - h) // 2
        log_weights[h + 2] = (log_weights[h] + math.log(4) + math.log(minor_hom)
                              + math.log(major_hom) - math.log(h + 2) - math.log(h + 1))
    exact, log10_exact, observed_probability, normalizer = _probability_ordered(log_weights, ab)
    deficit = math.exp(_logsumexp(w for h, w in log_weights.items() if h <= ab) - normalizer)
    excess = math.exp(_logsumexp(w for h, w in log_weights.items() if h >= ab) - normalizer)
    mid_p = max(0.0, exact - observed_probability / 2)
    freq_a = a_count / (2 * n)
    expected = [n * freq_a**2, 2 * n * freq_a * (1 - freq_a), n * (1 - freq_a)**2]
    return {
        "summary": f"Conditional Hardy–Weinberg exact test: {n} diploid genotypes, "
                   f"allele A frequency {freq_a:.6g}, two-sided p={exact:.6g}. "
                   "The test conditions on the observed allele count (not a χ² approximation).",
        "values": {"individuals": n, "allele_a_frequency": freq_a,
                   "allele_b_frequency": 1 - freq_a, "observed_heterozygotes": ab,
                   "expected_heterozygotes": expected[1],
                   "two_sided_p_value": exact, "log10_two_sided_p_value": log10_exact,
                   "mid_p_value": mid_p, "heterozygote_deficit_p_value": min(1.0, deficit),
                   "heterozygote_excess_p_value": min(1.0, excess),
                   "observed_configuration_probability": observed_probability,
                   "method": "Wigginton conditional probability-ordered exact HWE"},
        "table": {"columns": ["Genotype", "Observed (individuals)", "Expected HWE (individuals)"],
                  "rows": [[label, count, est] for label, count, est in
                           zip(("AA", "AB", "BB"), (aa, ab, bb), expected)]},
        "plot": {"title": "Hardy–Weinberg expected genotype counts", "xLabel": "Genotype index (AA, AB, BB)",
                 "yLabel": "Expected (individuals)", "x": [1, 2, 3], "y": expected},
    }


def _distances(r):
    if not 0 <= r < 0.5:
        raise ValueError("Mapping requires a recombination fraction below 0.5; unlinked loci have no finite map distance.")
    return -50 * math.log1p(-2 * r), 25 * (math.log1p(2 * r) - math.log1p(-2 * r))


def map_distances(data):
    recombinant = _counts([data["recombinants"]], label="Recombinants", size=1)[0]
    total = _counts([data["total_offspring"]], label="Total offspring", size=1)[0]
    if total == 0 or recombinant > total:
        raise ValueError("Total offspring must be positive and at least as large as recombinants.")
    r = recombinant / total
    haldane, kosambi = _distances(r)
    return {
        "summary": f"Recombination fraction {r:.6g} ({recombinant}/{total}): "
                   f"Haldane {haldane:.6g} cM; Kosambi {kosambi:.6g} cM.",
        "values": {"recombinants": recombinant, "offspring": total,
                   "recombination_fraction": r, "recombination_percent": 100 * r,
                   "haldane_distance_cm": haldane, "kosambi_distance_cm": kosambi,
                   "distance_unit": "cM"},
        "table": {"columns": ["Mapping function", "Distance (cM)"],
                  "rows": [["Haldane", haldane], ["Kosambi", kosambi]]},
        "plot": {"title": "Map-function distance comparison", "xLabel": "Function index (Haldane, Kosambi)",
                 "yLabel": "Map distance (cM)", "x": [1, 2], "y": [haldane, kosambi]},
    }


def _bit(mask, pos):
    return (mask >> (2 - pos)) & 1


def three_point_cross(data):
    """Infer parental/DCO complementary pairs and crossover intervals from 8 classes."""
    observed = _counts(data["progeny_counts"], label="Three-point progeny", size=8)
    total = sum(observed)
    if total == 0:
        raise ValueError("Three-point analysis requires observed progeny.")
    pairs = [(i, 7 - i, observed[i] + observed[7 - i]) for i in range(4)]
    most = max(p[2] for p in pairs)
    least = min(p[2] for p in pairs)
    parent = [p for p in pairs if p[2] == most]
    dco = [p for p in pairs if p[2] == least]
    if most == least or len(parent) != 1 or len(dco) != 1:
        raise ValueError("Parental and double-crossover complementary pairs must be uniquely identifiable; "
                         "the class totals are ambiguous.")
    parent_mask = 7 - parent[0][0]
    dco_mask = 7 - dco[0][0]
    if (parent_mask ^ dco_mask).bit_count() != 1:
        dco_mask ^= 7  # the complementary DCO differs from a parent at the middle locus
    changed = parent_mask ^ dco_mask
    if changed.bit_count() != 1:
        raise ValueError("Double-crossover classes do not identify a single middle locus.")
    middle = next(pos for pos in range(3) if _bit(changed, pos))
    outer = [pos for pos in range(3) if pos != middle]
    order = (outer[0], middle, outer[1])
    labels = "ABC"
    phase_left = _bit(parent_mask, order[0]) ^ _bit(parent_mask, order[1])
    phase_right = _bit(parent_mask, order[1]) ^ _bit(parent_mask, order[2])
    groups = {"parental": 0, "single left": 0, "single right": 0, "double": 0}
    rows = []
    for i, count in enumerate(observed):
        mask = 7 - i
        left = (_bit(mask, order[0]) ^ _bit(mask, order[1])) != phase_left
        right = (_bit(mask, order[1]) ^ _bit(mask, order[2])) != phase_right
        category = ("double" if left and right else "single left" if left else
                    "single right" if right else "parental")
        groups[category] += count
        rows.append([_HAPLOTYPES[i], count, category, int(left), int(right)])
    if groups["parental"] != most or groups["double"] != least:
        raise ValueError("The inferred parental/DCO assignments conflict with crossover classification.")
    left_count = groups["single left"] + groups["double"]
    right_count = groups["single right"] + groups["double"]
    r_left, r_right = left_count / total, right_count / total
    haldane_left, kosambi_left = _distances(r_left)
    haldane_right, kosambi_right = _distances(r_right)
    expected_dco = left_count * r_right
    coefficient = groups["double"] / expected_dco if expected_dco else "undefined (zero expected DCO)"
    interference = 1 - coefficient if expected_dco else "undefined (zero expected DCO)"
    order_label = "-".join(labels[pos] for pos in order)
    return {
        "summary": f"Three-point testcross order {order_label}; intervals "
                   f"{100 * r_left:.6g} and {100 * r_right:.6g} recombination cM "
                   f"(DCO={groups['double']}, expected {expected_dco:.6g}). "
                   "Double crossovers are counted in both intervals.",
        "values": {"gene_order": order_label, "middle_gene": labels[middle],
                   "left_interval": f"{labels[order[0]]}-{labels[order[1]]}",
                   "right_interval": f"{labels[order[1]]}-{labels[order[2]]}",
                   "offspring": total, "parental_offspring": groups["parental"],
                   "single_crossovers_left": groups["single left"],
                   "single_crossovers_right": groups["single right"],
                   "double_crossovers": groups["double"], "expected_double_crossovers": expected_dco,
                   "left_recombination_fraction": r_left, "right_recombination_fraction": r_right,
                   "outer_recombination_fraction": (groups["single left"] + groups["single right"]) / total,
                   "left_distance_cm": 100 * r_left, "right_distance_cm": 100 * r_right,
                   "haldane_left_cm": haldane_left, "haldane_right_cm": haldane_right,
                   "kosambi_left_cm": kosambi_left, "kosambi_right_cm": kosambi_right,
                   "coefficient_of_coincidence": coefficient, "interference": interference,
                   "distance_unit": "cM"},
        "table": {"columns": ["Gamete class", "Offspring (count)", "Crossover class",
                              "Recombinant left (0/1)", "Recombinant right (0/1)"], "rows": rows},
        "plot": {"title": f"Three-point intervals ({order_label})",
                 "xLabel": "Interval index (left, right)", "yLabel": "Observed interval distance (cM)",
                 "x": [1, 2], "y": [100 * r_left, 100 * r_right]},
    }


def _lod(recombinant, total, theta):
    if theta == 0:
        return -math.inf if recombinant else total * math.log10(2)
    if theta == 0.5:
        return 0.0
    return (recombinant * math.log10(2 * theta)
            + (total - recombinant) * math.log10(2 * (1 - theta)))


def two_point_lod(data):
    recombinant, nonrecombinant = _counts([data["recombinants"], data["nonrecombinants"]],
                                        label="Informative meioses", size=2)
    total = recombinant + nonrecombinant
    if not total:
        raise ValueError("LOD scoring requires at least one fully informative meiosis.")
    theta = data["theta"]
    if not math.isfinite(theta) or not 0 <= theta <= 0.5:
        raise ValueError("The hypothesized recombination fraction θ must be between 0 and 0.5.")
    estimate = min(0.5, recombinant / total)
    max_lod = max(0.0, _lod(recombinant, total, estimate))
    at_theta = _lod(recombinant, total, theta)
    x = sorted({*(i / 200 for i in range(0 if not recombinant else 1, 101)),
                estimate, *((theta,) if math.isfinite(at_theta) else ())})
    y = [_lod(recombinant, total, value) for value in x]
    if any(not math.isfinite(v) for v in y):
        raise ValueError("LOD profile cannot be represented at this sample size.")
    stated = f"{at_theta:.6g}" if math.isfinite(at_theta) else "-infinity (recombinants at θ=0)"
    return {
        "summary": f"Two-point linkage LOD at θ={theta:.6g}: {stated}; "
                   f"maximum LOD={max_lod:.6g} at θ={estimate:.6g} "
                   f"from {recombinant} recombinant and {nonrecombinant} nonrecombinant meioses.",
        "values": {"recombinant_meioses": recombinant, "nonrecombinant_meioses": nonrecombinant,
                   "informative_meioses": total, "tested_theta": theta,
                   "lod_at_theta": at_theta if math.isfinite(at_theta) else "minus infinity",
                   "theta_mle": estimate, "maximum_lod": max_lod,
                   "method": "log10 binomial likelihood ratio vs theta=0.5; known phase"},
        "table": {"columns": ["Recombination fraction (θ)", "LOD (log10 odds)"],
                  "rows": [[value, score] for value, score in zip(x, y)]},
        "plot": {"title": "Two-point linkage LOD profile", "xLabel": "Recombination fraction (θ)",
                 "yLabel": "LOD (log10 odds)", "x": x, "y": y},
    }


OPERATIONS = (
    Operation(
        "segregation_chi_square", "Mendelian ratio χ² test",
        "Pearson goodness-of-fit for a fixed expected two-to-16-class segregation ratio.",
        (Field("observed_counts", "Offspring in each category", "vector", unit="1"),
         Field("expected_ratio", "Expected Mendelian category weights", "vector", unit="1")),
        {"observed_counts": [315, 108, 101, 32], "expected_ratio": [9, 3, 3, 1]},
        segregation_chi_square,
        methods="Pearson χ² goodness-of-fit; df=k−1. Fixed a priori category ratios, no fitted parameters.",
        citations=(Citation("NIST chi-square goodness-of-fit test",
                            "https://www.itl.nist.gov/div898/handbook/eda/section3/eda35f.htm"),),
    ),
    Operation(
        "segregation_exact_binomial", "Exact two-class segregation test",
        "Two-sided probability-ordered binomial test for a fixed Mendelian two-class ratio.",
        (Field("observed_counts", "Offspring in the two classes", "vector", unit="1"),
         Field("expected_ratio", "Expected two-class Mendelian weights", "vector", unit="1")),
        {"observed_counts": [78, 22], "expected_ratio": [3, 1]},
        segregation_exact_binomial,
        methods="Conditional exact binomial; include all outcomes with probability ≤ observed probability.",
        citations=(Citation("NIST binomial distribution",
                            "https://www.itl.nist.gov/div898/handbook/eda/section3/eda366i.htm"),),
    ),
    Operation(
        "hardy_weinberg_exact", "Hardy–Weinberg exact test",
        "Conditional probability-ordered test for diploid autosomal biallelic genotypes.",
        (Field("homozygote_aa", "AA genotypes", "integer", unit="1"),
         Field("heterozygote_ab", "AB genotypes", "integer", unit="1"),
         Field("homozygote_bb", "BB genotypes", "integer", unit="1")),
        {"homozygote_aa": 30, "heterozygote_ab": 60, "homozygote_bb": 10},
        hardy_weinberg_exact,
        methods="Wigginton et al. (2005) exact recurrence, conditioned on allele copies and sample size.",
        citations=(Citation("Wigginton, Cutler & Abecasis (2005)",
                            "https://pmc.ncbi.nlm.nih.gov/articles/PMC1199378/"),),
    ),
    Operation(
        "map_distances", "Haldane and Kosambi map distances",
        "Convert an observed two-locus recombinant fraction below 0.5 to both map functions (cM).",
        (Field("recombinants", "Recombinant offspring", "integer", unit="1"),
         Field("total_offspring", "Total scored offspring", "integer", unit="1")),
        {"recombinants": 35, "total_offspring": 100},
        map_distances,
        methods="Haldane 1919: −50 ln(1−2r); Kosambi 1944: 25 ln[(1+2r)/(1−2r)] in cM.",
        citations=(Citation("BatchMap: Haldane and Kosambi mapping functions",
                            "https://rdrr.io/cran/BatchMap/man/map_func.html"),),
    ),
    Operation(
        "three_point_cross", "Three-point testcross and interference",
        "Infer middle locus, parental/SCO/DCO classes, adjacent interval distances and interference.",
        (Field("progeny_counts", "ABC, ABc, AbC, Abc, aBC, aBc, abC, abc counts", "vector", unit="1"),),
        {"progeny_counts": [350, 95, 10, 45, 45, 10, 95, 350]},
        three_point_cross,
        methods="Complementary parental and DCO pair inference; recombination fractions include DCO twice.",
        citations=(Citation("OpenStax Biology: Chromosomal theory and genetic linkage",
                            "https://openstax.org/books/biology-2e/pages/13-1-chromosomal-theory-and-genetic-linkage"),),
    ),
    Operation(
        "two_point_lod", "Two-point linkage LOD profile",
        "Compute a LOD at a proposed θ and maximum over θ∈[0,0.5] for fully informative meioses.",
        (Field("recombinants", "Recombinant informative meioses", "integer", unit="1"),
         Field("nonrecombinants", "Nonrecombinant informative meioses", "integer", unit="1"),
         Field("theta", "Hypothesized recombination fraction", "number", unit="1")),
        {"recombinants": 3, "nonrecombinants": 7, "theta": 0.3},
        two_point_lod,
        methods="Z(θ)=log10{θ^R(1−θ)^NR/0.5^(R+NR)}; maximum at min(R/N,0.5).",
        citations=(Citation("NIH linkage analysis course, LOD scores",
                            "https://www.genome.gov/sites/default/files/genome-old/pages/Research/IntramuralResearch/DIRCalendar/CurrentTopicsinGenomeAnalysis2005/CourseHandouts/CTGA2005Lec11.grey.pdf"),),
    ),
)
