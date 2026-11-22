"""Proposal KPI evidence tied to immutable planning computations and field trials."""
import copy
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from . import jobs, store, planning_portal as portal
from .job_compute import provenance
from .task_validation import task_validation
from .planning_assessment import AssessmentRequest, geometry, variants, planned_devices
from .proposal_kpi import HISTORICAL, VERSION, published_indicators, recorded_checks

router = APIRouter()


def current_snapshot(task_id):
    task, scene, calibration = portal.inputs(task_id)
    return dict(task=task, scene=scene, calibration=calibration, resources=portal.resources(task['scene'], scene))


def is_stale(job):
    if job['snapshot']['provenance'] != provenance():
        return True
    current = current_snapshot(job['snapshot']['task']['id'])
    return any(current[key] != job['snapshot'][key] for key in current)


def compatible_reference(previous, current):
    """Adding unused candidate sites preserves the reference deployment and prices."""
    if any(previous.get(key) != current[key] for key in ('task', 'calibration')):
        return False
    scene_metadata = {'revision', 'input_source'}
    if {k: v for k, v in previous['scene'].items() if k not in scene_metadata} != {k: v for k, v in current['scene'].items() if k not in scene_metadata}:
        return False
    old, new = previous['resources'], current['resources']
    ignored = {'revision', 'source', 'sites'}
    if {k: v for k, v in old.items() if k not in ignored} != {k: v for k, v in new.items() if k not in ignored}:
        return False
    sites = {site['id']: site for site in new['sites']}
    return all(sites.get(site['id']) == site for site in old['sites'])


def assessment_title(snapshot):
    parameters = snapshot['parameters']
    if parameters.get('verify_reference'):
        return '当前模型完整网格复核'
    if not parameters.get('optimize', True):
        return '已有部署复核'
    if parameters.get('low_altitude_target') is not None:
        if any(site['id'].startswith('LOW-') for site in snapshot['resources']['sites']):
            return '低位站址与设备组合优化'
        return '低空精度优化'
    return '全域精度优化' if parameters.get('strategy') == 'precision' else '覆盖规划基准'


@router.get('/api/acceptance')
def acceptance_indicators():
    return published_indicators()


@router.get('/acceptance')
def page():
    return FileResponse(store.ROOT / 'static' / 'acceptance.html')


@router.post('/api/workflow/tasks/{task_id}/assessments', status_code=202)
def submit(task_id: str, request: AssessmentRequest):
    # A write reservation holds task, resources, scene and calibration at one revision.
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        snapshot = current_snapshot(task_id)
        try:
            geometry(request, snapshot['task'])
            if request.retune_existing and request.optimize and request.retuning_reserve > snapshot['task']['budget']:
                raise ValueError('转向调试预留金额超过任务预算')
            if request.strategy == 'precision':
                from .precision_planning import options
                options(snapshot['resources'], request)
            else:
                variants(snapshot['resources'], request)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if request.reference_job_id:
            reference = jobs.get(request.reference_job_id)
            if reference['kind'] != 'assessment' or reference['status'] != 'succeeded':
                raise HTTPException(422, '对照记录须为已完成的规划验算')
            if not compatible_reference(reference['snapshot'], snapshot):
                raise HTTPException(409, '对照记录的任务、场景、资源或标定与当前输入不一致')
            snapshot['reference'] = dict(job_id=reference['id'], selected=reference['result']['selected'],
                                        source_provenance=reference['snapshot']['provenance'],
                                        updated_devices=reference['result'].get('updated_devices', []),
                                        added_cost=reference['result']['added_cost'],
                                        retuning_reserve=reference['result'].get('cost_breakdown', {}).get('retuning_reserve', 0),
                                        scope='对照方案按本次模型、网格及输入重新计算全部体素')
        snapshot.update(schema_version=1, kind='assessment', parameters=request.model_dump(), provenance=provenance())
    return jobs.submit(snapshot['task']['scene'], 'assessment', request.model_dump(), snapshot=snapshot)


@router.get('/api/workflow/tasks/{task_id}/assessments')
def list_assessments(task_id: str):
    portal.read_record('task', task_id)
    with store.connection() as conn:
        rows = conn.execute("SELECT id,status,created_at,finished_at,error,snapshot,result FROM computation_jobs WHERE kind='assessment' ORDER BY created_at DESC,id DESC").fetchall()
    records = []
    for row in rows:
        snapshot = json.loads(row['snapshot'])
        if snapshot['task']['id'] != task_id:
            continue
        result = json.loads(row['result']) if row['result'] else {}
        records.append(dict(id=row['id'], status=row['status'], created_at=row['created_at'],
                            finished_at=row['finished_at'], error=row['error'], title=assessment_title(snapshot),
                            reference_id=snapshot.get('reference', {}).get('job_id'),
                            low_altitude=result.get('low_altitude'), feasible=result.get('feasible'),
                            cost=result.get('added_cost')))
    for index, record in enumerate(records):
        record['version'] = len(records) - index
    return records


