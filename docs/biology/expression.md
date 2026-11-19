# Gene Expression — Biology Workbench

**Pack:** `expression` · **Plugin:** `science_expression` · **Family:** Biology Workbench · **Version:** 1.2.0

Enable **Gene Expression** in Settings → Plugins (separate `graph.read` and `graph.mutate` consent). Choose **Plugins → Biology Workbench → Gene Expression**. The pack is independent of legacy `science_biology` operations and saved receipts. It uses Python's standard library and the existing Scientific Workbench numerical tail function; no NumPy, SciPy, DESeq2, edgeR, external services, or code execution from input is needed.

## Inputs and operations

The input `counts` is a rectangular **genes × samples** JSON matrix of **raw** nonnegative integer counts (not pre-normalized measurements). `gene_ids` is a JSON string array with one distinct, nonblank ID per row (at most 80 characters each). Count-matrix operations accept up to **1,024 genes**, **32 samples** and **4,096 matrix cells** in the shared Workbench schema; integers must be exact, at most 2⁵³−1, and **every sample must have a nonzero library total**. No transcript-length adjustment is made. Counts should represent independent biological samples, not technical replicates treated as independent.

| Operation | Additional inputs | Result |
| --- | --- | --- |
| `normalize_counts` | `method`: `cpm` or `median_ratio` | Gene-by-sample normalized-count table; per-sample raw library totals and divisors; number of eligible reference genes |
| `differential_expression` | `sample_groups`: JSON array of two labels in **sample column order** (≥2 replicates in **each** group); `method`; `pseudocount` (positive, ≤1e6); `min_total_count` (nonnegative integer); `fdr_cutoff` in (0,1); `log2_fc_cutoff` in [0,100] | Per-gene log2 means, B−A log2 fold change, Welch t/df/p, BH q and testing status; volcano scatter with significance thresholds |
| `ma_values` | `sample_groups` (two groups, ≥2 samples each), `method`, `pseudocount` | Per-gene group mean normalized counts, MA abundance (A) and log2 ratio (M) |
| `sample_correlation` | `sample_ids` JSON array in column order, `method`, `pseudocount` | Cluster-ordered Pearson correlation matrix; JSON linkage and leaf order in values |
| `expression_pca` | `sample_ids`, `method`, `pseudocount` | PC1/PC2 scores by sample, variance and explained fractions |
| `over_representation_enrichment` | `universe_gene_ids`, `selected_gene_ids`, `gene_sets` (JSON object of ID arrays), `fdr_cutoff` | Hypergeometric right-tail enrichment, BH FDR, overlaps and excluded out-of-universe sets |

`sample_groups` can interleave group membership, e.g. `["control", "drug", "control", "drug"]`. The **first encountered label is group A** and the second group B, so a positive fold change means higher expression in B. Only MA values and differential expression require group labels; the normalization operation does not. A differential-expression operation always starts from raw counts and performs its chosen normalization internally; do **not** pass the normalized-count output back as if it were raw counts.

## Calculations

For count \(c_{ij}\), gene \(i\), sample \(j\), and sample total \(L_j=\sum_i c_{ij}\):

