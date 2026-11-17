"""航路连续覆盖的一次完整推演。

空间层：沿给定折线采样，判断雷达、光电、无线电各自能否看见该格，再按单位成本贪心补站。
动态层：站址确定后，按各段看见该航路的站点安排指向、扫描周期和协同站。
不生成飞机航线，也不移动已有站址。
"""
import math
import uuid
import numpy as np
from . import coverage_core as core
from .scenes import trajectories
from .store import now

MODALITIES = ("RADAR", "EO", "RF")


def default_points(scene):
    """场景里第一条模拟航迹，作为未另行指定时的关键航路。"""
    return trajectories(scene)["tracks"][0]["points"]


def resample(points, step):
    """把折线按不超过 step 米的间距重采样，并记下沿程距离。"""
    pts = [np.asarray(p, dtype=float) for p in points]
    if len(pts) < 2 or any(p.shape != (3,) for p in pts):
        raise ValueError("航路至少需要两个三维点")
    out = [pts[0]]
    arc = [0.0]
    travelled = 0.0
    for start, end in zip(pts, pts[1:]):
        segment = end - start
        length = float(np.linalg.norm(segment))
        if length == 0:
            continue
        pieces = max(1, int(np.ceil(length / step)))
        for i in range(1, pieces + 1):
            out.append(start + segment * (i / pieces))
            arc.append(travelled + length * (i / pieces))
        travelled += length
    if len(out) < 2:
        raise ValueError("航路长度需要大于 0")
    return np.vstack(out), np.asarray(arc)


def _hits(device, scene, samples, request):
    return core.device_field(scene,samples,device,request)[0]


def _fused(watchers, size):
    miss=np.ones(size)
    for _,pd in watchers:miss*=1-pd
    return 1-miss


def _kind(device):
    return "EO" if device["type"] == "IR" else device["type"]


def _summarize(samples, arc, air_ok, by_modality, step, covered=None):
    if covered is None:
        covered = np.zeros(len(samples), dtype=bool)
        for name in MODALITIES:
            covered |= by_modality[name]
    gaps = []
    index = 0
    while index < len(covered):
        if covered[index] or not air_ok[index]:
            index += 1
            continue
        end = index
        while end < len(covered) and air_ok[end] and not covered[end]:
            end += 1
        left=0. if index==0 else (arc[index-1]+arc[index])/2
        right=arc[-1] if end==len(arc) else (arc[end-1]+arc[end])/2
        length = float(right-left)
        gaps.append({"from_m": round(float(arc[index]), 1), "to_m": round(float(arc[end - 1]), 1),
                     "length_m": round(length, 1), "samples": int(end - index)})
        index = end
    flyable = int(air_ok.sum())
    seen = int((covered & air_ok).sum())
    return {
        "samples": int(len(samples)),
        "flyable_samples": flyable,
        "blocked_samples": int((~air_ok).sum()),
        "covered_samples": seen,
        "coverage": round(seen / flyable, 5) if flyable else None,
        "longest_gap_m": round(max((g["length_m"] for g in gaps), default=0.0), 1),
        "gaps": gaps,
        "by_modality": {name: int((by_modality[name] & air_ok).sum()) for name in MODALITIES},
    }


def assess(scene, points, step=50.0, request=None):
    """返回航路上每个采样点被哪些模态看见，以及连续覆盖缺口。"""
    samples, arc = resample(points, step)
    request=request or core.default_volume()
    air_ok=core.valid_points(scene,samples)
    by_modality = {name: np.zeros(len(samples), dtype=bool) for name in MODALITIES}
    watchers = []
    for device in scene["devices"]:
        kind = _kind(device)
        if kind not in by_modality:
            continue
        hits = _hits(device, scene, samples, request)
        watchers.append((device, hits))
        by_modality[kind] |= hits>=request.pd_threshold
    summary = _summarize(samples, arc, air_ok, by_modality, step, _fused(watchers,len(samples))>=request.pd_threshold)
    summary.update(core.metadata(request),sampling="航路实际坐标，非体素索引裁剪",metric_scope="航路采样覆盖率，非空间覆盖率")
    fused=_fused(watchers,len(samples))
    rows = []
    for i, point in enumerate(samples):
        rows.append({"x": round(float(point[0]), 1), "y": round(float(point[1]), 1), "z": round(float(point[2]), 1),
                     "along_m": round(float(arc[i]), 1), "in_air": bool(air_ok[i]), "pd":float(fused[i]), "covered":bool(fused[i]>=request.pd_threshold and air_ok[i]), "sensor_ids": [device["id"] for device,hits in watchers if hits[i]>0],
                     "watchers": [d["id"] for d,hits in watchers if hits[i]>0],
                     **{name.lower(): bool(by_modality[name][i]) for name in MODALITIES}})
    summary["samples_detail"] = rows
    return summary


def _pointing(device, target):
    dx = float(target[0] - device["x"])
    dy = float(target[1] - device["y"])
    dz = float(target[2] - device["z"])
    horizontal = math.hypot(dx, dy)
    azimuth = math.degrees(math.atan2(dx, dy)) % 360
    elevation = math.degrees(math.atan2(dz, max(horizontal, 1e-9)))
    return round(azimuth, 1), round(elevation, 1)


