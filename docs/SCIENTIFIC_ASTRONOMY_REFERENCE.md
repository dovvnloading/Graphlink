# Scientific Workbench — Astronomy reference

The Astronomy pack is self-contained (Python standard library only) and exposes
**12** operations via the existing Scientific Workbench `Operation`/`Field`
contract. The original operations (`parallax_distance`, `distance_modulus`,
`blackbody_peak`), their input keys, result keys, examples and calculations are
unchanged. The shared result serializer, provenance hashes and persisted result
format are unchanged; old saved workbench results remain readable. The manifest
and pack version remain **1.1.0**. New operations are appended to the tuple,
which is explicitly typed `tuple[Operation, ...]`.

## Operation contracts and units

All angles passed into the UI are **degrees** (not radians or RA hours), and
all longitude-like angles increase eastward or toward increasing right
ascension. Every `Field` has a unit, including `1` for dimensionless
parameters, `frame` for frame names, and `ISO-8601 UTC` for timestamps.
Result `values` keys carry units; when the quantity is dimensionless the key
specifies `fraction`, `ratio` or the explicitly dimensionless term. An **AU**
is exactly 149,597,870,700 m, a Julian day is exactly 86,400 s, a Julian year
is 365.25 days, and the nominal solar GM is 1.32712440018×10²⁰ m³/s².

| Operation | Inputs (key: unit) | Outputs (key: unit) |
|---|---|---|
| `parallax_distance` | `parallax_mas`: mas | `distance_pc`: pc; `distance_ly`: Julian light-year |
| `distance_modulus` | `apparent_mag`, `absolute_mag`, `extinction_mag`: mag | `true_distance_modulus_mag`: mag; `distance_pc`: pc |
| `blackbody_peak` | `temperature_k`: K | `peak_wavelength_nm`: nm |
| `keplerian_orbit` | `semimajor_axis_au`: AU; `eccentricity`: 1; `inclination_deg`, `ascending_node_deg`, `argument_periapsis_deg`, `mean_anomaly_epoch_deg`: deg; `epoch_jd`, `target_jd`: JD (days); `central_mass_solar`: solar mass | `x_au`, `y_au`, `z_au`, `radius_au`: AU; `vx_au_per_day`, `vy_au_per_day`, `vz_au_per_day`, `speed_au_per_day`: AU/day; `mean_anomaly_deg`, `eccentric_anomaly_deg`, `true_anomaly_deg`: deg; `orbital_period_days`: days |
| `julian_sidereal_time` | `utc_iso`: ISO-8601 UTC; `observer_longitude_deg_east`: deg east | `julian_date_jd`: JD (days); `greenwich_mean_sidereal_hours`, `local_mean_sidereal_hours`: sidereal hours |
| `coordinate_frames` | `source_frame`, `target_frame`: frame; `source_longitude_deg`, `source_latitude_deg`, `observer_latitude_deg`: deg; `observer_longitude_deg_east`: deg east; `utc_iso`: ISO-8601 UTC | `target_longitude_deg`, `target_latitude_deg`: deg; `julian_date_jd`: JD (days); `local_mean_sidereal_hours`: sidereal hours |
| `airmass_extinction` | `apparent_altitude_deg`: deg; `extinction_coefficient_mag_per_airmass`: mag/airmass; `observed_mag`: mag | `relative_airmass`: 1; `extinction_mag`, `above_atmosphere_mag`: mag; `transmission_fraction`: 1 |
| `flat_lcdm_distances` | `redshift`: 1; `h0_km_s_mpc`: km/s/Mpc; `omega_matter`: 1 | `comoving_distance_mpc`, `luminosity_distance_mpc`, `angular_diameter_distance_mpc`: Mpc; `lookback_time_gyr`: Gyr |
| `ab_flux_to_magnitude` | `flux_density_jy`: Jy (spectral fν) | `ab_magnitude_mag`: mag AB |
| `ab_magnitude_to_flux` | `ab_magnitude_mag`: mag AB | `flux_density_jy`: Jy (spectral fν) |
| `ab_colour_index` | `first_ab_mag`, `second_ab_mag`: mag AB | `colour_index_mag`: mag AB; `first_to_second_flux_ratio`: 1 |
| `exoplanet_transit` | `out_of_transit_flux_relative`, `in_transit_flux_relative`: relative flux (same scale); `stellar_radius_solar`: nominal solar radii; `semimajor_axis_au`: AU; `orbital_period_days`: days; `impact_parameter_stellar_radii`: stellar radii | `transit_depth_fraction`, `radius_ratio_planet_star`: 1; `transit_depth_ppm`: parts per million; `planet_radius_earth`: nominal Earth equatorial radii; `total_duration_hours`, `full_duration_hours`, `ingress_duration_hours`: hours |

