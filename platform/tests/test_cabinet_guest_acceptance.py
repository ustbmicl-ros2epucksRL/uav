import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from cabinet_guest_acceptance import preflight
from cabinet_trustee_guest import GuestTrustee


def test_mac_cannot_enter_guest_mode(monkeypatch):
    monkeypatch.setattr('platform.system', lambda: 'Darwin')
    for tee in ('snp','tdx'):
        with pytest.raises(ValueError, match='Linux'):
            preflight(tee, None, None)


def test_verifier_decision_must_match_reference_and_fresh_event(monkeypatch):
    guest = GuestTrustee.__new__(GuestTrustee)
    guest.config = {'namespace':'platform-test'}
    guest.mode, guest.controller, guest.run_id = 'tdx', '/test-controller', 'test-run'
    guest.measurement, guest.events, guest.calls, guest.sessions = None, set(), set(), set()
    event = dict(namespace='platform-test', tee='tdx', run_id='test-run',
                 measurement='a'*96, reference='a'*96, decision='approved', session_sha256='4'*64,ear_sha256='5'*64,event_id='first', operation='unwrap',resource='kek-v1', preview_id='a'*32, call_id='b'*32,raw_records={'as':'1'*64,'kbs':'2'*64})
    monkeypatch.setattr('subprocess.run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(event)))
    assert guest.control('observe-approved','a'*32)['measurement'] == 'a'*96
    with pytest.raises(ValueError, match='reused'):
        guest.control('observe-approved','a'*32)
    event.update(session_sha256='6'*64,ear_sha256='7'*64,event_id='second', decision='denied', operation='guard',resource='state-v1',call_id='c'*32)
    with pytest.raises(ValueError, match='inconsistent'):
        guest.control('observe-denied','a'*32)
    event.update(reference='b'*96)
    assert guest.control('observe-denied','a'*32)['decision'] == 'denied'
