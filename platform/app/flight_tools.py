"""Read-only summaries of the in-research flight platform, fetched over its public GET API."""
import os
from statistics import fmean

import httpx

POLICIES = ('heft', 'local', 'edge', 'cloud')
TASK_FIELDS = ('id', 'application_id', 'scene', 'status', 'purpose', 'vehicle_id', 'created_at',
               'execution_source', 'validation_passed', 'validation_source')


def endpoints():
    flight = os.getenv('FLIGHT_BASE_URL', 'http://127.0.0.1:8772').rstrip('/')
    return flight, os.getenv('LOWAIR_BASE_URL', flight + '/service').rstrip('/')


def fetch(url):
    with httpx.Client(timeout=8, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def policy_summary(snapshots):
    rows = {}
    for policy in POLICIES:
        found = [c for s in snapshots for c in s.get('comparisons', []) if c.get('policy') == policy]
        done = [c for c in found if c.get('status') == 'ok']
        rows[policy] = dict(samples=len(found), ok=len(done),
                            mean_makespan_s=fmean(c['makespan_s'] for c in done) if done else None,
                            mean_energy_j=fmean(c['energy_j'] for c in done) if done else None,
                            deadline_met=sum(c.get('deadline_met', 0) for c in done),
                            task_count=sum(c.get('task_count', 0) for c in done))
    return rows


def algorithm_runs(limit=5):
    flight, _ = endpoints()
    listed = fetch(flight + '/api/algorithms/runs')['records']
    runs, assumptions = [], []
    for item in listed[:limit]:
        detail = fetch(f"{flight}/api/algorithms/runs/{item['id']}")
        assumptions = assumptions or detail.get('assumptions', [])
        request, route = detail.get('request', {}), detail.get('route', {})
        runs.append(dict(id=detail['id'], created_at=detail['created_at'], status=detail['status'],
                         scene=request.get('scene'), area_id=request.get('area_id'), planner=request.get('planner'),
                         seed=request.get('seed'), source=detail.get('source'), input_sha256=detail.get('input_sha256'),
                         route={k: route.get(k) for k in ('status', 'algorithm', 'distance_m', 'note')},
                         policies=policy_summary(detail.get('snapshots', [])),
                         source_path='/api/algorithms/runs/' + detail['id']))
    return dict(runs=runs, listed=len(listed), list_cap=30, assumptions=assumptions), '/api/algorithms/runs'


def compute_execution(limit=10):
    flight, _ = endpoints()
    current = fetch(flight + '/api/compute')
    records = fetch(f'{flight}/api/sitl/records?limit={limit}')['items']
    linked = []
    for record in records:
        batches = fetch(f"{flight}/api/compute/records/{record['id']}/batches")['batches']
        linked.append(dict(record_id=record['id'], business_task_id=record.get('business_task_id'),
                           status=record['status'], batches=len(batches)))
    stability = fetch(flight + '/api/stability').get('items', [])
    compute_runs = [r for r in stability if not r.get('dual_exercise')]
    return dict(current=dict(state=current.get('state'), source=current.get('source')),
                capabilities=CAPABILITIES,
                records=linked, records_with_batches=sum(1 for r in linked if r['batches']),
                stability=dict(total=len(stability), compute_regressions=len(compute_runs),
                               compute_passed=sum(r['status'] == 'passed' for r in compute_runs),
                               dual_exercises=len(stability) - len(compute_runs),
                               recent=[dict(id=r['id'], status=r['status'], kind='dual_exercise' if r.get('dual_exercise') else 'compute',
                                            passed_rounds=sum(1 for x in r.get('rounds', []) if x.get('status') == 'passed'),
                                            requested_rounds=r.get('requested_rounds'), started_at=r.get('started_at'))
                                       for r in stability[:5]])), '/api/compute'


def flight_records(limit=10):
    flight, _ = endpoints()
    data = fetch(f'{flight}/api/sitl/records?limit={limit}')
    return dict(items=data['items'], total=data['total'], limit=limit), '/api/sitl/records'


def service_tasks(limit=10):
    _, service = endpoints()
    performance = fetch(service + '/api/performance')
    tasks = sorted(fetch(service + '/api/tasks')['tasks'], key=lambda t: t.get('created_at') or 0, reverse=True)
    return dict(performance=performance, total=len(tasks), limit=limit,
                tasks=[{k: t.get(k) for k in TASK_FIELDS} for t in tasks[:limit]]), '/service/api/tasks'


CAPABILITIES = ['端、边缘、云节点与有向链路模型，五任务 DAG，单位 Gcycles/Mbit/s/W',
                'HEFT 卸载调度，与全本地、固定边缘、固定云端对比',
                '绑定 SITL 执行的本机多进程计算，支持节点下线、弱网、断链、重试和检查点迁移',
                '测得耗时回灌 HEFT 重新分配']


TOOLS = dict(flight_algorithm_runs=algorithm_runs, flight_compute=compute_execution,
             flight_records=flight_records, service_tasks=service_tasks)
LIMITATIONS = dict(
    flight_algorithm_runs='航路与端边云对比为仿真实验：独立静态采样点、合成障碍、预留带宽，不下发任务、不控制飞机。',
    flight_compute='capabilities 为代码已实现能力；执行为本机进程与链路仿真，不是跨主机部署。稳定性回归在隔离数据目录运行；主平台档案批次数为 0 只说明这些飞行未带计算执行。',
    flight_records='飞行档案来自 ArduPilot SITL 仿真，不是真机飞行或外场记录。',
    service_tasks='低空服务任务为平台业务记录，任务完成不等于空域审批或现场验收。')


def facts(name, data):
    """Platform-written statements; the model may explain them but not contradict them."""
    if name == 'flight_algorithm_runs':
        lines = [f"已归档航路算法实验 {data['listed']} 次（列表上限 {data['list_cap']}）。"]
        for run in data['runs'][:3]:
            ok = {k: v['mean_makespan_s'] for k, v in run['policies'].items() if v['mean_makespan_s'] is not None}
            best = min(ok, key=ok.get) if ok else None
            lines.append(f"实验 {run['id'][:8]}：{run['planner']} 航路 {run['route']['status']}，"
                         f"长度 {run['route']['distance_m'] or 0:.0f} m；平均完工时间最短的策略为 {best or '无可行策略'}。")
        return lines
    if name == 'flight_compute':
        st = data['stability']
        return ['代码已实现：' + '；'.join(data['capabilities']) + '。',
                f"计算稳定性回归 {st['compute_regressions']} 次，通过 {st['compute_passed']} 次；另有双机演练 {st['dual_exercises']} 次。",
                f"主平台最近 {len(data['records'])} 条飞行档案中，带计算批次的 {data['records_with_batches']} 条。",
                f"当前计算执行状态：{data['current']['state']}（无运行中的执行不代表功能未实现）。"]
    if name == 'flight_records':
        return [f"SITL 飞行档案共 {data['total']} 条，已读取最近 {len(data['items'])} 条。"]
    perf = data['performance']
    return [f"低空服务任务 {data['total']} 项：已完成 {perf.get('completed', 0)}，待执行 {perf.get('scheduled', 0)}，"
            f"飞行中 {perf.get('flying', 0)}，中止 {perf.get('aborted', 0)}。"]


def read(name):
    try:
        data, source = TOOLS[name]()
        return dict(available=True, facts=facts(name, data), **data), source, [LIMITATIONS[name]]
    except (httpx.HTTPError, KeyError, ValueError) as error:
        return (dict(available=False, error=type(error).__name__),
                endpoints()[1 if name == 'service_tasks' else 0],
                [LIMITATIONS[name], '在研平台接口不可达或返回格式不符，缺少证据，不能据此判断完成情况。'])
