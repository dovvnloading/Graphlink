"""Closed-cohort and structured-population operations for Biology and Ecology.

The implementation uses only the standard library and the Workbench result
contract. These operations are appended to the historical Biology catalog.
"""

from __future__ import annotations

import math

from graphlink_plugins.scientific.core import Citation, Field, Operation


def _unit_label(value, name):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 40
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{name} must be a unit label of 1–40 characters.")
    return value.strip()


def mark_recapture(data):
    """Two-sample, closed-population Petersen and bias-corrected Chapman."""
    marked = data["marked_first"]
    second = data["captured_second"]
    recaptured = data["recaptured_marked"]
    if marked <= 0 or second <= 0 or not 0 <= recaptured <= min(marked, second):
        raise ValueError("Mark–recapture requires two positive catches and recaptures "
                         "between zero and the smaller catch.")
    observed = marked + second - recaptured
    if observed > 2**53 - 1:
        raise ValueError("Observed distinct individuals exceed the exact count range.")

    chapman = (marked + 1) * (second + 1) / (recaptured + 1) - 1
    variance = ((marked + 1) * (second + 1) * (marked - recaptured)
                * (second - recaptured) / ((recaptured + 1)**2 * (recaptured + 2)))
    se = math.sqrt(variance)
    petersen = (marked * second / recaptured if recaptured
                else "undefined (no recaptures)")
    if recaptured >= 5:
        lower = max(observed, chapman - 1.96 * se)
        upper = chapman + 1.96 * se
        interval_note = "Normal-approximation 95% interval (lower bound clipped to distinct animals observed)."
    else:
        lower = upper = "undefined (fewer than 5 recaptures)"
        interval_note = "Too few recaptures for a defensible normal-approximation interval."
    warning = ("Sparse recaptures; the point estimate is particularly uncertain."
               if recaptured < 5 else "Normal-approximation intervals may have poor small-sample coverage.")

    return {
        "summary": f"Two-capture Chapman estimate: {chapman:.6g} individuals "
                   f"({observed} distinct observed); Lincoln–Petersen is "
                   f"{petersen if isinstance(petersen, str) else f'{petersen:.6g}'}. "
                   f"{interval_note} Assumes a closed, mixed population, equal capture "
                   f"probability, no tag loss and no tag-induced response. {warning}",
        "values": {
            "marked_first": marked, "captured_second": second,
            "recaptured_marked": recaptured, "distinct_observed": observed,
            "sample_count_unit": "individuals",
            "lincoln_petersen_estimate": petersen,
            "chapman_estimate": chapman, "population_estimate_unit": "individuals",
            "chapman_variance": variance, "chapman_variance_unit": "individuals²",
            "chapman_standard_error": se, "chapman_standard_error_unit": "individuals",
            "chapman_ci95_lower": lower, "chapman_ci95_upper": upper,
            "chapman_ci95_unit": "individuals",
            "second_capture_recap_fraction": recaptured / second,
            "recapture_fraction_unit": "1 (dimensionless)",
            "caveat": warning,
        },
        "table": {
            "columns": ["Estimator (categorical)", "Population (individuals)"],
            "rows": [["Distinct individuals observed", observed],
                     ["Chapman corrected", chapman],
                     ["Lincoln–Petersen", petersen]],
        },
        "plot": {
            "title": "Observed minimum and Chapman population estimate",
            "xLabel": "Estimate code (0=observed minimum, 1=Chapman)",
            "yLabel": "Population (individuals)",
            "x": [0, 1], "y": [observed, chapman],
        },
    }


