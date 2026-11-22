import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import coverage_core as core, store, twin_experiment as twin
from app.scenes import seed
from app.twin_api import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    store.init()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope='module')
def experiment():
    return twin.run(seed('urban'), twin.TwinRequest(seed_count=6))


def test_same_frozen_inputs_give_the_same_digest(experiment):
    again = twin.compute(experiment['inputs'])
    assert twin.digest(again) == experiment['result_digest']
    assert twin.digest(experiment['inputs']) == experiment['input_digest']


def test_reference_track_is_evaluation_truth_only(experiment):
    inputs = experiment['inputs']
    assert '不输入定位或跟踪' in inputs['trajectory']['role']
    z = [p[2] for p in inputs['trajectory']['points']]
    assert min(z) >= 30 and max(z) <= 120
    for row in experiment['result']['deployments']:
        assert len(row['timeline']) == len(inputs['trajectory']['points'])


def test_planner_score_matches_the_coverage_kernel():
    scene = seed('urban')
    volume = core.Volume()
    points = twin.planning_points(twin.PLANNING_SPACE)
    choices = twin.options(scene)
    model = twin.PlannerModel(scene, volume, choices, points)
    ids = [choices[0]['id'], choices[17]['id'], choices[40]['id']]
    full = core.fields(dict(scene, devices=scene['devices'] + [o for o in choices if o['id'] in ids]), points, volume)
    assert model.score(ids) == int(twin.adequate(full, volume).sum())


def test_plans_respect_budget_and_one_device_per_site(experiment):
    budget = experiment['inputs']['budget']
    by_id = {o['id']: o for o in experiment['inputs']['options']}
    for row in experiment['result']['deployments'][1:]:
        assert row['metrics']['added_cost'] <= budget + 1e-9
        sites = [by_id[i]['site_id'] for i in row['added']]
        assert len(sites) == len(set(sites))
        assert row['planning_history'][0]['step'] == 'compare'


def test_counterfactuals_are_scored_in_the_reference_environment(experiment):
    inputs = experiment['inputs']
    scene, volume = inputs['scene'], core.Volume.model_validate(inputs['environment'])
    points = np.array(inputs['trajectory']['points'], float)
    for row in experiment['result']['deployments']:
        devices = scene['devices'] + [o for o in inputs['options'] if o['id'] in row['added']]
        f = core.fields(dict(scene, devices=devices), points, volume)
        assert row['metrics']['theoretical_pass'] == pytest.approx(twin.adequate(f, volume).mean(), abs=1e-6)


def test_paired_seeds_give_identical_draws_for_shared_devices():
    scene = seed('urban')
    points = np.array(twin.reference_track(scene)['points'][:30], float)
    volume = core.Volume()
    f = core.fields(scene, points, volume)
    first = twin.simulate(scene, scene['devices'], points, f, [1, 2, 3])
    second = twin.simulate(scene, scene['devices'], points, f, [1, 2, 3])
    assert np.array_equal(np.isnan(first), np.isnan(second))
    assert np.allclose(np.nan_to_num(first), np.nan_to_num(second))


def test_research_points_are_labelled_synthetic_and_chained(experiment):
    points = experiment['result']['research_points']
    assert [p['id'] for p in points] == [1, 2, 3, 4, 5]
    assert all(p['evidence_level'] == '合成孪生' and p['pending'] for p in points)
    relations = {(r['source'], r['target']) for r in experiment['result']['relations']}
    assert {(1, 2), (2, 3), (3, 4), (4, 5)} <= relations
    assert any('不是外场测量' in item for item in experiment['result']['limitations'])


def test_contrast_reports_when_a_counterfactual_is_not_worse():
    method = dict(theoretical_pass=.8, longest_gap_theoretical_s=9)
    assert '未得到支持' in twin.contrast('规划时忽略干扰', method, dict(theoretical_pass=.85, longest_gap_theoretical_s=5))
    assert '低 10.0 个百分点' in twin.contrast('规划时忽略遮挡', method, dict(theoretical_pass=.7, longest_gap_theoretical_s=12))


def test_api_archives_lists_and_recomputes(client):
    assert client.post('/api/twin-experiments', json={'scene': 'urban', 'seed_count': 2}).status_code == 422
    assert client.post('/api/twin-experiments', json={'scene': 'urban', 'extra': 1}).status_code == 422
    created = client.post('/api/twin-experiments', json={'scene': 'urban', 'seed_count': 4}).json()
    assert created['evidence_level'] == 'synthetic_twin' and created['scene_changed'] is False
    listed = client.get('/api/twin-experiments').json()['experiments']
    assert listed[0]['id'] == created['id']
    assert store.get('twin-experiment-latest:urban')['id'] == created['id']
    detail = client.get('/api/twin-experiments/' + created['id']).json()
    assert detail['result_digest'] == created['result_digest']
    check = client.post(f"/api/twin-experiments/{created['id']}/recompute").json()
    assert check['matches'] and check['input_matches']
    assert client.get('/api/twin-experiments/' + created['id']).json()['reproducibility']['matches']
    assert client.get('/api/twin-experiments/' + 'x' * 32).status_code == 404
    assert store.get('scene:urban') is None


def test_page_is_served(client):
    response = client.get('/twin')
    assert response.status_code == 200 and 'twin.js' in response.text


def test_segment_clips_gaps_and_keeps_common_budget_separate_from_equal_spend(client, experiment):
    identifier = 'c' * 32
    record = dict(id=identifier, evidence_level='synthetic_twin', **experiment)
    store.put('twin-experiment:' + identifier, record)
    path = '/api/twin-experiments/' + identifier + '/segment'
    response = client.get(path, params={'start': 68, 'end': 80})
    assert response.status_code == 200
    segment = response.json()
    assert segment['sample_count'] == 13 and segment['result_digest'] == record['result_digest']
    by_key = {row['key']: row for row in segment['deployments']}
    for row in record['result']['deployments']:
        selected = row['timeline'][68:81]
        assert by_key[row['key']]['pass_count'] == sum(p['state'] == 'ok' for p in selected)
    baseline = by_key['baseline']
    assert [(s['start'], s['end'], s['state']) for s in baseline['segments']] == [
        (68, 74, 'unlocatable'), (75, 75, 'blind'), (76, 80, 'unlocatable')]
    assert baseline['blind_count'] == 1 and baseline['longest_gap_s'] == 13
    comparisons = {item['key']: item for item in segment['comparisons']}
    assert not comparisons['baseline']['equal_cost']
    assert comparisons['ignore_occlusion']['equal_cost']
    assert comparisons['ignore_occlusion']['pass_delta_pp'] == 100
    assert comparisons['ignore_interference']['pass_delta_pp'] == 0
    assert client.get(path, params={'start': 75, 'end': 75}).json()['sample_count'] == 1
    for start, end in ((80, 68), (0, 121), (-1, 0)):
        assert client.get(path, params={'start': start, 'end': end}).status_code == 422
