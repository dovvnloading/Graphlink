# Scientific Workbench: Materials Science — models, units and reference checks

The `science_materials` plugin registers the `materials` pack. It uses **Python's
standard library only** and returns the same immutable Workbench result shape
(`summary`, `values`, optional `table`/`plot`) through the existing scientific
core, including its input validation, provenance receipt, saved graph nodes,
source bindings and export behavior. No saved-result schema/version migration
is required. Operations calculate estimates, **not certified material test
results, design allowables or acceptance criteria**.

## Legacy operations (unmodified)

| ID | Inputs (unit) | Output keys (unit) |
| --- | --- | --- |
| `bulk_density` | `dry_mass_g` (g), `bulk_volume_cm3` (cm³) | `bulk_density_g_cm3` (g/cm³), `bulk_density_kg_m3` (kg/m³) |
| `tensile_response` | `force_n` (N), `extension_mm` (mm), `original_area_mm2` (mm²), `gauge_length_mm` (mm) | `peak_measured_stress_mpa` (MPa), `engineering_strain_at_first_peak` (mm/mm, unitless ratio); paired table/plot axes specify mm/mm and MPa |
| `apparent_porosity` | `dry_mass_g`, `saturated_mass_g`, `suspended_mass_g` (all g) | `apparent_open_porosity_percent` (%), `absorbed_liquid_g` (g) |

All three original `Operation` IDs, field schemas, defaults, functions, output
keys and saved receipts remain unchanged. The original unsuffixed
`engineering_strain_at_first_peak` key is **mm/mm**; it was intentionally not
renamed because existing saved results and graph citations may depend on it.

## New operations

Every new numeric result key includes its unit or a `dimensionless` suffix;
unit-bearing tabular columns and plot axes also label units. Enum input fields
carry `unitless code` rather than physical units. Workbench matrix fields
accept rows of numeric values; the two input column units are specified in
the corresponding field. A "strain (mm/mm)" is the dimensionless fraction, **not
percent** (0.002 is 0.2%). MPa × strain = MJ/m³; 1 MPa = 1 N/mm².

### `stress_strain_analysis`

- **Inputs:** `engineering_curve` paired rows `[engineering strain (mm/mm),
  engineering stress (MPa)]`, with monotone, strictly increasing strains and
  an origin `(0,0)`; `elastic_limit_strain_mm_mm` (mm/mm), chosen **by the
  analyst** as the last point to include in the elastic least-squares fit.
- **Outputs:** `young_modulus_mpa` (MPa), `elastic_intercept_mpa` (MPa),
  `elastic_fit_r_squared_dimensionless` (1), `offset_yield_strength_mpa`
  (MPa), `offset_yield_strain_mm_mm` (mm/mm),
  `ultimate_tensile_strength_mpa` (MPa), `elongation_at_break_percent` (%),
  `toughness_mj_m3` (MJ/m³), `resilience_to_elastic_limit_mj_m3` (MJ/m³),
  plus tabular data and a plotted engineering curve.
- **Method:** ordinary least squares of stress against strain **up to and
  including** the chosen elastic limit, with a freely fitted intercept. The
  offset yield is the **first** linearly interpolated intersection on the
  rising/pre-UTS branch with the line parallel to that fit displaced by
  Δε = 0.002. No yield result is fabricated when an intersection is absent.
  Ultimate strength is the maximum sampled engineering stress, elongation at
  break is the **last sampled strain**, and both energy densities use
  trapezoidal integration of the measured curve (resilience only to the
  selected elastic limit, including an interpolated endpoint). There is no
  extrapolation beyond measurements. Use sufficiently dense elastic and
  yielding samples; slopes and intersection points depend on sampling,
  extensometer zeroing and elastic-region selection. The last row must be the
  **fracture endpoint** for reported elongation and toughness to be meaningful.

### `true_stress_strain`

- **Inputs:** `uniform_curve` rows `[strain (mm/mm), stress (MPa)]` plus
  `direction` (unitless code `engineering_to_true` or `true_to_engineering`).
- **Outputs:** `last_true_strain_mm_mm` (mm/mm), `last_true_stress_mpa`
  (MPa), `last_engineering_strain_mm_mm` (mm/mm),
  `last_engineering_stress_mpa` (MPa), and columns for both definitions.
