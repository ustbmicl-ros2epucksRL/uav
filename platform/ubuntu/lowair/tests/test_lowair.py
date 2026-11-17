import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import main, store

# Load the actual flight app under a separate package name for in-process HTTP integration.
root = Path(__file__).resolve().parents[2] / 'flight/app'
spec = importlib.util.spec_from_file_location('flight_integration', root / '__init__.py', submodule_search_locations=[str(root)])
package = importlib.util.module_from_spec(spec)
sys.modules['flight_integration'] = package
spec.loader.exec_module(package)
from flight_integration import main as flight, sim, station, business

ROUTE = [{'x':200,'y':200,'z':0},{'x':200,'y':200,'z':120},{'x':200,'y':200,'z':0}]


def body():
    return dict(applicant='集成测试',pilot_id='P-01',vehicle_id='UAV-01',scene='urban',purpose='原地升降验证',route=ROUTE)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('LOWAIR_DATA_DIR', str(tmp_path/'service'))
    monkeypatch.setenv('FLIGHT_DATA_DIR', str(tmp_path/'flight'))
    monkeypatch.setenv('SENSE_DATA_DIR', str(tmp_path/'planning'))
    sim.reset()
    station.CLOCK.update(running=False,speed=1)
    client = TestClient(flight.app)
    def request(method,path,body=None):
        response = client.request(method,path,json=body)
        if response.status_code>=400:
            raise HTTPException(response.status_code,response.json().get('detail'))
        return response.json()
    monkeypatch.setattr(main,'flight_request',request)
    return TestClient(main.app),client


def scheduled(api):
    service,_=api
    response=service.post('/api/applications',json=body())
    assert response.status_code==200,response.text
    ident=response.json()['id']
    response=service.post(f'/api/applications/{ident}/review',json=dict(decision='approve',reviewer='测试值班'))
    assert response.status_code==200,response.text
    task=service.post('/api/tasks/from-application/'+ident).json()
    return task


def test_resource_persistence_revision_and_binding(api):
    service,_=api
    resources=service.get('/api/resources').json()['resources']
    assert len(resources)==8
    row=next(r for r in resources if r['id']=='P-01')
    row.pop('allocation');row['name']='更新飞手'
    assert service.put('/api/resources/P-01',json=row).status_code==200
    assert service.put('/api/resources/P-01',json=row).status_code==409
    assert TestClient(main.app).get('/api/resources').json()['resources'][1]['name']=='更新飞手'
    assert service.post('/api/applications',json={**body(),'pilot_id':'P-02'}).status_code==422


def test_concurrent_approval_reserves_all_four_and_cancel_releases(api):
    service,_=api
    a=[service.post('/api/applications',json=body()).json()['id'] for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda ident:service.post(f'/api/applications/{ident}/review',json=dict(decision='approve',reviewer='测试')).status_code,a))
    assert sorted(results)==[200,409]
    rows=service.get('/api/resources').json()['resources']
    assert sum(r['allocation'] is not None for r in rows)==4
    winner=a[results.index(200)]
    assert service.post(f'/api/applications/{winner}/cancel').status_code==200
    assert not any(r['allocation'] for r in service.get('/api/resources').json()['resources'])


def test_reject_and_changed_resource_cannot_dispatch(api):
    service,_=api
    a=service.post('/api/applications',json=body()).json()['id']
    service.post(f'/api/applications/{a}/review',json=dict(decision='reject',reviewer='测试'))
    assert service.post('/api/tasks/from-application/'+a).status_code==409
    assert service.get('/api/portal/portal').json()['windows']==[]
    assert service.post('/api/reset').status_code==403


