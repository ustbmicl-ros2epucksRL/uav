// Logic tests with DOM/canvas mocks; no visual-layout assertion.
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/workbench.js'),'utf8');
async function setup(){
 const elements={},calls=[];let failure=false;
 function element(){return {value:'',disabled:false,textContent:'',clientWidth:800,files:[],replaceChildren(...items){this.children=items;if(items.length)this.value=items[0].value;},getContext(){return new Proxy({}, {get:()=>()=>{}})},setPointerCapture(){},click(){}};}
 const get=id=>elements[id]||(elements[id]=element());
 Object.entries({x0:700,x1:900,y0:700,y1:900,z0:120,z1:160,cell:10,cochannel_db:3,communication_db:2,weather_db:1,pd_threshold:.5,precision_m:30,budget:50,coverage_target:.9,focus_target:.95,precision_rate_target:.8,bandwidth:5,compute:5,field:3}).forEach(([k,v])=>get(k).value=String(v));
 const scene={id:'urban',devices:[{id:'R1',name:'雷达'}],buildings:[],candidates:[]};
 const context=vm.createContext({location:{pathname:"/"},document:{getElementById:get,createElement:element,querySelectorAll:()=>Object.values(elements)},window:{location:{pathname:'/'},addEventListener(){}},setTimeout,devicePixelRatio:1,URL,Blob,console,
  fetch:async(url,options={})=>{calls.push({url,options});return {ok:!failure,json:async()=>{
   if(failure)return {detail:'测试输入错误'};
   if(url==='/api/scenes')return {scenes:[{id:'urban',name:'建筑区'},{id:'corridor',name:'物流道路'}]};
   if(url.endsWith('/jobs')&&options.method==='POST')return {id:'J1',status:'succeeded'};
   if(url.endsWith('/publish')&&calls.some(c=>c.options.body&&JSON.parse(c.options.body).kind==='volume'))return {cloud:[[750,750,130,.8,12,null]],evaluated_cells:100};
   if(url.endsWith('/publish'))return {id:'P1',selected:[]};
   if(url.endsWith('/report'))return {project:'原项目'};
   return scene;
  }}}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/api-client.js'),'utf8'),context);
 vm.runInContext(script,context);for(let i=0;i<4;i++)await new Promise(r=>setImmediate(r));
 return {get,calls,context,setFailure(v){failure=v;}};
}
test('workbench initializes and sends exact volume controls',async()=>{
 const {get,calls}=await setup();assert.equal(get('scene').value,'urban');assert.equal(get('sensor').value,'R1');
 await get('volume').onclick();const call=calls.find(c=>c.url.endsWith('/jobs'));
 assert.deepEqual(JSON.parse(call.options.body).parameters.bounds,[700,900,700,900,120,160]);
 assert.match(get('volume-stats').textContent,/100/);assert.equal(get('scene').disabled,false);
});
test('plan can be applied once and scene reload invalidates local plan',async()=>{
 const {get,calls}=await setup();await get('plan').onclick();assert.equal(get('apply-plan').disabled,false);
 await get('apply-plan').onclick();assert(calls.some(c=>c.url.endsWith('/apply/P1')));assert.equal(get('apply-plan').disabled,true);
});
test('API rejection releases controls and reports error; resource input uses PUT',async()=>{
 const {get,calls,setFailure}=await setup();setFailure(true);await get('volume').onclick();
 assert.match(get('status').textContent,/测试输入错误/);assert.equal(get('volume').disabled,false);assert.equal(get('scene').disabled,false);
 setFailure(false);await get('read-resources').onclick();assert.equal(JSON.parse(get('resources').value).source,'合成演示场景');
 await get('save-resources').onclick();assert.equal(calls.find(c=>c.url.endsWith('/resources')).options.method,'PUT');
});
