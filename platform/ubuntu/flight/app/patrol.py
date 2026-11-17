"""Assigned park patrol routes for the local simulator; no camera detection claims."""
import copy
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import sim, business, incidents, disposition, route_plan, station

router=APIRouter(prefix='/api/park-patrol')
AREAS={
    'west':dict(id='west',name='西侧园区',bounds=[200,200,700,700]),
    'east':dict(id='east',name='东侧园区',bounds=[900,400,1400,900])}


class Assignment(BaseModel):
    area_id:str
    altitude:float=Field(default=180,ge=40,le=280,allow_inf_nan=False)


@router.get('')
def status():
    with sim.LOCK, business.connect() as db:
        result=[]
        for v in sim.VEHICLES.values():
            item=business.get(db,'patrol:'+v.id)
            if not item:continue
            same=item['boot']==business.BOOT and item['route']==v.mission
            state=item.get('terminal_state','unknown') if not same else 'completed' if v.done and v.done_reason=='mission' else 'interrupted' if v.done else 'patrolling' if v.mode=='AUTO' else 'paused' if v.armed else 'ready'
            result.append(dict(item,state=state,waypoint=v.wp if same else None,total=len(item['route']),position=list(v.position) if same else None))
        return dict(source='simulation',areas=list(AREAS.values()),assignments=result,anchor=station.config())


@router.post('/{vehicle_id}/assign')
def assign(vehicle_id:str,body:Assignment):
    with sim.LOCK:
        v=sim.get(vehicle_id)
        if not v:raise HTTPException(404,'飞机不存在')
        if body.area_id not in AREAS:raise HTTPException(422,'请选择已配置园区')
        if v.payload!='光电吊舱':raise HTTPException(409,'该飞机未配置光电吊舱，不能执行园区监控')
        if v.armed or v.position[2]>1:raise HTTPException(409,'请先落地并上锁后配置监控任务')
        try:business.require_unowned(v)
        except ValueError as e:raise HTTPException(409,str(e))
        x,y,x1,y1=AREAS[body.area_id]['bounds'];h=body.altitude
        route=[list(v.position),[*v.position[:2],h],[x,y,h],[x1,y,h],[x1,y1,h],[x,y1,h],[x,y,h],[*v.home[:2],h],list(v.home)]
        with business.connect() as db:space=incidents.airspace(db)
        original_route=copy.deepcopy(route)
        from .sitl import safe_patrol_route
        route,check=safe_patrol_route(route,disposition.obstacles(route_plan.scene_record('urban')['buildings'],space['zones']),sim.FENCE,v.battery)
        if not check['passed']:raise HTTPException(409,dict(message='园区航线未通过检查，请调整高度或空域配置',check=check))
        try:sim.load_mission(v,route)
        except ValueError as e:raise HTTPException(409,str(e))
        item=dict(id=uuid.uuid4().hex[:12],frames=[],alerts=[],observations=[],vehicle_id=v.id,area=copy.deepcopy(AREAS[body.area_id]),route=route,original_route=original_route,rerouted=route!=original_route,anchor=station.config(),boot=business.BOOT,check=check,source='simulation')
        with business.connect() as db:
            old=business.get(db,'patrol:'+v.id)
            if old:business.put(db,'patrol-history:'+old.get('id',v.id),old)
            business.put(db,'patrol:'+v.id,item)
        station.ack(v.id,'patrol','园区监控航线已装订，等待解锁与自动执行')
        return item


def capture(vehicles):
    """Synthetic observation fixture, not an image detector or tracking input."""
    with business.connect() as db:
        for v in vehicles:
            item=business.get(db,'patrol:'+v.id)
            if not item or item['boot']!=business.BOOT or item['route']!=v.mission or item.get('terminal_state'):continue
            frames=item.setdefault('frames',[])
            if not v.armed and not v.done:continue
            if frames and frames[-1]['second']==v.seconds:continue
            view=sim.view_of(v)
            frame=dict(second=v.seconds,position=list(v.position),mode=v.mode,waypoint=v.wp,
                       footprint=view.get('footprint',[]),source='synthetic-overhead')
            target=item.get('target')
            if target and v.position[2]>=40 and ((v.position[0]-target[0])**2+(v.position[1]-target[1])**2)**.5<120:
                frame['target']=target
                if not item['alerts']:
                    item['alerts'].append(dict(id='ALERT-'+item['id'],kind='simulated_intrusion',status='open',second=v.seconds,position=target,source='scripted-proximity-trigger',evidence=copy.deepcopy(frame)))
                item['observations'].append(dict(second=v.seconds,position=target))
            frames.append(frame);item['frames']=frames[-600:]
            if v.done:item['terminal_state']='completed' if v.done_reason=='mission' else 'interrupted'
            business.put(db,'patrol:'+v.id,item)


