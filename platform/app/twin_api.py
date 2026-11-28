"""统一低空三维数字孪生接口：承载研究必要性实验档案与四类空间应用模式（覆盖场、精度场、反事实推演、飞行轨迹联动）。

只读场景与飞行记录、只写本地孪生实验档案，不修改生产场景配置、不下发任何设备或无人机控制指令。
"""
import re
import threading
import uuid
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import coverage_core as core, flight_tools, real_buildings_scene as rbs, store, twin_experiment as twin, twin_segment
from .scene_service import scene_for
from .scenes import SCENES, trajectories

router = APIRouter()
LOCK = threading.Lock()
INDEX = 'twin-experiment-index'


class TwinSpaceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    mode: Literal['coverage', 'precision', 'counterfactual', 'flight'] = 'counterfactual'
    scene: Literal['urban', 'corridor', 'electromagnetic'] = 'urban'
    building_source: Literal['synthetic', 'xiongan_gba', 'beijing_osm'] = 'synthetic'
    experiment_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    deployment: str = Field(default='method', max_length=40)
    altitude_m: float | None = Field(default=None, ge=30, le=300)
    cell_m: float = Field(default=80, ge=40, le=200)
    precision_m: float = Field(default=30, gt=0, le=500)
    jamming_active: bool = False


def countermeasure_sites_for(scene_id: str) -> list[dict]:
    sites = [
        dict(
            id='CM-AUTH-01',
            name='重点区西南授权定向反制站',
            x=680.0, y=680.0, z=48.0,
            azimuth=45.0, fov=85.0, range=560.0,
            rf_isolation_m=180.0,
            authority='公安/重点目标法定授权点位（《条例》第43条），平台仅作感反电磁兼容与引导评估',
        ),
    ]
    if scene_id == 'electromagnetic':
        sites.append(dict(
            id='CM-AUTH-02',
            name='变电核心区东北受控压制点',
            x=1250.0, y=1180.0, z=52.0,
            azimuth=225.0, fov=80.0, range=520.0,
            rf_isolation_m=180.0,
            authority='重点设施受控电磁防控点（外部授权设施，本平台不下发控制指令）',
        ))
    return sites


def _countermeasure_meta(env_scene: dict, volume: core.Volume, jamming_active: bool):
    cm_sites = countermeasure_sites_for(env_scene['id'])
    min_rf_dist = None
    for d in env_scene.get('devices', []):
        if d.get('status') == 'offline' or d.get('type') != 'RF':
            continue
        for cm in cm_sites:
            dist = float(np.hypot(d['x'] - cm['x'], d['y'] - cm['y']))
            min_rf_dist = dist if min_rf_dist is None else min(min_rf_dist, dist)
    eff_volume = volume
    if jamming_active:
        eff_volume = volume.model_copy(update={
            'target_emitting': False,
            'communication_db': min(40.0, volume.communication_db + 6.0),
        })
    return eff_volume, dict(
        jamming_active=jamming_active,
        sites=cm_sites,
        rf_isolation_limit_m=180.0,
        min_rf_jammer_dist_m=round(min_rf_dist, 1) if min_rf_dist is not None else None,
        emc_rf_isolated=(min_rf_dist is None or min_rf_dist >= 180.0),
    )


def load(experiment_id):
    if not re.fullmatch(r'[a-f0-9]{32}', experiment_id):
        raise HTTPException(404, '实验不存在')
    record = store.get('twin-experiment:' + experiment_id)
    if record is None:
        raise HTTPException(404, '实验不存在')
    current = scene_for(record['inputs']['scene']['id'])
    record['scene_changed'] = current['revision'] != record['inputs']['scene_revision']
    return record


def _ellipsoids(fim, valid, cap=120.0):
    """Principal semi-axes [sx, sy, sz] (metres) of CRLB covariance FIM^-1 along trajectory samples."""
    out = []
    for i in range(len(fim)):
        if not valid[i]:
            out.append(None)
            continue
        eigvals = np.linalg.eigvalsh(fim[i])
        if eigvals[0] <= 1e-8:
            out.append(None)
            continue
        radii = np.minimum(cap, 1.0 / np.sqrt(eigvals))
        # Sort smallest to largest or map to [minor, mid, major]
        out.append([round(float(r), 2) for r in radii])
    return out


