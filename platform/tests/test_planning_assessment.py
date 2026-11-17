import copy
import time

import numpy as np
import pytest
from pydantic import ValidationError

from app import coverage_core as core, jobs, store
from app.planning_assessment import AssessmentRequest, batches, evaluate, geometry, inside, search, variants, engineering_cost, permitted
from app.planning_portal import Task, resources
from app.scenes import seed
from test_platform import client


def small_task():
    return Task(name='指标回归', focus={'x0':700,'x1':720,'y0':700,'y1':720,'z0':120,'z1':140}).model_dump()


def small_request(**changes):
    return AssessmentRequest(bounds=[690,730,690,730,120,140], **changes)


def wait(client, identifier):
    until = time.monotonic() + 30
    while time.monotonic() < until:
        job = client.get('/api/assessments/' + identifier).json()
        if job['status'] in jobs.TERMINAL:
            assert job['status'] == 'succeeded', job
            return job
        time.sleep(.05)
    pytest.fail('计算超时')


def test_incremental_field_matches_full_network():
    scene = seed('urban')
    request = core.Volume(bounds=[700,740,700,740,120,140], cell=10)
    points = core.grid(request)
    initial = core.fields(scene, points, request)
    result = core.fields(dict(scene, devices=scene['candidates'][:2]), points, request, initial=initial)
    full = core.fields(dict(scene, devices=scene['devices'] + scene['candidates'][:2]), points, request)
    for key in ['pd','sinr_db','precision_m','fim','count','valid']:
        np.testing.assert_allclose(result[key], full[key])
    np.testing.assert_allclose(initial['pd'], core.fields(scene, points, request)['pd'])


def test_mixed_grid_partitions_and_volume_weighting():
    scene, task, request = seed('urban'), small_task(), small_request()
    focus, expected = geometry(request, task)
    wide = np.concatenate(list(batches(request.bounds, request.wide_cell, 13)))
    outside = wide[~inside(wide, focus)]
    fine = np.concatenate(list(batches(focus, request.focus_cell, 29)))
    points = np.concatenate([outside, fine])
    weights = np.concatenate([np.full(len(outside), request.wide_cell**3), np.full(len(fine), request.focus_cell**3)])
    assert len(points) == expected
    assert weights.sum() == 40 * 40 * 20
    assert len(set(map(tuple, points))) == expected
    field = core.fields(scene, points, core.Volume(**task['volume']))
    valid = field['valid']
    covered = valid & (field['pd'] >= task['volume']['pd_threshold'])
    result = evaluate(scene, [], request, task, batch_size=37)
    assert result['evaluated_cells'] == expected
    assert result['after']['stats']['coverage'] == pytest.approx(weights[covered].sum()/weights[valid].sum())
    assert result['before'] == result['after']
    other = evaluate(scene, [], request, task, batch_size=101)
    assert other['after']['stats']['coverage'] == result['after']['stats']['coverage']
    assert other['after']['cloud'] == result['after']['cloud']


def test_geometry_rejects_overlap_ambiguity_and_invalid_values():
    with pytest.raises(ValidationError):
        small_request(heights=[float('nan')])
    with pytest.raises(ValidationError):
        small_request(focus_cell=10, wide_cell=2)
    task = small_task()
    task['focus']['x0'] = 701
    with pytest.raises(ValueError, match='对齐'):
        geometry(small_request(), task)
    task = small_task()
    task['focus']['x0'],task['focus']['x1'] = 800,820
    with pytest.raises(ValueError, match='交集'):
        geometry(small_request(), task)


def test_asset_identity_engineering_limits_and_search(client):
    scene = seed('urban')
    resource = resources('urban',scene)
    resource['candidates'] = resource['candidates'][:1]
    resource['candidates'][0]['asset_code'] = 'physical-001'
    candidate = resource['candidates'][0]
    assert variants(resource, small_request()) == [candidate]
    resource['candidates'][0]['asset_code'] = ''
    site = next(s for s in resource['sites'] if s['id'] == candidate['site_id'])
    site['min_height'],site['max_height'] = 120,130
    options = variants(resource, small_request())
    assert options and all(d['z'] == 125 for d in options)
    site['setup_cost'] = 11
    assert engineering_cost(resource, [options[0]]) == options[0]['cost'] + 11
    site['power_w'] = 0
    assert not permitted(resource,[options[0]])
    result = search(scene,resource,small_task(),small_request())
    assert result['selected'] == []


