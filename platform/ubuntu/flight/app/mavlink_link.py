"""Listen-only MAVLink2 telemetry.

The web station still flies the simulator. This module only watches a heartbeat
and position from ArduCopter. It does not arm, change mode, or upload a mission.
"""
import math
import re
import threading
import time

_URL = re.compile(r'^(udpin|udpout|tcp|tcpin|serial):[A-Za-z0-9_./:@-]{3,80}$')
_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD = None
_STATE = {
    'url': '',
    'session_id': 0,
    'listening': False,
    'installed': False,
    'heartbeat_at': None,
    'system_id': None,
    'armed': None,
    'relative_altitude_m': None,
    'latitude': None,
    'longitude': None,
    'roll_deg': None, 'pitch_deg': None, 'yaw_deg': None,
    'groundspeed_mps': None, 'climb_mps': None, 'battery_percent': None,
    'voltage_v': None, 'gps_fix': None, 'satellites': None, 'sample_times': {},
    'error': '',
}


def installed():
    try:
        import pymavlink  # noqa: F401
        return True
    except ImportError:
        return False


def status():
    with _LOCK:
        state = dict(_STATE)
    age = None if state['heartbeat_at'] is None else round(time.monotonic() - state['heartbeat_at'], 1)
    connected = bool(state['listening'] and age is not None and age < 3)
    return {
        **{key: state[key] for key in ('roll_deg','pitch_deg','yaw_deg','groundspeed_mps','climb_mps','battery_percent','voltage_v','gps_fix','satellites')},
        'field_age_s': {key: round(time.monotonic()-stamp, 2) for key,stamp in state['sample_times'].items()},
        'url': state['url'],
        'session_id': state['session_id'],
        'listening': state['listening'],
        'connected': connected,
        'installed': installed(),
        'commands': False,
        'age_s': age,
        'system_id': state['system_id'],
        'armed': state['armed'],
        'relative_altitude_m': state['relative_altitude_m'],
        'latitude': state['latitude'],
        'longitude': state['longitude'],
        'error': state['error'],
        'note': '只读遥测：心跳、位置、姿态、速度、电池和 GPS。解锁、模式和航线上传仍只作用于仿真，不发给飞机。',
    }


def _remember(**fields):
    with _LOCK:
        _STATE.update(fields)
        _STATE['sample_times'] = {**_STATE['sample_times'], **{k: time.monotonic() for k in fields if k not in ('sample_times','error','listening','url')}}


def _apply(message):
    kind = message.get_type()
    if kind == 'HEARTBEAT':
        if _STATE['system_id'] is not None and message.get_srcSystem() != _STATE['system_id']:
            return
        if int(getattr(message, 'type', 0)) == 6:
            return
        _remember(heartbeat_at=time.monotonic(), system_id=int(message.get_srcSystem()),
                  armed=bool(int(message.base_mode) & 128), error='')
    elif _STATE['system_id'] is None or message.get_srcSystem() != _STATE['system_id']:
        return
    elif kind == 'ATTITUDE':
        _remember(roll_deg=math.degrees(message.roll), pitch_deg=math.degrees(message.pitch), yaw_deg=math.degrees(message.yaw)%360)
    elif kind == 'VFR_HUD':
        _remember(groundspeed_mps=message.groundspeed, climb_mps=message.climb)
    elif kind == 'SYS_STATUS':
        _remember(battery_percent=None if message.battery_remaining < 0 else message.battery_remaining, voltage_v=None if message.voltage_battery == 65535 else message.voltage_battery/1000)
    elif kind == 'GPS_RAW_INT':
        _remember(gps_fix=message.fix_type, satellites=None if message.satellites_visible == 255 else message.satellites_visible)
    elif kind == 'GLOBAL_POSITION_INT':
        _remember(latitude=message.lat / 1e7, longitude=message.lon / 1e7,
                  relative_altitude_m=round(message.relative_alt / 1000, 1))


def _loop(url):
    from pymavlink import mavutil
    try:
        link = mavutil.mavlink_connection(url)
    except Exception as exc:
        _remember(listening=False, error=str(exc))
        return
    _remember(listening=True, error='')
    while not _STOP.is_set():
        try:
            message = link.recv_match(blocking=True, timeout=1)
        except Exception as exc:
            _remember(error=str(exc))
            break
        if message is not None:
            _apply(message)
    try:
        link.close()
    except Exception:
        pass
    _remember(listening=False)


def stop():
    global _THREAD
    _STOP.set()
    thread = _THREAD
    if thread and thread.is_alive():
        thread.join(timeout=2)
    _THREAD = None
    _remember(listening=False)


def configure(url, listen):
    global _THREAD
    target = url.strip()
    if target and (not _URL.match(target) or '..' in target):
        raise ValueError('连接地址只接受 udpin、udpout、tcp 或 serial，例如 udpin:0.0.0.0:14550')
    stop()
    _STOP.clear()
    with _LOCK:
        _STATE['session_id'] += 1
        for field in ('roll_deg','pitch_deg','yaw_deg','groundspeed_mps','climb_mps','battery_percent','voltage_v','gps_fix','satellites'):
            _STATE[field] = None
        _STATE['sample_times'] = {}
    _remember(url=target, listening=False, heartbeat_at=None, system_id=None, armed=None,
              relative_altitude_m=None, latitude=None, longitude=None, error='')
    if not listen:
        return status()
    if not target:
        raise ValueError('开始监听前要填写连接地址')
    if not installed():
        raise RuntimeError('服务器还没有 pymavlink，不能打开数传')
    _remember(listening=True)
    _THREAD = threading.Thread(target=_loop, args=(target,), name='mavlink-listen', daemon=True)
    _THREAD.start()
    return status()