def test_real_service_flight_validation_execution_and_release(api):
    service,ground=api
    task=scheduled(api);ident=task['id']
    assert service.post(f'/api/tasks/{ident}/dispatch').status_code in (404,409)
    validation=service.post(f'/api/tasks/{ident}/validate')
    assert validation.status_code==200,validation.text
    assert validation.json()['result']['passed'],validation.text
    assert service.post(f'/api/tasks/{ident}/dispatch').status_code==200
    snapshot=ground.get('/api/vehicles/UAV-01').json()
    assert snapshot['business_task_id']==ident
    assert snapshot['business_validation_id']==validation.json()['id']
    assert ground.post('/api/vehicles/UAV-01/mission',json={'waypoints':ROUTE}).status_code==409
    ground.post('/api/tick?steps=100')
    done=service.post(f'/api/tasks/{ident}/sync')
    assert done.json()['status']=='completed',done.text
    assert not any(r['allocation'] for r in service.get('/api/resources').json()['resources'])
    assert service.get('/api/performance').json()['completed']==1
    assert service.get('/api/portal/mobile?pilot_id=P-01').json()['tasks'][0]['id']==ident


def test_lost_response_retry_does_not_restart_flight(api,monkeypatch):
    service,ground=api
    task=scheduled(api);ident=task['id']
    service.post(f'/api/tasks/{ident}/validate')
    original=main.flight_request
    def lost(method,path,body=None):
        result=original(method,path,body)
        if '/dispatch/' in path:raise HTTPException(502,'response lost')
        return result
    monkeypatch.setattr(main,'flight_request',lost)
    assert service.post(f'/api/tasks/{ident}/dispatch').status_code==502
    ground.post('/api/tick?steps=2')
    before=ground.get('/api/vehicles/UAV-01').json()['seconds']
    monkeypatch.setattr(main,'flight_request',original)
    assert service.post(f'/api/tasks/{ident}/dispatch').status_code==200
    assert ground.get('/api/vehicles/UAV-01').json()['seconds']==before


def test_stale_validation_and_no_hidden_takeoff(api):
    service,ground=api
    ident=scheduled(api)['id'];service.post(f'/api/tasks/{ident}/validate')
    sim.get('UAV-01').position[0]+=5
    response=service.post(f'/api/tasks/{ident}/dispatch')
    assert response.status_code==409
    assert not sim.get('UAV-01').armed


def test_restart_uncertainty_keeps_resources_and_never_restarts(api,monkeypatch):
    service,ground=api
    ident=scheduled(api)['id'];service.post(f'/api/tasks/{ident}/validate');service.post(f'/api/tasks/{ident}/dispatch')
    monkeypatch.setattr(business,'BOOT','restarted')
    result=service.post(f'/api/tasks/{ident}/sync').json()
    assert result['status']=='uncertain'
    assert sum(bool(r['allocation']) for r in service.get('/api/resources').json()['resources'])==4


def test_safety_event_dedup_and_acknowledgement(api):
    service,ground=api
    ground.post('/api/tick?steps=4')
    assert service.post('/api/safety/refresh').status_code==200
    service.post('/api/safety/refresh')
    events=service.get('/api/events').json()['events']
    alerts=[e for e in events if e['kind']=='identity_unconfirmed']
    assert len(alerts)==1
    assert alerts[0]['evidence']['track']['identity']=='身份未确认无人机'
    assert service.post('/api/events/'+alerts[0]['id']+'/ack',json={'actor':'测试','note':'核验计划中'}).status_code==200
    assert service.get('/api/events').json()['events'][-1]['status']=='acknowledged'


def test_map_situation_joins_execution_without_overwriting_business_status(api, monkeypatch):
    service, ground = api
    ident = scheduled(api)['id']
    service.post(f'/api/tasks/{ident}/validate')
    service.post(f'/api/tasks/{ident}/dispatch')
    ground.post('/api/tick?steps=100')
    import httpx
    class Response:
        def raise_for_status(self): pass
        def json(self): return service.get('/api/tasks').json()
    monkeypatch.setattr(httpx, 'get', lambda *args, **kwargs: Response())
    snapshot = ground.get('/api/business/situation').json()
    assert snapshot['service_available'] is True
    task = snapshot['tasks'][0]
    assert task['service_status'] == 'flying'
    assert task['execution_status'] == 'completed'
    assert task['validation_passed'] is True
    assert task['anchor'] == station.config()
    assert service.get('/api/tasks/'+ident).json()['status'] == 'flying'
    service.post(f'/api/tasks/{ident}/sync')
    assert ground.get('/api/business/situation').json()['tasks'][0]['service_status'] == 'completed'


