"""Managed local ArduCopter SITL only; never connects to a configured real aircraft."""
import copy
import math
import os
from pathlib import Path
import subprocess
import socket
import threading
import time
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import flight_records

router = APIRouter(prefix='/api/sitl')
LOCK = threading.RLock()
WIRE = threading.Lock()
PROCESS = None
LINK = None
READER = None
STATE = {'source':'ardupilot-sitl','connected':False,'error':'尚未启动','telemetry':{},'receipts':{}}

class Command(BaseModel):
    command_id: str = Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    execution_id: str | None = Field(default=None, min_length=1, max_length=80)
    command: Literal['arm','disarm','guided','loiter','rtl','land','takeoff','auto']
    altitude: float = Field(default=40,ge=10,le=120,allow_inf_nan=False)
    expires_at: float = Field(allow_inf_nan=False)

def apply(message):
    from pymavlink import mavutil
    if message.get_srcSystem()!=1 or message.get_srcComponent()!=1:
        return
    kind=message.get_type(); values={}
    if kind=='HEARTBEAT':
        values=dict(armed=bool(message.base_mode&128),mode=mavutil.mode_string_v10(message))
        STATE['heartbeat_at']=time.monotonic()
    elif kind=='ATTITUDE':
        values=dict(roll=math.degrees(message.roll),pitch=math.degrees(message.pitch),yaw=math.degrees(message.yaw)%360)
    elif kind=='GLOBAL_POSITION_INT':
        values=dict(latitude=message.lat/1e7,longitude=message.lon/1e7,altitude=message.relative_alt/1000,speed=math.hypot(message.vx,message.vy)/100,climb=-message.vz/100)
    elif kind=='SYS_STATUS':
        values=dict(battery=None if message.battery_remaining<0 else message.battery_remaining)
    elif kind=='GPS_RAW_INT':
        values=dict(gps_fix=message.fix_type,satellites=message.satellites_visible)
    elif kind=='STATUSTEXT':
        STATE['message']=message.text
    elif kind=='MISSION_CURRENT':
        values=dict(waypoint=message.seq,mission_state=getattr(message,'mission_state',None))
        if STATE.get('patrol') and message.seq==len(STATE['patrol']['waypoints'])-1:
            STATE['patrol']['final_land_entered']=True
    elif kind=='EXTENDED_SYS_STATE':
        values=dict(landed_state=message.landed_state)
    elif kind=='MISSION_ITEM_REACHED':
        values=dict(reached=message.seq)
    now=time.time()
    with LOCK:
        STATE['telemetry'].update(values)
        STATE.setdefault('updated_at',{}).update({key:now for key in values})
        if kind=='GLOBAL_POSITION_INT' and not STATE.get('recording_complete') and (not STATE.get('track') or now-STATE['track'][-1]['at']>=.2):
            ages={key:max(0,now-stamp) for key,stamp in STATE['updated_at'].items()}
            from .algorithm_execution import sample
            try:STATE['algorithm_live']=sample(STATE,now)
            except Exception as exc:STATE['algorithm_live']=dict(status='error',at=now,message=str(exc))
            frame = dict(STATE['telemetry'],at=now,field_age_s=ages,compute=copy.deepcopy(STATE.get('algorithm_live')))
            purpose=(STATE.get('execution_binding') or {}).get('purpose')
            if purpose in ('AoI 双机巡检仿真 1','AoI 双机巡检仿真 2','AoI 策略调度仿真'):
                from . import aoi_policy
                try:
                    from . import aoi_joint
                    sampler=aoi_joint.sample if purpose.startswith(aoi_joint.PURPOSE) else aoi_policy.sample
                    frame['aoi_policy'] = sampler(STATE, now)
                except Exception as exc:
                    frame['aoi_policy'] = {'error': str(exc), 'at': now}
            STATE.setdefault('track',[]).append(frame)
            STATE['track']=STATE['track'][-5000:]
        if patrol_completed(STATE):
            STATE['patrol']['status']='completed'
            STATE['recording_complete']=True
            STATE.setdefault('track',[]).append(dict(STATE['telemetry'],at=now,field_age_s={key:max(0,now-stamp) for key,stamp in STATE['updated_at'].items()},compute=copy.deepcopy(STATE.get('algorithm_live'))))
        from . import compute_runtime
        if kind=='GLOBAL_POSITION_INT':compute_runtime.observe(STATE,now)
        if STATE.get('record_id'):
            try:
                flight_records.checkpoint(STATE, force=bool(STATE.get('recording_complete') and not STATE.get('_record_final_saved')))
                if STATE.get('recording_complete'):STATE['_record_final_saved']=True
            except Exception as exc:
                STATE['record_error']='飞行记录保存失败：'+str(exc)

