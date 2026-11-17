"""规划论文方法的独立实现。

静态层按新覆盖采样除以成本贪心加站，精度下界只在不超过 10 个候选的枚举里作约束。
动态层在已定站址上指向分段均值，并按模态数取 2 秒或 4 秒。
合成几何，不是外场测量，也不生成飞机指令。
"""
import json
import math
import os
from itertools import combinations

RANGE_SIGMA = {"RADAR": 24.0, "RF": 48.0, "EO": 12.0, "IR": 18.0}
ANGLE_SIGMA = {"RADAR": 0.02, "RF": 0.045, "EO": 0.008, "IR": 0.012}
MODALITIES = ("RADAR", "EO", "RF")
SAMPLE_STEP = 50.0


def wrap(angle):
    return (angle + 180.0) % 360.0 - 180.0


def look(origin, point):
    dx = point[0] - origin[0]
    dy = point[1] - origin[1]
    dz = point[2] - origin[2]
    horizontal = math.hypot(dx, dy)
    return math.hypot(horizontal, dz), math.degrees(math.atan2(dx, dy)), math.degrees(math.atan2(dz, max(horizontal, 1e-9))), (dx, dy, dz)


def ray_blocked(origin, point, building):
    lo = (building["x"], building["y"], 0.0)
    hi = (building["x"] + building["w"], building["y"] + building["d"], building["h"])
    direction = tuple(point[i] - origin[i] for i in range(3))
    enter, leave = 0.0, 1.0
    for axis in range(3):
        if abs(direction[axis]) < 1e-12:
            if origin[axis] < lo[axis] or origin[axis] > hi[axis]:
                return False
            continue
        t1 = (lo[axis] - origin[axis]) / direction[axis]
        t2 = (hi[axis] - origin[axis]) / direction[axis]
        enter = max(enter, min(t1, t2))
        leave = min(leave, max(t1, t2))
        if enter > leave:
            return False
    return leave > 1e-6 and enter < 1.0


def visible(device, point, buildings):
    if device["type"] not in MODALITIES:
        return False
    origin = (device["x"], device["y"], device["z"])
    distance, azimuth, elevation, delta = look(origin, point)
    if distance > device["range"] or distance < 1e-6:
        return False
    if abs(wrap(azimuth - device["azimuth"])) > device["fov"] / 2:
        return False
    if abs(elevation - device.get("pitch", 0.0)) > device.get("vertical_fov", 110.0) / 2:
        return False
    return not any(ray_blocked(origin, point, building) for building in buildings)


def detection(device, point, buildings):
    return float(device.get("pd", 0.8)) if visible(device, point, buildings) else 0.0


def fuse(probabilities):
    miss = 1.0
    for value in probabilities:
        miss *= 1.0 - value
    return 1.0 - miss


def _cubic_roots(trace, minor_sum, determinant):
    """对称矩阵特征多项式 λ^3 - trace λ^2 + minor_sum λ - determinant 的三个实根。"""
    shift = trace / 3.0
    p = minor_sum - trace * trace / 3.0
    q = (-2.0 * trace ** 3 + 9.0 * trace * minor_sum - 27.0 * determinant) / 27.0
    if p > -1e-14:
        return [shift, shift, shift]
    radius = 2.0 * math.sqrt(-p / 3.0)
    argument = max(-1.0, min(1.0, (-q / 2.0) / math.sqrt((-p / 3.0) ** 3)))
    theta = math.acos(argument)
    return [radius * math.cos((theta - 2.0 * math.pi * k) / 3.0) + shift for k in range(3)]


def eigenvalues(matrix):
    a, b, c = matrix[0][0], matrix[1][1], matrix[2][2]
    d, e, f = matrix[0][1], matrix[0][2], matrix[1][2]
    trace = a + b + c
    minors = a * b + a * c + b * c - d * d - e * e - f * f
    determinant = a * (b * c - f * f) - d * (d * c - e * f) + e * (d * f - b * e)
    return sorted(_cubic_roots(trace, minors, determinant))


