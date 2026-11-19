"""Small-matrix RNA-seq exploratory normalization and two-group expression tests.

These are transparent count-table calculations, NOT a negative-binomial RNA-seq
model (DESeq2/edgeR) or a substitute for biological replicate QC.
"""

from __future__ import annotations

import json
import math
import statistics

from graphlink_plugins.scientific.core import Citation, Field, Operation
from graphlink_plugins.scientific.packs.fitting import _student_t_cdf

_MAX_COUNT = 2**53 - 1


def _gene_ids(text: str, number: int) -> list[str]:
    try:
        genes = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError("Gene IDs must be a JSON array of unique nonempty strings.") from None
    if (not isinstance(genes, list) or len(genes) != number
            or any(not isinstance(gene, str) or not gene.strip() or len(gene) > 80
                   for gene in genes)
            or len(set(genes)) != len(genes)):
        raise ValueError("Supply one unique, nonempty gene ID (at most 80 characters) per count row.")
    return genes


def _count_matrix(data: dict) -> tuple[list[str], list[list[int]], list[float]]:
    counts = data["counts"]
    genes = _gene_ids(data["gene_ids"], len(counts))
    width = len(counts[0])
    if width < 2:
        raise ValueError("Provide at least two sample columns.")
    for row in counts:
        for value in row:
            if value < 0 or value > _MAX_COUNT or not value.is_integer():
                raise ValueError("Raw counts must be exact nonnegative integers at most 2^53 - 1.")
    # The field validator already enforces finite rectangular <=1024 x <=32 matrices.
    totals = [math.fsum(row[j] for row in counts) for j in range(width)]
    if any(not math.isfinite(total) or total <= 0 for total in totals):
        raise ValueError("Every sample must have a positive finite library total.")
    return genes, counts, totals


def _normalize(counts: list[list[int]], totals: list[float], method: str
               ) -> tuple[list[list[float]], list[float], int]:
    width = len(totals)
    if method == "cpm":
        factors = [total / 1_000_000 for total in totals]
        references = 0
    else:
        # DESeq's median-of-ratios uses genes with positive values in EVERY
        # sample. Compute ratios in log space and center size factors to GM=1.
        ratios: list[list[float]] = [[] for _ in totals]
        references = 0
        for row in counts:
            if all(count > 0 for count in row):
                log_gm = math.fsum(math.log(count) for count in row) / width
                for j, count in enumerate(row):
                    ratios[j].append(math.exp(math.log(count) - log_gm))
                references += 1
        if not references:
            raise ValueError("Median-ratio normalization needs a gene with positive counts in every sample.")
        factors = [statistics.median(column) for column in ratios]
        if any(factor <= 0 or not math.isfinite(factor) for factor in factors):
            raise ValueError("Median-ratio size factors must be positive and finite.")
        center = math.exp(math.fsum(math.log(factor) for factor in factors) / width)
        factors = [factor / center for factor in factors]
    normalized = [[float(value) / factors[j] for j, value in enumerate(row)]
                  for row in counts]
    if any(not math.isfinite(value) for row in normalized for value in row):
        raise ValueError("Normalized counts exceed the supported numeric range.")
    return normalized, factors, references


def normalize_counts(data: dict) -> dict:
    genes, counts, totals = _count_matrix(data)
    normalized, factors, references = _normalize(counts, totals, data["method"])
    width = len(totals)
    return {
        "summary": (f"Normalized {len(genes)} genes across {width} samples using "
                    f"{data['method']}; output is counts per million for CPM or "
                    "raw counts divided by median-ratio size factors."),
        "values": {
            "genes": len(genes), "samples": width,
            "median_ratio_reference_genes": references,
            **{f"sample_{j+1}_library_total": total for j, total in enumerate(totals)},
            **{f"sample_{j+1}_size_factor": factor for j, factor in enumerate(factors)},
        },
        "table": {"columns": ["gene_id", *[f"sample_{j+1}_normalized" for j in range(width)]],
                  "rows": [[gene, *row] for gene, row in zip(genes, normalized)]},
    }


