"""Attach local simulator traces to a frozen sensing plan without issuing flight commands."""
import importlib
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import sim, station

router = APIRouter()


class EvidenceLink(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    assessment_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    note: str = Field(default='', max_length=1000)


@router.post('/api/vehicles/{vehicle_id}/planning-evidence')
def link_trace(vehicle_id: str, request: EvidenceLink):
    vehicle = sim.VEHICLES.get(vehicle_id)
    if vehicle is None:
        raise HTTPException(404, '本地仿真飞行器不存在')
    acceptance = importlib.import_module('sensing_plan.acceptance_api')
    planning_store = importlib.import_module('sensing_plan.store')
    job = acceptance.detail(request.assessment_id)
    if job['status'] != 'succeeded' or not job['evidence']['plan_id']:
        raise HTTPException(409, '请先完成验算并归档规划方案')
    trace = sim.track_of(vehicle)
    if not trace['points']:
        raise HTTPException(409, '当前飞行器尚无仿真轨迹')
    record = dict(id=uuid.uuid4().hex, assessment_id=job['id'], plan_id=job['evidence']['plan_id'],
                  task_id=job['snapshot']['task']['id'], vehicle_id=vehicle_id, created_at=planning_store.now(),
                  provenance='synthetic', source='ground_station_local_simulator', note=request.note,
                  planning_input_digest=job['result']['input_digest'], planning_stale=job['stale'],
                  coordinate_frame='local_ENU_m', geographic_origin=station.config(),
                  coordinate_alignment='pending_verification', time_basis='vehicle_simulation_seconds',
                  track=trace, mission=[list(point) for point in vehicle.mission],
                  events=[dict(event) for event in station.EVENTS if event['vehicle_id'] == vehicle_id],
                  sensor_observations=[], scope='本地仿真参考航迹与执行事件；坐标对齐和独立感知观测待核验')
    planning_store.put('portal:flight-evidence:' + record['id'], record)
    return record
