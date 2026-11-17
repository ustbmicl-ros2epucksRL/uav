import time
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import sim,station

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_DATA_DIR',str(tmp_path));sim.reset();station.CLOCK['running']=False
    return TestClient(app)

def test_command_receipt_idempotency_and_hardware_isolation(client):
    body=dict(command_id='one',command='arm',source='simulation',expires_at=time.time()+30)
    r=client.post('/api/devices/UAV-01/commands',json=body).json()
    assert r['status']=='acknowledged' and sim.get('UAV-01').armed
    assert client.post('/api/devices/UAV-01/commands',json=body).json()==r
    assert client.get('/api/devices/commands/one/receipt').json()==r
    assert client.post('/api/devices/UAV-02/commands',json=body).status_code==409
    body.update(command_id='hw',source='hardware',command='disarm')
    assert client.post('/api/devices/UAV-01/commands',json=body).status_code==501
    assert sim.get('UAV-01').armed

def test_expired_and_rejected_commands(client):
    body=dict(command_id='old',command='auto',expires_at=time.time()-1)
    assert client.post('/api/devices/UAV-01/commands',json=body).status_code==409
    body.update(command_id='reject',expires_at=time.time()+30)
    assert client.post('/api/devices/UAV-01/commands',json=body).json()['status']=='rejected'

def test_video_session_frames_and_telemetry(client):
    t=client.get('/api/devices/UAV-01/telemetry').json();assert t['source']=='simulation' and t['valid']
    s=client.post('/api/devices/UAV-01/video/sessions',json={}).json()
    r=client.get(s['playback_url']);assert r.status_code==200 and 'SIMULATION' in r.text
    sim.get('UAV-01').position[2]=99
    assert '99.0m' in client.get(s['playback_url']).text
    assert client.post('/api/devices/UAV-01/video/sessions',json={'source':'hardware'}).status_code==501
    assert client.post('/api/devices/UAV-02/video/sessions',json={}).status_code==409
    assert client.get('/api/devices/video/missing/frame').status_code==410
