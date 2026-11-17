"""Cross-entry and boundary checks for the unified spatial model."""
import copy
import numpy as np
import pytest
from app import analysis, engineering, coverage_core as core
from app.route import assess, plan_route


def scene():
    return {'id':'urban','revision':1,'buildings':[],
            'devices':[{'id':'R','type':'RADAR','status':'online','x':0,'y':0,'z':100,
                        'azimuth':0,'pitch':0,'vertical_fov':180,'range':1000,'fov':360,'cost':1}],
            'candidates':[],'zone':{'x0':0,'x1':2000,'y0':0,'y1':2000,'z0':120,'z1':140}}


def test_diagnosis_engineering_and_route_agree_on_identical_points():
    s=scene();v=core.Volume(bounds=[100,120,100,120,100,160],cell=20)
    a=analysis.compute(s,v);b=engineering.volume(s,v)
    for key in ('coverage','focus_coverage','valid_cells','covered_cells','mean_pd','precision_pass_rate'):
        assert a['stats'][key]==b['stats'][key]
    assert a['stats']['focus_valid_cells']==1
    p=core.grid(v)
    r=assess(s,[p[0].tolist(),p[-1].tolist()],20,v)
    f=core.fields(s,p,v)
    assert np.allclose([x['pd'] for x in r['samples_detail']],f['pd'])
    assert r['coverage']==round(a['stats']['coverage'],5)
    assert a['model_version']==b['model_version']==r['model_version']==core.MODEL_VERSION


def test_building_boundary_outside_airspace_and_no_clipping():
    s=scene();s['buildings']=[{'x':100,'y':0,'w':50,'d':100,'h':150}]
    p=np.array([[150,50,100],[200,50,100],[200,50,250],[-1,0,100],[2001,0,100],[50,50,29]],float)
    f=core.fields(s,p,core.default_volume())
    assert f['valid'].tolist()==[False,True,True,False,False,False]
    assert f['pd'][0]==0 and f['pd'][1]==0 and f['pd'][2]>0
    r=assess(s,[[2001,0,100],[2101,0,100]],50)
    assert r['flyable_samples']==0 and r['coverage'] is None
    assert all(not row['sensor_ids'] for row in r['samples_detail'])


def test_correlated_fusion_not_claimed_and_weak_independent_fusion_is_visible():
    s=scene();s['devices'][0]['range']=600
    s['devices'].append({**s['devices'][0],'id':'R2'})
    v=core.Volume(bounds=[490,510,0,20,90,110],cell=20,cochannel_db=6,communication_db=2,weather_db=1)
    p=core.grid(v);f=core.fields(s,p,v)
    single=f['per_device']['R'][0]
    assert 0<single<.5 and f['pd'][0]>=.5
    a=analysis.compute(s,v)
    assert a['layers']==[[[1]]] and a['stats']['coverage']==1
    assert a['stats']['overlap']==0
    assert f['pd'][0]==pytest.approx(1-(1-single)**2)


def test_emitting_target_offline_and_cooperative_contract():
    s=scene();p=np.array([[100,100,100.]])
    for kind in ['RF','ADSB']:
        s['devices'][0]['type']=kind
        assert core.fields(s,p,core.Volume(target_emitting=False))['pd'][0]==0
    s['devices'][0].update(type='RADAR',status='offline')
    assert core.fields(s,p,core.Volume())['pd'][0]==0


def test_grid_rejects_partial_cells_and_supports_core_resolution():
    with pytest.raises(ValueError):core.Volume(bounds=[0,21,0,20,100,120],cell=10)
    p=core.grid(core.Volume(bounds=[10,14,20,24,100,104],cell=2))
    assert len(p)==8 and p[0].tolist()==[11,21,101] and p[-1].tolist()==[13,23,103]


def test_planned_result_recomputes_in_same_model():
    s=scene();s['candidates']=[s['devices'].pop()]
    v=core.Volume(bounds=[100,120,100,120,100,160],cell=20,precision_m=100)
    result=engineering.constrained_plan(s,engineering.ConstrainedPlan(volume=v,budget=1))
    deployed=copy.deepcopy(s);deployed['devices']+=result['selected']
    assert result['stats']==engineering.volume(deployed,v)['stats']
    r=plan_route(deployed,[[110,110,110],[110,110,150]],0,20,True,request=v)
    assert r['after']['coverage']==round(result['stats']['coverage'],5)


def test_geometric_contact_does_not_claim_detection_coverage():
    from test_platform import _open_scene
    r=plan_route(_open_scene(),[[100,500,120],[1800,500,120]],20,50)
    assert r['selected'] and not r['target_met']
    assert r['after']['covered_samples']<r['after']['flyable_samples']
    assert 0<r['after']['longest_gap_m']<=1700
