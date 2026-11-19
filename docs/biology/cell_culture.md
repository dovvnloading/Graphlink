# Cell Culture — Biology Workbench reference

**Pack:** `cell_culture` · **Plugin:** `science_cell_culture` · **Family:** Biology Workbench

Enable **Cell Culture** in Settings → Plugins and select **Plugins → Biology Workbench**. This is an independent, grant-gated pack: it does not change any existing `science_biology` operation, result, or saved receipt. All four calculations use the Python standard library, and every result uses the Workbench's verified summary, scalar values, and optional table contract.

## Operations at a glance

| Operation ID | Inputs and units | Outputs |
| --- | --- | --- |
| `cell_count_viability` | `live_counts`, `dead_counts` (cells per equal-volume counted region); `dilution_factor` (dimensionless); `counted_volume_ul` (µL per region); `sample_volume_ml` (mL of *original* suspension) | Total and live/nonviable counts (cells), viability (%), viable/nonviable/total density (cells/mL), estimated viable cells in original sample, per-region table |
| `seeding_plan` | `viable_cells_per_ml` (cells/mL); `target_cells_per_vessel` (cells); `vessels` (count); `final_volume_ml_per_vessel` (mL); `overage_percent` (%) | Suspension and medium per vessel and in a bulk mixture including overage (mL), bulk viable cells (cells), preparation table |
| `serial_dilution_plan` | `initial_concentration` (chosen `concentration_unit`); `concentration_unit` (text label, e.g. `cells/mL`); `step_dilution_factor` (dimensionless); `steps` (count); `retained_volume_ml` (mL per tube **after** downstream transfer) | Concentration at each step, volume taken from previous solution and diluent added (mL), initially mixed and finally retained volumes (mL), total stock and diluent requirements |
| `centrifuge_rcf_rpm` | `input_kind` (`rpm` or `rcf`); `input_value` (rpm or multiple of gravity ×g); `rotor_radius_cm` (cm from rotation axis to the sample position) | Both rpm and RCF (×g), with radius and units |

### 1. Count cells and estimate viability

For **n** equal-volume counted regions, with live counts `L_i`, nonviable counts `D_i`, total dilution factor `f` of the *original* sample, and counted volume `v` in µL **per region**:

- Viable cells/mL = `(sum(L_i) / n) × f × (1000 / v)`.
- Nonviable cells/mL = `(sum(D_i) / n) × f × (1000 / v)`; total concentration is their sum.
- Viability (%) = `100 × sum(L_i) / (sum(L_i) + sum(D_i))`.
- Estimated viable cells in sample = `viable_cells_per_ml × sample_volume_ml`.

For a standard Neubauer large square (area 1 mm², depth 0.1 mm), `v = 0.1 µL`, hence the conventional `10,000` cells/mL multiplier **before** sample-dilution correction. A 1:1 sample/trypan-blue mix uses `dilution_factor = 2`; include any additional dilution in this *total* factor. Do not enter the full chamber loading volume in place of the small region volume.

**Worked example:** Live counts `[90,110,100,100]`, nonviable counts `[10,10,20,0]`, factor `2`, region volume `0.1 µL`, original sample `5 mL`: **2,000,000 viable cells/mL**, **200,000 nonviable cells/mL**, **90.9091% viability**, and **10,000,000 estimated viable cells** in the sample. The output table preserves each region's counts. Empty-total counts are rejected because viability would be undefined; unequal-length series, negative/fractional counts, and totals exceeding 2⁵³−1 are also rejected.

This uses dye-exclusion observations; it does not assess metabolic fitness, contamination, growth behavior, or the accuracy of the original sample mix. Use a consistent counting-boundary rule and equivalent region volumes.

### 2. Calculate viable-cell seeding volumes

Let `C` be viable cells/mL, `N` target viable cells per vessel, `V` final vessel volume in mL, `k` vessel count, and `p` preparation overage (%) assumed to apply equally to both components.

- Cell suspension per vessel = `N / C` mL.
- Medium per vessel = `V − N / C` mL.
- Bulk suspension = `(N / C) × k × (1 + p/100)` mL.
- Bulk medium = `(V − N / C) × k × (1 + p/100)` mL.