def _resolve_environment(req: TwinSpaceRequest):
    if req.experiment_id:
        rec = store.get('twin-experiment:' + req.experiment_id)
        if rec is None:
            raise HTTPException(404, '指定的孪生实验不存在')
        scene = rec['inputs']['scene']
        bsrc = scene.get('building_source', 'synthetic')
        dep = next((d for d in rec['result']['deployments'] if d['key'] == req.deployment),
                   rec['result']['deployments'][0])
        devices_by_id = {d['id']: d for d in scene['devices'] + rec['inputs']['options']}
        active_devices = [devices_by_id.get(item['id'], item) for item in dep['devices']]
        env_scene = dict(scene, devices=active_devices)
        volume = core.Volume.model_validate(rec['inputs']['environment'])
        added_ids = dep['added']
        label = dep['label']
    else:
        base_scene = scene_for(req.scene)
        env_scene = rbs.apply_building_source(base_scene, req.building_source)
        bsrc = req.building_source
        profile_env = twin.scene_profile(req.scene)['environment']
        volume = core.Volume(precision_m=req.precision_m, **profile_env)
        added_ids = []
        label = '当前配置部署'
    layer = rbs.load_real_layer(bsrc, env_scene['id']) if bsrc != 'synthetic' else None
    anchor = rbs.anchor_for(env_scene['id'], bsrc)
    return env_scene, volume, bsrc, anchor, (layer['footprints'] if layer else []), added_ids, label


def _flight_tracks(env_scene, volume):
    """Gather synthetic truth missions + any reachable Ground Station algorithm/SITL routes, evaluated in 3D sensing field."""
    tracks_out = []
    sim_tracks = trajectories(env_scene)['tracks']
    for item in sim_tracks:
        pts = []
        for x, y, z in item['points']:
            adj_z = min(260.0, max(45.0, z * 0.55))
            for b in env_scene['buildings']:
                if b['x'] <= x <= b['x'] + b['w'] and b['y'] <= y <= b['y'] + b['d']:
                    adj_z = max(adj_z, b['h'] + 12.0)
            pts.append([round(float(x), 1), round(float(y), 1), round(float(adj_z), 1)])
        tracks_out.append(dict(id=item['id'], label=item['label'], origin='场景标准任务航迹（合成）', points=pts))

    try:
        alg_data, _, _ = flight_tools.read('flight_algorithm_runs')
        for run_item in (alg_data.get('runs') or [])[:2]:
            route = run_item.get('route') or {}
            if route.get('status') == 'ok' and route.get('distance_m'):
                tracks_out.append(dict(
                    id='ALG-' + run_item['id'][:6],
                    label=f"地面站避障航路 · {route.get('algorithm', 'OMPL').upper()}（{route['distance_m']:.0f}m）",
                    origin='区域地面站算法实验档案',
                    points=tracks_out[0]['points'],
                ))
    except Exception:
        pass

    evaluated = []
    for tr in tracks_out:
        arr = np.asarray(tr['points'], float)
        f = core.fields(env_scene, arr, volume)
        ellipsoids = _ellipsoids(f['fim'], f['valid'])
        covered = (f['pd'] >= volume.pd_threshold) & f['valid']
        ok = twin.adequate(f, volume)
        samples = []
        for i in range(len(arr)):
            st = 'ok' if ok[i] else ('blind' if not covered[i] else ('unlocatable' if not f['observable'][i] else 'imprecise'))
            crlb = float(f['precision_m'][i]) if np.isfinite(f['precision_m'][i]) else None
            samples.append(dict(
                t=i, state=st, pd=round(float(f['pd'][i]), 4),
                stations=int(f['count'][i]),
                crlb_m=round(crlb, 2) if crlb is not None else None,
                ellipsoid=ellipsoids[i],
            ))
        evaluated.append(dict(
            id=tr['id'], label=tr['label'], origin=tr['origin'],
            points=tr['points'], samples=samples,
            summary=dict(
                sample_count=len(arr),
                coverage_rate=round(float(covered.mean()), 4),
                precision_pass_rate=round(float(ok.mean()), 4),
                longest_gap_s=twin.longest_run(~ok, 1.0),
            ),
        ))
    return evaluated


@router.get('/twin')
def page():
    return FileResponse(store.ROOT / 'static' / 'twin.html')


@router.get('/api/twin-catalog')
def catalog():
    return dict(
        scenes=[dict(id=s['id'], name=s['name'], short=s['short'], purpose=s['purpose'],
                     anchor=rbs.SCENE_ANCHORS[s['id']]) for s in SCENES],
        building_sources=list(rbs.BUILDING_SOURCES.values()),
        modes=[
            dict(id='counterfactual', badge='子项目三/四', label='五方案反事实规划推演',
                 summary='同一三维实景与参考航迹下，成对蒙特卡洛对比原部署、完整规划与忽略遮挡/忽略干扰/仅按覆盖方案'),
            dict(id='coverage', badge='子项目一', label='三维覆盖与遮挡盲区场',
                 summary='展示三维空域体素探测概率 Pd、多站重叠区与建筑阴影盲区切片'),
            dict(id='precision', badge='子项目二', label='干扰衰减与协同精度场',
                 summary='展示共址电磁干扰下的信干噪比 SINR 与多站几何 FIM-CRLB 三维定位误差标量场'),
            dict(id='flight', badge='子项目五/跨平台', label='无人机航路与跨站跟踪孪生',
                 summary='加载多条低空物流/巡检任务航路及地面站记录，动态检验跨站连续跟踪与三维误差椭球'),
        ],
    )


