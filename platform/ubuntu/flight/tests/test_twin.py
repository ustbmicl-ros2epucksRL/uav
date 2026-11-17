import copy
from fastapi.testclient import TestClient
from app.main import app
from app import twin


def sample():
    fields=dict(latitude=39.05,longitude=115.98,relative_altitude_m=40,roll_deg=1,pitch_deg=2,yaw_deg=30,groundspeed_mps=4,climb_mps=0,battery_percent=90,gps_fix=3,satellites=10)
    return dict(**fields,field_age_s={k:0 for k in fields},connected=True,listening=True,system_id=42,armed=True,url='udpin:0.0.0.0:14550',age_s=.1,session_id=1)


def test_external_projection_stale_gps_and_disconnect_never_fallback(monkeypatch):
    s=sample();monkeypatch.setattr(twin.mavlink_link,'status',lambda:copy.deepcopy(s));monkeypatch.setattr(twin,'TRACK',[]);monkeypatch.setattr(twin,'IDENTITY',None)
    c=TestClient(app);d=c.get('/api/twin').json()
    assert d['vehicle_id']=='MAV-42' and d['position_valid'] and not d['commands']
    assert d['source']=='external-mavlink' and d['video']['url'] is None
    s['field_age_s']['latitude']=5
    d=c.get('/api/twin').json();assert not d['position_valid'] and d['latitude'] is None and len(d['track'])==1
    s['connected']=False
    d=c.get('/api/twin').json();assert d['telemetry']['roll_deg'] is None and not d['connected']
    s['session_id']=2
    assert c.get('/api/twin').json()['track']==[]


def test_video_is_explicitly_bound_to_external_system(monkeypatch):
    s=sample();monkeypatch.setattr(twin.mavlink_link,'status',lambda:s)
    monkeypatch.setenv('TWIN_VIDEO_URL','http://127.0.0.1:9000/camera.mp4')
    monkeypatch.setenv('TWIN_VIDEO_SYSTEM_ID','41');assert not twin.snapshot()['video']['configured']
    monkeypatch.setenv('TWIN_VIDEO_SYSTEM_ID','42');assert twin.snapshot()['video']['url']
    s['connected']=False;assert twin.snapshot()['video']['url'] is None
    monkeypatch.setenv('TWIN_VIDEO_URL','http://user:pass@localhost/camera');assert not twin.snapshot()['video']['configured']
