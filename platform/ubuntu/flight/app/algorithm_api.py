"""Read-only algorithm experiments integrated into the existing ground station."""
import hashlib
import importlib.metadata
import json
import math
import subprocess
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import algorithm_routes as routing, algorithm_compute as computing
from . import business, incidents, patrol, route_plan, station
from .disposition import obstacles

router=APIRouter(prefix='/api/algorithms',tags=['航路与云边端仿真实验'])
RUN_LOCK=threading.Lock()


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class Node(StrictModel):
    id: str=Field(pattern=r'^[a-zA-Z0-9-]{1,40}$')
    kind: Literal['device','edge','cloud']
    position: tuple[float,float,float]
    gcycles_s: float=Field(gt=0,le=1000)
    power_w: float=Field(ge=0,le=10000)
    busy_s: float=Field(ge=0,le=3600)


class Link(StrictModel):
    source: str
    target: str
    mbps: float=Field(gt=0,le=100000)
    latency_s: float=Field(ge=0,le=60)
    max_range_m: float=Field(ge=0,le=100000)


class Task(StrictModel):
    id: str=Field(pattern=r'^[a-zA-Z0-9-]{1,40}$')
    gcycles: float=Field(gt=0,le=10000)
    input_mbit: float=Field(ge=0,le=100000)
    output_mbit: float=Field(ge=0,le=100000)
    deadline_s: float=Field(gt=0,le=86400)
    parents: list[str]=Field(max_length=12)


class Compute(StrictModel):
    nodes: list[Node]=Field(min_length=1,max_length=8)
    links: list[Link]=Field(max_length=56)
    tasks: list[Task]=Field(min_length=1,max_length=12)

    @model_validator(mode='after')
    def coherent(self):
        nodes={n.id:n for n in self.nodes};tasks={t.id:t for t in self.tasks}
        if len(nodes)!=len(self.nodes) or len(tasks)!=len(self.tasks):raise ValueError('节点或任务编号重复')
        if 'device' not in nodes or nodes['device'].kind!='device' or sum(n.kind=='device' for n in self.nodes)!=1:
            raise ValueError('必须且只能有一个编号为 device 的机载端')
        if len({(l.source,l.target) for l in self.links})!=len(self.links):raise ValueError('链路重复')
        if any(l.source not in nodes or l.target not in nodes or l.source==l.target for l in self.links):raise ValueError('链路端点无效')
        for t in self.tasks:
            if any(p not in tasks for p in t.parents) or len(set(t.parents))!=len(t.parents):raise ValueError('依赖任务无效')
            if t.parents and t.input_mbit:raise ValueError('非入口任务从父任务读取数据，input_mbit 应为 0')
        done=set()
        while len(done)<len(tasks):
            ready={t.id for t in self.tasks if t.id not in done and set(t.parents)<=done}
            if not ready:raise ValueError('任务依赖不能有环')
            done|=ready
        return self


class Experiment(StrictModel):
    area_id: Literal['west','east']='west'
    scene: Literal['urban','corridor','electromagnetic']='urban'
    planner: Literal['bitstar','acs_ds','astar']='bitstar'
    seed: int=Field(default=7,ge=1,le=1000000)
    altitude_m: float=Field(default=120,ge=30,le=280)
    max_altitude_m: float=Field(default=280,ge=30,le=300)
    start_xy: tuple[float,float]|None=None
    goal_xy: tuple[float,float]|None=None
    solve_seconds: float=Field(default=1.5,ge=.1,le=4)
    compute: Compute|None=None

    @model_validator(mode='after')
    def heights(self):
        if self.max_altitude_m<self.altitude_m:raise ValueError('最大高度不能小于巡航起始高度')
        return self


def context(body):
    area=patrol.AREAS[body.area_id];x0,y0,x1,y1=area['bounds']
    bounds=dict(xyz_min=[x0,y0,30],xyz_max=[x1,y1,body.max_altitude_m])
    buildings=route_plan.scene_record(body.scene)['buildings']
    with business.connect() as db:space=incidents.airspace(db)
    boxes=obstacles(buildings,space['zones'])
    boxes=[b for b in boxes if all(b['upper'][i]>=bounds['xyz_min'][i] and b['lower'][i]<=bounds['xyz_max'][i] for i in range(3))]
    candidates=[[x,y,body.altitude_m] for x in (x0+25,(x0+x1)/2,x1-25)
                for y in (y0+25,(y0+y1)/2,y1-25)]
    candidates=[p for p in candidates if routing.valid_point(p,bounds,boxes)]
    if len(candidates)<2 and (body.start_xy is None or body.goal_xy is None):raise HTTPException(409,'该范围/高度没有足够安全端点，请修改参数')
    start=[*body.start_xy,body.altitude_m] if body.start_xy else candidates[0]
    goal=[*body.goal_xy,body.altitude_m] if body.goal_xy else max(candidates,key=lambda p:math.dist(start,p))
    return dict(area=area,bounds=bounds,boxes=boxes,start=start,goal=goal,
                anchor=station.config(),airspace_revision=space['revision'],scene=body.scene)


def sources():
    try:version=importlib.metadata.version('ompl')
    except importlib.metadata.PackageNotFoundError:version=None
    return dict(ompl=dict(version=version,repository='https://github.com/ompl/ompl',
                         paper='https://doi.org/10.1177/0278364919890396',algorithm='BIT*'),
                hatto=dict(commit='542b65fd19743f29809aa1a06054aec8cb89d622',
                           repository='https://github.com/Shuaijun-LIU/HATTO-UFog',
                           paper='https://doi.org/10.1016/j.comnet.2026.112064',algorithm='ACS-DS 独立模块'),
                heft=dict(commit='5e976e9c5c9d6ef6f608e780f49dc5bcbcc42745',
                          repository='https://github.com/mrocklin/heft',
                          paper='https://doi.org/10.1109/71.993206',algorithm='HEFT（成熟基线，2002）'))


