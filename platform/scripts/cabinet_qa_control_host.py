#!/usr/bin/env python3
"""SSH forced command on a dedicated Ubuntu QA KBS management host."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time


CONFIG = Path('/etc/cabinet-qa/control.json')


def handle(action, tee, run_id, preview_id, cfg, state):
    unit = cfg['unit']
    if not re.fullmatch(r'[a-zA-Z0-9_-]+\.service', unit) or tee != cfg['tee']:
        raise ValueError('wrong QA service')
    lease = state/'active'
    active = lease.read_text() if lease.exists() else None
    if active != run_id:
        if active or action != 'approved':
            raise ValueError('test instance unavailable')
        lease.write_text(run_id)
    audit = Path(cfg['audit_jsonl'])
    cursor = state/'cursor.json'
    if action == 'offline':
        subprocess.run(['systemctl','stop',unit],check=True)
        return
    if action.startswith('observe-'):
        decision = action.removeprefix('observe-')
        position = json.loads(cursor.read_text())
        if position['phase'] != ('approved' if decision == 'approved' else 'mismatch'):
            raise ValueError('wrong observation phase')
        with audit.open('rb') as stream:
            if os.fstat(stream.fileno()).st_ino != position['inode']:
                raise ValueError('audit rotated during phase')
            stream.seek(position['offset'])
            for raw in stream:
                event = json.loads(raw)
                if (event.get('guest_id') == cfg['guest_id'] and event.get('namespace') == 'platform-test'
                        and event.get('tee') == tee and event.get('decision') == decision
                        and preview_id and event.get('preview_id') == preview_id
                        and event.get('operation') == ('unwrap' if decision == 'approved' else 'guard')
                        and event.get('resource') == ('kek-v1' if decision == 'approved' else 'state-v1')):
                    refs = event['raw_records']
                    if set(refs) != {'as','kbs'}:
                        raise ValueError('both original records required')
                    for fingerprint in refs.values():
                        if not re.fullmatch('[a-f0-9]{64}', fingerprint):
                            raise ValueError('invalid original record digest')
                        blob = (Path(cfg['raw_archive'])/fingerprint).read_bytes()
                        if not blob or hashlib.sha256(blob).hexdigest() != fingerprint:
                            raise ValueError('original audit record digest mismatch')
                    fields = ('session_sha256','ear_sha256','operation','resource','preview_id','call_id','raw_records','namespace','tee','decision','measurement','reference','event_id')
                    print(json.dumps(dict(run_id=run_id, **{k:event[k] for k in fields})))
                    return
        raise ValueError('no matching authoritative audit record')
    subprocess.run(['systemctl','start',unit],check=True)
    command = [cfg['client'],'--url',cfg['url'],'--cert-file',cfg['ca_file'],'config',
               '--admin-token-file',cfg['admin_token_file'],'set-resource-policy','--policy-file',
               cfg['policies'][action]]
    for attempt in range(15):
        if subprocess.run(command, capture_output=True, timeout=5).returncode == 0:
            break
        time.sleep(1)
    else:
        raise ValueError('policy update failed')
    if action == 'restore':
        lease.unlink()
    else:
        stat = audit.stat()
        cursor.write_text(json.dumps(dict(phase=action,inode=stat.st_ino,offset=stat.st_size)))


def main():
    match = re.fullmatch(r'(approved|mismatch|offline|restore|observe-approved|observe-denied) (snp|tdx) ([0-9a-f]{32})(?: ([0-9a-f]{32}))?',
                         os.environ.get('SSH_ORIGINAL_COMMAND',''))
    if not match:
        raise SystemExit('invalid controller request')
    state = Path('/var/lib/cabinet-qa-control')
    with (state/'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        handle(*match.groups(), json.loads(CONFIG.read_text()), state)


if __name__ == '__main__':
    main()
