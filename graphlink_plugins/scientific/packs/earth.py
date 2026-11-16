"""Spherical geometry and simple observational geoscience models."""

import math
from fractions import Fraction

from graphlink_plugins.scientific.core import Field, Operation


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


OPERATIONS = (
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
    ),
)
