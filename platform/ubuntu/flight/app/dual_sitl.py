"""Paired local processes only. One coordinator owns both targets and the status queue."""
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from . import aoi_joint, business, disposition, incidents, route_plan, safety, sim, station

router = APIRouter(prefix='/api/dual-sitl')
lock = threading.Lock()
homes = [[8., 8., 0.], [8., 88., 0.]]
heights = 40.


def directory():
    path = Path(os.environ.get('FLIGHT_RECORDS_DIR',Path(__file__).resolve().parents[1]/'data'))/'dual'
    path.mkdir(parents=True,exist_ok=True)
    return path


def save(record):
    path=directory()/(record['id']+'.json')
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(record,ensure_ascii=False,allow_nan=False))
    tmp.replace(path)


def plane(channel, index, anchor):
    from . import sitl
    from pymavlink import mavutil
    try:
        home=station.geo(homes[index],anchor)
        sitl.start_local(21+index,6031+index,dict(anchor,latitude=home[0],longitude=home[1]))
        channel.send(sitl.PROCESS.pid)
        while True:
            op,arg=channel.recv()
            if op=='close':
                break
            if op=='read':
                with sitl.LOCK:
                    t=dict(sitl.STATE['telemetry'])
                    stamp=sitl.STATE['updated_at'].get('latitude',0)
                if 'latitude' in t:
                    t['xyz']=[math.radians(t['longitude']-anchor['longitude'])*station.EARTH_RADIUS*math.cos(math.radians(anchor['latitude'])),
                              math.radians(t['latitude']-anchor['latitude'])*station.EARTH_RADIUS,t['altitude']]
                channel.send(dict(t,at=stamp))
            elif op=='target':
                lat,lon,alt=station.geo(arg,anchor)
                with sitl.WIRE:
                    sitl.LINK.mav.set_position_target_global_int_send(0,1,1,mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                        3576,round(lat*1e7),round(lon*1e7),alt,0,0,0,0,0,0,0,0)
                channel.send('sent')
            else:
                receipt=sitl.command(sitl.Command(command=op,altitude=heights,command_id=uuid.uuid4().hex,expires_at=time.time()+15))
                channel.send(receipt['status'])
    except (EOFError,BrokenPipeError):
        pass
    except Exception as exc:
        channel.send({'error':str(exc)})
    finally:
        sitl.stop()
        channel.close()


def validate(starts, ends, buildings, temporary=()):
    with business.connect() as db:
        space=incidents.airspace(db)
    boxes=disposition.obstacles(buildings,[*space['zones'],*temporary])
    contacts=[]
    reasons=[]
    for a,b in zip(starts,ends):
        for box in boxes:
            hit=disposition.intersection_point(a,b,box)
            if hit is not None:
                contacts.append(dict(id=box['id'],position=hit))
        if not disposition.clear(a,b,boxes):
            reasons.append('禁飞区或建筑')
        for p in (a,b):
            if any(not sim.FENCE[k+'0']<=p[i]<=sim.FENCE[k+'1'] for i,k in enumerate('xyz')):
                reasons.append('超出围栏')
    # Conservative bound even if the vehicles traverse their segments asynchronously.
    gap=max(min(starts[1][1],ends[1][1])-max(starts[0][1],ends[0][1]),0)
    cpa=safety.closest_approach(starts[0],[b-a for a,b in zip(starts[0],ends[0])],
                                starts[1],[b-a for a,b in zip(starts[1],ends[1])],1)
    if min(gap,cpa['separation_m'])<50:
        reasons.append('双机间距不足 50 m')
    if '双机间距不足 50 m' in reasons:
        t=cpa['seconds_to_closest']
        positions=[[a+t*(b-a) for a,b in zip(start,end)] for start,end in zip(starts,ends)]
        contacts.append(dict(id='pair',position=[(a+b)/2 for a,b in zip(*positions)]))
    return dict(passed=not reasons,reasons=reasons,contacts=contacts,revision=space['revision'],zones=len(space['zones'])+len(temporary),min_lane_gap_m=gap,**cpa)


