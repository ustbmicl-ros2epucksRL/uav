import time
import pytest
from app.compute_feedback import Feedback
from app.algorithm_compute import default_compute, evaluate
from app.compute_runtime import Runtime
from test_compute_runtime import binding, sample, wait


def test_measured_cost_and_load_change_existing_heft_assignment():
    config=default_compute(dict(xyz_min=[200,200,30],xyz_max=[700,700,280]))
    f=Feedback()
    for task in config['tasks']:
        for node in config['nodes']:
            f.observe(task['id'],node['id'],{'output':{}},.1 if node['id']=='cloud' else 3)
    costs,_=f.matrix(config,{})
    before=evaluate(config,[280,280,120],costs=costs)
    assert next(r for r in before['rows'] if r['id']=='detect')['node']=='cloud'
    for _ in range(20):f.observe('detect','cloud',{'output':{}},20)
    costs,evidence=f.matrix(config,{'cloud':1})
    after=evaluate(config,[280,280,120],costs=costs)
    assert next(r for r in after['rows'] if r['id']=='detect')['node']!='cloud'
    assert evidence['detect']['cloud']['source']=='node-ewma'
    assert costs['detect']['cloud']==2*evidence['detect']['cloud']['base_s']
    assert all(r['node']!='cloud' for r in evaluate(config,[280,280,120],costs=costs,unavailable=['cloud'])['rows'])


def test_cold_load_does_not_pollute_steady_state():
    f=Feedback();f.observe('detect','cloud',{'output':{'model_load_s':5}},5.2)
    assert f.samples['detect','cloud']['seconds']==pytest.approx(.2)
    f.observe('detect','cloud',{'output':{}},.4)
    assert f.samples['detect','cloud']['seconds']==pytest.approx(.26)


def test_inflight_node_outage_migrates_checkpoint_then_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_RECORDS_DIR',str(tmp_path))
    r=Runtime(binding(),'outage',interval_s=30,timeout_s=10,workload='synthetic-test')
    r.inject('preprocess','timeout');r.start()
    try:
        s=wait(r,lambda s:s['current'] and s['current']['tasks']['preprocess']['status']=='running')
        failed=s['current']['tasks']['preprocess']['node']
        r.node_action(failed,'offline',3)
        s=wait(r,lambda s:s['counts']['completed']==1)
        attempts=s['last_batch']['tasks']['preprocess']['attempts']
        assert attempts[0]['error']=='node_offline'
        assert attempts[1]['node']!=failed
        assert failed in attempts[1]['decision']['offline']
        s=wait(r,lambda s:any(e['kind']=='node_recovered' for e in s['scheduling_events']))
        assert any(e['kind']=='task_migrated' for e in s['scheduling_events'])
        assert any(e['kind']=='task_recovered' for e in s['scheduling_events'])
        assert s['feedback_revision']>=5
        r.offer(sample(),terminal=True);r.thread.join(5)
        from app.compute_store import history
        assert any(e['kind']=='node_offline' for h in history('outage') for e in h['scheduling_events'])
    finally:r.stop()


def test_node_controls_reject_unknown_and_finished_runtime():
    r=Runtime(binding(),'none',workload='synthetic-test')
    with pytest.raises(ValueError):r.node_action('unknown','offline')
    r.state='completed'
    with pytest.raises(ValueError):r.node_action('cloud','recover')


def test_node_api_binding_and_bounds(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import compute_runtime
    r=Runtime(binding(),'none',workload='synthetic-test')
    monkeypatch.setattr(compute_runtime,'CURRENT',r)
    c=TestClient(app)
    body=dict(execution_id='exec-test',node_id='cloud',action='offline',duration_s=10)
    assert c.post('/api/compute/node',json={**body,'execution_id':'wrong'}).status_code==409
    assert c.post('/api/compute/node',json={**body,'node_id':'unknown'}).status_code==409
    assert c.post('/api/compute/node',json={**body,'duration_s':1000}).status_code==422
    assert c.post('/api/compute/node',json={**body,'action':'shell'}).status_code==422
    assert not r.node_requests