def test_async_evidence_publication_trial_replay_and_stale(client):
    task = client.post('/api/workflow/tasks',json=small_task()).json()
    body = small_request(optimize=False).model_dump()
    response = client.post(f'/api/workflow/tasks/{task["id"]}/assessments',json=body)
    assert response.status_code == 202, response.text
    identifier = response.json()['id']
    job = wait(client, identifier)
    assert job['result']['checks']['full_domain'] is False
    assert job['result']['simulation_kpi_pass'] is False
    assert job['result']['evaluated_cells'] == job['result']['expected_cells']
    assert job['stale'] is False
    assert all(row['status'] == 'pending_evidence' for row in job['indicators'] if row['id'].startswith('demo'))
    assert client.get('/acceptance').status_code == 200
    assert client.get('/api/assessments/' + identifier + '/export').json()['snapshot'] == job['snapshot']
    plan = client.post('/api/assessments/' + identifier + '/plan').json()
    assert plan['id'] == identifier
    assert client.post('/api/assessments/' + identifier + '/plan').json() == plan
    preview = client.get('/api/workflow/plans/' + identifier + '/preview').json()
    assert preview['after']['stats'] == job['result']['stats']
    trial_response = client.post('/api/demonstration/trials', json=dict(name='关联验证',task_id=task['id'],plan_id=identifier,kinds=['blind_fill'],objective='核对覆盖',conditions='合成回归',coordinate_frame='local_ENU_m',truth_source='合成'))
    assert trial_response.status_code == 200, trial_response.text
    trial = trial_response.json()
    coverage = client.get(f'/api/demonstration/trials/{trial["id"]}/coverage').json()
    assert coverage['after']['stats'] == job['result']['stats']
    handoff = client.get('/api/assessments/' + identifier + '/handoff').json()
    assert handoff['plan_id'] == identifier
    assert handoff['evidence']['trials'][0]['id'] == trial['id']
    task['name'] = '更新任务'
    client.put('/api/workflow/tasks/' + task['id'], json={k:v for k,v in task.items() if k in Task.model_fields})
    assert client.get('/api/assessments/' + identifier).json()['stale'] is True
    assert client.get('/api/workflow/plans/' + identifier).json()['stale'] is True


def test_published_joint_model_applies_actual_metadata(client):
    task = client.post('/api/workflow/tasks',json=small_task()).json()
    scene = seed('urban')
    # One inexpensive candidate is sufficient in this small region. Force a different model and pose.
    resource = resources('urban',scene)
    resource['candidates'] = resource['candidates'][:1]
    resource['devices'] = []
    response = client.put('/api/workflow/resources/urban',json=resource)
    assert response.status_code == 200,response.text
    task['coverage_target'],task['focus_target'],task['precision_rate_target'] = .1,.1,0
    updated = client.put('/api/workflow/tasks/' + task['id'],json={k:v for k,v in task.items() if k in Task.model_fields}).json()
    request = small_request(model_ids=['RF'], heights=[170], azimuths=[90])
    identifier = client.post(f'/api/workflow/tasks/{task["id"]}/assessments',json=request.model_dump()).json()['id']
    job = wait(client,identifier)
    assert job['result']['selected'],job['result']
    selected = job['result']['selected'][0]
    assert selected['type'] == 'RF' and selected['z'] == 170
    client.post('/api/assessments/' + identifier + '/plan')
    confirmed = client.post('/api/workflow/plans/' + identifier + '/status',json={'status':'confirmed'})
    assert confirmed.status_code == 200,confirmed.text
    applied = client.post('/api/workflow/plans/' + identifier + '/apply')
    assert applied.status_code == 200,applied.text
    deployed = client.get('/api/workflow/resources/urban').json()['devices'][0]
    assert deployed['model_id'] == 'RF' and deployed['z'] == 170 and deployed['cost'] == selected['cost']
    assert client.post('/api/workflow/plans/' + identifier + '/apply').status_code == 409


