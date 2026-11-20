"""Independent numerical and invalid-design tests for Biology population dynamics."""

import math

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import readable_result, run_operation


def run(op_id, **kwargs):
    operation = next(op for op in operations("biology") if op.id == op_id)
    return run_operation("biology", operation, kwargs)


def test_append_only_catalog_examples_and_signed_receipts():
    ids = [op.id for op in operations("biology")]
    assert ids[:3] == ["community_diversity", "quadrat_density", "community_overlap"]
    assert ids[11:14] == ["mark_recapture", "cohort_life_table", "matrix_population_projection"]
    assert ids[14:] == ["hill_numbers", "beta_diversity", "species_accumulation"]
    assert len(ids) == len(set(ids)) == 17
    for op in operations("biology")[11:14]:
        assert op.methods and op.citations
        assert all(f.unit for f in op.fields)
        artifact = run(op.id, **op.example)
        assert readable_result({"version": 1, "latest": artifact}, "biology")
        assert artifact["plot"]["x"] and len(artifact["plot"]["x"]) == len(artifact["plot"]["y"])
        assert all(len(row) == len(artifact["table"]["columns"])
                   for row in artifact["table"]["rows"])


def test_petersen_and_chapman_estimates_against_closed_form():
    # OpenStax's 80 marked, 100 second, 20 marked again: Petersen is 400.
    result = run("mark_recapture", marked_first=80, captured_second=100, recaptured_marked=20)
    v = result["values"]
    assert v["distinct_observed"] == 160
    assert v["lincoln_petersen_estimate"] == 400
    assert v["chapman_estimate"] == pytest.approx(81 * 101 / 21 - 1)
    variance = 81 * 101 * 60 * 80 / (21**2 * 22)
    assert v["chapman_variance"] == pytest.approx(variance)
    assert v["chapman_standard_error"] == pytest.approx(math.sqrt(variance))
    assert v["chapman_ci95_lower"] == pytest.approx(
        max(160, v["chapman_estimate"] - 1.96 * math.sqrt(variance)))
    assert v["chapman_ci95_upper"] == pytest.approx(
        v["chapman_estimate"] + 1.96 * math.sqrt(variance))
    assert v["population_estimate_unit"] == "individuals"
    assert result["table"]["rows"][1][1] == v["chapman_estimate"]
    assert result["plot"]["y"] == [160, v["chapman_estimate"]]


def test_mark_recapture_sparse_no_overlap_and_full_overlap():
    no_overlap = run("mark_recapture", marked_first=10, captured_second=5, recaptured_marked=0)
    v = no_overlap["values"]
    assert v["chapman_estimate"] == 65
    assert "undefined" in v["lincoln_petersen_estimate"]
    assert "undefined" in v["chapman_ci95_lower"]
    assert v["distinct_observed"] == 15
    assert "Sparse" in v["caveat"]
    full = run("mark_recapture", marked_first=10, captured_second=8, recaptured_marked=8)
    assert full["values"]["chapman_estimate"] == 10
    assert full["values"]["lincoln_petersen_estimate"] == 10
    assert full["values"]["chapman_variance"] == 0
    assert full["values"]["chapman_ci95_lower"] == 10
    assert full["values"]["chapman_ci95_upper"] == 10


@pytest.mark.parametrize("args", [
    {"marked_first": 0, "captured_second": 10, "recaptured_marked": 0},
    {"marked_first": 10, "captured_second": 0, "recaptured_marked": 0},
    {"marked_first": 8, "captured_second": 10, "recaptured_marked": 9},
    {"marked_first": 8, "captured_second": 10, "recaptured_marked": -1},
    {"marked_first": 2**53 - 1, "captured_second": 2**53 - 1, "recaptured_marked": 0},
])
def test_mark_recapture_rejects_impossible_or_inexact_counts(args):
    with pytest.raises(ValueError):
        run("mark_recapture", **args)


