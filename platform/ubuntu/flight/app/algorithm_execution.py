"""Telemetry-driven HEFT snapshots; no flight commands or real worker execution."""
import math
from .algorithm_compute import evaluate


def sample(state, now):
    binding=state.get('execution_binding',{}).get('algorithm')
    if not binding:return None
    t=state.get('telemetry',{}); stamps=state.get('updated_at',{})
    keys=('latitude','longitude','altitude','gps_fix')
    valid=all(isinstance(t.get(k),(int,float)) and math.isfinite(t[k]) and 0<=now-stamps.get(k,0)<3 for k in keys)
    if not valid or t['gps_fix']<3:
        return dict(status='stale',at=now,experiment_id=binding['experiment_id'])
    previous=state.get('algorithm_live')
    if previous and previous.get('status')=='ok' and 0<=now-previous['at']<1:
        return previous
    anchor=state['anchor']
    position=[math.radians(t['longitude']-anchor['longitude'])*6378137*math.cos(math.radians(anchor['latitude'])),
              math.radians(t['latitude']-anchor['latitude'])*6378137,t['altitude']]
    result=evaluate(binding['compute'],position,'heft')
    return dict(status='ok',at=now,telemetry_at=stamps['latitude'],position=position,
                experiment_id=binding['experiment_id'],execution_id=state['execution_binding']['execution_id'],
                result=result,model='independent-batch-heft',source='sitl-telemetry-simulation')
