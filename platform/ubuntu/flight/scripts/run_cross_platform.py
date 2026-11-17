import json, time, sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import httpx
base = 'http://127.0.0.1:'
g = base + '8772'
s = base + '8771'
import html
import traceback
import shutil
from contextlib import redirect_stdout
job = Path(sys.argv[1])
job.mkdir(parents=True)
rounds = int(sys.argv[2]) if len(sys.argv)>2 else 1
started = time.time()
data = {}
http = httpx.Client(trust_env=False, timeout=10)

def get(url):
    return http.get(url).raise_for_status().json()

def save():
    (out / 'acceptance.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))

def click_post(page, locator, path):
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith(path)) as op:
        locator.click()
    r = op.value
    assert r.ok, (r.status, r.text())
    return r.json()

def ctl(page):
    panel = page.locator('#sitl-panel')
    if not panel.evaluate('e=>e.open'):
        panel.locator('summary').click()

def popup(page, link):
    with page.expect_popup() as op:
        link.click()
    return op.value

def execute(cruise):
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel='chrome')
        ctx = b.new_context(viewport={'width': 1600, 'height': 1000})
        ground = ctx.new_page()
        ground.goto(g)
        service = ground
        ground.locator('#portal-link').click()
        expect(service).to_have_url(s + '/')
        print('RUN 创建并审批区域任务', flush=True)
        service.locator('button[data-tab="applications"]').click()
        form = service.locator('#application')
        free = [r for r in get(s + '/api/resources')['resources'] if not r.get('allocation') and r['enabled']]
        v = next((r for r in free if r['kind'] == 'vehicle' and all((k in {x['id'] for x in free} for k in (r['pilot_id'], r['airport_id'], r['payload_id'])))))
        # Distinct cruise heights expose accidental reuse of the previous task's route.
        route = [dict(zip(('x', 'y', 'z'), p)) for p in [[0, 0, 0], [0, 0, cruise], [225, 225, cruise], [325, 325, cruise], [0, 0, cruise], [0, 0, 0]]]
        form.locator('[name=applicant]').fill('跨平台联调')
        form.locator('[name=vehicle_id]').select_option(v['id'])
        form.locator('[name=pilot_id]').select_option(v['pilot_id'])
        form.locator('[name=purpose]').fill('区域任务跨平台 SITL 验收')
        form.locator('[name=route]').fill(json.dumps(route))
        app = click_post(service, form.locator('button'), '/api/applications')['id']

        def action(name, id):
            return service.locator(f'button[data-action="{name}"][data-id="{id}"]')
        approved = click_post(service, action('approve', app), f'/api/applications/{app}/review')
        assert approved['status'] == 'approved'
        task = click_post(service, action('schedule', app), '/api/tasks/from-application/' + app)
        tid = task['id']
        data.update(application=app, task=tid, states=[task['status']], vehicle=v['id'])
        save()
        print(data, flush=True)
        service.screenshot(path=out / 'approval.png')
        service.locator('button[data-tab="tasks"]').click()
        row = service.locator('tr').filter(has=action('validate-sitl', tid))
        row.get_by_text('规划任务航线').click()
        expect(service).to_have_url(base + '8767/flight-route/' + tid)
        print('RUN 核对规划航点', flush=True)
        plan = click_post(service, service.locator('#plan'), '/api/flight-route/' + tid)
        assert plan['route'] == [[point[axis] for axis in ('x', 'y', 'z')] for point in route]
        assert plan['approved_route_matches'] and plan['check']['passed'] and (plan['task_id'] == tid) and (plan['application_id'] == app)
        expect(service.locator('#status')).to_contain_text('与批准航线一致')
        service.screenshot(path=out / 'planning.png')
        data['plan'] = plan
        save()
        service.locator('#back').click()
        expect(service).to_have_url(s + '/?tab=tasks&task=' + tid)
        ground = popup(service, service.locator('#system-home'))
        ground.wait_for_load_state('domcontentloaded')
        ctl(ground)
        if not get(g + '/api/sitl')['running']:
            click_post(ground, ground.locator('#sitl-start'), '/api/sitl/start')
        for _ in range(90):
            st = get(g + '/api/sitl')
            if st['connected'] and st['telemetry'].get('gps_fix', 0) >= 3 and (st['telemetry'].get('landed_state') == 1):
                break
            time.sleep(1)
        val = click_post(service, action('validate-sitl', tid), f'/api/tasks/{tid}/validate-sitl')
        assert val['result']['passed'], val
        prep = click_post(service, action('prepare-sitl', tid), f'/api/tasks/{tid}/prepare-sitl')
        data['states'].append(prep['status'])
        data['execution'] = prep['execution']['execution_id']
        save()
        expect(service.locator('#task-execution-state')).to_contain_text('待起飞')
        ground = popup(service, service.locator('#situation-open'))
        ground.wait_for_load_state('domcontentloaded')
        assert 'task=' + tid in ground.url
        ctl(ground)
        print('RUN 执行 SITL', flush=True)

        def command(name):
            return click_post(ground, ground.locator(f'[data-sitl-command="{name}"]'), '/api/sitl/commands')
        try:
            assert command('guided')['status'] == 'acknowledged'
            for _ in range(25):
                if command('arm')['status'] == 'acknowledged':
                    break
                time.sleep(2)
            else:
                raise AssertionError('arm rejected')
            assert command('takeoff')['status'] == 'acknowledged'
            for _ in range(60):
                if get(g + '/api/sitl')['telemetry'].get('altitude', 0) > 35:
                    break
                time.sleep(1)
            assert command('auto')['status'] == 'acknowledged'
            expect(service.locator('tr').filter(has=action('sync', tid)).locator('.pill')).to_have_text('执行中', timeout=20000)
            ground.screenshot(path=out / 'execution.png')
            data['states'].append('flying')
            save()
            for _ in range(250):
                if get(s + '/api/tasks/' + tid)['status'] == 'completed':
                    break
                time.sleep(1)
            else:
                raise AssertionError('completion timeout')
        finally:
            if get(g + '/api/sitl')['telemetry'].get('armed'):
                command('land')
        print('RUN 打开历史回放', flush=True)
        service.locator('tr').filter(has=action('replay-task', tid)).get_by_text('结果回放').click()
        execdata = get(s + f'/api/tasks/{tid}/execution')
        record = execdata['record_id']
        replay = popup(service, service.locator('#task-context a').filter(has_text=record[:6]).first)
        expect(replay.locator('#notice')).to_contain_text(tid + ' · 历史回放', timeout=30000)
        replay.locator('#twin-record-time').fill('5')
        archive = get(g + '/api/sitl/records/' + record)
        assert archive['execution_id'] == data['execution'] and archive['business_task_id'] == tid and (archive['patrol']['route'] == plan['route'])
        assert execdata['status'] == 'completed' and archive['track'][-1]['armed'] is False
        replay.screenshot(path=out / 'replay.png')
        data.update(record=record, replay_url=replay.url, samples=len(archive['track']), result='passed')
        data['states'].append('completed')
        save()
        print(data, flush=True)


