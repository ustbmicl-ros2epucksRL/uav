import math
from app import follow, sim


def _bearing_error(vehicle, estimate):
    dx, dy, dz = estimate[0] - vehicle.position[0], estimate[1] - vehicle.position[1], estimate[2] - vehicle.position[2]
    horiz = math.hypot(dx, dy)
    yaw = math.radians((vehicle.yaw + vehicle.gimbal_yaw) % 360.0)
    pitch = math.radians(vehicle.gimbal_pitch)
    look = (math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), math.sin(pitch))
    target = (dx, dy, dz)
    scale = math.sqrt(sum(value * value for value in target))
    return sum(look[i] * target[i] for i in range(3)) / scale


def test_identity_uses_only_agreement_of_reports():
    assert follow.identity_of([{"cooperative_id": "DEMO-1"}, {"cooperative_id": "DEMO-1"}]) == ("合作", "报文身份一致")
    assert follow.identity_of([{"cooperative_id": "A"}, {"cooperative_id": "B"}])[0] == "身份待核验"
    assert follow.identity_of([{"type": "RADAR"}]) == ("身份待核验", "没有匹配到合作报文")
    assert follow.identity_of([{"type": "EO", "ground": True, "class_name": "无人机"}, {"type": "RADAR"}]) == ("身份未确认无人机", "地面光电类别为无人机，但未匹配合作报文，需核验飞行计划")
    assert follow.identity_of([{"cooperative_id": "DEMO-1", "type": "EO", "ground": True, "class_name": "无人机"}]) == ("合作", "报文身份一致")


def test_grounded_inspection_aircraft_does_not_slew_the_gimbal():
    sim.reset()
    sim.tick_steps(4)
    vehicle = sim.get("UAV-01")
    report = follow.public()
    assert vehicle.position[2] == 0
    assert vehicle.gimbal_pitch == -35 and vehicle.gimbal_yaw == 0
    assert report["pointing"] is False
    assert report["identity"] == "身份未确认无人机"
    assert report["reason"] == "地面光电类别为无人机，但未匹配合作报文，需核验飞行计划"
    assert report["state"] == "confirmed"
    assert "UAV-01" not in report["sources"]
    assert "truth" not in report


def test_airborne_gimbal_points_at_filtered_estimate():
    sim.reset()
    vehicle = sim.get("UAV-01")
    sim.load_mission(vehicle, [[200, 200, 0], [200, 200, 80], [900, 400, 80]])
    sim.command(vehicle, "arm")
    sim.command(vehicle, "auto")
    sim.tick_steps(8, "UAV-01")
    report = follow.public()
    assert report["state"] == "confirmed" and report["pointing"] is True
    assert report["identity"] == "身份未确认无人机"
    assert math.dist(report["estimate"], follow.truth_position()) < 40
    assert report["estimate"] != [round(value, 1) for value in follow.truth_position()]
    assert vehicle.mission[1] == [200, 200, 80]
    assert _bearing_error(vehicle, report["estimate"]) > 0.98


def test_unregistered_drone_is_identified_and_localized():
    sim.reset()
    start = follow.truth_position()
    sim.tick_steps(20)
    later = follow.truth_position()
    assert math.dist(start, later) > 50
    assert all(80 <= later[axis] <= 1920 for axis in range(2))
    report = follow.public()
    assert report["target"] == "非合作演练无人机"
    assert report["identity"] == "身份未确认无人机"
    assert report["reason"] == "地面光电类别为无人机，但未匹配合作报文，需核验飞行计划"
    assert report["localization"] == "地面雷达距离与光电角度融合"
    assert {"R-SW", "R-NE", "E-CORE"} <= set(report["sources"])
    assert report["state"] == "confirmed"
    assert math.dist(report["estimate"], later) < 40
    assert "truth" not in report
    sim.reset()
    sim.tick_steps(20)
    assert follow.truth_position() == later
