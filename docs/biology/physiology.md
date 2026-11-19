# Physiology — Biology Workbench reference

**Pack ID:** physiology · **Plugin ID:** science_physiology · **Family:** Biology Workbench · **Version:** 1.2.0

Enable **Physiology** in **Settings → Plugins**, granting this pack graph.read and graph.mutate, then open **Plugins → Biology Workbench** and select an operation. The pack is independent of historical science_biology: existing operation IDs, grants, and saved receipts remain unchanged. Calculations are deterministic, local, and standard-library-only; they return verified Workbench receipts, metrics and optional tables/plots. Dimensionally compatible measurement suffixes are accepted.

These are **educational/experimental models**, not clinical diagnosis, treatment, acquisition, or stimulation guidance.

## Operation inventory

| Operation ID | Inputs | Outputs |
| --- | --- | --- |
| **nernst_potential** | Inside/outside concentration (mM), integer valence, temperature (K) | Ion reversal potential (mV), log ratio |
| **ghk_potential** | Inside/outside K+, Na+, Cl− (mM), relative permeabilities, temperature (K) | GHK potential (mV), per-ion contributions |
| **membrane_properties** | Area (cm²), specific C (µF/cm²), specific R (Ω·cm²), resting voltage (mV), current (nA), time (ms) | Total C (nF), total R (MΩ), tau (ms), passive step voltage trace |
| **hodgkin_huxley** | Duration, step and pulse timing (ms), current density (µA/cm²), initial voltage (mV) | Voltage/gate simulation, sodium/potassium currents, spike count |
| **spike_detection** | Sample times (ms), voltages (mV), threshold (mV), refractory interval (ms) | Interpolated crossing times, sampled peaks, average rate |
| **fick_diffusion** | Diffusivity (m²/s), layer thickness (m), area (m²), left/right concentrations (mol/m³), time (s) | Flux (mol/m²/s), rate (mol/s), net transfer (mol), linear concentration profile |

No edits to the pinned pack catalog, picker or core result schema are needed.

## Nernst equilibrium potential

**Operation:** nernst_potential

For valence z, absolute temperature T and positive concentrations in identical units:

\[
E_{\rm ion}=\frac{RT}{zF}\ln\frac{[{\rm ion}]_{\rm outside}}{[{\rm ion}]_{\rm inside}}.
\]

R = 8.31446261815324 J/(mol·K) and F = 96485.33212 C/mol. Output is multiplied by 1000 to report mV. The sign convention is **inside minus outside**. Positive cations more concentrated inside give negative potentials; reversing the valence reverses the sign. The valence must be a nonzero integer from −3 through +3, concentrations must be strictly positive, and T must be above 0 K.

**Numerical oracle:** 140 mM intracellular K+, 5 mM extracellular K+, z=+1 and 310.15 K (37 °C) give **−89.05869404 mV**. Equal concentrations yield 0 mV and z=+2 halves the voltage. Inputs of 0.14 M and 5000 µM convert to the same reference concentrations.

This is a single-ion equilibrium potential, **not** a resting voltage unless that ion dominates permeability. Ideal ionic activities are approximated by concentrations; pumps, chemical activity coefficients and coupled transport are not modeled.

## Goldman–Hodgkin–Katz potential

**Operation:** ghk_potential

For constant-field passive fluxes of monovalent K+, Na+ and Cl−, with relative permeabilities P:

\[
V_m=\frac{RT}{F}\ln
\left(\frac{P_K[K]_o+P_{Na}[Na]_o+P_{Cl}[Cl]_i}
{P_K[K]_i+P_{Na}[Na]_i+P_{Cl}[Cl]_o}\right).
\]

**Cl− is reversed** in the numerator/denominator because it is an anion; voltage remains intracellular minus extracellular in mV. Concentrations and permeabilities may be zero but cannot be negative. At least one permeability and both complete weighted sums must be positive. Scaling all permeabilities equally does not affect the result.

