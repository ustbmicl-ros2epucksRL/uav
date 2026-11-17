import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import sim
from app.route_plan import plan, crossings


def test_open_sky_is_takeoff_straight_line_and_landing():
    result = plan((200, 200), (800, 200), 80, [{"x": 400, "y": 100, "w": 80, "d": 200, "h": 40}], sim.FENCE)
    points = result["waypoints"]
    assert points == [[200.0, 200.0, 0.0], [200.0, 200.0, 80.0], [800.0, 200.0, 80.0], [800.0, 200.0, 0.0]]
    assert result["blocked_buildings"] == 0


def test_gap_in_a_wall_is_used_instead_of_the_straight_line():
    wall = [
        {"x": 500, "y": 0, "w": 80, "d": 800, "h": 100},
        {"x": 500, "y": 1000, "w": 80, "d": 1000, "h": 100},
    ]
    result = plan((200, 200), (900, 200), 80, wall, sim.FENCE)
    cruise = [p for p in result["waypoints"] if p[2] == 80]
    assert max(p[1] for p in cruise) > 700
    assert crossings(result["waypoints"], wall) == []
    assert result["waypoints"][0][2] == 0 and result["waypoints"][-1] == [900.0, 200.0, 0.0]


def test_landing_inside_a_building_is_rejected():
    with pytest.raises(ValueError, match="终点落在合成建筑内"):
        plan((200, 200), (750, 750), 80, [{"x": 700, "y": 700, "w": 100, "d": 100, "h": 40}], sim.FENCE)


def test_ground_station_plans_around_scene_buildings_and_preview_agrees():
    api = TestClient(app)
    sim.reset()
    page = api.get("/?view=legacy")
    assert page.status_code == 200
    assert "生成绕障航线" in page.text
    planned = api.post("/api/vehicles/UAV-01/mission/plan", json={
        "goal_x": 1500, "goal_y": 1700, "altitude_m": 80, "scene": "urban"}).json()
    points = planned["waypoints"]
    assert points[0] == {"x": 200.0, "y": 200.0, "z": 0.0}
    assert points[-1] == {"x": 1500.0, "y": 1700.0, "z": 0.0}
    assert points[1]["z"] == 80 and len(points) <= 20
    checked = api.post("/api/vehicles/UAV-01/mission/preview?scene=urban", json={"waypoints": points}).json()
    assert checked["inside_fence"] and checked["building_hits"] == []
    blocked = api.post("/api/vehicles/UAV-01/mission/plan", json={
        "goal_x": 1700, "goal_y": 1500, "altitude_m": 80, "scene": "urban"})
    assert blocked.status_code == 409 and "终点" in blocked.json()["detail"]
    outside = api.post("/api/vehicles/UAV-01/mission/plan", json={
        "goal_x": 2100, "goal_y": 200, "altitude_m": 80, "scene": "urban"})
    assert outside.status_code == 422
