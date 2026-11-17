import numpy as np
import pytest
from app.engineering import Volume,grid,visible,fields,volume,Calibration,calibrate,Schedule,schedule,ConstrainedPlan,constrained_plan
from app.scenes import seed
from test_platform import client


def device(kind='RADAR'):
    return {'id':'D','type':kind,'x':0,'y':0,'z':100,'range':2000,'azimuth':0,'fov':360,'status':'online','cost':1}


def test_los_box_intersections_and_parallel():
    d=device();p=np.array([[200,0,100],[200,100,100],[200,0,200],[0,200,100]],float)
    b=[{'x':80,'y':-20,'w':40,'d':40,'h':150}]
    assert visible(p,d,b)[0].tolist()==[False,True,False,True]
    p[2,2]=300
    assert visible(p,d,b)[0][2]


def test_interference_and_singular_information():
    p=np.array([[500,0,100],[500,500,150]],float)
    s={'devices':[device('RF')],'buildings':[]}
    f=fields(s,p,Volume());assert not f['observable'].any()
    assert np.isinf(f['precision_m']).all()
    s['devices']=[device()]
    a=fields(s,p,Volume(cochannel_db=0,communication_db=0,weather_db=0))
    b=fields(s,p,Volume(cochannel_db=20))
    assert np.all(b['pd']<a['pd']) and np.all(b['precision_m']>a['precision_m'])
    s['devices'][0]['status']='offline'
    assert not fields(s,p,Volume())['observable'].any()


def test_resolution_and_bounded_grid():
    a=Volume(bounds=[0,20,0,20,120,140],cell=2)
    assert len(grid(a))==1000
    with pytest.raises(ValueError):Volume(bounds=[0,2000,0,2000,0,300],cell=2)
    r=volume(seed('urban'),a)
    assert r['evaluated_cells']==1000 and all(len(p)==6 for p in r['cloud'])


def test_calibration_holdout_and_outlier():
    rng=np.random.default_rng(2);a=rng.normal(0,1,(40,3))+[12,4,-3];a[0]=[2000]*3
    r=calibrate(Calibration(sensor_id='D',source='test residuals',residuals=a.tolist()))
    assert r['holdout_samples']==10 and r['accepted_samples']<30
    assert r['holdout_after_rmse_m']<r['holdout_before_rmse_m']*.2
    assert .5<r['sigma_m']<2


def test_subset_constraints_and_resource_limits():
    s={'id':'test','revision':1,'devices':[],'buildings':[],'zone':{'x0':100,'x1':300,'y0':100,'y1':300},'candidates':[device()]}
    v=Volume(bounds=[100,300,100,300,120,160],cell=20,precision_m=100)
    r=constrained_plan(s,ConstrainedPlan(volume=v,budget=0))
    assert not r['feasible'] and r['selected']==[] and r['constraint_deficit']>0
    r=constrained_plan(s,ConstrainedPlan(volume=v,budget=1))
    assert r['feasible'] and r['added_cost']==1
    s['devices']=[device()]
    request=Schedule(tasks=[{'id':'low','kind':'discovery','priority':1,'bandwidth':3,'compute':1},
                            {'id':'high','kind':'warning','priority':10,'bandwidth':3,'compute':2}],bandwidth=3,compute=2)
    r=schedule(s,request)
    assert r['assignments'][0]['id']=='high' and len(r['pending'])==1
    assert r['used_bandwidth']<=3 and r['used_compute']<=2


def test_workbench_api_persistence_and_validation(client):
    assert client.get('/workbench').status_code==200
    path='/api/scenes/urban/engineering'
    r=client.post(path+'/volume',json={'bounds':[700,800,700,800,120,140],'cell':10})
    assert r.status_code==200 and r.json()['evaluated_cells']==200
    assert client.get(path+'/report').json()['volume']['evaluated_cells']==200
    assert client.post(path+'/volume',json={'bounds':[0,2000,0,2000,0,300],'cell':2}).status_code==422
    assert client.post(path+'/calibration',json={'sensor_id':'missing','source':'test','residuals':[[1,2,3]]*20}).status_code==422
    data={'sensor_id':'RADAR-01','source':'independent test','residuals':[[1,2,3]]*20}
    assert client.post(path+'/calibration',json=data).status_code==200
    assert client.get(path+'/report').json()['calibration']['source']=='independent test'
    r=client.post(path+'/plan',json={'volume':{'bounds':[700,800,700,800,120,140],'cell':20},'budget':0})
    assert r.status_code==200 and r.json()['added_cost']==0


def test_bearing_triangulation_and_azimuth_convention():
    p=np.array([[500,500,140]],float)
    d=device('EO');d['fov']=30
    d['azimuth']=0
    assert not visible(np.array([[500,0,100.]]),d,[])[0][0]
    assert visible(np.array([[0,500,100.]]),d,[])[0][0]
    d['fov']=360
    s={'devices':[d],'buildings':[]}
    assert not fields(s,p,Volume())['observable'][0]
    other={**d,'id':'E2','x':1000}
    s['devices'].append(other)
    assert fields(s,p,Volume())['observable'][0]


def test_resources_and_stale_plan_application(client):
    scene=client.get('/api/scenes/urban').json()
    data={k:scene[k] for k in ('devices','buildings','candidates')};data['source']='test survey'
    data['devices'].append({**data['devices'][4],'id':'IR-01','type':'IR'})
    assert client.put('/api/scenes/urban/resources',json=data).status_code==200
    path='/api/scenes/urban/engineering'
    request={'volume':{'bounds':[700,800,700,800,120,140],'cell':20},'budget':0}
    plan=client.post(path+'/plan',json=request).json()
    assert client.put('/api/scenes/urban/resources',json=data).status_code==200
    assert client.post(path+'/apply/'+plan['id']).status_code==409
    report=client.get(path+'/report').json()
    assert report['artifact_state']['engineering-plan']['scene_changed']
    plan=client.post(path+'/plan',json=request).json()
    assert client.post(path+'/apply/'+plan['id']).status_code==200
    assert client.post(path+'/apply/'+plan['id']).status_code==409
    data['buildings']=[{'x':1990,'y':100,'w':100,'d':100,'h':10}]
    assert client.put('/api/scenes/urban/resources',json=data).status_code==422
