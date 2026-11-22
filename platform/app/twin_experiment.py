"""建筑区数字孪生必要性实验。

同一条参考轨迹、同一参考环境（建筑遮挡 + 同频/通信/天气损耗）下比较现有部署、方法方案，
以及规划时忽略遮挡、忽略干扰得到的方案。所有方案都放回参考环境评价，并用同一组随机种子
成对生成探测与量测噪声。参考轨迹只作评价真值，不输入定位或跟踪。合成证据，非外场结果。
"""
import copy
import hashlib
import json
import math
import zlib
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from . import coverage_core as core
from .proposal_kpi import VERSION as KPI_VERSION
from .scenes import CATALOG, trajectories

EXPERIMENT_VERSION = 'twin-necessity-v1'
TRACK_ID = 'UAV-002'
PLANNING_SPACE = dict(x=[125, 800, 50], y=[175, 1700, 50], z=[45, 75])
OPTION_AZIMUTHS = {'RADAR': [0, 90, 180, 270], 'EO': [45, 135, 225, 315], 'RF': [0]}
DEPLOYMENTS = [
    ('baseline', '现有部署', None, '保留场景中已有设备，不新增。'),
    ('method', '方法方案', 'full', '规划模型包含建筑遮挡、干扰损耗与多站协同定位。'),
    ('ignore_occlusion', '反事实：规划时忽略遮挡', 'no_occlusion', '规划模型去掉建筑遮挡，其余相同。'),
    ('ignore_interference', '反事实：规划时忽略干扰', 'no_interference', '规划模型去掉同频与通信干扰损耗，其余相同。'),
]
LIMITATIONS = [
    '合成场景、示例设备参数与模拟参考轨迹，不是外场测量。',
    '定位误差由线性化高斯量测与随机漏检模拟，不含共同偏差、多径和虚警。',
    '理论精度界（CRLB）按探测概率加权的信息矩阵计算，不等于实际定位误差。',
    '规划在任务空域采样点上进行，参考轨迹只用于评价；两种贪心加局部搜索不保证全局最优。',
    '只覆盖建筑区一个场景；强电磁、线性廊道、复杂水域与实地飞行需另行验证。',
]


class TwinRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    scene: Literal['urban'] = 'urban'
    budget: float = Field(default=50, gt=0, le=500)
    seed_count: int = Field(default=20, ge=4, le=64)
    precision_m: float = Field(default=30, gt=0, le=500)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def r6(value):
    if value is None:
        return None
    value = float(value)
    return round(value, 6) if np.isfinite(value) else None


def options(scene):
    """Candidate site × device type × azimuth. One option per site at most."""
    result = []
    for site in scene['candidates']:
        for kind in ('RADAR', 'RF', 'EO'):
            for azimuth in OPTION_AZIMUTHS[kind]:
                result.append(dict(id=f"{site['id']}-{kind}-{azimuth:03d}", site_id=site['id'],
                                   name=f"{site.get('name', site['id'])} · {kind} {azimuth}°", type=kind,
                                   x=site['x'], y=site['y'], z=site.get('z', 125), azimuth=azimuth,
                                   pitch=10, vertical_fov=110, status='online', **CATALOG[kind]))
    return result


def reference_track(scene):
    """Low-altitude cruise on the UAV-002 ground path; climbs 10 m above any building it crosses."""
    base = next(t for t in trajectories(scene)['tracks'] if t['id'] == TRACK_ID)
    points = []
    for t, (x, y, _) in enumerate(base['points']):
        z = 60 + 15 * math.sin(t / 18)
        for b in scene['buildings']:
            if b['x'] <= x <= b['x'] + b['w'] and b['y'] <= y <= b['y'] + b['d']:
                z = max(z, b['h'] + 10)
        points.append([x, y, round(z, 1)])
    return dict(id=base['id'] + '-LOW', label='建筑区低空巡航（以 30–100 m 为主，遇建筑爬升越过）', points=points)


