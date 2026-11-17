import os
import time
import pytest
from app.compute_runtime import Runtime
from app.algorithm_compute import default_compute
from app import compute_store, compute_runtime
from fastapi.testclient import TestClient
from app.main import app


def binding():
    return dict(execution_id='exec-test',task_id='TASK-1',algorithm=dict(experiment_id='a'*32,
        compute=default_compute(dict(xyz_min=[200,200,30],xyz_max=[700,700,280]))))


def sample(position=None):
    now=time.time()
    return dict(status='ok',at=now,telemetry_at=now,position=position or [280,280,120])


def wait(runtime, predicate, seconds=12, position=None):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        runtime.offer(sample(position))
        state=runtime.snapshot()
        if state and predicate(state):return state
        time.sleep(.04)
    raise AssertionError(runtime.snapshot())


@pytest.fixture
def runtime(tmp_path,monkeypatch):
    monkeypatch.setenv('FLIGHT_RECORDS_DIR',str(tmp_path))
    instance=Runtime(binding(),'record-test',interval_s=30,timeout_s=1,max_attempts=2,workload='synthetic-test')
    yield instance
    if instance.thread.is_alive():instance.stop()


def test_real_process_dag_result_and_retry_persist(runtime):
    runtime.inject('detect','fail');runtime.start()
    state=wait(runtime,lambda s:s['counts']['completed']==1)
    assert state['counts']['retries']==1
    assert state['events'][0]['recovered']
    assert len({n['pid'] for n in state['nodes']})==4 and all(n['pid']!=os.getpid() for n in state['nodes'])
    batch=state['last_batch'];jobs=batch['tasks']
    assert jobs['detect']['attempts'][0]['status']=='failed'
    assert jobs['detect']['attempts'][1]['status']=='completed'
    assert jobs['detect']['attempts'][1]['node']=='device'
    assert jobs['detect']['attempts'][1]['recovery']=='local_fallback'
    for task,job in jobs.items():
        a=job['attempts'][-1]
        assert a['pid']!=os.getpid() and a['compute_s']>=a['cpu_s']*.8
        assert a['roundtrip_s']>0 and job['output_sha256']
        for parent in {'preprocess':[],'detect':['preprocess'],'segment':['preprocess'],'fusion':['detect','segment'],'alert':['fusion']}[task]:
            assert a['sent_at']>=jobs[parent]['completed_at']
    assert batch['output']['synthetic'] and 'anomaly' in batch['output']
    runtime.offer(sample(),terminal=True)
    runtime.thread.join(5);assert not runtime.thread.is_alive()
    assert compute_store.history('record-test')[-1]['state']=='completed'
    assert compute_store.batches('record-test')[0]['output']==batch['output']
    assert not any(n['process'].is_alive() for n in runtime.nodes.values())


@pytest.mark.parametrize('fault',['timeout','crash'])
def test_lost_worker_restarts_and_retries(runtime,fault):
    runtime.inject('detect',fault);runtime.start()
    state=wait(runtime,lambda s:s['counts']['completed']==1)
    assert state['counts']['retries']==1
    state=wait(runtime,lambda s:any(n['restarts']>0 for n in s['nodes']))
    assert any(n['restarts']>0 for n in state['nodes'])
    attempts=state['last_batch']['tasks']['detect']['attempts']
    assert attempts[0]['error']==('timeout' if fault=='timeout' else 'worker_exit')
    assert attempts[0]['pid']!=attempts[1]['pid']


def test_stale_telemetry_does_not_create_batches(runtime):
    runtime.start();runtime.offer(dict(status='ok',telemetry_at=time.time()-10,position=[0,0,40]))
    until=time.monotonic()+5
    while not runtime.snapshot() and time.monotonic()<until:time.sleep(.03)
    state=runtime.snapshot()
    assert state['state']=='paused' and state['current'] is None
    runtime.stop()
    assert runtime.snapshot()['state']=='stopped'


def test_unsupported_tasks_fail_before_process_start():
    data=binding();data['algorithm']['compute']['tasks'][0]['id']='arbitrary-code'
    with pytest.raises(ValueError):Runtime(data,'record')


def test_controls_require_current_execution(monkeypatch,tmp_path):
    monkeypatch.setenv('FLIGHT_RECORDS_DIR',str(tmp_path))
    monkeypatch.setattr(compute_runtime,'CURRENT',None)
    c=TestClient(app)
    assert c.post('/api/compute/start',json={'execution_id':'wrong'}).status_code==409
    assert c.post('/api/compute/fault',json={'execution_id':'wrong'}).status_code==409
    assert c.post('/api/compute/stop',json={'execution_id':'wrong'}).status_code==409
    assert c.post('/api/compute/start',json={'execution_id':'wrong','shell':'anything'}).status_code==422


def test_retry_exhaustion_cancels_downstream_without_fake_success(runtime):
    runtime.max_attempts=1
    runtime.inject('preprocess','fail');runtime.start()
    state=wait(runtime,lambda s:s['counts']['failed']==1)
    assert state['last_batch']['output'] is None
    assert state['last_batch']['tasks']['alert']['status']=='cancelled'
    assert state['last_batch']['tasks']['preprocess']['status']=='failed'
    assert not state['counts']['completed']


def test_stop_cancels_inflight_work_and_closes_processes(runtime):
    runtime.timeout_s=10
    runtime.inject('preprocess','timeout');runtime.start()
    wait(runtime,lambda s:s['current'] and s['current']['tasks']['preprocess']['status']=='running')
    runtime.stop()
    state=runtime.snapshot()
    assert state['state']=='stopped' and state['counts']['cancelled']==1
    assert not any(n['alive'] or n['busy'] for n in state['nodes'])


def test_current_result_returns_durable_actual_execution_history(monkeypatch,tmp_path):
    from app import sitl
    monkeypatch.setenv('FLIGHT_RECORDS_DIR',str(tmp_path))
    snapshot=dict(runtime_id='r',record_id='record',at=1,state='completed')
    compute_store.save(snapshot)
    monkeypatch.setattr(sitl,'STATE',dict(record_id='record',track=[],receipts={}))
    result=TestClient(app).get('/api/sitl/patrol/result').json()
    assert result['runtime_history']==[snapshot]
