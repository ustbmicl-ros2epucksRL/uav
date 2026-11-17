"""Persistent service workflow and simulation supervision integration."""
import os
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
import httpx
from . import store, domain as d

ROOT = Path(__file__).resolve().parent
app = FastAPI(title='低空区域飞行管理系统 · 服务管理')


def flight_request(method, path, body=None):
    try:
        response = httpx.request(method, os.getenv('FLIGHT_BASE_URL', 'http://127.0.0.1:8772').rstrip('/') + path, json=body, timeout=15, trust_env=False)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text or '地面站返回 HTTP ' + str(exc.response.status_code)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, '地面站不可达或响应无效；请查询回执后重试') from exc


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)


class Point(Model):
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)
    z: float = Field(ge=0, le=300)


class ApplicationIn(Model):
    applicant: str = Field(min_length=1, max_length=40)
    pilot_id: str
    vehicle_id: str
    scene: Literal['urban', 'corridor', 'electromagnetic']
    purpose: str = Field(min_length=1, max_length=80)
    route: list[Point] = Field(min_length=2, max_length=20)
    algorithm_run_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class ReviewIn(Model):
    decision: Literal['approve', 'reject']
    reviewer: str = Field(min_length=1, max_length=40)
    note: str = Field(default='', max_length=200)


class ResourceIn(Model):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    kind: Literal['airport', 'vehicle', 'payload', 'pilot']
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    airport_id: str | None = None
    pilot_id: str | None = None
    payload_id: str | None = None
    revision: int = Field(default=0, ge=0)


class Acknowledge(Model):
    actor: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=1, max_length=300)


@app.get('/health')
def health():
    return {'service': 'lowair-service', 'mode': 'simulation', 'storage': 'sqlite', 'os': 'Ubuntu' if Path('/etc/os-release').exists() else 'local'}


@app.get('/')
@app.get('/safety')
@app.get('/validation')
def index():
    return FileResponse(ROOT / 'static/index.html', headers={'Cache-Control': 'no-store'})


def reset_state():
    with store.transaction() as state:
        state.clear()
        state.update(store.seed())


@app.post('/api/reset')
def reset():
    if os.getenv('LOWAIR_ALLOW_RESET') != '1':
        raise HTTPException(403, '仅隔离测试实例允许重置')
    reset_state()
    return {'ok': True}


@app.get('/api/resources')
@app.get('/api/devices')
def resources():
    with store.transaction(False) as state:
        held = d.reservations(state)
        return {'resources': [dict(r, allocation=held.get(r['id'])) for r in state['resources'].values()]}


@app.put('/api/resources/{resource_id}')
def save_resource(resource_id: str, body: ResourceIn):
    if resource_id != body.id:
        raise HTTPException(422, '编号不一致')
    with store.transaction() as state:
        old = state['resources'].get(resource_id)
        if old and (old['revision'] != body.revision or old['kind'] != body.kind):
            raise HTTPException(409, '资源版本已更新或类型不可变')
        if resource_id in d.reservations(state):
            raise HTTPException(409, '已预留或占用的资源不可修改')
        record = body.model_dump()
        record['revision'] = (old['revision'] if old else 0) + 1
        state['resources'][resource_id] = record
        if body.kind == 'vehicle':
            d.binding(state, resource_id, body.pilot_id)
            for vehicle in state['resources'].values():
                if vehicle['kind'] == 'vehicle' and vehicle['id'] != resource_id:
                    if any(vehicle.get(k) == record[k] for k in ('pilot_id', 'payload_id')):
                        raise HTTPException(409, '飞手或挂载已绑定其他飞机')
        d.event(state, 'resource_updated', f'{resource_id} 资源版本 {record["revision"]}')
        return record


@app.get('/api/applications')
def applications():
    with store.transaction(False) as state:
        return {'applications': list(state['applications'].values())}


