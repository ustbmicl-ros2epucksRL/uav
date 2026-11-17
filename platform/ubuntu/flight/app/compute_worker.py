"""Fixed local video-detection workers plus isolated synthetic test kernels.

Production dispatch selects video_workload; synthetic kernels support fast scheduler tests only.
No user supplied code, paths, commands or network destinations are executed.
"""
import hashlib
import json
import math
import os
import time

DEPENDENCIES = {'preprocess': [], 'detect': ['preprocess'], 'segment': ['preprocess'],
                'fusion': ['detect', 'segment'], 'alert': ['fusion']}


def execute(task, inputs, position):
    if task == 'preprocess':
        # Deterministic 128x128 synthetic intensity image keyed to observed ENU.
        x, y, z = position
        pixels = [round((math.sin(i * .017 + x * .001) + math.cos(i * .009 + y * .001) + 2) / 4, 6)
                  for i in range(128 * 128)]
        return {'pixels': pixels, 'width': 128, 'height': 128, 'altitude_m': z, 'synthetic': True}
    if task == 'detect':
        pixels = inputs['preprocess']['pixels']
        count = sum(v > .85 for v in pixels)
        return {'bright_pixels': count, 'ratio': count / len(pixels), 'synthetic': True}
    if task == 'segment':
        pixels = inputs['preprocess']['pixels']
        histogram = [0] * 8
        for v in pixels:
            histogram[min(7, int(v * 8))] += 1
        return {'histogram': histogram, 'pixels': len(pixels), 'synthetic': True}
    if task == 'fusion':
        return {'bright_ratio': inputs['detect']['ratio'], 'histogram': inputs['segment']['histogram'], 'synthetic': True}
    if task == 'alert':
        return {'anomaly': inputs['fusion']['bright_ratio'] > .08,
                'bright_ratio': inputs['fusion']['bright_ratio'],
                'label': '合成像素阈值结果，非真实目标识别', 'synthetic': True}
    raise ValueError('Unsupported fixed workload')


def worker(node_id, inbox, outbox):
    while True:
        job = inbox.get()
        if job is None:
            return
        started = time.time(); cpu = time.process_time(); tick = time.perf_counter()
        result = {'token': job['token'], 'node': node_id, 'pid': os.getpid(), 'started_at': started}
        try:
            if job.get('fault') == 'crash':
                os._exit(17)
            if job.get('fault') == 'timeout':
                time.sleep(60)  # Explicit fault injection only, never normal latency emulation.
            if job.get('fault') == 'fail':
                raise RuntimeError('显式注入的一次工作进程任务失败')
            if job.get('workload') == 'video':
                from .video_workload import execute as run
            else:
                run = execute
            output = run(job['task'], job['inputs'], job['position'])
            encoded = json.dumps(output, sort_keys=True).encode()
            result.update(status='completed', output=output, output_bytes=len(encoded),
                          sha256=hashlib.sha256(encoded).hexdigest())
        except Exception as exc:
            result.update(status='failed', error=str(exc))
        result.update(finished_at=time.time(), compute_s=time.perf_counter()-tick,
                      cpu_s=time.process_time()-cpu)
        outbox.put(result)