def verify_archives(completed):
    seen = {key: set() for key in ('application', 'task', 'execution', 'record')}
    for index, item in enumerate(completed, 1):
        task = item['task']
        business_url = f'{s}/api/tasks/{task}'
        business = get(business_url)
        binding = get(business_url + '/execution')
        record = get(g + '/api/sitl/records/' + item['record'])
        for key, values in seen.items():
            assert item[key] not in values, f'第 {index} 轮重复使用 {key}: {item[key]}'
            values.add(item[key])
        assert task == business['id'] == item['plan']['task_id'] == binding['task_id'] == record['business_task_id'], task
        assert item['application'] == business['application_id'] == item['plan']['application_id'], task
        assert item['record'] == binding['record_id'], task
        assert item['execution'] == binding['execution_id'] == record['execution_id'], task
        assert item['states'] == ['scheduled', 'prepared', 'flying', 'completed'], task
        assert binding['status'] == business['status'] == 'completed', task
        route = [[point[a] for a in ('x','y','z')] for point in business['route']]
        assert route == item['plan']['route'] == record['patrol']['route'], task
        assert route[1][2] == 119 + index and len(route) == 6, task
        assert len(record['track']) == item['samples'] and not record['track'][-1]['armed'], task


rows = []
completed = []
sections = []
fault_round = int(sys.argv[3]) if len(sys.argv)>3 else 0
resume = int(sys.argv[4]) if len(sys.argv)>4 else 1
parent = job.parent/sys.argv[5] if len(sys.argv)>5 and sys.argv[5] else None
failed_attempts = []
if parent:
    prior = json.loads((parent/'summary.json').read_text())
    failed_attempts.extend(prior.get('failed_attempts', []))
    evidence = parent/f'round-{resume}'/'acceptance.json'
    failed_attempts.append(json.loads(evidence.read_text()) if evidence.exists() else {})
    for index in range(1, resume):
        folder = f'round-{index}'
        shutil.copytree(parent/folder, job/folder)
        shutil.copy2(parent/f'{folder}-runner.log', job/f'{folder}-runner.log')
        completed.append(json.loads((job/folder/'acceptance.json').read_text()))
        rows.append(prior['rounds'][index-1])
        sections.append(prior['sections'][index-1])
    origin = f'/api/stability/{parent.name}/files/summary.html'
    sections.append(f'<p>保留前 {resume-1} 轮结果，从第 {resume} 轮恢复。<a href="{origin}">查看原失败报告、日志与截图</a></p>')
    (job/'summary.html').write_text('<meta charset="utf-8"><h1>重试运行中</h1>'+''.join(sections))
