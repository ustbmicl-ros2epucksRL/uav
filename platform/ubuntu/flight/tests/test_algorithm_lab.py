import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app
from app.algorithm_api import Compute, Experiment
from app.algorithm_compute import default_compute, evaluate, transfer
from app.algorithm_routes import solve_route, validate_path
from app.vendor.heft.core import Event, start_time, find_first_gap

BOUNDS=dict(xyz_min=[0,0,30],xyz_max=[1000,1000,280])

@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setenv('FLIGHT_DATA_DIR',str(tmp_path/'flight'))
    monkeypatch.setenv('FLIGHT_ALGORITHM_DIR',str(tmp_path/'experiments'))
    return TestClient(app)

def test_upstream_heft_directed_links_and_exact_fit():
    comm=lambda a,b,source,target: 9 if (source,target)==('a','b') else 2
    assert start_time('child',{'a':[Event('parent',0,3)],'b':[]},
                      {'parent':'a'},{'child':('parent',)},comm,lambda j,n:1,'b')==12
    assert find_first_gap([Event('x',4,8)],0,4)==0

@pytest.mark.parametrize('algorithm',['bitstar','acs_ds','astar'])
def test_open_source_routes_avoid_solid_prism(algorithm):
    boxes=[dict(id='TEMP-BARRIER',kind='no_fly',lower=[400,250,0],upper=[600,750,300])]
    start=[100,500,120];goal=[900,500,120]
    r=solve_route(start,goal,BOUNDS,boxes,algorithm,7,.5)
    assert r['status']=='ok'
    assert r['distance_m']>800
    assert validate_path(r['points'],start,goal,BOUNDS,boxes)

@pytest.mark.parametrize('algorithm',['bitstar','acs_ds','astar'])
def test_no_path_does_not_fall_back_to_colliding_straight_line(algorithm):
    boxes=[dict(id='wall',kind='no_fly',lower=[400,-20,0],upper=[600,1020,300])]
    r=solve_route([100,500,120],[900,500,120],BOUNDS,boxes,algorithm,7,.15)
    assert r['status']=='no_path' and r['points']==[]

def test_invalid_start_rejected():
    with pytest.raises(ValueError):solve_route([-1,0,120],[900,500,120],BOUNDS,[])

def test_dag_validation_rejects_cycles_duplicates_and_unknown_nodes():
    cfg=default_compute(BOUNDS);cfg['tasks'][0]['parents']=['alert'];cfg['tasks'][0]['input_mbit']=0
    with pytest.raises(ValidationError):Compute(**cfg)
    cfg=default_compute(BOUNDS);cfg['nodes'].append(cfg['nodes'][0])
    with pytest.raises(ValidationError):Compute(**cfg)
    cfg=default_compute(BOUNDS);cfg['links'][0]['target']='missing'
    with pytest.raises(ValidationError):Compute(**cfg)
    with pytest.raises(ValidationError):Experiment(altitude_m=200,max_altitude_m=100)

def test_heft_conservation_dependency_and_node_capacity():
    cfg=default_compute(BOUNDS);r=evaluate(cfg,[100,100,120])
    assert r==evaluate(cfg,[100,100,120])
    assert r['status']=='ok' and len(r['rows'])==len(cfg['tasks'])
    rows={r['id']:r for r in r['rows']}
    for row in rows.values():
        assert row['end_s']-row['start_s']==pytest.approx(row['compute_s'])
        assert row['start_s']>=row['ready_s']-1e-8
        assert row['queue_s']==pytest.approx(row['start_s']-row['ready_s'])
    for node in cfg['nodes']:
        events=sorted([r for r in rows.values() if r['node']==node['id']],key=lambda r:r['start_s'])
        assert all(a['end_s']<=b['start_s']+1e-8 for a,b in zip(events,events[1:]))
        assert all(r['start_s']>=node['busy_s']-1e-8 for r in events)
    for t in cfg['tasks']:
        for p in t['parents']:assert rows[t['id']]['start_s']>=rows[p]['end_s']
    assert r['energy_j']==pytest.approx(sum(x['energy_j'] for x in rows.values()))
    assert r['makespan_s']>=max(x['end_s'] for x in rows.values())

def test_disconnected_link_local_fallback_and_remote_unreachable():
    cfg=default_compute(BOUNDS);cfg['links']=[]
    r=evaluate(cfg,[100,100,120]);assert {t['node'] for t in r['rows']}=={'device'}
    assert evaluate(cfg,[100,100,120],'cloud')['status']=='unreachable'
    assert evaluate(cfg,[100,100,120],'edge')['makespan_s'] is None

def test_asymmetric_rate_and_deadline_are_not_fabricated_success():
    cfg=default_compute(BOUNDS)
    for t in cfg['tasks']:t['deadline_s']=.001
    r=evaluate(cfg,[100,100,120],'local')
    assert r['deadline_met']==0 and r['makespan_s']==pytest.approx(17.1)
    nodes={n['id']:n for n in cfg['nodes']};nodes['device']['position']=[100,100,120]
    up,_=transfer('device','edge-a',10,nodes,cfg['links'])
    down,_=transfer('edge-a','device',10,nodes,cfg['links'])
    assert up>down

def test_api_persistence_input_hash_and_no_flight_side_effect(client):
    before=client.get('/api/vehicles').json()
    assert client.get('/api/algorithms/catalog').status_code==200
    r=client.post('/api/algorithms/runs',json={'planner':'bitstar'});assert r.status_code==200,r.text
    d=r.json();assert d['status']=='ok' and len(d['snapshots'])==5
    assert d['sources']['ompl']['version'] and len(d['input_sha256'])==64
    assert client.get('/api/algorithms/runs/'+d['id']).json()==d
    assert client.get('/api/algorithms/runs').json()['records'][0]['id']==d['id']
    assert client.get('/api/vehicles').json()==before
    assert client.post('/api/algorithms/runs',json={'compute':{'nodes':[],'tasks':[],'links':[]}}).status_code==422
    assert client.get('/api/algorithms/runs/not-found').status_code==404

def test_config_and_history_do_not_mask_failed_route(client):
    assert client.post('/api/algorithms/runs',json={'start_xy':[-20,0]}).status_code==422
    assert client.get('/api/algorithms/runs').json()['records']==[]


def test_fixed_edge_does_not_silently_reassign_an_unreachable_node():
    cfg=default_compute(BOUNDS)
    cfg['links']=[l for l in cfg['links'] if 'edge-a' not in (l['source'],l['target'])]
    assert evaluate(cfg,[900,900,120],'edge')['status']=='unreachable'
    assert evaluate(cfg,[900,900,120],'heft')['status']=='ok'
