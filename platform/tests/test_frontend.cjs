// Code-level DOM mocks, not a substitute for visual browser inspection.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const {test}=require('node:test');
const path=require('node:path');
const fixture=JSON.parse(fs.readFileSync(process.env.SENSE_FRONTEND_REPORT||'/tmp/lowair-report.json','utf8'));
const script=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
class Element {
 constructor(){this.innerHTML='';this.textContent='';this.offsetWidth=800;this.value='';this.checked=false;this.handlers={};this.dataset={};this.classList={toggle(){}};this.elements=new Proxy({}, {get:(obj,key)=>obj[key]||(obj[key]=new Element())});}
 replaceChildren(...children){this.children=children;}
 addEventListener(event,handler){this.handlers[event]=handler;}
 getBoundingClientRect(){return {width:800,height:500,left:0,top:0};}
 getContext(){return new Proxy({}, {get:()=>()=>{}});}
 querySelector(){return new Element();}
 showModal(){this.open=true;}
 close(){this.open=false;}
}
async function setup(){
 const elements={},main=new Element(),timers=[];let clock=0;
 const get=id=>elements[id]||(elements[id]=new Element());
 get('speed').value='4';get('event-filter').value='all';get('altitude').value='175';
 get('show-observations').checked=true;
 const context=vm.createContext({location:{pathname:"/"},document:{getElementById:get,querySelectorAll:()=>[],querySelector:()=>main},window:{devicePixelRatio:1,addEventListener(){}},
  Option:class {constructor(text,value){this.textContent=text;this.value=value;}},performance:{now:()=>clock},setInterval:fn=>timers.push(fn),setTimeout,URL,Blob,console,
  fetch:async url=>({ok:true,json:async()=>{
   if(url==='/api/scenes')return {scenes:[fixture.scene]};
   if(url.endsWith('/coverage'))return structuredClone(fixture.coverage);
   if(url.includes('/run'))return structuredClone(fixture.run);
   return structuredClone(fixture.scene);
  }})});
 vm.runInContext(script,context);
 for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));
 return {context,elements,get,timers,tick(ms){clock+=ms;timers.forEach(fn=>fn());},main};
}
test('loads live-report fixture and renders maps, risk metrics, research and rule fields',async()=>{
 const {context,get,main}=await setup();
 assert.equal(vm.runInContext('state.time',context),40);
 assert.ok(get('risk-metrics').innerHTML.includes((fixture.run.metrics.observed_rate*100).toFixed(1)+'%'));
 assert.match(get('research-results').innerHTML,/平均提示时延/);
 assert.equal(Number(get('rules').elements.altitude_limit.value),185);
 assert.equal(main.inert,false);
 assert.doesNotMatch(get('notice').textContent,/失败/);
});
test('playback obeys rate and imported irregular frame times',async()=>{
 const {context,get,tick}=await setup();
 vm.runInContext('state.time=0;setPlaying(true)',context);
 tick(1000);assert.equal(vm.runInContext('state.time',context),4);
 vm.runInContext('setPlaying(false);state.time=0;state.run={frames:[{time:0,tracks:[],observations:[]},{time:10,tracks:[],observations:[]}],events:[],truth:[],metrics:null};state.imported=true;setPlaying(true)',context);
 tick(1000);assert.equal(vm.runInContext('state.time',context),0);
 tick(2000);assert.equal(vm.runInContext('state.time',context),1);
 assert.equal(vm.runInContext('state.playing',context),false);
});
test('imported observations show no truth-based metrics and enables separate replay event review',async()=>{
 const {context,get}=await setup();
 vm.runInContext('state.imported=true;state.run.metrics=null;render();renderResearch()',context);
 assert.match(get('risk-metrics').innerHTML,/无真值/);
 assert.match(get('research-results').innerHTML,/不能自动验证/);
 vm.runInContext('showEvent(state.run.events[0].id)',context);
 assert.equal(get('review-save').disabled,false);
});
test('plan summary includes cost, matched-sample error and risk comparison',async()=>{
 const {context,get}=await setup();
 context.planFixture={id:'DEMO',selected:[],added_cost:0,before:fixture.coverage.stats,after:fixture.coverage.stats,target_met:false,method:'测试基线',note:'测试输入'};
 context.compareFixture={before:fixture.run.metrics,after:fixture.run.metrics,single:fixture.run.metrics,common_samples:{samples:100,before_rmse_m:20,after_rmse_m:18},note:'相同任务'};
 vm.runInContext('state.plan=planFixture;state.comparison=compareFixture;renderPlan()',context);
 assert.match(get('plan-result').innerHTML,/共同匹配 100/);
 assert.match(get('plan-result').innerHTML,/20\.0 m 至 18\.0 m/);
 assert.match(get('plan-result').innerHTML,/平均提示时延/);
});
