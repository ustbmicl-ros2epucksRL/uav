"""Static ENU simulation route checks. No surveyed-data or operational certification."""
import heapq
import math
from . import route_plan

HORIZONTAL = 20.0
VERTICAL = 10.0
CELL = 25.0


def default_airspace():
    return dict(revision=0, source='simulation', zones=[], landing_sites=[
        dict(id='SITE-01', name='仿真备用点一', x=200.0, y=200.0),
        dict(id='SITE-02', name='仿真备用点二', x=900.0, y=400.0)])


def obstacles(buildings, zones):
    result=[]
    for index,b in enumerate(buildings):
        result.append(dict(id=str(b.get('id',f'building-{index}')), kind='building',
            lower=[b['x']-HORIZONTAL,b['y']-HORIZONTAL,0],
            upper=[b['x']+b['w']+HORIZONTAL,b['y']+b['d']+HORIZONTAL,b['h']+VERTICAL]))
    for z in zones:
        result.append(dict(id=z['id'],kind='no_fly',lower=[z['x']-HORIZONTAL,z['y']-HORIZONTAL,max(0,z['z_min']-VERTICAL)],
            upper=[z['x']+z['w']+HORIZONTAL,z['y']+z['d']+HORIZONTAL,z['z_max']+VERTICAL]))
    return result


def intersection_point(a, b, box):
    """First contact with a closed clearance prism, including grazing."""
    enter, leave = 0.0, 1.0
    for i in range(3):
        delta = b[i] - a[i]
        if abs(delta) < 1e-12:
            if not box['lower'][i] <= a[i] <= box['upper'][i]:
                return None
        else:
            lo = (box['lower'][i] - a[i]) / delta
            hi = (box['upper'][i] - a[i]) / delta
            enter = max(enter, min(lo, hi))
            leave = min(leave, max(lo, hi))
            if enter > leave:
                return None
    return [a[i] + enter * (b[i] - a[i]) for i in range(3)]


def intersects(a, b, box):
    return intersection_point(a, b, box) is not None


def check(points, boxes, fence, speed=25, battery=100):
    reasons=[]
    if len(points)<2 or any(len(p)!=3 or any(not math.isfinite(x) for x in p) for p in points):
        return dict(passed=False,reasons=[dict(kind='invalid_route')],distance_m=0,estimated_seconds=0)
    if any(not(fence['x0']<=p[0]<=fence['x1'] and fence['y0']<=p[1]<=fence['y1'] and fence['z0']<=p[2]<=fence['z1']) for p in points):
        reasons.append(dict(kind='fence'))
    for n,(a,b) in enumerate(zip(points,points[1:])):
        for box in boxes:
            point = intersection_point(a, b, box)
            if point is not None:
                reasons.append(dict(kind=box['kind'],obstacle_id=box['id'],segment=n+1,position=point))
    distance=sum(math.dist(a,b) for a,b in zip(points,points[1:]))
    # Simulator moves at most speed metres each one-second tick; include waypoint rounding.
    seconds=sum(max(1,math.ceil(math.dist(a,b)/max(speed,0.1))) for a,b in zip(points,points[1:]))
    if battery-seconds*.15<20:reasons.append(dict(kind='battery_reserve',remaining_pct=round(battery-seconds*.15,2)))
    if points[-1][2]!=0:reasons.append(dict(kind='landing_required'))
    return dict(passed=not reasons,reasons=reasons,distance_m=round(distance,1),estimated_seconds=seconds)


def clear(a,b,boxes):
    return not any(intersects(a,b,box) for box in boxes)


