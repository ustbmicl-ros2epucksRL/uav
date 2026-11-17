"""Durable local queue. One scheduler per data directory; one isolated compute process.

A filesystem lock coordinates the standalone and mounted services on the same host.
Queued jobs survive restarts; interrupted calculations require an explicit retry.
"""
import fcntl
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from fastapi import HTTPException
from . import store
from .job_compute import provenance, validate
from .scenes import SCENES, seed

TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
logger = logging.getLogger('uvicorn.error')


def decode(row, full=True):
    job = dict(row)
    if full:
        job['snapshot'] = json.loads(job['snapshot'])
        job['result'] = json.loads(job['result']) if job['result'] else None
    else:
        job.pop('snapshot', None)
        job.pop('result', None)
    return job


def get(job_id):
    with store.connection() as conn:
        row = conn.execute('SELECT * FROM computation_jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '计算任务不存在')
    return decode(row)


def submit(scene_id, kind, parameters, parent_id=None, snapshot=None):
    if scene_id not in {s['id'] for s in SCENES}:
        raise HTTPException(404, '场景不存在')
    parameters = validate(kind, parameters).model_dump()
    version = provenance()
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT count(*) FROM computation_jobs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
            raise HTTPException(429, '计算队列已满，请等待已有任务完成')
        if snapshot is None:
            def state(key, default):
                row = conn.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
                return json.loads(row[0]) if row else default
            snapshot = {
                'schema_version': 1, 'kind': kind, 'parameters': parameters,
                'scene': state(f'scene:{scene_id}', seed(scene_id)),
                'calibration': state(f'calibration:{scene_id}', None),
                'provenance': version,
            }
        elif snapshot['provenance'] != version:
            raise HTTPException(409, '算法源码或运行环境已改变；请按当前场景新建计算')
        job_id = uuid.uuid4().hex
        conn.execute('INSERT INTO computation_jobs (id,scene,kind,status,created_at,snapshot,parent_id) VALUES (?,?,?,?,?,?,?)',
                     (job_id, scene_id, kind, 'queued', store.now(), json.dumps(snapshot, ensure_ascii=False, allow_nan=False), parent_id))
    manager.start()
    return get(job_id)


def cancel(job_id):
    get(job_id)
    with store.connection() as conn:
        conn.execute("UPDATE computation_jobs SET status='cancelled', finished_at=? WHERE id=? AND status IN ('queued','running')", (store.now(), job_id))
    return get(job_id)


def retry(job_id):
    job = get(job_id)
    if job['status'] not in TERMINAL:
        raise HTTPException(409, '任务尚未结束')
    return submit(job['scene'], job['kind'], job['snapshot']['parameters'], job_id, job['snapshot'])


def publish(job_id):
    """Explicit promotion to legacy workbench artifacts; never changes devices."""
    job = get(job_id)
    if job['kind'] == 'assessment':
        from .acceptance_api import publish_assessment
        return publish_assessment(job_id)
    if job['status'] != 'succeeded':
        raise HTTPException(409, '仅成功任务可载入工作台')
    if job['snapshot']['provenance'] != provenance():
        raise HTTPException(409, '算法源码或运行环境已改变，请重新计算')
    scene_id = job['scene']
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        def state(key, default):
            row = conn.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default
        if (state(f'scene:{scene_id}', seed(scene_id)) != job['snapshot']['scene'] or
                state(f'calibration:{scene_id}', None) != job['snapshot']['calibration']):
            raise HTTPException(409, '场景或校正已改变；结果保留于历史，请按当前配置重新计算')
        key = 'volume' if job['kind'] == 'volume' else 'engineering-plan'
        result = {**job['result'], 'job_id': job_id}
        conn.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (f'{key}:{scene_id}', json.dumps(result, ensure_ascii=False, allow_nan=False)))
    return result


