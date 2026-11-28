"""Standardized scenario-parameter and engineering-constraint modeling layer.

Covers the three model-driven optimization domains that require explicit physical
parameters and operational constraints rather than RL training data:
1. Device Siting (设备选址): structural load, mains/fiber readiness, site lease,
   radar-radar & radar-RF electromagnetic isolation, spectrum band overlap, and
   3D coverage + FIM/CRLB targets.
2. Route Planning (航路规划): UAV speed/climb/turn kinematics, wind vector,
   payload & battery SoC reserve, timed NFZs, 4D multi-UAV separation, time
   windows, and coupling with the Stage-1 sensing coverage field.
3. Task Scheduling (任务调度): multi-UAV mission time-window/pad/turnaround
   dispatch, directional sensor turret slew/dwell scheduling, and memory/VRAM +
   wireless-contention constrained cloud-edge-device DAG scheduling.
"""
from __future__ import annotations

import copy
import hashlib
import heapq
import json
import math
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import coverage_core as core, store
from .real_buildings_scene import apply_building_source
from .scenes import SCENES, seed

router = APIRouter(tags=['标准场景参数与约束建模'])

SCHEMA_VERSION = 'standard-modeling-v1'
SceneId = Literal['urban', 'corridor', 'electromagnetic']


def _digest(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()
    ).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# 1. Device Siting Models & Constraints
# ---------------------------------------------------------------------------

class EngineeringSite(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)
    min_height: float = Field(default=5, ge=1, le=300)
    max_height: float = Field(default=160, ge=1, le=300)
    capacity: int = Field(default=4, ge=1, le=30)
    max_load_kg: float = Field(default=180, ge=5, le=5000)
    power_w: float = Field(default=2500, ge=50, le=200000)
    bandwidth_mbps: float = Field(default=500, ge=1, le=100000)
    mains_power_ready: bool = True
    fiber_ready: bool = True
    lease_approved: bool = True
    setup_cost: float = Field(default=3.0, ge=0, le=500)

    @model_validator(mode='after')
    def check_heights(self):
        if self.min_height > self.max_height:
            raise ValueError(f'站址 {self.id} 最低挂高不能大于最高挂高')
        return self


