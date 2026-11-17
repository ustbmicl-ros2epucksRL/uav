from types import SimpleNamespace
from app import sitl


def test_recorded_position_captures_field_ages_and_preserves_snapshot(monkeypatch):
    state = {'telemetry': {'roll': 12.0, 'pitch': 3.0}, 'updated_at': {'roll': 95.0, 'pitch': 99.0}}
    monkeypatch.setattr(sitl, 'STATE', state)
    monkeypatch.setattr(sitl.time, 'time', lambda: 100.0)
    msg = SimpleNamespace(get_srcSystem=lambda: 1, get_srcComponent=lambda: 1,
        get_type=lambda: 'GLOBAL_POSITION_INT', lat=390000000, lon=1160000000,
        relative_alt=40000, vx=300, vy=400, vz=-100)
    sitl.apply(msg)
    frame = state['track'][0]
    assert frame['at'] == 100.0
    assert frame['field_age_s']['roll'] == 5.0
    assert frame['field_age_s']['pitch'] == 1.0
    assert frame['field_age_s']['altitude'] == 0.0
    assert frame['speed'] == 5.0
    state['telemetry']['roll'] = 99
    assert frame['roll'] == 12.0


def test_completed_flight_record_is_not_overwritten_by_idle_telemetry(monkeypatch):
    state = {'telemetry': {'armed': False}, 'updated_at': {}, 'patrol': {'status': 'patrolling'}}
    monkeypatch.setattr(sitl, 'STATE', state)
    now = [100.0]
    monkeypatch.setattr(sitl.time, 'time', lambda: now[0])
    monkeypatch.setattr(sitl, 'patrol_completed', lambda s: s['patrol']['status'] == 'patrolling')
    msg = SimpleNamespace(get_srcSystem=lambda: 1, get_srcComponent=lambda: 1,
        get_type=lambda: 'GLOBAL_POSITION_INT', lat=390000000, lon=1160000000,
        relative_alt=0, vx=0, vy=0, vz=0)
    sitl.apply(msg)
    saved = list(state['track'])
    assert state['recording_complete']
    now[0] += 100
    msg.relative_alt = 1000
    sitl.apply(msg)
    assert state['track'] == saved
    assert state['telemetry']['altitude'] == 1.0