**Worked example:** `C=2,000,000 cells/mL`, `N=100,000 cells`, `k=12`, `V=2 mL`, `p=10%`: **0.05 mL (50 µL)** suspension + **1.95 mL** medium per vessel; prepare **0.66 mL** suspension + **25.74 mL** medium = **26.4 mL** bulk. Overage is for preparing extra *mixture*, not for increasing each vessel's target cell count. Plans requiring more cell suspension than the final vessel volume are rejected; the pack does not silently concentrate cells.

### 3. Prepare serial dilutions with enough volume left in every tube

A basic repeated transfer into `V` mL can leave earlier tubes short of the desired final amount. This planner instead works backward to provide **V mL remaining in each of the n tubes**, including the ones used to make subsequent dilutions.

For step factor `D > 1`, `C_i = C_0 / D^i`. Let `P_i` be the volume to mix initially in tube `i`. Starting at the last tube, `P_n = V`, and for earlier tubes `P_i = V + P_(i+1)/D`. Each tube takes `P_i / D` mL of the preceding solution and adds `P_i × (1 − 1/D)` mL diluent. The first tube takes its aliquot from the original stock. The table lists all these amounts.

**Worked example:** `C_0=1,000,000 cells/mL`, `D=10`, `n=3`, `V=1 mL`:

| Tube | Concentration (cells/mL) | Take from previous (mL) | Add diluent (mL) | Mix initially (mL) | Remains after transfer (mL) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 100,000 | 0.111 | 0.999 | 1.110 | 1.000 |
| 2 | 10,000 | 0.110 | 0.990 | 1.100 | 1.000 |
| 3 | 1,000 | 0.100 | 0.900 | 1.000 | 1.000 |

Total original stock used: **0.111 mL**; total diluent: **2.889 mL**; final retained volume: **3.000 mL**. The operation supports 1–100 steps and refuses underflow/overflow instead of printing zero concentrations. Numeric concentrations are assumed to be expressed in the supplied label; arbitrary labels are **not** converted to each other. This is an ideal volumetric plan; pipetting dead volume, instrument resolution, mixing error, and any desired excess must be accounted for separately.

### 4. Convert centrifuge RCF and rpm

For radius `r` in cm from the rotation axis to the selected sample position:

`RCF (×g) = 1.118 × 10^-5 × r(cm) × rpm²`

`rpm = sqrt(RCF / (1.118 × 10^-5 × r(cm)))`

Select `input_kind = "rpm"` or `"rcf"`; enter its numeric value and the applicable radius. **10 cm and 3,000 rpm → 1,006.2 ×g**; conversely 1,006.2 ×g at 10 cm → **3,000 rpm**. At 5 cm the same rpm generates only 503.1 ×g. Zero speed/RCF is supported; a zero/negative radius or negative speed/force is not.

RCF changes with radius: `r_max` often describes the **bottom of the tube**, but `r_avg` and `r_min` produce different forces. Consult the rotor specification for which point and radius a protocol uses. This conversion does not provide a centrifugation duration, biological recommendation, rotor limits, or approval to operate above instrument/tube ratings.

## Method references and limitations

- [Thermo Fisher Scientific — manual hemocytometer counts](https://www.thermofisher.com/us/en/home/references/gibco-cell-culture-basics/cell-culture-protocols/counting-cells-in-a-hemacytometer.html) explains the 0.1 µL square and 10⁴ multiplier.
- [UK Culture Collections — cell culture counting](https://www.culturecollections.org.uk/training-and-support/technical-support/cell-culture-protocols/) describes the dilution correction and live/(live+dead) viability.
- [Abcam — counting cells using a hemocytometer](https://www.abcam.com/en-us/technical-resources/protocols/counting-cells-using-a-haemocytometer) supplies the independent four-count, 1:5 dilution oracle used in the tests.
- [Eppendorf, *Start Separation at Ease*, §1.1](https://www.fishersci.pt/content/dam/fssite/eu/brands/e/eppendorf/28974_new_product_showcase_march_eppendorf/28974_Eppendorf_Essential_Tips_for_Good_Results.pdf) supplies the 1.118 × 10⁻⁵ rotor conversion and radius convention.

All outputs are deterministic arithmetic based on the provided measurements. The Workbench rejects invalid fields, nonfinite inputs/outputs, and oversized artifacts. Use measured viable counts, real vessel capacity, equipment guidance, and appropriate laboratory procedures when making practical decisions.
