import copy
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import flight_records as records


def state():
    return {'anchor': {'latitude': 39, 'longitude': 116}, 'patrol': {'status': 'completed', 'area_id': 'west', 'route': [[0, 0, 0], [10, 10, 40]]}, 'track': [{'at': 1.0, 'latitude': 39, 'longitude': 116, 'altitude': 40, 'roll': 2, 'pitch': 3, 'yaw': 4, 'field_age_s': {'roll': 0}}], 'receipts': {}}


def test_restart_new_task_and_pagination_preserve_old_record(tmp_path, monkeypatch):
    monkeypatch.setenv('FLIGHT_DATA_DIR', str(tmp_path))
    first = state()
    records.begin(first)
    ident = first['record_id']
    expected = records.read_record(ident)
    monkeypatch.setattr(records, 'BOOT', 'another-process')
    second = state()
    second['patrol']['status'] = 'uploaded'
    second['track'] = []
    records.begin(second)
    assert second['record_id'] != ident
    assert records.read_record(ident) == expected
    app = FastAPI(); app.include_router(records.router)
    with TestClient(app) as client:
        listing = client.get('/api/sitl/records?limit=1').json()
        assert listing['total'] == 2 and len(listing['items']) == 1
        assert client.get('/api/sitl/records?limit=1&offset=1').json()['items'][0]['id'] == ident
        assert client.get('/api/sitl/records/' + ident).json()['track'][0]['roll'] == 2
        assert client.get('/api/sitl/records/missing').status_code == 404
        assert client.get('/api/sitl/records?limit=0').status_code == 422


def test_checkpoints_are_incremental_atomic_and_crash_records_are_not_completed(tmp_path, monkeypatch):
    monkeypatch.setenv('FLIGHT_DATA_DIR', str(tmp_path))
    s = state(); s['patrol']['status'] = 'patrolling'
    records.begin(s)
    s['track'].append({**s['track'][0], 'at': 2.0, 'roll': 9})
    records.checkpoint(s, force=True)
    records.checkpoint(s, force=True)
    assert len(records.read_record(s['record_id'])['track']) == 2
    s['track'] = [{**s['track'][-1], 'at': 3.0}]
    records.checkpoint(s, force=True)
    assert len(records.read_record(s['record_id'])['track']) == 3
    monkeypatch.setattr(records, 'BOOT', 'after-abrupt-exit')
    assert records.list_records(30, 0)['items'][0]['status'] == 'interrupted'
    before = records.read_record(s['record_id'])
    s['track'].append({'at': 4.0, 'roll': float('nan')})
    with pytest.raises(ValueError):
        records.checkpoint(s, force=True)
    assert records.read_record(s['record_id']) == before
