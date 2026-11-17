"""Run sample or hardware acceptance and retain a secret-free JSON verdict."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import ssl
import subprocess
import tempfile
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

STAGES = ('approved_release', 'measurement_denied', 'offline_denied', 'video_integrity', 'rollback_denied')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def reachable(config, tls=True):
    url = urlsplit(config['url'])
    with socket.create_connection((url.hostname, url.port or 443), timeout=5) as sock:
        if tls:
            with ssl.create_default_context(cafile=config['ca_file']).wrap_socket(sock, server_hostname=url.hostname):
                pass


def preflight(mode, file, hook):
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'requires x86_64 Linux guest')
    require(platform.freedesktop_os_release()['ID'] == 'ubuntu', 'requires Ubuntu')
    devices = ['/dev/sev-guest'] if mode == 'snp' else ['/dev/tdx_guest', '/dev/tdx-guest']
    direct = any(Path(d).is_char_device() and os.access(d, os.R_OK | os.W_OK) for d in devices)
    tsm = Path('/sys/kernel/config/tsm/report')
    require(direct or (tsm.is_dir() and os.access(tsm, os.W_OK)), 'attestation device or writable TSM report interface required')
    require(file and hook, 'supply --config and --controller')
    cfg = json.loads(Path(file).read_text())
    require(cfg['namespace'] == 'platform-test', 'dedicated platform-test namespace required')
    client = Path(cfg['client'])
    require(client.is_absolute() and os.access(client, os.X_OK), 'native client unavailable')
    with client.open('rb') as stream:
        require(stream.read(4) == b'\x7fELF', 'native ELF client required')
    require(hashlib.sha256(client.read_bytes()).hexdigest() == cfg['client_sha256'], 'client digest mismatch')
    url = urlsplit(cfg['url'])
    require(url.scheme == 'https' and url.hostname and not url.username and not url.password
            and not url.query and not url.fragment and url.path in ('','/'), 'invalid KBS HTTPS origin')
    require(Path(cfg['ca_file']).is_absolute(), 'absolute CA file required')
    require(Path(hook).is_absolute() and os.access(hook, os.X_OK), 'executable controller required')
    reachable(cfg)
    return {'interface': 'guest-device' if direct else 'configfs-tsm', 'client_sha256': cfg['client_sha256'],
            'kernel': platform.release()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['sample','snp','tdx'], default='sample')
    parser.add_argument('--config')
    parser.add_argument('--controller')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    path = args.report or Path('outputs') / ('cabinet-' + now.strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    result = dict(mode=args.mode, utc=now.isoformat(), result='incomplete', preflight='pending',
                  stages={s:'pending' for s in STAGES}, pending_field_checks=['host root isolation', 'physical power loss'])
    result['audit_version'] = 3
    result['stages']['concurrent_previews'] = 'pending'
    if args.mode == 'sample':
        result['stages']['preview_requests'] = 'pending'
        result['pending_field_checks'] += ['SNP guest acceptance', 'TDX guest acceptance']
    else:
        result['pending_field_checks'].append('other TEE family')
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents an interrupted rerun from looking like a past success.
    with path.open('x') as out:
        json.dump(result, out, indent=2)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            events = base/'stages'
            env = dict(os.environ, CABINET_QA_KBS='trustee' if args.mode == 'sample' else args.mode,
                       CABINET_QA_JUNIT=str(base/'tests.xml'), CABINET_QA_EVENTS=str(events))
            if args.mode == 'sample':
                archive = path.resolve().with_name(path.name+'.audit')
                archive.mkdir(mode=0o700)
                env['CABINET_QA_AUDIT'] = str(archive)
            if args.mode != 'sample':
                result['guest'] = preflight(args.mode, args.config, args.controller)
                env.update(CABINET_QA_GUEST_CONFIG=str(Path(args.config).resolve()), CABINET_QA_CONTROLLER=args.controller)
            result['preflight'] = 'passed' if args.mode != 'sample' else 'sample only'
            proc = subprocess.run(['bash','scripts/run_seaweed_key_migration.sh'], env=env)
            if events.exists():
                for line in events.read_text().splitlines():
                    stage, detail = json.loads(line)
                    result['stages'][stage] = detail
            require(proc.returncode == 0, 'acceptance command failed')
            cases = ET.parse(base/'tests.xml').findall('.//testcase')
            require(len(cases) == 1 and not any(cases[0].find(x) is not None for x in ('skipped','error','failure')),
                    'end-to-end case did not pass')
            require('pending' not in result['stages'].values(), 'missing acceptance evidence')
            if args.mode == 'sample':
                from cabinet_trustee_audit import verify_report
                verify_report(path, result)
                stages = result['stages']
                requests = {row['preview_id']: row['calls'] for row in stages['preview_requests']}
                left, right = [requests[item['preview_id']] for item in stages['concurrent_previews']]
                overlap, a, b = max(
                    ((min(x['event']['end_ns'], y['event']['end_ns']) -
                      max(x['event']['begin_ns'], y['event']['begin_ns']), x['call_id'], y['call_id'])
                     for x in left for y in right))
                result['concurrent_key_sessions'] = dict(call_ids=[a,b], overlap_ns=overlap)
            result['result'] = 'passed'
    except (OSError, ValueError, KeyError, ET.ParseError) as exc:
        result.update(result='failed', error=str(exc))
        if result['preflight'] == 'pending':
            result['preflight'] = 'failed'
    finally:
        with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False) as out:
            json.dump(result,out,indent=2)
        os.replace(out.name,path)
        print(f"Acceptance {result['result']}: {path.resolve()}")
    return int(result['result'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
