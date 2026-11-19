"""Exploratory expression QC, ordination and gene-set over-representation.

Small, deterministic, standard-library methods. No external annotation databases or
count-likelihood modelling; inputs and reference universe are user supplied.
"""

from __future__ import annotations

import json
import math

from graphlink_plugins.scientific.core import Citation, Field, Operation
from graphlink_plugins.scientific.packs.linalg import _jacobi_symmetric


def _ids(text: str, label: str, *, limit: int = 1024) -> list[str]:
    try:
        items = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError(f"{label} must be a JSON array of unique nonempty strings.") from None
    if (not isinstance(items, list) or not 1 <= len(items) <= limit
            or any(not isinstance(item, str) or not item.strip() or len(item) > 80
                   for item in items) or len(set(items)) != len(items)):
        raise ValueError(f"{label} must contain 1–{limit} distinct nonempty IDs (at most 80 characters).")
    return items


def _pseudocount(data: dict) -> float:
    value = data["pseudocount"]
    if not 0 < value <= 1_000_000:
        raise ValueError("Pseudocount must be positive and at most 1,000,000 normalized counts.")
    return value


def _normalized(data: dict):
    # Lazy import: the legacy expression pack registers these operations and
    # imports EXTRA_OPERATIONS at the end of that module.
    from graphlink_plugins.scientific.packs.expression import _count_matrix, _normalize

    genes, counts, totals = _count_matrix(data)
    normalized, _, references = _normalize(counts, totals, data["method"])
    return genes, normalized, references


def ma_values(data: dict) -> dict:
    """M=log2(mean_B+p)-log2(mean_A+p); A=their half sum."""
    from graphlink_plugins.scientific.packs.expression import _group_indices

    genes, normalized, references = _normalized(data)
    width = len(normalized[0])
    a_name, b_name, a, b = _group_indices(data["sample_groups"], width)
    pseudo = _pseudocount(data)
    rows = []
    for gene, values in zip(genes, normalized):
        mean_a = math.fsum(values[i] for i in a) / len(a)
        mean_b = math.fsum(values[i] for i in b) / len(b)
        log_a, log_b = math.log2(mean_a + pseudo), math.log2(mean_b + pseudo)
        rows.append([gene, mean_a, mean_b, (log_a + log_b) / 2, log_b - log_a])
    return {
        "summary": (f"MA values for {a_name} (n={len(a)}) vs {b_name} (n={len(b)}): "
                    "M is B−A log2 ratio of group mean normalized counts with pseudocount; "
                    "A is their mean log2 abundance. Descriptive only, without a significance test."),
        "values": {"genes": len(genes), "samples": width,
                   "group_a": a_name, "group_b": b_name,
                   "n_group_a": len(a), "n_group_b": len(b),
                   "median_ratio_reference_genes": references},
        "table": {"columns": ["gene_id", "mean_normalized_group_a", "mean_normalized_group_b",
                              "ma_a_mean_log2", "ma_m_log2_b_vs_a"], "rows": rows},
    }


def sample_correlation(data: dict) -> dict:
    """Pearson correlations of log2-normalized profiles; average-linkage on 1-r."""
    genes, normalized, references = _normalized(data)
    width = len(normalized[0])
    sample_ids = _ids(data["sample_ids"], "Sample IDs", limit=32)
    if len(sample_ids) != width:
        raise ValueError("Supply one unique sample ID per matrix column.")
    if len(genes) < 2:
        raise ValueError("Sample correlations need at least two genes.")
    pseudo = _pseudocount(data)
    vectors = [[math.log2(row[j] + pseudo) for row in normalized] for j in range(width)]
    centered = [[x - math.fsum(vector) / len(vector) for x in vector] for vector in vectors]
    sums = [math.fsum(x*x for x in vector) for vector in centered]
    if any(v <= 0 for v in sums):
        raise ValueError("Sample correlation is undefined for a constant log2-expression profile.")
    correlation = [[0.0] * width for _ in range(width)]
    for i in range(width):
        correlation[i][i] = 1.0
        for j in range(i + 1, width):
            r = math.fsum(x*y for x, y in zip(centered[i], centered[j])) / math.sqrt(sums[i]*sums[j])
            correlation[i][j] = correlation[j][i] = max(-1.0, min(1.0, r))

    # Use average pairwise distance across all original sample pairs, not
    # unweighted averaging of two previously merged cluster distances.
    clusters = {i: [i] for i in range(width)}
    linkage = []
    for step in range(width - 1):
        best = None
        keys = sorted(clusters)
        for i, left in enumerate(keys):
            for right in keys[i + 1:]:
                la, lb = clusters[left], clusters[right]
                distance = math.fsum(1 - correlation[a][b] for a in la for b in lb) / (len(la)*len(lb))
                candidate = (distance, min(la), min(lb), left, right)
                if best is None or candidate < best:
                    best = candidate
        if best is None:
            raise ValueError("Hierarchical clustering requires at least two samples.")
        distance, _, _, left, right = best
        # Orient each pair by its earliest input leaf for stable dendrogram order.
        if min(clusters[left]) > min(clusters[right]):
            left, right = right, left
        merged = clusters.pop(left) + clusters.pop(right)
        clusters[width + step] = merged
        linkage.append([left, right, distance, len(merged)])
    order = next(iter(clusters.values()))
    return {
        "summary": (f"Pearson sample correlations across {len(genes)} genes on log2({data['method']} "
                    f"normalized counts + {pseudo}); hierarchical average-linkage clustering "
                    "of 1−r distances. Matrix rows and columns follow the dendrogram leaf order."),
        "values": {"genes": len(genes), "samples": width,
                   "median_ratio_reference_genes": references,
                   "sample_ids_input_order": json.dumps(sample_ids),
                   "cluster_order": json.dumps([sample_ids[i] for i in order]),
                   "linkage": json.dumps(linkage), "distance": "1 - Pearson r",
                   "linkage_method": "average"},
        "table": {"columns": ["sample_id", *[f"correlation_to_{sample_ids[j]}" for j in order]],
                  "rows": [[sample_ids[i], *[correlation[i][j] for j in order]] for i in order]},
    }


