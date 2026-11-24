import pytest
from fastapi.testclient import TestClient

from app import store, twin_experiment as twin
from app.main import app
from app.scenes import seed


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    store.init()
    with TestClient(app) as client:
        yield client


def test_overview_page_and_api_structure(client):
    page = client.get('/overview')
    assert page.status_code == 200
    assert 'static/overview.js' in page.text
    assert '研究体系总览' in page.text

    response = client.get('/api/research-overview')
    assert response.status_code == 200
    data = response.json()
    assert len(data['overview_digest']) == 64
    assert [p['id'] for p in data['platforms']] == ['planning', 'station', 'service', 'agent']
    assert len(data['importance']) == 3
    assert [q['id'] for q in data['scientific_questions']] == ['Q1', 'Q2', 'Q3', 'Q4']
    assert [s['id'] for s in data['subprojects']] == [1, 2, 3, 4, 5]
    edges = {(r['source'], r['target'], r['kind']) for r in data['relations']}
    assert (1, 2, 'forward') in edges and (2, 3, 'forward') in edges and (5, 4, 'feedback') in edges
    assert [f['id'] for f in data['feasibility']] == ['F1', 'F2', 'F3', 'F4']
    assert [s['stage'] for s in data['project']['landing_stages']] == ['第一阶段', '第二阶段', '第三阶段']
    assert any('合成/仿真验证' in note for note in data['boundaries'])
    assert [s['scene'] for s in data['necessity']['scenes']] == ['urban', 'corridor', 'electromagnetic']
    assert all(s['latest_twin']['available'] is False for s in data['necessity']['scenes'])


def test_overview_reflects_archived_twin_and_scene_filter(client):
    record = dict(id='d' * 32, created_at='2026-11-23T10:00:00Z',
                  **twin.run(seed('electromagnetic'), twin.TwinRequest(scene='electromagnetic', seed_count=4)))
    store.put('twin-experiment-latest:electromagnetic', twin.summary(record))
    store.put('twin-experiment-index', [dict(id=record['id'], scene='electromagnetic')])

    filtered = client.get('/api/research-overview', params={'scene': 'electromagnetic'})
    assert filtered.status_code == 200
    body = filtered.json()
    assert len(body['necessity']['scenes']) == 1
    em_twin = body['necessity']['scenes'][0]['latest_twin']
    assert em_twin['available'] is True and em_twin['experiment_id'] == record['id']
    assert em_twin['gain_vs_baseline_pp'] is not None
    assert em_twin['gain_vs_coverage_only_pp'] is not None
    assert client.get('/api/research-overview', params={'scene': 'unknown'}).status_code == 422
