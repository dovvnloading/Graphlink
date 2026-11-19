"""Spherical geometry and simple observational geoscience models."""

import math
from datetime import UTC, datetime
from fractions import Fraction

from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.reference_data import Dataset

REFERENCE_DATASETS = (
    Dataset("earth.wgs84_ellipsoid", "1.0.0", "earth", ("_WGS_A", "_WGS_F"),
            "NGA World Geodetic System 1984"),
    Dataset("earth.standard_atmosphere_1976", "1.0.0", "earth",
            ("_ISA_LAYERS", "_ISA_RE", "_ISA_R", "_ISA_G"),
            "US Standard Atmosphere 1976 / ISA dry-air layers"),
)


def great_circle_distance(data):
    lat1, lat2 = data["latitude_a"], data["latitude_b"]
    lon1, lon2 = data["longitude_a"], data["longitude_b"]
    if max(abs(lat1), abs(lat2)) > 90 or max(abs(lon1), abs(lon2)) > 180:
        raise ValueError("Latitudes must be within ±90° and longitudes within ±180°.")
    # The atan2 form of the spherical inverse stays usable near antipodes.
    delta_deg = lon2 - lon1
    if abs(delta_deg) > 180:
        delta_deg = math.fsum([lon2, -lon1, -math.copysign(360, delta_deg)])
    delta = math.radians(delta_deg)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    c1 = math.sin(math.radians(90 - abs(lat1))) if abs(lat1) > 45 else math.cos(phi1)
    c2 = math.sin(math.radians(90 - abs(lat2))) if abs(lat2) > 45 else math.cos(phi2)
    s1, s2 = math.sin(phi1), math.sin(phi2)
    cross = math.hypot(c2 * math.sin(delta), c1 * s2 - s1 * c2 * math.cos(delta))
    dot = s1 * s2 + c1 * c2 * math.cos(delta)
    if dot > 0:
        # Taking the latitude difference before converting to radians preserves small arcs.
        half_lat = math.sin(math.radians(lat2 - lat1) / 2)
        half_lon = math.sqrt(c1 * c2) * math.sin(delta / 2)
        angle = 2 * math.asin(min(1.0, math.hypot(half_lat, half_lon)))
    else:
        angle = math.atan2(cross, dot)
    radius = 6371.0088
    distance = radius * angle
    return {
        "summary": f"Great-circle surface distance is {distance:.6g} km on a mean-radius sphere.",
        "values": {"distance_km": distance, "central_angle_deg": math.degrees(angle), "sphere_radius_km": radius},
    }


def geothermal_gradient(data):
    shallow, deep = data["shallow_depth_m"], data["deep_depth_m"]
    t1, t2 = data["shallow_temperature_c"], data["deep_temperature_c"]
    if shallow < 0 or deep <= shallow:
        raise ValueError("Depth increases downwards; provide a nonnegative shallow depth and a greater deep depth.")
    if min(t1, t2) < -273.15:
        raise ValueError("Temperatures cannot be below absolute zero.")
    gradient = float((Fraction(t2) - Fraction(t1)) * 1000 / (Fraction(deep) - Fraction(shallow)))
    return {
        "summary": f"Temperature changes by {gradient:.6g} °C per kilometre of increasing depth.",
        "values": {"gradient_c_per_km": gradient, "depth_interval_m": deep - shallow, "temperature_change_c": t2 - t1},
    }


def seismic_distance(data):
    lag, vp, vs = data["s_minus_p_s"], data["p_speed_km_s"], data["s_speed_km_s"]
    if lag < 0 or not vp > vs > 0:
        raise ValueError("Use a nonnegative S-minus-P lag and wave speeds satisfying Vp > Vs > 0.")
    # Compute each reported quantity from the unrounded path length.
    p, s = Fraction(vp), Fraction(vs)
    path = Fraction(lag) * s * p / (p - s)
    distance = float(path)
    return {
        "summary": f"Estimated source-to-station path length is {distance:.6g} km.",
        "values": {"path_length_km": distance, "p_travel_s": float(path / p), "s_travel_s": float(path / s)},
    }




# WGS84 (NGA): a = 6378137 m, f = 1/298.257223563.
_WGS_A = 6378137.0
_WGS_F = 1 / 298.257223563
_WGS_B = _WGS_A * (1 - _WGS_F)
_WGS_E2 = _WGS_F * (2 - _WGS_F)
_WGS_EP2 = _WGS_E2 / (1 - _WGS_E2)
_UTM_SCALE = 0.9996


