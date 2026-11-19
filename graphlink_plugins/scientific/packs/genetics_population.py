"""Population genetics operations for the grant-gated Genetics workbench pack.

Descriptive biallelic estimators, one seeded Wright–Fisher trajectory, and
variance-component/sex-ratio models. Standard library only; no inference of
phase, breeding structure, or variance components from raw observations.
"""

from __future__ import annotations

import math
import random

from graphlink_plugins.scientific.core import Citation, Field, Operation


_MAX_COUNT = 2**53 - 1
_UNDEFINED_LD = "undefined (monomorphic locus)"
_UNDEFINED_FIS = "undefined (zero within-population expected heterozygosity)"
_UNDEFINED_TOTAL = "undefined (pooled monomorphic locus)"


def _whole_counts(values, name, *, count=None):
    if (not values or (count is not None and len(values) != count)
            or any(not math.isfinite(v) or v < 0 or v > _MAX_COUNT or int(v) != v for v in values)):
        raise ValueError(f"{name} must contain nonnegative whole-number counts"
                         + (f" in {count} classes." if count is not None else "."))
    result = [int(v) for v in values]
    if sum(result) > _MAX_COUNT:
        raise ValueError(f"{name} total exceeds the exact-count limit (2^53 - 1).")
    return result


def f_statistics(data):
    """Heterozygosity-based F_IS, F_ST and F_IT with individual-count weighting."""
    populations = data["genotype_counts"]
    if not 2 <= len(populations) <= 200:
        raise ValueError("F-statistics require 2–200 populations.")
    rows = []
    total_n = total_het = total_a = total_b = 0
    heterozygosity_sum = []
    for i, group in enumerate(populations, 1):
        aa, ab, bb = _whole_counts(group, f"Population {i} genotypes", count=3)
        n = aa + ab + bb
        if n == 0:
            raise ValueError("Each population must contain at least one diploid individual.")
        total_n += n
        total_het += ab
        copies_a, copies_b = 2 * aa + ab, 2 * bb + ab
        total_a += copies_a
        total_b += copies_b
        p, q = copies_a / (2 * n), copies_b / (2 * n)
        observed, expected = ab / n, 2 * p * q
        heterozygosity_sum.append(n * expected)
        rows.append([i, aa, ab, bb, n, p, observed, expected])
    if total_n > _MAX_COUNT:
        raise ValueError("Combined genotype counts exceed the exact-count limit (2^53 - 1).")
    h_i = total_het / total_n
    h_s = math.fsum(heterozygosity_sum) / total_n
    p_bar, q_bar = total_a / (2 * total_n), total_b / (2 * total_n)
    h_t = 2 * p_bar * q_bar
    f_is = (h_s - h_i) / h_s if h_s > 0 else _UNDEFINED_FIS
    f_st = (h_t - h_s) / h_t if h_t > 0 else _UNDEFINED_TOTAL
    f_it = (h_t - h_i) / h_t if h_t > 0 else _UNDEFINED_TOTAL
    return {
        "summary": (f"Heterozygosity-based fixation indices for {len(rows)} populations "
                    f"({total_n} individuals): F_IS={f_is:.6g}, F_ST={f_st:.6g}, "
                    f"F_IT={f_it:.6g}." if isinstance(f_is, float) else
                    f"Heterozygosity-based fixation indices for {len(rows)} populations "
                    f"({total_n} individuals): F_IS={f_is}; F_ST={f_st}; F_IT={f_it}."),
        "values": {
            "populations": len(rows), "individuals": total_n,
            "allele_a_frequency": p_bar, "allele_b_frequency": q_bar,
            "observed_heterozygosity_hi": h_i, "expected_within_hs": h_s,
            "expected_total_ht": h_t, "f_is": f_is, "f_st": f_st, "f_it": f_it,
            "frequency_unit": "1 (dimensionless)",
            "method": "sample-size-weighted heterozygosity ratios, not Weir–Cockerham theta",
        },
        "table": {
            "columns": ["Population", "AA", "AB", "BB", "Individuals", "A frequency",
                        "Observed heterozygosity", "Expected heterozygosity"],
            "rows": rows,
        },
        "plot": {"title": "Allele A frequency by population", "xLabel": "Population index",
                 "yLabel": "Allele A frequency (dimensionless)",
                 "x": [row[0] for row in rows], "y": [row[5] for row in rows]},
    }


