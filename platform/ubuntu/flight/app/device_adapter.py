"""Explicit simulation adapter; hardware paths never silently fall back to simulation."""
import time
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from . import sim, business

router=APIRouter(prefix='/api/devices')


class Command(BaseModel):
    command_id:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    source:Literal['simulation','hardware']='simulation'
    command:Literal['arm','disarm','auto','loiter','guided','rtl','land','stop']
    expires_at:float=Field(allow_inf_nan=False)


@router.get('/{vehicle_id}/telemetry')
def telemetry(vehicle_id:str):
    with sim.LOCK:
        v=sim.get(vehicle_id)
        if not v:raise HTTPException(404,'飞机不存在')
        return dict(source='simulation',vehicle_id=v.id,sampled_at=time.time(),valid=True,
                    coordinate_frame='local-ENU',altitude_reference='simulation-origin',telemetry=sim.snapshot(v))


@router.post('/{vehicle_id}/commands')
def send(vehicle_id:str,body:Command):
    if body.source=='hardware':raise HTTPException(501,'真实飞控指令适配器尚未启用；不会回退到仿真')
    with sim.LOCK,business.connect() as db:
        key='device-command:'+body.command_id
        old=business.get(db,key)
        request=dict(vehicle_id=vehicle_id,**body.model_dump())
        if old:
            if old['request']!=request:raise HTTPException(409,'指令编号已用于其他请求')
            return old
        if body.expires_at<time.time():raise HTTPException(409,'指令已过期，未执行')
        from .main import command,Command as LegacyCommand
        result=dict(command_id=body.command_id,request=request,source='simulation',received_at=time.time())
        try:
            result['telemetry']=command(vehicle_id,LegacyCommand(cmd=body.command))
            result['status']='acknowledged'
        except HTTPException as exc:
            result.update(status='rejected',reason=exc.detail)
        result['acknowledged_at']=time.time()
        business.put(db,key,result)
        return result


@router.get('/commands/{command_id}/receipt')
def receipt(command_id:str):
    with business.connect() as db:item=business.get(db,'device-command:'+command_id)
    if not item:raise HTTPException(404,'没有该指令回执')
    return item


class VideoRequest(BaseModel):
    source:Literal['simulation','hardware']='simulation'


@router.post('/{vehicle_id}/video/sessions')
def video(vehicle_id:str,body:VideoRequest):
    if body.source=='hardware':raise HTTPException(501,'真实视频网关未配置')
    with sim.LOCK:
        v=sim.get(vehicle_id)
        if not v:raise HTTPException(404,'飞机不存在')
        if v.payload!='光电吊舱':raise HTTPException(409,'飞机无光电载荷')
    ident=uuid.uuid4().hex
    item=dict(id=ident,vehicle_id=vehicle_id,source='simulation',transport='synthetic-svg-frames',expires_at=time.time()+3600,
              playback_url='/api/devices/video/'+ident+'/frame',note='合成遥测画面，不是真实视频或图像识别')
    with business.connect() as db:business.put(db,'video-session:'+ident,item)
    return item


@router.get('/video/{session_id}/frame')
def frame(session_id:str):
    with business.connect() as db:item=business.get(db,'video-session:'+session_id)
    if not item or item['expires_at']<time.time():raise HTTPException(410,'视频会话不存在或已过期')
    with sim.LOCK:
        v=sim.get(item['vehicle_id'])
        if not v:raise HTTPException(404,'飞机离线')
        x,y,z=v.position;second=v.seconds
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#112b38"/><path d="M0 90H640M0 180H640M0 270H640M160 0V360M320 0V360M480 0V360" stroke="#315767"/>
<circle cx="{40+x/2000*560:.1f}" cy="{320-y/2000*240:.1f}" r="9" fill="#6be0ca"/>
<text x="20" y="30" fill="white" font-size="18">SIMULATION / {item['vehicle_id']} / {second:.1f}s</text>
<text x="20" y="342" fill="#a8d5df" font-size="15">ENU {x:.1f}, {y:.1f} / ALT {z:.1f}m — synthetic telemetry frame</text></svg>'''
    return Response(svg,media_type='image/svg+xml',headers={'Cache-Control':'no-store'})