def _coordinates(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Latitude must be in [-90, 90] ° and longitude in [-180, 180] °.")


def _wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


def _vincenty_direct(lat: float, lon: float, bearing: float, distance: float) -> tuple[float, float, float]:
    """WGS84 Vincenty direct solution; radians in/out except distance in m."""
    u1 = math.atan((1 - _WGS_F) * math.tan(lat))
    su1, cu1 = math.sin(u1), math.cos(u1)
    sa1, ca1 = math.sin(bearing), math.cos(bearing)
    sigma1 = math.atan2(math.tan(u1), ca1)
    sin_alpha = cu1 * sa1
    cos2_alpha = max(0.0, 1 - sin_alpha**2)
    u2 = cos2_alpha * (_WGS_A**2 - _WGS_B**2) / _WGS_B**2
    aa = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    bb = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    sigma = distance / (_WGS_B * aa)
    for _ in range(30):
        cos2m = math.cos(2 * sigma1 + sigma)
        sin_s, cos_s = math.sin(sigma), math.cos(sigma)
        ds = bb * sin_s * (cos2m + bb / 4 * (cos_s * (-1 + 2 * cos2m**2)
                  - bb / 6 * cos2m * (-3 + 4 * sin_s**2) * (-3 + 4 * cos2m**2)))
        updated = distance / (_WGS_B * aa) + ds
        if abs(updated - sigma) < 1e-14:
            sigma = updated
            break
        sigma = updated
    sin_s, cos_s = math.sin(sigma), math.cos(sigma)
    cos2m = math.cos(2 * sigma1 + sigma)
    tmp = su1 * sin_s - cu1 * cos_s * ca1
    lat2 = math.atan2(su1 * cos_s + cu1 * sin_s * ca1,
                      (1 - _WGS_F) * math.hypot(sin_alpha, tmp))
    lam = math.atan2(sin_s * sa1, cu1 * cos_s - su1 * sin_s * ca1)
    cc = _WGS_F / 16 * cos2_alpha * (4 + _WGS_F * (4 - 3 * cos2_alpha))
    long_delta = lam - (1 - cc) * _WGS_F * sin_alpha * (sigma + cc * sin_s *
                         (cos2m + cc * cos_s * (-1 + 2 * cos2m**2)))
    final_bearing = math.atan2(sin_alpha, -tmp)
    return lat2, _wrap_angle(lon + long_delta), final_bearing


def _inverse_shooting(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float, float]:
    """Near-antipodal fallback: solve the WGS84 *direct* geodesic by shooting.

    Search both polar branches (and spherical seeds), keep the shortest
    converged geodesic. Unlike a sphere substitution this remains ellipsoidal.
    """
    dl = _wrap_angle(lon2 - lon1)
    spherical_bearing = math.atan2(math.sin(dl) * math.cos(lat2),
                                    math.cos(lat1) * math.sin(lat2) -
                                    math.sin(lat1) * math.cos(lat2) * math.cos(dl))
    arc = math.acos(max(-1.0, min(1.0, math.sin(lat1) * math.sin(lat2) +
                              math.cos(lat1) * math.cos(lat2) * math.cos(dl))))
    approx = 6371008.8 * arc
    best: tuple[float, float, float] | None = None
    # Search both polar branches and a spherical azimuth seed.
    seeds = (spherical_bearing, 0.0, math.pi, math.pi / 2, -math.pi / 2,
             math.pi / 4, -math.pi / 4, 3 * math.pi / 4, -3 * math.pi / 4)
    scale_lon = max(0.000001, abs(math.cos(lat2)))

    def residual(az: float, dist: float) -> tuple[float, float, float]:
        result_lat, result_lon, final_bearing = _vincenty_direct(lat1, lon1, az, dist)
        return result_lat - lat2, _wrap_angle(result_lon - lon2) * scale_lon, final_bearing

    for seed in seeds:
        az, dist = seed, approx
        for _ in range(45):
            rlat, rlon, terminal = residual(az, dist)
            err = math.hypot(rlat, rlon)
            if err < 2e-13 and 0 < dist <= math.pi * _WGS_A:
                if best is None or dist < best[0]:
                    best = (dist, az % (2 * math.pi), (terminal + math.pi) % (2 * math.pi))
                break
            da, ds = 1e-5, 10.0
            raa, rab, _ = residual(az + da, dist)
            rsa, rsb, _ = residual(az, dist + ds)
            j11, j21 = (raa - rlat) / da, (rab - rlon) / da
            j12, j22 = (rsa - rlat) / ds, (rsb - rlon) / ds
            det = j11 * j22 - j12 * j21
            if abs(det) < 1e-25:
                break
            step_az = max(-0.4, min(0.4, (-rlat * j22 + rlon * j12) / det))
            step_dist = max(-500000.0, min(500000.0, (-j11 * rlon + j21 * rlat) / det))
            improved = False
            for fraction in (1.0, 0.5, 0.25, 0.125, 0.0625):
                candidate_dist = dist + fraction * step_dist
                if candidate_dist <= 0 or candidate_dist > math.pi * _WGS_A:
                    continue
                candidate_az = az + fraction * step_az
                ra, rb, _ = residual(candidate_az, candidate_dist)
                if math.hypot(ra, rb) < err:
                    az, dist = candidate_az, candidate_dist
                    improved = True
                    break
            if not improved:
                break
    if best is None:
        raise ValueError("The nearly antipodal WGS84 inverse is ill-conditioned for these coordinates.")
    return best


def geodesic_inverse(data):
    lat1, lon1 = data["latitude_a_deg"], data["longitude_a_deg"]
    lat2, lon2 = data["latitude_b_deg"], data["longitude_b_deg"]
    _coordinates(lat1, lon1)
    _coordinates(lat2, lon2)
    if (lat1 == lat2 and (lon1 == lon2 or abs(lat1) == 90 or abs(lon1 - lon2) == 360)):
        distance, initial, reverse, method = 0.0, 0.0, 0.0, "coincident (bearings undefined)"
    else:
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        u1, u2 = math.atan((1 - _WGS_F) * math.tan(phi1)), math.atan((1 - _WGS_F) * math.tan(phi2))
        su1, cu1, su2, cu2 = math.sin(u1), math.cos(u1), math.sin(u2), math.cos(u2)
        ll = math.radians((lon2 - lon1 + 180) % 360 - 180)
        lam = ll
        converged = False
        for _ in range(200):
            sin_lam, cos_lam = math.sin(lam), math.cos(lam)
            sin_s = math.hypot(cu2 * sin_lam, cu1 * su2 - su1 * cu2 * cos_lam)
            cos_s = su1 * su2 + cu1 * cu2 * cos_lam
            if sin_s == 0 and cos_s > 0:
                distance, initial, reverse, method = 0.0, 0.0, 0.0, "coincident (bearings undefined)"
                converged = True
                break
            if sin_s == 0:
                break
            sigma = math.atan2(sin_s, cos_s)
            sin_alpha = cu1 * cu2 * sin_lam / sin_s
            cos2_alpha = max(0.0, 1 - sin_alpha**2)
            cos2m = cos_s - 2 * su1 * su2 / cos2_alpha if cos2_alpha > 1e-15 else 0.0
            cc = _WGS_F / 16 * cos2_alpha * (4 + _WGS_F * (4 - 3 * cos2_alpha))
            new_lam = ll + (1 - cc) * _WGS_F * sin_alpha * (sigma + cc * sin_s *
                                   (cos2m + cc * cos_s * (-1 + 2 * cos2m**2)))
            if abs(new_lam - lam) < 1e-13:
                lam = new_lam
                u_sq = cos2_alpha * (_WGS_A**2 - _WGS_B**2) / _WGS_B**2
                aa = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
                bb = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
                delta_s = bb * sin_s * (cos2m + bb / 4 * (cos_s * (-1 + 2 * cos2m**2) -
                                bb / 6 * cos2m * (-3 + 4 * sin_s**2) * (-3 + 4 * cos2m**2)))
                distance = _WGS_B * aa * (sigma - delta_s)
                initial = math.atan2(cu2 * sin_lam, cu1 * su2 - su1 * cu2 * cos_lam)
                reverse = math.atan2(cu1 * sin_lam, -su1 * cu2 + cu1 * su2 * cos_lam) + math.pi
                method = "Vincenty inverse (WGS84)"
                converged = True
                break
            lam = new_lam
        if not converged:
            distance, initial, reverse = _inverse_shooting(phi1, math.radians(lon1), phi2, math.radians(lon2))
            method = "Vincenty direct shooting (WGS84 antipodal fallback)"
    # At a geographic pole there is no unique local direction of north.
    # Preserve the distance and the other endpoint's bearing, but do not
    # report an arbitrary meridian-dependent bearing at the pole.
    if abs(lat1) == 90 or abs(lat2) == 90:
        method += "; pole bearing(s) undefined (0° convention)"
        if abs(lat1) == 90:
            initial = 0.0
        if abs(lat2) == 90:
            reverse = 0.0
    return {"summary": f"WGS84 ellipsoidal geodesic {distance:.3f} m; {method}.",
            "values": {"distance_m": distance, "initial_bearing_deg": math.degrees(initial) % 360,
                       "reverse_bearing_deg": math.degrees(reverse) % 360,
                       "solution_method": method}}


def _utm_zone(latitude: float, longitude: float) -> int:
    zone = min(60, int((longitude + 180) // 6) + 1)
    if 56 <= latitude < 64 and 3 <= longitude < 12:
        return 32
    if 72 <= latitude <= 84:
        for lo, hi, override in ((0, 9, 31), (9, 21, 33), (21, 33, 35), (33, 42, 37)):
            if lo <= longitude < hi:
                return override
    return zone


def _utm_meridian(phi: float) -> float:
    e2 = _WGS_E2
    return _WGS_A * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi
            - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
            + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi)
            - (35 * e2**3 / 3072) * math.sin(6 * phi))


def utm_forward(data):
    latitude, longitude = data["latitude_deg"], data["longitude_deg"]
    _coordinates(latitude, longitude)
    if not -80 <= latitude <= 84:
        raise ValueError("UTM covers latitudes from 80°S through 84°N; use UPS outside that range.")
    zone = _utm_zone(latitude, longitude)
    phi = math.radians(latitude)
    lambda0 = math.radians(zone * 6 - 183)
    dlam = math.radians(longitude) - lambda0
    n = _WGS_A / math.sqrt(1 - _WGS_E2 * math.sin(phi)**2)
    t = math.tan(phi)**2
    c = _WGS_EP2 * math.cos(phi)**2
    aa = math.cos(phi) * dlam
    x = 500000 + _UTM_SCALE * n * (aa + (1 - t + c) * aa**3 / 6 +
                                     (5 - 18 * t + t**2 + 72 * c - 58 * _WGS_EP2) * aa**5 / 120)
    y = _UTM_SCALE * (_utm_meridian(phi) + n * math.tan(phi) *
                     (aa**2 / 2 + (5 - t + 9 * c + 4 * c**2) * aa**4 / 24 +
                      (61 - 58 * t + t**2 + 600 * c - 330 * _WGS_EP2) * aa**6 / 720))
    hemisphere = "N" if latitude >= 0 else "S"
    if hemisphere == "S":
        y += 10000000
    return {"summary": f"WGS84 UTM zone {zone}{hemisphere} (metres, false easting/northing applied).",
            "values": {"zone_1": zone, "hemisphere": hemisphere, "easting_m": x, "northing_m": y}}


def utm_inverse(data):
    zone, hemisphere = data["zone_1"], data["hemisphere"]
    easting, northing = data["easting_m"], data["northing_m"]
    if not 1 <= zone <= 60 or hemisphere not in ("N", "S"):
        raise ValueError("UTM zone must be an integer 1–60 and hemisphere must be N or S.")
    if not 100000 <= easting <= 900000 or not 0 <= northing <= 10000000:
        raise ValueError("UTM easting must be 100000–900000 m and northing 0–10000000 m.")
    x = (easting - 500000) / _UTM_SCALE
    m = (northing - (10000000 if hemisphere == "S" else 0)) / _UTM_SCALE
    e2 = _WGS_E2
    mu = m / (_WGS_A * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu) +
            (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu) +
            151 * e1**3 / 96 * math.sin(6 * mu) + 1097 * e1**4 / 512 * math.sin(8 * mu))
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = _WGS_A / math.sqrt(1 - e2 * sin1**2)
    r1 = _WGS_A * (1 - e2) / (1 - e2 * sin1**2)**1.5
    t1, c1 = tan1**2, _WGS_EP2 * cos1**2
    d = x / n1
    phi = phi1 - n1 * tan1 / r1 * (d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 -
                             9 * _WGS_EP2) * d**4 / 24 +
                             (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * _WGS_EP2 -
                              3 * c1**2) * d**6 / 720)
    lon = math.radians(zone * 6 - 183) + (d - (1 + 2 * t1 + c1) * d**3 / 6 +
          (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * _WGS_EP2 + 24 * t1**2) * d**5 / 120) / cos1
    lat_deg = math.degrees(phi)
    lon_deg = (math.degrees(lon) + 180) % 360 - 180
    if not -80.00001 <= lat_deg <= 84.00001:
        raise ValueError("The resulting latitude falls outside the UTM coverage of 80°S to 84°N.")
    if (hemisphere == "N" and lat_deg < -0.00001) or (hemisphere == "S" and lat_deg > 0.00001):
        raise ValueError("UTM hemisphere and northing are inconsistent.")
    return {"summary": f"WGS84 latitude/longitude from UTM zone {zone}{hemisphere}.",
            "values": {"latitude_deg": lat_deg, "longitude_deg": lon_deg}}