- **Method:** during **uniform** incompressible deformation only,
  σ_true = σ_eng (1 + ε_eng), ε_true = ln(1 + ε_eng), and conversely
  σ_eng = σ_true exp(−ε_true), ε_eng = exp(ε_true) − 1. **Not valid after
  onset of necking**: local area data are required then. All strain readings
  must increase; tensile strain and stress must be nonnegative.

### `hollomon_fit` and `ramberg_osgood_fit`

- **Hollomon inputs:** `true_plastic_curve` paired `[true plastic strain
  (mm/mm), true stress (MPa)]`. **Outputs:** `strength_coefficient_k_mpa`
  (MPa), `strain_hardening_exponent_n_dimensionless` (1),
  `log_log_r_squared_dimensionless` (1), observed/fitted table.
- **Ramberg–Osgood inputs:** `total_strain_stress_curve` paired `[true total
  strain (mm/mm), true stress (MPa)]` and `young_modulus_mpa` (MPa).
  **Outputs:** `strength_coefficient_k_mpa` (MPa),
  `ramberg_osgood_n_dimensionless` (1), `log_log_r_squared_dimensionless`
  (1), observed/fitted table.
- **Method:** respectively σ = K εₚⁿ and ε_total = σ/E + (σ/K)^(1/n),
  using an ordinary least-squares fit of `ln σ` against `ln εₚ` (where the
  Ramberg–Osgood εₚ is obtained by subtracting σ/E). Require at least three
  points with **positive plastic strains and stresses**, and `0 < n ≤ 1`.
  Select the post-yield uniform hardening region; include neither the yield
  plateau nor necking. This is an **unweighted log-space fit** (errors are
  multiplicative in stress), not a nonlinear original-space least-squares
  fit and not the alternative α-parametrized Ramberg–Osgood equation.

### `fatigue_life`

- **Inputs:** `stress_amplitude_mpa` (MPa),
  `total_strain_amplitude_mm_mm` (mm/mm), `mean_stress_mpa` (MPa, signed),
  `ultimate_strength_mpa` (MPa), `yield_strength_mpa` (MPa),
  `young_modulus_mpa` (MPa), `fatigue_strength_coefficient_mpa` (MPa,
  σ′f), `basquin_exponent_b` (dimensionless, negative),
  `fatigue_ductility_coefficient_mm_mm` (mm/mm, ε′f),
  `coffin_manson_exponent_c` (dimensionless, negative), and
  `mean_stress_correction` (`none`, `goodman`, `gerber`, `soderberg`;
  unitless code).
- **Outputs:** `mean_stress_factor_dimensionless` (1),
  `corrected_alternating_stress_mpa` (MPa),
  `basquin_fatigue_life_cycles` (cycles),
  `basquin_coffin_manson_fatigue_life_cycles` (cycles).
- **Method:** Basquin σa,eq = σ′f (2N)^b; combined strain–life
  εa = (f σ′f/E)(2N)^b + ε′f(2N)^c. The *reversal count* is 2N, and
  outputs are **cycles**. `f = 1 − σm/Su` (Goodman),
  `f = 1 − (σm/Su)²` (Gerber), or `f = 1 − σm/Sy` (Soderberg), with
  σa,eq = σa/f. A nonpositive mean is conservatively assigned `f = 1`:
  **no undocumented compressive benefit**. Both exponents must be negative.
  Solving the combined equation uses a bounded bisection in log-reversals.
  Predictions below half a cycle or exceeding numerical capacity are rejected.
- **Important limitation:** Goodman/Gerber/Soderberg are **stress-life mean
  corrections**. Applying the same factor only to the *elastic coefficient*
  of combined strain-life is an explicitly labeled **engineering heuristic**,
  not Dowling's Morrow or Smith–Watson–Topper criterion. For critical fatigue
  design use appropriate calibrated mean-stress strain-life parameters,
  multiaxial correction and measured S–N/ε–N data. These equations omit
  variable amplitude, notches, temperature and probabilistic scatter.

### `fracture_stress_intensity` and `fracture_critical_length`

- **Intensity inputs:** `applied_tensile_stress_mpa` (MPa),
  `crack_length_a_m` (m), `plate_width_m` (m), `geometry` code:
  `infinite_center`, `semi_infinite_edge`, `finite_center`, `finite_edge`.
  **Outputs:** `geometry_factor_y_dimensionless` (1),
  `mode_i_stress_intensity_mpa_sqrt_m` (MPa√m).
