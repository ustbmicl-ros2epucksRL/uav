"""Durable SITL task archives; snapshots never control a vehicle."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix='/api/sitl/records')
BOOT = uuid.uuid4().hex


@contextmanager
def connect():
    directory = Path(os.environ.get('FLIGHT_RECORDS_DIR', os.environ.get('FLIGHT_DATA_DIR', Path(__file__).resolve().parents[1] / 'data')))
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / 'flight-records.sqlite3', timeout=10)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS flights (id TEXT PRIMARY KEY, created REAL, updated REAL, boot TEXT, status TEXT, body TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS frames (flight_id TEXT, at REAL, body TEXT, PRIMARY KEY(flight_id, at))')
        with db:
            yield db
    finally:
        db.close()


def checkpoint(state, force=False, closing=None):
    ident = state.get('record_id')
    if not ident:
        return
    now = time.time()
    if not force and now - state.get('_record_saved_at', 0) < 5:
        return
    status = state.get('patrol', {}).get('status', 'recording')
    if closing and status != 'completed':
        status = closing
    body = dict(source='ardupilot-sitl', record_id=ident, anchor=copy.deepcopy(state.get('anchor')),
                patrol=copy.deepcopy(state.get('patrol')), receipts=list(state.get('receipts', {}).values()),
                algorithm=copy.deepcopy(state.get('execution_binding',{}).get('algorithm')),
                video_evidence=None, business_task_id=state.get('execution_binding',{}).get('task_id'),
                execution_id=state.get('execution_binding',{}).get('execution_id'),
                business_vehicle_id=state.get('execution_binding',{}).get('business_vehicle_id'),
                validation_id=state.get('execution_binding',{}).get('validation_id'))
    frames = [p for p in state.get('track', []) if p['at'] >= state.get('_record_frame_at', float('-inf'))]
    with connect() as db:
        db.execute('INSERT INTO flights VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, boot=excluded.boot, status=excluded.status, body=excluded.body',
                   (ident, state.get('record_created_at', now), now, BOOT, status, json.dumps(body, allow_nan=False)))
        db.executemany('INSERT OR REPLACE INTO frames VALUES (?,?,?)',
                       [(ident, p['at'], json.dumps(p, allow_nan=False)) for p in frames])
    state['_record_saved_at'] = now
    if frames:
        state['_record_frame_at'] = max(p['at'] for p in frames)
    state.pop('record_error', None)


def begin(state):
    state['record_id'] = uuid.uuid4().hex
    state['record_created_at'] = time.time()
    state.pop('_record_saved_at', None)
    state.pop('_record_frame_at', None)
    checkpoint(state, force=True)


@router.get('')
def list_records(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), task_id: str | None = None):
    with connect() as db:
        where=" WHERE json_extract(body,'$.business_task_id')=?" if task_id else ''
        params=(task_id,) if task_id else ()
        total = db.execute('SELECT count(*) FROM flights'+where,params).fetchone()[0]
        rows = db.execute('SELECT id,created,updated,boot,status,body FROM flights'+where+' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?', params+(limit, offset)).fetchall()
        items = []
        for ident, created, updated, boot, status, body in rows:
            data = json.loads(body)
            count, start, end = db.execute('SELECT count(*),min(at),max(at) FROM frames WHERE flight_id=?', (ident,)).fetchone()
            if boot != BOOT and status not in ('completed', 'stopped', 'superseded', 'cancelled'):
                status = 'interrupted'
            items.append(dict(id=ident, created_at=created, updated_at=updated, status=status,
                              area_id=(data.get('patrol') or {}).get('area_id'), aircraft_id='SITL-1',
                              samples=count, start=start, end=end, duration=(end-start) if count else 0,
                              source='ardupilot-sitl', business_task_id=data.get('business_task_id'), execution_id=data.get('execution_id')))
        return dict(items=items, total=total, offset=offset, limit=limit)


@router.get('/{record_id}')
def read_record(record_id: str):
    with connect() as db:
        row = db.execute('SELECT body FROM flights WHERE id=?', (record_id,)).fetchone()
        if not row:
            raise HTTPException(404, '飞行记录不存在')
        data = json.loads(row[0])
        data['track'] = [json.loads(row[0]) for row in db.execute('SELECT body FROM frames WHERE flight_id=? ORDER BY at', (record_id,))]
        from . import compute_store
        data['runtime_history']=compute_store.history(record_id)
        return data
