const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/acceptance.js'),'utf8');
const settle=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
async function setup(prefix='', stale=false, precision=false, low=false){
 const elements={},calls=[],maps=[];
 const get=id=>elements[id]??={value:'',hidden:false,disabled:false,innerHTML:'',textContent:''};
 for(const [id,value] of Object.entries({'wide-cell':'10','focus-cell':'2',heights:'80,125,170',azimuths:'0,90,180,270',bounds:'0,2000,0,2000,30,300',optimize:'true',variant:'after',field:'3',view:'3d'}))get(id).value=value;
 const task={id:'T1',name:'基准任务',scene:'urban',revision:1,budget:50,coverage_target:.9,focus_target:.95,precision_rate_target:.8,focus:{}};
 const stats={coverage:.89,focus_coverage:.99,precision_pass_rate:.7,valid_volume_m3:1000};
 const result={simulation_kpi_pass:false,feasible:false,scope:'合成模型',stats,before:{stats,cloud:[]},after:{stats,cloud:[]},scene:{devices:[],buildings:[]},added_cost:48,selected:[],evaluated_cells:100,aggregation:'体积加权',precision_threshold_m:30,variant_count:20,regions:[],search_history:[],limitations:[],settings:{bounds:[0,2000,0,2000,30,300],wide_cell:10,focus_cell:2}};
 if(precision){
  const bottleneck={fractions:{blind:.1,unobservable:.05,geometry:.05},altitude:[{band:'30–100 m',precise:.8,blind:.1,geometry:.05}]};
  result.after.bottleneck=bottleneck;result.before.bottleneck=bottleneck;
  result.reference={stats:{...stats,precision_pass_rate:.67},cloud:[[1,2,3,.8,12,25,10]],selected:[{id:'old',type:'EO'}],added_cost:42,job_id:'old-job',bottleneck};
  result.stats={...stats,precision_pass_rate:.81};
 }
 if(low){
  result.scene.devices=[{id:'existing',type:'EO',azimuth:0,pitch:10,z:115}];
  result.updated_devices=[{id:'existing',type:'EO',azimuth:90,pitch:-15,z:115}];
  result.low_altitude={target:.8,rate:.82,passed:true};result.settings.strategy='precision';
  result.after.cloud=[[1,2,50,.9,12,20,10],[1,2,150,.9,12,20,10]];
  result.reference.cloud=result.after.cloud;
  result.cost_breakdown={procurement_and_sites:48,retuning_reserve:2,basis:'预留待核价'};
  result.search_alternatives=[{selected:[{type:'EO'},{type:'RF'}],low_site_ids:['LOW-1'],added_cost:20.5,rates:{coverage:.85,focus_coverage:.96,precision_pass_rate:.72,low_altitude_rate:.76},existing_constraints_pass:false}];
 }
 const job={id:'J1',status:'succeeded',stale,snapshot:{task,scene:{revision:1},provenance:{source_sha256:'hash'}},result,evidence:{trials:[],runs:[],flight_records:[],field_status:'待核验'},indicators:[{id:'5.3',requirement:'全域覆盖 ≥90%',value:.89,status:'simulation_fail',research:3,pdf_page:19}]};
 const context=vm.createContext({window:{addEventListener(){}},document:{getElementById:get,querySelectorAll:selector=>selector==='input[name=model]:checked'?[{value:'EO'}]:[]},location:{pathname:prefix+'/acceptance',search:'',href:'http://localhost'+prefix+'/acceptance'},localStorage:{getItem(){return null;},setItem(){}},history:{replaceState(){}},URL,URLSearchParams,structuredClone,setTimeout:()=>1,clearTimeout(){},console,PlanningMap:class{constructor(_,options){this.options=options;maps.push(this);}destroy(){}update(options){Object.assign(this.options,options);}},fetch:async(url,options)=>{
  calls.push({url,options});
  let data={};
  if(url.endsWith('/workflow/tasks'))data=[task];
  else if(url.includes('/workflow/resources/'))data={models:[{id:'EO',type:'EO',name:'光电'}]};
  else if(url.endsWith('/assessments'))data=options.method==='POST'?{id:'J1'}:[{id:'J1',status:'succeeded',created_at:'2026-11-16'}];
  else if(url.endsWith('/assessments/J1'))data=job;
  return {ok:true,json:async()=>data};
 }});
 vm.runInContext(script,context);
 await settle();
 return {get,calls,context,maps,job};
}
test('assessment displays numerical failure and preserves mounted links',async()=>{
 for(const prefix of ['', '/network']){
  const {get,calls}=await setup(prefix);
  assert.equal(get('result').hidden,false);
  assert.match(get('conclusion').textContent,/存在缺口/);
  assert.match(get('indicators').innerHTML,/89.00%/);
  assert.match(get('indicators').innerHTML,/本次仿真未通过/);
  assert.match(get('research-links').innerHTML,new RegExp(prefix+'/research/1\\?task=T1&assessment=J1'));
  assert(calls.every(call=>call.url.startsWith(prefix+'/api/')));
 }
});
test('stale evidence disables publication while keeping historical visualization',async()=>{
 const {get,maps}=await setup('',true);
 assert.equal(get('stale').hidden,false);
 assert.equal(get('publish').disabled,true);
 get('variant').value='before';get('field').value='5';get('field').onchange();
 assert.equal(maps.at(-1).field,5);
 assert.match(get('legend').textContent,/理论界/);
});
test('submission sends exact selected model and numeric grid configuration',async()=>{
 const {get,calls}=await setup('/network');
 get('assessment-form').onsubmit({preventDefault(){}});
 await settle();
 const sent=calls.find(call=>call.url.endsWith('/tasks/T1/assessments')&&call.options.method==='POST');
 assert.deepEqual(JSON.parse(sent.options.body),{wide_cell:10,focus_cell:2,bounds:[0,2000,0,2000,30,300],heights:[80,125,170],azimuths:[0,90,180,270],model_ids:['EO'],optimize:true});
 get('heights').value='80,,170';
 const count=calls.length;
 get('assessment-form').onsubmit({preventDefault(){}});await settle();
 assert.match(get('status').textContent,/逗号分隔/);
 assert.equal(calls.length,count);
});

 test('precision comparison switches actual reference cloud and submits method',async()=>{
 const {get,calls,maps}=await setup('/network',false,true);
 assert.equal(get('precision-analysis').hidden,false);
 assert.match(get('precision-comparison').innerHTML,/67.00%/);
 assert.match(get('precision-comparison').innerHTML,/81.00%/);
 assert.match(get('precision-bottleneck').innerHTML,/30–100 m/);
 get('variant').value='reference';get('variant').onchange();
 assert.equal(maps.at(-1).options.selected[0].id,'old');
 assert.equal(maps.at(-1).options.cloud[0][5],25);
 get('strategy').value='precision';get('reference').value='abcdef';
 get('assessment-form').onsubmit({preventDefault(){}});await settle();
 const sent=calls.find(call=>call.url.endsWith('/tasks/T1/assessments')&&call.options.method==='POST');
 assert.equal(JSON.parse(sent.options.body).strategy,'precision');
 assert.equal(JSON.parse(sent.options.body).reference_job_id,'abcdef');
 });

