"""Read compact summaries of agent-run archives without changing their execution state."""
import json
import re
from functools import lru_cache
from typing import Literal, get_args

RunStatus = Literal['running', 'completed', 'failed', 'cancelled', 'interrupted']
Workflow = Literal['research', 'platform']
RUN_ID = re.compile(r'[a-f0-9]{32}')


@lru_cache(maxsize=2048)
def _summary(path, file_version):
    # The runner publishes new versions by atomic replace. Cache only list fields,
    # never the full snapshot, model report or tool results.
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('id') != path.stem or data.get('status') not in get_args(RunStatus):
        raise ValueError('Invalid run header')
    request = data.get('request') or {}
    if not isinstance(request, dict):
        raise ValueError('Invalid run request')
    workflow = request.get('workflow', 'research')  # Original records predate platform diagnostics.
    if workflow not in get_args(Workflow):
        raise ValueError('Invalid workflow')
    fields = {name: request.get(name) for name in ('question', 'scene', 'task_id', 'mode')}
    if fields['question'] is not None and not isinstance(fields['question'], str):
        raise ValueError('Invalid question')
    for name in ('created_at', 'updated_at', 'stage', 'model'):
        if data.get(name) is not None and not isinstance(data[name], str):
            raise ValueError('Invalid run metadata')
    steps = data.get('steps')
    trace = data.get('trace')
    return {
        'id': path.stem, 'request': {**fields, 'workflow': workflow}, 'status': data['status'],
        'stage': data.get('stage'), 'created_at': data.get('created_at'), 'updated_at': data.get('updated_at'),
        'model': data.get('model'), 'step_count': len(steps) if isinstance(steps, list) else None,
        'tool_count': len(trace) if isinstance(trace, list) else None,
    }


def _load(path):
    stat = path.stat()
    return _summary(path, (stat.st_ino, stat.st_mtime_ns, stat.st_size))


def query(directory, jobs, lifecycle_lock, *, q, status, workflow, offset, limit):
    counts = {
        'total': 0, 'by_status': dict.fromkeys(get_args(RunStatus), 0),
        'by_workflow': dict.fromkeys(get_args(Workflow), 0), 'unreadable': 0,
    }
    needle = q.strip().casefold()
    matches = []
    for path in directory.glob('*.json'):
        if not RUN_ID.fullmatch(path.stem):
            continue
        try:
            row = _load(path)
            if row['status'] == 'running' and row['id'] not in jobs:
                # Recheck the file after a finish or a concurrent start. Opening the
                # management page must not rewrite an orphaned historical archive.
                with lifecycle_lock:
                    row = _load(path)
                    if row['status'] == 'running' and row['id'] not in jobs:
                        row = {**row, 'status': 'interrupted', 'stage': '服务重启，任务已中断'}
        except (OSError, ValueError):
            counts['unreadable'] += 1
            continue
        state = row['status']
        kind = row['request']['workflow']
        counts['total'] += 1
        counts['by_status'][state] += 1
        counts['by_workflow'][kind] += 1
        if (status and status != state) or (workflow and workflow != kind):
            continue
        question = row['request']['question'] or ''
        if needle and needle not in question.casefold() and needle not in row['id']:
            continue
        matches.append(row)
    matches.sort(key=lambda row: (row['created_at'] or '', row['id']), reverse=True)
    return {'runs': matches[offset:offset + limit], 'total': len(matches), 'offset': offset,
            'limit': limit, 'summary': counts}