**Numerical oracle:** 310.15 K, K=140/5 mM (inside/outside), Na=12/145 mM, Cl=10/110 mM, and P_K : P_Na : P_Cl = 1 : 0.04 : 0.45 gives numerator 15.3 mM, denominator 189.98 mM and **−67.32621760 mV**. If only one permeability remains, GHK reduces to the corresponding Nernst potential (including the anion sign).

The model assumes a uniform electric field, independent passive monovalent fluxes and constant concentrations. It excludes ion pumps, impermeant ions, cotransport, rectifying channels and concentration feedback.

## Passive membrane properties and RC step

**Operation:** membrane_properties

For a single isopotential compartment with membrane area A (cm²), specific capacitance c_m (µF/cm²) and specific resistance r_m (Ω·cm²):

\[
C=c_m A,\qquad R=\frac{r_m}{A},\qquad\tau=RC,\qquad
V(t)=V_{\rm rest}+IR[1-\exp(-t/\tau)].
\]

Total capacitance is reported in nF, input resistance in MΩ, tau in ms, and voltage in mV. With these units, R(MΩ)·C(nF) = tau(ms) and I(nA)·R(MΩ) = ΔV(mV). The current begins at t=0 and continues throughout the sampled window. Times must start at zero and increase strictly with at least two samples.

**Numerical oracle:** A=0.01 cm², c_m=1 µF/cm² and r_m=10000 Ω·cm² give R=1 MΩ, C=10 nF and tau=10 ms. From rest −65 mV with a +0.1 nA constant step, steady-state change is +0.1 mV; at 10 ms, voltage is −64.93678794 mV, and at 40 ms **−64.90183156 mV**. Doubling area halves R, doubles C, and preserves tau.

This is a linear lumped RC model, not a spatial cable or voltage-dependent conductance model. The exponential response uses a stable expm1 evaluation for small times.

## Hodgkin–Huxley action-potential simulation

**Operation:** hodgkin_huxley

Integrates the space-clamped **1952 squid giant axon model** at the historical **6.3 °C** reference. Variables V (mV) and gating fractions m, h, n evolve as:

\[
C_m\frac{dV}{dt}=I_{\rm applied}
-\bar g_{Na}m^3h(V-E_{Na})
-\bar g_Kn^4(V-E_K)-g_L(V-E_L),
\]
\[
\frac{dx}{dt}=\alpha_x(V)(1-x)-\beta_x(V)x,\quad x\in\{m,h,n\}.
\]

Fixed membrane values: C_m=1 µF/cm², g_Na=120, g_K=36, g_L=0.3 mS/cm², E_Na=+50, E_K=−77, E_L=−54.387 mV. These rate functions use the modern voltage axis (rest ≈−65 mV). At V=−40 mV, alpha_m=1 ms⁻¹; at V=−55 mV, alpha_n=0.1 ms⁻¹. Their removable singularities are evaluated analytically, avoiding zero-over-zero. Gate initial conditions are alpha/(alpha+beta) at the selected starting voltage.

The integrator uses **classical fourth-order Runge–Kutta (RK4)** with a uniform step at most the requested size. Current density is a square pulse (µA/cm²) sampled at each step midpoint, not a whole-cell current. The full integration grid determines extrema and zero-mV upward crossings; the output table/plot are **decimated** to at most 500 rows (eight columns: time, voltage, m, h, n, stimulus, Na and K currents), fitting the Workbench limits. The solver rejects trajectories leaving the supported voltage or gate ranges.

**Bounds:** 1–200 ms duration; requested step 0.005–0.05 ms; at most 20,000 solver steps; pulse start ≥0, pulse stop after start and no later than duration, pulse at least one actual step long; injected current −100…+100 µA/cm²; initial voltage −100…+80 mV. Reduce step size for convergence checking. No thermal correction, spatial propagation, stochastic channel gating, synaptic inputs or disease-specific interpretation is attempted.

