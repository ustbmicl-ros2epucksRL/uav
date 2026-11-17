import pytest
from fastapi import HTTPException
from app import sitl, compute_runtime


@pytest.mark.parametrize('telemetry', [{'armed': True, 'landed_state': 1}, {'armed': False, 'landed_state': 2}, {}])
def test_restart_rejects_unsafe_or_unknown_state(monkeypatch, telemetry):
    monkeypatch.setattr(sitl, 'STATE', {'telemetry': telemetry})
    monkeypatch.setattr(sitl, 'PROCESS', type('Process', (), {'poll': lambda self: None})())
    with pytest.raises(HTTPException):
        sitl.restart()


def test_restart_preserves_active_compute_and_resets_idle_simulator(monkeypatch):
    monkeypatch.setattr(sitl, 'STATE', {'telemetry': {'armed': False, 'landed_state': 1}})
    monkeypatch.setattr(sitl, 'PROCESS', None)
    calls = []
    monkeypatch.setattr(sitl, 'stop', lambda: calls.append('stop'))
    monkeypatch.setattr(sitl, 'start', lambda: calls.append('start'))
    monkeypatch.setattr(compute_runtime, 'snapshot', lambda: {'state': 'running'})
    with pytest.raises(HTTPException):
        sitl.restart()
    assert not calls
    monkeypatch.setattr(compute_runtime, 'snapshot', lambda: None)
    sitl.restart()
    assert calls == ['stop', 'start']
