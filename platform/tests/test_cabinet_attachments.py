import copy
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import pytest
from PIL import Image
from test_cabinet_demo import protected_client,accounts,login
from test_cabinet_templates import register,publish,package,step
from app import cabinet_demo as api
import template_registry as registry
import media_attachments as media


def png():
    out=io.BytesIO();Image.new('RGB',(8,8),(20,100,200)).save(out,format='PNG');return out.getvalue()


@pytest.fixture
def setup(accounts):
    client,server,ids=accounts
    registry.install(api.cabinet());media.install(api.cabinet())
    d=registry.examples()[2]['definition']
    v,h=register(client,d);publish(client,v,h)
    return client,server,ids,v


def upload(client,v,raw=None,field='photo',mime='image/png',headers=None):
    h=headers or login(client,'owner')
    return client.post(f"/api/cabinet/templates/{v['template_id']}/{v['version']}/attachments/upload",params={'field':field},content=png() if raw is None else raw,headers={**h,'Content-Type':mime})


def ingest(client,v,mid):
    h=login(client,'owner')
    return client.post('/api/cabinet/ingest',headers=h,json=package(v,{'description':'synthetic','photo':{'attachment_id':mid}}))


def grant(client,asset,name,purpose):
    h=login(client,'owner')
    r=client.post('/api/cabinet/grants',headers=h,json={'asset_id':asset,'username':name,'purpose':purpose})
    assert r.status_code==200,r.text
    return r.json()['grant_id']


def preview_url(gid,mid): return f'/api/cabinet/grants/{gid}/attachments/{mid}/preview'


def test_upload_preview_revoke_and_audit(setup):
    c,_,ids,v=setup
    uploaded=upload(c,v);assert uploaded.status_code==200,uploaded.text
    mid=uploaded.json()['attachment_id']
    aid=ingest(c,v,mid).json()['asset_id']
    pg=grant(c,aid,'alice','preview');sg=grant(c,aid,'bobby','stats')
    h=login(c,'alice');r=c.post(preview_url(pg,mid),headers=h)
    assert r.status_code==200 and r.content==png()
    assert r.headers['cache-control']=='no-store' and r.headers['x-content-type-options']=='nosniff'
    assert c.get(preview_url(pg,mid)).status_code==405
    assert c.post(preview_url(pg,mid),headers={**h,'Range':'bytes=0-1'}).status_code==416
    assert c.post(preview_url(pg,mid),headers={**h,'Origin':'https://evil.test'}).status_code==403
    h=login(c,'bobby')
    assert c.get(f'/api/cabinet/grants/{pg}/attachments').status_code==404
    assert c.post(preview_url(sg,mid),headers=h).status_code==403
    stats=c.post(f'/api/cabinet/grants/{sg}/run',headers=h)
    assert stats.status_code==200 and mid not in stats.text
    h=login(c,'other');assert c.post(f'/api/cabinet/assets/{aid}/attachments/{mid}/preview',headers=h).status_code==403
    h=login(c,'owner');assert c.post(f'/api/cabinet/assets/{aid}/attachments/{mid}/preview',headers=h).content==png()
    assert c.post(f'/api/cabinet/grants/{pg}/revoke',headers=h).status_code==200
    h=login(c,'alice');assert c.post(preview_url(pg,mid),headers=h).status_code==403
    assert c.post(preview_url(pg,mid),headers={**h,'Range':'bytes=0-1','If-None-Match':'old'}).status_code==403
    assert c.get(f'/api/cabinet/grants/{pg}/attachments').status_code==403
    login(c,'owner');audit=c.get('/api/cabinet/state').json()['audit']
    assert any(e['action']=='attachment_uploaded' for e in audit)
    assert any(e['action']=='attachment_bound' for e in audit)
    assert any(e['action']=='attachment_preview' for e in audit)
    assert png() not in (api.store.DATA/'cabinet-demo'/'persistent.db').read_bytes()