def directory():
    root=Path(os.environ.get('FLIGHT_ALGORITHM_DIR',Path(__file__).parents[1]/'data'/'algorithm-runs'))
    root.mkdir(parents=True,exist_ok=True)
    return root


@router.get('/catalog')
def catalog(area_id:Literal['west','east']='west',scene:Literal['urban','corridor','electromagnetic']='urban',altitude_m:float=120):
    try:body=Experiment(area_id=area_id,scene=scene,altitude_m=altitude_m)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    ctx=context(body)
    return dict(sources=sources(),context=ctx,compute=computing.default_compute(ctx['bounds']))


@router.get('/runs')
def runs():
    paths=sorted(directory().glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]
    result=[]
    for p in paths:
        try:
            r=json.loads(p.read_text());result.append({k:r[k] for k in ('id','created_at','status','request')})
        except (ValueError,KeyError):continue
    return dict(records=result)


@router.get('/runs/{ident}')
def read(ident:str):
    if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise HTTPException(404,'实验不存在')
    path=directory()/(ident+'.json')
    if not path.exists():raise HTTPException(404,'实验不存在')
    return json.loads(path.read_text())


@router.post('/runs')
def run(body:Experiment):
    if not RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'已有算法实验计算中，请等待完成')
    try:
        ctx=context(body);config=(body.compute or Compute(**computing.default_compute(ctx['bounds']))).model_dump()
        began=time.monotonic()
        try:route=routing.solve_route(ctx['start'],ctx['goal'],ctx['bounds'],ctx['boxes'],body.planner,body.seed,body.solve_seconds)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        except (RuntimeError,subprocess.TimeoutExpired) as exc:raise HTTPException(503,str(exc)) from exc
        snapshots=[]
        if route['status']=='ok':
            # Uniform arclength positions rather than arbitrary waypoint spacing.
            lengths=[math.dist(a,b) for a,b in zip(route['points'],route['points'][1:])]
            for fraction in (0,.25,.5,.75,1):
                distance=route['distance_m']*fraction;remaining=distance
                for i,length in enumerate(lengths):
                    if remaining<=length+1e-7 or i==len(lengths)-1:
                        f=min(1,remaining/max(length,1e-9))
                        pos=[a+(b-a)*f for a,b in zip(route['points'][i],route['points'][i+1])];break
                    remaining-=length
                snapshots.append(dict(fraction=fraction,distance_m=distance,position=pos,
                                      comparisons=[computing.evaluate(config,pos,p) for p in ('heft','local','edge','cloud')]))
        result=dict(id=uuid.uuid4().hex,created_at=time.time(),status=route['status'],request=body.model_dump(),
                    context=ctx,compute=config,route=route,snapshots=snapshots,sources=sources(),
                    elapsed_s=time.monotonic()-began,source='simulation',
                    assumptions=['独立静态航路采样点，每个采样点任务从零开始，不连续累积队列',
                                 '一个节点一个预留串行计算通道；链路使用预留带宽，不建模争用',
                                 '时延为传输、依赖等待和计算；能耗只计计算功率×计算时间',
                                 'HEFT 优化完工时间，不保证截止期或全局最优',
                                 '算法预览不下发任务，不控制飞机；障碍是合成体块，禁飞区是仿真配置'])
        result['input_sha256']=hashlib.sha256(json.dumps(dict(context=ctx,request=body.model_dump(),compute=config),sort_keys=True).encode()).hexdigest()
        path=directory()/(result['id']+'.json');tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2));tmp.replace(path)
        return result
    finally:RUN_LOCK.release()


@router.get('/runs/{ident}/mission')
def mission(ident: str):
    """Freeze the whole flight for approval; never upload or arm here."""
    from . import disposition, sim
    result=read(ident)
    if result['status']!='ok':raise HTTPException(409,'无安全规划结果，不能创建任务')
    ctx=result['context']
    if ctx['anchor']!=station.config():raise HTTPException(409,'坐标原点已变化，请重新规划')
    with business.connect() as db:space=incidents.airspace(db)
    if space['revision']!=ctx['airspace_revision']:raise HTTPException(409,'空域已变化，请重新规划')
    points=result['route']['points'];height=points[0][2]
    if height<40:raise HTTPException(409,'SITL 起飞高度至少 40 m，请重新规划')
    boxes=obstacles(route_plan.scene_record(ctx['scene'])['buildings'],space['zones'])
    home=[0,0,height]
    incoming=disposition.detour(home,points[0],height,boxes,sim.FENCE)
    outgoing=disposition.detour(points[-1],home,height,boxes,sim.FENCE)
    if not incoming or not outgoing:raise HTTPException(409,'起降点与区域之间无安全连接航线')
    route=[]
    for p in [[0,0,0],home]+incoming+points+outgoing+[home,[0,0,0]]:
        p=list(p)
        if not route or route[-1]!=p:route.append(p)
    checked=disposition.check(route,boxes,sim.FENCE,10,100)
    if not checked['passed'] or len(route)>20:raise HTTPException(409,dict(message='完整航线未通过安全/航点数量检查',check=checked))
    return dict(experiment_id=ident,area_id=result['request']['area_id'],scene=ctx['scene'],
                route=[dict(zip(('x','y','z'),p)) for p in route],anchor=ctx['anchor'],
                compute=result['compute'],input_sha256=result['input_sha256'],check=checked)
