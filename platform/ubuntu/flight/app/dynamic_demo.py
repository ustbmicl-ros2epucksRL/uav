"""Isolated deterministic demonstrations using the production simulation guard."""
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from . import business

router = APIRouter(prefix='/api/business/dynamic-demos')


class Request(BaseModel):
    scenario: Literal['traffic', 'airspace', 'encounter']


@router.post('')
def create(body: Request):
    # A separate process and database protect live missions, clock and observations.
    with tempfile.TemporaryDirectory(prefix='dynamic-demo-') as folder:
        env = dict(os.environ, FLIGHT_DATA_DIR=folder)
        try:
            result = subprocess.run([sys.executable, '-m', 'app.dynamic_demo', body.scenario],
                                    cwd=Path(__file__).resolve().parents[1], env=env,
                                    capture_output=True, text=True, timeout=45, check=True)
            report = json.loads(result.stdout)
        except (subprocess.SubprocessError, ValueError):
            raise HTTPException(503, '独立仿真未完成，请重试')
    with business.connect() as db:
        business.put(db, 'dynamic-demo:latest', report)
    return report


@router.get('/latest')
def latest():
    with business.connect() as db:
        result = business.get(db, 'dynamic-demo:latest')
    if not result:
        raise HTTPException(404, '尚无演示记录')
    return result


def run(scenario):
    from . import sim, dynamic, disposition, incidents, follow, route_plan
    # Deliberately empty synthetic building fixture isolates traffic/airspace effects.
    route_plan.scene_record = lambda _: dict(buildings=[])
    follow.public = lambda: dict(estimate=None, source='demo-no-observations')
    sim.reset()
    sim.get('UAV-02').position = [1900, 1900, 0]
    def managed(ident, start, goal):
        v = sim.get(ident)
        v.position=list(start);v.home=list(goal);v.armed=True;v.mode='RTL'
        v.disposition_route=[list(goal)];v.disposition_wp=0;v.disposition_reason='rtl'
        v.disposition_scene='urban';v.disposition_incident='DEMO-'+ident
        v.dynamic_guard=dict(state='pending',history=[],generation=0)
        return v
    a=managed('UAV-01',[800,200,100],[200,200,0])
    controlled=[a]
    if scenario=='encounter':
        controlled.append(managed('UAV-02',[200,200,100],[800,200,0]))
    with business.connect() as db:
        space=incidents.airspace(db)
    initial={v.id:[list(v.position)]+copy.deepcopy(v.disposition_route) for v in controlled}
    frames=[];events=[];violations=[]
    def frame(second, event=None):
        frames.append(dict(second=second,event=event,airspace=copy.deepcopy(space),vehicles=[sim.snapshot(v) for v in sim.VEHICLES.values()]))
        if event:events.append(dict(second=second,label=event))
    frame(0,'开始独立仿真')
    for second in range(1,121):
        event=None
        if scenario=='traffic' and second==3:
            v=sim.Vehicle('SIM-DEMO','新增演练机','simulation',[500,200,50],'报文终端')
            v.position=[500,200,50];v.armed=True;v.mode='LOITER';sim.VEHICLES[v.id]=v
            event='新增飞行器进入原处置路线'
        if scenario=='airspace' and second in (3,6):
            space['revision']+=1
            space['zones']=[dict(id='DEMO-COVER' if second==3 else 'DEMO-BARRIER',
                                x=700 if second==3 else 450,y=100,w=150 if second==3 else 80,
                                d=200,z_min=0,z_max=300)]
            with business.connect() as db:business.put(db,'airspace',space)
            event='禁飞区覆盖当前位置，暂停处置' if second==3 else '空域调整，重新规划恢复'
        old={v.id:list(v.position) for v in sim.VEHICLES.values()}
        sim.tick_steps(1)
        for v in controlled:
            for other in sim.VEHICLES.values():
                if other.id==v.id:continue
                if dynamic.conflicts([old[v.id],v.position],math.dist(old[v.id],v.position) or 1,
                                     [dict(id=other.id,points=[old[other.id],other.position],radius=50)],horizon=1):
                    violations.append(dict(second=second,kind='traffic',vehicle=v.id))
            # Current-location coverage is an explicit hold scenario, not a safe state.
            if old[v.id]!=v.position and any(disposition.intersects(old[v.id],v.position,b) for b in disposition.obstacles([],space['zones'])):
                violations.append(dict(second=second,kind='no_fly',vehicle=v.id))
        if event is None:
            changes=[v.id+' '+dict(executing='执行中',replanned='重新规划',holding='暂停',completed='已完成').get(v.dynamic_guard['state'],v.dynamic_guard['state']) for v in controlled if v.dynamic_guard['state']!=((frames[-1]['vehicles'][next(i for i,o in enumerate(frames[-1]['vehicles']) if o['id']==v.id)].get('dynamic_guard') or {}).get('state'))]
            event=' / '.join(changes) or None
        frame(second,event)
        if all(v.done for v in controlled):break
    return dict(scenario=scenario,source='isolated-simulation',anchor=space.get('anchor'),
                fixture='无建筑合成场景；复用动态避障算法，不影响实时业务',
                original_routes=initial,frames=frames,events=events,
                result=dict(completed=all(v.done for v in controlled),violations=violations,
                            replans=sum(v.dynamic_guard.get('generation',0) for v in controlled)))


if __name__=='__main__':
    print(json.dumps(run(sys.argv[1])))