def test_staging_reuse_and_cross_subject_binding(setup):
    c,_,ids,v=setup
    mid=upload(c,v).json()['attachment_id']
    h=login(c,'alice');assert upload(c,v,headers=h).status_code==403
    h=login(c,'other')
    assert c.post('/api/cabinet/ingest',headers=h,json=package(v,{'description':'x','photo':{'attachment_id':mid}})).status_code==403
    # Same-organisation different owner also cannot appropriate the staged object.
    from app import cabinet_auth
    with api.cabinet()._transaction() as db:org=db.execute('SELECT org FROM auth_users WHERE id=?',(ids['owner'],)).fetchone()[0]
    cabinet_auth.add_user(api.cabinet(),org,'secondowner','testing-only-unique-password-123','owner')
    h=login(c,'secondowner')
    assert c.post('/api/cabinet/ingest',headers=h,json=package(v,{'description':'x','photo':{'attachment_id':mid}})).status_code==403
    assert ingest(c,v,mid).status_code==200
    assert ingest(c,v,mid).status_code==403
    h=login(c,'owner');assert c.post(f'/api/cabinet/attachments/{mid}/discard',headers=h).status_code==403
    staged=upload(c,v).json()['attachment_id'];h=login(c,'owner')
    assert any(x['id']==staged for x in c.get('/api/cabinet/attachments/staged').json())
    assert c.post(f'/api/cabinet/attachments/{staged}/discard',headers=h).status_code==200
    assert ingest(c,v,staged).status_code==403


@pytest.mark.parametrize('attack',['rollback','ciphertext','binding','revoked','retired'])
def test_attachment_integrity(setup,attack):
    c,_,_,v=setup
    mid=upload(c,v).json()['attachment_id'];aid=ingest(c,v,mid).json()['asset_id'];gid=grant(c,aid,'alice','preview')
    path=api.store.DATA/'cabinet-demo'/'persistent.db';snapshot=path.read_bytes()
    h=login(c,'owner');c.post(f'/api/cabinet/grants/{gid}/revoke',headers=h)
    if attack=='rollback':path.write_bytes(snapshot)
    else:
        with sqlite3.connect(path) as db:
            db.execute({'ciphertext':"UPDATE cabinet_attachments SET ciphertext=x'00'",'binding':"UPDATE cabinet_attachments SET asset='other'",'revoked':'UPDATE grants SET revoked=0','retired':"UPDATE container_versions SET status='draft'"}[attack])
    assert c.get('/api/cabinet/me').status_code==409
    assert c.post(f'/api/cabinet/assets/{aid}/attachments/{mid}/preview',headers=h).status_code==409


def test_type_limits_and_wrong_version(setup):
    c,_,_,v=setup
    assert upload(c,v,raw=b'<svg onload="alert(1)"/>').status_code==422
    assert upload(c,v,mime='image/jpeg').status_code==422
    assert upload(c,v,mime='text/html').status_code==415
    assert upload(c,v,raw=b'x'*(media.MAX_BYTES+1)).status_code==413
    h=login(c,'owner')
    assert upload(c,v,field='../photo',headers=h).status_code==403
    v2,h=register(c,registry.examples()[2]['definition'],v['template_id']);publish(c,v2,h)
    mid=upload(c,v).json()['attachment_id']
    assert ingest(c,v2,mid).status_code==403



def test_independent_preview_from_dataset_permission(setup):
    import container_contract
    c,_,_,_=setup
    d=registry.examples()[0]['definition'];d['schema']=copy.deepcopy(d['schema'])
    d['schema']['_ord'].append('photo');d['schema']['photo']=container_contract.field(4,mime=['image/png'],max_bytes=media.MAX_BYTES)
    d['purposes'].append('preview');v,h=register(c,d);publish(c,v,h)
    mid=upload(c,v).json()['attachment_id']
    content=container_contract.example()['content'];content['photo']={'attachment_id':mid}
    h=login(c,'owner');aid=c.post('/api/cabinet/ingest',headers=h,json=package(v,content)).json()['asset_id']
    dataset=grant(c,aid,'alice','dataset')
    h=login(c,'alice');assert c.post(preview_url(dataset,mid),headers=h).status_code==403
    assert c.get(f'/api/cabinet/grants/{dataset}/attachments').status_code==403
    result=c.post(f'/api/cabinet/grants/{dataset}/run',headers=h)
    assert result.status_code==200 and mid not in result.text
    pg=grant(c,aid,'alice','preview');h=login(c,'alice')
    assert c.post(preview_url(pg,mid),headers=h).status_code==200


