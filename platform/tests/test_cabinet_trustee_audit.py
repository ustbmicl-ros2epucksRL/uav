"""Negative cases for audit correlation and replay of archived evidence."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from cabinet_trustee_audit import correlate, verify_report


def records(status):
    prefix = '2026-11-16T13:16:00.001Z 2026-11-16T13:16:00.000Z  INFO '
    path = '/kbs/v0/resource/platform-test/keys/state-v1'
    def access(method, route, code):
        return prefix + f'actix_web::middleware::logger: 172.20.0.3 "{method} {route} HTTP/1.1" {code} 123 "-" "attestation-agent-kbs-client/0.1.0" 0.002'
    return [access('GET',path,401),access('POST','/kbs/v0/auth',200),
            prefix+'attestation_service: Verifier/endorsement check passed. tee=Sample tee_class="cpu"',
            access('POST','/kbs/v0/attest',200), access('GET',path,status)]


def test_missing_as_ambiguous_and_wrong_resource():
    lines = records(403)
    for bad in (lines[:2]+lines[3:], lines+lines, lines[:-1]+[lines[-1].replace('state-v1','kek-v1')],
                lines[:-1]+[lines[-1].replace('172.20.0.3','172.20.0.4')]):
        with pytest.raises(ValueError):
            correlate(bad, '1'*64,'platform-test')


def test_original_record_tampering(tmp_path):
    report = tmp_path/'accept.json'
    archive = tmp_path/'accept.json.audit'
    archive.mkdir()
    stages = {}
    for stage, code in [('approved_release',200),('measurement_denied',403)]:
        stages[stage], raw = correlate(records(code),'1'*64,'platform-test')
        file = archive/stages[stage]['raw_file']
        file.write_bytes(raw)
    report.write_text(json.dumps(dict(mode='sample',stages=stages)))
    verify_report(report)
    file.write_bytes(raw.replace(b'0.002',b'0.003'))
    with pytest.raises(ValueError):
        verify_report(report)


def test_preview_replay_rejects_swapped_concurrent_events(tmp_path):
    report = tmp_path/'previews.json'
    archive = tmp_path/'previews.json.audit'
    archive.mkdir()
    previews, pair = [], []
    for idx, status in enumerate((200,200,403), 1):
        resource = 'kek-v1' if status == 200 else 'state-v1'
        lines = [line.replace('state-v1', resource) for line in records(status)]
        event, raw = correlate(lines, str(idx)*64, 'platform-test', resource)
        (archive/event['raw_file']).write_bytes(raw)
        rid = str(idx)*32
        call = dict(preview_id=rid,call_id=rid,operation='unwrap' if status==200 else 'guard',event=event)
        (archive/(rid+'.jsonl')).write_text(json.dumps(call)+'\n')
        content_range = f'bytes {idx}-{idx}/10' if status == 200 else None
        previews.append(dict(preview_id=rid,calls=[call],status=206 if status==200 else 503,content_range=content_range))
        if status == 200:
            pair.append(dict(preview_id=rid,event_id=event['event_id'],content_range=content_range))
    stages = dict(preview_requests=previews, concurrent_previews=pair,
                  approved_release=previews[0]['calls'][0]['event'],measurement_denied=event)
    result = dict(mode='sample',audit_version=2,stages=stages)
    verify_report(report,result)
    pair[0]['event_id'] = pair[1]['event_id']
    with pytest.raises(ValueError,match='association mismatch'):
        verify_report(report,result)


def test_sample_binding_rejects_cross_session_records(tmp_path):
    from cabinet_sample_session import canonical, sha
    from cabinet_trustee_audit import session_event
    prefix = '2026-11-16T01:00:00.001Z 2026-11-16T01:00:00.000Z  INFO actix_web::middleware::logger: 127.0.0.1 '
    preview, call, resource = 'a'*32, 'b'*32, 'platform-test/keys/kek-v1'
    binding = sha(canonical([preview,call,'unwrap',resource]))
    query = f'?preview={preview}&call={call}&binding={binding}'
    reference = '&session='+'1'*64+'&challenge='+'2'*64
    proof = sha(bytes.fromhex('4'*64)+b'x')
    routes = [('POST','auth'+query),('POST','attest'+query+reference),
              ('GET','resource/'+resource+query+reference+'&ear='+'3'*64+'&proof='+proof)]
    records = [prefix+f'"{verb} /kbs/v0/{url} HTTP/1.1" 200 123 "-" "cabinet-sample-session/1" 0.001'
               for verb,url in routes]
    evidence = dict(preview_id=preview,call_id=call,operation='unwrap',path=resource,binding=binding,
                    session_sha256='1'*64,nonce_sha256='2'*64,ear_sha256='3'*64,as_signing_digest='4'*64,
                    as_signature='eA',source_container='5'*64,decision='approved',begin_ns=1,end_ns=2,records=records)
    assert session_event(evidence)[0]['decision']=='approved'
    for changed in (dict(call_id='d'*32),dict(session_sha256='6'*64),dict(as_signing_digest='7'*64),
                    dict(records=records[:2]),dict(records=records[:2]+[records[2].replace(call,'d'*32)])):
        with pytest.raises(ValueError):
            session_event(dict(evidence,**changed))
