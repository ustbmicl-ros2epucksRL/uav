"""Bounded joint search and exhaustive, volume-weighted multiscale verification."""
import copy
import hashlib
import json
import math
import time
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import coverage_core as core


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    bounds: list[float] = Field(default=[0, 2000, 0, 2000, 30, 300], min_length=6, max_length=6)
    wide_cell: float = Field(default=10, ge=2, le=50)
    focus_cell: float = Field(default=2, ge=2, le=10)
    heights: list[float] = Field(default=[80, 125, 170], min_length=1, max_length=5)
    azimuths: list[float] = Field(default=[0, 90, 180, 270], min_length=1, max_length=8)
    model_ids: list[str] = Field(default_factory=list, max_length=5)
    optimize: bool = True
    verify_reference: bool = False
    retune_existing: bool = False
    retuning_reserve: float = Field(default=2, ge=0, le=10000)
    low_altitude_target: float | None = Field(default=None, gt=0, le=1)
    strategy: Literal["legacy", "precision"] = "legacy"
    reference_job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode='after')
    def validate_geometry(self):
        b = self.bounds
        if self.verify_reference and (self.optimize or not self.reference_job_id):
            raise ValueError('方案复核须选择已完成的对照方案并关闭优化')
        if self.retune_existing and self.low_altitude_target is None:
            raise ValueError('既有设备转向优化须设置低空层目标')
        if any(b[i] >= b[i + 1] for i in (0, 2, 4)) or min(b) < 0 or max(b[:4]) > 2000 or b[4] < 30 or b[5] > 300:
            raise ValueError('验算范围须位于 0–2000 m 平面和 30–300 m 空域')
        if self.low_altitude_target is not None:
            if self.strategy != 'precision' or b[4] != 30 or b[5] < 100:
                raise ValueError('低空优化须使用精度优化方法，并完整包含 30–100 m 高度层')
            if not math.isclose(70 / self.wide_cell, round(70 / self.wide_cell)):
                raise ValueError('低空层 100 m 边界须对齐广域网格')
        if self.focus_cell > self.wide_cell:
            raise ValueError('重点区网格应不大于广域网格')
        if any(not math.isclose((b[i + 1] - b[i]) / self.wide_cell, round((b[i + 1] - b[i]) / self.wide_cell)) for i in (0, 2, 4)):
            raise ValueError('验算范围须包含完整广域网格')
        if any(not math.isfinite(h) or h < 1 or h > 300 for h in self.heights):
            raise ValueError('挂高须在 1–300 m 内')
        if any(not math.isfinite(a) or a < 0 or a >= 360 for a in self.azimuths):
            raise ValueError('朝向须在 0–360° 内')
        if len(set(self.model_ids)) != len(self.model_ids):
            raise ValueError('型号编号重复')
        return self


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def geometry(request, task):
    b = request.bounds
    z = task['focus']
    focus = [max(b[0], z['x0']), min(b[1], z['x1']), max(b[2], z['y0']), min(b[3], z['y1']), max(b[4], z['z0']), min(b[5], z['z1'])]
    if any(focus[i] >= focus[i + 1] for i in (0, 2, 4)):
        raise ValueError('重点区与验算空域须有交集')
    for i in (0, 2, 4):
        for edge in focus[i:i + 2]:
            if not math.isclose((edge - b[i]) / request.wide_cell, round((edge - b[i]) / request.wide_cell)):
                raise ValueError('重点区边界须对齐广域网格，确保区域无重叠和遗漏')
        if not math.isclose((focus[i + 1] - focus[i]) / request.focus_cell, round((focus[i + 1] - focus[i]) / request.focus_cell)):
            raise ValueError('重点区须包含完整精细网格')
    if request.low_altitude_target is not None and focus[4] < 100 < focus[5] and not math.isclose((100-focus[4])/request.focus_cell, round((100-focus[4])/request.focus_cell)):
        raise ValueError('低空层 100 m 边界须对齐重点区网格')
    count = lambda box, cell: math.prod(round((box[i + 1] - box[i]) / cell) for i in (0, 2, 4))
    total = count(b, request.wide_cell) - count(focus, request.wide_cell) + count(focus, request.focus_cell)
    if total > 20000000:
        raise ValueError('本次验算超过 2000 万网格，请调整区域或分辨率')
    return focus, total