def cohort_life_table(data):
    """Closed-cohort life table with trapezoidal person-time by age interval."""
    ages, counts, fecundity = data["ages"], data["survivors"], data["fecundity"]
    unit = _unit_label(data["time_unit"], "Age")
    if not len(ages) == len(counts) == len(fecundity) or len(ages) < 2:
        raise ValueError("Life tables require matching age, survivor and fecundity vectors "
                         "with at least two age boundaries.")
    if ages[0] != 0 or any(a < 0 or b <= a for a, b in zip(ages, ages[1:])):
        raise ValueError("Life-table ages must start at zero and increase strictly.")
    if (any(n < 0 or int(n) != n or n > 2**53 - 1 for n in counts)
            or counts[0] == 0 or counts[-1] != 0
            or any(next_n > n for n, next_n in zip(counts, counts[1:]))):
        raise ValueError("A closed cohort needs positive initial survivors, nonincreasing "
                         "whole-number counts and zero survivors at the final boundary.")
    if any(m < 0 for m in fecundity) or fecundity[-1] != 0:
        raise ValueError("Fecundity must be nonnegative per age interval and zero "
                         "at the terminal boundary.")

    initial = counts[0]
    survivorship = [n / initial for n in counts]
    deaths = [a - b for a, b in zip(counts, counts[1:])] + [0]
    person_time = [
        (counts[i] + counts[i + 1]) * (ages[i + 1] - ages[i]) / 2
        for i in range(len(ages) - 1)
    ] + [0.0]
    remaining_time = [0.0] * len(ages)
    for i in range(len(ages) - 2, -1, -1):
        remaining_time[i] = person_time[i] + remaining_time[i + 1]

    reproduction = [survivorship[i] * fecundity[i] for i in range(len(ages))]
    net_reproduction = math.fsum(reproduction)
    if net_reproduction > 0:
        generation = math.fsum(age * births for age, births in zip(ages, reproduction)) / net_reproduction
        intrinsic = (math.log(net_reproduction) / generation if generation > 0
                     else "undefined (reproduction only at age zero)")
    else:
        generation = "undefined (no reproduction)"
        intrinsic = "undefined (no reproduction)"
    rows = [
        [age, n, deaths[i], survivorship[i],
         deaths[i] / n if n and i < len(ages) - 1 else "undefined (no interval or survivors)",
         person_time[i], remaining_time[i],
         remaining_time[i] / n if n else "undefined (no survivors)",
         fecundity[i], reproduction[i]]
        for i, (age, n) in enumerate(zip(ages, counts))
    ]

    return {
        "summary": f"Closed-cohort life table for {initial} initial individuals; "
                   f"life expectancy at birth is {remaining_time[0] / initial:.6g} {unit} "
                   f"and net reproductive rate R₀ is {net_reproduction:.6g} offspring "
                   "per initial individual. Person-time assumes uniformly distributed "
                   "deaths within each interval; all survivors must be accounted for "
                   "by the final age boundary.",
        "values": {
            "initial_cohort": initial, "initial_cohort_unit": "individuals",
            "intervals": len(ages) - 1, "intervals_unit": "intervals",
            "deaths_total": initial, "deaths_total_unit": "individuals",
            "life_expectancy_at_birth": remaining_time[0] / initial,
            "life_expectancy_unit": unit,
            "net_reproductive_rate": net_reproduction,
            "net_reproductive_rate_unit": "offspring per initial individual",
            "mean_generation_time": generation, "mean_generation_time_unit": unit,
            "approx_intrinsic_growth_rate": intrinsic,
            "approx_intrinsic_growth_rate_unit": f"1/{unit}",
            "intrinsic_growth_method": "ln(R0)/mean generation time approximation",
        },
        "table": {
            "columns": [
                f"Age ({unit})", "Alive (individuals)", "Deaths (individuals)",
                "l_x (probability)", "q_x (probability)",
                f"L_x (individuals·{unit})", f"T_x (individuals·{unit})",
                f"e_x ({unit})", "m_x (offspring/individual/interval)",
                "l_x m_x (offspring/initial individual/interval)",
            ],
            "rows": rows,
        },
        "plot": {
            "title": "Closed-cohort survivorship",
            "xLabel": f"Age ({unit})", "yLabel": "l_x (probability)",
            "x": ages, "y": survivorship,
        },
    }


