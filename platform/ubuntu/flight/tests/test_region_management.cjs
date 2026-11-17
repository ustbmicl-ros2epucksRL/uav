const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function setup(){
 const elements={},calls=[],polygons=[],polygonLayers=[],markers=[];let fits=0;
 const $=id=>elements[id]??={value:'',textContent:'',disabled:false,children:[],replaceChildren(){this.children=[]},append(x){this.children.push(x)}};
 const layer=()=>({addTo(){return this},clearLayers(){},bindTooltip(content,options){this.tooltip={content,options};return this},bindPopup(content){this.popup=content;return this},on(event,fn){this.click=fn;return this}});
 const state={selected:'UAV-01',scene3d:false,replay:false,busy:false};
 const plane={id:'UAV-01',name:'巡检机',payload:'光电吊舱',armed:false};
 const catalog={areas:[{id:'west',name:'西侧',polygon:[[1,2],[3,4]]},{id:'east',name:'东侧',polygon:[[5,6],[7,8]]}],zones:[{id:'TEMP-BARRIER',polygon:[[1,2],[3,4]],z_min:0,z_max:300}],anchor:{}};
 const api=async(path,body)=>{calls.push({path,body});return path.endsWith('catalog')?catalog:{id:'task1'}};
 const window={currentPatrol:{assignments:[{vehicle_id:'UAV-01',area:{id:'east'},anchor:{},alerts:[{id:'a',position:[1,2,0]}]}]}};
 const c=vm.createContext({$,state,window,vehicle:()=>plane,api,map:{fitBounds(){fits++}},L:{layerGroup:layer,polygon(p){polygons.push(p);const l=layer();polygonLayers.push(l);return l},circleMarker(){const m=layer();markers.push(m);return m}},Core:{geo:p=>p},document:{createElement:()=>({children:[],append(x){this.children.push(x)}})},setInterval(){},renderPatrolMonitor(){},refreshPatrol:async()=>{},switchView:v=>{state.view=v},operation:async f=>f(),openPatrolAlert:(v,a)=>calls.push({alert:a,vehicle:v}),JSON,Error});
 vm.runInContext(fs.readFileSync(__dirname+'/../app/static/region-management.js','utf8'),c);
 return {c,$,calls,polygons,polygonLayers,markers,state,plane,fits:()=>fits};
}
test('original page region switch renders boundary/no-fly without flight commands',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));assert.equal(s.polygons.length,3);
 s.$('managed-area').value='east';s.$('managed-area').onchange();
 assert.equal(s.state.selected,'UAV-01');assert.equal(s.$('patrol-area').value,'east');assert(s.fits()>0);
 assert(!s.calls.some(c=>c.body));s.markers.at(-1).click();assert.equal(s.calls.at(-1).alert,'a');
});
test('create uses selected area/aircraft and does not arm or start',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));s.$('managed-area').value='east';s.$('managed-area').onchange();
 await s.$('managed-create').onclick();const posted=s.calls.filter(c=>c.body);
 assert.deepEqual(JSON.parse(JSON.stringify(posted)),[{path:'/api/park-patrol/UAV-01/assign',body:{area_id:'east',altitude:180}}]);
 assert.equal(s.state.view,'patrol');assert.match(s.$('managed-result').textContent,/尚未起飞/);
 s.plane.armed=true;vm.runInContext('renderManaged()',s.c);assert(s.$('managed-create').disabled);
});

test('no-fly label is permanent and popup explains name height and simulation purpose',async()=>{
 const s=setup();await new Promise(r=>setImmediate(r));const zone=s.polygonLayers.at(-1);
 assert.equal(zone.tooltip.content.textContent,'仿真禁飞区');assert.equal(zone.tooltip.options.permanent,true);
 const text=zone.popup.children.map(x=>x.textContent).join(' ');
 assert.match(text,/TEMP-BARRIER/);assert.match(text,/0–300/);assert.match(text,/航线安全检查和绕障验证/);assert.match(text,/不是真实禁飞公告/);
});
