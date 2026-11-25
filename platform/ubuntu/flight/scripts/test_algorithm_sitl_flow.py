"""Opt-in unified service -> managed SITL -> durable replay acceptance."""
import argparse
import json
import time
import uuid
from pathlib import Path
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8773')
parser.add_argument('--report',default='/tmp/algorithm-sitl-flow.json')
args=parser.parse_args()
c=httpx.Client(base_url=args.url,trust_env=False,timeout=30)
report={'source':'managed-ardupilot-sitl','commands':[]}
def request(method,path,body=None):
    r=c.request(method,path,json=body);r.raise_for_status();return r.json()
def command(name):
    result=request('POST','/api/sitl/commands',dict(command_id=uuid.uuid4().hex,execution_id=report.get('execution',{}).get('execution_id'),command=name,expires_at=time.time()+20,altitude=40))
    report['commands'].append(result);print(name,result['status'],flush=True);return result
suffix=uuid.uuid4().hex[:8]
ids={kind:'QA-'+kind+'-'+suffix for kind in ['airport','pilot','payload','vehicle']}
for kind in ids:
    body=dict(id=ids[kind],kind=kind,name='SITL 任务联调 '+kind+' '+suffix,enabled=True,revision=0)
    if kind=='vehicle':body.update(airport_id=ids['airport'],pilot_id=ids['pilot'],payload_id=ids['payload'])
    request('PUT','/service/api/resources/'+ids[kind],body)
experiment=request('POST','/api/algorithms/runs',dict(planner='bitstar',area_id='west',altitude_m=120))
mission=request('GET','/api/algorithms/runs/'+experiment['id']+'/mission')
route=mission['route'];report['algorithm_run_id']=experiment['id']
application=request('POST','/service/api/applications',dict(applicant='SITL 自动联调',pilot_id=ids['pilot'],vehicle_id=ids['vehicle'],scene='urban',purpose='算法规划—SITL—遥测调度—回放仿真',route=route,algorithm_run_id=experiment['id']))
request('POST','/service/api/applications/'+application['id']+'/review',dict(decision='approve',reviewer='仿真验收',note='仅本机 SITL'))
task=request('POST','/service/api/tasks/from-application/'+application['id']);ident=task['id'];report['task_id']=ident
request('POST','/api/sitl/start')
for _ in range(60):
    state=request('GET','/api/sitl')
    if state['connected'] and state['telemetry'].get('gps_fix',0)>=3 and state['telemetry'].get('landed_state')==1:break
    time.sleep(1)
assert not state['telemetry']['armed']
validation=request('POST',f'/service/api/tasks/{ident}/validate-sitl');assert validation['result']['passed'],validation
report['validation_id']=validation['id']
prepared=request('POST',f'/service/api/tasks/{ident}/prepare-sitl');report['execution']=prepared['execution']
assert prepared['status']=='prepared'
again=request('POST',f'/service/api/tasks/{ident}/prepare-sitl');assert again['execution']['execution_id']==prepared['execution']['execution_id']
assert not request('GET','/api/sitl')['telemetry']['armed']
print('prepared',ident,prepared['execution']['execution_id'],flush=True)
try:
    assert command('guided')['status']=='acknowledged'
    for _ in range(20):
        if command('arm')['status']=='acknowledged':break
        time.sleep(2)
    else:raise AssertionError('SITL arm rejected')
    assert command('takeoff')['status']=='acknowledged'
    for _ in range(60):
        if request('GET','/api/sitl')['telemetry'].get('altitude',0)>35:break
        time.sleep(1)
    else:raise AssertionError('Takeoff not observed')
    assert command('auto')['status']=='acknowledged'
    assert request('POST',f'/service/api/tasks/{ident}/sync')['status']=='flying'
    for _ in range(300):
        execution=request('GET',f'/service/api/tasks/{ident}/execution')
        if execution['status']=='completed':break
        time.sleep(1)
    else:raise AssertionError('Completion not observed')
    final=request('POST',f'/service/api/tasks/{ident}/sync');assert final['status']=='completed'
    archive=request('GET','/api/sitl/records/'+execution['record_id'])
    assert archive['business_task_id']==ident and archive['execution_id']==execution['execution_id']
    assert archive['patrol']['route']==[[p['x'],p['y'],p['z']] for p in route]
    assert len(archive['track'])>10 and archive['track'][-1]['armed'] is False
    assert request('GET','/api/sitl/records?task_id='+ident)['total']==1
    samples=[p['compute'] for p in archive['track'] if p.get('compute',{} ) and p['compute']['status']=='ok']
    assert len({p['at'] for p in samples})>10
    assert all(p['experiment_id']==experiment['id'] for p in samples)
    assert max(p['position'][0] for p in samples)-min(p['position'][0] for p in samples)>400
    assert len({str(p['result']['rows']) for p in samples})>1
    assert archive['algorithm']['experiment_id']==experiment['id']
    report['compute_samples']=len({p['at'] for p in samples})
    report.update(result=archive,final_task=final,replay_url=execution['records'][0]['replay_url'])
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('PASS algorithm mission, approval, SITL upload, flight, telemetry-driven HEFT, sync, durable replay (HEFT previews only; no compute batches — use test_process_sitl_flow.py + verify_compute_chain.py)',flush=True)
finally:
    if request('GET','/api/sitl')['telemetry'].get('armed'):
        command('land')
        for _ in range(60):
            if not request('GET','/api/sitl')['telemetry'].get('armed'):break
            time.sleep(1)