def test_situation_service_failure_preserves_telemetry_without_stale_tasks(api, monkeypatch):
    _, ground = api
    import httpx
    def offline(*args, **kwargs):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx, 'get', offline)
    snapshot = ground.get('/api/business/situation').json()
    assert snapshot['service_available'] is False
    assert snapshot['tasks'] == []
    assert len(snapshot['vehicles']) == 2
    assert snapshot['source'] == 'simulation'


@pytest.mark.parametrize('command', ['rtl', 'land'])
def test_incident_disposition_roundtrip_and_idempotency(api, monkeypatch, command):
    from flight_integration import incidents
    service, ground = api
    ident=scheduled(api)['id']
    service.post(f'/api/tasks/{ident}/validate');service.post(f'/api/tasks/{ident}/dispatch')
    ground.post('/api/tick?steps=3')
    v=sim.get('UAV-01');other=sim.get('UAV-02')
    other.position=[v.position[0]+10,v.position[1],v.position[2]];other.armed=True;other.mode='LOITER'
    snapshot=business.safety_snapshot()
    snapshot.update(anchor=station.config(),tasks=service.get('/api/tasks').json()['tasks'])
    incident=next(i for i in incidents.capture(snapshot) if i['kind']=='predicted_conflict')
    assert incident['evidence']['tasks'][0]['validation']['passed'] is True
    assert incident['evidence']['trajectories']['UAV-01']['points']
    url='/api/business/incidents/'+incident['id']
    decision=dict(vehicle_id='UAV-01',command=command,actor='测试值班',note='核验任务后仿真处置')
    issued=ground.post(url+'/act',json=decision)
    assert issued.status_code==200,issued.text
    assert ground.post(url+'/act',json={**decision,'command':'land' if command=='rtl' else 'rtl'}).status_code==409
    ground.post('/api/tick?steps=1')
    before=sim.snapshot(v)
    assert ground.post(url+'/act',json=decision).status_code==200
    assert sim.snapshot(v)==before
    assert v.dynamic_guard['state']=='holding'
    other.position=[1800,1800,100]  # Resolve the original ten-metre conflict before completion.
    ground.post('/api/tick?steps=100')
    import httpx
    def remote_post(path,**kwargs):
        return service.post('/api/tasks/'+ident+'/sync')
    monkeypatch.setattr(httpx,'post',remote_post)
    result=ground.post(url+'/sync')
    assert result.status_code==200,result.text
    assert result.json()['status']=='closed'
    assert result.json()['action']['telemetry']['done_reason']==command
    assert service.get('/api/tasks/'+ident).json()['status']=='aborted'
    assert not any(r['allocation'] for r in service.get('/api/resources').json()['resources'])


def test_unknown_target_cannot_command_unrelated_aircraft(api):
    from flight_integration import incidents
    _,ground=api
    ground.post('/api/tick?steps=4')
    snapshot=business.safety_snapshot();snapshot.update(anchor=station.config(),tasks=[])
    incident=next(i for i in incidents.capture(snapshot) if i['kind']=='identity_unconfirmed')
    response=ground.post('/api/business/incidents/'+incident['id']+'/act',json=dict(vehicle_id='UAV-01',command='rtl',actor='测试',note='不应执行'))
    assert response.status_code==409
    assert not sim.get('UAV-01').armed


def test_incident_rejects_changed_task_and_keeps_restart_uncertain(api, monkeypatch):
    from flight_integration import incidents
    service,ground=api
    ident=scheduled(api)['id'];service.post(f'/api/tasks/{ident}/validate');service.post(f'/api/tasks/{ident}/dispatch');ground.post('/api/tick?steps=3')
    v=sim.get('UAV-01');other=sim.get('UAV-02');other.position=[v.position[0]+10,v.position[1],v.position[2]];other.armed=True;other.mode='LOITER'
    snapshot=business.safety_snapshot();snapshot.update(anchor=station.config(),tasks=service.get('/api/tasks').json()['tasks'])
    item=next(i for i in incidents.capture(snapshot) if i['kind']=='predicted_conflict')
    url='/api/business/incidents/'+item['id'];decision=dict(vehicle_id=v.id,command='rtl',actor='测试',note='重启测试')
    v.business_task_id='TASK-other'
    assert ground.post(url+'/act',json=decision).status_code==409
    v.business_task_id=ident
    assert ground.post(url+'/act',json=decision).status_code==200
    monkeypatch.setattr(business,'BOOT','new-process')
    result=ground.post(url+'/sync').json()
    assert result['action']['status']=='uncertain'
    assert result['status']!='closed'
    assert sum(bool(r['allocation']) for r in service.get('/api/resources').json()['resources'])==4