def radiometric_age(data):
    parent = data["parent_ratio_1"]
    daughter = data["radiogenic_daughter_ratio_1"]
    half = data["half_life_ma"]
    uncertainty = data["half_life_standard_uncertainty_ma"]
    if parent <= 0 or daughter < 0 or half <= 0 or uncertainty < 0:
        raise ValueError("Require positive parent ratio and half-life, nonnegative radiogenic daughter and half-life uncertainty.")
    # Factor first to avoid overflowing half/ln(2) or uncertainty/half
    # when the final age and uncertainty are both representable.
    age_per_half_life = math.log1p(daughter / parent) / math.log(2)
    age = half * age_per_half_life
    sigma = uncertainty * age_per_half_life
    return {"summary": "Closed-system decay age from radiogenic daughter/remaining parent; half-life uncertainty only (1σ).",
            "values": {"age_ma": age, "age_standard_uncertainty_ma": sigma,
                       "daughter_parent_ratio_1": daughter / parent}}


def darcy_flow(data):
    k, length, area = data["hydraulic_conductivity_m_s"], data["flow_length_m"], data["cross_section_area_m2"]
    porosity = data["effective_porosity_1"]
    if k <= 0 or length <= 0 or area <= 0 or not 0 < porosity <= 1:
        raise ValueError("Conductivity, flow length and area must be positive; effective porosity must be in (0,1].")
    gradient = (data["head_downstream_m"] - data["head_upstream_m"]) / length
    flux = -k * gradient
    return {"summary": "Signed 1-D steady Darcy flow: positive discharge follows the upstream-to-downstream axis.",
            "values": {"hydraulic_gradient_1": gradient, "darcy_flux_m_s": flux,
                       "discharge_m3_s": flux * area, "pore_velocity_m_s": flux / porosity}}


