# Genetics — Biology Workbench reference

**Pack:** `genetics` · **Plugin:** `science_genetics` · **Family:** Biology Workbench

Enable **Genetics** in Settings → Plugins, then choose it in **Plugins → Biology Workbench**. This is a separate, grant-gated pack with six operations. It does not alter the historic `science_biology` operation IDs or saved receipts. All calculations use the Python standard library and produce Workbench summaries, values, labeled tables/plots and verified provenance.

## Operation inventory

| Operation ID | Inputs | Outputs |
| --- | --- | --- |
| `segregation_chi_square` | `observed_counts` (2–16 offspring classes), `expected_ratio` (same-length positive weights, e.g. 3:1 or 9:3:3:1) | Pearson χ², k−1 degrees of freedom, approximate p, small-expected-count warning |
| `segregation_exact_binomial` | `observed_counts` (exactly two classes), `expected_ratio` (two positive weights) | Probability-ordered two-sided exact binomial p and log₁₀ p |
| `hardy_weinberg_exact` | `homozygote_aa`, `heterozygote_ab`, `homozygote_bb` (diploid individuals) | Conditional two-sided exact HWE p, mid-p, one-sided tails and allele frequencies |
| `map_distances` | `recombinants`, `total_offspring` (two-locus counts) | Recombination fraction, Haldane/Kosambi distances (cM) |
| `three_point_cross` | `progeny_counts` (eight complementary testcross gamete classes, fixed order below) | Gene order, parental/SCO/DCO classes, adjacent interval distances and interference |
| `two_point_lod` | `recombinants`, `nonrecombinants` (informative meioses), `theta` (0–0.5) | Two-point LOD at θ, maximum LOD, θ MLE and LOD profile |

Count and weight inputs are dimensionless, not percentages. Map distances are reported in **centimorgans** (cM), not physical base pairs. These operations assume fully classified, independent observations with no missing-data, penetrance, phase-uncertainty or population-relatedness model.

## Mendelian segregation-ratio tests

For a fixed expected ratio `w_1:…:w_k` and observed offspring `O_i` with total `N`, the expected count is `E_i = N*w_i/sum(w)`. Pearson's statistic is `chi2 = sum((O_i-E_i)^2/E_i)`, with **k−1 degrees of freedom** when the expected proportions were fixed *a priori* and no proportions were fit from the observations. The corresponding tail probability is the upper incomplete-gamma χ² survival function. This is an **asymptotic** approximation, and an expected count below five triggers a visible warning.

**Examples:** Observed [90,30] versus the fixed 3:1 ratio has expected [90,30], χ²=0 and p=1. To test a 9:3:3:1 dihybrid segregation, supply four observed class counts and expected weights [9,3,3,1]. Ratios chosen after examining the same observations invalidate the usual fixed-hypothesis p-value interpretation.

For two categories, `segregation_exact_binomial` evaluates the binomial distribution conditional on the total offspring and fixed class-1 probability `p=w_1/(w_1+w_2)`. Its *two-sided probability-ordered* p-value includes **all counts whose binomial probability is no greater than the observed count's probability**. This is not merely twice a one-sided tail. With counts [0,4] against the ratio 3:1, only the observed outcome has probability that small, and the exact p-value is **(1/4)^4 = 1/256 = 0.00390625**. Results also include log₁₀ p so exceptionally small probabilities remain interpretable after floating-point underflow. Enumeration is limited to **10,000** offspring.

