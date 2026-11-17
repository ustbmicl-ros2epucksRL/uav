'use strict';
(()=>{
 const layers=L.layerGroup().addTo(map);
 let selection=null,selectionRevision=0,selectionKey=null;
 let shown=null,busy=false,renderedRuntimeKey=null,selectedNode=null,layerKey=null;
 const text=(root,message)=>{const p=document.createElement('p');p.textContent=message;root.append(p);};
 function renderRuntime(display){
  shown=display;const runtime=display?.runtime,box=$('compute-runtime');
  const key=JSON.stringify([runtime?.runtime_id,runtime?.at,display?.execution_id,display?.historical,display?.connected,display?.phase,!!window.twinReplayActive,busy,!!runtime&&Date.now()/1000-runtime.at>3]);
  if(key===renderedRuntimeKey)return;renderedRuntimeKey=key;
  const metricsOpen=!!box.querySelector?.('details')?.open;box.replaceChildren();
  const enabled=!!display?.execution_id&&!display.historical&&!window.twinReplayActive&&!busy;
  const running=runtime&&['starting','running','paused','draining'].includes(runtime.state);
  $('compute-start').disabled=!enabled||running||!display.connected||display.phase==='completed';
  $('compute-stop').disabled=!enabled||!running;$('compute-fault').disabled=!enabled||!running;
  $('compute-offline').disabled=!enabled||!running;
  $('compute-recover').disabled=!enabled||!running||!runtime?.nodes?.some(n=>n.offline);
  for(const profile of ['weak','disconnected','normal'])$('compute-network-'+profile).disabled=!enabled||!running;
  if(runtime?.demo?.state==='running')for(const kind of ['fault','offline','recover','network-weak','network-disconnected','network-normal'])$('compute-'+kind).disabled=true;
  $('compute-demo').disabled=!enabled||!running||runtime?.demo?.state==='running';
  $('compute-demo-cancel').disabled=!enabled||!running||runtime?.demo?.state!=='running';
  const demo=runtime?.demo;
  $('compute-demo-status').textContent=demo?'演示 · '+({normal:'正常检测',weak:'弱网',disconnected:'断链',node_wait:'等待节点任务',node_offline:'节点掉线',recovery:'恢复验证',restored:'已恢复'}[demo.phase]||demo.phase)+' · '+({running:'进行中',completed:'验证通过',cancelled:'已中止',incomplete:'未完成'}[demo.state]||demo.state)+' · '+demo.reason:'依次演示正常、弱网、断链、节点掉线及恢复；仅影响计算进程。';
  if(!runtime){text(box,display?.historical?'此时尚无实际执行记录。':'实际进程尚未启动；下方为 HEFT 预测。');return;}
  const names={starting:'启动中',running:'运行中',paused:'暂停派发',draining:'等待结果回传',completed:'已完成',stopped:'已停止',error:'异常'};
  if(!display.historical&&running&&Date.now()/1000-runtime.at>3)text(box,'执行状态已过期，以下为最后收到的记录。');
  text(box,(display.historical?'历史实测':'本地进程实测')+' · '+(names[runtime.state]||runtime.state)+' · '+(runtime.reason||''));
  if(runtime.storage_error)text(box,'证据存储失败：'+runtime.storage_error);
  text(box,'完成 '+runtime.counts.completed+' 批 · 失败 '+runtime.counts.failed+' · 取消 '+runtime.counts.cancelled+' · 重试 '+runtime.counts.retries);
  const metrics=document.createElement('details'),summary=document.createElement('summary');summary.textContent='节点负载与各阶段耗时';metrics.open=metricsOpen;metrics.append(summary);
  for(const n of runtime.nodes)text(metrics,n.id+' · PID '+n.pid+' · '+(n.offline?'掉线，等待恢复':n.alive?'在线':'已退出')+' · 执行槽忙碌率/10s '+(n.busy_fraction_10s*100).toFixed(1)+'% · 重启 '+n.restarts);
  if(runtime.network){
   const net=runtime.network;
   text(box,'链路仿真 · '+({normal:'正常',weak:'弱网',disconnected:'断链'}[net.profile]||net.profile)+' · 修订 '+net.revision+(net.restore_at?' · 自动恢复 '+new Date(net.restore_at*1000).toLocaleTimeString():''));
   for(const l of net.links)text(metrics,l.source+' → '+l.target+' · '+(l.enabled?'连通':'断开')+' · '+l.effective_mbps.toFixed(2)+' Mbps · '+(l.effective_latency_s*1000).toFixed(0)+' ms（仿真）');
   for(const f of net.flows)text(box,'传输 '+f.source+' → '+f.target+' · '+f.status+' · 当前跳剩余 '+f.remaining_mbit.toFixed(3)+' Mbit');
  }
  const batch=runtime.last_batch||runtime.current;
  if(batch){
   text(box,'批次 '+batch.id.slice(0,8)+' · '+batch.status+(Number.isFinite(batch.latency_s)?' · 端到端（含链路仿真） '+(batch.latency_s*1000).toFixed(1)+' ms':''));
   for(const job of Object.values(batch.tasks)){
    const a=job.attempts.at(-1);
    if(a?.network_wait_s!=null)text(metrics,'链路仿真等待 '+(a.network_wait_s*1000).toFixed(1)+' ms · worker 实测往返 '+(a.worker_roundtrip_s==null?'—':(a.worker_roundtrip_s*1000).toFixed(1))+' ms');
    const estimate=a?.decision?.candidates?.[a.node];
    if(estimate)text(metrics,'派发依据 '+job.id+' · '+({configured:'配置估计','node-ewma':'本节点实测均值','local-peer-ewma':'本机其他进程实测'}[estimate.source]||estimate.source)+' '+(estimate.base_s*1000).toFixed(1)+' ms · 负载 '+(estimate.load_10s*100).toFixed(0)+'% · 修订 '+a.decision.revision);
    text(metrics,job.id+' → '+(job.node||'待分配')+' · '+job.status+(a?.decision?.reason==='failure_migration'?' · 迁移重试':a?.recovery?' · 端侧恢复':'')+' · '+job.attempts.length+' 次尝试'+(a?.roundtrip_s!=null?' · 全流程（含链路仿真） '+(a.roundtrip_s*1000).toFixed(1)+' ms / CPU '+(a.cpu_s==null?'—':(a.cpu_s*1000).toFixed(1))+' ms':''));
   }
   if(batch.output?.frame){
    const out=batch.output,f=out.frame;
    text(box,out.model+' · '+out.detections.length+' 个目标 · 推理 '+(out.inference_s*1000).toFixed(1)+' ms · 模型加载 '+(out.model_load_s*1000).toFixed(1)+' ms');
    text(box,'测试录像 '+f.video_time_s.toFixed(1)+' s · 采集 '+new Date(f.captured_at*1000).toLocaleTimeString()+' · 非机载画面');
    const panel=document.createElement('div');panel.style.cssText='position:relative;width:100%;line-height:0';
    const img=document.createElement('img');img.src=f.url;img.alt='归档检测原始帧';img.style.width='100%';panel.append(img);
    img.onerror=()=>{img.alt='帧证据读取失败';};
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 '+f.width+' '+f.height);svg.style.cssText='position:absolute;inset:0;width:100%;height:100%;pointer-events:none';
    for(const d of out.detections){
     const [x,y,x2,y2]=d.box,r=document.createElementNS(svg.namespaceURI,'rect');
     for(const [key,val] of Object.entries({x,y,width:x2-x,height:y2-y,fill:'none',stroke:'#51ffb2','stroke-width':3}))r.setAttribute(key,val);
     svg.append(r);const label=document.createElementNS(svg.namespaceURI,'text');label.setAttribute('x',x);label.setAttribute('y',Math.max(16,y-4));label.setAttribute('fill','#51ffb2');label.setAttribute('font-size','18');label.textContent=d.label+' '+Math.round(d.score*100)+'%';svg.append(label);
    }
    panel.append(svg);box.append(panel);text(box,out.label);
   }else if(batch.output)text(box,'历史合成任务结果（非目标检测）');
  }
  box.append(metrics);
  for(const e of runtime.events||[])text(box,'失败记录 '+e.batch_id.slice(0,8)+' / '+e.task+' / '+e.node+' / 第 '+e.attempt+' 次：'+({link_disconnected:'链路中断',link_unreachable:'链路不可达',network_timeout:'传输超时',node_offline:'节点掉线'}[e.error]||e.error)+'；'+(e.recovered?'重试成功':e.will_retry?'已安排重试':'已达重试上限'));
  for(const e of (runtime.scheduling_events||[]).slice(-8)){
   const names={demo_phase:'演示阶段',demo_finished:'演示结束',node_offline:'节点掉线',node_recovered:'节点恢复',task_migrated:'任务迁移',task_recovered:'迁移完成',placement_changed:'调度变化',link_changed:'链路变化',transfer_failed:'传输失败'};
   text(box,new Date(e.at*1000).toLocaleTimeString()+' · '+(names[e.kind]||e.kind)+' · '+(e.profile?({normal:'恢复正常',weak:'弱网',disconnected:'断链'}[e.profile]||e.profile)+' ':'')+(e.phase?({normal:'正常',weak:'弱网',disconnected:'断链',node_wait:'等待节点任务',node_offline:'节点掉线',recovery:'恢复验证'}[e.phase]||e.phase)+' ':'')+(e.task||'')+' '+(e.previous_node?e.previous_node+' → ':'')+(e.node||'')+(e.reason?' · '+({node_offline:'显式掉线',worker_exit:'进程退出',timeout:'执行超时',network_timeout:'传输超时',link_disconnected:'链路中断',link_unreachable:'链路不可达',failure_migration:'失败后迁移',measured_cost_load_telemetry:'实测耗时、负载与遥测更新'}[e.reason]||e.reason):'')+(e.batch_id?' · 批次 '+e.batch_id.slice(0,8):''));
  }
  if(runtime.feedback_revision!=null)text(box,'HEFT 实测反馈 · 修订 '+runtime.feedback_revision+' · 耗时滑动平均与执行槽负载参与调度');
  text(box,'检测与 worker 往返为本机实测；带宽、传播时延与断链为应用层仿真，不代表真实广域网性能。');
 }
 async function action(path){
  if(busy||window.twinReplayActive||!shown?.execution_id)return;
  selectedNode=null;busy=true;const execution_id=shown.execution_id;renderRuntime(shown);
  try{
   const nodeId=path==='recover'?shown.runtime?.nodes?.find(n=>n.offline)?.id:(shown.runtime?.current?.tasks?.detect?.node||shown.runtime?.last_batch?.tasks?.detect?.node||'cloud');
   const nodeAction=['offline','recover'].includes(path),networkAction=path.startsWith('network-'),demoAction=path.startsWith('demo');
   const r=await fetch('/api/compute/'+(demoAction?'demo':networkAction?'network':nodeAction?'node':path),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({execution_id,...(demoAction?{action:path==='demo'?'start':'cancel'}:networkAction?{profile:path.slice(8),duration_s:10,mbps:1,latency_ms:200}:nodeAction?{node_id:nodeId,action:path,duration_s:10}:path==='fault'?{task:'detect',mode:'fail'}:{})})});
   const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
   $('compute-action').textContent=demoAction?'演示请求已接受，阶段和结果将写入飞行档案。':networkAction?'链路仿真请求已接受；不改变真实网络或飞控连接。':nodeAction?'已接受节点'+nodeId+(path==='offline'?'掉线请求，10 秒后自动恢复。':'恢复请求。'):path==='fault'?'已安排下一次 detect 失败，重试会记录在档案中。':path==='start'?'工作进程启动请求已接受。':'本地计算已停止，飞机控制不受影响。';
  }catch(e){$('compute-action').textContent=e.message;}finally{busy=false;renderRuntime(shown);}
 }
 for(const kind of ['start','stop','fault','offline','recover','network-weak','network-disconnected','network-normal','demo','demo-cancel'])$('compute-'+kind).onclick=()=>action(kind);

 window.computeScene=display=>{
  const sample=display?.compute,algorithm=display?.algorithm;
  const time=display?.historical?display.cursor_at:Date.now()/1000;
  if(!display?.connected||!display.position_valid||!algorithm||sample?.status!=='ok'||time-sample.at<0||time-sample.at>3)return null;
  return {hideSample:true,id:sample.experiment_id,points:algorithm.route.map(p=>[p.x,p.y,p.z]),position:sample.position,nodes:algorithm.compute.nodes,topology:window.ComputeTopology?.project({...display,position:Core.enu([display.latitude,display.longitude,display.telemetry.relative_altitude_m],display.anchor)}),boxes:[],bounds:{xyz_min:[0,0,0],xyz_max:[2000,2000,300]}};
 };
 window.renderComputeTelemetry=display=>{
  const box=$('twin-compute');box.hidden=!display?.algorithm;
  if(box.hidden){layers.clearLayers();layerKey=null;selection=null;selectionRevision++;$('compute-selection').replaceChildren();return;}
  renderRuntime(display);
  if(selection)refreshSelection();
  if(selectedNode)window.showComputeNode(selectedNode,false);
  const scene=window.computeScene(display),sample=display.compute;
  $('twin-compute-results').replaceChildren();
  if(!scene){layers.clearLayers();layerKey=null;$('twin-compute-note').textContent=display.phase==='completed'?'任务已完成，调度已归档；读取飞行记录可查看历史结果。':'遥测或调度样本缺失/过期，暂停显示调度结果。';return;}
  const result=sample.result;
  $('twin-compute-note').textContent=(display.historical?'历史调度':'实时遥测驱动')+' · '+new Date(sample.at*1000).toLocaleTimeString()+' · 配置基线（非实际派发） · 完工 '+Number(result.makespan_s).toFixed(2)+' s · 按期 '+result.deadline_met+'/'+result.task_count;
  for(const row of result.rows||[]){const line=document.createElement('p');line.textContent=row.id+' → '+row.node+' · '+row.start_s.toFixed(2)+'–'+row.end_s.toFixed(2)+' s';$('twin-compute-results').append(line);}
  const geo=p=>Core.geo(p,display.anchor).slice(0,2),topology=scene.topology;
  if(!topology){layers.clearLayers();layerKey=null;return;}
  const key=JSON.stringify([display.anchor,topology]);if(key===layerKey)return;layerKey=key;layers.clearLayers();
  const tip=message=>{const span=document.createElement('span');span.textContent=message;return span;};
  const interactive=(marker,label,kind,id)=>{
   const element=marker.getElement();element?.setAttribute('role','button');element?.setAttribute('aria-label',label);element?.setAttribute('tabindex','0');
   element?.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();window.showComputeDetail(kind,id);}});
  };
  for(const link of topology.links){
   const id=link.source+'→'+link.target,marker=L.polyline(link.points.map(geo),{color:link.color,dashArray:link.status==='online'?null:'5 7',weight:5,opacity:.65}).bindTooltip(tip(link.label)).on('click',()=>window.showComputeDetail('link',id)).addTo(layers);
   interactive(marker,link.label,'link',id);
  }
  for(const migration of topology.migrations){
   const id=migration.batch_id+'|'+migration.task+'|'+migration.at;
   L.polyline(migration.points.map(geo),{color:migration.color,weight:5,dashArray:'10 5'}).on('click',()=>window.showComputeDetail('migration',id)).addTo(layers);
   const marker=L.circleMarker(geo(migration.points[1]),{color:migration.color,fillColor:migration.color,fillOpacity:1,radius:7}).bindTooltip(tip(migration.label),{permanent:true,direction:'top'}).on('click',()=>window.showComputeDetail('migration',id)).addTo(layers);
   interactive(marker,migration.label,'migration',id);
  }
  for(const node of topology.nodes){
   const marker=L.circleMarker(geo(node.position),{color:node.color,fillColor:node.color,fillOpacity:.85,radius:7}).bindTooltip(tip(node.label),{permanent:true,direction:'top'}).on('click',()=>window.showComputeNode(node.id)).addTo(layers);
   const element=marker.getElement();element?.setAttribute('role','button');element?.setAttribute('aria-label',node.label);element?.setAttribute('tabindex','0');element?.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();window.showComputeNode(node.id);}});
  }

 };
 window.showComputeNode=(id,open=true)=>{
  selection=null;selectionKey=null;selectionRevision++;$('compute-selection').replaceChildren();
  selectedNode=id;const topology=window.ComputeTopology?.project(shown),node=topology?.nodes.find(n=>n.id===id);if(!node){$('compute-action').textContent='';return;}
  if(open)$('twin-compute').open=true;
  $('compute-action').textContent=node.label+' · 执行槽负载 '+(node.load*100).toFixed(0)+'% · '+(shown.historical?'历史时刻':'实时')+' '+new Date(topology.at*1000).toLocaleTimeString()+' · 下方检测批次 '+(topology.batch_id?.slice(0,8)||'尚无结果')+'（结果与节点可能属于不同任务阶段）';
 };
 window.clearComputeSelection=()=>{selection=null;selectedNode=null;selectionKey=null;selectionRevision++;$('compute-selection').replaceChildren();};
 const batchCache=new Map();
 window.showComputeDetail=(kind,id)=>{selectedNode=null;selection={kind,id};selectionKey=null;$('twin-compute').open=true;refreshSelection();};
 window.showComputeEvent=event=>{selectedNode=null;selection={kind:'event',id:event.id,event};selectionKey=null;$('twin-compute').open=true;refreshSelection();};
 async function refreshSelection(){
  const key=JSON.stringify([selection,shown?.runtime?.runtime_id,shown?.runtime?.at,shown?.cursor_at]);
  if(key===selectionKey)return;selectionKey=key;
  const revision=++selectionRevision,box=$('compute-selection');box.replaceChildren();
  const topology=window.ComputeTopology?.project(shown),r=shown?.runtime;
  if(!topology||!r){text(box,'当前时刻无可用链路证据');return;}
  const item=selection.kind==='event'?(selection.event.runtime_id===r.runtime_id&&selection.event.seek_at<=shown.cursor_at?selection.event:null):selection.kind==='link'?topology.links.find(l=>l.source+'→'+l.target===selection.id):topology.migrations.find(m=>m.batch_id+'|'+m.task+'|'+m.at===selection.id);
  if(!item){text(box,'所选标识不在当前时间范围；请重新选择。');return;}
  text(box,item.label);
  if(selection.kind==='event'){text(box,'记录事件 · '+new Date(item.at*1000).toLocaleTimeString()+(item.reason?' · 原因 '+({node_offline:'节点掉线',failure_migration:'失败后迁移',link_disconnected:'链路中断'}[item.reason]||item.reason):''));if(r.network)text(box,'该时刻链路 · '+({normal:'正常',weak:'弱网',disconnected:'断链'}[r.network.profile]||r.network.profile));}

  if(item.previous_node){const link=topology.links.find(l=>l.source===item.previous_node&&l.target===item.node);if(link)text(box,'当前时刻链路：'+link.label);}

  const cursor=shown.historical?shown.cursor_at:Date.now()/1000;
  const usesLink=b=>b.created_at<=cursor&&Object.values(b.tasks||{}).some(j=>j.attempts.some(a=>a.sent_at<=cursor&&(a.transfers||[]).some(t=>t.started_at<=cursor&&t.path?.some((n,i,arr)=>n===item.source&&arr[i+1]===item.target))));
  let batchId=item.batch_id,archivedBatch=null;
  if(!batchId&&selection.kind==='event'){text(box,'该状态事件未绑定检测批次；可点击迁移或任务恢复事件查看对应检测证据。');return;}
  if(!batchId){
   const candidates=[r.current,r.last_batch].filter(Boolean);
   batchId=candidates.find(usesLink)?.id;
   if(!batchId){
    try{let batches=batchCache.get(r.record_id);if(!batches){const response=await fetch('/api/compute/records/'+encodeURIComponent(r.record_id)+'/batches');if(!response.ok)throw Error('读取链路关联批次失败');batches=(await response.json()).batches;if(shown.historical)batchCache.set(r.record_id,batches);}
     if(revision!==selectionRevision)return;
     archivedBatch=batches.filter(usesLink).sort((a,b)=>b.created_at-a.created_at)[0];batchId=archivedBatch?.id;
    }catch(e){if(revision===selectionRevision)text(box,e.message);return;}
   }
  }
  if(!batchId){text(box,'当前快照无经过此链路的关联批次。');return;}
  text(box,'关联批次 '+batchId+' · 业务任务 '+r.task_id);
  let batch=archivedBatch||[r.current,r.last_batch].find(b=>b?.id===batchId);
  if(!batch){
   try{
    let batches=batchCache.get(r.record_id);
    if(!batches){const response=await fetch('/api/compute/records/'+encodeURIComponent(r.record_id)+'/batches');if(!response.ok)throw Error('读取批次失败');batches=(await response.json()).batches;if(shown.historical)batchCache.set(r.record_id,batches);}
    if(revision!==selectionRevision)return;
    batch=batches.find(b=>b.id===batchId);
   }catch(e){if(revision===selectionRevision)text(box,e.message);return;}
  }
  if(!batch){text(box,'该批次尚未归档，等待执行结果。');return;}
  const comparison=window.ComputeReplayEvents?.comparison(batch,item,cursor);
  if(comparison){
   text(box,(comparison.before.node===comparison.after.node?'重试前后':'迁移前后')+' · 同一批次 / '+item.task);
   const table=document.createElement('table');table.className='compute-comparison';
   const row=(values,header=false)=>{const tr=document.createElement('tr');for(const value of values){const cell=document.createElement(header?'th':'td');cell.textContent=value;tr.append(cell);}table.append(tr);};
   const names={completed:'完成',node_offline:'节点掉线',link_disconnected:'链路中断',link_unreachable:'链路不可达',network_timeout:'传输超时'};
   const ms=x=>x==null?'—':x.toFixed(0)+' ms',a=comparison.before,b=comparison.after;
   row(['指标','迁移前','迁移后'],true);row(['节点',a.node,b.node]);row(['状态',names[a.status]||a.status,names[b.status]||b.status]);row(['执行往返',ms(a.worker_ms),ms(b.worker_ms)]);row(['链路等待',ms(a.network_ms),ms(b.network_ms)]);row(['尝试总耗时',ms(a.total_ms),ms(b.total_ms)]);row(['检测结果',a.detection,b.detection]);box.append(table);
   text(box,'执行往返为本机实测；链路等待含仿真。— 表示尚未完成或未采集，不等于 0。');
  }
  const jobs=item.task?[batch.tasks[item.task]]:Object.values(batch.tasks||{});
  if(!comparison)for(const j of jobs.filter(Boolean))for(const a of j.attempts.filter(a=>a.sent_at<=cursor)){
   text(box,j.id+' → '+a.node+' · '+(a.received_at<=cursor?({node_offline:'节点掉线',link_disconnected:'链路中断',link_unreachable:'链路不可达',network_timeout:'传输超时',completed:'完成'}[a.error||a.status]||a.error||a.status):'执行中')+(a.received_at<=cursor?' · 链路等待 '+(Number.isFinite(a.network_wait_s)?(a.network_wait_s*1000).toFixed(0)+' ms':'—')+' · worker 往返 '+(Number.isFinite(a.worker_roundtrip_s)?(a.worker_roundtrip_s*1000).toFixed(0)+' ms':'—'):''));
  }
  if(batch.finished_at<=cursor&&batch.output?.frame){
   const out=batch.output;const img=document.createElement('img');img.src=out.frame.url;img.alt='关联批次检测证据';img.style.width='100%';box.append(img);
   text(box,'关联检测 '+out.detections.length+' 个目标 · '+out.detections.map(d=>d.label+' '+Math.round(d.score*100)+'%').join('、')+' · 测试录像，非机载视频');
  }else text(box,'此时关联检测结果尚未完成；不展示未来结果。');
 }
 $('flight-source').addEventListener('change',()=>{layers.clearLayers();layerKey=null;});
})();