class StandardEquipmentModel(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    type: Literal['RADAR', 'EO', 'RF', 'IR', 'ADSB']
    range: float = Field(gt=50, le=20000)
    fov: float = Field(gt=5, le=360)
    vertical_fov: float = Field(default=110, gt=5, le=180)
    cost: float = Field(ge=0, le=2000)
    weight_kg: float = Field(default=25, gt=0.5, le=1000)
    power_w: float = Field(default=180, ge=5, le=50000)
    bandwidth_mbps: float = Field(default=25, ge=0.5, le=10000)
    requires_mains: bool = True
    requires_fiber: bool = False
    freq_low_mhz: float = Field(default=2400, ge=0, le=1000000)
    freq_high_mhz: float = Field(default=2483.5, ge=0, le=1000000)

    @model_validator(mode='after')
    def check_band(self):
        if self.freq_low_mhz > self.freq_high_mhz:
            raise ValueError(f'型号 {self.id} 频段下界不能高于上界')
        return self


class SiteDeviceSpec(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    site_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    model_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    z: float = Field(ge=1, le=300)
    azimuth: float = Field(default=0, ge=0, le=360)
    pitch: float = Field(default=8, ge=-85, le=85)
    status: Literal['online', 'offline'] = 'online'


class AuthorizedCountermeasureSite(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)
    z: float = Field(default=48.0, ge=2, le=300)
    azimuth: float = Field(default=45.0, ge=0, le=360)
    fov: float = Field(default=85.0, gt=5, le=360)
    range_m: float = Field(default=560.0, gt=50, le=5000)
    authority: str = Field(default='公安/重点目标法定授权点位（《条例》第43条），平台仅作感反电磁兼容评估，不下发控制指令', max_length=160)


class SitingModel(StrictModel):
    building_source: Literal['synthetic', 'xiongan_gba', 'beijing_osm'] = 'synthetic'
    terrain_elevation_m: float = Field(default=12.0, ge=0, le=200)
    noise_floor_dbm: float = Field(default=-95.0, ge=-120, le=-55)
    rain_fog_attenuation_db_km: float = Field(default=0.8, ge=0, le=12)
    cochannel_db: float = Field(default=3.0, ge=0, le=30)
    communication_db: float = Field(default=2.0, ge=0, le=30)
    pd_threshold: float = Field(default=0.5, ge=0.1, le=0.99)
    precision_m: float = Field(default=30.0, ge=5, le=300)
    grid_cell_m: float = Field(default=50.0, ge=25, le=100)
    budget: float = Field(default=68.0, ge=0, le=2000)
    max_new_sites: int = Field(default=4, ge=0, le=20)
    coverage_target: float = Field(default=0.82, ge=0.05, le=1.0)
    focus_target: float = Field(default=0.88, ge=0.05, le=1.0)
    precision_rate_target: float = Field(default=0.68, ge=0.0, le=1.0)
    min_radar_separation_m: float = Field(default=260.0, ge=0, le=1800)
    min_radar_rf_isolation_m: float = Field(default=90.0, ge=0, le=1200)
    min_band_overlap_separation_m: float = Field(default=150.0, ge=0, le=1800)
    min_jammer_rf_isolation_m: float = Field(default=180.0, ge=0, le=1800)
    jammer_suppression_db: float = Field(default=6.0, ge=0, le=35)
    countermeasure_sites: list[AuthorizedCountermeasureSite] = Field(default_factory=list, max_length=10)
    sites: list[EngineeringSite] = Field(min_length=1, max_length=40)
    models: list[StandardEquipmentModel] = Field(min_length=1, max_length=30)
    existing_devices: list[SiteDeviceSpec] = Field(max_length=30)
    candidate_devices: list[SiteDeviceSpec] = Field(max_length=25)

    @model_validator(mode='after')
    def check_references(self):
        site_ids = [s.id for s in self.sites]
        model_ids = [m.id for m in self.models]
        if len(site_ids) != len(set(site_ids)):
            raise ValueError('站址编号存在重复')
        if len(model_ids) != len(set(model_ids)):
            raise ValueError('设备型号编号存在重复')
        all_devs = self.existing_devices + self.candidate_devices
        if len(all_devs) != len({d.id for d in all_devs}):
            raise ValueError('已有与候选设备编号必须唯一')
        site_set, model_set = set(site_ids), set(model_ids)
        for d in all_devs:
            if d.site_id not in site_set:
                raise ValueError(f'设备 {d.id} 引用了不存在的站址 {d.site_id}')
            if d.model_id not in model_set:
                raise ValueError(f'设备 {d.id} 引用了不存在的型号 {d.model_id}')
        return self


# ---------------------------------------------------------------------------
# 2. Route Planning Models & Constraints
# ---------------------------------------------------------------------------

class TimedNoFlyZone(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    x0: float = Field(ge=0, le=2000)
    y0: float = Field(ge=0, le=2000)
    x1: float = Field(ge=0, le=2000)
    y1: float = Field(ge=0, le=2000)
    z0: float = Field(default=0, ge=0, le=300)
    z1: float = Field(default=220, ge=10, le=300)
    active_start_s: float = Field(default=0, ge=0, le=86400)
    active_end_s: float = Field(default=600, ge=0, le=86400)

    @model_validator(mode='after')
    def ordered(self):
        if self.x0 >= self.x1 or self.y0 >= self.y1 or self.z0 >= self.z1:
            raise ValueError(f'禁飞区 {self.id} 坐标范围上下界错误')
        if self.active_start_s > self.active_end_s:
            raise ValueError(f'禁飞区 {self.id} 生效时间窗顺序错误')
        return self


class PeerTrajectory(StrictModel):
    uav_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    depart_s: float = Field(default=0, ge=0, le=3600)
    speed_mps: float = Field(default=13.0, gt=1, le=40)
    waypoints: list[tuple[float, float, float]] = Field(min_length=2, max_length=20)


class RoutingModel(StrictModel):
    vehicle_name: str = Field(default='园区四旋翼巡检机 X4-Pro', min_length=1, max_length=80)
    cruise_speed_mps: float = Field(default=14.0, ge=4, le=35)
    max_climb_rate_mps: float = Field(default=3.5, ge=0.5, le=12)
    max_descent_rate_mps: float = Field(default=2.8, ge=0.5, le=10)
    min_turn_radius_m: float = Field(default=22.0, ge=0, le=150)
    battery_wh: float = Field(default=380.0, ge=50, le=5000)
    cruise_power_w: float = Field(default=420.0, ge=50, le=5000)
    climb_extra_power_w: float = Field(default=220.0, ge=0, le=3000)
    payload_kg: float = Field(default=1.6, ge=0, le=30)
    max_payload_kg: float = Field(default=3.5, gt=0.1, le=50)
    reserve_soc: float = Field(default=0.25, ge=0.1, le=0.6)
    start_xyz: tuple[float, float, float] = (180.0, 240.0, 0.0)
    goal_xyz: tuple[float, float, float] = (1820.0, 1740.0, 0.0)
    cruise_altitude_m: float = Field(default=125.0, ge=35, le=280)
    altitude_bounds_m: tuple[float, float] = (35.0, 260.0)
    obstacle_clearance_m: float = Field(default=22.0, ge=10, le=80)
    wind_xy_mps: tuple[float, float] = (3.0, -1.5)
    depart_s: float = Field(default=10.0, ge=0, le=3600)
    deadline_s: float = Field(default=360.0, gt=10, le=7200)
    min_uav_separation_m: float = Field(default=60.0, ge=15, le=300)
    min_route_coverage: float = Field(default=0.85, ge=0, le=1)
    max_blind_gap_m: float = Field(default=180.0, ge=0, le=2000)
    sensing_weight: float = Field(default=1.6, ge=0, le=6)
    no_fly_zones: list[TimedNoFlyZone] = Field(default_factory=list, max_length=12)
    peer_uavs: list[PeerTrajectory] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def check_bounds(self):
        z0, z1 = self.altitude_bounds_m
        if z0 < 20 or z1 > 300 or z0 >= z1:
            raise ValueError('巡航高度包线须位于 20–300 m 且下界小于上界')
        if not (z0 <= self.cruise_altitude_m <= z1):
            raise ValueError('基准巡航高度须位于高度包线范围内')
        for label, pt in (('起点', self.start_xyz), ('终点', self.goal_xyz)):
            if not (0 <= pt[0] <= 2000 and 0 <= pt[1] <= 2000 and 0 <= pt[2] <= 300):
                raise ValueError(f'{label}超出场景三维范围')
        if math.hypot(self.goal_xyz[0] - self.start_xyz[0], self.goal_xyz[1] - self.start_xyz[1]) < 50:
            raise ValueError('起终点水平距离至少 50 m')
        if self.depart_s >= self.deadline_s:
            raise ValueError('最晚到达时间须晚于起飞时间')
        return self


# ---------------------------------------------------------------------------
# 3. Task Scheduling Models & Constraints (Fleet + Sensor Turrets + Compute DAG)
# ---------------------------------------------------------------------------

class FleetVehicleSpec(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    max_payload_kg: float = Field(default=3.5, gt=0.1, le=50)
    battery_wh: float = Field(default=380.0, ge=50, le=5000)
    reserve_soc: float = Field(default=0.25, ge=0.1, le=0.6)
    turnaround_charge_s: float = Field(default=90.0, ge=15, le=3600)
    available_from_s: float = Field(default=0.0, ge=0, le=3600)


class FlightMissionDemand(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    priority: int = Field(default=5, ge=1, le=10)
    duration_s: float = Field(default=180.0, gt=20, le=3600)
    required_payload_kg: float = Field(default=1.5, ge=0, le=40)
    energy_wh: float = Field(default=190.0, gt=5, le=4000)
    earliest_start_s: float = Field(default=0.0, ge=0, le=7200)
    deadline_s: float = Field(default=600.0, gt=30, le=14400)

    @model_validator(mode='after')
    def check_window(self):
        if self.earliest_start_s + self.duration_s > self.deadline_s:
            raise ValueError(f'任务 {self.id} 最早开始加执行时长已超过截止时间')
        return self


class TrackingTargetDemand(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=80)
    position_xyz: tuple[float, float, float]
    priority: int = Field(default=5, ge=1, le=10)
    required_modalities: int = Field(default=2, ge=1, le=3)
    max_revisit_s: float = Field(default=6.0, ge=1.0, le=30.0)


class ComputeNodeSpec(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    kind: Literal['device', 'edge', 'cloud']
    position: tuple[float, float, float]
    gcycles_s: float = Field(gt=0.1, le=1000)
    power_w: float = Field(ge=1, le=10000)
    memory_gb: float = Field(default=8.0, ge=0.5, le=1024)
    vram_gb: float = Field(default=4.0, ge=0, le=512)
    busy_s: float = Field(default=0.0, ge=0, le=600)


class ComputeLinkSpec(StrictModel):
    source: str
    target: str
    mbps: float = Field(gt=0.5, le=100000)
    latency_s: float = Field(ge=0, le=10)
    max_range_m: float = Field(ge=0, le=100000)


class ComputeDagTaskSpec(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    gcycles: float = Field(gt=0.05, le=1000)
    memory_gb: float = Field(default=1.0, ge=0.1, le=256)
    requires_vram_gb: float = Field(default=0.0, ge=0, le=128)
    input_mbit: float = Field(default=0.0, ge=0, le=10000)
    output_mbit: float = Field(default=1.0, ge=0, le=10000)
    deadline_s: float = Field(default=5.0, gt=0.1, le=600)
    parents: list[str] = Field(default_factory=list, max_length=10)


class SchedulingModel(StrictModel):
    pad_capacity: int = Field(default=2, ge=1, le=10)
    pilot_concurrency: int = Field(default=2, ge=1, le=10)
    pad_lock_s: float = Field(default=35.0, ge=10, le=300)
    uavs: list[FleetVehicleSpec] = Field(min_length=1, max_length=12)
    missions: list[FlightMissionDemand] = Field(min_length=1, max_length=20)
    turret_horizon_s: float = Field(default=12.0, ge=4, le=60)
    turret_slew_deg_s: float = Field(default=45.0, ge=10, le=180)
    turret_dwell_s: float = Field(default=1.1, ge=0.3, le=8.0)
    targets: list[TrackingTargetDemand] = Field(min_length=1, max_length=12)
    shared_uav_count: int = Field(default=2, ge=1, le=12)
    hard_deadline_s: float = Field(default=4.5, gt=0.3, le=60)
    nodes: list[ComputeNodeSpec] = Field(min_length=1, max_length=8)
    links: list[ComputeLinkSpec] = Field(min_length=1, max_length=32)
    dag_tasks: list[ComputeDagTaskSpec] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def check_dag(self):
        if len({u.id for u in self.uavs}) != len(self.uavs):
            raise ValueError('机群飞机编号不能重复')
        if len({m.id for m in self.missions}) != len(self.missions):
            raise ValueError('飞行任务编号不能重复')
        node_ids = {n.id for n in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError('算力节点编号重复')
        if 'device' not in node_ids or sum(n.kind == 'device' for n in self.nodes) != 1:
            raise ValueError('算力调度须且仅能包含一个编号为 device 的机载节点')
        task_map = {t.id: t for t in self.dag_tasks}
        if len(task_map) != len(self.dag_tasks):
            raise ValueError('计算子任务编号重复')
        for link in self.links:
            if link.source not in node_ids or link.target not in node_ids or link.source == link.target:
                raise ValueError(f'算力链路端点无效：{link.source}->{link.target}')
        for t in self.dag_tasks:
            if any(p not in task_map for p in t.parents):
                raise ValueError(f'子任务 {t.id} 引用了不存在的父任务')
        done = set()
        while len(done) < len(task_map):
            ready = {t.id for t in self.dag_tasks if t.id not in done and set(t.parents) <= done}
            if not ready:
                raise ValueError('计算 DAG 存在循环依赖')
            done |= ready
        return self


class StandardModelSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    scene: SceneId = 'urban'
    revision: int = Field(default=1, ge=0)
    title: str = Field(default='园区标准场景参数与工程约束模型', min_length=1, max_length=120)
    siting: SitingModel
    routing: RoutingModel
    scheduling: SchedulingModel


class SolveRequest(StrictModel):
    stage: Literal['siting', 'routing', 'scheduling', 'all'] = 'all'
    spec: StandardModelSpec | None = None


# ---------------------------------------------------------------------------
# Default Realistic Engineering Presets per Scene
# ---------------------------------------------------------------------------

def default_models() -> list[dict]:
    return [
        dict(id='MDL-RADAR-X1', name='Ku波段低空相控阵雷达', type='RADAR', range=1150, fov=180, vertical_fov=95,
             cost=28.0, weight_kg=62.0, power_w=650.0, bandwidth_mbps=80.0, requires_mains=True, requires_fiber=True,
             freq_low_mhz=15800, freq_high_mhz=16200),
        dict(id='MDL-RADAR-C2', name='C波段补盲微雷达', type='RADAR', range=820, fov=120, vertical_fov=90,
             cost=17.5, weight_kg=34.0, power_w=320.0, bandwidth_mbps=45.0, requires_mains=True, requires_fiber=True,
             freq_low_mhz=5725, freq_high_mhz=5850),
        dict(id='MDL-RF-WIDE', name='全向无线电侦测站', type='RF', range=880, fov=360, vertical_fov=115,
             cost=12.0, weight_kg=18.0, power_w=110.0, bandwidth_mbps=18.0, requires_mains=False, requires_fiber=False,
             freq_low_mhz=2400, freq_high_mhz=5850),
        dict(id='MDL-EO-HD', name='双光云台光电跟踪站', type='EO', range=800, fov=95, vertical_fov=85,
             cost=6.5, weight_kg=22.0, power_w=140.0, bandwidth_mbps=60.0, requires_mains=True, requires_fiber=True,
             freq_low_mhz=0.0, freq_high_mhz=0.0),
        dict(id='MDL-IR-LOW', name='制冷红外低空警戒仪', type='IR', range=680, fov=75, vertical_fov=80,
             cost=8.5, weight_kg=16.0, power_w=95.0, bandwidth_mbps=30.0, requires_mains=False, requires_fiber=False,
             freq_low_mhz=0.0, freq_high_mhz=0.0),
        dict(id='MDL-ADSB-RX', name='Remote-ID/ADS-B合作接收机', type='ADSB', range=1500, fov=360, vertical_fov=130,
             cost=3.5, weight_kg=6.0, power_w=35.0, bandwidth_mbps=4.0, requires_mains=False, requires_fiber=False,
             freq_low_mhz=1088, freq_high_mhz=1092),
    ]


def default_spec(scene_id: SceneId) -> dict:
    raw = seed(scene_id)
    meta = next(s for s in SCENES if s['id'] == scene_id)
    type_to_model = {
        'RADAR': 'MDL-RADAR-X1',
        'RF': 'MDL-RF-WIDE',
        'EO': 'MDL-EO-HD',
        'IR': 'MDL-IR-LOW',
        'ADSB': 'MDL-ADSB-RX',
    }
    sites = []
    existing = []
    for idx, dev in enumerate(raw['devices']):
        sid = f'SITE-E{idx + 1:02d}'
        sites.append(dict(
            id=sid, name=f'{dev["name"]}站址', x=dev['x'], y=dev['y'],
            min_height=20, max_height=165, capacity=3,
            max_load_kg=180.0, power_w=2200.0, bandwidth_mbps=400.0,
            mains_power_ready=True, fiber_ready=True, lease_approved=True, setup_cost=0.0,
        ))
        existing.append(dict(
            id=dev['id'], name=dev['name'], site_id=sid, model_id=type_to_model[dev['type']],
            z=dev['z'], azimuth=dev['azimuth'], pitch=dev.get('pitch', 10), status=dev['status'],
        ))

    cand_profiles = [
        # (max_load_kg, power_w, bandwidth_mbps, mains, fiber, lease, setup_cost, alt_model)
        (160.0, 2000.0, 350.0, True, True, True, 3.5, 'MDL-RADAR-X1'),
        (95.0, 900.0, 120.0, True, False, True, 2.0, 'MDL-RF-WIDE'),
        (170.0, 2400.0, 500.0, True, True, True, 4.0, 'MDL-RADAR-C2'),
        (140.0, 1800.0, 300.0, True, True, True, 2.8, 'MDL-EO-HD'),
        (150.0, 1600.0, 320.0, True, True, True, 3.0, 'MDL-RF-WIDE'),
        (175.0, 2200.0, 420.0, True, True, True, 3.2, 'MDL-EO-HD'),
    ]
    candidates = []
    for idx, cand in enumerate(raw['candidates']):
        sid = f'SITE-C{idx + 1:02d}'
        load_kg, pwr, bw, mains, fiber, lease, setup_cost, mid = cand_profiles[idx % len(cand_profiles)]
        sites.append(dict(
            id=sid, name=f'候选工程站址 {idx + 1:02d}', x=cand['x'], y=cand['y'],
            min_height=25, max_height=175, capacity=3,
            max_load_kg=load_kg, power_w=pwr, bandwidth_mbps=bw,
            mains_power_ready=mains, fiber_ready=fiber, lease_approved=lease, setup_cost=setup_cost,
        ))
        candidates.append(dict(
            id=cand['id'], name=f'{cand["name"]}主选设备', site_id=sid, model_id=mid,
            z=cand['z'], azimuth=cand['azimuth'], pitch=cand.get('pitch', 8), status='online',
        ))
        # Secondary complementary option on Fiber-ready sites for richer constrained siting choices
        if fiber and idx in (0, 2, 3, 5):
            sec_model = 'MDL-EO-HD' if 'RADAR' in mid else 'MDL-RADAR-C2'
            candidates.append(dict(
                id=f'{cand["id"]}-B', name=f'{cand["name"]}互补设备', site_id=sid, model_id=sec_model,
                z=min(165, cand['z'] + 10), azimuth=(cand['azimuth'] + 45) % 360, pitch=6, status='online',
            ))

    noise_dbm = {'urban': -95.0, 'corridor': -98.0, 'electromagnetic': -90.0}[scene_id]
    coch_db = {'urban': 3.0, 'corridor': 2.0, 'electromagnetic': 4.2}[scene_id]
    start_goal = {
        'urban': ((280.0, 380.0, 0.0), (1660.0, 1580.0, 0.0), 125.0),
        'corridor': ((180.0, 960.0, 0.0), (1820.0, 980.0, 0.0), 115.0),
        'electromagnetic': ((300.0, 420.0, 0.0), (1620.0, 1540.0, 0.0), 125.0),
    }[scene_id]

    nfz = [
        dict(id='NFZ-CORE-01', name='变电站净空临时管控区', x0=860, y0=880, x1=1020, y1=1020,
             z0=0, z1=210, active_start_s=0, active_end_s=500),
    ]
    peers = [
        dict(uav_id='UAV-PEER-02', name='廊道物流机 B02（分层巡航）', depart_s=15.0, speed_mps=13.5,
             waypoints=[(1780.0, 360.0, 195.0), (1180.0, 640.0, 195.0), (320.0, 1520.0, 195.0)]),
    ]
    cm_sites = [
        dict(id='CM-AUTH-01', name='重点区西南授权定向反制站', x=680.0, y=680.0, z=48.0,
             azimuth=45.0, fov=85.0, range_m=560.0),
    ]
    if scene_id == 'electromagnetic':
        cm_sites.append(dict(id='CM-AUTH-02', name='变电核心区东北受控压制点', x=1250.0, y=1180.0, z=52.0,
                             azimuth=225.0, fov=80.0, range_m=520.0))

    spec = StandardModelSpec(
        scene=scene_id,
        revision=1,
        title=f'{meta["name"]} · 标准参数与工程约束模型',
        siting=SitingModel(
            building_source='synthetic',
            terrain_elevation_m=12.0 if scene_id != 'corridor' else 8.0,
            noise_floor_dbm=noise_dbm,
            rain_fog_attenuation_db_km=0.6,
            cochannel_db=coch_db,
            communication_db=2.0 if scene_id != 'electromagnetic' else 3.0,
            pd_threshold=0.5,
            precision_m=30.0,
            grid_cell_m=50.0,
            budget=68.0 if scene_id != 'electromagnetic' else 82.0,
            max_new_sites=4 if scene_id != 'electromagnetic' else 5,
            coverage_target={'urban': 0.80, 'corridor': 0.85, 'electromagnetic': 0.68}[scene_id],
            focus_target={'urban': 0.88, 'corridor': 0.90, 'electromagnetic': 0.85}[scene_id],
            precision_rate_target={'urban': 0.58, 'corridor': 0.60, 'electromagnetic': 0.45}[scene_id],
            min_radar_separation_m=260.0,
            min_radar_rf_isolation_m=90.0,
            min_band_overlap_separation_m=150.0,
            min_jammer_rf_isolation_m=180.0,
            jammer_suppression_db=6.0,
            countermeasure_sites=cm_sites,
            sites=sites,
            models=default_models(),
            existing_devices=existing,
            candidate_devices=candidates,
        ),
        routing=RoutingModel(
            start_xyz=start_goal[0],
            goal_xyz=start_goal[1],
            cruise_altitude_m=start_goal[2],
            wind_xy_mps=(3.0, -1.5) if scene_id != 'corridor' else (4.2, 0.8),
            min_route_coverage={'urban': 0.80, 'corridor': 0.88, 'electromagnetic': 0.72}[scene_id],
            max_blind_gap_m={'urban': 260.0, 'corridor': 180.0, 'electromagnetic': 380.0}[scene_id],
            sensing_weight=2.2,
            no_fly_zones=nfz,
            peer_uavs=peers,
        ),
        scheduling=SchedulingModel(
            pad_capacity=2,
            pilot_concurrency=2,
            pad_lock_s=35.0,
            uavs=[
                dict(id='UAV-01', name='园区巡检机 A01', max_payload_kg=3.5, battery_wh=380.0, reserve_soc=0.25, turnaround_charge_s=85.0, available_from_s=0.0),
                dict(id='UAV-02', name='廊道物流机 B02', max_payload_kg=6.0, battery_wh=520.0, reserve_soc=0.22, turnaround_charge_s=110.0, available_from_s=20.0),
                dict(id='UAV-03', name='应急核查机 C03', max_payload_kg=2.2, battery_wh=290.0, reserve_soc=0.25, turnaround_charge_s=70.0, available_from_s=0.0),
            ],
            missions=[
                dict(id='MSN-101', name='重点园区低空盲区巡检', priority=9, duration_s=175.0, required_payload_kg=1.6, energy_wh=175.0, earliest_start_s=10.0, deadline_s=360.0),
                dict(id='MSN-102', name='主干廊道医疗物资投送', priority=8, duration_s=210.0, required_payload_kg=4.2, energy_wh=260.0, earliest_start_s=20.0, deadline_s=450.0),
                dict(id='MSN-103', name='电磁异常源抵近复核', priority=10, duration_s=140.0, required_payload_kg=1.8, energy_wh=145.0, earliest_start_s=0.0, deadline_s=260.0),
                dict(id='MSN-104', name='北侧楼宇周界例行巡查', priority=6, duration_s=165.0, required_payload_kg=1.4, energy_wh=160.0, earliest_start_s=120.0, deadline_s=620.0),
            ],
            turret_horizon_s=12.0,
            turret_slew_deg_s=45.0,
            turret_dwell_s=1.1,
            targets=[
                dict(id='TGT-A', name='重点区非合作微轻型目标', position_xyz=(960.0, 980.0, 120.0), priority=10, required_modalities=2, max_revisit_s=5.0),
                dict(id='TGT-B', name='西侧廊道穿越目标', position_xyz=(480.0, 920.0, 115.0), priority=8, required_modalities=2, max_revisit_s=6.0),
                dict(id='TGT-C', name='东北塔站周界盘旋目标', position_xyz=(1420.0, 1320.0, 135.0), priority=7, required_modalities=2, max_revisit_s=8.0),
            ],
            shared_uav_count=2,
            hard_deadline_s=4.5,
            nodes=[
                dict(id='device', kind='device', position=(600.0, 600.0, 125.0), gcycles_s=1.4, power_w=12.0, memory_gb=4.0, vram_gb=2.0, busy_s=0.0),
                dict(id='edge-a', kind='edge', position=(420.0, 520.0, 25.0), gcycles_s=9.5, power_w=42.0, memory_gb=16.0, vram_gb=8.0, busy_s=0.1),
                dict(id='edge-b', kind='edge', position=(1480.0, 1380.0, 25.0), gcycles_s=7.0, power_w=32.0, memory_gb=12.0, vram_gb=6.0, busy_s=0.2),
                dict(id='cloud', kind='cloud', position=(1950.0, 1950.0, 0.0), gcycles_s=28.0, power_w=95.0, memory_gb=64.0, vram_gb=24.0, busy_s=0.0),
            ],
            links=[
                dict(source='device', target='edge-a', mbps=36.0, latency_s=0.008, max_range_m=1350.0),
                dict(source='edge-a', target='device', mbps=50.0, latency_s=0.008, max_range_m=1350.0),
                dict(source='device', target='edge-b', mbps=28.0, latency_s=0.011, max_range_m=1350.0),
                dict(source='edge-b', target='device', mbps=40.0, latency_s=0.011, max_range_m=1350.0),
                dict(source='edge-a', target='cloud', mbps=120.0, latency_s=0.045, max_range_m=0.0),
                dict(source='cloud', target='edge-a', mbps=120.0, latency_s=0.045, max_range_m=0.0),
                dict(source='edge-b', target='cloud', mbps=90.0, latency_s=0.055, max_range_m=0.0),
                dict(source='cloud', target='edge-b', mbps=90.0, latency_s=0.055, max_range_m=0.0),
            ],
            dag_tasks=[
                dict(id='preprocess', gcycles=0.8, memory_gb=1.2, requires_vram_gb=0.0, input_mbit=14.0, output_mbit=5.0, deadline_s=1.5, parents=[]),
                dict(id='detect', gcycles=7.5, memory_gb=3.0, requires_vram_gb=3.5, input_mbit=0.0, output_mbit=1.2, deadline_s=3.4, parents=['preprocess']),
                dict(id='rf_demod', gcycles=4.2, memory_gb=2.0, requires_vram_gb=0.0, input_mbit=0.0, output_mbit=1.5, deadline_s=3.4, parents=['preprocess']),
                dict(id='fusion', gcycles=2.2, memory_gb=2.0, requires_vram_gb=0.0, input_mbit=0.0, output_mbit=0.4, deadline_s=4.1, parents=['detect', 'rf_demod']),
                dict(id='warning', gcycles=0.4, memory_gb=0.8, requires_vram_gb=0.0, input_mbit=0.0, output_mbit=0.05, deadline_s=4.5, parents=['fusion']),
            ],
        ),
    )
    return spec.model_dump()


# ---------------------------------------------------------------------------
# Stage 1 Engine: Siting Engineering Constraints & Solver
# ---------------------------------------------------------------------------

def _expand_device(dev: dict, sites: dict[str, dict], models: dict[str, dict]) -> dict:
    site = sites[dev['site_id']]
    model = models[dev['model_id']]
    return dict(
        id=dev['id'],
        name=dev['name'],
        site_id=site['id'],
        model_id=model['id'],
        type=model['type'],
        x=site['x'],
        y=site['y'],
        z=dev['z'],
        azimuth=dev['azimuth'],
        pitch=dev['pitch'],
        range=model['range'],
        fov=model['fov'],
        vertical_fov=model['vertical_fov'],
        cost=model['cost'],
        weight_kg=model['weight_kg'],
        power_w=model['power_w'],
        bandwidth_mbps=model['bandwidth_mbps'],
        requires_mains=model['requires_mains'],
        requires_fiber=model['requires_fiber'],
        freq_low_mhz=model['freq_low_mhz'],
        freq_high_mhz=model['freq_high_mhz'],
        status=dev['status'],
    )


def _bands_overlap(a: dict, b: dict) -> bool:
    if a['type'] in ('EO', 'IR') or b['type'] in ('EO', 'IR'):
        return False
    return max(a['freq_low_mhz'], b['freq_low_mhz']) <= min(a['freq_high_mhz'], b['freq_high_mhz'])


def check_siting_set(siting: dict, selected_candidates: list[dict]) -> tuple[list[dict], list[str], float]:
    sites = {s['id']: s for s in siting['sites']}
    models = {m['id']: m for m in siting['models']}
    existing = [_expand_device(d, sites, models) for d in siting['existing_devices']]
    selected = [_expand_device(d, sites, models) for d in selected_candidates]
    all_devs = existing + selected
    online_devs = [d for d in all_devs if d['status'] == 'online']

    existing_sites = {d['site_id'] for d in existing}
    new_sites = {d['site_id'] for d in selected} - existing_sites
    added_cost = round(
        sum(d['cost'] for d in selected) + sum(sites[sid]['setup_cost'] for sid in new_sites), 3
    )

    violations: list[str] = []
    # Per-site structural, lease, power, bandwidth, height, mains/fiber checks
    max_load_ratio = 0.0
    max_power_ratio = 0.0
    max_bw_ratio = 0.0
    utility_ok = True
    lease_ok = True
    height_ok = True

    for sid, site in sites.items():
        ds = [d for d in all_devs if d['site_id'] == sid]
        if not ds:
            continue
        if not site['lease_approved']:
            lease_ok = False
            violations.append(f'站址 {site["name"]} 尚未取得场地准入许可')
        if len(ds) > site['capacity']:
            violations.append(f'站址 {site["name"]} 槽位超限：{len(ds)}/{site["capacity"]}')
        total_wt = sum(d['weight_kg'] for d in ds)
        total_pw = sum(d['power_w'] for d in ds)
        total_bw = sum(d['bandwidth_mbps'] for d in ds)
        max_load_ratio = max(max_load_ratio, total_wt / max(site['max_load_kg'], 1e-6))
        max_power_ratio = max(max_power_ratio, total_pw / max(site['power_w'], 1e-6))
        max_bw_ratio = max(max_bw_ratio, total_bw / max(site['bandwidth_mbps'], 1e-6))
        if total_wt > site['max_load_kg'] + 1e-9:
            violations.append(f'站址 {site["name"]} 承重超限：{total_wt:.1f}/{site["max_load_kg"]:.1f} kg')
        if total_pw > site['power_w'] + 1e-9:
            violations.append(f'站址 {site["name"]} 供电超限：{total_pw:.0f}/{site["power_w"]:.0f} W')
        if total_bw > site['bandwidth_mbps'] + 1e-9:
            violations.append(f'站址 {site["name"]} 回传带宽超限：{total_bw:.0f}/{site["bandwidth_mbps"]:.0f} Mbps')
        for d in ds:
            if not (site['min_height'] <= d['z'] <= site['max_height']):
                height_ok = False
                violations.append(f'设备 {d["name"]} 挂高 {d["z"]} m 超出站址范围 [{site["min_height"]}, {site["max_height"]}] m')
            if d['requires_mains'] and not site['mains_power_ready']:
                utility_ok = False
                violations.append(f'设备 {d["name"]} 需要市电接入，但站址 {site["name"]} 无市电')
            if d['requires_fiber'] and not site['fiber_ready']:
                utility_ok = False
                violations.append(f'设备 {d["name"]} 需要光纤回传，但站址 {site["name"]} 无光纤')

    # Pairwise electromagnetic & spatial isolation checks
    min_rr = math.inf
    min_rrf = math.inf
    min_band = math.inf
    for i, a in enumerate(online_devs):
        for b in online_devs[i + 1:]:
            dist = math.dist((a['x'], a['y'], a['z']), (b['x'], b['y'], b['z']))
            if a['type'] == 'RADAR' and b['type'] == 'RADAR':
                min_rr = min(min_rr, dist)
                if dist < siting['min_radar_separation_m'] - 1e-6:
                    violations.append(f'雷达同频隔离不足：{a["id"]} 与 {b["id"]} 间距 {dist:.1f} m < {siting["min_radar_separation_m"]} m')
            if {a['type'], b['type']} == {'RADAR', 'RF'}:
                min_rrf = min(min_rrf, dist)
                if dist < siting['min_radar_rf_isolation_m'] - 1e-6:
                    violations.append(f'雷达与无线电侦测共址阻塞：{a["id"]} 与 {b["id"]} 间距 {dist:.1f} m < {siting["min_radar_rf_isolation_m"]} m')
            if _bands_overlap(a, b):
                min_band = min(min_band, dist)
                if dist < siting['min_band_overlap_separation_m'] - 1e-6:
                    violations.append(f'重叠频段设备隔离不足：{a["id"]} 与 {b["id"]} 间距 {dist:.1f} m < {siting["min_band_overlap_separation_m"]} m')

    # Sensing-Countermeasure EMC blocking check against statutory authorized countermeasure sites
    jammer_iso = float(siting.get('min_jammer_rf_isolation_m', 180.0))
    for d in online_devs:
        if d['type'] != 'RF':
            continue
        for cm in siting.get('countermeasure_sites', []):
            cm_dist = math.dist((d['x'], d['y'], d['z']), (cm['x'], cm['y'], cm['z']))
            if cm_dist < jammer_iso - 1e-6:
                violations.append(f'感反电磁阻塞：无源RF设备 {d["id"]} 距授权反制站 {cm["id"]} 仅 {cm_dist:.1f} m < {jammer_iso:.0f} m')

    if len(new_sites) > siting['max_new_sites']:
        violations.append(f'新建站址数超限：{len(new_sites)} > {siting["max_new_sites"]}')
    if added_cost > siting['budget'] + 1e-9:
        violations.append(f'新增工程总费用超预算：{added_cost:.2f} > {siting["budget"]:.2f} 万元')

    checks = [
        dict(code='SITE_LEASE_AND_HEIGHT', stage='siting', title='站址物业准入与挂高包线',
             passed=lease_ok and height_ok, actual='合规' if (lease_ok and height_ok) else '存在违约',
             limit='全部准入且挂高合规', unit='状态'),
        dict(code='SITE_LOAD_POWER_BW', stage='siting', title='站址承重 / 供电 / 回传带宽峰值占比',
             passed=max(max_load_ratio, max_power_ratio, max_bw_ratio) <= 1.0 + 1e-9,
             actual=f'{max(max_load_ratio, max_power_ratio, max_bw_ratio) * 100:.1f}%',
             limit='<= 100%', unit='%'),
        dict(code='SITE_MAINS_FIBER', stage='siting', title='市电接入与光纤回传匹配性',
             passed=utility_ok, actual='全部匹配' if utility_ok else '存在缺失',
             limit='高功耗雷达/光电须具备光纤市电', unit='状态'),
        dict(code='EMC_RADAR_SEPARATION', stage='siting', title='有源雷达站间最小电磁隔离距',
             passed=min_rr >= siting['min_radar_separation_m'] - 1e-6,
             actual='无双站' if math.isinf(min_rr) else f'{min_rr:.1f}',
             limit=f'>= {siting["min_radar_separation_m"]:.0f}', unit='m'),
        dict(code='EMC_RADAR_RF_ISOLATION', stage='siting', title='有源雷达—无源RF接收阻塞隔离距',
             passed=min_rrf >= siting['min_radar_rf_isolation_m'] - 1e-6,
             actual='无组合' if math.isinf(min_rrf) else f'{min_rrf:.1f}',
             limit=f'>= {siting["min_radar_rf_isolation_m"]:.0f}', unit='m'),
        dict(code='EMC_BAND_OVERLAP', stage='siting', title='同频段/重叠频段最小空间隔离距',
             passed=min_band >= siting['min_band_overlap_separation_m'] - 1e-6,
             actual='无重叠' if math.isinf(min_band) else f'{min_band:.1f}',
             limit=f'>= {siting["min_band_overlap_separation_m"]:.0f}', unit='m'),
        dict(code='SITING_BUDGET_SITES', stage='siting', title='新增工程预算与新建站址上限',
             passed=added_cost <= siting['budget'] + 1e-9 and len(new_sites) <= siting['max_new_sites'],
             actual=f'{added_cost:.1f}万 / {len(new_sites)}站',
             limit=f'<= {siting["budget"]:.1f}万 / {siting["max_new_sites"]}站', unit='万元/站'),
    ]
    return checks, violations, added_cost


def _build_scene_and_volume(scene_id: SceneId, siting: dict, devices: list[dict]) -> tuple[dict, core.Volume]:
    base = apply_building_source(seed(scene_id), siting['building_source'])
    core_devs = [
        {k: d[k] for k in ('id', 'name', 'type', 'x', 'y', 'z', 'azimuth', 'pitch', 'vertical_fov', 'range', 'fov', 'cost', 'status')}
        for d in devices
    ]
    base['devices'] = core_devs
    # Map noise floor (-95 dBm nominal) + rain/fog specific attenuation into Volume weather/interference budget
    noise_penalty_db = max(0.0, siting['noise_floor_dbm'] - (-98.0)) * 0.22
    weather_db = min(40.0, siting['rain_fog_attenuation_db_km'] * 1.0 + noise_penalty_db)
    cell = float(siting['grid_cell_m'])
    xy_max = float(math.floor(2000.0 / cell) * cell)
    z_min = float(math.ceil(30.0 / cell) * cell)
    z_max = float(math.floor(300.0 / cell) * cell)
    if z_max <= z_min:
        z_min, z_max = 0.0, cell
    vol = core.Volume(
        bounds=[0.0, xy_max, 0.0, xy_max, z_min, z_max],
        cell=cell,
        precision_m=siting['precision_m'],
        pd_threshold=siting['pd_threshold'],
        cochannel_db=siting['cochannel_db'],
        communication_db=siting['communication_db'],
        weather_db=round(weather_db, 3),
    )
    return base, vol


def solve_siting(scene_id: SceneId, siting: dict) -> dict:
    sites = {s['id']: s for s in siting['sites']}
    models = {m['id']: m for m in siting['models']}
    existing = [_expand_device(d, sites, models) for d in siting['existing_devices']]
    base_checks, base_violations, _ = check_siting_set(siting, [])
    if base_violations:
        raise ValueError('既有部署违反工程约束：' + '；'.join(base_violations[:3]))

    scene_before, vol = _build_scene_and_volume(scene_id, siting, existing)
    pts = core.grid(vol)
    field_before = core.fields(scene_before, pts, vol)
    stats_before = core.summarize(field_before, vol, pts, scene_before['zone'])

    online_candidates = [c for c in siting['candidate_devices'] if c['status'] == 'online']
    # Precompute single-device incremental fields for candidates that pass individual feasibility
    cand_cache = []
    for cand in online_candidates:
        _, violations, _ = check_siting_set(siting, [cand])
        if violations and any('超预算' not in v for v in violations):
            # Still keep if only budget failed? Budget check is included in violations; check non-budget too
            pass
        if violations:
            continue
        exp = _expand_device(cand, sites, models)
        single_scene, _ = _build_scene_and_volume(scene_id, siting, [exp])
        f_single = core.fields(single_scene, pts, vol)
        cand_cache.append((cand, exp, f_single['pd'], f_single['fim']))

    valid = field_before['valid']
    z = scene_before['zone']
    focus = valid & (pts[:, 0] >= z['x0']) & (pts[:, 0] <= z['x1']) & (pts[:, 1] >= z['y0']) & (pts[:, 1] <= z['y1'])
    targets = np.array([siting['coverage_target'], siting['focus_target'], siting['precision_rate_target']])

    def eval_rates(pd_arr, fim_arr):
        eig = np.linalg.eigvalsh(fim_arr)
        obs = eig[:, 0] > 1e-8
        err = np.full(len(pd_arr), np.inf)
        err[obs] = np.sqrt((1.0 / eig[obs]).sum(axis=1))
        cov = pd_arr >= vol.pd_threshold
        prec = cov & (err <= vol.precision_m)
        rates = np.array([
            float(cov[valid].mean()) if valid.any() else 0.0,
            float(cov[focus].mean()) if focus.any() else 0.0,
            float(prec[valid].mean()) if valid.any() else 0.0,
        ])
        deficit = float(np.dot([3.0, 2.0, 2.5], np.maximum(0.0, targets - rates)))
        smooth = float(pd_arr[valid].mean()) if valid.any() else 0.0
        return deficit, smooth, rates

    selected: list[dict] = []
    pd_cur = field_before['pd'].copy()
    fim_cur = field_before['fim'].copy()
    cur_def, cur_smooth, cur_rates = eval_rates(pd_cur, fim_cur)
    history = []
    evaluated_combinations = 1

    while True:
        best = None
        _, _, cur_cost = check_siting_set(siting, selected)
        chosen_ids = {d['id'] for d in selected}
        for cand, exp, c_pd, c_fim in cand_cache:
            if cand['id'] in chosen_ids:
                continue
            trial = selected + [cand]
            _, violations, trial_cost = check_siting_set(siting, trial)
            if violations:
                continue
            evaluated_combinations += 1
            new_pd = 1.0 - (1.0 - pd_cur) * (1.0 - c_pd)
            new_fim = fim_cur + c_fim
            n_def, n_smooth, n_rates = eval_rates(new_pd, new_fim)
            gain = (cur_def - n_def) + 0.04 * (n_smooth - cur_smooth)
            if gain <= 1e-9:
                continue
            marginal = max(0.25, trial_cost - cur_cost)
            rank = (gain / marginal, -trial_cost)
            if best is None or rank > best[0]:
                best = (rank, cand, new_pd, new_fim, n_def, n_smooth, n_rates, trial_cost)
        if best is None:
            break
        _, cand, pd_cur, fim_cur, cur_def, cur_smooth, cur_rates, trial_cost = best
        selected.append(cand)
        history.append(dict(
            step=len(history) + 1, action='add', device_id=cand['id'], site_id=cand['site_id'],
            model_id=cand['model_id'], added_cost=trial_cost,
            coverage=round(float(cur_rates[0]), 4),
            focus_coverage=round(float(cur_rates[1]), 4),
            precision_pass_rate=round(float(cur_rates[2]), 4),
        ))
        if cur_def <= 1e-9:
            break

    # Exact verification over full network (captures co-channel interference between newly added sensors)
    final_expanded = existing + [_expand_device(d, sites, models) for d in selected]
    scene_after, _ = _build_scene_and_volume(scene_id, siting, final_expanded)
    field_after = core.fields(scene_after, pts, vol)
    stats_after = core.summarize(field_after, vol, pts, scene_after['zone'])
    checks, violations, added_cost = check_siting_set(siting, selected)

    kpi_checks = [
        dict(code='SITING_KPI_COVERAGE', stage='siting', title='全域三维覆盖率目标',
             passed=(stats_after['coverage'] or 0) >= siting['coverage_target'] - 1e-6,
             actual=f"{(stats_after['coverage'] or 0) * 100:.1f}%",
             limit=f">= {siting['coverage_target'] * 100:.1f}%", unit='%'),
        dict(code='SITING_KPI_FOCUS', stage='siting', title='重点监管区覆盖率目标',
             passed=(stats_after['focus_coverage'] or 0) >= siting['focus_target'] - 1e-6,
             actual=f"{(stats_after['focus_coverage'] or 0) * 100:.1f}%",
             limit=f">= {siting['focus_target'] * 100:.1f}%", unit='%'),
        dict(code='SITING_KPI_PRECISION', stage='siting', title='CRLB 定位精度合格率目标',
             passed=(stats_after['precision_pass_rate'] or 0) >= siting['precision_rate_target'] - 1e-6,
             actual=f"{(stats_after['precision_pass_rate'] or 0) * 100:.1f}%",
             limit=f">= {siting['precision_rate_target'] * 100:.1f}%", unit='%'),
    ]
    all_checks = checks + kpi_checks
    rejected_candidates = []
    for cand in online_candidates:
        if any(s['id'] == cand['id'] for s in selected):
            continue
        _, v_list, _ = check_siting_set(siting, selected + [cand])
        if v_list:
            rejected_candidates.append(dict(id=cand['id'], site_id=cand['site_id'], reason=v_list[0]))

    # Evaluate non-RF multimodal resilience when statutory countermeasure suppression is active
    cm_sites = siting.get('countermeasure_sites', [])
    jammer_iso = float(siting.get('min_jammer_rf_isolation_m', 180.0))
    min_rf_jammer = math.inf
    for d in final_expanded:
        if d['status'] == 'online' and d['type'] == 'RF':
            for cm in cm_sites:
                min_rf_jammer = min(min_rf_jammer, math.dist((d['x'], d['y'], d['z']), (cm['x'], cm['y'], cm['z'])))
    jam_vol = vol.model_copy(update={
        'target_emitting': False,
        'communication_db': min(40.0, vol.communication_db + float(siting.get('jammer_suppression_db', 6.0))),
    })
    field_jam = core.fields(scene_after, pts, jam_vol)
    stats_jam = core.summarize(field_jam, jam_vol, pts, scene_after['zone'])
    countermeasure_assessment = dict(
        legal_boundary='授权反制点位遵循《条例》第43条法定权限，本模型仅求解感知选址防阻塞隔离与压制态雷达/光电保底覆盖，不输出反制控制指令。',
        sites=cm_sites,
        min_jammer_rf_isolation_m=jammer_iso,
        actual_min_rf_jammer_m=None if math.isinf(min_rf_jammer) else round(min_rf_jammer, 1),
        emc_rf_isolated=math.isinf(min_rf_jammer) or min_rf_jammer >= jammer_iso - 1e-6,
        jamming_active_focus_coverage=stats_jam['focus_coverage'],
        jamming_active_coverage=stats_jam['coverage'],
        jamming_active_precision_rate=stats_jam['precision_pass_rate'],
    )

    return dict(
        method='工程与感反电磁兼容强约束下的边际收益贪心 + 全网络共信道干扰精确复核（无需训练数据）',
        evaluated_combinations=evaluated_combinations,
        selected_candidates=selected,
        deployed_network=final_expanded,
        rejected_candidates=rejected_candidates,
        added_cost=added_cost,
        before=stats_before,
        after=stats_after,
        countermeasure_assessment=countermeasure_assessment,
        history=history,
        checks=all_checks,
        feasible=all(c['passed'] for c in all_checks) and not violations,
        buildings=scene_after['buildings'],
        zone=scene_after['zone'],
    )


# ---------------------------------------------------------------------------
# Stage 2 Engine: Sensing-Aware & Wind-Aware Kinodynamic Route Planner
# ---------------------------------------------------------------------------

def _ground_speed(heading_ux: float, heading_uy: float, airspeed: float, wind_xy: tuple[float, float]) -> float | None:
    wx, wy = wind_xy
    # Track unit vector u = (ux, uy); solve || g*u - w || = airspeed => g^2 - 2(u.w)g + (||w||^2 - airspeed^2) = 0
    proj = heading_ux * wx + heading_uy * wy
    cross_sq = (wx * wx + wy * wy) - proj * proj
    if cross_sq >= airspeed * airspeed * 0.96:
        return None
    val = proj + math.sqrt(max(0.0, airspeed * airspeed - cross_sq))
    return val if val > 0.8 else None


def _box_blocked(x: float, y: float, z: float, buildings: list[dict], nfzs: list[dict], margin: float) -> bool:
    for b in buildings:
        if z <= b['h'] + 6.0:
            if b['x'] - margin <= x <= b['x'] + b['w'] + margin and b['y'] - margin <= y <= b['y'] + b['d'] + margin:
                return True
    for nfz in nfzs:
        if nfz['z0'] - 5.0 <= z <= nfz['z1'] + 5.0:
            if nfz['x0'] - margin <= x <= nfz['x1'] + margin and nfz['y0'] - margin <= y <= nfz['y1'] + margin:
                return True
    return False


def _segment_free(a: tuple[float, float, float], b: tuple[float, float, float],
                  buildings: list[dict], nfzs: list[dict], margin: float) -> bool:
    dist = math.dist(a, b)
    horiz = math.hypot(b[0] - a[0], b[1] - a[1])
    use_margin = 0.0 if horiz < 1.0 else margin
    steps = max(2, int(math.ceil(dist / 8.0)))
    for i in range(steps + 1):
        t = i / steps
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        z = a[2] + (b[2] - a[2]) * t
        if _box_blocked(x, y, z, buildings, nfzs, use_margin):
            return False
    return True


def _peer_position_at(peer: dict, t_s: float) -> tuple[float, float, float] | None:
    if t_s < peer['depart_s']:
        return None
    elapsed = t_s - peer['depart_s']
    pts = peer['waypoints']
    rem = elapsed * peer['speed_mps']
    for p0, p1 in zip(pts, pts[1:]):
        seg = math.dist(p0, p1)
        if seg < 1e-6:
            continue
        if rem <= seg:
            f = rem / seg
            return (p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f, p0[2] + (p1[2] - p0[2]) * f)
        rem -= seg
    return None


def solve_routing(scene_id: SceneId, siting: dict, routing: dict, deployed_devices: list[dict]) -> dict:
    scene_net, vol = _build_scene_and_volume(scene_id, siting, deployed_devices)
    buildings = scene_net['buildings']
    active_nfzs = [
        z for z in routing['no_fly_zones']
        if not (z['active_end_s'] < routing['depart_s'] or z['active_start_s'] > routing['deadline_s'])
    ]
    cell = 50.0
    nx = int(2000 // cell)
    alt = routing['cruise_altitude_m']

    # Compute sensing Pd map on the 40x40 cruise slice so A* can steer along covered corridors
    centers = np.array([[(i + 0.5) * cell, (j + 0.5) * cell, alt] for i in range(nx) for j in range(nx)], dtype=float)
    slice_field = core.fields(scene_net, centers, vol)
    pd_grid = slice_field['pd'].reshape(nx, nx)
    prec_grid = np.where(slice_field['observable'], slice_field['precision_m'], 999.0).reshape(nx, nx)

    free = np.ones((nx, nx), dtype=bool)
    for i in range(nx):
        for j in range(nx):
            cx, cy = (i + 0.5) * cell, (j + 0.5) * cell
            if _box_blocked(cx, cy, alt, buildings, active_nfzs, routing['obstacle_clearance_m']):
                free[i, j] = False

    def nearest_free(xy: tuple[float, float]) -> tuple[int, int] | None:
        ci = min(nx - 1, max(0, int(xy[0] // cell)))
        cj = min(nx - 1, max(0, int(xy[1] // cell)))
        best = None
        for r in range(8):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    ni, nj = ci + di, cj + dj
                    if 0 <= ni < nx and 0 <= nj < nx and free[ni, nj]:
                        cx, cy = (ni + 0.5) * cell, (nj + 0.5) * cell
                        if _segment_free((xy[0], xy[1], alt), (cx, cy, alt), buildings, active_nfzs, routing['obstacle_clearance_m']):
                            d = math.hypot(cx - xy[0], cy - xy[1])
                            if best is None or d < best[0]:
                                best = (d, (ni, nj))
            if best is not None:
                return best[1]
        return None

    s_cell = nearest_free(routing['start_xyz'][:2])
    g_cell = nearest_free(routing['goal_xyz'][:2])
    if s_cell is None or g_cell is None:
        raise ValueError('起终点被高层建筑或禁飞区阻隔，请调整起终点或提高巡航高度')

    def run_astar(sense_weight: float) -> list[tuple[float, float]] | None:
        pq = [(0.0, 0.0, s_cell)]
        cost_so_far = {s_cell: 0.0}
        parent = {}
        neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
        while pq:
            _, g_val, cur = heapq.heappop(pq)
            if g_val > cost_so_far.get(cur, 1e18) + 1e-9:
                continue
            if cur == g_cell:
                seq = [cur]
                while cur in parent:
                    cur = parent[cur]
                    seq.append(cur)
                seq.reverse()
                pts_2d = [(routing['start_xyz'][0], routing['start_xyz'][1])]
                for ci, cj in seq:
                    pt = ((ci + 0.5) * cell, (cj + 0.5) * cell)
                    if math.hypot(pt[0] - pts_2d[-1][0], pt[1] - pts_2d[-1][1]) > 1.0:
                        pts_2d.append(pt)
                if math.hypot(routing['goal_xyz'][0] - pts_2d[-1][0], routing['goal_xyz'][1] - pts_2d[-1][1]) > 1.0:
                    pts_2d.append((routing['goal_xyz'][0], routing['goal_xyz'][1]))
                return pts_2d
            ci, cj = cur
            for di, dj in neighbors:
                ni, nj = ci + di, cj + dj
                if not (0 <= ni < nx and 0 <= nj < nx and free[ni, nj]):
                    continue
                if di and dj and (not free[ci + di, cj] or not free[ci, cj + dj]):
                    continue
                step_dist = math.hypot(di * cell, dj * cell)
                ux, uy = (di * cell) / step_dist, (dj * cell) / step_dist
                gs = _ground_speed(ux, uy, routing['cruise_speed_mps'], routing['wind_xy_mps'])
                if gs is None:
                    continue
                blind_pen = max(0.0, vol.pd_threshold - float(pd_grid[ni, nj])) / max(vol.pd_threshold, 0.1)
                prec_pen = 0.35 if prec_grid[ni, nj] > vol.precision_m else 0.0
                edge_cost = (step_dist / gs) * (1.0 + sense_weight * (blind_pen + prec_pen))
                nxt = (ni, nj)
                ng = g_val + edge_cost
                if ng < cost_so_far.get(nxt, 1e18) - 1e-9:
                    cost_so_far[nxt] = ng
                    parent[nxt] = cur
                    heur = math.hypot((g_cell[0] - ni) * cell, (g_cell[1] - nj) * cell) / max(routing['cruise_speed_mps'] + 5.0, 1.0)
                    heapq.heappush(pq, (ng + heur, ng, nxt))
        return None

    raw_2d = run_astar(routing['sensing_weight'])
    shortest_2d = run_astar(0.0)
    if raw_2d is None:
        raise ValueError('在当前风速、建筑安全间隔与禁飞区约束下不存在可行航路')

    # String-pulling shortcutting while preserving sensing quality relative to the grid sub-path
    def shortcut(pts_2d: list[tuple[float, float]], check_sensing: bool) -> list[tuple[float, float]]:
        out = [pts_2d[0]]
        idx = 0
        while idx < len(pts_2d) - 1:
            far = len(pts_2d) - 1
            while far > idx + 1:
                a3 = (pts_2d[idx][0], pts_2d[idx][1], alt)
                b3 = (pts_2d[far][0], pts_2d[far][1], alt)
                ok = _segment_free(a3, b3, buildings, active_nfzs, routing['obstacle_clearance_m'])
                if ok and check_sensing and routing['sensing_weight'] > 0:
                    seg_len = math.hypot(b3[0] - a3[0], b3[1] - a3[1])
                    if seg_len > 90.0:
                        ns = max(2, int(math.ceil(seg_len / 35.0)))
                        sub = np.array([[a3[0] + (b3[0] - a3[0]) * k / ns, a3[1] + (b3[1] - a3[1]) * k / ns, alt] for k in range(ns + 1)])
                        orig = np.array([[p[0], p[1], alt] for p in pts_2d[idx:far + 1]])
                        sub_cov = float((core.fields(scene_net, sub, vol)['pd'] >= vol.pd_threshold).mean())
                        orig_cov = float((core.fields(scene_net, orig, vol)['pd'] >= vol.pd_threshold).mean())
                        if sub_cov + 0.02 < orig_cov:
                            ok = False
                if ok:
                    break
                far -= 1
            out.append(pts_2d[far])
            idx = far
        return out

    cruise_2d = shortcut(raw_2d, True)
    waypoints = [
        [round(routing['start_xyz'][0], 1), round(routing['start_xyz'][1], 1), round(routing['start_xyz'][2], 1)],
        [round(routing['start_xyz'][0], 1), round(routing['start_xyz'][1], 1), round(alt, 1)],
    ]
    for x, y in cruise_2d[1:]:
        waypoints.append([round(x, 1), round(y, 1), round(alt, 1)])
    waypoints.append([round(routing['goal_xyz'][0], 1), round(routing['goal_xyz'][1], 1), round(routing['goal_xyz'][2], 1)])

    # Evaluate full kinematics, wind groundspeed, energy Wh, turn geometry, 4D peer separation & sensing coverage
    t_cur = routing['depart_s']
    energy_wh = 0.0
    total_dist_m = 0.0
    payload_factor = 1.0 + 0.12 * routing['payload_kg']
    leg_rows = []
    wind_feasible = True
    climb_feasible = True

    samples_3d = []
    sample_times = []
    arc_vals = []
    running_arc = 0.0

    for li, (p0, p1) in enumerate(zip(waypoints, waypoints[1:])):
        dx, dy, dz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        horiz = math.hypot(dx, dy)
        seg_dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if horiz < 1.0:
            rate = routing['max_climb_rate_mps'] if dz >= 0 else routing['max_descent_rate_mps']
            dt = abs(dz) / max(rate, 0.1)
            pwr = (routing['cruise_power_w'] + (routing['climb_extra_power_w'] if dz > 0 else -40.0)) * payload_factor
            gs = rate
        else:
            ux, uy = dx / horiz, dy / horiz
            gs = _ground_speed(ux, uy, routing['cruise_speed_mps'], routing['wind_xy_mps'])
            if gs is None:
                wind_feasible = False
                gs = 1.0
            dt = horiz / gs
            if abs(dz) / max(dt, 1e-6) > (routing['max_climb_rate_mps'] if dz >= 0 else routing['max_descent_rate_mps']) + 1e-6:
                climb_feasible = False
            pwr = routing['cruise_power_w'] * payload_factor
        n_sub = max(1, int(math.ceil(seg_dist / 25.0)))
        for k in range(n_sub if li < len(waypoints) - 2 else n_sub + 1):
            f = k / n_sub
            samples_3d.append([p0[0] + dx * f, p0[1] + dy * f, p0[2] + dz * f])
            sample_times.append(t_cur + dt * f)
            arc_vals.append(running_arc + seg_dist * f)
        t_cur += dt
        running_arc += seg_dist
        total_dist_m += seg_dist
        e_leg = pwr * (dt / 3600.0)
        energy_wh += e_leg
        leg_rows.append(dict(leg=li + 1, from_xyz=p0, to_xyz=p1, distance_m=round(seg_dist, 1),
                             ground_speed_mps=round(gs, 2), duration_s=round(dt, 1), energy_wh=round(e_leg, 2)))

    # Turn angle vs minimum turn radius check on horizontal cruise corners
    max_turn_deg = 0.0
    turn_feasible = True
    cruise_pts = waypoints[1:-1]
    for a, b, c in zip(cruise_pts, cruise_pts[1:], cruise_pts[2:]):
        v1 = (b[0] - a[0], b[1] - a[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        if l1 > 35.0 and l2 > 35.0:
            cos_ang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
            turn_deg = math.degrees(math.acos(cos_ang))
            max_turn_deg = max(max_turn_deg, turn_deg)
            req_tangent = routing['min_turn_radius_m'] * math.tan(math.radians(min(135.0, turn_deg)) / 2.0)
            if min(l1, l2) < req_tangent:
                turn_feasible = False

    # 4D multi-UAV separation check
    min_peer_dist = math.inf
    for pt, ts in zip(samples_3d, sample_times):
        for peer in routing['peer_uavs']:
            p_pos = _peer_position_at(peer, ts)
            if p_pos is not None:
                min_peer_dist = min(min_peer_dist, math.dist(pt, p_pos))

    # Sensing coverage & blind gap along cruise samples (z >= 30 m)
    pts_arr = np.asarray(samples_3d, dtype=float)
    air_mask = pts_arr[:, 2] >= 30.0
    route_field = core.fields(scene_net, pts_arr, vol)
    covered_mask = route_field['pd'] >= vol.pd_threshold
    route_cov = float(covered_mask[air_mask].mean()) if air_mask.any() else 0.0
    precise_mask = covered_mask & route_field['observable'] & (route_field['precision_m'] <= vol.precision_m)
    route_prec = float(precise_mask[air_mask].mean()) if air_mask.any() else 0.0

    max_blind_m = 0.0
    cur_blind = 0.0
    for i in range(1, len(pts_arr)):
        if not air_mask[i]:
            cur_blind = 0.0
            continue
        seg = arc_vals[i] - arc_vals[i - 1]
        if not covered_mask[i]:
            cur_blind += seg
            max_blind_m = max(max_blind_m, cur_blind)
        else:
            cur_blind = 0.0

    # Baseline shortest route coverage comparison (without sensing_weight)
    shortest_cov = None
    if shortest_2d is not None:
        sh_pts = np.array([[x, y, alt] for x, y in shortest_2d], dtype=float)
        shortest_cov = round(float((core.fields(scene_net, sh_pts, vol)['pd'] >= vol.pd_threshold).mean()), 4)

    remaining_soc = 1.0 - (energy_wh / max(routing['battery_wh'], 1e-6))
    collision_free = all(
        _segment_free(tuple(a), tuple(b), buildings, active_nfzs, routing['obstacle_clearance_m'])
        for a, b in zip(waypoints[1:-1], waypoints[2:-1])
    )

    checks = [
        dict(code='ROUTE_CLEARANCE_NFZ', stage='routing', title='三维建筑安全间隔与时效禁飞区避让',
             passed=collision_free, actual='全部净空' if collision_free else '碰撞违约',
             limit=f'>= {routing["obstacle_clearance_m"]:.0f} m', unit='m'),
        dict(code='ROUTE_PAYLOAD_WIND_TURN', stage='routing', title='最大载重 / 抗风真空速 / 转弯与爬升包线',
             passed=(routing['payload_kg'] <= routing['max_payload_kg'] + 1e-9) and wind_feasible and climb_feasible and turn_feasible,
             actual=f'载重{routing["payload_kg"]:.1f}kg · 最大折角{max_turn_deg:.0f}°',
             limit=f'<= {routing["max_payload_kg"]:.1f}kg · Rmin {routing["min_turn_radius_m"]:.0f}m', unit='包线'),
        dict(code='ROUTE_BATTERY_SOC', stage='routing', title='风阻与载荷修正后返航剩余电量 SoC',
             passed=remaining_soc >= routing['reserve_soc'] - 1e-6,
             actual=f'{remaining_soc * 100:.1f}%（耗电 {energy_wh:.1f} Wh）',
             limit=f'>= {routing["reserve_soc"] * 100:.0f}%', unit='%'),
        dict(code='ROUTE_TIME_WINDOW', stage='routing', title='任务到达时间窗约束',
             passed=t_cur <= routing['deadline_s'] + 1e-6,
             actual=f'{t_cur:.1f}', limit=f'<= {routing["deadline_s"]:.1f}', unit='s'),
        dict(code='ROUTE_4D_SEPARATION', stage='routing', title='多机四维时空最小安全间隔',
             passed=min_peer_dist >= routing['min_uav_separation_m'] - 1e-6,
             actual='无冲突航迹' if math.isinf(min_peer_dist) else f'{min_peer_dist:.1f}',
             limit=f'>= {routing["min_uav_separation_m"]:.0f}', unit='m'),
        dict(code='ROUTE_SENSING_CORRIDOR', stage='routing', title='感知网络沿程覆盖率与最大盲区段约束',
             passed=(route_cov >= routing['min_route_coverage'] - 1e-6) and (max_blind_m <= routing['max_blind_gap_m'] + 1e-6),
             actual=f'覆盖 {route_cov * 100:.1f}% · 盲段 {max_blind_m:.0f}m',
             limit=f'>= {routing["min_route_coverage"] * 100:.0f}% · <= {routing["max_blind_gap_m"]:.0f}m', unit='%/m'),
    ]

    sample_rows = [
        dict(x=round(float(p[0]), 1), y=round(float(p[1]), 1), z=round(float(p[2]), 1),
             t_s=round(float(ts), 1), pd=round(float(route_field['pd'][i]), 3),
             covered=bool(covered_mask[i]),
             precision_m=round(float(route_field['precision_m'][i]), 2) if route_field['observable'][i] else None)
        for i, (p, ts) in enumerate(zip(pts_arr, sample_times))
    ]

    return dict(
        method='风场地面速修正 + 感知盲区惩罚加权三维 A* 与连续折线净空剪枝（无需训练数据）',
        waypoints=waypoints,
        legs=leg_rows,
        samples=sample_rows,
        summary=dict(
            distance_m=round(total_dist_m, 1),
            depart_s=routing['depart_s'],
            arrival_s=round(t_cur, 1),
            flight_duration_s=round(t_cur - routing['depart_s'], 1),
            energy_wh=round(energy_wh, 2),
            remaining_soc=round(remaining_soc, 4),
            route_coverage=round(route_cov, 4),
            shortest_baseline_coverage=shortest_cov,
            route_precision_rate=round(route_prec, 4),
            max_blind_gap_m=round(max_blind_m, 1),
            min_peer_separation_m=None if math.isinf(min_peer_dist) else round(min_peer_dist, 1),
        ),
        checks=checks,
        feasible=all(c['passed'] for c in checks),
    )


# ---------------------------------------------------------------------------
# Stage 3 Engine: Three-Layer Constrained Task Scheduler
# ---------------------------------------------------------------------------

def _schedule_fleet(sched: dict) -> tuple[dict, list[dict]]:
    uavs = {u['id']: dict(u, next_free_s=u['available_from_s'], used_wh=0.0) for u in sched['uavs']}
    pad_intervals: list[tuple[float, float]] = []
    flight_intervals: list[tuple[float, float]] = []

    def count_overlap(intervals: list[tuple[float, float]], a: float, b: float) -> int:
        pts = sorted({a, b} | {x for s, e in intervals for x in (s, e) if a <= x <= b})
        peak = 0
        for x, y in zip(pts, pts[1:]):
            mid = 0.5 * (x + y)
            peak = max(peak, sum(1 for s, e in intervals if s <= mid <= e))
        return peak

    ordered = sorted(sched['missions'], key=lambda m: (-m['priority'], m['deadline_s'], m['earliest_start_s']))
    assignments = []
    unassigned = []
    for m in ordered:
        best_choice = None
        for uid, u in uavs.items():
            max_usable_wh = u['battery_wh'] * (1.0 - u['reserve_soc'])
            if m['required_payload_kg'] > u['max_payload_kg'] + 1e-9 or m['energy_wh'] > max_usable_wh + 1e-9:
                continue
            t_start = max(m['earliest_start_s'], u['next_free_s'])
            while t_start + m['duration_s'] <= m['deadline_s'] + 1e-9:
                t_end = t_start + m['duration_s']
                pad_ok = (count_overlap(pad_intervals, t_start, t_start + sched['pad_lock_s']) < sched['pad_capacity']
                          and count_overlap(pad_intervals, t_end, t_end + sched['pad_lock_s']) < sched['pad_capacity'])
                pilot_ok = count_overlap(flight_intervals, t_start, t_end) < sched['pilot_concurrency']
                if pad_ok and pilot_ok:
                    rank = (t_end, u['max_payload_kg'] - m['required_payload_kg'])
                    if best_choice is None or rank < best_choice[0]:
                        best_choice = (rank, uid, t_start, t_end)
                    break
                t_start += 10.0
        if best_choice is None:
            unassigned.append(dict(mission_id=m['id'], name=m['name'], reason='无满足载重/电量/机位容量/飞手并发与截止期的可用飞机'))
            continue
        _, uid, t_start, t_end = best_choice
        u = uavs[uid]
        turnaround_end = t_end + u['turnaround_charge_s']
        u['next_free_s'] = turnaround_end
        pad_intervals.append((t_start, t_start + sched['pad_lock_s']))
        pad_intervals.append((t_end, t_end + sched['pad_lock_s']))
        flight_intervals.append((t_start, t_end))
        assignments.append(dict(
            mission_id=m['id'], mission_name=m['name'], priority=m['priority'], uav_id=uid, uav_name=u['name'],
            start_s=round(t_start, 1), end_s=round(t_end, 1), turnaround_ready_s=round(turnaround_end, 1),
            deadline_s=m['deadline_s'], slack_s=round(m['deadline_s'] - t_end, 1), energy_wh=m['energy_wh'],
        ))

    checks = [
        dict(code='SCHED_FLEET_ASSIGNMENT', stage='scheduling', title='多机多任务载重 / 充电周转 / 起降机位与飞手并发排班',
             passed=len(unassigned) == 0,
             actual=f'已排班 {len(assignments)}/{len(sched["missions"])} 项',
             limit=f'全部 {len(sched["missions"])} 项按期排班', unit='任务'),
    ]
    return dict(assignments=assignments, unassigned=unassigned), checks


def _schedule_turrets(scene_id: SceneId, siting: dict, sched: dict, deployed_devices: list[dict]) -> tuple[dict, list[dict]]:
    scene_net, vol = _build_scene_and_volume(scene_id, siting, deployed_devices)
    online_sensors = [d for d in deployed_devices if d['status'] == 'online' and d['type'] in ('RADAR', 'EO', 'IR', 'RF')]
    tgt_pts = np.array([t['position_xyz'] for t in sched['targets']], dtype=float)

    # Check sensor visibility & pointing angles to each target
    vis_matrix: dict[str, list[dict]] = {t['id']: [] for t in sched['targets']}
    for s in online_sensors:
        # Omnidirectional RF needs no mechanical slew; directional sensors point & slew
        s_copy = dict(s, fov=360, vertical_fov=180, pitch=0)
        pd_vec = core.device_field(scene_net, tgt_pts, s_copy, vol)[0]
        for ti, tgt in enumerate(sched['targets']):
            if pd_vec[ti] >= vol.pd_threshold * 0.7:
                dx = tgt['position_xyz'][0] - s['x']
                dy = tgt['position_xyz'][1] - s['y']
                dz = tgt['position_xyz'][2] - s['z']
                az = math.degrees(math.atan2(dx, dy)) % 360.0
                el = math.degrees(math.atan2(dz, max(1.0, math.hypot(dx, dy))))
                vis_matrix[tgt['id']].append(dict(sensor_id=s['id'], type=s['type'], azimuth=round(az, 1),
                                                   elevation=round(el, 1), pd=round(float(pd_vec[ti]), 3),
                                                   steerable=s['fov'] < 360))

    # Build cyclic turret timeline over horizon_s respecting slew_deg_s and dwell_s
    sensor_state = {s['id']: dict(time_s=0.0, az=s['azimuth']) for s in online_sensors}
    beam_events = []
    target_visits: dict[str, list[float]] = {t['id']: [] for t in sched['targets']}
    target_modalities: dict[str, set[str]] = {t['id']: set() for t in sched['targets']}

    slot_cursor = 0.0
    ordered_tgts = sorted(sched['targets'], key=lambda t: (-t['priority'], t['max_revisit_s']))
    while slot_cursor < sched['turret_horizon_s'] - 1e-6:
        progressed = False
        for tgt in ordered_tgts:
            candidates = sorted(vis_matrix[tgt['id']], key=lambda c: (-c['pd'], sensor_state[c['sensor_id']]['time_s']))
            picked = []
            seen_types = set()
            for cand in candidates:
                sid = cand['sensor_id']
                st = sensor_state[sid]
                if st['time_s'] >= sched['turret_horizon_s'] - 1e-6:
                    continue
                delta_az = abs((cand['azimuth'] - st['az'] + 180.0) % 360.0 - 180.0)
                slew_s = (delta_az / sched['turret_slew_deg_s']) if cand['steerable'] else 0.0
                start_s = max(st['time_s'], slot_cursor) + slew_s
                end_s = start_s + sched['turret_dwell_s']
                if end_s > sched['turret_horizon_s'] + 1e-6:
                    continue
                if cand['type'] not in seen_types or len(picked) < tgt['required_modalities']:
                    picked.append((cand, slew_s, start_s, end_s))
                    seen_types.add(cand['type'])
                if len(seen_types) >= tgt['required_modalities']:
                    break
            for cand, slew_s, start_s, end_s in picked:
                sid = cand['sensor_id']
                sensor_state[sid] = dict(time_s=end_s, az=cand['azimuth'])
                beam_events.append(dict(
                    sensor_id=sid, modality=cand['type'], target_id=tgt['id'], target_name=tgt['name'],
                    slew_s=round(slew_s, 2), start_s=round(start_s, 2), end_s=round(end_s, 2),
                    azimuth_deg=cand['azimuth'], elevation_deg=cand['elevation'],
                ))
                target_visits[tgt['id']].append(start_s)
                target_modalities[tgt['id']].add(cand['type'])
                progressed = True
        if not progressed:
            break
        slot_cursor += max(1.0, sched['turret_dwell_s'])

    revisit_ok = True
    modality_ok = True
    target_summaries = []
    for tgt in sched['targets']:
        visits = sorted(set(round(v, 2) for v in target_visits[tgt['id']]))
        if not visits:
            max_gap = sched['turret_horizon_s']
            revisit_ok = False
        else:
            gaps = [visits[0]] + [b - a for a, b in zip(visits, visits[1:])] + [max(0.0, sched['turret_horizon_s'] - visits[-1])]
            max_gap = max(gaps)
            if max_gap > tgt['max_revisit_s'] + 1e-6:
                revisit_ok = False
        mods = sorted(target_modalities[tgt['id']])
        if len(mods) < tgt['required_modalities']:
            modality_ok = False
        target_summaries.append(dict(
            target_id=tgt['id'], name=tgt['name'], visits=len(visits), modalities=mods,
            required_modalities=tgt['required_modalities'], max_gap_s=round(max_gap, 2),
            limit_s=tgt['max_revisit_s'], passed=(max_gap <= tgt['max_revisit_s'] + 1e-6 and len(mods) >= tgt['required_modalities']),
        ))

    checks = [
        dict(code='SCHED_TURRET_REVISIT', stage='scheduling', title='地面定向传感器云台转速 / 驻留与重访周期协同约束',
             passed=revisit_ok and modality_ok,
             actual=f'{sum(t["passed"] for t in target_summaries)}/{len(target_summaries)} 目标满足多模态重访',
             limit=f'转速 <= {sched["turret_slew_deg_s"]:.0f}°/s 且满足重访上限', unit='目标'),
    ]
    return dict(events=sorted(beam_events, key=lambda e: e['start_s']), targets=target_summaries), checks


def _schedule_compute_dag(sched: dict, uav_pos: tuple[float, float, float]) -> tuple[dict, list[dict]]:
    nodes = {n['id']: dict(n) for n in sched['nodes']}
    nodes['device']['position'] = uav_pos
    contention = max(1, sched['shared_uav_count'])
    links = sched['links']
    tasks = {t['id']: t for t in sched['dag_tasks']}

    def transfer_delay(src: str, dst: str, mbit: float) -> float | None:
        if src == dst:
            return 0.0
        pq = [(0.0, src)]
        seen = set()
        while pq:
            cost_t, cur = heapq.heappop(pq)
            if cur == dst:
                return cost_t
            if cur in seen:
                continue
            seen.add(cur)
            for lk in links:
                if lk['source'] != cur:
                    continue
                nxt = lk['target']
                if lk['max_range_m'] > 0 and math.dist(nodes[cur]['position'], nodes[nxt]['position']) > lk['max_range_m']:
                    continue
                eff_mbps = lk['mbps'] / contention if 'device' in (cur, nxt) else lk['mbps']
                heapq.heappush(pq, (cost_t + lk['latency_s'] + mbit / max(eff_mbps, 0.1), nxt))
        return None

    # Upward rank with memory/VRAM eligibility filtering
    succ = {tid: [c['id'] for c in sched['dag_tasks'] if tid in c['parents']] for tid in tasks}

    def eligible(tid: str, nid: str) -> bool:
        t, n = tasks[tid], nodes[nid]
        return n['memory_gb'] >= t['memory_gb'] - 1e-9 and n['vram_gb'] >= t['requires_vram_gb'] - 1e-9

    rank_memo: dict[str, float] = {}

    def upward_rank(tid: str) -> float:
        if tid in rank_memo:
            return rank_memo[tid]
        t = tasks[tid]
        elig_nodes = [nid for nid in nodes if eligible(tid, nid)]
        avg_comp = float(np.mean([t['gcycles'] / nodes[nid]['gcycles_s'] for nid in elig_nodes])) if elig_nodes else 1e6
        child_best = 0.0
        for cid in succ[tid]:
            child_best = max(child_best, (t['output_mbit'] / 40.0) + upward_rank(cid))
        rank_memo[tid] = avg_comp + child_best
        return rank_memo[tid]

    ordered_ids = sorted(tasks, key=lambda tid: upward_rank(tid), reverse=True)
    node_free = {nid: nodes[nid]['busy_s'] for nid in nodes}
    placed: dict[str, dict] = {}
    infeasible_reason = None

    for tid in ordered_ids:
        t = tasks[tid]
        best = None
        for nid, n in nodes.items():
            if not eligible(tid, nid):
                continue
            if not t['parents']:
                in_delay = transfer_delay('device', nid, t['input_mbit'])
                if in_delay is None:
                    continue
                ready_s = in_delay
            else:
                ready_s = 0.0
                ok = True
                for pid in t['parents']:
                    p_info = placed[pid]
                    tr = transfer_delay(p_info['node'], nid, tasks[pid]['output_mbit'])
                    if tr is None:
                        ok = False
                        break
                    ready_s = max(ready_s, p_info['end_s'] + tr)
                if not ok:
                    continue
            start_s = max(node_free[nid], ready_s)
            comp_s = t['gcycles'] / n['gcycles_s']
            end_s = start_s + comp_s
            ret_s = transfer_delay(nid, 'device', t['output_mbit']) if not succ[tid] else 0.0
            if ret_s is None:
                continue
            finish_s = end_s + ret_s
            if best is None or finish_s < best[0]:
                best = (finish_s, nid, start_s, end_s, comp_s, ready_s)
        if best is None:
            infeasible_reason = f'子任务 {tid} 无同时满足内存/显存容量与无线通信可达性的算力节点'
            break
        finish_s, nid, start_s, end_s, comp_s, ready_s = best
        node_free[nid] = end_s
        placed[tid] = dict(
            id=tid, node=nid, ready_s=round(ready_s, 3), start_s=round(start_s, 3), end_s=round(end_s, 3),
            result_s=round(finish_s, 3), deadline_s=t['deadline_s'],
            deadline_met=finish_s <= t['deadline_s'] + 1e-6,
            memory_gb=t['memory_gb'], requires_vram_gb=t['requires_vram_gb'],
            energy_j=round(comp_s * nodes[nid]['power_w'], 2),
        )

    rows = [placed[tid] for tid in ordered_ids if tid in placed]
    makespan_s = max((r['result_s'] for r in rows), default=math.inf) if not infeasible_reason else None
    passed = (infeasible_reason is None and makespan_s is not None
              and makespan_s <= sched['hard_deadline_s'] + 1e-6
              and all(r['deadline_met'] for r in rows))
    checks = [
        dict(code='SCHED_COMPUTE_DAG', stage='scheduling', title='显存/内存容量与多机带宽争用下的云边端 DAG 硬截止期',
             passed=passed,
             actual=infeasible_reason or f'完工 {makespan_s:.2f} s（{sched["shared_uav_count"]}机共享链路）',
             limit=f'<= {sched["hard_deadline_s"]:.2f} s 且显存合规', unit='s'),
    ]
    return dict(
        status='ok' if not infeasible_reason else 'infeasible',
        error=infeasible_reason,
        makespan_s=None if makespan_s is None else round(makespan_s, 3),
        total_energy_j=round(sum(r['energy_j'] for r in rows), 2),
        shared_uav_count=sched['shared_uav_count'],
        rows=sorted(rows, key=lambda r: r['start_s']),
    ), checks


def solve_scheduling(scene_id: SceneId, siting: dict, routing: dict, sched: dict,
                     deployed_devices: list[dict], uav_sample_pos: tuple[float, float, float]) -> dict:
    fleet_res, fleet_checks = _schedule_fleet(sched)
    turret_res, turret_checks = _schedule_turrets(scene_id, siting, sched, deployed_devices)
    dag_res, dag_checks = _schedule_compute_dag(sched, uav_sample_pos)
    checks = fleet_checks + turret_checks + dag_checks
    return dict(
        method='三级协同任务调度：机群时间窗/机位/周转排班 + 云台转速/驻留波束排程 + 显存/争用感知 HEFT（无需训练数据）',
        fleet=fleet_res,
        sensor_turrets=turret_res,
        compute_dag=dag_res,
        checks=checks,
        feasible=all(c['passed'] for c in checks),
    )


# ---------------------------------------------------------------------------
# Unified Check, Execution & Persistence
# ---------------------------------------------------------------------------

def load_spec(scene_id: SceneId) -> dict:
    saved = store.get(f'modeling:spec:{scene_id}')
    if saved is not None:
        return StandardModelSpec.model_validate(saved).model_dump()
    return default_spec(scene_id)


def static_audit(spec_dict: dict) -> dict:
    spec = StandardModelSpec.model_validate(spec_dict).model_dump()
    siting_checks, siting_violations, _ = check_siting_set(spec['siting'], [])
    sites = {s['id']: s for s in spec['siting']['sites']}
    models = {m['id']: m for m in spec['siting']['models']}
    existing = [_expand_device(d, sites, models) for d in spec['siting']['existing_devices']]
    cand_valid = sum(1 for c in spec['siting']['candidate_devices'] if not check_siting_set(spec['siting'], [c])[1])
    return dict(
        existing_device_count=len(existing),
        candidate_device_count=len(spec['siting']['candidate_devices']),
        feasible_single_candidates=cand_valid,
        existing_violations=siting_violations,
        siting_baseline_checks=siting_checks,
        parameter_completeness=dict(
            siting_ready=len(spec['siting']['sites']) > 0 and len(spec['siting']['models']) > 0,
            routing_ready=True,
            scheduling_ready=len(spec['scheduling']['missions']) > 0 and len(spec['scheduling']['dag_tasks']) > 0,
            rl_track_pairing_note='跨站航迹配对（强化学习 + 训练数据集）属独立数据驱动感知关联阶段，不在本纯参数约束运筹层中混同。',
        ),
    )


def run_pipeline(scene_id: SceneId, spec_dict: dict, stage: str = 'all') -> dict:
    spec = StandardModelSpec.model_validate(dict(spec_dict, scene=scene_id)).model_dump()
    sites = {s['id']: s for s in spec['siting']['sites']}
    models = {m['id']: m for m in spec['siting']['models']}
    existing = [_expand_device(d, sites, models) for d in spec['siting']['existing_devices']]

    prev = store.get(f'modeling:result:{scene_id}') or {}
    siting_res = prev.get('siting')
    routing_res = prev.get('routing')
    scheduling_res = prev.get('scheduling')

    if stage in ('siting', 'all') or siting_res is None:
        siting_res = solve_siting(scene_id, spec['siting'])
    deployed = siting_res['deployed_network'] if siting_res else existing

    if stage in ('routing', 'all') or routing_res is None:
        routing_res = solve_routing(scene_id, spec['siting'], spec['routing'], deployed)

    mid_sample = tuple(routing_res['waypoints'][len(routing_res['waypoints']) // 2]) if routing_res else (
        spec['routing']['start_xyz'][0], spec['routing']['start_xyz'][1], spec['routing']['cruise_altitude_m']
    )
    if stage in ('scheduling', 'all') or scheduling_res is None:
        scheduling_res = solve_scheduling(scene_id, spec['siting'], spec['routing'], spec['scheduling'], deployed, mid_sample)

    all_checks = siting_res['checks'] + routing_res['checks'] + scheduling_res['checks']
    payload = dict(
        schema_version=SCHEMA_VERSION,
        scene=scene_id,
        stage_executed=stage,
        spec_revision=spec['revision'],
        input_digest=_digest(spec),
        siting=siting_res,
        routing=routing_res,
        scheduling=scheduling_res,
        checks=all_checks,
        passed_checks=sum(1 for c in all_checks if c['passed']),
        total_checks=len(all_checks),
        all_feasible=all(c['passed'] for c in all_checks),
        created_at=store.now(),
        boundary_note=(
            '本模块为设备选址、航路规划、任务调度的标准参数与工程约束求解层（组合优化/三维A*/多约束排班，不需要强化学习训练数据）。'
            '参数默认值为工程基准模板，支持人工按现场勘测台账修改；跨站航迹配对强化学习与训练数据集属于后续感知关联模块。'
        ),
    )
    payload['result_digest'] = _digest({k: payload[k] for k in ('input_digest', 'siting', 'routing', 'scheduling', 'checks')})
    return payload


SCHEMA_CATALOG = {
    'schema_version': SCHEMA_VERSION,
    'paradigm_distinction': {
        'model_driven_modules': ['设备选址 (Device Siting)', '航路规划 (Route Planning)', '任务调度 (Task Scheduling)'],
        'requires_training_data': False,
        'explanation': '选址、航路、调度均由物理几何参数、资源台账与工程运行约束完备定义，直接通过组合优化、加权图搜索与时间窗排程求解，无需训练样本。',
        'next_phase_data_driven': '跨站航迹配对（断续重接与多源关联）在采用强化学习序列关联决策时才需要标注训练数据集与交互环境。',
    },
    'modules': [
        {
            'id': 'siting',
            'title': '一、设备选址标准参数与工程约束',
            'solver': '工程容量与电磁兼容强约束边际收益寻优 + 全网共信道干扰复核',
            'parameter_groups': ['三维场景与建筑源（合成/雄安GBA/北京OSM）', '地形基准高程、电磁底噪与雨雾衰减', '站址承重/供电/带宽/市电/光纤/准入台账', '设备型号重量/功耗/频段目录'],
            'hard_constraints': ['站址场地准入与挂高上下限', '屋顶/杆塔承重、供电功率与回传带宽容量', '市电与光纤回传刚性匹配', '有源雷达同频隔离、雷达-RF阻塞隔离与重叠频段最小间距', '新增预算与新建站址上限'],
        },
        {
            'id': 'routing',
            'title': '二、航路规划标准参数与空域动力学约束',
            'solver': '风场地面速修正 + 感知覆盖走廊耦合加权三维避障规划',
            'parameter_groups': ['机型真空速、最大爬升/下降率与最小转弯半径', '电池能量 Wh、巡航/爬升功耗、载荷重量与返航保留 SoC', '风场矢量、起飞与最晚到达时间窗', '时效禁飞区、邻机四维航迹与感知网络耦合权重'],
            'hard_constraints': ['建筑棱柱膨胀安全间隔与时效禁飞区避让', '最大有效载荷、抗风前进可行性与转弯/爬升包线', '返航剩余电量 >= 保留 SoC 阈值', '到达截止时间窗与多机四维安全间隔', '沿程感知覆盖率下限与最大连续盲区段上限'],
        },
        {
            'id': 'scheduling',
            'title': '三、三级任务调度标准参数与资源争用约束',
            'solver': '机群机位排班 + 地面云台转速驻留排程 + 显存/争用感知 HEFT',
            'parameter_groups': ['机场起降机位容量、值班飞手并发上限与充电周转时长', '多优先级飞行任务时间窗、载重与能耗需求', '地面定向传感器云台角速度、波束驻留与目标重访上限', '机载/边缘/云内存显存容量与多机共享无线链路争用系数'],
            'hard_constraints': ['飞机载重/可用电量匹配与充电周转不重叠', '起降机位容量锁与飞手同时监管架次上限', '云台机械转动耗时 + 驻留时间下的多模态目标重访周期', '节点内存/GPU显存硬容量、通信距离可达性与 DAG 硬截止期'],
        },
    ],
}


# ---------------------------------------------------------------------------
# HTTP Routes
# ---------------------------------------------------------------------------

def _assert_scene(scene_id: str) -> SceneId:
    if scene_id not in ('urban', 'corridor', 'electromagnetic'):
        raise HTTPException(404, '场景不存在，应为 urban、corridor 或 electromagnetic')
    return scene_id  # type: ignore[return-value]


@router.get('/modeling')
def modeling_page(request: Request):
    html = (store.ROOT / 'static' / 'modeling.html').read_text(encoding='utf-8')
    root_path = request.scope.get('root_path', '')
    if root_path:
        html = html.replace('<head>', f'<head><base href="{root_path}/">', 1)
    return HTMLResponse(html)


@router.get('/api/modeling/schema')
def get_schema():
    return SCHEMA_CATALOG


@router.get('/api/modeling/{scene_id}')
def get_modeling_state(scene_id: str):
    sid = _assert_scene(scene_id)
    spec = load_spec(sid)
    audit = static_audit(spec)
    latest = store.get(f'modeling:result:{sid}')
    if latest:
        latest = dict(latest, stale=latest.get('input_digest') != _digest(spec))
    return dict(spec=spec, audit=audit, latest_result=latest, scenes=SCENES)


@router.put('/api/modeling/{scene_id}')
def save_modeling_spec(scene_id: str, body: StandardModelSpec):
    sid = _assert_scene(scene_id)
    current = load_spec(sid)
    spec = body.model_dump()
    spec['scene'] = sid
    spec['revision'] = int(current.get('revision', 0)) + 1
    try:
        audit = static_audit(spec)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    store.put(f'modeling:spec:{sid}', spec)
    return dict(spec=spec, audit=audit)


@router.post('/api/modeling/{scene_id}/reset')
def reset_modeling_spec(scene_id: str):
    sid = _assert_scene(scene_id)
    spec = default_spec(sid)
    store.put(f'modeling:spec:{sid}', spec)
    result = run_pipeline(sid, spec, 'all')
    store.put(f'modeling:result:{sid}', result)
    return dict(spec=spec, audit=static_audit(spec), latest_result=result)


@router.post('/api/modeling/{scene_id}/check')
def check_modeling_spec(scene_id: str, body: StandardModelSpec):
    sid = _assert_scene(scene_id)
    spec = body.model_dump()
    spec['scene'] = sid
    return static_audit(spec)


@router.post('/api/modeling/{scene_id}/solve')
def solve_modeling(scene_id: str, request: SolveRequest = SolveRequest()):
    sid = _assert_scene(scene_id)
    if request.spec is not None:
        spec = request.spec.model_dump()
        spec['scene'] = sid
        spec['revision'] = int(load_spec(sid).get('revision', 0)) + 1
        store.put(f'modeling:spec:{sid}', spec)
    else:
        spec = load_spec(sid)
    try:
        result = run_pipeline(sid, spec, request.stage)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    store.put(f'modeling:result:{sid}', result)
    return dict(spec=spec, audit=static_audit(spec), result=result)
