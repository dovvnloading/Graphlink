"""Observational astronomy and idealized orbital/cosmological calculations.

Only Python's standard library is required. See docs/SCIENTIFIC_ASTRONOMY_REFERENCE.md
for reference frames, approximations, conventions, and published test cases.
"""

from __future__ import annotations

from datetime import UTC, datetime
import math

from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.reference_data import Dataset

REFERENCE_DATASETS = (
    Dataset("astronomy.iau_galactic_rotation", "1.0.0", "astronomy", ("_EQ_TO_GAL",),
            "Astronomical Almanac ICRS/J2000 Galactic rotation"),
)


def _coordinate_reference_datasets(inputs: dict) -> tuple[str, ...]:
    if any(isinstance(inputs.get(key), str) and inputs[key].strip().lower() == "galactic"
           for key in ("source_frame", "target_frame")):
        return ("astronomy.iau_galactic_rotation",)
    return ()


# IAU fixed au (m), Julian year (s), definition of the parsec and exact c (m/s).
_LIGHT_YEARS_PER_PARSEC = (648000 / math.pi) * 149597870700 / (31557600 * 299792458)
# NIST CODATA wavelength displacement constant b (m K), rounded to 10 figures.
# https://physics.nist.gov/cgi-bin/cuu/Value?bwien
_WIEN_M_K = 2.897771955e-3


def _parallax(data):
    parallax = data["parallax_mas"]
    if parallax <= 0:
        raise ValueError("A geometric inverse-parallax distance requires a positive parallax.")
    distance_pc = 1000 / parallax
    return {
        "summary": "Geometric inverse-parallax distance, intended for a precise positive stellar parallax.",
        "values": {"distance_pc": distance_pc, "distance_ly": distance_pc * _LIGHT_YEARS_PER_PARSEC},
    }


def _modulus(data):
    apparent, absolute, extinction = data["apparent_mag"], data["absolute_mag"], data["extinction_mag"]
    if extinction < 0:
        raise ValueError("Passband extinction must be nonnegative.")
    modulus = math.fsum([apparent, -absolute, -extinction])
    distance_pc = 10 ** (1 + modulus / 5)
    if distance_pc == 0:
        raise ValueError("The implied distance is below the supported numerical range.")
    return {
        "summary": "Photometric distance from apparent and absolute magnitudes corrected for extinction in the same band.",
        "values": {"true_distance_modulus_mag": modulus, "distance_pc": distance_pc},
    }


def _blackbody(data):
    temperature = data["temperature_k"]
    if temperature <= 0:
        raise ValueError("An emitting blackbody must have a positive absolute temperature.")
    wavelength_nm = (_WIEN_M_K * 1e9) / temperature
    return {
        "summary": "Peak of blackbody spectral radiance per unit wavelength by Wien’s displacement law.",
        "values": {"peak_wavelength_nm": wavelength_nm},
    }


_AU_M = 149597870700.0  # IAU 2012 B2 (exact)
_DAY_S = 86400.0
_JULIAN_YEAR_S = 31557600.0
_PC_KM = (648000 / math.pi) * _AU_M / 1000  # exact parsec definition
_MPC_KM = _PC_KM * 1e6
_C_KM_S = 299792.458
_MU_SUN_M3_S2 = 1.32712440018e20  # IAU 2015 B3, solar GM nominal
_R_SUN_M = 6.957e8  # IAU 2015 nominal solar radius
_R_EARTH_M = 6.3781e6  # IAU 2015 nominal terrestrial equatorial radius
_AB_ZERO_JY = 3631.0  # conventional AB-flux-density zero point
_J2000 = datetime(2000, 1, 1, 12, tzinfo=UTC)
_DEG = math.pi / 180

# ICRS/J2000 equatorial to IAU Galactic Cartesian rotation (Astrometric
# standards in the Astronomical Almanac; rows are Galactic x, y, z).
_EQ_TO_GAL = (
    (-0.0548755604162154, -0.8734370902348850, -0.4838350155487132),
    (0.4941094278755837, -0.4448296299600112, 0.7469822444972189),
    (-0.8676661490190047, -0.1980763734312015, 0.4559837761750669),
)


def _require_range(value: float, name: str, minimum: float, maximum: float) -> None:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.")


