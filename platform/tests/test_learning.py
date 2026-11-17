from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app import learning, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    app = FastAPI()
    app.include_router(learning.router)
    with TestClient(app) as client:
        yield client


def test_catalog_files_and_isolated_persistence(client, tmp_path):
    catalog = client.get('/api/learning/catalog').json()
    assert len(catalog['lessons']) == 11
    assert sum(x['resources'][0]['pages'] for x in catalog['lessons']) == 453
    assert all(r['available'] for l in catalog['lessons'] for r in l['resources'])
    for lesson in catalog['lessons']:
        for resource in lesson['resources']:
            response = client.get(f"/api/learning/files/{lesson['id']}/{resource['id']}", headers={'Range':'bytes=0-31'})
            assert response.status_code in (200, 206)
            assert response.content.startswith(b'%PDF')
            assert 'inline' in response.headers['content-disposition']
    payload = {'status':'studying','resource':'zh','page':14,'notes':'闭环与反馈\n<script>保留为纯文本</script>'}
    assert client.put('/api/learning/records/lecture-01', json=payload).status_code == 200
    assert client.get('/api/learning/catalog').json()['records']['lecture-01']['notes'] == payload['notes']
    assert client.get('/api/learning/export').json()['records']['lecture-01']['page'] == 14
    assert (store.DATA / 'learning.sqlite3').exists()
    assert not (store.DATA / 'platform.sqlite3').exists()
    assert 'attachment' in client.get('/api/learning/files/lecture-01/original?download=true').headers['content-disposition']


def test_validation_and_mount(client):
    assert client.get('/learning').status_code == 200
    for change in [{'page':0},{'page':74,'resource':'zh'},{'page':1.5},{'status':'bad'},{'notes':'x'*30001}]:
        assert client.put('/api/learning/records/lecture-01',json=change).status_code == 422
    assert client.put('/api/learning/records/lecture-99',json={}).status_code == 404
    assert client.put('/api/learning/records/lecture-02',json={'resource':'zh'}).status_code == 404
    assert client.get('/api/learning/files/lecture-01/not-real').status_code == 404
    assert client.get('/api/learning/files/unknown/original').status_code == 404
    outer = FastAPI(); sub = FastAPI(); sub.include_router(learning.router); outer.mount('/network', sub)
    with TestClient(outer) as mounted:
        assert mounted.get('/network/learning').status_code == 200
        assert mounted.get('/network/api/learning/catalog').status_code == 200


def test_multiple_courses_keep_original_records_and_isolate_same_lesson_ids(client):
    # The original HKU key remains readable without any migration or query parameter.
    client.put('/api/learning/records/lecture-01', json={'notes': 'HKU 原有记录', 'page': 2})
    courses = client.get('/api/learning/courses').json()
    assert {c['id'] for c in courses['courses']} >= {'hku-data8010', 'jhu-video-ai', 'cmu-eai-safety'}
    assert any('ETH' in r['status'] for r in courses['references'])
    for course in ['jhu-video-ai', 'cmu-eai-safety']:
        result = client.put(f'/api/learning/records/lecture-01?course={course}', json={'notes': course, 'page': 1})
        assert result.status_code == 200
        assert client.get(f'/api/learning/export?course={course}').json()['records']['lecture-01']['notes'] == course
        catalog = client.get(f'/api/learning/catalog?course={course}').json()
        assert all(r['available'] for l in catalog['lessons'] for r in l['resources'])
        for lesson in catalog['lessons']:
            response = client.get(f"/api/learning/files/{lesson['id']}/original?course={course}", headers={'Range':'bytes=0-15'})
            assert response.status_code in (200,206)
            assert response.content.startswith(b'%PDF')
    assert client.get('/api/learning/catalog').json()['records']['lecture-01']['notes'] == 'HKU 原有记录'
    assert len(client.get('/api/learning/catalog').json()['records']) == 1
    for course in ['../hku-data8010', 'unknown', 'hku-data8010/..']:
        assert client.get('/api/learning/catalog', params={'course':course}).status_code == 404


def test_bilingual_pages_and_alignment(client):
    response = client.get('/api/learning/study/lecture-01?page=9')
    assert response.status_code == 200
    item = response.json()
    assert item['available'] and item['complete']
    assert item['source_page'] == 9
    assert '根本区别' in item['content']['title']
    assert item['content']['annotation']
    assert 'source_text' not in item['content']
    assert client.get('/api/learning/study/lecture-01?page=73').status_code == 422
    assert client.get('/api/learning/study/lecture-01?page=0').status_code == 422
    assert client.get('/api/learning/study/unknown').status_code == 404
    catalog = client.get('/api/learning/catalog').json()
    chinese = next(r for r in catalog['lessons'][0]['resources'] if r['id'] == 'zh')
    assert chinese['source_page_map']['9'] == 10
    progress = client.get('/api/learning/translation-status').json()
    assert progress['state'] in ['not_started', 'running', 'failed', 'completed']
    assert 'error' not in progress