def inside(points, bounds):
    return np.all((points >= np.array(bounds)[::2]) & (points < np.array(bounds)[1::2]), axis=1)


def batches(bounds, cell, batch_size=30000):
    shape = [round((bounds[i + 1] - bounds[i]) / cell) for i in (0, 2, 4)]
    total = math.prod(shape)
    for start in range(0, total, batch_size):
        flat = np.arange(start, min(total, start + batch_size))
        indices = np.column_stack(np.unravel_index(flat, shape))
        yield np.array(bounds)[::2] + (indices + .5) * cell


def planned_devices(scene, selected, updated=None):
    replacements = {device['id']: device for device in (updated or [])}
    return [replacements.get(device['id'], device) for device in scene['devices']] + selected


def evaluate(scene, selected, request, task, calibration=None, batch_size=30000, reference=None, updated=None, reference_updated=None):
    focus, total = geometry(request, task)
    settings = core.Volume.model_validate(task['volume'])
    additions = dict(scene, devices=selected)
    stages = ['before', 'after'] + (['reference'] if reference is not None else [])
    sums = [{key: 0.0 for key in ('valid', 'covered', 'precise', 'pd', 'focus_valid', 'focus_covered', 'blind', 'unobservable', 'geometry')} for _ in stages]
    clouds = [[] for _ in stages]
    altitude = [{label: dict(valid=0., precise=0., blind=0., unobservable=0., geometry=0.) for label in ('30–100 m', '100–200 m', '200–300 m')} for _ in stages]
    regions = []
    evaluated = 0
    for label, bounds, cell in [('wide', request.bounds, request.wide_cell), ('focus', focus, request.focus_cell)]:
        counts = [dict(valid=0, covered=0, precise=0) for _ in stages]
        region_total = math.prod(round((bounds[i + 1] - bounds[i]) / cell) for i in (0, 2, 4))
        sample_step = max(1, math.ceil(region_total / 3000))
        offset = 0
        for points in batches(bounds, cell, batch_size):
            if label == 'wide':
                points = points[~inside(points, focus)]
            if not len(points):
                continue
            if updated:
                changed_ids = {d['id'] for d in updated}
                common = core.fields(dict(scene, devices=[d for d in scene['devices'] if d['id'] not in changed_ids]), points, settings, calibration)
                before = core.fields(dict(scene, devices=[d for d in scene['devices'] if d['id'] in changed_ids]), points, settings, calibration, initial=common)
                after = core.fields(dict(scene, devices=updated + selected), points, settings, calibration, initial=common)
            else:
                before = core.fields(scene, points, settings, calibration)
                after = core.fields(additions, points, settings, calibration, initial=before) if selected else before
            indices = np.arange(len(points))[(np.arange(len(points)) + offset) % sample_step == 0]
            offset += len(points)
            evaluated += len(points)
            fields = [before, after]
            if reference is not None:
                if reference_updated:
                    fields.append(core.fields(dict(scene, devices=planned_devices(scene, reference, reference_updated)), points, settings, calibration))
                else:
                    fields.append(core.fields(dict(scene, devices=reference), points, settings, calibration, initial=before))
            for index, field in enumerate(fields):
                valid = field['valid']
                covered = valid & (field['pd'] >= settings.pd_threshold)
                precise = covered & field['observable'] & (field['precision_m'] <= settings.precision_m)
                nvalid, ncovered, nprecise = int(valid.sum()), int(covered.sum()), int(precise.sum())
                counts[index]['valid'] += nvalid
                counts[index]['covered'] += ncovered
                counts[index]['precise'] += nprecise
                area = sums[index]
                area['valid'] += nvalid * cell ** 3
                area['covered'] += ncovered * cell ** 3
                area['precise'] += nprecise * cell ** 3
                area['pd'] += float(field['pd'][valid].sum()) * cell ** 3
                failure = dict(blind=valid & ~covered, unobservable=covered & ~field['observable'],
                               geometry=covered & field['observable'] & ~precise)
                for reason, mask in failure.items():
                    area[reason] += int(mask.sum()) * cell ** 3
                for band, lower, upper in [('30–100 m', 30, 100), ('100–200 m', 100, 200), ('200–300 m', 200, 301)]:
                    in_band = (points[:, 2] >= lower) & (points[:, 2] < upper)
                    for key, mask in dict(valid=valid, precise=precise, **failure).items():
                        altitude[index][band][key] += int((in_band & mask).sum()) * cell ** 3
                if label == 'focus':
                    area['focus_valid'] += nvalid * cell ** 3
                    area['focus_covered'] += ncovered * cell ** 3
                for i in indices[valid[indices]]:
                    clouds[index].append([*points[i].tolist(), float(field['pd'][i]), float(field['sinr_db'][i]), float(field['precision_m'][i]) if field['observable'][i] else None, cell])
        regions.append(dict(region=label, bounds=bounds, cell=cell, before=counts[0], after=counts[1]))
    results = []
    for values, cloud, bands in zip(sums, clouds, altitude):
        valid = values['valid']
        results.append(dict(stats={
            'coverage': values['covered'] / valid if valid else None,
            'focus_coverage': values['focus_covered'] / values['focus_valid'] if values['focus_valid'] else None,
            'precision_pass_rate': values['precise'] / valid if valid else None,
            'mean_pd': values['pd'] / valid if valid else None,
            'valid_volume_m3': valid, 'covered_volume_m3': values['covered'],
            'focus_valid_volume_m3': values['focus_valid'],
            'precision_threshold_m': settings.precision_m,
        }, cloud=cloud, bottleneck=dict(
            fractions={key: values[key] / valid if valid else None for key in ('blind', 'unobservable', 'geometry')},
            altitude=[dict(band=band, valid_volume_m3=v['valid'], **{key: v[key] / v['valid'] if v['valid'] else None for key in ('precise', 'blind', 'unobservable', 'geometry')}) for band, v in bands.items()])))
    return dict(**dict(zip(stages, results)), regions=regions, evaluated_cells=evaluated, expected_cells=total,
                aggregation='有效体素体积加权；重点区替换对应广域体素；统计遍历全部网格，点云为抽样',
                columns=['x', 'y', 'z', 'pd', 'sinr_db', 'crlb_position_rmse_m', 'cell_m'])


