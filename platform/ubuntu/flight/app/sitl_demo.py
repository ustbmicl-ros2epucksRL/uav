"""Managed SITL workflow. Injected demo alerts are never detector observations."""
import copy
import math
from typing import Literal
import threading
import time
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import business, sitl, patrol, station, incidents

router = APIRouter(prefix='/api/sitl-demo')
LOCK = threading.RLock()
JOB = None
WORKER = None
CANCEL = threading.Event()
ACTIVE = {'running', 'awaiting_resolution'}

class DemoRequest(BaseModel):
    auto_resolve: bool = True
    area_id: Literal["west", "east"] = "west"
    aircraft_id: Literal["SITL-1"] = "SITL-1"

@router.get('/catalog')
def catalog():
    """Only advertise aircraft actually controllable through this adapter."""
    anchor=station.config()
    def geo(x,y):
        return [anchor['latitude']+math.degrees(y/6378137),
                anchor['longitude']+math.degrees(x/(6378137*math.cos(math.radians(anchor['latitude']))))]
    def polygon(x,y,x1,y1):return [geo(x,y),geo(x1,y),geo(x1,y1),geo(x,y1)]
    with business.connect() as db:space=incidents.airspace(db)
    return dict(source='simulation',anchor=anchor,airspace_revision=space['revision'],
        areas=[dict(**a,polygon=polygon(*a['bounds'])) for a in patrol.AREAS.values()],
        aircraft=[dict(id='SITL-1',name='SITL 1 · ArduPilot 仿真',area_ids=list(patrol.AREAS))],
        zones=[dict(**z,polygon=polygon(z['x'],z['y'],z['x']+z['w'],z['y']+z['d'])) for z in space['zones']])


class Resolution(BaseModel):
    actor: str = Field(min_length=1,max_length=80)
    note: str = Field(min_length=1,max_length=500)


def persist():
    with business.connect() as db:
        business.put(db,'sitl-demo:latest',JOB['id'])
        business.put(db,'sitl-demo:'+JOB['id'],JOB)


def event(phase, text, status='running'):
    with LOCK:
        JOB.update(phase=phase,status=status,updated_at=time.time())
        JOB['events'].append(dict(phase=phase,text=text,at=time.time()))
        persist()


def current():
    global JOB
    with LOCK:
        if JOB is None:
            with business.connect() as db:
                ident=business.get(db,'sitl-demo:latest')
                JOB=business.get(db,'sitl-demo:'+ident) if ident else None
            if JOB and JOB['status'] in ACTIVE:
                JOB.update(status='interrupted',phase='interrupted',error='服务重启，演示未自动恢复')
                persist()
        return JOB


@router.get('')
def status():
    with LOCK:
        job=current()
        return copy.deepcopy({k:v for k,v in job.items() if k!='result'}) if job else {'status':'idle','events':[],'alerts':[]}


def wait_until(predicate, timeout, description):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if CANCEL.wait(.25):raise RuntimeError('演示已中止')
        s=sitl.status()
        if not s['connected']:raise RuntimeError('SITL 连接中断；停止自动指令')
        if predicate(s):return s
    raise RuntimeError(description+'超时；停止自动指令')


def send(name):
    if CANCEL.is_set():raise RuntimeError('演示已中止')
    r=sitl.execute_command(sitl.Command(command_id=uuid.uuid4().hex,command=name,expires_at=time.time()+20))
    with LOCK:
        JOB['receipts'].append(copy.deepcopy(r));persist()
    return r


def require_ack(name):
    r=send(name)
    if r['status']!='acknowledged':raise RuntimeError(f"{name} 回执为 {r['status']}，停止自动指令")


def create_alert(s):
    t=s['telemetry']
    alert=dict(id=uuid.uuid4().hex[:12],source='synthetic-injection',title='演示注入：园区异常目标',
               status='open',area_id=JOB.get('area_id','west'),aircraft_id=JOB.get('aircraft_id','SITL-1'),at=time.time(),telemetry=copy.deepcopy(t),video_evidence=None,
               note='仅用于流程演示；位置来自 SITL 遥测，没有图像识别或机载视频证据')
    with LOCK:JOB['alerts'].append(alert);persist()
    return alert


def resolve_alert(run_id,alert_id,body,automatic=False):
    if not body.actor.strip() or not body.note.strip():raise HTTPException(422,'处置人和说明不能为空')
    with LOCK:
        job=current()
        if not job or job['id']!=run_id:raise HTTPException(409,'演示任务已变化，请刷新')
        alert=next((a for a in job['alerts'] if a['id']==alert_id),None)
        if not alert:raise HTTPException(404,'预警不存在')
        if alert['status']=='resolved':return copy.deepcopy(alert)
        if CANCEL.is_set():raise HTTPException(409,'演示正在中止，不再触发续飞')
        if job['phase']!='review':raise HTTPException(409,'当前未处于预警核验阶段')
        alert.update(status='resolved',actor=body.actor.strip(),resolution=body.note.strip(),resolved_at=time.time(),automatic=automatic)
        job['events'].append(dict(phase='resolved',text='演示预警核验完成：'+body.note.strip(),at=time.time()))
        persist();return copy.deepcopy(alert)


@router.post('/{run_id}/alerts/{alert_id}/resolve')
def resolve(run_id:str,alert_id:str,body:Resolution):
    return resolve_alert(run_id,alert_id,body)