def test_cohort_life_table_exact_hand_calculation():
    # For age intervals [0,1), [1,2), [2,3), deaths are 20,30,50.
    # Lx=90,65,25 person-years and Tx=180,90,25; e0=1.8 years.
    result = run("cohort_life_table", ages=[0, 1, 2, 3],
                 survivors=[100, 80, 50, 0], fecundity=[0, 2, 1, 0],
                 time_unit="years")
    v = result["values"]
    rows = result["table"]["rows"]
    assert [row[2] for row in rows] == [20, 30, 50, 0]
    assert [row[3] for row in rows] == pytest.approx([1, .8, .5, 0])
    assert [row[4] for row in rows[:3]] == pytest.approx([.2, .375, 1])
    assert [row[5] for row in rows] == pytest.approx([90, 65, 25, 0])
    assert [row[6] for row in rows] == pytest.approx([180, 90, 25, 0])
    assert [row[7] for row in rows[:3]] == pytest.approx([1.8, 90/80, 25/50])
    assert "undefined" in rows[-1][4] and "undefined" in rows[-1][7]
    assert v["life_expectancy_at_birth"] == pytest.approx(1.8)
    assert v["net_reproductive_rate"] == pytest.approx(2.1)
    assert v["mean_generation_time"] == pytest.approx(2.6 / 2.1)
    assert v["approx_intrinsic_growth_rate"] == pytest.approx(math.log(2.1) / (2.6 / 2.1))
    assert result["plot"]["y"] == pytest.approx([1, .8, .5, 0])
    assert result["plot"]["xLabel"] == "Age (years)"


def test_life_table_unequal_ages_extinction_and_no_reproduction():
    result = run("cohort_life_table", ages=[0, 1, 3], survivors=[10, 5, 0],
                 fecundity=[0, 0, 0], time_unit="days")
    rows = result["table"]["rows"]
    assert [row[5] for row in rows] == pytest.approx([7.5, 5.0, 0.0])
    assert result["values"]["life_expectancy_at_birth"] == pytest.approx(1.25)
    assert result["values"]["net_reproductive_rate"] == 0
    assert "undefined" in result["values"]["mean_generation_time"]
    assert "undefined" in result["values"]["approx_intrinsic_growth_rate"]
    extinction = run("cohort_life_table", ages=[0, 1, 2],
                     survivors=[10, 0, 0], fecundity=[0, 0, 0], time_unit="days")
    assert "undefined" in extinction["table"]["rows"][1][4]
    born = run("cohort_life_table", ages=[0, 1],
               survivors=[10, 0], fecundity=[1, 0], time_unit="days")
    assert born["values"]["mean_generation_time"] == 0
    assert "undefined" in born["values"]["approx_intrinsic_growth_rate"]


@pytest.mark.parametrize("override", [
    {"ages": [0, 1, 2], "survivors": [10, 5]},
    {"ages": [1, 2, 3]},
    {"ages": [0, 1, 1]},
    {"survivors": [100, 85, 90, 0]},
    {"survivors": [100, 80, 50, 1]},
    {"survivors": [100, 80, 50.5, 0]},
    {"survivors": [0, 0, 0, 0]},
    {"fecundity": [0, 1, -1, 0]},
    {"fecundity": [0, 1, 1, 1]},
])
def test_life_table_rejects_noncohort_designs(override):
    fields = {"ages": [0, 1, 2, 3], "survivors": [100, 80, 50, 0],
              "fecundity": [0, 2, 1, 0], "time_unit": "years"}
    with pytest.raises(ValueError):
        run("cohort_life_table", **{**fields, **override})


def test_matrix_projection_fibonacci_leslie_and_perron_oracle():
    # Leslie A = [[1,1],[1,0]] has Fibonacci stages and Perron root phi.
    result = run("matrix_population_projection", projection_matrix=[[1, 1], [1, 0]],
                 initial_population=[1, 0], steps=5, time_step_unit="years")
    rows = result["table"]["rows"]
    assert [row[-1] for row in rows] == [1, 2, 3, 5, 8, 13]
    assert [row[1:3] for row in rows] == [[1, 0], [1, 1], [2, 1],
                                         [3, 2], [5, 3], [8, 5]]
    assert result["plot"]["x"] == [0, 1, 2, 3, 4, 5]
    assert result["plot"]["y"] == [1, 2, 3, 5, 8, 13]
    v = result["values"]
    phi = (1 + math.sqrt(5)) / 2
    assert v["final_total"] == 13
    assert v["finite_horizon_growth_factor_per_step"] == pytest.approx(13**(.2))
    assert v["asymptotic_growth_factor_per_step"] == pytest.approx(phi, rel=1e-10)
    assert v["stable_stage_1_fraction"] == pytest.approx(1 / phi)
    assert v["stable_stage_2_fraction"] == pytest.approx(1 / phi**2)
    assert v["asymptotic_log_growth_rate"] == pytest.approx(math.log(phi))


