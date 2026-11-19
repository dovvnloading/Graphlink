# Phylogenetics — Biology Workbench reference

**Pack ID:** `phylogenetics` · **Plugin ID:** `science_phylogenetics` · **Family:** Biology Workbench · **Dependencies:** Python standard library only.

Enable **Phylogenetics** in Settings → Plugins, then choose it in **Plugins → Biology Workbench**. It has an independent `graph.read` / `graph.mutate` grant. All seven operations use the usual Workbench input validation, results, receipts, and JSON/CSV export. Existing `science_biology` nodes and their saved receipts are unchanged.

## Operations

| Operation ID | Input fields | Output |
| --- | --- | --- |
| `upgma` | `taxa` (JSON string array), `distances` (square numeric matrix) | Rooted ultrametric Newick tree, taxon count, total branch length, pairwise distance-fit residuals |
| `neighbor_joining` | `taxa`, `distances` | Unrooted Newick tree as a trifurcating root, total length, counted negative-limb clips and pairwise distance-fit residuals |
| `parse_newick` | `tree` (Newick text) | Canonicalized Newick, taxon and internal-node counts, root-to-tip table |
| `reroot_tree` | `tree`, `method` (`outgroup` / `midpoint`), `outgroup` (taxon text; ignored for midpoint) | Rerooted Newick and total length when defined |
| `compare_trees` | `tree_a`, `tree_b`, `mode` (`rooted` / `unrooted`) | Robinson–Foulds distance, normalized RF, shared clades/splits and membership table |
| `patristic_distances` | `tree` (length-annotated Newick) | Table of all unordered taxon pairs and path lengths, mean and maximum distance |
| `consensus_tree` | `trees` (two or more semicolon-terminated Newick trees), `method` (`majority` / `strict`) | Rooted consensus Newick and retained clade support table |

All operation IDs are pack-local. To reuse a generated tree, copy the `newick` value to a downstream tree-text field. Text fields can also be entered manually; these operations don't need a network service.

## Numerical methods and interpretation

### Distance-tree inference

`taxa` must contain **2–32 distinct, nonempty labels** in JSON notation, in the same row/column order as `distances`. Matrices must be finite, nonnegative, square, symmetric (relative tolerance `1e-10` on each pair, **without** an absolute tolerance floor), have an exactly zero diagonal, and contain values no greater than `1e12`. Distance and branch-length units are the **input distance units** (for example, substitutions/site); the matrix field's numeric Workbench unit is dimensionless (`1`). No sequence alignment, bootstrap confidence, or evolutionary model is inferred by the pack.

UPGMA repeatedly selects the closest clusters, sets their parent height to half their distance and uses cluster-**size-weighted arithmetic mean** distances when updating clusters:

\[
D_{A\cup B,C} = \frac{|A|D_{A,C}+|B|D_{B,C}}{|A|+|B|}.
\]

It assumes approximately ultrametric, clocklike input distances and is **not** a general additive-tree method. The neighbor-joining implementation selects the minimum of `Q(i,j)=(n−2)D(i,j)−r_i−r_j`, computes joining limb lengths from row sums, and finishes with a three-way center. Negative estimated limbs are set to zero and **every** negative limb is counted in `negative_limbs_clipped`, including small values; zero/negative estimates do not provide evidence for well-resolved branches. Non-additive or nonmetric inputs need not have their original distances exactly reproduced, even when no limb was clipped. Both algorithms use deterministic taxon-name tie breaking; ties or zero-length internal branches must not be interpreted as statistical support.

Both methods compute pairwise path-length **fit diagnostics** for all `n(n−1)/2` unordered input-taxon pairs, using the inferred tree's edges:

\[
r_{ij}=d_{\mathrm{tree}}(i,j)-d_{\mathrm{input}}(i,j),\quad
\mathrm{max\_abs}=\max_{i<j}|r_{ij}|,\quad
\mathrm{RMS}=\sqrt{\frac{\sum_{i<j}r_{ij}^2}{n(n-1)/2}}.
\]

These appear as `maximum_absolute_distance_residual` and `root_mean_square_distance_residual`, both in **input distance units**. A nonzero residual is a measure of disagreement, not an uncertainty estimate or a hypothesis-test result. UPGMA may have nonzero residuals for non-ultrametric inputs; NJ may have nonzero residuals for non-additive inputs **without** any clipped branches.

**Worked distance example** (also the runnable example for both operations):

```json
{
  "taxa": "[\"A\", \"B\", \"C\", \"D\"]",
  "distances": [[0, 2, 6, 6], [2, 0, 6, 6], [6, 6, 0, 4], [6, 6, 4, 0]]
}
```

UPGMA gives `((A:1,B:1):2,(C:2,D:2):1);`: all four tips are depth 3 from the root, A–B patristic distance is 2, C–D is 4 and every cross-clade pair has distance 6. Neighbor joining recovers the same **unrooted splits and pairwise distances**, though its rooting and Newick edge placement differ.

### Newick and rerooting