@router.get('/api/workflow/precision-comparisons')
def precision_comparisons():
    with store.connection() as conn:
        rows = conn.execute("SELECT id,snapshot,result FROM computation_jobs WHERE kind='assessment' AND status='succeeded' ORDER BY created_at DESC").fetchall()
    result, seen = [], set()
    for row in rows:
        snapshot = json.loads(row['snapshot'])
        task = snapshot['task']
        if task['id'] in seen or snapshot['parameters'].get('strategy') != 'precision':
            continue
        calculation = json.loads(row['result'])
        if 'reference' not in calculation:
            continue
        seen.add(task['id'])
        result.append(dict(job_id=row['id'], task_id=task['id'], task_name=task['name'], scene=task['scene'],
                           before=calculation['reference']['stats']['precision_pass_rate'],
                           after=calculation['stats']['precision_pass_rate'], cost=calculation['added_cost'],
                           budget=task['budget'], feasible=calculation['feasible'],
                           low_before=calculation['reference'].get('bottleneck', {}).get('altitude', [{}])[0].get('precise'),
                           low_after=calculation['after'].get('bottleneck', {}).get('altitude', [{}])[0].get('precise'),
                           low_target=snapshot['parameters'].get('low_altitude_target'),
                           stale=is_stale(dict(snapshot=snapshot)),
                           coverage=calculation['stats']['coverage'], focus_coverage=calculation['stats']['focus_coverage'],
                           evaluated_cells=calculation['evaluated_cells']))
    return result


def evidence(job):
    plan_id = job['id']
    task_id = job['snapshot']['task']['id']
    trials = [t for t in portal.records('trial') if t['task_id'] == task_id and t.get('plan_id') == plan_id]
    trial_ids = {t['id'] for t in trials}
    runs = [r for r in portal.records('run') if r['trial_id'] in trial_ids]
    flight_records = [r for r in portal.records('flight-evidence') if r['assessment_id'] == job['id']]
    return dict(plan_id=plan_id if store.get(f'portal:plan:{plan_id}') else None,
                trials=[{k: t[k] for k in ('id', 'name', 'scene', 'kinds', 'snapshot_digest')} for t in trials],
                runs=[{k: r[k] for k in ('id', 'trial_id', 'name', 'provenance', 'model_version', 'snapshot_digest')} for r in runs],
                imported_field_runs=sum(r['provenance'] == 'field' for r in runs),
                flight_records=[{k: r[k] for k in ('id', 'vehicle_id', 'created_at', 'provenance', 'coordinate_alignment')} for r in flight_records],
                field_status='待核验现场原始记录、运行报告和第三方或用户证明')


