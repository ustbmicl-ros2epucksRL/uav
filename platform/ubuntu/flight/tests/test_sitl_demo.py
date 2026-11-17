import copy
import time
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import sitl, sitl_demo as demo

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(demo,'JOB',None)
    monkeypatch.setattr(sitl,'STATE',{'telemetry':{},'receipts':{}})
    demo.CANCEL.clear()
    return TestClient(app)

def job():
    return dict(id='run-1',status='awaiting_resolution',phase='review',events=[],alerts=[
        dict(id='alert-1',status='open',source='synthetic-injection',video_evidence=None)
    ],receipts=[],result=None)

def test_resolve_requires_current_job_and_nonblank_evidence(client):
    demo.JOB=job()
    path='/api/sitl-demo/run-1/alerts/alert-1/resolve'
    assert client.post(path,json={'actor':' ','note':'核验'}).status_code==422
    assert client.post('/api/sitl-demo/stale/alerts/alert-1/resolve',json={'actor':'值班员','note':'核验'}).status_code==409
    r=client.post(path,json={'actor':'值班员','note':'确认演示注入'}).json()
    assert r['status']=='resolved' and not r['automatic'] and r['video_evidence'] is None
    assert client.post(path,json={'actor':'其他人','note':'重复请求'}).json()==r

def test_demo_owns_control_and_persisted_run_is_not_resumed(client):
    sitl.STATE['demo_owner']='run-1'
    assert client.post('/api/sitl/commands',json={'command_id':'arm','command':'arm','expires_at':time.time()+10}).status_code==409
    assert client.post('/api/sitl/patrol/upload',json={}).status_code==409
    demo.JOB=job();demo.persist();demo.JOB=None
    r=client.get('/api/sitl-demo').json()
    assert r['status']=='interrupted'
    result=client.get('/api/sitl-demo/run-1/result').json()
    assert result['result']['track']==[]

def test_complete_orchestrator_and_auto_resolution(client,monkeypatch):
    demo.JOB=job();demo.JOB.update(auto_resolve=True,status='running',phase='starting',alerts=[])
    sent=[]
    status={'running':False,'connected':True,'telemetry':{'latitude':39.05,'longitude':115.98,'altitude':180,'mode':'LOITER','speed':0,'landed_state':1,'gps_fix':6,'waypoint':3}}
    monkeypatch.setattr(sitl,'status',lambda:copy.deepcopy(status))
    monkeypatch.setattr(sitl,'start',lambda:None)
    monkeypatch.setattr(sitl,'execute_upload',lambda body:None)
    monkeypatch.setattr(sitl,'execute_command',lambda body:sent.append(body.command) or dict(status='acknowledged',request=body.model_dump()))
    monkeypatch.setattr(sitl,'patrol_result',lambda:{'track':[{'latitude':39.05}],'patrol':{'status':'completed'}})
    monkeypatch.setattr(demo,'wait_until',lambda predicate,timeout,description:copy.deepcopy(status))
    monkeypatch.setattr(demo.CANCEL,'wait',lambda seconds:False)
    demo.run()
    assert sent==['guided','arm','takeoff','auto','loiter','auto']
    assert demo.JOB['status']=='completed'
    assert demo.JOB['alerts'][0]['automatic']
    assert demo.JOB['alerts'][0]['source']=='synthetic-injection'
    assert client.get('/api/sitl-demo/run-1/result').json()['result']['track']

def test_unknown_ack_stops_flow(client,monkeypatch):
    demo.JOB=job();demo.JOB.update(auto_resolve=True,status='running',alerts=[])
    monkeypatch.setattr(sitl,'status',lambda:{'running':False,'connected':True,'telemetry':{}})
    monkeypatch.setattr(sitl,'start',lambda:None)
    monkeypatch.setattr(sitl,'execute_upload',lambda body:None)
    monkeypatch.setattr(demo,'wait_until',lambda *args:None)
    calls=[]
    monkeypatch.setattr(sitl,'execute_command',lambda body:calls.append(body.command) or {'status':'unknown'})
    monkeypatch.setattr(sitl,'patrol_result',lambda:{'track':[]})
    demo.run()
    assert calls==['guided'] and demo.JOB['status']=='failed'

def test_cancel_requests_hover_and_blocks_resolution(client,monkeypatch):
    demo.JOB=job()
    monkeypatch.setattr(sitl,'status',lambda:{'connected':True,'telemetry':{'armed':True}})
    calls=[]
    monkeypatch.setattr(sitl,'execute_command',lambda body:calls.append(body.command) or {'status':'acknowledged'})
    r=client.post('/api/sitl-demo/cancel',json={}).json()
    assert r['hover_receipt']['status']=='acknowledged' and calls==['loiter']
    assert client.post('/api/sitl-demo/run-1/alerts/alert-1/resolve',json={'actor':'值班员','note':'核验'}).status_code==409

def test_finishing_worker_blocks_new_run(client,monkeypatch):
    demo.JOB=job();demo.JOB['status']='completed'
    monkeypatch.setattr(demo,'WORKER',type('Worker',(),{'is_alive':lambda self:True})())
    assert client.post('/api/sitl-demo/start',json={}).status_code==409
