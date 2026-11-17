"""Service workflow invariants; all callers hold a store transaction."""
from datetime import datetime, timezone
import hashlib
import json
from fastapi import HTTPException

TERMINAL = {'completed', 'aborted', 'cancelled'}


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(state, prefix):
    state['seq'] += 1
    return f'{prefix}-{state["seq"]:06d}'


def get(state, table, ident):
    if ident not in state[table]:
        raise HTTPException(404, '记录不存在')
    return state[table][ident]


def event(state, kind, detail, task_id=None):
    item = dict(id=identifier(state, 'EV'), kind=kind, detail=detail, task_id=task_id, time=now(), status='open', source='service-simulation')
    state['events'].append(item)
    return item


def binding(state, vehicle_id, pilot_id):
    vehicle = get(state, 'resources', vehicle_id)
    if vehicle['kind'] != 'vehicle' or vehicle.get('pilot_id') != pilot_id:
        raise HTTPException(422, '飞机与飞手绑定不一致')
    ids = [vehicle_id] + [vehicle.get(k) for k in ('airport_id', 'payload_id', 'pilot_id')]
    for ident, kind in zip(ids, ('vehicle', 'airport', 'payload', 'pilot')):
        resource = get(state, 'resources', ident)
        if resource['kind'] != kind or not resource['enabled']:
            raise HTTPException(409, '资源停用或绑定类型不正确')
    return ids


def reservations(state):
    held = {}
    for application in state['applications'].values():
        if application['status'] != 'approved':
            continue
        task = next((t for t in state['tasks'].values() if t['application_id'] == application['id']), None)
        if task and task['status'] in TERMINAL:
            continue
        for resource in application['resource_ids']:
            held[resource] = dict(application_id=application['id'], task_id=task['id'] if task else None,
                                  status='occupied' if task and task['status'] in {'flying', 'dispatching', 'uncertain'} else 'reserved')
    return held


def require_free(state, ids):
    held = reservations(state)
    if any(i in held for i in ids):
        raise HTTPException(409, '飞机、机场、挂载或飞手已被其他申请预留/占用')


def fingerprint(task):
    return hashlib.sha256(json.dumps({k: task.get(k) for k in ('vehicle_id', 'scene', 'route', 'resource_snapshot', 'algorithm_run_id')}, sort_keys=True).encode()).hexdigest()


def summary(state):
    return {'applications': len(state['applications']), 'tasks': len(state['tasks']),
            **{status: sum(t['status'] == status for t in state['tasks'].values()) for status in ('scheduled', 'flying', 'completed', 'aborted', 'uncertain')},
            **{status: sum(a['status'] == status for a in state['applications'].values()) for status in ('submitted', 'approved', 'rejected')}}
