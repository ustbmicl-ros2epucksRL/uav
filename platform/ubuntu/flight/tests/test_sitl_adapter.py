import time
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app import sitl

class Wire:
    def __init__(self,result):
        self.result=result;self.sent=False;self.calls=0;self.mav=self
    def command_long_send(self,*args):self.sent=True;self.calls+=1
    def recv_match(self,**kwargs):
        if not self.sent:return None
        self.sent=False
        return SimpleNamespace(get_type=lambda:'COMMAND_ACK',get_srcSystem=lambda:1,get_srcComponent=lambda:1,command=400,result=self.result)

@pytest.mark.parametrize('result,expected',[(0,'acknowledged'),(2,'rejected')])
def test_ack_and_idempotency(monkeypatch,result,expected):
    wire=Wire(result);monkeypatch.setattr(sitl,'LINK',wire)
    monkeypatch.setattr(sitl,'STATE',{'receipts':{},'telemetry':{}})
    monkeypatch.setattr(sitl,'status',lambda:{'connected':True})
    body=sitl.Command(command_id='ack',command='arm',expires_at=time.time()+30)
    r=sitl.command(body)
    assert r['status']==expected and r['mav_result']==result
    assert sitl.command(body)==r and wire.calls==1
    with pytest.raises(HTTPException):sitl.command(body.model_copy(update={'command':'disarm'}))

def test_missing_binary_never_falls_back(monkeypatch):
    monkeypatch.setattr(sitl,'PROCESS',None)
    monkeypatch.setenv('ARDUPILOT_SITL_BINARY','/missing/arducopter')
    with pytest.raises(HTTPException) as exc:sitl.start()
    assert exc.value.status_code==503

def test_completion_requires_final_land_and_observed_ground():
    state={'patrol':{'status':'patrolling','final_land_entered':True,'waypoints':[0]*9},
           'telemetry':{'armed':False,'landed_state':1,'altitude':0,'reached':7}}
    assert sitl.patrol_completed(state)
    state['telemetry']['landed_state']=2
    assert not sitl.patrol_completed(state)
    state['telemetry']['landed_state']=1
    state['patrol']['status']='interrupted'
    assert not sitl.patrol_completed(state)


def test_patrol_detours_barrier_without_removing_safety_checks():
    from app import disposition, sim
    zone=dict(id='TEMP-BARRIER',x=450,y=100,w=80,d=200,z_min=0,z_max=300)
    boxes=disposition.obstacles([], [zone])
    points=[[0,0,0],[0,0,180],[200,200,180],[700,200,180],[700,700,180],[200,700,180],[200,200,180],[0,0,180],[0,0,0]]
    assert not disposition.check(points,boxes,sim.FENCE,10,100)['passed']
    route,check=sitl.safe_patrol_route(points,boxes,sim.FENCE,100)
    assert check['passed'] and len(route)>len(points)
    assert all(p in route for p in points)
    assert all(disposition.clear(a,b,boxes) for a,b in zip(route,route[1:]))
    with pytest.raises(HTTPException):sitl.safe_patrol_route(points,boxes,sim.FENCE,20)
    blocked=disposition.obstacles([], [dict(zone,x=0,y=0,w=2000,d=2000)])
    with pytest.raises(HTTPException):sitl.safe_patrol_route(points,blocked,sim.FENCE,100)


def test_business_command_requires_matching_execution_and_cannot_restart_completed(monkeypatch):
    wire=Wire(0);monkeypatch.setattr(sitl,'LINK',wire)
    monkeypatch.setattr(sitl,'STATE',{'receipts':{},'telemetry':{},'execution_binding':{'execution_id':'exec-1'},'patrol':{'status':'uploaded'}})
    monkeypatch.setattr(sitl,'status',lambda:{'connected':True})
    body=sitl.Command(command_id='business-arm',command='arm',expires_at=time.time()+30)
    with pytest.raises(HTTPException):sitl.command(body)
    with pytest.raises(HTTPException):sitl.command(body.model_copy(update={'execution_id':'exec-2'}))
    assert wire.calls==0
    assert sitl.command(body.model_copy(update={'execution_id':'exec-1'}))['status']=='acknowledged'
    sitl.STATE['patrol']['status']='completed'
    with pytest.raises(HTTPException):sitl.command(body.model_copy(update={'execution_id':'exec-1','command_id':'new-arm'}))
    assert wire.calls==1
