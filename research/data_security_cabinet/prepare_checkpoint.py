#!/usr/bin/env python3
"""Offline research checkpoint provisioning. Run on the independent service host."""
import argparse
import json
import secrets
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from checkpoint_service import Authority
from prepare_trustee import certificate, pem, write


def prepare(output, host, namespace, port=9443):
    root = Path(output).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    key = ec.generate_private_key(ec.SECP256R1())
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = certificate(ca_key, 'Independent checkpoint CA')
    cert = certificate(key, host, ca_key, ca, host)
    write(root/'ca.pem', ca.public_bytes(serialization.Encoding.PEM))
    write(root/'tls.pem', cert.public_bytes(serialization.Encoding.PEM))
    write(root/'tls.key', pem(key))
    token = secrets.token_bytes(32)
    write(root/'token', token)
    write(root/'state.key', secrets.token_bytes(32))
    Authority.provision(root/'authority.db', namespace, token.hex())
    write(root/'platform.json', json.dumps(dict(
        url=f'https://{host}:{port}', namespace=namespace, ca_file=str(root/'ca.pem'),
        token_file=str(root/'token'), state_key_file=str(root/'state.key')), indent=2))
    # CA private key is intentionally not retained; renew using a controlled CA rotation.
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--port', type=int, default=9443)
    args = parser.parse_args()
    prepare(args.output, args.host, args.namespace, args.port)
    print('Provisioned. Credentials were written to protected files, not stdout.')
