# Independent Scientific Workbench correctness audit

**Scope:** the seven `graphlink_plugins/scientific/packs` modules physics, biology, astronomy, chemistry, engineering, linalg, and inferential, as delivered on `main` before this audit. This is a correctness review, **not a scientific validation certificate** or an implementation of additional models. The checks compare the implemented equations, sign/units conventions, stated numerical precision, finite-input validation, returned scalar/table/plot units and documented applicability with the primary sources linked in each pack reference below. Existing reference test suites additionally exercise published numeric examples.

## Coverage and source concordance

Each operation below was reviewed for its defining equation, dimensional consistency, parameter domain, near-zero/large-range behavior, small samples and ties where relevant, and its documentation contract. **✓** means no independently reproducible defect identified in this audit (it does not assert perfect numerical accuracy for all inputs). **F1–F10** identify reproduced defects and accompanying changes and tests; entries without an F-number are unchanged.

### Physics — 13 operations

Source concordance: [Physics/Engineering SI and published oracles](SCIENTIFIC_QUANTITATIVE_REFERENCE.md), OpenStax *University Physics* Vols. 1–3, NIST exact SI constants/CODATA.

| Operation | Formula and output units audited | Finding |
| --- | --- | --- |
| `spring_oscillator` | Hooke oscillator `ω=√(k/m)`; period s, frequency Hz, position m | ✓ |
| `thermal_layers` | Series `Σ L/(kA)`; thermal resistance K/W, heat rate W and flux W/m² | ✓ |
| `sensible_heat` | `mcΔT`; J, heat capacity J/K, ΔT K | ✓ |
| `damped_driven_oscillator` | Forced damped second-order ODE, transient m/m/s/m/s² and resonance Hz | ✓ |
| `projectile_motion` | Newtonian ballistic trajectory/quadratic drag, m/s, m, s | ✓ |
| `rigid_body_inertia` | Mass moments `Σmr²`, energy/rotation units kg·m²/J/rad | ✓ |
| `transient_heat_1d` | Diffusion finite differences and Fourier CFL; K, m, s, dimensionless Fo | ✓ |
| `doppler_shift` | Medium-wave and relativistic longitudinal shifts, Hz and wavelength m | ✓ |
| `relativistic_kinematics` | Lorentz γ, momentum kg·m/s, energy J, intervals s | ✓ |
| `geometric_optics` | Thin-lens/mirror `1/f=1/do+1/di`, Snell; image m, magnification 1, angles deg | **F1** |
| `slit_diffraction` | Fraunhofer sinc²/cos², W/m² and screen m, diffraction angle deg | ✓ |
| `series_rlc_response` | Transient series RLC and `|V/Z|`; V, A, J, Hz | ✓ |
| `series_rlc_frequency` | `Z=R+j(ωL−1/ωC)`; Ω, rad, A | ✓ |

### Biology — 11 operations

Sources: [Biology reference and numeric oracles](SCIENTIFIC_BIOLOGY_REFERENCE.md), Hurlbert (1971), Chao (1984), original Michaelis–Menten/logistic/Gompertz/HWE/KM/logrank equations and standard NLS covariance.

| Operation | Formula and output units audited | Finding |
| --- | --- | --- |
| `community_diversity` | Shannon entropy nats, Gini–Simpson 1, count individuals | ✓ |
| `quadrat_density` | `Σcounts/Σarea`; individuals/m² | ✓ |
| `community_overlap` | Jaccard/Sørensen overlap; proportions 1 | ✓ |
| `logistic_growth_fit` | Logistic K and intrinsic rate 1/time; SE and time units | ✓ |
| `gompertz_growth_fit` | Gompertz K, decay of specific growth rate 1/time | ✓ |
| `michaelis_menten_fit` | `Vmax·S/(Km+S)`; response rate and concentration units | ✓ |
| `dose_response_4pl_fit` | Four-parameter sigmoid; potency concentration and Hill slope 1 | ✓ |
| `lotka_volterra` | Coupled predator/prey ODE; individuals, selected time, 1/time rates | ✓ |
| `hardy_weinberg` | `p²,2pq,q²`; individuals, χ²/df/p dimensionless | ✓ |
| `kaplan_meier_logrank` | Product-limit/Greenwood/Mantel–Cox; probability 1, time unit | ✓ |
| `richness_rarefaction` | `Σ(1−C(N−ni,m)/C(N,m))`; expected taxa | **F2** |

### Astronomy — 12 operations

Sources: [Astronomy reference](SCIENTIFIC_ASTRONOMY_REFERENCE.md), IAU constants, Meeus, Kasten–Young, Hogg/Wright cosmological distances, Seager & Mallén-Ornelas transit geometry.

