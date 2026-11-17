const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(pathname='/network/agents') {
    const elements={}, calls=[], saved=new Map(), timers=[];
    class Element {
        constructor(){ this.value=''; this.children=[]; this.disabled=false; this.hidden=false; this.textContent=''; }
        append(...children){this.children.push(...children);}
        replaceChildren(...children){this.children=children;}
        click(){}
    }
    const get=id=>elements[id]??=new Element();
    for(const [id,value] of Object.entries({workflow:'platform',scene:'corridor',task:'1',mode:'review',question:'检查廊道'}))get(id).value=value;
    const state={run:{id:'a'.repeat(32),status:'completed',stage:'平台诊断完成',trace:[],report:{conclusion:'缺少验算',evidence:['无记录'],evidence_ids:['ev-1'],limitations:['非现场'],next_steps:[]}}};
    const fetch=async (url,options={})=>{
        calls.push({url,options});let data;
        if(url.endsWith('/status'))data={available:false,model:'local',provider:'Ollama',active_runs:[]};
        else if(url.endsWith('/tools'))data={tools:[{name:'scene_snapshot',description:'配置快照'}]};
        else if(url.endsWith('/runs')&&options.method==='POST')data={...state.run,status:'running',stage:'选择工具'};
        else if(url.endsWith('/runs'))data={runs:[{...state.run,request:{question:'<img onerror=alert(1)>'},created_at:'2026-11-16'}]};
        else data=state.run;
        return {ok:true,json:async()=>data};
    };
    const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element()},location:{pathname},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},fetch,console,URL,Blob,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){}});
    vm.runInContext(fs.readFileSync(__dirname+'/../static/research-agent.js','utf8'),context);
    return {get,calls,state,context,saved};
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
