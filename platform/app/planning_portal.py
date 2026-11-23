"""Versioned five-page planning workflow. All deployment actions are local scenarios."""
import copy
import csv
import hashlib
import io
import json
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import store, engineering
from .schemas import Device
from .planning_assessment import planned_devices
from .scenes import SCENES, seed, CATALOG, TYPES

router = APIRouter()

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)

class Focus(Strict):
    x0: float = Field(default=700, ge=0, le=2000)
    x1: float = Field(default=1300, ge=0, le=2000)
    y0: float = Field(default=700, ge=0, le=2000)
    y1: float = Field(default=1300, ge=0, le=2000)
    z0: float = Field(default=0, ge=0, le=300)
    z1: float = Field(default=300, ge=0, le=300)
    @model_validator(mode='after')
    def ordered(self):
        if self.x0 >= self.x1 or self.y0 >= self.y1 or self.z0 >= self.z1:
            raise ValueError('重点区上下界顺序错误')
        return self

class Task(engineering.ConstrainedPlan):
    name: str = Field(min_length=1, max_length=100)
    scene: Literal['urban', 'corridor', 'electromagnetic'] = 'urban'
    revision: int = Field(default=0, ge=0)
    focus: Focus = Field(default_factory=Focus)
    @model_validator(mode='after')
    def meaningful(self):
        self.name = self.name.strip()
        if not self.name: raise ValueError('任务名称不能为空')
        engineering.grid(self.volume)
        return self

class Site(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)
    min_height: float = Field(default=1, ge=1, le=300)
    max_height: float = Field(default=300, ge=1, le=300)
    capacity: int = Field(default=10, ge=1, le=50)
    power_w: float = Field(default=5000, ge=0, le=1000000)
    bandwidth_mbps: float = Field(default=1000, ge=0, le=1000000)
    setup_cost: float = Field(default=0, ge=0, le=10000)
    @model_validator(mode='after')
    def heights(self):
        if self.min_height > self.max_height: raise ValueError('最低挂高不能超过最高挂高')
        return self

class EquipmentModel(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    type: Literal['RADAR', 'EO', 'RF', 'IR', 'ADSB']
    range: float = Field(gt=0, le=20000)
    fov: float = Field(gt=0, le=360)
    vertical_fov: float = Field(default=110, gt=0, le=180)
    cost: float = Field(ge=0, le=10000)
    power_w: float = Field(default=100, ge=0, le=1000000)
    bandwidth_mbps: float = Field(default=10, ge=0, le=1000000)

class Deployment(Device):
    site_id: str
    model_id: str = ''
    asset_code: str = Field(default='', max_length=80)
    power_w: float = Field(default=100, ge=0, le=1000000)
    bandwidth_mbps: float = Field(default=10, ge=0, le=1000000)

class Building(Strict):
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)
    w: float = Field(gt=0, le=2000)
    d: float = Field(gt=0, le=2000)
    h: float = Field(gt=0, le=300)
    @model_validator(mode='after')
    def within(self):
        if self.x+self.w > 2000 or self.y+self.d > 2000: raise ValueError('建筑超出场景')
        return self


def capacity_errors(resources, selected):
    selected_ids = {d['id'] for d in selected}
    devices = resources['devices'] + [d for d in resources['candidates'] if d['id'] in selected_ids]
    errors = []
    for s in resources['sites']:
        ds = [d for d in devices if d['site_id']==s['id']]
        for label, amount, maximum in [('槽位', len(ds), s['capacity']), ('供电', sum(d.get('power_w',100) for d in ds), s.get('power_w',5000)), ('回传带宽', sum(d.get('bandwidth_mbps',10) for d in ds), s.get('bandwidth_mbps',1000))]:
            if amount > maximum: errors.append(f'{s["name"]}{label}不足：需要 {amount}，容量 {maximum}')
    return errors