A value in the **equatorial** frame is `(right ascension, declination)`;
**ecliptic** or **galactic** is `(longitude, latitude)`; **horizontal** is
`(azimuth from true north toward east, geometric altitude)`. The generic
`target_longitude_deg`/`target_latitude_deg` output keys follow this convention.
All longitude outputs are reduced to `[0°, 360°)`, and latitude/altitude is
`[-90°, +90°]`. At the celestial poles or zenith, longitude/azimuth is
mathematically indeterminate, so do not interpret its numerical value.

## Numerical models, scope, and caveats

**Two-body orbit.** Given semimajor axis *a*, eccentricity *e*, orientation
`(i, Ω, ω)`, mean anomaly *M₀* at epoch `epoch_jd`, and a target JD, the mean
motion is `n = √(G M/a³)` and `M = M₀ + n Δt`. For an **elliptic orbit only**
`0 ≤ e < 1`, the code solves `E − e sin E = M` with guarded **Newton
iteration** (bracket/bisection fallback) to a small residual, then rotates
`[a(cos E−e), a√(1−e²)sin E, 0]` and its analytic derivative into Cartesian
axes using `R₃(Ω) R₁(i) R₃(ω)`. The specified central mass is in solar masses;
small-body mass and all perturbations are neglected. The output Cartesian
axes are the **same inertial axes in which the orbital angular elements are
specified** (choose, for example, J2000 ecliptic). Orbital epoch/target JDs
should use a **uniform dynamical timescale**, preferably TDB for solar-system
orbits; a UTC civil calendar is **not** an appropriate time argument for
precision orbit fitting. For `e → 1`, `a → 0`, large propagation spans or
unphysical orbital elements, double precision and the two-body approximation
limit accuracy. Hyperbolic/parabolic trajectories are not provided.

**Time and sidereal rotation.** ISO-8601 timestamps are UTC with explicit
`Z` or `+00:00`; the calendar is **proleptic Gregorian**. Meeus's mean sidereal
polynomial returns **GMST**, not apparent sidereal time, in sidereal hours.
`LMST = (GMST + east_longitude/15) mod 24`. JD is based on UTC; for Earth
rotation, **UT1 rather than UTC** is strictly needed for sub-second sidereal
accuracy. Leap seconds are not directly representable (`23:59:60` is rejected).
No ΔUT1, ΔT, nutation or apparent-sidereal correction is available.

**Frames.** Equatorial and ecliptic coordinates are referred to the **mean
equinox and equator/ecliptic of the input date**; ecliptic rotation uses
Meeus's mean obliquity polynomial. Fixed IAU Galactic directions use the
J2000/ICRS Galactic matrix. Mean equatorial positions are precessed from/to
J2000 with the IAU 1976/Meeus model for Galactic transitions. Horizontal
coordinates use **local mean sidereal time** and geodetic observer latitude
for a distant source; they are **geometric**, not refraction-corrected.
The implementation deliberately restricts frame conversions to **1800–2200**.
It does not perform proper motion, parallax, topocentric lunar/planetary
shifts, nutation, aberration, Earth orientation corrections, or atmospheric
refraction, so it is a planning/teaching or approximate coordinate conversion,
**not** an astrometric reduction pipeline. Match input frame epochs before
using catalog positions; an ICRS catalog coordinate is not an equatorial-of-date
coordinate until precessed. `airmass_extinction` specifically requires an
**apparent** altitude, which may differ from the geometric altitude returned
by `coordinate_frames`, especially near the horizon.

