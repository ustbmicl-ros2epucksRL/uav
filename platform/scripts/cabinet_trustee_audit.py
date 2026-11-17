"""Correlate one isolated upstream sample request and verify its archived raw lines."""
import hashlib
import json
from pathlib import Path
import re
import sys

STAMP = r'\d{4}-\d\d-\d\dT[\d:.]+Z'
PREFIX = STAMP + ' ' + STAMP + r' +INFO '
ACCESS = re.compile(PREFIX + r'actix_web::middleware::logger: ([\d.]+) "(GET|POST) (/kbs/v0/[\w/-]+) HTTP/1.1" (\d{3}) \d+ "-" "attestation-agent-kbs-client/[\d.]+" [\d.]+')
AS = re.compile(PREFIX + r'attestation_service: Verifier/endorsement check passed\. tee=Sample tee_class="cpu"')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def correlate(lines, source, namespace, resource='state-v1'):
    if not re.fullmatch('[a-f0-9]{64}', source) or not re.fullmatch('[A-Za-z0-9_-]+', namespace):
        raise ValueError('invalid source identity')
    if resource not in {'state-v1','audit-v1','operator-v1','checkpoint-v1','kek-v1'}:
        raise ValueError('invalid audit resource')
    path = f'/kbs/v0/resource/{namespace}/keys/{resource}'
    sequence, raw, peers = [], [], set()
    for line in lines:
        match = ACCESS.fullmatch(line)
        if match:
            peer, method, route, status = match.groups()
            peers.add(peer)
            sequence.append((method, route, status))
        elif AS.fullmatch(line):
            sequence.append('sample')
        elif any(marker in line for marker in ('actix_web::middleware::logger:', 'Verifier/endorsement check passed.')):
            raise ValueError('unrecognized or concurrent Trustee record')
        else:
            continue
        raw.append(line)
    expected = [('GET',path,'401'), ('POST','/kbs/v0/auth','200'), 'sample',
                ('POST','/kbs/v0/attest','200')]
    if sequence[:-1] != expected or len(peers) != 1 or sequence[-1] not in [('GET',path,'200'),('GET',path,'403')]:
        raise ValueError('incomplete or ambiguous Trustee request')
    content = ('\n'.join(raw)+'\n').encode()
    event_id = digest(source.encode()+b'\n'+content)
    return dict(event_id=event_id, source_container=source, namespace=namespace, resource=resource,
                tee='sample', decision='approved' if sequence[-1][-1]=='200' else 'denied',
                raw_sha256=digest(content), raw_file=event_id+'.log'), content


def session_event(evidence):
    from cabinet_sample_session import canonical, decode
    rid, cid = evidence['preview_id'], evidence['call_id']
    op, path = evidence['operation'], evidence['path']
    if not all(re.fullmatch('[0-9a-f]{32}', v) for v in (rid,cid)) or op not in ('guard','unwrap'):
        raise ValueError('invalid call identity')
    namespace, folder, resource = path.split('/')
    if folder != 'keys' or not re.fullmatch(r'[\w-]+/keys/[\w-]+',path):
        raise ValueError('invalid resource')
    binding = digest(canonical([rid,cid,op,path]))
    if evidence['binding'] != binding or not all(re.fullmatch('[0-9a-f]{64}',evidence[k])
        for k in ('ear_sha256','session_sha256','nonce_sha256','source_container')):
        raise ValueError('invalid attested call binding')
    query = f'?preview={rid}&call={cid}&binding={binding}'
    decision = evidence['decision']
    if decision not in ('approved','denied') or evidence['begin_ns'] >= evidence['end_ns']:
        raise ValueError('invalid session result')
    session_ref = '&session='+evidence['session_sha256']+'&challenge='+evidence['nonce_sha256']
    proof = digest(bytes.fromhex(evidence['as_signing_digest'])+decode(evidence['as_signature']))
    expected = [('POST','auth'+query,200),('POST','attest'+query+session_ref,200),
                ('GET','resource/'+path+query+session_ref+'&ear='+evidence['ear_sha256']+'&proof='+proof,200 if decision=='approved' else 403)]
    if len(evidence['records']) != len(expected):
        raise ValueError('missing or duplicate request-specific server record')
    for line,(method,endpoint,status) in zip(evidence['records'],expected):
        pattern = PREFIX + r'actix_web::middleware::logger: [^ ]+ "' + re.escape(method+' /kbs/v0/'+endpoint) + r' HTTP/1.1" '+str(status)+r' \d+ "-" "cabinet-sample-session/1" [\d.]+'
        if not re.fullmatch(pattern,line):
            raise ValueError('cross-session or unexpected KBS record')
    raw = canonical(evidence)+b'\n'
    event_id = digest(raw)
    return dict(event_id=event_id,raw_file=event_id+'.log',raw_sha256=event_id,tee='sample',
                namespace=namespace,resource=resource,decision=decision,preview_id=rid,call_id=cid,
                operation=op,session_sha256=evidence['session_sha256'],ear_sha256=evidence['ear_sha256'],
                begin_ns=evidence['begin_ns'],end_ns=evidence['end_ns']), raw