def patrol_completed(state):
    task=state.get('patrol',{});t=state.get('telemetry',{})
    if task.get('status')!='patrolling' or not task.get('final_land_entered'):
        return False
    return (t.get('armed') is False and t.get('landed_state')==1
            and abs(t.get('altitude',999))<1
            and t.get('reached',-1)>=len(task['waypoints'])-2)

def receive():
    while PROCESS is not None and PROCESS.poll() is None:
        try:
            with WIRE:
                msg=LINK.recv_match(blocking=True,timeout=.1)
                if msg: apply(msg)
        except Exception as exc:
            with LOCK:STATE['error']=str(exc)
            break
        time.sleep(.001)

@router.get('')
def status():
    with LOCK:
        result=copy.deepcopy({key:value for key,value in STATE.items() if key not in ('receipts','track','_aoi_run')})
    result['running']=PROCESS is not None and PROCESS.poll() is None
    result['connected']=result['running'] and time.monotonic()-result.get('heartbeat_at',0)<3
    result['binary_available']=Path(os.environ.get('ARDUPILOT_SITL_BINARY','/tmp/lowair-sitl-source/build/sitl/bin/arducopter')).is_file()
    from . import compute_runtime
    result['compute_runtime']=compute_runtime.snapshot(result.get('execution_binding',{}).get('execution_id','unbound'))
    result['video_source']='independent-test-pattern'
    return result

@router.post('/start')
def start():
    return start_local()


def start_local(instance=None, port=None, anchor=None):
    global PROCESS,LINK,READER
    from pymavlink import mavutil
    with LOCK:
        if PROCESS is not None and PROCESS.poll() is None:return status()
        binary=Path(os.environ.get('ARDUPILOT_SITL_BINARY','/tmp/lowair-sitl-source/build/sitl/bin/arducopter'))
        if not binary.is_file():raise HTTPException(503,'ArduCopter SITL 二进制尚未构建')
        port=int(os.environ.get('SITL_TCP_PORT','5775')) if port is None else port
        instance=int(os.environ.get('SITL_INSTANCE','0')) if instance is None else instance
        directory=Path('/tmp/lowair-sitl-runtime-'+str(instance));directory.mkdir(exist_ok=True)
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(('127.0.0.1',port))
        except OSError:
            raise HTTPException(409,f'SITL 专用端口 {port} 已被占用，不连接未知进程')
        log=open(directory/'sitl.log','ab')
        from . import station
        anchor=anchor or station.config()
        flight_records.checkpoint(STATE, force=True, closing='stopped')
        for key in ['record_id','record_created_at','_record_saved_at','_record_frame_at','_record_final_saved','execution_binding','algorithm_live']:STATE.pop(key,None)
        STATE['anchor']=anchor
        STATE['speedup']=3
        home=f"{anchor['latitude']},{anchor['longitude']},10,0"
        PROCESS=subprocess.Popen([str(binary),'--instance',str(instance),'--model','+','--home',home,'--speedup','3','--serial0','tcp:'+str(port),'--defaults',str(Path(__file__).parent.parent/'sitl.parm')],cwd=directory,stdout=log,stderr=subprocess.STDOUT)
        log.close()
        STATE.pop('patrol',None)
        STATE.update(recording_complete=False,track=[],telemetry={},updated_at={},receipts={},heartbeat_at=0,error='正在等待 SITL 心跳')
    try:
        LINK=mavutil.mavlink_connection('tcp:127.0.0.1:'+str(port),source_system=255,retries=10)
        hb=LINK.wait_heartbeat(timeout=10)
        if not hb:raise RuntimeError('SITL 心跳超时')
        if PROCESS.poll() is not None:raise RuntimeError('本次 SITL 进程已退出')
        apply(hb)
        STATE['wire_protocol']=LINK.WIRE_PROTOCOL_VERSION
        LINK.mav.request_data_stream_send(1,1,mavutil.mavlink.MAV_DATA_STREAM_ALL,5,1)
        # Extended ground/air state is not included in legacy stream groups.
        LINK.mav.command_long_send(1,1,511,0,245,500000,0,0,0,0,0)
        STATE['error']=''
        READER=threading.Thread(target=receive,daemon=True,name='sitl-telemetry')
        READER.start()
    except Exception as exc:
        stop();raise HTTPException(503,str(exc))
    return status()