class ResourceSet(Strict):
    revision: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=300)
    sites: list[Site] = Field(max_length=60)
    models: list[EquipmentModel] = Field(default_factory=list, max_length=100)
    buildings: list[Building] | None = Field(default=None, max_length=200)
    devices: list[Deployment] = Field(max_length=50)
    candidates: list[Deployment] = Field(max_length=10)
    @model_validator(mode='after')
    def constraints(self):
        sites = {s.id:s for s in self.sites}
        models = {m.id:m for m in self.models}
        if len(sites)!=len(self.sites) or len(models)!=len(self.models): raise ValueError('站址或型号编号重复')
        devices=self.devices+self.candidates
        if len({d.id for d in devices})!=len(devices): raise ValueError('已有与候选设备编号必须唯一')
        codes=[d.asset_code for d in devices if d.asset_code]
        if len(set(codes))!=len(codes): raise ValueError('实物资产编号重复')
        for d in devices:
            s=sites.get(d.site_id)
            if not s: raise ValueError('设备引用了不存在的站址')
            if d.x!=s.x or d.y!=s.y: raise ValueError('设备平面位置必须与站址一致')
            if not s.min_height<=d.z<=s.max_height: raise ValueError('设备挂高超出站址允许范围')
            if d.model_id and (d.model_id not in models or models[d.model_id].type!=d.type): raise ValueError('设备型号不存在或类型不符')
        errors=capacity_errors(self.model_dump(), [])
        if errors: raise ValueError('；'.join(errors))
        return self


def read_record(kind, identifier):
    data=store.get(f'portal:{kind}:{identifier}')
    if data is None: raise HTTPException(404,'记录不存在')
    return data


def records(kind):
    with store.connection() as conn:
        rows=conn.execute('SELECT value FROM state WHERE key LIKE ?', (f'portal:{kind}:%',)).fetchall()
    return sorted([json.loads(r[0]) for r in rows],key=lambda x:x.get('updated_at',x.get('created_at','')),reverse=True)


from .scene_service import scene_for


def resources(scene_id, scene=None):
    s=scene if scene is not None else scene_for(scene_id)
    saved=store.get(f'portal-resources:{scene_id}') or {}
    # Preserve engineering metadata when the legacy editor updates device states/angles.
    sites=copy.deepcopy(saved.get('sites', []))
    old={d['id']:d for group in ('devices','candidates') for d in saved.get(group,[])}
    models=saved.get('models') or [EquipmentModel(id=k,name=TYPES[k]+'示例型号',type=k,**v).model_dump() for k,v in CATALOG.items()]
    groups={}
    for group in ('devices','candidates'):
        groups[group]=[]
        for d in s[group]:
            if group=='candidates' and d['id'] in {v['id'] for v in s['devices']}: continue
            previous=old.get(d['id'],{})
            site=next((x for x in sites if x['id']==previous.get('site_id') and x['x']==d['x'] and x['y']==d['y']),None)
            if site is None:
                sid='S-'+d['id'][:35]
                if any(x['id']==sid for x in sites): sid='S-'+uuid.uuid4().hex[:8]
                site=Site(id=sid,name=d['name']+'站址',x=d['x'],y=d['y']).model_dump();sites.append(site)
            extra={k:previous[k] for k in ('model_id','asset_code','power_w','bandwidth_mbps') if k in previous}
            if extra.get('model_id') and not any(m['id']==extra['model_id'] and m['type']==d['type'] for m in models): extra['model_id']=''
            groups[group].append(Deployment(**Device.model_validate(d).model_dump(),site_id=site['id'],**extra).model_dump())
    return dict(revision=s['revision'],source=s.get('input_source','合成演示场景'),sites=sites,models=models,buildings=s['buildings'],**groups)