def verify_report(report, result=None):
    report = Path(report)
    if result is None:
        result = json.loads(report.read_text())
    if result['mode'] != 'sample':
        raise ValueError('expected a sample report')
    archive = report.with_name(report.name+'.audit')
    def verify_event(event):
        if not re.fullmatch('[a-f0-9]{64}', event['event_id']):
            raise ValueError('invalid audit identifier')
        raw = (archive/(event['event_id']+'.log')).read_bytes()
        if 'session_sha256' in event:
            from cabinet_sample_session import decode
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec, utils
            evidence = json.loads(raw)
            parsed, content = session_event(evidence)
            sig = decode(evidence['as_signature'])
            signer = x509.load_pem_x509_certificate((archive/'as-signer.pem').read_bytes())
            signer.public_key().verify(utils.encode_dss_signature(int.from_bytes(sig[:32],'big'),
                int.from_bytes(sig[32:],'big')),bytes.fromhex(evidence['as_signing_digest']),
                ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        else:
            parsed, content = correlate(raw.decode().splitlines(), event['source_container'],
                                        event['namespace'], event['resource'])
        if content != raw or event != parsed:
            raise ValueError('report and original Trustee records disagree')

    stages = result['stages']
    for stage, decision in [('approved_release','approved'),('measurement_denied','denied')]:
        event = stages[stage]
        verify_event(event)
        if event['decision'] != decision:
            raise ValueError('unexpected resource decision')

    if 'preview_requests' not in stages and result.get('audit_version') != 2:
        return  # Archived probe-only report.
    seen_calls, seen_events, previews = set(), set(), {}
    for request in stages['preview_requests']:
        rid = request['preview_id']
        if not re.fullmatch('[0-9a-f]{32}',rid) or rid in previews:
            raise ValueError('invalid or duplicate preview')
        calls = [json.loads(line) for line in (archive/(rid+'.jsonl')).read_text().splitlines()]
        if calls != request['calls'] or not calls:
            raise ValueError('preview call index disagrees with report')
        for call in calls:
            cid = call['call_id']
            if call['preview_id'] != rid or not re.fullmatch('[0-9a-f]{32}',cid) or cid in seen_calls:
                raise ValueError('key call attributed to multiple previews')
            seen_calls.add(cid)
            event = call['event']
            if event == {'decision':'unavailable'} and request['status'] == 503:
                continue
            verify_event(event)
            if result.get('audit_version',0) >= 3 and (event.get('preview_id'),event.get('call_id'),event.get('operation')) != (rid,cid,call['operation']):
                raise ValueError('session assigned to wrong request')
            if event['event_id'] in seen_events:
                raise ValueError('Trustee event reused')
            seen_events.add(event['event_id'])
        last = calls[-1]['event']
        if request['status'] == 206 and (last['decision'],last['resource'],calls[-1]['operation']) != ('approved','kek-v1','unwrap'):
            raise ValueError('missing video unwrap evidence')
        previews[rid] = (last.get('event_id'),request['content_range'])
    pair = stages['concurrent_previews']
    if len(pair) != 2 or len({item['preview_id'] for item in pair}) != 2:
        raise ValueError('missing independent concurrent requests')
    for item in pair:
        if previews.get(item['preview_id']) != (item['event_id'],item['content_range']):
            raise ValueError('concurrent preview association mismatch')
    if result.get('audit_version',0) >= 3:
        sessions = [call['event'] for row in stages['preview_requests'] for call in row['calls']
                    if call['event']['decision'] != 'unavailable']
        for field in ('session_sha256','ear_sha256'):
            if len({event[field] for event in sessions}) != len(sessions):
                raise ValueError('proof session reused between calls')
        calls = {row['preview_id']:row['calls'] for row in stages['preview_requests']}
        left,right = [calls[item['preview_id']] for item in pair]
        if not any(max(a['event']['begin_ns'],b['event']['begin_ns']) <
                   min(a['event']['end_ns'],b['event']['end_ns']) for a in left for b in right):
            raise ValueError('no overlapping Trustee sessions')
    if not {stages[k]['event_id'] for k in ('approved_release','measurement_denied')} <= seen_events:
        raise ValueError('decision outside preview requests')


if __name__ == '__main__':
    verify_report(sys.argv[1])
    print('Verified Trustee evidence against archived AS/KBS records.')
