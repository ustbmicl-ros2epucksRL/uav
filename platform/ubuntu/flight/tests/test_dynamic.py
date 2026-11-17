import math
import pytest
from fastapi.testclient import TestClient
from app import sim, dynamic, business, disposition, incidents, station, route_plan, follow
from app.main import app


@pytest.fixture
def api(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(route_plan,'scene_record',lambda _:dict(buildings=[]))
    monkeypatch.setattr(follow,'public',lambda:dict(state='idle',estimate=None))
    sim.reset();station.CLOCK.update(running=False,speed=1)
    sim.get('UAV-02').position=[1900,1900,0]
    return TestClient(app)


def managed(ident='UAV-01',position=(800,200,100),goal=(200,200,0),command='rtl'):
    v=sim.get(ident);v.position=list(position);v.home=list(goal);v.armed=True;v.mode='RTL' if command=='rtl' else 'LAND'
    v.done=False;v.battery=100;v.disposition_route=[list(goal)];v.disposition_wp=0;v.disposition_reason=command
    v.disposition_scene='urban';v.disposition_incident='DYN-'+ident;v.dynamic_guard=dict(state='pending',history=[],generation=0)
    item=dict(id=v.disposition_incident,boot=business.BOOT,status='acting',action=dict(task_id=None,decision=dict(vehicle_id=ident,command=command),status='accepted'))
    with business.connect() as db:business.put(db,'incident:'+item['id'],item)
    return v


def update_space(api,zones):
    old=api.get('/api/business/airspace').json()
    response=api.put('/api/business/airspace',json=dict(revision=old['revision'],zones=zones,landing_sites=old['landing_sites']))
    assert response.status_code==200,response.text
    return response.json()


def test_exact_time_prediction_catches_between_tick_crossing():
    tracks=[dict(id='cross',points=dynamic.project([50,0,100],[[0,0,100]],50),radius=10)]
    hits=dynamic.conflicts([[0,0,100],[50,0,100]],50,tracks,horizon=1)
    assert hits[0]['separation_m']==0 and hits[0]['seconds']==.5


def test_new_aircraft_replans_before_next_step_and_closes(api):
    v=managed()
    response=api.post('/api/business/simulation-traffic',json=dict(id='SIM-cross',position=dict(x=500,y=200,z=50)))
    assert response.status_code==200
    assert v.dynamic_guard['generation']>=1
    assert v.dynamic_guard['state']=='replanned'
    other=sim.get('SIM-cross')
    for _ in range(100):
        old=list(v.position);api.post('/api/tick?steps=1')
        assert not dynamic.conflicts([old,v.position],math.dist(old,v.position) or 1,[dict(id=other.id,points=[other.position,other.position],radius=50)],horizon=1)
        if v.done:break
    assert v.done and v.done_reason=='rtl'
    assert api.post('/api/business/incidents/'+v.disposition_incident+'/sync').json()['status']=='closed'


def test_live_zone_change_replans_and_actual_segments_stay_out(api):
    v=managed();api.post('/api/tick?steps=2')
    zone=dict(id='new-zone',x=450,y=100,w=80,d=200,z_min=0,z_max=300)
    space=update_space(api,[zone]);assert v.dynamic_guard['generation']>=1
    boxes=disposition.obstacles([],space['zones'])
    for _ in range(100):
        before=list(v.position);api.post('/api/tick?steps=1')
        assert not any(disposition.intersects(before,v.position,b) for b in boxes)
        if v.done:break
    assert v.done and v.position==v.home
    assert any(e['airspace_revision']==space['revision'] for e in v.dynamic_guard['history'])


def test_covered_current_location_holds_without_release_then_resumes(api):
    v=managed(command='land',position=(800,200,120),goal=(800,200,0))
    update_space(api,[dict(id='covered',x=700,y=100,w=200,d=200,z_min=0,z_max=300)])
    before=list(v.position);api.post('/api/tick?steps=2')
    assert v.position==before and v.armed and not v.done
    assert v.dynamic_guard['state']=='holding' and not v.dynamic_guard['hold_safe']
    assert api.post('/api/business/incidents/'+v.disposition_incident+'/sync').json()['action']['status']=='holding'
    update_space(api,[]);api.post('/api/tick?steps=20')
    assert v.done_reason=='land' and not v.armed


def test_two_managed_aircraft_share_one_pre_move_snapshot(api):
    a=managed();b=managed('UAV-02',(200,200,100),(800,200,0))
    for _ in range(120):
        old_a=list(a.position);old_b=list(b.position)
        api.post('/api/tick?steps=1')
        hits=dynamic.conflicts([old_a,a.position],math.dist(old_a,a.position) or 1,[dict(id=b.id,points=[old_b,b.position],radius=50)],horizon=1)
        assert not hits
        if a.done and b.done:break
    assert a.done and b.done
    assert a.dynamic_guard['generation']+b.dynamic_guard['generation']>=1


def test_filtered_observation_is_used_without_truth_and_failure_stops(api,monkeypatch):
    v=managed()
    monkeypatch.setattr(follow,'public',lambda:dict(estimate=[805,200,100],velocity=[0,0,0],uncertainty_m=10,state='coasting'))
    api.post('/api/tick?steps=1')
    assert v.dynamic_guard['state']=='holding' and not v.dynamic_guard['hold_safe']
    assert v.dynamic_guard['hold_conflicts'][0]['target']=='filtered-target'
    monkeypatch.setattr(follow,'public',lambda:dict(estimate=None))
    monkeypatch.setattr(route_plan,'scene_record',lambda _: (_ for _ in ()).throw(RuntimeError('unavailable')))
    api.post('/api/tick?steps=1')
    assert v.dynamic_guard['reasons']==[dict(kind='guard_unavailable')]
    assert v.position==[800,200,100]


def test_remove_only_injected_traffic_and_idempotency(api):
    assert api.delete('/api/business/simulation-traffic/UAV-01').status_code==404
    body=dict(id='SIM-1',position=dict(x=500,y=400,z=80))
    assert api.post('/api/business/simulation-traffic',json=body).status_code==200
    assert api.post('/api/business/simulation-traffic',json=body).status_code==409
    assert api.delete('/api/business/simulation-traffic/SIM-1').status_code==200


def test_static_conflict_position_is_first_clearance_contact():
    box=dict(id='zone',kind='no_fly',lower=[40,-10,0],upper=[60,10,200])
    report=disposition.check([[0,0,100],[100,0,100],[100,0,0]],[box],dict(x0=0,x1=200,y0=-20,y1=20,z0=0,z1=300))
    assert report['reasons'][0]['position']==[40,0,100]
    assert disposition.intersection_point([0,20,100],[100,20,100],box) is None