def linkage_disequilibrium(data):
    """Signed D' and r² from the four *phased* two-locus haplotypes."""
    ab_big, ab_a, ab_b, ab_small = counts = _whole_counts(
        data["haplotype_counts"], "Phased haplotypes", count=4,
    )
    n = sum(counts)
    if n == 0:
        raise ValueError("Linkage disequilibrium needs at least one observed haplotype.")
    a, small_a = ab_big + ab_a, ab_b + ab_small
    b, small_b = ab_big + ab_b, ab_a + ab_small
    numerator = ab_big * ab_small - ab_a * ab_b
    d = numerator / (n * n)
    denominator = a * small_a * b * small_b
    if denominator == 0:
        d_prime = r_squared = _UNDEFINED_LD
    else:
        largest = (min(a * small_b, small_a * b) if numerator >= 0
                   else min(a * b, small_a * small_b))
        d_prime = numerator / largest
        r_squared = numerator**2 / denominator
    p_a, p_b = a / n, b / n
    expected = [n * p_a * p_b, n * p_a * (1 - p_b),
                n * (1 - p_a) * p_b, n * (1 - p_a) * (1 - p_b)]
    return {
        "summary": (f"Phased two-locus LD from {n} haplotypes: D={d:.6g}; "
                    f"signed D′={d_prime}; r²={r_squared}. "
                    + ("At least one locus is fixed, so standardized LD is undefined."
                       if denominator == 0 else "Both loci are polymorphic.")),
        "values": {
            "haplotypes": n, "allele_a_frequency": p_a, "allele_b_frequency": p_b,
            "d": d, "d_prime_signed": d_prime, "r_squared": r_squared,
            "frequency_unit": "1 (dimensionless)",
            "method": "four phased haplotype frequencies; signed Lewontin D-prime",
        },
        "table": {"columns": ["Haplotype", "Observed (copies)", "Frequency", "Expected at linkage equilibrium (copies)"],
                  "rows": [[label, count, count / n, exp] for label, count, exp in
                           zip(("AB", "Ab", "aB", "ab"), counts, expected)]},
        "plot": {"title": "Observed phased haplotype frequencies", "xLabel": "Haplotype index (AB, Ab, aB, ab)",
                 "yLabel": "Haplotype frequency (dimensionless)",
                 "x": [1, 2, 3, 4], "y": [count / n for count in counts]},
    }


def _selected_frequency(p, w_aa, w_ab, w_bb):
    # In the absence of mutation, absent alleles cannot reappear. Handle the
    # absorbing boundaries before scaling: if the fitness of the only present
    # genotype underflows relative to an absent genotype, its frequency is
    # still exactly zero or one, not an undefined mean fitness.
    if p == 0 or p == 1:
        return p
    # Relative fitness scaling avoids overflowing wbar at large positive fitness.
    maximum = max(w_aa, w_ab, w_bb)
    aa, ab, bb = w_aa / maximum, w_ab / maximum, w_bb / maximum
    q = 1 - p
    wbar = p * p * aa + 2 * p * q * ab + q * q * bb
    if wbar <= 0:
        raise ValueError("Mean fitness underflowed; supply less disparate fitness weights.")
    return (p * p * aa + p * q * ab) / wbar