- **CPM:** \(n_{ij}=10^6 c_{ij}/L_j\). The reported divisor is \(L_j/10^6\), and each column of CPM sums to one million (subject to floating-point rounding).
- **Median ratio (DESeq-style size-factor normalization):** for genes positive in **every** sample compute \(g_i=\exp(\mathrm{mean}_j\log(c_{ij}))\); take \(s_j=\mathrm{median}_{i}(c_{ij}/g_i)\). Divide all \(s_j\) by their cross-sample geometric mean to set \(\mathrm{GM}(s)=1\), then \(n_{ij}=c_{ij}/s_j\). If no gene is positive across all samples the operation fails rather than silently switching methods. This is **not** DESeq2's dispersion estimation or negative-binomial testing.
- **Two-group testing:** for each gene, transform \(z_{ij}=\log_2(n_{ij}+p)\), with user-specified positive pseudocount \(p\). Define \(\mathrm{log2FC}_i=\overline z_{i,B}-\overline z_{i,A}\) (difference of mean log2 counts; *not* log2 of arithmetic count means). Apply a two-sided **Welch unequal-variance t test** with Satterthwaite degrees of freedom to these log2 values, using the shared stable Student-t tail. Both groups need at least two samples.
- **Testing eligibility:** features with summed raw counts below `min_total_count`, or with **zero variance in both groups**, are **not tested**. Their table p/adjusted p/t/df cells say `not tested`; they do not appear in the volcano plot. If no genes are testable the result contains an empty but valid scatter. No automatic low-expression filtering beyond the specified cutoff is applied.
- **Multiplicity:** monotone Benjamini–Hochberg adjustment over **tested genes only**. A feature is called up in group B when \(q\le\mathrm{fdr\_cutoff}\) and \(\mathrm{log2FC}\ge\mathrm{log2\_fc\_cutoff}\), down for the negative cutoff. The volcano x axis is B−A log2 fold change; the y axis is \(-\log_{10}(q)\). The scatter colors up, down and other tested features and displays vertical fold-change and horizontal FDR thresholds. Underflowed q=0 is plotted at a labeled floor of 1e−300, not infinity.

**Interpretation:** This is a bounded, deterministic **exploratory** comparison of log-transformed normalized counts. Its Welch t tests are not count-aware and do not model RNA-seq library composition effects, mean–variance dependence, dispersion, batch effects, covariates or paired designs. For publishable genome-wide RNA-seq inference, use an appropriate negative-binomial pipeline (DESeq2/edgeR) with QC and a suitable experimental design. CPM and median-ratio normalization should not be confused with transcript-length-aware TPM or FPKM.

## Worked examples and regression checks

`normalize_counts` example: `gene_ids = ["GeneA","GeneB","GeneC"]`, `counts = [[10,20,40,80],[20,40,80,160],[30,60,120,240]]`, `method = cpm`. All four columns normalize to `[166666.6667,333333.3333,500000]`, irrespective of their 2× sequencing depth differences. With `median_ratio`, adjacent samples have a 2× size-factor ratio, and normalized gene counts are the same across all samples.

`differential_expression` example: five genes × six samples, columns assigned to `control,control,control,treated,treated,treated`; `method=cpm`, `pseudocount=1`, `min_total_count=10`, `fdr_cutoff=.05`, `log2_fc_cutoff=1`. Four genes are tested; the low-count gene `G4` is excluded. `G1` is up in treatment (log2FC ≈ 2.95429, p ≈ 0.000253642, q ≈ 0.00101457), `G2` is down (log2FC ≈ −2.76807, p ≈ 0.00101744, q ≈ 0.00203487); two others are nonsignificant. Regression tests check these reference values, CPM/median-ratio depth invariance, BH ordering, zero-variance exclusions, sample-group labels, invalid counts, security/bounds of the volcano view and saved-result receipts.

## References

