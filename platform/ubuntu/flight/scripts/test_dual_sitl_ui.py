"""Opt-in two-instance simulation and browser acceptance on 8772."""
import json
import math
import time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

def main(exercise=False, dest=None):
    url='http://127.0.0.1:8772/'
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome')
        p=browser.new_page()
        p.goto(url)
        p.locator('#settings').click()
        p.locator('#dual-open').click()
        if exercise:p.locator('#dual-exercise').check()
        with p.expect_response(lambda r:r.request.method=='POST' and r.url.split('?')[0].endswith('/api/dual-sitl')) as call:
            p.locator('#dual-create').click()
        assert call.value.status==202,call.value.text()
        ident=call.value.json()['id']
        if dest:
            (dest/'flow.json').write_text(json.dumps({'task_id':ident}))
        print('RUN 双机飞行与故障注入',ident,flush=True)
        for _ in range(300):
            data=p.request.get(url+'api/dual-sitl/'+ident).json()
            if data['status'] in ('completed','failed'):break
            time.sleep(1)
        if dest:
            (dest/'flight.json').write_text(json.dumps(data,ensure_ascii=False))
        assert data['id']==ident
        assert data['status']=='completed',data.get('error',data['status'])
        frames=data['frames']
        assert len(set(data['pids']))==2 and len(frames)==18
        blocked=[f for f in frames if f['dispatch']=='blocked']
        recovered=[f for f in frames if f.get('blocked_slot')]
        if exercise:
            assert [f['injection'] for f in blocked]==['临时禁飞区','航线交会']
            assert [f['blocked_slot'] for f in recovered]==[f['slot'] for f in blocked]
            assert all(f['safety']['contacts'] and all(t['mode']=='LOITER' and t['speed']<.5 for t in f['hold_telemetry']) for f in blocked)
            assert all(f['safety']['passed'] and f['dispatch']==['sent']*2 for f in recovered)
        else:assert not blocked
        assert all(f['safety']['passed'] and f['dispatch']==['sent']*2 and all(t['armed'] for t in f['telemetry']) for f in frames if f['dispatch']!='blocked')
        for i in range(2):
            assert math.dist(frames[0]['telemetry'][i]['xyz'][:2],frames[-1]['after'][i][:2])>2
        assert all(not t['armed'] and t['landed_state']==1 for t in data['landed'])
        for original,adjusted in zip(blocked,recovered):
            assert original['hold_ack']==['acknowledged']*2 and not original['safety']['passed']
            assert adjusted['targets']!=original['targets'] and all(t['armed'] for t in adjusted['telemetry'])
            next_frame=frames[adjusted['slot']]
            assert all(t['mode']=='GUIDED' for t in next_frame['telemetry'])
        print('RUN 核对回放时间轴与现场截图',flush=True)
        p.goto(url+'?dual='+ident)
        expect(p.locator('#dual-status')).to_contain_text('已落地上锁')
        p.locator('#dual-time').fill('2')
        expect(p.locator('#dual-slot')).to_contain_text('时隙 3 ')
        before=p.locator('#dual-chart').evaluate('c=>c.toDataURL()')
        p.locator('#dual-play').click()
        expect(p.locator('#dual-slot')).to_contain_text('时隙 4 ')
        p.locator('#dual-play').click()
        assert p.locator('#dual-chart').evaluate('c=>c.toDataURL()')!=before
        dest=dest or Path(__file__).resolve().parents[4]/'outputs/aoi-joint'/ident
        dest.mkdir(parents=True,exist_ok=True)
        if exercise:
            for f in sorted(blocked+recovered,key=lambda item:item['slot']):
                p.locator('#dual-events button').filter(has_text=str(f['slot'])+' ·').first.click()
                expect(p.locator('#dual-slot')).to_contain_text('时隙 '+str(f['slot'])+' ')
                expect(p.locator('#dual-slot')).to_contain_text(f['phase'])
                p.screenshot(path=dest/f"slot-{f['slot']}.png")
        p.screenshot(path=dest/'replay.png')
        print('PASS',data['pids'],len(frames),flush=True)
        return data

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--exercise",action="store_true")
    main(parser.parse_args().exercise)
