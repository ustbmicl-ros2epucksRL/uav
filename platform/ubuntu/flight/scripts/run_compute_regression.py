"""One-command local SITL/video/replay regression; leaves evidence on failure."""
import argparse
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse
import httpx

FLIGHT = Path(__file__).resolve().parents[1]


def run(args):
    output = Path(args.output).resolve() if args.output else FLIGHT.parents[2] / 'outputs' / 'compute-regression' / time.strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True, exist_ok=False)
    result = {'status': 'failed', 'started_at': time.time(), 'url': args.url, 'stages': [], 'browser': {'status': 'not_run'}}
    lock = None
    def stage(name, command, timeout=600, env=None):
        started = time.time()
        print('RUN', name, flush=True)
        with (output / (name + '.log')).open('w') as log:
            try:
                proc = subprocess.run(command, cwd=FLIGHT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, env=env)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = 124
                if name == 'sitl-flow' and (output / 'flow.json').exists():
                    partial = json.loads((output / 'flow.json').read_text())
                    owned = partial.get('execution', {}).get('execution_id')
                    with httpx.Client(base_url=args.url, trust_env=False, timeout=10) as client:
                        state = client.get('/api/sitl').json()
                        if owned and state.get('execution_binding', {}).get('execution_id') == owned and state.get('telemetry', {}).get('armed'):
                            reply = client.post('/api/sitl/commands', json={'command_id': uuid.uuid4().hex, 'execution_id': owned, 'command': 'land', 'expires_at': time.time()+20})
                            result['timeout_landing'] = {'http_status': reply.status_code, 'response': reply.json()}
                            for _ in range(60):
                                if not client.get('/api/sitl').json().get('telemetry', {}).get('armed'):
                                    result['timeout_landed'] = True
                                    break
                                time.sleep(1)
        result['stages'].append({'name': name, 'exit_code': code, 'duration_s': round(time.time() - started, 2)})
        if code:
            raise RuntimeError(f'{name} failed ({code}); see {name}.log')
    try:
        url = urlparse(args.url)
        if url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.scheme != 'http':
            raise ValueError('Only a local HTTP SITL service is supported')
        lock = open('/tmp/compute-regression-' + hashlib.sha256(args.url.encode()).hexdigest()[:12] + '.lock', 'w')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with httpx.Client(base_url=args.url, trust_env=False, timeout=10) as client:
            response = client.get('/api/sitl'); response.raise_for_status()
            state = response.json()
            if state.get('telemetry', {}).get('armed'):
                raise RuntimeError('SITL is armed: finish the existing flight before regression')
            response = client.get('/api/compute'); response.raise_for_status()
            if response.json().get('state') == 'running':
                raise RuntimeError('An existing compute run is active; regression will not replace it')
            response = client.post('/api/sitl/restart', timeout=45); response.raise_for_status()
        stage('sitl-flow', [sys.executable, 'scripts/test_process_sitl_flow.py', '--url', args.url, '--demo', '--report', str(output / 'flow.json')], 720)
        flow = json.loads((output / 'flow.json').read_text())
        result['task_id'] = flow['task_id']
        result['record_id'] = flow['result']['record_id']
        result['replay_url'] = args.url.rstrip('/') + '/?source=sitl&task=' + flow['task_id'] + '&record=' + result['record_id']
        stage('archive', ['node', 'scripts/test_compute_replay_archive.cjs', str(output / 'flow.json'), str(output / 'replay-checks.json')])
        env = dict(os.environ, COMPUTE_REPLAY_FLOW=str(output / 'flow.json'))
        stage('frontend', ['node', '--test', 'tests/test_compute_replay_events.cjs', 'tests/test_compute_topology.cjs', 'tests/test_compute_video_ui.cjs', 'tests/test_twin_ui.cjs'], env=env)
        result['short_transfer'] = json.loads((output / 'replay-checks.json').read_text())['short_transfer']
        stage('browser', [sys.executable, 'scripts/test_compute_browser.py', '--url', result['replay_url'], '--output', str(output)] + (['--headless'] if getattr(args, 'headless', False) else []), 300)
        result['counts'] = flow['runtime']['counts']
        result['status'] = 'passed'
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        if lock:
            lock.close()
        if (output / 'flow.json').exists():
            try:
                partial = json.loads((output / 'flow.json').read_text())
                result.setdefault('task_id', partial.get('task_id'))
                result.setdefault('execution_id', partial.get('execution', {}).get('execution_id'))
            except (ValueError, OSError):
                pass
        if (output / 'browser.json').exists():
            result['browser'] = json.loads((output / 'browser.json').read_text())
        result['finished_at'] = time.time()
        (output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        body = '<h1>云边端 SITL 回归：' + result['status'] + '</h1><pre>' + html.escape(json.dumps(result, ensure_ascii=False, indent=2)) + '</pre>'
        if result.get('replay_url'):
            body += '<p><a href="' + html.escape(result['replay_url'], quote=True) + '">打开本次任务回放</a></p>'
        body += '<p>网络为应用层仿真；检测使用测试录像，非真实机载画面。</p>'
        for file in sorted(output.iterdir()):
            if file.suffix == '.png':
                body += '<p>' + html.escape(file.name) + '</p><img style="max-width:100%" src="' + html.escape(file.name) + '">'
            if file.suffix in ('.json', '.log'):
                body += '<p><a href="' + html.escape(file.name) + '">' + html.escape(file.name) + '</a></p>'
        (output / 'report.html').write_text('<!doctype html><meta charset="utf-8"><title>SITL 回归报告</title><style>body{max-width:1000px;margin:40px auto;font:16px sans-serif}pre{white-space:pre-wrap;background:#eef3f7;padding:20px}</style>' + body)
        print(result['status'].upper(), output / 'report.html', flush=True)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8773')
    parser.add_argument('--headless', action='store_true', help='Use real Chrome without a visible window')
    parser.add_argument('--output', help='New, non-existing evidence directory')
    sys.exit(run(parser.parse_args()))