def fingerprint(task,scene,calibration):
    return hashlib.sha256(json.dumps([engineering.MODEL_VERSION,task,scene,calibration],sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def task_scene(task, scene):
    s=copy.deepcopy(scene)
    s['zone']=task.get('focus',Focus().model_dump())
    return s


def inputs(task_id):
    task=read_record('task',task_id);scene=scene_for(task['scene']);calibration=store.get(f'calibration:{task["scene"]}')
    return task,scene,calibration


def price(resource, selected):
    ids={d['id'] for d in selected}
    ds=[d for d in resource['candidates'] if d['id'] in ids]
    existing={d['site_id'] for d in resource['devices']}
    new={d['site_id'] for d in ds}-existing
    return round(sum(d['cost'] for d in ds)+sum(s.get('setup_cost',0) for s in resource['sites'] if s['id'] in new),6)


@router.get('/api/workflow/tasks')
def tasks(): return records('task')

@router.post('/api/workflow/tasks')
def create_task(request:Task):
    scene_for(request.scene)
    task=dict(request.model_dump(),id=uuid.uuid4().hex[:12],updated_at=store.now());task['revision']=1
    store.put(f'portal:task:{task["id"]}',task);return task

@router.put('/api/workflow/tasks/{task_id}')
def edit_task(task_id:str,request:Task):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT value FROM state WHERE key=?',(f'portal:task:{task_id}',)).fetchone()
        if not row: raise HTTPException(404,'任务不存在')
        if json.loads(row[0])['revision']!=request.revision: raise HTTPException(409,'任务已更新，请刷新后重试')
        task=dict(request.model_dump(),id=task_id,updated_at=store.now());task['revision']+=1
        conn.execute('UPDATE state SET value=? WHERE key=?',(json.dumps(task,ensure_ascii=False),f'portal:task:{task_id}'))
    return task

@router.get('/api/workflow/resources/{scene_id}')
def get_resources(scene_id:str): return resources(scene_id)

@router.post('/api/workflow/resources/{scene_id}/validate')
def validate_resources(scene_id:str,request:ResourceSet):
    scene_for(scene_id)
    return request.model_dump()

@router.put('/api/workflow/resources/{scene_id}')
def save_resources(scene_id:str,request:ResourceSet):
    initial=scene_for(scene_id)
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT value FROM state WHERE key=?',(f'scene:{scene_id}',)).fetchone()
        scene=json.loads(row[0]) if row else initial
        if request.revision!=scene['revision']: raise HTTPException(409,'场景已更新，请重新加载资源')
        scene.update({k:[{field:getattr(d,field) for field in Device.model_fields} for d in getattr(request,k)] for k in ('devices','candidates')})
        if request.buildings is not None: scene['buildings']=[b.model_dump() for b in request.buildings]
        scene['revision']+=1;scene['input_source']=request.source
        result=request.model_dump();result['revision']=scene['revision'];result['buildings']=scene['buildings']
        for key,value in ((f'scene:{scene_id}',scene),(f'portal-resources:{scene_id}',result)):
            conn.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value,ensure_ascii=False)))
    return result

@router.post('/api/workflow/tasks/{task_id}/diagnose')
def diagnose(task_id:str):
    task,scene,calibration=inputs(task_id)
    try: result=engineering.volume(task_scene(task,scene),engineering.Volume(**task['volume']),calibration)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    result.update(task_revision=task['revision'],fingerprint=fingerprint(task,scene,calibration),scene=scene,focus=task.get('focus'))
    store.put(f'portal-diagnosis:{task_id}',result)
    return result

@router.get('/api/workflow/tasks/{task_id}/diagnosis')
def saved_diagnosis(task_id:str):
    task,scene,calibration=inputs(task_id)
    result=store.get(f'portal-diagnosis:{task_id}')
    if result: result['stale']=result['fingerprint']!=fingerprint(task,scene,calibration)
    return result

class PlanningChoice(Strict):
    selected_ids:list[str]|None=Field(default=None,max_length=10)

