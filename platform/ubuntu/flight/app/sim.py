import copy
"""服务器侧飞机飞控仿真。指令、航线与遥测可重复，不替代机载飞控固件。"""
import math
import threading
from functools import wraps

LOCK=threading.RLock()

def synchronized(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        with LOCK:return fn(*args,**kwargs)
    return wrapped

from dataclasses import dataclass, field


def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _move(pos, target, speed, dt):
    gap = _dist(pos, target)
    if gap <= speed * dt or gap < 1e-6:
        return list(target), True
    scale = speed * dt / gap
    return [pos[i] + (target[i] - pos[i]) * scale for i in range(3)], False


@dataclass
class Vehicle:
    id: str
    name: str
    pilot_id: str
    home: list
    payload: str
    position: list = field(default_factory=list)
    mode: str = "LOITER"
    armed: bool = False
    mission: list = field(default_factory=list)
    wp: int = 0
    battery: float = 100.0
    done: bool = False
    done_reason: str = ""
    speed: float = 25.0
    seconds: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    gimbal_pitch: float = -35.0
    gimbal_yaw: float = 0.0
    track: list = field(default_factory=list)
    flags: set = field(default_factory=set)
    events: list = field(default_factory=list)

    def __post_init__(self):
        if not self.position:
            self.position = list(self.home)


def fleet():
    return {
        "UAV-01": Vehicle("UAV-01", "园区巡检机", "P-01", [200.0, 200.0, 0.0], "光电吊舱"),
        "UAV-02": Vehicle("UAV-02", "廊道物流机", "P-02", [900.0, 400.0, 0.0], "报文终端"),
    }


VEHICLES = fleet()
FENCE = {"x0": 0.0, "y0": 0.0, "x1": 2000.0, "y1": 2000.0, "z0": 0.0, "z1": 300.0}


@synchronized
def reset():
    global VEHICLES
    VEHICLES = fleet()
    from . import follow
    follow.reset()


def get(vehicle_id):
    if vehicle_id not in VEHICLES:
        return None
    return VEHICLES[vehicle_id]


@synchronized
def load_mission(vehicle, points):
    if vehicle.mode not in {"LOITER", "GUIDED"}:
        raise ValueError("飞行中不能改航线")
    if len(points) < 2 or len(points) > 20:
        raise ValueError("航线点数应为 2–20")
    vehicle.mission = [[p[0], p[1], p[2]] for p in points]
    vehicle.wp = 0
    vehicle.done = False
    vehicle.done_reason = ""


@synchronized
def command(vehicle, cmd):
    if cmd == "arm":
        if vehicle.armed:
            raise ValueError("已经解锁")
        if vehicle.mode != "LOITER":
            raise ValueError("请在定点模式解锁")
        vehicle.armed = True
        vehicle.done = False
        vehicle.done_reason = ""
    elif cmd == "disarm":
        if not vehicle.armed:
            raise ValueError("尚未解锁")
        if vehicle.mode != "LOITER":
            raise ValueError("请先回到定点再上锁")
        vehicle.armed = False
    elif cmd in {"start", "auto"}:
        if not vehicle.armed or vehicle.mode != "LOITER" or len(vehicle.mission) < 2:
            raise ValueError("需在定点模式解锁并装订航线")
        vehicle.mode = "AUTO"
        vehicle.wp = 0
        vehicle.done = False
        vehicle.done_reason = ""
        vehicle.track = []
        _record(vehicle)
    elif cmd == "loiter":
        if not vehicle.armed:
            raise ValueError("未解锁")
        if vehicle.mode == "LAND":
            raise ValueError("降落中不能改模式")
        vehicle.mode = "LOITER"
        vehicle.done = False
    elif cmd == "guided":
        if not vehicle.armed or vehicle.mode != "LOITER":
            raise ValueError("请在定点解锁后进入引导")
        vehicle.mode = "GUIDED"
        vehicle.done = False
    elif cmd == "rtl":
        if not vehicle.armed or vehicle.mode in {"RTL", "LAND"}:
            raise ValueError("当前状态不能返航")
        vehicle.mode = "RTL"
        vehicle.done = False
    elif cmd == "land":
        if not vehicle.armed or vehicle.mode not in {"LOITER", "AUTO", "RTL", "GUIDED"}:
            raise ValueError("当前状态不能降落")
        vehicle.mode = "LAND"
        vehicle.done = False
    elif cmd == "stop":
        vehicle.mode = "LOITER"
        vehicle.armed = False
        vehicle.roll = 0.0
        vehicle.pitch = 0.0
        if not vehicle.done:
            vehicle.done = True
            vehicle.done_reason = "stopped"
    else:
        raise ValueError("未知指令")
    vehicle.disposition_route = None
    vehicle.disposition_incident = None
    vehicle.dynamic_guard = None


def _mark(vehicle, kind, detail):
    if kind in vehicle.flags:
        return
    vehicle.flags.add(kind)
    vehicle.events.append({"kind": kind, "detail": detail, "position": list(vehicle.position), "second": round(vehicle.seconds, 1)})


def _inside(pos):
    return (FENCE["x0"] <= pos[0] <= FENCE["x1"]
            and FENCE["y0"] <= pos[1] <= FENCE["y1"]
            and FENCE["z0"] <= pos[2] <= FENCE["z1"])


def _attitude(prev, pos, prev_yaw):
    dx = pos[0] - prev[0]
    dy = pos[1] - prev[1]
    dz = pos[2] - prev[2]
    horiz = math.hypot(dx, dy)
    if horiz < 1e-4 and abs(dz) < 1e-4:
        return 0.0, 0.0, prev_yaw
    if horiz < 1e-4:
        return 0.0, 90.0 if dz > 0 else -90.0, prev_yaw
    yaw = math.degrees(math.atan2(dx, dy))
    if yaw < 0:
        yaw += 360.0
    pitch = math.degrees(math.atan2(dz, horiz))
    turn = (yaw - prev_yaw + 180.0) % 360.0 - 180.0
    roll = max(-30.0, min(30.0, -turn * 1.2))
    return roll, pitch, yaw


def _record(vehicle):
    vehicle.track.append({
        "t": round(vehicle.seconds, 1),
        "x": round(vehicle.position[0], 2),
        "y": round(vehicle.position[1], 2),
        "z": round(vehicle.position[2], 2),
        "roll": round(vehicle.roll, 1),
        "pitch": round(vehicle.pitch, 1),
        "yaw": round(vehicle.yaw, 1),
    })
    if len(vehicle.track) > 4000:
        del vehicle.track[: len(vehicle.track) - 4000]


def _ray_ground(pos, yaw_deg, pitch_deg, max_range=1200.0):
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    dx = math.sin(yaw) * math.cos(pitch)
    dy = math.cos(yaw) * math.cos(pitch)
    dz = math.sin(pitch)
    horiz = math.hypot(dx, dy)
    if dz >= -0.02 or pos[2] < 1.0:
        if horiz < 1e-6:
            return [pos[0], pos[1]]
        scale = max_range / horiz
        return [pos[0] + dx * scale, pos[1] + dy * scale]
    t = min(-pos[2] / dz, max_range / max(horiz, 1e-6))
    return [pos[0] + dx * t, pos[1] + dy * t]


@synchronized
def set_gimbal(vehicle, pitch, yaw):
    if vehicle.payload != "光电吊舱":
        raise ValueError("该载荷没有光学吊舱")
    if pitch < -90 or pitch > 20 or yaw < -180 or yaw > 180:
        raise ValueError("吊舱俯仰 -90–20，偏航 -180–180")
    vehicle.gimbal_pitch = float(pitch)
    vehicle.gimbal_yaw = float(yaw)


def view_of(vehicle):
    optical = vehicle.payload == "光电吊舱"
    view = {
        "kind": "optical" if optical else "none",
        "gimbal_pitch": round(vehicle.gimbal_pitch, 1) if optical else None,
        "gimbal_yaw": round(vehicle.gimbal_yaw, 1) if optical else None,
        "fov_h": 40.0 if optical else None,
        "fov_v": 26.0 if optical else None,
        "look_yaw": None,
        "look_pitch": None,
        "footprint": [],
        "note": "按姿态和吊舱指向推算的地面视野，不是机载视频，不含识别结果。",
    }
    if not optical:
        view["note"] = "报文终端不形成光学画面。地面站仍显示位置、姿态和轨迹。"
        return view
    if vehicle.position[2] < 2:
        view["note"] = "高度过低，暂无地面视野。这不是机载视频，也不含识别结果。"
        return view
    look_yaw = (vehicle.yaw + vehicle.gimbal_yaw) % 360.0
    look_pitch = vehicle.gimbal_pitch
    view["look_yaw"] = round(look_yaw, 1)
    view["look_pitch"] = round(look_pitch, 1)
    corners = []
    for pitch_off, yaw_off in ((13, -20), (13, 20), (-13, 20), (-13, -20)):
        hit = _ray_ground(vehicle.position, look_yaw + yaw_off, look_pitch + pitch_off)
        corners.append([round(hit[0], 1), round(hit[1], 1)])
    view["footprint"] = corners
    return view


def tick(vehicle, dt=1.0):
    if vehicle.mode == 'LOITER' and getattr(vehicle,'disposition_route',None) and getattr(vehicle,'disposition_incident',None):
        vehicle.seconds += dt
        vehicle.battery = max(0.0, vehicle.battery - .15*dt)
        _record(vehicle)
        return
    if vehicle.mode not in {"AUTO", "RTL", "LAND"}:
        return
    if vehicle.mode == "AUTO":
        target = vehicle.mission[vehicle.wp]
    elif getattr(vehicle, "disposition_route", None):
        target = vehicle.disposition_route[vehicle.disposition_wp]
    elif vehicle.mode == "LAND":
        target = [vehicle.position[0], vehicle.position[1], 0.0]
    else:
        target = list(vehicle.home)
    previous = list(vehicle.position)
    vehicle.position, arrived = _move(vehicle.position, target, vehicle.speed, dt)
    vehicle.roll, vehicle.pitch, vehicle.yaw = _attitude(previous, vehicle.position, vehicle.yaw)
    vehicle.seconds += dt
    vehicle.battery = max(0.0, vehicle.battery - 0.15 * dt)
    _record(vehicle)
    if vehicle.mode == "AUTO" and vehicle.battery <= 20:
        _mark(vehicle, "low_battery", "电量低于 20%，转入返航")
        vehicle.mode = "RTL"
        return
    if vehicle.mode == "AUTO" and not _inside(vehicle.position):
        _mark(vehicle, "geofence", "超出围栏，转入返航")
        vehicle.mode = "RTL"
        return
    if not arrived:
        return
    if vehicle.mode == "AUTO":
        vehicle.wp += 1
        if vehicle.wp >= len(vehicle.mission):
            vehicle.wp = len(vehicle.mission) - 1
            _park(vehicle, "mission")
        return
    if getattr(vehicle, "disposition_route", None):
        vehicle.disposition_wp += 1
        if vehicle.disposition_wp < len(vehicle.disposition_route):
            return
        reason = vehicle.disposition_reason
        vehicle.disposition_route = None
        _park(vehicle, reason)
        return
    _park(vehicle, "rtl" if vehicle.mode == "RTL" else "land")


def _park(vehicle, reason):
    if getattr(vehicle,'dynamic_guard',None):
        vehicle.dynamic_guard['state']='completed'
    vehicle.mode = "LOITER"
    vehicle.armed = False
    vehicle.done = True
    vehicle.done_reason = reason
    vehicle.roll = 0.0
    vehicle.pitch = 0.0


def moving():
    return any(v.mode in {"AUTO", "RTL", "LAND"} or (getattr(v,"disposition_route",None) and v.armed and not v.done) for v in VEHICLES.values())


@synchronized
def tick_steps(steps, vehicle_id=None):
    chosen = [VEHICLES[vehicle_id]] if vehicle_id else list(VEHICLES.values())
    from . import follow, dynamic
    for _ in range(steps):
        dynamic.prepare(chosen)
        for vehicle in chosen:
            tick(vehicle)
        follow.advance(VEHICLES["UAV-01"])
        from . import patrol
        patrol.capture(chosen)


@synchronized
def snapshot(vehicle):
    return {
        "disposition_incident": getattr(vehicle,"disposition_incident",None),
        "dynamic_guard": copy.deepcopy(getattr(vehicle,"dynamic_guard",None)),
        "disposition_wp": getattr(vehicle, "disposition_wp", 0),
        "disposition_route": [list(p) for p in getattr(vehicle, "disposition_route", []) or []],
        "business_task_id": getattr(vehicle, "business_task_id", None),
        "business_validation_id": getattr(vehicle, "business_validation_id", None),
        "id": vehicle.id,
        "name": vehicle.name,
        "pilot_id": vehicle.pilot_id,
        "payload": vehicle.payload,
        "home": list(vehicle.home),
        "position": [round(v, 2) for v in vehicle.position],
        "speed": vehicle.speed,
        "mode": vehicle.mode,
        "armed": vehicle.armed,
        "autopilot": "ArduCopter",
        "battery": round(vehicle.battery, 2),
        "wp": vehicle.wp,
        "mission": [list(p) for p in vehicle.mission],
        "done": vehicle.done,
        "done_reason": vehicle.done_reason,
        "seconds": round(vehicle.seconds, 1),
        "attitude": {
            "roll": round(vehicle.roll, 1),
            "pitch": round(vehicle.pitch, 1),
            "yaw": round(vehicle.yaw, 1),
        },
        "view": view_of(vehicle),
        "track_count": len(vehicle.track),
        "events": [dict(e,position=list(e["position"])) for e in vehicle.events],
        "fence": FENCE,
    }


@synchronized
def track_of(vehicle):
    return {"id": vehicle.id, "count": len(vehicle.track), "points": [dict(p) for p in vehicle.track]}
