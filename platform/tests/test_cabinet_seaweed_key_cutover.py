"""One synthetic video from real S3 upload through checkpoint key cutover."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from fastapi.testclient import TestClient
import hashlib
import os
import json
from contextlib import ExitStack
from cabinet_trustee_sample import SampleTrustee
import sqlite3
from contextlib import closing

import pytest
from app import cabinet_admin, cabinet_auth, cabinet_demo as api
from app import cabinet_key_migration as migration
from object_store import configured_store
from state_guard import CheckpointUnavailable, StateIntegrityError
from test_cabinet_demo import accounts, protected_client, login, PASSWORD
from test_cabinet_real_objects import real_stores, setup, synthetic_large_video, native_up, native_stop
from test_cabinet_chunks import start, put, complete
from test_cabinet_templates import package
from test_cabinet_attachments import upload, grant
from chunked_attachments import CHUNK_BYTES
from cabinet_trustee_guest import GuestTrustee


def record(stage, detail):
    if os.environ.get('CABINET_QA_EVENTS'):
        with open(os.environ['CABINET_QA_EVENTS'], 'a') as stream:
            stream.write(json.dumps([stage, detail]) + '\n')


def manifest(path, mid):
    store = configured_store()
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute('SELECT idx,object_key,size,envelope FROM attachment_chunks WHERE upload=? ORDER BY idx', (mid,))
        return [(index, key, hashlib.sha256(store.get(key, size + 16)).digest(), envelope)
                for index, key, size, envelope in rows]


def grants(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute('SELECT * FROM grants ORDER BY id').fetchall()


@pytest.fixture
def trustee_context():
    with ExitStack() as stack:
        yield stack


@pytest.mark.skipif(os.environ.get('CABINET_QA_KBS') not in ('trustee','snp','tdx'), reason='run scripts/accept_seaweed_key_migration.sh with real Trustee')
def test_video_key_cutover(setup, monkeypatch, trustee_context):
    client, _, _, version, stores = setup
    video = synthetic_large_video()
    owner = login(client, 'owner')
    mid = start(client, version, owner, len(video), 'video/mp4', 'video')
    assert put(client, mid, owner, video[:CHUNK_BYTES]).is_success
    native_stop(stores[0])
    try:
        assert put(client, mid, owner, video[CHUNK_BYTES:2*CHUNK_BYTES], 1).status_code == 503
        accepted = client.get(f'/api/cabinet/uploads/{mid}').json()['chunks']
        assert [item['idx'] for item in accepted] == [0]
    finally:
        native_up(stores[0])
    for offset in range(CHUNK_BYTES, len(video), CHUNK_BYTES):
        assert put(client, mid, owner, video[offset:offset+CHUNK_BYTES], offset//CHUNK_BYTES).is_success
    assert complete(client, mid, owner).is_success
    photo = upload(client, version).json()['attachment_id']
    owner = login(client, 'owner')
    asset = client.post('/api/cabinet/ingest', headers=owner, json=package(version,{
        'description':'synthetic video key migration','photo':{'attachment_id':photo},
        'video':{'attachment_id':mid}})).json()['asset_id']
    with api.cabinet()._transaction() as db:
        org = db.execute("SELECT org FROM auth_users WHERE username='alice'").fetchone()[0]
    cabinet_auth.add_user(api.cabinet(), org, 'reader2', PASSWORD, 'dataset')
    active = grant(client, asset, 'alice', 'preview')
    revoked = grant(client, asset, 'reader2', 'preview')
    def stream(gid, headers):
        return client.post(f'/api/cabinet/grants/{gid}/attachments/{mid}/stream', headers=headers)
    old_lease = stream(revoked, login(client,'reader2')).json()['stream_id']
    owner = login(client, 'owner')
    assert client.post(f'/api/cabinet/grants/{revoked}/revoke',headers=owner).is_success
    alice = login(client, 'alice')
    source_lease = stream(active, alice).json()['stream_id']
    assert client.get(f'/api/cabinet/streams/{source_lease}', headers={'Range':'bytes=0-31'}).content == video[:32]
    path = api.store.DATA/'cabinet-demo/persistent.db'
    old_db = path.read_bytes()
    before = manifest(path, mid)
    original_grants = grants(path)
    namespace = json.loads((api.store.DATA/'config.json').read_text())['namespace']
    guest = os.environ['CABINET_QA_KBS'] in ('snp','tdx')
    kbs_type = GuestTrustee if guest else SampleTrustee
    kbs = kbs_type(api.store.DATA/'trustee', namespace, trustee_context)
    source = kbs.target(monkeypatch)
    kbs.policy('require-snp')
    with pytest.raises(api.keys.KeyConfigurationError):
        cabinet_admin.main()
    assert path.read_bytes() == old_db
    kbs.policy('sample')
    # Lose the acknowledgement after the authority has actually committed both new keys.
    with monkeypatch.context() as fault:
        rotate = type(source.checkpoint).rotate
        def lost_reply(remote, *args):
            rotate(remote, *args)
            raise ConnectionError('synthetic lost checkpoint reply')
        fault.setattr(type(source.checkpoint), 'rotate', lost_reply)
        with pytest.raises(ConnectionError):
            cabinet_admin.main()
    assert old_db == path.read_bytes()
    with pytest.raises(CheckpointUnavailable):
        source.checkpoint.head()
    cabinet_admin.main()
    cabinet_admin.main()
    assert client.get('/api/cabinet/me').status_code == 401
    after = manifest(path, mid)
    assert [row[:3] for row in after] == [row[:3] for row in before]
    assert len(after) == (len(video)+CHUNK_BYTES-1)//CHUNK_BYTES
    assert all(a[3] != b[3] for a,b in zip(after,before))
    assert grants(path) == original_grants
    denied = login(client, 'reader2')
    assert stream(revoked, denied).status_code == 403
    assert client.get(f'/api/cabinet/streams/{old_lease}').status_code == 403
    alice = login(client,'alice')
    assert client.get(f'/api/cabinet/streams/{source_lease}').status_code == 403
    lease = stream(active,alice).json()['stream_id']
    previews = []
    def observe(response, decision):
        preview_id = response.headers['X-Cabinet-Preview-ID']
        if guest:
            event = kbs.control('observe-'+decision, preview_id)
            previews.append(dict(preview_id=preview_id,event=event,status=response.status_code,
                                 content_range=response.headers.get('Content-Range')))
            return event
        path = Path(os.environ['CABINET_QA_AUDIT'])/(preview_id+'.jsonl')
        calls = [json.loads(line) for line in path.read_text().splitlines()]
        assert all(call['preview_id'] == preview_id for call in calls)
        event = calls[-1]['event']
        assert event['decision'] == decision
        if decision == 'approved':
            assert event['resource'] == 'kek-v1'
        previews.append(dict(preview_id=preview_id, calls=calls, status=response.status_code,
                             content_range=response.headers.get('Content-Range')))
        return event

    with TestClient(client.app) as owner_client:
        owner_headers = login(owner_client, 'owner')
        owner_stream = owner_client.post(f'/api/cabinet/assets/{asset}/attachments/{mid}/stream',
                                         headers=owner_headers).json()['stream_id']
        gate = Barrier(2)
        def fetch(args):
            caller, stream_id, offset = args
            gate.wait(timeout=10)
            response = caller.get(f'/api/cabinet/streams/{stream_id}',headers={'Range':f'bytes={offset}-{offset+31}'})
            assert response.status_code == 206 and response.content == video[offset:offset+32]
            return response
        with ThreadPoolExecutor(max_workers=2) as pool:
            pair = list(pool.map(fetch, [(client,lease,0),(owner_client,owner_stream,CHUNK_BYTES)]))
        assert pair[0].headers['X-Cabinet-Preview-ID'] != pair[1].headers['X-Cabinet-Preview-ID']
        events = [observe(response,'approved') for response in pair]
        assert events[0]['event_id'] != events[1]['event_id']
        if not guest:
            left, right = [row['calls'] for row in previews[-2:]]
            assert any(max(a['event']['begin_ns'],b['event']['begin_ns']) <
                       min(a['event']['end_ns'],b['event']['end_ns']) for a in left for b in right)
        record('approved_release',events[0])
        record('concurrent_previews',[dict(preview_id=r.headers['X-Cabinet-Preview-ID'],event_id=e['event_id'],
                                           content_range=r.headers['Content-Range']) for r,e in zip(pair,events)])
    for fault in ('deny', 'offline'):
        if fault == 'deny':
            kbs.policy('require-snp')
        else:
            kbs.offline()
        assert client.get('/api/cabinet/me').status_code == 503
        failed = client.get(f'/api/cabinet/streams/{lease}',headers={'Range':'bytes=0-31'})
        assert failed.status_code == 503
        if fault == 'offline':
            if not guest:
                observe(failed,'unavailable')
            record('offline_denied', 'identity and existing video range returned 503')
            kbs.online()
        else:
            record('measurement_denied', observe(failed,'denied'))
        kbs.policy('sample')
        resumed = client.get(f'/api/cabinet/streams/{lease}', headers={'Range':'bytes=0-31'})
        assert resumed.content == video[:32]
        observe(resumed,'approved')
    print('Trustee sample policy rejection and disconnected KBS: protected reads refused')
    recovered = hashlib.sha256()
    for offset in range(0,len(video),CHUNK_BYTES):
        response = client.get(f'/api/cabinet/streams/{lease}',headers={'Range':f'bytes={offset}-'})
        assert response.status_code == 206
        observe(response,'approved')
        recovered.update(response.content)
    assert recovered.digest() == hashlib.sha256(video).digest()
    record('preview_requests',previews)
    record('video_integrity', dict(bytes=len(video), chunks=len(after), sha256=recovered.hexdigest()))
    for _ in range(3):
        assert stream(active,alice).is_success
    assert stream(active,alice).status_code == 403
    assert api.cabinet().verify_audit()['events'] > 0
    path.write_bytes(old_db)
    assert client.get('/api/cabinet/me').status_code == 409
    with pytest.raises(StateIntegrityError):
        cabinet_admin.main()
    record('rollback_denied', 'old snapshot returned 409')
    print(f'SeaweedFS video bytes={len(video)} chunks={len(after)} sha256={recovered.hexdigest()} remaining_after_cutover=4')