def run(record):
    workers=[]
    pipes=[]
    def receive(pipe,timeout=10):
        if not pipe.poll(timeout):
            raise RuntimeError('仿真飞控响应超时')
        result=pipe.recv()
        if isinstance(result,dict) and 'error' in result:
            raise RuntimeError(result['error'])
        return result
    def both(op,args=(None,None)):
        for pipe,arg in zip(pipes,args):
            pipe.send((op,arg))
        return [receive(pipe) for pipe in pipes]
    def until(predicate,timeout):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            pair=both('read')
            if predicate(pair):
                return pair
            time.sleep(.2)
        raise RuntimeError('遥测未达到预期状态')
    try:
        import torch
        model=aoi_joint.policy()
        queue=aoi_joint.JointQueue(1701)
        record['model_sha256']=hashlib.sha256(aoi_joint.MODEL.read_bytes()).hexdigest()
        buildings=route_plan.scene_record('urban')['buildings']
        ctx=multiprocessing.get_context('spawn')
        for i in range(2):
            local,remote=ctx.Pipe()
            worker=ctx.Process(target=plane,args=(remote,i,record['anchor']))
            worker.start()
            remote.close()
            pipes.append(local)
            workers.append(worker)
        record['pids']=[receive(pipe,60) for pipe in pipes]
        ready=until(lambda pair:all(t.get('gps_fix',0)>=3 and t.get('landed_state')==1 for t in pair),60)
        climb=validate([[*t['xyz'][:2],0] for t in ready],[[*h[:2],heights] for h in homes],buildings)
        if not climb['passed']:
            raise RuntimeError(str(climb))
        if both('guided')!=['acknowledged']*2:
            raise RuntimeError('引导模式被拒绝')
        for pipe in pipes:
            for _ in range(25):
                pipe.send(('arm',None))
                if receive(pipe)=='acknowledged':
                    break
                time.sleep(1)
            else:
                raise RuntimeError('解锁被拒绝')
        if both('takeoff')!=['acknowledged']*2:
            raise RuntimeError('起飞被拒绝')
        until(lambda pair:all(t['altitude']>38 for t in pair),60)
        record['status']='flying'
        pending=None
        zones=[]
        for slot in range(1,19):
            pair=both('read')
            now=time.time()
            if any(now-t['at']>1 or not t['armed'] or t['mode']!=('LOITER' if pending else 'GUIDED') or t['gps_fix']<3 for t in pair):
                raise RuntimeError('双机遥测或飞行模式异常')
            starts=[t['xyz'] for t in pair]
            queue.x[:]=[min(72,max(0,p[0]-8)) for p in starts]
            obs=queue.observation()
            with torch.no_grad():
                action=model(torch.from_numpy(obs[None])).numpy()[0]
            event=queue.move_and_schedule(action,measured=True)
            targets=[[8+x,h[1],heights] for x,h in zip(event['next_x'],homes)]
            injection=None
            if record.get('exercise') and slot==5:
                injection='临时禁飞区'
                zones=[dict(id='TEMP-DUAL',x=starts[0][0]+28,y=starts[0][1]-2,w=3,d=4,z_min=30,z_max=50)]
                targets[0][0]=starts[0][0]+14
            if slot==9:
                zones=[]
            if record.get('exercise') and slot==12:
                injection='航线交会'
                midpoint=[(a+b)/2 for a,b in zip(*starts)]
                targets=[midpoint[:],midpoint[:]]
            if pending:
                # Try a short adjacent parallel segment, then a shorter advance.
                for advance,lateral in [(3,3),(1,2),(-2,0)]:
                    candidate=[[p[0]+advance,p[1]+lateral,heights] for p in starts]
                    if validate(starts,candidate,buildings,zones)['passed']:
                        targets=candidate
                        break
                else:
                    raise RuntimeError('无通过检查的替代航段，保持悬停')
            check=validate(starts,targets,buildings,zones)
            frame=dict(at=now,telemetry=pair,targets=targets,safety=check,action=action.tolist(),observation=obs.tolist(),**event)
            frame.update(injection=injection,temporary_zones=zones,phase='巡检')
            record['frames'].append(frame)
            if not check['passed']:
                frame['dispatch']='blocked'
                frame['phase']='拦截并悬停'
                frame['hold_ack']=both('loiter')
                if frame['hold_ack']!=['acknowledged']*2:
                    raise RuntimeError('悬停指令未确认')
                held=until(lambda ts:all(t['mode']=='LOITER' and t['speed']<.5 and t['armed'] and time.time()-t['at']<1 for t in ts),15)
                frame['hold_telemetry']=held
                frame['after']=[t['xyz'] for t in held]
                pending=frame
                save(record)
                continue
            if pending:
                frame.update(phase='重规划并恢复',blocked_slot=pending['slot'])
                if both('guided')!=['acknowledged']*2:
                    raise RuntimeError('恢复引导模式失败')
                pending=None
            frame['dispatch']=both('target',targets)
            time.sleep(1)
            frame['after']=[t['xyz'] for t in both('read')]
            save(record)
        if both('land')!=['acknowledged']*2:
            raise RuntimeError('降落被拒绝')
        record['landed']=until(lambda pair:all(not t['armed'] and t['landed_state']==1 for t in pair),90)
        record['status']='completed'
    except Exception as exc:
        record.update(status='failed',error=str(exc))
    finally:
        for p in pipes:
            try:
                p.send(('close',None))
            except (EOFError,OSError):
                pass
        for worker in workers:
            worker.join(15)
        for p in pipes:
            p.close()
        save(record)
        lock.release()


@router.post('',status_code=202)
def create(exercise:bool=False):
    if not lock.acquire(blocking=False):
        raise HTTPException(409,'双机仿真正在运行')
    record=dict(id='DUAL-'+uuid.uuid4().hex[:12],status='starting',exercise=exercise,created=time.time(),anchor=station.config(),frames=[])
    try:
        save(record)
        threading.Thread(target=run,args=(record,),daemon=True).start()
    except Exception:
        lock.release()
        raise
    return {'id':record['id']}


@router.get('')
def history():
    return sorted((p.stem for p in directory().glob('DUAL-*.json')),key=lambda ident:(directory()/(ident+'.json')).stat().st_mtime,reverse=True)


@router.get('/{ident}')
def read(ident:str):
    import re
    if not re.fullmatch(r'DUAL-[0-9a-f]{12}',ident) or not (directory()/(ident+'.json')).is_file():
        raise HTTPException(404,'双机任务不存在')
    return json.loads((directory()/(ident+'.json')).read_text())
