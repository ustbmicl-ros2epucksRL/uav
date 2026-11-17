import copy
import json
import sqlite3
import pytest
from test_cabinet_demo import accounts, protected_client, login
from app import cabinet_demo as api
import template_registry as registry
import container_contract as contract


@pytest.fixture
def setup(accounts):
    client,server,ids=accounts
    registry.install(api.cabinet())
    return client,server,ids


def definition(summary=False):
    if summary:
        return {'name':'device-record','data_kind':'synthetic','schema':{'_ord':['serial','temperature'],
            'serial':contract.field(1),'temperature':contract.field(2,unit='C')},
            'mapping':{'processor':'record-summary-v1'},'purposes':['stats']}
    return {'name':'numeric-samples','data_kind':'synthetic','schema':copy.deepcopy(contract.TEMPLATE),
        'mapping':{'processor':'numeric-table-v1','table':'samples','features':['feature_1','feature_2'],'label':'label','group':'group_id'},'purposes':['dataset','stats']}


def register(client,d=None,tid=None):
    h=login(client,'owner')
    r=client.post('/api/cabinet/templates',headers=h,json={'definition':d or definition(),'template_id':tid})
    assert r.status_code==200,r.text
    return r.json(),h


def step(client,version,action,h):
    return client.post(f"/api/cabinet/templates/{version['template_id']}/{version['version']}/{action}",headers=h)


def publish(client,v,h):
    assert step(client,v,'submit',h).status_code==200
    h=login(client,'admin')
    assert step(client,v,'approve',h).status_code==200
    assert step(client,v,'publish',h).status_code==200


def package(v,content=None):
    return {'format':registry.FORMAT,'template_id':v['template_id'],'template_version':v['version'],
            'template_sha256':v['digest'],'content':content if content is not None else contract.example()['content']}


def grant(client,aid,name='alice',purpose='dataset'):
    h=login(client,'owner')
    return client.post('/api/cabinet/grants',headers=h,json={'asset_id':aid,'username':name,'purpose':purpose})


def test_lifecycle_version_binding_and_retirement(setup):
    client,_,_=setup
    v,h=register(client)
    assert step(client,v,'approve',h).status_code==403
    assert client.post('/api/cabinet/ingest',headers=h,json=package(v)).status_code==403
    publish(client,v,h)
    h=login(client,'owner'); aid=client.post('/api/cabinet/ingest',headers=h,json=package(v)).json()['asset_id']
    gid=grant(client,aid).json()['grant_id']
    h=login(client,'alice');job=client.post(f'/api/cabinet/grants/{gid}/run',headers=h).json()
    assert job['result']['container']['template_version']==1
    d=definition();d['mapping']['features']=['feature_2']
    v2,h=register(client,d,v['template_id']);assert v2['version']==2
    publish(client,v2,h)
    h=login(client,'alice')
    result=client.get('/api/cabinet/results/'+job['job_id']).json()['result']
    assert result['quality']['features']==2 and result['container']['template_sha256']==v['digest']
    assert client.post(f'/api/cabinet/grants/{gid}/run',headers=h).json()['result']['quality']['features']==2
    h=login(client,'owner');new=client.post('/api/cabinet/ingest',headers=h,json=package(v2)).json()['asset_id']
    assert new!=aid
    h=login(client,'alice');assert all(g['asset']!=new for g in client.get('/api/cabinet/state').json()['grants'])
    h=login(client,'admin');assert step(client,v,'retire',h).status_code==200
    assert step(client,v,'publish',h).status_code==409
    h=login(client,'alice');assert client.get('/api/cabinet/results/'+job['job_id']).status_code==403
    assert client.post(f'/api/cabinet/grants/{gid}/run',headers=h).status_code==403


def test_summary_type_and_org_isolation(setup):
    client,_,_=setup
    v,h=register(client,definition(True));publish(client,v,h)
    h=login(client,'other');assert client.get('/api/cabinet/templates').json()==[]
    assert client.post('/api/cabinet/ingest',headers=h,json=package(v,{'serial':'sensitive-serial','temperature':23})).status_code==403
    h=login(client,'owner');aid=client.post('/api/cabinet/ingest',headers=h,json=package(v,{'serial':'sensitive-serial','temperature':23})).json()['asset_id']
    assert grant(client,aid).status_code==403
    gid=grant(client,aid,'bobby','stats').json()['grant_id']
    h=login(client,'bobby');r=client.post(f'/api/cabinet/grants/{gid}/run',headers=h)
    assert r.status_code==200 and 'sensitive-serial' not in r.text and 'splits' not in r.text
    assert r.json()['result']['quality']['present_fields']==2