@app.post('/api/applications')
def create_application(body: ApplicationIn):
    algorithm = flight_request('GET', '/api/algorithms/runs/'+body.algorithm_run_id+'/mission') if body.algorithm_run_id else None
    if algorithm and (algorithm['route'] != [p.model_dump() for p in body.route] or algorithm['scene'] != body.scene):
        raise HTTPException(409, '申请航线与算法完整航线不一致')
    with store.transaction() as state:
        ids = d.binding(state, body.vehicle_id, body.pilot_id)
        d.require_free(state, ids)
        item = dict(body.model_dump(), id=d.identifier(state, 'APP'), status='submitted', created_at=d.now(), resource_ids=ids,
                    resource_snapshot={i: state['resources'][i] for i in ids})
        if algorithm:item.update(algorithm_run_id=body.algorithm_run_id, area_id=algorithm['area_id'], algorithm_input_sha256=algorithm['input_sha256'])
        state['applications'][item['id']] = item
        d.event(state, 'submitted', item['id'] + ' 已提交')
        return item


@app.get('/api/applications/{ident}')
def get_application(ident: str):
    with store.transaction(False) as state:
        return d.get(state, 'applications', ident)


@app.post('/api/applications/{ident}/review')
def review(ident: str, body: ReviewIn):
    with store.transaction() as state:
        item = d.get(state, 'applications', ident)
        if item['status'] != 'submitted':
            raise HTTPException(409, '申请已审批')
        if body.decision == 'approve':
            ids = d.binding(state, item['vehicle_id'], item['pilot_id'])
            d.require_free(state, ids)
            if {i: state['resources'][i] for i in ids} != item['resource_snapshot']:
                raise HTTPException(409, '资源版本已改变，请撤销后重新申请')
        item.update(status='approved' if body.decision == 'approve' else 'rejected', reviewer=body.reviewer, note=body.note, reviewed_at=d.now())
        d.event(state, 'reviewed', f'{ident}: {item["status"]}')
        return item


@app.post('/api/applications/{ident}/cancel')
def cancel_application(ident: str):
    with store.transaction() as state:
        item = d.get(state, 'applications', ident)
        if any(t['application_id'] == ident for t in state['tasks'].values()):
            raise HTTPException(409, '已有任务，请在任务中取消')
        if item['status'] not in {'submitted', 'approved'}:
            raise HTTPException(409, '当前申请不可撤销')
        item['status'] = 'cancelled'
        d.event(state, 'cancelled', ident + ' 已撤销')
        return item


@app.post('/api/tasks/from-application/{ident}')
def schedule(ident: str):
    with store.transaction() as state:
        item = d.get(state, 'applications', ident)
        if item['status'] != 'approved':
            raise HTTPException(409, '只有已批准申请可以调度')
        if any(t['application_id'] == ident for t in state['tasks'].values()):
            raise HTTPException(409, '该申请已生成任务')
        task = {k: item[k] for k in ('vehicle_id', 'pilot_id', 'scene', 'purpose', 'route', 'resource_snapshot')}
        task.update({k:item[k] for k in ('algorithm_run_id','area_id','algorithm_input_sha256') if k in item})
        task.update(id=d.identifier(state, 'TASK'), application_id=ident, status='scheduled', validation_id=None, telemetry=None, created_at=d.now())
        state['tasks'][task['id']] = task
        d.event(state, 'scheduled', '已生成待验证任务', task['id'])
        return task


@app.get('/api/tasks')
def tasks():
    with store.transaction(False) as state:
        return {'tasks': [dict(t, validation={k:v for k,v in state['validations'].get(t.get('validation_id'), {}).get('result', {}).items() if k != 'token'}, validation_anchor=state['validations'].get(t.get('validation_id'), {}).get('result', {}).get('anchor'), validation_passed=state['validations'].get(t.get('validation_id'), {}).get('result', {}).get('passed')) for t in state['tasks'].values()]}


@app.get('/api/tasks/{ident}')
def get_task(ident: str):
    with store.transaction(False) as state:
        return d.get(state, 'tasks', ident)


