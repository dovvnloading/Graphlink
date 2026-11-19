# Scientific Workbench — user and developer guide

The Scientific Workbench on `main` provides **16 independently granted packs and 152 registered operations**, from core data inspection through numerical analysis, physical modelling, fitting, inference and uncertainty. Calculations use Python's standard library, run locally and deterministically for the supplied inputs, and are explicitly initiated by the user—not by a model or agent. The comprehensive operation inventory below is derived from [`catalog.py`](../graphlink_plugins/scientific/catalog.py) and every registered module in [`packs/`](../graphlink_plugins/scientific/packs), including `inferential.py` (registered under **Fitting and Inference**).

Connected uncertainty propagation and explicit Graph Runner steps are both part of `main`; each has its own section below. For validation against independent published cases, numerical caveats and corrections, consult the [Scientific Workbench correctness audit](SCIENTIFIC_CORRECTNESS_AUDIT.md) and each pack's linked reference rather than assuming the methods are universally applicable.

## Quick start: a calculation with connected inputs

1. In **Settings → Plugins**, enable the pack(s) needed for the analysis. Grants are **per pack**. Choose **Plugins → Scientific Workbench** to create a node on an empty canvas, or branch a new node from a selected node; choose the operation inside the enabled pack.
2. Inspect the operation's **Example**, fill in **every** declared field, and check the pack reference for model assumptions and edge cases. Scalar, vector and matrix inputs may use compatible measurement suffixes; JSON models and choices go into text fields.
3. Press **Run calculation**. Inspect the summary, named metrics, tabular/XY outputs, and **Inputs and provenance**. Failed runs preserve the last successful result. Use **Export JSON** for the exact canonical result and receipt, or **Export CSV** for tabular, XY or metric data.
4. To reuse an upstream measurement, draw a direct edge from the source node to the destination Scientific Workbench node. On the destination's field choose **Use connected result…**, select an appropriate numerical metric, plot axis, numeric result column or rectangular table. The runtime checks the live upstream checksum, grant, edge, selected values and source identity. A manual edit of a bound field detaches the link; after an upstream change, reselect the latest result and rerun.
5. Save the workspace to persist the nodes, links and verified receipts. A changed successful calculation is one undoable graph edit; unchanged identical repeated submissions do not replace the original receipt. See [Results, persistence and charts](#results-persistence-and-charts).

## Input validation and resource limits

The shared [`core.py`](../graphlink_plugins/scientific/core.py) validator accepts **exactly** the fields declared in the catalog, rejecting unknown or omitted keys. Limits are independent of the stricter, method-specific mathematical restrictions in the linked references.

| Type or artifact | Input contract and hard limit |
| --- | --- |
| `number` | Finite integer or floating number, or a decimal string with compatible unit suffix (≤256 characters). No bool, NaN or infinity; exact integer counts must not lose precision. |
| `integer` | Exact integer within ±(2⁵³−1), or an exactly convertible unit-bearing integer string. |
| `vector` | List of **1–1,024** finite numeric measurements; operations may require paired equal-length or ordered vectors, and impose smaller caps. |
| `matrix` | Rectangular list of **1–1,024** rows, **1–32** columns and **≤4,096** cells; mixed-column units are converted independently when declared. |
| `text` | **1–65,536 characters** per field, including legacy Scientific Core `numeric_csv` data and JSON text inputs. |
| Operation output | `summary` and `values` required; optional `table` ≤1,024 rows/32 columns/4,096 cells, and optional `plot` of 1–1,024 paired x/y points with axis labels; **≤512 KiB** total canonical result JSON. |
| Imported typed CSV/TSV | Separately bounded: **1–100,000** data rows, **1–256** columns, **≤1,000,000** cells, **≤64 MiB** UTF-8 source bytes, **≤128 MiB** normalized JSON asset, **≤65,536** characters per cell and **≤256** per header. |

For example, polynomial evaluation allows at most 128 coefficients and 256 sample locations, the square linear solver at most 32 equations, and the FFT/inferential procedures have their own sample, rank and tolerance constraints. A large typed-table import does **not** bypass the 1,024-value limit on a connected vector field.

### Unit-aware measurements and accepted syntax

Enter `72 km/h` where a field expects `m/s` (normalized to 20 m/s), `2.4e-3 mol/L` for concentration, `20 °C` for absolute temperature, or `18 delta_degF` for a temperature difference (normalized to `10 delta_K`). Bare numeric values are interpreted in the field's declared/default unit. `convert_unit` takes `value` in the named `from_unit` and produces its output in `to_unit`. The runtime stores both normalized inputs and the units the user supplied in `provenance.inputUnits`. Text selectors such as `time_unit` and `abundance_unit` can determine a numerical field's effective unit; the inventory prints the **literal declared** `Field.unit`, even for these dynamic cases.

The dimensional parser in [`units.py`](../graphlink_plugins/scientific/units.py) uses seven SI base dimensions (m, kg, s, A, K, mol, cd), exact rational scales and dimensional compatibility checks. It recognizes named base/derived symbols `m, kg, s, A, K, mol, cd, g, rad, sr, Hz, N, Pa, J, W, C, V, F, ohm/Ω, S, Wb, T, H, lm, lx, Bq, Gy, Sv, kat`; `L, min, h, d, t, ha, au, eV, Wh, bar, atm`; and registered domain symbols such as `deg/°, arcmin, arcsec, mas, %, ppm, ppb, day, yr, Ma, pc, AU, M, u, Da, kDa, U, mU, Jy, mag, solar masses, solar radii` and `MPa√m`. Case-sensitive SI prefixes (including 2022 quetta/ronna/ronto/quecto) are supported; `µ`, `μ` and ASCII `u` can denote micro. For mass use prefixes on `g`, not `kg`.

Compound units accept `*`, `·`, `⋅`, `×`, `/`, space-separated multiplication, `^` or `**` integer powers, parentheses and superscript exponents: `m²/s²`, `kg*m/s^2`, `W/(m*K)`. Exponents are −24 to +24; expressions are capped at 128 characters, 96 tokens and 12 parentheses levels. Names are case-sensitive; e.g. `M` is molarity while `m` is a metre. Conversion requires matching physical dimensions and temperature kinds, **not** just a visually similar unit label. Dimensional equivalence alone does not certify identical physical quantities (Hz and Bq share dimensions). Pack labels that are not registered parser symbols are metadata, not arbitrary convertible tokens.

Standalone `K`, `°C/degC`, `°F/degF` and prefixed kelvins are **absolute temperature points**, with affine offsets and a ≥0 K domain. `delta_K/ΔK`, `delta_degC/Δ°C` and `delta_degF/Δ°F` are **differences** with no affine offset. `°C → delta_degC` is rejected. In products or quotients, `K` denotes an interval; affine Celsius/Fahrenheit points are not valid product factors. Unit factors and integer conversions are checked for overflow, underflow and exactness.

## Typed datasets: CSV and TSV (not the legacy numeric-CSV operation)

A Scientific Workbench node has a separate **Typed data table** panel. Choose **Paste CSV / TSV** or **Choose a file**, select comma or tab (a `.tsv` file selects TSV), and press **Import typed table**. Unlike `core.numeric_csv`, this imports UTF-8 file **bytes**, optional BOM, quoted fields and embedded line breaks into a full typed dataset with independent higher limits. It requires one nonempty header row and at least one data row; empty/duplicate headers, nonrectangular data, invalid values or oversized tables are rejected. The numeric-only `core.numeric_csv` operation remains useful for small, entirely numeric CSV data and its older 65,536-character input cap.

For example:

```csv
Sample,Temperature (degC),IsControl,Collected,Group
A,20.5,true,2026-11-01,control
B,NA,false,2026-11-02,trial
```

Types inferred from nonmissing data are **numeric, text, categorical, datetime** (ISO 8601) and **boolean** (true/false or yes/no, case-insensitive). Type overrides use per-header JSON like `{"Group":"categorical","Collected":"datetime"}`. Default missing markers are blank, `NA`, `N/A` and `null` (case-insensitive); user-specified additional markers are supported. Missing values remain **null**, never zero or false. `Temperature (degC)` retains the full header label and separately records name `Temperature` and unit `degC`; this is **metadata**, not an implicit conversion. The preview shows up to eight rows and eight columns, but the stored dataset is complete.

To use the table in a numerical operation, connect the source node directly to another scientific node, select a **numeric** column for its vector field, and restrict the row window to at most **1,024** rows (the UI initially offers the first 1,024; the `table_column` intent can specify another bounded window). **Reject missing** is the default and fails if the selected window contains nulls; **Drop missing (explicit)** filters them and records the policy and row window in the source selector, without changing the table. Non-numeric columns are not selectable as numeric vectors.

The canonical normalized table (all rows, types, units and nulls) is a SHA-256 **content-addressed AssetStore** JSON asset. Only its hash/ref, schema, counts, first-eight preview and an import receipt travel in graph/node state or the scene wire. The receipt includes the original file-bytes hash, CSV/TSV parsing choices, missing markers, type overrides, normalized table hash and its own checksum. Save/reopen and archive export/import preserve the verified asset. Missing/corrupted assets are reported rather than reconstructed from the preview. Implementation: [`data_table.py`](../graphlink_plugins/scientific/data_table.py).

## Complete operation catalog

Each row below is generated from the **actual** registered `Operation` declarations and result keys in the pack implementations: `pack_id.operation_id` and UI title, every **exact field key**, field **kind** and literal `Field.unit` (— means none declared), named `values` outputs, and optional `table` and/or `plot`. Result keys with templates such as `coefficient_{i}` describe names dynamically indexed by the method; conditionally defined results may be absent or contain a descriptive string. Scientific result components common to **every** operation are `summary`, `values`, `operationId`, `inputs` and `provenance`. Consult each reference for output units, algorithm-specific definitions and scientific validity. Output keys in this inventory denote observable field names, not independent guarantees of precision or applicability.

### Scientific Core (`core`) — 3 operations

**Scientific assumptions and checks:** [Scientific Core reference](SCIENTIFIC_QUANTITATIVE_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `core.numeric_csv` — Import numeric CSV | `csv` (text; `—`) | `rows`, `columns`; `table` |
| `core.convert_unit` — Convert a measurement | `value` (number; `—`); `from_unit` (text; `—`); `to_unit` (text; `—`) | `value ({to_unit})` |
| `core.plot_xy` — Plot paired samples | `x` (vector; `—`); `y` (vector; `—`); `x_label` (text; `—`); `y_label` (text; `—`) | `samples`; `plot` + `table` |

### Mathematics (`mathematics`) — 3 operations

**Scientific assumptions and checks:** [Mathematics reference](SCIENTIFIC_QUANTITATIVE_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `mathematics.polynomial` — Evaluate a polynomial | `coefficients` (vector; `—`); `points` (vector; `—`) | `samples`; `plot` + `table` |
| `mathematics.trapezoid` — Integrate sampled data | `x` (vector; `—`); `y` (vector; `—`) | `integral`, `intervals`; `plot` |
| `mathematics.linear_system` — Solve a linear system | `matrix` (matrix; `—`); `rhs` (vector; `—`) | `order`, `max_absolute_residual`; `table` |

### Statistics (`statistics`) — 3 operations

**Scientific assumptions and checks:** [Statistics reference](SCIENTIFIC_QUANTITATIVE_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `statistics.describe` — Describe a sample | `observations` (vector; `—`) | `count`, `mean`, `median`, `sample_sd`, `minimum`, `maximum` |
| `statistics.linear_regression` — Fit a regression line | `x` (vector; `—`); `y` (vector; `—`) | `count`, `slope`, `intercept`, `pearson_r`, `r_squared`; `plot` |
| `statistics.weighted_summary` — Summarize weighted observations | `observations` (vector; `—`); `weights` (vector; `—`) | `positive_weights`, `weighted_mean`, `population_sd` |

### Physics (`physics`) — 13 operations

**Scientific assumptions and checks:** [Physics reference](SCIENTIFIC_QUANTITATIVE_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `physics.spring_oscillator` — Model a spring oscillator | `mass_kg` (number; `kg`); `stiffness_n_m` (number; `N/m`); `amplitude_m` (number; `m`) | `period_s`, `frequency_hz`, `angular_frequency_rad_s`; `plot` |
| `physics.thermal_layers` — Calculate heat flow through layers | `thickness_m` (vector; `m`); `conductivity_w_mk` (vector; `W/(m·K)`); `area_m2` (number; `m²`); `left_k` (number; `K`); `right_k` (number; `K`) | `resistance_k_w`, `heat_rate_w`, `heat_flux_w_m2`; `table` |
| `physics.sensible_heat` — Calculate sensible heat | `mass_kg` (number; `kg`); `specific_heat_j_kgk` (number; `J/(kg·K)`); `initial_k` (number; `K`); `final_k` (number; `K`) | `temperature_change_k`, `heat_capacity_j_k`, `heat_j` |
| `physics.damped_driven_oscillator` — Solve damped and driven oscillator | `mass_kg` (number; `kg`); `stiffness_n_m` (number; `N/m`); `damping_n_s_m` (number; `N·s/m`); `drive_force_n` (number; `N`); `drive_frequency_hz` (number; `Hz`); `initial_displacement_m` (number; `m`); `initial_velocity_m_s` (number; `m/s`); `time_s` (vector; `s`); `scan_frequency_hz` (vector; `Hz`) | `natural_frequency_hz`, `damping_ratio_1`, `quality_factor_1`, `resonance_peak_frequency_hz`, `resonance_peak_amplitude_m`, `drive_amplitude_m`, `drive_phase_lag_rad`, `last_displacement_m`; `plot` + `table` |
| `physics.projectile_motion` — Model projectile with quadratic air drag | `initial_speed_m_s` (number; `m/s`); `launch_angle_deg` (number; `deg`); `release_height_m` (number; `m`); `gravity_m_s2` (number; `m/s²`); `mass_kg` (number; `kg`); `air_density_kg_m3` (number; `kg/m³`); `drag_coefficient_1` (number; `1`); `cross_section_m2` (number; `m²`); `time_step_s` (number; `s`); `max_duration_s` (number; `s`) | `flight_time_s`, `horizontal_range_m`, `maximum_height_m`, `impact_speed_m_s`; `plot` + `table` |
| `physics.rigid_body_inertia` — Calculate rigid-body moment of inertia | `shape` (text; `1`); `mass_kg` (number; `kg`); `radius_m` (number; `m`); `length_m` (number; `m`); `width_m` (number; `m`); `parallel_axis_offset_m` (number; `m`) | `centroidal_inertia_kg_m2`, `parallel_axis_inertia_kg_m2`, `radius_of_gyration_m`; `table` |
| `physics.transient_heat_1d` — Solve transient 1D heat conduction | `scheme` (text; `1`); `length_m` (number; `m`); `diffusivity_m2_s` (number; `m²/s`); `initial_temperature_k` (number; `K`); `left_boundary_k` (number; `K`); `right_boundary_k` (number; `K`); `nodes` (integer; `1`); `duration_s` (number; `s`); `time_step_s` (number; `s`) | `spatial_step_m`, `fourier_number_1`, `explicit_stability_limit_1`, `time_steps_1`, `midpoint_temperature_k`, `minimum_temperature_k`, `maximum_temperature_k`; `plot` + `table` |
| `physics.doppler_shift` — Calculate Doppler shift | `wave_type` (text; `1`); `emitted_frequency_hz` (number; `Hz`); `medium_wave_speed_m_s` (number; `m/s`); `source_toward_receiver_m_s` (number; `m/s`); `receiver_toward_source_m_s` (number; `m/s`) | `received_frequency_hz`, `frequency_ratio_1`, `fractional_frequency_shift_1`, `wavelength_m` |
| `physics.relativistic_kinematics` — Calculate relativistic energy and time | `rest_mass_kg` (number; `kg`); `velocity_m_s` (number; `m/s`); `proper_interval_s` (number; `s`) | `beta_1`, `lorentz_factor_1`, `dilated_interval_s`, `relativistic_momentum_kg_m_s`, `rest_energy_j`, `kinetic_energy_j`, `total_energy_j` |
| `physics.geometric_optics` — Solve lenses, mirrors and Snell refraction | `calculation` (text; `1`); `focal_length_m` (number; `m`); `object_distance_m` (number; `m`); `index_incident_1` (number; `1`); `index_transmitted_1` (number; `1`); `incidence_angle_deg` (number; `deg`) | `refracted_angle_deg`, `reflected_angle_deg`, `critical_angle_deg`, `total_internal_reflection_1`, `image_distance_m`, `lateral_magnification_1`, `image_type_1` |
| `physics.slit_diffraction` — Model single/double slit intensity | `pattern` (text; `1`); `wavelength_m` (number; `m`); `slit_width_m` (number; `m`); `slit_separation_m` (number; `m`); `screen_distance_m` (number; `m`); `central_intensity_w_m2` (number; `W/m²`); `screen_position_m` (vector; `m`) | `central_intensity_w_m2`, `single_slit_first_minimum_deg`, `paraxial_fringe_spacing_m`; `plot` + `table` |
| `physics.series_rlc_response` — Solve series RLC transient and AC response | `resistance_ohm` (number; `Ω`); `inductance_h` (number; `H`); `capacitance_f` (number; `F`); `step_voltage_v` (number; `V`); `initial_capacitor_voltage_v` (number; `V`); `initial_current_a` (number; `A`); `drive_amplitude_v` (number; `V`); `time_s` (vector; `s`); `scan_frequency_hz` (vector; `Hz`) | `natural_frequency_hz`, `quality_factor_1`, `half_power_bandwidth_hz`, `last_capacitor_voltage_v`, `last_current_a`; `plot` + `table` |
| `physics.series_rlc_frequency` — Analyze series RLC complex impedance | `resistance_ohm` (number; `Ω`); `inductance_h` (number; `H`); `capacitance_f` (number; `F`); `drive_amplitude_v` (number; `V`); `frequency_hz` (vector; `Hz`) | `natural_frequency_hz`, `quality_factor_1`, `half_power_bandwidth_hz`, `peak_current_amplitude_a`; `plot` + `table` |

### Astronomy (`astronomy`) — 12 operations

**Scientific assumptions and checks:** [Astronomy reference](SCIENTIFIC_ASTRONOMY_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `astronomy.parallax_distance` — Estimate distance from parallax | `parallax_mas` (number; `mas`) | `distance_pc`, `distance_ly` |
| `astronomy.distance_modulus` — Calculate a photometric distance | `apparent_mag` (number; `mag`); `absolute_mag` (number; `mag`); `extinction_mag` (number; `mag`) | `true_distance_modulus_mag`, `distance_pc` |
| `astronomy.blackbody_peak` — Find a blackbody spectral peak | `temperature_k` (number; `K`) | `peak_wavelength_nm` |
| `astronomy.keplerian_orbit` — Propagate a Keplerian orbit | `semimajor_axis_au` (number; `AU`); `eccentricity` (number; `1`); `inclination_deg` (number; `deg`); `ascending_node_deg` (number; `deg`); `argument_periapsis_deg` (number; `deg`); `mean_anomaly_epoch_deg` (number; `deg`); `epoch_jd` (number; `JD (days)`); `target_jd` (number; `JD (days)`); `central_mass_solar` (number; `solar masses`) | `x_au`, `y_au`, `z_au`, `vx_au_per_day`, `vy_au_per_day`, `vz_au_per_day`, `radius_au`, `speed_au_per_day`, `mean_anomaly_deg`, `eccentric_anomaly_deg`, `true_anomaly_deg`, `orbital_period_days` |
| `astronomy.julian_sidereal_time` — Julian date and sidereal time | `utc_iso` (text; `ISO-8601 UTC`); `observer_longitude_deg_east` (number; `deg`) | `julian_date_jd`, `greenwich_mean_sidereal_hours`, `local_mean_sidereal_hours` |
| `astronomy.coordinate_frames` — Convert sky coordinate frames | `source_frame` (text; `frame`); `target_frame` (text; `frame`); `source_longitude_deg` (number; `deg`); `source_latitude_deg` (number; `deg`); `utc_iso` (text; `ISO-8601 UTC`); `observer_latitude_deg` (number; `deg`); `observer_longitude_deg_east` (number; `deg`) | `target_longitude_deg`, `target_latitude_deg`, `julian_date_jd`, `local_mean_sidereal_hours` |
| `astronomy.airmass_extinction` — Estimate airmass and extinction | `apparent_altitude_deg` (number; `deg`); `extinction_coefficient_mag_per_airmass` (number; `mag/airmass`); `observed_mag` (number; `mag`) | `relative_airmass`, `extinction_mag`, `transmission_fraction`, `above_atmosphere_mag` |
| `astronomy.flat_lcdm_distances` — Cosmological distances and lookback time | `redshift` (number; `1`); `h0_km_s_mpc` (number; `km/(s·Mpc)`); `omega_matter` (number; `1`) | `comoving_distance_mpc`, `luminosity_distance_mpc`, `angular_diameter_distance_mpc`, `lookback_time_gyr` |
| `astronomy.ab_flux_to_magnitude` — Convert AB flux to magnitude | `flux_density_jy` (number; `Jy`) | `ab_magnitude_mag` |
| `astronomy.ab_magnitude_to_flux` — Convert AB magnitude to flux | `ab_magnitude_mag` (number; `mag AB`) | `flux_density_jy` |
| `astronomy.ab_colour_index` — AB colour and flux ratio | `first_ab_mag` (number; `mag AB`); `second_ab_mag` (number; `mag AB`) | `colour_index_mag`, `first_to_second_flux_ratio` |
| `astronomy.exoplanet_transit` — Estimate exoplanet transit parameters | `out_of_transit_flux_relative` (number; `relative flux`); `in_transit_flux_relative` (number; `relative flux`); `stellar_radius_solar` (number; `solar radii`); `semimajor_axis_au` (number; `AU`); `orbital_period_days` (number; `days`); `impact_parameter_stellar_radii` (number; `stellar radii`) | `transit_depth_fraction`, `transit_depth_ppm`, `radius_ratio_planet_star`, `planet_radius_earth`, `total_duration_hours`, `full_duration_hours`, `ingress_duration_hours` |

### Engineering (`engineering`) — 9 operations

**Scientific assumptions and checks:** [Engineering reference](SCIENTIFIC_QUANTITATIVE_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `engineering.resistor_network` — Combine ideal resistors | `resistance_ohm` (vector; `Ω`) | `series_ohm`, `parallel_ohm` |
| `engineering.rc_step` — Plot an RC step response | `resistance_ohm` (number; `Ω`); `capacitance_f` (number; `F`); `step_v` (number; `V`); `time_s` (vector; `s`) | `time_constant_s`, `final_sample_v`; `plot` + `table` |
| `engineering.linear_expansion` — Calculate thermal expansion | `length_m` (number; `m`); `coefficient_per_k` (number; `1/K`); `temperature_change_k` (number; `K`) | `strain`, `length_change_m`, `final_length_m` |
| `engineering.beam_response` — Beam deflection, slope, shear and moment | `support` (text; `1 (choice)`); `length_m` (number; `m`); `young_modulus_pa` (number; `Pa`); `second_moment_m4` (number; `m⁴`); `point_load_n` (number; `N`); `point_location_m` (number; `m`); `uniform_load_n_m` (number; `N/m`); `position_m` (vector; `m`) | `reaction_left_n`, `reaction_right_n`, `fixed_end_internal_moment_nm`, `max_deflection_m`, `max_deflection_at_m`, `max_abs_slope_rad`, `max_abs_bending_moment_nm`, `max_abs_bending_moment_at_m`; `plot` + `table` |
| `engineering.combined_stress` — Axial, bending and von Mises yield check | `axial_force_n` (number; `N`); `bending_moment_nm` (number; `N·m`); `area_m2` (number; `m²`); `section_modulus_m3` (number; `m³`); `shear_force_n` (number; `N`); `shear_area_m2` (number; `m²`); `yield_strength_pa` (number; `Pa`); `safety_factor_1` (number; `1`) | `axial_stress_pa`, `bending_stress_pa`, `shear_stress_pa`, `normal_stress_face_a_pa`, `normal_stress_face_b_pa`, `von_mises_face_a_pa`, `von_mises_face_b_pa`, `max_von_mises_pa`, `allowable_von_mises_pa`, `utilization_ratio_1`, `yield_check`; `table` |
| `engineering.pipe_flow` — Pipe Reynolds number and Darcy–Weisbach loss | `diameter_m` (number; `m`); `length_m` (number; `m`); `roughness_m` (number; `m`); `volume_flow_m3_s` (number; `m³/s`); `density_kg_m3` (number; `kg/m³`); `viscosity_pa_s` (number; `Pa·s`); `gravity_m_s2` (number; `m/s²`) | `velocity_m_s`, `reynolds_number_1`, `relative_roughness_1`, `darcy_friction_factor_1`, `colebrook_iterations_count`, `colebrook_residual_1`, `head_loss_m`, `pressure_drop_pa`, `flow_regime` |
| `engineering.heat_exchanger_sizing` — Size parallel/counterflow exchanger: LMTD and ε–NTU | `flow_arrangement` (text; `1 (choice)`); `hot_inlet_c` (number; `°C`); `cold_inlet_c` (number; `°C`); `hot_mass_flow_kg_s` (number; `kg/s`); `cold_mass_flow_kg_s` (number; `kg/s`); `hot_cp_j_kg_k` (number; `J/(kg·K)`); `cold_cp_j_kg_k` (number; `J/(kg·K)`); `overall_u_w_m2_k` (number; `W/(m²·K)`); `target_heat_w` (number; `W`) | `hot_outlet_c`, `cold_outlet_c`, `hot_capacity_rate_w_k`, `cold_capacity_rate_w_k`, `capacity_rate_ratio_1`, `max_possible_heat_w`, `target_heat_w`, `effectiveness_1`, `ntu_1`, `terminal_difference_1_k`, `terminal_difference_2_k`, `lmtd_k`, `required_area_lmtd_m2`, `required_area_ntu_m2`, `area_method_difference_percent`; `table` |
| `engineering.transfer_function_response` — First/second-order steps, Bode and stability margins | `order` (integer; `1`); `dc_gain_1` (number; `1`); `time_constant_s` (number; `s`); `natural_frequency_rad_s` (number; `rad/s`); `damping_ratio_1` (number; `1`); `step_amplitude_1` (number; `1`); `time_s` (vector; `s`); `angular_frequency_rad_s` (vector; `rad/s`) | `final_step_output_1`, `rise_time_10_90_s`, `peak_overshoot_percent`, `settling_time_2_percent_s`, `first_peak_time_s`, `gain_margin_ratio_1`, `gain_margin_db`, `phase_margin_deg`, `gain_crossover_rad_s`, `unity_gain_crossings_count`; `plot` + `table` |
| `engineering.pid_closed_loop` — PID-controlled first-order plant closed-loop step | `plant_gain_1` (number; `1`); `plant_tau_s` (number; `s`); `kp_1` (number; `1`); `ki_per_s` (number; `1/s`); `kd_s` (number; `s`); `setpoint_1` (number; `1`); `time_s` (vector; `s`) | `closed_loop_time_constant_s (P-only)`, `closed_loop_natural_frequency_rad_s (with integral)`, `closed_loop_damping_ratio_1 (with integral)`, `steady_state_output_1`, `steady_state_error_1`, `last_sample_output_1`, `last_sample_error_1`; `plot` + `table` |

### Chemistry (`chemistry`) — 13 operations

**Scientific assumptions and checks:** [Chemistry reference](SCIENTIFIC_NATURAL_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `chemistry.beer_lambert` — Beer–Lambert concentration | `absorbance` (number; `—`); `molar_absorptivity` (number; `L/(mol·cm)`); `path_length_cm` (number; `cm`) | `concentration_mol_l` |
| `chemistry.transmission_spectrum` — Convert transmission to absorbance | `wavelength_nm` (vector; `nm`); `transmission_percent` (vector; `%`) | `peak_absorbance`, `peak_wavelength_nm`; `plot` + `table` |
| `chemistry.isotope_average` — Isotope-weighted atomic mass | `isotope_mass_u` (vector; `u`); `abundance_percent` (vector; `%`) | `mean_atomic_mass_u`, `abundance_total_percent` |
| `chemistry.acid_base_speciation` — Acid/base pH and polyprotic speciation | `family` (text; `category: acid \| base`); `pka_values` (vector; `pKa`); `formal_concentration_mol_l` (number; `mol/L`); `spectator_charge_mol_l` (number; `mol charge/L`) | `ph`, `poh`, `hydrogen_mol_l`, `hydroxide_mol_l`, `dominant_form_protons_lost`; `plot` + `table` |
| `chemistry.buffer_ph_capacity` — Buffer pH and capacity: Henderson–Hasselbalch vs exact | `pka` (number; `pKa`); `acid_concentration_mol_l` (number; `mol/L`); `base_concentration_mol_l` (number; `mol/L`) | `ph_henderson_hasselbalch`, `ph_charge_balance`, `ph_exact_minus_hh`, `poh_charge_balance`, `buffer_capacity_hh_mol_l_per_ph`, `buffer_capacity_exact_mol_l_per_ph`, `hydrogen_mol_l`, `hydroxide_mol_l`, `equilibrium_base_fraction`; `table` |
| `chemistry.titration_curve` — Polyprotic acid/base titration with equivalence points | `family` (text; `category: acid \| base`); `pka_values` (vector; `pKa`); `analyte_concentration_mol_l` (number; `mol/L`); `analyte_volume_ml` (number; `mL`); `titrant_concentration_mol_l` (number; `mol/L`); `max_titrant_volume_ml` (number; `mL`); `samples` (integer; `count`) | `initial_ph`, `final_ph`, `equivalence_count`; `plot` + `table` |
| `chemistry.arrhenius_fit` — Arrhenius activation-energy regression | `temperature_k` (vector; `K`); `rate_constant` (vector; `see rate_constant_unit`); `rate_constant_unit` (text; `unit label`) | `activation_energy_kj_mol`, `activation_energy_standard_error_kj_mol`, `ln_prefactor`, `prefactor`, `prefactor_unit`, `r_squared`, `residual_degrees_of_freedom`; `plot` + `table` |
| `chemistry.rate_law_fit` — First/second-order integrated rate-law fit | `order` (text; `category: first \| second`); `time_s` (vector; `s`); `concentration_mol_l` (vector; `mol/L`) | `initial_concentration_mol_l`, `half_life_s`, `r_squared_transformed`, `residual_degrees_of_freedom`; `plot` + `table` |
| `chemistry.gas_pressure` — Ideal and van der Waals gas pressure | `amount_mol` (number; `mol`); `temperature_k` (number; `K`); `volume_l` (number; `L`); `a_l2_bar_mol2` (number; `L²·bar/mol²`); `b_l_mol` (number; `L/mol`) | `ideal_pressure_bar`, `van_der_waals_pressure_bar`, `compressibility_factor_vdw`, `vdw_minus_ideal_bar` |
| `chemistry.gas_volume` — Ideal and gas-branch van der Waals volume | `amount_mol` (number; `mol`); `temperature_k` (number; `K`); `pressure_bar` (number; `bar`); `a_l2_bar_mol2` (number; `L²·bar/mol²`); `b_l_mol` (number; `L/mol`) | `ideal_volume_l`, `van_der_waals_gas_volume_l`, `compressibility_factor_vdw`, `vdw_minus_ideal_l` |
| `chemistry.molar_mass` — Molar mass of grouped and hydrated formulas | `formula` (text; `formula string`) | `molar_mass_g_mol`, `element_count`, `atoms_per_formula_unit`; `table` |
| `chemistry.solution_dilution` — Prepare a dilution from stock solution | `stock_concentration_mol_l` (number; `mol/L`); `aliquot_volume_ml` (number; `mL`); `final_volume_ml` (number; `mL`) | `final_concentration_mol_l`, `dilution_factor`, `solute_amount_mmol`, `diluent_volume_ml` |
| `chemistry.reaction_yield` — Limiting reagent and percent product yield | `reactant_coefficients` (vector; `1`); `reactant_mass_g` (vector; `g`); `reactant_molar_mass_g_mol` (vector; `g/mol`); `product_coefficient` (number; `1`); `product_molar_mass_g_mol` (number; `g/mol`); `actual_product_mass_g` (number; `g`) | `limiting_reactant_index`, `reaction_extent_mol`, `theoretical_product_mol`, `theoretical_product_mass_g`, `actual_product_mass_g`, `percent_yield`; `table` |

### Biology and Ecology (`biology`) — 11 operations

**Scientific assumptions and checks:** [Biology and Ecology reference](SCIENTIFIC_BIOLOGY_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `biology.community_diversity` — Community diversity | `counts` (vector; `individuals`) | `individuals`, `richness`, `shannon_nats`, `gini_simpson` |
| `biology.quadrat_density` — Quadrat survey density | `counts` (vector; `individuals`); `area_m2` (vector; `m²`) | `pooled_density_per_m2`, `individuals`, `surveyed_area_m2`; `table` |
| `biology.community_overlap` — Community overlap | `site_a_counts` (vector; `individuals`); `site_b_counts` (vector; `individuals`) | `shared_taxa`, `union_taxa`, `jaccard_similarity`, `sorensen_similarity` |
| `biology.logistic_growth_fit` — Fit logistic population growth | `time` (vector; `selected time unit`); `abundance` (vector; `selected abundance unit`); `time_unit` (text; `unit label`); `abundance_unit` (text; `unit label`) | `model`, `carrying_capacity`, `carrying_capacity_unit`, `growth_rate_per_time_unit`, `growth_rate_unit`, `growth_rate_standard_error`, `growth_rate_standard_error_unit`, `carrying_capacity_standard_error`, `carrying_capacity_standard_error_unit`, `inflection_time`, `inflection_time_unit`, `initial_fitted_abundance`, `initial_fitted_abundance_unit`, `initial_specific_growth_rate_per_time_unit`, `initial_specific_growth_rate_unit`, `doubling_time_from_first_sample`, `doubling_time_unit`, `observations`, `residual_sum_squares`, `residual_standard_deviation`, `degrees_of_freedom`, `converged`, `r_squared (if defined)`; `plot` + `table` |
| `biology.gompertz_growth_fit` — Fit Gompertz population growth | `time` (vector; `selected time unit`); `abundance` (vector; `selected abundance unit`); `time_unit` (text; `unit label`); `abundance_unit` (text; `unit label`) | `model`, `carrying_capacity`, `carrying_capacity_unit`, `growth_rate_per_time_unit`, `growth_rate_unit`, `growth_rate_standard_error`, `growth_rate_standard_error_unit`, `carrying_capacity_standard_error`, `carrying_capacity_standard_error_unit`, `inflection_time`, `inflection_time_unit`, `initial_fitted_abundance`, `initial_fitted_abundance_unit`, `initial_specific_growth_rate_per_time_unit`, `initial_specific_growth_rate_unit`, `doubling_time_from_first_sample`, `doubling_time_unit`, `observations`, `residual_sum_squares`, `residual_standard_deviation`, `degrees_of_freedom`, `converged`, `r_squared (if defined)`; `plot` + `table` |
| `biology.michaelis_menten_fit` — Fit Michaelis–Menten enzyme kinetics | `substrate` (vector; `selected concentration unit`); `initial_rate` (vector; `selected rate unit`); `substrate_unit` (text; `unit label`); `rate_unit` (text; `unit label`) | `vmax`, `vmax_standard_error`, `vmax_unit`, `km`, `km_standard_error`, `km_unit`, `lineweaver_burk_vmax_comparison`, `lineweaver_burk_vmax_unit`, `lineweaver_burk_km_comparison`, `lineweaver_burk_km_unit`, `residual_standard_deviation_unit`, `residual_sum_squares_unit`, `r_squared_unit`; `plot` + `table` |
| `biology.dose_response_4pl_fit` — Fit dose–response (4PL) | `concentration` (vector; `selected concentration unit`); `response` (vector; `selected response unit`); `concentration_unit` (text; `unit label`); `response_unit` (text; `unit label`); `potency_metric` (text; `category (dimensionless)`) | `potency_metric`, `potency_concentration`, `potency_unit`, `potency_standard_error`, `potency_ci95_lower`, `potency_ci95_upper`, `hill_slope`, `hill_slope_standard_error`, `hill_slope_unit`, `low_dose_response`, `low_dose_response_standard_error`, `high_dose_response`, `high_dose_response_standard_error`, `plateau_unit`, `log_mid`, `log_mid_unit`, `residual_standard_deviation_unit`, `residual_sum_squares_unit`, `r_squared_unit`; `plot` + `table` |
| `biology.lotka_volterra` — Simulate Lotka–Volterra predator–prey | `initial_prey` (number; `individuals`); `initial_predators` (number; `individuals`); `prey_growth_rate` (number; `1 / selected time unit`); `predation_rate` (number; `1 / (predator × time unit)`); `conversion_rate` (number; `1 / (prey × time unit)`); `predator_death_rate` (number; `1 / selected time unit`); `step_size` (number; `selected time unit`); `steps` (integer; `steps (dimensionless)`); `time_unit` (text; `unit label`) | `initial_prey`, `initial_predators`, `final_prey`, `final_predators`, `population_unit`, `duration`, `time_unit`, `prey_growth_rate`, `prey_growth_rate_unit`, `predation_rate`, `predation_rate_unit`, `conversion_rate`, `conversion_rate_unit`, `predator_death_rate`, `predator_death_rate_unit`; `plot` + `table` |
| `biology.hardy_weinberg` — Hardy–Weinberg equilibrium and chi-square | `homozygote_aa` (integer; `individuals`); `heterozygote_ab` (integer; `individuals`); `homozygote_bb` (integer; `individuals`) | `individuals`, `individuals_unit`, `allele_a_frequency`, `allele_b_frequency`, `allele_frequency_unit`, `chi_square`, `chi_square_unit`, `degrees_of_freedom`, `p_value`, `p_value_unit`, `expected_count_below_5`, `expected_count_below_5_unit`, `degrees_of_freedom_unit`, `warning`; `plot` + `table` |
| `biology.kaplan_meier_logrank` — Kaplan–Meier and log-rank (two groups) | `time_a` (vector; `selected time unit`); `event_a` (vector; `0/1 (dimensionless)`); `time_b` (vector; `selected time unit`); `event_b` (vector; `0/1 (dimensionless)`); `time_unit` (text; `unit label`) | `group_a_size`, `group_b_size`, `group_size_unit`, `observed_events_a`, `expected_events_a`, `event_count_unit`, `logrank_variance`, `logrank_variance_unit`, `logrank_chi_square`, `logrank_chi_square_unit`, `logrank_degrees_of_freedom`, `logrank_degrees_of_freedom_unit`, `logrank_p_value`, `logrank_p_value_unit`, `median_survival_a`, `median_survival_b`, `median_survival_unit`, `final_survival_a`, `final_survival_b`, `survival_probability_unit`, `greenwood_se_a`, `greenwood_se_b`, `greenwood_se_unit`; `plot` + `table` |
| `biology.richness_rarefaction` — Species richness: Chao1 and rarefaction | `counts` (vector; `individuals per taxon`); `subsample_individuals` (integer; `individuals`) | `observed_richness`, `chao1_estimated_richness`, `richness_unit`, `singleton_taxa`, `doubleton_taxa`, `singleton_doubleton_unit`, `individuals`, `individuals_unit`, `subsample_individuals`, `subsample_unit`, `rarefied_expected_richness`, `rarefied_expected_richness_unit`, `chao1_method`; `plot` + `table` |

### Earth Science (`earth`) — 14 operations

**Scientific assumptions and checks:** [Earth Science reference](SCIENTIFIC_EARTH_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `earth.great_circle_distance` — Great-circle distance | `latitude_a` (number; `°`); `longitude_a` (number; `°`); `latitude_b` (number; `°`); `longitude_b` (number; `°`) | `distance_km`, `central_angle_deg`, `sphere_radius_km` |
| `earth.geothermal_gradient` — Two-depth geothermal gradient | `shallow_depth_m` (number; `m`); `deep_depth_m` (number; `m`); `shallow_temperature_c` (number; `°C`); `deep_temperature_c` (number; `°C`) | `gradient_c_per_km`, `depth_interval_m`, `temperature_change_c` |
| `earth.seismic_distance` — Seismic S–P distance | `s_minus_p_s` (number; `s`); `p_speed_km_s` (number; `km/s`); `s_speed_km_s` (number; `km/s`) | `path_length_km`, `p_travel_s`, `s_travel_s` |
| `earth.geodesic_inverse` — WGS84 distance and bearings | `latitude_a_deg` (number; `°N`); `longitude_a_deg` (number; `°E`); `latitude_b_deg` (number; `°N`); `longitude_b_deg` (number; `°E`) | `distance_m`, `initial_bearing_deg`, `reverse_bearing_deg`, `solution_method` |
| `earth.utm_forward` — Convert WGS84 latitude/longitude to UTM | `latitude_deg` (number; `°N`); `longitude_deg` (number; `°E`) | `zone_1`, `hemisphere`, `easting_m`, `northing_m` |
| `earth.utm_inverse` — Convert WGS84 UTM to latitude/longitude | `zone_1` (integer; `1 (zone)`); `hemisphere` (text; `N/S`); `easting_m` (number; `m`); `northing_m` (number; `m`) | `latitude_deg`, `longitude_deg` |
| `earth.radiometric_age` — Calculate closed-system radiometric age | `parent_ratio_1` (number; `1`); `radiogenic_daughter_ratio_1` (number; `1`); `half_life_ma` (number; `Ma`); `half_life_standard_uncertainty_ma` (number; `Ma`) | `age_ma`, `age_standard_uncertainty_ma`, `daughter_parent_ratio_1` |
| `earth.darcy_flow` — Darcy groundwater flow and pore velocity | `hydraulic_conductivity_m_s` (number; `m/s`); `head_upstream_m` (number; `m`); `head_downstream_m` (number; `m`); `flow_length_m` (number; `m`); `cross_section_area_m2` (number; `m²`); `effective_porosity_1` (number; `1`) | `hydraulic_gradient_1`, `darcy_flux_m_s`, `discharge_m3_s`, `pore_velocity_m_s` |
| `earth.theis_drawdown` — Theis confined aquifer well drawdown | `pumping_rate_m3_s` (number; `m³/s`); `transmissivity_m2_s` (number; `m²/s`); `storativity_1` (number; `1`); `radial_distance_m` (number; `m`); `time_since_pumping_s` (number; `s`) | `drawdown_m`, `theis_u_1`, `well_function_e1_1` |
| `earth.moment_magnitude` — Seismic moment to moment magnitude Mw | `seismic_moment_nm` (number; `N·m`) | `moment_magnitude_mw`, `estimated_radiated_energy_j` |
| `earth.seismic_moment` — Moment magnitude Mw to seismic moment | `moment_magnitude_mw` (number; `Mw (1)`) | `seismic_moment_nm`, `estimated_radiated_energy_j` |
| `earth.gutenberg_richter` — Fit Gutenberg–Richter b-value | `magnitudes_mw` (vector; `Mw (1)`); `completeness_magnitude_mw` (number; `Mw (1)`); `bin_width_mw` (number; `Mw (1)`); `target_magnitude_mw` (number; `Mw (1)`) | `events_above_completeness_1`, `events_below_completeness_1`, `a_intercept_log10_count_1`, `b_value_per_mw`, `b_approx_standard_error_per_mw`, `predicted_count_at_target_1`, `observed_count_at_target_1`; `table` |
| `earth.isa_atmosphere` — International Standard Atmosphere profile | `geometric_altitude_m` (number; `m`) | `geopotential_altitude_m`, `temperature_k`, `temperature_c`, `pressure_pa`, `density_kg_m3` |
| `earth.solar_position` — Solar elevation and true-north azimuth | `timestamp_iso8601` (text; `ISO 8601 timestamp`); `latitude_deg` (number; `°N`); `longitude_deg` (number; `°E`) | `elevation_deg`, `geometric_elevation_deg`, `azimuth_deg`, `declination_deg`, `equation_of_time_min`, `standard_refraction_correction_deg` |

### Environmental Science (`environment`) — 11 operations

**Scientific assumptions and checks:** [Environmental Science reference](SCIENTIFIC_ENVIRONMENT_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `environment.flow_weighted_concentration` — Flow-weighted concentration | `flow_m3_s` (vector; `m³/s`); `duration_s` (vector; `s`); `concentration_mg_l` (vector; `mg/L`) | `mean_concentration_mg_l`, `total_water_m3`, `total_constituent_kg`; `table` |
| `environment.rainfall_runoff` — Observed event runoff coefficient | `rainfall_mm` (number; `mm`); `area_km2` (number; `km²`); `runoff_volume_m3` (number; `m³`) | `rainfall_volume_m3`, `runoff_depth_mm`, `runoff_coefficient` |
| `environment.degree_days` — Growing degree-days | `daily_min_c` (vector; `°C`); `daily_max_c` (vector; `°C`); `base_c` (number; `°C`) | `growing_degree_days_c`, `days_observed`; `plot` + `table` |
| `environment.air_quality_index` — US EPA daily Air Quality Index | `o3_8h_ppm` (number; `ppm`); `o3_1h_ppm` (number; `ppm`); `pm25_24h_ug_m3` (number; `µg/m³`); `pm10_24h_ug_m3` (number; `µg/m³`); `co_8h_ppm` (number; `ppm`); `so2_1h_ppb` (number; `ppb`); `so2_24h_ppb` (number; `ppb`); `no2_1h_ppb` (number; `ppb`) | `aqi`, `aqi_category`, `main_pollutant`, `subindices_calculated`; `table` |
| `environment.gaussian_plume` — Gaussian plume, Pasquill–Gifford A–F | `emission_rate_g_s` (number; `g/s`); `wind_speed_m_s` (number; `m/s`); `downwind_m` (number; `m`); `crosswind_m` (number; `m`); `receptor_height_m` (number; `m`); `effective_stack_height_m` (number; `m`); `stability_class` (text; `category (1)`); `terrain` (text; `category (1)`) | `sigma_y_m`, `sigma_z_m`, `receptor_concentration_ug_m3`, `ground_centerline_concentration_ug_m3`; `plot` |
| `environment.dissolved_oxygen_sag` — Streeter–Phelps river oxygen sag | `initial_do_mg_l` (number; `mg/L`); `saturation_do_mg_l` (number; `mg/L`); `ultimate_bod_mg_l` (number; `mg/L`); `deoxygenation_per_day` (number; `day⁻¹`); `reaeration_per_day` (number; `day⁻¹`); `velocity_m_s` (number; `m/s`); `reach_length_km` (number; `km`) | `initial_deficit_mg_l`, `outlet_do_mg_l`, `minimum_do_mg_l`, `minimum_do_distance_km`, `travel_time_day`; `plot` + `table` |
| `environment.first_order_decay_fit` — Fit first-order pollutant decay and half-life | `time_day` (vector; `day`); `concentration_mg_l` (vector; `mg/L`); `prediction_time_day` (number; `day`) | `decay_constant_per_day`, `half_life_day`, `initial_concentration_mg_l`, `rate_standard_error_per_day`, `r_squared_log`, `predicted_concentration_mg_l`; `plot` + `table` |
| `environment.rational_runoff` — Rational-method peak discharge | `runoff_coefficient` (number; `1`); `rainfall_intensity_mm_h` (number; `mm/h`); `area_ha` (number; `ha`); `storm_duration_min` (number; `min`); `time_of_concentration_min` (number; `min`) | `peak_runoff_m3_s`, `runoff_coefficient`, `design_rainfall_depth_mm` |
| `environment.scs_curve_number` — NRCS/SCS event curve-number runoff | `rainfall_mm` (number; `mm`); `area_km2` (number; `km²`); `curve_number` (number; `1`); `initial_abstraction_ratio` (number; `1`) | `potential_retention_mm`, `initial_abstraction_mm`, `direct_runoff_depth_mm`, `direct_runoff_volume_m3`, `event_runoff_coefficient` |
| `environment.fao56_evapotranspiration` — FAO-56 Penman–Monteith reference ET₀ | `tmax_c` (number; `°C`); `tmin_c` (number; `°C`); `actual_vapour_pressure_kpa` (number; `kPa`); `wind_speed_2m_m_s` (number; `m/s`); `elevation_m` (number; `m`); `net_radiation_mj_m2_day` (number; `MJ/m²/day`); `soil_heat_flux_mj_m2_day` (number; `MJ/m²/day`) | `reference_et0_mm_day`, `radiation_term_mm_day`, `aerodynamic_term_mm_day`, `mean_temperature_c`, `saturation_vapour_pressure_kpa`, `vapour_pressure_deficit_kpa`, `slope_saturation_curve_kpa_per_c`, `psychrometric_constant_kpa_per_c`, `atmospheric_pressure_kpa` |
| `environment.mann_kendall_sen` — Mann–Kendall trend and Sen slope | `time_year` (vector; `year`); `observations` (vector; `observation_unit`); `observation_unit` (text; `unit label`); `significance_level` (number; `1`) | `mann_kendall_s`, `variance_s`, `z_score`, `two_sided_p_value`, `p_value_method`, `kendall_tau_b`, `sen_slope_per_year`, `sen_slope_unit`, `sen_intercept`, `intercept_unit`, `trend`, `significance_level`; `plot` + `table` |

### Materials Science (`materials`) — 15 operations

**Scientific assumptions and checks:** [Materials Science reference](SCIENTIFIC_MATERIALS_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `materials.bulk_density` — Dry bulk density | `dry_mass_g` (number; `g`); `bulk_volume_cm3` (number; `cm³`) | `bulk_density_g_cm3`, `bulk_density_kg_m3` |
| `materials.tensile_response` — Engineering stress–strain | `force_n` (vector; `N`); `extension_mm` (vector; `mm`); `original_area_mm2` (number; `mm²`); `gauge_length_mm` (number; `mm`) | `peak_measured_stress_mpa`, `engineering_strain_at_first_peak`; `plot` + `table` |
| `materials.apparent_porosity` — Apparent open porosity | `dry_mass_g` (number; `g`); `saturated_mass_g` (number; `g`); `suspended_mass_g` (number; `g`) | `apparent_open_porosity_percent`, `absorbed_liquid_g` |
| `materials.stress_strain_analysis` — Analyze a tensile curve | `engineering_curve` (matrix; `col 1: mm/mm; col 2: MPa`); `elastic_limit_strain_mm_mm` (number; `mm/mm`) | `young_modulus_mpa`, `elastic_intercept_mpa`, `elastic_fit_r_squared_dimensionless`, `offset_yield_strength_mpa`, `offset_yield_strain_mm_mm`, `ultimate_tensile_strength_mpa`, `elongation_at_break_percent`, `toughness_mj_m3`, `resilience_to_elastic_limit_mj_m3`; `plot` + `table` |
| `materials.true_stress_strain` — Convert engineering and true stress–strain | `uniform_curve` (matrix; `col 1: mm/mm; col 2: MPa`); `direction` (text; `unitless code`) | `last_true_strain_mm_mm`, `last_true_stress_mpa`, `last_engineering_strain_mm_mm`, `last_engineering_stress_mpa`; `plot` + `table` |
| `materials.hollomon_fit` — Fit Hollomon work hardening | `true_plastic_curve` (matrix; `col 1: mm/mm; col 2: MPa`) | `strength_coefficient_k_mpa`, `strain_hardening_exponent_n_dimensionless`, `log_log_r_squared_dimensionless`; `plot` + `table` |
| `materials.ramberg_osgood_fit` — Fit Ramberg–Osgood plasticity | `total_strain_stress_curve` (matrix; `col 1: mm/mm; col 2: MPa`); `young_modulus_mpa` (number; `MPa`) | `strength_coefficient_k_mpa`, `ramberg_osgood_n_dimensionless`, `log_log_r_squared_dimensionless`; `plot` + `table` |
| `materials.fatigue_life` — Estimate Basquin and Coffin–Manson life | `stress_amplitude_mpa` (number; `MPa`); `total_strain_amplitude_mm_mm` (number; `mm/mm`); `mean_stress_mpa` (number; `MPa`); `ultimate_strength_mpa` (number; `MPa`); `yield_strength_mpa` (number; `MPa`); `young_modulus_mpa` (number; `MPa`); `fatigue_strength_coefficient_mpa` (number; `MPa`); `basquin_exponent_b` (number; `1`); `fatigue_ductility_coefficient_mm_mm` (number; `mm/mm`); `coffin_manson_exponent_c` (number; `1`); `mean_stress_correction` (text; `unitless code`) | `mean_stress_factor_dimensionless`, `corrected_alternating_stress_mpa`, `basquin_fatigue_life_cycles`, `basquin_coffin_manson_fatigue_life_cycles` |
| `materials.fracture_stress_intensity` — Compute mode-I stress intensity | `applied_tensile_stress_mpa` (number; `MPa`); `crack_length_a_m` (number; `m`); `plate_width_m` (number; `m`); `geometry` (text; `unitless code`) | `geometry_factor_y_dimensionless`, `mode_i_stress_intensity_mpa_sqrt_m` |
| `materials.fracture_critical_length` — Compute critical crack length from toughness | `fracture_toughness_mpa_sqrt_m` (number; `MPa√m`); `applied_tensile_stress_mpa` (number; `MPa`); `constant_geometry_factor_y` (number; `1`) | `critical_crack_length_a_m`, `critical_crack_length_a_mm`, `full_center_crack_length_2a_m` |
| `materials.paris_crack_growth` — Integrate Paris-law fatigue crack growth | `initial_crack_a_m` (number; `m`); `final_crack_a_m` (number; `m`); `stress_range_mpa` (number; `MPa`); `constant_geometry_factor_y` (number; `1`); `paris_c_m_cycle_inv_mpa_sqrt_m_neg_m` (number; `m/cycle/(MPa√m)^m`); `paris_exponent_m` (number; `1`); `load_ratio_r` (number; `1`); `fracture_toughness_mpa_sqrt_m` (number; `MPa√m`) | `paris_growth_cycles`, `initial_delta_k_mpa_sqrt_m`, `final_delta_k_mpa_sqrt_m`, `final_k_max_mpa_sqrt_m` |
| `materials.thermal_expansion_stress` — Thermal expansion and restrained stress | `linear_expansion_per_k` (number; `K⁻¹`); `initial_length_mm` (number; `mm`); `temperature_change_k` (number; `K`); `young_modulus_mpa` (number; `MPa`); `axial_restraint_fraction` (number; `1`) | `free_length_change_mm`, `free_thermal_strain_mm_mm`, `restrained_axial_stress_mpa`, `actual_length_change_mm` |
| `materials.composite_moduli` — Estimate aligned composite moduli | `fiber_volume_fraction` (number; `1`); `fiber_axial_modulus_gpa` (number; `GPa`); `fiber_transverse_modulus_gpa` (number; `GPa`); `matrix_modulus_gpa` (number; `GPa`); `halpin_tsai_shape_xi` (number; `1`) | `longitudinal_rule_mixtures_modulus_gpa`, `transverse_inverse_rule_modulus_gpa`, `halpin_tsai_modulus_gpa`, `halpin_tsai_eta_dimensionless` |
| `materials.hardness_e140` — Convert ASTM E140 steel hardness | `e140_table` (text; `unitless code`); `from_scale` (text; `unitless code`); `to_scale` (text; `unitless code`); `hardness_reading` (number; `HV, HBW, HBS, HRC or HRB (as selected)`) | `converted_hardness_{to_scale}`, `original_hardness_{from_scale}` |
| `materials.arrhenius_diffusion_profile` — Compute Arrhenius diffusion and Fick II profile | `diffusion_prefactor_m2_s` (number; `m²/s`); `activation_energy_kj_mol` (number; `kJ/mol`); `temperature_k` (number; `K`); `elapsed_time_s` (number; `s`); `depth_mm` (vector; `mm`); `initial_solute_wt_percent` (number; `wt%`); `surface_solute_wt_percent` (number; `wt%`) | `diffusivity_m2_s`, `two_sqrt_dt_diffusion_length_mm`, `concentration_at_last_depth_wt_percent`; `plot` + `table` |

### Signal Processing (`signals`) — 8 operations

**Scientific assumptions and checks:** [Signal Processing reference](SCIENTIFIC_NATURAL_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `signals.moving_average` — Centered moving average | `samples` (vector; `—`); `window` (integer; `samples`) | `window_samples`, `windows`; `plot` + `table` |
| `signals.amplitude_spectrum` — One-sided amplitude spectrum | `samples` (vector; `—`); `sample_rate_hz` (number; `Hz`) | `bin_width_hz`, `peak_bin_hz`, `peak_bin_amplitude`; `plot` + `table` |
| `signals.linear_resample` — Piecewise linear resampling | `coordinates` (vector; `—`); `samples` (vector; `—`); `targets` (vector; `—`) | `observations`, `requested_samples`; `plot` + `table` |
| `signals.windowed_fft_spectrum` — Windowed FFT amplitude and power spectrum | `samples` (vector; `—`); `sample_times_s` (vector; `s`); `window` (text; `—`) | `bin_width_hz`, `coherent_gain`, `window_mean_square`, `peak_frequency_hz`, `peak_amplitude`, `total_power`; `plot` + `table` |
| `signals.welch_psd` — Welch power spectral density | `samples` (vector; `—`); `sample_times_s` (vector; `s`); `window` (text; `—`); `segment_samples` (integer; `samples`); `overlap_samples` (integer; `samples`) | `segments`, `segment_samples`, `bin_width_hz`, `equivalent_noise_bandwidth_hz`, `peak_frequency_hz`, `peak_psd`, `integrated_power`; `plot` + `table` |
| `signals.butterworth_filter` — Zero-phase Butterworth filter | `samples` (vector; `—`); `sample_times_s` (vector; `s`); `filter_type` (text; `—`); `order` (integer; `—`); `cutoff_hz` (vector; `Hz`) | `prototype_order`, `effective_order`, `sample_rate_hz`; `plot` + `table` |
| `signals.savitzky_golay` — Savitzky–Golay smoothing | `samples` (vector; `—`); `sample_times_s` (vector; `s`); `window_samples` (integer; `samples`); `polynomial_order` (integer; `—`) | `window_samples`, `polynomial_order`, `output_samples`; `plot` + `table` |
| `signals.detect_peaks` — Peak detection with prominence and spacing | `samples` (vector; `—`); `sample_times_s` (vector; `s`); `minimum_height` (number; `—`); `minimum_prominence` (number; `—`); `minimum_spacing_samples` (integer; `samples`) | `peaks_detected`, `minimum_spacing_samples`; `plot` + `table` |

### Fitting and Inference (`fitting`) — 23 operations

**Scientific assumptions and checks:** [Fitting and Inference reference](SCIENTIFIC_FITTING_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `fitting.weighted_linear_least_squares` — Weighted linear least squares | `predictors` (matrix; `—`); `response` (vector; `—`); `weights` (vector; `—`) | `observations`, `positive_weight_observations`, `parameters`, `degrees_of_freedom`, `weighted_residual_sum_squares`, `reduced_chi_square`, `residual_standard_deviation`, `r_squared (if defined)`, `coefficient_{i}`, `standard_error_{i}`, `residual_{i}`, `aic`, `aicc`, `bic`, `runs_count`, `runs_test_p_value`, `residual_shapiro_w`, `residual_shapiro_p_value`, `max_absolute_parameter_correlation`, `parameter_correlation_{i}_{j}`, `warnings`; `plot` + `table` |
| `fitting.nonlinear_least_squares` — Levenberg–Marquardt nonlinear fit | `model` (text; `—`); `x` (vector; `—`); `y` (vector; `—`); `weights` (vector; `—`); `initial_parameters` (vector; `—`) | `observations`, `parameters`, `degrees_of_freedom`, `iterations`, `accepted_steps`, `rejected_steps`, `converged`, `final_damping`, `weighted_residual_sum_squares`, `reduced_chi_square`, `residual_standard_deviation`, `gradient_infinity_norm`, `parameter_{i}`, `standard_error_{i}`, `ci95_lower_{i}`, `ci95_upper_{i}`, `estimate_{name} (named)`, `residual_{i}`, `aic`, `aicc`, `bic`, `runs_count`, `runs_test_p_value`, `residual_shapiro_w`, `residual_shapiro_p_value`, `max_absolute_parameter_correlation`, `parameter_correlation_{i}_{j}`, `warnings`; `plot` + `table` |
| `fitting.custom_nonlinear_least_squares` — Custom expression nonlinear least squares | `expression` (text; `—`); `parameters` (text; `—`); `x` (vector; `—`); `y` (vector; `—`); `weights` (vector; `—`) | `observations`, `parameters`, `degrees_of_freedom`, `iterations`, `accepted_steps`, `rejected_steps`, `converged`, `final_damping`, `weighted_residual_sum_squares`, `reduced_chi_square`, `residual_standard_deviation`, `gradient_infinity_norm`, `parameter_{i}`, `standard_error_{i}`, `ci95_lower_{i}`, `ci95_upper_{i}`, `estimate_{name} (named)`, `residual_{i}`, `aic`, `aicc`, `bic`, `runs_count`, `runs_test_p_value`, `residual_shapiro_w`, `residual_shapiro_p_value`, `max_absolute_parameter_correlation`, `parameter_correlation_{i}_{j}`, `warnings`; `plot` + `table` |
| `fitting.compare_models` — Compare models by corrected Akaike information | `x` (vector; `—`); `y` (vector; `—`); `weights` (vector; `—`); `models` (text; `—`) | `models_compared`, `best_model`; `table` |
| `fitting.welch_t_test` — Welch two-sample t test | `sample_a` (vector; `—`); `sample_b` (vector; `—`) | `mean_a`, `mean_b`, `t_statistic`, `degrees_of_freedom`, `p_value`, `sample_size_a`, `sample_size_b` |
| `fitting.chi_square_gof` — Chi-square goodness of fit | `observed` (vector; `—`); `expected` (vector; `—`) | `chi_square`, `degrees_of_freedom`, `p_value`, `categories`, `expected_count_below_5`; `table` |
| `fitting.two_way_anova` — Two-way ANOVA (Type II, interaction) | `values` (vector; `—`); `factor_a` (vector; `—`); `factor_b` (vector; `—`) | `observations`, `levels_a`, `levels_b`, `degrees_of_freedom_residual`, `residual_sum_squares`, `residual_mean_square`, `r_squared_full`, `ss_factor_a_type_ii`, `ss_factor_b_type_ii`, `ss_interaction`, `f_factor_a`, `f_factor_b`, `f_interaction`, `p_factor_a`, `p_factor_b`, `p_interaction`; `table` |
| `fitting.kruskal_wallis` — Kruskal-Wallis with Dunn post-hoc | `values` (vector; `—`); `groups` (vector; `—`) | `observations`, `group_count`, `degrees_of_freedom`, `h_statistic`, `tie_correction`, `p_value`; `table` |
| `fitting.chi_square_independence` — Chi-square test of independence | `table` (matrix; `—`) | `rows`, `columns`, `observations`, `degrees_of_freedom`, `chi_square`, `p_value`, `expected_cells_below_5`, `cramers_v`; `table` |
| `fitting.fisher_exact` — Fisher's exact test (2x2) | `table` (matrix; `—`) | `observations`, `odds_ratio`, `p_two_sided`, `p_less`, `p_greater`, `feasible_tables` |
| `fitting.multiple_linear_regression` — Multiple linear regression and diagnostics | `predictors` (matrix; `—`); `response` (vector; `—`) | `observations`, `parameters`, `degrees_of_freedom`, `residual_sum_squares`, `residual_standard_deviation`, `r_squared`, `adjusted_r_squared`, `durbin_watson`, `jarque_bera`, `jarque_bera_p_value`, `max_leverage`, `max_abs_standardized_residual`, `max_cooks_distance`; `plot` + `table` |
| `fitting.binary_logistic_regression` — Binary logistic regression (IRLS) | `predictors` (matrix; `—`); `outcome` (vector; `—`) | `observations`, `parameters`, `iterations`, `log_likelihood`, `null_log_likelihood`, `likelihood_ratio_chi_square`, `likelihood_ratio_p_value`, `aic`, `mcfadden_pseudo_r_squared`; `plot` + `table` |
| `fitting.bootstrap_confidence_interval` — Bootstrap percentile and BCa intervals | `sample` (vector; `—`); `statistic` (text; `—`); `replicates` (integer; `—`); `seed` (integer; `—`); `confidence_level` (number; `—`) | `observations`, `statistic`, `estimate`, `replicates`, `seed`, `confidence_level`, `percentile_lower`, `percentile_upper`, `bca_lower`, `bca_upper`, `bias_correction_z0`, `jackknife_acceleration` |
| `fitting.t_test_power` — t-test power and sample size | `effect_size` (number; `—`); `alpha` (number; `—`); `target_power` (number; `—`); `sample_size` (integer; `—`); `design` (text; `—`); `tails` (text; `—`) | `effect_size`, `alpha`, `tails`, `design`, `sample_size_per_group`, `achieved_power`, `target_power`, `required_sample_size_per_group`, `power_at_required_sample_size` |
| `fitting.proportion_power` — Proportion z-test power and sample size | `proportion_a` (number; `—`); `proportion_b` (number; `—`); `alpha` (number; `—`); `target_power` (number; `—`); `sample_size` (integer; `—`); `design` (text; `—`); `tails` (text; `—`) | `proportion_a`, `proportion_b`, `alpha`, `tails`, `design`, `sample_size_per_group`, `achieved_power`, `target_power`, `required_sample_size_per_group`, `power_at_required_sample_size` |
| `fitting.one_way_anova` — One-way ANOVA with Tukey HSD | `values` (vector; `—`); `groups` (vector; `—`) | `observations`, `group_count`, `df_between`, `df_within`, `ss_between`, `ss_within`, `ss_total`, `ms_between`, `ms_within`, `f_statistic`, `p_value`, `r_squared`, `residual_standard_deviation`, `tukey_q_critical_95`; `table` |
| `fitting.mann_whitney_u` — Mann-Whitney U | `sample_a` (vector; `—`); `sample_b` (vector; `—`) | `sample_a_size`, `sample_b_size`, `u_a`, `u_b`, `u_statistic`, `p_value`, `method`, `ties_present`, `absolute_z_statistic (asymptotic only)` |
| `fitting.wilcoxon_signed_rank` — Wilcoxon signed-rank | `before` (vector; `—`); `after` (vector; `—`) | `pairs`, `nonzero_pairs`, `zero_pairs_dropped`, `w_plus`, `w_minus`, `w_statistic`, `p_value`, `method`, `ties_present`, `absolute_z_statistic` |
| `fitting.shapiro_wilk` — Shapiro-Wilk normality | `sample` (vector; `—`) | `observations`, `w_statistic`, `p_value`, `p_value_method` |
| `fitting.pearson_correlation` — Pearson correlation and 95% CI | `x` (vector; `—`); `y` (vector; `—`) | `observations`, `r`, `degrees_of_freedom`, `p_value`, `ci95_lower`, `ci95_upper` |
| `fitting.spearman_correlation` — Spearman correlation and 95% CI | `x` (vector; `—`); `y` (vector; `—`) | `observations`, `rho`, `p_value`, `method`, `ties_present`, `ci95_lower`, `ci95_upper` |
| `fitting.bonferroni_correction` — Bonferroni p-value correction | `p_values` (vector; `—`); `alpha` (number; `—`) | `hypotheses`, `alpha`, `rejections`, `method`; `table` |
| `fitting.benjamini_hochberg_correction` — Benjamini-Hochberg FDR correction | `p_values` (vector; `—`); `alpha` (number; `—`) | `hypotheses`, `alpha`, `rejections`, `method`; `table` |

### Measurement Uncertainty (`uncertainty`) — 1 operations

**Scientific assumptions and checks:** [Measurement Uncertainty reference](SCIENTIFIC_UNCERTAINTY_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `uncertainty.propagate_uncertainty` — GUM measurement uncertainty propagation | `expression` (text; `—`); `measurements` (text; `—`); `correlations` (text; `—`); `coverage_probability` (number; `—`) | `estimate`, `combined_standard_uncertainty`, `effective_degrees_of_freedom`, `coverage_probability`, `coverage_factor`, `expanded_uncertainty`, `coverage_lower`, `coverage_upper`, `input_count`, `correlation_count`, `sensitivity_{name}`, `contribution_{name}`; `table` |

### Numerical Methods (`numerics`) — 5 operations

**Scientific assumptions and checks:** [Numerical Methods reference](SCIENTIFIC_NUMERICS_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `numerics.solve_ivp_rk45` — Adaptive Dormand-Prince RK45 IVP | `rhs_expressions` (text; `—`); `initial_values` (vector; `—`); `parameters` (text; `—`); `start_time` (number; `—`); `end_time` (number; `—`); `relative_tolerance` (number; `—`); `absolute_tolerance` (number; `—`); `max_steps` (integer; `—`) | `method`, `start_time`, `end_time`, `accepted_steps`, `rejected_steps`, `rhs_evaluations`, `jacobian_evaluations`, `linear_solves`, `estimated_max_local_error`, `estimated_last_local_error`, `max_accepted_error_ratio`, `tolerance_met`, `final_y{i}`; `plot` + `table` |
| `numerics.solve_ivp_implicit` — Adaptive implicit stiff IVP | `rhs_expressions` (text; `—`); `initial_values` (vector; `—`); `parameters` (text; `—`); `start_time` (number; `—`); `end_time` (number; `—`); `relative_tolerance` (number; `—`); `absolute_tolerance` (number; `—`); `max_steps` (integer; `—`) | `method`, `start_time`, `end_time`, `accepted_steps`, `rejected_steps`, `rhs_evaluations`, `jacobian_evaluations`, `linear_solves`, `estimated_max_local_error`, `estimated_last_local_error`, `max_accepted_error_ratio`, `tolerance_met`, `final_y{i}`; `plot` + `table` |
| `numerics.brent_root` — Brent bracketed root | `expression` (text; `—`); `parameters` (text; `—`); `lower` (number; `—`); `upper` (number; `—`); `relative_tolerance` (number; `—`); `absolute_tolerance` (number; `—`); `max_evaluations` (integer; `—`) | `root`, `estimated_absolute_error`, `bracket_width`, `absolute_residual`, `iterations`, `function_evaluations`, `tolerance_met` |
| `numerics.gauss_kronrod_integral` — Adaptive Gauss-Kronrod 15(7) quadrature | `expression` (text; `—`); `parameters` (text; `—`); `lower` (number; `—`); `upper` (number; `—`); `relative_tolerance` (number; `—`); `absolute_tolerance` (number; `—`); `max_evaluations` (integer; `—`) | `integral`, `estimated_absolute_error`, `function_evaluations`, `subdivisions`, `intervals`, `tolerance_met` |
| `numerics.cubic_spline` — Natural / clamped cubic spline | `x` (vector; `—`); `y` (vector; `—`); `queries` (vector; `—`); `boundary` (text; `—`); `left_slope` (number; `—`); `right_slope` (number; `—`) | `boundary`, `knots`, `intervals`, `evaluations`, `tridiagonal_solves`, `max_knot_residual`, `estimated_roundoff_error`, `underlying_function_error`, `tolerance_met`; `plot` + `table` |

### Linear Algebra (`linalg`) — 8 operations

**Scientific assumptions and checks:** [Linear Algebra reference](SCIENTIFIC_LINALG_REFERENCE.md).

| Catalog ID and UI title | Inputs: exact `Field.key` (`kind`; `Field.unit`) | Named result `values` and other components |
| --- | --- | --- |
| `linalg.qr_decomposition` — Pivoted QR decomposition | `matrix` (matrix; `—`) | `rows`, `columns`, `numerical_rank`, `condition_number`, `warning`, `column_permutation_zero_based`, `Q`, `R`, `relative_reconstruction_residual`, `max_orthogonality_error`; `table` |
| `linalg.cholesky_decomposition` — Cholesky decomposition | `matrix` (matrix; `—`) | `order`, `L`, `condition_number`, `warning`, `relative_reconstruction_residual`; `table` |
| `linalg.least_squares_qr` — Least squares (QR) | `matrix` (matrix; `—`); `rhs` (vector; `—`) | `rows`, `columns`, `numerical_rank`, `condition_number`, `warning`, `coefficients`, `residuals`, `residual_l2_norm`, `residual_sum_squares`, `relative_backward_residual`, `qr_relative_reconstruction_residual`, `qr_max_orthogonality_error`, `degrees_of_freedom`; `table` |
| `linalg.symmetric_eigen` — Symmetric eigenvalues and eigenvectors | `matrix` (matrix; `—`) | `order`, `eigenvalues`, `eigenvectors_columns`, `relative_eigenpair_residual`, `max_orthogonality_error`; `table` |
| `linalg.svd` — Singular value decomposition | `matrix` (matrix; `—`) | `rows`, `columns`, `U`, `singular_values`, `V_transpose`, `numerical_rank`, `numerical_rank_cutoff`, `condition_number`, `warning`, `relative_reconstruction_residual`, `max_orthogonality_error`; `table` |
| `linalg.matrix_inverse` — Matrix inverse | `matrix` (matrix; `—`) | `order`, `inverse`, `condition_number`, `warning`, `relative_identity_residual`, `relative_lu_factor_residual`; `table` |
| `linalg.matrix_determinant` — Matrix determinant | `matrix` (matrix; `—`) | `order`, `determinant`, `sign`, `log_absolute_determinant`, `condition_number`, `warning`, `relative_lu_factor_residual` |
| `linalg.pca` — Principal component analysis | `observations` (matrix; `—`); `scaling` (text; `—`) | `observations`, `features`, `components`, `numerical_rank`, `condition_number`, `warning`, `scaling`, `means`, `feature_standard_deviations`, `loadings`, `scores`, `explained_variance`, `explained_variance_ratio`, `relative_reconstruction_residual`, `max_orthogonality_error`; `table` |

## Execution budget, Cancel and error behavior

**Run calculation** first checks grants, exact keys, units, input limits and live linked source receipts in the parent session event loop. It then sends only validated, detached calculation inputs to an isolated **spawned Python child process** (including Windows). The graph, undo history and plugin-grant state remain in the parent. Default wall-clock budget: **15 seconds**, set via `GRAPHLINK_SCIENTIFIC_TIMEOUT_SECONDS`; an individual operation may set `Operation.timeout_s`. The resolved timeout must be finite, positive and **≤600 seconds**. This is elapsed wall time, not a CPU quota; worker startup is included. The worker terminates and is reaped on completion, timeout or cancellation.

A running node offers **Cancel calculation**. A second concurrent calculation on that node is rejected as `busy`. Cancellation and timeout yield an unsuccessful response (usually `ok:false`, `errorType` `cancelled` or `timeout`, `elapsedMs` and a message); invalid inputs and worker errors are typed where applicable. Failure creates **no new successful receipt or undo command**, and never replaces the previous result. If a source changes, the node is edited/deleted, or a grant is revoked during execution, the result is rejected instead of committing stale measurements. See [`execution.py`](../graphlink_plugins/scientific/execution.py) and [`workbench.py`](../graphlink_plugins/scientific/workbench.py).

## Versioned bundled reference datasets

New Scientific Workbench results (pack version `1.2.0` and later) record
`provenance.referenceData`: an array of `{id, version, checksum}` entries
for **exactly the bundled reference datasets used by the calculation**.
Results that consume no bundled dataset record `referenceData: []`.
The SHA-256 checksum covers the actual in-tree dataset values, including
table row and mapping order, not merely the standard's citation.

The versioned registry is in
[`reference_data.py`](../graphlink_plugins/scientific/reference_data.py).
`list_reference_datasets()` lists each dataset's ID, version, checksum and
source. The initial registry covers CIAAW 2024 atomic weights, EPA 2024
AQI breakpoints, Briggs rural/urban dispersion coefficients, ASTM E140
Tables 1/2, WGS84 ellipsoid parameters, the 1976 ISA atmosphere
profile, and the ICRS/J2000 Galactic rotation matrix. Results using
a selectable table (e.g. hardness, dispersion terrain) record only that
table; the Galactic matrix is recorded only when a Galactic frame is used.
User-supplied measurements and imported tables have their own linked
`provenance.sources` receipts and are not mislabeled as bundled data.

The existing `receiptHash` authenticates `referenceData`, and saved
results also check each registered dataset's current ID, version and
checksum to reject substituted/missing references even with a recomputed
receipt hash. To revise a reference dataset, assign a new version and
retain old dataset values for verifying historical receipts. Existing
1.0.0 and 1.1.0 receipts remain readable using their original policy.

Units now include the enzyme unit `U` (exactly `1 µmol/min`, or
`1/60,000,000 kat`) and dalton `Da` (equivalent to unified atomic
mass unit `u`, using `1.66053906660 × 10⁻²⁷ kg`). Both support SI
prefixes (`mU`, `kU`, `kDa`) and compound units like `U/mL`.

## Results, persistence and charts

An ordinary result contains `summary`, scalar/text entries in `values`, optional rectangular `table` and paired `plot`, `operationId`, normalized `inputs` and `provenance`. The latter records `packId`, `packVersion` (currently `1.2.0`), `operationId`, a UTC `createdAt` timestamp, SHA-256 `inputHash`, `resultHash` and `receiptHash`, `inputUnits` and the linked `sources` (stable `sourceKey`, selector, result/receipt hash). The result fingerprint omits the timestamp; the receipt hash authenticates time and lineage. Previously saved `1.0.0` receipts open under their original verification policy rather than being silently recomputed. Identical repeated operations with identical units/source lineage preserve their existing receipt; changed successful runs are one undoable graph edit.

Compact results persist in graph state; when an asset store is available, state ≥8 KiB is stored as content-addressed JSON. Workspace save/archive includes referenced assets. A disabled pack retains passive saved nodes/results but cannot execute or supply verified connected inputs until re-enabled. Missing assets or damaged receipt hashes produce visible errors rather than invented outputs.

A result card's **Chart type → Chart this** creates a connected, standard Graphlink **Chart** node from a current, grant-checked, checksum-verified science result **without an AI model**. **Scatter** uses an actual XY trace, ordering, axis labels/units and eligible nonnegative y-error columns; a GUM estimate can display its coverage bounds. Supported linear/weighted/nonlinear fits offer **Fitted line with 95% confidence envelope**; its limits are **pointwise mean-response** intervals, not prediction or simultaneous bands. **Box plot** needs actual grouped/raw observations, while **heatmap** needs a real rectangular numerical matrix (no fabricated cells). Chart adapters enforce **300 plotted points/observations/cells**, **12 box-plot groups**, or **75 paired points for a four-series fitted envelope**.

The Chart node pins `scienceReceipt` (`sourceKey`, `resultHash`, `receiptHash`) and its connection. A changed upstream receipt leaves the last chart rendered but marks it **Source changed · refresh to update**; **Refresh chart** re-renders the same Chart node from the current verified source, undoably. This also catches changed provenance when numeric results are identical. Scientific **Export JSON / Export CSV** applies to results, while ordinary Chart export/zoom/resize operates on the chart.

## Uncertainty across connected results

The Workbench propagates verified **numeric scalar** uncertainties over connected Scientific Workbench operations, leaving existing numeric values and exact-input receipt formats unchanged. Its optional receipt-protected `uncertainty` sidecar carries the source's **standard uncertainty (1σ)**, coverage factor/probability, unit and immutable source-root identity; the canvas displays **value ± standard uncertainty**, contribution budgets and coverage intervals.

Connected scalars use **first-order GUM** sensitivities and named signed variance/covariance contributions. **Automatic** chooses this for sufficiently linear models but switches to deterministic **JCGM 101 Gaussian Monte Carlo** for strongly nonlinear behavior. The UI also allows explicit **First-order GUM** or **Monte Carlo**; MC uses 16,384 deterministic, jointly sampled trials and reports a simulated uncertainty, mean and quantile interval. It keeps a *separately labeled* linear sensitivity budget for attribution. Correlations are preserved by tracking shared immutable source roots: reusing a single x twice yields `u(x+x)=2u(x)` and `u(x−x)=0`, even through branches. The standalone `uncertainty.propagate_uncertainty` operation remains its original analytic GUM expression/covariance tool.

**Known bounds and refusal cases:** the connected method is not elementwise propagation for vectors/matrices or a general covariance inference engine. Interval-only bootstrap/BCa, correlation or odds-ratio confidence limits do not supply 1σ; arbitrary covariance between distinct fitted outputs or between built-in model uncertainty and a linked uncertainty may be unknown. Unit/kind mismatch, nondifferentiability, invalid numerical perturbations, unsupported output shapes and nonphysical simulated inputs are explicit errors or flagged unpropagatable metrics—not guessed zeros, independent quantities or Gaussian PDFs. Native standard-error pairs are recognized where supported. Coverage remains conditional on the stated model and distribution. See the [Measurement Uncertainty reference](SCIENTIFIC_UNCERTAINTY_REFERENCE.md), plus [JCGM 100:2008](https://doi.org/10.59161/JCGM100-2008E) and [JCGM 101:2008](https://www.bipm.org/documents/20126/2071204/JCGM_101_2008_E.pdf) validations.

## Explicit scientific Graph Runner steps

Each scientific node has an **undoable Save for Graph Runner** configuration on each scientific node. Choose an operation and typed inputs/source bindings, then select **Save for Graph Runner**; this stores a versioned recipe **without running it**. Manual **Run calculation** remains separate. An unconfigured scientific node is never scheduled; operations are still not exposed as Builder/model/agent tools or implicitly callable by AI.

In the **Graph Runner** dialog (**Plugins → Graph Runner**), **Preview** plans the dependencies *without executing science or Model steps*. On user approval, the runner resolves current, verified upstream scalar receipts and selected, bounded numeric table columns, waits for **all upstream inputs to be committed**, and rechecks grants, input limits, typed sources and the same wall-clock worker. Mixed local **Model** and scientific steps dispatch separately, without a model silently invoking science. The persisted Graph Runner receipt and inspector trace include scientific result, elapsed milliseconds, hashes, input/source citations and typed failure reasons. On restart/resume it revalidates configuration and dependencies and **does not replay an unchanged completed scientific step**.

The runner-level **Cancel run** is distinct from **Cancel calculation**: it stops downstream scheduling, cancels/drains active scientific workers and checkpoints the cancelled run. An external Model request that might already have been sent still requires reconciliation, never unverified automatic replay. The adapter is [`graph_run_adapter.py`](../graphlink_plugins/scientific/graph_run_adapter.py).

## Worked examples: real connected scientific workflows

### Example 1 — Field runoff sampling: typed TSV to flow-weighted contaminant mass

**Question:** What total load and flow-weighted concentration crossed a gauging station during three 10-minute samples?

1. Enable **Environmental Science**, create a scientific node and in **Typed data table** select **Paste CSV / TSV → TSV**. Paste the following (literal tab separators):

   ```tsv
   Station	Flow (m³/s)	Duration (s)	Concentration (mg/L)
   reach-4	2	600	4
   reach-4	4	600	6
   reach-4	3	600	8
   ```

2. Create a second Environmental Science workbench node; choose `flow_weighted_concentration` and connect the first node directly. Select the table's three numeric columns into `flow_m3_s`, `duration_s` and `concentration_mg_l`; choose **Reject missing**. Run.
3. Check `total_water_m3 = 5,400`, `total_constituent_kg = 33.6` and `mean_concentration_mg_l ≈ 6.22222`. These reflect Σ(QΔt)=5,400 m³ and Σ(CQΔt)/Σ(QΔt), not an unweighted three-sample mean. Inspect the output table and typed-table selection receipt; export JSON as the sample/assumption audit trail. If a time-series chart is needed, connect an actual time vector and measured concentration vector to `core.plot_xy`, then choose **Chart this**; an aggregate scalar alone is not a time series.

### Example 2 — UV–Vis transmittance scan → Beer–Lambert concentration → micromolar result

**Question:** What analyte concentration corresponds to the observed absorption maximum?

1. Enable **Chemistry** and **Scientific Core**. On a Chemistry node choose `transmission_spectrum`; supply `wavelength_nm = [440, 460, 480, 500, 520]` and `transmission_percent = [82, 51, 20, 47, 79]`. Run and inspect `peak_wavelength_nm = 480`, `peak_absorbance ≈ −log₁₀(0.20) = 0.698970`. Use **Chart this → Scatter** (when offered) for a measured wavelength/absorbance chart.
2. Connect this node to another Chemistry node, choose `beer_lambert`, and link the upstream `peak_absorbance` to the downstream `absorbance`. Set `molar_absorptivity = 12500` (L/(mol·cm)) and `path_length_cm = 1`. Run; `concentration_mol_l ≈ 5.59176×10⁻⁵`.
3. Connect the concentration metric to a Scientific Core `convert_unit` node's `value`; set `from_unit = "mol/L"` and `to_unit = "µM"`. Run and inspect `value (µM) ≈ 55.9176`. Save and export the entire graph's receipts. Real analysis requires an appropriate blank/calibration and a justified molar absorptivity; this calculation does not infer them.

### Example 3 — Gauge-block correction → uncertainty-aware connected conversion → graph-run plan

**Question:** How can a corrected length and its measurement uncertainty be converted without losing its source lineage?

1. Enable **Measurement Uncertainty** and **Scientific Core**. Choose `propagate_uncertainty`, set `expression = "L + d"`, `correlations = "[]"` and `coverage_probability = 0.95`. Paste this in the `measurements` **text** field (values and standard uncertainties in *mm*):

   ```json
   {"L":{"value":50.000623,"uncertainty":0.000025},
    "d":{"value":0.000215,"uncertainty":0.0000097}}
   ```

   Run. The output `estimate` is **50.000838 mm** and `combined_standard_uncertainty` is approximately **0.00002682 mm (26.82 nm)**, with a two-sided coverage interval based on the displayed coverage factor. This is a *two-contribution* demonstration, not the full JCGM 100 Annex H.1 end-gauge model that includes thermal expansion terms (see the [uncertainty pack reference](SCIENTIFIC_UNCERTAINTY_REFERENCE.md)).
2. Connect the GUM node to Scientific Core `convert_unit`, bind its `estimate` to the destination `value` and set `from_unit = "mm"` and `to_unit = "m"`. Run. `value (m)` is **0.050000838 ± 2.682×10⁻⁸ m**: the connected estimate carries its verified 1σ uncertainty and independent root lineage through the conversion, and its linked source receipt is verified. Use the propagation budget to verify the 1000:1 scaling.
3. Choose **Save for Graph Runner** on both configured nodes, open **Plugins → Graph Runner**, preview the planned source-then-conversion ordering, and approve **Run graph**. An unchanged completed step shows **Up to date** and resumes from its saved receipt; change an upstream input and save it again to re-run both steps in order. **Cancel run** stops active scientific work and downstream scheduling.

## Developer contract and validation

- **Register packs/operations:** [`catalog.py`](../graphlink_plugins/scientific/catalog.py) defines `PACKS` and imports each pack's `OPERATIONS` on grant; `fitting` includes the `inferential.py` operations. Each `Operation` has a stable pack-local `id`, title, description, ordered `Field` declarations, executable `example`, calculation callable and optional `timeout_s`. `Operation.describe()` supplies the UI schema; **do not maintain a parallel hard-coded operation registry**.
- **Validate and calculate:** [`core.py`](../graphlink_plugins/scientific/core.py) enforces key equality, finite/exact values, units, table/plot shapes, 512 KiB outputs and canonical hashes. Pack implementations enforce method-specific domains, convergence/tolerance policies, fit assumptions and reference equations. Return `{"summary": "...", "values": {...}}` and optionally `"table"` / `"plot"`. Retain existing operation/metric names, examples, version and receipt verification compatibility for saved work.
- **Host, assets, scheduling:** [`workbench.py`](../graphlink_plugins/scientific/workbench.py) mediates grants, source selections, edits and undo; [`execution.py`](../graphlink_plugins/scientific/execution.py) enforces killable worker budgets; [`data_table.py`](../graphlink_plugins/scientific/data_table.py) persists typed tables by content hash. Chart adapters use only current verified science receipts. Connected results carry optional receipt-protected uncertainty sidecars, and the explicitly configured [Graph Runner adapter](../graphlink_plugins/scientific/graph_run_adapter.py) provides runner-level cancellation.
- **When adding an operation:** add a declared `Operation`, valid example, input/output unit conventions, independently checked numerical cases and errors. Update the correct pack reference and the table above. The test below deliberately fails if an operation is missing or field keys/units become stale; do not work around it by omitting a new pack from the catalog. See the [independent correctness audit](SCIENTIFIC_CORRECTNESS_AUDIT.md).

From a checkout with Python dependencies installed:

```sh
python -m pytest -n0 backend/tests/test_scientific_workbench_guide.py
python -m pytest -n0 backend/tests/test_scientific*.py tests/test_scientific*.py backend/tests/test_plugin_state_assets.py backend/tests/test_packaging_bundled_resources.py
```

From `web_ui` run `npm run check`; with a running backend and Playwright Chromium, `npx playwright test e2e/scientific-workbench.spec.ts` covers browser workflows. Compare numerical results against each pack's referenced literature, not only regressions against the implementation.
