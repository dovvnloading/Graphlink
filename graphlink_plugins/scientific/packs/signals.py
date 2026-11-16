"""Local signal summaries for uniformly sampled series and irregular observations."""

import math
from bisect import bisect_left

from graphlink_plugins.scientific.core import Field, Operation


def moving_average(data):
    samples, width = data["samples"], data["window"]
    if not 1 <= width <= len(samples) or width % 2 == 0:
        raise ValueError("Choose a positive odd window no longer than the input series.")
    half = width // 2
    centers = list(range(half, len(samples) - half))
    # Scale before summing to avoid overflowing the sum of large finite samples.
    scaled = [value / width for value in samples]
    means = [math.fsum(scaled[i - half : i + half + 1]) for i in centers]
    return {
        "summary": f"{len(means)} complete centered window(s) of {width} sample(s).",
        "values": {"window_samples": width, "windows": len(means)},
        "table": {
            "columns": ["Center index (zero-based)", "Mean amplitude"],
            "rows": [list(row) for row in zip(centers, means)],
        },
        "plot": {
            "title": "Centered moving average",
            "xLabel": "Sample index (zero-based)",
            "yLabel": "Mean amplitude (input units)",
            "x": centers,
            "y": means,
        },
    }


def amplitude_spectrum(data):
    samples, rate = data["samples"], data["sample_rate_hz"]
    n = len(samples)
    if not 2 <= n <= 256:
        raise ValueError("Provide 2–256 real samples for the direct Fourier transform.")
    if rate <= 0:
        raise ValueError("Sample rate must be positive.")
    scaled = [value / n for value in samples]
    # Periodic table prevents large phase arguments in the direct O(N²) sum.
    cosines = [math.cos(math.tau * j / n) for j in range(n)]
    sines = [math.sin(math.tau * j / n) for j in range(n)]
    bins = list(range(n // 2 + 1))
    frequencies = [(k / n) * rate for k in bins]
    amplitudes = []
    for k in bins:
        if k == 0:
            amplitude = abs(math.fsum(scaled))
        elif 2 * k == n:
            # Nyquist is a single real coefficient for even N.
            amplitude = abs(math.fsum(s if i % 2 == 0 else -s for i, s in enumerate(scaled)))
        else:
            real = math.fsum(value * cosines[(k * i) % n] for i, value in enumerate(scaled))
            imag = math.fsum(value * sines[(k * i) % n] for i, value in enumerate(scaled))
            amplitude = 2 * math.hypot(real, imag)
        amplitudes.append(amplitude)
    peak_bin = max(bins, key=amplitudes.__getitem__)
    return {
        "summary": f"Peak measured bin is {frequencies[peak_bin]:.6g} Hz with amplitude {amplitudes[peak_bin]:.6g}.",
        "values": {
            "bin_width_hz": rate / n,
            "peak_bin_hz": frequencies[peak_bin],
            "peak_bin_amplitude": amplitudes[peak_bin],
        },
        "table": {
            "columns": ["Frequency (Hz)", "One-sided amplitude"],
            "rows": [list(row) for row in zip(frequencies, amplitudes)],
        },
        "plot": {
            "title": "One-sided amplitude spectrum",
            "xLabel": "Frequency (Hz)",
            "yLabel": "Peak amplitude (input units)",
            "x": frequencies,
            "y": amplitudes,
        },
    }


def linear_resample(data):
    coordinates = data["coordinates"]
    values, targets = data["samples"], data["targets"]
    if len(coordinates) < 2 or len(values) != len(coordinates):
        raise ValueError("Provide at least two paired observation coordinates and samples.")
    if any(a >= b for a, b in zip(coordinates, coordinates[1:])):
        raise ValueError("Observation coordinates must be strictly increasing.")
    if any(x < coordinates[0] or x > coordinates[-1] for x in targets):
        raise ValueError("Target coordinates must lie within the observed interval.")
    rows = []
    for target in targets:
        i = bisect_left(coordinates, target)
        if coordinates[i] == target:
            interpolated = values[i]
        else:
            left, right = coordinates[i - 1], coordinates[i]
            width = right - left
            if math.isfinite(width):
                weight = (target - left) / width
            else:
                weight = (target / 2 - left / 2) / (right / 2 - left / 2)
            interpolated = (1 - weight) * values[i - 1] + weight * values[i]
        rows.append([target, interpolated])
    ordered = sorted(rows, key=lambda row: row[0])
    return {
        "summary": f"Interpolated {len(rows)} sample(s) from {len(values)} observed positions.",
        "values": {"observations": len(values), "requested_samples": len(rows)},
        "table": {"columns": ["Target coordinate", "Interpolated amplitude"], "rows": rows},
        "plot": {
            "title": "Linearly resampled observations",
            "xLabel": "Coordinate (input units)",
            "yLabel": "Amplitude (input units)",
            "x": [r[0] for r in ordered],
            "y": [r[1] for r in ordered],
        },
    }


OPERATIONS = (
    Operation(
        "moving_average",
        "Centered moving average",
        "Smooth uniformly spaced samples using equal weights in complete odd-width windows. "
        "The zero-based index marks each window center; edge windows are omitted.",
        (
            Field("samples", "Samples in observation order", "vector"),
            Field("window", "Window width", "integer", unit="samples"),
        ),
        {"samples": [2, 3, 8, 4, 3, 5, 7], "window": 3},
        moving_average,
    ),
    Operation(
        "amplitude_spectrum",
        "One-sided amplitude spectrum",
        "Use a direct real-input Fourier transform on 2–256 uniformly spaced samples with a "
        "rectangular window. Peak amplitudes include the DC component. Frequencies between bins "
        "spread over neighboring bins.",
        (
            Field("samples", "Uniformly sampled amplitudes", "vector"),
            Field("sample_rate_hz", "Sample rate", "number", unit="Hz"),
        ),
        {"samples": [1, 0, -1, 0, 1, 0, -1, 0], "sample_rate_hz": 8},
        amplitude_spectrum,
    ),
    Operation(
        "linear_resample",
        "Piecewise linear resampling",
        "Interpolate paired observations at target coordinates within their span. The table "
        "preserves target order and the plot sorts by coordinate. Use matching units for both coordinate lists.",
        (
            Field("coordinates", "Observation coordinates, increasing", "vector"),
            Field("samples", "Observed amplitudes", "vector"),
            Field("targets", "Requested sample coordinates", "vector"),
        ),
        {"coordinates": [0, 2, 3.5], "samples": [1, 5, 2], "targets": [0, 1, 2, 2.5, 3.5]},
        linear_resample,
    ),
)