def frozen_inputs(scene, request):
    track = reference_track(scene)
    volume = core.Volume(precision_m=request.precision_m)
    return dict(experiment_version=EXPERIMENT_VERSION, model_version=core.MODEL_VERSION, kpi_version=KPI_VERSION,
                scene=copy.deepcopy(scene), scene_revision=scene['revision'],
                trajectory=dict(id=track['id'], label=track['label'], dt_s=1.0,
                                times=list(range(len(track['points']))), points=track['points'],
                                role='合成参考真值，仅用于评价，不输入定位或跟踪'),
                environment=volume.model_dump(), budget=request.budget,
                seeds=[20261120 + i for i in range(request.seed_count)],
                options=options(scene), planning_space=PLANNING_SPACE)


def planning_points(space):
    x0, x1, step = space['x']
    y0, y1, _ = space['y']
    axes = [np.arange(x0, x1, step), np.arange(y0, y1, step), np.array(space['z'], float)]
    return np.stack(np.meshgrid(*axes, indexing='ij'), -1).reshape(-1, 3).astype(float)


def planner_model(scene, volume, kind):
    if kind == 'no_occlusion':
        return dict(scene, buildings=[]), volume
    if kind == 'no_interference':
        return scene, volume.model_copy(update=dict(cochannel_db=0, communication_db=0))
    return scene, volume


def adequate(f, volume):
    return (f['pd'] >= volume.pd_threshold) & f['valid'] & f['observable'] & (f['precision_m'] <= volume.precision_m)


class PlannerModel:
    """Same fusion as coverage_core.fields, with each option's contribution computed once."""

    def __init__(self, scene, volume, choices, points):
        self.volume = volume
        self.base = core.fields(scene, points, volume)
        self.parts = {}
        for option in choices:
            pd, _, r, delta, _ = core.device_field(scene, points, option, volume, self.base['valid'])
            self.parts[option['id']] = (pd, core.device_information(option, r, delta) * pd[:, None, None])

    def score(self, ids):
        miss, fim = 1 - self.base['pd'], self.base['fim'].copy()
        for identifier in ids:
            pd, information = self.parts[identifier]
            miss, fim = miss * (1 - pd), fim + information
        eig = np.linalg.eigvalsh(fim)
        observable = (eig[:, 0] > 1e-8) & self.base['valid']
        precision = np.full(len(miss), np.inf)
        precision[observable] = np.sqrt((1 / eig[observable]).sum(axis=1))
        return int(((1 - miss >= self.volume.pd_threshold) & observable & (precision <= self.volume.precision_m)).sum())


def plan(scene, volume, kind, choices, budget, points):
    """单位费用增益与绝对增益两种贪心各做局部搜索（删 1 加 1–2）和冗余删除，取规划模型得分高者；每站址至多一台。"""
    model_scene, model_volume = planner_model(scene, volume, kind)
    model = PlannerModel(model_scene, model_volume, choices, points)
    by_id = {option['id']: option for option in choices}
    cost = lambda ids: sum(by_id[i]['cost'] for i in ids)
    runs = [search(model, choices, budget, by_id, cost, per_cost) for per_cost in (True, False)]
    selected, score, history = max(runs, key=lambda r: (r[1], -cost(r[0])))
    compared = [dict(rule=h[0]['rule'] if h else ('单位费用增益贪心' if k == 0 else '绝对增益贪心'),
                     planner_score=s, cost=cost(ids), devices=list(ids)) for k, (ids, s, h) in enumerate(runs)]
    history = [dict(step='compare', rule='取规划模型得分高者', planning_samples=len(points),
                    baseline_score=model.score([]), candidates=compared)] + history
    return [copy.deepcopy(by_id[i]) for i in selected], cost(selected), history


