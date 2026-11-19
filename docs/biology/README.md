# Biology Workbench

Each biology pack has its own reference page and its own explicit Settings → Plugins grant. Open **Plugins → Biology Workbench** in the picker after granting a pack; the existing **Biology and Ecology** pack retains its original plugin ID, operations, and saved-result compatibility.

## Pack references

- [Biology and Ecology](biology.md) — community surveys, ecological modelling, growth and enzyme fits, genetics, survival and biodiversity (`science_biology`).
- [Plate Assays](plate_assays.md) — 96/384-well assay layouts, linear/quadratic/4PL/5PL calibration, back-calculated diluted unknowns, qPCR standard curves and ΔΔCt/Pfaffl multi-reference normalization, digital PCR Poisson quantification, A260 nucleic-acid purity, Z′-factor and %CV (`science_plate_assays`).
- [Cell Culture](cell_culture.md) — hemocytometer viability, 96/384-well plate maps, CFU plate counts, MPN, growth-curve fits, doubling time, seeding, serial dilutions and centrifuge conversion (`science_cell_culture`).
- [Enzyme Kinetics](enzyme_kinetics.md) — reversible inhibition, binding isotherms, Morrison tight-binding fits, Schild analysis, thermal-shift Tm, turnover and Hill saturation (`science_enzyme_kinetics`).
- [Genetics](genetics.md) — Mendelian segregation, exact Hardy–Weinberg, map distances, testcrosses, LOD scores, F-statistics, linkage disequilibrium, Wright–Fisher drift/selection, heritability and effective population size (`science_genetics`).

## Add a biology pack using new files only

Choose a unique lowercase pack ID such as `population_genetics`. Add these **new** files; no edits to `catalog.py`, `backend/plugins.py`, the picker or this index are required:

1. `graphlink_plugins/scientific/packs/<id>.py` — export an `OPERATIONS` tuple of `Operation` instances, each with fields, a runnable example, validated calculation, and stable pack-local operation ID.
2. `plugins/science_<id>/plugin.py` — register the pack with the shared Workbench host:
   
   ```python
   from graphlink_plugins.scientific.workbench import register_pack

   def register(host):
       register_pack(host, host.plugin_id.removeprefix("science_"))
   ```
3. `plugins/science_<id>/plugin.toml` — use the normal SDK manifest and the biology family flag:
   
   ```toml
   [plugin]
   id = "science_population_genetics"
   name = "Population Genetics"
   description = "Analyze genetic population observations."
   version = "1.1.0"
   sdk_api_version = 1
   entry_point = "plugin:register"

   [scopes]
   grants = ["graph.read", "graph.mutate"]

   [frontend]
   view = "generic"

   [persistence]
   passive_node_kinds = ["workbench"]

   [scientific]
   family = "biology"
   ```
4. `docs/biology/<id>.md` — an independent reference identifying the pack and plugin ID, operation IDs, input/output units, equations and assumptions, boundaries, worked examples and independent numerical regression sources. For the example above use `docs/biology/population_genetics.md`.

At app startup the catalog reads bundled `science_*/plugin.toml` metadata **without importing operation code**. The normal Plugin SDK discovers the new plugin, requests user consent, and activates its code only after it is granted. The biology flag places its entry under **Biology Workbench**; without it, a new scientific pack appears under **Scientific Workbench**. Each new pack can be tested independently, with no change to the historical operation table in [the main workbench guide](../SCIENTIFIC_WORKBENCH.md).

Do not rename or reuse a previously shipped pack ID or operation ID. Save files and result receipts depend on them. Include tests for valid examples, invalid inputs, numerical accuracy, and documentation; operations must use the standard workbench result contract and respect its input/output limits.
