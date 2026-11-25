"""Read-only acceptance of one business record, from algorithm experiment to persisted compute batches.

Only GET requests are issued. The chain is walked from the flight archive and every link is
re-read from the service that owns it, so a passing flight script alone is never sufficient.
"""
import argparse
import json
import sys
from pathlib import Path
import httpx


class ChainError(AssertionError):
    pass


def verify(client, record_id=None, task_id=None):
    checks = []

    def get(path):
        response = client.get(path)
        if response.status_code != 200:
            raise ChainError(f'GET {path} -> {response.status_code}')
        return response.json()

    def check(name, ok, detail=''):
        checks.append(dict(check=name, passed=bool(ok), detail=detail))
        if not ok:
            raise ChainError(name + (': ' + detail if detail else ''))

    report = dict(checks=checks, chain={})
    try:
        if not record_id:
            check('需要飞行档案或业务任务编号', task_id)
            items = get('/api/sitl/records?limit=1&task_id=' + task_id)['items']
            check('业务任务存在飞行档案', items, task_id)
            record_id = items[0]['id']
        archive = get('/api/sitl/records/' + record_id)
        experiment_id = (archive.get('algorithm') or {}).get('experiment_id')
        task_id = task_id or archive.get('business_task_id')
        execution_id = archive.get('execution_id')
        check('档案带业务任务、执行与算法实验编号', task_id and execution_id and experiment_id,
              f'task={task_id} execution={execution_id} experiment={experiment_id}')
        check('档案业务任务一致', archive.get('business_task_id') == task_id, str(archive.get('business_task_id')))

        experiment = get('/api/algorithms/runs/' + experiment_id)
        check('算法实验存在', experiment.get('id') == experiment_id, experiment_id)

        task = get('/service/api/tasks/' + task_id)
        check('业务任务已完成', task.get('status') == 'completed', str(task.get('status')))
        check('业务任务引用同一算法实验', task.get('algorithm_run_id') == experiment_id, str(task.get('algorithm_run_id')))
        application_id = task.get('application_id')
        application = get('/service/api/applications/' + application_id)
        check('申请已审批', application.get('status') == 'approved', str(application.get('status')))
        check('申请引用同一算法实验', application.get('algorithm_run_id') == experiment_id
              and application.get('algorithm_input_sha256') == task.get('algorithm_input_sha256'))
        route = [[p['x'], p['y'], p['z']] for p in application['route']]
        check('档案航线即审批航线', (archive.get('patrol') or {}).get('route') == route)

        execution = get(f'/service/api/tasks/{task_id}/execution')
        check('SITL 执行已完成', execution.get('status') == 'completed', str(execution.get('status')))
        check('执行编号一致', execution.get('execution_id') == execution_id, str(execution.get('execution_id')))
        check('执行最新档案即本档案', execution.get('record_id') == record_id, str(execution.get('record_id')))
        listed = get('/api/sitl/records?limit=100&task_id=' + task_id)['items']
        status = next((r['status'] for r in listed if r['id'] == record_id), None)
        check('飞行档案已完成', status == 'completed', str(status))

        batches = get(f'/api/compute/records/{record_id}/batches')['batches']
        binding = dict(record_id=record_id, execution_id=execution_id, task_id=task_id, experiment_id=experiment_id)
        bound = [b for b in batches if all(b.get(k) == v for k, v in binding.items()) and b.get('runtime_id')]
        completed = [b for b in bound if b.get('status') == 'completed']
        check('至少一个已落库且绑定一致的完成批次', completed,
              f'{len(batches)} 个批次，{len(bound)} 个带一致绑定，{len(completed)} 个完成')
        runtimes = {h.get('runtime_id') for h in archive.get('runtime_history', [])}
        check('批次所属运行在档案运行历史中', {b['runtime_id'] for b in completed} <= runtimes)

        again = get('/api/sitl/records/' + record_id)
        same = all(again.get(k) == archive.get(k) for k in ('business_task_id', 'execution_id')) \
            and (again.get('algorithm') or {}).get('experiment_id') == experiment_id
        check('重新读取档案关联仍在', same)
        again_batches = {b['id'] for b in get(f'/api/compute/records/{record_id}/batches')['batches']}
        check('重新读取批次仍在', {b['id'] for b in completed} <= again_batches)

        report['chain'] = dict(experiment_id=experiment_id, application_id=application_id, task_id=task_id,
                               execution_id=execution_id, record_id=record_id,
                               batch_ids=[b['id'] for b in completed], batches_total=len(batches))
        report['passed'] = True
    except ChainError as exc:
        report.update(passed=False, error=str(exc))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8894')
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--record', help='飞行档案编号')
    target.add_argument('--task', help='业务任务编号（取其最新档案）')
    parser.add_argument('--report', help='写出 JSON 报告')
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, trust_env=False, timeout=30) as client:
        report = verify(client, args.record, args.task)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(text)
    for item in report['checks']:
        print(('PASS ' if item['passed'] else 'FAIL ') + item['check'] + (' — ' + item['detail'] if item['detail'] else ''))
    print(json.dumps(report.get('chain') or {}, ensure_ascii=False))
    sys.exit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
