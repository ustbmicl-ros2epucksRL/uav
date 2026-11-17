"""Opt-in live acceptance against the managed local SITL adapter (never hardware)."""
import argparse
import json
import time
import uuid
from pathlib import Path
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8778')
parser.add_argument('--report',default='/tmp/lowair-sitl-acceptance.json')
args=parser.parse_args()
c=httpx.Client(base_url=args.url,trust_env=False,timeout=30)
report={'source':'ardupilot-sitl','commands':[]}
def post(path,body):
    r=c.post('/api/sitl'+path,json=body);r.raise_for_status();return r.json()
def command(name):
    body={'command_id':uuid.uuid4().hex,'command':name,'expires_at':time.time()+20,'altitude':40}
    r=post('/commands',body);report['commands'].append(r)
    assert post('/commands',body)==r,'Repeated command must return same receipt'
    print(name,r['status'],r.get('mav_result'),flush=True)
    return r
post('/start',{})
for _ in range(60):
    status=c.get('/api/sitl').json()
    if status['connected'] and status['telemetry'].get('gps_fix',0)>=3 and status['telemetry'].get('landed_state')==1:break
    time.sleep(1)
else:raise AssertionError('GPS/heartbeat/extended ground state not ready')
report['mission']=post('/patrol/upload',{})
assert command('guided')['status']=='acknowledged'
for _ in range(20):
    if command('arm')['status']=='acknowledged':break
    print(c.get('/api/sitl').json().get('message'),flush=True);time.sleep(2)
else:raise AssertionError('Pre-arm checks did not pass')
assert command('takeoff')['status']=='acknowledged'
for _ in range(60):
    if c.get('/api/sitl').json()['telemetry'].get('altitude',0)>35:break
    time.sleep(1)
else:raise AssertionError('Takeoff not observed')
assert command('auto')['status']=='acknowledged'
with c.stream('GET','/api/test-video/stream',timeout=10) as r:
    r.raise_for_status();data=b''
    for chunk in r.iter_bytes():
        data+=chunk
        if data.count(b'\xff\xd8')>=3:break
    assert data.count(b'\xff\xd8')>=3
    report['video']={'source':'independent-test-pattern','transport':'mjpeg','frames':3}
for step in range(240):
    status=c.get('/api/sitl').json()
    if step%10==0:print('patrol',status['telemetry'],flush=True)
    if status.get('patrol',{}).get('status')=='completed':break
    time.sleep(1)
else:raise AssertionError('Patrol did not complete')
report['result']=c.get('/api/sitl/patrol/result').json()
assert len(report['result']['track'])>10
assert status['telemetry']['armed'] is False
assert status['telemetry']['battery'] >= 20, 'Test fixture must land with battery reserve'
report['final_telemetry']=status['telemetry']
report['wire_protocol']=status.get('wire_protocol')
assert report['wire_protocol']=='2.0'
Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('PASS SITL telemetry, mission ACK, command ACK/idempotency, video frames, patrol landing; report:',args.report,flush=True)
