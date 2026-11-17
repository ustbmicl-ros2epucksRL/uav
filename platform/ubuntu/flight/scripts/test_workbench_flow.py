"""Explicit opt-in live SITL acceptance for workbench API operations."""
import argparse
import json
import time
from pathlib import Path
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8778')
parser.add_argument('--report',default='/tmp/lowair-workbench-acceptance.json')
parser.add_argument('--manual-resolve',action='store_true')
parser.add_argument('--test-cancel',action='store_true')
args=parser.parse_args()
c=httpx.Client(base_url=args.url,trust_env=False,timeout=30)
page=c.get('/?view=patrol');page.raise_for_status()
for ident in ('horizon','map','video','demo-start','alerts','replay-time'):
    assert 'id="'+ident+'"' in page.text
for asset in ('workbench.js','workbench.css','flight-instrument.js'):
    c.get('/static/'+asset).raise_for_status()
def wait_state(predicate,timeout=120):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=c.get('/api/sitl').json()
        if predicate(state):return state
        job=c.get('/api/sitl-demo').json()
        if job['status']=='failed':raise AssertionError(job)
        time.sleep(.5)
    raise AssertionError('Telemetry timeout')

cancel_evidence=None
if args.test_cancel:
    c.post('/api/sitl-demo/start',json={'auto_resolve':False}).raise_for_status()
    wait_state(lambda s:s['telemetry'].get('mode')=='AUTO' and s['telemetry'].get('altitude',0)>35)
    cancelled=c.post('/api/sitl-demo/cancel',json={});cancelled.raise_for_status()
    assert cancelled.json()['hover_receipt']['status']=='acknowledged'
    hover=wait_state(lambda s:s['telemetry'].get('mode')=='LOITER' and s['telemetry'].get('speed',99)<1)
    time.sleep(3)
    assert c.get('/api/sitl-demo').json()['status']=='interrupted'
    assert c.get('/api/sitl').json()['telemetry']['mode']=='LOITER'
    cancel_evidence={'receipt':cancelled.json(),'hover':hover['telemetry']}
    landing=c.post('/api/sitl/commands',json={'command_id':'acceptance-land-'+str(time.time_ns()),'command':'land','expires_at':time.time()+20})
    landing.raise_for_status();assert landing.json()['status']=='acknowledged'
    wait_state(lambda s:not s['telemetry'].get('armed') and s['telemetry'].get('landed_state')==1,180)
    print('PASS cancel / observed hover / no automatic resume / manual landing',flush=True)

r=c.post('/api/sitl-demo/start',json={'auto_resolve':not args.manual_resolve});r.raise_for_status();ident=r.json()['id']
assert c.post('/api/sitl-demo/start',json={'auto_resolve':True}).json()['id']==ident
assert c.post('/api/sitl/commands',json={'command_id':'blocked-during-demo','command':'arm','expires_at':time.time()+30}).status_code==409
with c.stream('GET','/api/test-video/stream',timeout=10) as video:
    video.raise_for_status();data=b''
    for chunk in video.iter_bytes():
        data+=chunk
        if data.count(b'\xff\xd8')>=3:break
    assert data.count(b'\xff\xd8')>=3
phases=[];saw_review=False
for _ in range(360):
    r=c.get('/api/sitl-demo');r.raise_for_status();job=r.json()
    if not phases or phases[-1]!=job['phase']:
        phases.append(job['phase']);print(job['phase'],job['events'][-1]['text'],flush=True)
    if job['phase']=='review':
        saw_review=True
        alert=job['alerts'][0]
        assert alert['source']=='synthetic-injection' and alert['video_evidence'] is None
        state=c.get('/api/sitl').json()
        assert state['telemetry']['mode']=='LOITER'
        assert isinstance(alert['telemetry']['latitude'],float)
        if args.manual_resolve and alert['status']=='open':
            resolved=c.post('/api/sitl-demo/'+ident+'/alerts/'+alert['id']+'/resolve',json={'actor':'联调测试','note':'确认仿真注入，恢复巡检'})
            resolved.raise_for_status()
    if job['status']=='completed':break
    if job['status'] in ('failed','interrupted'):raise AssertionError(job)
    time.sleep(1)
else:raise AssertionError('Demo timeout')
assert saw_review
result=c.get('/api/sitl-demo/'+ident+'/result').json()
assert result['alerts'][0]['status']=='resolved' and result['alerts'][0]['automatic']==(not args.manual_resolve)
patrol=result['result']['patrol']
assert patrol['check']['passed']
track=result['result']['track'];assert len(track)>100 and 'roll' in track[-1]
final=result['result']['final_telemetry']
assert final['armed'] is False and final['landed_state']==1 and final['battery']>=20
result['acceptance']={'phases':phases,'test_video_jpeg_frames':3,'browser_visual_test':False,'cancel':cancel_evidence}
Path(args.report).write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('PASS one-click SITL patrol / injected alert / hover / resolution / landing / replay data',len(track),'points',flush=True)
