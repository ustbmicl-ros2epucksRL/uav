"""Ground station metadata, ENU map anchor, route diagnostics and command acknowledgements."""
import json,math,os,uuid
from pathlib import Path
from datetime import datetime,timezone
from collections import deque
from pydantic import BaseModel,ConfigDict,Field

EARTH_RADIUS=6378137.0
EVENTS=deque(maxlen=300)
CLOCK={'running':False,'speed':1}

class MapConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    latitude:float=Field(default=39.05,ge=-80,le=80)
    longitude:float=Field(default=115.98,ge=-180,le=180)
    label:str=Field(default='雄安容城晾马台一带（仿真原点）',min_length=1,max_length=100)

def config_path():
    return Path(os.environ.get('FLIGHT_DATA_DIR',str(Path(__file__).resolve().parent.parent/'data')))/'map.json'

def config():
    path=config_path()
    if path.exists():return MapConfig.model_validate_json(path.read_text()).model_dump()
    return MapConfig().model_dump()

def save_config(data):
    path=config_path();path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(data.model_dump_json());tmp.replace(path)

def geo(point,anchor):
    lat=anchor['latitude']+math.degrees(point[1]/EARTH_RADIUS)
    lon=anchor['longitude']+math.degrees(point[0]/(EARTH_RADIUS*math.cos(math.radians(anchor['latitude']))))
    return [lat,lon,point[2] if len(point)>2 else 0]

def enu(point,anchor):
    return [math.radians(point[1]-anchor['longitude'])*EARTH_RADIUS*math.cos(math.radians(anchor['latitude'])),
            math.radians(point[0]-anchor['latitude'])*EARTH_RADIUS,point[2] if len(point)>2 else 0]

def ack(vehicle,kind,detail):
    item={'id':uuid.uuid4().hex[:12],'time':datetime.now(timezone.utc).isoformat(),'vehicle_id':vehicle,'kind':kind,'detail':detail,'transport':'simulator'}
    EVENTS.appendleft(item);return item

def preview(points,home,speed,fence):
    distance=sum(math.dist(a,b) for a,b in zip([home]+points,points))
    violations=[]
    for i,p in enumerate(points):
        if not (fence['x0']<=p[0]<=fence['x1'] and fence['y0']<=p[1]<=fence['y1'] and fence['z0']<=p[2]<=fence['z1']):
            violations.append({'waypoint':i+1,'reason':'超出当前仿真围栏'})
    return {'distance_m':round(distance,1),'estimated_seconds':round(distance/speed,1),
            'max_altitude_m':max(p[2] for p in points),'violations':violations,'inside_fence':not violations,
            'note':'直线航段及恒速估算，未检查地形、障碍物、空域许可或动力学可飞性。'}
