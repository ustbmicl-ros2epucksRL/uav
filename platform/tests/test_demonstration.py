import copy
import math
import pytest
from test_platform import client
from test_planning_portal import task_payload


def trial(client,scene='urban'):
    t=client.post('/api/workflow/tasks',json=task_payload()|{'scene':scene}).json()
    r=client.post('/api/demonstration/trials',json=dict(name='示范',task_id=t['id'],kinds=['positioning','tracking'],objective='实飞验证',conditions='相同航线环境',coordinate_frame='ENU-TEST',truth_source='RTK reference'))
    assert r.status_code==200,r.text
    return r.json()


def template(client,t,variant='baseline'):
    return client.get(f'/api/demonstration/trials/{t["id"]}/template?variant={variant}').json()


def save(client,t,data):
    r=client.post(f'/api/demonstration/trials/{t["id"]}/runs',json=data)
    assert r.status_code==200,r.text
    return r.json()


@pytest.mark.parametrize('scene',['urban','electromagnetic','corridor'])
def test_scene_closed_loop_and_report(client,scene):
    t=trial(client,scene);a=save(client,t,template(client,t));b=save(client,t,template(client,t,'improved'))
    report=client.get('/api/demonstration/runs/'+a['id']+'/report').json()
    assert report['trial']['snapshot_digest']==a['snapshot_digest']
    assert report['evidence_status']=='合成数据软件验证'
    result=client.get(f'/api/demonstration/compare/{a["id"]}/{b["id"]}').json()
    pair=result['common_sample_accuracy'][0]
    assert pair['samples']==53
    assert pair['before_rmse_m']==pytest.approx(math.sqrt(80))
    assert pair['after_rmse_m']==pytest.approx(math.sqrt(11.25))
    assert len(client.get(f'/api/demonstration/trials/{t["id"]}/runs').json())==2
    assert client.get('/demonstration').status_code==200


def test_snapshot_survives_scene_edit(client):
    t=trial(client);data=template(client,t);r=save(client,t,data)
    resources=client.get('/api/workflow/resources/urban').json();resources['devices'][0]['status']='offline'
    assert client.put('/api/workflow/resources/urban',json=resources).status_code==200
    report=client.get('/api/demonstration/runs/'+r['id']+'/report').json()
    assert report['trial']==t
    assert report['run']==r


@pytest.mark.parametrize('bad',['coordinates','duplicate','device','receive_time','algorithm','deployment'])
def test_invalid_data_rejected_atomically(client,bad):
    t=trial(client);d=template(client,t)
    if bad=='coordinates':d['coordinate_frame']='different'
    if bad=='duplicate':d['truth'].append(d['truth'][0])
    if bad=='device':d['observations'][0]['device_ids']=['unknown']
    if bad=='receive_time':d['observations'][0]['received_t']=-1
    if bad=='algorithm':d['algorithm_results']=[dict(research_task=1,name='coverage',version='v1',input_digest='0'*64,summary='wrong',metrics={})]
    if bad=='deployment':d['deployed_device_ids']=[]
    assert client.post(f'/api/demonstration/trials/{t["id"]}/runs',json=d).status_code==422
    assert client.get(f'/api/demonstration/trials/{t["id"]}/runs').json()==[]


def test_no_observations_and_missing_optional_fields(client):
    t=trial(client);d=template(client,t);d['observations']=[]
    assert save(client,t,d)['evaluation']['sources']=={}
    d=template(client,t)
    for p in d['observations']:
        p.pop('speed_mps');p.pop('category');p.pop('received_t')
    m=save(client,t,d)['evaluation']['sources']['fusion']
    assert m['speed_rmse_mps'] is None and m['recognition_accuracy'] is None and m['mean_latency_s'] is None
    assert m['sample_detection_rate']==53/61
    assert m['track_breaks']==1
    assert m['longest_continuous_s']==32


def test_one_to_one_matching_and_independent_sources(client):
    t=trial(client);d=template(client,t);d['truth']=d['truth'][:1]
    a=d['observations'][0];b=copy.deepcopy(a);b['t']=.1
    c=copy.deepcopy(a);c['source_id']='radar';c['x']+=1
    d['observations']=[a,b,c]
    e=save(client,t,d)['evaluation']
    assert len(e['residuals'])==2
    assert e['sources']['fusion']['matched_samples']==1
    assert e['sources']['fusion']['unmatched_observations']==1
    assert e['sources']['radar']['matched_samples']==1


@pytest.mark.parametrize('difference',['truth','provenance','association_method','actual_conditions','trial'])
def test_noncomparable_runs_rejected(client,difference):
    t=trial(client);d=template(client,t);a=save(client,t,d)
    if difference=='truth':d['truth'][0]['x']+=1
    if difference=='provenance':d['provenance']='field'
    if difference=='association_method':d['association_method']='unverified labels'
    if difference=='actual_conditions':d['actual_conditions']='changed interference conditions'
    if difference=='trial':t=trial(client)
    b=save(client,t,d)
    assert client.get(f'/api/demonstration/compare/{a["id"]}/{b["id"]}').status_code==409


def test_unknown_target_unmatched_and_algorithm_provenance(client):
    t=trial(client);d=template(client,t);d['observations'][0]['target_id']='unknown'
    d['algorithm_results']=[dict(research_task=2,name='协同精度',version='external-v2',input_digest=t['snapshot_digest'],summary='外部模型验证',metrics={'rmse_m':10.})]
    r=save(client,t,d)
    assert r['evaluation']['sources']['fusion']['unmatched_observations']==1
    assert r['algorithm_results'][0]['input_digest']==t['snapshot_digest']