def engineering_cost(resource, selected):
    existing = {d['site_id'] for d in resource['devices']}
    used = {d['site_id'] for d in selected} - existing
    return sum(d['cost'] for d in selected) + sum(s.get('setup_cost', 0) for s in resource['sites'] if s['id'] in used)


def permitted(resource, selected):
    devices = resource['devices'] + selected
    for site in resource['sites']:
        deployed = [d for d in devices if d['site_id'] == site['id']]
        if len(deployed) > site['capacity'] or sum(d.get('power_w', 100) for d in deployed) > site['power_w'] or sum(d.get('bandwidth_mbps', 10) for d in deployed) > site['bandwidth_mbps']:
            return False
        if any(not site['min_height'] <= d['z'] <= site['max_height'] for d in deployed):
            return False
    return True


def variants(resource, request):
    sites = {s['id']: s for s in resource['sites']}
    models = [m for m in resource['models'] if m['type'] != 'ADSB' and (not request.model_ids or m['id'] in request.model_ids)]
    if set(request.model_ids) - {m['id'] for m in models}:
        raise ValueError('所选型号缺失或为合作报文型号')
    result = []
    occupied = {d['id'] for d in resource['devices']}
    for candidate in resource['candidates']:
        if candidate['status'] != 'online' or candidate['id'] in occupied:
            continue
        site = sites[candidate['site_id']]
        # A physically identified asset keeps its model and pose. Anonymous candidate slots are configurable.
        if candidate.get('asset_code'):
            result.append(copy.deepcopy(candidate))
            continue
        for model in models:
            for height in sorted(set(request.heights)):
                if not site['min_height'] <= height <= site['max_height']:
                    continue
                for azimuth in ([0] if model['fov'] == 360 else sorted(set(request.azimuths))):
                    device = dict(candidate)
                    device.update({k: model[k] for k in ('type', 'range', 'fov', 'vertical_fov', 'cost', 'power_w', 'bandwidth_mbps')})
                    device.update(model_id=model['id'], z=height, azimuth=azimuth, name=site['name'] + ' / ' + model['name'])
                    result.append(device)
    if len(result) > 512:
        raise ValueError('设备、挂高与朝向组合超过 512 个，请缩小搜索范围')
    return result


