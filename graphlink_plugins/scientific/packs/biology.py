"""Descriptive ecology for observed community counts and quadrat surveys."""

import math

from graphlink_plugins.scientific.core import Field, Operation


def _counts(values):
    if not values or any(v < 0 or int(v) != v or v > 2**53 - 1 for v in values):
        raise ValueError("Counts must be nonnegative whole numbers below 2^53 in a nonempty series.")
    return [int(v) for v in values]


def community_diversity(data):
    counts = _counts(data["counts"])
    total = sum(counts)
    if total == 0:
        raise ValueError("At least one individual must have been observed.")
    proportions = [n / total for n in counts if n]
    richness = len(proportions)
    shannon = math.fsum(-p * math.log(p) for p in proportions)
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
    total_count = sum(counts)
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


OPERATIONS = (
    Operation(
        "community_diversity",
        "Community diversity",
        "Calculate observed richness, Shannon entropy (natural logarithm), and Gini–Simpson "
        "diversity (1 − Σp²) from counts by taxon. Zero-count taxa contribute no weight.",
        (Field("counts", "Individuals observed per taxon", "vector", unit="individuals"),),
        {"counts": [12, 6, 2, 0]},
        community_diversity,
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
    ),
)
