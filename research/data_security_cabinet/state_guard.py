"""Authenticated full-state commitments anchored outside the compute host.

Remote CAS precedes local commit. Interrupted cross-system commits deliberately
lock the cabinet instead of automatically accepting either snapshot. Recovery
requires an independently approved snapshot matching the current authority head.
No two-phase-commit availability guarantee; no reset or force-write operation.
"""
import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import ssl
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class StateIntegrityError(Exception):
    pass


class CheckpointUnavailable(StateIntegrityError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RemoteCheckpoint:
    def __init__(self, url, namespace, token, ca_file, timeout=10):
        origin = urlsplit(url)
        if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
                or origin.path not in ('', '/') or origin.query or origin.fragment):
            raise ValueError('Checkpoint requires an HTTPS origin')
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError('Independent checkpoint credential required')
        self.url, self.namespace, self.token = url.rstrip('/'), namespace, token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def _call(self, action, **values):
        nonce = secrets.token_hex(32)
        body = json.dumps(dict(namespace=self.namespace, nonce=nonce, **values)).encode()
        req = urllib.request.Request(self.url+'/v1/'+action, data=body, method='POST',
                                     headers={'Authorization':'Bearer '+self.token,
                                              'Content-Type':'application/json'})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read(8193)
                if len(raw) > 8192:
                    raise ValueError()
                value = json.loads(raw)
                if value.get('nonce') != nonce:
                    raise ValueError()
                return value
        except Exception:
            raise CheckpointUnavailable('Checkpoint rejected, stale or unavailable') from None

    def rotate(self, version, previous, current, next_token):
        return self._call('rotate', version=version, previous=previous, current=current,
                          next_token_sha256=hashlib.sha256(next_token.encode()).hexdigest())

    def head(self):
        return self._call('head')

    def advance(self, version, previous, current):
        return self._call('advance', version=version, previous=previous, current=current)


class StateGuard:
    def __init__(self, namespace, key, checkpoint):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError('State key must be independent and at least 32 bytes')
        self.namespace, self.key, self.checkpoint = namespace, key, checkpoint
        self.local = threading.local()

    def commitment(self, db):
        # Includes all application tables, schema, triggers, indexes and columns.
        # SQL names are quoted; untrusted schema is data, not interpolated code.
        mac = hmac.new(self.key, b'cabinet-state-v1\x00'+self.namespace.encode(), hashlib.sha256)
        def emit(value):
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                                 ensure_ascii=False, allow_nan=False).encode()
            mac.update(len(encoded).to_bytes(8,'big')+encoded)
        def atom(value):
            if isinstance(value, bytes):
                return ['blob',base64.b64encode(value).decode()]
            return [type(value).__name__,value]
        try:
            schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
            emit([list(r) for r in schema])
            for entry in schema:
                if entry[0] != 'table':
                    continue
                name = entry[1].replace('"','""')
                rows = [[atom(v) for v in row] for row in db.execute('SELECT * FROM "'+name+'"')]
                rows.sort(key=lambda r: json.dumps(r, sort_keys=True, separators=(',',':')))
                emit([entry[1],rows])
            return mac.hexdigest()
        except (sqlite3.Error, ValueError, TypeError):
            raise StateIntegrityError('Cannot authenticate database state') from None

    def verify(self, db):
        head = self.checkpoint.head()
        if head['digest'] is None or not hmac.compare_digest(head['digest'], self.commitment(db)):
            raise StateIntegrityError('Tampered, rolled back, uninitialized or mismatched database')
        self.local.now = head['time']
        return head

    def finish(self, db, head):
        # CAS even for reads prevents a second fork from returning a stale result.
        self.checkpoint.advance(head['version'], head['digest'], self.commitment(db))

    def initialize_empty(self, db):
        # Explicit bootstrap only. Never bless an existing business database.
        for table in ('identities','assets','grants','jobs','audit'):
            if db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]:
                raise StateIntegrityError('Refusing to initialize nonempty legacy state')
        head = self.checkpoint.head()
        if head['version'] != 0 or head['digest'] is not None:
            raise StateIntegrityError('Authority already initialized; reset forbidden')
        self.checkpoint.advance(0, None, self.commitment(db))

    def clock(self):
        if not hasattr(self.local, 'now'):
            raise StateIntegrityError('Trusted transaction time unavailable')
        return self.local.now
