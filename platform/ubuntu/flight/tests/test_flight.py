from fastapi.testclient import TestClient
from app.main import app
from app import sim


def client():
    sim.reset()
    return TestClient(app)


def fly_to_end(api, vehicle_id):
    route = {"waypoints": [
        {"x": 200, "y": 200, "z": 0},
        {"x": 200, "y": 200, "z": 80},
        {"x": 500, "y": 500, "z": 80},
        {"x": 500, "y": 500, "z": 0},
    ]}
    assert api.post(f"/api/vehicles/{vehicle_id}/mission", json=route).status_code == 200
    assert api.post(f"/api/vehicles/{vehicle_id}/command", json={"cmd": "start"}).status_code == 409
    assert api.post(f"/api/vehicles/{vehicle_id}/command", json={"cmd": "arm"}).status_code == 200
    assert api.post(f"/api/vehicles/{vehicle_id}/command", json={"cmd": "start"}).status_code == 200
    state = {}
    for _ in range(80):
        state = api.post("/api/tick", params={"steps": 1, "vehicle_id": vehicle_id}).json()
        vehicle = next(item for item in state["vehicles"] if item["id"] == vehicle_id)
        if vehicle["done"]:
            return vehicle
    raise AssertionError(state)


def test_mission_requires_arm_and_finishes_on_server_tick():
    api = client()
    assert api.get("/health").json()["service"] == "flight-control"
    vehicle = fly_to_end(api, "UAV-01")
    assert vehicle["mode"] == "LOITER"
    assert vehicle["armed"] is False
    assert vehicle["done_reason"] == "mission"
    assert vehicle["position"][2] == 0


def test_stop_holds_position_and_pauses_the_clock():
    api = client()
    route = {"waypoints": [{"x": 200, "y": 200, "z": 0}, {"x": 200, "y": 200, "z": 80}, {"x": 800, "y": 200, "z": 80}]}
    assert api.post("/api/vehicles/UAV-01/mission", json=route).status_code == 200
    assert api.post("/api/vehicles/UAV-01/command", json={"cmd": "arm"}).status_code == 200
    assert api.post("/api/vehicles/UAV-01/command", json={"cmd": "auto"}).status_code == 200
    api.post("/api/tick", params={"steps": 5, "vehicle_id": "UAV-01"})
    assert api.put("/api/simulation", json={"running": True, "speed": 2}).status_code == 200
    stopped = api.post("/api/vehicles/UAV-01/command", json={"cmd": "stop"}).json()
    assert stopped["mode"] == "LOITER" and stopped["armed"] is False and stopped["position"][2] > 0
    assert api.get("/api/station").json()["clock"]["running"] is False
    held = stopped["position"]
    api.post("/api/tick", params={"steps": 3, "vehicle_id": "UAV-01"})
    assert api.get("/api/vehicles/UAV-01").json()["position"] == held


def test_geofence_returns_home():
    api = client()
    route = {"waypoints": [{"x": 200, "y": 200, "z": 50}, {"x": 2100, "y": 200, "z": 50}]}
    assert api.post("/api/vehicles/UAV-01/mission", json=route).status_code == 200
    assert api.post("/api/vehicles/UAV-01/command", json={"cmd": "arm"}).status_code == 200
    assert api.post("/api/vehicles/UAV-01/command", json={"cmd": "start"}).status_code == 200
    seen = False
    for _ in range(200):
        payload = api.post("/api/tick", params={"steps": 1, "vehicle_id": "UAV-01"}).json()
        vehicle = next(item for item in payload["vehicles"] if item["id"] == "UAV-01")
        if any(event["kind"] == "geofence" for event in vehicle["events"]):
            seen = True
        if vehicle["done"]:
            assert seen
            assert vehicle["done_reason"] == "rtl"
            assert vehicle["position"] == [200.0, 200.0, 0.0]
            return
    raise AssertionError("返航未完成")


def test_planning_uses_the_ground_station_port():
    api = client()
    page = api.get("/?view=legacy")
    assert page.status_code == 200
    assert 'href="/network/' in page.text
    plan = api.get("/network/")
    assert plan.status_code == 200
    assert "多模态感知网络智能规划" in plan.text
    assert api.get("/network/static/app.js").status_code == 200
    scenes = api.get("/network/api/scenes").json()["scenes"]
    assert {item["id"] for item in scenes} >= {"urban", "corridor", "electromagnetic"}
    assert api.get("/api/vehicles/UAV-01").status_code == 200


def test_unknown_vehicle_and_page():
    api = client()
    page = api.get("/?view=legacy")
    assert page.status_code == 200
    assert "地面站" in page.text
    assert "姿态" in page.text
    assert api.get("/api/vehicles/UAV-09").status_code == 404


def test_ground_station_attitude_view_and_track():
    api = client()
    route = {"waypoints": [{"x": 200, "y": 200, "z": 80}, {"x": 400, "y": 200, "z": 80}]}
    assert api.post("/api/vehicles/UAV-01/mission", json=route).status_code == 200
    assert api.post("/api/vehicles/UAV-01/command", json={"cmd": "arm"}).status_code == 200
    assert api.post("/api/vehicles/UAV-01/command", json={"cmd": "start"}).status_code == 200
    assert api.get("/api/vehicles/UAV-01/track").json()["count"] == 1
    api.post("/api/tick", params={"steps": 2, "vehicle_id": "UAV-01"})
    vehicle = api.get("/api/vehicles/UAV-01").json()
    assert vehicle["attitude"]["pitch"] == 90
    assert vehicle["position"][2] == 50
    assert vehicle["view"]["kind"] == "optical"
    assert len(vehicle["view"]["footprint"]) == 4
    track = api.get("/api/vehicles/UAV-01/track").json()
    assert track["count"] == 3
    assert track["points"][-1]["z"] == 50
    pointed = api.post("/api/vehicles/UAV-01/gimbal", json={"pitch": -80, "yaw": 0}).json()
    assert pointed["view"]["look_pitch"] == -80
    assert api.post("/api/vehicles/UAV-02/gimbal", json={"pitch": -35, "yaw": 0}).status_code == 409
    other = api.get("/api/vehicles/UAV-02").json()
    assert other["view"]["kind"] == "none"
    assert other["view"]["footprint"] == []
