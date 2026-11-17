import copy
import sys
import time
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app
from app import flight_records, algorithm_execution
from app.algorithm_compute import default_compute

@pytest.fixture
def client(monkeypatch,tmp_path):
    for env in ('FLIGHT_DATA_DIR','FLIGHT_RECORDS_DIR','FLIGHT_ALGORITHM_DIR','LOWAIR_DATA_DIR','SENSE_DATA_DIR'):
        monkeypatch.setenv(env,str(tmp_path/env))
    c=TestClient(app)
    def request(method,path,body=None):
        r=c.request(method,path,json=body)
        if r.status_code>=400:raise HTTPException(r.status_code,r.text)
        return r.json()
    monkeypatch.setattr(sys.modules['lowair_service.main'],'flight_request',request)
    return c

def experiment(client):
    r=client.post('/api/algorithms/runs',json=dict(planner='astar',altitude_m=120))
    assert r.status_code==200,r.text
    return r.json()

def test_algorithm_application_task_binding_and_route_tamper(client):
    run=experiment(client)
    response=client.get('/api/algorithms/runs/'+run['id']+'/mission');assert response.status_code==200,response.text
    mission=response.json();route=mission['route']
    assert route[0]==route[-1]==dict(x=0,y=0,z=0)
    assert mission['check']['passed']
    body=dict(applicant='test',pilot_id='P-01',vehicle_id='UAV-01',scene='urban',purpose='算法任务',route=route,algorithm_run_id=run['id'])
    wrong=copy.deepcopy(body);wrong['route'][2]['x']+=1
    assert client.post('/service/api/applications',json=wrong).status_code==409
    r=client.post('/service/api/applications',json=body);assert r.status_code==200,r.text
    application=r.json();assert application['area_id']=='west'
    taskpath='/service/api/tasks/from-application/'+application['id']
    assert client.post(taskpath).status_code==409
    assert client.post('/service/api/applications/'+application['id']+'/review',json=dict(decision='approve',reviewer='test')).status_code==200
    task=client.post(taskpath).json()
    assert task['algorithm_run_id']==run['id'] and task['route']==route
    assert task['status']=='scheduled'

def telemetry_state(now):
    config=default_compute(dict(xyz_min=[0,0,30],xyz_max=[1000,1000,280]))
    return dict(anchor=dict(latitude=39,longitude=116),telemetry=dict(latitude=39,longitude=116,altitude=120,gps_fix=3),updated_at={k:now for k in ('latitude','longitude','altitude','gps_fix')},execution_binding=dict(task_id='TASK-1',execution_id='exec',algorithm=dict(experiment_id='a'*32,compute=config)))

def test_telemetry_recompute_staleness_and_archival(client):
    now=time.time();state=telemetry_state(now)
    first=algorithm_execution.sample(state,now);state['algorithm_live']=first
    assert first['position']==[0,0,120] and first['result']['status']=='ok'
    assert algorithm_execution.sample(state,now+.5)==first
    state['telemetry']['longitude']+=.01
    state['updated_at']={k:now+2 for k in state['updated_at']}
    second=algorithm_execution.sample(state,now+2)
    assert second['position'][0]>800 and second!=first
    assert algorithm_execution.sample(state,now+6)['status']=='stale'
    state['track']=[dict(at=now,compute=first),dict(at=now+2,compute=second)]
    state['patrol']={'status':'completed'};flight_records.begin(state)
    archived=client.get('/api/sitl/records/'+state['record_id']).json()
    assert archived['algorithm']['experiment_id']=='a'*32
    assert archived['track'][0]['compute']==first and archived['track'][1]['compute']==second
    assert archived['business_task_id']=='TASK-1'

def test_changed_airspace_blocks_application(client):
    from app import business,incidents
    run=experiment(client)
    with business.connect() as db:
        space=incidents.airspace(db);space['revision']+=1
        business.put(db,'airspace',space)
    assert client.get('/api/algorithms/runs/'+run['id']+'/mission').status_code==409