def _matrix_vector(matrix, population):
    return [math.fsum(a * n for a, n in zip(row, population)) for row in matrix]


def _power_iterate(matrix, iterations=4000):
    """Normalized power iteration; returns the converged fractions or None."""
    size = len(matrix)
    stage_fractions = [1.0 / size] * size
    for _ in range(iterations):
        projected = _matrix_vector(matrix, stage_fractions)
        total = math.fsum(projected)
        if total == 0:
            return None
        if not math.isfinite(total):
            raise ValueError("Matrix coefficients exceed the numeric range.")
        updated = [n / total for n in projected]
        if max(abs(a - b) for a, b in zip(updated, stage_fractions)) < 1e-12:
            return updated
        stage_fractions = updated
    return None


def _asymptotic_multiplier(matrix):
    """Return (dominant multiplier, stable stage fractions, oscillates).

    Every nonnegative matrix has a real dominant (Perron) eigenvalue, but
    plain power iteration does not converge for periodic (imprimitive)
    matrices such as semelparous Leslie matrices. Iterating A + I instead
    shares A's Perron eigenvector and converges for those matrices too.
    The result is reported only when A v = lambda v is verified, and the
    oscillates flag records that the plain projection cycles instead of
    approaching the stable structure.
    """
    size = len(matrix)
    # A nonnegative matrix is nilpotent (dominant multiplier 0) exactly when
    # A^n applied to a positive vector vanishes.
    probe = [1.0] * size
    for _ in range(size):
        probe = _matrix_vector(matrix, probe)
    if not any(probe):
        return 0.0, (), False
    shifted = [[a + (1.0 if i == j else 0.0) for j, a in enumerate(row)]
               for i, row in enumerate(matrix)]
    stable = _power_iterate(shifted)
    if stable is None:
        return "undefined (dominant multiplier did not converge)", (), False
    check = _matrix_vector(matrix, stable)
    eigenvalue = math.fsum(check)
    if not math.isfinite(eigenvalue):
        raise ValueError("Matrix coefficients exceed the numeric range.")
    scale = max(eigenvalue, max(stable))
    if max(abs(a - eigenvalue * b) for a, b in zip(check, stable)) > 1e-9 * scale:
        return "undefined (dominant multiplier did not converge)", (), False
    if eigenvalue <= 0:
        return 0.0, (), False
    oscillates = _power_iterate(matrix) is None
    return eigenvalue, stable, oscillates