def expression_pca(data: dict) -> dict:
    """Centered (not variance-scaled) gene features; samples are observations."""
    genes, normalized, references = _normalized(data)
    width = len(normalized[0])
    sample_ids = _ids(data["sample_ids"], "Sample IDs", limit=32)
    if len(sample_ids) != width:
        raise ValueError("Supply one unique sample ID per matrix column.")
    pseudo = _pseudocount(data)
    expression = [[math.log2(value + pseudo) for value in row] for row in normalized]
    centered = [[x - math.fsum(row) / width for x in row] for row in expression]
    gram = [[math.fsum(row[i]*row[j] for row in centered) / (width - 1)
             for j in range(width)] for i in range(width)]
    eigenvalues, eigenvectors, _, _ = _jacobi_symmetric(gram)
    total = math.fsum(max(0.0, value) for value in eigenvalues)
    if total <= 0:
        raise ValueError("Expression PCA needs variation between the normalized samples.")
    # A blanket 1e-12 relative cutoff discarded real, numerically resolvable
    # lower-variance PCs. Suppress only roundoff-scale Gram eigenvalues.
    tolerance = 64 * math.ulp(1.0) * total
    variances = [max(0.0, value) if value > tolerance else 0.0 for value in eigenvalues]
    scores = [[0.0, 0.0] for _ in range(width)]
    for pc in range(2):
        if variances[pc] == 0:
            continue
        scale = math.sqrt(variances[pc] * (width - 1))
        sign_anchor = max(range(width), key=lambda i: abs(eigenvectors[i][pc]))
        sign = 1 if eigenvectors[sign_anchor][pc] >= 0 else -1
        for sample in range(width):
            scores[sample][pc] = sign * eigenvectors[sample][pc] * scale
    return {
        "summary": (f"Sample PCA of {len(genes)} log2({data['method']} normalized count + {pseudo}) "
                    "gene features, centered across samples without feature scaling; "
                    "PC signs are oriented deterministically, not biologically meaningful."),
        "values": {"genes": len(genes), "samples": width,
                   "median_ratio_reference_genes": references,
                   "total_variance": total, "pc1_variance": variances[0],
                   "pc2_variance": variances[1],
                   "pc1_explained_fraction": variances[0] / total,
                   "pc2_explained_fraction": variances[1] / total,
                   "nonzero_components": sum(v > 0 for v in variances)},
        "table": {"columns": ["sample_id", "pc1_score", "pc2_score"],
                  "rows": [[sample_ids[j], *scores[j]] for j in range(width)]},
    }


def _unique_gene_set_names(pairs: list[tuple[str, object]]) -> dict:
    """Reject JSON duplicate keys rather than silently changing the BH test family."""
    result = {}
    for name, members in pairs:
        if name in result:
            raise ValueError(f"Duplicate gene-set name {name!r} in JSON.")
        result[name] = members
    return result


