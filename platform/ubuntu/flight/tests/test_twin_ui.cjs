const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function setup(){
 const elements={},calls=[],classes=new Set(),timers=[],instruments=[];let now=100000;class Clock extends Date{static now(){return now}};
 const $=id=>elements[id]??={value:id==='twin-record-list'?'':'simulation',replaceChildren(){this.children=[]},append(child){(this.children??=[]).push(child)},click(){return this.onclick?.()},textContent:'',hidden:false,disabled:false,paused:true,style:{},getContext:()=>new Proxy({},{get:()=>()=>{}}),pause(){this.paused=true},load(){},removeAttribute(k){delete this[k]}};
 const source={data:{vehicle_id:'MAV-42',connected:true,position_valid:true,latitude:39,longitude:116,telemetry:{roll_deg:0,pitch_deg:0,yaw_deg:20,relative_altitude_m:40,groundspeed_mps:2,battery_percent:80},track:[],video:{configured:false}}};
 const layer=()=>({addTo(){return this},clearLayers(){},bindTooltip(){return this}});
 const window={};const state={busy:false,scene3d:false};
 const c=vm.createContext({$,window,state,map:{setView(){}},L:{layerGroup:layer,polyline:layer,marker:layer,circleMarker:layer},document:{body:{classList:{toggle(k,v){v?classes.add(k):classes.delete(k)}}},createElement:()=>({dataset:{},setAttribute(k,v){this[k]=v}})},api:async path=>{calls.push(path);return path.includes('/records?')?(source.catalog||{items:[],total:0}):(path.endsWith('/patrol/result')||path.includes('/records/'))?source.result:source.data},arrow:()=>({}),Date:Clock,horizon(v,p,a){instruments.push({p,a})},setInterval(fn,ms){timers.push({fn,ms})},refreshSitl(){},stopReplay(){},switchView(){},message(){},renderTelemetry(){window.renderTwin()},renderMap(){window.renderTwin()},updateControls(){},Number});
 vm.runInContext(fs.readFileSync(__dirname+'/../app/static/twin.js','utf8'),c);return {$,c,window,source,calls,classes,timers,instruments,advance:ms=>{now+=ms}};
}
test('external source selection is read-only and missing video never uses test stream',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));assert.equal(s.window.externalMode,false);
 s.$('flight-source').value='external';s.$('flight-source').onchange();await new Promise(r=>setImmediate(r));
 assert(s.classes.has('external-mode'));assert.match(s.$('twin-title').textContent,/MAV-42/);assert.match(s.$('twin-video-note').textContent,/未接入/);assert.equal(s.$('twin-video').src,undefined);assert(s.calls.every(p=>p==='/api/twin'));
 s.source.data={...s.source.data,connected:false,position_valid:false,telemetry:{},track:[{latitude:39,longitude:116}]};await vm.runInContext('refreshTwin()',s.c);
 assert(s.window.externalMode);assert.match(s.$('twin-status').textContent,/不回退仿真/);assert(s.$('twin-locate').disabled);
 s.$('flight-source').value='simulation';s.$('flight-source').onchange();assert(!s.window.externalMode);
});
test('SITL selection requests only its adapter and drives the shared 3D scene',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));
 const snapshots=[];s.window.SitlTwin={project:d=>({...d,anchor:{latitude:39,longitude:116}}),scene:d=>({selected:d.vehicle_id})};s.window.GCS3D={update:d=>snapshots.push(d)};
 s.$('scene-mode').click=()=>{s.c.state.scene3d=true};
 s.source.data={...s.source.data,vehicle_id:'SITL-1'};s.$('flight-source').value='sitl';s.$('flight-source').onchange();await new Promise(r=>setImmediate(r));
 assert(s.window.sitlTwinMode);assert(s.classes.has('sitl-twin-mode'));assert(s.calls.includes('/api/sitl'));assert.equal(snapshots.at(-1).selected,'SITL-1');assert.match(s.$('twin-title').textContent,/仿真/);assert.match(s.$('twin-video-note').textContent,/未时间同步/);
 s.$('twin-test-toggle').onclick();assert.match(s.$('twin-test-video').src,/test-video\/stream/);s.$('twin-test-toggle').onclick();assert.equal(s.$('twin-test-video').src,undefined);
});
test('replay load, pause, seek and return-live keep instruments and 3D on one timeline without commands',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));const core=require('../app/static/gcs-core.js'),adapter=require('../app/static/sitl-twin.js'),scenes=[];
 s.window.SitlTwin=adapter;s.window.GCSCore=core;s.window.GCS3D={update:d=>scenes.push(d)};s.$('scene-mode').click=()=>{s.c.state.scene3d=true};
 const live={connected:true,anchor:{latitude:39,longitude:116},telemetry:{latitude:39,longitude:116,altitude:90,roll:9,pitch:8,yaw:7,speed:6,battery:80,gps_fix:6},updated_at:Object.fromEntries(['latitude','longitude','altitude','roll','pitch','yaw','speed','battery','gps_fix'].map(k=>[k,Date.now()/1000]))};
 s.source.data=live;s.source.result={anchor:live.anchor,track:[0,1,2].map(i=>({at:100+i,latitude:39+i*.001,longitude:116,altitude:10+i*10,roll:i,pitch:i*2,yaw:i*30,speed:i+1,battery:80,armed:true,field_age_s:{}}))};
 s.$('flight-source').value='sitl';s.$('flight-source').onchange();await new Promise(r=>setImmediate(r));await s.$('twin-record-load').onclick();assert(s.window.twinReplayActive);assert.equal(s.instruments.at(-1).p[2],10);assert.equal(scenes.at(-1).track.length,1);
 s.$('twin-record-play').onclick();s.advance(1000);s.timers.find(t=>t.ms===100).fn();assert.equal(s.instruments.at(-1).p[2],20);assert.equal(scenes.at(-1).vehicles[0].attitude.roll,1);
 s.$('twin-record-play').onclick();s.advance(3000);s.timers.find(t=>t.ms===100).fn();await vm.runInContext('refreshTwin()',s.c);assert.equal(s.instruments.at(-1).p[2],20);
 s.$('twin-record-time').value='2';s.$('twin-record-time').oninput();assert.equal(s.instruments.at(-1).p[2],30);assert.equal(scenes.at(-1).track.length,3);assert.match(s.$('twin-status').textContent,/历史回放/);
 s.$('twin-record-live').onclick();assert(!s.window.twinReplayActive);assert.equal(s.instruments.at(-1).p[2],90);assert(s.calls.every(p=>p.startsWith('/api/sitl/records?')||['/api/twin','/api/sitl','/api/sitl/patrol/result'].includes(p)));
});
test('late history response cannot reactivate replay after source change',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));s.window.sitlTwinMode=true;s.window.externalMode=true;s.window.SitlTwin=require('../app/static/sitl-twin.js');s.$('flight-source').value='sitl';
 let resolve;s.source.result=new Promise(r=>{resolve=r});const load=s.$('twin-record-load').onclick();assert(s.window.twinReplayActive);
 s.$('flight-source').value='simulation';s.$('flight-source').onchange();resolve({anchor:{latitude:39,longitude:116},track:[{at:100,latitude:39,longitude:116,altitude:10}]});await load;assert(!s.window.twinReplayActive);assert(s.$('twin-record-time').disabled);
});
test('selecting a saved task replays while SITL is offline and catalogue pages remain read-only',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));const adapter=require('../app/static/sitl-twin.js'),scenes=[];
 s.window.SitlTwin=adapter;s.window.GCSCore=require('../app/static/gcs-core.js');s.window.GCS3D={update:d=>scenes.push(d)};s.$('scene-mode').click=()=>{s.c.state.scene3d=true};
 s.source.data={connected:false,telemetry:{}};s.source.catalog={total:31,items:[{id:'saved-task',created_at:100,status:'completed',area_id:'east',samples:2}]};
 s.source.result={record_id:'saved-task',anchor:{latitude:40,longitude:117},track:[{at:100,latitude:40,longitude:117,altitude:40,roll:3,pitch:4,yaw:5,speed:6},{at:101,latitude:40.001,longitude:117,altitude:50,roll:4,pitch:5,yaw:6,speed:7}]};
 s.$('flight-source').value='sitl';s.$('flight-source').onchange();await new Promise(r=>setImmediate(r));assert.match(s.$('twin-record-list').children[1].textContent,/东侧园区/);assert(!s.$('twin-record-next').disabled);
 s.$('twin-record-list').value='saved-task';s.$('twin-record-list').onchange();await new Promise(r=>setImmediate(r));assert(s.window.twinReplayActive);assert(s.calls.includes('/api/sitl/records/saved-task'));assert.equal(scenes.at(-1).anchor.latitude,40);assert.equal(s.instruments.at(-1).a.roll,3);
 s.$('twin-record-time').value='1';s.$('twin-record-time').oninput();assert.equal(s.instruments.at(-1).p[2],50);assert.equal(scenes.at(-1).vehicles[0].attitude.yaw,6);
 s.$('twin-record-next').onclick();await new Promise(r=>setImmediate(r));assert(s.calls.includes('/api/sitl/records?limit=30&offset=30'));assert(s.window.twinReplayActive);
 s.$('twin-record-live').onclick();assert(!s.window.twinReplayActive);assert.match(s.$('twin-status').textContent,/失联/);
});
test('task-focused view never shows another execution or accepts another task archive',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));const scenes=[];
 s.window.SitlTwin=require('../app/static/sitl-twin.js');s.window.GCSCore=require('../app/static/gcs-core.js');s.window.GCS3D={update:d=>scenes.push(d)};s.$('scene-mode').click=()=>{s.c.state.scene3d=true};s.window.twinTaskContext='TASK-1';
 s.source.data={connected:true,anchor:{latitude:39,longitude:116},telemetry:{latitude:39,longitude:116,altitude:40,roll:2,pitch:3,yaw:4,speed:5,battery:80,gps_fix:6},updated_at:{},execution_binding:{task_id:'TASK-2',execution_id:'other'},compute_runtime:{runtime_id:'other'},patrol:{route:[[1,2,3]]}};
 s.$('flight-source').value='sitl';s.$('flight-source').onchange();await new Promise(r=>setImmediate(r));assert.equal(scenes.at(-1).vehicles.length,0);assert.equal(s.instruments.length,0);assert.equal(vm.runInContext('twinDisplay().runtime',s.c),null);assert.equal(vm.runInContext('twinDisplay().execution_id',s.c),undefined);assert.equal(scenes.at(-1).mission.length,0);
 s.source.result={business_task_id:'TASK-2',anchor:{latitude:39,longitude:116},track:[{at:100,latitude:39,longitude:116,altitude:40,roll:2,pitch:3,yaw:4,speed:5}]};
 await s.$('twin-record-load').onclick();assert(!s.window.twinReplayActive);assert.match(s.$('twin-record-note').textContent,/不匹配/);assert.equal(s.instruments.length,0);
});