- **Critical length inputs:** `fracture_toughness_mpa_sqrt_m` (MPa√m),
  `applied_tensile_stress_mpa` (MPa), `constant_geometry_factor_y` (1).
  **Outputs:** `critical_crack_length_a_m` (m),
  `critical_crack_length_a_mm` (mm), `full_center_crack_length_2a_m` (m).
- **Method:** K_I = Y σ √(π a). A *center crack* has **total length 2a**;
  an edge crack has length **a**. `Y = 1` for an infinite centered through
  crack and `Y = 1.12` for a small edge crack in a semi-infinite plate;
  finite centered crack `Y = [sec(π a/W)]^(1/2)` for a < W/2; finite
  edge crack `Y = 1.12 − 0.231(a/W) + 10.55(a/W)² − 21.72(a/W)³ +
  30.39(a/W)⁴` for 0 < a/W ≤ 0.6. Width W is the **full panel width**.
  The critical crack size `a_c = [K_IC/(Yσ)]²/π` assumes **constant Y**:
  use a geometry-dependent solver for crack sizes that substantially
  change Y. Linear-elastic, mode-I, small-scale yielding is assumed.
  Sufficient thickness and constraints are needed to use a measured
  plane-strain K_IC as the actual limit. No plastic-zone corrections.

### `paris_crack_growth`

- **Inputs:** `initial_crack_a_m`, `final_crack_a_m` (m),
  `stress_range_mpa` (MPa, Δσ), `constant_geometry_factor_y` (1),
  `paris_c_m_cycle_inv_mpa_sqrt_m_neg_m` with units
  **m/(cycle·(MPa√m)^m)** for the supplied exponent,
  `paris_exponent_m` (dimensionless), `load_ratio_r` (dimensionless
  σ_min/σ_max, supported 0 ≤ R < 1), and
  `fracture_toughness_mpa_sqrt_m` (MPa√m).
- **Outputs:** `paris_growth_cycles` (cycles),
  `initial_delta_k_mpa_sqrt_m` / `final_delta_k_mpa_sqrt_m` (MPa√m),
  and `final_k_max_mpa_sqrt_m` (MPa√m).
- **Method:** da/dN = C(ΔK)^m, ΔK = Y Δσ √(π a), with exact
  analytical integration and a stable `expm1` form near m = 2 (logarithmic
  antiderivative at m = 2). The requested final length cannot exceed
  K_max = K_IC when K_max = ΔK/(1−R). This **does not** check whether
  ΔK is above the Paris threshold, whether a closure/corrosion term is
  relevant, or whether the geometry factor changes with a. Fit C and m
  using the **same MPa√m and m/cycle units**. Do not mix with imperial C.

### `thermal_expansion_stress`

- **Inputs:** `linear_expansion_per_k` (K⁻¹), `initial_length_mm` (mm),
  `temperature_change_k` (K), `young_modulus_mpa` (MPa),
  `axial_restraint_fraction` (dimensionless, 0 to 1).
- **Outputs:** `free_length_change_mm` (mm),
  `free_thermal_strain_mm_mm` (mm/mm),
  `restrained_axial_stress_mpa` (MPa, negative is compression),
  `actual_length_change_mm` (mm).
- **Method:** ΔL_free = αLΔT, ε_th = αΔT, σ = −f E αΔT,
  ΔL_actual = (1−f)αLΔT, one-dimensional linear thermoelasticity at
  uniform temperature. Intermediate restraint fractions approximate a
  fixed fraction of free strain; real joints/springs need a stiffness model.
  This ignores transverse restraint, Poisson stresses and thermal gradients.

### `composite_moduli`

- **Inputs:** `fiber_volume_fraction` (dimensionless, 0 to 1),
  `fiber_axial_modulus_gpa` (GPa), `fiber_transverse_modulus_gpa`
  (GPa), `matrix_modulus_gpa` (GPa), `halpin_tsai_shape_xi`
  (dimensionless, positive).
- **Outputs:** `longitudinal_rule_mixtures_modulus_gpa` (GPa),
  `transverse_inverse_rule_modulus_gpa` (GPa),
  `halpin_tsai_modulus_gpa` (GPa),
  `halpin_tsai_eta_dimensionless` (1).
- **Method:** iso-strain E_L = V_f E_f,axial + (1−V_f) E_m;
  iso-stress 1/E_T = V_f/E_f,transverse + (1−V_f)/E_m. Halpin–Tsai:
  η = (E_f,transverse/E_m − 1)/(E_f,transverse/E_m + ξ),
  E_HT = E_m(1 + ξηV_f)/(1−ηV_f). Separate **axial and transverse**
  fiber moduli matter for strongly anisotropic carbon fibers; a single
  230 GPa carbon axial modulus must not be silently reused transversely.
  ξ depends on aspect ratio, fiber geometry and loading direction;
  for circular long fibers transverse loading ξ≈2 is a common model.
  No voids, imperfect adhesion or nonlinear interactions are modeled.

