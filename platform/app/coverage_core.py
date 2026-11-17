"""Shared deterministic 3D coverage model. Synthetic baseline, not field calibration."""
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Volume(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    bounds:list[float]=Field(default=[700,900,700,900,120,160],min_length=6,max_length=6)
    cell:float=Field(default=10,ge=2,le=100)
    cochannel_db:float=Field(default=3,ge=0,le=40)
    communication_db:float=Field(default=2,ge=0,le=40)
    weather_db:float=Field(default=1,ge=0,le=40)
    pd_threshold:float=Field(default=.5,gt=0,le=1)
    target_emitting:bool=True
    precision_m:float=Field(default=30,gt=0,le=1000)
    @model_validator(mode='after')
    def valid(self):
        b=self.bounds
        if any(b[i]>=b[i+1] for i in (0,2,4)) or min(b)<0 or max(b[:4])>2000 or b[5]>300:
            raise ValueError('边界顺序为 x0,x1,y0,y1,z0,z1，须在场景内')
        if any(not np.isclose((b[i+1]-b[i])/self.cell, round((b[i+1]-b[i])/self.cell)) for i in (0,2,4)):
            raise ValueError('各方向长度须为网格边长的整数倍，避免边界体素统计歧义')
        if np.prod([np.ceil((b[i+1]-b[i])/self.cell) for i in (0,2,4)])>150000:
            raise ValueError('单次区域最多 150000 网格；请分区计算')
        return self


def grid(request):
    b=request.bounds
    axes=[np.arange(b[i]+request.cell/2,b[i+1],request.cell) for i in (0,2,4)]
    if any(len(a)==0 for a in axes):raise ValueError('区域每个方向至少一个完整网格')
    return np.stack(np.meshgrid(*axes,indexing='ij'),-1).reshape(-1,3)


def visible(points,device,buildings):
    origin=np.array([device[k] for k in ('x','y','z')],float)
    delta=points-origin;distance=np.linalg.norm(delta,axis=1)
    angle=np.degrees(np.arctan2(delta[:,0],delta[:,1]))
    az=(angle-device['azimuth']+180)%360-180
    elevation=np.degrees(np.arctan2(delta[:,2],np.maximum(np.linalg.norm(delta[:,:2],axis=1),1e-9)))
    good=(distance<=device['range'])&(abs(az)<=device['fov']/2)&(abs(elevation-device.get('pitch',10))<=device.get('vertical_fov',110)/2)
    for b in buildings:
        lo=np.array([b['x'],b['y'],0]);hi=lo+np.array([b['w'],b['d'],b['h']])
        parallel=abs(delta)<1e-12
        outside=parallel&((origin<lo)|(origin>hi))
        safe=np.where(parallel,1,delta)
        t1=(lo-origin)/safe;t2=(hi-origin)/safe
        entry=np.max(np.where(parallel,-np.inf,np.minimum(t1,t2)),axis=1)
        leave=np.min(np.where(parallel,np.inf,np.maximum(t1,t2)),axis=1)
        blocked=(~outside.any(axis=1))&(leave>=np.maximum(entry,0))&(entry<1)&(leave>1e-6)
        good&=~blocked
    return good,distance,delta


def fields(scene,points,request,calibration=None,initial=None):
    points=np.asarray(points,dtype=float)
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all():
        raise ValueError('采样点须为有限 XYZ 数组')
    n=len(points);fim=np.zeros((n,3,3));miss=np.ones(n);sinr=np.full(n,-80.);count=np.zeros(n,int)
    valid=valid_points(scene,points)
    per_device={}
    if initial is not None:
        if not np.array_equal(valid, initial['valid']):
            raise ValueError('增量场必须使用相同有效空域')
        fim=initial['fim'].copy();miss=1-initial['pd']
        sinr=initial['sinr_db'].copy();count=initial['count'].copy()
        per_device=initial['per_device'].copy()
    for d in scene['devices']:
        pd,db,r,delta,mask=device_field(scene,points,d,request,valid)
        per_device[d['id']]=pd
        miss*=1-pd;sinr=np.maximum(sinr,np.where(mask,db,-80));count+=pd>=request.pd_threshold
        u=delta/np.maximum(r[:,None],1)
        sigma={'RADAR':24,'RF':48,'EO':12}.get(d['type'],18)
        if calibration and calibration.get('sensor_id')==d['id']:sigma=calibration['sigma_m']
        # Local tangent form of range + two angular measurement Jacobians.
        # Optical/RF channels have no standalone range; only triangulation resolves depth.
        outer=u[:,:,None]*u[:,None,:]
        angular_sigma={'RADAR':.02,'RF':.045,'EO':.008,'IR':.012}.get(d['type'],.02)
        if calibration and calibration.get('sensor_id')==d['id']:
            angular_sigma*=sigma/{'RADAR':24,'RF':48,'EO':12,'IR':18}.get(d['type'],18)
        tangent=(np.eye(3)[None]-outer)/np.maximum(r*angular_sigma,1)[:,None,None]**2
        information=tangent+(outer/(sigma*sigma) if d['type']=='RADAR' else 0)
        fim+=information*pd[:,None,None]
    eig=np.linalg.eigvalsh(fim);observable=(eig[:,0]>1e-8)&valid
    precision=np.full(n,np.inf)
    precision[observable]=np.sqrt((1/eig[observable]).sum(axis=1))
    pd=1-miss
    return {'pd':pd,'sinr_db':sinr,'precision_m':precision,'observable':observable,'count':count,'valid':valid,'per_device':per_device,'fim':fim}


def summarize(f,request,points,zone):
    valid=f['valid'];focus=valid&(points[:,0]>=zone['x0'])&(points[:,0]<=zone['x1'])&(points[:,1]>=zone['y0'])&(points[:,1]<=zone['y1'])&(points[:,2]>=zone.get('z0',0))&(points[:,2]<=zone.get('z1',300))
    covered=(f['pd']>=request.pd_threshold)&valid
    adequate=covered&f['observable']&(f['precision_m']<=request.precision_m)
    def rate(x,region):return float(x[region].mean()) if region.any() else None
    return {'valid_cells':int(valid.sum()),'coverage':rate(covered,valid),'focus_coverage':rate(covered,focus),
            'precision_pass_rate':rate(adequate,valid),'unobservable_cells':int((valid&~f['observable']).sum()),
            'focus_valid_cells':int(focus.sum()),'covered_cells':int(covered.sum()),'mean_pd':rate(f['pd'],valid),'precision_threshold_m':request.precision_m}


MODEL_VERSION = 'unified-coverage-v1'

def default_volume():
    # Existing whole-scene overview remains coarse; regional requests support 10m/2m.
    return Volume(bounds=[0,2000,0,2000,0,300],cell=50)

def metadata(request):
    return {'model_version':MODEL_VERSION,'coordinate_frame':'local_ENU_m',
            'azimuth_convention':'0=north,+90=east','settings':request.model_dump(),
            'coverage_definition':'有效空域采样点中，融合 Pd >= pd_threshold 的比例',
            'fusion':'1-product(1-Pd)，独立探测事件基线',
            'target_condition':'RF 仅对发射目标有效；ADSB 不计入非合作覆盖',
            'airspace':'0<=x,y<=2000，30<=z<=300，排除建筑内部及表面',
            'data_mode':'synthetic_model'}

def valid_points(scene,points):
    p=np.asarray(points)
    valid=np.isfinite(p).all(axis=1)&(p[:,0]>=0)&(p[:,0]<=2000)&(p[:,1]>=0)&(p[:,1]<=2000)&(p[:,2]>=30)&(p[:,2]<=300)
    for b in scene['buildings']:
        valid&=~((p[:,0]>=b['x'])&(p[:,0]<=b['x']+b['w'])&(p[:,1]>=b['y'])&(p[:,1]<=b['y']+b['d'])&(p[:,2]>=0)&(p[:,2]<=b['h']))
    return valid

def device_field(scene,points,device,request,valid=None):
    mask,r,delta=visible(points,device,scene['buildings'])
    mask &= valid_points(scene,points) if valid is None else valid
    if device['status']!='online' or device['type']=='ADSB' or (device['type']=='RF' and not request.target_emitting):
        mask[:]=False
    loss=request.weather_db
    if device['type'] in ('RADAR','RF'):loss+=request.cochannel_db+request.communication_db
    db=18-20*np.log10(np.maximum(r,20)/(device['range']*.4))-loss
    pd=np.where(mask,1/(1+np.exp(-np.clip((db-5)/3,-40,40))),0)
    return pd,db,r,delta,mask

