# Scientific Workbench: Environmental Science reference

The Environmental Science pack contains **11 operations**. Its original operation IDs, input schemas and returned values (`flow_weighted_concentration`, `rainfall_runoff`, `degree_days`) have not changed; saved results and their receipts remain readable through the shared workbench. This extension adds eight standard-library-only calculators. Inputs must be finite measurements; paired vectors must have equal lengths. A scalar without a physical dimension uses **1** (dimensionless), and an environmental measurement's chosen unit must be entered explicitly for trend analysis.

The **field names and units** below are the workbench schema. Output keys and their units are explicitly enumerated; tables and plots label their units. Results are deterministic for fixed normalized inputs and are saved through the existing workbench mechanism, not a new persistence format. Formula-based estimates are **not substitutes for regulatory AQI reporting, site-specific dispersion modeling, or a calibrated watershed model**.

## 1. US EPA Air Quality Index — `air_quality_index`

| Required field (all entered, but unavailable values may be `-1`) | Measurement / unit |
| --- | --- |
| `o3_8h_ppm` | Daily maximum 8-hour ozone concentration, ppm |
| `o3_1h_ppm` | Optional daily maximum 1-hour ozone, ppm |
| `pm25_24h_ug_m3` | 24-hour PM₂.₅ concentration, µg/m³ |
| `pm10_24h_ug_m3` | 24-hour PM₁₀ concentration, µg/m³ |
| `co_8h_ppm` | Daily maximum 8-hour CO, ppm |
| `so2_1h_ppb` | Daily maximum 1-hour SO₂, ppb |
| `so2_24h_ppb` | 24-hour SO₂ for higher AQI categories, ppb |
| `no2_1h_ppb` | Daily maximum 1-hour NO₂, ppb |

Each field is nonnegative if measured; **exactly `-1` means unmeasured**, not a concentration and not an instruction to use a shorter averaging period. At least one applicable pollutant subindex is required. For each measured value, truncate (never round up) at EPA's precision: O₃ **3 decimals**, PM₂.₅ and CO **1 decimal**, PM₁₀, SO₂ and NO₂ **integer**. Interpolate linearly between the **May 2024 EPA AQI Table 6** concentration breakpoints and index breakpoints (0–50, 51–100, 101–150, 151–200, 201–300, 301–500). Round the interpolated AQI to the nearest integer, with half values rounded up. For pollutant concentrations above their AQI-500 upper breakpoint, extend the 301–500 segment as directed by EPA rather than inventing a new slope.

The implementation includes all eight pollutant/averaging-period rows and the revised 2024 PM₂.₅ levels (0–9.0, 9.1–35.4, 35.5–55.4, 55.5–125.4, 125.5–225.4 and 225.5–325.4 µg/m³). O₃ 1-hour below 0.125 ppm and O₃ 8-hour above 0.200 ppm have no applicable breakpoint and are not used. Daily AQI normally requires the 8-hour ozone value; 1-hour is supplementary for high values. SO₂ 1-hour determines levels up to AQI 200; **SO₂ 24-hour at or above 305 ppb** determines AQI 201+. If SO₂ 1-hour is at least 305 ppb but 24-hour is below 305 ppb or unavailable, EPA assigns an SO₂ subindex of **200**. If no applicable subindex remains, the operation rejects the inputs rather than inventing a value.

**Outputs:** `aqi` (1), `aqi_category` (category, 1), `main_pollutant` (name and averaging period, 1), `subindices_calculated` (count, 1). The result table lists observed and truncated concentrations with their unit (ppm, ppb or µg/m³), subindex (1), and category (1). The highest subindex sets the AQI; first-in-field-order wins a tie. This is an **AQI from already-computed daily pollutant concentrations**, not the EPA NowCast or a routine for calculating rolling daily averages from hourly samples.