def wright_fisher_drift_selection(data):
    """Discrete diploid WF; viability selection then binomial gene-copy sampling."""
    n, copies = data["population_size"], data["initial_a_copies"]
    generations, seed = data["generations"], data["seed"]
    if not 1 <= n <= 5000 or not 0 <= copies <= 2 * n:
        raise ValueError("Wright–Fisher requires 1–5000 diploids and 0–2N initial A copies.")
    if not 1 <= generations <= 400:
        raise ValueError("Wright–Fisher supports 1–400 generations.")
    if not 0 <= seed <= 2**32 - 1:
        raise ValueError("The random seed must be an integer from 0 to 2^32−1.")
    fitness = (data["fitness_aa"], data["fitness_ab"], data["fitness_bb"])
    if any(not math.isfinite(v) or v <= 0 for v in fitness):
        raise ValueError("Relative genotype fitnesses must be strictly positive and finite.")
    random_draw = random.Random(seed).random
    initial_frequency = copies / (2 * n)
    frequencies = [initial_frequency]
    rows = []
    fixation_generation = 0 if copies in (0, 2 * n) else "not fixed"
    for generation in range(1, generations + 1):
        parental = copies / (2 * n)
        selected = _selected_frequency(parental, *fitness)
        # Independent Bernoulli draws are an exact Binomial(2N, selected).
        copies = sum(random_draw() < selected for _ in range(2 * n))
        frequency = copies / (2 * n)
        if fixation_generation == "not fixed" and copies in (0, 2 * n):
            fixation_generation = generation
        rows.append([generation, selected, copies, frequency])
        frequencies.append(frequency)
    return {
        "summary": (f"One seeded Wright–Fisher realization ({n} diploids, "
                    f"{generations} generations; seed {seed}): "
                    f"A frequency {initial_frequency:.6g} → {frequencies[-1]:.6g}. "
                    "Viability selection precedes binomial drift each generation."),
        "values": {
            "population_size": n, "generations": generations, "seed": seed,
            "initial_a_copies": data["initial_a_copies"], "final_a_copies": copies,
            "initial_a_frequency": initial_frequency, "final_a_frequency": frequencies[-1],
            "fixation_generation": fixation_generation, "frequency_unit": "1 (dimensionless)",
            "method": "diploid Wright–Fisher; viability fitness; seeded Binomial(2N, p_selected)",
        },
        "table": {"columns": ["Generation", "Selected parental A frequency", "Sampled A copies (2N)",
                              "Offspring A frequency"], "rows": rows},
        "plot": {"title": "Wright–Fisher drift and selection: A frequency", "xLabel": "Generation",
                 "yLabel": "Allele A frequency (dimensionless)",
                 "x": list(range(generations + 1)), "y": frequencies},
    }


def heritability_variance(data):
    """Variance-component H² and h²; components are inputs, not estimated here."""
    components = [data[key] for key in ("additive_variance", "dominance_variance",
                                        "epistatic_variance", "environmental_variance")]
    if any(not math.isfinite(v) or v < 0 for v in components):
        raise ValueError("Variance components must be finite and nonnegative.")
    try:
        total = math.fsum(components)
    except OverflowError:
        raise ValueError("Total phenotypic variance exceeds the numeric range.") from None
    if not 0 < total < math.inf:
        raise ValueError("Total phenotypic variance must be positive and finite.")
    unit = data["variance_unit"].strip()
    if not 1 <= len(unit) <= 40 or any(ord(c) < 32 for c in unit):
        raise ValueError("Variance unit must be a 1–40 character label.")
    additive, dominance, epistatic, environmental = components
    genetic = math.fsum(components[:3])
    narrow, broad = additive / total, genetic / total
    return {
        "summary": (f"Variance-component heritability: broad-sense H²={broad:.6g}, "
                    f"narrow-sense h²={narrow:.6g}; V_P={total:.6g} {unit}. "
                    "Components are supplied, not inferred from relatives or genotypes."),
        "values": {
            "additive_variance": additive, "dominance_variance": dominance,
            "epistatic_variance": epistatic, "environmental_variance": environmental,
            "genetic_variance": genetic, "phenotypic_variance": total,
            "broad_sense_h2": broad, "narrow_sense_h2": narrow,
            "variance_unit": unit, "heritability_unit": "1 (dimensionless)",
            "method": "V_P=V_A+V_D+V_I+V_E; no G×E or covariance components",
        },
        "table": {"columns": ["Variance source", f"Variance ({unit})", "Share of phenotypic variance"],
                  "rows": [[label, value, value / total] for label, value in zip(
                      ("Additive genetic", "Dominance genetic", "Epistatic genetic", "Environment"), components)]},
        "plot": {"title": "Phenotypic variance components", "xLabel": "Component index (A, D, I, E)",
                 "yLabel": "Share of phenotypic variance (dimensionless)",
                 "x": [1, 2, 3, 4], "y": [v / total for v in components]},
    }