def test_matrix_periodic_and_zero_population_edge_cases():
    # A period-two matrix still has a Perron root (sqrt(2 * 0.5) = 1) and
    # eigenvector (2:1); its stage proportions cycle instead of converging.
    periodic = run("matrix_population_projection",
                   projection_matrix=[[0, 2], [.5, 0]],
                   initial_population=[1, 1], steps=4, time_step_unit="months")
    assert [row[-1] for row in periodic["table"]["rows"]] == pytest.approx(
        [2, 2.5, 2, 2.5, 2])
    assert periodic["values"]["asymptotic_growth_factor_per_step"] == pytest.approx(1, rel=1e-9)
    assert periodic["values"]["finite_horizon_growth_factor_per_step"] == pytest.approx(1)
    assert periodic["values"]["stable_stage_1_fraction"] == pytest.approx(2 / 3, rel=1e-9)
    assert periodic["values"]["stable_stage_2_fraction"] == pytest.approx(1 / 3, rel=1e-9)
    assert periodic["values"]["stage_structure_behaviour"].startswith("periodic")
    assert "cycle" in periodic["summary"]

    zero = run("matrix_population_projection",
               projection_matrix=[[0, 0], [0, 0]],
               initial_population=[20, 10], steps=3, time_step_unit="years")
    assert zero["values"]["final_total"] == 0
    assert zero["values"]["finite_horizon_growth_factor_per_step"] == 0
    assert zero["values"]["asymptotic_growth_factor_per_step"] == 0
    assert "stable_stage_1_fraction" not in zero["values"]


def test_semelparous_leslie_matrix_reports_perron_root():
    # Only the last age class breeds, so the matrix is periodic (period 3).
    # The Perron root is (F s1 s2)^(1/3) and the eigenvector is
    # proportional to (1, s1/lambda, s1 s2/lambda^2).
    fertility, s1, s2 = 6.0, 0.5, 0.5
    result = run("matrix_population_projection",
                 projection_matrix=[[0, 0, fertility], [s1, 0, 0], [0, s2, 0]],
                 initial_population=[10, 0, 0], steps=9, time_step_unit="years")
    v = result["values"]
    lam = (fertility * s1 * s2) ** (1 / 3)
    assert v["asymptotic_growth_factor_per_step"] == pytest.approx(lam, rel=1e-9)
    assert v["asymptotic_log_growth_rate"] == pytest.approx(math.log(lam), rel=1e-8)
    weights = [1, s1 / lam, s1 * s2 / lam**2]
    for i, weight in enumerate(weights, start=1):
        assert v[f"stable_stage_{i}_fraction"] == pytest.approx(weight / sum(weights), rel=1e-9)
    assert v["stage_structure_behaviour"].startswith("periodic")
    # The primitive Fibonacci matrix keeps converging behaviour.
    primitive = run("matrix_population_projection", projection_matrix=[[1, 1], [1, 0]],
                    initial_population=[1, 0], steps=5, time_step_unit="years")
    assert primitive["values"]["stage_structure_behaviour"].startswith("converges")


def test_high_fecundity_period_two_leslie_has_verified_perron_pair():
    # Adding a fixed I makes this matrix converge too slowly in 4,000 steps.
    result = run("matrix_population_projection",
                 projection_matrix=[[0, 1_000_000], [1, 0]],
                 initial_population=[1, 1], steps=1, time_step_unit="years")
    values = result["values"]
    assert values["asymptotic_growth_factor_per_step"] == pytest.approx(1000, rel=1e-10)
    assert values["stable_stage_1_fraction"] == pytest.approx(1000 / 1001, rel=1e-10)
    assert values["stable_stage_2_fraction"] == pytest.approx(1 / 1001, rel=1e-10)
    assert values["stage_structure_behaviour"] == "periodic: stage proportions cycle"
    assert "is periodic" in result["summary"]