for index in range(resume, rounds + 1):
    out = job / f'round-{index}'
    out.mkdir()
    data = {}
    error = None
    begin = time.time()
    with (job/f'round-{index}-runner.log').open('w') as log, redirect_stdout(log):
        try:
            execute(119 + index)
            print('RUN 复核本轮及此前各轮飞行档案', flush=True)
            completed.append(data)
            verify_archives(completed)
            for old in failed_attempts:
                for key in ('task','application','record','execution'):
                    assert data[key] != old.get(key), f'重用了失败尝试的 {key}'
                if old.get('record'):
                    original = get(g + '/api/sitl/records/' + old['record'])
                    assert original['business_task_id'] == old['task']
                    assert original['execution_id'] == old['execution']
                    assert original['patrol']['route'] == old['plan']['route']
            if index == fault_round:
                raise RuntimeError('QA-CONTROLLED：回放归档后注入一次验收故障')
        except Exception:
            error = traceback.format_exc()
            print(error)
    finish = time.time()
    status = 'failed' if error else 'passed'
    row = dict(round=index, task_id=data.get('task'), status=status, duration_s=round(finish-begin, 2),
               started_at=begin, finished_at=finish, error=error)
    rows.append(row)
    (out/'report.json').write_text(json.dumps(row, ensure_ascii=False, indent=2))
    images = ''.join(f'<a href="{out.name}/{name}.png"><img width="280" alt="{label}" src="{out.name}/{name}.png"></a>'
                     for name,label in [('approval','审批'),('planning','规划'),('execution','执行'),('replay','回放')]
                     if (out/f'{name}.png').exists())
    replay = f'<a href="{html.escape(data["replay_url"], quote=True)}">打开历史回放</a>' if data.get('replay_url') else ''
    sections.append(f'<section><h2>第 {index} 轮 · {html.escape(data.get("task","待创建"))} · {status}</h2>'
                    f'<p>{row["duration_s"]} 秒 · {replay} · <a href="round-{index}-runner.log">日志</a></p>{images}'
                    f'<pre>{html.escape(error or "")}</pre><details><summary>任务、航点与档案编号</summary>'
                    f'<pre>{html.escape(json.dumps(data, ensure_ascii=False, indent=2))}</pre></details></section>')
    state = status if error or index == rounds else 'running'
    summary = dict(status=state, requested_rounds=rounds, started_at=started, rounds=rows, sections=[section for section in sections if section.startswith('<section>')], failed_attempts=failed_attempts)
    for filename, content in [('summary.html','<!doctype html><meta charset="utf-8"><title>跨平台回归</title>'
                              + f'<h1>跨平台 SITL 回归 · {index}/{rounds} · {state}</h1>' + ''.join(sections)),
                              ('summary.json',json.dumps(summary, ensure_ascii=False, indent=2))]:
        tmp = job/(filename+'.tmp')
        tmp.write_text(content)
        tmp.replace(job/filename)
    if error:
        sys.exit(1)