def _group_indices(text: str, width: int) -> tuple[str, str, list[int], list[int]]:
    try:
        groups = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError("Sample groups must be a JSON array of two group labels.") from None
    if (not isinstance(groups, list) or len(groups) != width
            or any(not isinstance(label, str) or not label.strip() or len(label) > 40
                   for label in groups)):
        raise ValueError("Supply a nonempty group label for every sample column.")
    unique = list(dict.fromkeys(groups))
    if len(unique) != 2:
        raise ValueError("Two-group expression analysis needs exactly two different group labels.")
    a = [i for i, group in enumerate(groups) if group == unique[0]]
    b = [i for i, group in enumerate(groups) if group == unique[1]]
    if len(a) < 2 or len(b) < 2:
        raise ValueError("Welch tests need at least two independent replicates per group.")
    return unique[0], unique[1], a, b


def _bh_adjust(rows: list[dict]) -> None:
    """Apply monotone Benjamini–Hochberg to TESTED genes only."""
    tested = sorted((r for r in rows if r["status"] == "tested"), key=lambda r: r["p"])
    q = 1.0
    m = len(tested)
    for rank in range(m, 0, -1):
        row = tested[rank - 1]
        q = min(q, row["p"] * m / rank)
        row["q"] = q


def differential_expression(data: dict) -> dict:
    genes, counts, totals = _count_matrix(data)
    a_name, b_name, a, b = _group_indices(data["sample_groups"], len(totals))
    pseudo, fdr, lfc_cutoff = data["pseudocount"], data["fdr_cutoff"], data["log2_fc_cutoff"]
    if not 0 < pseudo <= 1_000_000:
        raise ValueError("Pseudocount must be positive and at most 1,000,000 normalized counts.")
    if not 0 < fdr < 1:
        raise ValueError("FDR cutoff must be strictly between zero and one.")
    if not 0 <= lfc_cutoff <= 100:
        raise ValueError("Absolute log2 fold-change cutoff must be between zero and 100.")
    minimum = data["min_total_count"]
    if minimum < 0:
        raise ValueError("Minimum total raw count must be nonnegative.")
    normalized, _, references = _normalize(counts, totals, data["method"])
    results: list[dict] = []
    for gene, raw, row in zip(genes, counts, normalized):
        logs = [math.log2(value + pseudo) for value in row]
        first = [logs[i] for i in a]
        second = [logs[i] for i in b]
        mean_a, mean_b = statistics.mean(first), statistics.mean(second)
        lfc = mean_b - mean_a  # B vs A: positive means higher in group B.
        record = {"gene": gene, "mean_a": mean_a, "mean_b": mean_b, "lfc": lfc,
                  "t": None, "df": None, "p": None, "q": None, "status": "tested"}
        if math.fsum(raw) < minimum:
            record["status"] = "low_count"
        else:
            va = statistics.variance(first) / len(a)
            vb = statistics.variance(second) / len(b)
            se2 = va + vb
            if se2 <= 0:
                record["status"] = "zero_variance"
            else:
                t = lfc / math.sqrt(se2)
                wa, wb = va / se2, vb / se2
                df = 1 / (wa * wa / (len(a) - 1) + wb * wb / (len(b) - 1))
                if not math.isfinite(t) or not math.isfinite(df) or df <= 0:
                    raise ValueError("Welch statistic or degrees of freedom exceed numeric range.")
                record.update(t=t, df=df, p=min(1.0, 2 * _student_t_cdf(-abs(t), df)))
        results.append(record)
    _bh_adjust(results)
    tested = [r for r in results if r["status"] == "tested"]
    up = sum(r["q"] <= fdr and r["lfc"] > 0 and r["lfc"] >= lfc_cutoff for r in tested)
    down = sum(r["q"] <= fdr and r["lfc"] < 0 and r["lfc"] <= -lfc_cutoff for r in tested)
    return {
        "summary": (f"Exploratory two-sided Welch t tests on log2({data['method']} normalized counts "
                    f"+ {pseudo}) for {a_name} (n={len(a)}) vs {b_name} (n={len(b)}); "
                    "Benjamini–Hochberg correction across tested genes. "
                    "Not a negative-binomial RNA-seq differential-expression model."),
        "values": {
            "genes": len(genes), "tested_genes": len(tested),
            "low_count_genes": sum(r["status"] == "low_count" for r in results),
            "zero_variance_genes": sum(r["status"] == "zero_variance" for r in results),
            "significant_up_in_group_b": up, "significant_down_in_group_b": down,
            "group_a": a_name, "group_b": b_name, "n_group_a": len(a), "n_group_b": len(b),
            "median_ratio_reference_genes": references,
        },
        "table": {
            "columns": ["gene_id", "mean_log2_group_a", "mean_log2_group_b", "log2_fold_change_b_vs_a",
                        "welch_t", "welch_df", "p_value", "bh_fdr", "status"],
            "rows": [[r["gene"], r["mean_a"], r["mean_b"], r["lfc"],
                      *[r[key] if r[key] is not None else "not tested"
                        for key in ("t", "df", "p", "q")], r["status"]] for r in results],
        },
        "views": [{
            "type": "volcano_plot", "title": f"Volcano: {b_name} vs {a_name}",
            "xLabel": f"log2 fold change ({b_name} / {a_name})",
            "fdrThreshold": fdr, "log2FoldChangeThreshold": lfc_cutoff,
            "points": [{"gene": r["gene"], "log2FoldChange": r["lfc"], "qValue": r["q"]}
                       for r in tested],
        }],
    }


