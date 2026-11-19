"""Hill diversity, multisite beta diversity and sample-based accumulation curves.

These deterministic, standard-library operations extend the existing Biology and
Ecology pack without changing any previously shipped operation contracts.
Site matrices use rows as sampling units and columns as aligned taxa.
"""

from __future__ import annotations

import math

from graphlink_plugins.scientific.core import Citation, Field, Operation

_MAX_COUNT = 2**53 - 1


def _counts(values):
    if not values or any(
        type(n) not in (int, float) or not math.isfinite(n)
        or n < 0 or n > _MAX_COUNT or n != int(n)
        for n in values
    ):
        raise ValueError("Taxon abundances must be nonnegative exact whole-number counts below 2^53.")
    return [int(n) for n in values]


def _sites(matrix, *, maximum, nonempty_sites):
    if not isinstance(matrix, list) or not 2 <= len(matrix) <= maximum:
        raise ValueError(f"Supply 2–{maximum} sampling units, one row per site or survey.")
    if not isinstance(matrix[0], list) or not 1 <= len(matrix[0]) <= 32:
        raise ValueError("Each survey needs 1–32 aligned taxon columns.")
    width = len(matrix[0])
    if len(matrix) * width > 4096 or any(
        not isinstance(row, list) or len(row) != width for row in matrix
    ):
        raise ValueError("The taxon count matrix must be rectangular with at most 4,096 cells.")
    counts = [_counts(row) for row in matrix]
    totals = [sum(row) for row in counts]
    if sum(totals) > _MAX_COUNT:
        raise ValueError("Total individuals must be below 2^53 for exact graph reuse.")
    if sum(totals) == 0:
        raise ValueError("At least one individual must have been observed.")
    if nonempty_sites and any(total == 0 for total in totals):
        raise ValueError("Beta diversity requires at least one individual at each site.")
    return counts, totals


def _hill_number(observed, total, order):
    """Effective richness ^qD, with a Shannon limit and stable log-sum-exp."""
    richness = len(observed)
    if order == 0:
        return richness
    if order == 1:
        entropy = math.fsum(
            (n / total) * math.log1p((total - n) / n) for n in observed
        )
        return math.exp(entropy)
    if order == 2:
        return total * total / sum(n * n for n in observed)

    probabilities = [n / total for n in observed]
    logs = [math.log(p) for p in probabilities]
    distance = order - 1
    if abs(distance) < 1e-5:
        # The cumulant expansion resolves orders very near q=1 instead of
        # dividing two nearly equal logarithms (0/0 at the Shannon limit).
        mean = math.fsum(p * logp for p, logp in zip(probabilities, logs))
        variance = math.fsum(p * (logp - mean)**2
                             for p, logp in zip(probabilities, logs))
        return math.exp(-mean - distance * variance / 2)
    exponents = [distance * logp for logp in logs]
    largest = max(exponents)
    weighted = math.fsum(
        p * math.exp(exp_value - largest)
        for p, exp_value in zip(probabilities, exponents)
    )
    return math.exp(-(largest + math.log(weighted)) / distance)


def hill_numbers(data):
    counts = _counts(data["counts"])
    total = sum(counts)
    if not 0 < total <= _MAX_COUNT:
        raise ValueError("Hill diversity requires at least one individual and a total below 2^53.")
    order = data["order_q"]
    if not math.isfinite(order) or not 0 <= order <= 10:
        raise ValueError("Hill order q must be between 0 and 10.")
    observed = [n for n in counts if n]
    q0 = len(observed)
    q1 = _hill_number(observed, total, 1)
    q2 = _hill_number(observed, total, 2)
    selected = _hill_number(observed, total, order)
    maximum = max(2, order)
    grid = sorted({0.0, 1.0, 2.0, order,
                   *(maximum * index / 24 for index in range(25))})
    rows = [[q, _hill_number(observed, total, q)] for q in grid]
    return {
        "summary": f"{q0} observed taxa among {total} individuals; Hill effective richness "
                   f"q=0: {q0}, q=1: {q1:.6g}, q=2: {q2:.6g}; "
                   f"at q={order:g}: {selected:.6g} taxa.",
        "values": {
            "individuals": total, "individuals_unit": "individuals",
            "observed_taxa": q0, "observed_taxa_unit": "taxa",
            "hill_q0": q0, "hill_q1": q1, "hill_q2": q2,
            "hill_selected": selected, "hill_numbers_unit": "effective taxa",
            "selected_order_q": order, "order_unit": "1 (dimensionless)",
        },
        "table": {
            "columns": ["Hill order q (dimensionless)", "Effective richness (taxa)"],
            "rows": rows,
        },
        "plot": {
            "title": "Hill diversity profile",
            "xLabel": "Hill order q (dimensionless)",
            "yLabel": "Effective richness (taxa)",
            "x": [row[0] for row in rows], "y": [row[1] for row in rows],
        },
    }


