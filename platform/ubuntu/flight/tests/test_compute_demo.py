from test_compute_runtime import binding
from app.compute_runtime import Runtime


def test_demo_cancel_restores_network():
    r = Runtime(binding(), 'demo-cancel', workload='synthetic-test')
    r.demo_action('start')
    r._network_tick(10, None)
    r.counts['completed'] += 1
    r.network.begin('upload', 'device', 'edge-a', 120000, r.network_nodes, 11)
    r._network_tick(11, None)
    assert r.network.profile == 'weak'
    r.demo_action('cancel')
    r._network_tick(11.1, None)
    assert r.demo['state'] == 'cancelled' and r.network.profile == 'normal'
    assert not r.offline
    assert any(e['kind'] == 'demo_finished' for e in r.scheduling_events)


def test_demo_timeout_restores_without_claiming_success():
    r=Runtime(binding(),'demo-timeout',workload='synthetic-test')
    r.demo_action('start');r._demo_tick(10)
    r._demo_tick(111)
    assert r.demo['state']=='incomplete'
    assert r.network_requests[-1][0]=='normal'
    assert '超时' in r.demo['reason']


def test_demo_api_rejects_conflicting_faults(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import compute_runtime
    r=Runtime(binding(),'demo-api',workload='synthetic-test')
    monkeypatch.setattr(compute_runtime,'CURRENT',r)
    c=TestClient(app)
    assert c.post('/api/compute/demo',json={'execution_id':'wrong'}).status_code==409
    assert c.post('/api/compute/demo',json={'execution_id':'exec-test'}).status_code==200
    assert c.post('/api/compute/demo',json={'execution_id':'exec-test'}).status_code==409
    assert c.post('/api/compute/network',json={'execution_id':'exec-test','profile':'weak'}).status_code==409
    assert c.post('/api/compute/demo',json={'execution_id':'exec-test','action':'cancel'}).status_code==200


def test_demo_ignores_short_transfer_then_interrupts_durable_flow():
    r = Runtime(binding(), 'short-flow', workload='synthetic-test')
    r.demo_action('start')
    r._demo_tick(10)
    r.counts['completed'] += 1
    short = r.network.begin('short', 'device', 'edge-a', 1060, r.network_nodes, 11)
    r._network_tick(11, None)
    assert r.demo['phase'] == 'weak'
    r._network_tick(12, None)
    assert r.demo['phase'] == 'normal'
    assert short['status'] == 'completed'
    large = r.network.begin('frame', 'device', 'edge-a', 120000, r.network_nodes, 12)
    r._network_tick(12, None)
    assert r.network.profile == 'weak'
    r._network_tick(12.55, None)
    assert r.demo['weak_transfer']['token'] == 'frame'
    assert large['error'] == 'link_disconnected'


def test_weak_demo_does_not_disconnect_an_unrelated_transfer():
    r = Runtime(binding(), 'demo-identity', workload='synthetic-test')
    r.demo_action('start')
    r._demo_tick(10)
    r.counts['completed'] += 1
    r.network.begin('finished', 'device', 'edge-a', 1060, r.network_nodes, 11)
    r._network_tick(11, None)
    r.network.advance(12, r.network_nodes)
    other = r.network.begin('new', 'device', 'edge-a', 120000, r.network_nodes, 12)
    r._network_tick(12, None)
    assert other['status'] == 'moving'
    assert r.network.profile == 'normal'
    assert 'weak_transfer' not in r.demo


def test_short_probe_completes_before_work_transfer_is_selected():
    runtime = Runtime(binding(), 'short-probe', workload='synthetic-test')
    runtime.demo_action('short_transfer')
    runtime._network_tick(10, None)
    runtime.counts['completed'] += 1
    runtime._network_tick(11, None)
    short = runtime.demo['short_token']
    runtime._network_tick(12, None)
    assert runtime.demo['short_completed']['token'] == short
    assert runtime.demo['short_completed']['remaining_mbit'] == 0
    assert runtime.network.profile == 'normal' and short not in runtime.network.flows
    runtime.network.begin('work', 'device', 'edge-a', 120000, runtime.network_nodes, 13)
    runtime._network_tick(13, None)
    runtime._network_tick(13.1, None)
    assert runtime.demo['weak_transfer']['token'] == 'work'
    assert runtime.network.flows['work']['error'] == 'link_disconnected'