def effective_population_size(data):
    """Breeding-sex-ratio Ne and idealized heterozygosity-retention projection."""
    males, females, generations = (data["breeding_males"], data["breeding_females"],
                                   data["generations"])
    if not 1 <= males <= _MAX_COUNT or not 1 <= females <= _MAX_COUNT:
        raise ValueError("Breeding males and females must both be positive integer counts.")
    breeders = males + females
    if breeders > _MAX_COUNT:
        raise ValueError("Combined breeders exceed the exact-count limit (2^53 - 1).")
    if not 1 <= generations <= 400:
        raise ValueError("Effective-size projections support 1–400 generations.")
    ne = 4 * males * females / breeders
    delta_f = 1 / (2 * ne)
    rate = math.log1p(-delta_f)
    rows = [[t, math.exp(t * rate), -math.expm1(t * rate)] for t in range(generations + 1)]
    return {
        "summary": (f"Unequal-breeder sex-ratio effective size N_e={ne:.6g} for "
                    f"{males} males and {females} females; expected relative "
                    f"heterozygosity after {generations} generations={rows[-1][1]:.6g}. "
                    "Idealized, constant-size random-mating approximation."),
        "values": {
            "breeding_males": males, "breeding_females": females, "breeders": breeders,
            "effective_population_size": ne, "ne_to_breeders_ratio": ne / breeders,
            "inbreeding_rate_per_generation": delta_f, "generations": generations,
            "relative_heterozygosity_remaining": rows[-1][1],
            "projected_inbreeding_from_zero": rows[-1][2],
            "size_unit": "diploid breeders", "frequency_unit": "1 (dimensionless)",
            "method": "sex-ratio Ne=4NmNf/(Nm+Nf), ΔF=1/(2Ne), constant Ne",
        },
        "table": {"columns": ["Generation", "H_t/H_0 (expected)", "F_t if F_0=0 (expected)"], "rows": rows},
        "plot": {"title": "Idealized retention of heterozygosity", "xLabel": "Generation",
                 "yLabel": "Expected H_t/H_0 (dimensionless)",
                 "x": [row[0] for row in rows], "y": [row[1] for row in rows]},
    }