def _utc_jd(value: str) -> float:
    """Proleptic-Gregorian ISO-8601 UTC instant to Julian date (UTC ≈ UT1)."""
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Time must be an ISO-8601 UTC timestamp, e.g. 2000-01-01T12:00:00Z.") from None
    if instant.tzinfo is None or instant.utcoffset() != UTC.utcoffset(instant):
        raise ValueError("Time must have a UTC (Z or +00:00) offset.")
    return 2451545.0 + (instant - _J2000).total_seconds() / _DAY_S


def _gmst_deg(jd: float) -> float:
    """Meeus/IAU 1982 mean sidereal polynomial, using UTC for UT1."""
    centuries = (jd - 2451545.0) / 36525.0
    return (280.46061837 + 360.98564736629 * (jd - 2451545.0)
            + 0.000387933 * centuries**2 - centuries**3 / 38710000) % 360


def _julian_sidereal(data: dict) -> dict:
    jd = _utc_jd(data["utc_iso"])
    longitude = data["observer_longitude_deg_east"]
    _require_range(longitude, "Observer longitude", -180, 180)
    greenwich = _gmst_deg(jd)
    local = (greenwich + longitude) % 360
    return {
        "summary": "Proleptic-Gregorian UTC Julian date and mean sidereal time (UTC≈UT1; east-positive longitude).",
        "values": {
            "julian_date_jd": jd,
            "greenwich_mean_sidereal_hours": greenwich / 15,
            "local_mean_sidereal_hours": local / 15,
        },
    }


def _rotate_z(v: tuple[float, float, float], rad: float) -> tuple[float, float, float]:
    c, s = math.cos(rad), math.sin(rad)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1], v[2])


def _rotate_y(v: tuple[float, float, float], rad: float) -> tuple[float, float, float]:
    c, s = math.cos(rad), math.sin(rad)
    return (c * v[0] + s * v[2], v[1], -s * v[0] + c * v[2])


def _rotate_x(v: tuple[float, float, float], rad: float) -> tuple[float, float, float]:
    c, s = math.cos(rad), math.sin(rad)
    return (v[0], c * v[1] - s * v[2], s * v[1] + c * v[2])


def _precess(v: tuple[float, float, float], jd: float, to_date: bool) -> tuple[float, float, float]:
    """Meeus (IAU 1976) mean equatorial J2000 ↔ mean equator of date."""
    t = (jd - 2451545.0) / 36525
    zeta = (2306.2181 * t + 0.30188 * t**2 + 0.017998 * t**3) / 3600 * _DEG
    z = (2306.2181 * t + 1.09468 * t**2 + 0.018203 * t**3) / 3600 * _DEG
    theta = (2004.3109 * t - 0.42665 * t**2 - 0.041833 * t**3) / 3600 * _DEG
    if to_date:
        return _rotate_z(_rotate_y(_rotate_z(v, zeta), -theta), z)
    return _rotate_z(_rotate_y(_rotate_z(v, -z), theta), -zeta)


def _obliquity_rad(jd: float) -> float:
    t = (jd - 2451545.0) / 36525
    return (23 + 26 / 60 + (21.448 - 46.8150 * t - 0.00059 * t**2 + 0.001813 * t**3) / 3600) * _DEG


def _spherical_vector(lon_deg: float, lat_deg: float) -> tuple[float, float, float]:
    lon, lat = lon_deg * _DEG, lat_deg * _DEG
    return (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))


def _vector_angles(v: tuple[float, float, float]) -> tuple[float, float]:
    lon = (math.degrees(math.atan2(v[1], v[0]))) % 360
    lat = math.degrees(math.atan2(v[2], math.hypot(v[0], v[1])))
    return lon, lat


def _galactic_transform(v: tuple[float, float, float], inverse: bool) -> tuple[float, float, float]:
    if inverse:
        return tuple(sum(_EQ_TO_GAL[j][i] * v[j] for j in range(3)) for i in range(3))  # type: ignore[return-value]
    return tuple(sum(row[i] * v[i] for i in range(3)) for row in _EQ_TO_GAL)  # type: ignore[return-value]


def _eq_horizontal(v: tuple[float, float, float], sidereal: float, latitude: float,
                   inverse: bool) -> tuple[float, float, float]:
    """Rotate mean equatorial-of-date and north/east/up horizontal Cartesian bases."""
    phi = latitude * _DEG
    c, s, cp, sp = math.cos(sidereal), math.sin(sidereal), math.cos(phi), math.sin(phi)
    east = (-s, c, 0)
    north = (-sp * c, -sp * s, cp)
    up = (cp * c, cp * s, sp)
    axes = (north, east, up)  # Horizontal vector is (north, east, up).
    if inverse:
        return tuple(sum(v[j] * axes[j][i] for j in range(3)) for i in range(3))  # type: ignore[return-value]
    return tuple(sum(v[i] * row[i] for i in range(3)) for row in axes)  # type: ignore[return-value]