def indicator_rows(job, linked):
    result = job['result']
    stats = result['stats']
    version, standard, checks = recorded_checks(result)
    def numeric(identifier, requirement, value, passed, research, page):
        return dict(id=identifier, requirement=requirement, value=value, kpi_version=version,
                    status='simulation_pass' if passed else 'simulation_fail', research=research, pdf_page=page)
    def historical(identifier, requirement, value, passed, research, page):
        return dict(id=identifier, requirement=requirement, value=value, kpi_version=HISTORICAL['version'],
                    status='historical_met' if passed else 'historical_unmet', decides_pass=False,
                    research=research, pdf_page=page)
    grid_value = f"{result['settings']['wide_cell']:g} m / {result['settings']['focus_cell']:g} m，{result['evaluated_cells']:,} 网格"
    domain_ready = checks['full_domain'] and checks['full_focus'] and checks['resolution']
    rows = [
        numeric('3.2-grid', f"广域 ≤{standard['wide_cell_m']:g} m / 重点区 ≤{standard['focus_cell_m']:g} m，完整评估任务空域",
                grid_value, domain_ready, 1, 18),
        numeric('3.2-modalities', f"典型仿真 ≥{standard['modalities']} 类异构感知设备", '、'.join(result['modalities']), checks['modalities_3'], 1, 18),
        numeric('5.3-wide', f"典型规划仿真空间覆盖 ≥{standard['coverage']:.0%}", stats['coverage'], checks['coverage'] and domain_ready, 3, 19),
        numeric('5.3-focus', f"典型规划仿真重点区覆盖 ≥{standard['focus_coverage']:.0%}", stats['focus_coverage'], checks['focus_coverage'] and domain_ready, 3, 19),
    ]
    if version == VERSION:
        label = HISTORICAL['label'] + '，不决定本次通过'
        rows[1:1] = [historical('3.2-grid-historical', f"{label}：广域 ≤{HISTORICAL['wide_cell_m']:g} m / 重点区 ≤{HISTORICAL['focus_cell_m']:g} m",
                                grid_value, checks['historical']['resolution'], 1, 18)]
        rows += [historical('5.3-wide-historical', f"{label}：空间覆盖 ≥{HISTORICAL['coverage']:.0%}",
                            stats['coverage'], checks['historical']['coverage'], 3, 19),
                 historical('5.3-focus-historical', f"{label}：重点区覆盖 ≥{HISTORICAL['focus_coverage']:.0%}",
                            stats['focus_coverage'], checks['historical']['focus_coverage'], 3, 19)]
    else:
        rows.insert(0, dict(id='kpi-version', requirement=f'本记录按旧指标版本 {version} 判定',
                            value=f'按正式申报书口径 {VERSION} 评价需重新提交计算，生成新记录', kpi_version=version,
                            status='pending_evidence', research=1, pdf_page=18))
    task = job['snapshot']['task']
    precision = stats.get('precision_pass_rate')
    rows.append(numeric('task-precision',
                        f"任务全域理论精度合格比例 ≥{task['precision_rate_target']:.0%}，误差界 ≤{result['precision_threshold_m']:g} m",
                        precision, precision is not None and precision >= task['precision_rate_target']
                        and checks['full_domain'] and checks['full_focus'] and checks['resolution'], 3, 19))
    low = result.get('low_altitude') or {}
    if low.get('target') is not None:
        rate = low.get('rate')
        bounds = result['settings']['bounds']
        full_low = bounds[:4] == [0, 2000, 0, 2000] and bounds[4] == 30 and bounds[5] >= 100
        rows.append(numeric('task-low-precision', f"任务30–100 m低空层理论精度合格比例 ≥{low['target']:.0%}",
                            rate, rate is not None and rate >= low['target'] and full_low
                            and checks['full_focus'] and checks['resolution'], 3, 19))
    s = standard
    functional = [
        ('2.3', f"平台支持 ≥{s['platform_devices']} 类异构感知设备", '软件定义 RADAR / EO / RF / IR / ADSB 共 5 类；真实设备接入 0 类，协议联调证据待补', 4, 18),
        ('2.4', f"≥{s['planning_parameters']} 类规划决策参数可配置", '站址、型号组合、挂高、朝向 4 类进入搜索，另有预算和工程容量约束', 3, 18),
        ('4.1-4.4', f"≥{s['interference_factors']} 种干扰衰减因素、≥{s['efficacy_metrics']} 类效能指标", '同频、通信、天气 3 种损耗与建筑遮挡；Pd / SINR / CRLB 3 类已计算，物理模型标定待补', 2, 19),
    ]
    rows.extend(dict(id=i, requirement=r, value=v, status='function_verified', kpi_version=version, research=t, pdf_page=p) for i, r, v, t, p in functional)
    pending = [
        ('1-report', '低空多模态感知网络规划体系报告', '已有计算证据导出，体系报告需汇编与评审', 1, 17),
        ('5.1-5.3', '站址初筛、双约束加密、设备与挂载联合优化', result['method'] + '；' + result['optimality'], 3, 19),
        ('platform', '在线标定、滚动优化、云边端协同运行', '任务、方案、试验和运行记录已关联；在线设备执行与持续运行需联调', 4, 17),
        ('demo-scenes', f"≥{s['demo_scenes']} 个现场示范场景", f"本方案关联 {len(linked['trials'])} 项试验，导入现场标记记录 {linked['imported_field_runs']} 份，待现场核验；合成场景只作示范准备证据", 5, 19),
        ('demo-tasks', f"≥{s['demo_tasks']} 个现场示范任务", '、'.join(sorted({k for t in linked['trials'] for k in t['kinds']})) or '待建立本方案示范试验', 5, 19),
        ('outputs', f"≥{s['papers']} 篇论文、≥{s['patents']} 项发明专利、≥{s['software']} 项软著", '待补成果及受理/授权凭证', 5, 20),
        ('training', f"≥{s['graduate_students']} 名研究生、≥{s['engineers']} 名技术骨干", '待补培养与培训记录', 5, 16),
    ]
    rows.extend(dict(id=i, requirement=r, value=v, status='pending_evidence', kpi_version=version, research=t, pdf_page=p) for i, r, v, t, p in pending)
    return rows


