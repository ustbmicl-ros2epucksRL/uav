#!/usr/bin/env python3
"""Independent TLS checkpoint authority. Deploy in a DIFFERENT trusted domain.
No reset/delete HTTP endpoints. Authority disks and backups must not be rolled
back together with the compute node. This is a single-node research service.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Conflict(Exception):
    pass


class Authority:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        if not Path(path).is_file():
            raise ValueError('Provision authority offline first; no automatic recreation')

    @staticmethod
    def provision(path, namespace, token):
        if not re.fullmatch('[A-Za-z0-9_-]{1,80}', namespace) or len(token)<32:
            raise ValueError('Invalid namespace or token')
        fd = os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
        os.close(fd)
        db = sqlite3.connect(path)
        try:
            db.execute('CREATE TABLE heads(namespace TEXT PRIMARY KEY, token TEXT NOT NULL, '
                       'version INTEGER NOT NULL, digest TEXT)')
            db.execute('INSERT INTO heads VALUES(?,?,0,NULL)',
                       (namespace, hashlib.sha256(token.encode()).hexdigest()))
            db.commit()
        finally:
            db.close()

    def call(self, action, body, token):
        if action not in ('head','advance','rotate'):
            raise ValueError('Unknown operation')
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT token,version,digest FROM heads WHERE namespace=?',
                             (body['namespace'],)).fetchone()
            if row is None or not hmac.compare_digest(row[0], hashlib.sha256(token.encode()).hexdigest()):
                raise PermissionError()
            version, digest = row[1], row[2]
            if action in ('advance', 'rotate'):
                if type(body['version']) is not int or body['version'] != version or body['previous'] != digest:
                    raise Conflict('Checkpoint CAS conflict')
                if not isinstance(body['current'],str) or not re.fullmatch('[a-f0-9]{64}',body['current']):
                    raise ValueError('Invalid commitment')
                version, digest = version+1, body['current']
                token_hash = row[0]
                if action == 'rotate':
                    token_hash = body['next_token_sha256']
                    if not re.fullmatch('[0-9a-f]{64}', token_hash) or token_hash == row[0]:
                        raise ValueError('Independent new token required')
                db.execute('UPDATE heads SET version=?,digest=?,token=? WHERE namespace=?',
                           (version, digest, token_hash, body['namespace']))
            result = {'nonce':body['nonce'], 'version':version,'digest':digest,'time':self.clock()}
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def handler(authority):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log credentials or request bodies.

        def do_POST(self):
            try:
                self.connection.settimeout(10)
                if self.path not in ('/v1/head','/v1/advance','/v1/rotate'):
                    raise ValueError()
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 4096 or self.headers.get('Transfer-Encoding'):
                    raise ValueError()
                body = json.loads(self.rfile.read(size))
                if not re.fullmatch('[a-f0-9]{64}',body['nonce']):
                    raise ValueError()
                auth = self.headers.get('Authorization','')
                if not auth.startswith('Bearer '):
                    raise PermissionError()
                result = authority.call(self.path.rsplit('/',1)[1],body,auth[7:])
                payload = json.dumps(result, allow_nan=False).encode()
                self.send_response(200)
            except PermissionError:
                payload=b'{"error":"denied"}';self.send_response(403)
            except Conflict:
                payload=b'{"error":"conflict"}';self.send_response(409)
            except Exception:
                payload=b'{"error":"unavailable"}';self.send_response(503)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    return Handler


def make_server(authority, host, port, cert, key):
    server = ThreadingHTTPServer((host,port),handler(authority))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert,key)
    server.socket = context.wrap_socket(server.socket,server_side=True)
    return server


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('provision');init.add_argument('--db',required=True)
    init.add_argument('--namespace',required=True);init.add_argument('--token-file',required=True)
    serve=sub.add_parser('serve');serve.add_argument('--db',required=True)
    serve.add_argument('--host',default='127.0.0.1');serve.add_argument('--port',type=int,default=9443)
    serve.add_argument('--cert',required=True);serve.add_argument('--key',required=True)
    args=parser.parse_args()
    if args.command=='provision':
        # Credential is generated offline, never printed. Destination must be new.
        raw_token=secrets.token_bytes(32)
        token=raw_token.hex()
        fd=os.open(args.token_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw_token)
        Authority.provision(args.db,args.namespace,token)
        print('Provisioned empty namespace; credential written to protected file.')
    else:
        make_server(Authority(args.db),args.host,args.port,args.cert,args.key).serve_forever()
