const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(pathname='/network/agents', search='', activeRuns=[]) {
    const elements={}, calls=[], saved=new Map(), timers=[], classes=new Set(), events={}, messages=[];
    class Element {
        constructor(){ this.value=''; this.children=[]; this.disabled=false; this.hidden=false; this.textContent=''; }
        append(...children){this.children.push(...children);}
        replaceChildren(...children){this.children=children;}
        click(){}
        focus(){this.focused=true;}
    }
    const get=id=>elements[id]??=new Element();
    for(const [id,value] of Object.entries({workflow:'platform',scene:'corridor',task:'1',mode:'review',question:'检查廊道'}))get(id).value=value;
    const state={run:{id:'a'.repeat(32),status:'completed',stage:'平台诊断完成',trace:[],report:{conclusion:'缺少验算',evidence:['无记录'],evidence_ids:['ev-1'],limitations:['非现场'],next_steps:[]}}};
    const fetch=async (url,options={})=>{
        calls.push({url,options});let data;
        if(url.endsWith('/status'))data={available:false,model:'local',provider:'Ollama',active_runs:activeRuns};
        else if(url.endsWith('/tools'))data={tools:[{name:'scene_snapshot',description:'配置快照'}]};
        else if(url.endsWith('/runs')&&options.method==='POST')data={...state.run,status:'running',stage:'选择工具'};
        else if(url.endsWith('/runs'))data={runs:[{...state.run,request:{question:'<img onerror=alert(1)>'},created_at:'2026-11-16'}]};
        else data=state.run;
        return {ok:true,json:async()=>data};
    };
    const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element(),body:{classList:{add:name=>classes.add(name)}},addEventListener:(name,fn)=>events[name]=fn},window:{parent:{postMessage:(data,origin)=>messages.push({data,origin})}},location:{pathname,search},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},fetch,console,URL,URLSearchParams,Blob,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){}});
    vm.runInContext(fs.readFileSync(__dirname+'/../static/research-agent.js','utf8'),context);
    return {get,calls,state,context,saved,classes,events,messages};
}
const ready=()=>new Promise(resolve=>setImmediate(resolve));

test('platform workflow submits selected scope on mounted prefix and renders cited evidence',async()=>{
    const s=setup();await ready();await s.get('run').onclick();await ready();
    const request=s.calls.find(c=>c.options.method==='POST');
    assert.equal(request.url,'/network/api/research-agent/runs');
    assert.deepEqual(JSON.parse(request.options.body),{question:'检查廊道',task_id:1,mode:'review',workflow:'platform',scene:'corridor'});
    assert(s.get('answer').children.some(e=>e.textContent.includes('ev-1')));
    assert.equal(s.get('run').disabled,false);
});

test('history is rendered as text and reading it does not execute tools or create runs',async()=>{
    const s=setup('/agents');await ready();
    assert(s.get('history').children.some(e=>e.textContent.includes('<img onerror=alert(1)>')));
    s.get('history').value='a'.repeat(32);s.get('history').onchange();await ready();
    assert.equal(s.calls.filter(c=>c.options.method==='POST').length,0);
    assert(s.calls.some(c=>c.url==='/api/research-agent/runs/'+'a'.repeat(32)));
});

test('workflow selection separates scene from research subtask and handles unavailable model',async()=>{
    const s=setup();await ready();
    assert.equal(s.get('task-label').hidden,true);assert.equal(s.get('scene-label').hidden,false);
    s.get('workflow').value='research';s.get('workflow').onchange();
    assert.equal(s.get('task-label').hidden,false);assert.equal(s.get('scene-label').hidden,true);
    assert.match(s.get('model-status').textContent,/模型未就绪/);
    assert.match(s.get('tool-catalog').textContent,/scene_snapshot/);
});

test('obsolete poll responses cannot overwrite the selected run',async()=>{
    const s=setup();await ready();
    vm.runInContext('generation=5',s.context);
    await vm.runInContext("poll('old',4)",s.context);
    assert.equal(vm.runInContext('record',s.context),null);
});


