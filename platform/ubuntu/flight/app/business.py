"""Idempotent business execution, restricted to the local simulator."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import sim, station, route_plan

router = APIRouter(prefix='/api/business')
BOOT = str(uuid.uuid4())


class Point(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)
    z: float = Field(ge=0, le=300)


class Check(BaseModel):
    model_config = ConfigDict(extra='forbid')
    waypoints: list[Point] = Field(min_length=2, max_length=20)
    scene: Literal['urban', 'corridor', 'electromagnetic']


class Dispatch(Check):
    task_id: str = Field(pattern=r'^TASK-[0-9]+$')
    validation_id: str = Field(min_length=1, max_length=60)
    token: str


@contextmanager
def connect():
    path = Path(os.getenv('FLIGHT_DATA_DIR', str(Path(__file__).resolve().parents[1] / 'data')))
    path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / 'business.sqlite3', timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
    try:
        with db:
            yield db
    finally:
        db.close()


def get(db, key):
    row = db.execute('SELECT body FROM records WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(db, key, value):
    db.execute('INSERT OR REPLACE INTO records VALUES (?,?)', (key, json.dumps(value, allow_nan=False)))


def vehicle(ident):
    v = sim.get(ident)
    if not v:
        raise HTTPException(404, '飞机不存在')
    return v


def context(v, body):
    return dict(vehicle_id=v.id, position=v.position, battery=v.battery, mode=v.mode, armed=v.armed,
                route=[p.model_dump() for p in body.waypoints], scene=body.scene,
                buildings=route_plan.scene_record(body.scene)['buildings'], anchor=station.config(), fence=sim.FENCE)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def refresh(db, receipt):
    if receipt['status'] in {'completed', 'aborted'}:
        return receipt
    v = sim.get(receipt['vehicle_id'])
    if receipt['boot'] != BOOT or not v or getattr(v, 'business_task_id', None) != receipt['task_id']:
        receipt.update(status='uncertain', telemetry=None)
    else:
        receipt['telemetry'] = sim.snapshot(v)
        receipt['status'] = 'completed' if v.done and v.done_reason == 'mission' else 'aborted' if v.done else 'flying'
    put(db, 'task:' + receipt['task_id'], receipt)
    return receipt


def require_unowned(v):
    task_id = getattr(v, 'business_task_id', None)
    if task_id and not v.done:
        raise HTTPException(409, '业务任务执行中，不能覆盖航线；可返航、降落或停止后同步任务')
    # Preserve terminal receipt before allowing a new manual mission to reset done.
    if task_id:
        with connect() as db:
            receipt = get(db, 'task:' + task_id)
            if receipt:
                refresh(db, receipt)
        v.business_task_id = None


@router.post('/validate/{vehicle_id}')
def validate(vehicle_id: str, body: Check):
    with sim.LOCK:
        v = vehicle(vehicle_id)
        ctx = context(v, body)
        points = [[p.x, p.y, p.z] for p in body.waypoints]
        preview = station.preview(points, v.position, v.speed, sim.FENCE)
        hits = route_plan.crossings([v.position] + points, ctx['buildings'])
        reasons = []
        if v.armed or v.mode != 'LOITER':
            reasons.append('需要定点且未解锁')
        if v.battery <= 20:
            reasons.append('电量不足')
        if not preview['inside_fence']:
            reasons.append('超出围栏')
        if hits:
            reasons.append('航段穿过合成建筑安全间隔')
        if points[-1][2] != 0:
            reasons.append('末航点需落地以便任务结束')
        result = dict(passed=not reasons, reasons=reasons, preview=preview, building_hits=hits,
                      source='simulation', model='route-segment-clearance-v1', model_hash=digest(ctx['buildings']),
                      anchor=ctx['anchor'], context_hash=digest(ctx), token=str(uuid.uuid4()), expires_at=time.time()+600,
                      note='围栏、直线航段、合成障碍基线；非实测许可、非完整动力学或多机冲突认证。')
        with connect() as db:
            put(db, 'validation:' + result['token'], result)
        return result


@router.post('/dispatch/{vehicle_id}')
def dispatch(vehicle_id: str, body: Dispatch):
    with sim.LOCK, connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = get(db, 'task:' + body.task_id)
        request_hash = digest(dict(vehicle_id=vehicle_id, **body.model_dump()))
        if existing:
            if existing['request_hash'] != request_hash:
                raise HTTPException(409, '任务编号已用于不同航线或验证版本')
            return refresh(db, existing)
        v = vehicle(vehicle_id)
        for row in db.execute("SELECT body FROM records WHERE key LIKE 'task:%'").fetchall():
            previous = json.loads(row[0])
            if previous['vehicle_id'] == vehicle_id and previous['status'] not in {'completed', 'aborted'}:
                previous = refresh(db, previous)
                if previous['status'] not in {'completed', 'aborted'}:
                    raise HTTPException(409, '该机已有未结束或待核对业务任务')
        validation = get(db, 'validation:' + body.token)
        if not validation or not validation['passed'] or validation['expires_at'] < time.time() or validation['context_hash'] != digest(context(v, body)):
            raise HTTPException(409, '验证过期或飞机/地图/场景已变化，请重新验证')
        before = copy.deepcopy(v.__dict__)
        try:
            sim.load_mission(v, [[p.x, p.y, p.z] for p in body.waypoints])
            sim.command(v, 'arm')
            sim.command(v, 'start')
            v.business_task_id = body.task_id
            v.business_validation_id = body.validation_id
            v.business_scene = body.scene
            receipt = dict(task_id=body.task_id, validation_id=body.validation_id, vehicle_id=vehicle_id, request_hash=request_hash,
                           boot=BOOT, status='flying', telemetry=sim.snapshot(v), source='simulation')
            put(db, 'task:' + body.task_id, receipt)
            db.commit()
        except BaseException:
            v.__dict__.clear()
            v.__dict__.update(before)
            raise
        station.ack(vehicle_id, 'business', f'业务任务 {body.task_id} / {body.validation_id} 已开始仿真执行')
        return receipt


@router.get('/tasks/{task_id}')
def receipt(task_id: str):
    with sim.LOCK, connect() as db:
        item = get(db, 'task:' + task_id)
        if not item:
            raise HTTPException(404, '未找到执行回执；不要据此释放不确定任务资源')
        return refresh(db, item)


@router.get('/safety')
def safety_snapshot():
    from . import follow, safety
    with sim.LOCK:
        vehicles = [sim.snapshot(v) for v in sim.VEHICLES.values()]
        track = follow.public()
        return dict(source='simulation', vehicles=vehicles, track=track, **safety.assess(vehicles, track))


@router.get('/situation')
def situation():
    """Read-only join: service state is distinct from current execution evidence."""
    import httpx
    from datetime import datetime, timezone
    snapshot = safety_snapshot()
    snapshot.update(observed_at=datetime.now(timezone.utc).isoformat(), anchor=station.config(), tasks=[], service_available=False)
    try:
        base = os.getenv('LOWAIR_BASE_URL', os.getenv('FLIGHT_BASE_URL', 'http://127.0.0.1:8772') + '/service').rstrip('/')
        response = httpx.get(base + '/api/tasks', timeout=2, trust_env=False)
        response.raise_for_status()
        tasks = response.json()['tasks']
    except (httpx.HTTPError, ValueError, KeyError):
        from . import incidents
        snapshot['incidents'] = incidents.capture(snapshot)
        snapshot['airspace'] = incidents.get_airspace()
        snapshot['service_error'] = '业务服务不可达；仅显示地面站遥测，任务状态未知'
        return snapshot
    with sim.LOCK, connect() as db:
        for task in tasks:
            receipt = get(db, 'task:' + task['id'])
            source=task.get('execution_source','simulation')
            if source=='ardupilot-sitl':
                from . import sitl_business
                try:receipt=sitl_business.execution(task['id'])
                except HTTPException:receipt={'status':'uncertain'}
            elif receipt:
                receipt = refresh(db, receipt)
            # Never relocate a validated route when the map anchor changes.
            validation_anchor = task.get('validation_anchor')
            snapshot['tasks'].append(dict(id=task['id'], vehicle_id=task['vehicle_id'], purpose=task['purpose'],
                scene=task['scene'], service_status=task['status'], execution_status=receipt['status'] if receipt else None,
                validation_id=task.get('validation_id'), validation_passed=task.get('validation_passed'), route=task['route'], anchor=validation_anchor,
                validation=task.get('validation'), source=source))
    snapshot['service_available'] = True
    from . import incidents
    snapshot['incidents'] = incidents.capture(snapshot)
    snapshot['airspace'] = incidents.get_airspace()
    return snapshot
