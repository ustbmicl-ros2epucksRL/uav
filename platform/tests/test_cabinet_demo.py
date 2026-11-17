"""Account, organisation and external-checkpoint integration tests."""
import json
import sqlite3
import threading
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import cabinet_demo as demo
from app import cabinet_auth as auth
from checkpoint_service import Authority, make_server

@pytest.fixture
def protected_client(tmp_path, monkeypatch):
    monkeypatch.setattr(demo.store, 'DATA', tmp_path)
    from prepare_checkpoint import prepare
    provisioned = prepare(tmp_path/'provisioned', '127.0.0.1', 'platform-test')
    for name in ('authority.db', 'ca.pem', 'tls.pem', 'tls.key', 'token', 'state.key'):
        (tmp_path/name).write_bytes((provisioned/name).read_bytes())
    authority = Authority(tmp_path/'authority.db')
    server = make_server(authority, '127.0.0.1', 0,
                         tmp_path/'tls.pem', tmp_path/'tls.key')
    server.test_authority = authority
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = dict(url=f'https://127.0.0.1:{server.server_port}', namespace='platform-test',
                  ca_file=str(tmp_path/'ca.pem'), token_file=str(tmp_path/'token'),
                  state_key_file=str(tmp_path/'state.key'))
    (tmp_path/'config.json').write_text(json.dumps(config))
    monkeypatch.setenv('SENSE_CABINET_CHECKPOINT_CONFIG', str(tmp_path/'config.json'))
    monkeypatch.setenv('SENSE_CABINET_ALLOW_HTTP', '1')
    from app import cabinet_auth as auth
    cab = demo.persistence.open_cabinet(tmp_path/'cabinet-demo', demo.configured_guard())
    auth.install(cab)
    app = FastAPI(); app.include_router(demo.router)
    try:
        with TestClient(app) as client:
            yield client, server
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)



PASSWORD = 'testing-only-unique-password-123'


@pytest.fixture
def accounts(protected_client):
    client, server = protected_client
    cab = demo.cabinet()
    org1 = auth.add_org(cab, 'Owner organisation')
    org2 = auth.add_org(cab, 'Partner organisation')
    ids = {}
    for name, org, role in [('owner',org1,'owner'), ('alice',org2,'dataset'),
                             ('bobby',org2,'stats'), ('admin',org1,'org_admin'),
                             ('other',org2,'owner')]:
        ids[name] = auth.add_user(cab, org, name, PASSWORD, role)
    return client, server, ids


def login(client, name, password=PASSWORD):
    response=client.post('/api/cabinet/login',json={'username':name,'password':password}, headers={'X-Cabinet-Request':'1'})
    assert response.status_code==200, response.text
    return {'X-Cabinet-Request':'1','X-CSRF-Token':response.json()['csrf']}


def flow(client):
    headers=login(client,'owner')
    asset=client.post('/api/cabinet/ingest',headers=headers).json()['asset_id']
    grants={}
    for name,purpose in [('alice','dataset'),('bobby','stats')]:
        r=client.post('/api/cabinet/grants',headers=headers,json={'asset_id':asset,'username':name,'purpose':purpose})
        assert r.status_code==200,r.text
        grants[name]=r.json()['grant_id']
    return asset,grants


def test_independent_accounts_and_idor(accounts):
    client,_,ids=accounts
    asset,grants=flow(client)
    headers=login(client,'alice')
    a=client.post('/api/cabinet/grants/'+grants['alice']+'/run',headers=headers).json()
    assert 'splits' in a['result']
    assert 'token' not in client.get('/api/cabinet/me').text
    headers=login(client,'bobby')
    b=client.post('/api/cabinet/grants/'+grants['bobby']+'/run',headers=headers).json()
    assert 'splits' not in b['result']
    assert client.get('/api/cabinet/results/'+a['job_id']).status_code==404
    assert client.post('/api/cabinet/grants/'+grants['alice']+'/run',headers=headers).status_code==404
    assert client.post('/api/cabinet/ingest',headers=headers).status_code==403
    state=client.get('/api/cabinet/state').json()
    assert all(g['subject']==ids['bobby'] for g in state['grants'])
    assert all(j['id']!=a['job_id'] for j in state['jobs'])
    assert client.get('/api/cabinet-demo/resume').status_code==410
    assert client.post('/api/cabinet-demo/session').status_code==410
    headers=login(client,'other')
    assert client.get('/api/cabinet/state').json()['assets']==[]
    assert client.post('/api/cabinet/grants/'+grants['alice']+'/revoke',headers=headers).status_code==403
    assert client.post('/api/cabinet/grants',headers=headers,json={'asset_id':asset,'username':'bobby','purpose':'stats'}).status_code==404
    headers=login(client,'owner')
    assert client.post('/api/cabinet/grants/'+grants['alice']+'/revoke',headers=headers).status_code==200
    headers=login(client,'alice')
    assert client.get('/api/cabinet/results/'+a['job_id']).status_code==403
    headers=login(client,'bobby')
    assert client.get('/api/cabinet/results/'+b['job_id']).status_code==200