**EPA reference checks (to stated digits):** [EPA-454/B-24-002, pp. 13–16](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=P101AP0Q.txt) uses O₃ 0.07853333 ppm → truncated 0.078 ppm → **AQI 126**. Its three-pollutant example uses O₃ 0.078 ppm, PM₂.₅ 35.9 µg/m³, and CO 8.4 ppm → subindices **126, 102 and 90**, hence overall **126**. The current [EPA AQS AQI breakpoint table](https://aqs.epa.gov/aqsweb/documents/codetables/aqi_breakpoints.html) gives the regulatory ranges. **Published-example discrepancy:** the same TAD prints **148** for 1-hour O₃ 0.162 ppm; its stated 0.125–0.164 ppm / 101–150 interpolation yields **147.487...**, which rounds to **147** under the stated method. The pack intentionally follows the official equation and breakpoints rather than silently modifying them to reproduce that arithmetic discrepancy.

## 2. Gaussian plume with Pasquill–Gifford classes — `gaussian_plume`

| Input | Unit | Output | Unit |
| --- | --- | --- | --- |
| `emission_rate_g_s` (Q) | g/s | `receptor_concentration_ug_m3` | µg/m³ |
| `wind_speed_m_s` (u) | m/s | `ground_centerline_concentration_ug_m3` | µg/m³ |
| `downwind_m` (x) | m | `sigma_y_m` | m |
| `crosswind_m` (y, signed) | m | `sigma_z_m` | m |
| `receptor_height_m` (z) | m | profile plot vs x | m, µg/m³ |
| `effective_stack_height_m` (H) | m | | |
| `stability_class` | A, B, C, D, E or F (1) | | |
| `terrain` | rural or urban (category, 1) | | |

Uses the ground-reflected steady continuous point-source equation:

`C(x,y,z) = Q / (2π u σy σz) · exp[-y²/(2σy²)] · {exp[-(z−H)²/(2σz²)] + exp[-(z+H)²/(2σz²)]}`.

The Briggs parameterization of the Pasquill–Gifford dispersion curves is `σy=a_y x (1+b_y x)^(-1/2)` and `σz=a_z x (1+b_z x)^c`, with **rural/urban, class A–F** coefficients from [EPA/FEMA/DOT *Technical Guidance for Hazards Analysis*, Exhibit G-1](https://nrt.response.epa.gov/sites/2/files/tech%2520for%2520hazard%2520anal.pdf). Notably urban A–B σz uses exponent **+1/2** and urban E–F uses **b=0.00015 m⁻¹**. Enter effective stack height (physical stack height plus independently established plume rise). The published fits cover approximately **100–10,000 m** downwind; the operation enforces this range. No building downwash, dry/wet deposition, terrain elevation change, reactive chemistry, meteorological variation, or sophisticated regulatory averaging is modeled.

**Published-coefficient check:** For rural class D at x=1,000 m, the Briggs formulas produce σy=**76.277 m** and σz=**37.947 m**, matching the stated **76.3 m and 37.9 m** in [DOE Handbook 1224-2024, Chapter 6](https://directives.doe.doxcelerate.com/documents/doe-hdbk-1224-2024-hazard-and-accident-analysis-handbook).

## 3. Streeter–Phelps dissolved-oxygen sag — `dissolved_oxygen_sag`

**Inputs:** `initial_do_mg_l` (mg/L), `saturation_do_mg_l` (mg/L), `ultimate_bod_mg_l` (mg/L), `deoxygenation_per_day` (day⁻¹), `reaeration_per_day` (day⁻¹), `velocity_m_s` (m/s) and `reach_length_km` (km).

Let initial deficit `D₀ = DOsat − DO₀` (mg/L), BOD `L₀` (mg/L), and elapsed river travel time `t = 1000 · x_km / (86400 · velocity_m_s)` (day). With **natural-exponential rate constants** kd and kr (day⁻¹):

`D(t) = [kd · L₀/(kr−kd)] · [exp(−kd t)−exp(−kr t)] + D₀ exp(−kr t)` (mg/L), and `DO(t) = DOsat − D(t)` (mg/L).

At equal rates k, the continuous limiting expression is `D(t) = (D₀ + kL₀t)exp(−kt)`. The implementation evaluates the analytical stationary point and both reach boundaries, so the reported minimum is the **minimum within the supplied reach**, not the unconstrained critical point outside it.

**Outputs:** `initial_deficit_mg_l` (mg/L), `outlet_do_mg_l` (mg/L), `minimum_do_mg_l` (mg/L), `minimum_do_distance_km` (km), `travel_time_day` (day), and a distance (km) / travel time (day) / remaining BOD (mg/L) / deficit (mg/L) / DO (mg/L) profile and plot. The model assumes complete mixing at the start, constant discharge/velocity/temperature/rates, no new loads or sediments, and fixed saturation; a negative modeled DO is explicitly warned about, not hidden. Equation: [USGS/EPA *Watershed Assessment of River Stability and Sediment Supply*, Ch. 9, Eq. IX.11](https://19january2021snapshot.epa.gov/sites/static/files/2015-11/documents/wrens_ch9.pdf).

## 4. First-order pollutant decay fitted from measurements — `first_order_decay_fit`

**Inputs:** strictly increasing `time_day` (day vector, at least three times), paired positive `concentration_mg_l` (mg/L vector) and `prediction_time_day` (day). The unweighted least-squares model is `ln C(t) = ln C₀ − k t`. A nonnegative fitted log slope is rejected: this operation reports **decay**, not growth or an unidentified zero half-life.

**Outputs:** `decay_constant_per_day` (day⁻¹), `rate_standard_error_per_day` (day⁻¹, residual log-OLS standard error), `half_life_day` (day, `ln(2)/k`), `initial_concentration_mg_l` (mg/L), `predicted_concentration_mg_l` (mg/L at requested time), `r_squared_log` (1); table of measured/fitted/residual concentration (mg/L), and a fitted concentration plot versus time (day). Measurements below detection limits, variable uncertainty, secondary reactions and concentrations of zero need a dedicated statistical/chemical treatment and are not silently imputed. The log-linear fit assumes additive errors on **ln(concentration)**, not concentration.

## 5. Storm runoff

### Rational method — `rational_runoff`

**Inputs:** `runoff_coefficient` (1, 0–1), `rainfall_intensity_mm_h` (mm/h), `area_ha` (ha), `storm_duration_min` (min) and `time_of_concentration_min` (min). The duration must be **at least** the time of concentration. `peak_runoff_m3_s` (m³/s) is `C · i · A/360`; additional outputs are `runoff_coefficient` (1) and `design_rainfall_depth_mm` (mm). This is an **estimated peak**, not an event hydrograph or observed runoff coefficient. C and rainfall intensity must be chosen for the catchment and design storm. See [FHWA HEC-22, §3.2.2](https://www.fhwa.dot.gov/engineering/hydraulics/pubs/10009/10009.pdf) (typically applicable to small catchments, roughly ≤80 ha).

### SCS/NRCS curve-number event runoff — `scs_curve_number`

**Inputs:** `rainfall_mm` (mm), `area_km2` (km²), `curve_number` (1, greater than 0 through 100), `initial_abstraction_ratio` (1, 0–1; default example **0.20**). Retention `S = 25400/CN − 254` (mm), initial abstraction `Ia = λ S` (mm), and runoff `Q = 0` if `P ≤ Ia`, else `Q = (P−Ia)²/(P−Ia+S)` (mm).

**Outputs:** `potential_retention_mm` (mm), `initial_abstraction_mm` (mm), `direct_runoff_depth_mm` (mm), `direct_runoff_volume_m3` (m³; `Q_mm × area_km² × 1000`), `event_runoff_coefficient` (1; depth ratio to rainfall). The CN is externally selected to reflect soils, cover and antecedent moisture. This method computes **event direct-runoff depth** rather than time-of-concentration, peak discharge or routed hydrographs.

**Published check:** [USDA NRCS *TR-55*, 2nd ed., Ch. 2, Example 2-1 / Worksheet 2](https://infosys.ars.usda.gov/svn/code/third_party/climate/dat2par/tr55t.pdf): P=6.0 in (152.4 mm), weighted CN=70, λ=0.20 → computed **2.80519 in**, worksheet **2.81 in** to its printed 0.01 in.

## 6. FAO-56 Penman–Monteith reference ET — `fao56_evapotranspiration`

**Inputs:** `tmax_c` / `tmin_c` (°C), `actual_vapour_pressure_kpa` (kPa), `wind_speed_2m_m_s` (m/s at 2 m), `elevation_m` (m), `net_radiation_mj_m2_day` and `soil_heat_flux_mj_m2_day` (both MJ/m²/day). Rn and G are supplied **net values**, not shortwave radiation; the operation does not synthesize them from sunlight hours or cloud cover. FAO-56 equation 6 for the daily short-grass reference surface:

`ET₀ = [0.408 Δ (Rn−G) + γ [900/(Tmean+273)] u₂ (es−ea)] / [Δ + γ(1+0.34 u₂)]` (mm/day).

Here `Tmean=(Tmax+Tmin)/2` (°C), `es=[e°(Tmax)+e°(Tmin)]/2` (kPa) with `e°(T)=0.6108 exp(17.27T/(T+237.3))`, `Δ = 4098 e°(Tmean)/(Tmean+237.3)²` (kPa/°C), `P = 101.3[(293−0.0065z)/293]^5.26` (kPa), and `γ=0.000665 P` (kPa/°C). Real ET for a crop requires an appropriate crop coefficient and additional context; this operation is **ET₀**, not ETc.

**Outputs:** `reference_et0_mm_day`, `radiation_term_mm_day`, `aerodynamic_term_mm_day` (all mm/day); `mean_temperature_c` (°C); `saturation_vapour_pressure_kpa` and `vapour_pressure_deficit_kpa` (kPa); `slope_saturation_curve_kpa_per_c`, `psychrometric_constant_kpa_per_c` (kPa/°C); `atmospheric_pressure_kpa` (kPa).

**FAO-56 worked checks:** [FAO Irrigation and Drainage Paper 56, Chapter 4](https://www.fao.org/4/x0490e/x0490e08.htm):

| Published case | Values entered (Rn and G as in the worked sheet) | Computed ET₀ | Published ET₀ |
| --- | --- | ---: | ---: |
| Example 17, Bangkok, April | Tmax 34.8 °C, Tmin 25.6 °C, ea 2.85 kPa, u₂ 2 m/s, z 2 m, Rn 14.33 MJ/m²/day, G 0.14 MJ/m²/day | 5.71538 mm/day | 5.72 mm/day; **5.7** to 0.1 |
| Example 20, Lyon, July | Tmax 26.6 °C, Tmin 14.8 °C, ea 1.68 kPa, u₂ 2 m/s, z 200 m, Rn 13.48 MJ/m²/day, G 0 | 4.56511 mm/day | 4.56 mm/day; **4.6** to 0.1 |

The numerical calculation retains full precision for derived Δ, γ and es. FAO's printed intermediate values are rounded, giving approximately 0.01 mm/day difference at the second decimal in Example 20. Both examples reproduce their published **final single-decimal values**.

## 7. Mann–Kendall and Sen slope — `mann_kendall_sen`

**Inputs:** `time_year` (strictly increasing years), `observations` (vector in the named measurement unit), `observation_unit` (text unit label, e.g. mg/L, °C or mm), `significance_level` (1, e.g. 0.05). At least three paired samples are required. Enter actual elapsed years, including gaps, for correct Sen slopes; the rank-based Mann–Kendall S is insensitive to spacing.

For all i < j, `S = Σ sign(yj−yi)` (1). The tie-corrected `Var(S) = [n(n−1)(2n+5) − Σ t(t−1)(2t+5)]/18` (1), where each t is a repeated-value group size. The large-sample z uses the usual ±1 continuity correction and two-sided `p=erfc(|z|/√2)` (1). For **3–10 untied measurements**, exact permutation inversion counts replace the normal p-value, avoiding misleading small-sample p-values. Sen's slope is the median of `(yj−yi)/(xj−xi)` in **observation unit/year**, and the reported intercept is the median of `yi−slope·xi` in **observation unit**. Kendall τb adjusts for tied values; all-equal observations return p=1, slope=0 and τb=0.

**Outputs:** `mann_kendall_s`, `variance_s`, `z_score`, `two_sided_p_value`, `kendall_tau_b`, `significance_level` (all 1); `sen_slope_per_year` (numeric, unit specified by `sen_slope_unit` as e.g. mg/L/year); `sen_intercept` (numeric, unit specified by `intercept_unit`); `p_value_method`, `trend` (categories, 1). A table lists year, observed value and Sen fitted value with units; the plot shows the Sen line in chosen units over years. `trend` says increasing/decreasing only when two-sided p < α, else not significant.

The test is **nonseasonal** and assumes independent observations with no autocorrelation. Seasonality, serial correlation, censored values and step changes require specialized methods; a small p is not evidence of causation. See [USGS *Groundwater Levels in the Denver Basin*, Appendix 3](https://pubs.usgs.gov/sir/2020/5076/sir20205076.pdf) for MK and Sen's slope and [Pennsylvania DEP Statistical Guidance, §IV](https://files.dep.state.pa.us/EnvironmentalCleanupBrownfields/LandRecyclingProgram/LandRecyclingProgramPortalFiles/GuidanceTechTools/section_iv_january_2008_revision.pdf) for environmental trend analysis cautions.

## Tests and compatibility

Run `python -m pytest -q backend/tests/test_scientific_environment.py` from the repository root. Tests verify EPA 2024 breakpoint transitions and published subindices, FAO-56 Examples 17/20 at their published precision, Briggs dispersion widths, USDA TR-55 Example 2-1, equation limits and invalid inputs, legacy values, and every operation's saved-result-compatible artifact shape. The pack uses only the Python standard library (`math`, `decimal`, `statistics`, `collections`, `fractions`, `itertools`) plus the existing shared Scientific Workbench core. `OPERATIONS` is explicitly typed as `tuple[Operation, ...]` for static type checking. No other `docs/SCIENTIFIC_*.md` files are changed.
