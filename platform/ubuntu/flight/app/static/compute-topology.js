/* One time-cursor projection shared by 2D and 3D. No live requests in replay. */
(function(root){
 'use strict';
 const colors={online:'#56c9ae',weak:'#f7bd55',disconnected:'#ff6379',offline:'#8998ab',busy:'#55c9ee',migration:'#d5a2ff'};
 function project(display,now=Date.now()/1000){
  const r=display?.runtime,a=display?.algorithm,time=display?.historical?display.cursor_at:now;
  if(!a?.compute||!r||!display.connected||!display.position_valid||!Number.isFinite(time)||time-r.at<0||time-r.at>3)return null;
  const nodes=a.compute.nodes.map(n=>{
   const live=r.nodes.find(x=>x.id===n.id),position=n.kind==='device'?display.position||display.compute?.position:n.position;
   const status=live?.offline?'offline':!live?.alive?'offline':live?.busy?'busy':'online';
   return {...n,position,status,color:colors[status],label:({device:'端',edge:'边',cloud:'云（逻辑位置）'}[n.kind]||n.kind)+' · '+n.id+' · '+({offline:'离线',busy:'忙碌',online:'在线'}[status]),load:live?.busy_fraction_10s||0};
  }).filter(n=>Array.isArray(n.position)&&n.position.length===3&&n.position.every(Number.isFinite));
  const byId=Object.fromEntries(nodes.map(n=>[n.id,n]));
  const links=(r.network?.links||[]).flatMap(l=>{
   const s=byId[l.source],t=byId[l.target];if(!s||!t)return [];
   const range=l.max_range_m>0&&Math.hypot(...s.position.map((v,i)=>v-t.position[i]))>l.max_range_m;
   const status=!l.enabled||range?'disconnected':r.network.profile==='weak'?'weak':'online';
   return [{...l,status,color:colors[status],points:[s.position,t.position],label:l.source+' → '+l.target+' · '+(range?'超出通信范围':{disconnected:'断链',weak:'弱网',online:'正常'}[status])+' · '+l.effective_mbps.toFixed(2)+' Mbps / '+(l.effective_latency_s*1000).toFixed(0)+' ms（仿真）'}];
  });
  const events=(r.scheduling_events||[]).filter(e=>e.at<=time);
  const migrations=events.filter(e=>e.kind==='task_migrated'&&time-e.at<=8&&byId[e.previous_node]&&byId[e.node]&&e.previous_node!==e.node&&!events.some(x=>x.kind==='task_migrated'&&x.batch_id===e.batch_id&&x.task===e.task&&x.at>e.at)).map(e=>{
   const done=events.some(x=>x.kind==='task_recovered'&&x.batch_id===e.batch_id&&x.task===e.task&&x.node===e.node&&x.at>=e.at);
   const from=byId[e.previous_node].position,to=byId[e.node].position,dx=to[0]-from[0],dy=to[1]-from[1],length=Math.hypot(dx,dy)||1;
   const mid=[(from[0]+to[0])/2-dy/length*70,(from[1]+to[1])/2+dx/length*70,(from[2]+to[2])/2+30];
   return {...e,points:[from,mid,to],color:colors.migration,label:e.task+' · '+e.previous_node+' → '+e.node+' · '+(done?'迁移完成':'迁移中')+' · 批次 '+e.batch_id.slice(0,8)};
  });
  return {nodes,links,migrations,profile:r.network?.profile||'unknown',batch_id:r.last_batch?.id||r.current?.id,at:r.at};
 }
 const api={project,colors};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ComputeTopology=api;
})(typeof window!=='undefined'?window:this);
