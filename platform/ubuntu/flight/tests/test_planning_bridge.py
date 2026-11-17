import copy
import importlib
import time

from fastapi.testclient import TestClient
from app.main import app
from app import sim


def test_planning_trace_link_preserves_flight_and_data_provenance(tmp_path, monkeypatch):
    store = importlib.import_module('sensing_plan.store')
    monkeypatch.setattr(store, 'DATA', tmp_path / 'planning')
    monkeypatch.setenv('FLIGHT_DATA_DIR', str(tmp_path / 'flight'))
    sim.reset()
    with TestClient(app) as client:
        task_response = client.post('/network/api/workflow/tasks', json=dict(name='地面站关联回归',focus=dict(x0=700,x1=720,y0=700,y1=720,z0=120,z1=140)))
        assert task_response.status_code == 200, task_response.text
        task = task_response.json()
        response = client.post(f'/network/api/workflow/tasks/{task["id"]}/assessments', json=dict(bounds=[690,730,690,730,120,140],optimize=False))
        identifier = response.json()['id']
        until = time.monotonic() + 30
        while time.monotonic() < until:
            job = client.get('/network/api/assessments/' + identifier).json()
            if job['status'] in ('succeeded','failed'):
                break
            time.sleep(.05)
        assert job['status'] == 'succeeded',job
        assert client.post('/network/api/assessments/' + identifier + '/plan').status_code == 200
        vehicle = sim.VEHICLES['UAV-01']
        assert client.post('/api/vehicles/UAV-01/planning-evidence', json={'assessment_id':identifier}).status_code == 409
        vehicle.track.extend([{'t':0,'x':200,'y':200,'z':80},{'t':1,'x':205,'y':200,'z':80}])
        before = copy.deepcopy(sim.snapshot(vehicle))
        response = client.post('/api/vehicles/UAV-01/planning-evidence', json={'assessment_id':identifier,'note':'仅关联已有仿真轨迹'})
        assert response.status_code == 200,response.text
        record = response.json()
        assert record['task_id'] == task['id'] and record['plan_id'] == identifier
        assert record['provenance'] == 'synthetic'
        assert record['sensor_observations'] == []
        assert record['coordinate_alignment'] == 'pending_verification'
        assert sim.snapshot(vehicle) == before
        evidence = client.get('/network/api/assessments/' + identifier).json()['evidence']
        assert evidence['flight_records'][0]['id'] == record['id']
        exported = client.get(f'/network/api/assessments/{identifier}/flight-evidence/{record["id"]}')
        assert exported.json()['track'] == record['track']
        assert client.get(f'/network/api/assessments/wrong/flight-evidence/{record["id"]}').status_code == 404
