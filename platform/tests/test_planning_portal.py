import copy
from test_platform import client


def task_payload():
    return dict(name='园区试验', scene='urban', volume=dict(bounds=[700,800,700,800,120,160], cell=20), budget=50)


def test_five_pages_and_persistent_task_revision(client):
    for path in ('tasks','resources','diagnosis','planning','plans'):
        assert client.get('/'+path).status_code == 200
    t = client.post('/api/workflow/tasks', json=task_payload()).json()
    assert client.get('/api/workflow/tasks').json()[0]['id'] == t['id']
    changed = task_payload() | dict(name='修改后', revision=t['revision'])
    assert client.put('/api/workflow/tasks/'+t['id'], json=changed).status_code == 200
    assert client.put('/api/workflow/tasks/'+t['id'], json=changed).status_code == 409
    assert client.post('/api/workflow/tasks', json=task_payload() | {'volume':{'bounds':[900,700,0,100,100,120]}}).status_code == 422


def test_resources_constraints_and_version(client):
    url='/api/workflow/resources/urban'
    r=client.get(url).json()
    bad=copy.deepcopy(r);bad['devices'][0]['z']=301
    assert client.put(url,json=bad).status_code == 422
    bad=copy.deepcopy(r);bad['devices'][0]['site_id']='missing'
    assert client.put(url,json=bad).status_code == 422
    bad=copy.deepcopy(r);bad['candidates'][0]['id']=bad['devices'][0]['id']
    assert client.put(url,json=bad).status_code == 422
    r['sites'][0]['name']='已修改站址'
    saved=client.put(url,json=r)
    assert saved.status_code == 200, saved.text
    assert client.get(url).json()['sites'][0]['name']=='已修改站址'
    assert client.put(url,json=r).status_code == 409
    assert client.get('/api/scenes/urban').json()['revision']==saved.json()['revision']


def test_end_to_end_snapshot_export_and_stale_confirmation(client):
    t=client.post('/api/workflow/tasks',json=task_payload()).json()
    base='/api/workflow/tasks/'+t['id']
    diagnosis=client.post(base+'/diagnose',json={})
    assert diagnosis.status_code==200,diagnosis.text
    result=client.post(base+'/plan',json={})
    assert result.status_code==200,result.text
    p=result.json();url='/api/workflow/plans/'+p['id']
    assert p['before']==diagnosis.json()['stats']
    assert 'snapshot' in p and len(client.get('/api/workflow/plans').json())==1
    assert not client.get(url).json()['stale']
    csv=client.get(url+'/deployment.csv')
    assert csv.status_code==200 and '设备编号' in csv.text
    scene=client.get('/api/workflow/resources/urban').json()
    scene['devices'][0]['status']='offline'
    assert client.put('/api/workflow/resources/urban',json=scene).status_code==200
    old=client.get(url).json()
    assert old['stale'] and old['snapshot']['scene']==p['snapshot']['scene']
    assert client.post(url+'/status',json={'status':'confirmed'}).status_code==409
    assert client.post(url+'/status',json={'status':'archived'}).status_code==200
    assert client.post(url+'/status',json={'status':'confirmed'}).status_code==409


def test_feasible_confirmation_does_not_apply_devices(client):
    payload=task_payload();payload.update(coverage_target=.01,focus_target=.01,precision_rate_target=0)
    t=client.post('/api/workflow/tasks',json=payload).json()
    before=client.get('/api/scenes/urban').json()
    p=client.post('/api/workflow/tasks/'+t['id']+'/plan',json={}).json()
    assert p['feasible']
    url='/api/workflow/plans/'+p['id']+'/status'
    assert client.post(url,json={'status':'confirmed'}).status_code==200
    assert client.get('/api/scenes/urban').json()==before
    assert client.post(url,json={'status':'confirmed'}).status_code==409


def test_diagnosis_persistence_and_preview_use_frozen_inputs(client):
    t=client.post('/api/workflow/tasks',json=task_payload()).json()
    base='/api/workflow/tasks/'+t['id']
    original=client.post(base+'/diagnose',json={}).json()
    assert client.get(base+'/diagnosis').json()['fingerprint']==original['fingerprint']
    p=client.post(base+'/plan',json={}).json()
    url='/api/workflow/plans/'+p['id']+'/preview'
    preview=client.get(url).json()
    r=client.get('/api/workflow/resources/urban').json()
    r['devices'][0]['status']='offline'
    assert client.put('/api/workflow/resources/urban',json=r).status_code==200
    assert client.get(base+'/diagnosis').json()['stale']
    after=client.get(url).json()
    assert after['before']['cloud']==preview['before']['cloud']
    assert after['after']['stats']==p['stats']


