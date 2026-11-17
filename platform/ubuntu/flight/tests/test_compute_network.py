import pytest
from app.compute_network import Network
from app.compute_runtime import Runtime
from test_compute_runtime import binding, wait, sample


NODES={'device':{'position':[0,0,0]},'edge':{'position':[1,0,0]}}
LINKS=[dict(source='device',target='edge',mbps=1,latency_s=0,max_range_m=10),
       dict(source='edge',target='device',mbps=1,latency_s=0,max_range_m=10)]


def test_bandwidth_sharing_and_mid_transfer_rate_change():
    n=Network(LINKS)
    for key in ['a','b']:n.begin(key,'device','edge',125000,NODES,0)
    n.advance(1,NODES)
    assert n.flows['a']['remaining_mbit']==pytest.approx(.5)
    n.configure('weak',1,10,.5,0)
    n.advance(2,NODES)
    assert n.flows['a']['remaining_mbit']==pytest.approx(.25)
    n.advance(3,NODES)
    assert all(f['status']=='completed' for f in n.flows.values())


def test_latency_break_and_range_are_not_successful_delivery():
    n=Network(LINKS);n.configure('weak',0,10,1,500)
    n.begin('a','device','edge',125000,NODES,0);n.advance(.25,NODES)
    assert n.flows['a']['remaining_mbit']==1
    n.advance(.75,NODES);assert n.flows['a']['remaining_mbit']==pytest.approx(.75)
    n.configure('disconnected',.75);n.advance(.75,NODES)
    assert n.flows['a']['error']=='link_disconnected'
    n.configure('normal',1);n.begin('b','device','edge',10,NODES,1)
    n.advance(2,dict(NODES,edge={'position':[20,0,0]}))
    assert n.flows['b']['status']=='failed'


def test_weak_return_break_migrates_same_batch_then_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_RECORDS_DIR',str(tmp_path))
    r=Runtime(binding(),'network-record',interval_s=30,workload='synthetic-test')
    r.node_action('device','offline',30)
    r.network_action('weak',30,1,200)
    r.start()
    try:
        s=wait(r,lambda s:any(f['source']!='device' and f['status']=='moving' for f in s['network']['flows']))
        r.network_action('disconnected',3)
        s=wait(r,lambda s:any(e['kind']=='transfer_failed' for e in s['scheduling_events']))
        assert s['counts']['completed']==0
        r.node_action('device','recover')
        s=wait(r,lambda s:s['counts']['completed']==1)
        attempts=s['last_batch']['tasks']['preprocess']['attempts']
        assert attempts[0]['error']=='link_disconnected'
        assert attempts[0]['transfers'][-1]['phase']=='download'
        assert attempts[1]['node']=='device'
        assert s['last_batch']['output']['synthetic']
        s=wait(r,lambda s:s['network']['profile']=='normal')
        assert s['network']['revision']>=3
        r.offer(sample(),terminal=True);r.thread.join(5)
        from app.compute_store import history
        assert any(h['network']['profile']=='disconnected' for h in history('network-record'))
    finally:r.stop()


def test_network_control_is_execution_bound_and_validated(monkeypatch):
    from app import compute_runtime
    from app.main import app
    from fastapi.testclient import TestClient
    r=Runtime(binding(),'none',workload='synthetic-test')
    monkeypatch.setattr(compute_runtime,'CURRENT',r)
    c=TestClient(app)
    b=dict(execution_id='exec-test',profile='weak')
    assert c.post('/api/compute/network',json={**b,'execution_id':'wrong'}).status_code==409
    for invalid in [{'mbps':0},{'latency_ms':-1},{'duration_s':100},{'profile':'shell'},{'host':'external'}]:
        assert c.post('/api/compute/network',json={**b,**invalid}).status_code==422
    r.state='completed'
    assert c.post('/api/compute/network',json=b).status_code==409


def test_network_timeout_is_not_worker_timeout(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_RECORDS_DIR',str(tmp_path))
    r=Runtime(binding(),'slow-link',interval_s=30,workload='synthetic-test',max_attempts=1)
    r.network_timeout_s=.15
    r.node_action('device','offline',30);r.network_action('weak',30,.1,300);r.start()
    try:
        s=wait(r,lambda s:s['counts']['failed']==1)
        j=s['last_batch']['tasks']['preprocess']
        assert j['attempts'][0]['error']=='network_timeout'
        assert j['attempts'][0]['worker_roundtrip_s'] is None
        assert not s['last_batch']['output']
        assert all(n['restarts']==0 for n in s['nodes'])
    finally:r.stop()


def test_combined_node_failure_and_return_break_allow_third_attempt(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_RECORDS_DIR',str(tmp_path))
    r=Runtime(binding(),'combined',interval_s=30,timeout_s=10,workload='synthetic-test')
    r.node_action('device','offline',30);r.inject('preprocess','timeout');r.start()
    try:
        s=wait(r,lambda s:s['current'] and s['current']['tasks']['preprocess']['status']=='running')
        producer=s['current']['tasks']['preprocess']['node']
        r.node_action(producer,'offline',30);r.network_action('weak',30,1,200)
        wait(r,lambda s:any(f['source']!='device' and f['size_bytes']>10000 and f['status']=='moving' for f in s['network']['flows']))
        r.network_action('disconnected',3)
        wait(r,lambda s:any(e['kind']=='transfer_failed' for e in s['scheduling_events']))
        r.node_action('device','recover')
        s=wait(r,lambda s:s['counts']['completed']==1)
        attempts=s['last_batch']['tasks']['preprocess']['attempts']
        assert len(attempts)==3
        assert attempts[0]['error']=='node_offline'
        assert attempts[1]['error']=='link_disconnected'
        assert attempts[2]['node']=='device' and attempts[2]['status']=='completed'
    finally:r.stop()
