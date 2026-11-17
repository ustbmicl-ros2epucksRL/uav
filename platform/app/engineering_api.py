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

# Application planning workbench; distinct from approval and flight-control systems.
from . import engineering as engineering

@router.get('/workbench')
def workbench():
    return FileResponse(store.ROOT/'static'/'workbench.html')

@router.post('/api/scenes/{scene_id}/engineering/volume')
def engineering_volume(scene_id:str,request:engineering.Volume):
    try:result=engineering.volume(scene_for(scene_id),request,store.get(f'calibration:{scene_id}'))
    except ValueError as e:raise HTTPException(422,str(e))
    store.put(f'volume:{scene_id}',result)
    return result

@router.post('/api/scenes/{scene_id}/engineering/plan')
def engineering_plan(scene_id:str,request:engineering.ConstrainedPlan):
    try:
        if len(engineering.grid(request.volume))>15000:raise ValueError('子集方案比较单次最多 15000 网格，请缩小区域或增大网格')
        result=engineering.constrained_plan(scene_for(scene_id),request,store.get(f'calibration:{scene_id}'))
    except ValueError as e:raise HTTPException(422,str(e))
    result['calibration_id']=(store.get(f'calibration:{scene_id}') or {}).get('id')
    store.put(f'engineering-plan:{scene_id}',result)
    return result

@router.post('/api/scenes/{scene_id}/engineering/calibration')
def engineering_calibration(scene_id:str,request:engineering.Calibration):
    s=scene_for(scene_id)
    if request.sensor_id not in {d['id'] for d in s['devices']}:raise HTTPException(422,'校正设备须属于当前场景')
    result=engineering.calibrate(request);store.put(f'calibration:{scene_id}',result)
    return result

@router.post('/api/scenes/{scene_id}/engineering/schedule')
def engineering_schedule(scene_id:str,request:engineering.Schedule):
    result=engineering.schedule(scene_for(scene_id),request);store.put(f'schedule:{scene_id}',result)
    return result

@router.get('/api/scenes/{scene_id}/engineering/report')
def engineering_report(scene_id:str):
    s=scene_for(scene_id)
    return {'project':'面向低空经济的多模态感知网络智能规划关键技术研究及示范应用',
            'scene':s,'created_at':store.now(),
            'volume':store.get(f'volume:{scene_id}'),'plan':store.get(f'engineering-plan:{scene_id}'),
            'calibration':store.get(f'calibration:{scene_id}'),'schedule':store.get(f'schedule:{scene_id}'),
            'source':'感知网络任务指标与工程验证要求',
            'acceptance':{'heterogeneous_equipment':{'target':5,'real_integrations':0},
                          'grid_resolution_m':{'general':10,'focus':2},'coverage':{'general':.9,'focus':.95},
                          'scenes':3,'task_types':5,'status':'演示基线，不构成验收证据'},
            'research_gaps':['真实设备与场景测绘数据','经标定的信号传播与多径模型','GPR 精度场与大规模 MILP/Pareto 求解',
                             'Transformer 特征融合与 IMM-UKF、跨站重识别','NTP/PTP 实际授时、真实云边端调度','外场对照与正式测试报告'],
            'artifact_state':{key: {'present':store.get(f'{key}:{scene_id}') is not None,
                                'scene_changed':(store.get(f'{key}:{scene_id}') or {}).get('scene_revision',s['revision'])!=s['revision'],
                                'calibration_changed':key in ('volume','engineering-plan') and
                                    (store.get(f'{key}:{scene_id}') or {}).get('calibration_id')!=(store.get(f'calibration:{scene_id}') or {}).get('id')}
                              for key in ('volume','engineering-plan','schedule')},
            'detector_reference':{'title':'UAV-DETR: DETR for Anti-Drone Target Detection','url':'https://arxiv.org/abs/2603.22841',
                                  'status':'论文已归档，尚未接入推理权重'}}

class Building(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    x:float=Field(ge=0,le=2000);y:float=Field(ge=0,le=2000)
    w:float=Field(gt=0,le=2000);d:float=Field(gt=0,le=2000);h:float=Field(gt=0,le=300)
    @model_validator(mode='after')
    def in_scene(self):
        if self.x+self.w>2000 or self.y+self.d>2000:raise ValueError('建筑超出场景边界')
        return self

class Resources(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source:str=Field(min_length=1,max_length=300)
    buildings:list[Building]=Field(max_length=200)
    devices:list[Device]=Field(min_length=1,max_length=50)
    candidates:list[Device]=Field(max_length=10)
    @model_validator(mode='after')
    def ids(self):
        for group in (self.devices,self.candidates):
            if len({d.id for d in group})!=len(group):raise ValueError('设备或候选 ID 不可重复')
        return self

@router.put('/api/scenes/{scene_id}/resources')
def set_resources(scene_id:str,request:Resources):
    s=scene_for(scene_id)
    s.update({k:[v.model_dump() for v in getattr(request,k)] for k in ('buildings','devices','candidates')})
    s['input_source']=request.source;s['revision']+=1
    store.put(f'scene:{scene_id}',s);return s

@router.post('/api/scenes/{scene_id}/engineering/apply/{plan_id}')
def apply_engineering_plan(scene_id:str,plan_id:str):
    s=scene_for(scene_id);p=store.get(f'engineering-plan:{scene_id}')
    if not p or p['id']!=plan_id:raise HTTPException(404,'当前方案不存在')
    if p.get('model_version')!=MODEL_VERSION:raise HTTPException(409,'覆盖模型版本已变化，请重新规划')
    if s['revision']!=p['scene_revision']:raise HTTPException(409,'场景配置已变化，请重新规划')
    if p.get('calibration_id')!=(store.get(f'calibration:{scene_id}') or {}).get('id'):
        raise HTTPException(409,'校正模型已变化，请重新规划')
    s['devices']+=copy.deepcopy(p['selected']);s['revision']+=1;store.put(f'scene:{scene_id}',s)
    return s