test('archive event buttons seek one evidence cursor, pause, and clear on return live',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));
 s.window.SitlTwin=require('../app/static/sitl-twin.js');s.window.ComputeReplayEvents=require('../app/static/compute-replay-events.js');
 s.window.GCSCore=require('../app/static/gcs-core.js');const details=[];s.window.showComputeEvent=e=>details.push(e);
 const archived=JSON.parse(fs.readFileSync(process.env.COMPUTE_REPLAY_FLOW||__dirname+'/../../../../outputs/compute-demo/2026-11-13/sitl-flow.json','utf8')).result;
 s.source.result=archived;s.source.data={connected:false,telemetry:{}};
 s.$('flight-source').value='sitl';s.$('flight-source').onchange();await new Promise(r=>setImmediate(r));await s.$('twin-record-load').onclick();
 const buttons=s.$('twin-event-markers').children;assert(buttons.length>5);
 for(const category of ['normal','weak','disconnected','offline','recovery','migration']){
  const button=buttons.find(b=>b.dataset.category===category);assert(button,category);
  s.$('twin-record-play').onclick();button.onclick();
  const displayed=vm.runInContext('twinDisplay()',s.c),event=details.at(-1);
  assert.equal(displayed.cursor_at,event.seek_at);assert.equal(displayed.runtime.at,event.seek_at);assert.equal(s.$('twin-record-play').textContent,'播放');assert.equal(button['aria-pressed'],'true');
  if(['normal','weak','disconnected'].includes(category))assert.equal(displayed.runtime.network.profile,category);
  if(category==='offline')assert(displayed.runtime.nodes.some(n=>n.id===event.node&&n.offline));
 }
 const old=buttons[0];s.$('twin-record-live').onclick();assert(s.$('twin-event-panel').hidden);const count=details.length;old.onclick();assert.equal(details.length,count);assert(!s.window.twinReplayActive);
 assert(s.calls.every(p=>!p.includes('/commands')));
});
test('event filter and previous/next controls stay scoped and do not issue commands',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));
 s.window.SitlTwin=require('../app/static/sitl-twin.js');s.window.ComputeReplayEvents=require('../app/static/compute-replay-events.js');s.window.GCSCore=require('../app/static/gcs-core.js');
 const details=[];s.window.showComputeEvent=e=>details.push(e);
 s.source.result=JSON.parse(fs.readFileSync(process.env.COMPUTE_REPLAY_FLOW||__dirname+'/../../../../outputs/compute-demo/2026-11-13/sitl-flow.json','utf8')).result;
 s.$('flight-source').value='sitl';s.$('flight-source').onchange();await new Promise(r=>setImmediate(r));await s.$('twin-record-load').onclick();
 const filter=s.$('twin-event-filter');filter.value='migration';filter.onchange();
 const buttons=s.$('twin-event-markers').children.filter(b=>!b.hidden);assert(buttons.length>1);assert(buttons.every(b=>b.dataset.category==='migration'));
 buttons[0].onclick();assert(s.$('twin-event-prev').disabled);s.$('twin-event-next').onclick();assert.equal(details.at(-1).id,buttons[1].dataset.eventId);s.$('twin-event-prev').onclick();assert.equal(details.at(-1).id,buttons[0].dataset.eventId);
 buttons.at(-1).onclick();assert(s.$('twin-event-next').disabled);
 const cursor=vm.runInContext('twinDisplay().cursor_at',s.c);filter.value='missing';filter.onchange();assert(s.$('twin-event-prev').disabled);assert(s.$('twin-event-next').disabled);assert.equal(vm.runInContext('twinDisplay().cursor_at',s.c),cursor);
 s.$('twin-record-live').onclick();assert(s.$('twin-event-panel').hidden);assert(s.calls.every(p=>!p.includes('/commands')));
});