def beta_diversity(data):
    sites, totals = _sites(data["site_abundances"], maximum=32, nonempty_sites=True)
    site_count = len(sites)
    alpha = [sum(n > 0 for n in row) for row in sites]
    gamma = sum(any(row[k] > 0 for row in sites) for k in range(len(sites[0])))
    mean_alpha = math.fsum(alpha) / site_count
    rows = []
    for i, a in enumerate(sites):
        for j in range(i + 1, site_count):
            b = sites[j]
            shared = sum(x > 0 and y > 0 for x, y in zip(a, b))
            union = alpha[i] + alpha[j] - shared
            jaccard = 1 - shared / union
            sorensen = 1 - 2 * shared / (alpha[i] + alpha[j])
            bray_curtis = sum(abs(x - y) for x, y in zip(a, b)) / (totals[i] + totals[j])
            rows.append([i + 1, j + 1, jaccard, sorensen, bray_curtis])
    mean_jaccard = math.fsum(row[2] for row in rows) / len(rows)
    mean_sorensen = math.fsum(row[3] for row in rows) / len(rows)
    mean_bray = math.fsum(row[4] for row in rows) / len(rows)
    whittaker = gamma / mean_alpha
    return {
        "summary": f"Across {site_count} sites, pooled richness is {gamma} taxa and mean site "
                   f"richness is {mean_alpha:.6g} taxa; Whittaker beta is "
                   f"{whittaker:.6g}, mean Bray–Curtis dissimilarity {mean_bray:.6g}.",
        "values": {
            "sites": site_count, "sites_unit": "sites",
            "gamma_richness": gamma, "mean_alpha_richness": mean_alpha,
            "additive_beta": gamma - mean_alpha, "richness_unit": "taxa",
            "whittaker_beta": whittaker, "whittaker_beta_unit": "effective sites",
            "site_pairs": len(rows), "site_pairs_unit": "pairs",
            "mean_jaccard_dissimilarity": mean_jaccard,
            "mean_sorensen_dissimilarity": mean_sorensen,
            "mean_bray_curtis_dissimilarity": mean_bray,
            "dissimilarity_unit": "1 (dimensionless)",
        },
        "table": {
            "columns": [
                "Site A (index)", "Site B (index)",
                "Jaccard dissimilarity (dimensionless)",
                "Sørensen dissimilarity (dimensionless)",
                "Bray–Curtis dissimilarity (dimensionless)",
            ],
            "rows": rows,
        },
        "plot": {
            "title": "Pairwise Bray–Curtis dissimilarity",
            "xLabel": "Site pair (ordinal)", "yLabel": "Bray–Curtis dissimilarity (dimensionless)",
            "x": list(range(1, len(rows) + 1)), "y": [row[4] for row in rows],
        },
    }


