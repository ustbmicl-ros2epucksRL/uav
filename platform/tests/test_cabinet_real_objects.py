"""Opt-in integration against actual SeaweedFS processes, never Moto.
CABINET_WEED_BINARY=/absolute/weed pytest tests/test_cabinet_real_objects.py -q
"""
import hashlib
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import pytest
from test_cabinet_demo import protected_client,accounts,login
from test_cabinet_templates import register,publish,package
from test_cabinet_attachments import png,grant,upload
from test_cabinet_chunks import start,put,complete
from app import cabinet_demo as api
import template_registry as registry
import media_attachments as media
import chunked_attachments as chunks
from object_store import S3Store
from object_operations import check,move_store,move_inline
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cabinet_object_service import prepare,native_up,native_stop


def free_ports():
    for _ in range(100):
        base=21000+secrets.randbelow(4000);sockets=[]
        try:
            for p in [base+i for i in range(5)]+[base+10000+i for i in range(5)]:
                s=socket.socket();sockets.append(s);s.bind(('127.0.0.1',p))
            return base
        except OSError:continue
        finally:
            for s in sockets:s.close()
    raise RuntimeError('No free test ports')


@pytest.fixture(scope='module')
def real_stores(tmp_path_factory):
    binary=os.environ.get('CABINET_WEED_BINARY')
    if not binary:pytest.skip('Set CABINET_WEED_BINARY to run real SeaweedFS integration')
    root=tmp_path_factory.mktemp('real-objects');configs=[]
    try:
        for name in ('source','target'):
            cfg=prepare(root/name,'127.0.0.1',free_ports(),binary);configs.append(cfg);native_up(cfg)
        yield configs
    finally:
        for cfg in configs:native_stop(cfg)


@pytest.fixture
def setup(accounts,real_stores,monkeypatch):
    c,checkpoint,ids=accounts
    # Active config is separate from immutable source/target configs, so migration
    # and tests cannot silently change another test's service definitions.
    active=api.store.DATA/'objects-active.json'
    active.write_text((Path(real_stores[0]['folder'])/'client/objects.json').read_text());active.chmod(0o600)
    monkeypatch.setenv('SENSE_CABINET_OBJECT_CONFIG',str(active))
    registry.install(api.cabinet());media.install(api.cabinet());chunks.install(api.cabinet())
    d=registry.examples()[2]['definition'];d['schema']['video']['misc']['max_bytes']=chunks.MAX_BYTES
    v,h=register(c,d);publish(c,v,h)
    return c,checkpoint,ids,v,real_stores


def test_actual_service_policy_and_tls(real_stores,tmp_path):
    path=Path(real_stores[0]['folder'])/'client/objects.json'
    result=check(path,probe=True)
    assert result['conditional_write']=='passed' and result['delete_denied'] and result['anonymous_denied']
    data=json.loads(path.read_text());data['ca_file']=str(Path(real_stores[1]['folder'])/'client/ca.pem')
    wrong=tmp_path/'wrong-ca.json';wrong.write_text(json.dumps(data))
    with pytest.raises(media.MediaUnavailable):S3Store(wrong).put('f'*64,b'wrong trust')
    from botocore.exceptions import ClientError
    app=S3Store(path)
    with pytest.raises(ClientError) as exc:app.client.put_object(Bucket=app.bucket,Key='outside-prefix/probe',Body=b'no')
    assert exc.value.response['ResponseMetadata']['HTTPStatusCode']==403


def synthetic_large_video():
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg required to generate synthetic large video')
    raw=subprocess.check_output([ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=size=1280x720:rate=30','-t','9',
        '-c:v','libx264','-preset','ultrafast','-crf','16','-pix_fmt','yuv420p','-movflags','frag_keyframe+empty_moov','-f','mp4','pipe:1'],timeout=40)
    assert len(raw)>8*1024**2
    return raw