def test_template_asset_and_resource_metadata_survive_legacy_edit(client):
    url='/api/workflow/resources/urban';r=client.get(url).json()
    r['sites'][0].update(name='真实命名的站址',capacity=3,power_w=1200,setup_cost=2)
    r['devices'][0]['asset_code']='ASSET-01'
    r['devices'][0]['model_id']='RADAR'
    assert client.put(url,json=r).status_code==200
    d=client.get('/api/scenes/urban').json()['devices'][0]
    d['azimuth']=123
    assert client.put('/api/scenes/urban/devices/'+d['id'],json=d).status_code==200
    current=client.get(url).json()
    assert current['sites'][0]['name']=='真实命名的站址'
    assert current['sites'][0]['power_w']==1200
    assert current['devices'][0]['asset_code']=='ASSET-01'
    assert current['devices'][0]['azimuth']==123
    invalid=copy.deepcopy(current);invalid['devices'][1]['asset_code']='ASSET-01'
    assert client.put(url,json=invalid).status_code==422


def test_candidate_engineering_capacity_and_site_cost(client):
    r=client.get('/api/workflow/resources/urban').json()
    a,b=r['candidates'][:2]
    site=next(s for s in r['sites'] if s['id']==a['site_id'])
    site.update(capacity=1,setup_cost=7)
    b.update(site_id=a['site_id'],x=a['x'],y=a['y'])
    # Alternatives may share a slot; only simultaneous selected deployments are restricted.
    assert client.put('/api/workflow/resources/urban',json=r).status_code==200
    t=client.post('/api/workflow/tasks',json=task_payload()).json();url='/api/workflow/tasks/'+t['id']+'/plan'
    assert client.post(url,json={'selected_ids':[a['id'],b['id']]}).status_code==422
    manual=client.post(url,json={'selected_ids':[a['id']]}).json()
    assert manual['added_cost']==a['cost']+7
    auto=client.post(url,json={}).json()
    assert not {a['id'],b['id']}<={d['id'] for d in auto['selected']}
    r=client.get('/api/workflow/resources/urban').json()
    r['sites'][0]['power_w']=0
    assert client.put('/api/workflow/resources/urban',json=r).status_code==422


def test_confirm_apply_once_and_archive_history(client):
    payload=task_payload()|dict(coverage_target=.01,focus_target=.01,precision_rate_target=0,budget=100)
    t=client.post('/api/workflow/tasks',json=payload).json()
    candidate=client.get('/api/workflow/resources/urban').json()['candidates'][0]
    p=client.post('/api/workflow/tasks/'+t['id']+'/plan',json={'selected_ids':[candidate['id']]}).json()
    base='/api/workflow/plans/'+p['id']
    assert client.post(base+'/apply',json={}).status_code==409
    assert client.post(base+'/status',json={'status':'confirmed'}).status_code==200
    result=client.post(base+'/apply',json={})
    assert result.status_code==200,result.text
    assert client.post(base+'/apply',json={}).status_code==409
    r=client.get('/api/workflow/resources/urban').json()
    assert candidate['id'] in {d['id'] for d in r['devices']}
    assert candidate['id'] not in {d['id'] for d in r['candidates']}
    assert client.get(base).json()['snapshot']['resources']['candidates']
    assert client.post(base+'/status',json={'status':'archived'}).status_code==200
    assert [h['action'] for h in client.get(base).json()['history']]==['evaluated','confirmed','applied_to_scenario','archived']


def test_route_persists_and_rejects_unknown_offline_sensor(client):
    t=client.post('/api/workflow/tasks',json=task_payload()).json();url='/api/workflow/tasks/'+t['id']+'/route'
    assert client.post(url,json={'offline_ids':['unknown']}).status_code==422
    result=client.post(url,json={'points':[[200,900,150],[1800,900,150]],'offline_ids':['RADAR-01']})
    assert result.status_code==200,result.text
    saved=client.get(url).json()
    assert saved['fixed_network'] and saved['baseline'] and not saved['stale']
    assert saved['request']['offline_ids']==['RADAR-01']


def test_focus_height_and_offline_candidates(client):
    payload=task_payload()|dict(focus={'x0':700,'x1':800,'y0':700,'y1':800,'z0':200,'z1':250})
    t=client.post('/api/workflow/tasks',json=payload).json()
    result=client.post('/api/workflow/tasks/'+t['id']+'/diagnose',json={}).json()
    assert result['stats']['focus_coverage'] is None
    r=client.get('/api/workflow/resources/urban').json()
    for d in r['candidates']:d['status']='offline'
    assert client.put('/api/workflow/resources/urban',json=r).status_code==200
    p=client.post('/api/workflow/tasks/'+t['id']+'/plan',json={}).json()
    assert p['selected']==[] and p['evaluated_subsets']==1