def _schedule(samples, arc, air_ok, watchers):
    """站址固定后，把航路分成可见站点相同的段落，给出指向和扫描周期。"""
    rows = []
    index = 0
    count = len(samples)
    while index < count:
        if not air_ok[index]:
            index += 1
            continue
        seeing = tuple(device["id"] for device, hits in watchers if hits[index])
        end = index + 1
        while end < count and air_ok[end] and tuple(device["id"] for device, hits in watchers if hits[end]) == seeing:
            end += 1
        target = samples[index:end].mean(axis=0)
        sites = []
        modalities = []
        for device, hits in watchers:
            if device["id"] not in seeing:
                continue
            azimuth, elevation = _pointing(device, target)
            kind = _kind(device)
            sites.append({"id": device["id"], "type": kind, "azimuth": azimuth, "elevation": elevation})
            if kind not in modalities:
                modalities.append(kind)
        period = None if not sites else (2 if len(modalities) == 1 else 4)
        rows.append({"from_m": round(float(arc[index]), 1), "to_m": round(float(arc[end - 1]), 1),
                     "samples": int(end - index), "covered": bool(sites), "modalities": modalities,
                     "sites": sites, "target": [round(float(v), 1) for v in target], "scan_period_s": period,
                     "cooperate": [site["id"] for site in sites] if len(sites) >= 2 else []})
        index = end
    return rows


def _site_row(device, role):
    return {"id": device["id"], "name": device.get("name", device["id"]), "type": _kind(device),
            "x": device["x"], "y": device["y"], "z": device["z"],
            "install_azimuth": device.get("azimuth", 0), "install_pitch": device.get("pitch", 0),
            "cost": device.get("cost", 0), "role": role}


def plan_route(scene, points, budget, step=50.0, fixed_network=False, offline_ids=None, request=None):
    """贪心增补候选站，使航路上可飞格子被雷达、光电或无线电看见。"""
    import copy
    scene = copy.deepcopy(scene)
    offline_ids = set(offline_ids or [])
    unknown = offline_ids - {d["id"] for d in scene["devices"]}
    if unknown:
        raise ValueError("故障设备不属于当前部署：" + ", ".join(sorted(unknown)))
    request=request or core.default_volume()
    baseline = assess(scene, points, step, request) if offline_ids else None
    for device in scene["devices"]:
        if device["id"] in offline_ids:
            device["status"] = "offline"
    samples, arc = resample(points, step)
    request=request or core.default_volume()
    air_ok=core.valid_points(scene,samples)
    installed = list(scene["devices"])
    by_modality = {name: np.zeros(len(samples), dtype=bool) for name in MODALITIES}
    watchers = []
    for device in installed:
        kind = _kind(device)
        if kind not in by_modality:
            continue
        hits = _hits(device, scene, samples, request)
        watchers.append((device, hits))
        by_modality[kind] |= hits>=request.pd_threshold
    fused=_fused(watchers,len(samples))
    covered=fused>=request.pd_threshold
    before = _summarize(samples, arc, air_ok, by_modality, step, covered)
    pool = []
    for device in ([] if fixed_network else scene["candidates"]):
        if device["id"] in {d["id"] for d in installed}:
            continue
        pool.append((device, _hits(device, scene, samples, request)))
    chosen = []
    spent = 0.0
    while pool and np.any(air_ok & ~covered):
        ranked = []
        for device, hits in pool:
            if spent + device["cost"] > budget:
                continue
            gain = int(np.count_nonzero((1-(1-fused)*(1-hits)>=request.pd_threshold) & air_ok & ~covered))
            if gain <= 0:
                continue
            ranked.append((gain / max(device["cost"], 0.01), device, hits))
        if not ranked:
            break
        _, device, hits = max(ranked, key=lambda item: item[0])
        chosen.append(device)
        spent += device["cost"]
        watchers.append((device, hits))
        kind = _kind(device)
        if kind in by_modality:
            by_modality[kind] |= hits>=request.pd_threshold
        fused=1-(1-fused)*(1-hits)
        covered=fused>=request.pd_threshold
        pool = [(other, seen) for other, seen in pool if other["id"] != device["id"]]
    after = _summarize(samples, arc, air_ok, by_modality, step, covered)
    fused=_fused(watchers,len(samples))
    rows = []
    for i, point in enumerate(samples):
        rows.append({"x": round(float(point[0]), 1), "y": round(float(point[1]), 1), "z": round(float(point[2]), 1),
                     "along_m": round(float(arc[i]), 1), "in_air": bool(air_ok[i]), "pd":float(fused[i]), "covered":bool(fused[i]>=request.pd_threshold and air_ok[i]), "sensor_ids": [device["id"] for device,hits in watchers if hits[i]>0],
                     "watchers": [d["id"] for d,hits in watchers if hits[i]>0],
                     **{name.lower(): bool(by_modality[name][i]) for name in MODALITIES}})
    schedule = _schedule(samples, arc, air_ok, [(d,pd>0) for d,pd in watchers])
    for row in schedule:
        selected=(arc>=row["from_m"]-.1)&(arc<=row["to_m"]+.1)
        row["covered"]=bool(covered[selected].all()) if selected.any() else False
    sites = [_site_row(device, "已有") for device, _hits_row in watchers if device in installed]
    sites += [_site_row(device, "新增") for device in chosen]
    flyable = before["flyable_samples"]
    return {**core.metadata(request),"metric_scope":"航路采样覆盖率，非空间覆盖率","id": uuid.uuid4().hex[:12], "scene": scene["id"], "scene_revision": scene["revision"],
            "created_at": now(), "method": "固定部署航路评估与分段协同草案" if fixed_network else "航路连续覆盖一次推演：贪心补站，再按可见站安排指向和扫描周期",
            "fixed_network": fixed_network, "offline_ids": sorted(offline_ids),
            "baseline": baseline, "preview_only": True,
            "step_m": step, "budget": budget, "added_cost": round(spent, 2), "selected": chosen,
            "sites": sites, "schedule": schedule,
            "before": before, "after": after,
            "target_met": flyable > 0 and after["covered_samples"] == flyable,
            "samples": rows,
            "note": "一次推演给出站址表和时段表。指向与扫描周期只对这条航路有效，不改设备安装角，也不向飞机发送航线。未使用学习策略。"}
