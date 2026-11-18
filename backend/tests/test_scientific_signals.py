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


# The three original operation IDs, their examples and stored result schema are
# unchanged. New methods are append-only; old saved receipts remain readable.
def test_original_operation_ids_and_results_are_unchanged():
    assert [op.id for op in OPERATIONS[:3]] == ["moving_average", "amplitude_spectrum", "linear_resample"]
    for operation in OPERATIONS[:3]:
        result = run_operation("signals", operation, operation.example)
        assert result["operationId"] == operation.id
        assert result["provenance"]["packId"] == "signals"
        assert result["provenance"]["packVersion"] == "1.1.0"


def sampled(function, size, rate):
    return {"samples": [function(i) for i in range(size)],
            "sample_times_s": [i / rate for i in range(size)]}


@pytest.mark.parametrize("window,coherent_gain,mean_square", [
    ("rectangular", 1.0, 1.0),
    ("hann", 0.5, 3 / 8),
    ("hamming", 0.54, 0.3974),
    ("blackman", 0.42, 0.3046),
])
@pytest.mark.parametrize("size", [63, 64, 128])
def test_fft_published_periodic_window_gains_and_bin_centered_tone(size, window, coherent_gain, mean_square):
    rate = 2 * size
    data = sampled(lambda i: 1.25 + 3.5 * math.cos(math.tau * 7 * i / size + 0.29), size, rate)
    output = run("windowed_fft_spectrum", **data, window=window)
    assert output["values"]["coherent_gain"] == pytest.approx(coherent_gain, abs=1e-14)
    assert output["values"]["window_mean_square"] == pytest.approx(mean_square, abs=1e-14)
    assert output["values"]["peak_frequency_hz"] == 14
    assert output["values"]["peak_amplitude"] == pytest.approx(3.5, rel=1e-12)
    assert output["table"]["rows"][0][1] == pytest.approx(1.25, abs=1e-12)


@pytest.mark.parametrize("size", [15, 16, 31, 32, 1024])
@pytest.mark.parametrize("window", ["rectangular", "hann", "hamming", "blackman"])
def test_fft_parseval_weighted_mean_square_and_density_integral(size, window):
    # Parseval: sum(one-sided bin powers) == sum((samples*window)**2)/sum(window**2).
    from graphlink_plugins.scientific.packs.signals import _window

    data = sampled(lambda i: 0.75 + math.sin(math.tau * 3 * i / size) + (i % 3 - 1) / 7,
                   size, 100)
    output = run("windowed_fft_spectrum", **data, window=window)
    weights = _window(window, size)
    mean_square = (math.fsum((x * w) ** 2 for x, w in zip(data["samples"], weights))
                   / math.fsum(w * w for w in weights))
    delta_hz = output["values"]["bin_width_hz"]
    assert output["values"]["total_power"] == pytest.approx(mean_square, rel=2e-11)
    assert math.fsum(row[3] * delta_hz for row in output["table"]["rows"]) == pytest.approx(
        mean_square, rel=2e-11)
    if window == "rectangular":
        assert mean_square == pytest.approx(math.fsum(x * x for x in data["samples"]) / size)


def test_fft_even_nyquist_and_no_extra_doubling():
    data = sampled(lambda i: 2.0 * (-1) ** i, 32, 64)
    for window in ("rectangular", "hann", "hamming", "blackman"):
        output = run("windowed_fft_spectrum", **data, window=window)
        assert output["table"]["rows"][-1][:2] == pytest.approx([32, 2], abs=1e-12)
        assert output["table"]["rows"][-1][2] == pytest.approx(4 / ({
            "rectangular": 1, "hann": 1.5, "hamming": 0.3974 / 0.54 ** 2,
            "blackman": 0.3046 / 0.42 ** 2,
        }[window]), rel=1e-12)


def test_non_power_of_two_fft_matches_independent_direct_dft():
    data = sampled(lambda i: math.sin(i * 0.3) + 0.25 * math.cos(i * 0.87), 37, 77)
    output = run("windowed_fft_spectrum", **data, window="rectangular")
    for k, (_, amplitude, _, _) in enumerate(output["table"]["rows"]):
        cos_part = math.fsum(value * math.cos(math.tau * k * i / 37)
                             for i, value in enumerate(data["samples"]))
        sin_part = math.fsum(value * math.sin(math.tau * k * i / 37)
                             for i, value in enumerate(data["samples"]))
        expected = math.hypot(cos_part, sin_part) * (1 if k == 0 else 2) / 37
        assert amplitude == pytest.approx(expected, abs=1e-12)


