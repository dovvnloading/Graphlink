"""Independent post-merge audit regressions for Scientific Workbench PRs 588, 589 and 591.

Reference identities are derived directly from the GUM first-order propagation
law, Student-t(df=1) Cauchy quantile and exact SI prefix definitions; no pack
fixtures are loaded.
"""

import json
import math

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import run_operation
from graphlink_plugins.scientific.packs.fitting import _student_t_ppf


def run(pack, operation_id, inputs):
    operation = next(op for op in operations(pack) if op.id == operation_id)
    return run_operation(pack, operation, inputs)["values"]


def propagate(expression, name, value, uncertainty):
    return run("uncertainty", "propagate_uncertainty", {
        "expression": expression,
        "measurements": json.dumps({name: {"value": value, "uncertainty": uncertainty}}),
        "correlations": "[]",
        "coverage_probability": 0.95,
    })


def test_gum_linear_sensitivity_survives_a_large_constant_offset():
    # JCGM 100:2008 Eq. (10): for Y = X + constant, c_X = 1 and u(Y) = u(X).
    # Central differencing the two ~1e20 outputs loses the entire derivative.
    result = propagate("x + 1e20", "x", 1.0, 1.0)
    assert result["sensitivity_x"] == pytest.approx(1.0, rel=1e-14)
    assert result["combined_standard_uncertainty"] == pytest.approx(1.0, rel=1e-14)


def test_gum_log_sensitivity_for_small_positive_measurement():
    # At 1e-20, ln(x) is smooth and its GUM sensitivity is 1/x = 1e20.
    # A fixed 1e-16 finite-difference floor incorrectly probes x-h < 0.
    result = propagate("ln(x)", "x", 1e-20, 1e-21)
    assert result["estimate"] == pytest.approx(math.log(1e-20))
    assert result["sensitivity_x"] == pytest.approx(1e20, rel=1e-14)
    assert result["combined_standard_uncertainty"] == pytest.approx(0.1, rel=1e-14)


def test_gum_elementary_chain_rule_for_supported_functions_and_powers():
    # d/dx (sqrt(x) + ln(x) + 3*x^2) at x=4 = 1/4 + 1/4 + 24.
    result = propagate("sqrt(x) + ln(x) + 3*x^2", "x", 4, 0.02)
    assert result["sensitivity_x"] == pytest.approx(24.5, rel=1e-13)
    assert result["combined_standard_uncertainty"] == pytest.approx(0.49, rel=1e-13)


def test_student_t_extreme_lower_tail_uses_complement_without_rounding_to_one():
    # Student t with 1 degree of freedom is the standard Cauchy distribution.
    # The exact quantile is -cot(pi*p), still finite at p=1e-20.
    probability = 1e-20
    expected = -1 / math.tan(math.pi * probability)
    assert _student_t_ppf(probability, 1) == pytest.approx(expected, rel=2e-13)


def test_pearson_goodness_of_fit_rejects_zero_observed_sample():
    # An absolute tolerance in matching totals must not turn n=0 into a
    # valid goodness-of-fit test with a p-value and sample degrees of freedom.
    with pytest.raises(ValueError, match="positive observed total"):
        run("fitting", "chi_square_gof", {
            "observed": [0, 0], "expected": [1e-12, 1e-12],
        })


def test_absolute_kelvin_submultiple_conversion_detects_binary64_underflow():
    # 5e-324 mK is positive, but its Kelvin value is below the smallest
    # representable positive binary64 number; it must not become 0 K.
    with pytest.raises(ValueError, match="numerical range"):
        run("core", "convert_unit", {
            "value": math.ulp(0.0), "from_unit": "mK", "to_unit": "K",
        })


def test_nist_saturating_exponential_keeps_tiny_nonzero_increment():
    # NIST StRD Misra1a uses a*(1-exp(-b*x)); when b*x=1e-300,
    # exp(-b*x) rounds to 1 at 76-decimal-digit precision but the
    # 1-exp(-b*x) difference still matters when a=1e300.
    from decimal import Decimal, localcontext
    from graphlink_plugins.scientific.packs.fitting import _model

    with localcontext() as ctx:
        ctx.prec = 76
        estimate, derivatives = _model(
            "saturating_exponential", Decimal(1),
            [Decimal("1e300"), Decimal("1e-300")],
        )
    assert float(estimate) == pytest.approx(1.0, rel=1e-14)
    assert float(derivatives[0]) == pytest.approx(1e-300, rel=1e-14, abs=0)


def test_student_t_heavy_tail_does_not_underflow_while_probability_is_finite():
    # Student t(df=1) is standard Cauchy; P(T<-t) ~ 1/(pi*t).
    # At t=1e200, t*t overflows, but the tail is still ~3.18e-201.
    from graphlink_plugins.scientific.packs.fitting import _student_t_cdf

    t = 1e200
    assert _student_t_cdf(-t, 1) == pytest.approx(1 / (math.pi * t), rel=2e-13, abs=0)
    assert _student_t_ppf(1e-200, 1) == pytest.approx(
        -1 / math.tan(math.pi * 1e-200), rel=2e-13,
    )


def test_welch_keeps_representable_heavy_tail_p_value():
    # A nonzero sample variance with n=2 and a constant second sample
    # gives Welch-Satterthwaite df=1. The enormous but finite statistic
    # has a Cauchy p-value above binary64's smallest subnormal number.
    result = run("fitting", "welch_t_test", {
        "sample_a": [0, 1e-154], "sample_b": [1e150, 1e150],
    })
    assert result["degrees_of_freedom"] == pytest.approx(1)
    assert result["p_value"] > 0
    assert result["p_value"] == pytest.approx(
        2 / (math.pi * abs(result["t_statistic"])), rel=2e-13, abs=0,
    )