def test_org_admin_scope_and_disabled_sessions(accounts):
    client,_,ids=accounts
    _,grants=flow(client)
    headers=login(client,'admin')
    members=client.get('/api/cabinet/members').json()
    assert {m['username'] for m in members}=={'owner','admin'}
    assert client.post('/api/cabinet/members/'+ids['alice']+'/enabled',headers=headers,json={'enabled':False}).status_code==403
    assert client.post('/api/cabinet/ingest',headers=headers).status_code==403
    assert client.post('/api/cabinet/members',headers=headers,json={'username':'evil','password':PASSWORD,'role':'org_admin'}).status_code==403
    assert client.post('/api/cabinet/members',headers=headers,json={'username':'newuser','password':PASSWORD,'role':'stats','org':'other'}).status_code==422
    assert client.post('/api/cabinet/members',headers=headers,json={'username':'newuser','password':PASSWORD,'role':'stats'}).status_code==200
    admin_cookie=client.cookies.get(demo.COOKIE)
    with TestClient(client.app) as independent_owner:
        login(independent_owner,'owner'); owner_cookie=independent_owner.cookies.get(demo.COOKIE)
    client.cookies.set(demo.COOKIE,admin_cookie,domain='testserver.local',path='/')
    # Login replacement revokes the previous cookie in the same browser, so relogin.
    headers=login(client,'admin')
    assert client.post('/api/cabinet/members/'+ids['owner']+'/enabled',headers=headers,json={'enabled':False}).status_code==200
    with TestClient(client.app) as old:
        old.cookies.set(demo.COOKIE,owner_cookie)
        assert old.get('/api/cabinet/me').status_code==401
    headers=login(client,'alice')
    assert client.post('/api/cabinet/grants/'+grants['alice']+'/run',headers=headers).status_code==404


@pytest.mark.parametrize('attack',['quota','rollback','membership','session','outage','missing'])
def test_authenticated_integrity(accounts,attack):
    client,server,ids=accounts
    _,grants=flow(client)
    headers=login(client,'alice')
    path=demo.store.DATA/'cabinet-demo'/'persistent.db'
    snapshot=path.read_bytes()
    assert client.post('/api/cabinet/grants/'+grants['alice']+'/run',headers=headers).status_code==200
    if attack=='rollback':path.write_bytes(snapshot)
    elif attack=='missing':path.unlink()
    elif attack=='outage':server.shutdown();server.server_close()
    else:
        sql={'quota':'UPDATE grants SET used=0', 'membership':"UPDATE auth_users SET role='owner'", 'session':'UPDATE auth_sessions SET expires=99999999999'}[attack]
        with sqlite3.connect(path) as db:db.execute(sql)
    assert client.get('/api/cabinet/me').status_code==(503 if attack=='outage' else 409)
    assert client.post('/api/cabinet/grants/'+grants['alice']+'/run',headers=headers).status_code==(503 if attack=='outage' else 409)


def test_csrf_logout_password_and_lockout(accounts):
    client,_,_=accounts
    assert client.post('/api/cabinet/login',json={'username':'owner','password':PASSWORD}).status_code==403
    headers=login(client,'owner')
    assert client.post('/api/cabinet/ingest',headers={'X-Cabinet-Request':'1'}).status_code==403
    assert client.post('/api/cabinet/ingest',headers={**headers,'Origin':'https://attacker.test'}).status_code==403
    cookie=client.cookies.get(demo.COOKIE)
    assert client.post('/api/cabinet/password',headers=headers,json={'old':PASSWORD,'new':'replacement-password-12345'}).status_code==200
    with TestClient(client.app) as old:
        old.cookies.set(demo.COOKIE,cookie)
        assert old.get('/api/cabinet/me').status_code==401
    headers=login(client,'owner','replacement-password-12345')
    cookie=client.cookies.get(demo.COOKIE)
    assert client.post('/api/cabinet/logout',headers=headers).status_code==200
    client.cookies.set(demo.COOKIE,cookie)
    assert client.get('/api/cabinet/me').status_code==401
    for _ in range(5):
        assert client.post('/api/cabinet/login',json={'username':'alice','password':'wrong'},headers={'X-Cabinet-Request':'1'}).status_code==401
    assert client.post('/api/cabinet/login',json={'username':'alice','password':PASSWORD},headers={'X-Cabinet-Request':'1'}).status_code==429


def test_purpose_legacy_and_sensitive_validation(accounts):
    client,_,_=accounts
    headers=login(client,'owner')
    asset=client.post('/api/cabinet/ingest',headers=headers).json()['asset_id']
    assert client.post('/api/cabinet/grants',headers=headers,json={'asset_id':asset,'username':'bobby','purpose':'dataset'}).status_code==403
    assert client.post('/api/cabinet/grants',headers=headers,json={'asset_id':asset,'username':'alice','purpose':'dataset','role':'owner'}).status_code==422
    secret='x'*129
    response=client.post('/api/cabinet/login',json={'username':'owner','password':secret},headers={'X-Cabinet-Request':'1'})
    assert response.status_code==422 and secret not in response.text


