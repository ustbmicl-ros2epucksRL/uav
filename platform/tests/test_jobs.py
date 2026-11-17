import json
import sqlite3
import time

import pytest
from app import jobs, store
from app.job_compute import provenance
from test_platform import client

PARAMETERS = {'bounds': [700, 740, 700, 740, 120, 140], 'cell': 20}


def wait(client, job_id, desired=jobs.TERMINAL):
    end = time.monotonic() + 25
    while time.monotonic() < end:
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] in desired:
            return job
        time.sleep(.05)
    pytest.fail(f'task did not reach {desired}: {job}')


def submit(client, kind='volume', parameters=None):
    response = client.post('/api/scenes/urban/jobs', json={'kind': kind, 'parameters': parameters or PARAMETERS})
    assert response.status_code == 202, response.text
    return response.json()['id']


def test_real_process_snapshot_rerun_export_and_publish(client):
    job_id = submit(client)
    job = wait(client, job_id)
    assert job['status'] == 'succeeded', job
    assert job['result']['evaluated_cells'] == 4
    assert job['snapshot']['provenance'] == provenance()
    # Completion alone does not overwrite workbench state.
    assert store.get('volume:urban') is None
    response = client.post(f'/api/jobs/{job_id}/publish')
    assert response.status_code == 200
    assert store.get('volume:urban')['job_id'] == job_id
    exported = client.get(f'/api/jobs/{job_id}/export')
    assert 'attachment' in exported.headers['content-disposition']
    assert exported.json()['snapshot'] == job['snapshot']
    # Retry uses old inputs even after live data changes, but cannot publish stale output.
    scene = client.get('/api/scenes/urban').json()
    scene.pop('rules'); scene.pop('registry')
    scene['revision'] += 1
    store.put('scene:urban', scene)
    retry = client.post(f'/api/jobs/{job_id}/retry')
    assert retry.status_code == 202
    repeated = wait(client, retry.json()['id'])
    assert repeated['parent_id'] == job_id
    assert repeated['snapshot'] == job['snapshot']
    assert repeated['result']['stats'] == job['result']['stats']
    assert client.post(f'/api/jobs/{repeated["id"]}/publish').status_code == 409
    assert len(client.get('/api/jobs?scene=urban').json()['jobs']) == 2
    assert client.get('/api/jobs?scene=corridor').json()['jobs'] == []
    assert client.get('/health').headers['x-request-id']


def test_plan_validation_and_missing_tasks(client):
    assert client.post('/api/scenes/missing/jobs', json={'kind':'volume'}).status_code == 404
    assert client.post('/api/scenes/urban/jobs', json={'kind':'bad'}).status_code == 422
    assert client.post('/api/scenes/urban/jobs', json={'kind':'plan','parameters':{'volume':{'cell':2}}}).status_code == 422
    assert client.get('/api/jobs/missing').status_code == 404
    job_id = submit(client, 'plan', {'volume':PARAMETERS, 'budget':0})
    job = wait(client, job_id)
    assert job['status'] == 'succeeded', job
    assert job['result']['selected'] == []
    assert client.post(f'/api/jobs/{job_id}/publish').status_code == 200
    assert store.get('engineering-plan:urban')['job_id'] == job_id
    # Calibration changes with the same identifier are also detected.
    store.put('calibration:urban', {'id':'updated', 'sigma_m':3})
    assert client.post(f'/api/jobs/{job_id}/publish').status_code == 409


def test_queued_cancel_recovery_and_version_mismatch(client, monkeypatch):
    jobs.manager.stop()
    with monkeypatch.context() as patch:
        patch.setattr(jobs.manager, 'start', lambda: None)
        cancelled = submit(client)
        assert client.post(f'/api/jobs/{cancelled}/cancel').json()['status'] == 'cancelled'
        queued = submit(client)
        interrupted = submit(client)
        with store.connection() as conn:
            conn.execute("UPDATE computation_jobs SET status='running' WHERE id=?", (interrupted,))
    jobs.manager.start()
    assert wait(client, queued)['status'] == 'succeeded'
    assert wait(client, interrupted)['status'] == 'interrupted'
    assert jobs.get(cancelled)['result'] is None
    snapshot = jobs.get(queued)['snapshot']
    snapshot['provenance']['source_sha256'] = 'old-version'
    with store.connection() as conn:
        conn.execute('UPDATE computation_jobs SET snapshot=? WHERE id=?', (json.dumps(snapshot),queued))
    assert client.post(f'/api/jobs/{queued}/retry').status_code == 409
    assert client.post(f'/api/jobs/{queued}/publish').status_code == 409


def test_running_cancel_timeout_and_shutdown(client, monkeypatch):
    # A real subprocess remains under scheduler control even if a calculation hangs.
    real_popen = jobs.subprocess.Popen
    def slow_process(args, **kwargs):
        return real_popen([args[0], '-c', 'import time; time.sleep(60)'], **kwargs)
    monkeypatch.setattr(jobs.subprocess, 'Popen', slow_process)
    cancelled = submit(client)
    wait(client, cancelled, {'running'})
    assert client.post(f'/api/jobs/{cancelled}/retry').status_code == 409
    assert client.post(f'/api/jobs/{cancelled}/cancel').json()['status'] == 'cancelled'
    monkeypatch.setenv('SENSE_JOB_TIMEOUT', '1')
    timed_out = submit(client)
    timed = wait(client, timed_out)
    assert timed['status'] == 'failed' and '超过' in timed['error']
    monkeypatch.setenv('SENSE_JOB_TIMEOUT', '600')
    stopped = submit(client)
    wait(client, stopped, {'running'})
    jobs.manager.stop()
    assert jobs.get(stopped)['status'] == 'interrupted'
    assert jobs.get(cancelled)['result'] is None


def test_backup_preserves_wal_and_refuses_overwrite(client, tmp_path):
    store.put('backup-evidence', {'value':42})
    target = tmp_path / 'backup.sqlite3'
    store.backup(target)
    with sqlite3.connect(target) as conn:
        assert json.loads(conn.execute("SELECT value FROM state WHERE key='backup-evidence'").fetchone()[0]) == {'value':42}
        assert conn.execute('SELECT version FROM schema_migrations').fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        store.backup(target)


def test_two_services_share_one_scheduler(client, monkeypatch):
    real_popen = jobs.subprocess.Popen
    monkeypatch.setattr(jobs.subprocess, 'Popen', lambda args, **kwargs:
        real_popen([args[0], '-c', 'import time; time.sleep(60)'], **kwargs))
    job_id = submit(client)
    wait(client, job_id, {'running'})
    second = jobs.JobManager()
    second.start()
    try:
        time.sleep(.3)
        assert jobs.get(job_id)['status'] == 'running'
    finally:
        second.stop()
        jobs.cancel(job_id)