_EULER_GAMMA = 0.5772156649015328606


def _expint_e1(x: float) -> float:
    """E1(x) = integral_x^infty exp(-t)/t dt; series or Lentz CF."""
    if not x > 0 or not math.isfinite(x):
        raise ValueError("Theis dimensionless well argument must be positive and finite.")
    if x <= 1:
        summation = 0.0
        term = 1.0
        for k in range(1, 300):
            term *= -x / k
            delta = term / k
            summation += delta
            if abs(delta) <= 2e-16 * max(1, abs(summation)):
                break
        return -_EULER_GAMMA - math.log(x) - summation
    if x > 745:
        return 0.0
    b, c, d = x + 1, 1e300, 1 / (x + 1)
    h = d
    for i in range(1, 500):
        a = -i * i
        b += 2
        d = 1 / (a * d + b)
        c = b + a / c
        factor = c * d
        h *= factor
        if abs(factor - 1) < 3e-15:
            break
    return h * math.exp(-x)


def theis_drawdown(data):
    q, trans, storage = data["pumping_rate_m3_s"], data["transmissivity_m2_s"], data["storativity_1"]
    r, t = data["radial_distance_m"], data["time_since_pumping_s"]
    if q < 0 or trans <= 0 or not 0 < storage <= 1 or r <= 0 or t <= 0:
        raise ValueError("Require pumping rate ≥0, positive transmissivity/distance/time and storativity in (0,1].")
    # r², T*t and their quotient can overflow/underflow individually even
    # when the dimensionless Theis argument is representable.
    log_u = 2 * (math.log(r) - math.log(2)) + math.log(storage) - math.log(trans) - math.log(t)
    if not (math.log(float.fromhex("0x0.0000000000001p-1022")) <= log_u
            <= math.log(float.fromhex("0x1.fffffffffffffp+1023"))):
        raise ValueError("Theis dimensionless well argument is outside the supported numerical range.")
    u = math.exp(log_u)
    w = _expint_e1(u)
    s = (q / (4 * math.pi * trans)) * w
    return {"summary": "Theis confined-aquifer drawdown for a fully penetrating constant-rate well (no boundaries).",
            "values": {"drawdown_m": s, "theis_u_1": u, "well_function_e1_1": w}}


