const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/api-client.js'),'utf8');
function setup(fetch,pathname='/network/workbench'){
 const context=vm.createContext({window:{location:{pathname}},fetch,setTimeout:fn=>fn()});
 vm.runInContext(script,context);return context.window.SenseAPI;
}
test('mounted polling tracks queue and running phases before returning result',async()=>{
 const calls=[],updates=[],states=[{id:'J',status:'running'},{id:'J',status:'succeeded',result:{cells:4}}];
 const api=setup(async url=>{calls.push(url);return {ok:true,json:async()=>states.shift()};});
 const result=await api.wait({id:'J',status:'queued'},job=>updates.push(job.status));
 assert.deepEqual(updates,['queued','running','succeeded']);assert.equal(result.result.cells,4);
 assert.deepEqual(calls,['/network/api/jobs/J','/network/api/jobs/J']);
});
test('cancelled computations stop polling and expose cancellation',async()=>{
 let called=false;const api=setup(async()=>{called=true;});
 await assert.rejects(api.wait({id:'J',status:'cancelled'}),/取消/);assert.equal(called,false);
});
test('HTTP errors include request correlation and malformed responses fail clearly',async()=>{
 const api=setup(async()=>({ok:false,status:409,headers:{get:()=> 'RID'},json:async()=>({detail:'场景已改变'})}),'/workbench');
 assert.equal(api.root,'');await assert.rejects(api.post('/api/jobs/J/publish'),/场景已改变.*RID/);
 const invalid=setup(async()=>({status:502,json:async()=>{throw Error('html');}}));
 await assert.rejects(invalid.request('/api/jobs'),/HTTP 502/);
});
