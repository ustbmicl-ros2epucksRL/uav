#!/usr/bin/env python3
"""Real KBS connectivity smoke test. Run inside the approved confidential guest.
Does not attest hardware by itself; all attestation is delegated to kbs-client.
Output is deliberately not labelled as proof that all root attacks are resisted.
"""
import argparse
import json
from pathlib import Path
import time
from security_cabinet import Cabinet, synthetic_data
from state_guard import StateGuard, RemoteCheckpoint
from trustee_broker import AttestedResourceClient, TrusteeKeyBroker


def run(config_path, initialize_state=False):
    config = json.loads(Path(config_path).read_text())
    client = AttestedResourceClient(config['client'], config['client_sha256'],
                                    config['url'], config['ca_file'])
    namespace = config['namespace']
    broker = TrusteeKeyBroker(client, f'{namespace}/keys/kek-v1',
                              [f'{namespace}/keys/kek-v1'])
    audit_key = client.get(f'{namespace}/keys/audit-v1')
    operator_token = client.get(f'{namespace}/keys/operator-v1').hex()
    state_key = client.get(f'{namespace}/keys/state-v1')
    checkpoint_token = client.get(f'{namespace}/keys/checkpoint-v1').hex()
    checkpoint = RemoteCheckpoint(config['checkpoint_url'], namespace, checkpoint_token,
                                  config['checkpoint_ca_file'])
    guard = StateGuard(namespace, state_key, checkpoint)
    cabinet = Cabinet(config['database'], audit_key=audit_key,
                      operator_token=operator_token, broker=broker, state_guard=guard,
                      initialize_state=initialize_state)
    owner = cabinet.enroll(operator_token, 'synthetic-owner')
    user = cabinet.enroll(operator_token, 'synthetic-consumer')
    asset = cabinet.put(owner['token'], 'SYNTHETIC', synthetic_data())
    grant = cabinet.grant(owner['token'], asset['asset_id'], user['id'],
                          'dataset', expires=checkpoint.head()['time']+60, quota=1)
    job = cabinet.run(user['token'], grant)
    result = cabinet.read_result(user['token'], job)
    return {'synthetic':True, 'remote_kbs_roundtrip':'completed',
            'root_attack_validation':'NOT_PERFORMED',
            'samples':result['quality']['usable_rows'],
            'audit_events':cabinet.verify_audit()['events']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--initialize-state', action='store_true', help='One-time empty namespace bootstrap only')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.config, args.initialize_state), ensure_ascii=False, indent=2))
    except Exception:
        # Do not include subprocess output, key material or configuration secrets.
        raise SystemExit('Attested smoke failed closed; inspect sanitized server logs.')
