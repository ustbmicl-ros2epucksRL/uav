from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.research_analysis import router


def test_research_routes_and_mount():
    child = FastAPI()
    child.include_router(router)
    parent = FastAPI()
    parent.mount('/network', child)
    for app, prefix in [(child, ''), (parent, '/network')]:
        with TestClient(app) as client:
            response = client.get(prefix + '/research', follow_redirects=False)
            assert response.headers['location'] == prefix + '/research/1'
            for task in range(1, 6):
                response = client.get(prefix + f'/research/{task}')
                assert response.status_code == 200
                assert f'<base href="{prefix}/">' in response.text
            assert client.get(prefix + '/research/6').status_code == 404


def test_research_evidence_and_pdf():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        tasks = client.get('/api/research-analysis/tasks').json()
        assert [t['id'] for t in tasks] == list(range(1, 6))
        from app.proposal_source import TASK_PAGES
        for task in tasks:
            assert task['pages'] == TASK_PAGES[task['id']] and task['scenes'] and task['solutions']
            assert len(task['challenges']) == 3
            for c in task['challenges']:
                assert all(c[k] for k in ['problem', 'variable', 'baseline', 'metric', 'evidence', 'failure'])
        pdf = client.get('/api/research-analysis/proposal')
        assert pdf.status_code == 200
        assert pdf.content.startswith(b'%PDF')


def test_page_label_collapses_runs():
    from app.proposal_source import page_label
    assert page_label([20, 29, 36, 37, 38]) == '20、29、36–38'
    assert page_label([22]) == '22'
