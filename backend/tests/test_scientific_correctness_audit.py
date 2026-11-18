"""Independent published-formula and adversarial-range regression checks.

These examples were selected because the pre-fix implementations either
returned a physically invalid quantity or rejected valid, finite data.
The tests exercise the public Scientific Workbench run/validation path.
"""

import json
import math

import pytest

from graphlink_plugins.scientific.core import run_operation
from graphlink_plugins.scientific.packs import (
    astronomy, biology, chemistry, engineering, inferential, linalg, physics,
)


def _run(module, operation_id, **updates):
    op = next(item for item in module.OPERATIONS if item.id == operation_id)
    return run_operation(module.__name__.rsplit(".", 1)[-1], op, {**op.example, **updates})


@pytest.mark.parametrize("calculation", ["thin_lens", "spherical_mirror"])
def test_near_focus_is_finite_but_exact_focus_is_singular(calculation):
    # OpenStax University Physics 3, section 2.4: 1/f = 1/do + 1/di.
    focus = 1.0
    for obj in (math.nextafter(focus, math.inf), math.nextafter(focus, -math.inf)):
        result = _run(physics, "geometric_optics", calculation=calculation,
                      focal_length_m=focus, object_distance_m=obj)["values"]
        assert isinstance(result["image_distance_m"], float)
        assert result["image_distance_m"] == pytest.approx(obj / (obj - focus), rel=2e-15)
        assert result["lateral_magnification_1"] == pytest.approx(-result["image_distance_m"] / obj)
    exact = _run(physics, "geometric_optics", calculation=calculation,
                 focal_length_m=focus, object_distance_m=focus)["values"]
    assert exact["image_distance_m"] == "at infinity (m)"


def test_hurlbert_rarefaction_retains_rare_species_at_large_n():
    # E[S_m] = sum_i (1 - C(N-n_i,m)/C(N,m)); at N≈2**53
    # lgamma subtraction loses the ~1e-9 contribution of the second taxon.
    N, rare, subsample = 2**53 - 1, 3000, 3000
    log_absent = math.fsum(math.log1p(-rare / (N - j)) for j in range(subsample))
    expected = 2 - math.exp(log_absent)
    result = _run(biology, "richness_rarefaction",
                  counts=[N - rare, rare], subsample_individuals=subsample)["values"]
    assert result["rarefied_expected_richness"] == pytest.approx(expected, abs=1e-11, rel=0)


def test_reject_grazing_transit_when_depth_is_used_to_infer_radius():
    # Seager & Mallén-Ornelas: depth=(Rp/Rstar)^2 only for complete overlap.
    with pytest.raises(ValueError, match="[Gg]razing.*depth"):
        _run(astronomy, "exoplanet_transit", out_of_transit_flux_relative=1,
             in_transit_flux_relative=.99, impact_parameter_stellar_radii=1.0)
    nongrazing = _run(astronomy, "exoplanet_transit", out_of_transit_flux_relative=1,
                      in_transit_flux_relative=.99, impact_parameter_stellar_radii=.8)["values"]
    assert nongrazing["full_duration_hours"] > 0


def test_zero_theoretical_yield_is_valid_but_percentage_is_undefined():
    # OpenStax Chemistry 2e 4.4: percent yield = actual/theoretical * 100.
    zero = _run(chemistry, "reaction_yield",
                reactant_mass_g=[0, 20], actual_product_mass_g=0)["values"]
    assert zero["theoretical_product_mass_g"] == 0
    assert zero["actual_product_mass_g"] == 0
    assert "undefined" in zero["percent_yield"]
    with pytest.raises(ValueError, match="Positive actual product mass"):
        _run(chemistry, "reaction_yield", reactant_mass_g=[0, 20],
             actual_product_mass_g=1)


@pytest.mark.parametrize("zeta", [0.2, 1.0, 2.0])
def test_second_order_step_starts_quadratically_not_at_zero(zeta):
    # y''+2 ζ y'+y=1, y(0)=y'(0)=0 -> y(u)=u²/2−ζu³/3+O(u⁴).
    tiny = 1e-9
    response = _run(engineering, "transfer_function_response",
                    order=2, dc_gain_1=1, step_amplitude_1=1,
                    natural_frequency_rad_s=1, damping_ratio_1=zeta,
                    time_s=[0, tiny, 1], angular_frequency_rad_s=[0, 1])["plot"]["y"]
    assert response[0] == 0
    assert response[1] == pytest.approx(tiny*tiny/2 - zeta*tiny**3/3, rel=1e-9)