def moment_magnitude(data):
    moment = data["seismic_moment_nm"]
    if moment <= 0:
        raise ValueError("Seismic moment must be positive in N·m.")
    mw = (2 / 3) * (math.log10(moment) - 9.1)
    energy = 10 ** (1.5 * mw + 4.8)
    if energy == 0:
        raise ValueError("Seismic moment implies radiated energy below the supported numerical range.")
    return {"summary": "Moment magnitude Mw from M0 (N·m); radiated energy is an empirical estimate, not M0.",
            "values": {"moment_magnitude_mw": mw, "estimated_radiated_energy_j": energy}}


def seismic_moment(data):
    mw = data["moment_magnitude_mw"]
    moment_log = 1.5 * mw + 9.1
    energy_log = 1.5 * mw + 4.8
    # Both quantities must be positive, finite IEEE-754 binary64 values.
    # A fixed 10^300 cutoff wrongly rejects representable moments up to ~10^308.
    min_log = math.log10(float.fromhex("0x0.0000000000001p-1022"))
    max_log = math.log10(float.fromhex("0x1.fffffffffffffp+1023"))
    if not all(min_log <= exponent <= max_log for exponent in (moment_log, energy_log)):
        raise ValueError("Magnitude implies seismic moment or energy outside the supported numerical range.")
    return {"summary": "Seismic moment derived from Mw; energy uses the approximate Gutenberg–Richter energy relation.",
            "values": {"seismic_moment_nm": 10 ** moment_log,
                       "estimated_radiated_energy_j": 10 ** energy_log}}


def gutenberg_richter(data):
    mags = data["magnitudes_mw"]
    mc, delta, target = data["completeness_magnitude_mw"], data["bin_width_mw"], data["target_magnitude_mw"]
    if delta <= 0 or delta > 2:
        raise ValueError("Magnitude bin width must be positive and no greater than 2 Mw.")
    if target < mc:
        raise ValueError("Target magnitude must be at or above the catalogue completeness magnitude Mc.")
    complete = [m for m in mags if m >= mc]
    if len(complete) < 2:
        raise ValueError("At least two events above the magnitude of completeness are required.")
    mean_excess = math.fsum(m - mc for m in complete) / len(complete)
    denominator = mean_excess + delta / 2
    b = math.log10(math.e) / denominator
    a = math.log10(len(complete)) + b * mc
    count_est = 10 ** (math.log10(len(complete)) - b * (target - mc))
    if not math.isfinite(count_est):
        raise ValueError("Target magnitude causes numerical overflow in the fitted frequency.")
    observed = sum(m >= target for m in complete)
    # Decimal reported bin edges must not shift past an observed magnitude
    # (or lose the final bin) through binary floating-point accumulation.
    mc_bin, delta_bin = Fraction(str(mc)), Fraction(str(delta))
    max_steps = min(100, int((Fraction(str(max(complete))) - mc_bin) / delta_bin) + 1)
    rows = []
    for index in range(max_steps):
        threshold = float(mc_bin + index * delta_bin)
        log_count = math.log10(len(complete)) - b * (threshold - mc)
        rows.append([threshold, sum(m >= threshold for m in complete), 10 ** log_count])
    return {"summary": f"Gutenberg–Richter MLE b={b:.4g}/Mw from {len(complete)} complete events; Aki bin correction.",
            "values": {"events_above_completeness_1": len(complete), "events_below_completeness_1": len(mags) - len(complete),
                       "a_intercept_log10_count_1": a, "b_value_per_mw": b,
                       "b_approx_standard_error_per_mw": b / math.sqrt(len(complete)),
                       "predicted_count_at_target_1": count_est, "observed_count_at_target_1": observed},
            "table": {"columns": ["Minimum magnitude (Mw)", "Observed cumulative count (1)",
                                  "Fitted cumulative count (1)"], "rows": rows}}


# US Standard Atmosphere 1976 / ISA dry-air layers (geopotential m, K/m).
_ISA_LAYERS = ((0, -0.0065), (11000, 0.0), (20000, 0.001),
               (32000, 0.0028), (47000, 0.0), (51000, -0.0028), (71000, -0.002))
