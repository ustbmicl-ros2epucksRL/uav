"""Durable actual execution evidence kept beside flight records."""
import json
from .flight_records import connect


def save(snapshot, batch=None):
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS compute_snapshots (runtime_id TEXT, at REAL, record_id TEXT, body TEXT, PRIMARY KEY(runtime_id,at))')
        db.execute('INSERT OR REPLACE INTO compute_snapshots VALUES (?,?,?,?)',
                   (snapshot['runtime_id'], snapshot['at'], snapshot['record_id'], json.dumps(snapshot, allow_nan=False)))
        if batch:
            db.execute('CREATE TABLE IF NOT EXISTS compute_batches (id TEXT PRIMARY KEY, record_id TEXT, body TEXT)')
            db.execute('INSERT OR REPLACE INTO compute_batches VALUES (?,?,?)',
                       (batch['id'], snapshot['record_id'], json.dumps(batch, allow_nan=False)))


def history(record_id):
    with connect() as db:
        if not db.execute("SELECT name FROM sqlite_master WHERE name='compute_snapshots'").fetchone():
            return []
        return [json.loads(r[0]) for r in db.execute('SELECT body FROM compute_snapshots WHERE record_id=? ORDER BY at', (record_id,))]


def batches(record_id):
    with connect() as db:
        if not db.execute("SELECT name FROM sqlite_master WHERE name='compute_batches'").fetchone():
            return []
        return [json.loads(r[0]) for r in db.execute('SELECT body FROM compute_batches WHERE record_id=? ORDER BY id', (record_id,))]