class JobManager:
    def __init__(self):
        self.guard = threading.Lock()
        self.thread = None
        self.stopping = threading.Event()

    def start(self):
        with self.guard:
            if self.thread and self.thread.is_alive():
                return
            self.stopping.clear()
            self.thread = threading.Thread(target=self.loop, name='sensing-job-scheduler', daemon=True)
            self.thread.start()

    def stop(self):
        with self.guard:
            self.stopping.set()
            if self.thread:
                self.thread.join(timeout=10)
                if self.thread.is_alive():
                    raise RuntimeError('计算进程未按时停止')
                self.thread = None

    def loop(self):
        # The losing scheduler retries, allowing takeover after the owner exits.
        while not self.stopping.is_set():
            try:
                with (store.DATA / 'computation-worker.lock').open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        self.stopping.wait(.5)
                        continue
                    with store.connection() as conn:
                        conn.execute("UPDATE computation_jobs SET status='interrupted', finished_at=?, error=? WHERE status='running'",
                                     (store.now(), '上次计算进程退出，可按原输入重试'))
                    while not self.stopping.is_set():
                        with store.connection() as conn:
                            conn.execute('BEGIN IMMEDIATE')
                            row = conn.execute("SELECT id FROM computation_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                            if row:
                                conn.execute("UPDATE computation_jobs SET status='running', started_at=? WHERE id=?", (store.now(), row['id']))
                        if row:
                            self.run_one(row['id'])
                        else:
                            self.stopping.wait(.2)
            except Exception:
                logger.exception('computation scheduler failed')
                self.stopping.wait(1)

    def run_one(self, job_id):
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix='sense-job-') as directory:
                from pathlib import Path
                root = Path(directory)
                (root / 'input.json').write_text(json.dumps(get(job_id)['snapshot'], ensure_ascii=False), encoding='utf-8')
                env = {**os.environ, 'PYTHONPATH': str(store.ROOT), 'PYTHONDONTWRITEBYTECODE': '1',
                       'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
                       'SENSE_JOB_PARENT_PID': str(os.getpid())}
                with (root / 'input.json').open('rb') as source, (root / 'result.json').open('wb') as output, (root / 'error.txt').open('wb') as errors:
                    process = subprocess.Popen([sys.executable, '-m', 'app.job_compute'], cwd=store.ROOT,
                                               stdin=source, stdout=output, stderr=errors, env=env)
                    start = time.monotonic()
                    snapshot = get(job_id)['snapshot']
                    default_timeout = '1800' if snapshot.get('parameters', {}).get('strategy') == 'precision' else '600'
                    timeout = max(1, float(os.getenv('SENSE_JOB_TIMEOUT', default_timeout)))
                    while process.poll() is None:
                        if self.stopping.wait(.1):
                            raise InterruptedError('服务停止，计算中断；可按原输入重试')
                        if get(job_id)['status'] == 'cancelled':
                            return
                        if time.monotonic() - start > timeout:
                            raise TimeoutError(f'计算超过 {timeout:g} 秒，可调整参数后重新提交')
                if process.returncode:
                    # Store the final exception only; paths/tracebacks stay out of API payloads.
                    lines = (root / 'error.txt').read_text(encoding='utf-8', errors='replace').strip().splitlines()
                    raise RuntimeError(lines[-1][:500] if lines else '计算子进程异常退出')
                result = json.loads((root / 'result.json').read_text(encoding='utf-8'))
                with store.connection() as conn:
                    conn.execute("UPDATE computation_jobs SET status='succeeded', result=?, finished_at=? WHERE id=? AND status='running'",
                                 (json.dumps(result, ensure_ascii=False, allow_nan=False), store.now(), job_id))
        except Exception as error:
            status = 'interrupted' if isinstance(error, InterruptedError) else 'failed'
            with store.connection() as conn:
                conn.execute("UPDATE computation_jobs SET status=?, error=?, finished_at=? WHERE id=? AND status='running'",
                             (status, str(error)[:500], store.now(), job_id))
            logger.warning('computation %s: %s', job_id, error)
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


manager = JobManager()
