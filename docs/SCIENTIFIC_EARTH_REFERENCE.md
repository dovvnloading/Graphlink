# Scientific Workbench — Earth Science field reference

This pack retains the three existing `great_circle_distance`, `geothermal_gradient` and `seismic_distance` operations **without changing IDs, fields, defaults, calculators, summaries, outputs or stored receipts**. Eleven new operations append to the catalog. The workbench's existing result/provenance schema and saved workbench nodes are unchanged. Calculations use only Python's **standard library** (no network access, GIS library, SciPy or astronomical API).

Latitudes are **positive north** (°N), longitudes **positive east** (°E); bearings and azimuths increase **clockwise from true north**. Output suffixes encode units; `_1` is dimensionless. **Continuous numerical values in the result payload are unrounded binary64 floats; zone numbers and event counts are integers**; the decimal digits shown in summaries (`:.6g` for most scientific values and `:.3f` for geodesic distance in metres) are presentation, not extra accuracy. EPA-style discrete precision rules do not apply to Earth Science. In particular, the original great-circle distance uses a sphere of mean radius 6371.0088 km, *not* WGS84, and is not modified.

## Complete unit reference

| Operation | Inputs (units) | Outputs (units) |
| --- | --- | --- |
| `great_circle_distance` (original) | `latitude_a`, `longitude_a`, `latitude_b`, `longitude_b` (°) | `distance_km` (km), `central_angle_deg` (°), `sphere_radius_km` (km) |
| `geothermal_gradient` (original) | `shallow_depth_m`, `deep_depth_m` (m); `shallow_temperature_c`, `deep_temperature_c` (°C) | `gradient_c_per_km` (°C/km), `depth_interval_m` (m), `temperature_change_c` (°C) |
| `seismic_distance` (original) | `s_minus_p_s` (s); `p_speed_km_s`, `s_speed_km_s` (km/s) | `path_length_km` (km), `p_travel_s`, `s_travel_s` (s) |
| `geodesic_inverse` | `latitude_a_deg`, `latitude_b_deg` (°N); `longitude_a_deg`, `longitude_b_deg` (°E) | `distance_m` (m); `initial_bearing_deg`, `reverse_bearing_deg` (° clockwise from true north); `solution_method` (method category) |
| `utm_forward` | `latitude_deg` (°N), `longitude_deg` (°E) | `zone_1` (integer zone 1–60), `hemisphere` (N/S category), `easting_m`, `northing_m` (m) |
| `utm_inverse` | `zone_1` (zone number 1–60), `hemisphere` (N/S category), `easting_m`, `northing_m` (m) | `latitude_deg` (°N), `longitude_deg` (°E) |
| `radiometric_age` | `parent_ratio_1`, `radiogenic_daughter_ratio_1` (dimensionless ratios using the same normalizer); `half_life_ma`, `half_life_standard_uncertainty_ma` (Ma) | `age_ma`, `age_standard_uncertainty_ma` (Ma); `daughter_parent_ratio_1` (1) |
| `darcy_flow` | `hydraulic_conductivity_m_s` (m/s), `head_upstream_m`, `head_downstream_m`, `flow_length_m` (m), `cross_section_area_m2` (m²), `effective_porosity_1` (1) | `hydraulic_gradient_1` (m/m = 1); `darcy_flux_m_s`, `pore_velocity_m_s` (m/s); `discharge_m3_s` (m³/s) |
| `theis_drawdown` | `pumping_rate_m3_s` (m³/s), `transmissivity_m2_s` (m²/s), `storativity_1` (1), `radial_distance_m` (m), `time_since_pumping_s` (s) | `drawdown_m` (m); `theis_u_1`, `well_function_e1_1` (1) |
| `moment_magnitude` | `seismic_moment_nm` (N·m) | `moment_magnitude_mw` (Mw; dimensionless logarithmic magnitude), `estimated_radiated_energy_j` (J) |
| `seismic_moment` | `moment_magnitude_mw` (Mw; 1) | `seismic_moment_nm` (N·m), `estimated_radiated_energy_j` (J) |
| `gutenberg_richter` | `magnitudes_mw` (vector, Mw), `completeness_magnitude_mw`, `bin_width_mw`, `target_magnitude_mw` (Mw) | `events_above_completeness_1`, `events_below_completeness_1`, `observed_count_at_target_1`, `predicted_count_at_target_1` (event counts, 1); `a_intercept_log10_count_1` (log10 count, 1); `b_value_per_mw`, `b_approx_standard_error_per_mw` (Mw⁻¹) |
| `isa_atmosphere` | `geometric_altitude_m` (m above mean sea level) | `geopotential_altitude_m` (m), `temperature_k` (K), `temperature_c` (°C), `pressure_pa` (Pa), `density_kg_m3` (kg/m³) |
| `solar_position` | `timestamp_iso8601` (ISO 8601 timestamp with UTC offset), `latitude_deg` (°N), `longitude_deg` (°E) | `elevation_deg` (refraction-corrected °), `geometric_elevation_deg` (°), `azimuth_deg` (° true north clockwise), `declination_deg` (°), `equation_of_time_min` (minutes), `standard_refraction_correction_deg` (°) |