def test_welch_rectangular_reference_psd_and_integrated_power():
    # For a 2-amplitude sine and Fs=64, nperseg=16: 2 units² in the tone bin,
    # Δf=4 Hz, so the one-sided density at 16 Hz is 0.5 units²/Hz.
    data = sampled(lambda i: 2 * math.sin(math.tau * 4 * i / 16), 64, 64)
    output = run("welch_psd", **data, window="rectangular", segment_samples=16, overlap_samples=0)
    assert output["values"] == pytest.approx({
        "segments": 4, "segment_samples": 16, "bin_width_hz": 4,
        "equivalent_noise_bandwidth_hz": 4, "peak_frequency_hz": 16,
        "peak_psd": 0.5, "integrated_power": 2,
    })
    assert output["plot"]["y"][4] == pytest.approx(0.5, abs=1e-12)


def test_welch_overlap_detrending_and_hann_published_enbw():
    data = sampled(lambda i: 8 + 2 * math.sin(math.tau * 4 * i / 32), 96, 64)
    output = run("welch_psd", **data, window="hann", segment_samples=32, overlap_samples=16)
    assert output["values"]["segments"] == 5
    assert output["values"]["equivalent_noise_bandwidth_hz"] == pytest.approx(3)
    assert output["values"]["peak_frequency_hz"] == pytest.approx(8)
    assert output["values"]["peak_psd"] == pytest.approx(2 / 3, abs=1e-12)
    assert output["values"]["integrated_power"] == pytest.approx(2, abs=1e-12)
    dc = run("welch_psd", **sampled(lambda _: 5, 64, 64), window="blackman",
             segment_samples=32, overlap_samples=16)
    assert dc["plot"]["y"] == pytest.approx([0] * 17, abs=1e-12)


@pytest.mark.parametrize("kind,cutoffs", [
    ("lowpass", [10]), ("highpass", [10]), ("bandpass", [10, 30]),
])
@pytest.mark.parametrize("order", [1, 2, 3, 4, 6])
def test_butterworth_published_minus_three_decibel_cutoffs(kind, cutoffs, order):
    # SciPy signal.butter docs define Wn at |H| = 1/sqrt(2) for a single pass.
    from graphlink_plugins.scientific.packs.signals import _butterworth_sos, _sos_gain

    sos = _butterworth_sos(kind, order, cutoffs, 100)
    for cutoff in cutoffs:
        assert _sos_gain(sos, cutoff, 100) == pytest.approx(1 / math.sqrt(2), abs=2e-11)
    if kind == "lowpass":
        assert _sos_gain(sos, 0, 100) == pytest.approx(1, abs=1e-12)
    if kind == "highpass":
        assert _sos_gain(sos, 50, 100) == pytest.approx(1, abs=1e-12)
    if kind == "bandpass":
        assert _sos_gain(sos, 0, 100) == pytest.approx(0, abs=1e-12)
        assert _sos_gain(sos, 50, 100) == pytest.approx(0, abs=1e-12)


def test_butterworth_zero_phase_cutoff_is_minus_six_decibels_after_two_passes():
    size, rate, cutoff = 1024, 128, 16
    samples = sampled(lambda i: 4 * math.sin(math.tau * cutoff * i / rate + 0.4), size, rate)
    output = run("butterworth_filter", **samples, filter_type="lowpass", order=4, cutoff_hz=[cutoff])
    interior = slice(200, -200)
    original = samples["samples"][interior]
    filtered = output["plot"]["y"][interior]
    assert sum(x * y for x, y in zip(original, filtered)) / sum(x * x for x in original) == pytest.approx(
        0.5, abs=2e-3)
    assert output["values"]["effective_order"] == 8


def test_butterworth_bandpass_keeps_in_band_and_suppresses_out_of_band():
    data = sampled(lambda i: (math.sin(math.tau * 12 * i / 128) +
                               math.sin(math.tau * 35 * i / 128)), 512, 128)
    output = run("butterworth_filter", **data, filter_type="bandpass", order=4, cutoff_hz=[8, 18])
    actual = output["plot"]["y"][100:-100]
    indices = range(100, 412)
    desired = [math.sin(math.tau * 12 * i / 128) for i in indices]
    undesired = [math.sin(math.tau * 35 * i / 128) for i in indices]
    desired_gain = sum(x * y for x, y in zip(actual, desired)) / sum(x * x for x in desired)
    undesired_gain = sum(x * y for x, y in zip(actual, undesired)) / sum(x * x for x in undesired)
    assert desired_gain > 0.9
    assert abs(undesired_gain) < 0.01
    assert output["values"]["effective_order"] == 16


def test_savitzky_golay_preserves_polynomial_including_edges():
    data = sampled(lambda i: 3 - 2 * i + 0.5 * i ** 2 - 0.01 * i ** 3, 21, 4)
    output = run("savitzky_golay", **data, window_samples=7, polynomial_order=3)
    assert output["plot"]["y"] == pytest.approx(data["samples"], abs=1e-10)


