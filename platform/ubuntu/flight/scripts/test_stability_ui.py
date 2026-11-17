"""Run three regressions from the platform UI and attach acceptance evidence."""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from run_compute_stability import FLIGHT, write_summary


def open_panel(page, url):
    page.goto(url, wait_until='domcontentloaded')
    page.locator('#settings').click()
    page.locator('#stability-open').click()


def open_link(page, link):
    with page.expect_popup() as opened:
        link.click()
    popup = opened.value
    popup.wait_for_load_state('domcontentloaded')
    return popup


def verify_transfer_links(report_page, summary):
    for row in summary['rounds']:
        chain = row['short_transfer']
        for field, start in [('short_token', 0), ('transfer_token', 1)]:
            tab = open_link(report_page, report_page.get_by_role('link', name=chain[field], exact=True))
            try:
                note = tab.locator('#twin-event-note')
                detail = tab.locator('#compute-selection')
                labels = ['短传输结束', '重新选择传输', '弱网', '断链', '迁移 ·', '节点恢复']
                expect(tab.locator('#notice')).to_contain_text(row['task_id'])
                for index in range(start, len(labels)):
                    expect(note).to_contain_text(labels[index])
                    expect(detail).to_contain_text(labels[index])
                    if index:
                        expect(detail).to_contain_text(chain['batch'])
                    if index in (2, 3):
                        expect(detail).to_contain_text('该时刻链路 · ' + labels[index])
                        expect(tab.locator('#map [aria-label*="→"][aria-label*="' + labels[index] + '"]').first).to_be_attached()
                    if index == 5:
                        expect(detail).to_contain_text(chain['recovered_node'])
                        image = detail.get_by_alt_text('关联批次检测证据')
                        image.evaluate('(img)=>img.decode()')
                    else:
                        tab.locator('#twin-event-next').click()
                expect(tab.locator('#twin-event-next')).to_be_disabled()
            finally:
                tab.close()


def verify_continuous_replay(page, summary):
    for row in summary['rounds']:
        number = row['round']
        link = page.locator(f'a[data-round="{number}"]')
        link.click()
        note = page.locator('#twin-event-note')
        play = page.locator('#twin-record-play')
        detail = page.locator('#compute-selection')
        expect(note).to_contain_text('短传输结束')
        expect(note).to_contain_text('重新选择传输', timeout=10000)
        play.click()
        expect(play).to_have_text('播放')
        page.wait_for_timeout(2700)
        expect(note).to_contain_text('重新选择传输')
        page.locator('#twin-event-prev').click()
        expect(note).to_contain_text('短传输结束')
        page.locator('#twin-event-next').click()
        page.locator('#twin-event-next').click()
        expect(note).to_contain_text('弱网')
        page.locator('#transfer-return-report').click()
        expect(page).to_have_url(re.compile(f'#round-{number}$'))
        expect(link.locator('..')).to_contain_text('第 3/6 步 · 弱网')
        link.click()
        expect(note).to_contain_text('弱网')
        for step in ['弱网', '断链']:
            expect(detail).to_contain_text('该时刻链路 · ' + step, timeout=10000)
            expect(page.locator('#map [aria-label*="→"][aria-label*="' + step + '"]').first).to_be_attached()
        expect(note).to_contain_text('迁移 ·', timeout=10000)
        expect(detail).to_contain_text(row['short_transfer']['batch'])
        expect(note).to_contain_text('节点恢复', timeout=10000)
        detail.get_by_alt_text('关联批次检测证据').evaluate('(img)=>img.decode()')
        expect(play).to_have_text('播放')
        page.locator('#transfer-return-report').click()
        expect(link.locator('..')).to_contain_text('第 6/6 步')