def test_perron_balancing_handles_extreme_fecundity_to_survival_ratio():
    # The tiny survival edge must not disappear when scaling by the fecundity.
    result = run("matrix_population_projection",
                 projection_matrix=[[0, 1e300], [1e-300, 0]],
                 initial_population=[1, 1], steps=1, time_step_unit="years")
    values = result["values"]
    assert values["asymptotic_growth_factor_per_step"] == pytest.approx(1, rel=1e-10)
    assert values["stable_stage_2_fraction"] == pytest.approx(1e-300, rel=1e-9, abs=0)
    assert values["stage_structure_behaviour"].startswith("periodic")


def test_nearly_periodic_but_primitive_matrix_converges():
    # The small positive diagonal makes this graph aperiodic, despite the
    # unshifted second eigenvalue being nearly as large in magnitude.
    result = run("matrix_population_projection",
                 projection_matrix=[[.001, 4], [1, .001]],
                 initial_population=[1, 1], steps=1, time_step_unit="years")
    values = result["values"]
    assert values["asymptotic_growth_factor_per_step"] == pytest.approx(2.001, rel=1e-10)
    assert values["stable_stage_1_fraction"] == pytest.approx(2 / 3, rel=1e-9)
    assert values["stable_stage_2_fraction"] == pytest.approx(1 / 3, rel=1e-9)
    assert values["stage_structure_behaviour"] == "converges to the stable stage structure"
    assert "is periodic" not in result["summary"]


def test_tiny_positive_diagonal_perron_root_survives_many_stage_probe():
    matrix = [[1e-100 if i == j else 0 for j in range(4)] for i in range(4)]
    result = run("matrix_population_projection", projection_matrix=matrix,
                 initial_population=[1, 1, 1, 1], steps=1, time_step_unit="years")
    values = result["values"]
    assert values["final_total"] == pytest.approx(4e-100, rel=1e-12, abs=0)
    # Explicit abs=0: pytest's default absolute tolerance would accept zero.
    assert values["asymptotic_growth_factor_per_step"] == pytest.approx(1e-100, rel=1e-12, abs=0)
    assert values["asymptotic_log_growth_rate"] == pytest.approx(math.log(1e-100))
    for i in range(1, 5):
        assert values[f"stable_stage_{i}_fraction"] == pytest.approx(.25)
    assert values["stage_structure_behaviour"].startswith("converges")


def test_tiny_positive_triangular_root_and_genuinely_nilpotent_matrix():
    # A^4 underflows even though the spectral radius is positive.
    triangular = [[1e-100, 1e80, 0, 0], [0, 1e-100, 1e80, 0],
                  [0, 0, 1e-100, 1e80], [0, 0, 0, 1e-100]]
    result = run("matrix_population_projection", projection_matrix=triangular,
                 initial_population=[1, 1, 1, 1], steps=1, time_step_unit="years")
    assert result["values"]["asymptotic_growth_factor_per_step"] == pytest.approx(
        1e-100, rel=1e-12, abs=0)

    nilpotent = run("matrix_population_projection",
                    projection_matrix=[[0, 2], [0, 0]],
                    initial_population=[1, 1], steps=2, time_step_unit="years")
    assert nilpotent["values"]["asymptotic_growth_factor_per_step"] == 0
    assert "stable_stage_1_fraction" not in nilpotent["values"]


@pytest.mark.parametrize("override", [
    {"projection_matrix": [[1, 2, 3], [4, 5, 6]]},
    {"projection_matrix": [[1, 1], [-1, 0]]},
    {"projection_matrix": [[0, 1], [0, 0]], "initial_population": [0, 0]},
    {"initial_population": [1, 0, 0]},
    {"initial_population": [1, -1]},
    {"steps": 0},
    {"steps": 401},
    {"projection_matrix": [[1e308, 1e308], [0, 0]], "initial_population": [1, 1]},
])
def test_matrix_projection_rejects_bad_dimensions_and_unbounded_projections(override):
    fields = {"projection_matrix": [[1, 1], [1, 0]],
              "initial_population": [1, 0], "steps": 5,
              "time_step_unit": "years"}
    with pytest.raises(ValueError):
        run("matrix_population_projection", **{**fields, **override})