def search(model, choices, budget, by_id, cost, per_cost):
    rule = '单位费用增益贪心' if per_cost else '绝对增益贪心'
    selected, history = [], []
    score = model.score(selected)
    while True:
        used = {by_id[i]['site_id'] for i in selected}
        best = None
        for option in choices:
            if option['site_id'] in used or cost(selected) + option['cost'] > budget + 1e-9:
                continue
            gain = model.score(selected + [option['id']]) - score
            key = gain / option['cost'] if per_cost else (gain, -option['cost'])
            if gain > 0 and (best is None or key > best[0]):
                best = (key, gain, option['id'])
        if best is None:
            break
        selected.append(best[2])
        score += best[1]
        history.append(dict(rule=rule, step='add', option=best[2], gain_samples=best[1], planner_score=score))
    for _ in range(20):
        best = None
        for removed in [None] + selected:
            kept = [i for i in selected if i != removed]
            used = {by_id[i]['site_id'] for i in kept}
            room = budget - cost(kept) + 1e-9
            free = [o for o in choices if o['site_id'] not in used and o['cost'] <= room]
            moves = [[o] for o in free] + [[a, b] for k, a in enumerate(free) for b in free[k + 1:]
                                           if a['site_id'] != b['site_id'] and a['cost'] + b['cost'] <= room]
            for added in moves:
                trial = kept + [o['id'] for o in added]
                value = model.score(trial)
                key = (value, -cost(trial))
                if value > score and (best is None or key > best[0]):
                    best = (key, trial, removed, [o['id'] for o in added])
        if best is None:
            break
        (value, _), selected, removed, added = best
        history.append(dict(rule=rule, step='replace' if removed else 'add', option='+'.join(added), replaced=removed,
                            gain_samples=value - score, planner_score=value))
        score = value
    for identifier in sorted(selected, key=lambda i: -by_id[i]['cost']):
        trial = [i for i in selected if i != identifier]
        if model.score(trial) >= score:
            selected = trial
            history.append(dict(rule=rule, step='remove', option=identifier, gain_samples=0, planner_score=score))
    return selected, score, history


def longest_run(mask, dt):
    best = run = 0
    for value in mask:
        run = run + 1 if value else 0
        best = max(best, run)
    return best * dt


def cause_of(i, f, f_open, f_quiet, volume):
    th = volume.pd_threshold
    if not f['valid'][i]:
        return 'invalid', '轨迹点位于建筑内部'
    if f['pd'][i] < th:
        if f_open['pd'][i] >= th:
            return 'blind', '建筑遮挡'
        if f_quiet['pd'][i] >= th:
            return 'blind', '干扰衰减'
        return 'blind', '超出探测范围或视场'
    if not f['observable'][i]:
        return 'unlocatable', '单站观测，无法定位'
    if f['precision_m'][i] > volume.precision_m:
        return 'imprecise', '观测几何差，理论误差超限'
    return 'ok', '可定位且理论误差合格'


def simulate(scene, devices, points, f, seeds):
    """Paired Monte Carlo: same seed and device id give identical draws in every deployment."""
    n = len(points)
    parts = []
    for d in devices:
        _, r, delta = core.visible(points, d, scene['buildings'])
        info = core.device_information(d, r, delta)
        w, vectors = np.linalg.eigh(info)
        parts.append((d['id'], f['per_device'][d['id']], info, vectors * np.sqrt(np.clip(w, 0, None))[:, None, :]))
    errors = np.full((len(seeds), n), np.nan)
    for k, s in enumerate(seeds):
        information = np.zeros((n, 3, 3))
        weighted = np.zeros((n, 3))
        for device_id, pd, info, root in parts:
            rng = np.random.default_rng([s, zlib.crc32(device_id.encode())])
            detected = rng.random(n) < pd
            noise = np.einsum('nij,nj->ni', root, rng.standard_normal((n, 3)))
            information += info * detected[:, None, None]
            weighted += noise * detected[:, None]
        fixed = (np.linalg.eigvalsh(information)[:, 0] > 1e-8) & f['valid']
        if fixed.any():
            estimate = np.linalg.solve(information[fixed], weighted[fixed][..., None])[..., 0]
            errors[k, fixed] = np.linalg.norm(estimate, axis=1)
    return errors