def species_accumulation(data):
    sites, totals = _sites(data["site_abundances"], maximum=256, nonempty_sites=False)
    n_sites = len(sites)
    n_taxa = len(sites[0])
    incidence = [sum(row[t] > 0 for row in sites) for t in range(n_taxa)]
    observed_richness = sum(f > 0 for f in incidence)
    seen = [False] * n_taxa
    cumulative = []
    for row in sites:
        for t, n in enumerate(row):
            if n:
                seen[t] = True
        cumulative.append(sum(seen))

    # Each taxon occurs in f of K sampling units. For m uniformly chosen
    # distinct units, P(absent) = C(K-f,m)/C(K,m). Update that probability
    # recursively: P_m = P_(m-1) * (K-f-m+1)/(K-m+1).
    absent = [1.0] * n_taxa
    expected = []
    for m in range(1, n_sites + 1):
        for t, frequency in enumerate(incidence):
            if frequency:
                absent[t] = (absent[t] * (n_sites - frequency - m + 1) / (n_sites - m + 1)
                             if m <= n_sites - frequency else 0.0)
        expected.append(math.fsum(1 - absent[t] for t in range(n_taxa) if incidence[t]))
    expected[-1] = float(observed_richness)
    rows = [[m, cumulative[m - 1], expected[m - 1]] for m in range(1, n_sites + 1)]
    return {
        "summary": f"{observed_richness} taxa across {n_sites} survey units. "
                   "The sample-based expected accumulation curve averages over all "
                   "sample orders without replacement (not extrapolation).",
        "values": {
            "sampling_units": n_sites, "sampling_units_unit": "surveys",
            "individuals": sum(totals), "individuals_unit": "individuals",
            "observed_richness": observed_richness, "richness_unit": "taxa",
            "first_ordered_richness": cumulative[0],
            "expected_richness_one_sample": expected[0],
            "expected_richness_all_samples": expected[-1],
        },
        "table": {
            "columns": [
                "Samples accumulated (surveys)",
                "Observed richness, input order (taxa)",
                "Expected richness, randomized order (taxa)",
            ],
            "rows": rows,
        },
        "plot": {
            "title": "Sample-based species accumulation (expected over orders)",
            "xLabel": "Sampling units accumulated (surveys)",
            "yLabel": "Expected observed richness (taxa)",
            "x": list(range(1, n_sites + 1)), "y": expected,
        },
    }


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "hill_numbers", "Hill numbers (effective species richness)",
        "Calculate q=0, q=1, q=2 and a selected nonnegative order, with a Hill diversity profile.",
        (Field("counts", "Individuals per taxon", "vector", unit="individuals per taxon"),
         Field("order_q", "Selected diversity order q (0–10)", "number", unit="1 (dimensionless)")),
        {"counts": [12, 6, 2, 0], "order_q": 1}, hill_numbers,
        methods=(
            "For observed taxa with pᵢ=nᵢ/N, compute Hill ^qD=(Σpᵢ^q)^(1/(1−q)), "
            "q≠1; at q=1 use exp(−Σpᵢ ln pᵢ). The special orders are q=0 "
            "(observed taxa), q=1 (exp Shannon entropy), q=2 (inverse Simpson "
            "concentration). Zero-abundance taxa are omitted. Selected q must "
            "be within [0,10]; profile values and the result are effective taxon "
            "counts, not Shannon nats or the Gini–Simpson index."
        ),
        citations=(Citation(
            "Hill (1973), Diversity and Evenness: A Unifying Notation and Its Consequences, Ecology 54:427–432",
            "https://doi.org/10.2307/1934352",
        ),),
    ),
    Operation(
        "beta_diversity", "Beta diversity across sites",
        "Multisite Whittaker turnover and pairwise Jaccard, Sørensen and Bray–Curtis dissimilarity.",
        (Field("site_abundances", "Rows: sites; columns: aligned taxa", "matrix",
               unit="individuals per taxon per site"),),
        {"site_abundances": [[3, 2, 0, 0], [1, 0, 4, 0], [0, 1, 2, 1]]},
        beta_diversity,
        methods=(
            "For 2–32 nonempty sites with aligned taxon columns, compute "
            "gamma (pooled presence richness), mean alpha (arithmetic mean "
            "site richness), multiplicative Whittaker beta gamma/alpha and "
            "additive beta gamma−alpha. For each site pair report "
            "Jaccard dissimilarity 1−shared/union, Sørensen dissimilarity "
            "1−2 shared/(Sₐ+Sᵦ) and abundance Bray–Curtis dissimilarity "
            "Σ|nₐᵢ−nᵦᵢ|/(Nₐ+Nᵦ). Pairwise means are unweighted, and "
            "abundance comparisons assume comparable survey effort."
        ),
        citations=(
            Citation(
                "Whittaker (1960), Vegetation of the Siskiyou Mountains, Ecological Monographs 30:279–338",
                "https://doi.org/10.2307/1943563",
            ),
            Citation(
                "Bray & Curtis (1957), An Ordination of Upland Forest Communities, Ecological Monographs 27:325–349",
                "https://doi.org/10.2307/1942268",
            ),
        ),
    ),
    Operation(
        "species_accumulation", "Species accumulation by sample",
        "Observed survey-order richness and exact expected sample-based accumulation across random orders.",
        (Field("site_abundances", "Rows: ordered surveys; columns: aligned taxa", "matrix",
               unit="individuals per taxon per survey"),),
        {"site_abundances": [[2, 0, 0, 0], [0, 3, 1, 0], [1, 0, 2, 4], [0, 1, 0, 0]]},
        species_accumulation,
        methods=(
            "For 2–256 ordered survey units with aligned taxa, return the "
            "cumulative number of taxa observed in input order. For each "
            "sample count m, compute the exact random-order expectation "
            "Σᵢ[1−C(K−fᵢ,m)/C(K,m)], where K is the total survey count "
            "and fᵢ is the number of surveys containing taxon i. The "
            "probabilities use a stable sequential ratio; this is "
            "sample-based incidence rarefaction, not individual-based "
            "rarefaction, a confidence interval, or extrapolation. Empty "
            "sampling units are included in sampling effort."
        ),
        citations=(Citation(
            "Gotelli & Colwell (2001), Quantifying Biodiversity: Procedures and Pitfalls, Ecology Letters 4:379–391",
            "https://doi.org/10.1046/j.1461-0248.2001.00230.x",
        ),),
    ),
)