test('embedded assistant uses validated initial scene and escape only requests close',async()=>{
    const s=setup('/network/agents','?embed=1&scene=electromagnetic');await ready();
    assert(s.classes.has('agent-embedded'));assert.equal(s.get('scene').value,'electromagnetic');
    s.events.keydown({key:'Escape',stopPropagation(){}});
    assert.equal(s.messages[0].data.type,'platform-assistant-ready');
    assert.equal(s.messages[1].data.type,'platform-assistant-close');
    assert.equal(s.calls.filter(c=>c.options.method==='POST').length,0);
    const unknown=setup('/agents','?embed=1&scene=invalid');await ready();
    assert.equal(unknown.get('scene').value,'corridor');
});

test('saved report shows its own scene even if current draft selection differs',async()=>{
    const s=setup();await ready();s.state.run.request={workflow:'platform',scene:'urban',question:'历史园区问题'};
    s.get('history').value='a'.repeat(32);s.get('history').onchange();await ready();
    assert.match(s.get('run-context').textContent,/园区高密度建筑区.*历史园区问题/);
});


test('steps, report citations and tool cards address the same saved result', async () => {
    const s = setup('/network/agents', '?embed=1&workspace=service');
    await ready();
    const run = s.state.run;
    const result = {evidence_id:'ev-1', provenance:'stored_records', scene_revision:2, data:{tasks:[]}};
    Object.assign(run, {
        request:{workflow:'platform', scene:'urban', question:'旧任务', mode:'single'},
        steps:[{label:'读取记录', status:'completed', started_at:'2026-11-21T16:00:00Z'}],
        trace:[{tool:'planning_records', step_index:0, result}]
    });
    await vm.runInContext("poll('" + run.id + "',0)", s.context);
    const card = s.get('trace').children[0];
    const target = '/network/agents?embed=1&workspace=service#' + card.id;
    assert.equal(s.get('host-context').textContent, '低空服务管理');
    assert(s.get('steps').children[0].children.some(el => el.href === target));
    assert(s.get('answer').children.some(el => el.href === target));
    assert(card.children.some(el => el.textContent.includes('0 条规划任务')));
    assert.equal(s.get('review-card').hidden, true);
    await vm.runInContext("poll('" + run.id + "',0)", s.context);
    assert.equal(s.get('trace').children[0], card);
});

test('opening a legacy record clears previous evidence without inventing steps', async () => {
    const s = setup();
    await ready();
    s.state.run.trace = [{tool:'scene_snapshot', result:{evidence_id:'ev-first'}}];
    await vm.runInContext("poll('first',0)", s.context);
    s.state.run = {id:'b'.repeat(32),status:'interrupted',stage:'服务重启，任务已中断',trace:[]};
    await vm.runInContext("poll('second',0)", s.context);
    assert.match(s.get('stage').textContent, /中断/);
    assert.match(s.get('steps').children[0].textContent, /未保存逐步时间/);
    assert.equal(s.get('trace').children.length, 0);
    assert.equal(s.calls.filter(c => c.options.method === 'POST').length, 0);
});

test('empty input stays in the composer and makes no request', async () => {
    const s = setup();
    await ready();
    s.get('question').value = '   ';
    await s.get('run').onclick();
    assert(s.get('question').focused);
    assert.equal(s.calls.filter(c => c.options.method === 'POST').length, 0);
});


test('management opens precisely the chosen archive even when another run is active', async () => {
    const chosen = 'd'.repeat(32), active = 'e'.repeat(32);
    const s = setup('/network/agents', '?run=' + chosen, [active]);
    await ready();
    const details = s.calls.filter(c => /\/runs\/[0-9a-f]{32}$/.test(c.url));
    assert.deepEqual(details.map(c => c.url), ['/network/api/research-agent/runs/' + chosen]);
    assert.equal(s.calls.filter(c => c.options.method === 'POST').length, 0);
});

test('a management workflow link prepares a draft and an invalid run link stays local', async () => {
    const active = ['d'.repeat(32)];
    const draft = setup('/agents', '?workflow=research', active);
    const invalid = setup('/agents', '?run=../other', active);
    await ready();
    assert.equal(draft.get('workflow').value, 'research');
    assert.equal(draft.get('task-label').hidden, false);
    assert.match(invalid.get('stage').textContent, /编号无效/);
    for (const s of [draft, invalid]) {
        assert.equal(s.calls.filter(c => c.options.method === 'POST' || c.url.includes('/runs/')).length, 0);
    }
});