def matrix_population_projection(data):
    """Project n(t+1) = A n(t) with a nonnegative Leslie/Lefkovitch matrix."""
    matrix, population = data["projection_matrix"], data["initial_population"]
    steps = data["steps"]
    unit = _unit_label(data["time_step_unit"], "Time step")
    size = len(matrix)
    if not 2 <= size <= 24 or any(len(row) != size for row in matrix):
        raise ValueError("Population projection needs a square matrix with 2–24 stages.")
    if any(a < 0 for row in matrix for a in row):
        raise ValueError("Matrix fertility, survival and transition entries must be nonnegative.")
    if len(population) != size or any(n < 0 for n in population):
        raise ValueError("Provide one nonnegative starting abundance per stage.")
    if not 1 <= steps <= 400:
        raise ValueError("Project between 1 and 400 time steps.")
    first_total = math.fsum(population)
    if first_total <= 0:
        raise ValueError("At least one individual must be present initially.")

    current = list(population)
    rows = []
    for step in range(steps + 1):
        total = math.fsum(current)
        if not math.isfinite(total):
            raise ValueError("Projection overflows; reduce the number of steps or matrix coefficients.")
        rows.append([step, *current, total])
        if step < steps:
            current = _matrix_vector(matrix, current)
            if any(not math.isfinite(n) or n < 0 for n in current):
                raise ValueError("Projection overflows; reduce the number of steps or matrix coefficients.")
    final_total = rows[-1][-1]
    multiplier, stable, oscillates = _asymptotic_multiplier(matrix)
    finite_horizon = (final_total / first_total)**(1 / steps)
    values = {
        "stages": size, "stages_unit": "stages",
        "projected_steps": steps, "projected_steps_unit": unit,
        "initial_total": first_total, "final_total": final_total,
        "abundance_unit": "individuals",
        "finite_horizon_growth_factor_per_step": finite_horizon,
        "asymptotic_growth_factor_per_step": multiplier,
        "growth_factor_unit": "1 (dimensionless)",
        "asymptotic_log_growth_rate": (
            math.log(multiplier) if isinstance(multiplier, float) and multiplier > 0
            else "undefined (no reliable positive asymptotic multiplier)"
        ),
        "asymptotic_log_growth_rate_unit": f"1/{unit}",
    }
    if stable:
        values["stable_stage_fraction_unit"] = "1 (dimensionless)"
        values.update({f"stable_stage_{i + 1}_fraction": portion
                       for i, portion in enumerate(stable)})
        values["stage_structure_behaviour"] = (
            "periodic: stage proportions cycle" if oscillates
            else "converges to the stable stage structure")
    if isinstance(multiplier, float) and oscillates:
        asymptotic_note = (f"Dominant (Perron) multiplier: {multiplier:.6g} per step. The matrix "
                           "is periodic, so stage proportions cycle instead of settling "
                           "into the stable structure.")
    elif isinstance(multiplier, float):
        asymptotic_note = f"Dominant (Perron) multiplier: {multiplier:.6g} per step."
    else:
        asymptotic_note = "No verified dominant multiplier or stable stage proportions."
    return {
        "summary": f"Projected {size} stages through {steps} {unit} steps: "
                   f"{first_total:.6g} to {final_total:.6g} individuals; "
                   f"finite-horizon geometric factor {finite_horizon:.6g} per step. "
                   f"{asymptotic_note} This is a deterministic, time-invariant, "
                   "density-independent projection.",
        "values": values,
        "table": {
            "columns": [f"Time ({unit})", *[f"Stage {i + 1} (individuals)"
                       for i in range(size)], "Total (individuals)"],
            "rows": rows,
        },
        "plot": {
            "title": "Structured population projection",
            "xLabel": f"Time ({unit})", "yLabel": "Total abundance (individuals)",
            "x": [row[0] for row in rows], "y": [row[-1] for row in rows],
        },
    }


