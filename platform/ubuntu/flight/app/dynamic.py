"""Deterministic simulation guard. Only telemetry and public filtered tracks are inputs."""
import copy
import math
import time
from . import sim, disposition as static, route_plan, business, follow

HORIZON = 20
SEPARATION = 50.0


def project(position, route, speed, horizon=HORIZON):
    """Match the simulator: no leftover time after reaching a waypoint in a tick."""
    result=[list(position)];index=0
    for _ in range(horizon):
        if index>=len(route):result.append(list(result[-1]));continue
        point,arrived=sim._move(result[-1],route[index],speed,1.0)
        result.append(point)
        if arrived:index+=1
    return result


def future(v, moving_ids=None):
    if moving_ids is not None and v.id not in moving_ids:
        return project(v.position,[],v.speed)
    if not v.armed or v.done:return project(v.position,[],v.speed)
    if v.mode in {'RTL','LAND'} and getattr(v,'disposition_route',None):
        route=v.disposition_route[v.disposition_wp:]
    elif v.mode=='AUTO':route=v.mission[v.wp:]
    elif v.mode=='RTL':route=[v.home]
    elif v.mode=='LAND':route=[[*v.position[:2],0]]
    else:route=[]
    return project(v.position,route,v.speed)


def traffic(v, moving_ids=None):
    result=[dict(id=o.id,points=future(o,moving_ids),radius=SEPARATION,source='simulation-telemetry')
            for o in sim.VEHICLES.values() if o.id!=v.id]
    observed=follow.public()
    if observed.get('estimate'):
        pos=observed['estimate'];vel=observed.get('velocity') or [0,0,0]
        uncertainty=max(0,observed.get('uncertainty_m') or 0)
        result.append(dict(id='filtered-target',points=[[pos[i]+vel[i]*t for i in range(3)] for t in range(HORIZON+1)],
                           radius=SEPARATION+uncertainty,source='filtered-observation'))
    return result


def conflicts(points, speed, tracks, horizon=HORIZON):
    own=project(points[0],points[1:],speed,horizon)
    hits=[]
    for target in tracks:
        minimum=float('inf');at=0;own_point=None;target_point=None
        for t in range(horizon):
            delta=[target['points'][t][i]-own[t][i] for i in range(3)]
            velocity=[target['points'][t+1][i]-target['points'][t][i]-own[t+1][i]+own[t][i] for i in range(3)]
            norm=sum(x*x for x in velocity)
            fraction=max(0,min(1,-sum(delta[i]*velocity[i] for i in range(3))/norm)) if norm else 0
            distance=math.sqrt(sum((delta[i]+fraction*velocity[i])**2 for i in range(3)))
            if distance<minimum:
                minimum=distance;at=t+fraction
                own_point=[own[t][i]+fraction*(own[t+1][i]-own[t][i]) for i in range(3)]
                target_point=[target['points'][t][i]+fraction*(target['points'][t+1][i]-target['points'][t][i]) for i in range(3)]
        if minimum<target['radius']:
            hits.append(dict(target=target['id'],separation_m=round(minimum,2),threshold_m=target['radius'],seconds=round(at,2),position=own_point,target_position=target_point))
    return hits


def swept_boxes(tracks):
    boxes=[]
    for target in tracks:
        points=target['points'];r=target['radius']+5
        boxes.append(dict(id=target['id'],kind='predicted_traffic',lower=[min(p[i] for p in points)-r for i in range(3)],
                          upper=[max(p[i] for p in points)+r for i in range(3)]))
    return boxes


def record(v, state, reasons, revision, route=None):
    previous=getattr(v,'dynamic_guard',{})
    history=list(previous.get('history',[]))
    generation=previous.get('generation',0)+(1 if route is not None else 0)
    changed=state!=previous.get('state') or route is not None or revision!=previous.get('airspace_revision') or reasons!=previous.get('reasons')
    if changed:
        history.append(dict(anchor=copy.deepcopy(getattr(v,'dynamic_anchor',None)),position=list(v.position),original_route=copy.deepcopy(getattr(v,'dynamic_previous_route',None)),zones=copy.deepcopy(getattr(v,'dynamic_zones',[])),time=time.time(),flight_second=v.seconds,state=state,reasons=reasons,
                            airspace_revision=revision,generation=generation,route=copy.deepcopy(route)))
    v.dynamic_guard=dict(state=state,reasons=reasons,airspace_revision=revision,generation=generation,
                         checked_at=time.time(),horizon_seconds=HORIZON,separation_m=SEPARATION,
                         history=history[-100:],source='simulation')


def hold(v, reasons, revision, tracks):
    v.mode='LOITER'
    risk=conflicts([v.position,v.position],v.speed,tracks)
    record(v,'holding',reasons,revision)
    v.dynamic_guard['hold_conflicts']=risk
    v.dynamic_guard['hold_safe']=not risk