def finish_result():
    result=sitl.patrol_result()
    result['final_telemetry']=sitl.status()['telemetry']
    with LOCK:JOB['result']=result;persist()


def run():
    try:
        event('connecting','连接本机 ArduPilot SITL')
        if sitl.status()['running']:
            event('resetting','重置已上锁的本机 SITL，为演示补充仿真电量')
            sitl.stop()
        sitl.start()
        wait_until(lambda s:s['telemetry'].get('gps_fix',0)>=3 and s['telemetry'].get('landed_state')==1,60,'定位与地面状态')
        area_id=JOB.get('area_id','west')
        event('upload','检查建筑/禁飞区并上传'+patrol.AREAS[area_id]['name']+'航线')
        sitl.execute_upload(sitl.PatrolRequest(area_id=area_id))
        require_ack('guided')
        event('arming','等待飞控预检通过并解锁')
        for _ in range(20):
            r=send('arm')
            if r['status']=='acknowledged':break
            if r['status']!='rejected':raise RuntimeError('解锁结果不确定，不自动重发')
            if CANCEL.wait(2):raise RuntimeError('演示已中止')
        else:raise RuntimeError('飞控预检未通过')
        event('takeoff','起飞并等待实际高度到达')
        require_ack('takeoff')
        wait_until(lambda s:s['telemetry'].get('altitude',0)>35,60,'起飞')
        event('patrolling','执行园区巡检')
        require_ack('auto')
        wait_until(lambda s:s['telemetry'].get('waypoint',0)>=3,120,'进入园区')
        require_ack('loiter')
        s=wait_until(lambda s:s['telemetry'].get('mode')=='LOITER' and s['telemetry'].get('speed',99)<1,30,'悬停核验')
        alert=create_alert(s)
        event('review','演示预警已注入，飞机悬停等待核验','awaiting_resolution')
        if JOB['auto_resolve']:
            if CANCEL.wait(8):raise RuntimeError('演示已中止')
            resolve_alert(JOB['id'],alert['id'],Resolution(actor='仿真演示脚本',note='已核验为演示注入目标，继续巡检'),True)
        wait_until(lambda s:JOB['alerts'][0]['status']=='resolved',300,'预警核验')
        event('resuming','处置已记录，恢复巡检')
        require_ack('auto')
        wait_until(lambda s:s.get('patrol',{}).get('status')=='completed',300,'巡检落地')
        finish_result()
        event('completed','园区巡检完成，已落地上锁；预警已处置，可回放','completed')
    except Exception as exc:
        finish_result()
        event('interrupted' if CANCEL.is_set() else 'failed',str(exc),'interrupted' if CANCEL.is_set() else 'failed')
        with LOCK:JOB['error']=str(exc);persist()
    finally:
        with sitl.LOCK:sitl.STATE.pop('demo_owner',None)


@router.post('/start')
def start(body:DemoRequest):
    global JOB,WORKER
    if sitl.STATE.get('execution_binding') and sitl.STATE.get('patrol',{}).get('status') not in ('completed','cancelled'):raise HTTPException(409,'SITL 已绑定业务任务，请从该任务继续执行')
    with LOCK:
        old=current()
        if old and old['status'] in ACTIVE:
            if old.get('area_id','west')!=body.area_id or old.get('aircraft_id','SITL-1')!=body.aircraft_id:
                raise HTTPException(409,'飞机正在执行其他区域任务，请先完成或中止')
            return status()
        if WORKER is not None and WORKER.is_alive():raise HTTPException(409,'上一轮演示正在结束，请稍后重试')
        s=sitl.status()
        if s['telemetry'].get('armed'):raise HTTPException(409,'SITL 已解锁，请先落地上锁')
        if s['running'] and not s['connected']:raise HTTPException(409,'SITL 进程在运行但遥测不通，请先恢复连接')
        JOB=dict(id=uuid.uuid4().hex,source='ardupilot-sitl',status='running',phase='starting',auto_resolve=body.auto_resolve,
                 area_id=body.area_id,aircraft_id=body.aircraft_id,area_name=patrol.AREAS[body.area_id]['name'],created_at=time.time(),events=[],alerts=[],receipts=[],result=None)
        CANCEL.clear()
        with sitl.LOCK:
            sitl.STATE['demo_owner']=JOB['id'];sitl.STATE['track']=[]
        persist()
        WORKER=threading.Thread(target=run,daemon=True,name='sitl-demo')
        WORKER.start()
        return status()


@router.post('/cancel')
def cancel():
    with LOCK:
        job=current()
        if not job or job['status'] not in ACTIVE:return status()
        CANCEL.set()
    # Serialize with an in-flight command; acknowledgement is required for the claim of hover.
    s=sitl.status()
    receipt=None
    if s['connected'] and s['telemetry'].get('armed'):
        receipt=sitl.execute_command(sitl.Command(command_id=uuid.uuid4().hex,command='loiter',expires_at=time.time()+20))
    return {'cancel_requested':True,'hover_receipt':receipt}


@router.get('/{run_id}/result')
def result(run_id:str):
    with LOCK:
        job=current()
        if job and job['id']==run_id:
            data=copy.deepcopy(job)
        else:
            with business.connect() as db:data=business.get(db,'sitl-demo:'+run_id)
        if not data:raise HTTPException(404,'演示记录不存在')
    if data.get('result') is None:
        data['result']=sitl.patrol_result() if data['status'] in ACTIVE and sitl.STATE.get('demo_owner')==run_id else {'track':[], 'video_evidence':None}
    return data
