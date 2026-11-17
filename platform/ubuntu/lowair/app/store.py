"""SQLite transactions serialize reservations across service workers."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def seed():
    resources = {}
    for i in (1, 2):
        for kind, prefix, name in [('airport', 'AP', '起降点'), ('pilot', 'P', '飞手'), ('payload', 'PL', '光电吊舱' if i == 1 else '报文终端'), ('vehicle', 'UAV', '无人机')]:
            ident = f'{prefix}-{i:02d}'
            resources[ident] = dict(id=ident, kind=kind, name=f'{name} {i}', enabled=True, revision=1)
        resources[f'UAV-{i:02d}'].update(airport_id=f'AP-{i:02d}', pilot_id=f'P-{i:02d}', payload_id=f'PL-{i:02d}')
    return dict(resources=resources, applications={}, tasks={}, validations={}, events=[], seq=0)


@contextmanager
def transaction(write=True):
    path = Path(os.environ.get('LOWAIR_DATA_DIR', str(Path(__file__).resolve().parents[1] / 'data'))) / 'service.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        row = db.execute('SELECT body FROM state WHERE id=1').fetchone()
        state = json.loads(row[0]) if row else seed()
        yield state
        if write:
            db.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (json.dumps(state, ensure_ascii=False, allow_nan=False),))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