test('low layer filters actual points, applies steering, and sends reserve',async()=>{
 const {get,maps,calls}=await setup('/network',false,true,true);
 assert.equal(get('altitude-layer').value,'low');
 assert.equal(maps.at(-1).options.cloud.length,1);
 assert.equal(maps.at(-1).options.scene.devices[0].azimuth,90);
 assert.match(get('precision-comparison').innerHTML,/转向调试预留 2/);
 assert.match(get('precision-comparison').innerHTML,/EO × 1 \+ RF × 1/);
 assert.match(get('precision-comparison').innerHTML,/分层搜索样本/);
 assert.match(get('metrics').innerHTML,/82.00%/);
 get('variant').value='reference';get('variant').onchange();
 assert.equal(maps.at(-1).options.scene.devices[0].azimuth,0);
 get('altitude-layer').value='all';get('altitude-layer').onchange();
 assert.equal(maps.at(-1).options.cloud.length,2);
 get('objective').value='low';get('strategy').value='precision';get('low-target').value='80';
 get('retune-existing').checked=true;get('retuning-reserve').value='2';
 get('assessment-form').onsubmit({preventDefault(){}});await settle();
 const sent=calls.find(call=>call.url.endsWith('/tasks/T1/assessments')&&call.options.method==='POST');
 const body=JSON.parse(sent.options.body);
 assert.equal(body.low_altitude_target,.8);assert.equal(body.retune_existing,true);assert.equal(body.retuning_reserve,2);
});