def information(device, point, buildings):
    probability = detection(device, point, buildings)
    if probability <= 0.0:
        return probability, [[0.0, 0.0, 0.0] for _ in range(3)]
    origin = (device["x"], device["y"], device["z"])
    distance, _, _, delta = look(origin, point)
    unit = tuple(value / distance for value in delta)
    outer = [[unit[i] * unit[j] for j in range(3)] for i in range(3)]
    angle = max(distance * ANGLE_SIGMA.get(device["type"], 0.02), 1e-9)
    matrix = [[(1.0 if i == j else 0.0) - outer[i][j] for j in range(3)] for i in range(3)]
    scale = probability / (angle * angle)
    if device["type"] == "RADAR":
        sigma = RANGE_SIGMA["RADAR"]
        range_scale = probability / (sigma * sigma)
    else:
        range_scale = 0.0
    return probability, [[scale * matrix[i][j] + range_scale * outer[i][j] for j in range(3)] for i in range(3)]


def crlb(devices, point, buildings):
    total = [[0.0, 0.0, 0.0] for _ in range(3)]
    for device in devices:
        _, added = information(device, point, buildings)
        for i in range(3):
            for j in range(3):
                total[i][j] += added[i][j]
    values = eigenvalues(total)
    if values[0] <= 1e-8:
        return math.inf
    return math.sqrt(sum(1.0 / value for value in values))


def covered(devices, points, buildings, threshold):
    flags = []
    for point in points:
        flags.append(fuse(detection(device, point, buildings) for device in devices) >= threshold)
    return flags


def resample(points, step=SAMPLE_STEP):
    if len(points) < 2:
        raise ValueError("航路至少需要两个三维点")
    output = [tuple(points[0])]
    for start, end in zip(points, points[1:]):
        length = math.dist(start, end)
        if length == 0:
            continue
        pieces = max(1, math.ceil(length / step))
        for index in range(1, pieces + 1):
            output.append(tuple(start[axis] + (end[axis] - start[axis]) * index / pieces for axis in range(3)))
    if len(output) < 2:
        raise ValueError("航路长度为 0")
    return output


def density_greedy(sets, budget):
    """预算内按新增权重除以成本选取。不枚举，因此不是 Khuller 的 1-1/e 算法。"""
    chosen = []
    spent = 0.0
    owned = set()
    while True:
        best = None
        for item in sets:
            if item["id"] in {row["id"] for row in chosen} or spent + item["cost"] > budget:
                continue
            gain = sum(weight for element, weight in item["elements"] if element not in owned)
            if gain <= 0:
                continue
            score = gain / item["cost"]
            if best is None or score > best[0]:
                best = (score, item, gain)
        if best is None:
            return {"chosen": chosen, "weight": sum(weight for _, weight in _flatten(chosen)), "cost": spent}
        _, item, _ = best
        chosen.append(item)
        spent += item["cost"]
        owned.update(element for element, _ in item["elements"])


def _flatten(chosen):
    seen = set()
    for item in chosen:
        for element, weight in item["elements"]:
            if element not in seen:
                seen.add(element)
                yield element, weight


def optimal_budgeted(sets, budget):
    best = {"chosen": [], "weight": 0.0, "cost": 0.0}
    for size in range(len(sets) + 1):
        for combo in combinations(sets, size):
            cost = sum(item["cost"] for item in combo)
            if cost > budget:
                continue
            weight = sum(weight for _, weight in _flatten(combo))
            if weight > best["weight"] or (weight == best["weight"] and cost < best["cost"]):
                best = {"chosen": list(combo), "weight": weight, "cost": cost}
    return best


def plan_sites(route, installed, candidates, buildings, budget, threshold=0.5):
    """已有站保留。候选按新覆盖采样数除以成本加入，精度不进入这个得分。"""
    points = resample(route)
    chosen = []
    spent = 0.0
    current = covered(installed, points, buildings, threshold)
    while True:
        best = None
        for device in candidates:
            if device["id"] in {item["id"] for item in chosen} or spent + device["cost"] > budget:
                continue
            flags = covered(installed + chosen + [device], points, buildings, threshold)
            gain = sum(1 for before, after in zip(current, flags) if after and not before)
            if gain <= 0:
                continue
            score = gain / device["cost"]
            if best is None or score > best[0]:
                best = (score, device, flags)
        if best is None:
            break
        _, device, current = best
        chosen.append(device)
        spent += device["cost"]
    return {
        "samples": points,
        "selected": chosen,
        "added_cost": spent,
        "covered": sum(current),
        "total": len(points),
        "installed_ids": [device["id"] for device in installed],
    }