def test_fresh_process_restart_expiry_and_no_plaintext(accounts):
    import subprocess,sys,os,time
    client,server,_=accounts
    _,grants=flow(client)
    headers=login(client,'alice')
    for _ in range(5):
        response=client.post('/api/cabinet/grants/'+grants['alice']+'/run',headers=headers)
        assert response.status_code==200
    job=response.json()['job_id']
    script='''
import json,sys
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import cabinet_demo as api
v=json.load(sys.stdin)
app=FastAPI();app.include_router(api.router)
with TestClient(app) as c:
 c.cookies.set(api.COOKIE,v['cookie'])
 me=c.get('/api/cabinet/me'); assert me.status_code==200
 assert me.json()['user']['username']=='alice'
 assert c.get('/api/cabinet/results/'+v['job']).status_code==200
 assert c.post('/api/cabinet/grants/'+v['grant']+'/run',headers={'X-Cabinet-Request':'1','X-CSRF-Token':me.json()['csrf']}).status_code==403
'''
    result=subprocess.run([sys.executable,'-c',script],input=json.dumps({'cookie':client.cookies.get(demo.COOKIE),'job':job,'grant':grants['alice']}),capture_output=True,text=True,env=dict(os.environ,SENSE_DATA_DIR=str(demo.store.DATA)),timeout=30)
    assert result.returncode==0,result.stderr
    raw=(demo.store.DATA/'cabinet-demo'/'persistent.db').read_bytes()
    assert PASSWORD.encode() not in raw and client.cookies.get(demo.COOKIE).encode() not in raw
    future=time.time()+auth.SESSION_TTL+1
    server.test_authority.clock=lambda:future
    assert client.get('/api/cabinet/me').status_code==401


def test_role_changes_revoke_grants_and_sessions(accounts):
    client,_,ids=accounts
    _,grants=flow(client)
    with TestClient(client.app) as independent_owner:
        login(independent_owner,'owner');cookie=independent_owner.cookies.get(demo.COOKIE)
    headers=login(client,'admin')
    assert client.post('/api/cabinet/members/'+ids['alice']+'/role',headers=headers,json={'role':'owner'}).status_code==403
    assert client.post('/api/cabinet/members/'+ids['owner']+'/role',headers=headers,json={'role':'org_admin'}).status_code==403
    assert client.post('/api/cabinet/members/'+ids['owner']+'/role',headers=headers,json={'role':'stats'}).status_code==200
    assert client.post('/api/cabinet/members/'+ids['owner']+'/role',headers=headers,json={'role':'owner'}).status_code==200
    with TestClient(client.app) as old:
        old.cookies.set(demo.COOKIE,cookie)
        assert old.get('/api/cabinet/me').status_code==401
    headers=login(client,'alice')
    assert client.post('/api/cabinet/grants/'+grants['alice']+'/run',headers=headers).status_code==403



def test_mounted_origin_and_org_lifecycle(accounts):
    import os,subprocess,sys
    client,_,ids=accounts
    headers=login(client,'alice');cookie=client.cookies.get(demo.COOKIE)
    org=client.get('/api/cabinet/me').json()['user']['org']
    outer=FastAPI();outer.mount('/network',client.app)
    with TestClient(outer) as mounted:
        mounted.cookies.set(demo.COOKIE,cookie)
        assert mounted.get('/network/api/cabinet/me',headers={'Origin':'http://testserver'}).status_code==200
    for enabled in ('no','yes'):
        result=subprocess.run([sys.executable,'-m','app.cabinet_admin','set-org','--org',org,'--enabled',enabled],
            env=dict(os.environ,SENSE_DATA_DIR=str(demo.store.DATA)),capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr
        assert client.get('/api/cabinet/me').status_code==401
    login(client,'alice')
    assert client.get('/api/cabinet/me').status_code==200


def test_container_ingest_and_authorized_outputs(accounts):
    import copy
    client,_,_=accounts
    headers=login(client,'owner')
    response=client.get('/api/cabinet/container-template')
    assert response.status_code==200
    package=response.json()['example']
    bad=copy.deepcopy(package);bad['template_sha256']='0'*64
    assert client.post('/api/cabinet/ingest',headers=headers,json=bad).status_code==422
    assert client.get('/api/cabinet/state').json()['assets']==[]
    package['content']['provenance']['source']='private container acquisition source'
    asset=client.post('/api/cabinet/ingest',headers=headers,json=package).json()['asset_id']
    grants={}
    for name,purpose in [('alice','dataset'),('bobby','stats')]:
        grants[name]=client.post('/api/cabinet/grants',headers=headers,json={'asset_id':asset,'username':name,'purpose':purpose}).json()['grant_id']
    for name in ('alice','bobby'):
        headers=login(client,name)
        assert client.post('/api/cabinet/ingest',headers=headers,json=package).status_code==403
        result=client.post('/api/cabinet/grants/'+grants[name]+'/run',headers=headers)
        assert result.status_code==200
        assert ('splits' in result.json()['result'])==(name=='alice')
        assert result.json()['result']['container']['template_sha256']==package['template_sha256']
        assert 'private container acquisition source' not in result.text
    assert b'private container acquisition source' not in (demo.store.DATA/'cabinet-demo'/'persistent.db').read_bytes()