**Airmass.** Kasten & Young (1989) empirical optical relative air mass at
apparent altitude *h*, valid only for `0° ≤ h ≤ 90°`, is
`X = 1/[sin(h) + 0.50572(h + 6.07995)^(−1.6364)]` (angle inside the power in
degrees). A uniform single-passband extinction coefficient *k* gives
`A = k X` (magnitudes), `f_ground/f_above = 10^(−0.4 A)`, and
`m_above = m_observed − A`. Horizon results are approximate; no wavelength-
dependent refraction, weather, altitude or site-pressure adjustment is made.

**Cosmology.** For `0 ≤ z ≤ 1000`, `0 ≤ Ωm ≤ 1`, `H₀ > 0` with
`ΩΛ = 1 − Ωm`, `E(z)=√[Ωm(1+z)³ + ΩΛ]`, and **radiation set to zero**:

- `D_C = (c/H₀)∫₀ᶻ dz'/E(z')` (radial and transverse comoving coincide for flatness)
- `D_L = (1+z) D_C`; `D_A = D_C/(1+z)`
- `t_L = H₀⁻¹∫₀ᶻ dz'/[(1+z')E(z')]`.

Adaptive Simpson quadrature on `log(1+z)` uses a bound in Hubble units of
`2×10⁻¹¹` (before floating-point rounding). This is **flat matter+Λ only**:
no radiation, curvature, neutrino sector, non-Λ dark energy, peculiar
velocity, lensing or K-correction. Radiation omission causes increasing error
at high redshift, **especially at z ≳ 100**; reported values there are only
results of the defined simplified model. 1 Gyr = 10⁹ Julian years.

**AB photometry and transits.** `m_AB = −2.5 log₁₀(fν/3631 Jy)` and
`fν = 3631 Jy × 10^(−0.4 m_AB)`; `m₁−m₂` yields `fν₁/fν₂ = 10^(−0.4(m₁−m₂))`.
These are **spectral flux densities**, not band-integrated energy fluxes or
bolometric magnitudes. Color calculations assume comparable AB calibration
and photometric band definitions; no extinction or K-correction is applied.
For the transit calculation, `δ = 1 − f_in/f_out = (Rp/R*)²`, and for a
**circular** orbit with `b = (a/R*) cos i`, the first-to-fourth contact duration
is `T₁₄ = (P/π) asin[(R*/a) √((1+k)²−b²) / sin i]`, with `k = Rp/R*`.
`T₂₃` substitutes `(1−k)²`. **Only non-grazing transits with `b < 1−k` are accepted**; for grazing events, the observed depth does not equal the full planetary area fraction, so inferring `k=√δ` would produce a biased radius. Supply a non-grazing fit or a limb-overlap model instead;
one ingress or egress takes `(T₁₄−T₂₃)/2`. **No** dilution, finite integration
cadence, starspots, limb darkening or orbital eccentricity. Use a fitted
light-curve depth and system parameters, not a noisy single sample; results
are not uncertainties or limb-darkened transit fits. Solar radius is
6.957×10⁸ m, terrestrial equatorial radius is 6.3781×10⁶ m (IAU nominal).

## Published checks and tolerances

`backend/tests/test_scientific_astronomy.py` runs calculations through the
Workbench's public `run_operation`, tests every example and old operation,
checks rejecting bad domains, performs round trips, and compares:

