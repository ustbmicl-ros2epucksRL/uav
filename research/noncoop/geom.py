"""Geometric localization and identity evidence.

Identity is cooperative only when the reported remote id agrees.
Otherwise the result is 身份待核验. This module never emits a 黑飞 label.
"""
import numpy as np
from sim import NOISE, look, radar_point, wrap


def identity_of(measurements):
    reported = [item["cooperative_id"] for item in measurements if item.get("cooperative_id")]
    if reported and len(set(reported)) == 1:
        return "合作", "报文身份一致"
    if len(set(reported)) > 1:
        return "身份待核验", "报文身份互相冲突"
    return "身份待核验", "没有匹配到合作报文"


def _residuals(point, measurements):
    values = []
    for item in measurements:
        if item["type"] == "ADSB":
            values.extend((np.asarray(point, float) - item["xyz"]) / NOISE["ADSB"]["xyz"])
            continue
        slant, azimuth, elevation = look(item["station"], point)
        if item["range_m"] is not None:
            values.append((slant - item["range_m"]) / NOISE["RADAR"]["range"])
        if item["azimuth"] is not None:
            values.append(wrap(azimuth - item["azimuth"]) / NOISE[item["type"]]["az"])
        if item["elevation"] is not None:
            values.append((elevation - item["elevation"]) / NOISE[item["type"]]["el"])
    return np.asarray(values, float)


def _jacobian(point, measurements):
    columns = []
    for axis in range(3):
        step = np.zeros(3)
        step[axis] = 0.5
        columns.append((_residuals(point + step, measurements) - _residuals(point - step, measurements)) / 1.0)
    return np.column_stack(columns)


def _initial(measurements):
    points = []
    for item in measurements:
        if item["type"] == "RADAR":
            points.append(radar_point(item["station"], item["range_m"], item["azimuth"], item["elevation"]))
        elif item["type"] == "ADSB":
            points.append(np.asarray(item["xyz"], float))
    if points:
        return np.mean(points, axis=0)
    for item in measurements:
        if item["azimuth"] is not None and item["elevation"] is not None:
            return radar_point(item["station"], 500.0, item["azimuth"], item["elevation"])
    return None


def solve(measurements):
    """Gauss-Newton fit of one target position. Returns None when no usable measurement exists."""
    point = _initial(measurements)
    if point is None:
        return None
    damping = 1.0
    for _ in range(16):
        residual = _residuals(point, measurements)
        if residual.size == 0:
            return None
        cost = float(residual @ residual)
        jacobian = _jacobian(point, measurements)
        step, *_ = np.linalg.lstsq(jacobian, residual, rcond=None)
        if not np.all(np.isfinite(step)):
            break
        accepted = None
        for _line in range(6):
            candidate = np.clip(point - damping * step, -200, 2500)
            trial = _residuals(candidate, measurements)
            if float(trial @ trial) <= cost + 1e-6:
                accepted = candidate
                damping = min(1.0, damping * 2)
                break
            damping *= 0.5
        if accepted is None or np.linalg.norm(step) < 0.05:
            break
        point = accepted
    return point


def cluster(measurements, merge_m=55.0):
    """Group radar and cooperative fixes, then attach angle-only hits that look at the same point."""
    seeds = []
    angles = []
    for item in measurements:
        if item["type"] == "RADAR":
            seeds.append({"pos": radar_point(item["station"], item["range_m"], item["azimuth"], item["elevation"]), "meas": [item]})
        elif item["type"] == "ADSB":
            seeds.append({"pos": np.asarray(item["xyz"], float), "meas": [item]})
        else:
            angles.append(item)
    groups = []
    used = [False] * len(seeds)
    for index, seed in enumerate(seeds):
        if used[index]:
            continue
        group = {"meas": list(seed["meas"])}
        used[index] = True
        growing = True
        while growing:
            growing = False
            center = _seed_center(group["meas"])
            for other, item in enumerate(seeds):
                if used[other] or np.linalg.norm(item["pos"] - center) > merge_m:
                    continue
                group["meas"].extend(item["meas"])
                used[other] = True
                growing = True
        groups.append(group)
    for item in angles:
        best, best_error = None, 1e9
        limit = np.deg2rad(2.5 if item["type"] == "EO" else 8.0)
        for index, group in enumerate(groups):
            _, azimuth, elevation = look(item["station"], _seed_center(group["meas"]))
            error = abs(wrap(azimuth - item["azimuth"]))
            if item["elevation"] is not None:
                error = max(error, abs(elevation - item["elevation"]))
            if error <= limit and error < best_error:
                best, best_error = index, error
        if best is not None:
            groups[best]["meas"].append(item)
    estimates = []
    for group in groups:
        position = solve(group["meas"])
        if position is None:
            continue
        label, reason = identity_of(group["meas"])
        estimates.append({
            "position": position,
            "identity": label,
            "reason": reason,
            "sources": [item["sensor_id"] for item in group["meas"]],
            "target_ids": [item["target_id"] for item in group["meas"]],
        })
    return estimates


def _seed_center(measurements):
    points = []
    for item in measurements:
        if item["type"] == "RADAR":
            points.append(radar_point(item["station"], item["range_m"], item["azimuth"], item["elevation"]))
        elif item["type"] == "ADSB":
            points.append(np.asarray(item["xyz"], float))
    return np.mean(points, axis=0)