OPERATIONS = (
    Operation(
        "mark_recapture", "Estimate population by mark–recapture",
        "Lincoln–Petersen and Chapman two-capture estimates with Chapman variance and approximate interval.",
        (Field("marked_first", "Marked and released in first capture", "integer", unit="individuals"),
         Field("captured_second", "All individuals captured second time", "integer", unit="individuals"),
         Field("recaptured_marked", "Marked individuals recaptured", "integer", unit="individuals")),
        {"marked_first": 80, "captured_second": 100, "recaptured_marked": 20},
        mark_recapture,
        methods=(
            "For M marked and released, C captured next and R recaptured marks, the "
            "Lincoln–Petersen estimator is MC/R (undefined at R=0). Report the "
            "small-sample Chapman correction (M+1)(C+1)/(R+1)−1 and its hypergeometric "
            "variance (M+1)(C+1)(M−R)(C−R)/[(R+1)²(R+2)]. With at least five "
            "recaptures, show a 1.96-SE normal-approximation interval, clipped below "
            "at the number of distinct captured individuals. Its small-sample coverage "
            "is not guaranteed. Assumes population closure, mixing, independent equal "
            "catchability, and permanent non-disruptive marking."
        ),
        citations=(
            Citation("Chapman (1951), Some Properties of the Hypergeometric Distribution, "
                     "University of California Publications in Statistics 1:131–160",
                     "https://search.r-project.org/CRAN/refmans/recapr/html/NChapman.html"),
            Citation("recapr: estimated Chapman variance and mark–recapture assumptions",
                     "https://search.r-project.org/CRAN/refmans/recapr/html/vChapman.html"),
        ),
    ),
    Operation(
        "cohort_life_table", "Closed-cohort life table and reproduction",
        "Calculate age-specific survivorship, mortality, person-time, life expectancy and reproductive rates.",
        (Field("ages", "Age interval boundaries starting at zero", "vector",
               unit="selected time unit"),
         Field("survivors", "Cohort alive at each age boundary", "vector", unit="individuals"),
         Field("fecundity", "Offspring per survivor in each interval", "vector",
               unit="offspring/individual/interval"),
         Field("time_unit", "Age unit (e.g. years)", "text", unit="unit label")),
        {"ages": [0, 1, 2, 3], "survivors": [100, 80, 50, 0],
         "fecundity": [0, 2, 1, 0], "time_unit": "years"},
        cohort_life_table,
        methods=(
            "A closed cohort starts with n₀ positive at age zero, has nonincreasing "
            "whole-number survivors, and reaches zero at its final boundary. Compute "
            "dₓ=nₓ−nₓ₊₁, lₓ=nₓ/n₀, qₓ=dₓ/nₓ, interval person-time "
            "Lₓ=(nₓ+nₓ₊₁)Δt/2 (uniform within-interval mortality), remaining "
            "Tₓ=ΣL, and eₓ=Tₓ/nₓ. Fecundity mₓ is offspring per survivor "
            "per interval, so R₀=Σlₓmₓ and generation time Σx lₓmₓ/R₀; "
            "ln(R₀)/generation time is a heuristic, not the Euler–Lotka root. "
            "Undefined ratios are labeled explicitly."
        ),
        citations=(
            Citation("OpenStax Biology 2e §45.1, Population Demography — cohort life tables",
                     "https://openstax.org/books/biology-2e/pages/45-1-population-demography"),
            Citation("Leslie (1945), On the use of matrices in certain population mathematics",
                     "https://doi.org/10.1093/biomet/33.3.183"),
        ),
    ),
    Operation(
        "matrix_population_projection", "Project structured populations (Leslie/Lefkovitch)",
        "Deterministic age/stage projection, finite-horizon growth and convergent asymptotic stage structure.",
        (Field("projection_matrix", "Nonnegative fecundity and transition matrix", "matrix",
               unit="1"),
         Field("initial_population", "Initial abundance in matching stage order", "vector",
               unit="individuals"),
         Field("steps", "Number of projection steps (1–400)", "integer",
               unit="steps (dimensionless)"),
         Field("time_step_unit", "Time represented by one step (e.g. years)", "text",
               unit="unit label")),
        {"projection_matrix": [[1, 1], [1, 0]],
         "initial_population": [1, 0], "steps": 10, "time_step_unit": "years"},
        matrix_population_projection,
        methods=(
            "For a nonnegative square Leslie age-class or Lefkovitch stage-class "
            "projection matrix A, iterate nₜ₊₁=A nₜ exactly for each requested "
            "step. Report total and stage abundance, and the finite-horizon geometric "
            "factor (N_end/N_initial)^(1/steps). The dominant (Perron) multiplier "
            "and stable stage fractions come from normalized power iteration on "
            "A + I, which shares A's Perron eigenvector and also converges for "
            "periodic (imprimitive) matrices such as semelparous Leslie matrices; "
            "they are reported only after A v = λ v is verified, and periodic "
            "matrices are flagged because their stage proportions cycle. "
            "Time invariant, no density dependence, environmental variability or "
            "integer rounding of expected counts."
        ),
        citations=(
            Citation("Leslie (1945), On the Use of Matrices in Certain Population Mathematics, "
                     "Biometrika 33:183–212",
                     "https://doi.org/10.1093/biomet/33.3.183"),
        ),
    ),
)
