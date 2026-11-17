"""Joint network steering and siting refinement across local geometry plateaus."""
import copy

import numpy as np
from scipy.stats import qmc

from . import coverage_core as core
from .planning_assessment import engineering_cost, geometry, inside, permitted
from .precision_planning import options


def balanced_screen_indices(whole_count, low_count):
    """Keep power-of-two prefixes of both Sobol nets, without thinning either net."""
    return np.concatenate([np.arange(min(512, whole_count)),
                           whole_count + np.arange(min(512, low_count))])


def refine(scene, resource, task, request, plan, calibration=None):
    if not plan['selected']:
        return plan
    result = copy.deepcopy(plan)
    settings = core.Volume.model_validate(task['volume'])
    bounds = np.array(request.bounds)
    whole = qmc.scale(qmc.Sobol(3, scramble=True, seed=20261116).random_base2(13), bounds[::2], bounds[1::2])
    low_bounds = bounds.copy()
    low_bounds[4:6] = [30, 100]
    points = np.concatenate([whole, qmc.scale(qmc.Sobol(3, scramble=True, seed=20261117).random_base2(13), low_bounds[::2], low_bounds[1::2])])
    screen = balanced_screen_indices(len(whole), len(points)-len(whole))
    mutable = [d for d in resource['devices'] if d['status']=='online' and d['type']!='ADSB' and d['fov']<360]
    mutable_ids = {d['id'] for d in mutable}
    fixed = core.fields(dict(scene, devices=[d for d in scene['devices'] if d['id'] not in mutable_ids]), points, settings, calibration)
    valid = fixed['valid']
    whole_mask = valid & (np.arange(len(points)) < len(whole))
    focus_mask = whole_mask & inside(points, geometry(request, task)[0])
    low_mask = valid & (points[:,2] < 100)
    targets = np.array([task['coverage_target'], task['focus_target'], task['precision_rate_target'], min(1,request.low_altitude_target+.005)])
    updated = {d['id']: d for d in plan.get('updated_devices', [])}
    groups, initial = [], []

    def key(device):
        return (device['type'],device['x'],device['y'],device['z'],device['azimuth'],device['pitch'],device.get('model_id'))

    def cache(devices):
        rows, seen = [], set()
        for device in devices:
            if key(device) in seen:
                continue
            seen.add(key(device))
            field = core.fields(dict(scene,devices=[device]),points,settings,calibration)
            rows.append((device,field['pd'].astype(np.float32),field['fim'].astype(np.float32)))
        return rows

    for original in mutable:
        current = updated.get(original['id'],original)
        variants = [current] + [dict(original,azimuth=azimuth,pitch=pitch)
                   for azimuth in np.arange(0,360,22.5).tolist() for pitch in (-30,-15,0,15,30)]
        groups.append(cache(variants))
        initial.append(0)
    budget = task['budget'] - request.retuning_reserve
    pool = options(resource,request)
    pool = [d for d in pool if d['cost'] <= budget+1e-9]
    purchases = [(None, np.zeros(len(points), dtype=np.float32), np.zeros_like(fixed['fim'], dtype=np.float32))]
    purchases += cache(plan['selected'] + pool)
    lookup = {key(row[0]):i for i,row in enumerate(purchases) if row[0] is not None}
    for device in plan['selected']:
        groups.append(purchases)
        initial.append(lookup[key(device)])
    existing_count = len(mutable)
    minimum_cost = min((device['cost'] for device in pool), default=budget)
    slots = min(50-len(resource['devices']), int(budget/max(minimum_cost, .01)))
    for _ in range(max(0, slots-len(plan['selected']))):
        groups.append(purchases)
        initial.append(0)
    comparisons = 0

    def assess(genes, sample=slice(None)):
        nonlocal comparisons
        rows = [group[index] for group,index in zip(groups,genes)]
        selected = [row[0] for row in rows[existing_count:] if row[0] is not None]
        if len({key(d) for d in selected}) != len(selected) or engineering_cost(resource,selected)>budget+1e-9 or not permitted(resource,selected):
            return None
        miss, fim = 1-fixed['pd'][sample], fixed['fim'][sample].copy()
        for _,pd,information in rows:
            miss = miss*(1-pd[sample])
            fim += information[sample]
        eigen = np.linalg.eigvalsh(fim)
        observable = eigen[:,0]>1e-8
        error = np.full(len(miss),np.inf)
        error[observable] = np.sqrt((1/eigen[observable]).sum(axis=1))
        covered = 1-miss>=settings.pd_threshold
        precise = covered & (error<=settings.precision_m)
        rates = np.array([float(mask[area].mean()) if area.any() else 0 for mask,area in
                          [(covered,whole_mask[sample]),(covered,focus_mask[sample]),(precise,whole_mask[sample]),(precise,low_mask[sample])]])
        comparisons += 1
        loss = float(np.dot([10,4,4,3],np.maximum(0,targets-rates)))
        return loss, rates

    initial = tuple(initial)
    initial_score = assess(initial)
    if initial_score is None:
        return result
    population = [(initial_score[0],initial,initial_score[1])]
    best = population[0]
    # Start each catalogue type at a low site before filling the remaining budget.
    # This crosses the cost barrier created by a fully occupied cheap-device baseline.
    low_sites = {site['id'] for site in resource['sites'] if site['max_height'] <= 100}
    starters = sorted({row[0]['type'] for row in purchases if row[0] and row[0]['site_id'] in low_sites})
    result['search_alternatives'] = []
    for model_type in starters:
        genes = list(initial[:existing_count]) + [0]*(len(initial)-existing_count)
        for position in range(existing_count, len(genes)):
            candidates = []
            for index, row in enumerate(purchases[1:], start=1):
                if position == existing_count and (row[0]['type'] != model_type or row[0]['site_id'] not in low_sites):
                    continue
                trial = genes.copy()
                trial[position] = index
                candidate = assess(trial, screen)
                if candidate is not None:
                    candidates.append((candidate[0], -candidate[1][3], index))
            if not candidates:
                break
            genes[position] = min(candidates)[2]
        candidate = assess(genes)
        if candidate is not None:
            entry = candidate[0], tuple(genes), candidate[1]
            population.append(entry)
            selected = [purchases[index][0] for index in genes[existing_count:] if index]
            reserve = request.retuning_reserve if plan.get('updated_devices') else 0
            result['search_alternatives'].append(dict(
                start_model=model_type, selected=selected, updated_devices=plan.get('updated_devices', []),
                added_cost=engineering_cost(resource, selected)+reserve,
                low_site_ids=sorted({device['site_id'] for device in selected} & low_sites),
                rates=dict(zip(('coverage','focus_coverage','precision_pass_rate','low_altitude_rate'),candidate[1].tolist())),
                existing_constraints_pass=bool(np.all(candidate[1][:3]>=targets[:3])),
                evidence_type='search_sample', sample_count=len(points)))
            if np.all(candidate[1][:3] >= targets[:3]) and (not np.all(best[2][:3] >= targets[:3]) or candidate[1][3] > best[2][3]):
                best = entry
    random = np.random.default_rng(20261118)
    visited = {entry[1] for entry in population}
    for generation in range(300):
        candidates = list(population)
        for _ in range(64):
            parent = population[int(random.integers(len(population)))][1]
            genes = list(parent)
            count = min(len(genes),int(random.choice([1,2,3,4])))
            for position in random.choice(len(genes),size=count,replace=False):
                # Empty purchase slots permit two-for-one model changes and release site fees.
                if position >= existing_count and random.random() < .18:
                    genes[position] = 0
                else:
                    genes[position] = int(random.integers(len(groups[position])))
            genes = tuple(genes)
            if genes in visited:
                continue
            visited.add(genes)
            candidate = assess(genes)
            if candidate is None:
                continue
            loss, rates = candidate
            candidates.append((loss,genes,rates))
            if np.all(rates[:3]>=targets[:3]) and (not np.all(best[2][:3]>=targets[:3]) or rates[3] > best[2][3]):
                best = loss,genes,rates
                result['search_history'].append(dict(action='joint_exchange',device_id='joint-network',model_id='EO/RADAR',
                    height=None,azimuth=None,coverage=float(rates[0]),focus_coverage=float(rates[1]),
                    precision_pass_rate=float(rates[2]),low_altitude_rate=float(rates[3])))
        candidates.sort(key=lambda row:(row[0],-row[2][3]))
        population = candidates[:24]
        if best[2][3] >= targets[3] and np.all(best[2][:3]>=targets[:3]):
            break
    chosen = [group[index][0] for group,index in zip(groups,best[1])]
    original_by_id = {d['id']:d for d in resource['devices']}
    result['updated_devices'] = [d for d in chosen[:existing_count] if key(d)!=key(original_by_id[d['id']])]
    selected = []
    occupied = {d['id'] for d in resource['devices']+resource['candidates']}
    for index, device in enumerate(chosen[existing_count:]):
        if device is None:
            continue
        number=index
        while 'L-'+str(number) in occupied:
            number+=1
        identifier='L-'+str(number)
        occupied.add(identifier)
        selected.append(dict(device,id=identifier))
    result['selected']=selected
    procurement=engineering_cost(resource,selected)
    reserve=request.retuning_reserve if result['updated_devices'] else 0
    result['added_cost']=procurement+reserve
    result['cost_breakdown']=dict(procurement_and_sites=procurement,retuning_reserve=reserve,basis='既有设备转向调试预留款，实际费用需工程核价')
    result['search_rates']=dict(zip(('coverage','focus_coverage','precision_pass_rate','low_altitude_rate'),best[2].tolist()))
    result['evaluated_subsets']+=comparisons
    result['method']+='。既有转向、新增站址与可变采购数量的全网联合种群搜索，允许多台低价设备与少量高价设备组合互换'
    return result