def test_video_preview_and_missing_probe(setup,monkeypatch):
    c,_,_,v=setup
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg fixture generator required')
    raw=subprocess.check_output([ffmpeg,'-v','error','-f','lavfi','-i','color=c=blue:s=32x32:r=5','-t','0.4',
          '-c:v','libx264','-pix_fmt','yuv420p','-movflags','frag_keyframe+empty_moov','-f','mp4','pipe:1'],timeout=20)
    video=upload(c,v,raw=raw,field='video',mime='video/mp4')
    assert video.status_code==200,video.text
    photo=upload(c,v).json()['attachment_id'];mid=video.json()['attachment_id'];h=login(c,'owner')
    aid=c.post('/api/cabinet/ingest',headers=h,json=package(v,{'description':'synthetic media',
        'photo':{'attachment_id':photo},'video':{'attachment_id':mid}})).json()['asset_id']
    pg=grant(c,aid,'alice','preview');h=login(c,'alice')
    response=c.post(preview_url(pg,mid),headers=h)
    assert response.status_code==200 and response.content==raw and response.headers['content-type']=='video/mp4'
    monkeypatch.setenv('SENSE_CABINET_FFPROBE','/no/such/ffprobe')
    assert upload(c,v,raw=raw,field='video',mime='video/mp4').status_code==503


def test_quota_restart_expiry_retirement_and_outage(setup):
    c,server,_,v=setup
    mid=upload(c,v).json()['attachment_id'];aid=ingest(c,v,mid).json()['asset_id'];gid=grant(c,aid,'alice','preview')
    h=login(c,'alice')
    for _ in range(5):assert c.post(preview_url(gid,mid),headers=h).status_code==200
    cookie=c.cookies.get(api.COOKIE)
    script='''
import json,sys
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import cabinet_demo as api
v=json.load(sys.stdin);a=FastAPI();a.include_router(api.router)
with TestClient(a) as c:
 c.cookies.set(api.COOKIE,v['cookie']);me=c.get('/api/cabinet/me').json()
 r=c.post(v['url'],headers={'X-Cabinet-Request':'1','X-CSRF-Token':me['csrf']})
 assert r.status_code==403,r.text
'''
    result=subprocess.run([sys.executable,'-c',script],input=json.dumps({'cookie':cookie,'url':preview_url(gid,mid)}),
        text=True,capture_output=True,env=dict(os.environ,SENSE_DATA_DIR=str(api.store.DATA)),timeout=30)
    assert result.returncode==0,result.stderr
    h=login(c,'admin');assert step(c,v,'retire',h).status_code==200
    h=login(c,'owner');assert c.post(f'/api/cabinet/assets/{aid}/attachments/{mid}/preview',headers=h).status_code==403
    server.shutdown();server.server_close()
    assert c.post(f'/api/cabinet/assets/{aid}/attachments/{mid}/preview',headers=h).status_code==503



def test_expired_staging_and_failed_atomic_binding(setup):
    import time
    c,server,_,v=setup
    photo=upload(c,v).json()['attachment_id']
    h=login(c,'owner')
    duplicate=package(v,{'description':'synthetic','photo':{'attachment_id':photo},'video':{'attachment_id':photo}})
    assert c.post('/api/cabinet/ingest',headers=h,json=duplicate).status_code==422
    bad=package(v,{'description':'synthetic','photo':{'attachment_id':photo},'video':{'attachment_id':'f'*32}})
    assert c.post('/api/cabinet/ingest',headers=h,json=bad).status_code==403
    with api.cabinet()._transaction() as db:
        assert db.execute('SELECT asset FROM cabinet_attachments WHERE id=?',(photo,)).fetchone()[0] is None
        assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==0
    future=time.time()+86401;server.test_authority.clock=lambda:future
    assert ingest(c,v,photo).status_code==403


def test_upload_retirement_and_owner_disable(setup):
    c,_,ids,v=setup
    mid=upload(c,v).json()['attachment_id'];aid=ingest(c,v,mid).json()['asset_id'];gid=grant(c,aid,'alice','preview')
    h=login(c,'admin')
    assert c.post(f"/api/cabinet/members/{ids['owner']}/enabled",headers=h,json={'enabled':False}).status_code==200
    h=login(c,'alice');assert c.post(preview_url(gid,mid),headers=h).status_code==404
    h=login(c,'admin');assert c.post(f"/api/cabinet/members/{ids['owner']}/enabled",headers=h,json={'enabled':True}).status_code==200
    assert step(c,v,'retire',h).status_code==200
    assert upload(c,v).status_code==403
