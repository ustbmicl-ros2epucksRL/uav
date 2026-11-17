"""Real optional model integration: run with requirements-video and pinned assets."""
import hashlib
import time
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import video_workload as video
from app.compute_runtime import Runtime
from test_compute_runtime import binding, wait, sample


def test_frame_paths_reject_arbitrary_files(tmp_path, monkeypatch):
    monkeypatch.setenv('FLIGHT_RECORDS_DIR', str(tmp_path))
    client = TestClient(app)
    assert client.get('/api/test-video/frames/not-a-hash').status_code == 404
    assert client.get('/api/test-video/frames/' + 'a' * 64).status_code == 404
    with pytest.raises(ValueError):video.evidence_path('../secret')


def test_real_video_detection_retry_and_archive(tmp_path, monkeypatch):
    pytest.importorskip('torchvision')
    if not video.available():pytest.skip('Run scripts/setup_video_workload.py for optional video integration')
    monkeypatch.setenv('FLIGHT_RECORDS_DIR', str(tmp_path))
    runtime = Runtime(binding(), 'video-record', interval_s=60)
    runtime.inject('detect', 'fail')
    runtime.start()
    try:
        state = wait(runtime, lambda s: s['counts']['completed'] == 1, seconds=75)
        result = state['last_batch']['output']
        assert result['synthetic'] is False and result['onboard'] is False
        assert result['detections'] and result['inference_s'] > 0
        assert any(d['label'] == 'person' for d in result['detections'])
        assert state['events'][0]['recovered']
        attempts = state['last_batch']['tasks']['detect']['attempts']
        assert attempts[-1]['node'] != attempts[0]['node']
        assert all(a['frame_sha256'] == result['frame']['sha256'] for a in attempts)
        assert result['frame']['captured_at'] >= state['last_batch']['telemetry_at']
        for d in result['detections']:
            x, y, x2, y2 = d['box']
            assert 0 <= x < x2 <= result['frame']['width']
            assert 0 <= y < y2 <= result['frame']['height']
        response = TestClient(app).get(result['frame']['url'])
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == result['frame']['sha256']
        runtime.offer(sample(), terminal=True)
        runtime.thread.join(10)
        from app.compute_store import history, batches
        assert batches('video-record')[0]['output'] == result
        assert history('video-record')[-1]['state'] == 'completed'
        assert not any(n['alive'] for n in history('video-record')[-1]['nodes'])
    finally:
        runtime.stop()
