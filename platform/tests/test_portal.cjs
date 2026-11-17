// Workflow page runtime tests using a small DOM mock. Visual browser QA is separate.
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/portal.js'),'utf8');
const task={id:'T1',name:'园区任务',scene:'urban',revision:1,budget:50,coverage_target:.9,focus_target:.95,precision_rate_target:.8,volume:{bounds:[700,900,700,900,120,160],cell:10,precision_m:30,pd_threshold:.5,cochannel_db:3,communication_db:2,weather_db:1}};
async function setup(page='tasks',prefix=''){
 const elements={},calls=[];
 const get=id=>elements[id]||(elements[id]=new Element(id));
 class Element{
  constructor(id){this.id=id;this.value='';this.textContent='';this.disabled=false;this._html='';}
  set innerHTML(html){this._html=html;for(const m of html.matchAll(/<(?:input|select|button|canvas|div|section|p)\b[^>]*\bid="([^"]+)"[^>]*>/g)){const node=get(m[1]);node.value=m[0].match(/\bvalue="([^"]*)"/)?.[1]||'';}for(const m of html.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)){const options=[...m[2].matchAll(/<option value="([^"]*)"([^>]*)>/g)];get(m[1]).value=(options.find(o=>o[2].includes('selected'))||options[0])?.[1]||'';}}
  get innerHTML(){return this._html;}
  getContext(){return {fillRect(){}};}
  click(){}
  append(){}
  scrollIntoView(){}
 }
 const stats={coverage:.8,focus_coverage:.9,precision_pass_rate:.7,valid_cells:100};
 const plan={id:'P1',task_id:'T1',created_at:'2026-10-07T00:00:00',status:'evaluated',stats,before:stats,selected:[],feasible:true,added_cost:0,evaluated_subsets:1,method:'子集比较',scope:'局部',fingerprint:'F'};
 const resources={revision:1,source:'演示',sites:[{id:'S1',name:'站址',x:10,y:20,min_height:1,max_height:100,capacity:2}],devices:[],candidates:[]};
 const context=vm.createContext({window:{addEventListener(){}},PlanningMap:class {constructor(){this.options={};}draw(){}destroy(){}update(){}},location:{pathname:prefix+'/'+page,search:''},document:{getElementById:get,querySelectorAll:()=>[],createElement:()=>new Element('a')},localStorage:{getItem:()=>null,setItem(){}},URL,URLSearchParams,Blob,setTimeout,console,
 fetch:async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>{if(url.endsWith('/api/scenes'))return {scenes:[{id:'urban',name:'园区'}]};if(url.endsWith('/workflow/tasks'))return options.method==='POST'?task:[task];if(url.includes('/workflow/resources/'))return resources;if(url.endsWith('/diagnosis')||url.endsWith('/route'))return null;if(url.endsWith('/diagnose'))return {stats,cloud:[[750,750,130,.8]],settings:task.volume,scene:{},scope:'局部',assumptions:['合成']};if(url.endsWith('/plan'))return plan;if(url.endsWith('/workflow/plans'))return [plan];if(url.endsWith('/workflow/plans/P1'))return plan;return {};}}}});
 vm.runInContext(script,context);for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r));return {get,calls,context};
}
test('all five pages initialize at standalone and mounted URLs',async()=>{for(const prefix of ['', '/network'])for(const page of ['tasks','resources','diagnosis','planning','plans']){const {get,calls}=await setup(page,prefix);assert.equal(get('status').textContent,'已完成',page);assert.match(get('navigation').innerHTML,new RegExp(prefix+'/tasks'));assert(calls.every(c=>c.url.startsWith(prefix+'/api/')));}});
test('task form sends numeric bounds and task constraints',async()=>{const {get,calls}=await setup();get('name').value='新任务';get('task-form').onsubmit({preventDefault(){}});for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));const call=calls.find(c=>c.options.method==='POST');const body=JSON.parse(call.options.body);assert.equal(body.name,'新任务');assert.deepEqual(body.volume.bounds,[700,900,700,900,120,160]);assert.equal(body.budget,50);});
test('diagnosis and planning invoke selected task and render results',async()=>{const d=await setup('diagnosis');await d.get('diagnose').onclick();assert.match(d.get('diagnosis-result').innerHTML,/80.0%/);const p=await setup('planning','/network');await p.get('generate').onclick();assert(p.calls.some(c=>c.url==='/network/api/workflow/tasks/T1/plan'));assert.match(p.get('plan-result').innerHTML,/\/network\/plans\?plan=P1/);});
test('resource form rejects impossible mounting height before staging',async()=>{const {get,calls}=await setup('resources');get('dz').value='150';get('device-form').onsubmit({preventDefault(){}});for(let i=0;i<3;i++)await new Promise(r=>setImmediate(r));assert.match(get('status').textContent,/挂高超出/);assert(!calls.some(c=>c.options.method==='PUT'));});
