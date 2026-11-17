#!/usr/bin/env python3
"""Compare three saved baseline deployments through the running assessment API."""
import argparse
import json
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8896/network')
    parser.add_argument('--baselines', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--configuration', type=Path, help='各场景的挂高与初始朝向 JSON')
    parser.add_argument('--scenes', nargs='+', choices=['urban', 'corridor', 'electromagnetic'], default=['urban', 'corridor', 'electromagnetic'])
    parser.add_argument('--resume', action='store_true', help='继续输出目录 jobs.json 中的计算记录')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.base_url.rstrip('/') + '/api/', timeout=120, trust_env=False) as client:
        results = []
        if args.resume:
            pending = json.loads((args.output / 'jobs.json').read_text())
        else:
            pending = {}
            configuration = json.loads(args.configuration.read_text()) if args.configuration else {}
            for scene in dict.fromkeys(args.scenes):
                baseline = json.loads((args.baselines / (scene + '.json')).read_text())
                task = baseline['snapshot']['task']
                parameters = dict(strategy='precision', reference_job_id=baseline['id'],
                                  heights=[125, 170, 230], azimuths=list(range(0, 360, 45)))
                parameters.update(configuration.get(scene, {}))
                response = client.post(f'workflow/tasks/{task["id"]}/assessments', json=parameters)
                response.raise_for_status()
                pending[response.json()['id']] = scene
            (args.output / 'jobs.json').write_text(json.dumps(pending, indent=2))
        observed = {}
        deadline = time.monotonic() + 7200
        while pending:
            if time.monotonic() > deadline:
                raise TimeoutError('验算超过两小时，请检查服务中的计算记录')
            for identifier, scene in list(pending.items()):
                response = client.get('jobs/' + identifier)
                response.raise_for_status()
                job = response.json()
                if job['status'] != observed.get(identifier):
                    print(scene, identifier, job['status'], flush=True)
                    observed[identifier] = job['status']
                if job['status'] not in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                    continue
                if job['status'] != 'succeeded':
                    raise RuntimeError(job.get('error') or job['status'])
                response = client.post('assessments/' + identifier + '/plan')
                response.raise_for_status()
                response = client.get('assessments/' + identifier + '/export')
                response.raise_for_status()
                job = response.json()
                (args.output / (scene + '.json')).write_text(json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False))
                result = job['result']
                summary = dict(scene=scene, task_id=job['snapshot']['task']['id'], job_id=identifier,
                               before=result['reference']['stats'], after=result['stats'],
                               before_cost=result['reference']['added_cost'], added_cost=result['added_cost'],
                               low_altitude=result.get('low_altitude'), cost_breakdown=result.get('cost_breakdown'),
                               updated_devices=result.get('updated_devices', []),
                               feasible=result['feasible'], evaluated_cells=result['evaluated_cells'], seconds=result['seconds'],
                               before_bottleneck=result['reference']['bottleneck'], after_bottleneck=result['after']['bottleneck'])
                results.append(summary)
                (args.output / 'summary.json').write_text(json.dumps(dict(evidence_type='synthetic_model', budget=50, precision_threshold_m=30,
                    target=.8, results=results), ensure_ascii=False, indent=2, allow_nan=False))
                print(json.dumps({k: v for k, v in summary.items() if 'bottleneck' not in k}, ensure_ascii=False), flush=True)
                del pending[identifier]
            if pending:
                time.sleep(10)


if __name__ == '__main__':
    main()
