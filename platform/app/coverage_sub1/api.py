"""REST 接口（FastAPI）：设备建档、覆盖计算、覆盖率、盲区诊断。

启动：uvicorn coverage_sub1.api:app --reload
"""
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import numpy as np

from .grid import Grid3D
from .environment import Environment
from .sensors import import_sensors
from .fusion import coverage_fusion
from .blind import diagnose_blind, regions_to_dict
from .metrics import coverage_rate, coverage_heatmap, zone_box_mask
from . import viz
from .georef import DEFAULT_LON0, DEFAULT_LAT0

app = FastAPI(title="多模态三维感知覆盖数字映射子模块", version="0.1")

# 允许前端跨域访问（开发期放开，生产按需收紧）
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# 简易内存态（生产环境应替换为 PostGIS / 任务队列）
STATE = {"sensors": [], "grid": None, "env": None, "fusion": None,
         "zone": (1200, 1200, 1800, 1800), "regions": None}


class GridCfg(BaseModel):
    nx: int = 100
    ny: int = 100
    nz: int = 24
    cell: float = 10.0
    z_air_min: float = 30.0
    z_air_max: float = 270.0


class Zone(BaseModel):
    x0: float; y0: float; x1: float; y1: float


@app.post("/sensors")
def post_sensors(payload: list = Body(...)):
    STATE["sensors"] = import_sensors(payload)
    return {"count": len(STATE["sensors"])}


@app.post("/coverage/compute")
def compute(cfg: GridCfg):
    if not STATE["sensors"]:
        raise HTTPException(400, "请先 POST /sensors 导入设备")
    grid = Grid3D(cfg.nx, cfg.ny, cfg.nz, cfg.cell,
                  z_air_min=cfg.z_air_min, z_air_max=cfg.z_air_max)
    env = Environment.synthetic(grid)
    fusion = coverage_fusion(STATE["sensors"], grid, env, by_type=True)
    STATE.update(grid=grid, env=env, fusion=fusion)
    return {"rate_all": round(coverage_rate(fusion["union"], grid), 4),
            "covered_voxels": int(fusion["union"].sum())}


@app.get("/coverage/rate")
def rate(x0: Optional[float] = None, y0: Optional[float] = None,
         x1: Optional[float] = None, y1: Optional[float] = None):
    _require_fusion()
    grid, fusion = STATE["grid"], STATE["fusion"]
    zone = None
    if None not in (x0, y0, x1, y1):
        zone = zone_box_mask(grid, x0, y0, x1, y1)
    return {"rate": round(coverage_rate(fusion["union"], grid, zone), 4)}


@app.get("/coverage/heatmap")
def heatmap():
    _require_fusion()
    hm = coverage_heatmap(STATE["fusion"]["count"], STATE["grid"])
    return {"shape": list(hm.shape), "max": float(hm.max()), "data": hm.tolist()}


@app.get("/blind/diagnose")
def blind():
    _require_fusion()
    regions = diagnose_blind(STATE["fusion"]["union"], STATE["grid"], STATE["env"])
    STATE["regions"] = regions
    return {"count": len(regions), "regions": regions_to_dict(regions)}


# ---------------- 前端可渲染格式（Cesium）----------------
@app.get("/viz/devices")
def viz_devices():
    if not STATE["sensors"]:
        raise HTTPException(400, "请先 POST /sensors")
    return {"devices": viz.device_domes(STATE["sensors"])}


@app.get("/viz/heatmap.geojson")
def viz_heatmap():
    _require_fusion()
    hm = coverage_heatmap(STATE["fusion"]["count"], STATE["grid"])
    return viz.heatmap_geojson(hm, STATE["grid"])


@app.get("/viz/blind.geojson")
def viz_blind():
    _require_fusion()
    regions = STATE["regions"] or diagnose_blind(
        STATE["fusion"]["union"], STATE["grid"], STATE["env"])
    STATE["regions"] = regions
    return viz.blind_geojson(regions)


@app.get("/viz/voxels.json")
def viz_voxels(stride: int = 4, max_points: int = 20000):
    """真实融合覆盖体素点云（降采样），供前端三维点云渲染。"""
    _require_fusion()
    return viz.coverage_voxels(
        STATE["fusion"]["union"], STATE["fusion"]["count"], STATE["grid"],
        stride=stride, max_points=max_points)


@app.get("/viz/isosurface.json")
def viz_isosurface(ds: int = 2, smooth: float = 1.0):
    """真实融合覆盖体的等值面三角网格（实体表面）。"""
    _require_fusion()
    return viz.coverage_isosurface(
        STATE["fusion"]["union"], STATE["grid"], ds=ds, smooth=smooth)


@app.get("/timeline/step")
def timeline_step(k: int):
    """时间轴：仅启用前 k 台设备，重算覆盖并更新场景，返回统计（演示设备增减动态）。"""
    if not STATE["sensors"] or STATE["grid"] is None:
        raise HTTPException(400, "请先 POST /sensors 和 /coverage/compute")
    k = max(1, min(k, len(STATE["sensors"])))
    subset = STATE["sensors"][:k]
    grid, env = STATE["grid"], STATE["env"]
    fusion = coverage_fusion(subset, grid, env, by_type=True)
    regions = diagnose_blind(fusion["union"], grid, env)
    zmask = zone_box_mask(grid, *STATE["zone"])
    STATE["fusion"], STATE["regions"] = fusion, regions   # /viz/* 随之刷新
    return {
        "k": k,
        "active": [s.sid for s in subset],
        "stats": {
            "rate_all": round(coverage_rate(fusion["union"], grid), 4),
            "rate_zone": round(coverage_rate(fusion["union"], grid, zmask), 4),
            "cross_enhanced": int((fusion["count"] >= 2).sum()),
            "n_devices": k,
            "n_blind": len(regions),
        },
    }


@app.get("/viz/scene")
def viz_scene():
    """聚合场景：统计 + 设备圆顶 + 盲区 + 重点区（前端一次拉取）。"""
    _require_fusion()
    grid, fusion = STATE["grid"], STATE["fusion"]
    zone_box = STATE["zone"]
    zmask = zone_box_mask(grid, *zone_box)
    regions = STATE["regions"] or diagnose_blind(fusion["union"], grid, STATE["env"])
    STATE["regions"] = regions
    return {
        "stats": {
            "rate_all": round(coverage_rate(fusion["union"], grid), 4),
            "rate_zone": round(coverage_rate(fusion["union"], grid, zmask), 4),
            "cross_enhanced": int((fusion["count"] >= 2).sum()),
            "n_devices": len(STATE["sensors"]),
            "n_blind": len(regions),
        },
        "devices": viz.device_domes(STATE["sensors"]),
        "blind": viz.blind_geojson(regions),
        "zone": viz.zone_geojson(zone_box),
        "origin": {"lon": DEFAULT_LON0, "lat": DEFAULT_LAT0},
    }


def _require_fusion():
    if STATE["fusion"] is None:
        raise HTTPException(400, "请先 POST /coverage/compute")
