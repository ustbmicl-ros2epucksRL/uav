const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../static/agent-assistant.js','utf8');
function setup({nested=false,existing=false,src='http://localhost:8772/network/static/agent-assistant.js',scene='corridor'}={}) {
    const elements=[],events={},body={children:[],append(e){this.children.push(e)}},self={};
    class Element {
        constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.handlers={};this.contentWindow={};this.srcWrites=0;elements.push(this);}
        append(...nodes){this.children.push(...nodes)}
        attachShadow(){this.shadow=new Element('shadow');return this.shadow}
        setAttribute(key,value){this.attributes[key]=value}
        addEventListener(key,fn){this.handlers[key]=fn}
        focus(){this.focused=true}
        set src(value){this.url=value;this.srcWrites++}get src(){return this.url}
    }
    const context=vm.createContext({window:{top:nested?{}:self,self,addEventListener:(key,fn)=>events[key]=fn},document:{currentScript:{src},body,getElementById:id=>id==='scene'?{value:scene}:existing?{}:null,createElement:tag=>new Element(tag)},URL});
    vm.runInContext(source,context);
    return {body,elements,events,context,frame:elements.find(e=>e.tag==='iframe'),launcher:elements.find(e=>e.className==='launcher'),panel:elements.find(e=>e.className==='panel'),close:elements.find(e=>e.attributes['aria-label']==='收起平台助手')};
}

test('assistant starts collapsed and reopening preserves its iframe and host content',()=>{
    const s=setup();assert.equal(s.body.children.length,1);assert.equal(s.panel.hidden,true);assert.equal(s.frame.src,undefined);
    s.launcher.onclick();const url=new URL(s.frame.src);assert.equal(url.pathname,'/network/agents');assert.equal(url.searchParams.get('scene'),'corridor');
    assert.equal(s.panel.hidden,false);s.close.onclick();assert.equal(s.panel.hidden,true);assert(s.launcher.focused);
    s.launcher.onclick();assert.equal(s.frame.srcWrites,1);assert.equal(s.body.children.length,1);
});

test('nested workspaces and duplicate scripts never create extra assistants',()=>{
    assert.equal(setup({nested:true}).body.children.length,0);assert.equal(setup({existing:true}).body.children.length,0);
});

test('standalone planning path works and unrecognized scene cannot enter the URL',()=>{
    const s=setup({src:'http://localhost:8767/static/agent-assistant.js',scene:'arbitrary'});s.launcher.onclick();
    const url=new URL(s.frame.src);assert.equal(url.pathname,'/agents');assert.equal(url.searchParams.get('scene'),null);
});

test('only expected iframe origin and source can dismiss or mark the assistant ready',()=>{
    const s=setup();s.launcher.onclick();
    s.events.message({origin:'http://evil.test',source:s.frame.contentWindow,data:{type:'platform-assistant-close'}});assert.equal(s.panel.hidden,false);
    s.events.message({origin:'http://localhost:8772',source:{},data:{type:'platform-assistant-close'}});assert.equal(s.panel.hidden,false);
    s.events.message({origin:'http://localhost:8772',source:s.frame.contentWindow,data:{type:'platform-assistant-ready'}});
    assert(s.elements.find(e=>e.className==='load-status').hidden);
    s.events.message({origin:'http://localhost:8772',source:s.frame.contentWindow,data:{type:'platform-assistant-close'}});assert.equal(s.panel.hidden,true);
});