def search(scene, resource, task, request, calibration=None):
    settings = core.Volume.model_validate(task['volume'])
    points = core.grid(core.default_volume())
    points = points[inside(points, request.bounds)]
    # Small custom regions still receive a search sample when no 50 m centre intersects them.
    if not len(points):
        points = np.array([(np.array(request.bounds)[::2] + np.array(request.bounds)[1::2]) / 2])
    base = core.fields(scene, points, settings, calibration)
    focus_bounds, _ = geometry(request, task)
    focus = base['valid'] & inside(points, focus_bounds)
    valid = base['valid']
    targets = [task['coverage_target'], task['focus_target'], task['precision_rate_target']]

    def score(pd, information):
        covered = pd >= settings.pd_threshold
        eigen = np.linalg.eigvalsh(information)
        observable = eigen[:, 0] > 1e-8
        error = np.full(len(points), np.inf)
        error[observable] = np.sqrt((1 / eigen[observable]).sum(axis=1))
        precise = covered & observable & (error <= settings.precision_m)
        rates = [float(covered[valid].mean()) if valid.any() else 0,
                 float(covered[focus].mean()) if focus.any() else 0,
                 float(precise[valid].mean()) if valid.any() else 0]
        deficit = sum(max(0, target - rate) for target, rate in zip(targets, rates))
        # Continuous probability provides progress across the hard detection threshold.
        return deficit, float(pd[valid].mean()) if valid.any() else 0, rates

    choices = variants(resource, request) if request.optimize else []
    if not permitted(resource, []):
        raise ValueError('已有部署超出站址工程容量或挂高范围')
    cache = []
    for device in choices:
        field = core.fields(dict(scene, devices=[device]), points, settings, calibration)
        cache.append((device, field['pd'], field['fim']))
    all_variants = cache.copy()
    selected = []
    pd, information = base['pd'], base['fim']
    current = score(pd, information)
    history = []
    comparisons = 0
    while cache and current[0] > 1e-10:
        best = None
        current_cost = engineering_cost(resource, selected)
        for index, (device, contribution, info) in enumerate(cache):
            trial = selected + [device]
            cost = engineering_cost(resource, trial)
            if cost > task['budget'] + 1e-9 or not permitted(resource, trial):
                continue
            new_pd = 1 - (1 - pd) * (1 - contribution)
            new_info = information + info
            candidate_score = score(new_pd, new_info)
            comparisons += 1
            gain = current[0] - candidate_score[0]
            probability_gain = candidate_score[1] - current[1]
            if gain <= 1e-12 and probability_gain <= 1e-12:
                continue
            rank = ((gain + .01 * probability_gain) / max(cost - current_cost, .01), gain, -cost)
            if best is None or rank > best[0]:
                best = (rank, index, new_pd, new_info, candidate_score)
        if best is None:
            break
        _, index, pd, information, current = best
        device = cache[index][0]
        selected.append(device)
        cache = [row for row in cache if row[0]['id'] != device['id']]
        history.append(dict(device_id=device['id'], model_id=device.get('model_id'), height=device['z'], azimuth=device['azimuth'],
                            cost=engineering_cost(resource, selected), coverage=current[2][0], focus_coverage=current[2][1], precision_pass_rate=current[2][2]))
    # Revisit inexpensive early choices after additions exhaust the candidate slots.
    # Each replacement uses the remaining network, so a type change can trade cost for precision.
    for _ in range(3):
        changed = False
        for previous in list(selected):
            remainder = [device for device in selected if device['id'] != previous['id']]
            remaining_field = core.fields(dict(scene, devices=scene['devices'] + remainder), points, settings, calibration)
            replacement = None
            best_deficit = current[0]
            for device, contribution, info in all_variants:
                if device['id'] != previous['id']:
                    continue
                trial = remainder + [device]
                if engineering_cost(resource, trial) > task['budget'] + 1e-9 or not permitted(resource, trial):
                    continue
                candidate_score = score(1 - (1 - remaining_field['pd']) * (1 - contribution), remaining_field['fim'] + info)
                comparisons += 1
                if candidate_score[0] < best_deficit - 1e-12:
                    replacement = (device, candidate_score)
                    best_deficit = candidate_score[0]
            if replacement is not None:
                device, current = replacement
                selected = remainder + [device]
                changed = True
                history.append(dict(action='replace', device_id=device['id'], model_id=device.get('model_id'), height=device['z'], azimuth=device['azimuth'],
                                    cost=engineering_cost(resource, selected), coverage=current[2][0], focus_coverage=current[2][1], precision_pass_rate=current[2][2]))
        if not changed:
            break
    # Remove redundant devices while preserving the attained constraints on the search grid.
    for device in list(reversed(selected)):
        trial = [d for d in selected if d['id'] != device['id']]
        field = core.fields(dict(scene, devices=scene['devices'] + trial), points, settings, calibration)
        candidate_score = score(field['pd'], field['fim'])
        if candidate_score[0] <= current[0] + 1e-12:
            selected, current = trial, candidate_score
    return dict(selected=selected, added_cost=engineering_cost(resource, selected), search_history=history,
                evaluated_subsets=comparisons, variant_count=len(choices), search_cell=50,
                method='离散型号、挂高、朝向联合贪心增补、局部替换与冗余删除；覆盖和精度双约束；细网格独立复核',
                optimality='启发式有限候选搜索，全局最优性待验证')


