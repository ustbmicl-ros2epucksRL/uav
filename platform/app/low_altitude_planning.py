"""Low-layer planning with budgeted steering of existing directional sensors."""
import copy

import numpy as np
from scipy.stats import qmc

from . import coverage_core as core
from .planning_assessment import engineering_cost, geometry, inside, planned_devices
from .precision_planning import search as procurement_search


def _search_once(scene, resource, task, request, calibration=None, seed=None, seed_updated=None):
    budget = task['budget'] - request.retuning_reserve
    if budget < 0:
        raise ValueError('转向调试预留金额超过任务预算')
    planning_scene = dict(scene, devices=planned_devices(scene, [], seed_updated))
    plan = procurement_search(planning_scene, resource, dict(task, budget=budget), request, calibration, seed=seed)
    selected = copy.deepcopy(plan['selected'])
    devices = copy.deepcopy(planning_scene['devices'])
    settings = core.Volume.model_validate(task['volume'])
    bounds = np.array(request.bounds)
    whole = qmc.scale(qmc.Sobol(3, scramble=True, seed=20261116).random_base2(13), bounds[::2], bounds[1::2])
    low_bounds = bounds.copy()
    low_bounds[4:6] = [30, 100]
    low = qmc.scale(qmc.Sobol(3, scramble=True, seed=20261117).random_base2(13), low_bounds[::2], low_bounds[1::2])
    points = np.concatenate([whole, low])
    focus_bounds = geometry(request, task)[0]
    targets = np.array([task['coverage_target'], task['focus_target'], task['precision_rate_target']])

    def rates(field):
        valid = field['valid']
        global_mask = valid & (np.arange(len(points)) < len(whole))
        focus_mask = global_mask & inside(points, focus_bounds)
        low_mask = valid & (points[:, 2] < 100)
        covered = field['pd'] >= settings.pd_threshold
        precise = covered & field['observable'] & (field['precision_m'] <= settings.precision_m)
        return [float(mask[region].mean()) if region.any() else 0 for mask, region in
                [(covered, global_mask), (covered, focus_mask), (precise, global_mask), (precise, low_mask)]]

    network = devices + selected
    current = rates(core.fields(dict(scene, devices=network), points, settings, calibration))
    existing_count = len(devices)
    for iteration in range(3):
        changed = False
        for index, original in enumerate(list(network)):
            if original['status'] != 'online' or original['type'] == 'ADSB' or original['fov'] == 360:
                continue
            remainder = network[:index] + network[index + 1:]
            initial = core.fields(dict(scene, devices=remainder), points, settings, calibration)
            azimuths = np.arange(0, 360, 22.5).tolist() if index < existing_count else [(original['azimuth'] + offset) % 360 for offset in (-22.5, -11.25, 0, 11.25, 22.5)]
            best = None
            for azimuth in azimuths:
                for pitch in (-30, -15, 0, 15, 30):
                    device = dict(original, azimuth=azimuth, pitch=pitch)
                    field = core.fields(dict(scene, devices=[device]), points, settings, calibration, initial=initial)
                    candidate = rates(field)
                    plan['evaluated_subsets'] += 1
                    # Preserve all existing targets and gradually retain up to 0.3 pp of coverage margin.
                    minimum = targets.copy()
                    minimum[0] = max(targets[0], min(current[0], targets[0] + .003))
                    minimum[2] = max(targets[2], min(current[2], targets[2] + .003))
                    if any(value + 1e-12 < target for value, target in zip(candidate[:3], minimum)):
                        continue
                    rank = (candidate[3], candidate[2], candidate[0])
                    if rank <= (current[3], current[2], current[0]):
                        continue
                    if best is None or rank > best[0]:
                        best = rank, device, candidate
            if best:
                _, device, current = best
                network[index] = device
                changed = True
                plan['search_history'].append(dict(action='retune_existing' if index < existing_count else 'mount',
                    device_id=device['id'], model_id=device.get('model_id', device['type']), height=device['z'],
                    azimuth=device['azimuth'], pitch=device['pitch'], coverage=current[0], focus_coverage=current[1],
                    precision_pass_rate=current[2], low_altitude_rate=current[3]))
        if not changed:
            break
    metadata = {d['id']: d for d in resource['devices']}
    updated = [dict(metadata[d['id']], azimuth=d['azimuth'], pitch=d['pitch'])
               for d, original in zip(network[:existing_count], scene['devices'])
               if d['azimuth'] != original['azimuth'] or d['pitch'] != original['pitch']]
    selected = network[existing_count:]
    procurement = engineering_cost(resource, selected)
    reserve = request.retuning_reserve if updated else 0
    plan.update(selected=selected, updated_devices=updated, added_cost=procurement + reserve,
                cost_breakdown=dict(procurement_and_sites=procurement, retuning_reserve=reserve,
                                    basis='既有设备转向调试预留款，实际费用需工程核价'),
                search_rates=dict(zip(('coverage', 'focus_coverage', 'precision_pass_rate', 'low_altitude_rate'), current)))
    plan['method'] += '。既有定向设备和新增设备朝向/俯仰交替细化，保留全域覆盖、重点区覆盖和全域精度约束'
    plan['optimality'] = '有限候选与局部交替搜索，全局最优性待验证。转向调试采用预留预算'
    return plan


def search(scene, resource, task, request, calibration=None, seed=None, seed_updated=None):
    best = _search_once(scene, resource, task, request, calibration, seed, seed_updated)
    total = best['evaluated_subsets']
    rounds = 1
    if best['search_rates']['low_altitude_rate'] < min(1, request.low_altitude_target + .005):
        from .low_altitude_joint import refine
        best = refine(scene, resource, task, request, best, calibration)
        total = best['evaluated_subsets']
        rounds += 1
    best['evaluated_subsets'] = total
    best['optimization_rounds'] = rounds
    best['method'] += f'。完成 {rounds} 轮站址组合与既有转向交替搜索'
    return best