def test_visual_sampling_covers_altitudes_and_preserves_statistics():
    from app.planning_visualization import clouds
    scene, task, request = seed('urban'), small_task(), small_request()
    result = evaluate(scene, [], request, task)
    result['selected'] = []
    job = dict(result=result, snapshot=dict(scene=scene,task=task,calibration=None))
    before = copy.deepcopy(result['after']['stats'])
    first = clouds(job)
    assert first == clouds(job)
    assert len({point[2] for point in first['after']}) >= 10
    assert result['after']['stats'] == before
    assert all(len(point) == 7 for point in first['after'])


def test_reference_recomputed_with_exclusive_failure_partition():
    scene, task, request = seed('urban'), small_task(), small_request()
    reference = scene['candidates'][:1]
    result = evaluate(scene, scene['candidates'][1:2], request, task, reference=reference, batch_size=101)
    expected = evaluate(scene, reference, request, task, batch_size=101)
    assert result['reference']['stats'] == expected['after']['stats']
    for stage in ('before', 'after', 'reference'):
        part = result[stage]
        assert sum(part['bottleneck']['fractions'].values()) + part['stats']['precision_pass_rate'] == pytest.approx(1)
        assert sum(b['valid_volume_m3'] for b in part['bottleneck']['altitude']) == part['stats']['valid_volume_m3']


def test_precision_procurement_uses_available_sites_and_preserves_assets(client):
    from app.precision_planning import options, search as precision_search
    scene = seed('urban')
    resource = resources('urban', scene)
    original = copy.deepcopy(resource)
    request = small_request(strategy='precision', model_ids=['EO'], heights=[170], azimuths=[0, 90])
    choices = options(resource, request)
    assert {d['site_id'] for d in choices} == {s['id'] for s in resource['sites']}
    assert len({d['id'] for d in choices}) == len(choices)
    assert not {d['id'] for d in choices} & {d['id'] for d in resource['devices'] + resource['candidates']}
    task = small_task()
    task['budget'] = 6
    result = precision_search(scene, resource, task, request)
    assert result['added_cost'] <= 6
    assert permitted(resource, result['selected'])
    assert resource == original
    for site in resource['sites']:
        site['power_w'] = sum(d['power_w'] for d in resource['devices'] if d['site_id'] == site['id'])
    assert options(resource, request) == []


def test_reference_api_same_inputs_and_new_procurement_apply(client):
    task_body = small_task()
    task_body.update(coverage_target=.1, focus_target=.1, precision_rate_target=0)
    task = client.post('/api/workflow/tasks', json=task_body).json()
    resource = resources('urban', seed('urban'))
    resource['devices'] = []
    assert client.put('/api/workflow/resources/urban', json=resource).status_code == 200
    url = f'/api/workflow/tasks/{task["id"]}/assessments'
    baseline = client.post(url, json=small_request(optimize=False).model_dump()).json()['id']
    wait(client, baseline)
    body = small_request(strategy='precision', reference_job_id=baseline, model_ids=['RF'], heights=[170], azimuths=[0]).model_dump()
    response = client.post(url, json=body)
    assert response.status_code == 202, response.text
    identifier = response.json()['id']
    job = wait(client, identifier)
    assert job['result']['reference']['job_id'] == baseline
    assert job['result']['selected'][0]['id'].startswith('P-')
    assert client.get('/api/workflow/precision-comparisons').json()[0]['job_id'] == identifier
    assert client.post('/api/assessments/' + identifier + '/plan').status_code == 200
    assert client.post('/api/workflow/plans/' + identifier + '/status', json={'status':'confirmed'}).status_code == 200
    response = client.post('/api/workflow/plans/' + identifier + '/apply')
    assert response.status_code == 200, response.text
    deployed = client.get('/api/workflow/resources/urban').json()['devices']
    assert deployed[0]['id'] == job['result']['selected'][0]['id']
    assert deployed[0]['model_id'] == 'RF'
    assert client.post(url, json=body).status_code == 409


