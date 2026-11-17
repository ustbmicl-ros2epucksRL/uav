const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const script=fs.readFileSync(__dirname+'/../app/static/planning-evidence.js','utf8');
async function setup(external=false){
 const elements={},calls=[];
 class Element{constructor(){this.value='';this.textContent='';}replaceChildren(...children){this.children=children;this.value=children[0]?.value||'';}}
 const get=id=>elements[id]??=new Element();
 get('vehicle-select').value='UAV-01';get('sensing-run-note').value='仿真验证';
 const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element()},window:{externalMode:external},location:{search:'?assessment=A1'},URLSearchParams,fetch:async(url,options)=>{
  calls.push({url,options});return {ok:true,json:async()=>url.endsWith('/plans')?[{id:'A1',assessment_job_id:'A1',task_id:'T1',task_name:'规划任务',selected:[]}]:{id:'F1',track:{count:11}}};
 }});
 vm.runInContext(script,context);await new Promise(resolve=>setImmediate(resolve));return {get,calls};
}
test('ground station saves selected plan identity and current local vehicle',async()=>{
 const {get,calls}=await setup();
 await get('sensing-link-trace').onclick({currentTarget:get('sensing-link-trace')});
 assert.deepEqual(JSON.parse(calls.at(-1).options.body),{assessment_id:'A1',note:'仿真验证'});
 assert.equal(calls.at(-1).url,'/api/vehicles/UAV-01/planning-evidence');
 assert.match(get('sensing-link-status').textContent,/11 个仿真航迹点/);
 assert.match(get('sensing-assessment-link').href,/task=T1&assessment=A1/);
 assert(!calls.some(call=>/command|mission|simulation/.test(call.url)));
});
test('external telemetry cannot be labeled as the local simulator trace',async()=>{
 const {get,calls}=await setup(true);
 await get('sensing-link-trace').onclick({currentTarget:get('sensing-link-trace')});
 assert.equal(calls.length,1);
 assert.match(get('sensing-link-status').textContent,/外部飞控记录/);
 assert.equal(get('sensing-link-trace').disabled,false);
});
