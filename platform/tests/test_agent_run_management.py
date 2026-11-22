import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import research_agent as agent
from app import store


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(agent, 'JOBS', {})
    app = FastAPI()
    app.include_router(agent.router)
    with TestClient(app) as client:
        yield client


URL = '/api/research-agent/management/runs'


def save(index, status='completed', workflow='platform'):
    record = dict(id=f'{index:032x}', status=status, request={'question':f'测试 Area {index}'},
                  created_at='2026-11-20T08:00:00+00:00', input_snapshot={'large':'do not return'})
    if workflow:
        record['request']['workflow'] = workflow
    agent.save(record)
    return record


def test_search_and_page_do_not_truncate_global_totals_or_return_snapshot(api):
    for index in range(31):
        save(index, status='failed' if index % 2 else 'completed', workflow='research' if index % 2 else 'platform')
    params = dict(q=' aREa ', status='failed', workflow='research', offset=2, limit=3)
    result = api.get(URL, params=params).json()
    assert [row['id'] for row in result['runs']] == [f'{n:032x}' for n in (25, 23, 21)]
    assert all('input_snapshot' not in row for row in result['runs'])
    assert result['total'] == 15 and result['summary']['total'] == 31
    assert result['summary']['by_status']['completed'] == 16
    assert result['summary']['by_workflow'] == dict(research=15, platform=16)
    assert api.get(URL, params={**params, 'offset':200}).json()['runs'] == []
    assert api.get(URL, params={'q':'0000000A'}).json()['total'] == 1


def test_management_reads_orphan_legacy_and_invalid_archives_without_mutating(api):
    record = save(6, status='running', workflow=None)
    save(7, status='running')
    agent.JOBS[f'{7:032x}'] = object()
    (agent.folder() / ('e' * 32 + '.json')).write_text('[]')
    (agent.folder() / ('f' * 32 + '.json')).write_text('{')
    (agent.folder() / 'notes.json').write_text('{}')
    result = api.get(URL, params={'status':'interrupted'}).json()
    assert result['summary']['total'] == 2 and result['summary']['unreadable'] == 2
    assert result['summary']['by_status']['running'] == 1
    row, = result['runs']
    assert row['request']['workflow'] == 'research' and row['step_count'] is None
    assert row['status'] == 'interrupted' and row['tool_count'] is None
    assert agent.read(record['id']) == record


def test_updates_replace_cached_summary_and_filters_validate(api):
    assert api.get(URL).json()['summary']['total'] == 0
    assert not (store.DATA / 'research_agent_runs').exists()
    record = save(4)
    assert api.get(URL).json()['runs'][0]['status'] == 'completed'
    record.update(status='failed', steps=[{'key':'select'}], trace=[])
    agent.save(record)
    row = api.get(URL).json()['runs'][0]
    assert row['status'] == 'failed' and row['step_count'] == 1 and row['tool_count'] == 0
    for params in ({'status':'bogus'}, {'workflow':'other'}, {'offset':-1}, {'limit':0}, {'limit':101}):
        assert api.get(URL, params=params).status_code == 422
