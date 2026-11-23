"""Local simulation incident evidence and idempotent operator disposition."""
import copy
import json
import time
import uuid
from typing import Literal
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import business as b, sim, disposition, route_plan

router = APIRouter(prefix='/api/business/incidents')


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    vehicle_id: str
    command: Literal['rtl', 'land']
    actor: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=1, max_length=300)
    plan_id: str | None = None
    route_id: str | None = None


def capture(snapshot):
    alerts = [('conflict:' + ':'.join(sorted(c['pair'])), 'predicted_conflict', c['pair'], c) for c in snapshot['conflicts']]
    track = snapshot.get('track', {})
    if track.get('estimate'):
        alerts.append(('identity:unconfirmed-target', 'identity_unconfirmed', ['unconfirmed-target'], track))
    with sim.LOCK, b.connect() as db:
        rows = [json.loads(row[0]) for row in db.execute("SELECT body FROM records WHERE key LIKE 'incident:%'")]
        for item in rows:
            if item['boot'] != b.BOOT or item['key'] not in {a[0] for a in alerts}:
                item['active'] = False
                b.put(db, 'incident:' + item['id'], item)
        for key, kind, pair, detail in alerts:
            if any(i['key'] == key and i['active'] and i['boot'] == b.BOOT for i in rows):
                continue
            vehicles = [v for v in snapshot['vehicles'] if v['id'] in pair]
            # Identity-only observations carry no authority to command unrelated aircraft.
            item = dict(id='INC-' + uuid.uuid4().hex[:12], key=key, kind=kind, pair=pair, active=True,
                        status='open', boot=b.BOOT, created_at=time.time(), source='simulation',
                        evidence=dict(detail=detail, vehicles=vehicles, track=track, anchor=snapshot['anchor'],
                                      tasks=[t for t in snapshot['tasks'] if t['id'] in {v.get('business_task_id') for v in vehicles}],
                                      trajectories={v['id']:sim.track_of(sim.get(v['id'])) for v in vehicles}), action=None)
            b.put(db, 'incident:' + item['id'], item)
            rows.append(item)
        return sorted(rows, key=lambda r:r['created_at'], reverse=True)


def read(db, ident):
    item = b.get(db, 'incident:' + ident)
    if not item:
        raise HTTPException(404, '预警不存在')
    return item


@router.get('/{ident}')
def detail(ident: str):
    with b.connect() as db:
        return read(db, ident)


