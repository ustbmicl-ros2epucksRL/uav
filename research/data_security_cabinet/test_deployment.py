"""Offline deployment artifact tests; requires a clean pinned upstream checkout.
CABINET_TRUSTEE_SOURCE=/path/to/trustee python test_deployment.py
Does not start services and does not test hardware attestation.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import tomllib
import unittest

from cryptography import x509
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from prepare_trustee import prepare
from renew_admin_token import renew


@unittest.skipUnless(os.environ.get('CABINET_TRUSTEE_SOURCE'), 'Pinned Trustee checkout required')
class TestDeployment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='cabinet-deploy-test-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)/'deployment'
        prepare(os.environ['CABINET_TRUSTEE_SOURCE'], cls.root, 'kbs.test')

    def test_secret_permissions_and_disjoint_roots(self):
        for name in ('kek-v1', 'audit-v1', 'operator-v1', 'state-v1'):
            p = self.root/'admin'/name
            self.assertEqual(len(p.read_bytes()), 32)
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertNotEqual((self.root/'admin/ca.pem').read_bytes(),
                            (self.root/'kbs/as-ca.pem').read_bytes())
        cert = x509.load_pem_x509_certificate((self.root/'kbs/tls.pem').read_bytes())
        self.assertEqual(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                         .value.get_values_for_type(x509.DNSName), ['kbs.test'])

    def test_short_lived_admin_token_signature(self):
        renew(self.root)
        token = (self.root/'admin/admin-token').read_bytes().strip().split(b'.')
        decode = lambda b: base64.urlsafe_b64decode(b+b'='*(-len(b)%4))
        pub = load_pem_public_key((self.root/'kbs/admin.pub').read_bytes())
        pub.verify(decode(token[2]), b'.'.join(token[:2]))
        claims = json.loads(decode(token[1]))
        self.assertEqual(claims['exp']-claims['iat'], 600)
        self.assertGreater(claims['exp'], time.time())
        self.assertEqual(claims['aud'], ['KBS'])

    def test_default_deny_tls_and_network_isolation(self):
        conf = tomllib.loads((self.root/'kbs/config.toml').read_text())
        self.assertFalse(conf['http_server']['insecure_http'])
        self.assertEqual(conf['attestation_token']['trusted_certs_paths'], ['/run/cabinet/as-ca.pem'])
        policy = (self.root/'source/kbs/sample_policies/default.rego').read_text()
        self.assertEqual(policy.strip(), 'package policy\ndefault allow = false')
        services = json.loads((self.root/'compose.json').read_text())['services']
        self.assertNotIn('ports', services['as'])
        self.assertNotIn('ports', services['rvps'])
        self.assertEqual(services['kbs']['ports'], ['127.0.0.1:8443:8443'])
        for service in services.values():
            self.assertNotIn('/admin', str(service['volumes']))
        if shutil.which('docker'):
            subprocess.run(['docker','compose','-f',str(self.root/'compose.json'),
                            'config','--quiet'], check=True)

    def test_refuse_existing_destination(self):
        with self.assertRaises(FileExistsError):
            prepare(os.environ['CABINET_TRUSTEE_SOURCE'], self.root, 'kbs.test')


if __name__ == '__main__':
    unittest.main(verbosity=2)