- Anders S, Huber W (2010). [Differential expression analysis for sequence count data](https://doi.org/10.1186/gb-2010-11-10-r106). *Genome Biology* 11:R106. Median-ratio normalization; the present operation **does not implement DESeq testing**.
- Benjamini Y, Hochberg Y (1995). [Controlling the false discovery rate](https://doi.org/10.1111/j.2517-6161.1995.tb02031.x). *J. Royal Statistical Society B* 57:289–300.
- NIST/SEMATECH [e-Handbook: two-sample t tests](https://www.itl.nist.gov/div898/handbook/eda/section3/eda353.htm). Reference for Welch-type independent group inference.

## MA values, sample QC and gene-set enrichment

These four additional operations use the same consent-gated Workbench inputs and signed result receipts. **No normalization output is accepted as raw counts**: MA, correlation and PCA start with the original count matrix and normalize internally. The added count-matrix analyses require a positive pseudocount (at most 1,000,000 normalized counts). `sample_ids` is a JSON string array with exactly one distinct nonblank sample name (up to 80 characters) for every count-matrix column. An input with any empty library is rejected. The existing two operations and their result shapes have not changed.

### MA values: `ma_values`

For a gene, let `mean_A` and `mean_B` be the arithmetic means of its normalized counts in groups A and B. With pseudocount `p`, report `M=log2(mean_B+p)-log2(mean_A+p)` and `A=(log2(mean_B+p)+log2(mean_A+p))/2`, as well as both untransformed group means. A positive `M` indicates greater abundance in the **second encountered** group (B). This is a *descriptive* MA table, not a hypothesis test or a significance plot. The existing `differential_expression` operation calculates its Welch-test effect using **means of individual log2 counts**, not log2 of arithmetic group means, so the two fold-change values may differ.

### Sample correlation and clustering: `sample_correlation`

Each sample is a vector of `log2(normalized_count+p)` across genes; pairs are compared by **Pearson r**. Correlation requires at least two genes and nonconstant transformed expression in every sample. Average-linkage agglomerative clustering uses the distance **1−r**, with deterministic tie-breaking by sample index and cluster orientation; this is not Euclidean distance or complete linkage. The correlation table contains a `sample_id` column and columns named `correlation_to_<sample ID>`, all in clustered leaf order. The scalar `values` also contain JSON strings for `sample_ids_input_order`, `cluster_order` and `linkage`. Each linkage record is `[left_cluster_id, right_cluster_id, distance, merged_size]`; original sample IDs are indexes `0..n-1`, and merge `i` has ID `n+i`. Correlations are symmetric, the diagonal is 1 and perfect anticorrelation corresponds to distance 2. Results with 32 samples may use a verified table asset because the matrix has 33 columns including its row labels.

### Expression PCA: `expression_pca`

The **samples** are observations and the **genes** are features. Transform each count to `log2(normalized_count+p)` and center **each gene** across samples without variance scaling. Eigen-decompose the symmetric sample Gram covariance (the existing standard-library Jacobi solver) and report the first two sample scores with their component variances and fractions of total explained variance. Scores sum to zero per component; PC signs are oriented by the strongest sample score for reproducibility but their biological sign has no intrinsic meaning. If the data have only one nonzero component, PC2 scores and variance are zero. Constant normalized expression across samples is rejected; batch effects and population structure are not corrected.

### Gene-set over-representation: `over_representation_enrichment`

This operation does **not** infer a gene universe from a partial input list or contact a gene-annotation service. Supply all genes eligible for selection as `universe_gene_ids` (JSON array, at most 1,024 unique names), a nonempty subset as `selected_gene_ids` (at least one unselected gene must remain), and `gene_sets` as a JSON object, e.g. `{"cell_cycle":["A","B","D"],"stress":["C","E"]}`. At most **128 sets** and **8,192 gene memberships** are supported, subject to the common 65,536-character text limit. Repeated IDs within a set, invalid JSON, non-string IDs and selected genes outside the universe fail validation.

For each gene set, intersect its members with the **explicit universe**, calculate `K` eligible members, `k` selected members, `N` total background genes and `n` selected genes. The right-tail hypergeometric probability is `Pr[X≥k] = Σ_(x=k..min(K,n)) C(K,x) C(N-K,n-x) / C(N,n)`; combinations are evaluated exactly with Python integers before conversion to a probability. The expected overlap is `nK/N`, and fold enrichment is `k/(nK/N)` when `K>0`. Benjamini–Hochberg correction is across **all sets with K>0**, including sets with zero overlap (p=1). A set without any in-universe genes has status `not_in_universe` and `not tested` p/q values and is excluded from the BH denominator. The result includes selected overlap IDs in input order. Enrichment tests association under fixed margins, not causation; background choice and dependency among gene sets matter.

The catalog examples run all four operations without external data. Regression tests cover MA group direction, normalization-depth invariance, Pearson symmetry and first clustering merge, centered/orthogonal PCA scores, exact large/small hypergeometric tails, BH adjustments, invalid groups/sets and receipt readability.

### Additional method references

- Sokal RR, Michener CD (1958). *A statistical method for evaluating systematic relationships*. Average-linkage clustering.
- Jolliffe IT, Cadima J (2016). [Principal component analysis: a review and recent developments](https://doi.org/10.1098/rsta.2015.0202).
- Rivals I et al. (2007). [Enrichment or depletion of a GO category within a class of genes](https://doi.org/10.1093/bioinformatics/btl633). One-sided hypergeometric gene-set over-representation.
