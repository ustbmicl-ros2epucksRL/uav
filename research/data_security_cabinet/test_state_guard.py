"""Real localhost HTTPS authority tests; software tests, not TEE/root proof."""
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from checkpoint_service import Authority, make_server
from prepare_trustee import certificate, pem
from security_cabinet import Cabinet, Denied, LocalDemoBroker, synthetic_data
from state_guard import StateGuard, RemoteCheckpoint, StateIntegrityError, CheckpointUnavailable


class TestProtectedState(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cabinet-state-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.now=1000
        self.token=secrets.token_hex(32)
        Authority.provision(self.root/'authority.db','cabinet',self.token)
        self.authority=Authority(self.root/'authority.db',clock=lambda:self.now)
        ca_key=ec.generate_private_key(ec.SECP256R1())
        ca=certificate(ca_key,'test root')
        key=ec.generate_private_key(ec.SECP256R1())
        cert=certificate(key,'127.0.0.1',ca_key,ca,'127.0.0.1')
        for name,content in [('ca.pem',ca.public_bytes(serialization.Encoding.PEM)),
                             ('tls.pem',cert.public_bytes(serialization.Encoding.PEM)),('tls.key',pem(key))]:
            (self.root/name).write_bytes(content)
        self.server=make_server(self.authority,'127.0.0.1',0,self.root/'tls.pem',self.root/'tls.key')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop_server)
        self.remote=RemoteCheckpoint('https://127.0.0.1:'+str(self.server.server_port),
                                     'cabinet',self.token,str(self.root/'ca.pem'))
        self.guard=StateGuard('cabinet',b's'*32,self.remote)
        self.broker=LocalDemoBroker(b'k'*32,allow_insecure_demo=True)
        self.cab=self.open(initialize=True)
        self.owner=self.cab.enroll('o'*32,'owner');self.user=self.cab.enroll('o'*32,'user')
        self.asset=self.cab.put(self.owner['token'],'synthetic',synthetic_data())
        self.grant=self.cab.grant(self.owner['token'],self.asset['asset_id'],self.user['id'],
                                  'stats',expires=2000,quota=1)

    def stop_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    def open(self,initialize=False,path=None):
        return Cabinet(path or self.root/'vault.db',audit_key=b'a'*32,operator_token='o'*32,
                       broker=self.broker,state_guard=self.guard,initialize_state=initialize)

    def mutate(self,sql,args=()):
        with sqlite3.connect(self.root/'vault.db') as db:db.execute(sql,args)

    def assert_blocked(self):
        with patch.object(self.broker,'unwrap',wraps=self.broker.unwrap) as decrypt:
            with self.assertRaises(StateIntegrityError):
                self.cab.run(self.user['token'],self.grant)
            decrypt.assert_not_called()

    def test_authorized_roundtrip_restart(self):
        job=self.cab.run(self.user['token'],self.grant)
        cab=self.open()
        self.assertEqual(cab.read_result(self.user['token'],job)['quality']['usable_rows'],12)
        self.assertFalse(cab.verify_audit()['external_checkpoint_required'])

    def test_grant_tampering(self):
        # Every permission-sensitive field must be included in the commitment.
        snapshot=(self.root/'vault.db').read_bytes()
        for sql in ["UPDATE grants SET quota=999", "UPDATE grants SET used=-1",
                    "UPDATE grants SET expires=999999", "UPDATE grants SET purpose='dataset'",
                    "UPDATE grants SET subject='attacker'", "DELETE FROM grants"]:
            with self.subTest(sql=sql):
                (self.root/'vault.db').write_bytes(snapshot)
                self.mutate(sql);self.assert_blocked()

    def test_identity_owner_and_schema_tampering(self):
        snapshot=(self.root/'vault.db').read_bytes()
        for sql in ["UPDATE identities SET token_hash='attacker' WHERE name='user'", "UPDATE assets SET owner='attacker'",
                    "CREATE TRIGGER evil AFTER UPDATE ON grants BEGIN UPDATE grants SET quota=999; END",
                    "DROP TABLE grants", "DELETE FROM audit"]:
            with self.subTest(sql=sql):
                (self.root/'vault.db').write_bytes(snapshot)
                self.mutate(sql);self.assert_blocked()

    def test_rollback_before_revocation(self):
        snapshot=(self.root/'vault.db').read_bytes()
        self.cab.revoke(self.owner['token'],self.grant)
        (self.root/'vault.db').write_bytes(snapshot)
        self.assert_blocked()
        with self.assertRaises(StateIntegrityError):self.open()

    def test_rollback_before_quota_consumption(self):
        snapshot=(self.root/'vault.db').read_bytes()
        self.cab.run(self.user['token'],self.grant)
        (self.root/'vault.db').write_bytes(snapshot)
        self.assert_blocked()

    def test_revoked_flag_cannot_be_reenabled(self):
        self.cab.revoke(self.owner['token'],self.grant)
        self.mutate('UPDATE grants SET revoked=0')
        self.assert_blocked()

    def test_result_read_checks_state(self):
        job=self.cab.run(self.user['token'],self.grant)
        self.mutate('UPDATE grants SET quota=999')
        with self.assertRaises(StateIntegrityError):self.cab.read_result(self.user['token'],job)
        with self.assertRaises(StateIntegrityError):self.cab.verify_audit()

    def test_independent_clock(self):
        self.now=2000
        with patch('time.time',return_value=1):
            with self.assertRaises(Denied):self.cab.run(self.user['token'],self.grant)

    def test_fork_only_one_can_advance(self):
        clone=self.root/'clone.db';shutil.copyfile(self.root/'vault.db',clone)
        second=self.open(path=clone)
        def go(cab):
            try: cab.run(self.user['token'],self.grant);return 1
            except StateIntegrityError:return 0
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(go,[self.cab,second])),1)

    def test_disk_mutation_during_authorization_is_not_resigned(self):
        original=self.cab._authorize
        def attack(db, actor, grant_id, **kwargs):
            self.mutate("UPDATE grants SET purpose='dataset',quota=999")
            return original(db,actor,grant_id,**kwargs)
        with patch.object(self.cab,'_authorize',side_effect=attack):
            job=self.cab.run(self.user['token'],self.grant)
        result=self.cab.read_result(self.user['token'],job)
        self.assertEqual(result['purpose'],'stats')
        self.assertNotIn('splits',result)
        with sqlite3.connect(self.root/'vault.db') as db:
            row=db.execute('SELECT purpose,quota FROM grants').fetchone()
            self.assertEqual(row,('stats',1))

    def test_checkpoint_cas_replay_rejected(self):
        head=self.remote.head()
        self.remote.advance(head['version'],head['digest'],head['digest'])
        with self.assertRaises(CheckpointUnavailable):
            self.remote.advance(head['version'],head['digest'],head['digest'])

    def test_wrong_credential_and_server_failure(self):
        wrong=RemoteCheckpoint(self.remote.url,'cabinet','x'*64,str(self.root/'ca.pem'))
        with self.assertRaises(CheckpointUnavailable):wrong.head()
        with patch.object(self.guard.checkpoint,'head',side_effect=CheckpointUnavailable('offline')):
            self.assert_blocked()

    def test_wrong_state_key(self):
        self.guard.key=b'w'*32
        self.assert_blocked()

    def test_no_rebootstrap_or_legacy_auto_migration(self):
        with self.assertRaises(StateIntegrityError):self.open(initialize=True)
        empty=self.root/'empty.db'
        with self.assertRaises(StateIntegrityError):self.open(initialize=True,path=empty)

    def test_lost_cas_response_locks_instead_of_rollback(self):
        original=self.remote.advance
        def lose(*args):
            original(*args)
            raise CheckpointUnavailable('response lost after remote commit')
        with patch.object(self.remote,'advance',side_effect=lose):
            with self.assertRaises(CheckpointUnavailable):self.cab.run(self.user['token'],self.grant)
        self.assert_blocked()
        with sqlite3.connect(self.root/'vault.db') as db:
            self.assertEqual(db.execute('SELECT used FROM grants').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)


if __name__=='__main__':unittest.main(verbosity=2)