def _coordinates(data: dict) -> dict:
    source, target = data["source_frame"].strip().lower(), data["target_frame"].strip().lower()
    frames = {"equatorial", "ecliptic", "galactic", "horizontal"}
    if source not in frames or target not in frames:
        raise ValueError("Frames must be equatorial, ecliptic, galactic, or horizontal.")
    latitude = data["source_latitude_deg"]
    observer_lat = data["observer_latitude_deg"]
    longitude = data["observer_longitude_deg_east"]
    _require_range(latitude, "Source latitude/declination/altitude", -90, 90)
    _require_range(observer_lat, "Observer latitude", -90, 90)
    _require_range(longitude, "Observer longitude", -180, 180)
    jd = _utc_jd(data["utc_iso"])
    # The IAU 1976 precession polynomial is suitable for typical modern epochs;
    # do not silently extrapolate beyond the interval documented for the tool.
    if not 1800 <= 2000 + (jd - 2451545) / 365.25 <= 2200:
        raise ValueError("Frame transforms support dates from 1800 through 2200 UTC.")
    lmst = (_gmst_deg(jd) + longitude) % 360
    lon = data["source_longitude_deg"]
    if source == "horizontal":
        az, alt = lon * _DEG, latitude * _DEG
        v = (math.cos(alt) * math.cos(az), math.cos(alt) * math.sin(az), math.sin(alt))
        eq = _eq_horizontal(v, lmst * _DEG, observer_lat, True)
    else:
        v = _spherical_vector(lon, latitude)
        if source == "ecliptic":
            eq = _rotate_x(v, _obliquity_rad(jd))
        elif source == "galactic":
            eq = _precess(_galactic_transform(v, True), jd, True)
        else:
            eq = v
    if target == "horizontal":
        h = _eq_horizontal(eq, lmst * _DEG, observer_lat, False)
        # Azimuth from north through east, and geometric (unrefracted) altitude.
        target_lon = math.degrees(math.atan2(h[1], h[0])) % 360
        target_lat = math.degrees(math.atan2(h[2], math.hypot(h[0], h[1])))
    else:
        if target == "ecliptic":
            v = _rotate_x(eq, -_obliquity_rad(jd))
        elif target == "galactic":
            v = _galactic_transform(_precess(eq, jd, False), False)
        else:
            v = eq
        target_lon, target_lat = _vector_angles(v)
    return {
        "summary": f"{source.title()} → {target} coordinates: (lon,lat) are (RA,Dec) for equatorial and (az,alt) for horizontal. Mean equator/ecliptic of date; Galactic ICRS/J2000; geometric horizon.",
        "values": {
            "target_longitude_deg": target_lon, "target_latitude_deg": target_lat,
            "julian_date_jd": jd, "local_mean_sidereal_hours": lmst / 15,
        },
    }


def _eccentric_anomaly(mean_rad: float, eccentricity: float) -> float:
    """Monotonic elliptic Kepler solve via bracket-guarded Newton iteration."""
    m = (mean_rad + math.pi) % math.tau - math.pi
    if eccentricity == 0:
        return m
    lo, hi = -math.pi, math.pi
    e = m if eccentricity < 0.8 else math.copysign(math.pi, m if m else 1)
    for _ in range(80):
        residual = e - eccentricity * math.sin(e) - m
        if abs(residual) < 1e-14:
            return e
        if residual > 0:
            hi = e
        else:
            lo = e
        slope = 1 - eccentricity * math.cos(e)
        proposal = e - residual / slope
        e = proposal if lo < proposal < hi and math.isfinite(proposal) else (lo + hi) / 2
    raise ValueError("Kepler iteration failed to converge.")