@router.get('/api/assessments/{job_id}')
def detail(job_id: str):
    job = jobs.get(job_id)
    if job['kind'] != 'assessment':
        raise HTTPException(422, '此记录为其他计算类型')
    job['stale'] = is_stale(job)
    job['evidence'] = evidence(job)
    if job['status'] == 'succeeded' and job['snapshot']['provenance'] == provenance():
        from .planning_visualization import clouds
        view = clouds(job)
        for stage in ('before', 'after', 'reference'):
            if stage not in view:
                continue
            job['result'][stage]['cloud'] = view.pop(stage)
        job['visualization'] = view
    job['indicators'] = indicator_rows(job, job['evidence']) if job['status'] == 'succeeded' else []
    job['task_validation'] = task_validation(job) if job['status'] == 'succeeded' else []
    job['proposal'] = dict(file='项目申报书-面向低空经济的多模态感知网络智能规划关键技术研究及示范应用.pdf', kpi_file_pages=[17, 18, 19, 20])
    return job


@router.get('/api/assessments/{job_id}/export')
def export(job_id: str):
    return JSONResponse(detail(job_id), headers={'Content-Disposition': f'attachment; filename="assessment-{job_id}.json"'})


@router.get('/api/assessments/{job_id}/flight-evidence/{record_id}')
def flight_evidence(job_id: str, record_id: str):
    record = portal.read_record('flight-evidence', record_id)
    if record['assessment_id'] != job_id:
        raise HTTPException(404, '运行记录不属于此规划验算')
    return JSONResponse(record, headers={'Content-Disposition': f'attachment; filename="flight-evidence-{record_id}.json"'})


@router.post('/api/assessments/{job_id}/plan')
def publish_assessment(job_id: str):
    job = jobs.get(job_id)
    if job['kind'] != 'assessment' or job['status'] != 'succeeded':
        raise HTTPException(409, '请先完成规划验算')
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT value FROM state WHERE key=?', (f'portal:plan:{job_id}',)).fetchone()
        if row:
            return json.loads(row[0])
        if is_stale(job):
            raise HTTPException(409, '任务、资源、标定或算法已更新，请重新验算')
        result, snapshot = job['result'], job['snapshot']
        task = snapshot['task']
        plan = {key: result[key] for key in ('selected', 'added_cost', 'stats', 'feasible', 'evaluated_subsets', 'method', 'scope', 'model_version')}
        for key in ('updated_devices', 'cost_breakdown', 'low_altitude'):
            if key in result:
                plan[key] = copy.deepcopy(result[key])
        plan.update(id=job_id, assessment_job_id=job_id, task_id=task['id'], task_name=task['name'], scene=task['scene'],
                    scene_revision=snapshot['scene']['revision'], created_at=job['finished_at'], status='evaluated',
                    before=result['before']['stats'], request={k: task[k] for k in portal.engineering.ConstrainedPlan.model_fields},
                    snapshot={k: copy.deepcopy(snapshot[k]) for k in ('task', 'scene', 'calibration', 'resources')},
                    fingerprint=portal.fingerprint(task, snapshot['scene'], snapshot['calibration']),
                    assessment_provenance=snapshot['provenance'], history=[dict(action='evaluated', time=job['finished_at'])])
        conn.execute('INSERT INTO state VALUES (?,?)', (f'portal:plan:{job_id}', json.dumps(plan, ensure_ascii=False, allow_nan=False)))
    return plan


@router.get('/api/assessments/{job_id}/handoff')
def handoff(job_id: str):
    job = detail(job_id)
    if job['status'] != 'succeeded':
        raise HTTPException(409, '计算尚未成功')
    result, snapshot = job['result'], job['snapshot']
    route = store.get(f'portal-route:{snapshot["task"]["id"]}')
    if route:
        route['matches_assessment'] = route.get('fingerprint') == portal.fingerprint(snapshot['task'], snapshot['scene'], snapshot['calibration'])
    data = dict(schema_version=1, task_id=snapshot['task']['id'], assessment_job_id=job_id,
                plan_id=job['evidence']['plan_id'], input_digest=result['input_digest'], stale=job['stale'],
                coordinate_frame='local_ENU_m', geographic_origin=None,
                coordinate_note='现场导入须提供测量坐标原点与坐标转换，local_ENU_m 为当前合成场景坐标',
                deployment=planned_devices(snapshot['scene'], result['selected'], result.get('updated_devices')),
                updated_devices=result.get('updated_devices', []), cost_breakdown=result.get('cost_breakdown'),
                evidence=job['evidence'], route=route,
                observation_import='使用关联示范试验的 template 与 runs 接口，观测、真值和设备事件分别保存',
                execution_status='规划交接包，现场执行状态由设备事件和实测记录提供')
    return JSONResponse(data, headers={'Content-Disposition': f'attachment; filename="planning-handoff-{job_id}.json"'})