**Numerical regression oracle:** Initial −65 mV, dt 0.01 ms, duration 50 ms, +10 µA/cm² applied from t=5 to 35 ms: peak **+40.26307 mV around 7.14 ms**, with two upward 0-mV crossings. Without stimulation, the model remains near rest. This is a model regression, not physiological validation against measurements.

## Spike detection from a voltage trace

**Operation:** spike_detection

Input time and voltage vectors must be aligned, contain at least three samples and have strictly increasing times. A candidate is detected on an **upward crossing** from voltage strictly below threshold to voltage at or above it; the crossing time is interpolated linearly between the two samples. The peak is the maximum measured voltage before the trace next falls below threshold. A crossing is rejected when its interpolated onset occurs less than the refractory interval after the most recent **accepted** onset. If the record starts above threshold, the initial high sample does not count as an event.

Outputs include onset and sampled-peak times/voltages, window-average rate = count / full recording duration in seconds, and mean interspike interval. Fewer than two events yield an **undefined** mean interval. No events yield count 0, rate 0 and an empty table. A coarsely sampled trace can miss narrow spikes; this simple algorithm does not remove artifacts or infer electrophysiological identity.

**Numerical oracle:** For samples at 0…10 ms and voltages [−65, −30, +30, −50, −65, −25, +35, −45, −65, −60, −65] mV, threshold 0 mV and refractory 2 ms, accepted onsets are 1.5 and 5+25/60 ms, peaks at 2 and 6 ms, and the window-average rate is 200 Hz. Raising the refractory interval to 4 ms retains the first event only.

A HH result's plotted time/voltage axes can be bound to the detector using the workbench's normal graph-edge grant and receipt checks.

## Fick steady diffusion

**Operation:** fick_diffusion

Steady, one-dimensional, constant-D diffusion through a planar layer of thickness L:

\[
\frac{dC}{dx}=\frac{C_R-C_L}{L},\quad
J=-D\frac{dC}{dx},\quad
\dot n=JA,\quad
n_{\rm net}=JAt.
\]

Positive flux is **left to right**, reported as mol/(m²·s); rate is mol/s and signed transferred amount is mol. D, L, A and duration t must be positive, and boundary concentrations nonnegative. Equal boundaries give zero net flux and reversing them reverses the direction. Nine evenly spaced concentration points show the assumed **linear** profile.

**Numerical oracle:** D=10⁻⁹ m²/s, L=10⁻³ m, A=2×10⁻⁴ m², left concentration 3 and right 1 mol/m³, duration 100 s: gradient **−2000 mol/m⁴**, flux **+2×10⁻⁶ mol/(m²·s)**, rate **+4×10⁻¹⁰ mol/s**, net transfer **+4×10⁻⁸ mol**.

The boundary concentrations remain fixed: no reaction, convection, partitioning, transient depletion or numerical solution of Fick's **second** law.

## Test and literature references

Dedicated automated regression suite: **backend/tests/test_scientific_physiology.py**. It checks all six example operations, manifest/biology discovery, saved-result receipts, unit conversions, independent oracles, HH bounds, spike-event edge cases and invalid domains.

Run the focused checks with **python -m pytest -q -n0 backend/tests/test_scientific_physiology.py**; the repository-wide checks are **python -m pytest -q** and **python -m ruff check .**.

- [NIST CODATA physical constants](https://physics.nist.gov/cuu/Constants/) — R and Faraday constant.
- [Hodgkin and Huxley (1952), A quantitative description of membrane current](https://pmc.ncbi.nlm.nih.gov/articles/PMC1392413/) — gating dynamics and parameterization.
- [Goldman (1943), Potential, Impedance, and Rectification in Membranes](https://rupress.org/jgp/article/27/1/37/12329/Potential-Impedance-and-Rectification-in) — constant-field formulation.
- [Fick's laws of diffusion](https://en.wikipedia.org/wiki/Fick%27s_laws_of_diffusion) — steady-state first-law sign convention.

Numerical correctness tests and mathematical references do not constitute experimental or clinical validation.
