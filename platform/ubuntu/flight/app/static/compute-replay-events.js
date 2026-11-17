/* Build a deduplicated index from persisted evidence, never from live state. */
(function(root){
 'use strict';
 function index(record){
  const rows=[],seen=new Set(),history=(record.runtime_history||[]).filter(r=>Number.isFinite(r.at)).slice().sort((a,b)=>a.at-b.at);
  let previousProfile;
  for(const r of history){
   const events=(r.scheduling_events||[]).filter(e=>Number.isFinite(e.at)&&e.at<=r.at).slice();
   if(r.network?.profile!==previousProfile&&!events.some(e=>e.kind==='link_changed'&&e.profile===r.network.profile))events.push({kind:'link_changed',profile:r.network.profile,at:r.at,inferred:true});
   previousProfile=r.network?.profile;
   for(const e of events){
    const id=[r.runtime_id,e.kind,e.at,e.node,e.previous_node,e.batch_id,e.task,e.profile].join('|');
    if(seen.has(id))continue;seen.add(id);
    let category,label;
    if(e.kind==='link_changed'){
     const lastLink=rows.filter(x=>x.kind==='link_changed'&&x.runtime_id===r.runtime_id).at(-1);
     if(lastLink?.profile===e.profile)continue;
     category=e.profile==='normal'?(lastLink?'recovery':'normal'):e.profile;
     label={normal:'正常',weak:'弱网',disconnected:'断链',recovery:'链路恢复'}[category];
    }else if(e.kind==='node_offline'){category='offline';label='节点掉线 · '+e.node;}
    else if(e.kind==='node_recovered'){category='recovery';label='节点恢复 · '+e.node;}
    else if(e.kind==='task_migrated'){category='migration';label='迁移 · '+e.task+' · '+e.previous_node+' → '+e.node;}
    else if(e.kind==='task_recovered'){category='recovery';label='任务恢复 · '+e.task;}
    else continue;
    if(!label||r.at<record.start||r.at>record.end)continue;
    const failure=(r.events||[]).filter(f=>f.at<=r.at&&f.node===e.node&&(e.kind==='node_recovered'||Math.abs(f.at-e.at)<.01)).at(-1);
    rows.push({...e,id,runtime_id:r.runtime_id,category,label,seek_at:r.at,batch_id:e.batch_id||failure?.batch_id,task:e.task||failure?.task,reason:e.reason||failure?.error});
   }
  }
  return rows.sort((a,b)=>a.seek_at-b.seek_at||a.at-b.at);
 }
 function transferChain(record, events, token){
  const history=record.runtime_history||[];
  const demo=history.map(r=>r.demo).find(d=>d?.state==='completed'&&d.short_completed&&d.weak_transfer&&[d.short_completed.token,d.weak_transfer.token].includes(token));
  if(!demo)return [];
  const short=demo.short_completed, selected=demo.weak_transfer.token;
  const weak=events.find(e=>e.category==='weak'&&e.transfer_token===selected);
  const cut=events.find(e=>e.category==='disconnected'&&e.transfer_token===selected);
  const move=events.find(e=>e.category==='migration'&&e.transfer_token===selected);
  const recovery=events.find(e=>e.kind==='node_recovered'&&e.node===demo.node&&e.at>move?.at);
  const done=history.find(r=>r.demo?.short_completed?.token===short.token);
  if(!done||!weak||!cut||!move||!recovery)return [];
  const finished={id:'finished-'+short.token,label:'短传输结束',transfer_token:short.token,at:short.finished_at,seek_at:done.at,runtime_id:weak.runtime_id};
  const reselected={...weak,id:'selected-'+selected,label:'重新选择传输'};
  return [finished,reselected,weak,cut,move,recovery].map((e,i)=>({...e,category:'transfer',batch_id:i?move.batch_id:null,task:i?move.task:null}));
 }
 function filtered(events,type){return type==='all'?events:events.filter(e=>e.category===type);}
 function neighbor(events,selected,cursor,direction){
  const i=events.findIndex(e=>e.id===selected);
  if(i>=0)return events[i+direction]||null;
  return direction<0?events.filter(e=>e.seek_at<=cursor).at(-1)||null:events.find(e=>e.seek_at>=cursor)||null;
 }
 function comparison(batch,event,cursor){
  const job=batch.tasks?.[event.task];if(!job)return null;
  const attempts=(job.attempts||[]).filter(a=>a.sent_at<=cursor);
  let i=event.kind==='task_migrated'?attempts.findIndex(a=>a.node===event.node&&Math.abs(a.sent_at-event.at)<.01):attempts.length-1;
  if(i<1)return null;
  const view=a=>{
   const finished=Number.isFinite(a.received_at)&&a.received_at<=cursor;
   const output=event.task==='detect'&&finished&&a.status==='completed'&&batch.finished_at<=cursor?batch.output:null;
   return {node:a.node,status:finished?(a.error||a.status):'执行中',worker_ms:finished&&Number.isFinite(a.worker_roundtrip_s)?a.worker_roundtrip_s*1000:null,network_ms:finished&&Number.isFinite(a.network_wait_s)?a.network_wait_s*1000:null,total_ms:finished&&Number.isFinite(a.roundtrip_s)?a.roundtrip_s*1000:null,detection:event.task!=='detect'?'非检测阶段':output?.detections?'检出 '+output.detections.length+' 个目标':finished&&a.status!=='completed'?'失败，无有效检测结果':'结果尚未完成'};
  };
  return {before:view(attempts[i-1]),after:view(attempts[i])};
 }
 const api={transferChain,index,filtered,neighbor,comparison};if(typeof module!=='undefined')module.exports=api;else root.ComputeReplayEvents=api;
})(typeof window!=='undefined'?window:this);
