"""Synthetic multi-station measurements for offline training.

Truth target ids are kept only so the experiment can score errors.
The estimator is not allowed to read them.
"""
import numpy as np

STATIONS = (
    {"id": "R-01", "type": "RADAR", "xyz": np.array([250.0, 250.0, 30.0])},
    {"id": "R-02", "type": "RADAR", "xyz": np.array([1750.0, 400.0, 28.0])},
    {"id": "E-01", "type": "EO", "xyz": np.array([400.0, 1650.0, 36.0])},
    {"id": "F-01", "type": "RF", "xyz": np.array([1550.0, 1550.0, 22.0])},
    {"id": "A-01", "type": "ADSB", "xyz": np.array([1000.0, 1000.0, 15.0])},
)

NOISE = {
    "RADAR": {"range": 12.0, "az": np.deg2rad(0.6), "el": np.deg2rad(0.7)},
    "EO": {"az": np.deg2rad(0.2), "el": np.deg2rad(0.2)},
    "RF": {"az": np.deg2rad(2.0)},
    "ADSB": {"xyz": 6.0},
}
DETECT = {"RADAR": 0.9, "EO": 0.75, "RF": 0.65, "ADSB": 0.92}


def wrap(angle):
    return (angle + np.pi) % (2 * np.pi) - np.pi


def look(station, point):
    delta = np.asarray(point, float) - np.asarray(station, float)
    horizontal = np.hypot(delta[0], delta[1])
    slant = np.linalg.norm(delta)
    azimuth = np.atan2(delta[0], delta[1])
    elevation = np.atan2(delta[2], max(horizontal, 1e-6))
    return slant, azimuth, elevation


def radar_point(station, range_m, azimuth, elevation):
    horizontal = range_m * np.cos(elevation)
    offset = np.array([
        horizontal * np.sin(azimuth),
        horizontal * np.cos(azimuth),
        range_m * np.sin(elevation),
    ])
    return np.asarray(station, float) + offset


def _targets(rng, steps, crossing):
    if crossing:
        speed = float(rng.uniform(12.0, 16.0))
        first, second = [], []
        for time in range(steps):
            along = (time - steps // 2) * speed
            first.append([1000.0 + along, 1000.0 + rng.normal(0, 2.0), 80.0])
            second.append([1000.0 + rng.normal(0, 2.0), 1000.0 + along, 70.0])
        paths = (np.asarray(first), np.asarray(second))
    else:
        paths = []
        for _ in range(2):
            point = np.array([rng.uniform(400, 1600), rng.uniform(400, 1600), rng.uniform(50, 120)])
            speed = float(rng.uniform(10.0, 18.0))
            heading = float(rng.uniform(-np.pi, np.pi))
            samples = []
            for _time in range(steps):
                velocity = np.array([speed * np.sin(heading), speed * np.cos(heading), 0.0])
                point = np.clip(point + velocity, [80, 80, 40], [1920, 1920, 160])
                if rng.random() < 0.04:
                    heading += float(rng.normal(0, 0.4))
                samples.append(point.copy())
            paths.append(np.asarray(samples))
    return (
        {"id": "T1", "cooperative_id": "TASK-01", "points": paths[0]},
        {"id": "T2", "cooperative_id": None, "points": paths[1]},
    )


def _measure(station, point, target, rng, interference):
    kind = station["type"]
    if kind == "ADSB" and not target["cooperative_id"]:
        return None
    probability = DETECT[kind] * (0.72 if interference and kind in {"RADAR", "RF"} else 1.0)
    if rng.random() > probability:
        return None
    slant, azimuth, elevation = look(station["xyz"], point)
    scale = 2.4 if interference and kind in {"RADAR", "RF"} else 1.0
    measured = {
        "sensor_id": station["id"], "type": kind, "station": station["xyz"].copy(),
        "azimuth": None, "elevation": None, "range_m": None, "xyz": None,
        "cooperative_id": None, "target_id": target["id"],
    }
    if kind == "RADAR":
        measured["range_m"] = slant + float(rng.normal(0, NOISE["RADAR"]["range"] * scale))
        measured["azimuth"] = azimuth + float(rng.normal(0, NOISE["RADAR"]["az"] * scale))
        measured["elevation"] = elevation + float(rng.normal(0, NOISE["RADAR"]["el"] * scale))
    elif kind == "EO":
        measured["azimuth"] = azimuth + float(rng.normal(0, NOISE["EO"]["az"]))
        measured["elevation"] = elevation + float(rng.normal(0, NOISE["EO"]["el"]))
    elif kind == "RF":
        measured["azimuth"] = azimuth + float(rng.normal(0, NOISE["RF"]["az"] * scale))
    else:
        measured["xyz"] = point + rng.normal(0, NOISE["ADSB"]["xyz"], 3)
        measured["cooperative_id"] = target["cooperative_id"]
    return measured


def _clutter(rng):
    hits = []
    for station in STATIONS:
        if station["type"] != "RADAR" or rng.random() > 0.12:
            continue
        hits.append({
            "sensor_id": station["id"], "type": "RADAR", "station": station["xyz"].copy(),
            "azimuth": float(rng.uniform(-np.pi, np.pi)),
            "elevation": float(rng.uniform(np.deg2rad(-5), np.deg2rad(25))),
            "range_m": float(rng.uniform(150, 1800)),
            "xyz": None, "cooperative_id": None, "target_id": None,
        })
    return hits


def episode(seed, steps=48, crossing=False, interference=False):
    rng = np.random.default_rng(seed)
    targets = _targets(rng, steps, crossing)
    frames = []
    for time in range(steps):
        measurements = []
        for target in targets:
            for station in STATIONS:
                hit = _measure(station, target["points"][time], target, rng, interference)
                if hit is not None:
                    measurements.append(hit)
        measurements.extend(_clutter(rng))
        frames.append({"time": time, "measurements": measurements})
    return {
        "seed": seed,
        "crossing": crossing,
        "interference": interference,
        "targets": targets,
        "frames": frames,
    }