_CITES = (
    Citation("Anders & Huber, differential expression analysis for sequence count data (2010)",
             "https://doi.org/10.1186/gb-2010-11-10-r106"),
    Citation("Benjamini & Hochberg, controlling the false discovery rate (1995)",
             "https://doi.org/10.1111/j.2517-6161.1995.tb02031.x"),
    Citation("NIST e-Handbook, two-sample t testing",
             "https://www.itl.nist.gov/div898/handbook/eda/section3/eda353.htm"),
)

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        id="normalize_counts", title="Normalize expression counts (CPM / median ratio)",
        description="Normalize an exact, nonnegative gene-by-sample count matrix for exploratory comparisons.",
        fields=(
            Field("gene_ids", "Gene IDs (JSON string array)", "text",
                  description="One unique name per matrix row, in the same order."),
            Field("counts", "Raw counts (genes × samples)", "matrix", unit="1",
                  description="Rectangular JSON matrix of nonnegative integer counts; columns are samples."),
            Field("method", "Normalization method", "choice", choices=("cpm", "median_ratio")),
        ),
        example={"gene_ids": '["GeneA", "GeneB", "GeneC"]',
                 "counts": [[10, 20, 40, 80], [20, 40, 80, 160], [30, 60, 120, 240]],
                 "method": "cpm"},
        calculate=normalize_counts,
        methods="CPM: 10^6*c_ij / sum_i(c_ij); median_ratio: geometric mean per all-positive gene, "
                "median of per-sample ratios, size factors centered to geometric mean 1. No length correction.",
        citations=(_CITES[0],),
    ),
    Operation(
        id="differential_expression", title="Two-group differential expression (Welch / BH)",
        description="Test independent biological replicates on log2-normalized counts; show an FDR volcano scatter.",
        fields=(
            Field("gene_ids", "Gene IDs (JSON string array)", "text"),
            Field("counts", "Raw counts (genes × samples)", "matrix", unit="1"),
            Field("sample_groups", "Sample groups (JSON label array)", "text",
                  description="Group label for each sample column; exactly two groups, >=2 replicates each; B versus A."),
            Field("method", "Normalization method", "choice", choices=("cpm", "median_ratio")),
            Field("pseudocount", "Log2 pseudocount (normalized count)", "number", unit="1"),
            Field("min_total_count", "Minimum raw count summed over samples", "integer", unit="1"),
            Field("fdr_cutoff", "Benjamini–Hochberg FDR cutoff", "number", unit="1"),
            Field("log2_fc_cutoff", "Absolute log2 fold-change cutoff", "number", unit="1"),
        ),
        example={
            "gene_ids": '["G1", "G2", "G3", "G4", "G5"]',
            "counts": [[10, 12, 9, 80, 85, 75], [80, 77, 85, 12, 10, 14],
                       [30, 34, 32, 31, 30, 32], [1, 0, 1, 1, 0, 2],
                       [20, 24, 18, 23, 19, 21]],
            "sample_groups": '["control", "control", "control", "treated", "treated", "treated"]',
            "method": "cpm", "pseudocount": 1, "min_total_count": 10,
            "fdr_cutoff": 0.05, "log2_fc_cutoff": 1,
        },
        calculate=differential_expression,
        methods="For each eligible gene, Welch's unequal-variance two-sided t test on log2(normalized "
                "count + pseudocount), with Satterthwaite degrees of freedom. BH adjusts tested p values; "
                "genes below the minimum count or with zero within-group variance are untested. "
                "Effect is difference of mean log2 counts (B-A), not a count likelihood estimate.",
        citations=_CITES,
    ),
)

# Keep the original operation IDs and receipt shapes unchanged.
from graphlink_plugins.scientific.packs.expression_additions import EXTRA_OPERATIONS

OPERATIONS += EXTRA_OPERATIONS
