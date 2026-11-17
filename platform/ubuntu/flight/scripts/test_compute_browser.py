"""Actual browser interactions against this run's persisted archive (no app-state injection)."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect, TimeoutError as BrowserTimeout


def run(url, directory, headless=False):
    output = Path(directory)
    checks = json.loads((output / 'replay-checks.json').read_text())
    flow = json.loads((output / 'flow.json').read_text())
    report = {'status': 'failed', 'checks': [], 'screenshots': [], 'page_errors': [], 'control_requests': [], 'warnings': []}
    browser = None
    page = None
    def passed(name):
        report['checks'].append(name)
    def capture(name):
        try:
            page.wait_for_function("""() => {const map=document.querySelector('#map'); const r=map.getBoundingClientRect(); const tiles=[...map.querySelectorAll('img.leaflet-tile')].filter(i=>{const b=i.getBoundingClientRect();return b.right>r.left&&b.left<r.right&&b.bottom>r.top&&b.top<r.bottom});return tiles.length>0&&tiles.every(i=>i.complete&&i.naturalWidth>0&&Number(getComputedStyle(i).opacity)>=0.99)}""", timeout=15000)
        except BrowserTimeout:
            warning = '外部卫星瓦片未全部加载；节点、链路及迁移图层已独立检查。'
            if warning not in report['warnings']:
                report['warnings'].append(warning)
        page.screenshot(path=str(output / (name + '.png')))
        report['screenshots'].append(name + '.png')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=headless)
            page = browser.new_page(viewport={'width': 1600, 'height': 1000})
            page.set_default_timeout(15000)
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            page.on('request', lambda request: report['control_requests'].append(request.url) if request.method != 'GET' and '/api/' in request.url else None)
            try:
                page.goto(url, wait_until='domcontentloaded')
                expect(page.locator('#twin-record-note')).to_contain_text('已暂停', timeout=45000)
                expect(page.locator('#notice')).to_contain_text(checks['task'])
                page.locator('#twin-event-summary').click()
                page.get_by_text('视角', exact=True).click()
                page.get_by_role('button', name='二维', exact=True).click()
                page.get_by_role('button', name='Zoom out', exact=True).click()
                markers = page.locator('#twin-event-markers button:visible')
                detail = page.locator('#compute-selection')
                for category in ['normal', 'weak', 'disconnected', 'offline', 'migration', 'recovery']:
                    expected = [e for e in checks['events'] if e['category'] == category]
                    page.locator('#twin-event-filter').select_option(category)
                    expect(markers).to_have_count(len(expected))
                    markers.first.click()
                    expect(page.locator('#twin-event-prev')).to_be_disabled()
                    for index, event in enumerate(expected):
                        if index:
                            page.locator('#twin-event-next').click()
                        expect(markers.nth(index)).to_have_attribute('aria-pressed', 'true')
                        expect(page.locator('#twin-event-note')).to_contain_text(event['label'])
                        assert abs(float(page.locator('#twin-record-time').input_value()) - event['seconds']) < .11
                        expect(page.locator('#twin-record-play')).to_have_text('播放')
                        expect(detail).to_contain_text(event['label'])
                        if event.get('transfer_token'):
                            expect(page.locator('#twin-event-note')).to_contain_text(event['transfer_token'])
                        if category in ['normal', 'weak', 'disconnected']:
                            state = {'normal': '正常', 'weak': '弱网', 'disconnected': '断链'}[category]
                            expect(detail).to_contain_text('该时刻链路 · ' + state)
                            assert page.locator('#map [aria-label]').evaluate_all('(els)=>els.map(e=>e.getAttribute("aria-label"))')
                            labels = page.locator('#map [aria-label]').evaluate_all('(els)=>els.map(e=>e.getAttribute("aria-label"))')
                            assert any('→' in x and state in x for x in labels), labels
                        if event['label'].startswith('节点恢复'):
                            node = event['label'].split(' · ')[-1]
                            labels = page.locator('#map [aria-label]').evaluate_all('(els)=>els.map(e=>e.getAttribute("aria-label"))')
                            assert any((' · '+node+' · ') in label and ('在线' in label or '忙碌' in label) for label in labels), labels
                        if event['label'] == '链路恢复':
                            expect(detail).to_contain_text('该时刻链路 · 正常')
                        if category == 'offline':
                            node = event['label'].split(' · ')[-1]
                            labels = page.locator('#map [aria-label]').evaluate_all('(els)=>els.map(e=>e.getAttribute("aria-label"))')
                            assert any(' · '+node+' · 离线' in label for label in labels), labels
                    expect(page.locator('#twin-event-next')).to_be_disabled()
                    if len(expected) > 1:
                        page.locator('#twin-event-prev').click()
                        expect(markers.nth(len(expected)-2)).to_have_attribute('aria-pressed', 'true')
                    passed('filter/navigation/map: ' + category)
                capture('browser-events')
                page.locator('#twin-event-filter').select_option('all')
                for index in range(len(checks['events'])-1):
                    a, b = checks['events'][index:index+2]
                    if a['seconds'] == b['seconds']:
                        markers.nth(index).click()
                        page.locator('#twin-event-next').click()
                        expect(markers.nth(index+1)).to_have_attribute('aria-pressed', 'true')
                        expect(page.locator('#twin-event-note')).to_contain_text(b['label'])
                        passed('same-time distinct event navigation')
                        break
                page.locator('#twin-event-filter').select_option('migration')
                for index, migration in enumerate(checks['migration_checks']):
                    markers.nth(index).click()
                    expect(detail).to_contain_text(migration['batch'])
                    expect(detail.locator('table')).to_be_visible()
                    labels = page.locator('#map [aria-label]').evaluate_all('(els)=>els.map(e=>e.getAttribute("aria-label"))')
                    assert any(migration['batch'][:8] in label for label in labels), labels
                    page.locator('#map [aria-label*='+chr(34)+migration['batch'][:8]+chr(34)+']').first.press('Enter')
                    expect(detail).to_contain_text(migration['batch'])
                    batch = next(b for b in flow['batches'] if b['id'] == migration['batch'])
                    start = flow['result']['track'][0]['at']
                    if batch['finished_at'] > start + migration['seconds']:
                        expect(detail.get_by_role('img', name='关联批次检测证据')).to_have_count(0)
                        expect(detail).to_contain_text('尚未完成')
                    cursor = min(flow['result']['runtime_history'][-1]['at'], max(batch['finished_at'] + .1, start + migration['seconds']))
                    page.locator('#twin-record-time').fill(str(round(cursor-start, 1)))
                    expect(detail).to_contain_text('关联检测 ' + str(migration['targets']) + ' 个目标')
                    for label, key in [('节点', 'node'), ('执行往返', 'worker_ms'), ('链路等待', 'network_ms'), ('尝试总耗时', 'total_ms')]:
                        row = detail.get_by_role('row').filter(has=page.get_by_role('cell', name=label, exact=True))
                        for column, side in [(1, 'before'), (2, 'after')]:
                            value = migration[side][key]
                            text = value if key == 'node' else '—' if value is None else str(int(value+.5))+' ms'
                            expect(row.get_by_role('cell').nth(column)).to_have_text(text)
                    image = detail.get_by_role('img', name='关联批次检测证据')
                    expect(image).to_have_attribute('src', batch['output']['frame']['url'])
                    image.evaluate('(img)=>img.decode()')
                    assert image.evaluate('(img)=>img.naturalWidth') > 0
                    passed('migration timing and matching detection: ' + migration['batch'])
                    if index == 0:
                        detail.scroll_into_view_if_needed()
                        capture('browser-migration')
                page.locator('#twin-record-live').click()
                expect(page.locator('#twin-event-panel')).to_be_hidden()
                page.locator('#twin-record-load').click()
                expect(page.locator('#twin-record-note')).to_contain_text('已暂停')
                passed('return live and reload archive')
                assert not report['control_requests'], report['control_requests']
                assert not report['page_errors'], report['page_errors']
                report['status'] = 'passed'
            except Exception as error:
                report['error'] = str(error)
                capture('browser-failure')
            finally:
                browser.close()
    except Exception as error:
        report['error'] = str(error)
    (output / 'browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    if urlparse(args.url).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('Only local replay pages are supported')
    raise SystemExit(run(args.url, args.output, args.headless))