def failure_history(browser, url, job, out):
    """Use this run's archive with an intentionally incorrect expected task."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    sys.path.insert(0, str(FLIGHT))
    from app.stability_api import router

    source = job / 'round-1'
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / 'controlled-failure' / 'round-1'
        folder.mkdir(parents=True)
        report = json.loads((source / 'report.json').read_text())
        checks = json.loads((source / 'replay-checks.json').read_text())
        expected = 'QA-EXPECTED-MISMATCH'
        checks['task'] = expected
        (folder / 'replay-checks.json').write_text(json.dumps(checks))
        shutil.copyfile(source / 'flow.json', folder / 'flow.json')
        with (out / 'browser.log').open('w') as log:
            process = subprocess.run([sys.executable, str(FLIGHT / 'scripts/test_compute_browser.py'),
                                      '--url', report['replay_url'], '--output', str(folder), '--headless'],
                                     stdout=log, stderr=subprocess.STDOUT, timeout=180)
        result = json.loads((folder / 'browser.json').read_text())
        assert process.returncode and expected in result['error'], result
        shutil.copyfile(out / 'browser.log', folder / 'browser.log')
        shutil.copyfile(folder / 'browser-failure.png', out / 'browser-failure.png')
        (folder / 'report.json').write_text(json.dumps(dict(report, status='failed', browser=result)))
        (folder.parent / 'summary.json').write_text(json.dumps(dict(status='failed', requested_rounds=1, rounds=[])))
        app = FastAPI()
        app.include_router(router)
        context = browser.new_context()
        with patch.dict(os.environ, FLIGHT_STABILITY_DIR=tmp), TestClient(app) as client:
            def serve(route):
                response = client.get(urlsplit(route.request.url).path)
                route.fulfill(status=response.status_code, content_type=response.headers['content-type'], body=response.content)
            context.route('**/api/stability**', serve)
            page = context.new_page()
            open_panel(page, url)
            row = page.locator('.stability-round').first
            expect(row).to_contain_text(expected)
            row.locator('summary').click()
            for name in ['browser.log', 'browser-failure.png']:
                popup = open_link(page, row.get_by_role('link', name=name, exact=True))
                if name.endswith('.png'):
                    popup.locator('img').evaluate('(img) => img.decode()')
                else:
                    expect(popup.locator('body')).to_contain_text(expected)
                popup.close()
            page.screenshot(path=out / 'failure-entry.png')
        context.close()


def run(args):
    root = Path(os.environ.get('FLIGHT_STABILITY_DIR', FLIGHT.parents[2] / 'outputs/compute-stability'))
    job = root / args.job if args.job else None
    out = Path(args.output) if args.output else None
    result = {'status': 'failed', 'checks': []}
    checks = result['checks']
    def step(name):
        if job:
            folder = job / 'platform-ui'
            temporary = folder / 'progress.json.tmp'
            temporary.write_text(json.dumps(dict(status='running', step=name, checks=checks), ensure_ascii=False))
            temporary.replace(folder / 'progress.json')
            print(name, flush=True)

    started = time.monotonic()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(viewport={'width': 1600, 'height': 1000})
        errors = []
        context.on('page', lambda tab: tab.on('pageerror', lambda e: errors.append(str(e))))
        page = context.new_page()
        try:
            open_panel(page, args.url)
            if args.job:
                ident = args.job
                page.locator('#stability-history').select_option(ident)
            else:
                page.locator('#stability-rounds').fill('3')
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/stability')) as response:
                    page.locator('#stability-start').click()
                assert response.value.status == 202, response.value.text()
                ident = response.value.json()['id']
                job = root / ident
            assert (job / 'job.json').is_file(), '请使用与本地服务相同的 FLIGHT_STABILITY_DIR'
            out = job / 'platform-ui'
            out.mkdir(exist_ok=True)
            print(job / 'summary.html', flush=True)
            result['job'] = ident
            heading = '#stability-detail h3'
            expect(page.locator(heading)).to_contain_text(ident)
            expect(page.locator('#stability-start')).to_be_disabled()
            checks.append('页面启动三轮')
            step('刷新并恢复第一轮')
            page.reload()
            page.locator('#settings').click()
            page.locator('#stability-open').click()
            expect(page.locator(heading)).to_contain_text(ident)
            expect(page.locator('.stability-round').first).to_contain_text('运行中')
            page.screenshot(path=out / 'refresh.png')
            checks.append('刷新自动恢复第一轮')
            marker = os.environ.get('FLIGHT_TEST_REPORT_FAILURE_ONCE')
            if marker and Path(marker).is_file():
                Path(marker).unlink()
                context.route('**/files/summary.html', lambda route: route.fulfill(
                    status=503, content_type='text/plain; charset=utf-8', body='测试故障：统一报告暂时不可用（503）'))
                popup = open_link(page, page.get_by_role('link', name='打开统一稳定性报告 ↗'))
                reason = popup.locator('body').inner_text()
                popup.screenshot(path=out / 'report-503.png')
                raise AssertionError(reason)
            step('关闭控制页，等待后台进入第二轮')
            page.close()
            assert not context.pages
            deadline = time.monotonic() + 360
            while not (job / 'round-2-runner.log').exists():
                assert time.monotonic() < deadline, '等待后台进入第二轮超时'
                assert json.loads((job / 'summary.json').read_text())['status'] == 'running', '第一轮提前结束'
                time.sleep(2)
            step('重新打开控制页并核对第二轮')
            page = context.new_page()
            open_panel(page, args.url)
            expect(page.locator(heading)).to_contain_text(ident)
            rows = page.locator('.stability-round')
            expect(rows.nth(0)).to_contain_text('通过')
            expect(rows.nth(1)).to_contain_text('运行中')
            page.screenshot(path=out / 'reopened.png')
            checks.append('关闭唯一控制页期间后台进入第二轮，重新打开自动恢复')
            step('等待三轮执行完成')
            detail = page.locator('#stability-detail')
            expect(detail).to_have_attribute('data-round-status', re.compile('^(passed|failed|interrupted)$'), timeout=720000)
            expect(detail).to_have_attribute('data-round-status', 'passed')
            expect(rows).to_have_count(3)
            step('核对历史报告和回放截图')
            tasks = []
            for row in rows.all():
                title = row.locator('h4').inner_text()
                assert '通过 · TASK-' in title, title
                tasks.append(title.split(' · ')[-1])
                row.locator('summary').click()
                expect(row.locator('img')).to_have_count(2)
                for img in row.locator('img').all():
                    img.scroll_into_view_if_needed()
                    img.evaluate('(img) => img.decode()')
                popup = open_link(page, row.get_by_role('link', name='report.html', exact=True))
                expect(popup.locator('body')).to_contain_text(tasks[-1])
                popup.close()
                row.locator('summary').click()
            assert len(set(tasks)) == 3
            result['tasks'] = tasks
            popup = open_link(page, page.get_by_role('link', name='打开统一稳定性报告 ↗'))
            expect(popup.locator('body')).to_contain_text('通过 3，失败 0，未运行 0')
            step('验证统一报告传输链接与六步回放')
            verify_transfer_links(popup, json.loads((job / 'summary.json').read_text()))
            verify_continuous_replay(popup, json.loads((job / 'summary.json').read_text()))
            checks.append('逐轮连续播放、暂停、前后跳转、返回报告恢复轮次事件及地图检测联动')
            checks.append('三轮六个传输链接、六步事件导航、地图与关联检测图')
            popup.close()
            page.screenshot(path=out / 'completed.png')
            checks.append('三个独立任务、三份历史报告、六张回放截图和统一报告')
            step('验证失败日志和截图入口')
            failure_history(browser, args.url, job, out)
            checks.append('隔离故障样例的具体失败原因、日志和截图入口')
            assert not errors, errors
            result['status'] = 'passed'
        except Exception as exc:
            result['error'] = str(exc)
            if out and out.is_dir() and not page.is_closed():
                page.screenshot(path=out / 'acceptance-failure.png')
        finally:
            browser.close()
    result['duration_s'] = round(time.monotonic() - started, 2)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if job and out == job / 'platform-ui' and out.is_dir():
        result['files'] = sorted('platform-ui/' + f.name for f in out.iterdir() if f.is_file())
        temporary = out / 'acceptance.json.tmp'
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        temporary.replace(out / 'acceptance.json')
        summary = json.loads((job / 'summary.json').read_text())
        if summary['status'] != 'running':
            write_summary(job, summary)
        if args.output:
            shutil.copytree(out, args.output, dirs_exist_ok=True)
    return int(result['status'] != 'passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8773')
    parser.add_argument('--job', help='Attach to the three rounds started by the platform')
    parser.add_argument('--output', help='Optional additional copy of acceptance evidence')
    args = parser.parse_args()
    launched = time.monotonic()
    try:
        sys.exit(run(args))
    except Exception as exc:
        if args.job:
            root = Path(os.environ.get('FLIGHT_STABILITY_DIR', FLIGHT.parents[2] / 'outputs/compute-stability'))
            job = root / args.job
            folder = job / 'platform-ui'
            if folder.is_dir():
                progress = folder / 'progress.json'
                last = json.loads(progress.read_text()) if progress.exists() else {}
                failure = dict(status='failed', checks=last.get('checks', []), duration_s=round(time.monotonic()-launched, 2),
                               error=str(exc), files=['platform-ui/acceptance.log'])
                temporary = folder / 'acceptance.json.tmp'
                temporary.write_text(json.dumps(failure, ensure_ascii=False, indent=2))
                temporary.replace(folder / 'acceptance.json')
                summary = json.loads((job / 'summary.json').read_text())
                if summary['status'] != 'running':
                    write_summary(job, summary)
        raise