def _gene_sets(text: str) -> dict[str, list[str]]:
    try:
        gene_sets = json.loads(text, object_pairs_hook=_unique_gene_set_names)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("Gene sets must be a JSON object mapping set names to arrays of gene IDs.") from None
    if not isinstance(gene_sets, dict) or not 1 <= len(gene_sets) <= 128:
        raise ValueError("Supply 1–128 named gene sets as a JSON object.")
    memberships = 0
    for name, members in gene_sets.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("Gene-set names must be nonempty strings of at most 80 characters.")
        if (not isinstance(members, list) or not members
                or any(not isinstance(gene, str) or not gene.strip() or len(gene) > 80
                       for gene in members) or len(set(members)) != len(members)):
            raise ValueError("Each gene set needs a nonempty array of distinct nonempty gene IDs.")
        memberships += len(members)
    if memberships > 8192:
        raise ValueError("User gene sets may contain at most 8,192 memberships in total.")
    return gene_sets


def _hypergeometric_right_tail(population: int, successes: int, draws: int, overlap: int) -> float:
    """Exact fixed-margin Pr[X >= overlap], summing integer combinatorial weights."""
    if overlap == 0:
        return 1.0
    numerator = sum(math.comb(successes, x) * math.comb(population - successes, draws - x)
                    for x in range(overlap, min(successes, draws) + 1)
                    if 0 <= draws - x <= population - successes)
    return min(1.0, numerator / math.comb(population, draws))


def over_representation_enrichment(data: dict) -> dict:
    """One-sided hypergeometric enrichment using an explicit feature universe."""
    from graphlink_plugins.scientific.packs.expression import _bh_adjust

    universe = _ids(data["universe_gene_ids"], "Universe gene IDs")
    selected = _ids(data["selected_gene_ids"], "Selected gene IDs")
    universe_set = set(universe)
    selected_set = set(selected)
    if not selected_set <= universe_set:
        raise ValueError("Every selected gene must belong to the supplied universe.")
    if len(selected) == len(universe):
        raise ValueError("Enrichment needs at least one unselected background gene.")
    sets = _gene_sets(data["gene_sets"])
    cutoff = data["fdr_cutoff"]
    if not 0 < cutoff < 1:
        raise ValueError("FDR cutoff must be strictly between zero and one.")
    records = []
    for name, members in sets.items():
        background_members = set(members) & universe_set
        overlap = selected_set & background_members
        size, count = len(background_members), len(overlap)
        expected = len(selected) * size / len(universe)
        records.append({"name": name, "size": size, "count": count, "expected": expected,
                        "fold": count / expected if size else None,
                        "overlap": [gene for gene in selected if gene in overlap],
                        "status": "tested" if size else "not_in_universe",
                        "p": _hypergeometric_right_tail(len(universe), size, len(selected), count)
                        if size else None, "q": None})
    _bh_adjust(records)
    tested = [r for r in records if r["status"] == "tested"]
    return {
        "summary": (f"One-sided hypergeometric over-representation of {len(selected)} selected genes "
                    f"among {len(universe)} user-supplied background genes, with BH correction "
                    "across sets having at least one in-universe gene. "
                    "Unrecognized set members are excluded from the background; "
                    "sets with no in-universe genes have undefined fold enrichment."),
        "values": {"universe_genes": len(universe), "selected_genes": len(selected),
                   "gene_sets": len(sets), "tested_sets": len(tested),
                   "out_of_universe_sets": len(records) - len(tested),
                   "significant_sets": sum(r["q"] <= cutoff for r in tested)},
        "table": {"columns": ["gene_set", "background_set_size", "selected_overlap",
                              "expected_overlap", "fold_enrichment", "p_value", "bh_fdr",
                              "overlap_gene_ids", "status"],
                  "rows": [[r["name"], r["size"], r["count"], r["expected"],
                            r["fold"] if r["fold"] is not None else "undefined",
                            r["p"] if r["p"] is not None else "not tested",
                            r["q"] if r["q"] is not None else "not tested",
                            json.dumps(r["overlap"]), r["status"]] for r in records]},
    }


_C_COUNT = (
    Field("gene_ids", "Gene IDs (JSON string array)", "text",
          description="Unique gene IDs in count-matrix row order."),
    Field("counts", "Raw counts (genes × samples)", "matrix", unit="1",
          description="Nonnegative exact integer counts; sample columns."),
)
_C_METHOD = Field("method", "Normalization method", "choice", choices=("cpm", "median_ratio"))
_C_PSEUDO = Field("pseudocount", "Log2 pseudocount (normalized count)", "number", unit="1")
_C_SAMPLE = Field("sample_ids", "Sample IDs (JSON string array)", "text",
                  description="Distinct sample names in count-matrix column order.")
_MA_COUNTS = [[10, 12, 9, 80, 85, 75], [80, 77, 85, 12, 10, 14],
              [30, 34, 32, 31, 30, 32], [1, 0, 1, 1, 0, 2],
              [20, 24, 18, 23, 19, 21]]
_EXPRESSION_EXAMPLE = {"gene_ids": '["G1", "G2", "G3", "G4", "G5"]',
                       "counts": _MA_COUNTS, "method": "cpm", "pseudocount": 1}