@pytest.mark.parametrize('attack',['schema','binding','rollback','status'])
def test_template_tamper_rollback(setup,attack):
    client,_,_=setup
    v,h=register(client);publish(client,v,h)
    h=login(client,'owner');client.post('/api/cabinet/ingest',headers=h,json=package(v))
    path=api.store.DATA/'cabinet-demo'/'persistent.db';snapshot=path.read_bytes()
    h=login(client,'admin');step(client,v,'retire',h)
    if attack=='rollback':path.write_bytes(snapshot)
    else:
        with sqlite3.connect(path) as db:
            db.execute({'schema':"UPDATE container_versions SET definition='{}'",'binding':"UPDATE asset_templates SET version=999",'status':"UPDATE container_versions SET status='published'"}[attack])
    assert client.get('/api/cabinet/templates').status_code==409


def test_invalid_schema_mapper_and_self_review(setup):
    client,_,_=setup
    h=login(client,'owner')
    for change in ('exec','hidden','unsupported'):
        d=definition()
        if change=='exec':d['mapping']['processor']='python:exec'
        if change=='hidden':d['schema']['undeclared']=contract.field(1)
        if change=='unsupported':d['schema']['provenance']['t']=5
        assert client.post('/api/cabinet/templates',headers=h,json={'definition':d}).status_code==422
    h=login(client,'admin');r=client.post('/api/cabinet/templates',headers=h,json={'definition':definition()}).json()
    assert step(client,r,'submit',h).status_code==200
    assert step(client,r,'approve',h).status_code==403
    assert step(client,r,'publish',h).status_code==403


def test_rejected_versions_and_fresh_process(setup):
    import os, subprocess, sys
    client,_,_=setup
    v,h=register(client)
    assert step(client,v,'submit',h).status_code==200
    h=login(client,'admin');assert step(client,v,'reject',h).status_code==200
    assert step(client,v,'publish',h).status_code==409
    v2,h=register(client,tid=v['template_id']);publish(client,v2,h)
    h=login(client,'owner');aid=client.post('/api/cabinet/ingest',headers=h,json=package(v2)).json()['asset_id']
    gid=grant(client,aid).json()['grant_id']
    h=login(client,'alice');cookie=client.cookies.get(api.COOKIE)
    script='''
import json,sys
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import cabinet_demo as api
v=json.load(sys.stdin); app=FastAPI();app.include_router(api.router)
with TestClient(app) as c:
 c.cookies.set(api.COOKIE,v['cookie'])
 me=c.get('/api/cabinet/me');assert me.status_code==200
 r=c.post('/api/cabinet/grants/'+v['grant']+'/run',headers={'X-Cabinet-Request':'1','X-CSRF-Token':me.json()['csrf']})
 assert r.status_code==200,r.text
 assert r.json()['result']['container']['template_version']==2
'''
    result=subprocess.run([sys.executable,'-c',script],input=json.dumps({'cookie':cookie,'grant':gid}),text=True,capture_output=True,env=dict(os.environ,SENSE_DATA_DIR=str(api.store.DATA)),timeout=30)
    assert result.returncode==0,result.stderr


def test_atomic_binding_and_digest_substitution(setup):
    client,_,_=setup
    v,h=register(client);publish(client,v,h);h=login(client,'owner')
    altered=package(v);altered['template_sha256']='0'*64
    assert client.post('/api/cabinet/ingest',headers=h,json=altered).status_code==403
    altered=package(v);altered['content']['samples'][0]['feature_1']=True
    assert client.post('/api/cabinet/ingest',headers=h,json=altered).status_code==422
    with api.cabinet()._transaction() as db:
        assert db.execute('SELECT count(*) FROM asset_templates').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==0
    # Extra permissions in the envelope never become grants or template settings.
    altered=package(v);altered['purposes']=['dataset']
    assert client.post('/api/cabinet/ingest',headers=h,json=altered).status_code==422



def test_invalid_identifiers_rejected(setup):
    client,_,_=setup
    h=login(client,'owner')
    for field,value in [('template_id',{}),('template_version',True),('template_sha256',[]),('template_version',-1)]:
        body={'format':registry.FORMAT,'template_id':'a'*32,'template_version':1,'template_sha256':'b'*64,'content':{}}
        body[field]=value
        assert client.post('/api/cabinet/ingest',headers=h,json=body).status_code==422
