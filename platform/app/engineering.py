"""Application-aligned numerical baseline. Local metres; parameters require calibration."""
import copy
import itertools
import uuid
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import store


from .coverage_core import Volume, grid, visible, fields, summarize, metadata, MODEL_VERSION


def volume(scene,request,calibration=None):
    p=grid(request);f=fields(scene,p,request,calibration);indices=np.flatnonzero(f['valid'])[::max(1,len(p)//8000)]
    cloud=[]
    for i in indices:
        cloud.append([*p[i].tolist(),round(float(f['pd'][i]),4),round(float(f['sinr_db'][i]),2),
                      round(float(f['precision_m'][i]),2) if f['observable'][i] else None])
    return {**metadata(request),'scene_revision':scene['revision'],'calibration_id':(calibration or {}).get('id'),'settings':request.model_dump(),'evaluated_cells':len(p),
            'stats':summarize(f,request,p,scene['zone']),'cloud':cloud,'columns':['x','y','z','pd','sinr_db','crlb_position_rmse_m'],
            'created_at':store.now(),'data_mode':'synthetic_model','scope':'仅所选区域，不能代表全域验收覆盖率',
            'assumptions':['轴对齐建筑射线遮挡；无绕射和多径','示例链路预算及独立观测信息；CRLB 是理论界，非实测定位误差','RF/光电仅方向信息，雷达含距离；报文不计入非合作覆盖','图形为抽样点云，统计来自全部计算网格']}


class ConstrainedPlan(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    volume:Volume=Field(default_factory=Volume)
    budget:float=Field(default=50,ge=0,le=1000)
    coverage_target:float=Field(default=.9,gt=0,le=1)
    focus_target:float=Field(default=.95,gt=0,le=1)
    precision_rate_target:float=Field(default=.8,ge=0,le=1)


def constrained_plan(scene,request,calibration=None, allowed=None, cost_of=None):
    candidates=[d for d in scene['candidates'] if d.get('status')=='online' and d['id'] not in {x['id'] for x in scene['devices']}]
    if len(candidates)>10:raise ValueError('精确子集比较最多 10 个候选，需先预选')
    p=grid(request.volume);alternatives=[]
    for bits in itertools.product((0,1),repeat=len(candidates)):
        selected=[d for d,b in zip(candidates,bits) if b]
        if allowed and not allowed(selected):continue
        cost=cost_of(selected) if cost_of else sum(d['cost'] for d in selected)
        if cost>request.budget:continue
        s=copy.deepcopy(scene);s['devices']+=selected
        stats=summarize(fields(s,p,request.volume,calibration),request.volume,p,s['zone'])
        deficits=[max(0,request.coverage_target-(stats['coverage'] or 0)),
                  max(0,request.focus_target-(stats['focus_coverage'] or 0)),
                  max(0,request.precision_rate_target-(stats['precision_pass_rate'] or 0))]
        feasible=stats['focus_coverage'] is not None and max(deficits)<1e-9
        alternatives.append({'selected':selected,'added_cost':cost,'stats':stats,'feasible':feasible,'constraint_deficit':sum(deficits)})
    if not alternatives:raise ValueError('工程容量或预算约束下没有可用方案，请检查已有部署')
    best=min(alternatives,key=lambda x:(not x['feasible'],0 if x['feasible'] else x['constraint_deficit'],x['added_cost']))
    return {**best,**metadata(request.volume),'id':uuid.uuid4().hex[:12],'scene':scene['id'],'scene_revision':scene['revision'],
            'request':request.model_dump(),'evaluated_subsets':len(alternatives),'created_at':store.now(),
            'method':'预算内枚举候选设备子集，逐方案重新计算 Pd 与 FIM；仅此有限候选空间最优',
            'status':'draft','scope':'区域示例规划；不代表 MILP、大规模全域求解或验收达标'}


class Calibration(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    sensor_id:str=Field(min_length=1,max_length=80)
    residuals:list[list[float]]=Field(min_length=20,max_length=10000)
    source:str=Field(min_length=1,max_length=300)
    @model_validator(mode='after')
    def shape(self):
        if any(len(v)!=3 or not np.isfinite(v).all() for v in self.residuals):raise ValueError('每条残差必须为有限 XYZ 三维向量')
        return self


def calibrate(data):
    a=np.array(data.residuals);split=int(len(a)*.75);train=a[:split];test=a[split:]
    center=np.median(train,axis=0);r=np.linalg.norm(train-center,axis=1);mad=np.median(abs(r-np.median(r)))
    keep=r<=np.median(r)+4*max(mad,1e-3);clean=train[keep];bias=clean.mean(axis=0)
    sigma=max(.1,float(np.sqrt(np.mean((clean-bias)**2))))
    return {'id':uuid.uuid4().hex[:12],'sensor_id':data.sensor_id,'source':data.source,'bias_xyz_m':bias.tolist(),'sigma_m':sigma,
            'training_samples':len(train),'accepted_samples':len(clean),'holdout_samples':len(test),
            'holdout_before_rmse_m':float(np.sqrt(np.mean(np.sum(test**2,axis=1)))),
            'holdout_after_rmse_m':float(np.sqrt(np.mean(np.sum((test-bias)**2,axis=1)))),
            'created_at':store.now(),'note':'时间顺序留后 25% 验证；XYZ 残差噪声尺度按示例相对比例进入精度场，非原生距离/测角标定；未自动修改量测偏差。'}


class Task(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id:str=Field(min_length=1,max_length=80)
    kind:str=Field(pattern='^(discovery|localization|tracking|handover|warning)$')
    priority:int=Field(default=1,ge=1,le=10)
    bandwidth:float=Field(default=1,gt=0,le=10000)
    compute:float=Field(default=1,gt=0,le=10000)


class Schedule(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    tasks:list[Task]=Field(min_length=1,max_length=100)
    bandwidth:float=Field(default=20,ge=0,le=10000)
    compute:float=Field(default=20,ge=0,le=10000)
    @model_validator(mode='after')
    def unique(self):
        if len({t.id for t in self.tasks})!=len(self.tasks):raise ValueError('任务 ID 不可重复')
        return self


def schedule(scene,request):
    online=[d for d in scene['devices'] if d['status']=='online'];assignments=[];pending=[];bw=cpu=0
    for t in sorted(request.tasks,key=lambda t:-t.priority):
        if online and bw+t.bandwidth<=request.bandwidth and cpu+t.compute<=request.compute:
            d=online[len(assignments)%len(online)];assignments.append({**t.model_dump(),'sensor_id':d['id'],'execution':'simulated_ack'})
            bw+=t.bandwidth;cpu+=t.compute
        else:pending.append({**t.model_dump(),'reason':'设备离线或资源不足'})
    return {'id':uuid.uuid4().hex[:12],'assignments':assignments,'pending':pending,'used_bandwidth':bw,'used_compute':cpu,
            'scene_revision':scene['revision'],'created_at':store.now(),'note':'优先级贪心与设备轮转的调度基线；单位由输入声明，未发送真实设备控制指令。'}