### `hardness_e140`

- **Inputs:** `e140_table` (code `table1_non_austenitic_hrc` or
  `table2_non_austenitic_hrb`), `from_scale` and `to_scale` (hardness
  scale codes; respectively `HRC`, `HV`, `HBW` for Table 1, or `HRB`,
  `HV`, `HBS` for Table 2), `hardness_reading` (points **in its selected
  scale**). All codes are unitless strings, and the selected hardness
  scale is the unit of `hardness_reading`.
- **Outputs:** `original_hardness_<scale>` and
  `converted_hardness_<scale>` (each in the named HRC/HRB/HV/HBW/HBS
  scale; a hardness number is **not** a stress in MPa).
- **Method:** piecewise-linear interpolation **only between tabulated
  adjacent values**. Embedded values are from the earlier **ASTM
  E140-12b(2019)e1**, Tables 1/2, cross-checked against a publicly posted
  reference chart. Table 1 covers **20–68 HRC** ↔ **238–940 HV**;
  Brinell HBW is available only for **20–59 HRC** (226–634 HBW).
  Table 2 covers **55–100 HRB** ↔ **100–240 HV/HBS**. Untabulated
  or outside-scale conversions throw `ValueError`; Table 1 and Table 2
  cannot be merged through the overlapping HV range. Rockwell B's older
  table lists **HBS** (steel-ball Brinell) rather than HBW (carbide-ball
  Brinell); the implementation does not interchange them.
- **Scope:** non-austenitic carbon, alloy and tool steels under the
  listed homogeneous conditions **only**. Not valid for austenitic
  stainless (e.g. 304/316), aluminum, copper alloys, white iron or
  arbitrary castings (ASTM specifies separate tables). Converted values
  are **approximate and not traceable measurements**; they should
  primarily be used for specification limits when a direct measurement is
  impossible, not as a substitute for testing. Interpolated non-knot
  values must not be reported to more precision than the tabulated data.
  The numerical table is **not claimed to be verified against the newly
  issued E140-26**; use that edition directly when contractually required.

### `arrhenius_diffusion_profile`

- **Inputs:** `diffusion_prefactor_m2_s` (m²/s, D₀),
  `activation_energy_kj_mol` (kJ/mol, Q), `temperature_k` (K),
  `elapsed_time_s` (s), increasing `depth_mm` (mm),
  `initial_solute_wt_percent` and `surface_solute_wt_percent` (both wt%).
- **Outputs:** `diffusivity_m2_s` (m²/s),
  `two_sqrt_dt_diffusion_length_mm` (mm),
  `concentration_at_last_depth_wt_percent` (wt%), and a depth/concentration
  table and plot in **mm / wt%**.
- **Method:** D = D₀ exp[−1000 Q/(R T)] with R =
  8.31446261815324 J/(mol·K), and
  C(x,t) = C₀ + (C_s − C₀) erfc[x/(2√(Dt))]. The erfc form
  avoids subtracting `erf` from 1 at large depth. Valid for a **semi-infinite
  solid** with an instantaneous, constant surface composition, uniform
  initial composition, Fickian constant D, no reaction or concentration-
  dependent diffusivity, and t > 0. `C(0,t) = C_s`; the far field tends
  to C₀. Models both carburizing and decarburizing. Reject zero/negative
  diffusivity, time/temperature, invalid depth ordering and concentration
  outside 0–100 wt%. Convert °C to K before input.

## Published worked checks and numerical precision

The new test file `backend/tests/test_scientific_materials.py` includes
reference-value checks **to the digits stated**, plus independently derived
analytical/origin/round-trip/negative-input tests. Numeric examples are
illustrative; precision is not material property uncertainty.

