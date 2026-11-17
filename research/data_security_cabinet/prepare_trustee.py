#!/usr/bin/env python3
"""Prepare isolated TLS Trustee deployment. Does not start Docker or release keys.
Run ONLY on the independent trusted service host; output contains secrets.
Pinned source is copied, with default KBS resource policy changed to deny-all.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from trustee_broker import TRUSTEE_COMMIT


def write(path, content):
    data = content.encode() if isinstance(content, str) else content
    with open(path, 'xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data)


def pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def certificate(key, name, issuer_key=None, issuer_cert=None, host=None):
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    builder = (x509.CertificateBuilder().subject_name(subject)
               .issuer_name(issuer_cert.subject if issuer_cert else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - timedelta(minutes=5))
               .not_valid_after(now + timedelta(days=30 if issuer_cert else 365))
               .add_extension(x509.BasicConstraints(ca=issuer_cert is None, path_length=None), critical=True))
    builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
    builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
        (issuer_key or key).public_key()), False)
    builder = builder.add_extension(x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=False,
        data_encipherment=False, key_agreement=False, key_cert_sign=issuer_cert is None,
        crl_sign=issuer_cert is None, encipher_only=False, decipher_only=False), True)
    if host:
        try:
            san = x509.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            san = x509.DNSName(host)
        builder = builder.add_extension(x509.SubjectAlternativeName([san]), False)
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
    return builder.sign(issuer_key or key, hashes.SHA256())


def prepare(source, output, host):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not host or any(x in host for x in '/:@ \n'):
        raise ValueError('Supply one DNS name or IPv4 address, without port')
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip()
    if actual != TRUSTEE_COMMIT or dirty:
        raise ValueError('A clean checkout of the pinned Trustee commit is required')
    if output == source or source in output.parents:
        raise ValueError('Output must be outside source checkout')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for folder in ('admin', 'kbs', 'as', 'rvps', 'data'):
        (output/folder).mkdir(mode=0o700)
    shutil.copytree(source, output/'source', ignore=shutil.ignore_patterns('.git', 'target'))
    # Fail closed even before the first admin request or on empty state recovery.
    (output/'source/kbs/sample_policies/default.rego').write_text('package policy\ndefault allow = false\n')
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = certificate(ca_key, 'Cabinet proof-of-concept CA')
    tls_key, as_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    tls = certificate(tls_key, host, ca_key, ca, host)
    as_ca_key = ec.generate_private_key(ec.SECP256R1())
    as_ca = certificate(as_ca_key, 'Cabinet attestation-only CA')
    as_cert = certificate(as_key, 'Cabinet AS signer', as_ca_key, as_ca)
    cert_pem = lambda c: c.public_bytes(serialization.Encoding.PEM)
    write(output/'admin/ca.key', pem(ca_key))
    write(output/'admin/ca.pem', cert_pem(ca))
    write(output/'kbs/as-ca.pem', cert_pem(as_ca))
    write(output/'admin/as-ca.key', pem(as_ca_key))
    write(output/'kbs/tls.key', pem(tls_key))
    write(output/'kbs/tls.pem', cert_pem(tls) + cert_pem(ca))
    write(output/'as/token.key', pem(as_key))
    write(output/'as/token.pem', cert_pem(as_cert) + cert_pem(as_ca))
    admin_key = ed25519.Ed25519PrivateKey.generate()
    write(output/'admin/signing.key', pem(admin_key))
    write(output/'kbs/admin.pub', admin_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=')
    now = int(time.time())
    header = b64(b'{"alg":"EdDSA","typ":"JWT"}')
    claims = b64(json.dumps({'iss':'CabinetAdmin','sub':'operator','aud':['KBS'],
                             'role':'admin','iat':now,'exp':now+3600}).encode())
    signing = header+b'.'+claims
    write(output/'admin/admin-token', signing+b'.'+b64(admin_key.sign(signing))+b'\n')
    for resource in ('kek-v1', 'audit-v1', 'operator-v1', 'state-v1'):
        write(output/'admin'/resource, os.urandom(32))
    write(output/'kbs/config.toml', '''[http_server]
sockets = ["0.0.0.0:8443"]
insecure_http = false
private_key = "/run/cabinet/tls.key"
certificate = "/run/cabinet/tls.pem"
[attestation_token]
trusted_certs_paths = ["/run/cabinet/as-ca.pem"]
[attestation_service]
type = "coco_as_grpc"
as_addr = "http://as:50004"
[admin]
authorization_mode = "AuthenticatedAuthorization"
[admin.authentication.bearer_jwt]
identity_providers = [{issuer="CabinetAdmin", public_key_uri="/run/cabinet/admin.pub"}]
[admin.authorization.regex_acl]
acls = [{role="admin", allowed_endpoints="^/kbs/.+$"}]
[[plugins]]
name = "resource"
storage_backend_type = "kvstorage"
[storage_backend]
storage_type = "LocalFs"
[storage_backend.backends.local_fs]
dir_path = "/var/lib/kbs"
''')
    write(output/'as/config.json', json.dumps({
        'storage_backend': {'storage_type':'LocalFs','backends':{'local_fs':{'dir_path':'/var/lib/as'}}},
        'rvps_config':{'type':'GrpcRemote','address':'http://rvps:50003'},
        'attestation_token_broker':{'duration_min':1,'signer':{
            'key_path':'/run/cabinet/token.key','cert_path':'/run/cabinet/token.pem'}}}, indent=2))
    write(output/'rvps/config.json', json.dumps({'storage':{
        'storage_type':'LocalFs','backends':{'local_fs':{'dir_path':'/var/lib/rvps'}}}}, indent=2))
    # JSON is accepted as a Compose file; avoid an additional YAML dependency.
    services = {}
    for name, dockerfile, command in [
        ('kbs','kbs/docker/coco-as-grpc/Dockerfile',['/usr/local/bin/kbs','--config-file','/run/cabinet/config.toml']),
        ('as','attestation-service/docker/as-grpc/Dockerfile',['grpc-as','--socket','0.0.0.0:50004','--config-file','/run/cabinet/config.json']),
        ('rvps','rvps/docker/Dockerfile',['rvps','--address','0.0.0.0:50003','--config','/run/cabinet/config.json'])]:
        services[name] = {'build':{'context':'./source','dockerfile':dockerfile},
                          'command':command,'restart':'unless-stopped',
                          'environment':{'RUST_LOG':'warn'},
                          'security_opt':['no-new-privileges:true'],
                          'volumes':[f'./{name}:/run/cabinet:ro', f'./data/{name}:/var/lib/{name}:rw']}
    services['kbs']['ports'] = ['127.0.0.1:8443:8443']
    services['kbs']['depends_on'] = ['as']
    services['as']['depends_on'] = ['rvps']
    write(output/'compose.json', json.dumps({'services':services}, indent=2))
    write(output/'DEPLOYMENT.json', json.dumps({
        'upstream_commit':TRUSTEE_COMMIT,'patch':'KBS default resource policy deny-all',
        'tls_host':host,'admin_token_expires':now+3600,
        'hardware_verified':False,'policy_enabled':False}, indent=2))
    print('Prepared TLS deployment with deny-all policy; no services started. Protect output directory.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--host', required=True)
    args = parser.parse_args()
    prepare(args.source, args.output, args.host)
