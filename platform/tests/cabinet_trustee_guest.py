"""Guest-side acceptance adapter. Policy administration stays on a separate host."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from app import cabinet_demo as api
from cabinet_guest_acceptance import reachable, require


class GuestTrustee:
    def __init__(self, folder, namespace, stack):
        self.config = json.loads(Path(os.environ['CABINET_QA_GUEST_CONFIG']).read_text())
        require(namespace == self.config['namespace'], 'namespace mismatch')
        self.folder = folder
        self.folder.mkdir()
        self.mode = os.environ['CABINET_QA_KBS']
        self.controller = os.environ['CABINET_QA_CONTROLLER']
        self.run_id = os.urandom(16).hex()
        self.measurement = None
        self.events = set()
        self.calls = set()
        self.sessions = set()
        stack.callback(self.control, 'restore')
        self.control('approved')

    def control(self, action, preview_id=None):
        args = [self.controller, action, self.mode, self.run_id]
        if preview_id:
            args.append(preview_id)
        proc = subprocess.run(args,
                              capture_output=True, timeout=120)
        require(proc.returncode == 0, f'controller failed during {action}')
        if action.startswith('observe-'):
            event = json.loads(proc.stdout)
            decision = action.removeprefix('observe-')
            require(event['run_id'] == self.run_id and event['tee'] == self.mode and
                    event['decision'] == decision and event['namespace'] == self.config['namespace'],
                    'unexpected verifier event')
            require(preview_id and event.get('preview_id') == preview_id and
                    re.fullmatch('[a-f0-9]{32}',event.get('call_id','')), 'unbound preview event')
            expected = ('unwrap','kek-v1') if decision == 'approved' else ('guard','state-v1')
            require((event.get('operation'),event.get('resource')) == expected, 'wrong preview key operation')
            for name in ('session_sha256','ear_sha256'):
                require(re.fullmatch('[0-9a-f]{64}',event.get(name,'')), 'missing attested session reference')
            require(event['session_sha256'] not in self.sessions, 'proof session reused')
            size = 96  # SNP MEASUREMENT and TDX MRTD are SHA-384 sized.
            for name in ('measurement', 'reference'):
                require(re.fullmatch(f'[0-9a-f]{{{size}}}', event[name]), 'invalid launch measurement')
            require((event['reference'] == event['measurement']) == (decision == 'approved'),
                    'verdict inconsistent with measured launch digest')
            require(self.measurement in (None,event['measurement']), 'guest measurement changed between policies')
            self.measurement = event['measurement']
            require(re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', event['event_id']) and event['event_id'] not in self.events,
                    'invalid or repeated verifier event ID')
            refs = event.get('raw_records', {})
            require(set(refs) == {'as','kbs'} and all(re.fullmatch('[0-9a-f]{64}', item) for item in refs.values()),
                    'missing original AS or KBS digest')
            require(event['call_id'] not in self.calls, 'repeated key call')
            self.sessions.add(event['session_sha256'])
            self.calls.add(event['call_id'])
            self.events.add(event['event_id'])
            return {key:event[key] for key in ('session_sha256','ear_sha256','operation','resource','preview_id','call_id','run_id','namespace','tee','decision','measurement','reference','event_id','raw_records')}

    def target(self, monkeypatch):
        old = api.store.DATA/'config.json'
        checkpoint = json.loads(old.read_text())
        hw = self.folder/'target.json'
        hw.write_text(json.dumps(dict(self.config, checkpoint_url=checkpoint['url'],
                                      checkpoint_ca_file=checkpoint['ca_file'])))
        monkeypatch.setenv('SENSE_CABINET_KEY_MODE','hardware')
        monkeypatch.setenv('SENSE_CABINET_ATTESTATION_CONFIG',str(hw))
        monkeypatch.setattr(sys,'argv',['admin','migrate-demo-keys','--source-checkpoint',str(old)])
        return api.demo_guard(old)

    def policy(self, name):
        self.control('approved' if name == 'sample' else 'mismatch')
        reachable(self.config)

    def offline(self):
        self.control('offline')
        try:
            reachable(self.config, tls=False)
        except OSError:
            return
        raise ValueError('guest still has a TCP route to KBS')

    def online(self):
        self.control('approved')
        reachable(self.config)
