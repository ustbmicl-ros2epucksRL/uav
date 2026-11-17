"""Local, persisted regression jobs and read-only evidence browsing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix='/api/stability', tags=['stability'])
FLIGHT = Path(__file__).resolve().parents[1]
GUARD = threading.Lock()
PROCESSES = {}


def root():
    path = Path(os.environ.get('FLIGHT_STABILITY_DIR', str(FLIGHT.parents[2] / 'outputs/compute-stability')))
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def directory(ident):
    if not ident or Path(ident).name != ident or ident in ('.', '..'):
        raise HTTPException(400, '无效回归编号')
    path = root() / ident
    if not path.is_dir() or path.is_symlink():
        raise HTTPException(404, '回归记录不存在')
    return path


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def describe(path):
    meta = read(path / 'job.json', {})
    summary = read(path / 'summary.json', {})
    process = PROCESSES.get(path.name)
    running = process.poll() is None if process else alive(meta.get('pid'))
    legacy = 'failed' if summary.get('failed', 0) else 'passed' if summary.get('passed', 0) else 'unknown'
    state = summary.get('status', meta.get('status', legacy))
    if state == 'running' and not running:
        state = 'interrupted'
    rounds = []
    count = summary.get('requested_rounds', meta.get('rounds', len(summary.get('rounds', []))))
    for index in range(1, count + 1):
        folder = path / f'round-{index}'
        report = read(folder / 'report.json', {})
        saved = next((r for r in summary.get('rounds', []) if r.get('round') == index), {})
        log = path / f'round-{index}-runner.log'
        lines = log.read_text(errors='replace').splitlines()[-20:] if log.exists() else []
        stages = [line[4:] for line in lines if line.startswith('RUN ')]
        status = report.get('status', saved.get('status', ('running' if state == 'running' else 'interrupted') if log.exists() else 'not_run'))
        if status == 'running' and state != 'running':
            status = 'interrupted'
        partial = read(folder / 'flow.json', {}) if not report.get('task_id') and not saved.get('task_id') and folder.exists() else {}
        artifacts = []
        candidates = [log] + (list(folder.glob('*')) if folder.exists() else [])
        for file in sorted(candidates):
            if file.is_file() and not file.is_symlink() and file.suffix in ('.log', '.json', '.html', '.png'):
                artifacts.append({'name': file.name, 'path': str(file.relative_to(path))})
        browser = report.get('browser', {})
        rounds.append({'round': index, 'status': status, 'task_id': report.get('task_id', saved.get('task_id', partial.get('task_id'))), 'stage': stages[-1] if stages else None, 'duration_s': saved.get('duration_s', round(report.get('finished_at', 0)-report.get('started_at', 0), 2) if report.get('finished_at') else None), 'reason': browser.get('error') or report.get('error') or saved.get('failure_reason'), 'warnings': browser.get('warnings', []), 'artifacts': artifacts})
    ui = read(path/'platform-ui/acceptance.json') or read(path/'platform-ui/progress.json')
    if not ui and meta.get('page_acceptance'):
        ui = {'status': 'running', 'step': '启动验收浏览器'}
    if ui:
        worker = PROCESSES.get(path.name + '-ui')
        worker_alive = worker.poll() is None if worker else alive(meta.get('ui_pid'))
        if ui.get('status') == 'running' and meta.get('page_acceptance') and not worker_alive:
            ui = dict(ui, status='failed', error='页面验收进程已退出，请查看验收日志')
        ui = dict(ui, evidence=[str(f.relative_to(path)) for f in sorted((path/'platform-ui').glob('*'))
                                if f.is_file() and f.suffix in ('.log', '.png')])
    return {'retry_acceptance': read(path/'retry-acceptance.json'), 'dual_exercise': meta.get('dual_exercise', any(str(r['task_id']).startswith('DUAL-') for r in rounds)), 'cross_platform': meta.get('cross_platform', False), 'retry_of': meta.get('retry_of'), 'page_acceptance': ui, 'id': path.name, 'status': state, 'requested_rounds': count, 'started_at': summary.get('started_at', meta.get('created_at', path.stat().st_mtime)), 'rounds': rounds, 'report_available': (path/'summary.html').exists(), 'launcher_log': (path/'launcher.log').exists(), 'reason': meta.get('error') or ('回归进程已退出，请查看保留的日志' if state == 'interrupted' else None)}


@router.get('')
def catalogue():
    paths = [p for p in root().iterdir() if p.is_dir() and not p.is_symlink() and ((p/'summary.json').exists() or (p/'job.json').exists())]
    def order(path):
        summary = read(path/'summary.json', {})
        meta = read(path/'job.json', {})
        return summary.get('started_at') or meta.get('created_at') or path.stat().st_mtime

    paths.sort(key=order, reverse=True)
    return {'items': [describe(p) for p in paths[:50]]}


class Start(BaseModel):
    dual_retry_acceptance: bool = False
    fault_round: int = Field(default=0, ge=0, le=20)
    dual_exercise: bool = False
    cross_platform: bool = False
    page_acceptance: bool = False
    rounds: int = Field(default=3, ge=1, le=20)


@router.post('', status_code=202)
def start(body: Start):
    return launch(body)


@router.post('/{ident}/retry', status_code=202)
def retry(ident: str):
    return launch(Start(page_acceptance=True, rounds=3), retry_of=ident)


@router.post('/{ident}/rounds/{index}/retry', status_code=202)
def retry_round(ident: str, index: int):
    source = describe(directory(ident))
    return launch(Start(cross_platform=source['cross_platform'], dual_exercise=source['dual_exercise'], rounds=source['requested_rounds']), retry_of=ident, retry_index=index)


def launch(body, retry_of=None, retry_index=None):
    from . import sitl, compute_runtime
    with GUARD:
        if body.dual_retry_acceptance:
            if body.rounds!=3 or not body.dual_exercise or body.page_acceptance or body.cross_platform:
                raise HTTPException(422,'双机失败重试验收固定执行三轮')
            body.fault_round=2
        if retry_of is not None:
            source = describe(directory(retry_of))
            if retry_index is not None:
                if (not (source['cross_platform'] or source['dual_exercise']) or source['status'] != 'failed'
                        or not any(r['round'] == retry_index and r['status'] == 'failed' for r in source['rounds'])):
                    raise HTTPException(409, '仅可重试失败的演练轮次')
                if detail(retry_of)['retries']:
                    raise HTTPException(409, '该次失败已有重试记录，请打开关联记录')
            elif (source.get('page_acceptance') or {}).get('status') != 'failed':
                raise HTTPException(409, '仅失败的页面验收可以重试')
        if body.fault_round and (not (body.cross_platform or body.dual_exercise) or body.fault_round > body.rounds):
            raise HTTPException(422, '故障轮次超出跨平台测试范围')
        if body.page_acceptance and body.rounds != 3:
            raise HTTPException(422, '页面验收固定执行三轮')
        if any(item['status'] == 'running' or (item.get('page_acceptance') or {}).get('status') == 'running' for item in catalogue()['items']):
            raise HTTPException(409, '已有稳定性回归正在执行')
        if sitl.STATE.get('telemetry', {}).get('armed') or (compute_runtime.snapshot() or {}).get('state') == 'running':
            raise HTTPException(409, '请先结束当前飞行和计算任务')
        url = os.environ.get('FLIGHT_BASE_URL', 'http://127.0.0.1:8772')
        if urlparse(url).hostname not in ('localhost', '127.0.0.1', '::1') or urlparse(url).scheme != 'http':
            raise HTTPException(409, '回归仅允许本机 HTTP 测试服务')
        ident = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        path = root()/ident
        # Suite owns creation of its output directory; metadata is written after launch.
        command = [sys.executable, str(FLIGHT/'scripts/run_compute_stability.py'), '--rounds', str(body.rounds), '--url', url, '--output', str(path)]
        if body.dual_exercise:
            if urlparse(url).port != 8772 or body.cross_platform or body.page_acceptance:
                raise HTTPException(422,'双机演练需从 8772 独立启动')
            command = [sys.executable, '-u', str(FLIGHT/'scripts/run_dual_stability.py'), str(path), str(body.rounds), str(body.fault_round), str(retry_index or 1), retry_of or '']
        if body.cross_platform:
            if urlparse(url).port != 8772 or body.page_acceptance:
                raise HTTPException(422, '跨平台回归需从 8772 启动')
            command = [sys.executable, '-u', str(FLIGHT/'scripts/run_cross_platform.py'), str(path), str(body.rounds), str(body.fault_round), str(retry_index or 1), retry_of or '']
        if body.page_acceptance:
            command.insert(2, '--headless')
        log = root()/(ident+'.launcher.log')
        with log.open('w') as stream:
            process = subprocess.Popen(command, cwd=FLIGHT, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        PROCESSES[ident] = process
        for _ in range(100):
            if path.exists() or process.poll() is not None:
                break
            time.sleep(.02)
        if not path.exists():
            process.terminate() if process.poll() is None else None
            process.wait(timeout=5)
            path.mkdir(exist_ok=True)
            log.rename(path/'launcher.log')
            (path/'job.json').write_text(json.dumps({'status':'failed','rounds':body.rounds,'created_at':time.time(),'error':'回归进程未启动，请查看启动日志'}))
            raise HTTPException(500, '回归进程未启动，失败记录和启动日志已保留')
        log.rename(path/'launcher.log')
        meta = {'dual_exercise': body.dual_exercise, 'cross_platform': body.cross_platform, 'status': 'running', 'pid': process.pid, 'rounds': body.rounds, 'created_at': time.time()}
        if retry_of:
            meta['retry_of'] = retry_of
        if body.page_acceptance:
            folder = path/'platform-ui'
            folder.mkdir()
            meta['page_acceptance'] = True
            with (folder/'acceptance.log').open('w') as stream:
                try:
                    worker = subprocess.Popen([sys.executable, '-u', str(FLIGHT/'scripts/test_stability_ui.py'),
                                               '--url', url, '--job', ident], cwd=FLIGHT,
                                              stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                    meta['ui_pid'] = worker.pid
                    PROCESSES[ident + '-ui'] = worker
                except OSError as exc:
                    stream.write(str(exc))
        (path/'job.json').write_text(json.dumps(meta))
        if body.dual_retry_acceptance:
            with (path/'retry-acceptance.log').open('w') as stream:
                worker=subprocess.Popen([sys.executable,'-u',str(FLIGHT/'scripts/test_cross_round_retry_ui.py'),
                                         '--dual','--job',ident,'--root',str(root())],
                                        stdout=stream,stderr=subprocess.STDOUT,cwd=FLIGHT,start_new_session=True)
                PROCESSES[ident+'-acceptance']=worker
        if retry_index is not None:
            report = directory(retry_of)/'summary.html'
            updated = report.with_suffix('.new')
            updated.write_text(report.read_text() + f'<p><a href="/api/stability/{ident}/files/summary.html">第 {retry_index} 轮重试及后续轮次的统一报告</a></p>')
            updated.replace(report)
        return describe(path)


@router.get('/{ident}')
def detail(ident: str):
    result = describe(directory(ident))
    result['retries'] = sorted(p.name for p in root().iterdir() if p.is_dir() and not p.is_symlink()
                               and read(p/'job.json', {}).get('retry_of') == ident)
    return result


@router.get('/{ident}/files/{name:path}')
def evidence(ident: str, name: str):
    base = directory(ident)
    path = (base/name).resolve()
    if not path.is_relative_to(base) or not path.is_file() or path.suffix not in ('.json', '.log', '.html', '.png'):
        raise HTTPException(404, '证据文件不存在')
    media = {'.log':'text/plain', '.json':'application/json', '.html':'text/html', '.png':'image/png'}[path.suffix]
    return FileResponse(path, media_type=media)