@router.post('/{ident}/act')
def act(ident: str, decision: Decision):
    with sim.LOCK, b.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        item = read(db, ident)
        if item['action']:
            if item['action']['decision'] != decision.model_dump():
                raise HTTPException(409, '已有处置，请查询回执；不能替换处置对象或指令')
            return item
        current = b.safety_snapshot()
        still_present = any(sorted(c['pair']) == sorted(item['pair']) for c in current['conflicts'])
        if item['boot'] != b.BOOT or not item['active'] or not still_present:
            raise HTTPException(409, '预警已过期，请刷新态势')
        original = next((v for v in item['evidence']['vehicles'] if v['id']==decision.vehicle_id), None)
        if not original:
            raise HTTPException(409, '只能处置预警直接关联的合作仿真飞机')
        v = b.vehicle(decision.vehicle_id)
        if not v.armed or v.done or v.mission != original['mission'] or getattr(v,'business_task_id',None) != original.get('business_task_id'):
            raise HTTPException(409, '飞机或任务已变化，请重新核验')
        for row in db.execute("SELECT body FROM records WHERE key LIKE 'incident:%'"):
            other = json.loads(row[0])
            if other.get('action') and other['status']=='acting' and other['action']['decision']['vehicle_id']==v.id:
                raise HTTPException(409, '该机已有处置执行中')
        space = airspace(db)
        scene = scene_for(item, v)
        buildings = route_plan.scene_record(scene)['buildings']
        context_hash = safety_context(v, scene, buildings, space)
        if decision.plan_id:
            plan = b.get(db, 'safety-plan:' + decision.plan_id)
            if not plan or plan['incident_id'] != ident or plan['vehicle_id'] != v.id or plan['command'] != decision.command or plan['context_hash'] != context_hash or plan['expires_at'] < time.time():
                raise HTTPException(409, '安全检查已过期或飞机/空域已变化，请重新检查')
            chosen = next((c for c in plan['choices'] if c['id']==decision.route_id), None)
            if not chosen:
                raise HTTPException(409, '请选择已通过检查的处置路线')
        else:
            report = disposition.propose(v, decision.command, buildings, space, sim.FENCE)
            if not report['direct']['passed']:
                raise HTTPException(409, dict(message='原处置航线不安全，请先检查并选择替代方案', direct=report['direct']))
            chosen = report['choices'][0]
        checked = disposition.check(chosen['route'], disposition.obstacles(buildings, space['zones']), sim.FENCE, v.speed, v.battery)
        if not checked['passed']:
            raise HTTPException(409, '处置航线复核失败，请重新检查')
        if (decision.command == 'rtl' and v.mode in {'RTL', 'LAND'}) or (decision.command == 'land' and v.mode not in {'LOITER','AUTO','RTL','GUIDED'}):
            raise HTTPException(409, '当前飞行模式不能执行该处置')
        before = copy.deepcopy(v.__dict__)
        try:
            sim.command(v, decision.command)
            v.disposition_route = copy.deepcopy(chosen['route'][1:])
            v.disposition_wp = 0
            v.disposition_reason = decision.command
            v.disposition_incident = ident
            v.disposition_scene = scene
            v.dynamic_guard = dict(state='pending',history=[],generation=0)
            item.update(status='acting', action=dict(decision=decision.model_dump(), issued_at=time.time(),
                        safety=dict(scene=scene, airspace_revision=space['revision'], context_hash=context_hash, selected=chosen, check=checked, source='simulation'),
                        dynamic_enabled=True, task_id=original.get('business_task_id'), status='accepted', telemetry=sim.snapshot(v), service_synced=False))
            b.put(db, 'incident:' + ident, item)
            db.commit()
        except BaseException:
            v.__dict__.clear();v.__dict__.update(before)
            raise
        return item


@router.post('/{ident}/sync')
def sync(ident: str):
    with sim.LOCK, b.connect() as db:
        item = read(db, ident)
        action = item['action']
        if not action:
            raise HTTPException(409, '尚未选择处置')
        if item['status']=='closed':
            return item
        v = b.vehicle(action['decision']['vehicle_id'])
        if item['boot'] != b.BOOT or getattr(v,'business_task_id',None) != action['task_id']:
            action['status']='uncertain'
        else:
            action['telemetry']=sim.snapshot(v)
            action['dynamic']=copy.deepcopy(getattr(v,'dynamic_guard',None))
            expected=action['decision']['command']
            action['status']='completed' if v.done and not v.armed and v.done_reason==expected else 'interrupted' if v.done else 'holding' if (getattr(v,'dynamic_guard',None) or {}).get('state')=='holding' else 'executing'
        if action.get('dynamic_enabled') and not v.done and getattr(v,'disposition_incident',None)!=ident:
            action['status']='interrupted'
        b.put(db, 'incident:' + ident, item)
    if action['status']=='completed':
        synced = not action['task_id']
        if action['task_id']:
            import os
            try:
                response=httpx.post(os.getenv('LOWAIR_BASE_URL',os.getenv('FLIGHT_BASE_URL','http://127.0.0.1:8772')+'/service').rstrip('/')+'/api/tasks/'+action['task_id']+'/sync',timeout=5,trust_env=False)
                response.raise_for_status()
                synced=response.json()['status']=='aborted'
            except (httpx.HTTPError,ValueError,KeyError):
                synced=False
        with b.connect() as db:
            item=read(db,ident)
            item['action']['service_synced']=synced
            if synced:
                item.update(status='closed',closed_at=time.time())
            b.put(db,'incident:'+ident,item)
    return item


