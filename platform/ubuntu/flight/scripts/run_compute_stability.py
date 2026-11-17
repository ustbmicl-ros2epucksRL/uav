"""Sequential SITL/Chrome regressions with durable, fail-fast stability reports."""
import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode

FLIGHT = Path(__file__).resolve().parents[1]


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('rounds must be >= 1')
    return number


def write_summary(base, summary):
    summary['passed'] = sum(row['status'] == 'passed' for row in summary['rounds'])
    summary['failed'] = sum(row['status'] == 'failed' for row in summary['rounds'])
    summary['not_run'] = summary['requested_rounds'] - len(summary['rounds'])
    metadata = base / 'job.json'
    if metadata.is_file():
        parent = json.loads(metadata.read_text()).get('retry_of')
        if parent:
            summary['retry_of'] = parent
    acceptance = base / 'platform-ui/acceptance.json'
    if acceptance.is_file():
        summary['platform_ui'] = json.loads(acceptance.read_text())
    temporary = base / 'summary.json.tmp'
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    temporary.replace(base / 'summary.json')
    esc = lambda value: html.escape(str(value if value is not None else '—'), quote=True)
    body = '<h1>云边端连续回归稳定性报告</h1><p>状态：' + esc(summary['status']) + '；计划 ' + str(summary['requested_rounds']) + ' 轮，通过 ' + str(summary['passed']) + '，失败 ' + str(summary['failed']) + '，未运行 ' + str(summary['not_run']) + '。</p>'
    body += '<p>本机 SITL、测试录像及应用层网络仿真；失败即停止，保留全部日志、档案及截图。每轮耗时包含启动与检查开销。</p>'
    if summary.get('retry_of'):
        parent = esc(summary['retry_of'])
        body += '<p><a href="/api/stability/' + parent + '/files/summary.html">原失败报告 · ' + parent + '</a></p>'
    body += '<table><tr><th>轮次</th><th>任务</th><th>状态</th><th>总耗时/s</th><th>SITL/s</th><th>浏览器/s</th><th>失败原因</th></tr>'
    for row in summary['rounds']:
        stages = {stage['name']: stage.get('duration_s') for stage in row['stages']}
        body += '<tr>' + ''.join('<td>' + esc(value) + '</td>' for value in [row['round'], row.get('task_id'), row['status'], row['duration_s'], stages.get('sitl-flow'), stages.get('browser'), row['failure_reason'] or '无']) + '</tr>'
    body += '</table>'
    for row in summary['rounds']:
        body += '<h2 id="round-' + str(row['round']) + '">第 ' + str(row['round']) + ' 轮 · ' + esc(row.get('task_id')) + '</h2>'
        if chain := row.get('short_transfer'):
            def replay(token):
                query = urlencode(dict(source='sitl', task=row['task_id'], record=row['record_id'], transfer=token))
                return '<a target="_blank" rel="noopener" href="' + esc(summary['url'].rstrip('/') + '/?' + query) + '">' + esc(token) + '</a>'
            body += '<p>短传输 ' + replay(chain['short_token']) + ' 已结束。重新选择 ' + replay(chain['transfer_token']) + ' 后记录弱网、断链与迁移。</p>'
            query = urlencode(dict(source='sitl', task=row['task_id'], record=row['record_id'], transfer=chain['short_token'], report=base.name, round=row['round'], autoplay=1))
            body += '<p><a data-round="' + str(row['round']) + '" href="' + esc(summary['url'].rstrip('/') + '/?' + query) + '">连续播放六步</a> <span></span></p>'
            body += '<p>批次 ' + esc(chain['batch']) + ' · ' + esc(chain['task']) + ' · ' + esc(chain['from']) + ' → ' + esc(chain['to']) + ' · 节点恢复 ' + esc(chain['recovered_node']) + '</p>'
        for link in row['artifacts']:
            body += '<p><a href="' + esc(link) + '">' + esc(link) + '</a></p>'
        if row['warnings']:
            body += '<p>警告：' + esc('；'.join(row['warnings'])) + '</p>'
        for screenshot in row['screenshots']:
            body += '<p>' + esc(screenshot) + '</p><img loading="lazy" src="' + esc(screenshot) + '">'
    if 'platform_ui' in summary:
        ui = summary['platform_ui']
        body += '<h2>平台页面验收</h2><p>' + esc(ui['status']) + ' · ' + esc(ui['duration_s']) + ' 秒</p>'
        for check in ui['checks']:
            body += '<p>✓ ' + esc(check) + '</p>'
        if ui.get('error'):
            body += '<pre>' + esc(ui['error']) + '</pre>'
        for file in ['platform-ui/acceptance.json', *ui['files']]:
            body += '<p><a href="' + esc(file) + '">' + esc(file) + '</a></p>'
    body += '''<script>
function restoreProgress(){
 for(const link of document.querySelectorAll('a[data-round]')){
  const url=new URL(link.href), key='transfer:'+url.searchParams.get('report')+':'+url.searchParams.get('record');
  let saved;
  try{saved=JSON.parse(sessionStorage.getItem(key));}catch{continue;}
  if(!saved||!Number.isInteger(saved.index)||saved.index<0||saved.index>5)continue;
  link.nextElementSibling.textContent='第 '+(saved.index+1)+'/6 步 · '+saved.label;
  link.textContent=saved.index===5?'重新播放六步':'继续播放六步';
  url.searchParams.set('step',saved.index===5?0:saved.index);link.href=url;
 }
 if(/^#round-[0-9]+$/.test(location.hash))document.getElementById(location.hash.slice(1))?.scrollIntoView();
}
document.addEventListener('click',event=>{
 const link=event.target.closest('a[data-round]');
 if(link)history.replaceState(null,'','#round-'+link.dataset.round);
});
window.addEventListener('pageshow',restoreProgress);
</script>'''
    temporary = base / 'summary.html.tmp'
    temporary.write_text('<!doctype html><meta charset="utf-8"><title>连续回归稳定性报告</title><style>body{max-width:1200px;margin:32px auto;font:16px sans-serif}table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:10px}img{max-width:100%}</style>' + body)
    temporary.replace(base / 'summary.html')


