import copy
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import verify_compute_chain  # noqa: E402

EXPERIMENT = 'a' * 32
BINDING = dict(runtime_id='run-1', record_id='rec-1', execution_id='exe-1', task_id='TASK-1', experiment_id=EXPERIMENT)
ROUTE = [dict(x=1, y=2, z=40), dict(x=5, y=6, z=40)]


def test_persisted_batch_carries_execution_binding():
    from app import compute_store
    compute_store.save(dict(BINDING, at=1.0, state='running'), dict(id='batch-1', status='completed'))
    assert compute_store.batches('rec-1') == [dict(id='batch-1', status='completed', **BINDING)]


def platform(batches):
    data = {
        '/api/sitl/records/rec-1': dict(business_task_id='TASK-1', execution_id='exe-1', algorithm=dict(experiment_id=EXPERIMENT),
                                        patrol=dict(route=[[p['x'], p['y'], p['z']] for p in ROUTE]),
                                        runtime_history=[dict(runtime_id='run-1', state='completed')]),
        '/api/sitl/records': dict(items=[dict(id='rec-1', status='completed')], total=1),
        f'/api/algorithms/runs/{EXPERIMENT}': dict(id=EXPERIMENT),
        '/service/api/tasks/TASK-1': dict(id='TASK-1', status='completed', application_id='APP-1', algorithm_run_id=EXPERIMENT, algorithm_input_sha256='s'),
        '/service/api/applications/APP-1': dict(id='APP-1', status='approved', algorithm_run_id=EXPERIMENT, algorithm_input_sha256='s', route=ROUTE),
        '/service/api/tasks/TASK-1/execution': dict(status='completed', execution_id='exe-1', record_id='rec-1'),
        '/api/compute/records/rec-1/batches': dict(batches=batches),
    }
    def handle(request):
        if request.method != 'GET':
            return httpx.Response(405)
        body = data.get(request.url.path)
        return httpx.Response(200, json=copy.deepcopy(body)) if body is not None else httpx.Response(404)
    return httpx.Client(base_url='http://platform', transport=httpx.MockTransport(handle))


def test_chain_passes_only_with_bound_completed_batch():
    report = verify_compute_chain.verify(platform([dict(id='batch-1', status='completed', **BINDING)]), 'rec-1')
    assert report['passed'], report
    assert report['chain'] == dict(experiment_id=EXPERIMENT, application_id='APP-1', task_id='TASK-1', execution_id='exe-1',
                                   record_id='rec-1', batch_ids=['batch-1'], batches_total=1)


def test_chain_rejects_missing_or_unbound_batches():
    assert not verify_compute_chain.verify(platform([]), 'rec-1')['passed']
    legacy = verify_compute_chain.verify(platform([dict(id='batch-1', status='completed')]), 'rec-1')
    assert not legacy['passed'] and '绑定一致' in legacy['error']
    other = verify_compute_chain.verify(platform([dict(id='batch-1', status='completed', **dict(BINDING, task_id='TASK-2'))]), 'rec-1')
    assert not other['passed']