@app.post('/api/tasks/{ident}/validate')
def validate_task(ident: str):
    with store.transaction() as state:
        task = d.get(state, 'tasks', ident)
        if task['status'] != 'scheduled':
            raise HTTPException(409, '只有待下发任务可验证')
        result = flight_request('POST', f'/api/business/validate/{task["vehicle_id"]}', {'waypoints': task['route'], 'scene': task['scene']})
        record = dict(id=d.identifier(state, 'VAL'), task_id=ident, created_at=d.now(), fingerprint=d.fingerprint(task), result=result, source='simulation')
        state['validations'][record['id']] = record
        task['validation_id'] = record['id'];task['validation_source']='simulation'
        d.event(state, 'validated', '仿真航线验证：' + ('通过' if result['passed'] else '未通过'), ident)
        return record


@app.post('/api/tasks/{ident}/dispatch')
def dispatch(ident: str):
    # Persist intent before the remote call. The flight endpoint uses task ID as its idempotency key.
    with store.transaction() as state:
        task = d.get(state, 'tasks', ident)
        if task['status'] not in {'scheduled', 'uncertain', 'dispatching'}:
            raise HTTPException(409, '任务不在可下发/待核对状态')
        if not task['validation_id']:
            raise HTTPException(409, '请先验证航线')
        validation = d.get(state, 'validations', task['validation_id'])
        if validation.get('source')=='ardupilot-sitl':raise HTTPException(409,'该验证面向 SITL，请使用准备 SITL 执行')
        if not validation['result']['passed'] or validation['fingerprint'] != d.fingerprint(task):
            raise HTTPException(409, '航线验证未通过或已失效')
        task['status'] = 'dispatching'
        payload = dict(task_id=ident, validation_id=validation['id'], token=validation['result']['token'], waypoints=task['route'], scene=task['scene'])
        vehicle = task['vehicle_id']
    try:
        receipt = flight_request('POST', f'/api/business/dispatch/{vehicle}', payload)
    except HTTPException as error:
        with store.transaction() as state:
            task = d.get(state, 'tasks', ident)
            task['status'] = 'scheduled' if error.status_code in {400, 404, 409, 422} else 'uncertain'
            task['last_error'] = str(error.detail)
            d.event(state, 'dispatch_rejected' if task['status'] == 'scheduled' else 'dispatch_uncertain',
                    '地面站拒绝下发，请重新验证' if task['status'] == 'scheduled' else '下发未确认，资源继续占用；查询回执或幂等重试', ident)
        raise
    with store.transaction() as state:
        task = d.get(state, 'tasks', ident)
        task.update(status=receipt.get('status', 'uncertain'), receipt=receipt, execution_source='simulation', dispatched_at=d.now())
        d.event(state, 'dispatched', '地面站已接收并执行仿真任务', ident)
        return task


@app.post('/api/tasks/{ident}/sync')
def sync(ident: str):
    with store.transaction() as state:
        task = d.get(state, 'tasks', ident)
        if task.get('execution_source')=='ardupilot-sitl':
            receipt=flight_request('GET',f'/api/sitl/business/tasks/{ident}')
            if receipt['task_id']!=ident:raise HTTPException(409,'执行任务不匹配')
            task.update(execution=receipt,status=receipt['status'],telemetry=receipt.get('telemetry'),synced_at=d.now())
            return task
        if task['status'] in d.TERMINAL:
            return task
        if task['status'] not in {'flying', 'uncertain', 'dispatching'}:
            raise HTTPException(409, '任务尚未下发')
        receipt = flight_request('GET', f'/api/business/tasks/{ident}')
        if receipt['task_id'] != ident or receipt['vehicle_id'] != task['vehicle_id']:
            raise HTTPException(409, '回执与任务不一致')
        telemetry = receipt.get('telemetry')
        if not telemetry:
            task['status'] = 'uncertain'
        else:
            task['telemetry'] = telemetry
            task['status'] = 'completed' if telemetry.get('done') and telemetry.get('done_reason') == 'mission' else 'aborted' if telemetry.get('done') else 'flying'
            for event in telemetry.get('events', []):
                key = f'{ident}:{event["kind"]}:{event.get("second", "")}'
                if not any(e.get('dedup') == key for e in state['events']):
                    d.event(state, event['kind'], event.get('detail', event['kind']), ident)['dedup'] = key
            if task['status'] in d.TERMINAL:
                d.event(state, 'finished', task['status'] + '，资源已释放', ident)
        task['synced_at'] = d.now()
        return task


