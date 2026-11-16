# Measurement, field science, and signal processing reference

Each entry gives the measurement model and a small reference calculation. Open a pack in the Scientific Workbench to see its field names, units, and an editable example. For paired vectors, enter measurements in the same order in each field.

## Chemistry

| Operation | Inputs and method | Reference calculation |
| --- | --- | --- |
| Beer–Lambert concentration | Enter blank-corrected absorbance, positive molar absorption coefficient in L/(mol·cm), and path length in cm. For one absorbing species in its linear range, concentration is absorbance divided by the product of coefficient and length. | Absorbance 0.15, coefficient 2000, path 0.5 cm → 0.00015 mol/L. |
| Convert transmission to absorbance | Pair strictly increasing positive wavelengths in nm with transmission in (0, 100]%. Each absorbance is −log₁₀(percent transmission / 100). The first measured maximum identifies the peak wavelength. | 100%, 10%, and 1% → absorbances 0, 1, and 2. |
| Isotope-weighted atomic mass | Enter positive isotope masses in u and number abundances in percent. The calculation weights each mass by its fraction of the supplied abundance total. The allowed rounding difference from 100% is 0.01 percentage point. | Masses 10 and 12 u at 25% and 75% → 11.5 u. |

The optical operations use decadic absorbance. [IUPAC Beer–Lambert law](https://goldbook.iupac.org/terms/view/B00626)

## Biology and ecology

| Operation | Inputs and method | Reference calculation |
| --- | --- | --- |
| Community diversity | Enter whole individual counts in taxon order. Positive counts determine observed richness. For observed proportions p, Shannon entropy is −Σp ln(p) and Gini–Simpson diversity is 1 − Σp². At least one individual must be present. | Four equally represented taxa → richness 4, entropy ln(4) nats, Gini–Simpson 0.75. |
| Quadrat survey density | Pair whole counts with surveyed areas in m². Density for the pooled survey is total count divided by total area. The table also gives within-quadrat densities. | 5 individuals in 1 m² and 0 in 4 m² → 1 individual/m² pooled. |
| Community overlap | Align count vectors by taxon across two sites. A positive count establishes presence. For shared richness J and union richness U, Jaccard similarity is J/U and Sørensen similarity is 2J/(U+J). Two empty surveys have undefined similarity. | Sites with taxa A+B and A+C → Jaccard 1/3 and Sørensen 1/2. |

For conventions used in ecology software, see [vegan diversity indices](https://vegandevs.github.io/vegan/reference/diversity.html) and [vegan community dissimilarities](https://vegandevs.github.io/vegan/reference/vegdist.html).

## Earth science

| Operation | Inputs and method | Reference calculation |
| --- | --- | --- |
| Great-circle distance | Enter latitude and longitude in degrees north and east. Distance equals the shorter central angle in radians times the workbench's spherical Earth radius, 6371.0088 km. Elevation and ellipsoidal flattening are outside this model. | On the equator, 179° E to 179° W spans 2°, about 222.39 km. |
| Two-depth geothermal gradient | Enter shallow and deep borehole depths in m, increasing downward, with measured temperatures in °C. Divide the signed temperature change by the change in depth in km. | A 2 °C decrease from 500 to 750 m gives −8 °C/km. |
| Seismic S–P distance | Supply the S arrival time minus the P arrival time in seconds and positive constant speeds in km/s with Vp > Vs. Source-to-station path length is lag / (1/Vs − 1/Vp). | Lag 5 s, Vp 6 km/s, Vs 3 km/s → 30 km, with P and S travel times of 5 and 10 s. |

The straight-path seismic estimate suits a homogeneous constant-speed model. Measured travel-time curves account for Earth structure. [USGS earthquake travel times](https://www.usgs.gov/programs/earthquake-hazards/earthquake-travel-times)

## Environmental science

| Operation | Inputs and method | Reference calculation |
| --- | --- | --- |
| Flow-weighted concentration | For each interval enter mean flow Q in m³/s, duration t in s, and concentration C in mg/L. The volume V = Qt weights the mean concentration. Constituent mass in kg is ΣVC/1000. A zero-flow interval contributes zero mass. | 20 m³ at 3 mg/L plus 30 m³ at 8 mg/L → 6 mg/L and 0.3 kg total. |
| Observed event runoff coefficient | Enter area-average event rainfall in mm, catchment area in km², and observed direct runoff volume in m³. Divide runoff by rainfall volume. A value above one is retained and flagged for interpretation of the event's water balance. | 5 mm on 4 km² is 20,000 m³. Runoff of 15,000 m³ gives a coefficient of 0.75. |
| Growing degree-days | Pair daily minimum and maximum temperatures in °C for consecutive days. Add the positive part of each daily midpoint minus the chosen base. The method uses no upper cutoff. | Midpoints 2.5, 12, and 15 °C with base 10 °C → 0 + 2 + 5 = 7 °C·days. |

## Materials science

| Operation | Inputs and method | Reference calculation |
| --- | --- | --- |
| Dry bulk density | Divide dry mass in g by external specimen volume in cm³, including pore volume. The result includes g/cm³ and kg/m³. | 15 g in 10 cm³ → 1.5 g/cm³ or 1500 kg/m³. |
| Engineering stress–strain | Pair axial force in N with gauge extension in mm. Divide force by original area in mm² for engineering stress in MPa. Divide extension by original gauge length in mm for strain. The peak is the largest sampled engineering stress. | 400 N over 20 mm² and 1 mm extension over 50 mm → 20 MPa at 0.02 strain. |
| Apparent open porosity | Measure dry mass D, saturated surface-dry mass S, and suspended mass W in the saturating liquid. Correct the suspended measurement for holder tare. Accessible pore volume fraction is (S − D)/(S − W). Require S ≥ D > W and D > 0. | D = 80 g, S = 90 g, W = 50 g → apparent open porosity 25%. |

Apparent porosity assumes full liquid penetration of accessible pores and a nonreactive specimen. [ASTM scope and measurement assumptions](https://store.astm.org/c0020-00r10.html)

## Signal processing

| Operation | Inputs and method | Reference calculation |
| --- | --- | --- |
| Centered moving average | Supply uniformly spaced samples and a positive odd width no greater than the series length. The mean uses each complete window with equal weights. Positions are zero-based center indices. | [0, 0, 9, 0, 0] and width 3 → means [3, 3, 3] at indices [1, 2, 3]. |
| One-sided amplitude spectrum | Enter 2–256 uniformly spaced real samples and positive sampling frequency in Hz. The rectangular-window DFT returns frequencies k·Fs/N, from k = 0 through floor(N/2). Divide magnitudes by N and double the positive-frequency bins with a negative-frequency partner. DC and the even-length Nyquist bin each use a factor of one. | [1, 0, −1, 0, 1, 0, −1, 0] at 8 Hz → unit peak at 2 Hz. |
| Piecewise linear resampling | Pair at least two strictly increasing observed coordinates with amplitudes. Enter target coordinates within the observed interval in the same units. Tables preserve target order, including repeated coordinates. Plots sort by coordinate. | Between (1, 10) and (4, 4), the value at 2.5 is 7. |

The amplitude spectrum reports peak amplitude in the units of the samples. A tone between bin centers contributes to multiple bins. [MathWorks single-sided amplitude convention](https://www.mathworks.com/help/matlab/ref/fft.html)
