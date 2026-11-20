# Biology Workbench — user guide

The Biology Workbench is the set of biology packs inside the [Scientific Workbench](SCIENTIFIC_WORKBENCH.md). Every calculation runs locally with Python's standard library, is started by you rather than by a model, and returns a result whose inputs, units, method and reference data are recorded in a verifiable receipt. Results can be connected to other nodes, charted, exported, discussed in chat and summarised as a methods paragraph.

This guide covers what is included, how to turn packs on, the conventions every pack follows, and five end-to-end workflows. Each pack also has its own reference page listed in the [Biology Workbench index](biology/README.md).

## What is included

| Pack | Plugin | Covers |
| --- | --- | --- |
| [Biology and Ecology](biology/biology.md) | `science_biology` | Community diversity, Hill numbers, beta diversity, species accumulation, growth and enzyme fits, mark–recapture, cohort life tables, structured-population projection |
| [Plate Assays](biology/plate_assays.md) | `science_plate_assays` | 96/384-well layouts, linear/quadratic/4PL/5PL standard curves, diluted unknowns, Z′-factor and %CV, qPCR standard curves, ΔΔCt and Pfaffl, digital PCR, A260 purity |
| [Cell Culture](biology/cell_culture.md) | `science_cell_culture` | Hemocytometer viability, seeding and serial-dilution plans, CFU, MPN, growth curves, doubling time, centrifuge conversion |
| [Enzyme Kinetics](biology/enzyme_kinetics.md) | `science_enzyme_kinetics` | Inhibition model fitting and comparison, kcat and kcat/Km, Hill fits, Cheng–Prusoff, binding isotherms, Morrison tight binding, Schild analysis, thermal-shift Tm |
| [Genetics](biology/genetics.md) | `science_genetics` | Segregation tests, exact Hardy–Weinberg, map distances, three-point crosses, LOD scores, F-statistics, linkage disequilibrium, Wright–Fisher simulation, heritability, Ne |
| [Epidemiology and Biostatistics](biology/epidemiology.md) | `science_epidemiology` | 2×2 measures, diagnostic accuracy, ROC/AUC, incidence rates, direct standardisation, Mantel–Haenszel, meta-analysis with forest plots, multi-group Kaplan–Meier, Cox regression |
| [Pharmacokinetics](biology/pharmacokinetics.md) | `science_pharmacokinetics` | Noncompartmental analysis, one- and two-compartment fits, multiple dosing, dose scaling, drug combinations |
| [Physiology](biology/physiology.md) | `science_physiology` | Nernst and GHK potentials, passive membrane properties, Hodgkin–Huxley simulation, spike detection, Fick diffusion |
| [Gene Expression](biology/expression.md) | `science_expression` | CPM and median-ratio normalisation, two-group differential expression, volcano plots, MA values, sample correlation and clustering, PCA, gene-set over-representation |
| [Phylogenetics](biology/phylogenetics.md) | `science_phylogenetics` | UPGMA and neighbor-joining trees, Newick parsing, rerooting, Robinson–Foulds comparison, patristic distances, consensus trees |

The packs analyse numbers you supply. They do not fetch data from biological databases, and the expression pack's enrichment uses gene sets that you provide.

## Turning packs on

1. Open **Settings → Plugins** and enable each biology pack you need. Every pack is granted separately.
2. Choose **Plugins → Biology Workbench** in the toolbar and pick a pack. A Workbench node appears on the canvas (or branches from the selected node).
3. Choose an operation in the node, fill in the inputs (or **Load example**) and choose **Run calculation**.

## Conventions

