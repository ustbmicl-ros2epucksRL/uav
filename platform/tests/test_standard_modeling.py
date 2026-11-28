import copy
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import standard_modeling as sm, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    store.init()
    app = FastAPI()
    app.include_router(sm.router)
    with TestClient(app) as c:
        yield c


def test_schema_and_mounted_page_distinguish_model_vs_rl_paradigms(client):
    page = client.get('/modeling')
    assert page.status_code == 200
    assert '标准参数与工程约束建模' in page.text
    schema = client.get('/api/modeling/schema').json()
    assert schema['paradigm_distinction']['requires_training_data'] is False
    assert len(schema['modules']) == 3
    assert '航迹配对' in schema['paradigm_distinction']['next_phase_data_driven']


@pytest.mark.parametrize('scene_id', ['urban', 'corridor', 'electromagnetic'])
def test_three_stage_pipeline_satisfies_all_19_constraints_and_is_reproducible(client, scene_id):
    state = client.get(f'/api/modeling/{scene_id}').json()
    assert state['spec']['scene'] == scene_id
    assert state['audit']['existing_violations'] == []

    first = client.post(f'/api/modeling/{scene_id}/solve', json={'stage': 'all'}).json()['result']
    second = client.post(f'/api/modeling/{scene_id}/solve', json={'stage': 'all'}).json()['result']
    assert first['total_checks'] == 19
    assert first['passed_checks'] == 19
    assert first['all_feasible'] is True
    assert first['input_digest'] == second['input_digest']
    assert first['result_digest'] == second['result_digest']
    assert first['siting']['after']['coverage'] >= first['siting']['before']['coverage']
    assert first['routing']['summary']['route_coverage'] >= (first['routing']['summary']['shortest_baseline_coverage'] or 0) - 1e-6


def test_siting_enforces_structural_load_fiber_lease_and_emc_isolation(client):
    spec = sm.default_spec('urban')
    # 1) Existing site lease violation is caught immediately by check endpoint
    broken = copy.deepcopy(spec)
    broken['siting']['sites'][0]['lease_approved'] = False
    audit = client.post('/api/modeling/urban/check', json=broken).json()
    assert any('准入许可' in v for v in audit['existing_violations'])
    assert client.post('/api/modeling/urban/solve', json={'stage': 'siting', 'spec': broken}).status_code == 422

    # 2) Strip fiber and reduce structural load on all candidate sites -> heavy fiber-dependent Radar/EO rejected
    constrained = copy.deepcopy(spec)
    for site in constrained['siting']['sites']:
        if site['id'].startswith('SITE-C'):
            site['fiber_ready'] = False
            site['max_load_kg'] = 20.0
    solved = client.post('/api/modeling/urban/solve', json={'stage': 'siting', 'spec': constrained}).json()['result']['siting']
    models = {m['id']: m for m in constrained['siting']['models']}
    for chosen in solved['selected_candidates']:
        m = models[chosen['model_id']]
        assert not m['requires_fiber']
        assert m['weight_kg'] <= 20.0
    assert any('光纤' in r['reason'] or '承重' in r['reason'] for r in solved['rejected_candidates'])

    # 3) Large radar-radar separation (1250m < existing 1394m) keeps existing radars valid but blocks candidate radars
    strict_emc = copy.deepcopy(spec)
    strict_emc['siting']['min_radar_separation_m'] = 1250.0
    emc_res = client.post('/api/modeling/urban/solve', json={'stage': 'siting', 'spec': strict_emc}).json()['result']['siting']
    assert all(models[c['model_id']]['type'] != 'RADAR' for c in emc_res['selected_candidates'])

    # 4) Sensing-countermeasure EMC isolation rejects RF candidates inside jammer blocking radius
    jam_emc = copy.deepcopy(spec)
    assert jam_emc['siting']['countermeasure_sites'][0]['id'] == 'CM-AUTH-01'
    jam_emc['siting']['min_jammer_rf_isolation_m'] = 800.0  # existing RF at ~856m stays valid; candidate RF < 800m rejected
    jam_res = client.post('/api/modeling/urban/solve', json={'stage': 'siting', 'spec': jam_emc}).json()['result']['siting']
    assert jam_res['countermeasure_assessment']['emc_rf_isolated'] is True
    assert jam_res['countermeasure_assessment']['jamming_active_focus_coverage'] > 0
    assert any('感反电磁阻塞' in r['reason'] for r in jam_res['rejected_candidates'])


def test_routing_enforces_battery_soc_4d_peer_separation_and_real_buildings(client):
    spec = sm.default_spec('urban')
    spec['siting']['building_source'] = 'xiongan_gba'
    spec['routing']['battery_wh'] = 55.0
    spec['routing']['cruise_power_w'] = 950.0  # Exceeds 25% reserve SoC across diagonal mission
    spec['routing']['peer_uavs'] = [
        dict(uav_id='UAV-CONFLICT', name='同高度冲突机', depart_s=45.0, speed_mps=14.0,
             waypoints=[[280.0, 380.0, 125.0], [900.0, 900.0, 125.0], [1660.0, 1580.0, 125.0]])
    ]
    res = client.post('/api/modeling/urban/solve', json={'stage': 'all', 'spec': spec}).json()['result']
    by_code = {c['code']: c for c in res['routing']['checks']}
    assert by_code['ROUTE_BATTERY_SOC']['passed'] is False
    assert by_code['ROUTE_4D_SEPARATION']['passed'] is False
    assert len(res['siting']['buildings']) > 100  # Real Xiong'an GBA footprints loaded


def test_scheduling_enforces_fleet_payload_turret_slew_and_vram_contention(client):
    spec = sm.default_spec('urban')
    # Exceed max payload on MSN-102, slow turret slew rate, and strip GPU VRAM from edge/cloud nodes
    spec['scheduling']['missions'][1]['required_payload_kg'] = 12.0
    spec['scheduling']['turret_slew_deg_s'] = 10.0
    spec['scheduling']['turret_dwell_s'] = 4.5
    for node in spec['scheduling']['nodes']:
        node['vram_gb'] = 1.0  # detect task requires 3.5 GB VRAM
    res = client.post('/api/modeling/urban/solve', json={'stage': 'all', 'spec': spec}).json()['result']['scheduling']
    by_code = {c['code']: c for c in res['checks']}
    assert by_code['SCHED_FLEET_ASSIGNMENT']['passed'] is False
    assert len(res['fleet']['unassigned']) >= 1
    assert by_code['SCHED_TURRET_REVISIT']['passed'] is False
    assert by_code['SCHED_COMPUTE_DAG']['passed'] is False
    assert res['compute_dag']['status'] == 'infeasible'
