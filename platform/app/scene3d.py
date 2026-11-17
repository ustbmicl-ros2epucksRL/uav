"""Ray inspection using the same axis-aligned building geometry as engineering fields."""
import math
import numpy as np
from pydantic import BaseModel,ConfigDict,Field
from .engineering import visible

class RayRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    sensor_id:str=Field(min_length=1,max_length=80)
    target: list[float]=Field(min_length=3,max_length=3)
    surface_only:bool=False

    def valid_target(self):
        return all(math.isfinite(x) for x in self.target) and 0<=self.target[0]<=2000 and 0<=self.target[1]<=2000 and 0<=self.target[2]<=300


def first_hit(origin,target,buildings,surface_only=False):
    o=np.asarray(origin,dtype=float);v=np.asarray(target,dtype=float)-o;best=None
    for i,b in enumerate(buildings):
        if surface_only:
            if abs(v[2])<1e-10:continue
            t=(b['h']-o[2])/v[2];p=o+t*v
            if not (1e-7<t<1-1e-7 and b['x']<=p[0]<=b['x']+b['w'] and b['y']<=p[1]<=b['y']+b['d']):continue
        else:
            lo=[b['x'],b['y'],0];hi=[b['x']+b['w'],b['y']+b['d'],b['h']];entry=0.;leave=1.
            for k in range(3):
                if abs(v[k])<1e-10:
                    if not lo[k]<=o[k]<=hi[k]:entry=2;break
                else:
                    a,c=sorted(((lo[k]-o[k])/v[k],(hi[k]-o[k])/v[k]));entry=max(entry,a);leave=min(leave,c)
            if entry>leave or leave<=1e-7 or entry>=1-1e-7:continue
            t=max(entry,0)
        if best is None or t<best['fraction']:
            best={'building_index':i,'building_id':f'L-{i+1:03d}','fraction':float(t),'position':(o+t*v).tolist()}
    return best


def inspect(scene,request):
    d=next((d for d in scene['devices'] if d['id']==request.sensor_id),None)
    if not d:raise KeyError('设备不存在')
    if not request.valid_target():raise ValueError('目标坐标须在 0–2000 m / 0–300 m 内')
    origin=[d[k] for k in ('x','y','z')]
    hit=first_hit(origin,request.target,scene['buildings'],request.surface_only)
    opportunity=bool(visible(np.array([request.target]),d,[])[0][0])
    return {'scene_revision':scene['revision'],'sensor_id':d['id'],'origin':origin,'target':request.target,
            'surface_only':request.surface_only,'first_hit':hit,'blocked':hit is not None,
            'distance_m':round(math.dist(origin,request.target),2),'within_range_and_fov':opportunity,
            'sensor_online':d['status']=='online','noncooperative_coverage_eligible':d['type']!='ADSB',
            'visible':hit is None and opportunity and d['status']=='online' and d['type']!='ADSB',
            'note':'屋顶模式仅用于几何对照；实体模式使用规划场同一楼体。未实现深度/标签融合或真实建筑重建。'}
