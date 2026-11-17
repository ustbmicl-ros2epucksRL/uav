const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function setup(){
 const elements={},calls=[],views=[],classes=new Set(),buttons=['arm','land'].map(command=>({dataset:{command}}));
 class Element{
  constructor(){this.style={};this.children=[];this.textContent='';this.value='';this.checked=true;this.disabled=false;this.hidden=false;}
  replaceChildren(){this.children=[]}append(child){this.children.push(child)}removeAttribute(name){delete this[name]}
  getContext(){return new Proxy({},{get:()=>()=>{}})}
 }
 const $=id=>elements[id]??=new Element();
 $('actor').value='测试值班员';$('resolution').value='确认演示目标';
 const layer=()=>({addTo(){return this},bindTooltip(){return this},on(){return this},clearLayers(){},setLatLng(p){this.point=p;return this},getLatLng(){return this.point}});
 let resized=0;
 const map={setView(p,z){views.push([p,z]);return this},fitBounds(){},invalidateSize(){resized++},removeLayer(){}};
 const telemetry={latitude:39.05,longitude:115.98,roll:0,pitch:0,yaw:10,altitude:180,speed:0,battery:80,mode:'LOITER',armed:true,landed_state:2};
 const source={sitl:{connected:true,running:true,telemetry,anchor:{latitude:39.05,longitude:115.98},updated_at:Object.fromEntries(Object.keys(telemetry).map(k=>[k,Date.now()/1000]))},job:{status:'idle',events:[],alerts:[]}};
 const fetch=async(path,options)=>{
  calls.push({path,options});let data={};
  if(path==='/api/sitl')data=source.sitl;
  else if(path==='/api/sitl-demo')data=source.job;
  else if(path==='/api/sitl-demo/catalog')data={areas:[{id:'west',name:'西侧园区',polygon:[[39,115],[39.1,115.1]]},{id:'east',name:'东侧园区',polygon:[[39.2,115.2],[39.3,115.3]]}],aircraft:[{id:'SITL-1',name:'SITL 1',area_ids:['west','east']}],zones:[]};
  else if(path==='/api/sitl-demo/start'){source.job={id:'run',status:'running',phase:'starting',events:[],alerts:[]};data=source.job;}
  else if(path==='/api/test-video/status')data={available:true,url:'/api/test-video/stream'};
  else if(path.endsWith('/result'))data={status:'completed',result:{track:[{...telemetry,at:1}]}};
  else data={status:'acknowledged',mav_result:0};
  return {ok:true,json:async()=>data};
 };
 const context=vm.createContext({document:{getElementById:$,createElement:()=>new Element(),querySelectorAll:()=>buttons,addEventListener(){},body:{classList:{toggle(k,value){const on=value??!classes.has(k);on?classes.add(k):classes.delete(k);return on},add(k){classes.add(k)},remove(k){classes.delete(k)},contains:k=>classes.has(k)}}},window:{addEventListener(){}},L:{map:()=>map,tileLayer:layer,layerGroup:layer,circleMarker:(p)=>{const l=layer();l.point=p;return l},polyline:layer,polygon:layer},fetch,setTimeout,clearTimeout,setInterval:()=>1,clearInterval(){},AbortController,crypto:{randomUUID:()=> 'command'},Date,Math,Number,Error,Promise,JSON,horizon(){},console});
 vm.runInContext(fs.readFileSync(__dirname+'/../app/static/workbench.js','utf8'),context);
 return {context,elements,calls,views,buttons,source,classes,resized:()=>resized};
}
async function ready(s){await new Promise(resolve=>setImmediate(resolve));await vm.runInContext('refresh()',s.context);}
test('one click starts demo and video through their real API contracts',async()=>{const s=setup();s.source.sitl.telemetry.armed=false;await ready(s);await s.elements['demo-start'].onclick();const r=s.calls.find(c=>c.path==='/api/sitl-demo/start');assert.deepEqual(JSON.parse(r.options.body),{auto_resolve:true,area_id:'west',aircraft_id:'SITL-1'});assert.match(s.elements.video.src,/\/api\/test-video\/stream/);});
test('alert click locates recorded position and displays evidence without commands',async()=>{const s=setup();s.source.job={id:'run',status:'awaiting_resolution',phase:'review',events:[],alerts:[{id:'a',title:'演示目标',status:'open',at:1,telemetry:s.source.sitl.telemetry}]};await ready(s);s.elements.alerts.children[0].onclick();assert.equal(s.elements.evidence.hidden,false);assert.equal(s.views.at(-1)[1],17);assert.equal(s.calls.filter(c=>c.path==='/api/sitl/commands').length,0);await s.elements.resolve.onclick();assert(s.calls.some(c=>c.path==='/api/sitl-demo/run/alerts/a/resolve'));});
test('result replay locks controls and never sends aircraft commands',async()=>{const s=setup();s.source.job={id:'run',status:'completed',events:[],alerts:[]};await ready(s);await s.elements.result.onclick();assert(s.classes.has('historical'));assert(s.buttons.every(b=>b.disabled));await s.buttons[0].onclick();assert(!s.calls.some(c=>c.path==='/api/sitl/commands'));s.elements['replay-exit'].onclick();assert(!s.classes.has('historical'));});
test('map expand resizes map and toggle restores layout',async()=>{const s=setup();await ready(s);s.elements['map-expand'].onclick();assert(s.classes.has('map-expanded'));s.elements['map-expand'].onclick();assert(!s.classes.has('map-expanded'));assert.equal(s.resized(),2);});

