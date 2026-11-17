'use strict';
(()=>{
 const C=window.AlgorithmCore, layer=L.layerGroup().addTo(map);
 let result=null, catalog=null, loading=false, catalogToken=0, inputKey='';
 const label=(text)=>{const span=document.createElement('span');span.textContent=text;return span;};
 const geo=p=>Core.geo(p,result.context.anchor).slice(0,2);
 const active=()=>state.view==='algorithms'&&!window.externalMode;
 async function request(path,body){
  const r=await fetch('/api/algorithms'+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;
 }
 function feedback(text){$('algorithm-input-status').textContent=text;}
 function clearResult(note){result=null;layer.clearLayers();$('algorithm-results').hidden=true;$('algorithm-status').textContent=note;renderScene3d();}
 async function defaults(){
  const token=++catalogToken;catalog=null;$('algorithm-run').disabled=true;
  clearResult('参数已调整，请重新计算。');
  try{
   const area=$('managed-area').value||'west',scene=$('algorithm-scene').value,alt=$('algorithm-altitude').value;
   const data=await request(`/catalog?area_id=${encodeURIComponent(area)}&scene=${scene}&altitude_m=${encodeURIComponent(alt)}`);
   if(token!==catalogToken)return;catalog=data;
   for(const [name,point] of [['start',data.context.start],['goal',data.context.goal]]){ $('algorithm-'+name+'-x').value=point[0];$('algorithm-'+name+'-y').value=point[1]; }
   $('algorithm-config').value=JSON.stringify(data.compute,null,2);inputKey=area;
   renderSources(data.sources);feedback(data.context.area.name+' · 参数就绪，示例可编辑。');
  }catch(e){if(token===catalogToken)feedback('参数读取失败：'+e.message);}
  finally{if(token===catalogToken)$('algorithm-run').disabled=!catalog||loading;}
 }
 function renderSources(sources){
  const box=$('algorithm-sources');box.replaceChildren();
  for(const s of Object.values(sources)){
   const p=document.createElement('p');p.textContent=s.algorithm+' · '+(s.version||s.commit?.slice(0,10)||'未安装')+' ';
   const a=document.createElement('a');a.href=s.paper;a.target='_blank';a.rel='noopener';a.textContent='论文';p.append(a);
   const code=document.createElement('a');code.href=s.repository;code.target='_blank';code.rel='noopener';code.textContent=' / 源码';p.append(code);box.append(p);
  }
 }
 function redraw(){
  layer.clearLayers();if(!result||!active())return;
  const {snap,comparison}=C.selected(result,$('algorithm-sample').value,$('algorithm-policy').value);
  const route=result.route,ctx=result.context;
  const lo=ctx.bounds.xyz_min,hi=ctx.bounds.xyz_max;
  L.rectangle([geo(lo),geo(hi)],{color:'#e4c269',weight:2,dashArray:'4 6',fillOpacity:0}).bindTooltip(label('实验范围 · '+ctx.area.name)).addTo(layer);
  for(const b of ctx.boxes)L.rectangle([geo(b.lower),geo(b.upper)],{color:b.kind==='no_fly'?'#ff6379':'#a0aebe',weight:1,fillOpacity:.08}).bindPopup(label(`${b.kind==='no_fly'?'仿真禁飞区':'合成建筑安全包络'} ${b.id} · ${b.lower[2]}–${b.upper[2]} m`)).addTo(layer);
  if(route.points.length)L.polyline(route.points.map(geo),{color:'#f8c774',weight:4}).bindTooltip(label('算法巡航段 · '+route.algorithm)).addTo(layer);
  if(!snap){renderScene3d();return;}
  const positions=Object.fromEntries(result.compute.nodes.map(n=>[n.id,n.kind==='device'?snap.position:n.position]));
  for(const n of result.compute.nodes){if(n.kind==='cloud')continue;L.circleMarker(geo(positions[n.id]),{radius:n.kind==='device'?8:6,color:n.kind==='device'?'#f8c774':'#b6a0ff',fillOpacity:.9}).bindTooltip(label(n.id+(n.kind==='device'?' · 算法采样点':' · 仿真边缘站'))).addTo(layer);}
  for(const flow of comparison?.transfers||[]){
   const terrestrial=flow.path.filter(n=>result.compute.nodes.find(x=>x.id===n)?.kind!=='cloud');
   if(terrestrial.length>1)L.polyline(terrestrial.map(n=>geo(positions[n])),{color:'#b6a0ff',weight:2,dashArray:'3 7'}).bindTooltip(label(`${flow.from_task} → ${flow.to_task} · ${flow.path.join(' → ')} · ${C.display(flow.mbit)} Mbit`)).addTo(layer);
  }
  renderScene3d();
 }
 function render(){
  if(!result)return;
  $('algorithm-status').textContent=`${result.context.area.name} · ${result.route.algorithm} · ${result.status==='ok'?'计算完成':'未找到安全路线'} · 实验 ${result.id.slice(0,8)}`;
  renderSources(result.sources);$('algorithm-results').hidden=result.status!=='ok';
  if(result.status!=='ok'){feedback(result.route.note);redraw();return;}
  $('algorithm-route-note').textContent=`巡航段 ${C.display(result.route.distance_m,1)} m · ${result.route.points.length} 航点 · 空域版本 ${result.context.airspace_revision}。金色航线 / 紫色卸载链路，均为算法仿真。`;
  const {snap,comparison}=C.selected(result,$('algorithm-sample').value,$('algorithm-policy').value);
  $('algorithm-sample-note').textContent=`航路 ${Math.round(snap.fraction*100)}% · 高度 ${C.display(snap.position[2],1)} m · 独立任务批次`;
  $('algorithm-comparison').replaceChildren();
  for(const row of snap.comparisons){const tr=document.createElement('tr');for(const value of [C.names[row.policy],C.display(row.makespan_s),row.status==='ok'?row.deadline_met+'/'+row.task_count:'—']){const td=document.createElement('td');td.textContent=value;tr.append(td);}$('algorithm-comparison').append(tr);}
  $('algorithm-compute-note').textContent=comparison.status==='ok'?`计算能耗 ${C.display(comparison.energy_j)} J（不含飞行与传输）；结果已含回传时延。`:'此位置无法往返连接目标节点，未分配任务。';
  const gantt=$('algorithm-gantt');gantt.replaceChildren();
  for(const r of C.bars(comparison)){
   const line=document.createElement('button');line.className='algorithm-task';line.type='button';line.setAttribute('aria-label',r.id+' 在 '+r.node);
   const caption=document.createElement('span');caption.textContent=r.id+' · '+r.node;
   const track=document.createElement('span');track.className='algorithm-bar-track';const bar=document.createElement('i');bar.style.left=r.left+'%';bar.style.width=Math.max(.4,r.width)+'%';bar.className=r.deadline_met?'':'late';track.append(bar);
   const time=document.createElement('small');time.textContent=C.display(r.start_s)+'–'+C.display(r.end_s)+' s';line.append(caption,track,time);
   line.onclick=()=>{$('algorithm-task-detail').textContent=`${r.id} → ${r.node}：数据就绪 ${C.display(r.ready_s)} s，排队 ${C.display(r.queue_s)} s，计算 ${C.display(r.compute_s)} s；截止 ${C.display(r.deadline_s)} s，${r.deadline_met?'按期':'超期'}。`;};gantt.append(line);
  }
  $('algorithm-task-detail').textContent='点击任务查看依赖等待和计算时间。';
  $('algorithm-loads').replaceChildren();
  for(const [node,load] of Object.entries(comparison.loads||{})){const p=document.createElement('p');p.textContent=node+' · '+C.display(load*100,1)+'%';const meter=document.createElement('progress');meter.max=1;meter.value=load;p.append(meter);$('algorithm-loads').append(p);}
  redraw();
 }
 window.algorithmScene=()=>{
  if(!active()||!result||result.status!=='ok')return null;
  // ENU positions belong to the archived anchor, not necessarily today's anchor.
  if(result.context.anchor.latitude!==anchor().latitude||result.context.anchor.longitude!==anchor().longitude)return null;
  const {snap}=C.selected(result,$('algorithm-sample').value,$('algorithm-policy').value);
  return {id:result.id,points:result.route.points,position:snap.position,nodes:result.compute.nodes,boxes:result.context.boxes,bounds:result.context.bounds};
 };
 window.algorithmViewChanged=view=>{
  $('algorithm-inputs').hidden=view!=='algorithms';
  if(view==='algorithms'&&(!catalog||inputKey!==$('managed-area').value))defaults();
  redraw();
 };
 $('algorithm-run').onclick=async()=>{
  if(loading||!catalog)return;loading=true;$('algorithm-run').disabled=true;
  clearResult('正在执行开源规划与调度…');feedback('计算中，请等待；未发送飞行指令。');
  const token=catalogToken;
  try{
   const body={area_id:$('managed-area').value||'west',planner:$('algorithm-planner').value,scene:$('algorithm-scene').value,altitude_m:Number($('algorithm-altitude').value),seed:Number($('algorithm-seed').value),start_xy:[Number($('algorithm-start-x').value),Number($('algorithm-start-y').value)],goal_xy:[Number($('algorithm-goal-x').value),Number($('algorithm-goal-y').value)],compute:JSON.parse($('algorithm-config').value)};
   const r=await request('/runs',body);
   if(token!==catalogToken){feedback('计算期间参数已变化，结果保存在实验记录中。');return;}
   result=r;$('algorithm-sample').value=0;render();feedback('实验已保存，未改变飞机或已装订任务。');await history();
  }catch(e){feedback('计算失败：'+e.message);$('algorithm-status').textContent='计算失败，请查看左侧原因。';}
  finally{loading=false;$('algorithm-run').disabled=!catalog;}
 };
 async function history(){try{const r=await request('/runs');$('algorithm-history').replaceChildren(new Option('选择已保存实验',''));for(const h of r.records)$('algorithm-history').append(new Option(new Date(h.created_at*1000).toLocaleString()+' · '+h.request.planner+' · '+h.request.area_id,h.id));}catch(e){feedback('记录读取失败：'+e.message);}}
 $('algorithm-load').onclick=async()=>{const id=$('algorithm-history').value;if(!id)return;try{result=await request('/runs/'+id);$('algorithm-sample').value=0;render();feedback('显示归档实验；使用当时的场景、空域和参数。');}catch(e){feedback(e.message);}};
 $('algorithm-defaults').onclick=defaults;$('algorithm-refresh').onclick=history;
 for(const id of ['algorithm-scene','algorithm-altitude'])$(id).addEventListener('change',defaults);
 $('managed-area').addEventListener('change',()=>{catalogToken++;catalog=null;clearResult('区域已变化，请重新计算。');if(active())defaults();});
 for(const id of ['algorithm-planner','algorithm-seed','algorithm-start-x','algorithm-start-y','algorithm-goal-x','algorithm-goal-y','algorithm-config'])$(id).addEventListener('change',()=>{catalogToken++;clearResult('参数已变化，请重新计算。');});
 $('algorithm-sample').oninput=render;$('algorithm-policy').onchange=render;
 $('algorithm-fit').onclick=()=>{if(!result)return;if(state.scene3d){window.GCS3D.frame('algorithm');return;}map.fitBounds([geo(result.context.bounds.xyz_min),geo(result.context.bounds.xyz_max)],{padding:[55,75]});};
 $('algorithm-application').onclick=async()=>{
  if(!result||result.status!=='ok')return;
  const button=$('algorithm-application');button.disabled=true;
  try{
   const mission=await request('/runs/'+result.id+'/mission');
   const resources=await fetch('/service/api/resources').then(r=>r.json());
   const vehicle=resources.resources.find(v=>v.id===state.selected&&v.kind==='vehicle');
   if(!vehicle)throw Error('当前飞机未在服务管理注册，请先配置资源');
   const response=await fetch('/service/api/applications',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({applicant:'区域规划工作台',pilot_id:vehicle.pilot_id,vehicle_id:vehicle.id,scene:mission.scene,purpose:result.context.area.name+'航路与协同计算仿真',route:mission.route,algorithm_run_id:result.id})});
   const item=await response.json();if(!response.ok)throw Error(typeof item.detail==='string'?item.detail:JSON.stringify(item.detail));
   feedback('申请 '+item.id+' 已创建。请在服务管理审批并生成任务，再验证、准备 SITL；尚未起飞。');
   $('algorithm-application-link').hidden=false;
  }catch(e){feedback('创建申请失败：'+e.message);}finally{button.disabled=false;}
 };
 $('algorithm-export').onclick=()=>{if(result)download('algorithm-'+result.id+'.json',result);};
 $('scene-mode').addEventListener('click',()=>{if(active()&&result&&state.scene3d)window.GCS3D?.frame('algorithm');});
 $('flight-source').addEventListener('change',()=>{layer.clearLayers();});
 history();
})();
