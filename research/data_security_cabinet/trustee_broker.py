"""Trustee KBS adapter for a measured confidential VM; no local attestation bypass.

The pinned upstream kbs-client owns RCAR evidence collection, nonce/key binding,
TLS validation and JWE decryption. This adapter never accepts caller-supplied
attestation results. Test doubles do NOT establish hardware security.
A KEK is scoped to a trusted cabinet executor, NOT independently to each user.
Business authorization must run inside that executor with rollback-protected state.
"""
from contextvars import ContextVar
import secrets
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from security_cabinet import Denied, IntegrityError, ReleaseRequest, canonical

TRUSTEE_COMMIT = '22ca43188fb2db632d43d3d5a164e0edbd5ab021'
RESOURCE_PATTERN = re.compile(r'[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+\Z')


key_operation = ContextVar('key_operation', default='guard')
preview_request = ContextVar('preview_request', default=None)


class AttestedResourceClient:
    """Run a pinned real hardware attester inside the TEE, with fresh sessions.

    No shell, fallback, token import, private-key import, inherited proxy or
    secret logging. Binary hash and CA must be provisioned in the measured image.
    Hash checking alone does not protect against guest root / TOCTOU replacement.
    """
    def __init__(self, binary, binary_sha256, url, ca_file, *, timeout=30):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('KBS URL must be an HTTPS origin without credentials')
        if not re.fullmatch('[a-f0-9]{64}', binary_sha256):
            raise ValueError('A pinned client SHA-256 is required')
        self.binary = Path(binary)
        self.ca = Path(ca_file)
        if not self.binary.is_absolute() or not self.ca.is_absolute():
            raise ValueError('Client and CA paths must be absolute')
        if not self.binary.is_file() or not self.ca.is_file():
            raise ValueError('Client or trusted CA does not exist')
        if not 1 <= timeout <= 120:
            raise ValueError('Timeout must be between 1 and 120 seconds')
        self.sha = binary_sha256
        self.ca_digest = hashlib.sha256(self.ca.read_bytes()).digest()
        self.url = url.rstrip('/')
        self.timeout = timeout

    def get(self, resource):
        if not RESOURCE_PATTERN.fullmatch(resource):
            raise Denied('Invalid resource path')
        try:
            if (hashlib.sha256(self.binary.read_bytes()).hexdigest() != self.sha
                    or hashlib.sha256(self.ca.read_bytes()).digest() != self.ca_digest):
                raise Denied('Client or CA has changed')
            # stdout is base64 secret data in the pinned upstream CLI. Never log it.
            env = {'PATH': '/usr/bin:/bin', 'RUST_LOG': 'off'}
            if preview_request.get():
                env.update(CABINET_PREVIEW_ID=preview_request.get(), CABINET_KEY_CALL_ID=secrets.token_hex(16),
                           CABINET_KEY_OPERATION=key_operation.get())
            completed = subprocess.run(
                [str(self.binary), '--url', self.url, '--cert-file', str(self.ca),
                 'get-resource', '--path', resource],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=self.timeout, check=False,
                env=env)
            if completed.returncode != 0 or len(completed.stdout) > 128:
                raise Denied('KBS request or attestation rejected')
            key = base64.b64decode(completed.stdout.strip(), validate=True)
            if len(key) != 32:
                raise Denied('KBS resource must contain exactly 32 raw bytes')
            return key
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise Denied('KBS unavailable or invalid response') from None


class TrusteeKeyBroker:
    """Fetch scoped KEK via attestation for every operation; no persistent cache.

    KEK rotation uses a new resource version and an explicit read allowlist.
    Old LocalDemoBroker envelopes are intentionally not accepted. Migration must
    decrypt and re-encrypt in a trusted environment under an explicit procedure.
    """
    def __init__(self, client, write_resource, read_resources):
        self.client = client
        self.write_resource = write_resource
        self.read_resources = frozenset(read_resources)
        if write_resource not in self.read_resources:
            raise ValueError('Write resource must be in the explicit read allowlist')
        if not self.read_resources or not all(RESOURCE_PATTERN.fullmatch(x) for x in self.read_resources):
            raise ValueError('Invalid resource allowlist')

    def wrap(self, key, aad):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError('DEK must be 32 bytes')
        header = canonical({'version': 1, 'resource': self.write_resource})
        kek = self.client.get(self.write_resource)
        nonce = os.urandom(12)
        payload = AESGCM(kek).encrypt(nonce, key, header + b'\x00' + aad)
        return canonical({'header': json.loads(header),
                          'wrapped': base64.b64encode(nonce + payload).decode()})

    def unwrap(self, envelope, aad, request):
        if not isinstance(request, ReleaseRequest) or not all(
                isinstance(x, str) and x for x in
                (request.asset_id, request.principal, request.purpose, request.job_id)):
            raise Denied('Missing execution context')
        try:
            if len(envelope) > 2048:
                raise ValueError()
            obj = json.loads(envelope)
            header = obj['header']
            if (set(obj) != {'header', 'wrapped'} or set(header) != {'version', 'resource'}
                    or type(header['version']) is not int or header['version'] != 1):
                raise ValueError()
            resource = header['resource']
            if resource not in self.read_resources:
                raise Denied('Resource outside executor allowlist')
            wrapped = base64.b64decode(obj['wrapped'], validate=True)
            if len(wrapped) != 60:
                raise ValueError()
        except (ValueError, KeyError, TypeError) as error:
            raise IntegrityError('Invalid remote envelope') from None
        operation = key_operation.set('unwrap')
        try:
            kek = self.client.get(resource)
        finally:
            key_operation.reset(operation)
        # InvalidTag is handled by Cabinet._open. It is intentionally not retried.
        return AESGCM(kek).decrypt(wrapped[:12], wrapped[12:], canonical(header) + b'\x00' + aad)
