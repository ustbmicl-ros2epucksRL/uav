import copy
import math
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app import store
from app.main import app, CACHE, DEFAULT_RULES, DEFAULT_REGISTRY, Replay
from app.scenes import seed
from app.sensing import execute, track, risks, evaluate_risks, compare_common_samples
from app.route import plan_route


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    CACHE.clear()
    with TestClient(app) as c:yield c
    CACHE.clear()


def test_simulation_repeatability_and_no_truth_input():
    scene=seed('urban')
    a=execute(scene,DEFAULT_RULES,DEFAULT_REGISTRY)
    b=execute(scene,DEFAULT_RULES,DEFAULT_REGISTRY)
    assert a==b
    assert a['metrics']['truth_samples']==363
    assert 0<=a['metrics']['observed_rate']<=1
    assert a['metrics']['position_rmse_m'] is not None
    for frame in a['frames']:
        for observation in frame['observations']:
            assert 'target_id' not in observation and 'truth_id' not in observation
    # A silent, unidentified target cannot be detected by RF or cooperative reports alone.
    scene['devices']=[d for d in scene['devices'] if d['type'] in {'RF','ADSB'}]
    r=execute(scene,DEFAULT_RULES,DEFAULT_REGISTRY)
    assert r['metrics']['per_target'][1]['observed_rate']==0


def test_all_offline_cannot_generate_tracks_alerts_or_false_precision():
    scene=seed('electromagnetic')
    for d in scene['devices']:d['status']='offline'
    r=execute(scene,DEFAULT_RULES,DEFAULT_REGISTRY)
    assert all(not f['tracks'] for f in r['frames'])
    assert r['events']==[]
    assert r['metrics']['observed_rate']==0
    assert r['metrics']['position_rmse_m'] is None
    assert r['metrics']['risk']['recall']==0


def test_tracker_coasts_then_expires_without_truth():
    def obs(x):return {'sensor_id':'RADAR-1','type':'RADAR','position':[x,100.,100.],'sigma':10.,'cooperative_id':None}
    frames=[{'time':t,'observations':[obs(100+t*10)] if t<4 or t==12 else []} for t in range(13)]
    history=track(frames)
    assert history[2]['tracks'][0]['id']==history[3]['tracks'][0]['id']
    assert history[4]['tracks'][0]['state']=='coasting'
    assert history[9]['tracks']==[]
    assert history[12]['tracks'][0]['id']!=history[0]['tracks'][0]['id']
    assert all(not a['observed'] for f in history[4:9] for a in f['tracks'])


def test_unknown_identity_is_separate_from_denied_registration():
    item={'id':'TRK-001','position':[800,800,100],'state':'confirmed','cooperative_id':None,
          'sources':['RADAR-1'],'uncertainty_m':10}
    events=risks([{'time':1,'tracks':[item]}],DEFAULT_RULES,{})
    assert {e['kind'] for e in events}=={'identity','zone'}
    item['cooperative_id']='ID-1'
    events=risks([{'time':1,'tracks':[item]}],DEFAULT_RULES,{'ID-1':{'status':'denied'}})
    assert 'authorization' in {e['kind'] for e in events}
    assert 'identity' not in {e['kind'] for e in events}
    item['state']='coasting'
    assert risks([{'time':1,'tracks':[item]}],DEFAULT_RULES,{})==[]


def test_truth_event_delay_and_false_event_are_distinguished():
    truth=[{'id':'T','points':[[0,0,100],[800,800,100],[900,900,100],[0,0,100]]}]
    events=[{'kind':'zone','time':2,'position':[900,900,100]}, {'kind':'zone','time':3,'position':[0,0,100]}]
    r=evaluate_risks(events,truth,DEFAULT_RULES)
    assert r['episodes']==1 and r['detected_episodes']==1
    assert r['mean_delay_s']==1 and r['false_geometric_events']==1


def test_common_sample_error_excludes_new_hard_samples():
    a={'matched_sample_errors_m':{'T:0':3.,'T:1':4.}}
    b={'matched_sample_errors_m':{'T:0':2.,'T:1':2.,'T:2':100.}}
    result=compare_common_samples(a,b)
    assert result=={'samples':2,'before_rmse_m':3.54,'after_rmse_m':2.,'definition':'仅比较前后都成功匹配的相同目标、相同时间样本。新增观测样本不进入此误差比较。'}


