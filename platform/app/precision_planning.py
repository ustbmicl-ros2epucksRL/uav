"""Capacity-constrained new procurement with site relocation and mount refinement."""
import copy

import numpy as np
from scipy.stats import qmc

from . import coverage_core as core
from .planning_assessment import engineering_cost, geometry, inside, permitted


def options(resource, request):
    models = [m for m in resource['models'] if m['type'] != 'ADSB' and (not request.model_ids or m['id'] in request.model_ids)]
    if set(request.model_ids) - {m['id'] for m in models}:
        raise ValueError('所选型号缺失或为合作报文型号')
    occupied = {d['id'] for d in resource['devices'] + resource['candidates']}
    choices = []
    for site in resource['sites']:
        for model in models:
            for height in sorted(set(request.heights)):
                if not site['min_height'] <= height <= site['max_height']:
                    continue
                for azimuth in ([0] if model['fov'] == 360 else sorted(set(request.azimuths))):
                    number = len(choices)
                    while 'P-' + str(number) in occupied:
                        number += 1
                    identifier = 'P-' + str(number)
                    device = dict(id=identifier, name=(site['name'] + ' / ' + model['name'])[:80],
                                  site_id=site['id'], model_id=model['id'], asset_code='', status='online',
                                  x=site['x'], y=site['y'], z=height, azimuth=azimuth, pitch=0,
                                  **{k: model[k] for k in ('type', 'range', 'fov', 'vertical_fov', 'cost', 'power_w', 'bandwidth_mbps')})
                    if permitted(resource, [device]):
                        choices.append(device)
                        occupied.add(identifier)
    if len(choices) > 1600:
        raise ValueError('精度优化选项超过 1600 个，请减少挂高、朝向或型号')
    return choices