def test_completed_disposition_waits_for_business_sync(api, monkeypatch):
    from flight_integration import incidents
    import httpx
    service,ground=api
    ident=scheduled(api)['id'];service.post(f'/api/tasks/{ident}/validate');service.post(f'/api/tasks/{ident}/dispatch');ground.post('/api/tick?steps=3')
    v=sim.get('UAV-01');other=sim.get('UAV-02');other.position=[v.position[0]+10,v.position[1],v.position[2]];other.armed=True;other.mode='LOITER'
    snapshot=business.safety_snapshot();snapshot.update(anchor=station.config(),tasks=service.get('/api/tasks').json()['tasks'])
    item=next(i for i in incidents.capture(snapshot) if i['kind']=='predicted_conflict')
    url='/api/business/incidents/'+item['id']
    ground.post(url+'/act',json=dict(vehicle_id=v.id,command='land',actor='测试',note='业务服务中断演练'))
    other.position=[1800,1800,100]
    ground.post('/api/tick?steps=100')
    def unavailable(*args,**kwargs):raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx,'post',unavailable)
    result=ground.post(url+'/sync').json()
    assert result['action']['status']=='completed'
    assert result['status']=='acting' and not result['action']['service_synced']
    assert service.get('/api/tasks/'+ident).json()['status']=='flying'
    monkeypatch.setattr(httpx,'post',lambda *args,**kwargs:service.post('/api/tasks/'+ident+'/sync'))
    assert ground.post(url+'/sync').json()['status']=='closed'


@pytest.mark.parametrize('command',['rtl','land'])
def test_unsafe_disposition_selects_alternative_and_closes_business(api, monkeypatch, command):
    from flight_integration import incidents, disposition, route_plan
    import httpx
    service,ground=api
    ident=scheduled(api)['id'];service.post(f'/api/tasks/{ident}/validate');service.post(f'/api/tasks/{ident}/dispatch')
    v=sim.get('UAV-01');v.position=[800,200,180];v.mode='LOITER'
    other=sim.get('UAV-02');other.position=[810,200,180];other.armed=True;other.mode='LOITER'
    buildings=[dict(id='wall',x=450,y=100,w=80,d=200,h=200)] if command=='rtl' else [dict(id='roof',x=750,y=150,w=100,d=100,h=80)]
    monkeypatch.setattr(route_plan,'scene_record',lambda scene:dict(buildings=buildings))
    snapshot=business.safety_snapshot();snapshot.update(anchor=station.config(),tasks=service.get('/api/tasks').json()['tasks'])
    item=next(i for i in incidents.capture(snapshot) if i['kind']=='predicted_conflict');url='/api/business/incidents/'+item['id']
    decision=dict(vehicle_id=v.id,command=command,actor='测试',note='绕障方案验收')
    before=sim.snapshot(v)
    assert ground.post(url+'/act',json=decision).status_code==409
    assert sim.snapshot(v)==before
    preview=ground.post(url+'/preview',json=dict(vehicle_id=v.id,command=command)).json()
    assert not preview['direct']['passed'] and preview['choices']
    chosen=preview['choices'][0]
    payload=dict(**decision,plan_id=preview['id'],route_id=chosen['id'])
    assert ground.post(url+'/act',json=payload).status_code==200
    other.position=[1800,1800,100]
    boxes=disposition.obstacles(buildings,[])
    for _ in range(250):
        pos=list(v.position);ground.post('/api/tick?steps=1')
        assert not any(disposition.intersects(pos,v.position,box) for box in boxes)
        if v.done:break
    assert v.done_reason==command and v.position==chosen['route'][-1]
    monkeypatch.setattr(httpx,'post',lambda *args,**kwargs:service.post('/api/tasks/'+ident+'/sync'))
    closed=ground.post(url+'/sync').json()
    assert closed['status']=='closed' and closed['action']['safety']['check']['passed']
    assert service.get('/api/tasks/'+ident).json()['status']=='aborted'


