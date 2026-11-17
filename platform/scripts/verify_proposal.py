#!/usr/bin/env python3
"""Reproduce the three default proposal benchmarks through public task/job APIs."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    data = args.data_dir or Path(tempfile.mkdtemp(prefix='sensing-proposal-verification-'))
    os.environ['SENSE_DATA_DIR'] = str(data.resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient
    from app.main import app
    from app.scenes import SCENES
    from app.jobs import TERMINAL

    args.output.mkdir(parents=True, exist_ok=True)
    print(json.dumps({'data_dir': str(data.resolve()), 'output': str(args.output.resolve())}), flush=True)
    results = []
    with TestClient(app) as client:
        pending = {}
        for scene in SCENES:
            response = client.post('/api/workflow/tasks', json={'name': '申请书指标基准 · ' + scene['name'], 'scene': scene['id']})
            response.raise_for_status()
            task = response.json()
            response = client.post(f'/api/workflow/tasks/{task["id"]}/assessments', json={})
            response.raise_for_status()
            pending[response.json()['id']] = scene
        observed = {}
        deadline = time.monotonic() + 3600
        while pending:
            if time.monotonic() > deadline:
                raise TimeoutError('三场景验证超过一小时，请检查计算记录')
            for identifier, scene in list(pending.items()):
                job = client.get('/api/assessments/' + identifier).json()
                if observed.get(identifier) != job['status']:
                    print(json.dumps({'scene': scene['id'], 'job': identifier, 'status': job['status']}, ensure_ascii=False), flush=True)
                    observed[identifier] = job['status']
                if job['status'] not in TERMINAL:
                    continue
                if job['status'] == 'succeeded':
                    response = client.post('/api/assessments/' + identifier + '/plan')
                    response.raise_for_status()
                    job = client.get('/api/assessments/' + identifier).json()
                (args.output / (scene['id'] + '.json')).write_text(json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False))
                result = job.get('result') or {}
                summary = dict(scene=scene['id'], name=scene['name'], job_id=identifier, task_id=job['snapshot']['task']['id'], status=job['status'],
                               coverage=result.get('stats', {}).get('coverage'), focus_coverage=result.get('stats', {}).get('focus_coverage'),
                               precision_pass_rate=result.get('stats', {}).get('precision_pass_rate'), added_cost=result.get('added_cost'),
                               simulation_kpi_pass=result.get('simulation_kpi_pass'), task_constraints_pass=result.get('feasible'),
                               evaluated_cells=result.get('evaluated_cells'), seconds=result.get('seconds'), error=job['error'])
                results.append(summary)
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                del pending[identifier]
            if pending:
                time.sleep(2)
    summary = dict(data_dir=str(data.resolve()), evidence_type='synthetic_model', budget=50,
                   wide_cell_m=10, focus_cell_m=2, results=results,
                   all_simulation_kpis_pass=all(row['simulation_kpi_pass'] for row in results),
                   field_acceptance='pending_evidence')
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    if any(row['status'] != 'succeeded' for row in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
