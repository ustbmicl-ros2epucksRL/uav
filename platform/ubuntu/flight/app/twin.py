"""Read-only projection of the external MAVLink listener, never simulator fallback."""
import math
import os
import threading
import time
from urllib.parse import urlparse
from fastapi import APIRouter
from . import mavlink_link

router=APIRouter(prefix='/api/twin')
LOCK=threading.Lock()
TRACK=[]
IDENTITY=None
LAST_SAMPLE=None


def fresh(s,key):
    value=s.get(key)
    return s['connected'] and isinstance(value,(int,float)) and math.isfinite(value) and s.get('field_age_s',{}).get(key,999)<3


@router.get('')
def snapshot():
    global IDENTITY,LAST_SAMPLE
    s=mavlink_link.status()
    ages=s.get('field_age_s',{})
    position_valid=all(fresh(s,k) for k in ['latitude','longitude','relative_altitude_m']) and -90<=s['latitude']<=90 and -180<=s['longitude']<=180 and fresh(s,'gps_fix') and s['gps_fix']>=3
    identity=(s['url'],s['system_id'],s.get('session_id',0))
    with LOCK:
        if identity!=IDENTITY or not s['listening']:
            TRACK.clear();LAST_SAMPLE=None;IDENTITY=identity
        sample=(s['latitude'],s['longitude'],s['relative_altitude_m'])
        if position_valid and sample!=LAST_SAMPLE:
            TRACK.append(dict(latitude=sample[0],longitude=sample[1],altitude=sample[2],at=time.time()))
            del TRACK[:-2000];LAST_SAMPLE=sample
        track=list(TRACK)
    url=os.environ.get('TWIN_VIDEO_URL','')
    parsed=urlparse(url)
    bound=os.environ.get('TWIN_VIDEO_SYSTEM_ID','')==str(s['system_id'])
    video_ready=bool(s['system_id'] is not None and bound and parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password)
    fields={key:(s.get(key) if fresh(s,key) else None) for key in ['roll_deg','pitch_deg','yaw_deg','groundspeed_mps','climb_mps','battery_percent','gps_fix','satellites','relative_altitude_m']}
    return dict(source='external-mavlink',vehicle_id='MAV-'+str(s['system_id']) if s['system_id'] is not None else None,
        system_id=s['system_id'],listening=s['listening'],connected=s['connected'],heartbeat_age_s=s['age_s'],commands=False,
        position_valid=position_valid,latitude=s['latitude'] if position_valid else None,longitude=s['longitude'] if position_valid else None,
        telemetry=fields,armed=s['armed'] if s['connected'] else None,track=track,
        video=dict(configured=video_ready,url=url if video_ready and s['connected'] else None,source='external-camera',note='机载视频需单独配置；不使用仿真画面替代'),
        note='外部 MAVLink 数据；需由操作者确认来源是真实飞控还是外部仿真。只读监视，不具备控制权。')