@router.post('/api/workflow/tasks/{task_id}/plan')
def generate(task_id:str,choice:PlanningChoice=PlanningChoice()):
    task,scene,calibration=inputs(task_id);resource=resources(task['scene'],scene)
    request=engineering.ConstrainedPlan(**{k:task[k] for k in engineering.ConstrainedPlan.model_fields})
    try:
        if len(engineering.grid(request.volume))>15000: raise ValueError('方案比较最多 15000 网格，请缩小任务区域或增大间距')
        errors=capacity_errors(resource,[])
        if errors: raise ValueError('；'.join(errors))
        assessed=task_scene(task,scene)
        if choice.selected_ids is None:
            result=engineering.constrained_plan(assessed,request,calibration,allowed=lambda ds:not capacity_errors(resource,ds),cost_of=lambda ds:price(resource,ds))
        else:
            ids=set(choice.selected_ids)
            available={d['id'] for d in scene['candidates'] if d['status']=='online'}
            if not ids<=available or len(ids)!=len(choice.selected_ids): raise ValueError('候选编号重复、不存在或已离线')
            ds=[d for d in scene['candidates'] if d['id'] in ids]
            errors=capacity_errors(resource,ds)
            if errors: raise ValueError('；'.join(errors))
            trial=copy.deepcopy(assessed);trial['devices']+=ds
            stats=engineering.volume(trial,request.volume,calibration)['stats']
            deficits=[max(0,target-(stats[k] or 0)) for k,target in [('coverage',request.coverage_target),('focus_coverage',request.focus_target),('precision_pass_rate',request.precision_rate_target)]]
            cost=price(resource,ds)
            result=dict(id=uuid.uuid4().hex[:12],scene=scene['id'],scene_revision=scene['revision'],selected=ds,added_cost=cost,stats=stats,feasible=cost<=request.budget and stats['focus_coverage'] is not None and max(deficits)<1e-9,constraint_deficit=sum(deficits),request=request.model_dump(),evaluated_subsets=1,created_at=store.now(),method='人工候选组合，在相同模型与工程约束下验算',scope='所选计算区域的数值评估')
        before=engineering.volume(assessed,request.volume,calibration)['stats']
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    result.update(engineering.metadata(request.volume))
    plan=dict(result,task_id=task_id,task_name=task['name'],before=before,snapshot=dict(task=task,scene=scene,calibration=calibration,resources=resource),fingerprint=fingerprint(task,scene,calibration),status='evaluated',history=[dict(action='evaluated',time=store.now())])
    store.put(f'portal:plan:{plan["id"]}',plan)
    return plan

@router.get('/api/workflow/plans')
def plans(): return [{k:v for k,v in p.items() if k!='snapshot'} for p in records('plan')]

@router.get('/api/workflow/plans/{plan_id}')
def get_plan(plan_id:str):
    p=read_record('plan',plan_id);p['stale']=plan_stale(p);return p


def plan_stale(plan):
    if plan.get('assessment_job_id'):
        from .acceptance_api import is_stale
        from .jobs import get
        return is_stale(get(plan['assessment_job_id']))
    return plan['fingerprint'] != fingerprint(*inputs(plan['task_id']))

@router.get('/api/workflow/plans/{plan_id}/preview')
def preview(plan_id:str):
    p=read_record('plan',plan_id);snapshot=p['snapshot'];scene=task_scene(snapshot['task'],snapshot['scene']);request=engineering.Volume(**snapshot['task']['volume'])
    if p.get('assessment_job_id'):
        from .acceptance_api import detail
        result = detail(p['assessment_job_id'])['result']
        views = {}
        for key in ('before', 'after'):
            views[key] = dict(result[key], settings=dict(bounds=result['settings']['bounds'], cell=result['settings']['wide_cell'], precision_m=result['precision_threshold_m']))
        scene['devices'] = planned_devices(scene, p['selected'], p.get('updated_devices'))
        return dict(**views, scene=scene, selected=p['selected'], plan_id=plan_id, scope=result['aggregation'])
    before=engineering.volume(scene,request,snapshot['calibration'])
    scene['devices']+=p['selected']
    after=engineering.volume(scene,request,snapshot['calibration'])
    return dict(before=before,after=after,scene=scene,selected=p['selected'],plan_id=plan_id,scope='按历史输入快照重算；不会改变部署')