test('cancel remains available while another action is waiting and sends request',async()=>{
 const s=setup();s.source.job={id:'run',status:'running',phase:'patrolling',events:[],alerts:[]};await ready(s);
 vm.runInContext('wb.busy=true;controls()',s.context);
 assert.equal(s.elements['demo-cancel'].disabled,false);
 await s.elements['demo-cancel'].onclick();
 assert(s.calls.some(c=>c.path==='/api/sitl-demo/cancel'));
});
test('failed mission clearly explains why cancel is unavailable',async()=>{
 const s=setup();s.source.job={id:'run',status:'failed',events:[{at:1,text:'航线与禁飞区 TEMP-BARRIER 相交'}],alerts:[]};await ready(s);
 assert.match(s.elements.stage.textContent,/任务已失败/);
 assert.match(s.elements.stage.textContent,/TEMP-BARRIER/);
 assert.equal(s.elements['demo-cancel'].disabled,true);
 assert.match(s.elements['demo-cancel'].title,/没有运行/);
});
test('interrupted task requires observed stable hover before next-action confirmation',async()=>{
 const s=setup();s.source.job={id:'run',status:'interrupted',phase:'interrupted',events:[],alerts:[]};s.source.sitl.telemetry.climb=0;await ready(s);
 assert.match(s.elements['aircraft-state'].textContent,/已悬停/);assert.match(s.elements['next-action'].textContent,/选择返航或降落/);
 s.source.sitl.telemetry.speed=4;await ready(s);assert.match(s.elements['next-action'].textContent,/尚未确认/);
 s.source.sitl.telemetry.armed=false;s.source.sitl.telemetry.landed_state=1;await ready(s);assert.match(s.elements['next-action'].textContent,/已落地上锁/);
});
test('replay guidance distinguishes historical instruments from live aircraft and clears on exit',async()=>{
 const s=setup();s.source.job={id:'run',status:'completed',phase:'completed',events:[],alerts:[]};await ready(s);await s.elements.result.onclick();
 assert.match(s.elements['display-state'].textContent,/历史回放/);assert.match(s.elements['next-action'].textContent,/返回实时/);
 s.elements['replay-exit'].onclick();assert.equal(s.elements['display-state'].textContent,'实时工作台');
 s.source.sitl.updated_at.mode=0;await ready(s);assert.equal(s.elements['aircraft-state'].textContent,'遥测待更新');assert.match(s.elements['next-action'].textContent,/等待有效遥测/);
});
