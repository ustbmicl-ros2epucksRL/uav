"""Explicit simulator traffic injection; never accepts real-device commands."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import sim, dynamic
from .business import Point

router=APIRouter(prefix='/api/business/simulation-traffic')


class Traffic(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^SIM-[A-Za-z0-9_-]{1,24}$')
    position: Point
    waypoints: list[Point] = Field(default_factory=list,max_length=20)
    speed: float = Field(default=20,ge=1,le=50,allow_inf_nan=False)


@router.post('')
def add(body: Traffic):
    with sim.LOCK:
        if body.id in sim.VEHICLES:raise HTTPException(409,'演练机编号已存在')
        if sum(k.startswith('SIM-') for k in sim.VEHICLES)>=10:raise HTTPException(409,'最多 10 架演练机')
        pos=[body.position.x,body.position.y,body.position.z]
        v=sim.Vehicle(body.id,'动态交通演练机','simulation',list(pos),'报文终端')
        v.position=list(pos);v.speed=body.speed;v.armed=True
        v.mission=[[p.x,p.y,p.z] for p in body.waypoints]
        v.mode='AUTO' if v.mission else 'LOITER'
        sim.VEHICLES[v.id]=v
        dynamic.prepare()
        return dict(source='simulation',vehicle=sim.snapshot(v))


@router.delete('/{ident}')
def remove(ident:str):
    with sim.LOCK:
        if not ident.startswith('SIM-') or ident not in sim.VEHICLES:
            raise HTTPException(404,'仅可移除本接口创建的演练机')
        del sim.VEHICLES[ident]
        dynamic.prepare()
        return dict(removed=ident,source='simulation')