test('calculation records show readable versions and keep identifiers in details',async()=>{
 const {get,context}=await setup('/network',false,true,true);
 const label=vm.runInContext("recordName({version:4,title:'低位站址与设备组合优化'})",context);
 assert.equal(label,'第4版 · 低位站址与设备组合优化');
 const outcome=vm.runInContext("recordOutcome({status:'succeeded',low_altitude:{rate:.76,passed:false}})",context);
 assert.equal(outcome,'低空 76.00% · 尚有缺口');
 assert.match(get('job-status').textContent,/低空 82.00%/);
 assert.doesNotMatch(get('job-status').textContent,/J1/);
 assert.match(get('job-details').textContent,/记录编号 J1/);
 assert.match(get('job-context').textContent,/本地时间/);
});

test('validation progress groups actual indicators without claiming field completion',async()=>{
 const {get}=await setup('/network',true);
 assert.match(get('validation-progress').innerHTML,/未通过 1 项/);
 assert.match(get('validation-progress').innerHTML,/历史输入，需重新验证/);
 assert.match(get('validation-progress').innerHTML,/research\/3\?task=T1/);
 assert.doesNotMatch(get('indicators').innerHTML,/申请书|申报书/);
});

test('task reports render backend methods results and pending tests safely',async()=>{
 const {get,context,job}=await setup('/network');
 job.task_validation=[{id:2,name:'协同精度',stale:false,counts:{simulation_pass:0,simulation_fail:1,pending_evidence:1},method:'逐体素复核',results:['低空 73.34%','<不可信标签>'],failed_requirements:['低空达到80%'],next_tests:['检验随机漏检'],pending_requirements:['设备标定']}];
 context.reportJob=job;
 vm.runInContext('renderValidationProgress(reportJob)',context);
 assert.match(get('validation-progress').innerHTML,/逐体素复核/);
 assert.match(get('validation-progress').innerHTML,/低空 73.34%/);
 assert.match(get('validation-progress').innerHTML,/检验随机漏检/);
 assert.match(get('validation-progress').innerHTML,/&lt;不可信标签&gt;/);
 assert.match(get('validation-progress').innerHTML,/待完成证据 1 项/);
});
test('reference verification sends explicit mode and requires saved reference',async()=>{
 const {get,calls}=await setup('/network');
 get('strategy').value='precision';get('optimize').value='reference';
 get('reference').value='abcdef';
 get('assessment-form').onsubmit({preventDefault(){}});await settle();
 const sent=calls.find(call=>call.url.endsWith('/tasks/T1/assessments')&&call.options.method==='POST');
 assert.equal(JSON.parse(sent.options.body).verify_reference,true);
 assert.equal(JSON.parse(sent.options.body).optimize,false);
 get('reference').value='';
 get('assessment-form').onsubmit({preventDefault(){}});await settle();
 assert.match(get('status').textContent,/请选择待复核/);
});
test('fixed reference verification compares the deployment against the original base',async()=>{
 const {get,context,job}=await setup('/network',false,true,true);
 job.result.settings.verify_reference=true;
 context.verifiedJob=job;
 vm.runInContext('renderResult(verifiedJob)',context);
 assert.match(get('variant').innerHTML,/本次复核方案/);
 assert.match(get('precision-comparison').innerHTML,/已有基础部署/);
 assert.match(get('precision-comparison').innerHTML,/所选方案复核/);
 assert.match(get('precision-comparison').innerHTML,/70.00%/);
 assert.doesNotMatch(get('precision-comparison').innerHTML,/67.00%/);
});
