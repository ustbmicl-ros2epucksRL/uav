"""Local worker controls, always bound to the current managed SITL execution."""
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import compute_runtime as runtime, compute_store, sitl

router=APIRouter(prefix='/api/compute',tags=['本地端边云实际执行'])


class Binding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    execution_id: str=Field(min_length=1,max_length=80)


class Fault(Binding):
    task: Literal['preprocess','detect','segment','fusion','alert']='detect'
    mode: Literal['fail','timeout','crash']='fail'


class NodeAction(Binding):
    node_id: str=Field(min_length=1,max_length=80)
    action: Literal['offline','recover']
    duration_s: float=Field(default=10,ge=1,le=60,allow_inf_nan=False)


class NetworkAction(Binding):
    profile: Literal['normal','weak','disconnected']
    duration_s: float=Field(default=10,ge=1,le=60,allow_inf_nan=False)
    mbps: float=Field(default=1,ge=.05,le=1000,allow_inf_nan=False)
    latency_ms: float=Field(default=200,ge=0,le=3000,allow_inf_nan=False)


@router.get('')
def status():
    return runtime.snapshot() or dict(state='not_started',source='local-process-execution')


@router.post('/start')
def start(body:Binding):
    state=sitl.status()
    binding=state.get('execution_binding',{})
    if body.execution_id!=binding.get('execution_id') or not binding.get('algorithm') or not state.get('record_id'):
        raise HTTPException(409,'请先准备关联算法的 SITL 区域任务')
    if not state['connected'] or state.get('recording_complete'):
        raise HTTPException(409,'SITL 须在线且任务未结束')
    try:
        item=runtime.start(binding,state['record_id'])
        item.offer(state.get('algorithm_live'))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return dict(runtime_id=item.id,state='starting',execution_id=body.execution_id)


def matching(body):
    item=runtime.CURRENT
    if not item or item.binding['execution_id']!=body.execution_id:
        raise HTTPException(409,'计算执行编号不匹配')
    return item


@router.post('/stop')
def stop(body:Binding):
    matching(body).stop()
    return runtime.snapshot()


@router.post('/fault')
def fault(body:Fault):
    manual_matching(body)
    try:matching(body).inject(body.task,body.mode)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return dict(accepted=True,task=body.task,mode=body.mode,scope='下一次该任务派发，显式故障注入')


@router.get('/records/{record_id}/batches')
def batches(record_id:str):
    return dict(batches=compute_store.batches(record_id))


@router.post('/node')
def node_action(body:NodeAction):
    manual_matching(body)
    try:
        matching(body).node_action(body.node_id,body.action,body.duration_s)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    return dict(accepted=True,node_id=body.node_id,action=body.action,
                scope='本机计算进程故障仿真，不控制飞机')


@router.post('/network')
def network_action(body:NetworkAction):
    manual_matching(body)
    try:
        matching(body).network_action(body.profile,body.duration_s,body.mbps,body.latency_ms)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    return dict(accepted=True,profile=body.profile,scope='本地链路仿真，不修改主机网络或飞控连接')


class DemoAction(Binding):
    action: Literal['start','short_transfer','cancel']='start'


@router.post('/demo')
def demo_action(body:DemoAction):
    try:matching(body).demo_action(body.action)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return dict(accepted=True,scope='仅计算链路与进程故障仿真，不操作飞机')


def manual_matching(body):
    item=matching(body)
    with item.lock:
        if item.demo_request in ('start', 'short_transfer') or item.demo and item.demo['state']=='running':
            raise HTTPException(409,'请先中止一键演示，再单独注入故障')
    return item