The Gutenberg–Richter table columns are `Minimum magnitude (Mw)`, `Observed cumulative count (1)` and `Fitted cumulative count (1)`; counts are numbers of events at or above each threshold.

## WGS84 geodesics and UTM

WGS84 has semi-major axis **6,378,137.0 m** and inverse flattening **298.257223563** ([NGA WGS84](https://earth-info.nga.mil/?action=wgs84&dir=wgs84)). Vincenty's inverse iterates longitude to 10⁻¹³ radians (max 200 iterations). When inverse Vincenty fails to converge near antipodes, multi-start shooting uses the **Vincenty direct ellipsoidal** equations, finds endpoint latitude/longitude, and retains the shortest converged solution: it does **not** substitute a sphere. Unresolvable cases raise an error. Bearings for coincident locations are mathematically undefined; this operation returns 0° by explicit numerical convention. At a **geographic pole**, the initial bearing when the start is a pole and the reverse bearing when the destination is a pole are also undefined: the respective field is returned as **0° by convention**, and `solution_method` discloses this convention. `reverse_bearing_deg` is the bearing **B→A**, not the forward terminal course. A short crossing of the antimeridian, from (10°N, 179.999°E) to (10°N, 179.999°W), agrees with the [Karney WGS84 geodesic](https://geographiclib.sourceforge.io/html/python/geodesics.html) at **219.278728 m**; the shortest path, not the ~360° complement, is used.

The [ICSM GDA2020 Technical Manual v1.8, Table 5.1](https://www.icsm.gov.au/sites/default/files/GDA2020%20Technical%20Manual%20V1.8_published_0.pdf) prints Flinders Peak (37°57′03.72030″ S, 144°25′29.52440″ E) to Buninyong (37°39′10.15610″ S, 143°55′35.38390″ E): **54,972.271 m**, initial bearing **306°52′05.37″**, reverse bearing **127°10′25.07″**. Tests compare to the **1 mm and 0.01 arcsecond printed digits** (±0.0005 m and ±0.005″). The exact equatorial antipode has an ellipsoidal half-meridian distance **20,003,931.459 m**, unlike a spherical half-circumference.

UTM forward and inverse use WGS84 Transverse Mercator series, central scale **0.9996**, **500,000 m false easting**, **10,000,000 m southern false northing**, automatic 6° zone selection and Norway/Svalbard zone exceptions. Coverage is **80°S to 84°N**; UPS at the poles is unsupported. Inverse requires zone and literal hemisphere N/S, not an MGRS latitude band. These series target field/GIS coordinates and do not perform datum, geoid, plate epoch or cadastral transformations; do not claim millimetre cadastral accuracy at zone extremes. [USGS Professional Paper 1395](https://pubs.usgs.gov/pp/1395/report.pdf) gives projection theory. WGS84 **40°N, 105°W → UTM zone 13N, E=500,000 m, N=4,427,757.219 m**, cross-checked to the [published UTM example](https://roughlogic.com/tools/utm-conversion/) and tested along with southern, Norway/Svalbard and inverse round-trip cases.

## Geochronology, groundwater and aquifers

For a *closed-system*, initial-daughter-corrected decay ratio, `t=(t½/ln 2) ln(1+D*/P)` in **Ma**, where radiogenic daughter `D*` and surviving parent `P` are normalized consistently. Only half-life **1σ** standard uncertainty propagates: `u(t)=t·u(t½)/t½` (Ma). This is not total geological age uncertainty: ratio measurement errors, initial-daughter correction, common lead, fractionation, system openness, decay branching and covariances are excluded. The example one-to-one daughter/parent ratio with **1250 ± 5 Ma** half-life gives **1250 ± 5 Ma** age. Parent and half-life must be positive, daughter and uncertainty nonnegative. The implementation multiplies the half-life by the dimensionless age-per-half-life factor, avoiding an avoidable intermediate overflow for a large finite half-life and a small daughter/parent ratio; no extra numerical rounding is applied.

For signed, 1-D saturated steady Darcy flow along the named upstream-to-downstream axis, gradient `i=(h_down−h_up)/L` (m/m), specific discharge `q=−K i` (m/s), rate `Q=q A` (m³/s), average linear pore velocity `v=q/n_e` (m/s). Effective porosity must be >0 and ≤1; negative discharge means flow opposite the chosen axis. An example with `K=10⁻⁴ m/s`, heads `10→9 m` over `100 m`, area `20 m²` and effective porosity `0.25` gives `i=−0.01`, `q=10⁻⁶ m/s`, `Q=2×10⁻⁵ m³/s` and `v=4×10⁻⁶ m/s`. The model omits anisotropy, unsaturated transport and heterogeneity.

For constant-rate extraction from an ideal confined aquifer, Theis drawdown is `s=Q E1(u)/(4πT)` (m) with `u=r²S/(4Tt)` (dimensionless), `E1(u)=integral_u^∞ exp(−x)/x dx`. `T` is transmissivity (m²/s), `S` storativity (1), `r` radial distance (m), `t` pumping duration (s). The exponential integral uses a convergent series for `u≤1` and a continued fraction for larger `u`, standard library only. The dimensionless `u` is assembled using logarithms to avoid overflow of the intermediate `r²` and `Tt` products when their ratio is representable; an unrepresentable `u` is rejected explicitly. Requires Q≥0, T>0, 0<S≤1, r>0, t>0. The model assumes an infinite homogeneous confined aquifer, full penetration, constant discharge, zero-radius well, no boundaries, delayed response, leakage or wellbore storage. The [published Theis well-function table](https://pe-calc.com/tools/well-drawdown.html) lists `E1(0.1)=1.823`, `E1(0.5)=0.5598`, `E1(1)=0.2194`, `E1(5)=0.001148`, all asserted to displayed digits.

## Seismology

Hanks–Kanamori moment magnitude: `Mw=(2/3)(log10(M0[N·m])−9.1)` and `M0[N·m]=10^(1.5Mw+9.1)`. The approximate radiated-energy relation `log10(E[J])≈1.5Mw+4.8` is **not** a direct measurement of seismic energy. For **Mw=6**, seismic moment is **1.2589254×10¹⁸ N·m** and empirical energy is **6.3095734×10¹³ J**. A universal local Richter `ML`→`Mw` conversion does not exist without regional instrumental calibration; this pack does not invent one. The inverse magnitude conversion accepts **positive finite binary64** scalar moments and empirical energy estimates within representable ranges, rather than enforcing an arbitrary `10³⁰⁰` cutoff; moment-to-magnitude rejects an estimate that would underflow to exactly zero joules.

Given a catalogue complete above a supplied `Mc` and rounded to bin width `ΔM`, the discrete Aki maximum-likelihood Gutenberg–Richter slope is `b=log10(e)/(mean(M≥Mc)−Mc+ΔM/2)` (Mw⁻¹). The intercept is `a=log10 N(M≥Mc)+b Mc` (log10 event count), so `log10 N(M≥m)=a−bm`. Counts and fitted cumulative frequencies use the provided spatial and temporal catalogue window. The estimate drops below-completeness events, requires ≥2 above threshold, and reports `b/√N` as an approximate **1σ**, not a complete uncertainty budget. The target prediction threshold must be **at or above Mc**, since a catalogue incomplete below Mc cannot support its fitted cumulative counts there. Cumulative table thresholds use **decimal-exact bin steps based on the supplied numbers**, before conversion to output floats, so a stated `0.3 Mw` edge is not omitted by `0.3/0.1` rounding. Magnitudes should be on a consistent scale; `Mc` is **assumed**, not estimated. Catalog selection, aftershocks, truncation, completeness changes and clustered events invalidate naive statistical inferences.

## International Standard Atmosphere

For dry air, the US Standard Atmosphere 1976/ISA uses geometric `z` converted to geopotential altitude `h=6356766z/(6356766+z)` (m). Constants: `g0=9.80665 m/s²`, `R=8314.32/28.9644 J/(kg·K)`, sea-level `T0=288.15 K`, `P0=101325 Pa`. Standard layer temperatures and pressures follow `T=T_b+L_b(h−h_b)` (K), `P=P_b(T/T_b)^(−g0/(R L_b))` (Pa) for nonzero lapse rate `L_b`, `P=P_b exp(−g0(h−h_b)/(RT_b))` for isothermal layers. Density is `ρ=P/(RT)` in kg/m³. Geometric altitude coverage is **−2000 to 86000 m**. **Standards scope:** [ICAO Doc 7488](https://store.icao.int/en/manual-of-the-icao-standard-atmosphere-extended-to-80-kilometres-262500-feet-doc-7488) describes the international reference through **80 km**, while this implementation follows the layer structure and constants of the [U.S. Standard Atmosphere, 1976](https://ntrs.nasa.gov/api/citations/20050207438/downloads/20050207438.pdf?attachment=true) through its **86 km geometric** limit. Do not attribute the 80–86 km portion to ICAO Doc 7488. The returned geopotential height, density, temperature and pressure are full-precision model evaluations, not values rounded to the printed table digits. This is a climatological *standard*, not today's pressure, temperature, humidity or density observation.

[1976 Standard Atmosphere layer table](https://www.braeunig.us/space/atmmodel.htm) values at **geopotential** layer heights (tests convert the given geometric input to recover h):

| h (m) | Temperature (K) | Pressure (Pa) |
| ---: | ---: | ---: |
| 0 | 288.15 | 101325.0 |
| 11,000 | 216.65 | 22632.06 |
| 20,000 | 216.65 | 5474.889 |
| 32,000 | 228.65 | 868.0187 |
| 47,000 | 270.65 | 110.9063 |
| 51,000 | 270.65 | 66.93887 |
| 71,000 | 214.65 | 3.956420 |

All compared to the **published printed digits** without rounding intermediate height. Sea-level density is **1.225 kg/m³**. Other ISA summaries may use a slightly rounded gas constant; do not mix constants while claiming all table digits.

## NOAA solar position: reference and accuracy

The [NOAA solar calculator method](https://www.gml.noaa.gov/grad/solcalc/calcdetails.html) uses Meeus-based Julian-century orbital elements, equation of time, declination, true solar time/hour angle, horizontal coordinates, and NOAA's piecewise **standard atmospheric refraction**. The instant must be timezone-aware ISO 8601, e.g. `2003-10-17T19:30:30Z` equals `2003-10-17T12:30:30-07:00`. Year range **1901–2099** matches NOAA's spreadsheet approximation. Azimuth uses **0° north, 90° east, 180° south, 270° west**. Solar-centre `geometric_elevation_deg` omits atmospheric refraction; `elevation_deg` includes the standard correction, not observed local refraction. At a pole or exact zenith the azimuth is undefined and 0° is returned by documented convention. Terrain horizon, observer elevation, site pressure and temperature are not modeled.

[NREL Solar Position Algorithm (SPA), Golden, Colorado published example](https://www.nrel.gov/docs/fy08osti/34302.pdf): **2003-10-17 12:30:30 MST** / **19:30:30Z** at **39.742476°N, 105.1786°W**, SPA azimuth **194.34024°**, apparent elevation **39.88838°** at pressure 820 mbar and temperature 11°C. This **NOAA approximation** yields azimuth ~**194.34258°**, apparent elevation ~**39.89136°** using standard refraction, and geometric elevation ~**39.87207°**. The angles agree to **0.01° (±0.005°)**, **not** SPA's five decimal digits: the methods and refraction conditions differ. The regression test intentionally uses that justified accuracy. For survey-grade solar angles, use SPA with its local meteorology and ΔT; do not claim that NOAA's approximation attains SPA precision.

## Testing and scope

In a complete Graphlink development checkout, run:

```sh
python -m pytest -n0 backend/tests/test_scientific_earth.py
```

The tests cover all registered new workbench examples and legacy saved receipts, published Vincenty metre/arcsecond cases and Karney antimeridian distances, WGS84 near-antipodal and pole-bearing conventions, Norway/Svalbard and dateline UTM boundaries, isotope age/half-life propagation including finite extreme values, Darcy sign, Theis well-function digits and extreme-but-representable arguments, Mw/M0 including numeric range guards, decimal magnitude-frequency bin endpoints and catalogue-completeness guards, seven ISA pressure/temperature layer values and NOAA solar position with timezone offsets and appropriate comparison tolerance. There is no dependency or network requirement for the new calculators. Only the Earth Science and Environmental Science reference docs were updated in this audit; neither the core registry nor the persistence protocol is modified.
