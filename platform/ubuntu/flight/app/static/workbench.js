'use strict';
const $=id=>document.getElementById(id);
const modes={GUIDED:'引导',AUTO:'自动巡检',LOITER:'悬停',RTL:'返航',LAND:'降落',STABILIZE:'自稳'};
const wb={sitl:null,job:null,catalog:null,catalogAt:0,area:"west",aircraftId:"SITL-1",geoSignature:"",busy:false,cancelling:false,replay:null,selectedAlert:null,video:false,timer:null,autoReplay:null,alertSignature:'',receiptAt:0,replayAnchor:null};
const map=L.map('map').setView([39.05,115.98],14);
const tiles=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{attribution:'Esri World Imagery',maxZoom:19}).addTo(map);
tiles.on('tileerror',()=>{$('map-warning').hidden=false;});
const routeLayer=L.layerGroup().addTo(map),alertLayer=L.layerGroup().addTo(map),historyLayer=L.layerGroup().addTo(map),replayRoute=L.layerGroup().addTo(map);
const regionLayer=L.layerGroup().addTo(map),zoneLayer=L.layerGroup().addTo(map);
let aircraft=null,routeSignature='';
const selectedJob=()=>wb.job&& (wb.job.area_id||'west')===wb.area&&(wb.job.aircraft_id||'SITL-1')===wb.aircraftId?wb.job:null;
const areaRecord=()=>wb.catalog?.areas.find(a=>a.id===wb.area);
const planeRecord=()=>wb.catalog?.aircraft.find(a=>a.id===wb.aircraftId);

