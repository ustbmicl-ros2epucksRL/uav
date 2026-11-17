"""Capture/verify durable archives across restarts or new tasks; read-only HTTP."""
import argparse
import hashlib
import json
from pathlib import Path
import httpx

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8772')
parser.add_argument('--baseline', required=True)
parser.add_argument('--capture', action='store_true')
args = parser.parse_args()
with httpx.Client(base_url=args.url, trust_env=False, timeout=30) as client:
    result = {}; offset = 0
    while True:
        response = client.get('/api/sitl/records', params={'limit': 100, 'offset': offset})
        response.raise_for_status(); page = response.json()
        for item in page['items']:
            response = client.get('/api/sitl/records/' + item['id'])
            response.raise_for_status(); record = response.json()
            if item['status'] != 'completed':
                continue
            payload = {key: record[key] for key in ['anchor', 'patrol', 'track']}
            result[item['id']] = {'samples': len(record['track']), 'sha256': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}
        offset += len(page['items'])
        if offset >= page['total']:
            break
    assert result, 'No completed archive is available for validation'
    if args.capture:
        Path(args.baseline).write_text(json.dumps(result, indent=2))
        print('Captured', len(result), 'completed records')
    else:
        expected = json.loads(Path(args.baseline).read_text())
        for ident, value in expected.items():
            assert result.get(ident) == value, f'Archive changed or disappeared: {ident}'
        print('PASS:', len(expected), 'completed records preserved across restart/new task')