def detour(start,goal,altitude,boxes,fence):
    up=[start[0],start[1],altitude];down=[goal[0],goal[1],altitude]
    if not clear(start,up,boxes) or not clear(down,goal,boxes):return None
    if clear(up,down,boxes):return [list(start),up,down,list(goal)]
    nx=int((fence['x1']-fence['x0'])/CELL)+1;ny=int((fence['y1']-fence['y0'])/CELL)+1
    point=lambda cell:[fence['x0']+cell[0]*CELL,fence['y0']+cell[1]*CELL,altitude]
    def nearest(p):
        cx=round((p[0]-fence['x0'])/CELL);cy=round((p[1]-fence['y0'])/CELL)
        options=[(i,j) for i in range(max(0,cx-3),min(nx,cx+4)) for j in range(max(0,cy-3),min(ny,cy+4))]
        return next((c for c in sorted(options,key=lambda c:math.dist(point(c),p)) if clear(p,point(c),boxes)),None)
    origin=nearest(up);target=nearest(down)
    if origin is None or target is None:return None
    cost={origin:0};previous={};queue=[(0,0,origin)]
    while queue:
        _,spent,cell=heapq.heappop(queue)
        if spent!=cost[cell]:continue
        if cell==target:
            cells=[cell]
            while cell in previous:cell=previous[cell];cells.append(cell)
            raw=[up]+[point(c) for c in reversed(cells)]+[down]
            pulled=[raw[0]];i=0
            while i<len(raw)-1:
                j=len(raw)-1
                while j>i+1 and not clear(raw[i],raw[j],boxes):j-=1
                pulled.append(raw[j]);i=j
            return [list(start)]+pulled+[list(goal)]
        for dx,dy in [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]:
            nxt=(cell[0]+dx,cell[1]+dy)
            if not(0<=nxt[0]<nx and 0<=nxt[1]<ny) or not clear(point(cell),point(nxt),boxes):continue
            new=spent+math.hypot(dx,dy)
            if new<cost.get(nxt,float('inf')):
                cost[nxt]=new;previous[nxt]=cell
                heapq.heappush(queue,(new+math.dist(nxt,target),new,nxt))
    return None


def propose(v,command,buildings,airspace,fence, *, force_alternatives=False, goal_override=None, extra_boxes=None):
    boxes=obstacles(buildings,airspace['zones'])+list(extra_boxes or []);start=list(v.position)
    goal=list(goal_override) if goal_override is not None else list(v.home) if command=='rtl' else [*start[:2],0]
    direct=[start,goal];result=check(direct,boxes,fence,v.speed,v.battery)
    choices=[]
    def add(name,points):
        # Remove consecutive duplicates without changing any segment geometry.
        cleaned=[points[0]]
        for p in points[1:]:
            if math.dist(p,cleaned[-1])>1e-8:cleaned.append(p)
        if len(cleaned)<2:return
        checked=check(cleaned,boxes,fence,v.speed,v.battery)
        if checked['passed'] and len(cleaned)<=40 and not any(c['route']==cleaned for c in choices):
            choices.append(dict(id=f'route-{len(choices)+1}',name=name,route=cleaned,check=checked))
    if result['passed']:add('直接返航' if command=='rtl' else '原地垂直降落',direct)
    if not result['passed'] or force_alternatives:
        targets=[('原目的地绕障',goal)]+[(s['name'],[s['x'],s['y'],0]) for s in airspace['landing_sites']]
        levels=sorted({max(start[2],60),max(start[2],120),max(start[2],200),max(start[2],280)})
        for label,target in targets:
            for altitude in levels:
                if altitude>fence['z1']:continue
                points=detour(start,target,altitude,boxes,fence)
                if points:add(label+f' · {altitude:g} m',points)
                if len(choices)>=3:break
            if len(choices)>=3:break
    return dict(direct=result,choices=choices,source='simulation',model='prism-segment-astar-v1',
        horizontal_clearance_m=HORIZONTAL,vertical_clearance_m=VERTICAL,
        no_route=not choices,note='合成建筑与局部 ENU 禁飞棱柱；逐航段精确相交检测。备用点为仿真配置，不代表可实际降落。未认证动态交通、风场或真实地形。')
