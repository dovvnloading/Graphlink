# Measurement, field science, and signal processing reference

Each entry gives the measurement model and a small reference calculation. Open a pack in the Scientific Workbench to see its field names, units, and an editable example. For paired vectors, enter measurements in the same order in each field. Measurements and reported results use finite binary64 numbers. For count totals, the exact integer limit is 9,007,199,254,740,991.

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
| One-sided amplitude spectrum | Enter 2–256 uniformly spaced real samples and positive sampling frequency in Hz. The rectangular-window DFT returns frequencies k·Fs/N, from k = 0 through floor(N/2). Divide magnitudes by N and double the positive-frequency bins with a negative-frequency partner. DC and the even-length Nyquist bin each use a factor of one. The non-DC transform uses centered samples so a constant offset contributes only to DC. | [1, 0, −1, 0, 1, 0, −1, 0] at 8 Hz → unit peak at 2 Hz. |
| Piecewise linear resampling | Pair at least two strictly increasing observed coordinates with amplitudes. Enter target coordinates within the observed interval in the same units. Tables preserve target order, including repeated coordinates. Plots sort by coordinate. | Between (1, 10) and (4, 4), the value at 2.5 is 7. |
| Windowed FFT amplitude and power spectrum | Pair 4–1024 amplitudes with uniform `sample_times_s` in seconds; choose `rectangular`, `hann`, `hamming`, or `blackman`. Uses an FFT for any length (radix-2 / Bluestein), periodic windows, coherent-gain amplitude correction and mean-square window-energy power correction. The table includes bin power and power density in input units²/Hz. | A 3.5-amplitude, 14 Hz bin-centered tone sampled at 128 Hz for 64 samples peaks at exactly 14 Hz and amplitude 3.5 under all four windows. |
| Welch power spectral density | Pair at least 8 uniformly spaced samples/times; select a window, `segment_samples` ≥ 8 (no longer than input), and `overlap_samples` from 0 through segment length − 1. Subtract the mean of each **complete** segment, compute its one-sided modified periodogram, and average the PSDs. No padding/partial trailing segment. | A sine of amplitude 2 at 16 Hz, sampled at 64 Hz with 16-point rectangular segments and zero overlap, has density 0.5 input units²/Hz at 16 Hz and total integrated power 2 input units². |
| Zero-phase Butterworth filter | Pair uniform samples/times, select `lowpass`, `highpass`, or `bandpass`, a prototype `order`, and `cutoff_hz` containing one cutoff for low/high or **two ascending** cutoffs for bandpass. Prewarp cutoffs, bilinear-transform stable Butterworth poles, cascade second-order sections and filter forward/backward with odd-reflection padding. Limits: prototype order 1–8 (bandpass 1–6); cutoffs strictly between zero and Nyquist. | Each cutoff has single-pass gain 1/√2 (≈ −3.0103 dB). Two passes yield gain 1/2 (≈ −6.0206 dB) at cutoff with zero phase shift away from edge transients. |
| Savitzky–Golay smoothing | Pair uniform samples/times, an odd `window_samples` ≥ 3 no longer than the signal, and `polynomial_order` from 0 to 6, strictly smaller than the window. Evaluate a least-squares polynomial at each point; edge points use the first/last full window (interpolating endpoints, no zero padding). | A 5-point quadratic center uses weights [−3, 12, 17, 12, −3]/35. Any cubic sampled polynomial is recovered by a degree-3 fit, including at both edges. |
| Peak detection with prominence and spacing | Pair at least 3 uniform samples/times with `minimum_height`, nonnegative `minimum_prominence`, and integer `minimum_spacing_samples` ≥ 1. Find interior maxima, assigning a flat top to its left-of-center index; prominence compares the peak to the higher of its left/right lowest contour before a **strictly higher** peak. Apply height and prominence first, then retain stronger peaks during spacing conflicts (leftmost wins exact height ties). | For [0, 4, 2, 3, 1, 5, 0], maxima at indices 1, 3, and 5 have prominences 3, 1, and 5. |

The three original operations and their stored result/receipt formats are unchanged. The five new operations all require `sample_times_s` paired one-to-one with `samples` and reject non-increasing or non-uniform spacing (relative tolerance 10⁻⁸), unequal vector lengths, insufficient samples, invalid cutoffs/windows and numerically unstable filter settings. The shared workbench limit of 1024 points and finite binary64 values applies. The original sample-rate-only amplitude spectrum still assumes uniform sampling because it has no time coordinate input.

**FFT normalization.** For real samples `x[n]`, a periodic window `w[n]` and unnormalized DFT `X[k] = FFT(x·w)[k]`, set `d[k] = 1` at DC and even-length Nyquist, and 2 at the other one-sided bins. The reported amplitude is `d[k]|X[k]| / Σw`; bin power is `d[k]|X[k]|² / (N Σw²)` and PSD is bin power divided by `Fs/N`. Thus amplitude compensates **coherent gain** (`Σw/N`), whereas power compensates **window energy**, not the square of coherent gain. Parseval gives `Σ bin_power = Σ(x·w)² / Σw²` exactly up to floating-point error; with the rectangular window this is the ordinary mean-square amplitude. A tone off a bin center exhibits leakage and may not reach its true peak amplitude in any single bin.

**Welch normalization.** Each constant-detrended segment follows the same energy-normalized one-sided density convention, divided by `Fs Σw²`; averaged PSD multiplied by its bin spacing integrates to the average window-weighted power of those centered segments. Equivalent noise bandwidth is `Fs Σw² / (Σw)²` (e.g. Hann: 1.5 FFT bins). The periodogram’s `scaling="spectrum"` convention differs from density by this ENBW factor. With a rectangular window and no overlap, Welch reduces to Bartlett averaging.

**Butterworth interpretation.** The analog frequency is prewarped using `Ω = 2Fs tan(πf/Fs)` before the bilinear transform. `order` denotes the *single-pass lowpass prototype*, not the final zero-phase order: a bandpass has twice as many poles before the forward-backward pass. A single-pass Butterworth response is −3.0103 dB at each specified cutoff; forward-backward filtering squares its magnitude, making the **filtered output** approximately −6.0206 dB there. Reflection padding and steady-state initial conditions reduce but cannot eliminate edge effects. Too-short records are rejected with the minimum sample requirement; poles too close to the unit circle or overly high prototype orders are rejected rather than producing unstable output.

References: [MathWorks single-sided amplitude convention](https://www.mathworks.com/help/matlab/ref/fft.html), [SciPy periodic window definitions](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.get_window.html), [SciPy spectral scaling](https://docs.scipy.org/doc/scipy/tutorial/signal.html), [Welch PSD and ENBW](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html), [Butterworth cutoff convention](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.butter.html), [forward-backward filter and edge handling](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html), [Savitzky–Golay edge interpolation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.savgol_filter.html), and [peak height, prominence and distance](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.find_peaks.html).
