import importlib.util
import json
from types import SimpleNamespace
from pathlib import Path

spec = importlib.util.spec_from_file_location('regression', Path(__file__).parents[1] / 'scripts/run_compute_regression.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_nonlocal_target_fails_with_report_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(module.httpx, 'Client', lambda **kw: (_ for _ in ()).throw(AssertionError('network must not run')))
    directory = tmp_path / 'evidence'
    assert module.run(SimpleNamespace(url='https://example.com', output=str(directory))) == 1
    report = json.loads((directory / 'report.json').read_text())
    assert report['status'] == 'failed'
    assert 'local' in report['error']
    assert (directory / 'report.html').exists()


def test_active_flight_is_not_restarted(tmp_path, monkeypatch):
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'telemetry': {'armed': True}})
        def post(self, *args, **kwargs): raise AssertionError('must not restart')
    monkeypatch.setattr(module.httpx, 'Client', Client)
    directory = tmp_path / 'evidence'
    assert module.run(SimpleNamespace(url='http://127.0.0.1:18773', output=str(directory))) == 1
    assert 'armed' in json.loads((directory / 'report.json').read_text())['error']


def test_failed_stage_keeps_log_and_generates_failure_report(tmp_path, monkeypatch):
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'telemetry': {'armed': False}})
        def post(self, *args, **kw): return SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(module.httpx, 'Client', Client)
    def failed_run(*args, **kw):
        kw['stdout'].write('intentional test failure')
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(module.subprocess, 'run', failed_run)
    directory = tmp_path / 'evidence'
    assert module.run(SimpleNamespace(url='http://127.0.0.1:18774', output=str(directory))) == 1
    report = json.loads((directory / 'report.json').read_text())
    assert report['stages'][0]['exit_code'] == 7
    assert 'sitl-flow' in report['error']
    assert (directory / 'sitl-flow.log').read_text() == 'intentional test failure'
    assert (directory / 'report.html').exists()


def test_browser_failure_is_merged_and_fails_whole_run(tmp_path, monkeypatch):
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'telemetry': {'armed': False}})
        def post(self, *args, **kw): return SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(module.httpx, 'Client', Client)
    directory = tmp_path / 'evidence'
    def execute(command, **kw):
        if 'scripts/test_process_sitl_flow.py' in command:
            (directory / 'flow.json').write_text(json.dumps({'task_id': 'TASK-test', 'result': {'record_id': 'record-test'}, 'runtime': {'counts': {}}}))
        if 'scripts/test_compute_replay_archive.cjs' in command:
            (directory / 'replay-checks.json').write_text(json.dumps({'short_transfer': {'transfer_token': 'test'}}))
        if 'scripts/test_compute_browser.py' in command:
            (directory / 'browser.json').write_text(json.dumps({'status': 'failed', 'error': 'missing detection', 'screenshots': ['browser-failure.png']}))
            (directory / 'browser-failure.png').write_bytes(b'fixture')
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', execute)
    assert module.run(SimpleNamespace(url='http://127.0.0.1:18775', output=str(directory))) == 1
    report = json.loads((directory / 'report.json').read_text())
    assert report['status'] == 'failed'
    assert report['browser']['error'] == 'missing detection'
    assert report['stages'][-1]['name'] == 'browser'
    assert '<img' in (directory / 'report.html').read_text()
    assert 'browser-failure.png' in (directory / 'report.html').read_text()