def _kepler_orbit(data: dict) -> dict:
    a, ecc = data["semimajor_axis_au"], data["eccentricity"]
    mass = data["central_mass_solar"]
    if a <= 0 or not 0 <= ecc < 1 or mass <= 0:
        raise ValueError("Elliptical orbits require a>0 AU, 0≤e<1 and central mass>0 solar masses.")
    inc = data["inclination_deg"]
    _require_range(inc, "Inclination", 0, 180)
    mean_motion = math.sqrt((_MU_SUN_M3_S2 * mass) * (_DAY_S / _AU_M)**2 / (a**3 * _AU_M))
    # Equivalent to sqrt(GM_Sun*mass*day^2/(a*AU)^3) [rad/day].
    mean = (data["mean_anomaly_epoch_deg"] * _DEG
            + mean_motion * (data["target_jd"] - data["epoch_jd"])) % math.tau
    anomaly = _eccentric_anomaly(mean, ecc)
    s = math.sqrt((1 - ecc) * (1 + ecc))
    denominator = 1 - ecc * math.cos(anomaly)
    xp = a * (math.cos(anomaly) - ecc)
    yp = a * s * math.sin(anomaly)
    vxp = -a * mean_motion * math.sin(anomaly) / denominator
    vyp = a * mean_motion * s * math.cos(anomaly) / denominator
    node = data["ascending_node_deg"] * _DEG
    peri = data["argument_periapsis_deg"] * _DEG
    i = inc * _DEG
    co, so, cw, sw, ci, si = math.cos(node), math.sin(node), math.cos(peri), math.sin(peri), math.cos(i), math.sin(i)
    p = (co * cw - so * sw * ci, so * cw + co * sw * ci, sw * si)
    q = (-co * sw - so * cw * ci, -so * sw + co * cw * ci, cw * si)
    pos = [xp * p[j] + yp * q[j] for j in range(3)]
    vel = [vxp * p[j] + vyp * q[j] for j in range(3)]
    true = math.atan2(s * math.sin(anomaly), math.cos(anomaly) - ecc) % math.tau
    return {
        "summary": "Unperturbed two-body elliptic orbit, Cartesian axes defined by the orbital elements; velocities in AU/day.",
        "values": {
            "x_au": pos[0], "y_au": pos[1], "z_au": pos[2],
            "vx_au_per_day": vel[0], "vy_au_per_day": vel[1], "vz_au_per_day": vel[2],
            "radius_au": a * denominator,
            "speed_au_per_day": math.hypot(*vel),
            "mean_anomaly_deg": math.degrees(mean),
            "eccentric_anomaly_deg": math.degrees(anomaly % math.tau),
            "true_anomaly_deg": math.degrees(true),
            "orbital_period_days": math.tau / mean_motion,
        },
    }


def _airmass(data: dict) -> dict:
    altitude = data["apparent_altitude_deg"]
    _require_range(altitude, "Apparent altitude", 0, 90)
    coefficient = data["extinction_coefficient_mag_per_airmass"]
    if coefficient < 0:
        raise ValueError("Atmospheric extinction coefficient must be nonnegative.")
    # Kasten & Young (1989); apparent, not unrefracted, altitude.
    x = 1 / (math.sin(altitude * _DEG) + 0.50572 * (altitude + 6.07995) ** -1.6364)
    extinction = x * coefficient
    return {
        "summary": "Kasten–Young optical relative airmass for apparent altitude ≥0°; one-band Bouguer extinction.",
        "values": {
            "relative_airmass": x,
            "extinction_mag": extinction,
            "transmission_fraction": 10 ** (-0.4 * extinction),
            "above_atmosphere_mag": data["observed_mag"] - extinction,
        },
    }


def _simpson_integral(fn, upper: float) -> float:
    """Adaptive Simpson integration, absolute tolerance 2e-11 in Hubble units."""
    if upper == 0:
        return 0.0
    left, right = 0.0, upper
    mid = (left + right) / 2
    fa, fm, fb = fn(left), fn(mid), fn(right)
    whole = (right - left) * (fa + 4 * fm + fb) / 6
    stack = [(left, right, fa, fm, fb, whole, 2e-11, 24)]
    total = 0.0
    while stack:
        a, b, f_a, f_m, f_b, s, tol, depth = stack.pop()
        midpoint = (a + b) / 2
        fl = fn((a + midpoint) / 2)
        fr = fn((midpoint + b) / 2)
        left_s = (midpoint - a) * (f_a + 4 * fl + f_m) / 6
        right_s = (b - midpoint) * (f_m + 4 * fr + f_b) / 6
        error = left_s + right_s - s
        if abs(error) <= 15 * tol:
            total += left_s + right_s + error / 15
        elif depth == 0:
            raise ValueError("Cosmological integral failed to converge.")
        else:
            stack.append((midpoint, b, f_m, fr, f_b, right_s, tol / 2, depth - 1))
            stack.append((a, midpoint, f_a, fl, f_m, left_s, tol / 2, depth - 1))
    return total


