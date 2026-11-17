'use strict';
window.externalMode=false;
window.sitlTwinMode=false;
let twinState=null,twinBusy=false,twinVideoUrl='',twinIdentity=null,twinOpen3d=false;
let twinEvents=[],twinEventRecord=null,twinSelectedEvent=null;
let twinRecording=null,twinCursor=0,twinPlaying=false,twinTick=0,twinLoadGeneration=0;
window.twinReplayActive=false;
let twinListOffset=0,twinListGeneration=0;
async function twinRefreshRecords(){
 const generation=++twinListGeneration;
 try{const data=await api(`/api/sitl/records?limit=30&offset=${twinListOffset}`+(window.twinTaskContext?'&task_id='+encodeURIComponent(window.twinTaskContext):''));if(generation!==twinListGeneration||!window.sitlTwinMode)return;
 const select=$('twin-record-list'),previous=select.value;select.replaceChildren();const current=document.createElement('option');current.value='';current.textContent='当前 SITL 记录';select.append(current);
 const status={completed:'已完成',uploaded:'已创建',patrolling:'飞行中',interrupted:'已中断',stopped:'会话已结束',superseded:'已换任务'};
 for(const item of data.items||[]){const option=document.createElement('option');option.value=item.id;option.textContent=new Date(item.created_at*1000).toLocaleString()+' · '+({west:'西侧园区',east:'东侧园区'}[item.area_id]||item.area_id||'SITL 飞行')+' · '+(status[item.status]||item.status)+' · '+(item.business_task_id||'独立飞行')+' · '+item.samples+' 帧 · '+item.id.slice(0,6);select.append(option);}
 select.value=(data.items||[]).some(x=>x.id===previous)?previous:'';
 $('twin-record-prev').disabled=twinListOffset===0;$('twin-record-next').disabled=twinListOffset+30>=(data.total||0);
 $('twin-record-catalog-note').textContent=`已保存 ${data.total||0} 条 · 第 ${Math.floor(twinListOffset/30)+1} 页 · SITL 仿真记录`;
 }catch(e){if(generation===twinListGeneration)$('twin-record-catalog-note').textContent='历史记录加载失败：'+e.message;}
}
function twinDisplay(){return window.twinReplayActive&&twinRecording?window.SitlTwin.replay(twinRecording,twinCursor):window.twinReplayActive?null:twinState;}
let twinEventFilter='all', twinTransferEvents=[];
function twinFilteredEvents(){if(twinEventFilter==='transfer')return twinTransferEvents;return window.ComputeReplayEvents?.filtered(twinEvents,twinEventFilter)||[];}
let transferTimer=null, transferContext=null;
function stopTransferPlayback(){
 if(transferTimer!==null)clearTimeout(transferTimer);
 transferTimer=null;
}
function playTransferSteps(){
 const at=twinTransferEvents.findIndex(e=>e.id===twinSelectedEvent);
 if(at<0||at===5)twinJumpEvent(twinTransferEvents[0]);
 function advance(){
  const index=twinTransferEvents.findIndex(e=>e.id===twinSelectedEvent);
  if(index<0||index===5)return;
  transferTimer=setTimeout(()=>{twinJumpEvent(twinTransferEvents[index+1]);advance();},2500);
  $('twin-record-play').textContent='暂停';
 }
 advance();
}
function twinJumpEvent(event){
 if(!twinRecording||!window.twinReplayActive||!twinFilteredEvents().includes(event))return;
 stopTransferPlayback();
 const index=twinTransferEvents.indexOf(event);
 if(index>=0&&transferContext){
  sessionStorage.setItem(transferContext.key,JSON.stringify({index,label:event.label}));
 }
 twinPlaying=false;twinCursor=event.seek_at;twinSelectedEvent=event.id;twinReplayControls();window.renderTwin();
 window.showComputeEvent?.(event);
 $('twin-event-note').textContent=event.label+(event.transfer_token?' · 传输 '+event.transfer_token:'')+' · 定位到 '+(twinCursor-twinRecording.start).toFixed(1)+'s；地图与检测证据使用同一时刻。';
}
$('twin-event-filter').onchange=()=>{
 stopTransferPlayback();twinEventFilter=$('twin-event-filter').value;twinSelectedEvent=null;window.clearComputeSelection?.();twinEventControls();
};
for(const [id,direction] of [['prev',-1],['next',1]])$('twin-event-'+id).onclick=()=>{
 const event=window.ComputeReplayEvents?.neighbor(twinFilteredEvents(),twinSelectedEvent,twinCursor,direction);if(event)twinJumpEvent(event);
};
function twinEventControls(){
 const panel=$('twin-event-panel'),list=$('twin-event-markers');panel.hidden=!twinRecording;
 if(twinEventRecord!==twinRecording){
  stopTransferPlayback();transferContext=null;$('transfer-return-report')?.remove?.();twinTransferEvents=[];$('twin-event-filter').querySelector?.('option[value=transfer]')?.remove();
  twinEventRecord=twinRecording;twinSelectedEvent=null;twinEventFilter='all';$('twin-event-filter').value='all';list.replaceChildren();
  twinEvents=twinRecording?(window.ComputeReplayEvents?.index(twinRecording)||[]):[];
  $('twin-event-summary').textContent='计算事件 · '+twinEvents.length+' 条';
  $('twin-event-note').textContent=twinEvents.length?'点击跳到该事件首个已保存快照；暂停播放，不发送飞控指令。':'这条记录没有计算链路事件。';
  for(const event of twinEvents){
   const button=document.createElement('button');button.type='button';button.dataset.eventId=event.id;button.dataset.category=event.category;
   button.textContent=(event.seek_at-twinRecording.start).toFixed(1)+'s · '+event.label;
   button.title='事件 '+new Date(event.at*1000).toLocaleTimeString()+'；定位到首个保存快照';
   button.onclick=()=>twinJumpEvent(twinFilteredEvents().find(e=>e.id===event.id)||event);list.append(button);
  }
 }
 if(twinRecording&&!twinSelectedEvent)$('twin-event-note').textContent=twinEvents.length?'点击事件定位；拖动或播放后，详情按当前时间更新。':'这条记录没有计算链路事件。';
 const visible=twinFilteredEvents();
 $('twin-event-summary').textContent='计算事件 · '+visible.length+' / '+twinEvents.length+' 条';
 if(twinRecording&&!visible.length)$('twin-event-note').textContent='当前类型没有事件，请切换筛选。';
 for(const [id,direction] of [['prev',-1],['next',1]])$('twin-event-'+id).disabled=!twinRecording||!window.ComputeReplayEvents?.neighbor(visible,twinSelectedEvent,twinCursor,direction);
 for(const button of list.children||[]){button.hidden=!visible.some(e=>e.id===button.dataset.eventId);button.setAttribute('aria-pressed',String(button.dataset.eventId===twinSelectedEvent));}
}
function twinReplayControls(){
 twinEventControls();
 $('twin-record-play').disabled=!twinRecording;$('twin-record-live').disabled=!window.twinReplayActive;
 $('twin-record-time').disabled=!twinRecording;$('twin-record-play').textContent=twinPlaying||transferTimer?'暂停':'播放';
 if(twinRecording){$('twin-record-time').max=twinRecording.end-twinRecording.start;$('twin-record-time').value=twinCursor-twinRecording.start;
 $('twin-record-note').textContent=(twinPlaying?'播放中':twinCursor>=twinRecording.end?'回放结束':'已暂停')+' · '+new Date(twinCursor*1000).toLocaleString()+' · '+(twinCursor-twinRecording.start).toFixed(1)+' / '+(twinRecording.end-twinRecording.start).toFixed(1)+' s · '+(twinRecording.record_id?'任务 '+twinRecording.record_id.slice(0,6)+' · ':'')+'回放不暂停 SITL 飞行';
 const decision=twinDisplay()?.aoi_policy;
 let chart=$('joint-aoi-curve');
 if(chart)chart.hidden=true;
 if(decision?.role){
  $('twin-record-note').textContent+=' · SITL '+decision.role+' 号机 · 双机分配传感器 '+decision.sensor.join(' / ')+' · 策略下一步东向位置 '+decision.next_x.map(x=>x.toFixed(1)).join(' / ')+' m · 平均 AoI '+decision.mean_age_s.toFixed(2)+' s';
  if(!chart){chart=document.createElement('canvas');chart.id='joint-aoi-curve';chart.width=300;chart.height=70;chart.setAttribute('aria-label','接收端平均 AoI 随调度时隙变化');$('twin-record-note').after(chart);}
  chart.hidden=false;
  const points=[...new Map(twinRecording.track.filter(p=>p.at<=twinCursor&&p.aoi_policy?.role).map(p=>[p.aoi_policy.slot,p.aoi_policy])).values()];
  const ctx=chart.getContext('2d'),peak=Math.max(1,...points.map(p=>p.mean_age_s));
  ctx.clearRect(0,0,300,70);ctx.fillStyle='#6294a8';ctx.fillText('AoI (s)  0–'+peak.toFixed(1)+'  ·  1–'+decision.slot+' 时隙',3,12);
  ctx.strokeStyle='#24a5bb';ctx.beginPath();points.forEach((p,i)=>{const x=3+292*i/Math.max(1,points.length-1),y=67-50*p.mean_age_s/peak;if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y);});ctx.stroke();
 }else if(decision?.model_sha256)$('twin-record-note').textContent+=' · AttnSAC 传感器 '+decision.sensor+' · 平均 AoI '+decision.mean_age_s.toFixed(2)+' 秒 · '+(decision.delivered?'送达':'未送达');}
 else{if($('joint-aoi-curve'))$('joint-aoi-curve').hidden=true;$('twin-record-time').max=0;$('twin-record-time').value=0;}
}
function twinReturnLive(){window.clearComputeSelection?.();if(window.twinTaskContext)$('notice').textContent='区域任务 '+window.twinTaskContext+' · 实时状态，等待当前执行遥测';twinLoadGeneration++;twinRecording=null;twinPlaying=false;window.twinReplayActive=false;twinReplayControls();$('twin-record-note').textContent='实时状态 · 返回实时不会恢复或启动飞机任务';}
const twinLayer=L.layerGroup().addTo(map);
function twinNumber(n,digits=1){return Number.isFinite(n)?n.toFixed(digits):'—';}
window.renderTwin=function(){
 if(!window.externalMode){twinLayer.clearLayers();return;}
 const s=twinDisplay(),t=s?.telemetry||{};
 window.renderComputeTelemetry?.(s);
 if(s?.historical&&s.business_task_id)$('notice').textContent='区域任务 '+s.business_task_id+' · 历史回放 · 算法、姿态与航迹共用记录时间轴';
 if($('twin-control-note'))$('twin-control-note').textContent=window.sitlTwinMode?'本机 SITL 仿真：下方飞控按钮用于执行已批准任务，回放期间禁止控制。':'实飞只读监视：不向真实飞机发送指令。';
 $('twin-title').textContent=(s?.vehicle_id||'外部飞控')+(window.sitlTwinMode?' · 仿真孪生':' · 只读监视');
 $('twin-status').textContent=window.twinReplayActive?(s?'历史回放 · 非实时 · '+(s.position_valid?'记录定位有效':'该时段记录缺失')+(s.legacy?' · 旧记录无字段时效信息':''):'正在读取历史记录'):!s?'遥测服务不可用':!s.connected?'遥测失联 · 不回退仿真':s.position_valid?'实时遥测 · 定位有效':'已连接 · 等待有效定位';
 $('twin-values').textContent='高度 '+twinNumber(t.relative_altitude_m)+' m · 速度 '+twinNumber(t.groundspeed_mps)+' m/s · 电量 '+twinNumber(t.battery_percent,0)+'%';
 $('twin-age').textContent=s?.historical?'历史采样 '+new Date(s.recorded_at*1000).toLocaleTimeString()+' · 控制已锁定':'心跳距今 '+twinNumber(s?.heartbeat_age_s)+' s · GPS '+(t.gps_fix??'—')+' · '+(s?.armed===true?'已解锁':s?.armed===false?'未解锁':'解锁状态未知');
 $('vehicle-title').textContent=s?.vehicle_id||'外部飞控';$('instrument-source').textContent=window.twinReplayActive?'SITL 历史回放':window.sitlTwinMode?'ArduPilot SITL · 仿真':'外部飞控 · 只读';
 for(const [id,key] of [['altitude','relative_altitude_m'],['speed','groundspeed_mps'],['heading','yaw_deg'],['battery','battery_percent']])$(id).textContent=twinNumber(t[key]);
 $('distance').textContent='—';$('position').textContent=s?.position_valid?s.latitude.toFixed(6)+' / '+s.longitude.toFixed(6):'定位不可用';
 $('link').textContent=window.twinReplayActive?'● 历史回放 · 非实时':s?.connected?(window.sitlTwinMode?'● SITL 仿真在线':'● 外部飞控在线 · 只读'):'● 遥测未连接';
 $('twin-locate').disabled=!s?.position_valid;
 const c=$('twin-horizon');
 if(s?.connected&&s.position_valid&&['roll_deg','pitch_deg','yaw_deg','relative_altitude_m','groundspeed_mps'].every(k=>Number.isFinite(t[k]))){
  horizon({home:[0,0,0],mode:s.historical?'回放':window.sitlTwinMode?'仿真':'只读',armed:s.armed,battery:t.battery_percent??NaN},[0,0,t.relative_altitude_m],{roll:t.roll_deg,pitch:t.pitch_deg,yaw:t.yaw_deg},t.groundspeed_mps,'twin-horizon');
 }else{const x=c.getContext('2d');x.clearRect(0,0,c.width,c.height);x.fillStyle='#b7cad9';x.font='14px sans-serif';x.fillText('等待有效姿态与定位遥测',12,45);}
 twinLayer.clearLayers();
 if(s?.track?.length)L.polyline(s.track.map(p=>[p.latitude,p.longitude]),{color:s.connected?'#64d6b7':'#94a3b2',weight:2}).bindTooltip(window.sitlTwinMode?'SITL 仿真航迹':'外部飞控历史轨迹').addTo(twinLayer);
 if(s?.position_valid){
  const label=document.createElement('span');label.textContent=s.vehicle_id+(s.historical?' · 历史回放 · ':window.sitlTwinMode?' · SITL 仿真 · ':' · 外部遥测 · ')+twinNumber(t.relative_altitude_m)+' m';
  const marker=Number.isFinite(t.yaw_deg)?L.marker([s.latitude,s.longitude],{icon:arrow({attitude:{yaw:t.yaw_deg}},'#64d6b7')}):L.circleMarker([s.latitude,s.longitude],{radius:7,color:'#64d6b7'});
  marker.bindTooltip(label,{permanent:true}).addTo(twinLayer);
 }else if(s?.track?.length){const p=s.track.at(-1);L.circleMarker([p.latitude,p.longitude],{radius:7,color:'#94a3b2'}).bindTooltip('最后有效位置（历史，非实时）',{permanent:true}).addTo(twinLayer);}
 window.renderSitlTwin3d();
 const url=s?.video?.url||'';
 if(url!==twinVideoUrl){twinVideoUrl=url;const video=$('twin-video');video.pause();video.removeAttribute('src');video.load();video.hidden=!url;if(url)video.src=url;}
 if(window.twinReplayActive)$('twin-video-note').textContent='测试流已停止；云边端面板显示该时刻已完成的检测帧证据';
 else if(window.sitlTwinMode)$('twin-video-note').textContent='OpenCV 测试录像循环流 · 非机载画面，与遥测未时间同步；检测帧关联见云边端面板';
 else if(!url)$('twin-video-note').textContent=s?.video?.configured?'遥测未连接，视频已停止':'未接入机载视频 · 不显示测试画面';
 else if($('twin-video').paused)$('twin-video-note').textContent='外部视频已配置 · 点击播放；与遥测的时间同步尚未校准';
};
$('twin-video').onplaying=()=>{$('twin-video-note').textContent='外部机载视频 · 与遥测时间同步尚未校准';};
$('twin-video').onerror=()=>{$('twin-video-note').textContent='视频连接失败或格式不支持，请核对视频网关；不会回退测试画面';};
$('twin-video').onwaiting=()=>{$('twin-video-note').textContent='视频缓冲中 · 画面可能延迟';};
$('twin-locate').onclick=()=>{if(state.scene3d&&window.sitlTwinMode){window.GCS3D?.frame('vehicle');return;}const s=twinDisplay();if(s?.position_valid)map.setView([s.latitude,s.longitude],17);};
$('flight-source').onchange=()=>{
 if(state.busy){$('flight-source').value=window.sitlTwinMode?'sitl':window.externalMode?'external':'simulation';return;}
 twinReturnLive();
 if(state.scene3d)$('scene-flat').click();
 window.sitlTwinMode=$('flight-source').value==='sitl';twinOpen3d=window.sitlTwinMode;
 window.externalMode=$('flight-source').value!=='simulation';
 twinState=null;
 document.body.classList.toggle('sitl-twin-mode',window.sitlTwinMode);
 $('twin-timeline').hidden=!window.sitlTwinMode;
 if(window.sitlTwinMode){twinListOffset=0;twinRefreshRecords();}
 $('twin-test-toggle').hidden=!window.sitlTwinMode;
 $('twin-test-toggle').textContent='播放测试视频';$('twin-test-video').hidden=true;$('twin-test-video').removeAttribute('src');
 $('sitl-panel').open=window.sitlTwinMode;
 if(typeof patrolEvidence!=='undefined')patrolEvidence.clearLayers();
 if(typeof sitlMarker!=='undefined'&&sitlMarker){map.removeLayer(sitlMarker);sitlMarker=null;}
 if(typeof sitlTrackLayer!=='undefined'&&sitlTrackLayer){map.removeLayer(sitlTrackLayer);sitlTrackLayer=null;}
 if(typeof sitlReplayMarker!=='undefined'&&sitlReplayMarker){map.removeLayer(sitlReplayMarker);sitlReplayMarker=null;}document.body.classList.toggle('external-mode',window.externalMode);
 state.replay=false;stopReplay();state.adding=false;state.picking=false;
 $('twin-panel').hidden=!window.externalMode;
 if(window.externalMode){switchView('fly');message(window.sitlTwinMode?'SITL 仿真孪生：地图与仪表共用遥测，控制只作用于本机 SITL。':'实飞孪生只读监视：先在设置中启动 MAVLink 监听，并确认外部飞控来源。');}
 else{twinLayer.clearLayers();$('twin-video').pause();$('twin-video').removeAttribute('src');$('twin-video').load();twinVideoUrl='';message('已返回本地仿真，控制只作用于仿真飞机。');}
 renderTelemetry();renderMap();updateControls();if(window.sitlTwinMode)$('scene-mode').disabled=false;window.renderManaged?.();refreshTwin();
};
async function refreshTwin(){
 if(twinBusy)return;twinBusy=true;
 try{const mode=$('flight-source').value;const data=await api(mode==='sitl'?'/api/sitl':'/api/twin');if(mode!==$('flight-source').value)return;const matching=mode!=='sitl'||!window.twinTaskContext||data.execution_binding?.task_id===window.twinTaskContext;twinState=mode==='sitl'?window.SitlTwin.project(matching?data:{...data,connected:false,telemetry:{},compute_runtime:null,execution_binding:null,algorithm_live:null,patrol:null}):data;if(!matching)twinState.track=[];$('external-discovery').textContent=window.sitlTwinMode?'SITL 遥测孪生 · 仿真':twinState.connected?'发现 '+twinState.vehicle_id+' · 可切换只读监视':twinState.listening?'正在监听外部飞控，等待心跳':'未连接真实飞机 · 当前可使用仿真';
  if(mode==='sitl'&&window.twinTaskContext&&matching&&!window.twinReplayActive){const phase=data.patrol?.status;const labels={completed:'已完成，可历史回放',patrolling:'执行中',uploaded:'已准备，尚未起飞',interrupted:'已中止',cancelled:'已取消'};$('notice').textContent='区域任务 '+window.twinTaskContext+' · '+(labels[phase]||'等待执行状态')+' · 仅本机 SITL 仿真';}
  if(mode==='sitl'&&data.record_error)$('external-discovery').textContent=data.record_error;
  if(twinIdentity!==twinState.vehicle_id){twinIdentity=twinState.vehicle_id;twinLayer.clearLayers();}
 }catch(e){if(twinState)twinState={...twinState,connected:false,position_valid:false,telemetry:{},video:{configured:false}};$('external-discovery').textContent='外部飞控服务不可达';}
 finally{twinBusy=false;window.renderTwin();}
}
window.renderSitlTwin3d=()=>{
 if(!window.sitlTwinMode)return;
 const displayed=twinDisplay();
 $('scene-mode').disabled=false;
 if(twinOpen3d&&window.GCS3D&&(displayed?.anchor||twinState?.anchor)){twinOpen3d=false;if(!state.scene3d)$('scene-mode').click();}
 if(state.scene3d&&window.GCS3D&&(displayed?.anchor||twinState?.anchor)){const d=window.SitlTwin.scene(displayed||{...twinState,position_valid:false,telemetry:{},track:[]},window.GCSCore,$('follow').checked);if(typeof managedCatalog!=='undefined'&&managedCatalog){d.regions=managedCatalog.areas;d.zones=managedCatalog.zones;}d.algorithm=window.computeScene?.(displayed);window.GCS3D.update(d);}
};
$('twin-test-toggle').onclick=()=>{if(window.twinReplayActive)return;const img=$('twin-test-video');img.hidden=!img.hidden;if(img.hidden)img.removeAttribute('src');else img.src='/api/test-video/stream?t='+Date.now();$('twin-test-toggle').textContent=img.hidden?'播放测试视频':'停止测试视频';};
$('twin-test-video').onerror=()=>{$('twin-video-note').textContent='测试视频连接失败 · 不影响 SITL 遥测';};
refreshTwin();setInterval(refreshTwin,1000);

