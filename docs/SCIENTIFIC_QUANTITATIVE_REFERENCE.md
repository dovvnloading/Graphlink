# Quantitative plugin reference

Each operation accepts finite numbers in the units shown in its form. Vector inputs represent ordered observations. Calculations use the supplied measurements directly. The examples below give reference values for checking a result by hand.

## Mathematics

| Operation | Input contract and method | Reference result |
| --- | --- | --- |
| Evaluate a polynomial (`polynomial`) | Supply 1–128 real coefficients in ascending power order and 1–256 sample coordinates. Horner’s method evaluates each point. The table preserves input order and the plot sorts by coordinate. | Coefficients `[2, -1, 3]` at `[2, -1, 0]` produce `[12, 6, 2]`. |
| Integrate sampled data (`trapezoid`) | Supply equally long x and y vectors, with at least two strictly increasing x values. Sum the signed trapezoid areas on each interval. The result has units of x times units of y. | x = `[0, 1, 3]` and y = `[-2, 0, 4]` give an integral of 3. |
| Solve a linear system (`linear_system`) | Supply a square real matrix A of order 1–32 and a matching right-hand side b. Each equation is scaled by its largest coefficient before Gaussian elimination with partial pivoting. A pivot at or below n times machine epsilon in this scaled system causes a validation error. | `[[2, 1], [1, -1]]` with b = `[5, 1]` gives x = `[2, 1]` and a maximum absolute residual of zero. |

## Statistics

| Operation | Input contract and method | Reference result |
| --- | --- | --- |
| Describe a sample (`describe`) | Supply at least two observations. The output includes count, mean, median, minimum, maximum and sample standard deviation with denominator n − 1. | `[1, 2, 3]` has mean 2, median 2 and sample standard deviation 1. |
| Fit a regression line (`linear_regression`) | Supply at least two paired observations with distinct predictor values. Ordinary least squares fits an intercept and slope. The plot spans the observed predictor range. For a constant response, the slope is zero and correlation and R² are undefined. | x = `[1, 2, 3]` and y = `[5, 3, 1]` give slope −2, intercept 7, Pearson r = −1 and R² = 1. |
| Summarize weighted observations (`weighted_summary`) | Supply paired observations and nonnegative relative weights, including at least one positive weight. The population variance uses the weighted sum of squared deviations divided by the total weight. Zero-weight observations contribute nothing. | Observations `[3, 5, 90]` with weights `[1, 1, 0]` give a weighted mean of 4 and population standard deviation of 1. |

## Physics

| Operation | Input contract and method | Reference result |
| --- | --- | --- |
| Model a spring oscillator (`spring_oscillator`) | Positive mass m (kg), positive stiffness k (N/m), and nonnegative displacement amplitude A (m). An undamped linear spring released from rest has period T = 2π√(m/k). The plot samples displacement A cos(2πt/T) at 65 points over one period. | m = 1 kg, k = 4 N/m, A = 0.1 m give T = π s and displacement −0.1 m at T/2. |
| Calculate heat flow through layers (`thermal_layers`) | Supply matching positive layer thicknesses (m) and conductivities (W/(m·K)), a common positive area (m²), and boundary temperatures (K). Sum each layer’s L/(kA) to obtain steady resistance. Positive heat flow travels from left to right. The model assumes perfect thermal contact. | For layers 0.1 m and 0.02 m thick, conductivities 0.2 and 0.04 W/(m·K), and area 2 m², total resistance is 0.5 K/W. Faces at 295 K and 275 K give 40 W. |
| Calculate sensible heat (`sensible_heat`) | With positive mass m (kg) and constant specific heat c (J/(kg·K)), heat transferred to a sample is mc(Tfinal − Tinitial). Temperatures are in kelvin. The model assumes a single phase throughout the interval. | A 2 kg sample with c = 400 J/(kg·K) cooling from 100 K to 90 K releases 8,000 J. The reported heat transfer is −8,000 J. |

## Astronomy

| Operation | Input contract and method | Reference result |
| --- | --- | --- |
| Estimate distance from parallax (`parallax_distance`) | A precise, positive annual parallax p in milliarcseconds gives distance 1,000/p parsecs. The operation also reports light-years. For noisy measurements, inverse parallax alone can be a poor distance estimate. | A parallax of 100 mas gives 10 pc, or approximately 32.6156 ly. |
| Calculate a photometric distance (`distance_modulus`) | Supply apparent magnitude m, absolute magnitude M and nonnegative extinction A in a common passband. The true modulus μ = m − M − A gives distance 10^(1 + μ/5) parsecs. | m = 8 mag, M = 1 mag and A = 2 mag give μ = 5 mag and distance 100 pc. |
| Find a blackbody spectral peak (`blackbody_peak`) | A positive temperature T (K) gives the peak of spectral radiance per unit wavelength by λmax = b/T. The operation uses b = 2.897771955 × 10⁻³ m·K and reports nanometres. | A 3,000 K blackbody peaks at approximately 965.924 nm. |

The parallax convention and Wien displacement constant are documented by ESA and NIST. [ESA stellar parallax](https://www.esa.int/Science_Exploration/Space_Science/Gaia/Parallax) · [NIST Wien wavelength displacement constant](https://physics.nist.gov/cgi-bin/cuu/Value?bwien)

## Engineering

| Operation | Input contract and method | Reference result |
| --- | --- | --- |
| Combine ideal resistors (`resistor_network`) | Supply at least one nonnegative resistance in ohms. The operation reports the sum for a series circuit and the reciprocal sum of conductances for a parallel circuit. A zero-ohm branch shorts a parallel circuit. | 10 Ω and 30 Ω give 40 Ω in series and 7.5 Ω in parallel. |
| Plot an RC step response (`rc_step`) | Positive resistance R (Ω), positive capacitance C (F), voltage step V (V), and increasing sample times t ≥ 0 (s). An initially uncharged capacitor has voltage V(1 − exp(−t/RC)). The table and plot use the supplied times. | R = 1,000 Ω, C = 0.001 F and V = 5 V give a time constant of 1 s and a capacitor voltage of approximately 3.16060 V at 1 s. |
| Calculate thermal expansion (`linear_expansion`) | Positive initial length L (m), constant linear expansion coefficient α (1/K), and uniform signed temperature change ΔT (K). Unconstrained small-strain expansion gives ΔL = LαΔT. The final length must remain positive. | A 2 m bar with α = 0.000023/K warming by 40 K extends 0.00184 m to 2.00184 m. |