OPERATIONS = (
    Operation(
        "f_statistics", "Population F-statistics (F_IS, F_ST, F_IT)",
        "Descriptive heterozygosity ratios for two or more populations at one biallelic diploid locus.",
        (Field("genotype_counts", "Rows of population counts [AA, AB, BB]", "matrix", unit="1"),),
        {"genotype_counts": [[20, 10, 20], [10, 30, 10]]}, f_statistics,
        methods=("N-weighted H_I = ΣAB_i/ΣN_i; H_S = ΣN_i·2p_iq_i/ΣN_i; "
                 "H_T=2p̄q̄, then F_IS=(H_S−H_I)/H_S, F_ST=(H_T−H_S)/H_T, F_IT=(H_T−H_I)/H_T. "
                 "Undefined zero-denominator indices stay labeled; not a bias-corrected θ estimator."),
        citations=(Citation("Wright's F-statistics: population and quantitative genetics notes",
                            "https://cooplab.github.io/popgen-notes/"),),
    ),
    Operation(
        "linkage_disequilibrium", "Two-locus linkage disequilibrium",
        "Compute D, signed D′ and r² from observed, phased AB/Ab/aB/ab haplotypes.",
        (Field("haplotype_counts", "Phased counts [AB, Ab, aB, ab]", "vector", unit="1"),),
        {"haplotype_counts": [40, 10, 10, 40]}, linkage_disequilibrium,
        methods=("D=p_AB−p_Ap_B; signed D′=D/D_max with sign-specific bound; "
                 "r²=D²/[p_A(1−p_A)p_B(1−p_B)]. Requires phased two-locus copies; "
                 "standardized LD undefined if either locus is fixed."),
        citations=(Citation("One Hundred Years of Linkage Disequilibrium, Genetics 209 (2018)",
                            "https://academic.oup.com/genetics/article/209/3/629/5931007"),),
    ),
    Operation(
        "wright_fisher_drift_selection", "Wright–Fisher drift and selection",
        "One reproducible diploid trajectory with genotype viability selection and binomial sampling.",
        (Field("population_size", "Diploid population size N", "integer", unit="1"),
         Field("initial_a_copies", "Initial allele A copies (0–2N)", "integer", unit="1"),
         Field("generations", "Simulated generations", "integer", unit="1"),
         Field("fitness_aa", "AA relative viability", "number", unit="1"),
         Field("fitness_ab", "AB relative viability", "number", unit="1"),
         Field("fitness_bb", "BB relative viability", "number", unit="1"),
         Field("seed", "Reproducible random seed", "integer", unit="1")),
        {"population_size": 100, "initial_a_copies": 100, "generations": 25,
         "fitness_aa": 1.1, "fitness_ab": 1.05, "fitness_bb": 1, "seed": 42},
        wright_fisher_drift_selection,
        methods=("Random-mating zygotes with p²/2pq/q² are viability-weighted by positive AA/AB/BB "
                 "relative fitnesses; draw next generation A copies from Binomial(2N,p_selected). "
                 "One seeded path, no mutation, migration, or population-size changes."),
        citations=(Citation("Mathematical Wright–Fisher model (diploid gene-copy sampling)",
                            "https://pmc.ncbi.nlm.nih.gov/articles/PMC4269093/"),),
    ),
    Operation(
        "heritability_variance", "Broad- and narrow-sense heritability",
        "Compute H² and h² from supplied additive, dominance, epistatic and environmental variances.",
        (Field("additive_variance", "Additive variance (declared unit)", "number", unit="selected variance unit"),
         Field("dominance_variance", "Dominance variance (declared unit)", "number", unit="selected variance unit"),
         Field("epistatic_variance", "Epistatic variance (declared unit)", "number", unit="selected variance unit"),
         Field("environmental_variance", "Environmental variance (declared unit)", "number", unit="selected variance unit"),
         Field("variance_unit", "Variance unit label (e.g. cm²)", "text", unit="unit label")),
        {"additive_variance": 4, "dominance_variance": 2, "epistatic_variance": 1,
         "environmental_variance": 3, "variance_unit": "cm²"},
        heritability_variance,
        methods=("V_P=V_A+V_D+V_I+V_E; broad H²=(V_A+V_D+V_I)/V_P and "
                 "narrow h²=V_A/V_P. Variance components are supplied, not estimated; "
                 "assumes zero covariance and no genotype×environment component."),
        citations=(Citation("Stanford Encyclopedia of Philosophy: Heritability and variance components",
                            "https://plato.stanford.edu/archives/spr2021/entries/heredity/"),),
    ),
    Operation(
        "effective_population_size", "Effective population size from breeder sex ratio",
        "Sex-ratio Ne and idealized loss of heterozygosity under constant random mating.",
        (Field("breeding_males", "Number of reproducing males", "integer", unit="1"),
         Field("breeding_females", "Number of reproducing females", "integer", unit="1"),
         Field("generations", "Projected generations", "integer", unit="1")),
        {"breeding_males": 20, "breeding_females": 80, "generations": 10},
        effective_population_size,
        methods=("For a dioecious, non-overlapping-generation ideal population, "
                 "N_e=4N_mN_f/(N_m+N_f); per-generation ΔF=1/(2N_e), "
                 "so expected H_t/H_0=(1−ΔF)^t. Ignores family-size variation, "
                 "overlap, selection and demographic fluctuations."),
        citations=(Citation("Wang, Santiago & Caballero (2016), Prediction and estimation of effective population size",
                            "https://www.nature.com/articles/hdy201643"),),
    ),
)