$('twin-record-load').onclick=async()=>{
 if(!window.sitlTwinMode)return;
 const generation=++twinLoadGeneration;twinPlaying=false;twinRecording=null;window.twinReplayActive=true;
 $('twin-test-video').hidden=true;$('twin-test-video').removeAttribute('src');$('twin-test-toggle').textContent='播放测试视频';
 twinReplayControls();$('twin-record-note').textContent='正在读取 SITL 飞行记录…';window.renderTwin();refreshSitl();
 try{const selected=$('twin-record-list').value;const data=await api(selected?'/api/sitl/records/'+encodeURIComponent(selected):'/api/sitl/patrol/result');if(generation!==twinLoadGeneration||!window.sitlTwinMode)return;
 if(window.twinTaskContext&&data.business_task_id!==window.twinTaskContext)throw Error('档案与所选业务任务不匹配');
 twinRecording=window.SitlTwin.recording(data);twinCursor=twinRecording.start;twinReplayControls();window.renderTwin();
 }catch(e){if(generation!==twinLoadGeneration)return;twinReturnLive();$('twin-record-note').textContent='无法回放：'+e.message;window.renderTwin();refreshSitl();}
};
$('twin-record-play').onclick=()=>{if(!twinRecording)return;if(twinEventFilter==='transfer'){if(transferTimer){stopTransferPlayback();twinReplayControls();}else playTransferSteps();return;}if(!twinPlaying&&twinCursor>=twinRecording.end)twinCursor=twinRecording.start;twinSelectedEvent=null;twinPlaying=!twinPlaying;twinTick=Date.now();twinReplayControls();window.renderTwin();};
$('twin-record-time').oninput=()=>{if(!twinRecording)return;stopTransferPlayback();twinSelectedEvent=null;twinPlaying=false;twinCursor=Math.max(twinRecording.start,Math.min(twinRecording.end,twinRecording.start+Number($('twin-record-time').value)));twinReplayControls();window.renderTwin();};
$('twin-record-live').onclick=()=>{twinReturnLive();window.renderTwin();refreshSitl();refreshTwin();};
setInterval(()=>{if(!window.twinReplayActive||!twinRecording||!twinPlaying)return;const now=Date.now();twinCursor=Math.min(twinRecording.end,twinCursor+(now-twinTick)/1000);twinTick=now;if(twinCursor>=twinRecording.end)twinPlaying=false;twinReplayControls();window.renderTwin();},100);

