import copy
import numpy as np
from app import coverage_core as core, engineering, store
from app.baseline_comparison import calculate
from app.scenes import seed
from test_platform import client


def test_same_input_b2_and_delta_accounting():
    s=seed('urban');before=copy.deepcopy(s);v=core.default_volume();r=calculate(s,v)
    assert s==before
    assert r['models']['B2']['coverage']==engineering.volume(s,v)['stats']['coverage']
    a,b,c=[np.array(r['models'][k]['layers']) for k in ('B0','B1','B2')]
    assert np.array_equal(a<0,b<0) and np.array_equal(b<0,c<0)
    assert r['changes']['B0_B1']['lost_cells']==int(((a==1)&(b==0)).sum())
    assert r['changes']['B0_B1']['gained_cells']==0
    assert r['valid_cells']==int((a>=0).sum())


def test_rf_silent_offline_and_empty_focus():
    s=seed('urban');s['buildings']=[];s['devices']=[d for d in s['devices'] if d['type']=='RF'];s['zone']={'x0':0,'x1':10,'y0':0,'y1':10}
    v=core.Volume(target_emitting=False)
    r=calculate(s,v)
    assert all(m['coverage']==0 and m['focus_coverage'] is None for m in r['models'].values())
    assert all(d['status']=='online' for d in s['devices'])


def test_persist_export_stale_and_validation(client):
    assert client.get('/baselines').status_code==200
    assert client.get('/static/baselines.js').status_code==200
    path='/api/scenes/urban/baselines';v={'bounds':[700,740,700,740,120,140],'cell':2}
    response=client.post(path,json=v);assert response.status_code==200,response.text
    r=response.json();saved=client.get('/api/baselines/'+r['id']).json()
    assert saved['status']=='completed' and not saved['stale']
    assert saved['snapshot']==r['snapshot'] and saved['models']==r['models']
    assert len(client.get(path).json())==1
    s=client.get('/api/scenes/urban').json();d=s['devices'][0];d['status']='offline'
    assert client.put('/api/scenes/urban/devices/'+d['id'],json=d).status_code==200
    assert client.get('/api/baselines/'+r['id']).json()['stale']
    assert client.post(path,json={**v,'pd_threshold':0}).status_code==422
    assert len(client.get(path).json())==1
    assert client.get('/api/baselines/missing').status_code==404
    assert client.get('/api/scenes/missing/baselines').status_code==404


def test_regions_partition_and_candidate_gains_match_recomputed_model():
    from app.baseline_comparison import ComparisonRequest
    s=seed('urban');request=ComparisonRequest(**core.default_volume().model_dump(),candidate_budget=50)
    r=calculate(s,request);impact=r['impact']
    assert sum(x['valid_cells'] for x in impact['regions'])==r['valid_cells']
    assert sum(x['disagreement_cells'] for x in impact['regions'])==impact['disagreement_cells']
    assert impact['disagreement_cells']>=0
    for row in impact['candidates']:
        trial=copy.deepcopy(s);trial['devices'].append(next(d for d in s['candidates'] if d['id']==row['id']))
        actual=engineering.volume(trial,request)['stats']
        assert row['gains']['B2']['new_cells']==actual['covered_cells']-r['models']['B2']['covered_cells']
        assert row['gains']['B2']['after_coverage']==actual['coverage']
    assert all(row['cost']<=50 for row in impact['candidates'])


def test_no_candidates_zero_gain_and_free_ties():
    from app.baseline_comparison import ComparisonRequest
    s=seed('urban');s['buildings']=[];s['devices']=[]
    d={**s['candidates'][0],'x':700,'y':700,'z':130,'range':2000,'fov':360,'vertical_fov':180,'status':'online','cost':0,'type':'RADAR'}
    s['candidates']=[{**d,'id':'A'},{**d,'id':'B'},{**d,'id':'C','cost':1}]
    q=ComparisonRequest(candidate_budget=0)
    r=calculate(s,q)['impact']
    assert all(set(ids)=={'A','B'} for ids in r['top_sets'].values())
    assert not r['top_sets_differ'] and r['excluded']==[{'id':'C','reason':'超过单设备预算'}]
    s['devices']=[{**d,'id':'installed'}]
    # Existing strong device already covers this local volume: no new threshold coverage.
    r=calculate(s,q)['impact'];assert all(not x for x in r['top_sets'].values())
    s['candidates']=[]
    assert calculate(s,q)['impact']['candidates']==[]