Reference: [NIST χ² goodness-of-fit](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35f.htm), [NIST binomial distribution](https://www.itl.nist.gov/div898/handbook/eda/section3/eda366i.htm).

## Hardy–Weinberg exact test

This is a **conditional exact test** for diploid autosomal **biallelic** genotypes (AA, AB, BB), not the χ² approximation already available through `biology.hardy_weinberg`. The observed allele-A frequency is `p=(2AA+AB)/(2N)`. Expected genotype counts are `Np²`, `2Np(1−p)` and `N(1−p)²`; these are descriptive displays, not the probabilities used in the exact tail.

Following [Wigginton, Cutler and Abecasis (2005)](https://pmc.ncbi.nlm.nih.gov/articles/PMC1199378/), the operation conditions on the number of copies of the minor allele and the sample size. It enumerates permitted heterozygote counts with matching parity using a stable probability recurrence centered near the mode. The primary two-sided p-value adds configurations with conditional probabilities no greater than that of the observed configuration. It also returns the observed configuration probability, heterozygote-deficit and heterozygote-excess one-sided tails, and **mid-p = two-sided p − P(observed)/2**. Mid-p is an explicitly labeled alternative, not a replacement for the conventional exact p-value.

**Example:** AA=1, AB=0, BB=1 permits zero heterozygotes (probability **1/3**) or two heterozygotes (probability **2/3**), conditional on two copies of each allele. The observed two-sided exact p-value is **1/3**, whereas a chi-square approximation is unreliable with these sparse counts.

At a *monomorphic* locus only one conditional configuration is possible, so exact p=1; that is **not** evidence that the population's equilibrium assumptions are proven. Samples must contain **1–100,000** diploid genotype counts; negative, fractional or missing counts are rejected.

## Map distances: Haldane and Kosambi

With an observed two-point recombinant fraction `r = recombinants/total_offspring`, the map functions (in cM) are:

- **Haldane:** `d_H = -50 * ln(1 - 2r)`, the Poisson/no-interference model.
- **Kosambi:** `d_K = 25 * ln((1 + 2r)/(1 - 2r))`, a different interference model.

At r=0.35 (**35 recombinants out of 100**), Haldane gives approximately **60.19864 cM** and Kosambi **43.36503 cM**. Both give zero at r=0 and diverge as r approaches one half. At **r≥0.5** there is no finite positive map-distance estimate under these transformations. The operation refuses to invent infinity or silently rephase counts; verify the marker phase or interpretation before retrying. Crossover mapping functions are model assumptions, not a physical-length measurement.

Reference: [BatchMap's Haldane and Kosambi definitions](https://rdrr.io/cran/BatchMap/man/map_func.html), citing Haldane (1919) and Kosambi (1944).

## Three-point testcross analysis

Supply **exactly eight offspring gamete classes** in this **fixed order**:

`[ABC, ABc, AbC, Abc, aBC, aBc, abC, abc]`

Uppercase and lowercase designate alternative alleles at three loci, not a phenotype score. All eight categories must be supplied, even if some observed counts are zero. This operation analyzes a fully scored **three-point testcross**, not an arbitrary F₂ or an unphased pedigree.

The algorithm forms four complementary pairs: `ABC/abc`, `ABc/abC`, `AbC/aBc`, `Abc/aBC`. The **uniquely most abundant complementary pair** is inferred to be parental; the **uniquely least abundant complementary pair** is inferred to be the double crossover (DCO). A tie at either extreme causes an **ambiguous-order error**, not an arbitrary gene-order assignment. The locus at which the DCO haplotype differs from the matching parental haplotype is the **middle gene**. The remaining pairs are the two single-crossover (SCO) groups, classified by adjacent phase changes.

Each interval's recombination fraction includes the DCO:

`r_left = (SCO_left + DCO)/N` and `r_right = (SCO_right + DCO)/N`.

Uncorrected intervals are `100*r_left` and `100*r_right` cM; both map-function distances are also reported for each interval. The observed **outer-marker** recombinant fraction is `(SCO_left+SCO_right)/N` because DCO restores the parental outer phase. With an independence baseline, `expected_DCO = N*r_left*r_right`; the coefficient of coincidence is `observed_DCO/expected_DCO` and **interference = 1 − coefficient of coincidence**. Excess double crossovers may produce negative interference. Undefined denominators are never converted to a fictitious zero.

**Worked example:** Counts `[350,95,10,45,45,10,95,350]` give parental `ABC/abc` (**700** offspring), DCO `AbC/aBc` (**20**), left SCO **90**, right SCO **190**, and order **A–B–C**. The raw map intervals are **11 cM** and **21 cM**, the observed outer-marker fraction is **0.28**, and expected DCO is **23.1**. Coincidence is **20/23.1 ≈ 0.86580** and interference **≈ 0.13420**. Intervals at or above 50% recombination are rejected because finite Haldane/Kosambi distances do not exist. Parent/DCO inference from class extremes is a testcross heuristic and cannot establish a reliable map when data are sparse or ambiguous.

## Two-point linkage LOD scores

The LOD is a **log₁₀ likelihood ratio** of linkage at a hypothesized recombination fraction θ against unlinked θ=0.5, conditional on independent, known-phase, fully informative meioses. With `R` recombinant and `NR` nonrecombinant meioses:

`Z(theta) = R*log10(2*theta) + NR*log10(2*(1-theta))`.

The maximum over **0≤θ≤0.5** occurs at `theta_hat = min(R/(R+NR), 0.5)`. Results report the LOD at the requested θ, its maximizer and a finite sampled profile. At θ=0 with **any** recombinant meioses the likelihood vanishes and the LOD is **minus infinity**, represented as a string rather than an invalid numeric artifact; the infinite endpoint is excluded from the plot. At θ=0.5 the LOD is always zero; when R≥NR the constrained maximum is zero at θ=0.5.

**Worked example:** R=3, NR=7 and θ=0.3 gives `3*log10(0.6)+7*log10(1.4) ≈ 0.357`, equal to the maximum LOD at θ̂=0.3. With zero recombinants in ten informative meioses, the maximum LOD is **10 log₁₀2 ≈ 3.0103** at θ̂=0. Conventional linkage-evidence thresholds assume a valid pedigree/penetrance design; a LOD value is **not** itself a p-value.

Reference: [NIH linkage analysis course, LOD scores](https://www.genome.gov/sites/default/files/genome-old/pages/Research/IntramuralResearch/DIRCalendar/CurrentTopicsinGenomeAnalysis2005/CourseHandouts/CTGA2005Lec11.grey.pdf).

## Numerical limits and interpretation

All class counts must be nonnegative integers and sums must stay in the exact-count range (≤2^53−1). The exact binomial and exact HWE size caps also bound runtime. Positive expected ratio weights must not underflow to zero when normalized. Mapping and three-point algorithms reject non-finite map distances; two-point LOD excludes infinite plot coordinates. No p-value from this pack establishes a causal variant, a medical genetic diagnosis or the validity of sampling/phase assumptions without independent evidence.