def test_low_altitude_goal_requires_aligned_complete_layer():
    with pytest.raises(ValidationError, match='低空优化'):
        small_request(low_altitude_target=.8)
    with pytest.raises(ValidationError, match='100 m'):
        AssessmentRequest(strategy='precision', low_altitude_target=.8, bounds=[0,2000,0,2000,30,290], wide_cell=20)
    request = AssessmentRequest(strategy='precision', low_altitude_target=.8)
    assert geometry(request, Task(name='低空').model_dump())[1] == 13132800


def test_low_altitude_full_volume_target_and_constraint(client):
    task = small_task()
    task['focus'].update(z0=30,z1=100)
    task.update(coverage_target=.1,focus_target=.1,precision_rate_target=0)
    saved = client.post('/api/workflow/tasks',json=task).json()
    body = dict(strategy='precision',low_altitude_target=1,optimize=False,bounds=[690,730,690,730,30,110])
    response = client.post(f'/api/workflow/tasks/{saved["id"]}/assessments',json=body)
    assert response.status_code == 202,response.text
    job = wait(client,response.json()['id'])
    result = job['result']
    band = result['after']['bottleneck']['altitude'][0]
    assert result['low_altitude']['rate'] == band['precise']
    assert result['low_altitude']['passed'] == (band['precise'] >= 1)
    assert result['feasible'] == result['low_altitude']['passed']
    assert result['search_samples'] == 16384
    assert result['low_altitude']['bounds'] == [30,100]
    rows = {row['id']: row for row in job['indicators']}
    assert rows['task-precision']['status'] == 'simulation_fail'
    assert rows['task-low-precision']['status'] == 'simulation_fail'


def test_existing_steering_is_preserved_through_publication_handoff_trial_and_apply(client, monkeypatch):
    import json
    from app import low_altitude_planning
    from app.planning_assessment import calculate
    jobs.manager.stop()
    monkeypatch.setattr(jobs.manager, 'start', lambda: None)
    body = small_task()
    body['focus'].update(z0=30,z1=100)
    body.update(coverage_target=.1,focus_target=.1,precision_rate_target=0)
    task = client.post('/api/workflow/tasks',json=body).json()
    resource = client.get('/api/workflow/resources/urban').json()
    original = next(d for d in resource['devices'] if d['type']=='EO')
    changed = dict(original,azimuth=(original['azimuth']+45)%360,pitch=-15)
    # Exercise real field computation and persistence with a fixed optimizer proposal.
    monkeypatch.setattr(low_altitude_planning, 'search', lambda *args, **kwargs: dict(
        selected=[],updated_devices=[changed],added_cost=2,cost_breakdown=dict(procurement_and_sites=0,retuning_reserve=2,basis='测试预留'),
        search_history=[],evaluated_subsets=1,variant_count=1,search_cell=None,method='固定转向回归',optimality='固定方案'))
    parameters = dict(strategy='precision',low_altitude_target=.1,retune_existing=True,bounds=[690,730,690,730,30,110])
    response = client.post(f'/api/workflow/tasks/{task["id"]}/assessments',json=parameters)
    assert response.status_code == 202,response.text
    job = response.json()
    result = calculate(job['snapshot'])
    assert result['feasible']
    with store.connection() as conn:
        conn.execute("UPDATE computation_jobs SET status='succeeded',result=?,finished_at=? WHERE id=?",(json.dumps(result),store.now(),job['id']))
    identifier=job['id']
    plan=client.post('/api/assessments/'+identifier+'/plan').json()
    assert plan['updated_devices']==[changed]
    preview=client.get('/api/workflow/plans/'+identifier+'/preview').json()
    assert next(d for d in preview['scene']['devices'] if d['id']==changed['id'])['azimuth']==changed['azimuth']
    handoff=client.get('/api/assessments/'+identifier+'/handoff').json()
    assert next(d for d in handoff['deployment'] if d['id']==changed['id'])['pitch']==-15
    trial=client.post('/api/demonstration/trials',json=dict(name='转向回归',task_id=task['id'],plan_id=identifier,kinds=['blind_fill'],objective='低空',conditions='合成',coordinate_frame='local_ENU_m',truth_source='合成')).json()
    assert next(d for d in trial['snapshot']['resources']['devices'] if d['id']==changed['id'])['pitch']==-15
    compared=client.post(f'/api/workflow/tasks/{task["id"]}/assessments',json=dict(parameters,reference_job_id=identifier,optimize=False)).json()
    assert compared['snapshot']['reference']['updated_devices']==[changed]
    comparison=calculate(compared['snapshot'])
    assert comparison['reference']['stats']==result['after']['stats']
    client.post('/api/jobs/'+compared['id']+'/cancel',json={})
    assert client.post('/api/workflow/plans/'+identifier+'/status',json={'status':'confirmed'}).status_code==200
    assert client.post('/api/workflow/plans/'+identifier+'/apply').status_code==200
    actual=client.get('/api/workflow/resources/urban').json()
    assert next(d for d in actual['devices'] if d['id']==changed['id'])['azimuth']==changed['azimuth']
    assert client.post('/api/workflow/plans/'+identifier+'/apply').status_code==409


