import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app import stability_api as api, sitl, compute_runtime


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('FLIGHT_STABILITY_DIR', str(tmp_path))
    monkeypatch.setenv('FLIGHT_BASE_URL', 'http://127.0.0.1:8773')
    monkeypatch.setattr(api, 'PROCESSES', {})
    monkeypatch.setattr(sitl, 'STATE', {'telemetry': {'armed': False}})
    monkeypatch.setattr(compute_runtime, 'snapshot', lambda: None)
    app=FastAPI();app.include_router(api.router)
    return tmp_path, TestClient(app)


def test_start_count_and_busy_guard(setup, monkeypatch):
    root, client=setup
    def launch(command, **kwargs):
        path=root/command[-1].split('/')[-1];path.mkdir()
        (path/'summary.json').write_text(json.dumps({'status':'running','requested_rounds':2,'rounds':[]}))
        assert command[command.index('--rounds')+1]=='2'
        return SimpleNamespace(pid=123,poll=lambda:None)
    monkeypatch.setattr(api.subprocess,'Popen',launch)
    assert client.post('/api/stability',json={'rounds':0}).status_code==422
    assert client.post('/api/stability',json={'rounds':2}).status_code==202
    assert client.post('/api/stability',json={'rounds':2}).status_code==409


def test_failure_history_progress_and_files(setup):
    root,client=setup;p=root/'saved';p.mkdir();r=p/'round-1';r.mkdir()
    (p/'summary.json').write_text(json.dumps({'status':'failed','requested_rounds':2,'rounds':[{'round':1,'duration_s':2}]}))
    (p/'round-1-runner.log').write_text('RUN sitl-flow\nRUN browser\n')
    (r/'report.json').write_text(json.dumps({'status':'failed','task_id':'TASK-1','error':'browser failed (1); see browser.log','browser':{'status':'failed','error':'image missing'}}))
    (r/'browser-failure.png').write_bytes(b'evidence')
    response=client.get('/api/stability/saved').json()
    assert response['rounds'][0]['stage']=='browser'
    assert response['rounds'][0]['reason']=='image missing'
    assert response['rounds'][1]['status']=='not_run'
    assert client.get('/api/stability/saved/files/round-1/browser-failure.png').content==b'evidence'
    assert client.get('/api/stability').json()['items'][0]['id']=='saved'
    with pytest.raises(HTTPException):api.evidence('saved','../../secret.json')
    with pytest.raises(HTTPException):api.directory('..')


def test_live_flight_and_remote_target_rejected(setup, monkeypatch):
    _,client=setup
    monkeypatch.setattr(sitl,'STATE',{'telemetry':{'armed':True}})
    assert client.post('/api/stability',json={'rounds':1}).status_code==409
    monkeypatch.setattr(sitl,'STATE',{})
    monkeypatch.setenv('FLIGHT_BASE_URL','https://example.com')
    assert client.post('/api/stability',json={'rounds':1}).status_code==409


def test_server_restart_recovers_persisted_job_status(setup, monkeypatch):
    root,client=setup;p=root/'interrupted';p.mkdir()
    (p/'job.json').write_text(json.dumps({'status':'running','pid':123,'rounds':1}))
    (p/'round-1-runner.log').write_text('RUN sitl-flow\n')
    monkeypatch.setattr(api,'alive',lambda pid:False)
    assert client.get('/api/stability/interrupted').json()['rounds'][0]['status']=='interrupted'
    assert client.get('/api/stability/interrupted').json()['status']=='interrupted'


def test_failed_launcher_is_retained_in_history(setup, monkeypatch):
    _,client=setup
    monkeypatch.setattr(api.subprocess,'Popen',lambda *a,**kw:SimpleNamespace(pid=456,poll=lambda:2,wait=lambda **kw:2))
    assert client.post('/api/stability',json={'rounds':1}).status_code==500
    item=client.get('/api/stability').json()['items'][0]
    assert item['status']=='failed' and item['launcher_log']
    assert '启动日志' in item['reason']


def test_legacy_history_is_recognized(setup):
    root,client=setup;p=root/'legacy';p.mkdir()
    (p/'summary.json').write_text(json.dumps({'passed':3,'failed':0,'rounds':[{}, {}, {}]}))
    assert client.get('/api/stability/legacy').json()['status']=='passed'


def test_catalogue_orders_by_start_even_when_old_directory_changes(setup):
    root, client = setup
    for ident, started in [('recent', 200), ('old', 100)]:
        folder = root / ident
        folder.mkdir()
        (folder / 'summary.json').write_text(json.dumps({'started_at': started, 'status': 'passed'}))
    (root / 'old' / 'launcher.log').touch()
    assert [item['id'] for item in client.get('/api/stability').json()['items']] == ['recent', 'old']


def test_page_acceptance_worker_progress_and_failure(setup, monkeypatch):
    root, client = setup
    calls = []
    def launch(command, **kwargs):
        calls.append(command)
        if '--output' in command:
            folder = root / command[-1].split('/')[-1]
            folder.mkdir()
            (folder / 'summary.json').write_text(json.dumps({'status': 'running', 'requested_rounds': 3}))
        return SimpleNamespace(pid=123, poll=lambda: None)
    monkeypatch.setattr(api.subprocess, 'Popen', launch)
    assert client.post('/api/stability', json={'rounds': 2, 'page_acceptance': True}).status_code == 422
    response = client.post('/api/stability', json={'rounds': 3, 'page_acceptance': True})
    assert response.status_code == 202
    job = response.json()
    assert len(calls) == 2 and calls[1][-2:] == ['--job', job['id']]
    assert job['page_acceptance']['status'] == 'running'
    folder = root / job['id']
    (folder / 'summary.json').write_text(json.dumps({'status': 'passed', 'requested_rounds': 3}))
    assert client.post('/api/stability', json={'rounds': 3}).status_code == 409
    evidence = folder / 'platform-ui'
    (evidence / 'acceptance-failure.png').write_bytes(b'png')
    (evidence / 'acceptance.json').write_text(json.dumps({'status':'failed','error':'image missing'}))
    ui = client.get('/api/stability/' + job['id']).json()['page_acceptance']
    assert ui['error'] == 'image missing'
    assert 'platform-ui/acceptance-failure.png' in ui['evidence']


def test_retry_creates_linked_job_without_changing_original(setup, monkeypatch):
    root, client = setup
    parent = root / 'original'
    folder = parent / 'platform-ui'
    folder.mkdir(parents=True)
    (parent / 'summary.json').write_text(json.dumps({'status': 'passed', 'requested_rounds': 3}))
    (folder / 'acceptance.json').write_text(json.dumps({'status':'passed'}))
    assert client.post('/api/stability/original/retry').status_code == 409
    (folder / 'acceptance.json').write_text(json.dumps({'status':'failed','error':'report 503'}))
    (folder / 'failure.png').write_bytes(b'original')
    def launch(command, **kwargs):
        if '--output' in command:
            destination = root / command[-1].split('/')[-1]
            destination.mkdir()
            (destination / 'summary.json').write_text(json.dumps({'status':'running','requested_rounds':3}))
        return SimpleNamespace(pid=123, poll=lambda:None)
    monkeypatch.setattr(api.subprocess, 'Popen', launch)
    result = client.post('/api/stability/original/retry')
    assert result.status_code == 202
    assert result.json()['retry_of'] == 'original'
    child = result.json()['id']
    assert client.get('/api/stability/original').json()['retries'] == [child]
    assert client.post('/api/stability/original/retry').status_code == 409
    assert (folder / 'failure.png').read_bytes() == b'original'