const active=()=>['running','awaiting_resolution'].includes(wb.job?.status);
async function request(path,body){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),25000);
 try{const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});
 const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;
 }finally{clearTimeout(timer);}
}
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function renderGeography(fit=false){
 const area=areaRecord();if(!area)return;
 const signature=JSON.stringify([wb.area,wb.catalog.anchor,wb.catalog.zones]);
 if(signature!==wb.geoSignature){
  wb.geoSignature=signature;regionLayer.clearLayers();zoneLayer.clearLayers();
  const title=document.createElement('span');title.textContent=area.name+' · 仿真管理范围';
  L.polygon(area.polygon,{color:'#72bfff',weight:2,fillOpacity:.06,dashArray:'7 5'}).bindTooltip(title).addTo(regionLayer);
  for(const zone of wb.catalog.zones){
   const label=document.createElement('span');label.textContent=zone.id+' · 禁飞 '+zone.z_min+'–'+zone.z_max+' m（仿真）';
   L.polygon(zone.polygon,{color:'#ff7373',weight:2,fillOpacity:.2}).bindTooltip(label).addTo(zoneLayer);
  }
 }
 if(fit)map.fitBounds(area.polygon,{padding:[35,35],maxZoom:17});
 $('task-target').textContent=area.name+' / '+(planeRecord()?.name||'未选择飞机');
 $('aircraft-title').textContent=planeRecord()?.name||'当前飞机';
}
function fillSelection(){
 for(const [id,rows,value] of [['area-select',wb.catalog.areas,wb.area],['aircraft-select',wb.catalog.aircraft,wb.aircraftId]]){
  $(id).replaceChildren();for(const row of rows){const option=document.createElement('option');option.value=row.id;option.textContent=row.name;$(id).append(option);}$(id).value=value;
 }
}
function changeSelection(){
 if(active()||wb.busy||wb.replay||wb.sitl?.telemetry?.armed){fillSelection();notice('任务运行、飞行或回放期间不能切换控制对象。');return;}
 const area=$('area-select').value,plane=$('aircraft-select').value;
 if(!wb.catalog?.areas.some(a=>a.id===area)||!wb.catalog?.aircraft.some(a=>a.id===plane&&a.area_ids.includes(area))){fillSelection();notice('当前飞机不能用于此区域');return;}
 wb.area=area;wb.aircraftId=plane;wb.selectedAlert=null;$('evidence').hidden=true;
 renderGeography(true);renderLive();renderJob();controls();notice('已选择 '+areaRecord().name+'；创建任务后才会启动巡检。');
}
$('area-select').onchange=changeSelection;$('aircraft-select').onchange=changeSelection;
$('map-region').onclick=()=>renderGeography(true);
function renderSummary(){
 const s=wb.sitl,t=s?.telemetry||{},j=selectedJob();
 const fresh=s?.connected&&['armed','mode','landed_state','speed'].every(k=>Date.now()/1000-(s.updated_at?.[k]||0)<3);
 const landed=t.landed_state===1;
 const hovering=fresh&&t.armed&&t.mode==='LOITER'&&t.speed<1&&Math.abs(t.climb??99)<.5;
 $('aircraft-state').textContent=!fresh?'遥测待更新':landed?(t.armed?'地面 · 已解锁':'地面 · 已上锁'):hovering?'空中 · 已悬停':'空中 · '+(modes[t.mode]||t.mode||'状态未知');
 $('aircraft-detail').textContent=fresh?(modes[t.mode]||t.mode)+' · '+(t.armed?'已解锁':'未解锁')+' · 高度 '+(t.altitude??0).toFixed(1)+' m':'请核对连接，勿把历史状态当作实时状态';
 const phases={starting:'准备启动',connecting:'连接飞控',resetting:'重置仿真',upload:'检查并装载航线',arming:'解锁预检',takeoff:'起飞',patrolling:'巡检中',review:'预警待核验',resuming:'继续巡检',completed:'已完成',interrupted:'已中止',failed:'执行失败'};
 $('mission-state').textContent=phases[j?.phase]||'未启动';
 const points=s?.patrol?.waypoints||[];
 $('mission-progress').textContent=active()&&points.length?'当前目标航点 '+Math.min((t.waypoint??0)+1,points.length)+' / '+points.length+(s.patrol.rerouted?' · 已绕障':''):j?.status==='completed'?'任务已归档，可读取结果回放':j?.status==='interrupted'?'自动巡检流程已结束；飞机状态见左侧':j?.status==='failed'?'查看上方失败原因；不会继续自动执行':'连接 → 起飞 → 巡检 → 核验 → 落地';
 $('display-state').textContent=wb.replay?'历史回放 · 控制锁定':'实时工作台';
 $('display-detail').textContent=wb.replay?'仪表和黄色位置为历史；左侧飞机状态卡仍为实时':'仪表与紫色飞机位置来自实时遥测';
 let guide='确认飞机地面上锁后，展开右侧“创建巡检任务”，选择启动巡检。';
 if(wb.replay)guide='正在回放：可拖动时间轴；先点击“返回实时”，再操作飞机。回放不会发送飞行指令。';
 else if(!fresh)guide='等待有效遥测：检查 SITL 连接，状态恢复后再进行飞行操作。';
 else if(wb.cancelling)guide='正在中止：请求回执不等于飞机已悬停，请观察模式、高度和速度。';
 else if(j?.status==='interrupted'||j?.status==='failed')guide=landed&&!t.armed?'飞机已落地上锁：可读取本轮结果，或重新启动巡检。':hovering?'已确认悬停：巡检不会自动恢复。请在左侧选择返航或降落，并等待落地上锁；中止不等于降落。':'自动流程已结束：飞机尚未确认落地或稳定悬停，请核对遥测并使用左侧悬停、返航或降落控制。';
 else if(j?.phase==='review')guide='预警核验中：点击右侧预警查看当时位置和遥测；'+(j.auto_resolve?'系统将自动核验后继续巡检。':'填写处置人和说明，核验后继续巡检。');
 else if(active())guide='巡检自动执行中：需要停止时点击“中止并悬停”，等待实时状态确认；不会自动降落。';
 else if(t.armed)guide='飞机已解锁：请先使用悬停、返航或降落完成当前飞行，落地上锁后才能启动新巡检。';
 else if(j?.status==='completed')guide='上轮巡检已完成：点击“读取结果”验收回放，或启动新一轮巡检。';
 $('next-action').textContent=guide;
}
function controls(){
 renderSummary();
 const s=wb.sitl,t=s?.telemetry||{},locked=wb.busy||active()||!!wb.replay;
 $('demo-start').disabled=locked||!!t.armed||!areaRecord()||!planeRecord();
 $('area-select').disabled=$('aircraft-select').disabled=locked||!!t.armed;
 $('result').disabled=wb.busy||!selectedJob()?.id;
 $('demo-cancel').disabled=wb.cancelling||!active();
 $('demo-cancel').hidden=!active()&&!wb.cancelling;
 $('demo-cancel').textContent=wb.cancelling?'正在中止…':'中止并悬停';
 $('demo-cancel').title=active()?'停止巡检流程并请求悬停':'当前没有运行中的巡检任务';
 $('connect').disabled=wb.busy||s?.running||active();
 $('auto-resolve').disabled=active()||wb.busy;
 document.querySelectorAll('[data-command]').forEach(b=>{
 const c=b.dataset.command;
 b.disabled=locked||!s?.connected||(c==='arm'&&!!t.armed)||(c==='disarm'&&(!t.armed||t.landed_state!==1))||(c==='takeoff'&&(!t.armed||t.mode!=='GUIDED'));
 });
 $('resolve').disabled=wb.busy||!!wb.replay||wb.selectedAlert?.status!=='open'||wb.job?.phase!=='review';
}
async function action(fn){if(wb.busy)return;wb.busy=true;controls();try{await fn();}catch(e){notice(e.message);}finally{wb.busy=false;await refresh();controls();}}
function clearInstrument(){
 const c=$('horizon'),x=c.getContext('2d');x.clearRect(0,0,c.width,c.height);x.fillStyle='#a5bccc';x.font='14px sans-serif';x.fillText('等待有效 SITL 遥测',15,50);
 for(const id of ['altitude','speed','battery','heading'])$(id).textContent='—';
}
function instrument(t,historical=false){
 const s=wb.sitl,anchor=historical?wb.replayAnchor:s?.anchor;
 if(!t||(!historical&&(!s?.connected||!['roll','pitch','yaw','altitude','speed'].every(k=>Number.isFinite(t[k])&&Date.now()/1000-(s.updated_at?.[k]||0)<3)))){clearInstrument();return;}
 const attitude=['roll','pitch','yaw'].every(k=>Number.isFinite(t[k]));
 if(attitude&&anchor)horizon({home:[0,0,0],battery:t.battery,mode:t.mode||'AUTO',armed:t.armed},[(t.longitude-anchor.longitude)*111319.49*Math.cos(anchor.latitude*Math.PI/180),(t.latitude-anchor.latitude)*111319.49,t.altitude],t,t.speed);
 else{const c=$('horizon');c.getContext('2d').clearRect(0,0,c.width,c.height);}
 for(const [id,key,digits] of [['altitude','altitude',1],['speed','speed',1],['battery','battery',0],['heading','yaw',0]])$(id).textContent=Number.isFinite(t[key])?t[key].toFixed(digits):'—';
 $('battery').style.color=t.battery<=20?'#ffa18b':'#dcecf7';
 $('flight-mode').textContent=(historical?'历史 · ':'')+(modes[t.mode]||t.mode||'航迹');
}
function renderLive(){
 const s=wb.sitl,t=s?.telemetry||{};
 document.body.classList.toggle('offline',!s?.connected);
 $('connection').textContent=s?.connected?'● MAVLink2 在线 · 3 倍速':'● SITL 未连接';
 if(!wb.replay)$('flight-health').textContent=s?.connected?'GPS '+(t.gps_fix??'—')+' / '+(t.satellites??'—')+' 星 · '+(t.armed?'已解锁':'已上锁'):'遥测不可用，禁止飞行控制';
 if(!wb.replay)instrument(t);
 const fresh=s?.connected&&Date.now()/1000-(s.updated_at?.latitude||0)<3;
 if(fresh&&Number.isFinite(t.latitude)&&Number.isFinite(t.longitude)){
  const p=[t.latitude,t.longitude];
  if(!aircraft)aircraft=L.circleMarker(p,{radius:8,color:'#c7a0ff',fillOpacity:1}).bindTooltip('SITL 实时飞机').addTo(map);else aircraft.setLatLng(p);
  $('coordinates').textContent=t.latitude.toFixed(6)+' / '+t.longitude.toFixed(6);
 }else if(aircraft){map.removeLayer(aircraft);aircraft=null;$('coordinates').textContent='位置已失效';}
 const points=(s?.patrol?.area_id||'west')===wb.area?s?.patrol?.waypoints||[]:[],signature=JSON.stringify(points);
 if(signature!==routeSignature){routeSignature=signature;routeLayer.clearLayers();if(points.length)L.polyline(points.map(p=>p.slice(0,2)),{color:'#68dccb',weight:3,dashArray:'6 5'}).addTo(routeLayer);}
}
function renderJob(){
 const j=selectedJob();
 if(j?.status==='failed')$('task-detail').open=true;
 $('stage').textContent=(j?.status==='failed'?'任务已失败（不再执行自动流程）：':j?.status==='interrupted'?'任务已中止：':'')+(j?.events?.at(-1)?.text||'准备演示：连接 → 巡检 → 预警核验 → 落地 → 回放');
 $('events').replaceChildren();
 for(const e of j?.events||[]){const li=document.createElement('li');li.textContent=new Date(e.at*1000).toLocaleTimeString()+' · '+e.text;$('events').append(li);}
 const alerts=j?.alerts||[];
 $('alert-count').textContent=alerts.length+' 条';
 const signature=JSON.stringify(alerts);
 if(signature!==wb.alertSignature){
  wb.alertSignature=signature;$('alerts').replaceChildren();alertLayer.clearLayers();if(!alerts.length){const empty=document.createElement('p');empty.className='muted';empty.textContent='当前区域暂无任务预警。';$('alerts').append(empty);}
  for(const a of alerts){
   const button=document.createElement('button');button.textContent=(a.status==='resolved'?'✓ 已核验 · ':'● 待核验 · ')+a.title;
   button.onclick=()=>selectAlert(a.id);$('alerts').append(button);
   L.circleMarker([a.telemetry.latitude,a.telemetry.longitude],{radius:13,color:'#ff9277'}).bindTooltip('演示注入预警').on('click',()=>selectAlert(a.id)).addTo(alertLayer);
  }
 }
 if(wb.selectedAlert){const selected=alerts.find(a=>a.id===wb.selectedAlert.id);if(selected){wb.selectedAlert=selected;renderEvidence(selected);}}
 const receipt=j?.receipts?.at(-1);
 if(receipt&&(receipt.sent_at||0)>=wb.receiptAt){wb.receiptAt=receipt.sent_at||0;$('receipt').textContent=receipt.request.command+' · '+receipt.status+' · MAV_RESULT '+(receipt.mav_result??'未知');}
}
function renderEvidence(a){
 $('evidence').hidden=false;$('evidence-title').textContent=a.title;
 $('evidence-meta').textContent=new Date(a.at*1000).toLocaleTimeString()+' · 高度 '+a.telemetry.altitude.toFixed(1)+' m · SITL 遥测快照';
 const c=$('evidence-frame'),x=c.getContext('2d');x.fillStyle='#0c1a26';x.fillRect(0,0,320,150);
 x.strokeStyle='#294455';for(let i=0;i<320;i+=40){x.beginPath();x.moveTo(i,0);x.lineTo(i,150);x.stroke();}
 x.fillStyle='#ffb286';x.beginPath();x.arc(160,65,8,0,Math.PI*2);x.fill();
 x.fillStyle='#d5e5f0';x.font='12px sans-serif';x.fillText('演示预警时刻 · 非相机画面',20,25);
 x.fillText(a.telemetry.latitude.toFixed(6)+' / '+a.telemetry.longitude.toFixed(6),20,115);
 $('resolution-state').textContent=a.status==='resolved'?'已核验：'+a.actor+' · '+a.resolution:'等待核验；飞机悬停中';
}
function selectAlert(id){
 const a=selectedJob()?.alerts?.find(a=>a.id===id);if(!a)return;
 wb.selectedAlert=a;renderEvidence(a);map.setView([a.telemetry.latitude,a.telemetry.longitude],17);
 controls();notice('已定位预警时刻飞机；当前显示遥测快照，不是机载视频证据。');
}
function stopVideo(){
 wb.video=false;$('video').hidden=true;$('video').removeAttribute('src');$('video-placeholder').hidden=false;$('video-toggle').textContent='播放测试流';
}
async function startVideo(){
 const s=await request('/api/test-video/status');if(!s.available)throw Error('FFmpeg 测试流不可用');
 wb.video=true;$('video').hidden=false;$('video-placeholder').hidden=true;$('video').src=s.url+'?t='+Date.now();$('video-toggle').textContent='关闭测试流';$('video-status').textContent='10 fps 连续 MJPEG 测试图 · 非机载视频，无识别证据';
}
$('video-toggle').onclick=()=>action(async()=>{if(wb.video)stopVideo();else await startVideo();});
$('video').onerror=()=>{stopVideo();$('video-status').textContent='视频流中断，请重新连接';};
function stopPlayback(){clearInterval(wb.timer);wb.timer=null;$('replay-play').textContent='播放回放';}
function exitReplay(){
 stopPlayback();wb.replay=null;wb.replayAnchor=null;historyLayer.clearLayers();replayRoute.clearLayers();document.body.classList.remove('historical');
 $('replay-time').disabled=true;$('replay-play').disabled=true;$('replay-exit').disabled=true;$('result-state').textContent='已返回实时遥测';renderLive();controls();
}
function drawReplay(){
 const t=wb.replay?.[Number($('replay-time').value)];if(!t)return;historyLayer.clearLayers();
 L.circleMarker([t.latitude,t.longitude],{radius:9,color:'#ffe18b',fillOpacity:1}).bindTooltip('历史飞机位置').addTo(historyLayer);
 instrument(t,true);$('flight-health').textContent='历史遥测 · '+new Date(t.at*1000).toLocaleTimeString();$('replay-caption').textContent=new Date(t.at*1000).toLocaleTimeString()+' · 历史高度 '+t.altitude.toFixed(1)+' m · 历史地速 '+t.speed.toFixed(1)+' m/s';
}
async function loadResult(){
 $('replay-panel').open=true;
 if(!selectedJob()?.id)throw Error('当前区域尚无任务结果');
 const r=await request('/api/sitl-demo/'+wb.job.id+'/result'),points=r.result?.track||[];
 if(!points.length)throw Error('尚无可回放航迹');
 stopPlayback();wb.replay=points;wb.replayAnchor=r.result.anchor||wb.sitl?.anchor;replayRoute.clearLayers();L.polyline(points.map(p=>[p.latitude,p.longitude]),{color:'#ffe18b',weight:2}).addTo(replayRoute);document.body.classList.add('historical');$('replay-time').max=points.length-1;$('replay-time').value=0;
 $('replay-time').disabled=false;$('replay-play').disabled=false;$('replay-exit').disabled=false;
 $('result-state').textContent=(r.status==='completed'?'巡检与处置已完成':'任务状态：'+r.status)+' · '+points.length+' 个记录点 · 无机载视频录像';
 notice('历史回放中：仪表和黄色位置为历史记录，飞机控制已锁定；视频仍是独立测试图。');
 drawReplay();map.fitBounds(points.map(p=>[p.latitude,p.longitude]),{padding:[30,30],maxZoom:17});controls();
}
$('result').onclick=()=>action(loadResult);
$('replay-time').oninput=()=>{stopPlayback();drawReplay();};
$('replay-play').onclick=()=>{
 if(wb.timer){stopPlayback();return;}if(!wb.replay)return;
 if(Number($('replay-time').value)>=wb.replay.length-1)$('replay-time').value=0;
 $('replay-play').textContent='暂停回放';wb.timer=setInterval(()=>{const index=Math.min(Number($('replay-time').value)+5,wb.replay.length-1);$('replay-time').value=index;drawReplay();if(index===wb.replay.length-1)stopPlayback();},100);
};
$('replay-exit').onclick=()=>{exitReplay();notice('已返回实时工作台');};
$('demo-start').onclick=()=>action(async()=>{
 if(wb.replay)throw Error('请先退出回放');wb.selectedAlert=null;
 if(!areaRecord()||!planeRecord())throw Error('请先选择有效区域和飞机');
 wb.job=await request('/api/sitl-demo/start',{auto_resolve:$('auto-resolve').checked,area_id:wb.area,aircraft_id:wb.aircraftId});
 controls();wb.autoReplay=wb.job.id;notice('演示已启动，将使用真实 ArduPilot SITL 完成巡检与处置流程');
 try{await startVideo();}catch(e){notice('演示已启动，视频未连接：'+e.message);}
});
$('demo-cancel').onclick=async()=>{
 if(wb.cancelling||!active())return;
 wb.cancelling=true;wb.autoReplay=null;controls();notice('正在中止巡检并请求悬停…');
 try{
  const r=await request('/api/sitl-demo/cancel',{});
  notice(r.hover_receipt?'已请求中止；悬停回执：'+r.hover_receipt.status+'。请核对实时模式与速度；中止不会自动降落。':r.cancel_requested?'已请求中止演示；未发送悬停指令，请核对飞机状态。':'任务已结束，无需中止。');
 }catch(e){notice('中止请求未确认：'+e.message+'；请检查连接与实时飞行状态。');}
 finally{wb.cancelling=false;await refresh();controls();}
};
$('connect').onclick=()=>action(async()=>{await request('/api/sitl/start',{});notice('SITL 已连接，等待定位与飞控预检');});
document.querySelectorAll('[data-command]').forEach(b=>b.onclick=()=>action(async()=>{
 if(active()||wb.replay)throw Error('演示或回放中不可手动控制');
 const r=await request('/api/sitl/commands',{command_id:crypto.randomUUID(),command:b.dataset.command,expires_at:Date.now()/1000+20});
 wb.receiptAt=r.sent_at||Date.now()/1000;$('receipt').textContent=b.textContent+' · '+r.status+' · MAV_RESULT '+(r.mav_result??'未知');notice('控制回执：'+r.status+'；实际状态以遥测为准');
}));
$('resolve').onclick=()=>action(async()=>{
 if(wb.replay)throw Error('请先退出回放再处置预警');if(!wb.selectedAlert||!wb.job)throw Error('先选择预警');
 await request('/api/sitl-demo/'+wb.job.id+'/alerts/'+wb.selectedAlert.id+'/resolve',{actor:$('actor').value,note:$('resolution').value});
 notice('处置已记录，演示流程将恢复巡检');
});
$('map-locate').onclick=()=>{if(aircraft)map.setView(aircraft.getLatLng(),17);};
$('map-fit').onclick=()=>{const points=(wb.sitl?.patrol?.area_id||'west')===wb.area?wb.sitl?.patrol?.waypoints:null;if(points?.length)map.fitBounds(points.map(p=>p.slice(0,2)),{padding:[30,30]});};
$('map-expand').onclick=()=>{const expanded=document.body.classList.toggle('map-expanded');$('map-expand').textContent=expanded?'收起地图':'展开地图';map.invalidateSize();};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('map-expanded'))$('map-expand').click();});
window.addEventListener('resize',()=>{map.invalidateSize();if(!wb.replay)renderLive();});
window.addEventListener('pagehide',()=>{stopVideo();stopPlayback();});
let refreshing=false;
async function refresh(){
 if(refreshing)return;refreshing=true;
 try{
  const [s,j]=await Promise.all([request('/api/sitl'),request('/api/sitl-demo')]);wb.sitl=s;wb.job=j;
  if(!wb.catalog||Date.now()-wb.catalogAt>5000){
   try{const first=!wb.catalog;wb.catalog=await request('/api/sitl-demo/catalog');wb.catalogAt=Date.now();fillSelection();renderGeography(first);}
   catch(e){wb.catalog=null;regionLayer.clearLayers();zoneLayer.clearLayers();notice('区域和禁飞区加载失败：'+e.message);}
  }
  if(active()&&wb.catalog&&(wb.area!==(j.area_id||'west')||wb.aircraftId!==(j.aircraft_id||'SITL-1'))){wb.area=j.area_id||'west';wb.aircraftId=j.aircraft_id||'SITL-1';fillSelection();renderGeography(true);}
  renderLive();renderJob();controls();
  if(j.status==='completed'&&wb.autoReplay===j.id){wb.autoReplay=null;await loadResult();}
 }catch(e){wb.sitl=null;renderLive();controls();$('connection').textContent='● 服务断开';notice('无法刷新工作台：'+e.message);}
 finally{refreshing=false;}
}
controls();setInterval(()=>{refresh();},1000);refresh();