def _cosmological_distances(data: dict) -> dict:
    z, h0, omega_m = data["redshift"], data["h0_km_s_mpc"], data["omega_matter"]
    if not 0 <= z <= 1000 or h0 <= 0:
        raise ValueError("Require 0≤redshift≤1000 and H0>0 km/s/Mpc.")
    _require_range(omega_m, "Matter density parameter", 0, 1)
    omega_l = 1 - omega_m
    # Log(1+z) substitution keeps both integrands well conditioned at high z.
    def inverse_hubble(t: float) -> float:
        return 1 / math.sqrt(omega_m * math.exp(3 * t) + omega_l)
    bound = math.log1p(z)
    dc = _C_KM_S / h0 * _simpson_integral(lambda t: math.exp(t) * inverse_hubble(t), bound)
    lb = (_MPC_KM / h0) / (_JULIAN_YEAR_S * 1e9) * _simpson_integral(inverse_hubble, bound)
    return {
        "summary": "Flat matter+Λ FLRW model (ΩΛ=1−Ωm; no radiation or curvature); lookback is elapsed observer time.",
        "values": {
            "comoving_distance_mpc": dc,
            "luminosity_distance_mpc": dc * (1 + z),
            "angular_diameter_distance_mpc": dc / (1 + z),
            "lookback_time_gyr": lb,
        },
    }


def _flux_to_ab(data: dict) -> dict:
    flux = data["flux_density_jy"]
    if flux <= 0:
        raise ValueError("Flux density must be positive in Jy.")
    return {
        "summary": "AB magnitude from monochromatic flux density fν, using the conventional 3631 Jy reference.",
        "values": {"ab_magnitude_mag": -2.5 * (math.log10(flux) - math.log10(_AB_ZERO_JY))},
    }


def _ab_to_flux(data: dict) -> dict:
    flux = _AB_ZERO_JY * 10 ** (-0.4 * data["ab_magnitude_mag"])
    if not math.isfinite(flux) or flux == 0:
        raise ValueError("Magnitude corresponds to an unrepresentable flux density in Jy.")
    return {
        "summary": "Monochromatic AB flux density fν from AB magnitude (zero point: 3631 Jy).",
        "values": {"flux_density_jy": flux},
    }


def _colour_index(data: dict) -> dict:
    colour = data["first_ab_mag"] - data["second_ab_mag"]
    ratio = 10 ** (-0.4 * colour)
    if not math.isfinite(ratio) or ratio == 0:
        raise ValueError("Colour corresponds to an unrepresentable flux-density ratio.")
    return {
        "summary": "AB colour (first−second) and first/second fν flux-density ratio, with matching AB zero points.",
        "values": {"colour_index_mag": colour, "first_to_second_flux_ratio": ratio},
    }


