import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import sim,route_plan,station

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(route_plan,'scene_record',lambda _:dict(buildings=[]))
    sim.reset();station.CLOCK['running']=False
    return TestClient(app)

def test_patrol_control_and_completion(client):
    r=client.post('/api/park-patrol/UAV-01/assign',json={'area_id':'west','altitude':180})
    assert r.status_code==200,r.text
    for cmd in ['arm','auto']:
        assert client.post('/api/vehicles/UAV-01/command',json={'cmd':cmd}).status_code==200
    client.post('/api/tick?steps=4')
    assert client.get('/api/park-patrol').json()['assignments'][0]['state']=='patrolling'
    client.post('/api/vehicles/UAV-01/command',json={'cmd':'loiter'})
    pos=list(sim.get('UAV-01').position)
    client.post('/api/tick?steps=2');assert sim.get('UAV-01').position==pos
    client.post('/api/vehicles/UAV-01/command',json={'cmd':'auto'})
    for _ in range(150):
        client.post('/api/tick?steps=1')
        if sim.get('UAV-01').done:break
    assert client.get('/api/park-patrol').json()['assignments'][0]['state']=='completed'
    assert not sim.get('UAV-01').armed

def test_patrol_rejects_payload_and_airborne_assignment(client):
    assert client.post('/api/park-patrol/UAV-02/assign',json={'area_id':'west'}).status_code==409
    sim.get('UAV-01').armed=True
    assert client.post('/api/park-patrol/UAV-01/assign',json={'area_id':'west'}).status_code==409

def test_patrol_rejects_nofly_and_old_mission_unknown(client):
    client.post('/api/park-patrol/UAV-01/assign',json={'area_id':'west'})
    sim.get('UAV-01').mission=[]
    assert client.get('/api/park-patrol').json()['assignments'][0]['state']=='unknown'
    s=client.get('/api/business/airspace').json()
    s['zones']=[dict(id='blocked',x=100,y=100,w=700,d=700,z_min=0,z_max=300)]
    client.put('/api/business/airspace',json={k:s[k] for k in ['revision','zones','landing_sites']})
    assert client.post('/api/park-patrol/UAV-01/assign',json={'area_id':'west'}).status_code==409


def test_monitoring_alert_resolution_and_replay(client):
    base='/api/park-patrol/UAV-01'
    assert client.post(base+'/assign',json={'area_id':'west'}).status_code==200
    assert client.post(base+'/simulate-target').status_code==200
    assert client.get(base+'/result').json()['alerts']==[]
    assert client.post(base+'/control',json={'command':'start'}).status_code==200
    station.CLOCK['running']=False
    client.post('/api/tick?steps=5')
    result=client.get(base+'/result').json()
    assert len(result['alerts'])==1 and result['frames'][-1]['target']
    assert not result['monitoring_complete']
    assert client.post(base+'/control',json={'command':'pause'}).status_code==200
    p=list(sim.get('UAV-01').position);client.post('/api/tick?steps=2');assert p==sim.get('UAV-01').position
    assert client.post(base+'/resolve',json={'actor':' ','note':' '}).status_code==422
    assert client.post(base+'/resolve',json={'actor':'tester','note':'核验仿真目标并记录'}).status_code==200
    assert not client.get(base+'/result').json()['monitoring_complete']
    client.post(base+'/control',json={'command':'resume'});station.CLOCK['running']=False
    for _ in range(150):
        client.post('/api/tick?steps=1')
        if sim.get('UAV-01').done:break
    result=client.get(base+'/result').json()
    assert result['monitoring_complete'] and result['frames'][-1]['position'][2]==0
    assert result['alerts'][0]['evidence']['target']==[240,240,0]
    sim.reset()
    assert client.get(base+'/result').json()['monitoring_complete']


def test_patrol_start_rechecks_changed_airspace(client):
    base='/api/park-patrol/UAV-01'
    client.post(base+'/assign',json={'area_id':'west'})
    s=client.get('/api/business/airspace').json()
    s['zones']=[dict(id='changed',x=100,y=100,w=800,d=800,z_min=0,z_max=300)]
    client.put('/api/business/airspace',json={k:s[k] for k in ['revision','zones','landing_sites']})
    assert client.post(base+'/control',json={'command':'start'}).status_code==409
    assert not sim.get('UAV-01').armed


def test_resolution_rejects_stale_task_or_alert(client):
    base='/api/park-patrol/UAV-01'
    client.post(base+'/assign',json={'area_id':'west'})
    client.post(base+'/simulate-target')
    client.post(base+'/control',json={'command':'start'});station.CLOCK['running']=False
    client.post('/api/tick?steps=5')
    item=client.get(base+'/result').json()
    body=dict(actor='tester',note='reviewed',task_id='stale',alert_id=item['alerts'][0]['id'])
    assert client.post(base+'/resolve',json=body).status_code==409
    body['task_id']=item['id'];body['alert_id']='wrong'
    assert client.post(base+'/resolve',json=body).status_code==409
    body['alert_id']=item['alerts'][0]['id']
    assert client.post(base+'/resolve',json=body).status_code==200
    assert client.get(base+'/result').json()['alerts'][0]['status']=='resolved'

@pytest.mark.parametrize('area_id',['west','east'])
def test_region_task_barrier_pause_alert_and_replay(client,area_id):
    space=client.get('/api/business/airspace').json()
    zone=dict(id='TEMP-BARRIER',x=450,y=100,w=80,d=200,z_min=0,z_max=300)
    client.put('/api/business/airspace',json=dict(revision=space['revision'],zones=[zone],landing_sites=space['landing_sites']))
    catalog=client.get('/api/sitl-demo/catalog').json()
    assert {a['id'] for a in catalog['areas']}=={'west','east'}
    assert catalog['zones'][0]['polygon'] and catalog['zones'][0]['id']=='TEMP-BARRIER'
    base='/api/park-patrol/UAV-01'
    assigned=client.post(base+'/assign',json=dict(area_id=area_id,altitude=180))
    assert assigned.status_code==200,assigned.text
    assert assigned.json()['area']['id']==area_id and assigned.json()['check']['passed']
    assert not sim.get('UAV-01').armed
    client.post(base+'/simulate-target')
    assert client.post(base+'/control',json={'command':'start'}).status_code==200
    station.CLOCK['running']=False
    client.post('/api/tick?steps=4')
    assert client.post(base+'/control',json={'command':'pause'}).status_code==200
    position=list(sim.get('UAV-01').position)
    client.post('/api/tick?steps=3');assert sim.get('UAV-01').position==position
    assert client.post(base+'/control',json={'command':'resume'}).status_code==200
    station.CLOCK['running']=False
    for _ in range(300):
        client.post('/api/tick?steps=1')
        item=client.get(base+'/result').json()
        for alert in item['alerts']:
            if alert['status']=='open':
                r=client.post(base+'/resolve',json=dict(task_id=item['id'],alert_id=alert['id'],actor='验收',note='仿真目标已核验'))
                assert r.status_code==200
        if sim.get('UAV-01').done:break
    result=client.get(base+'/result').json()
    assert result['area']['id']==area_id and result['frames']
    assert result['monitoring_complete'] and not sim.get('UAV-01').armed
    assert result['alerts'] and all(a['status']=='resolved' for a in result['alerts'])
