import pytest
from fastapi.testclient import TestClient
from app import sim, business, incidents
from app.main import app


@pytest.mark.parametrize('scenario', ['traffic', 'airspace', 'encounter'])
def test_demo_closed_loop_and_live_isolation(tmp_path, monkeypatch, scenario):
    monkeypatch.setenv('FLIGHT_DATA_DIR', str(tmp_path))
    sim.reset()
    before=[sim.snapshot(v) for v in sim.VEHICLES.values()]
    client=TestClient(app)
    space=client.get('/api/business/airspace').json()
    response=client.post('/api/business/dynamic-demos',json={'scenario':scenario})
    assert response.status_code==200, response.text
    report=response.json()
    assert report['result']['completed']
    assert report['result']['violations']==[]
    assert report['result']['replans']>=1
    assert before==[sim.snapshot(v) for v in sim.VEHICLES.values()]
    assert space==client.get('/api/business/airspace').json()
    assert client.get('/api/business/dynamic-demos/latest').json()==report
    histories=[e for f in report['frames'] for v in f['vehicles'] for e in (v.get('dynamic_guard') or {}).get('history',[])]
    assert any(e.get('original_route') and e.get('position') for e in histories)
    if scenario=='airspace':
        assert any(e['state']=='holding' and e['reasons'][0]['kind']=='no_fly' for e in histories)
        assert len({f['airspace']['revision'] for f in report['frames']})==3
    else:
        hits=[r for e in histories for r in e['reasons'] if r['kind']=='traffic']
        assert hits and all(len(r['position'])==3 and len(r['target_position'])==3 for r in hits)


def test_demo_rejects_unknown_scenario(tmp_path, monkeypatch):
    monkeypatch.setenv('FLIGHT_DATA_DIR',str(tmp_path))
    client=TestClient(app)
    assert client.get('/api/business/dynamic-demos/latest').status_code==404
    assert client.post('/api/business/dynamic-demos',json={'scenario':'real-flight'}).status_code==422
