#!/usr/bin/env python3
"""Issue a 10-minute admin token on the isolated trust host; never prints it."""
import argparse
import base64
import json
import os
from pathlib import Path
import tempfile
import time
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def renew(directory):
    root = Path(directory).resolve()/'admin'
    key = load_pem_private_key((root/'signing.key').read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Expected Ed25519 admin signing key')
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=')
    now = int(time.time())
    payload = {'iss':'CabinetAdmin','sub':'operator','aud':['KBS'],
               'role':'admin','iat':now,'exp':now+600}
    message = enc(b'{"alg":"EdDSA","typ":"JWT"}')+b'.'+enc(json.dumps(payload).encode())
    fd, path = tempfile.mkstemp(dir=root, prefix='.admin-token-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(message+b'.'+enc(key.sign(message))+b'\n')
        os.replace(path, root/'admin-token')
    finally:
        if os.path.exists(path):
            os.unlink(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('deployment')
    args = parser.parse_args()
    renew(args.deployment)
    print('Admin token renewed for 10 minutes; secret not displayed.')