def test_joint_low_search_sparse_network_stays_deterministic_and_budgeted(client, monkeypatch):
    from scipy.stats import qmc
    from app import low_altitude_joint
    from app.precision_planning import options
    scene=seed('urban')
    scene['devices']=[]
    scene['buildings']=[]
    resource=resources('urban',scene)
    resource['devices']=[]
    request=AssessmentRequest(strategy='precision',low_altitude_target=.8,retune_existing=True,retuning_reserve=0,model_ids=['EO'],heights=[170],azimuths=[0])
    device=options(resource,request)[0]
    original_sampler=qmc.Sobol.random_base2
    monkeypatch.setattr(qmc.Sobol,'random_base2',lambda self,m:original_sampler(self,5))
    monkeypatch.setattr(low_altitude_joint,'options',lambda *_:[device,dict(device,azimuth=90)])
    task=Task(name='单台测角',budget=6).model_dump()
    plan=dict(selected=[device],updated_devices=[],added_cost=6,search_history=[],evaluated_subsets=0,method='回归')
    original=copy.deepcopy(plan)
    first=low_altitude_joint.refine(scene,resource,task,request,plan)
    second=low_altitude_joint.refine(scene,resource,task,request,plan)
    assert first==second
    assert plan==original
    assert first['added_cost']<=6
    assert first['search_rates']['low_altitude_rate']==0
    assert first['updated_devices']==[]


def test_reference_accepts_only_additive_sites_and_preserves_prices():
    from app.acceptance_api import compatible_reference, assessment_title
    original=dict(task={'id':'task'},scene={},calibration={},resources=dict(revision=1,source='合成',sites=[dict(id='old',setup_cost=0)],models=[dict(id='EO',cost=6)],devices=[],candidates=[]),parameters=dict(strategy='precision',low_altitude_target=.8))
    current=copy.deepcopy(original)
    current['resources']['sites'].append(dict(id='LOW-200-200',setup_cost=.5))
    current['resources']['revision']=2
    current['scene'].update(revision=2,input_source='低位候选')
    current['resources']['source']='合成候选，待踏勘'
    assert compatible_reference(original,current)
    assert assessment_title(current)=='低位站址与设备组合优化'
    assert assessment_title(original)=='低空精度优化'
    current['resources']['models'][0]['cost']=5
    assert not compatible_reference(original,current)
    current=copy.deepcopy(original)
    current['resources']['sites'][0]['setup_cost']=1
    assert not compatible_reference(original,current)