def verify(v, space, moving_ids):
    scene=getattr(v,'disposition_scene','urban')
    buildings=route_plan.scene_record(scene)['buildings'];boxes=static.obstacles(buildings,space['zones'])
    remaining=[list(v.position)]+v.disposition_route[v.disposition_wp:]
    v.dynamic_previous_route=copy.deepcopy(remaining)
    v.dynamic_zones=copy.deepcopy(space['zones'])
    v.dynamic_anchor=copy.deepcopy(space['anchor'])
    tracks=traffic(v,moving_ids)
    checked=static.check(remaining,boxes,sim.FENCE,v.speed,v.battery)
    collisions=conflicts(remaining,v.speed,tracks)
    revision=space['revision']
    if checked['passed'] and not collisions:
        was_holding=getattr(v,'dynamic_guard',{}).get('state')=='holding'
        v.mode='RTL' if v.disposition_reason=='rtl' else 'LAND'
        record(v,'executing',[],revision,remaining if was_holding else None)
        return
    reasons=checked['reasons']+[dict(kind='traffic',**c) for c in collisions]
    # Plans keep their accepted landing endpoint; alternate configured sites are permitted
    # only if the original destination itself becomes blocked.
    if any(math.dist(v.position,t['points'][0])<t['radius'] for t in tracks):
        hold(v,reasons,revision,tracks)
        v.dynamic_guard['hold_static_clear']=not any(static.intersects(v.position,v.position,box) for box in boxes)
        v.dynamic_guard['hold_safe']=v.dynamic_guard['hold_safe'] and v.dynamic_guard['hold_static_clear']
        return
    goal=list(v.disposition_route[-1])
    for extra in [[],swept_boxes(tracks)]:
        report=static.propose(v,v.disposition_reason,buildings,space,sim.FENCE,
                             force_alternatives=True,goal_override=goal,extra_boxes=extra)
        for choice in report['choices']:
            if conflicts(choice['route'],v.speed,tracks):continue
            if not static.check(choice['route'],boxes,sim.FENCE,v.speed,v.battery)['passed']:continue
            v.disposition_route=copy.deepcopy(choice['route'][1:]);v.disposition_wp=0
            v.mode='RTL' if v.disposition_reason=='rtl' else 'LAND'
            record(v,'replanned',reasons,revision,choice['route'])
            return
    hold(v,reasons,revision,tracks)
    v.dynamic_guard['hold_static_clear']=not any(static.intersects(v.position,v.position,box) for box in boxes)
    v.dynamic_guard['hold_safe']=v.dynamic_guard['hold_safe'] and v.dynamic_guard['hold_static_clear']


def prepare(chosen=None):
    """Called under sim.LOCK, before any aircraft moves in a simulation second."""
    chosen=list(chosen) if chosen is not None else list(sim.VEHICLES.values())
    moving_ids={v.id for v in chosen}
    managed=sorted([v for v in chosen if getattr(v,'disposition_incident',None) and
                    getattr(v,'disposition_route',None) and v.armed and not v.done],key=lambda v:v.id)
    if not managed:return
    try:
        from . import incidents
        with business.connect() as db:space=incidents.airspace(db)
        for v in managed:verify(v,space,moving_ids)
        # A later priority change may invalidate an earlier prediction. Freeze implicated
        # managed aircraft until no new freezes occur; never advance an unsafe first step.
        for _ in range(len(managed)+1):
            changed=False
            for v in managed:
                if v.mode not in {'RTL','LAND'}:continue
                points=[v.position]+v.disposition_route[v.disposition_wp:]
                risk=conflicts(points,v.speed,traffic(v,moving_ids),horizon=1)
                if risk:
                    hold(v,[dict(kind='next_step_conflict',**c) for c in risk],space['revision'],traffic(v,moving_ids));changed=True
            if not changed:break
        for v in managed:
            if v.mode=='LOITER':
                v.dynamic_guard['hold_conflicts']=conflicts([v.position,v.position],v.speed,traffic(v,moving_ids))
                v.dynamic_guard['hold_safe']=not v.dynamic_guard['hold_conflicts'] and v.dynamic_guard.get('hold_static_clear',True)
        with business.connect() as db:
            for v in managed:
                item=business.get(db,'incident:'+v.disposition_incident)
                if item and item.get('action'):
                    item['action']['dynamic']=copy.deepcopy(v.dynamic_guard)
                    item['action']['status']='holding' if v.mode=='LOITER' else 'executing'
                    item['action']['current_route']=copy.deepcopy(v.disposition_route[v.disposition_wp:])
                    business.put(db,'incident:'+item['id'],item)
    except Exception:
        # Failed safety data/storage must not allow an unverified motion step.
        for v in managed:
            v.mode='LOITER'
            record(v,'holding',[dict(kind='guard_unavailable')],None)
            v.dynamic_guard.update(hold_safe=False,hold_conflicts=[],error='动态复核不可用，处置暂停；请检查服务')