class Control(BaseModel):
    command:str


@router.post('/{vehicle_id}/control')
def control(vehicle_id:str,body:Control):
    with sim.LOCK, business.connect() as db:
        v=sim.get(vehicle_id);item=business.get(db,'patrol:'+vehicle_id)
        if not v or not item or item['boot']!=business.BOOT or item['route']!=v.mission:
            raise HTTPException(409,'没有匹配的当前园区任务')
        if body.command not in {'start','pause','resume'}:raise HTTPException(422,'不支持的控制')
        if v.done:raise HTTPException(409,'任务已结束，请重新装订')
        try:
            business.require_unowned(v)
            if body.command=='pause':
                if not v.armed:raise ValueError('飞机尚未解锁')
                sim.command(v,'loiter')
            else:
                space=incidents.airspace(db)
                points=[list(v.position)]+v.mission[v.wp:]
                checked=disposition.check(points,disposition.obstacles(route_plan.scene_record('urban')['buildings'],space['zones']),sim.FENCE,v.speed,v.battery)
                if not checked['passed']:raise HTTPException(409,dict(message='当前航线复核未通过',check=checked))
                if body.command=='start' and not v.armed:sim.command(v,'arm')
                if body.command=='resume' and not v.armed:raise ValueError('飞机尚未解锁')
                sim.command(v,'auto');station.CLOCK['running']=True
        except ValueError as exc:raise HTTPException(409,str(exc))
        item.setdefault('actions',[]).append(dict(second=v.seconds,command=body.command))
        business.put(db,'patrol:'+v.id,item)
        return sim.snapshot(v)


@router.post('/{vehicle_id}/simulate-target')
def target(vehicle_id:str):
    with sim.LOCK, business.connect() as db:
        v=sim.get(vehicle_id);item=business.get(db,'patrol:'+vehicle_id)
        if not v or not item or item['boot']!=business.BOOT or item['route']!=v.mission or v.done:
            raise HTTPException(409,'请先装订当前监控任务')
        if item.get('target'):return item
        x,y,_,_=item['area']['bounds'];item['target']=[x+40,y+40,0]
        business.put(db,'patrol:'+vehicle_id,item)
        return item


class Resolution(BaseModel):
    task_id:str | None = None
    alert_id:str | None = None
    actor:str=Field(min_length=1,max_length=40)
    note:str=Field(min_length=1,max_length=200)


@router.post('/{vehicle_id}/resolve')
def resolve(vehicle_id:str,body:Resolution):
    if not body.actor.strip() or not body.note.strip():raise HTTPException(422,'请填写处置人及说明')
    with sim.LOCK, business.connect() as db:
        item=business.get(db,'patrol:'+vehicle_id)
        if not item or not item.get('alerts'):raise HTTPException(409,'尚无观测预警')
        alert=item['alerts'][0]
        if (body.task_id and body.task_id!=item['id']) or (body.alert_id and body.alert_id!=alert['id']):
            raise HTTPException(409,'预警或任务已变化，请刷新后重新选择')
        if alert['status']=='open':alert.update(status='resolved',actor=body.actor,note=body.note)
        business.put(db,'patrol:'+vehicle_id,item)
        return item


@router.get('/{vehicle_id}/result')
def result(vehicle_id:str):
    with business.connect() as db:item=business.get(db,'patrol:'+vehicle_id)
    if not item:raise HTTPException(404,'尚无园区任务记录')
    return dict(item,monitoring_complete=item.get('terminal_state')=='completed' and all(a['status']=='resolved' for a in item.get('alerts',[])),
                note='监控画面为局部俯视合成示意，异常来自显式注入及距离规则；非真实视频识别')
