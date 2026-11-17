"""Run the three-round retry acceptance against the local 8772 SITL workbench."""
from playwright.sync_api import sync_playwright,expect
from pathlib import Path
import json

def progress(folder,step,status='running',report=False):
    tmp=folder/'retry-acceptance.tmp'
    tmp.write_text(json.dumps(dict(step=step,status=status,report=report),ensure_ascii=False))
    tmp.replace(folder/'retry-acceptance.json')

def main(dual=False,job=None,root=None):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome')
        page=browser.new_page()
        page.goto('http://127.0.0.1:8772',wait_until='domcontentloaded')
        page.locator('#settings').click()
        page.locator('#stability-open').click()
        page.locator("#stability-rounds").fill("3")
        import hashlib,json
        def inject(route):
            if route.request.method=='POST':
                route.continue_(post_data=json.dumps(dict(route.request.post_data_json,fault_round=2)))
            else:route.continue_()
        if not job:
            page.route('**/api/stability',inject)
            with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/stability')) as resp:
                page.get_by_text('双机禁飞区与交会回归' if dual else '跨平台任务回归',exact=True).click()
            assert resp.value.status==202,resp.value.text()
            job=resp.value.json()['id']
        else:
            progress(root/job,'等待第二轮故障')
            page.locator('#stability-history').select_option(job)
        print(job,flush=True)
        report_url='http://127.0.0.1:8772/api/stability/'+job+'/files/summary.html'
        import time
        for _ in range(900):
            result=page.request.get('http://127.0.0.1:8772/api/stability/'+job).json()
            if result['status']!='running':break
            time.sleep(1)
        assert result['status']=='failed' and result['rounds'][1]['status']=='failed' and result['rounds'][2]['status']=='not_run',result
        files=(root or Path(__file__).resolve().parents[4]/'outputs/compute-stability')/job
        def hashes():
            paths=list((files/'round-2').glob('*'))+[files/'round-2-runner.log']
            return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        before=hashes()
        if root:progress(files,'第二轮已按预期失败，正在从窗口重试')
        assert len([k for k in before if k.endswith('.png')])==(6 if dual else 4)
        expect(page.locator('#stability-detail')).to_contain_text('QA-CONTROLLED',timeout=15000)
        with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/rounds/2/retry')) as response:
            page.get_by_role('button',name='重试第 2 轮').click()
        assert response.value.status==202,response.value.text()
        child=response.value.json()['id']
        if root:progress(files,'第二轮重试与第三轮续跑')
        print('retry',child,flush=True)
        for _ in range(700):
            detail=page.request.get('http://127.0.0.1:8772/api/stability/'+child).json()
            if detail['status']!='running':break
            time.sleep(1)
        assert detail['status']=='passed',detail
        assert before==hashes()
        assert detail['rounds'][0]['task_id']==result['rounds'][0]['task_id']
        assert detail['rounds'][1]['task_id']!=result['rounds'][1]['task_id']
        page.goto(report_url)
        page.get_by_role('link',name='第 2 轮重试及后续轮次的统一报告').click()
        assert page.locator('img').count()==(15 if dual else 12)
        page.locator('img').evaluate_all('imgs=>Promise.all(imgs.map(i=>i.decode()))')
        if dual:
            originals=[files/'round-1',files/'round-2']
            child_dir=files.parent/child
            assert (originals[0]/'flight.json').read_bytes()==(child_dir/'round-1/flight.json').read_bytes()
            ids=set()
            for folder in originals+[child_dir/'round-2',child_dir/'round-3']:
                flight=json.loads((folder/'flight.json').read_text())
                tid=flight['id']
                assert tid==json.loads((folder/'flow.json').read_text())['task_id'] and tid not in ids
                ids.add(tid)
                assert page.request.get('http://127.0.0.1:8772/api/dual-sitl/'+tid).json()==flight
                tab=browser.new_page()
                tab.goto('http://127.0.0.1:8772/?dual='+tid)
                expect(tab.locator('#dual-status')).to_contain_text(tid+' · 双机已落地上锁')
                tab.close()
        else:
            for a in page.get_by_role('link',name='打开历史回放').all():
                tab=browser.new_page()
                tab.goto(a.get_attribute('href'))
                expect(tab.locator('#notice')).to_contain_text('历史回放')
                tab.close()
        if root:
            sections=[]
            attempts=[(files,1,'第一轮 · 沿用'),(files,2,'第二轮 · 预期失败'),
                      (child_dir,2,'第二轮 · 重试通过'),(child_dir,3,'第三轮 · 自动续跑通过')]
            for location,index,title in attempts:
                folder=location/f'round-{index}'
                tid=json.loads((folder/'flow.json').read_text())['task_id']
                base=f'/api/stability/{location.name}/files/'
                shots=' · '.join(f'<a href="{base}{folder.name}/{img.name}">{img.stem}</a>' for img in sorted(folder.glob('*.png')))
                sections.append(f'<h2>{title}</h2><p>{tid} · <a href="/?dual={tid}">任务回放</a> · '
                                f'<a href="{base}round-{index}-runner.log">执行日志</a> · '
                                f'<a href="{base}{folder.name}/flight.json">飞行档案</a></p><p>{shots}</p>')
            report='<meta charset="utf-8"><h1>双机失败重试验收 · 通过</h1>'
            for location,label in [(files,'原批次'),(child_dir,'重试批次')]:
                report+=f'<a href="/api/stability/{location.name}/files/summary.html">{label} {location.name}</a> '
            (files/'retry-acceptance.html').write_text(report+''.join(sections))
            for location in (files,child_dir):
                summary=location/'summary.html'
                tmp=summary.with_suffix('.new')
                tmp.write_text(summary.read_text()+f'<p><a href="/api/stability/{job}/files/retry-acceptance.html">双机失败重试统一报告</a></p>')
                tmp.replace(summary)
            progress(files,'通过 · 四份档案和原始证据均已核对','passed',True)
        print('passed',[(r['task_id'],r['duration_s']) for r in detail['rounds']],flush=True)


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--dual",action="store_true")
    parser.add_argument('--job')
    parser.add_argument('--root',type=Path)
    args=parser.parse_args()
    try:
        main(args.dual,args.job,args.root)
    except Exception as exc:
        if args.root and args.job:
            progress(args.root/args.job,'失败 · '+str(exc),'failed')
        raise
