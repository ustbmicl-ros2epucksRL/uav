import os
import httpx


PLANNING = os.environ["PLANNING_BASE_URL"].rstrip("/")
FLIGHT = os.environ["FLIGHT_BASE_URL"].rstrip("/")
LOWAIR = os.environ["LOWAIR_BASE_URL"].rstrip("/")
ROUTE = [
    {"x": 200, "y": 200, "z": 0},
    {"x": 200, "y": 200, "z": 80},

    {"x": 200, "y": 200, "z": 0},
]


def test_three_ubuntu_services_complete_one_flight():
    with httpx.Client(timeout=5.0) as client:
        scenes = client.get(PLANNING + "/api/scenes")
        assert scenes.status_code == 200
        assert "urban" in {item["id"] for item in scenes.json()["scenes"]}
        assert client.get(PLANNING + "/health").json()["os"] == "Ubuntu"
        assert client.get(FLIGHT + "/health").json()["os"] == "Ubuntu"
        assert client.get(LOWAIR + "/health").json()["os"] == "Ubuntu"
        client.post(FLIGHT + "/api/reset")
        completed_before = client.get(LOWAIR + "/api/performance").json()["completed"]
        linked = client.get(LOWAIR + "/api/planning/scenes").json()
        assert linked["available"] is True
        application = client.post(LOWAIR + "/api/applications", json={
            "applicant": "园区物流",
            "pilot_id": "P-01",
            "vehicle_id": "UAV-01",
            "scene": "urban",
            "purpose": "服务器联调飞行",
            "route": ROUTE,
        })
        assert application.status_code == 200, application.text
        app_id = application.json()["id"]
        assert client.post(LOWAIR + f"/api/applications/{app_id}/review", json={
            "decision": "approve", "reviewer": "服务器值班",
        }).status_code == 200
        task = client.post(LOWAIR + f"/api/tasks/from-application/{app_id}")
        assert task.status_code == 200, task.text
        task_id = task.json()["id"]
        validation = client.post(LOWAIR + f"/api/tasks/{task_id}/validate")
        assert validation.status_code == 200 and validation.json()["result"]["passed"], validation.text
        assert client.post(LOWAIR + f"/api/tasks/{task_id}/dispatch").status_code == 200
        finished = None
        for _ in range(120):
            assert client.post(FLIGHT + "/api/tick", params={"steps": 1, "vehicle_id": "UAV-01"}).status_code == 200
            synced = client.post(LOWAIR + f"/api/tasks/{task_id}/sync")
            assert synced.status_code == 200, synced.text
            if synced.json()["status"] == "completed":
                finished = synced.json()
                break
        assert finished is not None
        assert finished["telemetry"]["mode"] == "LOITER"
        assert finished["telemetry"]["armed"] is False
        performance = client.get(LOWAIR + "/api/performance").json()
        assert performance["completed"] == completed_before + 1
        assert client.get(LOWAIR + "/api/live/UAV-01").json()["status"] == "reserved"
