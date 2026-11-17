"""One unregistered drone, identified by the ground EO and localized by ground radar.

Its route is a seeded random flight inside the fence. The inspection aircraft keeps
its own route. When it is high enough, its gimbal points at the filtered position.
A matching cooperative report stays 合作. Ground EO class “无人机” with no cooperative
report is reported as 身份未确认无人机. That label is a synthetic sensor rule, not video
recognition and not an enforcement decision.
"""
import math
import random
import threading

LOCK = threading.Lock()
STATIONS = (
    {"id": "R-SW", "type": "RADAR", "xyz": (360.0, 480.0, 40.0)},
    {"id": "R-NE", "type": "RADAR", "xyz": (1450.0, 1350.0, 40.0)},
    {"id": "E-CORE", "type": "EO", "xyz": (970.0, 1020.0, 50.0)},
)
RANGE_SIGMA = 12.0
RADAR_ANGLE = math.radians(0.4)
EO_ANGLE = math.radians(0.3)


def _noise(key):
    n = 2166136261
    for ch in str(key):
        n ^= ord(ch)
        n = (n * 16777619) & 0xFFFFFFFF
    u1 = ((n & 0xFFFF) + 1) / 65536.0
    u2 = (((n >> 16) & 0xFFFF) + 1) / 65536.0
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _look(station, point):
    dx, dy, dz = point[0] - station[0], point[1] - station[1], point[2] - station[2]
    horiz = math.hypot(dx, dy)
    return math.sqrt(horiz * horiz + dz * dz), math.atan2(dx, dy), math.atan2(dz, max(horiz, 1e-6))


def _wrap(angle):
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _radar_point(station, slant, azimuth, elevation):
    horiz = slant * math.cos(elevation)
    return [
        station[0] + horiz * math.sin(azimuth),
        station[1] + horiz * math.cos(azimuth),
        station[2] + slant * math.sin(elevation),
    ]


_FLIGHT = {}


def reset_flight():
    _FLIGHT.clear()
    _FLIGHT.update(points=[(1100.0, 980.0, 80.0)], heading=0.6, rng=random.Random(20261018))


def truth_at(second):
    """Seeded random flight. The same second always returns the same point after reset."""
    if not _FLIGHT:
        reset_flight()
    second = max(0, int(second))
    while len(_FLIGHT["points"]) <= second:
        _FLIGHT["heading"] += _FLIGHT["rng"].uniform(-0.5, 0.5)
        x, y, _z = _FLIGHT["points"][-1]
        x += 10.0 * math.sin(_FLIGHT["heading"])
        y += 10.0 * math.cos(_FLIGHT["heading"])
        z = 75.0 + 12.0 * math.sin(len(_FLIGHT["points"]) / 13.0)
        if x < 80.0 or x > 1920.0:
            _FLIGHT["heading"] = math.pi - _FLIGHT["heading"]
            x = min(1920.0, max(80.0, x))
        if y < 80.0 or y > 1920.0:
            _FLIGHT["heading"] = -_FLIGHT["heading"]
            y = min(1920.0, max(80.0, y))
        _FLIGHT["points"].append((round(x, 1), round(y, 1), round(z, 1)))
    return _FLIGHT["points"][second]


def identity_of(measurements):
    reported = [item["cooperative_id"] for item in measurements if item.get("cooperative_id")]
    if reported and len(set(reported)) == 1:
        return "合作", "报文身份一致"
    if len(set(reported)) > 1:
        return "身份待核验", "报文身份互相冲突"
    ground_eo = [item for item in measurements if item.get("ground") and item.get("type") == "EO"]
    if ground_eo and all(item.get("class_name") == "无人机" for item in ground_eo):
        return "身份未确认无人机", "地面光电类别为无人机，但未匹配合作报文，需核验飞行计划"
    return "身份待核验", "没有匹配到合作报文"


def _measure(truth, second, own):
    measurements = []
    for station in STATIONS:
        slant, azimuth, elevation = _look(station["xyz"], truth)
        if station["type"] == "RADAR":
            measurements.append({
                "sensor_id": station["id"], "type": "RADAR", "station": station["xyz"],
                "range_m": slant + RANGE_SIGMA * _noise(f"{second}:{station['id']}:r"),
                "azimuth": azimuth + RADAR_ANGLE * _noise(f"{second}:{station['id']}:a"),
                "elevation": elevation + RADAR_ANGLE * _noise(f"{second}:{station['id']}:e"),
            })
        else:
            measurements.append({
                "sensor_id": station["id"], "type": "EO", "ground": True, "station": station["xyz"],
                "class_name": "无人机",
                "range_m": None,
                "azimuth": azimuth + EO_ANGLE * _noise(f"{second}:{station['id']}:a"),
                "elevation": elevation + EO_ANGLE * _noise(f"{second}:{station['id']}:e"),
            })
    if own is not None and own.position[2] >= 2.0 and own.payload == "光电吊舱":
        _, azimuth, elevation = _look(own.position, truth)
        measurements.append({
            "sensor_id": own.id, "type": "EO", "station": tuple(own.position),
            "range_m": None,
            "azimuth": azimuth + EO_ANGLE * _noise(f"{second}:{own.id}:a"),
            "elevation": elevation + EO_ANGLE * _noise(f"{second}:{own.id}:e"),
        })
    return measurements


