"""Seeded spatial sampling for views of an already verified planning snapshot."""
import hashlib
from pathlib import Path

import numpy as np

from . import coverage_core as core
from .planning_assessment import inside, planned_devices


def clouds(job):
    result, snapshot = job['result'], job['snapshot']
    settings = core.Volume.model_validate(snapshot['task']['volume'])
    scene = snapshot['scene']
    additions = dict(scene, devices=result['selected'])
    stages = ["before", "after"] + (["reference"] if "reference" in result else [])
    sampled = [[] for _ in stages]
    random = np.random.default_rng(20261116)
    focus = result['regions'][1]['bounds']
    for region in result['regions']:
        bounds, cell = region['bounds'], region['cell']
        shape = [round((bounds[i + 1] - bounds[i]) / cell) for i in (0, 2, 4)]
        count = int(np.prod(shape))
        # Sampling flat indices uniformly avoids the altitude aliasing of a regular stride.
        indices = np.sort(random.choice(count, size=min(3000, count), replace=False))
        points = np.array(bounds)[::2] + (np.column_stack(np.unravel_index(indices, shape)) + .5) * cell
        if region['region'] == 'wide':
            points = points[~inside(points, focus)]
        if not len(points):
            continue
        before = core.fields(scene, points, settings, snapshot['calibration'])
        after = core.fields(additions, points, settings, snapshot['calibration'], initial=before) if result['selected'] else before
        if result.get('updated_devices'):
            after = core.fields(dict(scene, devices=planned_devices(scene, result['selected'], result['updated_devices'])), points, settings, snapshot['calibration'])
        fields = [before, after]
        if 'reference' in result:
            reference = result['reference']
            if reference.get('updated_devices'):
                fields.append(core.fields(dict(scene, devices=planned_devices(scene, reference['selected'], reference['updated_devices'])), points, settings, snapshot['calibration']))
            else:
                fields.append(core.fields(dict(scene, devices=reference['selected']), points, settings, snapshot['calibration'], initial=before))
        for target, field in zip(sampled, fields):
            for index in np.flatnonzero(field['valid']):
                target.append([*points[index].tolist(), float(field['pd'][index]), float(field['sinr_db'][index]),
                               float(field['precision_m'][index]) if field['observable'][index] else None, cell])
    return dict(**dict(zip(stages, sampled)), seed=20261116,
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                sampling='每个分区固定种子均匀无放回抽样，模型使用原始输入；统计沿用全部体素验算')
