import copy
import hashlib
import json
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse, FileResponse
from pydantic import BaseModel, Field, ConfigDict, model_validator
from . import store
from .scenes import SCENES, seed, CATALOG
from .schemas import Device, PlanRequest
from .analysis import compute, optimize
from .coverage_core import MODEL_VERSION
from .sensing import execute, track, risks, optimize_tasks, compare_common_samples
from .route import default_points, plan_route

from .scene_service import scene_for, settings, current, CACHE, DEFAULT_RULES, DEFAULT_REGISTRY

router = APIRouter()

@router.get('/')
def index():
    return FileResponse(store.ROOT/'static'/'index.html')


@router.get('/api/scenes')
def list_scenes():
    return {"scenes":SCENES,"catalog":CATALOG}


@router.get('/api/scenes/{scene_id}')
def get_scene(scene_id:str):
    s=scene_for(scene_id);s["rules"],s["registry"]=settings(scene_id);return s


@router.put('/api/scenes/{scene_id}/devices/{device_id}')
def edit_device(scene_id:str,device_id:str,device:Device):
    s=scene_for(scene_id)
    if device.id!=device_id:raise HTTPException(422,"设备 ID 与路径不一致")
    if device_id not in {d["id"] for d in s["devices"]}:raise HTTPException(404,"设备不存在")
    s["devices"]=[device.model_dump() if d["id"]==device_id else d for d in s["devices"]]
    s["revision"]+=1;store.put(f'scene:{scene_id}',s);return s


@router.post('/api/scenes/{scene_id}/reset')
def reset(scene_id:str):
    scene_for(scene_id);s=seed(scene_id);s["revision"]=scene_for(scene_id)["revision"]+1
    store.put(f'scene:{scene_id}',s);store.put(f'rules:{scene_id}',DEFAULT_RULES);store.put(f'registry:{scene_id}',DEFAULT_REGISTRY)
    return s


