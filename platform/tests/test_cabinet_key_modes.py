"""A resource client double exercises platform wiring, never hardware evidence."""
import json
import os
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import cabinet_demo as api
from app import cabinet_auth as auth
from checkpoint_service import Authority, make_server
from prepare_checkpoint import prepare
from security_cabinet import Denied
from test_cabinet_demo import PASSWORD, accounts, protected_client, login


def test_explicit_demo_and_failed_mode_selection(accounts, monkeypatch):
    client, _, _ = accounts
    monkeypatch.setenv('SENSE_CABINET_KEY_MODE', 'demo')
    login(client, 'owner')
    assert client.get('/api/cabinet/me').json()['key_mode'] == 'demo'
    monkeypatch.setenv('SENSE_CABINET_KEY_MODE', 'hardware')
    assert client.get('/api/cabinet/security-mode').json()['mode'] == 'hardware'
    assert client.get('/api/cabinet/me').status_code == 503
    monkeypatch.setenv('SENSE_CABINET_KEY_MODE', 'typo')
    assert client.get('/api/cabinet/me').status_code == 503
    monkeypatch.setenv('SENSE_CABINET_ATTESTATION_CONFIG', '/missing')
    monkeypatch.delenv('SENSE_CABINET_KEY_MODE')
    assert client.get('/api/cabinet/security-mode').status_code == 503
    monkeypatch.setenv('SENSE_CABINET_KEY_MODE', 'demo')
    assert client.get('/api/cabinet/me').status_code == 503


def test_remote_release_and_failure_after_login(tmp_path, monkeypatch):
    root = prepare(tmp_path/'independent', '127.0.0.1', 'test-tee')
    server = make_server(Authority(root/'authority.db'), '127.0.0.1', 0, root/'tls.pem', root/'tls.key')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'https://127.0.0.1:{server.server_port}'
        cfg = dict(namespace='test-tee', client='/synthetic/client', client_sha256='0'*64,
                   ca_file=str(root/'ca.pem'), url='https://synthetic.invalid',
                   checkpoint_url=url, checkpoint_ca_file=str(root/'ca.pem'))
        cfgfile = tmp_path/'hardware.json'
        cfgfile.write_text(json.dumps(cfg))
        monkeypatch.setenv('SENSE_CABINET_KEY_MODE', 'hardware')
        monkeypatch.setenv('SENSE_CABINET_ATTESTATION_CONFIG', str(cfgfile))
        monkeypatch.setenv('SENSE_CABINET_ALLOW_HTTP', '1')
        monkeypatch.setattr(api.store, 'DATA', tmp_path/'app')
        monkeypatch.setattr(api.persistence, 'LocalDemoBroker', lambda *a, **kw: pytest.fail('demo fallback'))
        resources = {label:os.urandom(32) for label in ('state','audit','kek','operator')}
        resources['checkpoint'] = (root/'token').read_bytes()
        denied = set()
        class TestClientDouble:
            def __init__(self, *args): pass
            def get(self, path):
                label = path.removeprefix('test-tee/keys/').removesuffix('-v1')
                if label in denied: raise Denied('synthetic refusal')
                return resources[label]
        monkeypatch.setattr(api.keys, 'AttestedResourceClient', TestClientDouble)
        cab = api.persistence.open_cabinet(api.store.DATA/'cabinet-demo', api.configured_guard())
        auth.install(cab)
        org = auth.add_org(cab,'synthetic')
        auth.add_user(cab, org, 'owner', PASSWORD, 'owner')
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            headers = login(client,'owner')
            result = client.get('/api/cabinet/me').json()
            assert result['key_mode']=='hardware' and result['remote_keys_released']
            assert result['hardware_verified'] is False
            assert client.post('/api/cabinet/ingest',headers=headers,json={}).status_code == 200
            for label in resources:
                denied.add(label)
                assert client.get('/api/cabinet/me').status_code == 503
                assert client.post('/api/cabinet/ingest',headers=headers,json={}).status_code == 503
                denied.clear()
            # Same checkpoint credentials still cannot reinterpret the protected mode marker.
            (tmp_path/'state').write_bytes(resources['state'])
            demo = dict(namespace='test-tee',url=url,ca_file=str(root/'ca.pem'),
                        state_key_file=str(tmp_path/'state'),token_file=str(root/'token'))
            cfgfile.write_text(json.dumps(demo))
            monkeypatch.delenv('SENSE_CABINET_ATTESTATION_CONFIG')
            monkeypatch.setenv('SENSE_CABINET_KEY_MODE','demo')
            monkeypatch.setenv('SENSE_CABINET_CHECKPOINT_CONFIG',str(cfgfile))
            from security_cabinet import LocalDemoBroker
            monkeypatch.setattr(api.persistence,'LocalDemoBroker',LocalDemoBroker)
            assert client.get('/api/cabinet/me').status_code == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
