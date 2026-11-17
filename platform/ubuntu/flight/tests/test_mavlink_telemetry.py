from types import SimpleNamespace
import math
from app import mavlink_link as link

def message(kind, system=1, **fields):
    return SimpleNamespace(get_type=lambda:kind,get_srcSystem=lambda:system,**fields)

def test_telemetry_units_target_and_reset():
    link.configure('',False)
    link._apply(message('HEARTBEAT',type=2,base_mode=0))
    link._apply(message('ATTITUDE',roll=math.pi/6,pitch=-math.pi/12,yaw=-math.pi/2))
    link._apply(message('SYS_STATUS',battery_remaining=-1,voltage_battery=65535))
    link._apply(message('VFR_HUD',groundspeed=12,climb=2))
    link._apply(message('GPS_RAW_INT',fix_type=3,satellites_visible=14))
    s=link.status()
    assert round(s['roll_deg'])==30 and round(s['pitch_deg'])==-15 and s['yaw_deg']==270
    assert s['battery_percent'] is None and s['voltage_v'] is None
    assert s['groundspeed_mps']==12 and s['satellites']==14
    link._apply(message('ATTITUDE',system=2,roll=0,pitch=0,yaw=0))
    assert link.status()['yaw_deg']==270
    link.configure('',False)
    assert link.status()['yaw_deg'] is None
