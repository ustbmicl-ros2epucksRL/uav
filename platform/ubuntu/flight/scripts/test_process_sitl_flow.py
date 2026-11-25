"""Opt-in unified service -> managed SITL -> durable replay acceptance."""
import atexit
import argparse
import json
import sys
import time
import uuid
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parent))
import verify_compute_chain

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8773')
parser.add_argument('--demo',action='store_true',help='Exercise the server-driven one-click scenario')
parser.add_argument('--network',action='store_true',help='Exercise weak links, interrupted transfers and node recovery')
parser.add_argument('--dynamic',action='store_true',help='Exercise measured feedback and in-flight worker migration')
parser.add_argument('--report',default='/tmp/process-sitl-flow.json')
args=parser.parse_args()
c=httpx.Client(base_url=args.url,trust_env=False,timeout=30)
report={'source':'managed-ardupilot-sitl-video-detection','commands':[]}
def save_report():
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2))
atexit.register(save_report)
def request(method,path,body=None):
    r=c.request(method,path,json=body);r.raise_for_status();return r.json()
def command(name):
    result=request('POST','/api/sitl/commands',dict(command_id=uuid.uuid4().hex,execution_id=report.get('execution',{}).get('execution_id'),command=name,expires_at=time.time()+20,altitude=40))
    report['commands'].append(result);save_report();print(name,result['status'],flush=True);return result
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
runtime=request('POST','/api/compute/start',dict(execution_id=prepared['execution']['execution_id']))
request('POST','/api/compute/fault',dict(execution_id=prepared['execution']['execution_id'],task='detect',mode='fail'))
report['runtime_id']=runtime['runtime_id']
if args.dynamic:
    for _ in range(120):
        runtime=request('GET','/api/compute')
        if runtime.get('counts',{}).get('completed',0)>=2:break
        time.sleep(.25)
    else:raise AssertionError('No measured warm-up batches')
    request('POST','/api/compute/fault',dict(execution_id=prepared['execution']['execution_id'],task='detect',mode='timeout'))
    for _ in range(200):
        runtime=request('GET','/api/compute')
        job=(runtime.get('current') or {}).get('tasks',{}).get('detect',{})
        if job.get('status')=='running' and job['attempts'][-1].get('fault')=='timeout':break
        time.sleep(.1)
    else:raise AssertionError('Injected running detection not observed')
    report['offline_node']=job['node']
    request('POST','/api/compute/node',dict(execution_id=prepared['execution']['execution_id'],node_id=job['node'],action='offline',duration_s=8))
    print('node offline during detection',job['node'],flush=True)

