"""Reproducible synthetic scenes. All coordinates are local metres, not survey data."""
import math
import random

TYPES = {"RADAR": "低空雷达", "EO": "光电设备", "RF": "无线电侦测", "IR": "红外设备", "ADSB": "合作目标报文"}
CATALOG = {
    "IR": {"range": 650, "fov": 60, "cost": 8.0},
    "RADAR": {"range": 1100, "fov": 180, "cost": 28.0},
    "RF": {"range": 850, "fov": 360, "cost": 12.0},
    "EO": {"range": 800, "fov": 90, "cost": 6.0},
    "ADSB": {"range": 1500, "fov": 360, "cost": 3.5},
}
SCENES = [
    {"id": "urban", "name": "园区高密度建筑区", "short": "园区补盲", "purpose": "建筑遮挡 · 三维覆盖 · 多模态互补", "seed": 32},
    {"id": "corridor", "name": "物流主干道路", "short": "廊道跟踪", "purpose": "连续覆盖 · 跨站航迹 · 协同观测", "seed": 11},
    {"id": "electromagnetic", "name": "园区复杂电磁区", "short": "干扰评估", "purpose": "共址干扰 · 信号质量 · 资源调度", "seed": 18},
]


def seed(scene_id):
    config = next(s for s in SCENES if s["id"] == scene_id)
    rng = random.Random(config["seed"])
    buildings = []
    for ix in range(7):
        for iy in range(7):
            if (scene_id == "corridor" and 2 <= iy <= 4) or rng.random() < .15:
                continue
            buildings.append({"x": 100 + ix * 255 + rng.randint(-20, 20),
                              "y": 100 + iy * 255 + rng.randint(-20, 20),
                              "w": rng.randint(75, 160), "d": rng.randint(65, 155),
                              "h": rng.randint(25, 105)})
    sites = [(360, 480), (1450, 1350), (400, 1490), (1520, 480), (970, 1020), (820, 400), (1120, 1640)]
    modalities = ["RADAR", "RADAR", "RF", "RF", "EO", "EO", "ADSB"]
    devices = []
    for i, ((x, y), kind) in enumerate(zip(sites, modalities)):
        devices.append({"id": f"{kind}-{i + 1:02d}", "name": ["西南通信塔", "东北通信塔", "西北楼宇站", "东南杆塔站", "核心区光电站", "南侧光电站", "北侧报文站"][i],
                        "type": kind, "x": x, "y": y, "z": 115, "azimuth": [45,225,0,0,330,30,0][i],
                        "pitch":10,"vertical_fov":110,"status": "online", **CATALOG[kind]})
    candidates = []
    for i, (x, y) in enumerate([(250,950),(1000,1750),(1770,960),(1050,200),(650,1300),(1320,720)]):
        candidates.append({"id": f"C-{i+1:02d}", "name": f"候选站址 {i+1:02d}", "type": "RF" if i%2 else "RADAR",
                           "x": x, "y": y, "z": 125,"pitch":10,"vertical_fov":110, "azimuth": (i*65)%360, "status": "online",
                           **CATALOG["RF" if i%2 else "RADAR"]})
    return {**config, "width": 2000, "height": 2000, "altitude_max": 300,
            "data_mode": "synthetic", "revision": 1, "devices": devices, "buildings": buildings,
            "candidates": candidates, "zone": {"x0":700,"y0":700,"x1":1300,"y1":1300},
            "updated_at": None}


def trajectories(scene):
    """Deterministic truth trajectories and rule-based alerts, not an implemented tracker."""
    tracks=[]
    for j in range(3):
        points=[]
        for t in range(121):
            if j == 0:
                x,y=150+t*14, 900+180*math.sin(t/22)
            elif j == 1:
                x,y=450+290*math.sin(t/25), 200+t*12
            else:
                x,y=1450+210*math.cos(t/20), 1200+260*math.sin(t/20)
            points.append([round(x,1), round(y,1), round(155+35*math.sin(t/18+j),1)])
        tracks.append({"id":f"UAV-{j+1:03d}","label":["物流穿越航迹","建筑区巡航航迹","外围巡检航迹"][j],
                       "source":"模拟真值航迹", "points":points})
    zone=scene["zone"]
    events=[]
    for track in tracks:
        for t,(x,y,z) in enumerate(track["points"]):
            if zone["x0"]<=x<=zone["x1"] and zone["y0"]<=y<=zone["y1"]:
                events.append({"id":f"zone-{track['id']}","target":track["id"],"time":t,
                               "level":"关注","title":"进入重点监测区域","detail":"电子围栏规则触发 · 模拟航迹",
                               "position":[x,y,z]})
                break
    return {"tracks":tracks,"events":events,"duration":120,"mode":"synthetic_truth",
            "note":"航迹为模拟真值，告警由区域规则生成；尚未接入传感器量测、定位融合或跨站跟踪算法。"}