def test_airspace_version_validation_and_stale_plan_block(api):
    from flight_integration import incidents
    service,ground=api
    space=ground.get('/api/business/airspace').json()
    payload={k:space[k] for k in ['revision','zones','landing_sites']}
    assert ground.put('/api/business/airspace',json={**payload,'zones':[dict(id='bad',x=1999,y=0,w=5,d=20)]}).status_code==422
    assert ground.put('/api/business/airspace',json=payload).status_code==200
    assert ground.put('/api/business/airspace',json=payload).status_code==409
    ident=scheduled(api)['id'];service.post(f'/api/tasks/{ident}/validate');service.post(f'/api/tasks/{ident}/dispatch');ground.post('/api/tick?steps=3')
    v=sim.get('UAV-01');other=sim.get('UAV-02');other.position=[210,200,v.position[2]];other.armed=True;other.mode='LOITER'
    snapshot=business.safety_snapshot();snapshot.update(anchor=station.config(),tasks=service.get('/api/tasks').json()['tasks'])
    item=next(i for i in incidents.capture(snapshot) if i['kind']=='predicted_conflict');url='/api/business/incidents/'+item['id']
    preview=ground.post(url+'/preview',json=dict(vehicle_id=v.id,command='land')).json()
    assert ground.put('/api/business/airspace',json={**payload,'revision':1}).status_code==200
    v.position[0]+=1
    decision=dict(vehicle_id=v.id,command='land',actor='测试',note='旧方案必须拒绝',plan_id=preview['id'],route_id=preview['choices'][0]['id'])
    assert ground.post(url+'/act',json=decision).status_code==409
    assert v.mode=='AUTO' and not getattr(v,'disposition_route',None)


def test_direct_ground_station_cannot_bypass_no_fly_check(api):
    _,ground=api
    space=ground.get('/api/business/airspace').json()
    payload={k:space[k] for k in ['revision','zones','landing_sites']}
    payload['zones']=[dict(id='barrier',x=480,y=100,w=60,d=200,z_min=0,z_max=300)]
    assert ground.put('/api/business/airspace',json=payload).status_code==200
    v=sim.get('UAV-01');v.position=[800,200,100];v.armed=True;v.mode='LOITER'
    before=sim.snapshot(v)
    response=ground.post('/api/vehicles/UAV-01/command',json={'cmd':'rtl'})
    assert response.status_code==409
    assert sim.snapshot(v)==before


def test_dynamic_hold_keeps_business_resources_and_recovers_to_close(api, monkeypatch):
    from flight_integration import incidents
    import httpx
    service,ground=api
    ident=scheduled(api)['id'];service.post(f'/api/tasks/{ident}/validate');service.post(f'/api/tasks/{ident}/dispatch');ground.post('/api/tick?steps=3')
    v=sim.get('UAV-01');other=sim.get('UAV-02');other.position=[210,200,v.position[2]];other.armed=True;other.mode='LOITER'
    snapshot=business.safety_snapshot();snapshot.update(anchor=station.config(),tasks=service.get('/api/tasks').json()['tasks'])
    item=next(i for i in incidents.capture(snapshot) if i['kind']=='predicted_conflict');url='/api/business/incidents/'+item['id']
    assert ground.post(url+'/act',json=dict(vehicle_id=v.id,command='land',actor='动态验收',note='验证空域突变')).status_code==200
    other.position=[1800,1800,100]
    original=ground.get('/api/business/airspace').json()
    payload={k:original[k] for k in ['revision','zones','landing_sites']}
    payload['zones']=[dict(id='temporary',x=100,y=100,w=200,d=200,z_min=0,z_max=300)]
    assert ground.put('/api/business/airspace',json=payload).status_code==200
    ground.post('/api/tick?steps=2')
    paused=ground.post(url+'/sync').json()
    assert paused['action']['status']=='holding' and paused['status']=='acting'
    service.post('/api/tasks/'+ident+'/sync')
    assert sum(bool(r['allocation']) for r in service.get('/api/resources').json()['resources'])==4
    payload.update(revision=1,zones=[])
    assert ground.put('/api/business/airspace',json=payload).status_code==200
    ground.post('/api/tick?steps=30')
    monkeypatch.setattr(httpx,'post',lambda *args,**kwargs:service.post('/api/tasks/'+ident+'/sync'))
    assert ground.post(url+'/sync').json()['status']=='closed'
    assert not any(r['allocation'] for r in service.get('/api/resources').json()['resources'])