| Source/method | Published or derived worked reference | Test assertion |
| --- | --- | --- |
| Callister & Rethwisch, tensile chapter | Conventional 0.2% parallel elastic offset | Synthesized piecewise-linear curve: E = 200,000 MPa; offset yield = 457.89473684 MPa; elastic resilience = 0.4 MJ/m³ from integration |
| Callister & Rethwisch, *Materials Science and Engineering: An Introduction*, Example 8.1 (glass flaw) | E = 69 GPa, surface energy = 0.3 J/m², σ = 40 MPa; maximum flaw `a` = **8.2 µm** | Convert surface energy to K_c = √(2Eγ) and invert K_c=σ√πa; compare to **8.2 µm to 2 significant digits** |
| Callister & Rethwisch, *Materials Science and Engineering: An Introduction*, Fick II carburizing worked example | 950°C; D = **1.6×10⁻¹¹ m²/s**; C₀ = 0.25 wt%, C_s = 1.20 wt%; at x = 0.5 mm and t ≈ **25,400 s**, C ≈ **0.80 wt%** | Infer D₀ from specified Q and T to reproduce published D; compare computed C = 0.8002 wt% to **0.80 wt%** within example rounding |
| ASTM E140-12b(2019)e1 Table 1, non-austenitic steel | **38 HRC ↔ 372 HV ↔ 353 HBW**; 30 HRC ↔ 302 HV; 68 HRC ↔ 940 HV | Exact table values; interpolated 38.5 HRC → 357.5 HBW; no HBW at 65 HRC |
| ASTM E140-12b(2019)e1 Table 2, non-austenitic steel | **90 HRB ↔ 185 HV/HBS**; 100 HRB ↔ 240 HV/HBS | Exact values and inverse conversion; cross-table or out-of-range requests rejected |
| Halpin–Tsai composite worked example | E_f = 230 GPa, E_m = 3.5 GPa, V_f = .6, ξ = 2 → **17.61 GPa** | 17.614 GPa (0.01 GPa tolerance); separately verify 139.4 GPa iso-strain and 8.55 GPa inverse rule (illustrative isotropic-fiber test) |
| Dowling, stress-life / strain-life conventions | Basquin σa=σ′f(2N)^b; Coffin–Manson combined εa=(σ′f/E)(2N)^b+ε′f(2N)^c | Synthesize amplitudes at **100,000 cycles** and invert to 100,000 cycles; separately verify Goodman/Gerber/Soderberg factors |
| Paris power-law model | Closed-form m=3 integral; log integral for m=2 | Check integration vs analytical expressions at stated inputs; near m=2 numerically stable |
| Thermal elasticity | α = 12×10⁻⁶ K⁻¹; L=1000 mm; ΔT=50 K; E=200,000 MPa | ΔL_free=0.6 mm; fixed-end stress=−120 MPa |

References and context:

1. William D. Callister Jr. and David G. Rethwisch, *Materials Science and
   Engineering: An Introduction*, fracture and diffusion worked examples.
   [Worked glass flaw from chapter 8](https://ocw.ui.ac.id/pluginfile.php/10737/mod_resource/content/1/2.%20book_material-science-callister.pdf),
   [diffusion example reproduced in lecture notes](https://studylib.net/doc/28316046/william-d.-callister-jr.--david-g-rethwisch---callister-s...).
2. Norman E. Dowling, *Mechanical Behavior of Materials*, stress-life and
   strain-life conventions; [fe-safe fatigue theory guide, §5](https://help.3ds.com/2020/English/DSSIMULIA_Established/FesafeFatigueTheoryPdf/FesafeFatigueTheory.pdf)
   for Goodman/Gerber/Soderberg definitions.
3. ASTM International, [**E140-12b(2019)e1**](https://store.astm.org/e0140-12br19e01.html)
   and [**E140-26** (newer, **not** the source of embedded numbers)](https://store.astm.org/e0140-26.html).
   [Published Table 1/2 reference values](https://tecarvepipes.com/hardness-conversion-chart-astm-e140/).
4. Halpin–Tsai equation and
   [worked numerical example](https://theconverterhub.com/en/halpin-tsai/),
   [NASA composite-model context](https://ntrs.nasa.gov/api/citations/20230009799/downloads/AbbottFraileIzquierdoSantos_EDLSeminar_small.pdf).

## Execution

From a checkout with the repository development dependencies installed:

```shell
python -m pytest -q -n0 backend/tests/test_scientific_materials.py
python -m pytest -q -n0 backend/tests/test_scientific_workbench.py
python -m mypy graphlink_plugins/scientific/packs/materials.py
python -m ruff check graphlink_plugins/scientific/packs/materials.py backend/tests/test_scientific_materials.py
```

`OPERATIONS: tuple[Operation, ...]` is explicitly typed so mypy does not
infer a fixed-length tuple from the first three existing entries.