def _exoplanet_transit(data: dict) -> dict:
    baseline, inside = data["out_of_transit_flux_relative"], data["in_transit_flux_relative"]
    rs, a, period = data["stellar_radius_solar"], data["semimajor_axis_au"], data["orbital_period_days"]
    b = data["impact_parameter_stellar_radii"]
    if baseline <= 0 or not 0 < inside < baseline:
        raise ValueError("Fluxes must satisfy 0 < in-transit < out-of-transit (same relative scale).")
    if rs <= 0 or a <= 0 or period <= 0 or b < 0:
        raise ValueError("Stellar radius, semimajor axis and period must be positive; impact parameter nonnegative.")
    depth = (baseline - inside) / baseline
    k = math.sqrt(depth)
    rstar_over_a = rs * _R_SUN_M / (a * _AU_M)
    if rstar_over_a * (1 + k) >= 1 or b >= 1 + k:
        raise ValueError("Require a>Rstar+Rplanet and impact parameter <1+Rp/Rstar (a transit).")
    # The inferred radius k=sqrt(depth) assumes an unocculted, fully
    # superimposed planetary disk at mid-transit. Grazing events violate it:
    # the observed depth is then smaller than the area ratio.
    if b >= 1 - k:
        raise ValueError("A grazing transit cannot infer Rp/Rstar from depth; require b < 1-Rp/Rstar.")
    sin_i = math.sqrt(1 - (b * rstar_over_a)**2)
    chord_outer = math.sqrt((1 + k)**2 - b**2)
    chord_inner = math.sqrt((1 - k)**2 - b**2)
    t14 = period / math.pi * math.asin(rstar_over_a * chord_outer / sin_i)
    t23 = period / math.pi * math.asin(rstar_over_a * chord_inner / sin_i)
    return {
        "summary": "Circular, uniformly bright, undiluted star; small dark planet; no limb darkening. Duration T14 is first-to-fourth contact.",
        "values": {
            "transit_depth_fraction": depth,
            "transit_depth_ppm": depth * 1e6,
            "radius_ratio_planet_star": k,
            "planet_radius_earth": k * rs * _R_SUN_M / _R_EARTH_M,
            "total_duration_hours": t14 * 24,
            "full_duration_hours": t23 * 24,
            "ingress_duration_hours": (t14 - t23) * 12,
        },
    }


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "parallax_distance",
        "Estimate distance from parallax",
        "Invert a precise positive stellar parallax to estimate distance in parsecs and light-years.",
        (Field("parallax_mas", "Annual parallax", "number", unit="mas"),),
        {"parallax_mas": 100},
        _parallax,
    ),
    Operation(
        "distance_modulus",
        "Calculate a photometric distance",
        "Convert the extinction-corrected distance modulus to parsecs using magnitudes from a common passband.",
        (
            Field("apparent_mag", "Apparent magnitude", "number", unit="mag"),
            Field("absolute_mag", "Absolute magnitude", "number", unit="mag"),
            Field("extinction_mag", "Passband extinction", "number", unit="mag"),
        ),
        {"apparent_mag": 10.3, "absolute_mag": 5.0, "extinction_mag": 0.3},
        _modulus,
    ),
    Operation(
        "blackbody_peak",
        "Find a blackbody spectral peak",
        "Compute the wavelength of maximum spectral radiance for an ideal blackbody at the specified temperature.",
        (Field("temperature_k", "Blackbody temperature", "number", unit="K"),),
        {"temperature_k": 5772},
        _blackbody,
    ),
    Operation(
        "keplerian_orbit",
        "Propagate a Keplerian orbit",
        "Advance an elliptic two-body orbit with Newton-solved Kepler's equation; Cartesian position (AU) and velocity (AU/day).",
        (
            Field("semimajor_axis_au", "Semimajor axis", "number", unit="AU"),
            Field("eccentricity", "Eccentricity", "number", unit="1"),
            Field("inclination_deg", "Inclination", "number", unit="deg"),
            Field("ascending_node_deg", "Longitude of ascending node", "number", unit="deg"),
            Field("argument_periapsis_deg", "Argument of periapsis", "number", unit="deg"),
            Field("mean_anomaly_epoch_deg", "Mean anomaly at epoch", "number", unit="deg"),
            Field("epoch_jd", "Element epoch", "number", unit="JD (days)"),
            Field("target_jd", "Propagation target time", "number", unit="JD (days)"),
            Field("central_mass_solar", "Central body mass", "number", unit="solar masses"),
        ),
        {"semimajor_axis_au": 1.0, "eccentricity": 0.1, "inclination_deg": 5,
         "ascending_node_deg": 80, "argument_periapsis_deg": 40, "mean_anomaly_epoch_deg": 5,
         "epoch_jd": 2451545.0, "target_jd": 2451555.0, "central_mass_solar": 1},
        _kepler_orbit,
        headline="radius_au",
    ),
    Operation(
        "julian_sidereal_time",
        "Julian date and sidereal time",
        "Convert an ISO UTC time to Julian date and Greenwich/local mean sidereal time; UTC approximates UT1.",
        (
            Field("utc_iso", "Observation time", "text", unit="ISO-8601 UTC"),
            Field("observer_longitude_deg_east", "Observer longitude (east positive)", "number", unit="deg"),
        ),
        {"utc_iso": "1987-04-10T19:21:00Z", "observer_longitude_deg_east": 0},
        _julian_sidereal,
    ),
    Operation(
        "coordinate_frames",
        "Convert sky coordinate frames",
        "Convert equatorial/ecliptic (mean of date), ICRS/J2000 Galactic, and geometric horizontal coordinates.",
        (
            Field("source_frame", "Input frame: equatorial/ecliptic/galactic/horizontal", "text", unit="frame"),
            Field("target_frame", "Output frame: equatorial/ecliptic/galactic/horizontal", "text", unit="frame"),
            Field("source_longitude_deg", "Input RA/longitude/azimuth", "number", unit="deg"),
            Field("source_latitude_deg", "Input declination/latitude/altitude", "number", unit="deg"),
            Field("utc_iso", "Observation time", "text", unit="ISO-8601 UTC"),
            Field("observer_latitude_deg", "Observer geodetic latitude", "number", unit="deg"),
            Field("observer_longitude_deg_east", "Observer longitude (east positive)", "number", unit="deg"),
        ),
        {"source_frame": "equatorial", "target_frame": "horizontal", "source_longitude_deg": 120,
         "source_latitude_deg": 20, "utc_iso": "2000-01-01T12:00:00Z",
         "observer_latitude_deg": 30, "observer_longitude_deg_east": 0},
        _coordinates,
        reference_datasets=_coordinate_reference_datasets,
        headline="target_longitude_deg",
    ),
    Operation(
        "airmass_extinction",
        "Estimate airmass and extinction",
        "Calculate Kasten–Young optical airmass and atmospheric magnitude correction for apparent altitude ≥0°.",
        (
            Field("apparent_altitude_deg", "Apparent altitude", "number", unit="deg"),
            Field("extinction_coefficient_mag_per_airmass", "Passband extinction coefficient", "number", unit="mag/airmass"),
            Field("observed_mag", "Observed magnitude in passband", "number", unit="mag"),
        ),
        {"apparent_altitude_deg": 45, "extinction_coefficient_mag_per_airmass": 0.2, "observed_mag": 10},
        _airmass,
    ),
    Operation(
        "flat_lcdm_distances",
        "Cosmological distances and lookback time",
        "Integrate flat matter+Λ cosmology with adjustable H₀ and Ωm; no radiation or curvature.",
        (
            Field("redshift", "Redshift", "number", unit="1"),
            Field("h0_km_s_mpc", "Hubble constant H₀", "number", unit="km/(s·Mpc)"),
            Field("omega_matter", "Matter density Ωm", "number", unit="1"),
        ),
        {"redshift": 1.0, "h0_km_s_mpc": 70, "omega_matter": 0.3},
        _cosmological_distances,
        headline="luminosity_distance_mpc",
    ),
    Operation(
        "ab_flux_to_magnitude",
        "Convert AB flux to magnitude",
        "Compute AB magnitude from fν flux density, with zero point 3631 Jy.",
        (Field("flux_density_jy", "Flux density fν", "number", unit="Jy"),),
        {"flux_density_jy": 3631},
        _flux_to_ab,
    ),
    Operation(
        "ab_magnitude_to_flux",
        "Convert AB magnitude to flux",
        "Compute fν flux density from AB magnitude, with zero point 3631 Jy.",
        (Field("ab_magnitude_mag", "AB magnitude", "number", unit="mag AB"),),
        {"ab_magnitude_mag": 20},
        _ab_to_flux,
    ),
    Operation(
        "ab_colour_index",
        "AB colour and flux ratio",
        "Difference of AB magnitudes in two passbands and their fν ratio (first/second).",
        (
            Field("first_ab_mag", "First passband AB magnitude", "number", unit="mag AB"),
            Field("second_ab_mag", "Second passband AB magnitude", "number", unit="mag AB"),
        ),
        {"first_ab_mag": 20, "second_ab_mag": 19.5},
        _colour_index,
    ),
    Operation(
        "exoplanet_transit",
        "Estimate exoplanet transit parameters",
        "Infer transit depth and radius from relative fluxes and T14, T23 contact durations for a circular orbit.",
        (
            Field("out_of_transit_flux_relative", "Out-of-transit flux", "number", unit="relative flux"),
            Field("in_transit_flux_relative", "Mid-transit flux", "number", unit="relative flux"),
            Field("stellar_radius_solar", "Stellar radius", "number", unit="solar radii"),
            Field("semimajor_axis_au", "Orbital semimajor axis", "number", unit="AU"),
            Field("orbital_period_days", "Orbital period", "number", unit="days"),
            Field("impact_parameter_stellar_radii", "Transit impact parameter", "number", unit="stellar radii"),
        ),
        {"out_of_transit_flux_relative": 1, "in_transit_flux_relative": 0.99,
         "stellar_radius_solar": 1, "semimajor_axis_au": 0.05, "orbital_period_days": 4,
         "impact_parameter_stellar_radii": 0},
        _exoplanet_transit,
    ),
)