_SAMPLE_EXAMPLE = {**_EXPRESSION_EXAMPLE,
                   "sample_ids": '["control_1", "control_2", "control_3", '
                                 '"treated_1", "treated_2", "treated_3"]'}
_EXPRESSION_CITES = (
    Citation("Pearson, mathematical contributions to evolutionary theory (1895)",
             "https://doi.org/10.1098/rsta.1896.0007"),
    Citation("Sokal & Michener, a statistical method for evaluating systematic relationships (1958)",
             "https://doi.org/10.2307/2412050"),
    Citation("Jolliffe & Cadima, principal component analysis: a review (2016)",
             "https://doi.org/10.1098/rsta.2015.0202"),
    Citation("Rivals et al., enrichment and gene function analysis (2007)",
             "https://doi.org/10.1093/bioinformatics/btl633"),
    Citation("Benjamini & Hochberg, controlling the false discovery rate (1995)",
             "https://doi.org/10.1111/j.2517-6161.1995.tb02031.x"),
)

EXTRA_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        id="ma_values", title="MA expression values (two groups)",
        description="Descriptive log2 ratio (M) and average abundance (A) for two groups.",
        fields=(*_C_COUNT,
                Field("sample_groups", "Sample groups (JSON label array)", "text",
                      description="Two labels, at least two independent samples per group."),
                _C_METHOD, _C_PSEUDO),
        example={**_EXPRESSION_EXAMPLE,
                 "sample_groups": '["control", "control", "control", "treated", "treated", "treated"]'},
        calculate=ma_values,
        methods="Log2 of group mean normalized counts plus pseudocount; M=log2(B+p)-log2(A+p), "
                "A=(log2(B+p)+log2(A+p))/2. Not the existing Welch test's mean-of-logs effect.",
        citations=(_EXPRESSION_CITES[2],),
        headline="genes",
    ),
    Operation(
        id="sample_correlation", title="Sample correlation and hierarchical clustering",
        description="Pairwise Pearson log2-expression correlations, with average-linkage 1−r clustering.",
        fields=(*_C_COUNT, _C_SAMPLE, _C_METHOD, _C_PSEUDO),
        example=_SAMPLE_EXAMPLE,
        calculate=sample_correlation,
        methods="Pearson r across gene-wise log2(normalized+p) profiles, distance 1−r; "
                "unweighted pair-group arithmetic-mean (average-linkage) clustering with stable ties. "
                "Linkage indices 0..samples-1 refer to input sample order.",
        citations=_EXPRESSION_CITES[:2],
        headline="samples",
    ),
    Operation(
        id="expression_pca", title="Expression PCA (sample scores)",
        description="First two sample PCs of centered log2-normalized gene expression.",
        fields=(*_C_COUNT, _C_SAMPLE, _C_METHOD, _C_PSEUDO),
        example=_SAMPLE_EXAMPLE,
        calculate=expression_pca,
        methods="Center each log2(normalized count+p) gene across samples; no gene variance scaling. "
                "Eigen-decompose sample Gram covariance, report PC scores and explained fractions; "
                "values within 64 machine eps of total variance are treated as unresolved noise.",
        citations=(_EXPRESSION_CITES[2],),
        headline="pc1_explained_fraction",
    ),
    Operation(
        id="over_representation_enrichment", title="Gene-set over-representation (hypergeometric / BH)",
        description="Test selected genes against user-provided sets in an explicit background universe.",
        fields=(
            Field("universe_gene_ids", "Background universe (JSON gene ID array)", "text",
                  description="All genes eligible for selection, including unselected genes."),
            Field("selected_gene_ids", "Selected gene IDs (JSON array)", "text",
                  description="A unique nonempty subset of the background universe."),
            Field("gene_sets", "User gene sets (JSON object of gene ID arrays)", "text",
                  description="Mapping from gene-set name to unique gene IDs, at most 128 sets."),
            Field("fdr_cutoff", "Benjamini–Hochberg FDR cutoff", "number", unit="1"),
        ),
        example={
            "universe_gene_ids": '["A", "B", "C", "D", "E", "F", "G", "H"]',
            "selected_gene_ids": '["A", "B", "C"]',
            "gene_sets": '{"pathway_alpha": ["A", "B", "D"], '
                         '"pathway_beta": ["C", "E", "F", "G"], '
                         '"outside_background": ["X", "Y"]}',
            "fdr_cutoff": 0.05,
        },
        calculate=over_representation_enrichment,
        methods="Right-tail hypergeometric Pr[X≥observed] under fixed background/set/selection sizes; "
                "intersect user sets with explicit universe; zero-size sets have undefined "
                "fold enrichment and are excluded from BH correction.",
        citations=_EXPRESSION_CITES[3:],
        headline="significant_sets",
    ),
)