@app.post('/api/tasks/{ident}/cancel')
def cancel_task(ident: str):
    with store.transaction() as state:
        task = d.get(state, 'tasks', ident)
        if task.get('execution_source')=='ardupilot-sitl' and task['status'] not in d.TERMINAL:
            receipt=flight_request('POST',f'/api/sitl/business/tasks/{ident}/cancel',{})
            task.update(status='cancelled',execution=receipt);return task
        if task['status'] != 'scheduled':
            raise HTTPException(409, '仅未下发任务可取消；执行中请在地面站处置')
        task['status'] = 'cancelled'
        d.event(state, 'cancelled', '任务取消，资源释放', ident)
        return task


@app.get('/api/validations')
def validations():
    with store.transaction(False) as state:
        return {'validations': list(state['validations'].values())}


@app.get('/api/events')
def events():
    with store.transaction(False) as state:
        return {'events': state['events']}


@app.post('/api/events/{ident}/ack')
def acknowledge(ident: str, body: Acknowledge):
    with store.transaction() as state:
        item = next((e for e in state['events'] if e['id'] == ident), None)
        if not item:
            raise HTTPException(404, '事件不存在')
        if item['status'] != 'open':
            raise HTTPException(409, '事件已确认')
        item.update(status='acknowledged', actor=body.actor, note=body.note, acknowledged_at=d.now())
        return item


@app.get('/api/performance')
def performance():
    with store.transaction(False) as state:
        return d.summary(state)


@app.get('/api/safety')
def safety():
    # Planning output is observation-derived; no simulated truth is injected into tracking.
    base = os.getenv('PLANNING_BASE_URL', os.getenv('FLIGHT_BASE_URL', 'http://127.0.0.1:8772') + '/network').rstrip('/')
    try:
        response = httpx.get(base + '/api/scenes/urban/run', timeout=15)
        response.raise_for_status()
        sensing = response.json()
    except (httpx.HTTPError, ValueError):
        sensing = None
    with store.transaction(False) as state:
        return {'source': 'simulation', 'sensing_available': sensing is not None, 'sensing': sensing,
                'cooperative_tasks': [{k: t[k] for k in ('id', 'vehicle_id', 'status', 'scene')} for t in state['tasks'].values() if t['status'] not in d.TERMINAL],
                'events': state['events'], 'identity_rule': '无合作身份报文仅表示身份未确认，不自动认定黑飞',
                'capabilities': {'real_sensor_ingestion': False, 'automatic_interception': False}}


@app.get('/api/share/{ident}')
def share(ident: str):
    task = get_task(ident)
    return {k: task[k] for k in ('id', 'scene', 'purpose', 'status', 'vehicle_id')}


@app.get('/api/portal/{role}')
def portal(role: str, pilot_id: str | None = None):
    with store.transaction(False) as state:
        if role == 'city':
            return {'tasks': list(state['tasks'].values()), 'performance': d.summary(state)}
        if role == 'portal':
            return {'windows': [{k: a[k] for k in ('id', 'scene', 'purpose', 'status')} for a in state['applications'].values() if a['status'] == 'approved']}
        if role == 'mobile' and pilot_id:
            return {'tasks': [t for t in state['tasks'].values() if t['pilot_id'] == pilot_id]}
        raise HTTPException(422, '选择 city、portal 或 mobile（需要 pilot_id）；这些视图不是鉴权机制')


@app.get('/api/modules')
def modules():
    return {'business': ['飞行申请审批', '飞行任务调度', '资源管理'], 'operations': ['飞行控制', '仿真策略验证', '安全监管事件'], 'mode': 'simulation'}