- **Compact nodes.** A node shows the operation, its inputs and the headline result. Unit help (**?**), methods and citations, the table importer, and every part of the result (full summary, all values, plots and views, tables, units, inputs and provenance, actions and exports) open on demand.
- **Units.** Every numeric input declares a default unit. You can type a compatible unit after a value (for example `25 mm`); the receipt records the unit you used.
- **Receipts.** Each result stores hashes of its inputs and outputs, and the version and checksum of any bundled reference data it used. Saved results are re-verified when they are loaded, connected or exported.
- **Typed views.** Some results include a view built for the data: a plate map, Kaplan–Meier survival curves, a forest plot, a phylogenetic tree or a volcano plot.
- **Discuss in chat.** **Actions & exports → Discuss in chat** opens a chat node linked to the result. The node shows a compact card: the pack and operation, the headline value, the summary and the receipt. The complete verified result, which is what the model reads, is under **Data sent to the model**.
- **Copy methods.** **Actions & exports → Copy methods** copies a methods paragraph built from the result's receipt; **Export methods** in the toolbar collects the methods for every verified result in the workspace.
- **Assumptions are stated, not hidden.** Summaries and methods say what each calculation assumes (for example, that 2^-ΔΔCt assumes 100% efficiency for every assay).

## Workflow 1: ELISA plate → 4PL standard curve → concentrations → Discuss in chat

Data: a clearly labelled **synthetic** sandwich-ELISA run. Seven standards from 15.6 to 1000 pg/mL were generated from a 4PL curve (bottom 0.05, top 2.50, inflection 120 pg/mL, slope 1.2) and two samples were diluted 1:2.

1. Enable **Plate Assays** and add a Biology Workbench node for it.
2. Optional: choose **96/384-well assay layout** to record which wells hold standards, blanks and samples. The result includes a plate-map view.
3. Choose **Standard curves and diluted unknowns** and enter:
   - Calibration model: `4pl`
   - Standard concentrations: `15.6, 31.25, 62.5, 125, 250, 500, 1000` (pg/mL)
   - Standard signals: `0.2449, 0.4566, 0.8186, 1.305, 1.7821, 2.1256, 2.3216` (OD450)
   - Unknown signals: `0.62, 1.48`; dilution factors: `2, 2`
4. Run it. The fit recovers bottom 0.050, top 2.500, inflection 120.0 pg/mL and slope 1.200 (R² > 0.9999). Both unknowns fall inside the calibration interval: 44.4 and 159.0 pg/mL in the well, so **88.8 and 318.0 pg/mL** in the original samples. The lowest standard is flagged as outside the back-calculation range rather than silently extrapolated.
5. Open **Actions & exports → Discuss in chat** to ask questions about the result. The chat starts from the verified result, so the model sees the same numbers, units and receipt you do.

For plate quality, **Assay quality: Z′, signal window and %CV** takes positive and negative control wells and reports the Z′-factor without imposing a pass/fail threshold.

## Workflow 2: qPCR with two reference genes → ΔΔCt → chart

Data: the operation's built-in example (**Load example**).

1. Enable **Plate Assays**. If you ran a dilution series, start with **qPCR Ct standard curve and amplification efficiency** to estimate each assay's efficiency from its slope. The example standards give 100% efficiency (E = 2 per cycle).
2. Choose **qPCR ΔΔCt and efficiency-corrected Pfaffl ratio** and load the example: target Ct 22 (sample) vs 25 (calibrator), two reference genes with Cts 19 and 21 in both, target efficiency 1.95 and reference efficiencies 2.0 and 1.9.
3. Run it. The sample is normalised to the geometric mean of both reference genes: **2^-ΔΔCt = 8**, and the efficiency-corrected **Pfaffl ratio = 7.41**. The summary notes that 2^-ΔΔCt assumes E = 2 for every assay, which is why the two numbers differ.
4. Open **Actions & exports**, pick a chart type and choose **Chart this** to place a linked chart node. If you rerun the calculation, the chart shows that it is stale and **Refresh chart** updates it.

## Workflow 3: inhibition model selection → Ki → Cheng–Prusoff check

Data: the built-in example, 18 initial rates at six substrate and three inhibitor concentrations (synthetic).