def evaluate(scene, devices, inputs, added, cost, history, key, label, description):
    volume = core.Volume.model_validate(inputs['environment'])
    points = np.array(inputs['trajectory']['points'], float)
    dt = inputs['trajectory']['dt_s']
    env = dict(scene, devices=devices)
    f = core.fields(env, points, volume)
    f_open = core.fields(dict(env, buildings=[]), points, volume)
    f_quiet = core.fields(env, points, volume.model_copy(update=dict(cochannel_db=0, communication_db=0)))
    errors = simulate(scene, devices, points, f, inputs['seeds'])
    covered = (f['pd'] >= volume.pd_threshold) & f['valid']
    theory_ok = adequate(f, volume)
    finite = np.isfinite(errors)
    sim_ok = finite & (np.nan_to_num(errors, nan=np.inf) <= volume.precision_m)
    values = errors[finite]
    timeline, segments = [], []
    for i, t in enumerate(inputs['trajectory']['times']):
        state, cause = cause_of(i, f, f_open, f_quiet, volume)
        column = errors[:, i][finite[:, i]]
        timeline.append(dict(t=t, state=state, cause=cause, pd=r6(f['pd'][i]),
                             crlb_m=r6(f['precision_m'][i]), stations=int(f['count'][i]),
                             simulated_median_error_m=r6(np.median(column)) if len(column) else None,
                             simulated_fix_fraction=r6(finite[:, i].mean())))
        if state != 'ok':
            if segments and segments[-1]['cause'] == cause and segments[-1]['end'] == t - 1:
                segments[-1]['end'] = t
            else:
                segments.append(dict(start=t, end=t, cause=cause, state=state, position=inputs['trajectory']['points'][i]))
    for segment in segments:
        segment['duration_s'] = (segment['end'] - segment['start'] + 1) * dt
    causes = {}
    for segment in segments:
        causes[segment['cause']] = causes.get(segment['cause'], 0) + segment['duration_s']
    covered_ok = theory_ok[covered]
    single = covered & (f['count'] == 1)
    multi = covered & (f['count'] >= 2)
    metrics = dict(
        added_cost=r6(cost), added_devices=len(added),
        track_coverage=r6(covered.mean()), theoretical_pass=r6(theory_ok.mean()),
        longest_gap_theoretical_s=longest_run(~theory_ok, dt),
        simulated_fix_rate=r6(finite.mean()), simulated_pass=r6(sim_ok.mean()),
        simulated_longest_gap_median_s=float(np.median([longest_run(~row, dt) for row in sim_ok])),
        simulated_rmse_m=r6(np.sqrt(np.mean(values ** 2))) if len(values) else None,
        simulated_p90_m=r6(np.percentile(values, 90)) if len(values) else None,
        crlb_median_m=r6(np.median(f['precision_m'][np.isfinite(f['precision_m'])])) if np.isfinite(f['precision_m']).any() else None,
        simulated_median_m=r6(np.median(values)) if len(values) else None,
        covered_pass_rate=r6(covered_ok.mean()) if len(covered_ok) else None,
        single_station_samples=int(single.sum()), multi_station_samples=int(multi.sum()),
        single_station_pass=r6(theory_ok[single].mean()) if single.any() else None,
        multi_station_pass=r6(theory_ok[multi].mean()) if multi.any() else None,
        unavailable_seconds_by_cause={k: v for k, v in sorted(causes.items())})
    return dict(key=key, label=label, description=description, metrics=metrics,
                devices=[{k: d[k] for k in ('id', 'name', 'type', 'x', 'y', 'z', 'azimuth', 'fov', 'range', 'status')} for d in devices],
                added=[d['id'] for d in added], planning_history=history, segments=segments, timeline=timeline)


def pct(value):
    return '无样本' if value is None else f'{value * 100:.1f}%'


def points_diff(a, b):
    return None if a is None or b is None else round((a - b) * 100, 1)