@app.get('/api/live/{vehicle_id}')
def live(vehicle_id: str):
    return {'status': 'reserved', 'reason': '未接入机载视频，不返回识别结果'}


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.post('/api/safety/refresh')
def refresh_safety():
    result = flight_request('GET', '/api/business/safety')
    with store.transaction() as state:
        alerts = [('conflict:' + ':'.join(c['pair']), 'predicted_conflict', f'{c["pair"]} 预计 {c["seconds_to_closest"]} 秒后间隔 {c["separation_m"]} m') for c in result['conflicts']]
        track = result.get('track', {})
        if track.get('estimate') and track.get('identity') != '合作':
            alerts.append(('identity:unconfirmed-target', 'identity_unconfirmed', '观测目标身份未确认；请核验有效飞行计划'))
        active = {key for key,_,_ in alerts}
        for item in state['events']:
            if item.get('alert_key') and item.get('active') and item['alert_key'] not in active:
                item['active'] = False
                item['cleared_at'] = d.now()
        for key, kind, detail in alerts:
            if not any(e.get('alert_key') == key and e.get('active') for e in state['events']):
                item = d.event(state, kind, detail)
                item.update(alert_key=key, active=True, evidence=result)
        state['safety_snapshot'] = dict(result, received_at=d.now())
    return result


@app.get('/api/planning/scenes')
def planning_scenes():
    base = os.getenv('PLANNING_BASE_URL', os.getenv('FLIGHT_BASE_URL', 'http://127.0.0.1:8772') + '/network').rstrip('/')
    try:
        response = httpx.get(base + '/api/scenes', timeout=5, trust_env=False)
        response.raise_for_status()
        return dict(available=True, scenes=response.json()['scenes'])
    except (httpx.HTTPError, ValueError, KeyError):
        return dict(available=False, scenes=[])


def sitl_mission(task):
    return dict(task_id=task['id'], vehicle_id=task['vehicle_id'], purpose=task['purpose'], scene=task['scene'], route=task['route'], algorithm_run_id=task.get('algorithm_run_id'))

@app.post('/api/tasks/{ident}/validate-sitl')
def validate_sitl(ident: str):
    with store.transaction() as state:
        task=d.get(state,'tasks',ident)
        if task['status']!='scheduled':raise HTTPException(409,'只有待执行任务可验证')
        result=flight_request('POST','/api/sitl/business/validate',sitl_mission(task))
        record=dict(id=d.identifier(state,'VAL'),task_id=ident,created_at=d.now(),fingerprint=d.fingerprint(task),result=result,source='ardupilot-sitl')
        state['validations'][record['id']]=record;task['validation_id']=record['id'];task['validation_source']='ardupilot-sitl'
        d.event(state,'validated-sitl','SITL 批准航线检查：'+('通过' if result['passed'] else '未通过'),ident)
        return record

@app.post('/api/tasks/{ident}/prepare-sitl')
def prepare_sitl(ident: str):
    with store.transaction() as state:
        task=d.get(state,'tasks',ident)
        if task['status'] not in ('scheduled','uncertain','dispatching','prepared'):
            raise HTTPException(409,'任务不处于可准备状态')
        if not task.get('validation_id'):raise HTTPException(409,'请先验证 SITL 航线')
        validation=d.get(state,'validations',task['validation_id'])
        if validation['source']!='ardupilot-sitl' or not validation['result']['passed'] or validation['fingerprint']!=d.fingerprint(task):
            raise HTTPException(409,'请先通过当前任务的 SITL 验证')
        payload=dict(**sitl_mission(task),token=validation['result']['token'],validation_id=validation['id'])
        task.update(status='dispatching',execution_source='ardupilot-sitl')
    try:
        receipt=flight_request('POST','/api/sitl/business/prepare',payload)
    except HTTPException:
        try:flight_request('GET',f'/api/sitl/business/tasks/{ident}');uncertain=True
        except HTTPException as err:uncertain=err.status_code!=404
        with store.transaction() as state:
            d.get(state,'tasks',ident)['status']='uncertain' if uncertain else 'scheduled'
        raise
    with store.transaction() as state:
        task=d.get(state,'tasks',ident);task.update(status=receipt['status'],execution=receipt,prepared_at=d.now())
        d.event(state,'sitl-prepared','已关联 SITL 执行；上传不解锁、不起飞',ident)
        return task

@app.get('/api/tasks/{ident}/execution')
def task_execution(ident: str):
    with store.transaction(False) as state:
        task=d.get(state,'tasks',ident)
        if task.get('execution_source')!='ardupilot-sitl':raise HTTPException(409,'当前任务没有 SITL 执行关联')
    return flight_request('GET',f'/api/sitl/business/tasks/{ident}')
