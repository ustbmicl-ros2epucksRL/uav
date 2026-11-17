"""Pure snapshot calculation, also executable in an isolated Python process."""
import hashlib
import importlib.metadata
import json
import os
import threading
import time
from pathlib import Path
import platform
import sys

from . import engineering
from .coverage_core import MODEL_VERSION


def provenance():
    root = Path(__file__).parent
    names = ['job_compute.py', 'engineering.py', 'coverage_core.py', 'planning_assessment.py', 'precision_planning.py', 'low_altitude_planning.py', 'low_altitude_joint.py']
    names += [str(p.relative_to(root)) for p in sorted((root / 'coverage_sub1').glob('*.py'))]
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return {
        'model_version': MODEL_VERSION, 'source_sha256': digest.hexdigest(),
        'python': platform.python_version(),
        'packages': {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'pydantic')},
        'random_seed': None,
        'randomness': 'deterministic numerical calculation; IDs and timestamps are not numerical outputs',
        'coordinate_frame': 'local_ENU_m', 'evidence_type': 'synthetic_model',
    }


def validate(kind, parameters):
    if kind == 'assessment':
        from .planning_assessment import AssessmentRequest
        return AssessmentRequest.model_validate(parameters)
    if kind == 'volume':
        return engineering.Volume.model_validate(parameters)
    if kind != 'plan':
        raise ValueError('未知计算类型')
    request = engineering.ConstrainedPlan.model_validate(parameters)
    if len(engineering.grid(request.volume)) > 15000:
        raise ValueError('子集方案比较单次最多 15000 网格，请缩小区域或增大网格')
    return request


def calculate(snapshot):
    if snapshot['provenance'] != provenance():
        raise ValueError('算法源码或运行环境已改变，无法按原版本重跑；请提交新的计算')
    kind = snapshot['kind']
    request = validate(kind, snapshot['parameters'])
    scene, calibration = snapshot['scene'], snapshot['calibration']
    if kind == 'assessment':
        from .planning_assessment import calculate as assess
        return assess(snapshot)
    elif kind == 'volume':
        result = engineering.volume(scene, request, calibration)
    else:
        result = engineering.constrained_plan(scene, request, calibration)
        result['calibration_id'] = (calibration or {}).get('id')
    return result


if __name__ == '__main__':
    # Prevent an orphan numerical process after an abrupt web-server exit.
    if os.getenv('SENSE_JOB_PARENT_PID'):
        parent_pid = int(os.environ['SENSE_JOB_PARENT_PID'])
        def watch_parent():
            while True:
                if os.getppid() != parent_pid:
                    os._exit(75)
                time.sleep(.5)
        threading.Thread(target=watch_parent, daemon=True).start()
    # Only stdin/stdout files are used; this subprocess never writes the live database.
    json.dump(calculate(json.load(sys.stdin)), sys.stdout, ensure_ascii=False, allow_nan=False)
