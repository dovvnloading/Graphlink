"""Local signal summaries for uniformly sampled series and irregular observations."""

import math
from bisect import bisect_left
from fractions import Fraction
from itertools import accumulate

from graphlink_plugins.scientific.core import Field, Operation


def moving_average(data):
    samples, width = data["samples"], data["window"]
    if not 1 <= width <= len(samples) or width % 2 == 0:
        raise ValueError("Choose a positive odd window no longer than the input series.")
    half = width // 2
    centers = list(range(half, len(samples) - half))
    # Exact prefix sums preserve subnormals and cancellation in sliding differences.
    prefix = list(accumulate(map(Fraction, samples), initial=Fraction(0)))
    means = [float((right - left) / width) for left, right in zip(prefix, prefix[width:])]
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
    if rate / n == 0:
        raise ValueError("Frequency-bin spacing is below the supported numerical range.")
    exact = [Fraction(value) for value in samples]
    mean = sum(exact) / n
    # Subtract the DC component before projecting onto a sinusoid. A constant
    # input then has exactly zero non-DC amplitude, even at large DC offsets.
    deviations = [value - mean for value in exact]
    scale = max(map(abs, deviations))
    normalized = [float(value / scale) for value in deviations] if scale else [0.0] * n
    # Periodic table prevents large phase arguments in the direct O(N²) sum.
    cosines = [math.cos(math.tau * j / n) for j in range(n)]
    sines = [math.sin(math.tau * j / n) for j in range(n)]
    bins = list(range(n // 2 + 1))
    frequencies = [(k / n) * rate for k in bins]
    amplitudes = []
    for k in bins:
        if k == 0:
            amplitude = float(abs(mean))
        elif 2 * k == n:
            # Nyquist is a single real coefficient for even N.
            amplitude = float(abs(sum(exact[::2]) - sum(exact[1::2])) / n)
        else:
            real = math.fsum(value * cosines[(k * i) % n] for i, value in enumerate(normalized))
            imag = math.fsum(value * sines[(k * i) % n] for i, value in enumerate(normalized))
            amplitude = float(Fraction(math.hypot(real, imag)) * scale * 2 / n)
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
            left, right = Fraction(coordinates[i - 1]), Fraction(coordinates[i])
            point = Fraction(target)
            # Calculate both weights explicitly to resolve a target close to either endpoint.
            weighted = (right - point) * Fraction(values[i - 1]) + (point - left) * Fraction(values[i])
            interpolated = float(weighted / (right - left))
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


# Extended signal operations take measured sample times so a non-uniform record
# cannot silently be analyzed using uniformly spaced FFT/filter assumptions.
def _sampling_rate(samples, times, minimum):
    if len(samples) != len(times):
        raise ValueError("Samples and sample times must have the same length.")
    if len(samples) < minimum:
        raise ValueError(f"At least {minimum} samples are required for this operation.")
    spacing = times[1] - times[0]
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("Sample times must be strictly increasing and uniformly spaced.")
    for earlier, later in zip(times[1:], times[2:]):
        step = later - earlier
        if not math.isfinite(step) or step <= 0 or not math.isclose(step, spacing, rel_tol=1e-8):
            raise ValueError("Non-uniform sampling: sample times must have equal positive spacing.")
    rate = 1.0 / spacing
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("The sample spacing produces an unsupported sampling rate.")
    return rate


def _window(name, length):
    kind = name.strip().lower()
    if kind not in ("rectangular", "hann", "hamming", "blackman"):
        raise ValueError("Window must be rectangular, Hann, Hamming, or Blackman.")
    if kind == "rectangular":
        return [1.0] * length
    # Periodic/DFT-even windows: denominator N, not N-1. This is the
    # convention of SciPy's fftbins=True (and its Welch default).
    coefficients = {"hann": (0.5, 0.5, 0), "hamming": (0.54, 0.46, 0),
                    "blackman": (0.42, 0.5, 0.08)}
    a0, a1, a2 = coefficients[kind]
    return [a0 - a1 * math.cos(math.tau * index / length)
            + a2 * math.cos(2 * math.tau * index / length) for index in range(length)]


def _fft_radix2(values, inverse=False):
    """Iterative Cooley–Tukey FFT for a power-of-two length."""
    size = len(values)
    output = [complex(x) for x in values]
    j = 0
    for index in range(1, size):
        bit = size >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if index < j:
            output[index], output[j] = output[j], output[index]
    block = 2
    while block <= size:
        angle = (1 if inverse else -1) * math.tau / block
        rotation = complex(math.cos(angle), math.sin(angle))
        for start in range(0, size, block):
            factor = 1 + 0j
            half = block // 2
            for offset in range(half):
                even = output[start + offset]
                odd = factor * output[start + offset + half]
                output[start + offset] = even + odd
                output[start + offset + half] = even - odd
                factor *= rotation
        block *= 2
    return [value / size for value in output] if inverse else output


def _fft(values):
    """Unnormalized forward FFT for any length, using Bluestein otherwise."""
    size = len(values)
    if not size & (size - 1):
        return _fft_radix2(values)
    padded = 1 << (2 * size - 2).bit_length()
    signal = [0j] * padded
    kernel = [0j] * padded
    for index, value in enumerate(values):
        angle = math.pi * (index * index % (2 * size)) / size
        chirp = complex(math.cos(angle), -math.sin(angle))
        signal[index] = value * chirp
        kernel[index] = chirp.conjugate()
        if index:
            kernel[padded - index] = chirp.conjugate()
    transformed = _fft_radix2(signal)
    kernel_fft = _fft_radix2(kernel)
    convolution = _fft_radix2([a * b for a, b in zip(transformed, kernel_fft)], inverse=True)
    return [convolution[index] * complex(
        math.cos(math.pi * (index * index % (2 * size)) / size),
        -math.sin(math.pi * (index * index % (2 * size)) / size))
        for index in range(size)]


def _one_sided_transform(samples, weights, sample_rate):
    """Coherent-gain amplitude and window-energy-normalized power/PSD."""
    length = len(samples)
    sum_weights = math.fsum(weights)
    sum_squared = math.fsum(weight * weight for weight in weights)
    transform = _fft([sample * weight for sample, weight in zip(samples, weights)])
    frequencies, amplitudes, powers, densities = [], [], [], []
    for bin_index in range(length // 2 + 1):
        factor = 1 if bin_index == 0 or 2 * bin_index == length else 2
        magnitude = abs(transform[bin_index])
        frequency = (bin_index / length) * sample_rate
        amplitude = factor * magnitude / sum_weights
        power = factor * (magnitude / length) ** 2 * length / sum_squared
        density = power / (sample_rate / length)
        if not all(map(math.isfinite, (amplitude, power, density, frequency))):
            raise ValueError("The signal's spectrum exceeds the supported numerical range.")
        frequencies.append(frequency)
        amplitudes.append(amplitude)
        powers.append(power)
        densities.append(density)
    return frequencies, amplitudes, powers, densities


def windowed_fft_spectrum(data):
    samples, times = data["samples"], data["sample_times_s"]
    sample_rate = _sampling_rate(samples, times, 4)
    weights = _window(data["window"], len(samples))
    frequencies, amplitudes, powers, densities = _one_sided_transform(samples, weights, sample_rate)
    peak = max(range(len(amplitudes)), key=amplitudes.__getitem__)
    total_power = math.fsum(powers)
    return {
        "summary": (f"{data['window'].strip().lower()} window: peak at {frequencies[peak]:.6g} Hz, "
                    f"{amplitudes[peak]:.6g} amplitude units."),
        "values": {
            "bin_width_hz": sample_rate / len(samples),
            "coherent_gain": math.fsum(weights) / len(weights),
            "window_mean_square": math.fsum(w * w for w in weights) / len(weights),
            "peak_frequency_hz": frequencies[peak],
            "peak_amplitude": amplitudes[peak],
            "total_power": total_power,
        },
        "table": {
            "columns": ["Frequency (Hz)", "One-sided amplitude", "Bin power (input units²)",
                        "PSD (input units²/Hz)"],
            "rows": [list(row) for row in zip(frequencies, amplitudes, powers, densities)],
        },
        "plot": {
            "title": "Windowed one-sided FFT amplitude",
            "xLabel": "Frequency (Hz)", "yLabel": "Peak amplitude (input units)",
            "x": frequencies, "y": amplitudes,
        },
    }


def welch_psd(data):
    samples, times = data["samples"], data["sample_times_s"]
    sample_rate = _sampling_rate(samples, times, 8)
    segment = data["segment_samples"]
    overlap = data["overlap_samples"]
    if segment < 8 or segment > len(samples):
        raise ValueError("Welch segment length must be at least 8 and no longer than the signal.")
    if not 0 <= overlap < segment:
        raise ValueError("Welch overlap must be nonnegative and smaller than the segment length.")
    weights = _window(data["window"], segment)
    step = segment - overlap
    starts = range(0, len(samples) - segment + 1, step)
    summed = [0.0] * (segment // 2 + 1)
    count = 0
    for start in starts:
        block = samples[start:start + segment]
        average = math.fsum(block) / segment
        # Match Welch's conventional constant-detrended modified periodograms.
        centered = [value - average for value in block]
        _, _, _, density = _one_sided_transform(centered, weights, sample_rate)
        summed = [a + b for a, b in zip(summed, density)]
        count += 1
    density = [item / count for item in summed]
    frequencies = [(index / segment) * sample_rate for index in range(len(density))]
    peak = max(range(len(density)), key=density.__getitem__)
    return {
        "summary": f"Welch PSD from {count} complete segment(s) of {segment} samples.",
        "values": {
            "segments": count, "segment_samples": segment,
            "bin_width_hz": sample_rate / segment,
            "equivalent_noise_bandwidth_hz": (sample_rate * math.fsum(w * w for w in weights)
                                             / math.fsum(weights) ** 2),
            "peak_frequency_hz": frequencies[peak], "peak_psd": density[peak],
            "integrated_power": math.fsum(density) * (sample_rate / segment),
        },
        "table": {
            "columns": ["Frequency (Hz)", "Welch PSD (input units²/Hz)"],
            "rows": [list(row) for row in zip(frequencies, density)],
        },
        "plot": {
            "title": "Welch power spectral density", "xLabel": "Frequency (Hz)",
            "yLabel": "Power density (input units²/Hz)", "x": frequencies, "y": density,
        },
    }


def _butterworth_sos(kind, order, cutoffs, sample_rate):
    """Bilinear-transform analog Butterworth poles into stable SOS sections.

    Each section is normalized to unit gain at DC (lowpass), Nyquist
    (highpass), or the prewarped band center (bandpass).
    """
    if kind not in ("lowpass", "highpass", "bandpass"):
        raise ValueError("Butterworth type must be lowpass, highpass, or bandpass.")
    if not 1 <= order <= (6 if kind == "bandpass" else 8):
        raise ValueError("Butterworth order is unstable or unsupported: use 1–8 (1–6 for bandpass).")
    if len(cutoffs) != (2 if kind == "bandpass" else 1):
        raise ValueError("Use two cutoff frequencies for bandpass; one for lowpass or highpass.")
    nyquist = sample_rate / 2
    if any(not 0 < frequency < nyquist for frequency in cutoffs):
        raise ValueError("Butterworth cutoff frequencies must lie strictly between 0 and Nyquist.")
    if len(cutoffs) == 2 and cutoffs[0] >= cutoffs[1]:
        raise ValueError("Bandpass cutoffs must be strictly increasing.")
    rate_2 = 2 * sample_rate
    analog_edges = [rate_2 * math.tan(math.pi * value / sample_rate) for value in cutoffs]
    if not all(math.isfinite(value) and value > 0 for value in analog_edges):
        raise ValueError("Butterworth cutoffs exceed the supported numerical range.")
    prototype = [complex(math.cos(math.pi * (2 * k + 1 + order) / (2 * order)),
                         math.sin(math.pi * (2 * k + 1 + order) / (2 * order)))
                 for k in range(order)]
    if kind == "lowpass":
        analog_poles = [analog_edges[0] * pole for pole in prototype]
        reference = 0.0
    elif kind == "highpass":
        analog_poles = [analog_edges[0] / pole for pole in prototype]
        reference = math.pi
    else:
        bandwidth = analog_edges[1] - analog_edges[0]
        center = math.sqrt(analog_edges[0]) * math.sqrt(analog_edges[1])
        analog_poles = []
        for pole in prototype:
            midpoint = bandwidth * pole / 2
            root = (midpoint * midpoint - center * center) ** 0.5
            analog_poles.extend([midpoint + root, midpoint - root])
        reference = 2 * math.atan(center / rate_2)
    poles = [(rate_2 + pole) / (rate_2 - pole) for pole in analog_poles]
    if any(not math.isfinite(abs(pole)) or abs(pole) >= 1 - 1e-10 for pole in poles):
        raise ValueError("Butterworth poles are too close to instability; reduce order or adjust cutoffs.")
    pending = poles[:]
    sections = []
    while pending:
        pole = pending.pop(0)
        if abs(pole.imag) < 1e-10:
            group = [complex(pole.real)]
        else:
            index = min(range(len(pending)), key=lambda i: abs(pending[i] - pole.conjugate()))
            partner = pending.pop(index)
            if abs(partner - pole.conjugate()) > 1e-7:
                raise ValueError("Butterworth pole pairing is numerically unstable.")
            group = [pole, partner]
        if len(group) == 1:
            a1, a2 = -group[0].real, 0.0
            numerator = [1.0, 1.0 if kind == "lowpass" else -1.0, 0.0]
        else:
            a1, a2 = -(group[0] + group[1]).real, (group[0] * group[1]).real
            numerator = ([1.0, 2.0, 1.0] if kind == "lowpass" else
                         [1.0, -2.0, 1.0] if kind == "highpass" else [1.0, 0.0, -1.0])
        inverse_z = complex(math.cos(reference), -math.sin(reference))
        gain = abs((numerator[0] + numerator[1] * inverse_z + numerator[2] * inverse_z ** 2)
                   / (1 + a1 * inverse_z + a2 * inverse_z ** 2))
        if not math.isfinite(gain) or gain < 1e-12:
            raise ValueError("Butterworth section gain is numerically unstable; adjust cutoffs or order.")
        sections.append((*[item / gain for item in numerator], a1, a2))
    return sections


def _sos_gain(sections, frequency_hz, sample_rate):
    """Single-pass response magnitude, independently testable at cutoffs."""
    phase = -math.tau * frequency_hz / sample_rate
    z_inv = complex(math.cos(phase), math.sin(phase))
    response = 1 + 0j
    for b0, b1, b2, a1, a2 in sections:
        response *= (b0 + b1 * z_inv + b2 * z_inv ** 2) / (1 + a1 * z_inv + a2 * z_inv ** 2)
    return abs(response)


def _run_sos(samples, sections):
    output = list(samples)
    for b0, b1, b2, a1, a2 in sections:
        # Steady-state initial conditions for a constant edge value. Each
        # lowpass section has DC gain 1; each high/bandpass section has 0.
        first = output[0]
        steady = first if b1 > 0 else 0.0
        z1 = steady - b0 * first
        z2 = b2 * first - a2 * steady
        result = []
        for value in output:
            filtered = b0 * value + z1
            z1, z2 = b1 * value - a1 * filtered + z2, b2 * value - a2 * filtered
            if not all(map(math.isfinite, (filtered, z1, z2))):
                raise ValueError("Butterworth filtering exceeds the supported numerical range.")
            result.append(filtered)
        output = result
    return output


def _zero_phase_filter(samples, sections):
    # Odd reflection and steady-state section initialization reduce edge
    # transients. Apply the same filter twice, reversing between passes.
    padding = 3 * (2 * len(sections) + 1 - int(any(b2 == 0 and a2 == 0
                                                   for _, _, b2, _, a2 in sections)))
    if len(samples) <= padding:
        raise ValueError(f"Butterworth zero-phase filtering needs at least {padding + 1} samples.")
    left = [2 * samples[0] - samples[index] for index in range(padding, 0, -1)]
    right = [2 * samples[-1] - samples[-2 - index] for index in range(padding)]
    expanded = left + list(samples) + right
    forward = _run_sos(expanded, sections)
    reverse = _run_sos(forward[::-1], sections)[::-1]
    return reverse[padding:padding + len(samples)]


def butterworth_filter(data):
    samples, times = data["samples"], data["sample_times_s"]
    sample_rate = _sampling_rate(samples, times, 4)
    kind = data["filter_type"].strip().lower()
    sections = _butterworth_sos(kind, data["order"], data["cutoff_hz"], sample_rate)
    filtered = _zero_phase_filter(samples, sections)
    return {
        "summary": (f"Zero-phase {kind} Butterworth filter, prototype order {data['order']} "
                    f"({(4 if kind == 'bandpass' else 2) * data['order']} effective order)."),
        "values": {
            "prototype_order": data["order"], "effective_order": (4 if kind == "bandpass" else 2) * data["order"],
            "sample_rate_hz": sample_rate,
            **({"lower_cutoff_hz": data["cutoff_hz"][0],
                "upper_cutoff_hz": data["cutoff_hz"][1]}
               if kind == "bandpass" else {"cutoff_hz": data["cutoff_hz"][0]}),
        },
        "table": {"columns": ["Time (s)", "Filtered amplitude"],
                  "rows": [list(row) for row in zip(times, filtered)]},
        "plot": {"title": f"Zero-phase Butterworth {kind}", "xLabel": "Time (s)",
                 "yLabel": "Filtered amplitude (input units)", "x": times, "y": filtered},
    }


def _solve_linear(matrix, vector):
    """Small pivoted normal-equation solve for Savitzky–Golay weights."""
    n = len(vector)
    rows = [list(row) + [value] for row, value in zip(matrix, vector)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda i: abs(rows[i][column]))
        if abs(rows[pivot][column]) < 1e-12:
            raise ValueError("Savitzky–Golay polynomial fit is ill-conditioned; reduce polynomial order.")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        for index in range(column, n + 1):
            rows[column][index] /= divisor
        for index in range(n):
            if index == column:
                continue
            factor = rows[index][column]
            for j in range(column, n + 1):
                rows[index][j] -= factor * rows[column][j]
    return [row[-1] for row in rows]


def _savgol_weights(width, polynomial_order, position):
    half = width // 2
    positions = [(index - half) / half for index in range(width)]
    powers = [[position ** degree for degree in range(polynomial_order + 1)]
              for position in positions]
    gram = [[math.fsum(row[i] * row[j] for row in powers)
             for j in range(polynomial_order + 1)] for i in range(polynomial_order + 1)]
    coefficients = _solve_linear(gram, [position ** p for p in range(polynomial_order + 1)])
    return [math.fsum(c * x for c, x in zip(coefficients, row)) for row in powers]


def savitzky_golay(data):
    samples, times = data["samples"], data["sample_times_s"]
    _sampling_rate(samples, times, 3)
    width, degree = data["window_samples"], data["polynomial_order"]
    if width < 3 or width % 2 == 0 or width > len(samples):
        raise ValueError("Savitzky–Golay window must be odd, at least 3, and no longer than the signal.")
    if not 0 <= degree < width or degree > 6:
        raise ValueError("Savitzky–Golay polynomial order must be 0–6 and smaller than the window.")
    half = width // 2
    weights = {offset: _savgol_weights(width, degree, offset / half)
               for offset in range(-half, half + 1)}
    filtered = []
    for index in range(len(samples)):
        start = max(0, min(index - half, len(samples) - width))
        offset = index - start - half
        filtered.append(math.fsum(w * x for w, x in zip(weights[offset], samples[start:start + width])))
    return {
        "summary": f"Savitzky–Golay fit with {width}-sample window and degree {degree}.",
        "values": {"window_samples": width, "polynomial_order": degree,
                   "output_samples": len(filtered)},
        "table": {"columns": ["Time (s)", "Smoothed amplitude"],
                  "rows": [list(row) for row in zip(times, filtered)]},
        "plot": {"title": "Savitzky–Golay smoothing", "xLabel": "Time (s)",
                 "yLabel": "Smoothed amplitude (input units)", "x": times, "y": filtered},
    }


def _peak_candidates(samples):
    """Strict local maxima; flat-top maxima use their left-of-center index."""
    candidates = []
    index = 1
    while index < len(samples) - 1:
        end = index
        while end + 1 < len(samples) and samples[end + 1] == samples[index]:
            end += 1
        if end < len(samples) - 1 and samples[index] > samples[index - 1] and samples[end] > samples[end + 1]:
            candidates.append((index + end) // 2)
        index = end + 1
    return candidates


def _prominence(samples, peak):
    height = samples[peak]
    lowest_left = height
    for index in range(peak - 1, -1, -1):
        if samples[index] > height:
            break
        lowest_left = min(lowest_left, samples[index])
    lowest_right = height
    for index in range(peak + 1, len(samples)):
        if samples[index] > height:
            break
        lowest_right = min(lowest_right, samples[index])
    return height - max(lowest_left, lowest_right)


def detect_peaks(data):
    samples, times = data["samples"], data["sample_times_s"]
    _sampling_rate(samples, times, 3)
    height = data["minimum_height"]
    prominence = data["minimum_prominence"]
    spacing = data["minimum_spacing_samples"]
    if prominence < 0:
        raise ValueError("Minimum peak prominence must be nonnegative.")
    if spacing < 1:
        raise ValueError("Minimum peak spacing must be at least one sample.")
    candidates = [(index, _prominence(samples, index)) for index in _peak_candidates(samples)
                  if samples[index] >= height]
    candidates = [(index, value) for index, value in candidates if value >= prominence]
    selected = []
    # Highest peaks win spacing conflicts; equal-height ties go to the lower index.
    for index, value in sorted(candidates, key=lambda entry: (-samples[entry[0]], entry[0])):
        if all(abs(index - accepted[0]) >= spacing for accepted in selected):
            selected.append((index, value))
    selected.sort()
    rows = [[index, times[index], samples[index], value] for index, value in selected]
    return {
        "summary": f"Detected {len(rows)} peak(s) meeting height, prominence, and spacing thresholds.",
        "values": {"peaks_detected": len(rows), "minimum_spacing_samples": spacing},
        "table": {"columns": ["Peak index", "Time (s)", "Height", "Prominence"],
                  "rows": rows},
        "plot": {"title": "Signal with detected peaks in table", "xLabel": "Time (s)",
                 "yLabel": "Amplitude (input units)", "x": times, "y": samples},
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

    Operation(
        "windowed_fft_spectrum",
        "Windowed FFT amplitude and power spectrum",
        "For uniformly timed samples, calculate a one-sided FFT using rectangular, Hann, Hamming, "
        "or Blackman windows. Amplitude is corrected by coherent gain; bin power and PSD "
        "are corrected by window mean-square energy. DC/Nyquist are not doubled.",
        (
            Field("samples", "Measured amplitudes", "vector"),
            Field("sample_times_s", "Uniform sample times", "vector", unit="s"),
            Field("window", "Window (rectangular, hann, hamming, blackman)", "text"),
        ),
        {"samples": [1, 0, -1, 0, 1, 0, -1, 0],
         "sample_times_s": [i / 8 for i in range(8)], "window": "hann"},
        windowed_fft_spectrum,
    ),
    Operation(
        "welch_psd",
        "Welch power spectral density",
        "Average one-sided, constant-detrended modified periodograms of complete overlapping "
        "segments. PSD is in amplitude squared per Hz; overlap is in samples.",
        (
            Field("samples", "Measured amplitudes", "vector"),
            Field("sample_times_s", "Uniform sample times", "vector", unit="s"),
            Field("window", "Window (rectangular, hann, hamming, blackman)", "text"),
            Field("segment_samples", "Segment length", "integer", unit="samples"),
            Field("overlap_samples", "Overlap", "integer", unit="samples"),
        ),
        {"samples": [1, 0, -1, 0] * 4, "sample_times_s": [i / 8 for i in range(16)],
         "window": "hann", "segment_samples": 8, "overlap_samples": 4},
        welch_psd,
    ),
    Operation(
        "butterworth_filter",
        "Zero-phase Butterworth filter",
        "Design Butterworth lowpass, highpass, or bandpass sections by prewarped bilinear "
        "transform and apply forward-backward filtering with odd-reflected edges. "
        "One cutoff for low/high; two ascending cutoffs for bandpass, both in Hz. "
        "The single-pass -3 dB cutoff becomes -6 dB after forward-backward filtering.",
        (
            Field("samples", "Measured amplitudes", "vector"),
            Field("sample_times_s", "Uniform sample times", "vector", unit="s"),
            Field("filter_type", "Type (lowpass, highpass, bandpass)", "text"),
            Field("order", "Butterworth prototype order", "integer"),
            Field("cutoff_hz", "Cutoff frequency/frequencies", "vector", unit="Hz"),
        ),
        {"samples": [math.sin(math.tau * 3 * i / 64) + 0.3 * math.sin(math.tau * 20 * i / 64)
                     for i in range(64)],
         "sample_times_s": [i / 64 for i in range(64)],
         "filter_type": "lowpass", "order": 4, "cutoff_hz": [8]},
        butterworth_filter,
    ),
    Operation(
        "savitzky_golay",
        "Savitzky–Golay smoothing",
        "Fit a local polynomial on each odd-width window and evaluate at the center. "
        "At signal edges, use a polynomial fitted to the first or last complete window.",
        (
            Field("samples", "Measured amplitudes", "vector"),
            Field("sample_times_s", "Uniform sample times", "vector", unit="s"),
            Field("window_samples", "Odd window length", "integer", unit="samples"),
            Field("polynomial_order", "Polynomial order (0–6)", "integer"),
        ),
        {"samples": [0, 1, 4, 9, 16, 25, 36], "sample_times_s": list(range(7)),
         "window_samples": 5, "polynomial_order": 2},
        savitzky_golay,
    ),
    Operation(
        "detect_peaks",
        "Peak detection with prominence and spacing",
        "Find interior local maxima, including flat tops, with minimum height and topographic "
        "prominence. Retain stronger peaks when minimum sample spacing conflicts.",
        (
            Field("samples", "Measured amplitudes", "vector"),
            Field("sample_times_s", "Uniform sample times", "vector", unit="s"),
            Field("minimum_height", "Minimum peak height", "number"),
            Field("minimum_prominence", "Minimum prominence", "number"),
            Field("minimum_spacing_samples", "Minimum peak spacing", "integer", unit="samples"),
        ),
        {"samples": [0, 1, 0, 4, 0, 2, 0, 3, 0],
         "sample_times_s": [i / 10 for i in range(9)],
         "minimum_height": 2, "minimum_prominence": 1, "minimum_spacing_samples": 2},
        detect_peaks,
    ),
)