def stop():
    global PROCESS,LINK,READER
    from . import compute_runtime
    compute_runtime.shutdown()
    if PROCESS is not None and PROCESS.poll() is None:
        PROCESS.terminate()
        try:PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:PROCESS.kill();PROCESS.wait()
    PROCESS=None
    if LINK is not None:LINK.close()
    LINK=None
    if READER is not None and READER is not threading.current_thread():READER.join(timeout=2)
    READER=None
    with LOCK:
        flight_records.checkpoint(STATE, force=True, closing='stopped')

@router.post('/restart')
def restart():
    """Reset only the owned local simulator, preserving archived flights."""
    from . import compute_runtime
    with WIRE:
        telemetry = STATE.get('telemetry', {})
        if PROCESS is not None and PROCESS.poll() is None:
            if telemetry.get('armed') is not False or telemetry.get('landed_state') != 1:
                raise HTTPException(409, '仅允许在已落地且未解锁时重启本机 SITL')
        if STATE.get('demo_owner') or (compute_runtime.snapshot() or {}).get('state') == 'running':
            raise HTTPException(409, '请先结束当前仿真或计算任务')
        stop()
        return start()

@router.post('/commands')
def command(body:Command):
    if STATE.get('demo_owner'):raise HTTPException(409,'一键演示运行中，请先中止演示再手动控制')
    return execute_command(body)

def execute_command(body:Command):
    from pymavlink import mavutil
    with WIRE:
        with LOCK:
            binding=STATE.get('execution_binding',{})
            if body.execution_id!=binding.get('execution_id'):raise HTTPException(409,'指令执行编号与当前 SITL 任务不匹配')
            if binding and STATE.get('patrol',{}).get('status') in ('completed','cancelled'):raise HTTPException(409,'该业务执行已结束，不能重复起飞')
            old=STATE['receipts'].get(body.command_id)
            if old:
                if old['request']!=body.model_dump():raise HTTPException(409,'指令编号重复且参数不一致')
                return old
        if body.expires_at<time.time():raise HTTPException(409,'指令已过期')
        if not status()['connected']:raise HTTPException(409,'SITL 未连接')
        modes={'guided':4,'loiter':5,'rtl':6,'land':9,'auto':3}
        if body.command in modes: cmd=176;params=[1,modes[body.command],0,0,0,0,0]
        elif body.command in ('arm','disarm'):cmd=400;params=[int(body.command=='arm'),0,0,0,0,0,0]
        else:cmd=22;params=[0,0,0,0,0,0,body.altitude]
        if any(r.get('mav_command')==cmd and r['status'] in ('unknown','in_progress') for r in STATE['receipts'].values()):
            raise HTTPException(409,'同类指令结果尚不确定，请核验状态；禁止重新发送')
        # Drain old ACKs before issuing this serialized command. No automatic retries.
        while (message:=LINK.recv_match(blocking=False)) is not None:apply(message)
        result=dict(command_id=body.command_id,request=body.model_dump(),source='ardupilot-sitl',status='sent',sent_at=time.time(),mav_command=cmd)
        with LOCK:STATE['receipts'][body.command_id]=result
        LINK.mav.command_long_send(1,1,cmd,0,*params)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            message=LINK.recv_match(blocking=True,timeout=.15)
            if not message:continue
            apply(message)
            if message.get_type()=='COMMAND_ACK' and message.get_srcSystem()==1 and message.get_srcComponent()==1 and message.command==cmd:
                result.update(status='acknowledged' if message.result==0 else 'in_progress' if message.result==5 else 'rejected',mav_result=message.result,received_at=time.time())
                break
        else:result.update(status='unknown',reason='未收到 COMMAND_ACK；不自动重发')
        if result['status']=='acknowledged' and STATE.get('patrol'):
            if body.command=='auto':STATE['patrol']['status']='patrolling'
            elif body.command in ('loiter','rtl','land'):STATE['patrol']['status']='interrupted'
        return result