def _residuals(point, measurements):
    values = []
    for item in measurements:
        slant, azimuth, elevation = _look(item["station"], point)
        if item["range_m"] is not None:
            values.append((slant - item["range_m"]) / RANGE_SIGMA)
        angle = RADAR_ANGLE if item["type"] == "RADAR" else EO_ANGLE
        values.append(_wrap(azimuth - item["azimuth"]) / angle)
        values.append((elevation - item["elevation"]) / angle)
    return values


def _solve3(matrix, vector):
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ZeroDivisionError
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        for j in range(col, 4):
            a[col][j] /= scale
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col]
            for j in range(col, 4):
                a[row][j] -= factor * a[col][j]
    return [a[i][3] for i in range(3)]


def _localize(measurements):
    seeds = [_radar_point(item["station"], item["range_m"], item["azimuth"], item["elevation"])
             for item in measurements if item["type"] == "RADAR"]
    if not seeds:
        return None
    point = [sum(seed[axis] for seed in seeds) / len(seeds) for axis in range(3)]
    damping = 1.0
    for _ in range(12):
        residual = _residuals(point, measurements)
        cost = sum(value * value for value in residual)
        columns = []
        for axis in range(3):
            plus, minus = point[:], point[:]
            plus[axis] += 0.5
            minus[axis] -= 0.5
            high, low = _residuals(plus, measurements), _residuals(minus, measurements)
            columns.append([(high[k] - low[k]) for k in range(len(residual))])
        normal = [[sum(columns[a][k] * columns[b][k] for k in range(len(residual))) for b in range(3)] for a in range(3)]
        projected = [sum(columns[a][k] * residual[k] for k in range(len(residual))) for a in range(3)]
        for axis in range(3):
            normal[axis][axis] += damping
        try:
            step = _solve3(normal, projected)
        except ZeroDivisionError:
            break
        if not all(math.isfinite(value) for value in step):
            break
        accepted = None
        for _line in range(6):
            candidate = [min(2500.0, max(-200.0, point[axis] - damping * step[axis])) for axis in range(3)]
            trial = _residuals(candidate, measurements)
            if sum(value * value for value in trial) <= cost + 1e-6:
                accepted = candidate
                damping = min(1.0, damping * 2)
                break
            damping *= 0.5
        if accepted is None or math.dist(point, [point[axis] - step[axis] for axis in range(3)]) < 0.05:
            if accepted is not None:
                point = accepted
            break
        point = accepted
    return point


def _eye(n):
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))] for i in range(len(a))]


def _transpose(matrix):
    return [list(row) for row in zip(*matrix)]


def _matvec(matrix, vector):
    return [sum(row[j] * vector[j] for j in range(len(vector))) for row in matrix]


def _add(a, b):
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def _inv3(matrix):
    inverse = _eye(3)
    work = [matrix[i][:] for i in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(work[row][col]))
        if abs(work[pivot][col]) < 1e-12:
            raise ZeroDivisionError
        work[col], work[pivot] = work[pivot], work[col]
        inverse[col], inverse[pivot] = inverse[pivot], inverse[col]
        scale = work[col][col]
        for j in range(3):
            work[col][j] /= scale
            inverse[col][j] /= scale
        for row in range(3):
            if row == col:
                continue
            factor = work[row][col]
            for j in range(3):
                work[row][j] -= factor * work[col][j]
                inverse[row][j] -= factor * inverse[col][j]
    return inverse


def _gimbal(own, estimate):
    dx, dy, dz = estimate[0] - own.position[0], estimate[1] - own.position[1], estimate[2] - own.position[2]
    horiz = math.hypot(dx, dy)
    bearing = math.degrees(math.atan2(dx, dy))
    if bearing < 0:
        bearing += 360.0
    pitch = math.degrees(math.atan2(dz, max(horiz, 1e-6)))
    pitch = min(20.0, max(-90.0, pitch))
    yaw = (bearing - own.yaw + 180.0) % 360.0 - 180.0
    return pitch, yaw


