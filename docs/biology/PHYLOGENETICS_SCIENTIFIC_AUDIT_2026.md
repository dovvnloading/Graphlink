# Phylogenetics scientific correctness audit (2026-11-19)

**Pack:** `phylogenetics` · **Plugin:** `science_phylogenetics` · **Scope:** all seven Biology Workbench phylogenetics operations. This is a *computational correctness review*, not a biological validation certificate. Models use supplied distances/trees, not aligned sequences or inferred statistical support.

## Independent method checks

| Operation | Independent oracle / invariant | Scientific interpretation |
| --- | --- | --- |
| `upgma` | For distances AB=2, CD=4, and all cross-cluster distances=6, merges occur at heights 1, 2 and 3; every tip has root depth 3 and all six patristic distances reproduce the matrix. For a non-ultrametric ABC matrix with AB=2, AC=4, BC=5, expected fitted cross distances are 4.5 (maximum absolute residual 0.5). | Cluster-size-weighted arithmetic mean (UPGMA) presumes approximately clocklike distances; residuals need not vanish. |
| `neighbor_joining` | Check minimum-Q joining and three-lineage limbs on an additive quartet. On the non-additive quartet AB=3, AC=4, AD=5, BC=6, BD=6, CD=2, the inferred tree has positive limbs but a maximum pairwise residual of 0.25. | NJ estimates a distance tree, not statistical support. Nonadditivity can cause mismatch **without** any negative-length clipping. |
| `parse_newick` | Reparse canonicalized output and independently compare tip labels and weighted paths. Unquoted `A_B` represents `A B`, while `'A_B'` represents the literal underscore. Confirm shortest roundtripping double-precision branch lengths. | Correct Newick interoperability prevents taxa changing identities and branch lengths losing precision on export. |
| `reroot_tree` | Compare all unordered tip-to-tip paths before and after outgroup/midpoint rooting. | Changing the root does not change undirected tip paths when all edges are weighted; it **does** change rooted clade membership. |
| `compare_trees` | Different binary quartet splits give unrooted RF=2; disjoint rooted nontrivial clades give rooted RF=4. | RF compares topology only, not branch lengths or support. Polytomies reduce the number of compared informative features. |
| `patristic_distances` | Sum the unique undirected paths independently; e.g. `(A:0,B:2,C:3);` gives AB=2, AC=3, BC=5. | Every non-root branch must carry a length; missing lengths cannot be treated as zero. |
| `consensus_tree` | For two `((A,B),(C,D));` trees and one conflicting `((A,C),(B,D));`, majority retains the two 2/3 clades, while strict produces a four-way polytomy. | Strict retains clades in 100% of trees; majority requires strictly more than 50%. Branch lengths are not inferred. |

## Fixed problems

1. **Newick taxon identity.** Decode underscores in *unquoted* labels as spaces; quote literal underscores on output. Reject unquoted apostrophes and brackets rather than silently treating them as label characters. Use Newick-style escaping for displayed clades/splits, so taxon names containing commas cannot make two different sets print identically.
2. **Distance matrix quality control.** Require relative symmetry at the supplied scale. The previous `max(1, d_ij, d_ji)` tolerance allowed 0 and 10⁻¹², or two values with substantial relative mismatch, to be treated as symmetric.
3. **Neighbor-joining provenance.** Count every fitted negative limb that is set to zero, including negatives smaller than 10⁻¹⁰ in absolute units. Absolute-size clipping-report thresholds are inappropriate when distance scales vary.
4. **Numerical fidelity.** Render branch lengths using a shortest binary64-roundtrip representation rather than 15 significant digits. Reject nonzero numeric literals that underflow to zero, while accepting true zeros.
5. **Model misfit visibility.** For both distance-inference operations, compute `maximum_absolute_distance_residual` and `root_mean_square_distance_residual` from **original observed matrix pairs** and inferred tip-to-tip paths. The diagnostics use the input distance units and remain meaningful when no negative NJ limbs were clipped.

For each unordered pair `i < j`, let `r_ij = d_tree(i,j) − d_input(i,j)`. The maximum is `max(|r_ij|)`; the RMS is `sqrt(sum(r_ij²) / (n(n−1)/2))`. The RMS implementation uses a scaled Euclidean norm (`math.hypot`) for floating-point robustness. These are **descriptive residuals, not standard errors, confidence intervals, p-values, bootstrap frequencies, or model-selection statistics**.

## Remaining constraints and assumptions

- Distance estimates are only as meaningful as the input dissimilarity and its units; the methods neither correct for molecular substitution models nor compare evolutionary hypotheses statistically.
- UPGMA assumes approximately ultrametric (clocklike) distances; neighbor joining is an unrooted distance method and can yield negative estimated edges on non-additive data. This pack clips negative fitted NJ limbs to zero, discloses their count, and displays the resulting distance residuals; these changes do not turn the algorithm into a nonnegative least-squares tree fit.
- Equal Q scores and zero-length interior branches can yield deterministic but not uniquely supported resolutions. A printed binary split is not evidence of bootstrap support.
- RF ignores lengths and tests only clades or bipartitions. Rooted consensus depends on consistent input rooting and yields no estimates of edge lengths or statistical support beyond input-tree frequency.
- Unspecified edge lengths remain unspecified; output for outgroup rerooting of a partly weighted tree can lose a known segment when contracted with an unknown one. Rerooting drops internal-node labels rather than interpreting them as edge supports.
- Newick accepted here is deliberately limited to nonnegative finite branch lengths, distinct labeled tips, at most 32 taxa, and at most 100 consensus trees; it does not attempt to implement every program's extended Newick dialect.

## Verification

Regression tests are in `tests/test_scientific_phylogenetics.py`. They exercise all seven operations, with independent path-sum checks, analytical UPGMA heights, additive and non-additive NJ, rooted and unrooted RF, reroot-invariant paths, consensus frequency thresholds, punctuation/underscore roundtrips, tiny-scale symmetry checks, nonzero-underflow rejection, full-precision length roundtrips, and accurate negative-limb clip counts.

Run: `python -m pytest -q tests/test_scientific_phylogenetics.py`

These numerical checks are useful software regressions, not evidence that a tree reconstructed from real biological data is the true evolutionary history.

## Sources

- Sokal and Michener (1958), UPGMA: https://doi.org/10.2307/2412447
- Saitou and Nei (1987), neighbor joining: https://doi.org/10.1093/oxfordjournals.molbev.a040454
- PHYLIP Neighbor manual (model assumptions and negative limbs): https://phylipweb.github.io/phylip/doc/neighbor.html
- Gary Olsen (1990), *Newick's 8:45 Tree Format Standard* (quoted/unquoted labels, underscores and comments): https://phylipweb.github.io/phylip/newick_doc.html
- Robinson and Foulds (1981), comparison of phylogenetic trees: https://doi.org/10.1007/BF01734359
- PHYLIP Consense manual (strict and majority-rule consensus): https://evolution.genetics.washington.edu/phylip/doc/consense.html