@router.get('/commands/{command_id}')
def receipt(command_id:str):
    with LOCK:result=copy.deepcopy(STATE['receipts'].get(command_id))
    if not result:raise HTTPException(404,'回执不存在')
    return result

class PatrolRequest(BaseModel):
    area_id: Literal['west','east']='west'
    altitude: float=Field(default=180,ge=40,le=280,allow_inf_nan=False)

@router.post('/patrol/upload')
def upload_patrol(body:PatrolRequest):
    if STATE.get('demo_owner'):raise HTTPException(409,'一键演示运行中，不可改写航线')
    return execute_upload(body)

def safe_patrol_route(points, boxes, fence, battery):
    """Keep patrol targets; reroute blocked cruise legs at the requested altitude."""
    from . import disposition
    route=[list(points[0])]
    for start,goal in zip(points,points[1:]):
        if disposition.clear(start,goal,boxes):
            leg=[start,goal]
        elif start[2]==goal[2] and start[2]>0:
            # Extra turn margin beyond the standard obstacle clearance.
            planning_boxes=copy.deepcopy(boxes)
            for box in planning_boxes:
                for axis in (0,1):
                    box['lower'][axis]-=20
                    box['upper'][axis]+=20
            leg=disposition.detour(start,goal,start[2],planning_boxes,fence)
            if leg is None:raise HTTPException(409,'巡检航段无安全绕行路线，禁止上传和起飞')
        else:
            raise HTTPException(409,'起降航段受阻，禁止上传和起飞')
        for point in leg[1:]:
            if math.dist(route[-1],point)>1e-8:route.append(list(point))
    checked=disposition.check(route,boxes,fence,10,battery)
    if not checked['passed'] or len(route)>80:
        raise HTTPException(409,dict(message='SITL 园区航线未通过静态检查',check=checked))
    return route,checked


