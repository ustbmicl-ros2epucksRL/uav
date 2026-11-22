"""研究必要性数字孪生实验的档案接口。只写本地实验记录，不改场景、不下发设备或飞机指令。"""
import re
import threading
import uuid

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from . import store, twin_experiment as twin, twin_segment
from .scene_service import scene_for

router = APIRouter()
LOCK = threading.Lock()
INDEX = 'twin-experiment-index'


def load(experiment_id):
    if not re.fullmatch(r'[a-f0-9]{32}', experiment_id):
        raise HTTPException(404, '实验不存在')
    record = store.get('twin-experiment:' + experiment_id)
    if record is None:
        raise HTTPException(404, '实验不存在')
    current = scene_for(record['inputs']['scene']['id'])
    record['scene_changed'] = current['revision'] != record['inputs']['scene_revision']
    return record


@router.get('/twin')
def page():
    return FileResponse(store.ROOT / 'static' / 'twin.html')


@router.post('/api/twin-experiments')
def create(request: twin.TwinRequest):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, '已有孪生实验在计算，请稍后')
    try:
        record = dict(id=uuid.uuid4().hex, created_at=store.now(), **twin.run(scene_for(request.scene), request),
                      evidence_level='synthetic_twin', reproducibility=None)
    finally:
        LOCK.release()
    store.put('twin-experiment:' + record['id'], record)
    brief = twin.summary(record)
    store.put('twin-experiment-latest:' + request.scene, brief)
    index = [row for row in store.get(INDEX, []) if row['id'] != record['id']]
    store.put(INDEX, ([{k: brief[k] for k in ('id', 'created_at', 'scene', 'scene_revision', 'budget', 'seeds',
                                              'kpi_version', 'input_digest', 'result_digest')}] + index)[:50])
    record['scene_changed'] = False
    return record


@router.get('/api/twin-experiments')
def history():
    return dict(experiments=store.get(INDEX, []))


@router.get('/api/twin-experiments/{experiment_id}')
def detail(experiment_id: str):
    return load(experiment_id)


@router.get('/api/twin-experiments/{experiment_id}/segment')
def segment(experiment_id: str, start: int = Query(ge=0), end: int = Query(ge=0)):
    return twin_segment.analyze(load(experiment_id), start, end)


@router.post('/api/twin-experiments/{experiment_id}/recompute')
def recompute(experiment_id: str):
    record = load(experiment_id)
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, '已有孪生实验在计算，请稍后')
    try:
        result = twin.compute(record['inputs'])
    finally:
        LOCK.release()
    recomputed = twin.digest(result)
    check = dict(checked_at=store.now(), input_digest=twin.digest(record['inputs']),
                 input_matches=twin.digest(record['inputs']) == record['input_digest'],
                 result_digest=recomputed, matches=recomputed == record['result_digest'])
    record['reproducibility'] = check
    saved = {k: v for k, v in record.items() if k != 'scene_changed'}
    store.put('twin-experiment:' + experiment_id, saved)
    return check