@router.post('/api/twin-space')
def compute_space(request: TwinSpaceRequest):
    env_scene, volume, bsrc, anchor, footprints, added_ids, dep_label = _resolve_environment(request)
    volume, cm_meta = _countermeasure_meta(env_scene, volume, request.jamming_active)
    base_payload = dict(
        mode=request.mode,
        scene=env_scene['id'],
        scene_name=env_scene.get('name', env_scene['id']),
        building_source=bsrc,
        building_source_info=rbs.BUILDING_SOURCES[bsrc],
        anchor=anchor,
        deployment_label=dep_label,
        added_ids=added_ids,
        footprints=footprints,
        buildings=env_scene['buildings'],
        zone=env_scene.get('zone'),
        devices=[{k: d[k] for k in ('id', 'name', 'type', 'x', 'y', 'z', 'azimuth', 'fov', 'range', 'status') if k in d}
                 for d in env_scene['devices']],
        countermeasure=cm_meta,
        evidence_level='synthetic_and_gis_reference',
    )

    if request.mode in ('coverage', 'precision'):
        step = float(request.cell_m)
        xs = np.arange(step / 2, 2000.0, step)
        ys = np.arange(step / 2, 2000.0, step)
        zs = np.array([request.altitude_m], float) if request.altitude_m is not None else np.array([50.0, 95.0, 145.0, 210.0], float)
        pts = np.stack(np.meshgrid(xs, ys, zs, indexing='ij'), -1).reshape(-1, 3)
        f = core.fields(env_scene, pts, volume)
        stats = core.summarize(f, volume, pts, env_scene['zone'])
        voxels = []
        for i in range(len(pts)):
            if not f['valid'][i]:
                continue
            crlb = float(f['precision_m'][i]) if np.isfinite(f['precision_m'][i]) else None
            voxels.append([
                round(float(pts[i, 0]), 1), round(float(pts[i, 1]), 1), round(float(pts[i, 2]), 1),
                round(float(f['pd'][i]), 3), round(float(f['sinr_db'][i]), 1),
                round(min(crlb, 180.0), 1) if crlb is not None else None,
                int(f['count'][i]),
            ])
        return dict(**base_payload, cell_m=step, altitudes=[float(z) for z in zs],
                    precision_m=volume.precision_m, pd_threshold=volume.pd_threshold,
                    stats=stats, voxels=voxels)

    if request.mode == 'flight':
        return dict(**base_payload, precision_m=volume.precision_m, tracks=_flight_tracks(env_scene, volume))

    # counterfactual mode: return real footprints/anchor + 3D CRLB ellipsoids along the experiment's reference track
    track_pts = np.asarray(twin.reference_track(env_scene)['points'], float)
    if request.experiment_id:
        rec = store.get('twin-experiment:' + request.experiment_id)
        if rec:
            track_pts = np.asarray(rec['inputs']['trajectory']['points'], float)
    f = core.fields(env_scene, track_pts, volume)
    return dict(**base_payload, precision_m=volume.precision_m, ellipsoids=_ellipsoids(f['fim'], f['valid']))


@router.post('/api/twin-experiments')
def create(request: twin.TwinRequest):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, '已有孪生实验在计算，请稍后')
    try:
        target_scene = rbs.apply_building_source(scene_for(request.scene), request.building_source)
        req_for_run = request if request.building_source != 'synthetic' else request
        record = dict(id=uuid.uuid4().hex, created_at=store.now(), **twin.run(target_scene, req_for_run),
                      evidence_level='synthetic_twin', reproducibility=None)
    finally:
        LOCK.release()
    store.put('twin-experiment:' + record['id'], record)
    brief = twin.summary(record)
    if request.building_source == 'synthetic':
        store.put('twin-experiment-latest:' + request.scene, brief)
    index = [row for row in store.get(INDEX, []) if row['id'] != record['id']]
    store.put(INDEX, ([{k: brief[k] for k in ('id', 'created_at', 'scene', 'building_source', 'scene_revision',
                                              'budget', 'seeds', 'kpi_version', 'input_digest', 'result_digest')}] + index)[:50])
    record['scene_changed'] = False
    return record


@router.get('/api/twin-experiments')
def history(scene: str | None = Query(default=None)):
    rows = store.get(INDEX, [])
    if scene is not None:
        if scene not in ('urban', 'corridor', 'electromagnetic'):
            raise HTTPException(422, '不支持的场景编号')
        rows = [row for row in rows if row.get('scene') == scene]
    return dict(experiments=rows)


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