def precision_rate(devices, points, buildings, threshold, limit):
    flags = covered(devices, points, buildings, threshold)
    if not any(flags):
        return 0.0
    passed = 0
    for point, flag in zip(points, flags):
        if flag and crlb(devices, point, buildings) <= limit:
            passed += 1
    return passed / sum(flags)


def enumerate_sites(route, installed, candidates, buildings, budget, threshold=0.5, coverage_target=0.9, precision_target=0.8, precision_limit=30.0):
    if len(candidates) > 10:
        raise ValueError("枚举只用于不超过 10 个候选")
    points = resample(route)
    best = None
    for size in range(len(candidates) + 1):
        for combo in combinations(candidates, size):
            added = list(combo)
            cost = sum(device["cost"] for device in added)
            if cost > budget:
                continue
            devices = installed + added
            flags = covered(devices, points, buildings, threshold)
            ratio = sum(flags) / len(points)
            precise = precision_rate(devices, points, buildings, threshold, precision_limit)
            if ratio < coverage_target or precise < precision_target:
                continue
            if best is None or cost < best["added_cost"] or (cost == best["added_cost"] and ratio > best["coverage"]):
                best = {"selected": added, "added_cost": cost, "coverage": ratio, "precision_rate": precise}
    return best


def schedule(route, devices, buildings, threshold=0.5):
    points = resample(route)
    groups = []
    for point in points:
        watchers = tuple(device["id"] for device in devices if detection(device, point, buildings) >= threshold)
        if groups and groups[-1]["watchers"] == watchers:
            groups[-1]["points"].append(point)
        else:
            groups.append({"watchers": watchers, "points": [point]})
    by_id = {device["id"]: device for device in devices}
    rows = []
    for group in groups:
        count = len(group["points"])
        mean = tuple(sum(point[axis] for point in group["points"]) / count for axis in range(3))
        members = [by_id[identifier] for identifier in group["watchers"]]
        modalities = sorted({device["type"] for device in members})
        rows.append({
            "watchers": list(group["watchers"]),
            "mean": mean,
            "period_s": 4.0 if len(modalities) >= 2 else 2.0,
            "cooperate": len(members) >= 2,
            "pointing": {
                device["id"]: {
                    "azimuth": math.degrees(math.atan2(mean[0] - device["x"], mean[1] - device["y"])) % 360.0,
                    "pitch": math.degrees(math.atan2(mean[2] - device["z"], math.hypot(mean[0] - device["x"], mean[1] - device["y"]))),
                }
                for device in members
            },
        })
    return rows


def _device(identifier, kind, x, y, z, azimuth, fov, sensing_range, cost, pd=0.8):
    return {"id": identifier, "type": kind, "x": x, "y": y, "z": z, "azimuth": azimuth, "fov": fov, "pitch": 0.0, "vertical_fov": 80.0, "range": sensing_range, "cost": cost, "pd": pd}


def demonstration():
    buildings = [{"x": 70.0, "y": 20.0, "w": 30.0, "d": 30.0, "h": 40.0}]
    installed = [_device("R-0", "RADAR", 0.0, 80.0, 25.0, 90.0, 70.0, 160.0, 0.0)]
    candidates = [
        _device("E-gap", "EO", 40.0, 180.0, 25.0, 180.0, 70.0, 120.0, 4.0),
        _device("R-far", "RADAR", 400.0, 80.0, 25.0, 270.0, 90.0, 80.0, 30.0),
    ]
    route = [(40.0, 80.0, 30.0), (40.0, 140.0, 30.0)]
    greedy = plan_sites(route, installed, candidates, buildings, budget=10.0)
    enumerated = enumerate_sites(route, installed, candidates[:1], buildings, budget=10.0, coverage_target=0.5, precision_target=0.0, precision_limit=80.0)
    timed = schedule(route, installed + greedy["selected"], buildings)
    return {
        "scene": "synthetic",
        "evidence_status": "synthetic_only",
        "greedy_selected": [device["id"] for device in greedy["selected"]],
        "greedy_covered": greedy["covered"],
        "greedy_total": greedy["total"],
        "enumerate_selected": None if enumerated is None else [device["id"] for device in enumerated["selected"]],
        "schedule_periods": [row["period_s"] for row in timed],
        "note": "合成航路和合成建筑。贪心只看新覆盖除以成本。",
    }


def main():
    result = demonstration()
    target = os.path.join(os.path.dirname(__file__), "outputs", "result.json")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