class Zone(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=50)
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)
    w: float = Field(gt=0, le=2000)
    d: float = Field(gt=0, le=2000)
    z_min: float = Field(default=0, ge=0, le=300)
    z_max: float = Field(default=300, gt=0, le=300)

    @model_validator(mode='after')
    def dimensions(self):
        if self.x+self.w>2000 or self.y+self.d>2000 or self.z_min>=self.z_max:
            raise ValueError('禁飞区范围或高度无效')
        return self


class LandingSite(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=80)
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)


class Airspace(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    zones: list[Zone] = Field(max_length=40)
    landing_sites: list[LandingSite] = Field(max_length=10)

    @model_validator(mode='after')
    def unique_ids(self):
        for rows in [self.zones,self.landing_sites]:
            if len({r.id for r in rows})!=len(rows):raise ValueError('编号重复')
        return self


airspace_router = APIRouter(prefix='/api/business/airspace')


def airspace(db):
    value = b.get(db, 'airspace') or dict(disposition.default_airspace(), anchor=b.station.config())
    if value['anchor'] != b.station.config():
        raise HTTPException(409, '空域配置锚点与地图不一致，请在未解锁状态重新保存空域')
    return value


@airspace_router.get('')
def get_airspace():
    with b.connect() as db:
        return b.get(db,'airspace') or dict(disposition.default_airspace(),anchor=b.station.config())


@airspace_router.put('')
def set_airspace(body: Airspace):
    with sim.LOCK, b.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=b.get(db,'airspace') or disposition.default_airspace()
        if body.revision!=old['revision']:raise HTTPException(409,'空域版本已变化，请重新读取')
        if any(v.armed for v in sim.VEHICLES.values()) and old.get('anchor',b.station.config()) != b.station.config():
            raise HTTPException(409,'飞行中不能更换空域坐标锚点')
        value={**body.model_dump(),'revision':body.revision+1,'anchor':b.station.config(),'source':'simulation'}
        b.put(db,'airspace',value)
        db.commit()
        from . import dynamic
        dynamic.prepare()
        return value


def scene_for(item,v):
    task=next((t for t in item['evidence']['tasks'] if t['id']==getattr(v,'business_task_id',None)),None)
    return task.get('scene','urban') if task else 'urban'


def safety_context(v,scene,buildings,space):
    return b.digest(dict(boot=b.BOOT,position=v.position,home=v.home,mode=v.mode,armed=v.armed,
                         battery=v.battery,speed=v.speed,mission=v.mission,task=getattr(v,'business_task_id',None),
                         scene=scene,buildings=buildings,airspace=space,fence=sim.FENCE))


class Preview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    vehicle_id: str
    command: Literal['rtl','land']


@router.post('/{ident}/preview')
def preview(ident: str, body: Preview):
    with sim.LOCK,b.connect() as db:
        item=read(db,ident)
        if item['action'] or item['boot']!=b.BOOT:
            raise HTTPException(409,'已处置或过期事件不能重新规划')
        if body.vehicle_id not in {v['id'] for v in item['evidence']['vehicles']}:
            raise HTTPException(409,'只能检查预警关联的合作飞机')
        v=b.vehicle(body.vehicle_id)
        space=airspace(db);scene=scene_for(item,v);buildings=route_plan.scene_record(scene)['buildings']
        report=disposition.propose(v,body.command,buildings,space,sim.FENCE)
        report.update(id=uuid.uuid4().hex,incident_id=ident,vehicle_id=v.id,command=body.command,
                      scene=scene,airspace=space,buildings=buildings,anchor=b.station.config(),
                      context_hash=safety_context(v,scene,buildings,space),expires_at=time.time()+120)
        b.put(db,'safety-plan:'+report['id'],report)
        return report
