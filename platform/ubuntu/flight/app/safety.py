"""Deterministic closest-approach baseline, using telemetry/filtered estimates only."""
import math


def closest_approach(a, va, b, vb, horizon=20):
    delta = [b[i]-a[i] for i in range(3)]
    velocity = [vb[i]-va[i] for i in range(3)]
    norm = sum(x*x for x in velocity)
    t = max(0, min(horizon, -sum(delta[i]*velocity[i] for i in range(3))/norm)) if norm else 0
    return {'seconds_to_closest': round(t, 2), 'separation_m': round(math.sqrt(sum((delta[i]+velocity[i]*t)**2 for i in range(3))), 2)}


def assess(vehicles, track):
    candidates = []
    for v in vehicles:
        if v['position'][2] <= 2 or not v['armed']:
            continue
        points = v['mission']
        target = points[min(v['wp'],len(points)-1)] if points and v['mode']=='AUTO' else v['position']
        if v['mode'] == 'RTL':
            target = v['home']
        elif v['mode'] == 'LAND':
            target = [*v['position'][:2], 0]
        if v.get('disposition_route') and v['mode'] in {'RTL','LAND'}:
            target=v['disposition_route'][v.get('disposition_wp',0)]
        distance = math.dist(target,v['position'])
        velocity = [(target[i]-v['position'][i])*v['speed']/distance for i in range(3)] if distance else [0,0,0]
        candidates.append((v['id'],v['position'],velocity))
    if track.get('state')=='confirmed' and track.get('estimate'):
        velocity=track.get('velocity') or [0,0,0]
        candidates.append(('unconfirmed-target',track['estimate'],velocity))
    conflicts=[]
    for n,(aid,a,va) in enumerate(candidates):
        for bid,b,vb in candidates[n+1:]:
            result=closest_approach(a,va,b,vb)
            if result['separation_m']<50:
                conflicts.append(dict(pair=[aid,bid],**result,threshold_m=50))
    return {'conflicts':conflicts,'model':'constant-velocity-cpa-v1','horizon_seconds':20,
            'note':'20秒恒速直线预测，50米阈值；合成跟踪与仿真遥测，不是避碰认证。'}
