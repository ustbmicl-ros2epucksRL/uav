import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location('stability', Path(__file__).parents[1] / 'scripts/run_compute_stability.py')
stability = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stability)


def options(tmp_path, rounds=3):
    return SimpleNamespace(output=str(tmp_path/'suite'), rounds=rounds, url='http://127.0.0.1:8773', headless=True)


def fake(monkeypatch, failure=None, invalid=None):
    calls = []
    def execute(command, **kwargs):
        directory = Path(command[command.index('--output')+1]); directory.mkdir()
        index = len(calls)+1; calls.append(index)
        failed = index == failure
        data = {'status': 'failed' if failed else 'passed', 'task_id': f'TASK-{index}', 'browser': {'status': 'failed' if failed else 'passed', 'error': '<missing image>' if failed else None, 'checks': ['filter', 'next'], 'warnings': []}, 'stages': [{'name': 'browser', 'duration_s': 1}]}
        if invalid != 'missing':
            (directory/'report.json').write_text('[]' if invalid == 'array' else 'broken' if invalid else json.dumps(data))
        (directory/'browser-evidence.png').write_bytes(b'keep evidence')
        return SimpleNamespace(returncode=1 if failed else 0)
    monkeypatch.setattr(stability.subprocess, 'run', execute)
    return calls


def test_configured_count_order_and_screenshots(tmp_path, monkeypatch):
    calls = fake(monkeypatch); args = options(tmp_path, 12)
    assert stability.run(args) == 0
    root = Path(args.output); report = json.loads((root/'summary.json').read_text())
    assert calls == list(range(1,13))
    assert report['passed'] == 12 and report['not_run'] == 0
    assert [r['task_id'] for r in report['rounds']] == [f'TASK-{i}' for i in calls]
    assert report['rounds'][0]['screenshots'] == ['round-1/browser-evidence.png']
    assert '<img' in (root/'summary.html').read_text()


def test_failed_round_stops_preserving_all_evidence(tmp_path, monkeypatch):
    calls = fake(monkeypatch, failure=2); args = options(tmp_path)
    assert stability.run(args) == 1
    root = Path(args.output); report = json.loads((root/'summary.json').read_text())
    assert calls == [1,2]
    assert (report['passed'],report['failed'],report['not_run']) == (1,1,1)
    assert report['rounds'][1]['failure_reason'] == '<missing image>'
    assert (root/'round-2/browser-evidence.png').read_bytes() == b'keep evidence'
    assert not (root/'round-3').exists()
    assert '&lt;missing image&gt;' in (root/'summary.html').read_text()


@pytest.mark.parametrize('invalid', ['missing','malformed','array'])
def test_bad_child_report_fails(tmp_path, monkeypatch, invalid):
    calls = fake(monkeypatch, invalid=invalid); args = options(tmp_path)
    assert stability.run(args) == 1
    assert calls == [1]
    assert json.loads((Path(args.output)/'summary.json').read_text())['status'] == 'failed'


def test_existing_output_preserved(tmp_path):
    args = options(tmp_path); root = Path(args.output); root.mkdir(); (root/'keep').write_text('evidence')
    with pytest.raises(FileExistsError): stability.run(args)
    assert (root/'keep').read_text() == 'evidence'


@pytest.mark.parametrize('value',['0','-1'])
def test_invalid_rounds(value):
    with pytest.raises(argparse.ArgumentTypeError): stability.positive(value)


def test_page_acceptance_is_included_in_both_reports(tmp_path):
    folder = tmp_path / 'platform-ui'
    folder.mkdir()
    ui = dict(status='failed', duration_s=1, checks=['刷新'], error='<failure>', files=['platform-ui/error.png'])
    (folder / 'acceptance.json').write_text(json.dumps(ui))
    stability.write_summary(tmp_path, dict(status='running', requested_rounds=3, rounds=[]))
    assert json.loads((tmp_path / 'summary.json').read_text())['platform_ui'] == ui
    report = (tmp_path / 'summary.html').read_text()
    assert '&lt;failure&gt;' in report and 'href="platform-ui/error.png"' in report