| Operation | Formula and output units audited | Finding |
| --- | --- | --- |
| `parallax_distance` | Inverse annual parallax; pc and Julian light-years | ✓ |
| `distance_modulus` | `m−M−A=5log10(d/10pc)`; mag/pc | ✓ |
| `blackbody_peak` | Wien displacement; nm from K | ✓ |
| `keplerian_orbit` | Elliptic Kepler solve; AU, AU/day, deg and days | ✓ |
| `julian_sidereal_time` | Proleptic UTC Julian date / mean sidereal time; JD/h | ✓ |
| `coordinate_frames` | IAU galactic and mean-of-date equatorial rotations; deg/h/JD | ✓ |
| `airmass_extinction` | Kasten–Young airmass and Bouguer extinction; 1/mag | ✓ |
| `flat_lcdm_distances` | Flat FLRW integral; Mpc and Gyr | ✓ |
| `ab_flux_to_magnitude` | `−2.5log10(fν/3631Jy)`; mag AB | ✓ |
| `ab_magnitude_to_flux` | `3631·10^(−0.4mag)`; Jy | ✓ |
| `ab_colour_index` | AB colour / spectral flux ratio; mag/1 | ✓ |
| `exoplanet_transit` | Uniform-disk depth/contacts; planet radius Earth radii, depth 1/ppm, duration h | **F3** |

### Chemistry — 13 operations

Sources: [Chemistry reference and published examples](SCIENTIFIC_NATURAL_REFERENCE.md), OpenStax *Chemistry 2e* chapters 4, 9, 12 and 14; IUPAC and CIAAW abridged atomic weights; CODATA.

| Operation | Formula and output units audited | Finding |
| --- | --- | --- |
| `beer_lambert` | `A=εcl`; mol/L | ✓ |
| `transmission_spectrum` | `−log10(T/100)`; absorbance 1 vs nm | ✓ |
| `isotope_average` | Number-abundance mean; u and % | ✓ |
| `acid_base_speciation` | Polyprotic mass/charge balance and `Kw`; pH/1, mol/L | ✓ |
| `buffer_ph_capacity` | Henderson–Hasselbalch/exact charge, mol/(L·pH) | ✓ |
| `titration_curve` | Stoichiometric equivalents with charge balance; mL and pH | ✓ |
| `arrhenius_fit` | `ln k` vs `1000/T`; activation energy kJ/mol and rate units | ✓ |
| `rate_law_fit` | First/second-order integrated laws; s⁻¹ or L/(mol·s), mol/L | ✓ |
| `gas_pressure` | Ideal and van der Waals EOS; bar and compressibility 1 | ✓ |
| `gas_volume` | Largest admissible van der Waals volume root; L | ✓ |
| `molar_mass` | Element stoichiometry/CIAAW; g/mol | ✓ |
| `solution_dilution` | `C1V1=C2V2`; mol/L, mmol, mL | ✓ |
| `reaction_yield` | Limiting extent, theoretical mass g and actual/theoretical % | **F4** |

### Engineering — 9 operations

Sources: [Engineering reference](SCIENTIFIC_QUANTITATIVE_REFERENCE.md), NCEES FE Reference Handbook, USDA pipe-flow reference, ASHRAE heat-exchanger methods, Illinois ECE486 control response reference.

| Operation | Formula and output units audited | Finding |
| --- | --- | --- |
| `resistor_network` | Series sum/parallel reciprocal; Ω | ✓ |
| `rc_step` | `V(1−exp(−t/RC))`; V, s | ✓ |
| `linear_expansion` | `ΔL=αLΔT`; m and strain 1 | ✓ |
| `beam_response` | Euler–Bernoulli point+uniform loads, deflection m, shear N, moment N·m | ✓ |
| `combined_stress` | Axial/bending/shear and plane-stress von Mises; Pa, utilization 1 | ✓ |
| `pipe_flow` | Darcy–Weisbach and Colebrook; Re/f 1, head m, pressure Pa | ✓ |
| `heat_exchanger_sizing` | LMTD and ε–NTU; area m², heat W, differences K | ✓ |
| `transfer_function_response` | First/second-order step, Bode, settling; response 1, time s, phase deg | **F5** |
| `pid_closed_loop` | First-order plant PID closed loop; gain 1, time s, output 1 | ✓ |

### Linear algebra — 8 operations

Sources: [Linear Algebra reference and NIST/Higham examples](SCIENTIFIC_LINALG_REFERENCE.md), Golub & Van Loan, *Matrix Computations*; Higham, *Accuracy and Stability*; NIST StRD.

| Operation | Formula and output units audited | Finding |
| --- | --- | --- |
| `qr_decomposition` | Pivoted Householder `A[:,p]=QR`; matrix input units and residual 1 | ✓ |
| `cholesky_decomposition` | `A=LLᵀ`; L in sqrt(input units), relative error 1 | ✓ |
| `least_squares_qr` | Pivoted-scaled QR `min||Ax−b||₂`; response-unit residual and squared RSS | **F6** |
| `symmetric_eigen` | `AV=VΛ`; eigenvalues in input units, relative residual 1 | ✓ |
| `svd` | `A=UΣVᵀ`; singular values input units, residual 1 | ✓ |
| `matrix_inverse` | Pivoted LU inverse; reciprocal input units, residual 1 | ✓ |
| `matrix_determinant` | Signed LU product; input units to power n, log magnitude | ✓ |
| `pca` | Centered/standardized SVD; means/feature SD input units, scores/variance | **F7** |

### Inferential statistics — 17 operations

