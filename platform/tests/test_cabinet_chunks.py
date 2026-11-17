"""Real HTTPS checkpoint + S3 protocol emulator; all media/identities synthetic."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import pytest
from test_cabinet_demo import protected_client,accounts,login
from test_cabinet_templates import register,publish,package,step
from test_cabinet_attachments import png,grant
from app import cabinet_demo as api
import template_registry as registry
import media_attachments as media
import chunked_attachments as chunks
from object_store import configured_store


@pytest.fixture
def setup(accounts,monkeypatch):
    from moto.server import create_backend_app
    from werkzeug.serving import make_server
    c,checkpoint,ids=accounts;folder=api.store.DATA
    server=make_server('127.0.0.1',0,create_backend_app('s3'),threaded=True,
        ssl_context=(str(folder/'tls.pem'),str(folder/'tls.key')))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    (folder/'access').write_text('synthetic-access');(folder/'secret').write_text('synthetic-secret')
    (folder/'objects.json').write_text(json.dumps({'endpoint':f'https://127.0.0.1:{server.server_port}',
        'bucket':'cabinet-test','prefix':'chunks','ca_file':str(folder/'ca.pem'),
        'access_key_file':str(folder/'access'),'secret_key_file':str(folder/'secret')}))
    monkeypatch.setenv('SENSE_CABINET_OBJECT_CONFIG',str(folder/'objects.json'))
    configured_store().client.create_bucket(Bucket='cabinet-test')
    registry.install(api.cabinet());media.install(api.cabinet());chunks.install(api.cabinet())
    d=registry.examples()[2]['definition'];d['schema']['video']['misc']['max_bytes']=chunks.MAX_BYTES
    v,h=register(c,d);publish(c,v,h)
    try:yield c,checkpoint,ids,v,server
    finally:server.shutdown();server.server_close();thread.join(2)


def start(c,v,h,size,mime='image/png',field='photo'):
    r=c.post(f"/api/cabinet/templates/{v['template_id']}/{v['version']}/attachments/uploads",headers=h,
        json={'field':field,'size':size,'mime':mime});assert r.status_code==200,r.text
    return r.json()['upload_id']


def put(c,mid,h,raw,index=0):
    return c.post(f'/api/cabinet/uploads/{mid}/chunks/{index}',headers=h,content=raw)


def complete(c,mid,h):return c.post(f'/api/cabinet/uploads/{mid}/complete',headers=h)


def fixture_asset(c,v):
    h=login(c,'owner');raw=png();mid=start(c,v,h,len(raw));assert put(c,mid,h,raw).status_code==200
    assert complete(c,mid,h).status_code==200
    aid=c.post('/api/cabinet/ingest',headers=h,json=package(v,{'description':'synthetic','photo':{'attachment_id':mid}})).json()['asset_id']
    gid=grant(c,aid,'alice','preview');h=login(c,'alice')
    r=c.post(f'/api/cabinet/grants/{gid}/attachments/{mid}/stream',headers=h);assert r.status_code==200,r.text
    return aid,mid,gid,r.json()['stream_id'],h


def test_resume_restart_and_ciphertext_external(setup):
    c,_,_,v,_=setup;h=login(c,'owner');raw=png();mid=start(c,v,h,len(raw))
    assert put(c,mid,h,raw).status_code==200
    assert put(c,mid,h,raw).json()['reused']
    assert put(c,mid,h,b'x'*len(raw)).status_code==409
    # A fresh process reconstructs both checkpoint and HTTPS S3 clients.
    script='''
import json,sys
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import cabinet_demo as api
v=json.load(sys.stdin);a=FastAPI();a.include_router(api.router)
with TestClient(a) as c:
 c.cookies.set(api.COOKIE,v['cookie']);me=c.get('/api/cabinet/me').json()
 h={'X-Cabinet-Request':'1','X-CSRF-Token':me['csrf']}
 s=c.get('/api/cabinet/uploads/'+v['mid']);assert s.status_code==200,s.text
 assert len(s.json()['chunks'])==1
 r=c.post('/api/cabinet/uploads/'+v['mid']+'/complete',headers=h);assert r.status_code==200,r.text
'''
    result=subprocess.run([sys.executable,'-c',script],input=json.dumps({'cookie':c.cookies.get(api.COOKIE),'mid':mid}),
        text=True,capture_output=True,env=dict(os.environ,SENSE_DATA_DIR=str(api.store.DATA)),timeout=40)
    assert result.returncode==0,result.stderr
    assert complete(c,mid,h).json()['attachment_id']==mid
    with api.cabinet()._transaction() as db:
        attachment=db.execute('SELECT * FROM cabinet_attachments WHERE id=?',(mid,)).fetchone()
        chunk=db.execute('SELECT * FROM attachment_chunks WHERE upload=?',(mid,)).fetchone()
        assert attachment['ciphertext']==b''
        assert configured_store().get(chunk['object_key'],len(raw)+16)!=raw
    assert raw not in (api.store.DATA/'cabinet-demo'/'persistent.db').read_bytes()


def test_subject_range_quota_revoke_and_login_binding(setup):
    c,_,_,v,_=setup;aid,mid,gid,sid,h=fixture_asset(c,v);url=f'/api/cabinet/streams/{sid}'
    for _ in range(7):
        r=c.get(url,headers={'Range':'bytes=0-9'});assert r.status_code==206 and r.content==png()[:10]
        assert r.headers['content-range']==f'bytes 0-9/{len(png())}'
        assert r.headers['cache-control']=='no-store' and 'etag' not in r.headers
    assert c.get(url,headers={'Range':'bytes=-4'}).content==png()[-4:]
    assert c.get(url,headers={'Range':'bytes=0-1,4-5'}).status_code==416
    assert c.get(url,headers={'Range':'bytes=99999-'}).status_code==416
    login(c,'bobby');assert c.get(url).status_code==403
    h=login(c,'alice');assert c.get(url).status_code==403 # same user, different login
    r=c.post(f'/api/cabinet/grants/{gid}/attachments/{mid}/stream',headers=h);sid=r.json()['stream_id']
    h=login(c,'owner');assert c.post(f'/api/cabinet/grants/{gid}/revoke',headers=h).status_code==200
    # Retain the original Alice cookie, otherwise the login binding alone would deny.
    with api.cabinet()._transaction() as db:
        assert db.execute('SELECT used FROM grants WHERE id=?',(gid,)).fetchone()[0]==2
    h=login(c,'alice');assert c.post(f'/api/cabinet/grants/{gid}/attachments/{mid}/stream',headers=h).status_code==403


@pytest.mark.parametrize('attack',['ciphertext','missing','manifest','snapshot'])
def test_tamper_and_rollback(setup,attack):
    c,_,_,v,_=setup;aid,mid,gid,sid,h=fixture_asset(c,v);cookie=c.cookies.get(api.COOKIE)
    path=api.store.DATA/'cabinet-demo'/'persistent.db';snapshot=path.read_bytes()
    with api.cabinet()._transaction() as db: chunk=dict(db.execute('SELECT * FROM attachment_chunks WHERE upload=?',(mid,)).fetchone())
    store=configured_store()
    if attack=='ciphertext':
        store.client.put_object(Bucket=store.bucket,Key=store.key(chunk['object_key']),Body=b'x'*(chunk['size']+16))
        expected=409
    elif attack=='missing':
        store.client.delete_object(Bucket=store.bucket,Key=store.key(chunk['object_key']));expected=503
    elif attack=='manifest':
        with sqlite3.connect(path) as db:db.execute("UPDATE attachment_chunks SET object_key=?",('f'*64,))
        expected=409
    else:
        h=login(c,'owner');c.post(f'/api/cabinet/grants/{gid}/revoke',headers=h)
        path.write_bytes(snapshot);c.cookies.set(api.COOKIE,cookie);expected=409
    r=c.get(f'/api/cabinet/streams/{sid}',headers={'Range':'bytes=0-9'})
    assert r.status_code==expected,r.text
    assert png()[:10] not in r.content


def test_incomplete_expired_cancel_and_template_binding(setup):
    c,checkpoint,_,v,_=setup;h=login(c,'owner');mid=start(c,v,h,chunks.CHUNK_BYTES+5,'video/mp4','video')
    assert complete(c,mid,h).status_code==422
    assert put(c,mid,h,b'x'*5,index=2).status_code==422
    assert put(c,mid,h,b'x'*(chunks.CHUNK_BYTES+1)).status_code==413
    h=login(c,'other');assert c.get(f'/api/cabinet/uploads/{mid}').status_code==403
    assert put(c,mid,h,b'x'*chunks.CHUNK_BYTES).status_code==403
    h=login(c,'owner');assert c.post(f'/api/cabinet/uploads/{mid}/cancel',headers=h).status_code==200
    assert c.get(f'/api/cabinet/uploads/{mid}').status_code==403
    mid=start(c,v,h,len(png()));assert put(c,mid,h,png()).status_code==200
    admin=login(c,'admin');assert step(c,v,'retire',admin).status_code==200
    h=login(c,'owner');assert complete(c,mid,h).status_code==403


def test_active_stream_revocation_expiry_and_checkpoint_outage(setup):
    import time
    c,checkpoint,_,v,_=setup;aid,mid,gid,sid,h=fixture_asset(c,v);cookie=c.cookies.get(api.COOKIE)
    c.cookies.clear();owner=login(c,'owner');c.post(f'/api/cabinet/grants/{gid}/revoke',headers=owner)
    c.cookies.clear();c.cookies.set(api.COOKIE,cookie)
    assert c.get(f'/api/cabinet/streams/{sid}',headers={'Range':'bytes=2-5'}).status_code==403
    h=login(c,'owner');r=c.post(f'/api/cabinet/assets/{aid}/attachments/{mid}/stream',headers=h)
    url='/api/cabinet/streams/'+r.json()['stream_id']
    checkpoint.test_authority.clock=lambda:time.time()+301
    assert c.get(url).status_code==403
    checkpoint.shutdown();checkpoint.server_close()
    assert c.get(url).status_code==503


def test_video_multichunk_range_and_resume(setup):
    import shutil
    c,_,_,v,_=setup;ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg required')
    raw=subprocess.check_output([ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=size=1280x720:rate=30','-t','3',
        '-c:v','libx264','-preset','ultrafast','-crf','16','-pix_fmt','yuv420p','-movflags','frag_keyframe+empty_moov','-f','mp4','pipe:1'],timeout=30)
    assert len(raw)>2*chunks.CHUNK_BYTES
    h=login(c,'owner');mid=start(c,v,h,len(raw),'video/mp4','video')
    for index in reversed(range((len(raw)+chunks.CHUNK_BYTES-1)//chunks.CHUNK_BYTES)):
        assert put(c,mid,h,raw[index*chunks.CHUNK_BYTES:(index+1)*chunks.CHUNK_BYTES],index).status_code==200
    assert complete(c,mid,h).status_code==200
    photo=start(c,v,h,len(png()));put(c,photo,h,png());complete(c,photo,h)
    aid=c.post('/api/cabinet/ingest',headers=h,json=package(v,{'description':'synthetic video','photo':{'attachment_id':photo},'video':{'attachment_id':mid}})).json()['asset_id']
    gid=grant(c,aid,'alice','preview');h=login(c,'alice')
    sid=c.post(f'/api/cabinet/grants/{gid}/attachments/{mid}/stream',headers=h).json()['stream_id']
    start_byte=chunks.CHUNK_BYTES-10
    r=c.get('/api/cabinet/streams/'+sid,headers={'Range':f'bytes={start_byte}-{start_byte+30}'})
    assert r.status_code==206 and r.content==raw[start_byte:start_byte+31]
    r=c.get('/api/cabinet/streams/'+sid,headers={'Range':'bytes=0-'})
    assert len(r.content)==chunks.CHUNK_BYTES
    assert c.post(f'/api/cabinet/grants/{gid}/attachments/{mid}/preview',headers=h).status_code==403


def test_store_failure_does_not_commit_partial_manifest(setup,monkeypatch):
    from object_store import S3Store
    c,_,_,v,_=setup;h=login(c,'owner');raw=png();mid=start(c,v,h,len(raw))
    original=S3Store.put
    def lost_ack(self,key,data):
        original(self,key,data)
        raise media.MediaUnavailable('synthetic lost acknowledgement')
    monkeypatch.setattr(S3Store,'put',lost_ack)
    assert put(c,mid,h,raw).status_code==503
    assert c.get(f'/api/cabinet/uploads/{mid}').json()['chunks']==[]
    monkeypatch.setattr(S3Store,'put',original)
    assert put(c,mid,h,raw).status_code==200
    assert complete(c,mid,h).status_code==200
    store=configured_store()
    # Two ciphertext objects, exactly one committed reference. No plaintext release.
    with api.cabinet()._transaction() as db:
        assert db.execute('SELECT count(*) FROM attachment_chunks WHERE upload=?',(mid,)).fetchone()[0]==1


def test_stream_retirement_and_quota(setup):
    c,_,_,v,_=setup;aid,mid,gid,sid,h=fixture_asset(c,v);url=f'/api/cabinet/grants/{gid}/attachments/{mid}/stream'
    for _ in range(4):assert c.post(url,headers=h).status_code==200
    assert c.post(url,headers=h).status_code==403
    assert c.get('/api/cabinet/streams/'+sid,headers={'Range':'bytes=0-1'}).status_code==206
    cookie=c.cookies.get(api.COOKIE);c.cookies.clear()
    admin=login(c,'admin');assert step(c,v,'retire',admin).status_code==200
    c.cookies.clear();c.cookies.set(api.COOKIE,cookie)
    assert c.get('/api/cabinet/streams/'+sid,headers={'Range':'bytes=0-1'}).status_code==403


def test_upload_expiry_and_invalid_media(setup):
    import time
    c,checkpoint,_,v,_=setup;h=login(c,'owner');mid=start(c,v,h,len(b'<svg>bad media</svg>!'))
    assert put(c,mid,h,b'<svg>bad media</svg>!').status_code==200
    assert complete(c,mid,h).status_code==422
    with api.cabinet()._transaction() as db:
        assert not db.execute('SELECT 1 FROM cabinet_attachments WHERE id=?',(mid,)).fetchone()
    checkpoint.test_authority.clock=lambda:time.time()+86401
    h=login(c,'owner')
    assert c.get(f'/api/cabinet/uploads/{mid}').status_code==403
    assert c.post(f'/api/cabinet/uploads/{mid}/cancel',headers=h).status_code==200


def test_range_queues_short_lock_contention(setup):
    import fcntl
    c,_,_,v,_=setup;_,_,_,sid,_=fixture_asset(c,v)
    lock=open(api.store.DATA/'cabinet-demo'/'platform.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    timer=threading.Timer(0.15,lock.close);timer.start()
    try:
        r=c.get('/api/cabinet/streams/'+sid,headers={'Range':'bytes=0-9'})
        assert r.status_code==206 and r.content==png()[:10]
    finally:timer.join();lock.close()


@pytest.mark.parametrize('ca',[False,'','/no/such/ca.pem'])
def test_object_store_cannot_disable_tls(tmp_path,monkeypatch,ca):
    path=tmp_path/'objects.json'
    path.write_text(json.dumps({'endpoint':'https://objects.invalid','ca_file':ca}))
    monkeypatch.setenv('SENSE_CABINET_OBJECT_CONFIG',str(path))
    with pytest.raises(media.MediaUnavailable):configured_store()