class _Track:
    def __init__(self):
        self.reset()

    def reset(self):
        self.second = 0
        self.truth = truth_at(0)
        self.x = None
        self.P = None
        self.hits = 0
        self.observed = False
        self.identity = "身份待核验"
        self.reason = "没有匹配到合作报文"
        self.sources = []
        self.pointing = False
        self.track = []

    def _predict(self):
        motion = _eye(6)
        for axis in range(3):
            motion[axis][axis + 3] = 1.0
        self.x = _matvec(motion, self.x)
        gain = [[0.0] * 3 for _ in range(6)]
        for axis in range(3):
            gain[axis][axis] = 0.5
            gain[axis + 3][axis] = 1.0
        process = _matmul(_matmul(gain, [[9.0 if i == j else 0.0 for j in range(3)] for i in range(3)]), _transpose(gain))
        self.P = _add(_matmul(_matmul(motion, self.P), _transpose(motion)), process)

    def _assimilate(self, measurement):
        if self.x is None:
            self.x = [measurement[0], measurement[1], measurement[2], 0.0, 0.0, 0.0]
            self.P = [[400.0 if i == j < 3 else 64.0 if i == j else 0.0 for j in range(6)] for i in range(6)]
            self.hits = 1
            self.observed = True
            return
        self._predict()
        innovation = [measurement[i] - self.x[i] for i in range(3)]
        if math.sqrt(sum(value * value for value in innovation)) > 80.0:
            self.observed = False
            return
        position = [[self.P[i][j] for j in range(3)] for i in range(6)]
        covariance = [[self.P[i][j] + (324.0 if i == j else 0.0) for j in range(3)] for i in range(3)]
        gain = _matmul(position, _inv3(covariance))
        correction = _matvec(gain, innovation)
        self.x = [self.x[i] + correction[i] for i in range(6)]
        eye = _eye(6)
        for i in range(6):
            for j in range(3):
                eye[i][j] -= gain[i][j]
        noise = _matmul(_matmul(gain, [[324.0 if i == j else 0.0 for j in range(3)] for i in range(3)]), _transpose(gain))
        self.P = _add(_matmul(_matmul(eye, self.P), _transpose(eye)), noise)
        self.hits += 1
        self.observed = True

    def advance(self, own):
        with LOCK:
            self.second += 1
            self.truth = truth_at(self.second)
            measurements = _measure(self.truth, self.second, own)
            self.identity, self.reason = identity_of(measurements)
            self.sources = [item["sensor_id"] for item in measurements]
            point = _localize(measurements)
            if point is None:
                if self.x is not None:
                    self._predict()
                self.observed = False
            else:
                self._assimilate(point)
            self.pointing = False
            if self.x is not None and self.hits >= 3 and self.observed and own is not None and own.position[2] >= 2.0 and own.payload == "光电吊舱":
                own.gimbal_pitch, own.gimbal_yaw = _gimbal(own, self.x[:3])
                self.pointing = True
            if self.x is not None:
                self.track.append([round(self.x[0], 1), round(self.x[1], 1), round(self.x[2], 1)])
                if len(self.track) > 400:
                    del self.track[:-400]

    def public(self):
        with LOCK:
            if self.x is None:
                estimate = None
                state = "idle"
            else:
                estimate = [round(value, 1) for value in self.x[:3]]
                state = "confirmed" if self.hits >= 3 and self.observed else "tentative" if self.observed else "coasting"
            trace = math.sqrt(sum(self.P[i][i] for i in range(3))) if self.P is not None else None
            return {
                "second": self.second,
                "state": state,
                "identity": self.identity,
                "reason": self.reason,
                "estimate": estimate,
                "velocity": [round(value, 3) for value in self.x[3:6]] if self.x is not None else None,
                "uncertainty_m": round(trace, 1) if trace is not None else None,
                "sources": list(self.sources),
                "pointing": self.pointing,
                "track": [point[:] for point in self.track],
                "target": "非合作演练无人机",
                "localization": "地面雷达距离与光电角度融合",
                "evidence_status": "synthetic_only",
                "note": "识别来自地面光电的目标类型，没有合作报文时记为身份未确认无人机。定位来自地面雷达距离与光电角度的融合跟踪。二者都是合成量测，不是视频识别，也不是执法结论。",
            }


TRACK = _Track()


def reset():
    reset_flight()
    TRACK.reset()


def advance(own):
    TRACK.advance(own)


def public():
    return TRACK.public()


def truth_position():
    with LOCK:
        return tuple(TRACK.truth)