def test_savitzky_golay_five_point_quadratic_published_coefficients():
    # The classic 5-point quadratic center weights are [-3, 12, 17, 12, -3]/35.
    data = sampled(lambda i: [0, 0, 10, 0, 0][i], 5, 1)
    output = run("savitzky_golay", **data, window_samples=5, polynomial_order=2)
    assert output["plot"]["y"][2] == pytest.approx(34 / 7, abs=1e-12)


def test_peaks_prominence_plateaus_spacing_and_no_peaks():
    data = sampled(lambda i: [0, 4, 2, 3, 1, 5, 0, 2, 2, 2, 0][i], 11, 2)
    result = run("detect_peaks", **data, minimum_height=2,
                 minimum_prominence=1, minimum_spacing_samples=1)
    assert result["table"]["rows"] == [
        [1, 0.5, 4, 3], [3, 1.5, 3, 1], [5, 2.5, 5, 5], [8, 4, 2, 2],
    ]
    spaced = run("detect_peaks", **data, minimum_height=2,
                 minimum_prominence=1, minimum_spacing_samples=3)
    assert [row[0] for row in spaced["table"]["rows"]] == [1, 5, 8]
    empty = run("detect_peaks", **data, minimum_height=6,
                minimum_prominence=0, minimum_spacing_samples=1)
    assert empty["values"]["peaks_detected"] == 0
    assert empty["table"]["rows"] == []
    assert empty["plot"]["y"] == data["samples"]


def test_peaks_even_plateau_chooses_left_center_and_equal_height_spacing_is_deterministic():
    data = sampled(lambda i: [0, 3, 3, 0, 3, 0][i], 6, 1)
    output = run("detect_peaks", **data, minimum_height=3,
                 minimum_prominence=2, minimum_spacing_samples=4)
    assert [row[0] for row in output["table"]["rows"]] == [1]


@pytest.mark.parametrize("operation", ["windowed_fft_spectrum", "welch_psd", "butterworth_filter",
                                      "savitzky_golay", "detect_peaks"])
def test_new_operations_reject_nonuniform_sampling_and_mismatched_times(operation):
    example = dict(next(op.example for op in OPERATIONS if op.id == operation))
    example["sample_times_s"] = list(example["sample_times_s"])
    example["sample_times_s"][-1] += 0.015625
    with pytest.raises(ValueError, match="Non-uniform sampling"):
        run(operation, **example)
    example["sample_times_s"].pop()
    with pytest.raises(ValueError, match="same length"):
        run(operation, **example)


@pytest.mark.parametrize("name,changes,error", [
    ("windowed_fft_spectrum", {"samples": [1, 2, 3], "sample_times_s": [0, 1, 2]}, "At least 4"),
    ("windowed_fft_spectrum", {"window": "triangular"}, "Window must"),
    ("welch_psd", {"samples": [1] * 7, "sample_times_s": list(range(7))}, "At least 8"),
    ("welch_psd", {"segment_samples": 100}, "segment length"),
    ("welch_psd", {"overlap_samples": 8}, "overlap"),
    ("butterworth_filter", {"order": 9}, "order"),
    ("butterworth_filter", {"order": 0}, "order"),
    ("butterworth_filter", {"cutoff_hz": [32]}, "Nyquist"),
    ("butterworth_filter", {"cutoff_hz": [0]}, "Nyquist"),
    ("butterworth_filter", {"cutoff_hz": [1, 2]}, "one for"),
    ("butterworth_filter", {"filter_type": "bandpass", "cutoff_hz": [5, 4]}, "increasing"),
    ("butterworth_filter", {"filter_type": "bandpass", "cutoff_hz": [5, 10], "order": 7}, "order"),
    ("butterworth_filter", {"samples": [0] * 8, "sample_times_s": list(range(8)),
                            "cutoff_hz": [0.1]}, "at least"),
    ("savitzky_golay", {"window_samples": 4}, "odd"),
    ("savitzky_golay", {"window_samples": 11}, "no longer"),
    ("savitzky_golay", {"polynomial_order": 7}, "order"),
    ("savitzky_golay", {"polynomial_order": 5}, "smaller"),
    ("detect_peaks", {"minimum_prominence": -1}, "nonnegative"),
    ("detect_peaks", {"minimum_spacing_samples": 0}, "at least one"),
    ("detect_peaks", {"samples": [1, 2], "sample_times_s": [0, 1]}, "At least 3"),
])
def test_new_operations_reject_invalid_parameters(name, changes, error):
    inputs = dict(next(op.example for op in OPERATIONS if op.id == name))
    inputs.update(changes)
    with pytest.raises(ValueError, match=error):
        run(name, **inputs)


def test_all_new_operations_produce_valid_saved_results():
    for operation in OPERATIONS[3:]:
        result = run_operation("signals", operation, operation.example)
        assert result["operationId"] == operation.id
        assert result["provenance"]["resultHash"]
        assert result["provenance"]["receiptHash"]
        assert len(result["plot"]["x"]) == len(result["plot"]["y"])