def execute_upload(body:PatrolRequest, business_mission=None):
    """Use the platform park area and static obstacle checks, then mission handshake."""
    from pymavlink import mavutil
    with WIRE:
        if STATE.get('execution_binding') and STATE.get('patrol',{}).get('status') not in ('completed','cancelled') and business_mission is None:raise HTTPException(409,'SITL 已绑定业务任务，不能覆盖航线')
        if not status()['connected']:raise HTTPException(409,'SITL 未连接')
        if STATE['telemetry'].get('armed'):raise HTTPException(409,'先落地上锁再上传')
        from . import patrol, disposition, route_plan, incidents, business, sim
        anchor=STATE['anchor'];home=(anchor['latitude'],anchor['longitude'])
        x,y,x1,y1=patrol.AREAS[body.area_id]['bounds'];h=body.altitude
        points=[(0,0,0),(0,0,h),(x,y,h),(x1,y,h),(x1,y1,h),(x,y1,h),(x,y,h),(0,0,h),(0,0,0)]
        with business.connect() as db:space=incidents.airspace(db)
        battery=STATE['telemetry'].get('battery')
        if battery is None:raise HTTPException(409,'SITL 电池遥测尚未有效，不上传任务')
        if business_mission:points=business_mission['route']
        original_points=copy.deepcopy(points)
        boxes=disposition.obstacles(route_plan.scene_record(business_mission['scene'] if business_mission else 'urban')['buildings'],space['zones'])
        if business_mission:
            check=disposition.check(points,boxes,sim.FENCE,10,battery)
            if not check['passed']:raise HTTPException(409,'批准航线在当前约束下不安全，禁止静默绕行')
        else:points,check=safe_patrol_route(points,boxes,sim.FENCE,battery)
        items=[]
        for i,(east,north,height) in enumerate(points):
            lat=home[0]+math.degrees(north/6378137)
            lon=home[1]+math.degrees(east/(6378137*math.cos(math.radians(home[0]))))
            cmd=22 if i==1 else 21 if i==len(points)-1 else 16
            items.append((lat,lon,height,cmd))
        LINK.mav.mission_count_send(1,1,len(items))
        deadline=time.monotonic()+8; sent=set()
        while time.monotonic()<deadline:
            m=LINK.recv_match(blocking=True,timeout=.2)
            if not m:continue
            apply(m)
            if m.get_srcSystem()!=1 or m.get_srcComponent()!=1:continue
            if m.get_type() in ('MISSION_REQUEST_INT','MISSION_REQUEST') and 0<=m.seq<len(items):
                lat,lon,z,cmd=items[m.seq]
                if m.get_type()=='MISSION_REQUEST_INT':
                    LINK.mav.mission_item_int_send(1,1,m.seq,6,cmd,0,1,0,0,0,0,int(lat*1e7),int(lon*1e7),z)
                else:
                    LINK.mav.mission_item_send(1,1,m.seq,3,cmd,0,1,0,0,0,0,lat,lon,z)
                sent.add(m.seq)
            elif m.get_type()=='MISSION_ACK':
                if m.type!=0 or len(sent)!=len(items):raise HTTPException(409,'航线上传未被完整接受')
                with LOCK:
                    flight_records.checkpoint(STATE, force=True, closing='superseded')
                    STATE['track']=[];STATE['recording_complete']=False;STATE.pop('algorithm_live',None)
                    STATE.pop('_record_final_saved',None)
                    STATE['patrol']={'status':'uploaded','waypoints':items,'source':'ardupilot-sitl','area_id':business_mission['binding'].get('algorithm',{}).get('area_id') if business_mission else body.area_id,'route':points,'original_route':original_points,'rerouted':points!=[list(p) for p in original_points],'airspace_revision':space['revision'],'check':check,'note':'平台合成建筑与禁飞区静态检查；不代表真实空域许可'}
                    if business_mission:STATE['execution_binding']=business_mission['binding']
                    else:STATE.pop('execution_binding',None)
                    flight_records.begin(STATE)
                return STATE['patrol']
        raise HTTPException(504,'MISSION_ACK 超时，禁止自动起飞')

@router.get('/patrol/result')
def patrol_result():
    with LOCK:
        result = copy.deepcopy({'source':'ardupilot-sitl','record_id':STATE.get('record_id'),'business_task_id':STATE.get('execution_binding',{}).get('task_id'),'execution_id':STATE.get('execution_binding',{}).get('execution_id'),'anchor':STATE.get('anchor'),'patrol':STATE.get('patrol'), 'track':STATE.get('track',[]), 'receipts':list(STATE['receipts'].values()), 'video_evidence':None,'algorithm':STATE.get('execution_binding',{}).get('algorithm')})
    from . import compute_store
    result['runtime_history']=compute_store.history(result['record_id']) if result.get('record_id') else []
    return result