class Transition(Strict):
    status:Literal['confirmed','archived']

@router.post('/api/workflow/plans/{plan_id}/status')
def transition(plan_id:str,request:Transition):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT value FROM state WHERE key=?',(f'portal:plan:{plan_id}',)).fetchone()
        if not row: raise HTTPException(404,'方案不存在')
        p=json.loads(row[0])
        if request.status=='confirmed':
            if p['status']!='evaluated': raise HTTPException(409,'仅已评估方案可以确认')
            if plan_stale(p): raise HTTPException(409,'任务、场景或模型已变化，请重新规划')
            if not p['feasible']: raise HTTPException(409,'方案未满足全部约束，不能确认；可归档保留评估')
        elif p['status']=='archived': raise HTTPException(409,'方案已归档')
        p['status']=request.status;p['updated_at']=store.now();p.setdefault('history',[]).append(dict(action=request.status,time=p['updated_at']))
        conn.execute('UPDATE state SET value=? WHERE key=?',(json.dumps(p,ensure_ascii=False),f'portal:plan:{plan_id}'))
    return p

@router.post('/api/workflow/plans/{plan_id}/apply')
def apply(plan_id:str):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT value FROM state WHERE key=?',(f'portal:plan:{plan_id}',)).fetchone()
        if not row: raise HTTPException(404,'方案不存在')
        p=json.loads(row[0])
        if p['status']!='confirmed' or p.get('applied_at'): raise HTTPException(409,'仅未应用的已确认方案可应用')
        task,scene,calibration=inputs(p['task_id'])
        if plan_stale(p): raise HTTPException(409,'输入已变化，请重新规划')
        ids={d['id'] for d in p['selected']}
        resource=resources(p['scene'],scene)
        updates = {d['id']: d for d in p.get('updated_devices', [])}
        resource['devices'] = [Deployment.model_validate(dict(d, **updates.get(d['id'], {}))).model_dump() for d in resource['devices']]
        scene['devices'] = [{k:v for k,v in d.items() if k in Device.model_fields} for d in planned_devices(scene, [], p.get('updated_devices'))]
        candidates = {d['id']: d for d in resource['candidates']}
        resource['devices'] += [Deployment.model_validate(dict(candidates.get(d['id'], {}), **d)).model_dump() for d in p['selected']]
        resource['candidates']=[d for d in resource['candidates'] if d['id'] not in ids]
        scene['devices'] += [{k: d[k] for k in Device.model_fields if k in d} for d in p['selected']]
        scene['candidates']=[d for d in scene['candidates'] if d['id'] not in ids]
        scene['revision']+=1;resource['revision']=scene['revision']
        p['applied_at']=store.now();p['applied_revision']=scene['revision'];p.setdefault('history',[]).append(dict(action='applied_to_scenario',time=p['applied_at']))
        for key,value in ((f'portal:plan:{plan_id}',p),(f'scene:{p["scene"]}',scene),(f'portal-resources:{p["scene"]}',resource)):
            conn.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value,ensure_ascii=False)))
    return p

class RouteRequest(Strict):
    points:list[list[float]]|None=Field(default=None,min_length=2,max_length=80)
    offline_ids:list[str]=Field(default_factory=list,max_length=50)
    step_m:float=Field(default=50,ge=10,le=200)
    @model_validator(mode='after')
    def valid(self):
        if self.points:
            for p in self.points:
                if len(p)!=3 or not (0<=p[0]<=2000 and 0<=p[1]<=2000 and 0<=p[2]<=300): raise ValueError('航路点应为场景内 XYZ 三维坐标')
        return self

@router.post('/api/workflow/tasks/{task_id}/route')
def route(task_id:str,request:RouteRequest):
    from .route import default_points,plan_route
    task,scene,calibration=inputs(task_id)
    try: result=plan_route(scene,request.points or default_points(scene),0,request.step_m,True,request.offline_ids,engineering.Volume(**task["volume"]))
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    result.update(task_id=task_id,request=request.model_dump(),fingerprint=fingerprint(task,scene,calibration),scene=scene)
    store.put(f'portal-route:{task_id}',result);return result

