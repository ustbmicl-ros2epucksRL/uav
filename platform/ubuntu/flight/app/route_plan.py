"""定高绕障航线。

从飞机原点垂直起飞，在给定巡航高度上绕开更高的建筑，再在终点垂直降落。
建筑来自感知场景里的合成体块，不是测绘障碍。不改感知站址，也不在这里向飞机发指令。
"""
import heapq
import math
from pathlib import Path

CELL = 25.0
CLEARANCE_M = 20.0
SCENES = ("urban", "corridor", "electromagnetic")


def scene_record(scene_id):
    if scene_id not in SCENES:
        raise ValueError("场景应为园区、廊道或电磁区")
    import importlib.util
    import sys
    loaded = sys.modules.get("sensing_plan.scenes")
    if loaded is not None:
        return loaded.seed(scene_id)
    path = Path(__file__).resolve().parents[3] / "app" / "scenes.py"
    spec = importlib.util.spec_from_file_location("sensing_plan.scenes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.seed(scene_id)


def _inside(x, y, building, margin):
    return (building["x"] - margin <= x <= building["x"] + building["w"] + margin
            and building["y"] - margin <= y <= building["y"] + building["d"] + margin)


def _blocked(x, y, z, buildings, margin):
    return any(b["h"] >= z and _inside(x, y, b, margin) for b in buildings)


def _segment_clear(a, b, buildings, margin):
    dist = math.hypot(b[0] - a[0], b[1] - a[1])
    vertical = dist < 1.0
    use = 0.0 if vertical else margin
    steps = max(1, int(math.ceil(max(dist, abs(b[2] - a[2])) / 5.0)))
    for i in range(steps + 1):
        t = i / steps
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        z = a[2] + (b[2] - a[2]) * t
        if _blocked(x, y, z, buildings, use):
            return False
    return True


def crossings(points, buildings, margin=CLEARANCE_M):
    """points 的第一点是飞机当前位置，其后是航点。返回穿过建筑的航段。"""
    hits = []
    for i, (a, b) in enumerate(zip(points, points[1:])):
        if _segment_clear(a, b, buildings, margin):
            continue
        label = lambda k: "当前位置" if k == 0 else f"航点 {k}"
        hits.append({"from": label(i), "to": label(i + 1)})
    return hits


def _cell(v, n):
    return min(n - 1, max(0, int(v // CELL)))


def _center(i, j):
    return ((i + 0.5) * CELL, (j + 0.5) * CELL)


def _free_cells(n, altitude, buildings, fence):
    free = set()
    for i in range(n):
        for j in range(n):
            x0, y0 = i * CELL, j * CELL
            x1, y1 = x0 + CELL, y0 + CELL
            if x1 < fence["x0"] or x0 > fence["x1"] or y1 < fence["y0"] or y0 > fence["y1"]:
                continue
            blocked = False
            for b in buildings:
                if b["h"] < altitude:
                    continue
                if x0 < b["x"] + b["w"] + CLEARANCE_M and x1 > b["x"] - CLEARANCE_M and y0 < b["y"] + b["d"] + CLEARANCE_M and y1 > b["y"] - CLEARANCE_M:
                    blocked = True
                    break
            if not blocked:
                free.add((i, j))
    return free


def _nearest(x, y, free, buildings, altitude):
    n = int(2000 / CELL)
    i0, j0 = _cell(x, n), _cell(y, n)
    best = None
    for radius in range(6):
        for di in range(-radius, radius + 1):
            for dj in range(-radius, radius + 1):
                cell = (i0 + di, j0 + dj)
                if cell not in free:
                    continue
                cx, cy = _center(*cell)
                if not _segment_clear((x, y, altitude), (cx, cy, altitude), buildings, CLEARANCE_M):
                    continue
                dist = math.hypot(cx - x, cy - y)
                if best is None or dist < best[0]:
                    best = (dist, cell)
        if best is not None:
            break
    return None if best is None else best[1]


def _astar(start, goal, free):
    if start not in free or goal not in free:
        return None

    def heuristic(cell):
        return math.hypot(cell[0] - goal[0], cell[1] - goal[1])

    queue = [(heuristic(start), 0.0, start)]
    previous = {}
    cost = {start: 0.0}
    while queue:
        _, spent, current = heapq.heappop(queue)
        if spent != cost.get(current):
            continue
        if current == goal:
            path = [current]
            while current in previous:
                current = previous[current]
                path.append(current)
            path.reverse()
            return path
        i, j = current
        for di, dj, step in ((1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1),
                             (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)),
                             (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2))):
            nxt = (i + di, j + dj)
            if nxt not in free:
                continue
            if di and dj and ((i + di, j) not in free or (i, j + dj) not in free):
                continue
            nxt_cost = spent + step
            if nxt_cost < cost.get(nxt, 1e18):
                cost[nxt] = nxt_cost
                previous[nxt] = current
                heapq.heappush(queue, (nxt_cost + heuristic(nxt), nxt_cost, nxt))
    return None


def _pull(points, altitude, buildings):
    kept = [points[0]]
    index = 0
    while index < len(points) - 1:
        farthest = len(points) - 1
        while farthest > index + 1 and not _segment_clear(
                (*points[index], altitude), (*points[farthest], altitude), buildings, CLEARANCE_M):
            farthest -= 1
        kept.append(points[farthest])
        index = farthest
    return kept


def plan(start, goal, altitude, buildings, fence):
    """start、goal 为 (x, y)。返回含起飞和降落的航点。"""
    sx, sy = float(start[0]), float(start[1])
    gx, gy = float(goal[0]), float(goal[1])
    altitude = float(altitude)
    if not (fence["z0"] <= altitude <= fence["z1"]):
        raise ValueError("巡航高度超出仿真围栏")
    for name, x, y in (("原点", sx, sy), ("终点", gx, gy)):
        if not (fence["x0"] <= x <= fence["x1"] and fence["y0"] <= y <= fence["y1"]):
            raise ValueError(f"{name}超出仿真围栏")
    if math.hypot(gx - sx, gy - sy) < 30:
        raise ValueError("终点距原点不足 30 米")
    if _blocked(sx, sy, 0, buildings, 0):
        raise ValueError("原点落在合成建筑内，不能垂直起飞")
    if _blocked(gx, gy, 0, buildings, 0):
        raise ValueError("终点落在合成建筑内，不能垂直降落")
    if _blocked(sx, sy, altitude, buildings, CLEARANCE_M):
        raise ValueError("原点距更高的建筑不足 20 米，请提高巡航高度")
    if _blocked(gx, gy, altitude, buildings, CLEARANCE_M):
        raise ValueError("终点距更高的建筑不足 20 米，请提高巡航高度或改选终点")

    n = int(round(max(fence["x1"], fence["y1"]) / CELL))
    free = _free_cells(n, altitude, buildings, fence)
    start_cell = _nearest(sx, sy, free, buildings, altitude)
    goal_cell = _nearest(gx, gy, free, buildings, altitude)
    if start_cell is None or goal_cell is None:
        raise ValueError("起终点附近都被更高的建筑围住")
    cells = _astar(start_cell, goal_cell, free)
    if cells is None:
        raise ValueError("该高度下起终点被建筑隔断，请提高巡航高度或改选终点")
    raw = [(sx, sy)]
    for cell in cells:
        center = _center(*cell)
        if math.hypot(center[0] - raw[-1][0], center[1] - raw[-1][1]) > 0.5:
            raw.append(center)
    if math.hypot(gx - raw[-1][0], gy - raw[-1][1]) > 0.5:
        raw.append((gx, gy))
    cruise = _pull(raw, altitude, buildings)
    waypoints = [[round(sx, 1), round(sy, 1), 0.0], [round(sx, 1), round(sy, 1), round(altitude, 1)]]
    for x, y in cruise[1:]:
        waypoints.append([round(x, 1), round(y, 1), round(altitude, 1)])
    waypoints.append([round(gx, 1), round(gy, 1), 0.0])
    if len(waypoints) > 20:
        raise ValueError("绕障转折超过 18 处，航点多于 20 个，请提高巡航高度或改选终点")
    triples = [tuple(p) for p in waypoints]
    if crossings(triples, buildings):
        raise ValueError("抽成航点后仍穿过建筑，请提高巡航高度")
    distance = sum(math.dist(a, b) for a, b in zip(waypoints, waypoints[1:]))
    blocked = sum(1 for b in buildings if b["h"] >= altitude)
    return {
        "method": "定高绕障：原点垂直起飞，巡航高度上绕开更高的合成建筑，终点垂直降落",
        "waypoints": waypoints,
        "altitude_m": round(altitude, 1),
        "clearance_m": CLEARANCE_M,
        "blocked_buildings": blocked,
        "distance_m": round(distance, 1),
        "note": "草案尚未上传，也不改感知站址。建筑是所选场景的合成体块，不是测绘障碍。未检查空域许可。",
    }