def calculate(snapshot):
    started = time.perf_counter()
    request = AssessmentRequest.model_validate(snapshot['parameters'])
    task = snapshot['task']
    scene = copy.deepcopy(snapshot['scene'])
    scene['zone'] = task['focus']
    optimizer = search
    if request.strategy == 'precision':
        from .precision_planning import search as optimizer
    if request.retune_existing and request.optimize:
        from .low_altitude_planning import search as optimizer
    reference = snapshot.get('reference')
    extra = {'seed': reference['selected']} if request.low_altitude_target is not None and reference else {}
    if request.retune_existing and request.optimize and reference:
        extra['seed_updated'] = reference.get('updated_devices', [])
    if request.verify_reference:
        selected = copy.deepcopy(reference['selected'])
        updated = copy.deepcopy(reference.get('updated_devices', []))
        updates = {device['id']: device for device in updated}
        resource = dict(snapshot['resources'], devices=[dict(device, **{key: value for key, value in updates.get(device['id'], {}).items() if key != 'id'})
                        for device in snapshot['resources']['devices']])
        site_ids = {site['id'] for site in resource['sites']}
        if any(device['site_id'] not in site_ids for device in resource['devices'] + selected) or not permitted(resource, selected):
            raise ValueError('待复核方案不满足当前站址与工程容量约束')
        procurement = engineering_cost(resource, selected)
        reserve = reference.get('retuning_reserve', 0)
        plan = dict(selected=selected, updated_devices=updated, added_cost=procurement + reserve,
                    cost_breakdown=dict(procurement_and_sites=procurement, retuning_reserve=reserve,
                                        basis='设备与站址按当前资源核算，转向预留沿用冻结方案'),
                    search_history=[], evaluated_subsets=0, variant_count=0, search_cell=None,
                    method='冻结所选方案的设备、站址与挂载参数，使用当前模型逐体素重新计算',
                    optimality='本次执行固定方案复核，最优性需要独立搜索对照')
    else:
        plan = optimizer(scene, snapshot['resources'], task, request, snapshot['calibration'], **extra)
    calculation = evaluate(scene, plan['selected'], request, task, snapshot['calibration'],
                           reference=reference['selected'] if reference else None, updated=plan.get('updated_devices'), reference_updated=reference.get('updated_devices') if reference else None)
    if reference:
        calculation['reference'].update(job_id=reference['job_id'], selected=reference['selected'], updated_devices=reference.get('updated_devices', []), added_cost=reference.get('added_cost', engineering_cost(snapshot['resources'], reference['selected'])))
    stats = calculation['after']['stats']
    full_bounds = request.bounds == [0, scene['width'], 0, scene['height'], 30, scene['altitude_max']]
    full_focus = all(task['focus'][key] >= request.bounds[i] and task['focus'][other] <= request.bounds[i + 1]
                     for key, other, i in [('x0', 'x1', 0), ('y0', 'y1', 2)]) and task['focus']['z1'] <= request.bounds[5] and max(30, task['focus']['z0']) >= request.bounds[4]
    coverage_pass = stats['coverage'] is not None and stats['coverage'] >= .9
    focus_pass = stats['focus_coverage'] is not None and stats['focus_coverage'] >= .95
    resolution_pass = request.wide_cell <= 10 and request.focus_cell <= 2
    modalities = sorted({d['type'] for d in scene['devices'] + plan['selected'] if d['status'] == 'online' and d['type'] != 'ADSB' and (d['type'] != 'RF' or task['volume']['target_emitting'])})
    feasible = all(stats[key] is not None and stats[key] + 1e-12 >= task[target] for key, target in
                   [('coverage', 'coverage_target'), ('focus_coverage', 'focus_target'), ('precision_pass_rate', 'precision_rate_target')])
    low_rate = calculation['after']['bottleneck']['altitude'][0]['precise']
    low_pass = request.low_altitude_target is None or (low_rate is not None and low_rate >= request.low_altitude_target)
    feasible = feasible and low_pass and plan['added_cost'] <= task['budget'] + 1e-9
    return dict(low_altitude=dict(bounds=[30, 100], target=request.low_altitude_target, rate=low_rate, passed=low_pass if request.low_altitude_target is not None else None), **plan, **calculation, stats=stats, scene=scene, settings=request.model_dump(),
                feasible=feasible, simulation_kpi_pass=full_bounds and full_focus and resolution_pass and coverage_pass and focus_pass and len(modalities) >= 3,
                checks=dict(full_domain=full_bounds, full_focus=full_focus, resolution=resolution_pass,
                            coverage_90=coverage_pass, focus_95=focus_pass, modalities_3=len(modalities) >= 3),
                modalities=modalities, evidence_type='synthetic_model', input_digest=digest(snapshot),
                model_version=core.MODEL_VERSION, precision_threshold_m=task['volume']['precision_m'],
                scope='感知网络典型规划仿真数值验证；现场接入、科学模型和示范成果需相应证据',
                limitations=['合成场景与示例设备参数', '平面地形、轴对齐建筑遮挡，未建模绕射和多径',
                             '独立探测融合与简化损耗模型，CRLB 为理论精度下界', '有限搜索采样可能遗漏改进方案；判定采用全体素复核'],
                seconds=round(time.perf_counter() - started, 3))
