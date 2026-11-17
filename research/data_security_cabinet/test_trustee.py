"""Contract tests only: mocked client responses are not hardware evidence."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from security_cabinet import Cabinet, Denied, IntegrityError, ReleaseRequest, synthetic_data
from trustee_broker import AttestedResourceClient, TrusteeKeyBroker
from render_resource_policy import render


class TestTrustee(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root/'client'
        self.binary.write_bytes(b'contract-test-only-not-a-real-attester')
        self.ca = self.root/'ca.pem'
        self.ca.write_bytes(b'contract-test-only')
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.client = AttestedResourceClient(str(self.binary), self.sha,
                                             'https://kbs.test:8443', str(self.ca))
        self.key = b'k'*32
        self.broker = TrusteeKeyBroker(self.client, 'cabinet/keys/kek-v1', ['cabinet/keys/kek-v1'])
        self.request = ReleaseRequest('asset', 'subject', 'stats', 'job')

    def result(self, stdout=None, returncode=0):
        return subprocess.CompletedProcess([], returncode,
            stdout if stdout is not None else base64.b64encode(self.key)+b'\n')

    def test_command_contract_and_no_credential_import(self):
        with patch('trustee_broker.subprocess.run', return_value=self.result()) as run:
            self.assertEqual(self.client.get('cabinet/keys/kek-v1'), self.key)
            args, kwargs = run.call_args
            self.assertEqual(args[0][-3:], ['get-resource', '--path', 'cabinet/keys/kek-v1'])
            self.assertNotIn('--attestation-token', args[0])
            self.assertNotIn('--tee-key-file', args[0])
            self.assertNotIn('HTTPS_PROXY', kwargs['env'])
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)

    def test_no_http_or_unpinned_client(self):
        for url in ('http://kbs.test', 'https://u:p@kbs.test', 'https://kbs.test/?x=y'):
            with self.assertRaises(ValueError):
                AttestedResourceClient(str(self.binary), self.sha, url, str(self.ca))
        self.binary.write_bytes(b'changed')
        with self.assertRaises(Denied):
            self.client.get('cabinet/keys/kek-v1')

    def test_ca_tampering(self):
        self.ca.write_bytes(b'changed')
        with self.assertRaises(Denied):
            self.client.get('cabinet/keys/kek-v1')

    def test_failure_and_bad_key_response(self):
        for result in (self.result(returncode=1), self.result(b'not-base64'),
                       self.result(base64.b64encode(b'short')), self.result(b'x'*129)):
            with patch('trustee_broker.subprocess.run', return_value=result):
                with self.assertRaises(Denied):
                    self.client.get('cabinet/keys/kek-v1')
        with patch('trustee_broker.subprocess.run', side_effect=subprocess.TimeoutExpired('client', 30)):
            with self.assertRaises(Denied):
                self.client.get('cabinet/keys/kek-v1')

    def test_roundtrip_aad_and_no_cache(self):
        with patch('trustee_broker.subprocess.run', return_value=self.result()) as run:
            envelope = self.broker.wrap(b'd'*32, b'asset-aad')
            self.assertEqual(self.broker.unwrap(envelope, b'asset-aad', self.request), b'd'*32)
            self.assertEqual(run.call_count, 2)
            with self.assertRaises(InvalidTag):
                self.broker.unwrap(envelope, b'changed-aad', self.request)
        with patch('trustee_broker.subprocess.run', return_value=self.result(returncode=1)):
            with self.assertRaises(Denied):
                self.broker.unwrap(envelope, b'asset-aad', self.request)

    def test_resource_substitution_and_envelope_validation(self):
        with patch('trustee_broker.subprocess.run', return_value=self.result()):
            envelope = json.loads(self.broker.wrap(b'd'*32, b'aad'))
        envelope['header']['resource'] = 'other/keys/kek-v1'
        with patch('trustee_broker.subprocess.run') as run:
            with self.assertRaises(Denied):
                self.broker.unwrap(json.dumps(envelope).encode(), b'aad', self.request)
            run.assert_not_called()
        for value in (b'{}', b'legacy envelope', b'[]'):
            with self.assertRaises(IntegrityError):
                self.broker.unwrap(value, b'aad', self.request)
        with self.assertRaises(Denied):
            self.client.get('../keys/a')

    def test_cabinet_integration_and_remote_failure_rollback(self):
        cabinet = Cabinet(self.root/'vault.db', audit_key=b'a'*32,
                          operator_token='o'*32, broker=self.broker)
        owner = cabinet.enroll('o'*32, 'owner')
        user = cabinet.enroll('o'*32, 'user')
        with patch('trustee_broker.subprocess.run', return_value=self.result()):
            asset = cabinet.put(owner['token'], 'synthetic', synthetic_data())
            grant = cabinet.grant(owner['token'], asset['asset_id'], user['id'],
                                  'dataset', expires=9999999999, quota=2)
            job = cabinet.run(user['token'], grant)
            self.assertEqual(cabinet.read_result(user['token'], job)['quality']['usable_rows'], 12)
        with patch('trustee_broker.subprocess.run', return_value=self.result(returncode=1)):
            with self.assertRaises(Denied):
                cabinet.run(user['token'], grant)
        with cabinet._transaction() as db:
            self.assertEqual(db.execute('SELECT used FROM grants').fetchone()[0], 1)
        cabinet.verify_audit()

    def test_policy_explicit_scope_and_no_sample(self):
        text = render('snp', 'b'*64, 'a'*96, 'cabinet')
        self.assertIn('default allow = false', text)
        self.assertIn('cpu.ear_status == "affirming"', text)
        self.assertIn('policy_debug_allowed == false', text)
        self.assertIn('data["resource-path"][0] == "cabinet"', text)
        with self.assertRaises(ValueError):
            render('sample', 'x', 'a'*96, 'cabinet')
        with self.assertRaises(ValueError):
            render('snp', '', 'a'*96, 'cabinet')


if __name__ == '__main__':
    unittest.main(verbosity=2)