def contrast(name, method, other):
    diff = points_diff(method['theoretical_pass'], other['theoretical_pass'])
    gap = other['longest_gap_theoretical_s'] - method['longest_gap_theoretical_s']
    if diff is None:
        return f'{name}缺少有效样本，无法判断。'
    if diff <= 0 and gap <= 0:
        return f'本算例中{name}放回参考环境后未变差（理论合格 {pct(other["theoretical_pass"])}），这一项的必要性在本轨迹上未得到支持。'
    return (f'{name}，放回含遮挡与干扰的参考环境后，理论合格比例 {pct(other["theoretical_pass"])}，'
            f'比方法方案低 {diff:.1f} 个百分点；最长连续不可用 {other["longest_gap_theoretical_s"]:g} s，'
            f'方法方案 {method["longest_gap_theoretical_s"]:g} s。')


def search_note(rows):
    row = next(r for r in rows if r['key'] == 'method')
    compare = next((h for h in row['planning_history'] if h.get('step') == 'compare'), None)
    text = '不规划时保持现有部署的缺测；只按理想模型规划则如子项目一、二的反事实所示。'
    if not compare:
        return text
    parts = '；'.join(f'{c["rule"]}得 {c["planner_score"]} 个合格采样点、费用 {c["cost"]:g} 万元' for c in compare['candidates'])
    scores = {c['planner_score'] for c in compare['candidates']}
    tail = '同一规划模型下搜索方式不同，结果不同，需要多目标优化与最优差距检验。' if len(scores) > 1 else '两种搜索在本算例中结果相同。'
    return f'{text}规划空域 {compare["planning_samples"]} 个采样点中现有部署合格 {compare["baseline_score"]} 个；{parts}。{tail}'


def research_points(rows, inputs):
    by = {row['key']: row['metrics'] for row in rows}
    base, method = by['baseline'], by['method']
    occlusion, interference = by['ignore_occlusion'], by['ignore_interference']
    blocked = base['unavailable_seconds_by_cause'].get('建筑遮挡', 0)
    single, multi = method['single_station_pass'], method['multi_station_pass']
    common = dict(evidence_level='合成孪生', evidence_note='合成场景与模拟参考轨迹；不能写成外场精度或现场验收')
    return [
        dict(id=1, subproject='子项目一', title='三维感知覆盖建模',
             question='建筑遮挡造成的漏看出现在航迹的哪一段？',
             finding=f'现有部署在这条航迹上因建筑遮挡不可用 {blocked:g} s，轨迹覆盖 {pct(base["track_coverage"])}。',
             necessity=contrast('规划时忽略遮挡', method, occlusion),
             feeds=[2, 3], evidence=dict(baseline_blocked_s=blocked, ignore_occlusion=occlusion, method=method),
             pending=['用测绘建筑与单设备标定数据复核遮挡和视场', '网格收敛与独立空间采样对照'], **common),
        dict(id=2, subproject='子项目二', title='干扰与多站协同精度',
             question='同一位置上，单站和多站、有干扰和无干扰时，能否定位、误差多大？',
             finding=(f'方法方案中单站覆盖样本理论合格 {pct(single)}，多站覆盖样本 {pct(multi)}；'
                      f'理论界中位数 {method["crlb_median_m"]} m，模拟定位误差中位数 {method["simulated_median_m"]} m。'
                      '理论界低不等于实际误差低。'),
             necessity=contrast('规划时忽略干扰', method, interference),
             depends_on=[1], feeds=[3], evidence=dict(single_station_pass=single, multi_station_pass=multi,
                                                     ignore_interference=interference, method=method),
             pending=['实测标定干扰传播与误差相关性', '加入共同偏差与真实估计器后的误差对照'], **common),
        dict(id=3, subproject='子项目三', title='按需规划与多目标优化',
             question='同样的预算下，补站之后缺测缩短多少、费用增加多少？',
             finding=(f'新增 {method["added_devices"]} 台、费用 {method["added_cost"]:g} 万元（预算 {inputs["budget"]:g} 万元）；'
                      f'理论合格 {pct(base["theoretical_pass"])} → {pct(method["theoretical_pass"])}，'
                      f'最长连续不可用 {base["longest_gap_theoretical_s"]:g} s → {method["longest_gap_theoretical_s"]:g} s，'
                      f'模拟定位合格 {pct(base["simulated_pass"])} → {pct(method["simulated_pass"])}。'),
             necessity=search_note(rows),
             depends_on=[1, 2], feeds=[4], evidence=dict(baseline=base, method=method),
             pending=['与小规模穷举或整数规划比较最优差距', '核验站址挂高、供电、回传与报价'], **common),
        dict(id=4, subproject='子项目四', title='规划平台与可复算档案',
             question='任务、场景、部署、轨迹和模型能否冻结并复算出同一结果？',
             finding=f'本实验冻结场景版本 {inputs["scene_revision"]}、覆盖模型 {inputs["model_version"]}、指标版本 {inputs["kpi_version"]} 与 {len(inputs["seeds"])} 个随机种子。',
             necessity='没有冻结输入，覆盖、精度和方案的前后数字无法对上，也无法复核。',
             depends_on=[3], feeds=[5], evidence=dict(seeds=len(inputs['seeds']), model_version=inputs['model_version']),
             pending=['真实设备协议接入与时间同步', '运行反馈驱动的滚动优化'], **common),
        dict(id=5, subproject='子项目五', title='典型场景示范',
             question='这条闭环能否换成实测轨迹与现场观测再跑一次？',
             finding='建筑区合成闭环已可复现，只作为示范准备证据。',
             necessity='只有接入现场观测与独立真值，才能把合成结论换成外场证据。',
             depends_on=[4], evidence=dict(scenes_ready=['建筑区（合成）']),
             pending=['强电磁、线性廊道、复杂水域场景映射', '实地组网、无人机对抗与独立参考真值'], **common),
    ]