@pytest.mark.parametrize('objective',['coverage','task'])
def test_api_plan_compare_apply_stale_and_report(client,objective):
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    result=client.post('/api/plan',json={'scene':'urban','budget':50,'target':.95,'objective':objective})
    assert result.status_code==200,result.text
    p=result.json()
    assert p['added_cost']<=50
    assert len({d['id'] for d in p['selected']})==len(p['selected'])
    comparison=client.get(f'/api/scenes/urban/compare/{p["id"]}')
    assert comparison.status_code==200,comparison.text
    assert comparison.json()['before']['truth_samples']==comparison.json()['after']['truth_samples']
    assert client.post(f'/api/scenes/urban/apply/{p["id"]}').status_code==200
    assert client.post(f'/api/scenes/urban/apply/{p["id"]}').status_code==409
    report=client.get('/api/scenes/urban/report')
    assert report.status_code==200
    assert report.json()['run']['data_mode']=='synthetic_measurements'
    assert report.json()['scene']['revision']==2


def test_rule_configuration_review_persistence_and_reset(client):
    result=client.get('/api/scenes/urban/run').json()
    event=result['events'][0]
    assert client.put(f'/api/scenes/urban/events/{event["id"]}',json={'status':'false_alarm','note':'已人工核验'}).status_code==200
    reloaded=client.get('/api/scenes/urban/run').json()
    assert next(e for e in reloaded['events'] if e['id']==event['id'])['note']=='已人工核验'
    rules=copy.deepcopy(DEFAULT_RULES);rules['zone']['x0']=1500;rules['zone']['x1']=1400
    assert client.put('/api/scenes/urban/rules',json=rules).status_code==422
    rules=copy.deepcopy(DEFAULT_RULES);rules['altitude_limit']=290
    assert client.put('/api/scenes/urban/rules',json=rules).status_code==200
    changed=client.get('/api/scenes/urban/run').json()
    assert changed['run_id']!=result['run_id']
    assert not any(e['kind']=='altitude' for e in changed['events'])
    assert client.post('/api/scenes/urban/reset').status_code==200
    assert client.get('/api/scenes/urban').json()['rules']['altitude_limit']==185


def test_import_validates_timestamps_and_uncertainty_and_has_no_truth_metrics(client):
    data={'label':'外部量测','frames':[{'time':0,'observations':[{'sensor_id':'R1','type':'RADAR','position':[100,100,100],'sigma':10}]}]}
    r=client.post('/api/scenes/urban/replay',json=data)
    assert r.status_code==200,r.text
    assert r.json()['metrics'] is None and r.json()['truth']==[]
    data['frames'].append(copy.deepcopy(data['frames'][0]))
    assert client.post('/api/scenes/urban/replay',json=data).status_code==422
    data['frames']=data['frames'][:1];data['frames'][0]['observations'][0]['sigma']=0
    assert client.post('/api/scenes/urban/replay',json=data).status_code==422
    data['frames'][0]['observations'][0]['sigma']=10;data['frames'][0]['observations'][0]['position'][0]=math.nan
    with pytest.raises(ValidationError):Replay.model_validate(data)


def test_imported_event_reviews_are_saved_to_correct_replay(client):
    data={'label':'外部风险量测','frames':[{'time':t,'observations':[{'sensor_id':'R1','type':'RADAR','position':[800,800,100],'sigma':10}]} for t in range(4)]}
    r=client.post('/api/scenes/urban/replay',json=data).json()
    event=r['events'][0]
    assert client.put(f'/api/scenes/urban/events/{event["id"]}?run_id={r["run_id"]}',json={'status':'resolved','note':'外部量测已核验'}).status_code==200
    stored=client.get(f'/api/scenes/urban/replays/{r["run_id"]}').json()
    assert stored['events'][0]['note']=='外部量测已核验'
    assert client.put(f'/api/scenes/urban/events/{event["id"]}?run_id=0000000000000000',json={'status':'resolved'}).status_code==404


def test_scene_and_device_validation_and_zero_budget(client):
    assert client.get('/api/scenes/missing').status_code==404
    s=client.get('/api/scenes/urban').json();d=s['devices'][0]
    d['x']=3000
    assert client.put('/api/scenes/urban/devices/'+d['id'],json=d).status_code==422
    p=client.post('/api/plan',json={'scene':'urban','budget':0,'target':.99}).json()
    assert p['selected']==[] and p['added_cost']==0


def _radar(device_id, x, range_m, cost=10):
    return {"id": device_id, "name": device_id, "type": "RADAR", "x": x, "y": 500, "z": 80,
            "azimuth": 0, "pitch": 0, "vertical_fov": 180, "status": "online",
            "range": range_m, "fov": 360, "cost": cost}


def _open_scene():
    return {"id": "urban", "revision": 1, "buildings": [],
            "devices": [_radar("RADAR-1", 200, 500)],
            "candidates": [_radar("C-01", 1400, 700, 20)],
            "zone": {"x0": 0, "y0": 0, "x1": 100, "y1": 100}}


