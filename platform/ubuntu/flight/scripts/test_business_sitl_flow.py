"""Opt-in unified service -> managed SITL -> durable replay acceptance."""
import argparse
import json
import time
import uuid
from pathlib import Path
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8772')
parser.add_argument('--report',default='/tmp/business-sitl-flow.json')
parser.add_argument('--aoi-policy', action='store_true')
parser.add_argument('--joint-role',type=int,choices=[1,2])
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
route=[dict(x=x,y=0,z=z) for x,z in [(0,0),(0,40),(40,40),(0,40),(0,0)]]
purpose='AoI 策略调度仿真' if args.aoi_policy else '业务任务—SITL—档案闭环仿真'
if args.joint_role:
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from app import aoi_joint
    routes, decisions=aoi_joint.plans()
    route=routes[args.joint_role-1]
    report['joint_plan']=dict(routes=routes, decisions=decisions,role=args.joint_role)
    purpose=aoi_joint.PURPOSE+str(args.joint_role)
application=request('POST','/service/api/applications',dict(applicant='SITL 自动联调',pilot_id=ids['pilot'],vehicle_id=ids['vehicle'],scene='urban',purpose=purpose,route=route))
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
    for _ in range(120):
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
    if args.aoi_policy or args.joint_role:
        from hashlib import sha256
        model = Path(__file__).resolve().parents[1]/'data/aoi-policy/actor.pt'
        if args.joint_role:
            model=aoi_joint.MODEL
        digest = sha256(model.read_bytes()).hexdigest()
        events = [p['aoi_policy'] for p in archive['track'] if p.get('aoi_policy')]
        assert len({e['slot'] for e in events}) >= 3
        assert all(e['model_sha256']==digest and e['execution_id']==execution['execution_id'] for e in events)
        report['policy_evidence'] = dict(model_sha256=digest, decisions=len({e['slot'] for e in events}))
    report.update(result=archive,final_task=final,replay_url=execution['records'][0]['replay_url'])
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('PASS business approval, SITL validation/upload/idempotency, flight, task sync, archive and replay link',flush=True)
finally:
    if request('GET','/api/sitl')['telemetry'].get('armed'):
        command('land')
        for _ in range(60):
            if not request('GET','/api/sitl')['telemetry'].get('armed'):break
            time.sleep(1)
