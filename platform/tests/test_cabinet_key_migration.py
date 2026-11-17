"""Synthetic CPU-only migration rehearsal with a real TLS checkpoint authority."""
import json
import os
import sqlite3
import sys
from contextlib import closing

import pytest
from app import cabinet_admin, cabinet_demo as api
from app import cabinet_key_migration as migration
from test_cabinet_demo import accounts, protected_client, flow, login
from test_cabinet_templates import register, publish
from test_cabinet_attachments import upload, png, ingest, grant
from test_cabinet_chunks import start, put, complete
import template_registry
import media_attachments
import chunked_attachments
from security_cabinet import Denied
from state_guard import StateIntegrityError


def configure_synthetic_target(monkeypatch):
    cfg = json.loads((api.store.DATA/'config.json').read_text())
    secrets = {label:os.urandom(32) for label in ('audit','operator','kek','state')}
    secrets['checkpoint'] = os.urandom(32)
    class Resources:
        def __init__(self,*args): pass
        def get(self,path):
            return secrets[path.rsplit('/',1)[1].removesuffix('-v1')]
    monkeypatch.setattr(api.keys,'AttestedResourceClient',Resources)
    hw = api.store.DATA/'hw.json'
    hw.write_text(json.dumps(dict(namespace=cfg['namespace'],url='https://synthetic.invalid',
                                  client='/synthetic',client_sha256='0'*64,ca_file=cfg['ca_file'],
                                  checkpoint_url=cfg['url'],checkpoint_ca_file=cfg['ca_file'])))
    monkeypatch.setenv('SENSE_CABINET_KEY_MODE','hardware')
    monkeypatch.setenv('SENSE_CABINET_ATTESTATION_CONFIG',str(hw))
    monkeypatch.setattr(sys,'argv',['admin','migrate-demo-keys','--source-checkpoint',str(api.store.DATA/'config.json')])
    source = api.demo_guard(api.store.DATA/'config.json')
    return source


@pytest.mark.parametrize('interruption', ['before-cas', 'after-cas', 'lost-reply'])
def test_migration_recovery_and_preserved_access(accounts, monkeypatch, interruption):
    client, _, _ = accounts
    asset, grants = flow(client)
    headers = login(client, 'alice')
    job = client.post(f"/api/cabinet/grants/{grants['alice']}/run", headers=headers).json()
    headers = login(client, 'owner')
    assert client.post(f"/api/cabinet/grants/{grants['bobby']}/revoke", headers=headers).is_success
    # An inline photo and a chunked copy use distinct envelope tables.
    objects = {}
    class SyntheticStore:
        def put(self, key, data): objects[key] = data
        def get(self, key, size): return objects[key]
    monkeypatch.setattr(chunked_attachments, 'configured_store', SyntheticStore)
    for module in (template_registry, media_attachments, chunked_attachments):
        module.install(api.cabinet())
    version, headers = register(client, template_registry.examples()[2]['definition'])
    publish(client, version, headers)
    inline = upload(client, version).json()['attachment_id']
    headers = login(client, 'owner')
    chunked = start(client, version, headers, len(png()))
    assert put(client, chunked, headers, png()).is_success
    assert complete(client, chunked, headers).is_success
    frozen_objects = dict(objects)
    previews = {}
    for mid in (inline, chunked):
        aid = ingest(client, version, mid).json()['asset_id']
        previews[mid] = grant(client, aid, 'alice', 'preview')
    folder = api.store.DATA/'cabinet-demo'
    active = folder/'persistent.db'
    snapshot = active.read_bytes()
    with closing(sqlite3.connect(active)) as db:
        expected = db.execute('SELECT * FROM grants ORDER BY id').fetchall()
        original = db.execute('SELECT ciphertext,envelope FROM assets WHERE id=?',(asset,)).fetchone()
    source = configure_synthetic_target(monkeypatch)
    original_head = source.checkpoint.head()
    with monkeypatch.context() as fault:
        if interruption=='after-cas':
            fault.setattr(migration.os,'replace',lambda *_: (_ for _ in ()).throw(OSError('interrupted install')))
            error = OSError
        elif interruption == 'lost-reply':
            checkpoint_type = type(source.checkpoint)
            rotate = checkpoint_type.rotate
            def lose_reply(remote, *args):
                rotate(remote, *args)
                raise OSError('client lost the successful CAS reply')
            fault.setattr(checkpoint_type, 'rotate', lose_reply)
            error = OSError
        else:
            wrap = api.keys.TrusteeKeyBroker.wrap
            attempts = []
            def fail_second(broker, key, aad):
                if attempts:
                    raise Denied('synthetic KBS outage')
                attempts.append(True)
                return wrap(broker, key, aad)
            fault.setattr(api.keys.TrusteeKeyBroker, 'wrap', fail_second)
            error = Denied
        with pytest.raises(error):
            cabinet_admin.main()
    assert active.read_bytes()==snapshot
    if interruption == 'before-cas':
        assert original_head['digest'] == source.checkpoint.head()['digest']
    else:
        assert original_head['digest'] != api.keys.hardware_guard().checkpoint.head()['digest']
    cabinet_admin.main()
    cabinet_admin.main()
    from state_guard import CheckpointUnavailable
    with pytest.raises(CheckpointUnavailable):
        source.checkpoint.head()
    assert client.get('/api/cabinet/me').status_code==401
    with closing(sqlite3.connect(active)) as db:
        assert db.execute('SELECT * FROM grants ORDER BY id').fetchall()==expected
        cipher, envelope = db.execute('SELECT ciphertext,envelope FROM assets WHERE id=?',(asset,)).fetchone()
        assert cipher==original[0] and envelope!=original[1]
    assert api.cabinet().verify_audit()['events']>0
    # Original revoked grant still denies execution.
    headers=login(client,'bobby')
    assert client.post(f"/api/cabinet/grants/{grants['bobby']}/run",headers=headers).status_code==403
    headers=login(client,'alice')
    assert objects == frozen_objects
    url = f'/api/cabinet/grants/{previews[inline]}/attachments/{inline}/preview'
    assert client.post(url, headers=headers).content == png()
    url = f'/api/cabinet/grants/{previews[chunked]}/attachments/{chunked}/stream'
    stream = client.post(url, headers=headers).json()['stream_id']
    assert client.get('/api/cabinet/streams/'+stream, headers={'Range':'bytes=0-'}).content == png()
    assert client.get('/api/cabinet/results/'+job['job_id']).json()['result']==job['result']
    for _ in range(4):
        assert client.post(f"/api/cabinet/grants/{grants['alice']}/run",headers=headers).is_success
    assert client.post(f"/api/cabinet/grants/{grants['alice']}/run",headers=headers).status_code==403
    active.write_bytes(snapshot)
    assert client.get('/api/cabinet/me').status_code==409
    with pytest.raises(StateIntegrityError):
        cabinet_admin.main()
