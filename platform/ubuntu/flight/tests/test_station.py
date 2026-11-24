import time
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import sim,station

@pytest.fixture
def api(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_DATA_DIR',str(tmp_path));sim.reset();station.EVENTS.clear();station.CLOCK.update(running=False,speed=1)
    with TestClient(app) as client:yield client
    station.CLOCK.update(running=False,speed=1)


def test_geographic_anchor_roundtrip_and_axes():
    a={'latitude':39.05,'longitude':115.98};p=[1345.4,572.2,80]
    g=station.geo(p,a);assert g[0]>a['latitude'] and g[1]>a['longitude']
    assert station.enu(g,a)==pytest.approx(p,abs=1e-6)
    assert station.geo([0,0,0],a)==[39.05,115.98,0]


def test_local_map_resources_and_metadata(api):
    assert '/static/gcs.js' in api.get('/?view=legacy').text
    for path in ('gcs.js','gcs.css','gcs-core.js','vendor/leaflet/leaflet.js','vendor/leaflet/leaflet.css'):
        r=api.get('/static/'+path);assert r.status_code==200 and len(r.content)>100
    d=api.get('/api/station').json();assert d['mode']=='simulation' and not d['transport']['mavlink_connected']
    assert d['map_library']['version']=='1.9.4'
    assert len(api.get('/api/station/state').json()['vehicles'][0]['geographic_position'])==3


def test_map_anchor_persists_and_cannot_change_inflight(api):
    a={'latitude':39.1,'longitude':116.1,'label':'test surveyed origin'}
    assert api.put('/api/station/map',json=a).status_code==200
    assert api.get('/api/station').json()['map']==a
    assert api.put('/api/station/map',json={**a,'latitude':90}).status_code==422
    route={'waypoints':[{'x':200,'y':200,'z':80},{'x':500,'y':500,'z':80}]}
    api.post('/api/vehicles/UAV-01/mission',json=route)
    api.post('/api/vehicles/UAV-01/command',json={'cmd':'arm'})
    api.post('/api/vehicles/UAV-01/command',json={'cmd':'start'})
    assert api.put('/api/station/map',json=a).status_code==409


def test_route_diagnostics_and_command_ack(api):
    route={'waypoints':[{'x':200,'y':200,'z':80},{'x':2100,'y':200,'z':80}]}
    p=api.post('/api/vehicles/UAV-01/mission/preview',json=route).json()
    assert not p['inside_fence'] and p['violations'][0]['waypoint']==2
    assert p['distance_m']==1980 and p['estimated_seconds']==79.2
    assert api.post('/api/vehicles/UAV-01/command',json={'cmd':'start'}).status_code==409
    assert api.get('/api/station/state').json()['events']==[]
    api.post('/api/vehicles/UAV-01/mission',json=route)
    api.post('/api/vehicles/UAV-01/command',json={'cmd':'arm'})
    events=api.get('/api/station/state').json()['events']
    assert events[0]['transport']=='simulator' and events[0]['kind']=='command'
    assert events[1]['kind']=='mission'
    assert api.post('/api/vehicles/UAV-01/command',json={'cmd':'invalid'}).status_code==422


def test_server_clock_advances_without_browser_ticks(api):
    api.post('/api/vehicles/UAV-01/mission',json={'waypoints':[{'x':200,'y':200,'z':100},{'x':1000,'y':1000,'z':100}]})
    api.post('/api/vehicles/UAV-01/command',json={'cmd':'arm'})
    api.post('/api/vehicles/UAV-01/command',json={'cmd':'start'})
    assert api.put('/api/simulation',json={'running':True,'speed':2}).status_code==200
    assert api.post('/api/tick').status_code==409
    time.sleep(1.3)
    assert api.get('/api/vehicles/UAV-01').json()['seconds']>=2
    assert api.put('/api/simulation',json={'running':False,'speed':1}).status_code==200
    assert api.post('/api/tick').status_code==200
    assert api.put('/api/simulation',json={'running':True,'speed':100}).status_code==422


def test_mavlink_stays_listen_only_until_a_real_heartbeat(api):
    from app import mavlink_link
    body = api.get('/api/mavlink').json()
    assert body['commands'] is False and body['connected'] is False
    assert api.get('/api/station').json()['transport']['mavlink_commands'] is False
    assert api.put('/api/mavlink', json={'url': 'file:/tmp/x', 'listen': True}).status_code == 422
    saved = api.put('/api/mavlink', json={'url': 'udpin:127.0.0.1:14550', 'listen': False})
    assert saved.status_code == 200 and saved.json()['commands'] is False and not saved.json()['listening']
    if not mavlink_link.installed():
        denied = api.put('/api/mavlink', json={'url': 'udpin:127.0.0.1:14550', 'listen': True})
        assert denied.status_code == 409
    class Heartbeat:
        type = 2
        base_mode = 128
        def get_type(self):
            return 'HEARTBEAT'
        def get_srcSystem(self):
            return 1
    mavlink_link._remember(listening=True)
    mavlink_link._apply(Heartbeat())
    live = mavlink_link.status()
    assert live['connected'] and live['system_id'] == 1 and live['armed'] and live['commands'] is False
    mavlink_link.stop()


def test_planning_workflow_mounted_pages_and_task(api):
    for page in ('overview','tasks','resources','diagnosis','planning','plans','twin'):
        assert api.get('/network/'+page).status_code == 200
    assert api.get('/network/api/research-overview').status_code == 200
    assert api.get('/network/static/portal.js').status_code == 200
    result=api.post('/network/api/workflow/tasks',json={'name':'挂载路径验证','scene':'urban'})
    assert result.status_code == 200, result.text
    resources=api.get('/network/api/workflow/resources/urban')
    assert resources.status_code == 200 and resources.json()['sites']
    task_id=result.json()['id']
    assert api.post('/network/api/workflow/tasks/'+task_id+'/diagnose',json={}).status_code == 200


def test_workspace_entry_and_station_deep_links(api):
    for path in ('/', '/?platform=planning'):
        page = api.get(path)
        assert page.status_code == 200
        assert '/static/unified-workspace.js' in page.text
        assert page.headers['cache-control'] == 'no-store'
    embedded = api.get('/', headers={'Sec-Fetch-Dest': 'iframe'})
    assert '/static/gcs.js' in embedded.text
    for path in ('/station', '/?view=patrol', '/?view=fly', '/?view=unknown', '/?source=sim&record=sample',
                 '/?dual=sample', '/?assessment=sample'):
        page=api.get(path)
        assert page.status_code==200
        assert '/static/gcs.js' in page.text
        assert '/static/region-management.js' in page.text
        assert 'id="managed-area"' in page.text
        assert 'id="vehicle-select"' in page.text
        assert 'id="portal-link" href="/service/"' in page.text
        assert '/static/workbench.js' not in page.text
    assert '/static/gcs.js' in api.get('/?view=workbench').text


def test_archived_planning_segment_opens_read_only_replay(api):
    page = api.get('/station', params={'twin_experiment': 'ab' * 16, 'start': 3, 'end': 8, 'embed': 1})
    assert page.status_code == 200
    assert page.headers['cache-control'] == 'no-store'
    assert '/static/planning-replay.js' in page.text
    assert '/static/gcs.js' not in page.text
    for name in ('planning-replay.js', 'planning-replay.css'):
        assert api.get('/static/' + name).status_code == 200