def test_plan_task_mismatch(client):
    t=trial(client);other=client.post('/api/workflow/tasks',json=task_payload()).json()
    p=client.post('/api/workflow/tasks/'+other['id']+'/plan',json={}).json()
    fields={k:t[k] for k in ['name','task_id','kinds','objective','conditions','coordinate_frame','truth_source']}
    assert client.post('/api/demonstration/trials',json=fields|{'plan_id':p['id']}).status_code==422


def test_calibration_holdout_and_immutable_source(client):
    t=trial(client);r=save(client,t,template(client,t));url='/api/demonstration/runs/'+r['id']
    c=client.post(url+'/calibrations',json={'source_id':'fusion'})
    assert c.status_code==200,c.text
    c=c.json()
    assert c['train_samples']+c['holdout_samples']==53
    assert c['correction_m']=={'dx':-8.,'dy':-4.,'dz':0.}
    assert c['after_rmse_m']==pytest.approx(0,abs=1e-10) and c['holdout_improved']
    assert client.get(url).json()==r
    assert client.get(url+'/report').json()['calibration_candidates']==[c]
    assert client.post(url+'/calibrations',json={'source_id':'missing'}).status_code==422


def test_calibration_rejects_harmful_correction(client):
    t=trial(client);d=template(client,t)
    for o in d['observations']:
        if o['t']>=34:o['x']-=16;o['y']-=8
    r=save(client,t,d)
    c=client.post('/api/demonstration/runs/'+r['id']+'/calibrations',json={'source_id':'fusion'}).json()
    assert not c['holdout_improved'] and c['after_rmse_m']>c['before_rmse_m']


def test_reacquisition_and_signal_summary(client):
    t=trial(client);r=save(client,t,template(client,t));m=r['evaluation']['sources']['fusion']
    assert m['mean_reacquisition_s']==8 and m['reacquisition_intervals']==1
    assert m['mean_snr_db']==14


def test_historical_plan_uses_its_original_scene(client):
    t=trial(client)
    plan=client.post('/api/workflow/tasks/'+t['task_id']+'/plan',json={}).json()
    payload=task_payload()|{'scene':'corridor','revision':1}
    assert client.put('/api/workflow/tasks/'+t['task_id'],json=payload).status_code==200
    fields={k:t[k] for k in ['name','task_id','kinds','objective','conditions','coordinate_frame','truth_source']}
    r=client.post('/api/demonstration/trials',json=fields|{'plan_id':plan['id']})
    assert r.status_code==200,r.text
    assert r.json()['scene']=='urban' and r.json()['snapshot']['task']['scene']=='urban'


def test_three_scene_routes_and_overview_isolation(client):
    for scene in ('urban','corridor','electromagnetic'):
        r=client.get('/demonstration/'+scene)
        assert r.status_code==200 and '<base href="/">' in r.text
    assert client.get('/demonstration/missing').status_code==404
    assert 'scene-cards' in client.get('/demonstration').text
    t=trial(client,'corridor');save(client,t,template(client,t))
    scenes={x['scene']:x for x in client.get('/api/demonstration/overview').json()['scenes']}
    assert scenes['corridor']['tasks']==1 and scenes['corridor']['synthetic_runs']==1
    assert scenes['urban']['tasks']==0 and scenes['electromagnetic']['runs']==0


def test_scene_insights_uses_reference_gaps_and_signal_data(client):
    t=trial(client,'corridor');r=save(client,t,template(client,t))
    data=client.get('/api/demonstration/runs/'+r['id']+'/insights').json()['sources']['fusion']
    assert data['gaps']==[dict(target_id='UAV-TEST-01',start=20.,end=28.,missing_samples=8,recovered=True)]
    assert sum(b['count'] for b in data['snr_bins'])==53
    assert len(data['station_changes'])==3


def test_frozen_coverage_comparison(client):
    t=trial(client);task=t['task_id']
    plan=client.post('/api/workflow/tasks/'+task+'/plan',json={'selected_ids':['C-01']}).json()
    fields={k:t[k] for k in ['name','task_id','kinds','objective','conditions','coordinate_frame','truth_source']}
    t=client.post('/api/demonstration/trials',json=fields|{'plan_id':plan['id']}).json()
    r=client.get('/api/demonstration/trials/'+t['id']+'/coverage')
    assert r.status_code==200,r.text
    a=r.json();assert a['before'] is not None
    assert len(a['scene']['devices'])==len(a['before_scene']['devices'])+1
    assert a['before']['settings']==a['after']['settings']
    resources=client.get('/api/workflow/resources/urban').json()
    resources['devices'][0]['status']='offline';client.put('/api/workflow/resources/urban',json=resources)
    b=client.get('/api/demonstration/trials/'+t['id']+'/coverage').json()
    assert a['after']['cloud']==b['after']['cloud'] and a['after']['stats']==b['after']['stats']


def test_descriptive_condition_comparison_is_explicit(client):
    t=trial(client,'electromagnetic');d=template(client,t);a=save(client,t,d)
    d['actual_conditions']='不同干扰条件';b=save(client,t,d)
    url=f'/api/demonstration/compare/{a["id"]}/{b["id"]}'
    assert client.get(url).status_code==409
    r=client.get(url+'?mode=conditions').json()
    assert r['mode']=='conditions' and r['after_conditions']=='不同干扰条件'
    assert '不能' in r['note']
