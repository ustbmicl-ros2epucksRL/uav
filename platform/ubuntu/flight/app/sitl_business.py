"""Business-task binding to the managed SITL; upload is not takeoff."""
import copy
import json
import math
import threading
import time
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from . import sitl, business, flight_records, disposition, incidents, route_plan, sim

router = APIRouter(prefix='/api/sitl/business')
EXEC_LOCK = threading.Lock()

class Mission(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    task_id: str = Field(pattern=r'^TASK-[0-9]+$')
    vehicle_id: str = Field(min_length=1, max_length=80)
    purpose: str = Field(default='', max_length=80)
    scene: Literal['urban','corridor','electromagnetic']
    route: list[business.Point] = Field(min_length=3, max_length=20)
    algorithm_run_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')

class Prepare(Mission):
    token: str
    validation_id: str


def database():
    return flight_records.connect()


def table(db):
    db.execute('CREATE TABLE IF NOT EXISTS sitl_executions (task_id TEXT PRIMARY KEY, body TEXT NOT NULL)')


def put(item):
    with database() as db:
        table(db)
        db.execute('INSERT OR REPLACE INTO sitl_executions VALUES (?,?)', (item['task_id'], json.dumps(item, allow_nan=False)))


def saved(task_id):
    with database() as db:
        table(db)
        row = db.execute('SELECT body FROM sitl_executions WHERE task_id=?', (task_id,)).fetchone()
        return json.loads(row[0]) if row else None


def check(mission):
    status = sitl.status(); t = status['telemetry']; points = [[p.x,p.y,p.z] for p in mission.route]
    with business.connect() as db:
        space = incidents.airspace(db)
    reasons = []
    if mission.algorithm_run_id:
        from .algorithm_api import mission as planned_mission
        approved=planned_mission(mission.algorithm_run_id)
        if approved['route'] != [p.model_dump() for p in mission.route] or approved['scene'] != mission.scene or approved['anchor'] != status.get('anchor'):
            reasons.append('算法航线、场景或 SITL 原点不匹配')
    if not status['connected'] or t.get('armed'):
        reasons.append('SITL 必须在线且未解锁')
    if points[0] != [0,0,0] or points[1][:2] != [0,0] or not 40 <= points[1][2] <= 280 or points[-1][2] != 0 or any(p[2] <= 0 for p in points[1:-1]):
        reasons.append('SITL 航线须从本机原点垂直起飞（40–280m），中间航点在空中，末点落地；不能静默修改批准航线')
    anchor = status.get('anchor')
    fresh = all(isinstance(t.get(k),(int,float)) and time.time()-status.get('updated_at',{}).get(k,0)<3 for k in ['latitude','longitude','altitude','gps_fix','battery'])
    if not fresh or t.get('gps_fix',0)<3:
        reasons.append('SITL 定位或电池遥测缺失/过期')
    elif anchor and (abs(t['latitude']-anchor['latitude'])*111320 > 5 or abs(t['longitude']-anchor['longitude'])*111320*math.cos(math.radians(anchor['latitude'])) > 5 or abs(t['altitude'])>1):
        reasons.append('SITL 未在任务原点地面，不能直接装订本任务')
    checked = disposition.check(points, disposition.obstacles(route_plan.scene_record(mission.scene)['buildings'], space['zones']), sim.FENCE, 10, t.get('battery') or 0)
    if not checked['passed']:reasons.append('建筑、禁飞区、围栏或电量检查未通过')
    return dict(passed=not reasons, reasons=reasons, check=checked, anchor=anchor, airspace_revision=space['revision'], source='ardupilot-sitl', model='sitl-approved-route-v1')


@router.post('/validate')
def validate(body: Mission):
    result = check(body)
    result.update(token=uuid.uuid4().hex, expires_at=time.time()+600, request_hash=business.digest(body.model_dump()))
    with business.connect() as db:business.put(db,'sitl-validation:'+result['token'],result)
    return result


@router.get('/tasks/{task_id}')
def execution(task_id: str):
    item = saved(task_id)
    if not item:raise HTTPException(404,'该业务任务尚未准备 SITL 执行')
    with database() as db:
        rows = db.execute("SELECT id,status,body FROM flights WHERE json_extract(body,'$.execution_id')=? ORDER BY created DESC", (item['execution_id'],)).fetchall()
    item['records'] = [dict(id=r[0], status=r[1], replay_url='/?source=sitl&record='+r[0]+'&task='+task_id) for r in rows]
    if rows:item['record_id']=rows[0][0]
    live = sitl.status()
    binding = live.get('execution_binding',{})
    item['telemetry'] = None
    if binding.get('execution_id') == item['execution_id'] and live['connected']:
        phase=live.get('patrol',{}).get('status')
        item['phase']=phase;item['telemetry']=live['telemetry']
        item['status']='completed' if phase=='completed' else 'aborted' if phase=='interrupted' and not live['telemetry'].get('armed') and live['telemetry'].get('landed_state')==1 else 'flying' if live['telemetry'].get('armed') else 'prepared' if phase=='uploaded' else 'uncertain'
    elif rows:
        item['status'] = 'cancelled' if rows[0][1]=='cancelled' else 'completed' if rows[0][1]=='completed' else 'aborted' if rows[0][1] in ('stopped','superseded','cancelled') else 'uncertain'
    item['ground_url']='/?source=sitl&task='+task_id
    return item


@router.post('/prepare')
def prepare(body: Prepare):
    mission=Mission(**{k:v for k,v in body.model_dump().items() if k not in ('token','validation_id')})
    fingerprint=business.digest(mission.model_dump())
    with EXEC_LOCK:
        old=saved(body.task_id)
        if old:
            if old['request_hash']!=fingerprint:raise HTTPException(409,'同一业务任务不能改写已绑定航线')
            return execution(body.task_id)
        if sitl.STATE.get('demo_owner'):raise HTTPException(409,'SITL 演示正在运行')
        binding=sitl.STATE.get('execution_binding')
        if binding and execution(binding['task_id'])['status'] not in ('completed','aborted','cancelled'):
            raise HTTPException(409,'SITL 已有未结束业务执行')
        with database() as db:
            table(db)
            pending=[json.loads(r[0]) for r in db.execute('SELECT body FROM sitl_executions')]
        if any(execution(p['task_id'])['status'] in ('uncertain','prepared','flying','preparing') for p in pending):
            raise HTTPException(409,'SITL 有未完成或待核对执行，不能覆盖')
        with business.connect() as db:validated=business.get(db,'sitl-validation:'+body.token)
        current=check(mission)
        if not validated or not validated['passed'] or validated['expires_at']<time.time() or validated['request_hash']!=fingerprint or validated['anchor']!=current['anchor'] or validated['airspace_revision']!=current['airspace_revision'] or not current['passed']:
            raise HTTPException(409,'SITL 验证失效，请重新验证当前批准航线')
        item=dict(task_id=body.task_id, business_vehicle_id=body.vehicle_id, aircraft_id='SITL-1', execution_id=uuid.uuid4().hex, validation_id=body.validation_id, request_hash=fingerprint, status='preparing', created_at=time.time(), source='ardupilot-sitl')
        put(item)
        try:
            binding={**item,'purpose':body.purpose}
            if body.algorithm_run_id:
                from .algorithm_api import mission as planned_mission
                binding['algorithm']=planned_mission(body.algorithm_run_id)
                item['algorithm_run_id']=body.algorithm_run_id
                put(item)
            sitl.execute_upload(sitl.PatrolRequest(), business_mission=dict(route=[[p.x,p.y,p.z] for p in mission.route], scene=mission.scene, binding=binding))
        except Exception:
            item['status']='uncertain';put(item)
            raise
        return execution(body.task_id)


@router.post('/tasks/{task_id}/cancel')
def cancel(task_id: str):
    with EXEC_LOCK, sitl.WIRE, sitl.LOCK:
        item=execution(task_id)
        if item['status']=='cancelled':return item
        if sitl.STATE.get('execution_binding',{}).get('task_id')!=task_id or sitl.STATE['telemetry'].get('armed'):
            raise HTTPException(409,'仅可取消当前未解锁的 SITL 准备任务；飞行中请在地面站处置')
        sitl.STATE['patrol']['status']='cancelled';sitl.STATE['recording_complete']=True
        flight_records.checkpoint(sitl.STATE, force=True)
        sitl.STATE.pop('execution_binding',None)
        item['status']='cancelled';put(item)
        return item