def test_real_large_resume_restart_network_revoke_rollback(setup):
    c,_,_,v,stores=setup;h=login(c,'owner');raw=synthetic_large_video()
    mid=start(c,v,h,len(raw),'video/mp4','video')
    assert put(c,mid,h,raw[:chunks.CHUNK_BYTES]).status_code==200
    # Independent platform process resumes the same login, DB and manifest.
    script='''
import json,sys,base64
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import cabinet_demo as api
v=json.load(sys.stdin);app=FastAPI();app.include_router(api.router)
with TestClient(app) as c:
 c.cookies.set(api.COOKIE,v['cookie']);r=c.get('/api/cabinet/uploads/'+v['mid'])
 assert r.status_code==200,r.text
 assert [x['idx'] for x in r.json()['chunks']]==[0]
 me=c.get('/api/cabinet/me').json()
 r=c.post('/api/cabinet/uploads/'+v['mid']+'/chunks/1',content=base64.b64decode(v['part']),headers={'X-Cabinet-Request':'1','X-CSRF-Token':me['csrf']})
 assert r.status_code==200,r.text
'''
    proc=subprocess.run([sys.executable,'-c',script],input=json.dumps({'cookie':c.cookies.get(api.COOKIE),'mid':mid,'part':base64.b64encode(raw[chunks.CHUNK_BYTES:2*chunks.CHUNK_BYTES]).decode()}),
        text=True,capture_output=True,env=dict(os.environ,SENSE_DATA_DIR=str(api.store.DATA)),timeout=30)
    assert proc.returncode==0,proc.stderr
    native_stop(stores[0])
    # Real connection refusal, not a mocked SDK exception.
    assert put(c,mid,h,raw[2*chunks.CHUNK_BYTES:3*chunks.CHUNK_BYTES],2).status_code==503
    assert len(c.get(f'/api/cabinet/uploads/{mid}').json()['chunks'])==2
    native_up(stores[0])
    assert put(c,mid,h,raw[:chunks.CHUNK_BYTES]).json()['reused']
    for i in range(2,(len(raw)+chunks.CHUNK_BYTES-1)//chunks.CHUNK_BYTES):
        r=put(c,mid,h,raw[i*chunks.CHUNK_BYTES:(i+1)*chunks.CHUNK_BYTES],i);assert r.status_code==200,r.text
    assert complete(c,mid,h).status_code==200
    photo=upload(c,v).json()['attachment_id'];h=login(c,'owner')
    aid=c.post('/api/cabinet/ingest',headers=h,json=package(v,{'description':'synthetic real S3','photo':{'attachment_id':photo},'video':{'attachment_id':mid}})).json()['asset_id']
    gid=grant(c,aid,'alice','preview');h=login(c,'alice')
    sid=c.post(f'/api/cabinet/grants/{gid}/attachments/{mid}/stream',headers=h).json()['stream_id'];url='/api/cabinet/streams/'+sid
    cookie=c.cookies.get(api.COOKIE)
    native_stop(stores[0]);assert c.get(url,headers={'Range':'bytes=1048570-1048600'}).status_code==503
    native_up(stores[0]);r=c.get(url,headers={'Range':'bytes=1048570-1048600'});assert r.status_code==206 and r.content==raw[1048570:1048601]
    dbpath=api.store.DATA/'cabinet-demo/persistent.db';snapshot=dbpath.read_bytes()
    c.cookies.clear();owner=login(c,'owner');assert c.post(f'/api/cabinet/grants/{gid}/revoke',headers=owner).status_code==200
    c.cookies.clear();c.cookies.set(api.COOKIE,cookie);assert c.get(url).status_code==403
    dbpath.write_bytes(snapshot);assert c.get(url).status_code==409


def test_inline_and_object_migration_preserve_grants(setup):
    c,_,_,v,stores=setup
    mid=upload(c,v).json()['attachment_id'];h=login(c,'owner')
    aid=c.post('/api/cabinet/ingest',headers=h,json=package(v,{'description':'synthetic migrate','photo':{'attachment_id':mid}})).json()['asset_id']
    gid=grant(c,aid,'alice','preview')
    assert move_inline(api.cabinet())['inline_attachments']==1
    assert move_inline(api.cabinet(),True)['migrated']==1
    assert move_inline(api.cabinet(),True)['migrated']==0
    target=Path(stores[1]['folder'])/'client/objects.json'
    assert move_store(api.cabinet(),target)['chunks']==1
    assert move_store(api.cabinet(),target,True)['switched']
    native_stop(stores[0])
    try:
        h=login(c,'alice');sid=c.post(f'/api/cabinet/grants/{gid}/attachments/{mid}/stream',headers=h).json()['stream_id']
        assert c.get('/api/cabinet/streams/'+sid,headers={'Range':'bytes=0-'}).content==png()
        with api.cabinet()._transaction() as db:
            assert db.execute('SELECT asset FROM grants WHERE id=?',(gid,)).fetchone()[0]==aid
            assert db.execute('SELECT ciphertext FROM cabinet_attachments WHERE id=?',(mid,)).fetchone()[0]==b''
    finally:native_up(stores[0])


def test_failed_copy_keeps_source_and_resumes(setup):
    c,_,_,v,stores=setup;h=login(c,'owner');mid=start(c,v,h,len(png()));put(c,mid,h,png());complete(c,mid,h)
    active=Path(os.environ['SENSE_CABINET_OBJECT_CONFIG']);before=active.read_bytes()
    target=Path(stores[1]['folder'])/'client/objects.json';native_stop(stores[1])
    try:
        with pytest.raises(media.MediaUnavailable):move_store(api.cabinet(),target,True)
        assert active.read_bytes()==before
    finally:native_up(stores[1])
    assert move_store(api.cabinet(),target,True)['switched']
    assert move_store(api.cabinet(),Path(stores[0]['folder'])/'client/objects.json',True)['switched']



def test_target_conflict_cannot_switch_configuration(setup):
    c,_,_,v,stores=setup;h=login(c,'owner');mid=start(c,v,h,len(png()));put(c,mid,h,png());complete(c,mid,h)
    with api.cabinet()._transaction() as db:row=dict(db.execute('SELECT * FROM attachment_chunks WHERE upload=?',(mid,)).fetchone())
    target_path=Path(stores[1]['folder'])/'client/objects.json';target=S3Store(target_path)
    target.put(row['object_key'],b'x'*(row['size']+16))
    active=Path(os.environ['SENSE_CABINET_OBJECT_CONFIG']);before=active.read_bytes()
    from security_cabinet import IntegrityError
    with pytest.raises(IntegrityError):move_store(api.cabinet(),target_path,True)
    assert active.read_bytes()==before
    assert target.get(row['object_key'],row['size']+16)==b'x'*(row['size']+16)


def test_rollback_cannot_start_migration(setup):
    c,_,_,v,stores=setup;h=login(c,'owner');mid=start(c,v,h,len(png()));put(c,mid,h,png());complete(c,mid,h)
    path=api.store.DATA/'cabinet-demo/persistent.db';snapshot=path.read_bytes()
    assert c.post('/api/cabinet/uploads/'+mid+'/cancel',headers=h).status_code==200
    path.write_bytes(snapshot)
    from state_guard import StateIntegrityError
    with pytest.raises(StateIntegrityError):move_store(api.cabinet(),Path(stores[1]['folder'])/'client/objects.json',True)


def test_cli_moves_unfinished_upload_and_inline_attachment(setup):
    c,_,_,v,stores=setup;h=login(c,'owner');mid=start(c,v,h,len(png()));assert put(c,mid,h,png()).status_code==200
    target=Path(stores[1]['folder'])/'client/objects.json'
    def cli(*args):
        p=subprocess.run([sys.executable,'-m','app.cabinet_admin',*args],capture_output=True,text=True,
            env=dict(os.environ,SENSE_DATA_DIR=str(api.store.DATA)),timeout=40)
        assert p.returncode==0,p.stderr
        return json.loads(p.stdout)
    assert cli('migrate-object-store','--target',str(target),'--apply')['switched']
    state=c.get('/api/cabinet/uploads/'+mid).json()
    assert state['state']=='uploading' and len(state['chunks'])==1
    assert complete(c,mid,h).status_code==200
    legacy=upload(c,v).json()['attachment_id']
    assert cli('migrate-inline-attachments')['inline_attachments']==1
    assert cli('migrate-inline-attachments','--apply')['migrated']==1
    with api.cabinet()._transaction() as db:
        assert db.execute('SELECT id FROM attachment_uploads WHERE id=?',(legacy,)).fetchone()[0]==legacy
