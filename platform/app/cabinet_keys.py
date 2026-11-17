"""Select key sources from server configuration before opening any cabinet state."""
import json
import os
import re
from pathlib import Path

from security_cabinet import Denied
from state_guard import RemoteCheckpoint, StateGuard
from trustee_broker import AttestedResourceClient, TrusteeKeyBroker


class KeyConfigurationError(Exception):
    pass


def selected_mode():
    mode = os.environ.get('SENSE_CABINET_KEY_MODE')
    if mode is None and 'SENSE_CABINET_ATTESTATION_CONFIG' not in os.environ:
        return 'demo'  # Existing Mac installations predate the selector.
    if mode not in ('demo', 'hardware'):
        raise KeyConfigurationError('密钥模式无效，请配置 demo 或 hardware')
    if mode == 'demo' and 'SENSE_CABINET_ATTESTATION_CONFIG' in os.environ:
        raise KeyConfigurationError('演示模式与远程证明配置冲突')
    return mode


class HardwareGuard(StateGuard):
    def __init__(self, config):
        namespace = config['namespace']
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', namespace):
            raise ValueError('Invalid namespace')
        client = AttestedResourceClient(config['client'], config['client_sha256'],
                                        config['url'], config['ca_file'])
        prefix = f'{namespace}/keys/'
        state_key = client.get(prefix + 'state-v1')
        token = client.get(prefix + 'checkpoint-v1')
        remote = RemoteCheckpoint(config['checkpoint_url'], namespace, token.hex(),
                                  config['checkpoint_ca_file'])
        super().__init__(namespace, state_key, remote)
        self.audit_key = client.get(prefix + 'audit-v1')
        self.operator_token = client.get(prefix + 'operator-v1').hex()
        resource = prefix + 'kek-v1'
        client.get(resource)  # Confirm resource policy even on login/status reads.
        self.broker = TrusteeKeyBroker(client, resource, [resource])


def hardware_guard():
    try:
        config = json.loads(Path(os.environ['SENSE_CABINET_ATTESTATION_CONFIG']).read_text())
        return HardwareGuard(config)
    except (KeyError, OSError, TypeError, ValueError, Denied):
        raise KeyConfigurationError('硬件模式远程取钥失败，请检查证明客户端、策略和可信服务') from None
