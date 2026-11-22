import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import research_agent as a
from app import store


def test_tool_catalog_and_execution():
    assert len(asyncio.run(a.tool('catalog')))==15
    r=asyncio.run(a.tool('1-1',1))
    assert r['values']==[.6,.84,.6] and r['evidence_status']=='synthetic_only'


@pytest.mark.parametrize('mode',['single','review'])
def test_tool_workflow_two_roles(tmp_path,monkeypatch,mode):
    monkeypatch.setattr(store,'DATA',tmp_path);roles=[]
    async def fake(role,payload,schema):
        roles.append(role)
        if schema is a.Selection:return {'purpose':'检验相关性','experiments':[{'key':'1-1','value':1}]}
        assert payload['experiments'][0]['values'][2]==.6
        return {'conclusion':'合成结果为 0.6','evidence':['1-1'],'limitations':['不是现场数据'],'next_steps':['采集独立数据']}
    monkeypatch.setattr(a,'model',fake)
    r={'id':'a'*32,'request':{'task_id':1,'question':'检验相关性','mode':mode},'status':'running','trace':[]}
    asyncio.run(a.execute(r))
    assert r['status']=='completed' and r['report']
    assert ('review' in r)==(mode=='review')
    assert len(roles)==(3 if mode=='review' else 2) and len(r['trace'])==2
    saved = a.read(r['id'])
    assert saved['trace'][1]['result']['values'][2] == .6
    expected_steps = ['catalog', 'select', 'experiment:1-1', 'report']
    if mode == 'review':
        expected_steps.append('review')
    assert [step['key'] for step in saved['steps']] == expected_steps
    assert all(step['status'] == 'completed' and step['finished_at'] >= step['started_at'] for step in saved['steps'])
    assert [entry['step_index'] for entry in saved['trace']] == [0, 2]


def test_unauthorized_model_action_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    async def fake(*args):return {'purpose':'越界测试','experiments':[{'key':'5-0','value':2}]}
    monkeypatch.setattr(a,'model',fake)
    r={'id':'b'*32,'request':{'task_id':1,'question':'测试','mode':'single'},'status':'running','trace':[]}
    asyncio.run(a.execute(r))
    assert r['status']=='failed' and '未授权' in r['error'] and len(r['trace'])==1
    assert [(step['key'], step['status']) for step in r['steps']] == [('catalog', 'completed'), ('select', 'failed')]


def test_memory_increment_skips_nearby_duplicate_and_respects_budget():
    from app.memory_value import Segment, select
    segments=[
        Segment(id='r1',holder='site-a',kind='position',t0=0,t1=10,x=100,y=100,z=80,nbytes=400),
        Segment(id='r2',holder='site-b',kind='position',t0=8,t1=18,x=140,y=110,z=80,nbytes=400),
        Segment(id='r3',holder='site-a',kind='identity',t0=0,t1=20,nbytes=80),
    ]
    ranked=select(segments,'position',0,20,2000)
    assert [item['id'] for item in ranked['chosen']]==['r1']
    far=segments[1].model_copy(update={'x':300,'y':100,'z':80})
    tight=select([segments[0],far],'position',0,20,500)
    assert [item['id'] for item in tight['chosen']]==['r1'] and tight['used_bytes']==400
    both=select([segments[0],far],'position',0,20,800)
    assert [item['id'] for item in both['chosen']]==['r1','r2']


def test_memory_rank_uses_model_only_for_intent_and_wording(tmp_path,monkeypatch):
    roles=[]
    async def fake(role,payload,schema):
        roles.append(role)
        if schema is a.MemoryIntent:return {'kind':'position','t0':0,'t1':20}
        assert payload['ranking']['chosen'][0]['id']=='r1'
        return {'conclusion':'留下 r1','evidence':['r1'],'limitations':['不是现场数据'],'next_steps':[]}
    monkeypatch.setattr(a,'model',fake)
    out=asyncio.run(a.rank_memory(a.MemoryRankRequest(question='目标在哪',budget_bytes=500,segments=[
        {'id':'r1','holder':'site-a','kind':'position','t0':0,'t1':10,'x':100,'y':100,'z':80,'nbytes':400},
        {'id':'r2','holder':'site-b','kind':'position','t0':8,'t1':18,'x':140,'y':110,'z':80,'nbytes':400},
    ])))
    assert roles==['记忆查询解析','研究分析员'] and out['ranking']['chosen'][0]['id']=='r1'
    assert out['evidence_status']=='record_only'


def test_routes_and_interrupted_job(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    child=FastAPI();child.include_router(a.router);app=FastAPI();app.mount('/network',child)
    with TestClient(app) as c:
        r=c.get('/network/agents');assert r.status_code==200 and '<base href="/network/">' in r.text
        assert c.post('/network/api/research-agent/runs',json={'question':'测试','task_id':7}).status_code==422
        a.save({'id':'c'*32,'status':'running'})
        assert c.get('/network/api/research-agent/runs/'+'c'*32).json()['status']=='interrupted'
        assert c.get('/network/api/research-agent/runs/not-an-id').status_code==404


def test_cancel_releases_slot_and_model_failure_is_not_success(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    async def slow(*args):await asyncio.sleep(10)
    monkeypatch.setattr(a,'model',slow)
    app=FastAPI();app.include_router(a.router)
    with TestClient(app) as c:
        run=c.post('/api/research-agent/runs',json={'question':'测试取消','task_id':1}).json()
        assert c.post('/api/research-agent/runs',json={'question':'同时运行','task_id':1}).status_code==409
        cancelled=c.post('/api/research-agent/runs/'+run['id']+'/cancel').json()
        assert cancelled['status']=='cancelled' and not a.JOBS
        assert cancelled['steps'][-1]['status'] == 'cancelled'
        assert cancelled['steps'][-1]['finished_at']
    async def fail(*args):raise RuntimeError('模型服务未就绪')
    monkeypatch.setattr(a,'model',fail)
    record={'id':'d'*32,'request':{'task_id':1,'question':'失败测试','mode':'single'},'status':'running','trace':[]}
    asyncio.run(a.execute(record))
    assert record['status']=='failed' and 'report' not in record


def test_generation_grammar_avoids_large_repetition_but_validation_remains():
    import json
    import pytest
    from pydantic import ValidationError
    assert 'maxLength' not in json.dumps(a.generation_schema(a.Report))
    assert a.Report.model_json_schema()['properties']['conclusion']['maxLength']==4000
    with pytest.raises(ValidationError):
        a.Report(conclusion='x'*4001,evidence=[],limitations=['边界'],next_steps=[])



@pytest.mark.parametrize('stored_status', ['running', 'interrupted'])
def test_history_marks_abandoned_step_without_inventing_end_time(tmp_path, monkeypatch, stored_status):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    run_id = '9' * 32
    step = dict(key='report', label='生成报告', started_at='2026-11-17T08:00:00Z', finished_at=None, status='running')
    a.save(dict(id=run_id, stage='生成报告', status=stored_status, steps=[step], trace=[]))
    result = a.result(run_id)
    assert result['status'] == 'interrupted'
    assert result['steps'] == [dict(step, status='interrupted')]
    assert asyncio.run(a.cancel(run_id))['steps'] == result['steps']
