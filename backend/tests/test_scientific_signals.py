"""Signal-processing specifications exercised through the scientific runner."""

import math

import pytest

from graphlink_plugins.scientific.core import run_operation
from graphlink_plugins.scientific.packs.signals import OPERATIONS


def run(name, **inputs):
    operation = next(op for op in OPERATIONS if op.id == name)
    return run_operation("signals", operation, inputs)


@pytest.mark.parametrize(
    ("window", "centers", "means"),
    [
        (1, [0, 1, 2, 3, 4], [0, 0, 9, 0, 0]),
        (3, [1, 2, 3], [3, 3, 3]),
        (5, [2], [1.8]),
    ],
)
def test_centered_complete_windows(window, centers, means):
    result = run("moving_average", samples=[0, 0, 9, 0, 0], window=window)
    assert result["plot"]["x"] == centers
    assert result["plot"]["y"] == pytest.approx(means)


def test_large_finite_mean():
    result = run("moving_average", samples=[1e308] * 3, window=3)
    assert result["plot"]["y"] == pytest.approx([1e308])


@pytest.mark.parametrize("n", [2, 8, 16, 255, 256])
def test_single_sided_dc_and_nyquist_scaling(n):
    if n % 2:
        inputs = [2 * math.cos(math.tau * (n // 2) * i / n + 0.31) + 3 for i in range(n)]
    else:
        inputs = [3 + 2 * (-1) ** i for i in range(n)]
    result = run("amplitude_spectrum", samples=inputs, sample_rate_hz=2 * n)
    expected = [3] + [0] * (n // 2 - 1) + [2]
    assert result["plot"]["y"] == pytest.approx(expected, abs=1e-12)
    assert result["plot"]["x"] == pytest.approx([2 * k for k in range(n // 2 + 1)])
    assert result["values"]["peak_bin_hz"] == 0


def test_dc_and_bin_centered_phase_shifted_real_tone():
    n = 32
    samples = [-2 + 5 * math.sin(math.tau * 3 * i / n + 0.75) for i in range(n)]
    result = run("amplitude_spectrum", samples=samples, sample_rate_hz=160)
    assert result["values"] == pytest.approx({"peak_bin_hz": 15, "peak_bin_amplitude": 5, "bin_width_hz": 5})
    assert result["plot"]["y"][0] == pytest.approx(2)


def test_resample_preserves_exact_knots_repeated_targets_and_table_order():
    result = run("linear_resample", coordinates=[0, 1, 4], samples=[4, 10, 4], targets=[4, 0, 2.5, 0.5, 1, 1])
    assert result["table"]["rows"] == [[4, 4], [0, 4], [2.5, 7], [0.5, 7], [1, 10], [1, 10]]
    assert result["plot"]["x"] == [0, 0.5, 1, 1, 2.5, 4]


def test_resample_wide_signed_coordinate_interval():
    result = run("linear_resample", coordinates=[-1e308, 1e308], samples=[-1e308, 1e308], targets=[0])
    assert result["table"]["rows"] == [[0, 0]]


@pytest.mark.parametrize(
    ("name", "inputs"),
    [
        ("moving_average", {"samples": [1, 2, 3], "window": 2}),
        ("moving_average", {"samples": [1, 2], "window": 3}),
        ("moving_average", {"samples": [1, 2], "window": -1}),
        ("amplitude_spectrum", {"samples": [1] * 257, "sample_rate_hz": 1}),
        ("amplitude_spectrum", {"samples": [1], "sample_rate_hz": 1}),
        ("amplitude_spectrum", {"samples": [1, 2], "sample_rate_hz": 0}),
        ("linear_resample", {"coordinates": [0, 0], "samples": [1, 2], "targets": [0]}),
        ("linear_resample", {"coordinates": [0, 1], "samples": [1, 2], "targets": [-1]}),
        ("linear_resample", {"coordinates": [0, 1], "samples": [1], "targets": [1]}),
    ],
)
def test_invalid_signals(name, inputs):
    with pytest.raises(ValueError):
        run(name, **inputs)