def test_exact_large_response_least_squares_has_zero_rss():
    # NIST StRD regression RSS is sum of squared achieved residuals, not
    # (squared response scale) multiplied by zero.
    result = _run(linalg, "least_squares_qr",
                  matrix=[[1.0], [0.0]], rhs=[1e200, 0.0])["values"]
    assert json.loads(result["coefficients"]) == pytest.approx([1e200])
    assert result["residual_l2_norm"] == 0
    assert result["residual_sum_squares"] == 0


def test_centered_pca_reports_measured_sample_standard_deviations():
    # SVD-based PCA does not turn physical feature standard deviations into 1.
    result = _run(linalg, "pca", scaling="center",
                  observations=[[1, 2], [2, 4], [3, 6], [4, 8]])["values"]
    assert json.loads(result["feature_standard_deviations"]) == pytest.approx(
        [math.sqrt(5/3), math.sqrt(20/3)], rel=1e-12)
    assert json.loads(result["means"]) == pytest.approx([2.5, 5])


def test_pearson_correlation_avoids_overflowing_finite_spans():
    xs = [-1e308, -5e307, 0, 5e307, 1e308]
    ys = [-2, -1, 0, 1, 2]
    values = _run(inferential, "pearson_correlation", x=xs, y=ys)["values"]
    assert values["r"] == pytest.approx(1, abs=1e-14)
    assert values["p_value"] == pytest.approx(0, abs=1e-12)


def test_pearson_finite_spans_keep_precision_after_large_translation():
    # Pearson r is translation invariant. Scaling ~1e9 before subtracting
    # these unit differences used to shift the result by ~7e-9.
    x = [1_000_000_000 + i for i in range(1, 6)]
    y = [2, 1, 4, 3, 5]
    r = _run(inferential, "pearson_correlation", x=x, y=y)["values"]["r"]
    assert r == pytest.approx(4 / 5, abs=1e-12, rel=0)


def test_spearman_rank_validation_also_accepts_finite_large_spans():
    xs = [-1e308, -5e307, 0, 5e307, 1e308]
    ys = [-2, -1, 0, 1, 2]
    values = _run(inferential, "spearman_correlation", x=xs, y=ys)["values"]
    assert values["rho"] == pytest.approx(1, abs=1e-14)
    assert values["p_value"] == pytest.approx(2 / math.factorial(5), rel=1e-12)


def test_shapiro_wilk_is_invariant_under_large_scaling():
    sample = [-1e308, -5e307, 0, 5e307, 1e308]
    reference = _run(inferential, "shapiro_wilk", sample=[-2, -1, 0, 1, 2])["values"]
    large = _run(inferential, "shapiro_wilk", sample=sample)["values"]
    assert large["w_statistic"] == pytest.approx(reference["w_statistic"], rel=1e-12)
    assert large["p_value"] == pytest.approx(reference["p_value"], abs=1e-10)


def test_fisher_exact_maintains_tiny_tail_probability_with_large_margins():
    # Hypergeometric fixed margins: the extreme matching 2x2 table has
    # probability 1/(n), a value lgamma differences cannot resolve at 1e15.
    size = 10**15 + 1
    values = _run(inferential, "fisher_exact", table=[[size, 0], [0, 1]])["values"]
    expected = 1 / (size + 1)
    assert values["p_greater"] == pytest.approx(expected, rel=1e-12)
    assert values["p_two_sided"] == pytest.approx(expected, rel=1e-12)
    assert values["p_less"] == pytest.approx(1)
    ordinary = _run(inferential, "fisher_exact", table=[[3, 1], [1, 3]])["values"]
    assert ordinary["p_two_sided"] == pytest.approx(34/70, rel=1e-12)


def test_linalg_singular_or_ill_conditioned_solve_still_refused():
    hilbert8 = [[1/(i+j+1) for j in range(8)] for i in range(8)]
    with pytest.raises(ValueError, match="ill-conditioned|rank-deficient"):
        _run(linalg, "matrix_inverse", matrix=hilbert8)


def test_rejected_domain_explanations_keep_units_or_physical_assumption():
    with pytest.raises(ValueError, match="Focal length must be nonzero"):
        _run(physics, "geometric_optics", calculation="thin_lens",
             object_distance_m=1, focal_length_m=0)
    with pytest.raises(ValueError, match="zero theoretical yield"):
        _run(chemistry, "reaction_yield",
             reactant_mass_g=[0, 10], actual_product_mass_g=1)