$('twin-record-refresh').onclick=()=>twinRefreshRecords();
$('twin-record-prev').onclick=()=>{twinListOffset=Math.max(0,twinListOffset-30);twinRefreshRecords();};
$('twin-record-next').onclick=()=>{twinListOffset+=30;twinRefreshRecords();};
$('twin-record-list').onchange=()=>{$('twin-record-load').click();};

async function openTaskContext(){
 if(typeof location==='undefined')return;
 const query=new URLSearchParams(location.search);
 if(query.get('source')!=='sitl')return;
 const task=query.get('task'),record=query.get('record');window.twinTaskContext=task;
 $('flight-source').value='sitl';$('flight-source').onchange();
 if(task){try{const execution=await api('/api/sitl/business/tasks/'+encodeURIComponent(task));$('notice').textContent='业务任务 '+task+' · SITL 执行 '+execution.execution_id+' · '+execution.status;}catch(e){$('notice').textContent='任务执行读取失败：'+e.message;}}
 if(record){await twinRefreshRecords();const option=document.createElement('option');option.value=record;option.textContent=(task||'指定任务')+' · 档案 '+record.slice(0,8);$('twin-record-list').append(option);$('twin-record-list').value=record;await $('twin-record-load').onclick();
  const token=query.get('transfer');
  if(token&&twinRecording?.record_id===record){
   twinTransferEvents=window.ComputeReplayEvents.transferChain(twinRecording,twinEvents,token);
   $('twin-event-panel').open=true;
   if(!twinTransferEvents.length){$('twin-event-note').textContent='档案中未找到此传输的完整事件记录';return;}
   const opt=document.createElement('option');opt.value='transfer';opt.textContent='本次传输';$('twin-event-filter').append(opt);$('twin-event-filter').value=twinEventFilter='transfer';
   const list=$('twin-event-markers');
   for(const event of twinTransferEvents){
    event.id='transfer-'+event.id;
    const button=document.createElement('button');button.type='button';button.dataset.eventId=event.id;button.textContent=event.label;button.onclick=()=>twinJumpEvent(event);list.append(button);
   }
   let index=token===twinTransferEvents[0].transfer_token?0:1;
   const report=query.get('report'),round=query.get('round');
   if(report&&/^[A-Za-z0-9_-]+$/.test(report)&&/^[1-9][0-9]*$/.test(round)){
    transferContext={key:'transfer:'+report+':'+record};
    const back=document.createElement('a');back.id='transfer-return-report';back.textContent='返回第 '+round+' 轮报告';
    back.href='/api/stability/'+encodeURIComponent(report)+'/files/summary.html#round-'+round;
    $('twin-event-note').after(back);
    if(/^[0-5]$/.test(query.get('step')||''))index=Number(query.get('step'));
   }
   twinJumpEvent(twinTransferEvents[index]);
   if(query.get('autoplay')==='1')playTransferSteps();
  }
 }
}
openTaskContext();