| Reference | Inputs | Expected / allowed agreement |
|---|---|---|
| Jean Meeus, *Astronomical Algorithms* (2nd ed.), Example 7.a | 1957 Oct 4.81 UTC | JD **2436116.31**, tested to 2×10⁻⁸ day |
| Meeus, Chapter 11, Examples 11.a and 11.b | 1987 Apr 10 at 00:00 and 19:21 UTC | GMST **13h 10m 46.3668s** and **8h 34m 57.0896s**; tested within 0.00005 sidereal s (published to 0.0001 s; model computed differences ≈0.00002 s) |
| Meeus, Kepler worked examples 22.a and 22.b | e=0.1, M=5°; e=0.99, M=2° | E=**5.554589°**, true anomaly **6.139762°**; E=**32.361007°**, true anomaly **152.542134°** (±5×10⁻⁷°) |
| IAU Galactic frame landmarks and mean obliquity at J2000 | Galactic centre, pole, and ecliptic lon=90° | (l,b)≈(0°,0°), b≈90°; ε=**23°26′21.448″** |
| Kasten–Young (1989), *Applied Optics* **28**, 4735–4738 | apparent altitudes 0°, 10°, 30°, 45°, 90° | Reproduces their analytic approximation to relative 10⁻¹⁴; X(0°)≈37.92 |
| Wright (2006) flat-ΛCDM formulas; independently printed values at [iCosmos comparison](https://icosmos.co.uk/compare/) | z=1, H₀=70 km/s/Mpc, Ωm=0.3 | D_C=**3303.83 Mpc**, D_L=**6607.66 Mpc**, D_A=**1651.91 Mpc** (±0.005 Mpc); t_L=**7.71534 Gyr** (±0.000005 Gyr) |
| AB 3631-Jy definition and circular uniform-disk transit equations | AB=20, 1% transit depth | fν=**36.31 µJy**, Rp/R*=**0.1**; contact geometry and inverse conversion agree algebraically |

Other tests include exact analytic flat-universe limits (Ωm=0 and Ωm=1),
periodic propagation and Kepler residuals near e=1, vector length/speed,
frame inversion, field-unit labels and negative-domain validation.

### Primary references

- Meeus, Jean, *Astronomical Algorithms*, 2nd edition, Willmann-Bell, 1998. Reproducible [worked sidereal examples](https://pkg.go.dev/github.com/soniakeys/meeus/v3/sidereal) and [Kepler example](https://pymeeus.readthedocs.io/en/stable/core/Coordinates.html).
- U.S. Naval Observatory / *The Astronomical Almanac*, [approximate sidereal time](https://aa.usno.navy.mil/faq/GAST), [horizontal coordinates](https://aa.usno.navy.mil/faq/alt_az), [sidereal reference service](https://aa.usno.navy.mil/data/siderealtime).
- ESA Hipparcos/Gaia Galactic conventions; M. Perryman, *Astronomical Applications of Astrometry*, 2009, Galactic matrix, Sect. 1.2.
- Kasten, F. & Young, A. T. (1989), *Revised optical air mass tables and approximation formula*, *Applied Optics*, **28**, 4735–4738. [Reproduced approximation](https://www.e-education.psu.edu/eme810/node/533).
- Hogg, D. W. (1999), *Distance Measures in Cosmology*, [astro-ph/9905116](https://arxiv.org/abs/astro-ph/9905116); Wright, E. L. (2006), *A Cosmology Calculator for the World Wide Web*, PASP **118**, 1711. NASA/IPAC [cosmology-calculator reference](https://ned.ipac.caltech.edu/help/cosmology_calc.html).
- IAU Resolution B2 (2012) on the AU and Resolution B3 (2015) on nominal solar/planetary conversion constants.

### Independent correctness audit: transit geometry

A **grazing** transit does not fully superimpose the planetary disk on the stellar disk at mid-transit. The simple uniform-disk inversion `k = sqrt(depth)` then underestimates the true radius ratio even before limb darkening. The operation now explicitly rejects `b >= 1-k` with a physical-domain error instead of reporting a spurious radius and zero T23. This does **not** add a grazing overlap model. A non-grazing 1% depth still implies `k = 0.1` (1), and the contact durations remain in hours. See [NASA Exoplanet Archive transit documentation](https://exoplanetarchive.ipac.caltech.edu/docs/API_transit_detection.html) and Seager & Mallén-Ornelas (2003), *ApJ* 585, 1038–1055.