Sources: [Inference/fit reference](SCIENTIFIC_FITTING_REFERENCE.md), NIST/SEMATECH e-Handbook, Fisher (1935), Shapiro–Wilk/Royston, Tukey–Kramer, Benjamini–Hochberg, and [SciPy statistical reference](https://docs.scipy.org/doc/scipy/reference/stats.html) for externally stated numeric conventions. Significance levels and probabilities are dimensionless; generic measured x/y/response fields retain their user-provided units.

| Operation | Formula and output units audited | Finding |
| --- | --- | --- |
| `two_way_anova` | Full-factorial Type-II sums of squares; response², F/p 1 | ✓ |
| `kruskal_wallis` | Tie-adjusted H and Dunn/Holm tests; rank/1 | ✓ |
| `chi_square_independence` | Pearson χ², Cramér V; count, statistic/p 1 | ✓ |
| `fisher_exact` | Fixed-margin hypergeometric p, unconditional sample odds ratio; 1 | **F10** |
| `multiple_linear_regression` | OLS/Wald/VIF/leverage/Cook; coefficients response/x units | ✓ |
| `binary_logistic_regression` | Binomial MLE by IRLS, odds and confidence intervals; 1 | ✓ |
| `bootstrap_confidence_interval` | Percentile/BCa iid bootstrap; statistic in sample units | ✓ |
| `t_test_power` | Noncentral-t power, target sample size; probability/individuals | ✓ |
| `proportion_power` | Normal proportion-test power; 1 and sample size | ✓ |
| `one_way_anova` | Pooled F and Tukey–Kramer; response², F/p 1 | ✓ |
| `mann_whitney_u` | Exact conditional ranks/tie-aware normal; rank sum/p 1 | ✓ |
| `wilcoxon_signed_rank` | Exact sign permutations/tie-aware normal; rank sum/p 1 | ✓ |
| `shapiro_wilk` | Royston W, normality p; 1 | **F9** |
| `pearson_correlation` | Product moment r and Fisher-z CI; 1 | **F8** |
| `spearman_correlation` | Conditional rank permutation and approximate CI; 1 | **F8** (shared numeric helper) |
| `bonferroni_correction` | Multiply and clip p by number of hypotheses; 1 | ✓ |
| `benjamini_hochberg_correction` | Reverse cumulative p adjustment and FDR; 1 | ✓ |

## Confirmed findings and regression coverage

| ID | Before | After | Regression |
| --- | --- | --- | --- |
| F1 | Finite paraxial object distances within relative 1e-14 of focus called “infinite” | Only exact focal equality singular; finite large image/magnification elsewhere | Near-adjacent binary64 distances on both lens and mirror |
| F2 | Differences of enormous log-gamma terms erased low rare-taxon probabilities | Stable product/limited-curvature evaluation | N=2**53−1; rare taxon 3000/large background |
| F3 | Grazing transit used `k=√depth` despite partial occultation | Reject invalid depth-to-radius inversion with physical error | Grazing rejection; central/full transit unaffected |
| F4 | Zero theoretical yield rejected despite legal zero input masses | Return 0 g and undefined percentage for zero/zero; reject positive actual with zero theoretical | Both boundaries and explanatory error |
| F5 | Early-time second-order step canceled `~5e−19` to zero | Bounded O(u⁴) short-time ODE series | ζ=.2,1,2; u=1e−9 |
| F6 | Exact large-response fit raised overflow computing response-scale² × zero | Square achieved residual norm; check finite representability | A=[[1],[0]], b=[1e200,0] |
| F7 | Centered PCA reported unit scale factors [1,…] as measured feature SD | Report actual sample SD, in original feature units | [1,2,3,4] and doubled feature |
| F8 | Correlation data with opposite-sign `~1e308` values failed on subtraction overflow | Normalize magnitudes before differences, keep dimensionless correlations | Near-maximum finite perfect Pearson data |
| F9 | Shapiro–Wilk failed on the same finite-span overflow | Normalize before differencing; same W/p under large scaling | Scale-invariance regression |
| F10 | Large-margin Fisher gamma-log subtraction inflated `~1e−15` p-values | Exact-count hypergeometric weight recurrence from mode | 10¹⁵ margin case and Fisher tea-tasting 34/70 |

## Verification and limits

New defect regressions live in `backend/tests/test_scientific_correctness_audit.py`, with the original astronomy grazing expectation updated in `test_scientific_astronomy.py`. For existing normal-domain, rank-deficient and published benchmark coverage see `test_scientific_astronomy.py`, `test_scientific_biology.py`, `test_scientific_chemistry.py`, `test_scientific_engineering.py`, `test_scientific_inference.py`, `test_scientific_linalg.py`, and the other pack tests. All changes are confined to these seven pack modules, reference documentation and backend tests. `core.py`, `workbench.py` and the web UI are unchanged.

Floating-point claims are **binary64** numeric fidelity, not infinite precision. Inverse problems, nonlinear fitting, cosmological extrapolation and large-sample asymptotic inference retain the documented scientific-domain restrictions; absence of a new finding does not warrant extrapolating a model outside those restrictions.