class Zone(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    x0:float=Field(ge=0,le=2000);y0:float=Field(ge=0,le=2000)
    x1:float=Field(ge=0,le=2000);y1:float=Field(ge=0,le=2000)
    z0:float=Field(ge=0,le=300);z1:float=Field(ge=0,le=300)
    @model_validator(mode='after')
    def ordered(self):
        if self.x0>=self.x1 or self.y0>=self.y1 or self.z0>=self.z1:raise ValueError('区域边界需按从小到大填写')
        return self


class Rules(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    zone:Zone
    altitude_limit:float=Field(ge=0,le=300)
    loiter_radius:float=Field(ge=10,le=500)
    interference:bool=True


class RoutePlan(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    points:list[list[float]]|None=None
    budget:float=Field(default=50,ge=0,le=1000)
    step_m:float=Field(default=50,ge=10,le=200)
    fixed_network:bool=True
    offline_ids:list[str]=Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def waypoints(self):
        if self.points is None:
            return self
        if not 2<=len(self.points)<=80:
            raise ValueError('航路需要 2 到 80 个点')
        for point in self.points:
            if len(point)!=3:
                raise ValueError('每个航路点要有 x、y、z')
            x,y,z=point
            if not (0<=x<=2000 and 0<=y<=2000 and 0<=z<=300):
                raise ValueError('航路点需落在 2 公里范围、高度 300 米以内')
        return self


class Registration(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    status:Literal['approved','denied','unknown']
    route_y:float|None=Field(default=None,ge=0,le=2000)
    route_tolerance:float=Field(default=120,ge=10,le=500)


@router.put('/api/scenes/{scene_id}/rules')
def set_rules(scene_id:str,rules:Rules):
    scene_for(scene_id);store.put(f'rules:{scene_id}',rules.model_dump());return rules


@router.put('/api/scenes/{scene_id}/registry/{identity}')
def set_registration(scene_id:str,identity:str,data:Registration):
    scene_for(scene_id)
    if not identity or len(identity)>80:raise HTTPException(422,'识别码长度应为 1–80')
    _,registry=settings(scene_id);registry[identity]=data.model_dump();store.put(f'registry:{scene_id}',registry);return registry


@router.get('/api/scenes/{scene_id}/coverage')
def coverage(scene_id:str):return compute(scene_for(scene_id))


@router.get('/api/scenes/{scene_id}/run')
def run(scene_id:str,mode:Literal['fusion','single']='fusion'):
    result=current(scene_id,mode)
    for e in result['events']:
        review=store.get(f'review:{result["run_id"]}:{e["id"]}')
        if review:e.update(review)
    return result


@router.post('/api/scenes/{scene_id}/route')
def route(scene_id:str,request:RoutePlan):
    scene=scene_for(scene_id)
    points=request.points if request.points is not None else default_points(scene)
    try:
        return plan_route(scene,points,request.budget,request.step_m,request.fixed_network,request.offline_ids)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.post('/api/plan')
def plan(request:PlanRequest):
    s=scene_for(request.scene)
    rules,registry=settings(request.scene)
    result=optimize_tasks(s,request.budget,request.target,rules) if request.objective=='task' else optimize(s,request.budget,request.target)
    result['objective']=request.objective
    result['settings_fingerprint']=hashlib.sha256(json.dumps([rules,registry],sort_keys=True).encode()).hexdigest()
    with store.connection() as conn:
        conn.execute('INSERT INTO plans VALUES (?,?,?,?)',(result['id'],request.scene,json.dumps(result,ensure_ascii=False),store.now()))
    return result


def get_plan(scene_id,plan_id):
    scene_for(scene_id)
    with store.connection() as conn:r=conn.execute('SELECT payload FROM plans WHERE id=? AND scene=?',(plan_id,scene_id)).fetchone()
    if not r:raise HTTPException(404,'规划方案不存在')
    p=json.loads(r[0])
    if p.get('model_version')!=MODEL_VERSION:raise HTTPException(409,'覆盖模型版本已变化，请重新生成方案')
    if scene_for(scene_id)['revision']!=p['scene_revision']:raise HTTPException(409,'设备配置已变化，请重新生成方案')
    rules,registry=settings(scene_id)
    fingerprint=hashlib.sha256(json.dumps([rules,registry],sort_keys=True).encode()).hexdigest()
    if p.get('settings_fingerprint')!=fingerprint:raise HTTPException(409,'风险规则或登记配置已变化，请重新生成方案')
    return p


@router.get('/api/scenes/{scene_id}/plan-coverage/{plan_id}')
def plan_coverage(scene_id:str,plan_id:str):
    """Preview on identical grid geometry without applying the candidate devices."""
    import numpy as np
    p=get_plan(scene_id,plan_id);s=scene_for(scene_id)
    after=copy.deepcopy(s);after['devices']+=copy.deepcopy(p['selected'])
    before=compute(s);improved=compute(after)
    a=np.asarray(before['layers']);b=np.asarray(improved['layers'])
    valid=(a>=0)&(b>=0)
    changes={'newly_covered_voxels':int(((a==0)&(b>0)&valid).sum()),
             'lost_coverage_voxels':int(((a>0)&(b==0)&valid).sum()),
             'new_overlap_voxels':int(((a<2)&(b>=2)&valid).sum())}
    return {'scene':scene_id,'scene_revision':s['revision'],'plan_id':plan_id,
            'before':before,'after':improved,'changes':changes,
            'preview_only':True,'definition':'相同统一模型、50 m全景网格与有效空域；按融合Pd阈值判断覆盖，多设备重叠不代表定位精度。'}


@router.get('/api/scenes/{scene_id}/compare/{plan_id}')
def compare(scene_id:str,plan_id:str):
    p=get_plan(scene_id,plan_id);s=scene_for(scene_id);after=copy.deepcopy(s);after['devices']+=p['selected']
    rules,registry=settings(scene_id)
    base=current(scene_id);improved=execute(after,rules,registry);single=current(scene_id,'single')
    return {"before":base['metrics'],"after":improved['metrics'],"single":single['metrics'],
            "common_samples":compare_common_samples(base['metrics'],improved['metrics']),
            "before_events":len(base['events']),"after_events":len(improved['events']),"added_cost":p['added_cost'],
            "plan":p,"note":"相同真值、相同每设备随机噪声种子，按不同设备配置重新模拟量测。规划依据为覆盖或观测信息代理目标，实际感知指标可能不改善。"}


@router.post('/api/scenes/{scene_id}/apply/{plan_id}')
def apply_plan(scene_id:str,plan_id:str):
    p=get_plan(scene_id,plan_id);s=scene_for(scene_id);s['devices']+=p['selected'];s['revision']+=1
    store.put(f'scene:{scene_id}',s);return s


class Review(BaseModel):
    status:Literal['pending','acknowledged','resolved','false_alarm']
    note:str=Field(default='',max_length=1000)


@router.put('/api/scenes/{scene_id}/events/{event_id}')
def review_event(scene_id:str,event_id:str,review:Review,run_id:str|None=Query(default=None,pattern=r'^[a-f0-9]{16}$')):
    result=current(scene_id)
    if run_id and run_id!=result['run_id']:
        result=store.get(f'replay:{scene_id}:{run_id}')
        if not result:raise HTTPException(404,'回放版本不存在，请重新加载当前结果')
    if event_id not in {e['id'] for e in result['events']}:raise HTTPException(404,'事件不存在')
    data={**review.model_dump(),'reviewed_at':store.now()}
    store.put(f'review:{result["run_id"]}:{event_id}',data);return data


class Observation(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    sensor_id:str=Field(min_length=1,max_length=80)
    type:Literal['RADAR','RF','EO','IR','ADSB']
    position:list[float]=Field(min_length=3,max_length=3)
    sigma:float=Field(gt=0,le=1000)
    cooperative_id:str|None=Field(default=None,max_length=80)


class Frame(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    time:float=Field(ge=0,le=86400)
    observations:list[Observation]=Field(max_length=100)


class Replay(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str=Field(min_length=1,max_length=100)
    frames:list[Frame]=Field(min_length=1,max_length=600)
    @model_validator(mode='after')
    def chronological(self):
        times=[f.time for f in self.frames]
        if any(b<=a for a,b in zip(times,times[1:])):raise ValueError('量测帧时间必须严格递增')
        return self


@router.post('/api/scenes/{scene_id}/replay')
def replay(scene_id:str,data:Replay):
    scene_for(scene_id);rules,registry=settings(scene_id)
    history=track([f.model_dump() for f in data.frames])
    run_id=hashlib.sha256(json.dumps([data.model_dump(),rules,registry],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    result={'run_id':run_id,'label':data.label,'data_mode':'imported_observations','frames':history,'events':risks(history,rules,registry),
            'truth':[],'metrics':None,'limitations':['导入标准化位置量测，输入来源由用户声明。无独立真值，不能计算准确率和定位误差。']}
    store.put(f'replay:{scene_id}:{run_id}',result)
    return get_replay(scene_id,run_id)


@router.get('/api/scenes/{scene_id}/replays/{run_id}')
def get_replay(scene_id:str,run_id:str):
    scene_for(scene_id)
    result=store.get(f'replay:{scene_id}:{run_id}')
    if not result:raise HTTPException(404,'量测回放不存在')
    for e in result['events']:
        review=store.get(f'review:{result["run_id"]}:{e["id"]}')
        if review:e.update(review)
    return result


@router.get('/api/scenes/{scene_id}/report')
def report(scene_id:str):
    result=run(scene_id);s=get_scene(scene_id)
    return {'scene':s,'coverage':compute(s),'run':result,'created_at':store.now(),
            'contribution':{'problem':'复杂环境三维覆盖表达、干扰与协同精度演化、成本效能约束规划，以及大规模智能规划和三维可视化。',
                            'research':['复杂环境多模态三维覆盖建模','干扰与多节点协同精度评估','成本效能约束下自适应规划','运行数据驱动优化与综合三维平台'],
                            'status':'当前为可重复模拟量测与算法基线。研究贡献需通过真实数据、对照试验和外场验证证明。'}}

from . import scene3d

@router.get('/scene3d')
def scene3d_page():
    return RedirectResponse('./')

@router.post('/api/scenes/{scene_id}/ray')
def inspect_ray(scene_id:str,request:scene3d.RayRequest):
    try:return scene3d.inspect(scene_for(scene_id),request)
    except KeyError as e:raise HTTPException(404,str(e))
    except ValueError as e:raise HTTPException(422,str(e))