def test_route_plan_closes_the_uncovered_stretch_within_budget():
    points = [[100, 500, 120], [1800, 500, 120]]
    scene = _open_scene()
    # Fusion Pd threshold and link loss require more range than geometric visibility.
    scene["candidates"][0]["range"] = 900
    held = plan_route(scene, points, 0, 50)
    assert held["selected"] == [] and held["added_cost"] == 0
    assert held["before"]["coverage"] == held["after"]["coverage"]
    assert 0 < held["before"]["coverage"] < 1
    assert held["before"]["by_modality"]["RADAR"] > 0
    assert held["before"]["by_modality"]["EO"] == 0
    planned = plan_route(scene, points, 20, 50)
    assert [d["id"] for d in planned["selected"]] == ["C-01"]
    assert planned["after"]["coverage"] > planned["before"]["coverage"]
    assert planned["after"]["longest_gap_m"] < planned["before"]["longest_gap_m"]
    assert planned["target_met"]
    assert [(s["id"], s["role"]) for s in planned["sites"]] == [("RADAR-1", "已有"), ("C-01", "新增")]
    assert scene["devices"][0]["azimuth"] == 0
    assert planned["schedule"][0]["from_m"] == 0
    assert all(row["covered"] and row["scan_period_s"] in (2, 4) for row in planned["schedule"])
    pointed = [(row, site) for row in planned["schedule"] for site in row["sites"] if site["id"] == "C-01"]
    assert pointed
    for row, site in pointed:
        midpoint = 100 + (row["from_m"] + row["to_m"]) / 2
        expected = 90 if midpoint >= 1400 else 270
        assert abs((site["azimuth"] - expected + 180) % 360 - 180) < 25
    scene["devices"][0]["type"] = "ADSB"
    silent = plan_route(scene, points, 0, 50)
    assert silent["before"]["coverage"] == 0
    assert silent["before"]["by_modality"] == {"RADAR": 0, "EO": 0, "RF": 0}
    assert silent["sites"] == []
    assert all(not row["covered"] for row in silent["schedule"])


def test_route_endpoint_uses_scene_track_and_rejects_a_short_path(client):
    planned = client.post("/api/scenes/corridor/route", json={"budget": 0}).json()
    assert planned["samples"][0]["along_m"] == 0
    assert planned["before"]["samples"] >= 2
    site_ids={s["id"] for s in planned["sites"]}
    assert all(set(s["sensor_ids"])<=site_ids for s in planned["samples"])
    assert all(not s["sensor_ids"] for s in planned["samples"] if not s["in_air"])
    assert all(len(s["target"])==3 for s in planned["schedule"])
    assert client.post("/api/scenes/corridor/route", json={"points": [[0, 0, 100]]}).status_code == 422


def test_plan_becomes_stale_after_risk_rule_change(client):
    p=client.post('/api/plan',json={'scene':'urban','budget':50,'target':.9}).json()
    rules=copy.deepcopy(DEFAULT_RULES);rules['interference']=False
    assert client.put('/api/scenes/urban/rules',json=rules).status_code==200
    assert client.get(f'/api/scenes/urban/compare/{p["id"]}').status_code==409
    assert client.post(f'/api/scenes/urban/apply/{p["id"]}').status_code==409


def test_plan_coverage_preview_does_not_apply_and_rejects_stale(client):
    import numpy as np
    before=client.get('/api/scenes/urban').json()
    p=client.post('/api/plan',json={'scene':'urban','budget':50,'target':.95,'objective':'coverage'}).json()
    r=client.get('/api/scenes/urban/plan-coverage/'+p['id'])
    assert r.status_code==200,r.text
    result=r.json();a=np.asarray(result['before']['layers']);b=np.asarray(result['after']['layers'])
    assert a.shape==b.shape and np.array_equal(a<0,b<0)
    assert result['changes']['newly_covered_voxels']==int(((a==0)&(b>0)).sum())
    assert result['changes']['lost_coverage_voxels']==0
    assert result['preview_only'] is True
    assert client.get('/api/scenes/urban').json()==before
    d=before['devices'][0];d['status']='offline'
    assert client.put('/api/scenes/urban/devices/'+d['id'],json=d).status_code==200
    assert client.get('/api/scenes/urban/plan-coverage/'+p['id']).status_code==409


def test_fixed_network_failure_preview_preserves_deployment(client):
    scene=client.get('/api/scenes/urban').json()
    ids=[d['id'] for d in scene['devices']]
    result=client.post('/api/scenes/urban/route',json={'offline_ids':ids}).json()
    assert result['fixed_network'] is True
    assert result['selected']==[] and result['added_cost']==0
    assert result['after']['covered_samples']==0
    assert result['baseline']['covered_samples']>=0
    assert all(not p['sensor_ids'] for p in result['samples'])
    assert client.get('/api/scenes/urban').json()['devices']==scene['devices']
    assert client.post('/api/scenes/urban/route',json={'offline_ids':['unknown']}).status_code==422