if args.network:
    for _ in range(160):
        runtime=request('GET','/api/compute')
        if runtime.get('counts',{}).get('completed',0)>=2:break
        time.sleep(.25)
    else:raise AssertionError('No baseline detection batches')
    bound=dict(execution_id=prepared['execution']['execution_id'])
    request('POST','/api/compute/network',dict(**bound,profile='weak',duration_s=20,mbps=1,latency_ms=200))
    request('POST','/api/compute/node',dict(**bound,node_id='device',action='offline',duration_s=15))
    for _ in range(200):
        runtime=request('GET','/api/compute')
        net=runtime.get('network',{})
        if net.get('profile')=='weak' and any(f['size_bytes']>10000 and f['status']=='moving' and f['revision']==net['revision'] and time.time()-f['started_at']>.35 for f in net.get('flows',[])):break
        time.sleep(.1)
    else:raise AssertionError('No actual weak-link transfer observed')
    report['interrupted_flow']=runtime['network']['flows']
    request('POST','/api/compute/network',dict(**bound,profile='disconnected',duration_s=5))
    for _ in range(100):
        runtime=request('GET','/api/compute')
        if any(e['kind']=='transfer_failed' for e in runtime.get('scheduling_events',[])):break
        time.sleep(.1)
    else:raise AssertionError('Interrupted transfer was not rejected')
    request('POST','/api/compute/node',dict(**bound,node_id='device',action='recover'))
    print('weak link transfer interrupted; recovered local worker for migration',flush=True)

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
    if args.demo:
        request('POST','/api/compute/demo',dict(execution_id=prepared['execution']['execution_id'],action='short_transfer'))
        for _ in range(440):
            runtime=request('GET','/api/compute')
            demo=runtime.get('demo') or {}
            if demo.get('state') in ('completed','incomplete','cancelled'):break
            time.sleep(.25)
        assert demo.get('state')=='completed',demo
        report['demo']=demo
        print('one-click demo completed',demo,flush=True)
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
    for _ in range(150):
        runtime=request('GET','/api/compute')
        if runtime['state']=='completed':break
        time.sleep(.5)
    assert runtime['state']=='completed',runtime
    assert runtime['counts']['completed']>10 and runtime['counts']['retries']>=1
    assert any(e.get('recovered') for e in runtime['events'])
    batches=request('GET','/api/compute/records/'+execution['record_id']+'/batches')['batches']
    assert len(batches)==runtime['counts']['completed']+runtime['counts']['failed']
    successful=[b for b in batches if b['status']=='completed']
    assert all(b['output']['synthetic'] is False and b['output']['frame'] for b in successful)
    assert any(b['output']['detections'] for b in successful)
    import hashlib
    for b in successful:
        frame=b['output']['frame']
        response=c.get(frame['url']);response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest()==frame['sha256']
        assert b['output']['inference_s']>0
    used={j['node'] for b in successful for j in b['tasks'].values()}
    kinds={n['kind'] for n in archive['algorithm']['compute']['nodes'] if n['id'] in used}
    assert kinds=={'device','edge','cloud'},used
    assert all(a.get('pid') and a.get('roundtrip_s',0)>0 for b in successful for j in b['tasks'].values() for a in j['attempts'])
    archive=request('GET','/api/sitl/records/'+execution['record_id'])
    assert archive['runtime_history'][-1]['state']=='completed'
    assert not any(n['alive'] for n in archive['runtime_history'][-1]['nodes'])
    if args.dynamic:
        events=[e for h in archive['runtime_history'] for e in h.get('scheduling_events',[])]
        assert {'node_offline','node_recovered','task_migrated','task_recovered'} <= {e['kind'] for e in events}
        migrated=[b for b in successful if any(a.get('error')=='node_offline' for j in b['tasks'].values() for a in j['attempts'])]
        assert migrated
        for b in migrated:
            for job in b['tasks'].values():
                assert all(a.get('frame_sha256')==b['output']['frame']['sha256'] for a in job['attempts'])
        decisions=[a['decision'] for b in successful for j in b['tasks'].values() for a in j['attempts']]
        assert any(d['revision']>0 and any(c['source']=='node-ewma' for c in d['candidates'].values()) for d in decisions)
        assert any(e['kind']=='placement_changed' and e.get('previous_node') for e in events)
        report['dynamic_verified']=True
    if args.demo:
        assert {'normal','weak','disconnected'} <= {h.get('network',{}).get('profile') for h in archive['runtime_history']}
        assert any((h.get('demo') or {}).get('state')=='completed' for h in archive['runtime_history'])
        report['demo_replay_verified']=True
    if args.network:
        profiles={h.get('network',{}).get('profile') for h in archive['runtime_history']}
        assert {'normal','weak','disconnected'}<=profiles
        events=[e for h in archive['runtime_history'] for e in h.get('scheduling_events',[])]
        assert any(e['kind']=='transfer_failed' for e in events)
        migrated=[b for b in successful if any(a.get('error')=='link_disconnected' for j in b['tasks'].values() for a in j['attempts'])]
        assert migrated
        for b in migrated:
            for job in b['tasks'].values():
                assert all(a['frame_sha256']==b['output']['frame']['sha256'] for a in job['attempts'])
        assert any(a.get('network_wait_s',0)>.3 for b in successful for j in b['tasks'].values() for a in j['attempts'])
        assert all(a.get('worker_roundtrip_s',0)>0 for b in successful for j in b['tasks'].values() for a in j['attempts'] if a['status']=='completed')
        report['network_verified']=True
    chain=verify_compute_chain.verify(c,execution['record_id'])
    report['chain']=chain
    assert chain['passed'],chain.get('error')
    assert chain['chain']['experiment_id']==experiment['id'] and chain['chain']['application_id']==application['id'] and chain['chain']['task_id']==ident
    report.update(runtime=runtime,batches=batches)
    report.update(result=archive,final_task=final,replay_url=execution['records'][0]['replay_url'])
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('PASS real video model inference, immutable frames, processes, retries, SITL telemetry and persistent replay',flush=True)
finally:
    if request('GET','/api/sitl')['telemetry'].get('armed'):
        command('land')
        for _ in range(60):
            if not request('GET','/api/sitl')['telemetry'].get('armed'):break
            time.sleep(1)
    if 'final_task' not in report:
        current = request('GET', '/api/compute')
        execution_id = prepared['execution']['execution_id']
        if current.get('execution_id') == execution_id and current.get('state') == 'running':
            request('POST', '/api/compute/stop', dict(execution_id=execution_id))