_ISA_RE = 6356766.0
_ISA_R = 8314.32 / 28.9644
_ISA_G = 9.80665


def isa_atmosphere(data):
    geometric = data["geometric_altitude_m"]
    if not -2000 <= geometric <= 86000:
        raise ValueError("This ISA lower-atmosphere model covers geometric altitudes -2000 to 86000 m.")
    h = _ISA_RE * geometric / (_ISA_RE + geometric)
    tbase, pbase = 288.15, 101325.0
    for index, (base, lapse) in enumerate(_ISA_LAYERS):
        end = _ISA_LAYERS[index + 1][0] if index + 1 < len(_ISA_LAYERS) else h
        dh = min(h, end) - base
        if index == 0 and h < 0:
            dh = h
        if dh >= 0 or (index == 0 and h < 0):
            temp = tbase + lapse * dh
            if lapse:
                p = pbase * (temp / tbase)**(-_ISA_G / (_ISA_R * lapse))
            else:
                p = pbase * math.exp(-_ISA_G * dh / (_ISA_R * tbase))
            if h <= end or index == len(_ISA_LAYERS) - 1:
                density = p / (_ISA_R * temp)
                return {"summary": "1976 ISA hydrostatic, dry-air standard profile (geometric altitude converted to geopotential).",
                        "values": {"geopotential_altitude_m": h, "temperature_k": temp,
                                   "temperature_c": temp - 273.15, "pressure_pa": p, "density_kg_m3": density}}
            tbase, pbase = temp, p
    raise ValueError("Altitude outside ISA layer range.")