@router.get('/api/workflow/tasks/{task_id}/route')
def saved_route(task_id:str):
    task,scene,calibration=inputs(task_id);r=store.get(f'portal-route:{task_id}')
    if r: r['stale']=r['fingerprint']!=fingerprint(task,scene,calibration)
    return r

@router.get('/api/workflow/plans/{plan_id}/deployment.csv')
def deployment_csv(plan_id:str):
    p=read_record('plan',plan_id);out=io.StringIO();writer=csv.writer(out)
    writer.writerow(['类别','设备编号','名称','类型','X/m','Y/m','挂高/m','方位/度','俯仰/度','设备安装费/万元'])
    for kind,group in (('已有/转向后' if p.get('updated_devices') else '已有',planned_devices(p['snapshot']['scene'], [], p.get('updated_devices'))),('新增',p['selected'])):
        for d in group:
            values=[kind]+[d.get(k,'') for k in ('id','name','type','x','y','z','azimuth','pitch','cost')]
            writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in values])
    if p.get('cost_breakdown'):
        writer.writerow(['转向调试预留/万元',p['cost_breakdown']['retuning_reserve']])
    writer.writerow(['新增总费用（含新站址一次性费用）',p['added_cost']])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="deployment-{plan_id}.csv"'})

@router.get('/tasks')
@router.get('/resources')
@router.get('/diagnosis')
@router.get('/planning')
@router.get('/plans')
def page(): return FileResponse(store.ROOT/'static'/'portal.html')


@router.get('/flight-route/{task_id}')
def flight_route_page(task_id: str):
    return FileResponse(store.ROOT / 'static' / 'flight-route.html')


@router.post('/api/flight-route/{task_id}')
def plan_approved_flight(task_id: str):
    import re
    import httpx
    from ubuntu.flight.app import disposition
    from .flight_tools import endpoints

    if not re.fullmatch(r'TASK-[0-9]+', task_id):
        raise HTTPException(422, '无效任务编号')
    ground, service = endpoints()
    try:
        with httpx.Client(timeout=10, trust_env=False) as client:
            def fetch(url):
                response = client.get(url)
                response.raise_for_status()
                return response.json()
            task = fetch(f'{service}/api/tasks/{task_id}')
            if task['status'] != 'scheduled':
                raise HTTPException(409, '只有待执行任务可以规划航线')
            buildings = fetch(f"{ground}/api/obstacles?scene={task['scene']}")['buildings']
            airspace = fetch(ground + '/api/business/airspace')
            fence = fetch(ground + '/api/station')['limits']['fence']
    except httpx.HTTPStatusError as error:
        raise HTTPException(error.response.status_code, '无法读取任务或空域') from error
    except httpx.RequestError as error:
        raise HTTPException(502, '服务平台或地面站不可达') from error
    approved = [[p['x'], p['y'], p['z']] for p in task['route']]
    boxes = disposition.obstacles(buildings, airspace['zones'])
    route = [approved[0]]
    for start, end in zip(approved, approved[1:]):
        leg = [start, end] if disposition.clear(start, end, boxes) else disposition.detour(start, end, max(start[2], end[2]), boxes, fence)
        if not leg:
            raise HTTPException(409, '批准航点之间未找到可行航线')
        for point in leg[1:]:
            if point != route[-1]:
                route.append(point)
    check = disposition.check(route, boxes, fence, speed=10)
    result = dict(task_id=task_id, application_id=task['application_id'], status=task['status'],
                  route=route, approved_route_matches=route == approved, check=check,
                  airspace_revision=airspace['revision'])
    store.put('flight-route:' + task_id, result)
    return result


@router.get('/api/flight-route/{task_id}')
def saved_flight_route(task_id: str):
    plan = store.get('flight-route:' + task_id)
    if plan is None:
        raise HTTPException(404, '尚未保存航线')
    return plan