def test_business_sitl_execution_and_archive_binding(api, monkeypatch):
    import copy, time
    from flight_integration import sitl, flight_records
    service, ground = api
    telemetry=dict(latitude=39.05,longitude=115.98,altitude=0,gps_fix=6,battery=100,armed=False,landed_state=1)
    state=dict(telemetry=telemetry,updated_at={k:time.time() for k in telemetry},anchor=dict(latitude=39.05,longitude=115.98),receipts={},track=[],connected=True)
    monkeypatch.setattr(sitl,'STATE',state)
    monkeypatch.setattr(sitl,'status',lambda:copy.deepcopy(state))
    uploaded=[]
    def upload(body,business_mission=None):
        uploaded.append(business_mission)
        state.update(patrol={'status':'uploaded','route':business_mission['route']},execution_binding=business_mission['binding'])
        flight_records.begin(state)
    monkeypatch.setattr(sitl,'execute_upload',upload)
    request=body();request['route']=[dict(x=0,y=0,z=z) for z in [0,40,0]]
    application=service.post('/api/applications',json=request).json()['id']
    assert service.post(f'/api/applications/{application}/review',json=dict(decision='approve',reviewer='SITL 测试')).status_code==200
    task=service.post('/api/tasks/from-application/'+application).json();ident=task['id']
    validation=service.post(f'/api/tasks/{ident}/validate-sitl');assert validation.status_code==200,validation.text
    assert validation.json()['result']['passed'],validation.text
    assert service.post(f'/api/tasks/{ident}/dispatch').status_code==409
    prepared=service.post(f'/api/tasks/{ident}/prepare-sitl');assert prepared.status_code==200,prepared.text
    execution=prepared.json()['execution'];assert execution['status']=='prepared'
    assert not telemetry['armed'] and len(uploaded)==1
    assert service.post(f'/api/tasks/{ident}/prepare-sitl').json()['execution']['execution_id']==execution['execution_id']
    assert len(uploaded)==1
    record=ground.get('/api/sitl/records/'+execution['record_id']).json()
    assert record['business_task_id']==ident and record['execution_id']==execution['execution_id']
    assert record['patrol']['route']==[[0,0,0],[0,0,40],[0,0,0]]
    assert ground.get('/api/sitl/records',params={'task_id':ident}).json()['total']==1
    assert ground.get('/api/sitl/records?task_id=TASK-999999').json()['total']==0
    state['patrol']['status']='completed';state['track']=[dict(at=time.time(),latitude=39.05,longitude=115.98,altitude=0,roll=0,pitch=0,yaw=0,armed=False)]
    flight_records.checkpoint(state,force=True)
    synced=service.post(f'/api/tasks/{ident}/sync').json();assert synced['status']=='completed'
    state.clear();state.update(connected=False,telemetry={})
    historical=service.get(f'/api/tasks/{ident}/execution').json()
    assert historical['status']=='completed' and historical['record_id']==execution['record_id']
    assert 'record='+execution['record_id'] in historical['records'][0]['replay_url']
    import httpx
    monkeypatch.setattr(httpx,'get',lambda *args,**kwargs:service.get('/api/tasks'))
    situation=ground.get('/api/business/situation').json()
    linked=next(t for t in situation['tasks'] if t['id']==ident)
    assert linked['source']=='ardupilot-sitl' and linked['execution_status']=='completed'


def test_canonical_service_entry_and_assets(api):
    _, ground=api
    page=ground.get('/service/')
    assert page.status_code==200 and '低空区域飞行管理系统' in page.text
    assert './static/workflow.js' in page.text
    assert ground.get('/service/static/workflow.js').status_code==200
    assert '/static/workbench.js' not in ground.get('/?view=workbench').text