def compute(inputs):
    scene = inputs['scene']
    volume = core.Volume.model_validate(inputs['environment'])
    points = planning_points(inputs['planning_space'])
    rows = []
    for key, label, kind, description in DEPLOYMENTS:
        if kind is None:
            added, cost, history = [], 0.0, []
        else:
            added, cost, history = plan(scene, volume, kind, inputs['options'], inputs['budget'], points)
        rows.append(evaluate(scene, scene['devices'] + added, inputs, added, cost, history, key, label, description))
    return dict(deployments=rows, research_points=research_points(rows, inputs),
                relations=[dict(source=1, target=2, meaning='覆盖与遮挡决定哪些设备能观测，是精度场的输入'),
                           dict(source=2, target=3, meaning='定位是否可行、误差多大，构成规划的约束与目标'),
                           dict(source=1, target=3, meaning='盲区位置决定补站候选'),
                           dict(source=3, target=4, meaning='方案、轨迹与模型版本冻结为一份可复算档案'),
                           dict(source=4, target=5, meaning='示范时以同一档案接入实测观测')],
                planning_samples=len(points), limitations=LIMITATIONS)


def run(scene, request):
    inputs = frozen_inputs(scene, request)
    result = compute(inputs)
    return dict(inputs=inputs, input_digest=digest(inputs), result=result, result_digest=digest(result))


def summary(record):
    result = record['result']
    return dict(id=record['id'], created_at=record['created_at'], scene=record['inputs']['scene']['id'],
                scene_revision=record['inputs']['scene_revision'], experiment_version=record['inputs']['experiment_version'],
                kpi_version=record['inputs']['kpi_version'], model_version=record['inputs']['model_version'],
                budget=record['inputs']['budget'], seeds=len(record['inputs']['seeds']),
                input_digest=record['input_digest'], result_digest=record['result_digest'],
                deployments=[dict(key=row['key'], label=row['label'], metrics=row['metrics'], added=row['added'])
                             for row in result['deployments']],
                research_points=[{k: p[k] for k in ('id', 'subproject', 'title', 'question', 'finding', 'necessity', 'pending', 'evidence_level')}
                                 for p in result['research_points']],
                baseline_segments=[s for s in result['deployments'][0]['segments']][:8],
                limitations=result['limitations'])
