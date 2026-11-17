"""Adapters to published/open-source planners; never sends flight commands."""
import json
import math
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
from .disposition import clear, intersection_point, detour


def valid_point(point, bounds, boxes):
    return all(bounds['xyz_min'][i] <= point[i] <= bounds['xyz_max'][i] for i in range(3)) and clear(point, point, boxes)


def validate_path(points, start, goal, bounds, boxes):
    return (len(points) >= 2 and math.dist(points[0], start) < 1e-5
            and math.dist(points[-1], goal) < 1e-5
            and all(valid_point(p, bounds, boxes) for p in points)
            and all(clear(a, b, boxes) for a, b in zip(points, points[1:])))


def solve_route(start, goal, bounds, boxes, algorithm='bitstar', seed=7, budget=1.5):
    if not valid_point(start, bounds, boxes) or not valid_point(goal, bounds, boxes):
        raise ValueError('起点或终点超出规划范围，或位于建筑/禁飞区安全间隔内')
    if math.dist(start, goal) < 1:
        raise ValueError('起终点距离至少 1 米')
    if algorithm == 'acs_ds':
        path = acs_route(start, goal, bounds, boxes, seed)
    elif algorithm == 'astar':
        fence = dict(x0=bounds['xyz_min'][0], y0=bounds['xyz_min'][1],
                     x1=bounds['xyz_max'][0], y1=bounds['xyz_max'][1])
        path = detour(start, goal, start[2], boxes, fence)
    else:
        # Process isolation: OMPL global RNG and native objects never share API threads.
        payload = dict(start=start, goal=goal, bounds=bounds, boxes=boxes, seed=seed, budget=budget)
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name('ompl_worker.py'))],
                              input=json.dumps(payload), capture_output=True, text=True, timeout=budget + 15)
        if proc.returncode:
            raise RuntimeError('OMPL 不可用或计算失败：' + proc.stderr[-400:])
        path = json.loads(proc.stdout)['path']
    # ACS-DS upstream returns a direct edge when search fails. Never trust that fallback.
    if not path or not validate_path(path, start, goal, bounds, boxes):
        return dict(status='no_path', algorithm=algorithm, points=[], distance_m=None,
                    note='未找到通过连续航段安全检查的路线；不输出直线替代')
    cleaned = [list(path[0])]
    for p in path[1:]:
        if math.dist(p, cleaned[-1]) > 1e-6:
            cleaned.append(list(p))
    return dict(status='ok', algorithm=algorithm, points=cleaned,
                distance_m=sum(math.dist(a, b) for a, b in zip(cleaned, cleaned[1:])),
                note='巡航段几何规划，已检查膨胀障碍棱柱；不含起降、动力学或飞行许可')


def acs_route(start, goal, bounds, boxes, seed):
    from .vendor.hatto.acs_ds import ACSDSBaseline
    # Platform adapter supplies a small visibility graph around inflated prism corners.
    # The optimizer remains the author's ACS-DS. Graph construction is our integration.
    nodes = [tuple(start), tuple(goal)]
    levels = sorted({start[2], goal[2], min(bounds['xyz_max'][2], max(start[2], goal[2]) + 60)})
    for box in boxes:
        for x in (box['lower'][0] - 2, box['upper'][0] + 2):
            for y in (box['lower'][1] - 2, box['upper'][1] + 2):
                for z in levels:
                    p = (x, y, z)
                    if valid_point(p, bounds, boxes) and p not in nodes:
                        nodes.append(p)
    if len(nodes) > 250:
        raise ValueError('ACS-DS 对照场景过大，请缩小管理区域或使用 BIT*')
    edges = [[] for _ in nodes]
    for i, a in enumerate(nodes):
        for j in range(i + 1, len(nodes)):
            if clear(a, nodes[j], boxes):
                edges[i].append(j); edges[j].append(i)
    world = SimpleNamespace(waypoints=SimpleNamespace(nodes=nodes, edges=edges),
                            cfg=SimpleNamespace(connect_step_m=5),
                            segment_is_free_cached=lambda a, b, **kw: clear(a, b, boxes))
    planner = ACSDSBaseline(dict(ants=12, iterations=15, safety_radius_m=120,
                                safety_sample_nodes=8, max_stale_steps=40))
    planner.reset(world, seed)
    return planner.plan(dict(uav_pos=start, targets=[goal]))['path']