1. Enable **Enzyme Kinetics** and choose **Compare enzyme inhibition models**. Load the example and run it.
2. All four mechanisms are fitted to the same rates and ranked by AICc. **Competitive** has the lowest AICc with an Akaike weight of **0.877**; mixed follows at 0.123 (ΔAICc 3.9), and noncompetitive and uncompetitive are far behind (ΔAICc 111 and 125). Akaike weights are relative evidence, not the probability that a mechanism is physically true.
3. Choose **Fit enzyme inhibition mechanism** with model `competitive`. The joint nonlinear fit gives **Vmax = 99.8, Km = 1.98 and Ki = 1.49** in the input units, with local standard errors.
4. To check an IC50 from a separate dose-response experiment, choose **Convert IC50 to competitive Ki**. The example (IC50 = 15 µM, [S] = 8 µM, Km = 4 µM) gives **Ki = 5 µM** from IC50/(1 + [S]/Km). The summary states that this does not apply to tight-binding, irreversible, noncompetitive or substrate-depleting assays.

## Workflow 4: survival table → Kaplan–Meier by group → Cox model

Data: the built-in examples (synthetic follow-up times).

1. Enable **Epidemiology and Biostatistics** and choose **Multi-group Kaplan–Meier and log-rank test**. Load the example: 12 subjects in 3 groups, with follow-up times in days and an event indicator per subject.
2. Run it. The result has a survival-curve view with censoring ticks. Median survival is 2, 5 and 6 days in groups 1–3, and the log-rank test gives **χ² = 0.98 on 2 degrees of freedom, p = 0.61**: with four subjects per group there is no evidence of a difference.
3. Choose **Cox proportional hazards regression** and load its example: 8 subjects, 6 events, one Treatment covariate, Efron ties.
4. Run it. The hazard ratio for Treatment is **0.71 (95% CI 0.12–4.36, p = 0.71)**. The summary notes that hazard ratios are conditional associations per covariate unit and that proportional hazards is assumed, not tested.

For pooled 2×2 strata use **Mantel–Haenszel stratified odds ratio**; to combine published effect sizes use **Inverse-variance meta-analysis and forest plot**, which draws a forest-plot view.

## Workflow 5: expression table → normalisation → differential expression → volcano → enrichment

Data: the built-in examples (synthetic counts for five genes in three control and three treated samples).

1. Enable **Gene Expression** and choose **Normalize expression counts (CPM / median ratio)** to inspect normalised counts. CPM totals cover only the genes you supply, not a whole-library read count.
2. Choose **Two-group differential expression (Welch / BH)** and load the example. It runs Welch t-tests on log2(normalised count + 1) with Benjamini–Hochberg correction.
3. Run it. **G1 is up** in treated (log2 fold change 2.95, BH q = 0.0010) and **G2 is down** (−2.77, q = 0.0020); G3 and G5 are unchanged, and G4 is excluded as low-count rather than tested. The result includes a volcano-plot view with the significance thresholds. This is exploratory and is not a negative-binomial RNA-seq model.
4. Choose **Gene-set over-representation (hypergeometric / BH)**. Supply the background genes, the genes you selected (for example the significant ones) and your gene sets as JSON. The example tests two sets; neither is significant after BH correction, and a set with no genes in the background is reported as not tested rather than dropped silently.

For an overview of the samples, **Sample correlation and hierarchical clustering**, **Expression PCA (sample scores)** and **MA expression values (two groups)** work on the same count matrix.

## Correctness audits

Every pack was audited independently after it was built. The audits and their fixes are recorded in the [2026 scientific correctness audit](biology/SCIENTIFIC_CORRECTNESS_AUDIT_2026.md), the [phylogenetics audit](biology/PHYLOGENETICS_SCIENTIFIC_AUDIT_2026.md) and the pull requests for each pack. They check the calculations against reference formulas and independent implementations; they are not an experimental validation certificate.