Newick must end in `;`. The parser supports binary trees, polytomies, optional internal labels, single-quoted labels (with doubled apostrophe escaping), optional nonnegative decimal/scientific-notation edge lengths, whitespace and balanced bracket comments. **Unquoted underscores encode spaces** by the Newick convention (so `A_B` means taxon `A B`); a literal underscore must be single-quoted (`'A_B'`). Unquoted reserved punctuation (including apostrophes and brackets) is not accepted. The canonical serializer quotes labels as needed and uses shortest lossless binary64 branch-length formatting. An underflowing nonzero length such as `1e-999` is rejected rather than silently converted to zero. The outermost root-edge length is ignored because it is not part of a leaf-to-leaf path. It rejects missing/duplicated leaf labels, unary or empty groups, missing semicolons, malformed lengths, non-finite or negative lengths, overly deep comments, and trees with more than 32 tips. Labels are case-sensitive. It canonicalizes child order but does not turn internal node labels into support estimates.

**Outgroup rerooting** places the root halfway along the pendant edge of the named tip (not at the tip itself); `outgroup` must match exactly one leaf. It works for unweighted trees too, retaining absent lengths as absent. **Midpoint rerooting** locates the middle of the longest weighted tip-to-tip path; it requires an explicit length on every non-root edge. Both methods contract introduced unary vertices and preserve all taxon-to-taxon paths and their lengths when edge lengths are fully specified. On partly weighted trees, an outgroup reroot may propagate an unspecified edge length through a contraction; unspecified distances remain unspecified. Internal-node labels are dropped when rerooting rather than being assigned an invented interpretation at a different root. Rooted clade comparisons can change after rerooting; unrooted splits do not.

### Tree comparison and distances

Robinson–Foulds (RF) distance is the number of features found in only one tree. In `rooted` mode the features are clades containing 2 through `n−1` taxa; in `unrooted` mode they are unique bipartitions with at least two taxa on both sides. Rooted branch lengths and internal labels do not affect either comparison. For a binary quartet with different internal splits, unrooted RF is **2**; if two fully resolved rooted quartet trees have disjoint nontrivial clades, rooted RF is **4**. `normalized_rf = RF / (number_of_features_A + number_of_features_B)`, or zero when there are no informative features. Comparisons require identical tip-label sets; for unrooted trees of 2–3 taxa, no informative splits exist. Clade/split membership tables use single-quoted Newick escaping for names containing whitespace, punctuation or literal underscores, avoiding ambiguous printed taxon groups.

Patristic distance between two leaves is the **sum of branch lengths along their unique connecting path**. Every branch must be length-annotated; zero-length branches are valid, but missing lengths are not silently treated as zero. The result has one row per unordered tip pair (up to 496 rows for 32 taxa) and reports the arithmetic mean and maximum across those pairs. Length units are inherited from the Newick input.

### Rooted consensus

Provide **2–100** semicolon-terminated trees in one text field (whitespace/newlines between trees are allowed). All trees must have exactly the same leaf-label set. A **strict** consensus includes clades present in every input tree; **majority** includes those present in *strictly more than 50%* of the trees (ties are excluded). Accepted rooted clades are mutually compatible and are nested to form a tree; unresolved relationships remain polytomies. The support table gives the number and fraction of contributing trees for each retained clade. **Branch lengths are not inferred or averaged**. Rooting choices affect rooted clade frequencies: reroot the inputs consistently before consensus if the intended analysis is rooted.

For `((A,B),(C,D));((A,B),(C,D));((A,C),(B,D));`, majority consensus is `((A,B),(C,D));` with each retained clade supported by 2/3 trees; strict consensus is the unresolved `(A,B,C,D);`.

## Scope and independent references

Work is locally bounded by the Workbench's 65,536-character text field and result-size limits (512 KiB per receipt), with 32-tip trees and at most 100 consensus trees. There is no approximate-likelihood, bootstrap, sequence alignment, branch-support estimation, variance model or phylogenetic visualization in this pack. Methods are descriptive; they do not establish evolutionary correctness without suitable data and model assumptions.

- Sokal and Michener (1958), UPGMA: <https://doi.org/10.2307/2412447>
- Saitou and Nei (1987), neighbor joining: <https://doi.org/10.1093/oxfordjournals.molbev.a040454>
- Robinson and Foulds (1981), tree comparison: <https://doi.org/10.1007/BF01734359>
- Newick conventions (Gary Olsen's 1990 interpretation of Newick 8:45): <https://phylipweb.github.io/phylip/newick_doc.html>
- PHYLIP format and consensus references: <https://evolution.genetics.washington.edu/phylip/newicktree.html> and <https://evolution.genetics.washington.edu/phylip/doc/consense.html>

Numerical tests cover the UPGMA four-taxon height/path example, additive and non-additive NJ reconstruction, fit residuals verified from independently calculated tip paths, rooted/unrooted RF, exact patristic path sums, reroot-invariant paths, majority/strict consensus, taxon-name escaping, floating-point roundtripping, tiny asymmetric distances, clipped limbs and malformed-input boundaries. See the [focused phylogenetics correctness audit](PHYLOGENETICS_SCIENTIFIC_AUDIT_2026.md) for methods and remaining limitations.