def search(scene, resource, task, request, calibration=None, seed=None):
    if not permitted(resource, []):
        raise ValueError('已有部署超出站址工程容量或挂高范围')
    settings = core.Volume.model_validate(task['volume'])
    bounds = np.array(request.bounds)
    points = qmc.scale(qmc.Sobol(3, scramble=True, seed=20261116).random_base2(13), bounds[::2], bounds[1::2])
    whole_count = len(points)
    if request.low_altitude_target is not None:
        low_bounds = bounds.copy()
        low_bounds[4:6] = [30, 100]
        low_points = qmc.scale(qmc.Sobol(3, scramble=True, seed=20261117).random_base2(13), low_bounds[::2], low_bounds[1::2])
        points = np.concatenate([points, low_points])
    base = core.fields(scene, points, settings, calibration)
    valid = base['valid'] & (np.arange(len(points)) < whole_count)
    low = base['valid'] & (points[:, 2] >= 30) & (points[:, 2] < 100)
    focus = valid & inside(points, geometry(request, task)[0])
    targets = np.array([task['coverage_target'], task['focus_target'], task['precision_rate_target']])
    # Modest search margins reduce sensitivity to the independent fine-grid evaluation.
    search_targets = np.minimum(1, targets + [0, 0, .015])
    low_mode = request.low_altitude_target is not None
    if low_mode:
        search_targets = np.append(search_targets, min(1, request.low_altitude_target + .01))
    weights_main = [4, 2, 2, 4] if low_mode else [4, 2, 1]
    comparisons = 0

    def score(pd, fim, sample=slice(None)):
        nonlocal comparisons
        comparisons += 1
        pd, fim = pd[sample], fim[sample]
        mask, focus_mask = valid[sample], focus[sample]
        eigen = np.linalg.eigvalsh(fim)
        observable = eigen[:, 0] > 1e-8
        error = np.full(len(pd), np.inf)
        error[observable] = np.sqrt((1 / eigen[observable]).sum(axis=1))
        covered = pd >= settings.pd_threshold
        precise = covered & (error <= settings.precision_m)
        rates = np.array([covered[mask].mean() if mask.any() else 0,
                          covered[focus_mask].mean() if focus_mask.any() else 0,
                          precise[mask].mean() if mask.any() else 0])
        if low_mode:
            low_mask = low[sample]
            rates = np.append(rates, precise[low_mask].mean() if low_mask.any() else 0)
        loss = float(np.dot(weights_main, np.maximum(0, search_targets - rates)))
        smooth = float(np.minimum(1, settings.precision_m / np.maximum(error[mask], 1)).mean()) if mask.any() else 0
        return loss, smooth, rates.tolist()

    choices = options(resource, request) if request.optimize else []
    seed_devices = []
    if seed and request.optimize:
        occupied = {d['id'] for d in choices + resource['devices'] + resource['candidates']}
        for index, original in enumerate(seed):
            if original.get('asset_code'):
                continue
            if request.model_ids and original['model_id'] not in request.model_ids:
                continue
            number = index
            while 'W-' + str(number) in occupied:
                number += 1
            identifier = 'W-' + str(number)
            occupied.add(identifier)
            seed_devices.append(dict(original, id=identifier))
        if engineering_cost(resource, seed_devices) > task['budget'] or not permitted(resource, seed_devices):
            seed_devices = []
        choices += seed_devices
    cache = []
    for device in choices:
        field = core.fields(dict(scene, devices=[device]), points, settings, calibration)
        cache.append((device, field['pd'].astype(np.float32), field['fim'].astype(np.float32)))
    selected = []
    pd, fim = base['pd'], base['fim']
    current = score(pd, fim)
    history = []
    best_feasible = None

    def remember(devices, candidate):
        nonlocal best_feasible
        if low_mode and all(rate >= target for rate, target in zip(candidate[2][:3], targets)):
            if best_feasible is None or candidate[2][3] > best_feasible[1][2][3]:
                best_feasible = copy.deepcopy(devices), candidate

    def record(action, device):
        remember(selected, current)
        history.append(dict(action=action, device_id=device['id'], model_id=device['model_id'],
                            site_id=device['site_id'], height=device['z'], azimuth=device['azimuth'], pitch=device['pitch'],
                            cost=engineering_cost(resource, selected), coverage=current[2][0],
                            focus_coverage=current[2][1], precision_pass_rate=current[2][2], low_altitude_rate=current[2][3] if low_mode else None))

    # Different greedy priorities avoid committing every start to early coverage gains.
    best_start = None
    priorities = ([4, 2, 2, 4], [2, 1, 2, 6], [1, 1, 1, 8]) if low_mode else ([4, 2, 1], [1, 1, 2], [.25, .5, 1])
    for weights in priorities:
        selected, history = [], []
        pd, fim = base['pd'], base['fim']
        current = score(pd, fim)
        while len(selected) + len(resource['devices']) < 50 and current[0] > 1e-10:
            best = None
            cost = engineering_cost(resource, selected)
            ids = {d['id'] for d in selected}
            for device, contribution, information in cache:
                trial = selected + [device]
                total = engineering_cost(resource, trial)
                if device['id'] in ids or total > task['budget'] + 1e-9 or not permitted(resource, trial):
                    continue
                new_pd, new_fim = 1 - (1 - pd) * (1 - contribution), fim + information
                candidate = score(new_pd, new_fim)
                current_loss = float(np.dot(weights, np.maximum(0, search_targets - current[2])))
                candidate_loss = float(np.dot(weights, np.maximum(0, search_targets - candidate[2])))
                gain = current_loss - candidate_loss + .03 * (candidate[1] - current[1])
                if gain <= 1e-12:
                    continue
                rank = gain / max(.01, total - cost)
                if best is None or rank > best[0]:
                    best = (rank, device, new_pd, new_fim, candidate)
            if best is None:
                break
            _, device, pd, fim, current = best
            selected.append(device)
            record('add', device)
    
        if best_start is None or current[0] < best_start[0][0]:
            best_start = current, selected, history
    if seed_devices:
        field = core.fields(dict(scene, devices=scene['devices'] + seed_devices), points, settings, calibration)
        candidate = score(field['pd'], field['fim'])
        remember(seed_devices, candidate)
        if candidate[0] < best_start[0][0]:
            best_start = candidate, seed_devices, []
    current, selected, history = best_start

    # Relocate or change the model of each new device against the entire remaining network.
    for _ in range(3):
        changed = False
        for previous in list(selected):
            remainder = [d for d in selected if d['id'] != previous['id']]
            field = core.fields(dict(scene, devices=scene['devices'] + remainder), points, settings, calibration)
            best = None
            ids = {d['id'] for d in remainder}
            for device, contribution, information in cache:
                trial = remainder + [device]
                if device['id'] in ids or engineering_cost(resource, trial) > task['budget'] + 1e-9 or not permitted(resource, trial):
                    continue
                candidate = score(1 - (1 - field['pd']) * (1 - contribution), field['fim'] + information)
                if candidate[0] < current[0] - 1e-10 and (best is None or candidate[0] < best[1][0]):
                    best = device, candidate
            if best:
                device, current = best
                selected = remainder + [device]
                record('relocate', device)
                changed = True
        if not changed:
            break

    # Coordinated swaps escape bearing-only geometry plateaus that defeat one-device moves.
    # A bounded evolutionary population preserves budget and capacity for every accepted trial.
    if len(selected) >= 2 and current[0] > 1e-10:
        random = np.random.default_rng(20261116)
        by_id = {row[0]['id']: index for index, row in enumerate(cache)}
        initial = tuple(by_id[d['id']] for d in selected)
        population = [(current[0], initial, current)]
        visited = {tuple(sorted(initial))}
        for generation in range(160):
            candidates = list(population)
            for _ in range(32):
                parent = population[int(random.integers(len(population)))][1]
                genes = list(parent)
                changed = random.choice(len(genes), size=min(len(genes), int(random.choice([1, 2, 3, 4]))), replace=False)
                for position in changed:
                    remainder = [cache[index][0] for offset, index in enumerate(genes) if offset != position]
                    allowance = task['budget'] - engineering_cost(resource, remainder)
                    affordable = [index for index, row in enumerate(cache) if row[0]['cost'] <= allowance + 1e-9 and index not in genes]
                    if affordable:
                        genes[position] = int(random.choice(affordable))
                key = tuple(sorted(genes))
                devices = [cache[index][0] for index in genes]
                if key in visited or engineering_cost(resource, devices) > task['budget'] + 1e-9 or not permitted(resource, devices):
                    continue
                visited.add(key)
                miss, information = 1 - base['pd'], base['fim'].copy()
                for index in genes:
                    miss = miss * (1 - cache[index][1])
                    information += cache[index][2]
                candidate = score(1 - miss, information)
                candidates.append((candidate[0], tuple(genes), candidate))
            candidates.sort(key=lambda row: (row[0], -row[2][1]))
            population = candidates[:12]
            if population[0][0] < current[0] - 1e-10:
                _, genes, current = population[0]
                selected = [cache[index][0] for index in genes]
                record('joint_exchange', selected[0])
            if current[0] <= 1e-10:
                break

    # Refine azimuth and pitch without changing the catalogue's FoV, range, noise or price.
    for _ in range(2):
        changed = False
        for previous in list(selected):
            if previous['fov'] == 360:
                continue
            remainder = [d for d in selected if d['id'] != previous['id']]
            field = core.fields(dict(scene, devices=scene['devices'] + remainder), points, settings, calibration)
            best = None
            for offset in (-22.5, -11.25, 0, 11.25, 22.5):
                for pitch in ((-30, -15, 0, 15) if low_mode else (-15, 0, 15)):
                    device = dict(previous, azimuth=(previous['azimuth'] + offset) % 360, pitch=pitch)
                    contribution = core.fields(dict(scene, devices=[device]), points, settings, calibration)
                    candidate = score(1 - (1 - field['pd']) * (1 - contribution['pd']), field['fim'] + contribution['fim'])
                    if candidate[0] < current[0] - 1e-10 and (best is None or candidate[0] < best[1][0]):
                        best = device, candidate
            if best:
                device, current = best
                selected = remainder + [device]
                record('mount', device)
                changed = True
        if not changed:
            break
    if best_feasible is not None:
        selected, current = best_feasible
        if selected:
            record('feasible_best', selected[0])
    return dict(selected=copy.deepcopy(selected), added_cost=engineering_cost(resource, selected), search_history=history,
                evaluated_subsets=comparisons, variant_count=len(choices), search_cell=None, search_samples=len(points),
                search_rates=dict(zip(('coverage', 'focus_coverage', 'precision_pass_rate', 'low_altitude_rate'), current[2])),
                method=('低空 30–100 m 分层加密与精度约束；' if low_mode else '') + '预算与工程容量约束下新增采购；多起点全站址型号组合、单台及多设备协同跨站替换、挂高与连续局部朝向/俯仰优化；Sobol 空间采样搜索与全体素独立复核',
                optimality='有限候选启发式搜索，全局最优性待验证；已有实物设备保持原部署')