def solar_position(data):
    latitude, longitude = data["latitude_deg"], data["longitude_deg"]
    _coordinates(latitude, longitude)
    try:
        stamp = datetime.fromisoformat(data["timestamp_iso8601"].replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Supply an ISO 8601 timestamp with UTC offset, e.g. 2024-03-20T12:00:00Z.") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Solar position requires a timezone-aware ISO 8601 timestamp (Z or ±hh:mm offset).")
    stamp = stamp.astimezone(UTC)
    if not 1901 <= stamp.year <= 2099:
        raise ValueError("NOAA solar approximation is validated for years 1901–2099.")
    day_fraction = (stamp.hour + (stamp.minute + (stamp.second + stamp.microsecond / 1e6) / 60) / 60) / 24
    jd = stamp.toordinal() + 1721424.5 + day_fraction
    century = (jd - 2451545) / 36525
    l0 = (280.46646 + century * (36000.76983 + century * 0.0003032)) % 360
    m = 357.52911 + century * (35999.05029 - 0.0001537 * century)
    eccentricity = 0.016708634 - century * (0.000042037 + 0.0000001267 * century)
    mr = math.radians(m)
    center = (math.sin(mr) * (1.914602 - century * (0.004817 + 0.000014 * century)) +
              math.sin(2 * mr) * (0.019993 - 0.000101 * century) + math.sin(3 * mr) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * century)
    apparent_long = math.radians(l0 + center - 0.00569 - 0.00478 * math.sin(omega))
    mean_obliq = 23 + (26 + (21.448 - century * (46.815 + century * (0.00059 - century * 0.001813))) / 60) / 60
    eps = math.radians(mean_obliq + 0.00256 * math.cos(omega))
    decl = math.asin(math.sin(eps) * math.sin(apparent_long))
    y = math.tan(eps / 2)**2
    l0r = math.radians(l0)
    eqtime_min = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * eccentricity * math.sin(mr) +
                 4 * eccentricity * y * math.sin(mr) * math.cos(2 * l0r) -
                 0.5 * y**2 * math.sin(4 * l0r) - 1.25 * eccentricity**2 * math.sin(2 * mr))
    utc_min = day_fraction * 1440
    true_solar_min = (utc_min + eqtime_min + 4 * longitude) % 1440
    hour_angle = math.radians(true_solar_min / 4 - 180)
    lat = math.radians(latitude)
    cos_zenith = (math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle))
    zenith = math.acos(max(-1.0, min(1.0, cos_zenith)))
    geometric_elevation = 90 - math.degrees(zenith)
    az_undefined = abs(latitude) == 90 or abs(math.sin(zenith)) < 1e-13
    azimuth = 0.0 if az_undefined else (math.degrees(math.atan2(math.sin(hour_angle),
                        math.cos(hour_angle) * math.sin(lat) - math.tan(decl) * math.cos(lat))) + 180) % 360
    e = geometric_elevation
    if e > 85:
        refraction = 0.0
    elif e > 5:
        tan_e = math.tan(math.radians(e))
        refraction = (58.1 / tan_e - 0.07 / tan_e**3 + 0.000086 / tan_e**5) / 3600
    elif e > -0.575:
        refraction = (1735 + e * (-518.2 + e * (103.4 + e * (-12.79 + e * 0.711)))) / 3600
    else:
        refraction = (-20.772 / math.tan(math.radians(e))) / 3600
    return {"summary": "NOAA approximate solar coordinates; azimuth clockwise from true north" +
                        (" (undefined at pole/zenith; 0° convention)." if az_undefined else "."),
            "values": {"elevation_deg": geometric_elevation + refraction,
                       "geometric_elevation_deg": geometric_elevation, "azimuth_deg": azimuth,
                       "declination_deg": math.degrees(decl), "equation_of_time_min": eqtime_min,
                       "standard_refraction_correction_deg": refraction}}


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "great_circle_distance",
        "Great-circle distance",
        "Compute the shorter surface arc between two latitude/longitude points on a sphere of "
        "radius 6371.0088 km. Coordinates use degrees north and east.",
        (
            Field("latitude_a", "Start latitude", "number", unit="°"),
            Field("longitude_a", "Start longitude", "number", unit="°"),
            Field("latitude_b", "End latitude", "number", unit="°"),
            Field("longitude_b", "End longitude", "number", unit="°"),
        ),
        {"latitude_a": 0, "longitude_a": 179, "latitude_b": 0, "longitude_b": -179},
        great_circle_distance,
    ),
    Operation(
        "geothermal_gradient",
        "Two-depth geothermal gradient",
        "Estimate the signed average change in measured temperature with depth in a borehole.",
        (
            Field("shallow_depth_m", "Shallow sample depth", "number", unit="m"),
            Field("deep_depth_m", "Deep sample depth", "number", unit="m"),
            Field("shallow_temperature_c", "Shallow temperature", "number", unit="°C"),
            Field("deep_temperature_c", "Deep temperature", "number", unit="°C"),
        ),
        {"shallow_depth_m": 100, "deep_depth_m": 900, "shallow_temperature_c": 14, "deep_temperature_c": 34},
        geothermal_gradient,
        headline="gradient_c_per_km",
    ),
    Operation(
        "seismic_distance",
        "Seismic S–P distance",
        "Estimate source-to-station distance from the arrival lag of primary and shear waves "
        "assuming a common straight path and constant speeds in a homogeneous medium.",
        (
            Field("s_minus_p_s", "S arrival minus P arrival", "number", unit="s"),
            Field("p_speed_km_s", "P-wave speed", "number", unit="km/s"),
            Field("s_speed_km_s", "S-wave speed", "number", unit="km/s"),
        ),
        {"s_minus_p_s": 10, "p_speed_km_s": 6, "s_speed_km_s": 3.5},
        seismic_distance,
        headline="path_length_km",
    ),
    Operation(
        "geodesic_inverse", "WGS84 distance and bearings",
        "Vincenty ellipsoidal inverse; near-antipodal fallback uses a WGS84 Vincenty direct-geodesic solve. "
        "Bearings are clockwise from geodetic north (the reverse bearing points B→A).",
        (Field("latitude_a_deg", "Point A geodetic latitude", "number", unit="°N"),
         Field("longitude_a_deg", "Point A longitude", "number", unit="°E"),
         Field("latitude_b_deg", "Point B geodetic latitude", "number", unit="°N"),
         Field("longitude_b_deg", "Point B longitude", "number", unit="°E")),
        {"latitude_a_deg": -37.95103341666667, "longitude_a_deg": 144.4248678888889,
         "latitude_b_deg": -37.65282113888889, "longitude_b_deg": 143.92649552777777},
        geodesic_inverse,
        reference_datasets=("earth.wgs84_ellipsoid",),
    ),
    Operation(
        "utm_forward", "Convert WGS84 latitude/longitude to UTM",
        "Automatic WGS84 zone (including Norway/Svalbard exceptions), 0.9996 central scale, "
        "500000 m false easting and southern false northing. Latitude domain 80°S to 84°N.",
        (Field("latitude_deg", "WGS84 latitude", "number", unit="°N"),
         Field("longitude_deg", "WGS84 longitude", "number", unit="°E")),
        {"latitude_deg": 40.0, "longitude_deg": -105.0}, utm_forward,
        reference_datasets=("earth.wgs84_ellipsoid",),
    ),
    Operation(
        "utm_inverse", "Convert WGS84 UTM to latitude/longitude",
        "Inverse transverse Mercator (WGS84); N/S is UTM hemisphere, not latitude band.",
        (Field("zone_1", "UTM zone number", "integer", unit="1 (zone)"),
         Field("hemisphere", "UTM hemisphere (N or S)", "text", unit="N/S"),
         Field("easting_m", "UTM easting", "number", unit="m"),
         Field("northing_m", "UTM northing", "number", unit="m")),
        {"zone_1": 13, "hemisphere": "N", "easting_m": 500000.0, "northing_m": 4427757.2189},
        utm_inverse,
        reference_datasets=("earth.wgs84_ellipsoid",),
    ),
    Operation(
        "radiometric_age", "Calculate closed-system radiometric age",
        "Use t = half-life/ln(2) × ln(1 + radiogenic daughter/remaining parent). "
        "Ratios are relative to the same stable isotope/normalizer; daughter is initial-daughter corrected. "
        "Only 1σ half-life uncertainty is propagated.",
        (Field("parent_ratio_1", "Remaining parent isotope ratio", "number", unit="1"),
         Field("radiogenic_daughter_ratio_1", "Radiogenic daughter isotope ratio", "number", unit="1"),
         Field("half_life_ma", "Half-life", "number", unit="Ma"),
         Field("half_life_standard_uncertainty_ma", "Half-life 1σ standard uncertainty", "number", unit="Ma")),
        {"parent_ratio_1": 1, "radiogenic_daughter_ratio_1": 1,
         "half_life_ma": 1250, "half_life_standard_uncertainty_ma": 5}, radiometric_age,
    ),
    Operation(
        "darcy_flow", "Darcy groundwater flow and pore velocity",
        "Steady saturated 1-D Darcy flow; positive flow runs from the named upstream section "
        "to downstream. Pore velocity assumes effective connected porosity.",
        (Field("hydraulic_conductivity_m_s", "Hydraulic conductivity", "number", unit="m/s"),
         Field("head_upstream_m", "Upstream hydraulic head", "number", unit="m"),
         Field("head_downstream_m", "Downstream hydraulic head", "number", unit="m"),
         Field("flow_length_m", "Distance between head samples", "number", unit="m"),
         Field("cross_section_area_m2", "Cross-sectional flow area", "number", unit="m²"),
         Field("effective_porosity_1", "Effective connected porosity", "number", unit="1")),
        {"hydraulic_conductivity_m_s": 0.0001, "head_upstream_m": 10, "head_downstream_m": 9,
         "flow_length_m": 100, "cross_section_area_m2": 20, "effective_porosity_1": 0.25}, darcy_flow,
    ),
    Operation(
        "theis_drawdown", "Theis confined aquifer well drawdown",
        "Transient drawdown from a fully penetrating constant-rate well in an infinite homogeneous "
        "confined aquifer, using the exponential integral E1(u).",
        (Field("pumping_rate_m3_s", "Extraction rate", "number", unit="m³/s"),
         Field("transmissivity_m2_s", "Aquifer transmissivity", "number", unit="m²/s"),
         Field("storativity_1", "Confined aquifer storativity", "number", unit="1"),
         Field("radial_distance_m", "Radial distance from well", "number", unit="m"),
         Field("time_since_pumping_s", "Elapsed pumping time", "number", unit="s")),
        {"pumping_rate_m3_s": 0.02, "transmissivity_m2_s": 0.001,
         "storativity_1": 0.0001, "radial_distance_m": 100, "time_since_pumping_s": 3600}, theis_drawdown,
    ),
    Operation(
        "moment_magnitude", "Seismic moment to moment magnitude Mw",
        "Convert M0 (N·m) to Mw via Mw=(2/3)(log10(M0)−9.1); "
        "empirical energy estimate log10(E[J])=1.5Mw+4.8.",
        (Field("seismic_moment_nm", "Scalar seismic moment", "number", unit="N·m"),),
        {"seismic_moment_nm": 1e18}, moment_magnitude,
        headline="moment_magnitude_mw",
    ),
    Operation(
        "seismic_moment", "Moment magnitude Mw to seismic moment",
        "Inverse Hanks–Kanamori conversion; estimated energy is not the exact measured radiated energy.",
        (Field("moment_magnitude_mw", "Moment magnitude Mw", "number", unit="Mw (1)"),),
        {"moment_magnitude_mw": 6}, seismic_moment,
        headline="seismic_moment_nm",
    ),
    Operation(
        "gutenberg_richter", "Fit Gutenberg–Richter b-value",
        "Maximum-likelihood b-value for a catalog above a specified complete magnitude with "
        "the Aki rounded-bin correction. Fits log10 N(M≥m)=a−bm; gives approximate b/√N uncertainty.",
        (Field("magnitudes_mw", "Catalog event magnitudes", "vector", unit="Mw (1)"),
         Field("completeness_magnitude_mw", "Magnitude of completeness Mc", "number", unit="Mw (1)"),
         Field("bin_width_mw", "Magnitude reporting bin size", "number", unit="Mw (1)"),
         Field("target_magnitude_mw", "Prediction threshold", "number", unit="Mw (1)")),
        {"magnitudes_mw": [2.0, 2.1, 2.1, 2.2, 2.3, 2.4, 2.5, 2.7, 2.9, 3.0],
         "completeness_magnitude_mw": 2.0, "bin_width_mw": 0.1, "target_magnitude_mw": 3.0},
        gutenberg_richter,
        headline="b_value_per_mw",
    ),
    Operation(
        "isa_atmosphere", "International Standard Atmosphere profile",
        "Dry-air 1976 ISA to 86 km geometric altitude, with geometric→geopotential correction. "
        "Pressure, density and temperature are model values, not weather observations.",
        (Field("geometric_altitude_m", "Geometric altitude above sea level", "number", unit="m"),),
        {"geometric_altitude_m": 11019.0678}, isa_atmosphere,
        reference_datasets=("earth.standard_atmosphere_1976",),
        headline="pressure_pa",
    ),
    Operation(
        "solar_position", "Solar elevation and true-north azimuth",
        "NOAA Julian-century approximation with standard atmospheric refraction; east-positive "
        "longitude, north-positive latitude and timezone-aware ISO 8601 instant (1901–2099).",
        (Field("timestamp_iso8601", "Observation date/time with timezone", "text", unit="ISO 8601 timestamp"),
         Field("latitude_deg", "Observer latitude", "number", unit="°N"),
         Field("longitude_deg", "Observer longitude", "number", unit="°E")),
        {"timestamp_iso8601": "2024-03-20T12:00:00Z", "latitude_deg": 0, "longitude_deg": 0},
        solar_position,
        headline="elevation_deg",
    ),

)