def test_joint_search_can_exchange_two_devices_for_one_costlier_model(client, monkeypatch):
    import numpy as np
    from scipy.stats import qmc
    from app import low_altitude_joint
    from app.precision_planning import options
    scene=seed('urban')
    scene.update(devices=[],buildings=[])
    resource=resources('urban',scene)
    resource['devices']=[]
    request=AssessmentRequest(strategy='precision',low_altitude_target=.8,retune_existing=True,retuning_reserve=0,model_ids=['EO'],heights=[90],azimuths=[0])
    first=options(resource,request)[0]
    second=dict(first,id='second',azimuth=90)
    radio=dict(first,id='radio',model_id='RF',type='RF',cost=12,fov=360)
    next(site for site in resource['sites'] if site['id']==first['site_id'])['max_height']=100
    sampler=qmc.Sobol.random_base2
    monkeypatch.setattr(qmc.Sobol,'random_base2',lambda self,m:sampler(self,5))
    monkeypatch.setattr(low_altitude_joint,'options',lambda *_:[first,second,radio])
    def fields(scene,points,*args):
        strength=sum(1 if d['type']=='RF' else .0001 for d in scene['devices'])
        return dict(valid=np.ones(len(points),dtype=bool),pd=np.full(len(points),bool(scene['devices']),dtype=float),fim=np.broadcast_to(np.eye(3)*strength,(len(points),3,3)).copy())
    monkeypatch.setattr(low_altitude_joint.core,'fields',fields)
    task=Task(name='组合互换',budget=12).model_dump()
    plan=dict(selected=[first,second],updated_devices=[],added_cost=12,search_history=[],evaluated_subsets=0,method='回归')
    result=low_altitude_joint.refine(scene,resource,task,request,plan)
    assert [d['type'] for d in result['selected']]==['RF']
    assert result['added_cost']==12
    assert result['search_rates']['low_altitude_rate']==1
    alternatives={row['start_model']:row for row in result['search_alternatives']}
    assert alternatives['RF']['added_cost']==12
    assert alternatives['RF']['evidence_type']=='search_sample'
    assert alternatives['RF']['existing_constraints_pass']


def test_low_screen_preserves_sobol_spatial_balance():
    import numpy as np
    from scipy.stats import qmc
    from app.low_altitude_joint import balanced_screen_indices
    whole=qmc.Sobol(3,scramble=True,seed=20261116).random_base2(13)
    low=qmc.Sobol(3,scramble=True,seed=20261117).random_base2(13)
    points=np.concatenate([whole,low])
    indices=balanced_screen_indices(len(whole),len(low))
    assert len(indices)==1024
    for sample in [points[indices[:512]],points[indices[512:]]]:
        for axis in range(3):
            assert np.histogram(sample[:,axis],bins=np.linspace(0,1,9))[0].tolist()==[64]*8


def test_current_model_reference_revalidation_preserves_deployment():
    from app.planning_assessment import calculate
    scene, task = seed('urban'), small_task()
    resource = resources('urban', scene)
    selected = [copy.deepcopy(resource['candidates'][0])]
    updated = [dict(scene['devices'][0], azimuth=90)]
    request = small_request(optimize=False, verify_reference=True, reference_job_id='a' * 32)
    snapshot = dict(task=task, scene=scene, resources=resource, calibration=None,
                    parameters=request.model_dump(), reference=dict(job_id='a' * 32, selected=selected,
                    updated_devices=updated, retuning_reserve=2, added_cost=8))
    result = calculate(snapshot)
    assert result['selected'] == selected
    assert result['updated_devices'] == updated
    assert result['added_cost'] == engineering_cost(resource, selected) + 2
    assert result['stats'] == result['reference']['stats']
    assert result['evaluated_subsets'] == 0
    with pytest.raises(ValidationError, match='方案复核'):
        small_request(verify_reference=True)


def test_task_validation_reports_gaps_without_promoting_field_evidence(client):
    task = client.post('/api/workflow/tasks', json=small_task()).json()
    job = wait(client, client.post(f'/api/workflow/tasks/{task["id"]}/assessments',
                                  json=small_request(optimize=False).model_dump()).json()['id'])
    reports = job['task_validation']
    assert [item['id'] for item in reports] == [1, 2, 3, 4, 5]
    assert all(item['method'] and item['results'] and item['next_tests'] for item in reports)
    assert reports[2]['failed_requirements']
    assert reports[1]['failed_requirements']
    assert reports[4]['counts']['pending_evidence'] == 4
    assert reports[4]['counts']['simulation_pass'] == 0
    assert reports[4]['evidence_type'] == 'synthetic_model'