def run(args):
    base = Path(args.output).resolve() if args.output else FLIGHT.parents[2] / 'outputs' / 'compute-stability' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    base.mkdir(parents=True, exist_ok=False)
    summary = {'status': 'running', 'requested_rounds': args.rounds, 'url': args.url, 'started_at': time.time(), 'rounds': []}
    write_summary(base, summary)
    print('REPORT', base / 'summary.html', flush=True)
    for index in range(1, args.rounds + 1):
        directory = base / f'round-{index}'
        log = base / f'round-{index}-runner.log'
        command = [sys.executable, str(FLIGHT / 'scripts/run_compute_regression.py'), '--url', args.url, '--output', str(directory)]
        if args.headless:
            command.append('--headless')
        started = time.time()
        print(f'ROUND {index}/{args.rounds}', flush=True)
        error = None
        try:
            with log.open('w') as stream:
                code = subprocess.run(command, cwd=FLIGHT, stdout=stream, stderr=subprocess.STDOUT).returncode
        except (OSError, KeyboardInterrupt) as exc:
            code, error = 1, str(exc) or 'Interrupted by user'
        report = {}
        try:
            report = json.loads((directory / 'report.json').read_text())
            if not isinstance(report, dict) or not isinstance(report.get('browser', {}), dict):
                raise ValueError('Round report must contain an object and browser object')
        except (ValueError, OSError) as exc:
            error = error or 'Round report unavailable: ' + str(exc)
            report = {}
        browser = report.get('browser', {})
        passed = code == 0 and report.get('status') == 'passed' and browser.get('status') == 'passed'
        reason = None if passed else error or report.get('error') or browser.get('error') or f'Round exit {code}; regression/browser did not report passed'
        row = {'round': index, 'status': 'passed' if passed else 'failed', 'task_id': report.get('task_id'), 'record_id': report.get('record_id'), 'exit_code': code, 'duration_s': round(time.time()-started, 2), 'stages': report.get('stages', []), 'counts': report.get('counts'), 'failure_reason': reason, 'warnings': browser.get('warnings', []), 'browser_checks': len(browser.get('checks', [])), 'screenshots': [str(p.relative_to(base)) for p in sorted(directory.glob('*.png'))], 'artifacts': [log.name] + [str(p.relative_to(base)) for p in sorted(directory.glob('*')) if p.suffix in ('.html', '.json', '.log')]}
        row['short_transfer'] = report.get('short_transfer')
        summary['rounds'].append(row)
        summary['status'] = 'running' if passed and index < args.rounds else 'passed' if passed else 'failed'
        summary['updated_at'] = time.time()
        summary['duration_s'] = round(summary['updated_at']-summary['started_at'], 2)
        if summary['status'] != 'running':
            summary['finished_at'] = summary['updated_at']
        write_summary(base, summary)
        print(row['status'].upper(), index, row['duration_s'], flush=True)
        if not passed:
            break
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=positive, default=3)
    parser.add_argument('--url', default='http://127.0.0.1:8773')
    parser.add_argument('--output', help='New, non-existing evidence directory')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except FileExistsError:
        parser.exit(2, 'Output directory already exists; choose a new directory to preserve evidence.\n')
