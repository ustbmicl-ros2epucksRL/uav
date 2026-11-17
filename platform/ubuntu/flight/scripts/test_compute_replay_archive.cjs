const fs=require('fs'),assert=require('assert/strict');
const path=require('path');
const root=path.resolve(__dirname,'../../../..'),base=root+'/platform/ubuntu/flight/app/static/';
if(!process.argv[2]||!process.argv[3])throw Error('Usage: node test_compute_replay_archive.cjs flow-report.json checks.json');
const E=require(base+'compute-replay-events.js'),S=require(base+'sitl-twin.js'),T=require(base+'compute-topology.js');
const report=JSON.parse(fs.readFileSync(process.argv[2]));
const record=S.recording(report.result),events=E.index(record),categories=[...new Set(events.map(e=>e.category))];
for(const k of ['normal','weak','disconnected','offline','migration','recovery']){
 const list=E.filtered(events,k);assert(list.length,k);
 list.forEach((e,i)=>{assert.equal(E.neighbor(list,e.id,e.seek_at,-1),list[i-1]||null);assert.equal(E.neighbor(list,e.id,e.seek_at,1),list[i+1]||null);});
}
const checked=[];
for(const e of events){
 const display=S.replay(record,e.seek_at),map=T.project(display);assert(map,e.label);assert.equal(display.runtime.at,e.seek_at);
 if(['normal','weak','disconnected'].includes(e.category))assert.equal(map.profile,e.category);
 if(e.category==='offline')assert.equal(map.nodes.find(n=>n.id===e.node).status,'offline');
 if(e.category==='migration'){
 const b=report.batches.find(b=>b.id===e.batch_id);assert(b);const pair=E.comparison(b,e,record.end);assert(pair);assert.equal(pair.before.node,e.previous_node);assert.equal(pair.after.node,e.node);assert(pair.after.total_ms>0);assert(map.migrations.some(m=>m.batch_id===e.batch_id&&m.task===e.task));
 const early=E.comparison(b,e,e.seek_at);assert(early);
 const attempts=b.tasks[e.task].attempts;const selected=attempts.find(a=>a.node===e.node&&Math.abs(a.sent_at-e.at)<.01);
 if(selected.received_at>e.seek_at){assert.equal(early.after.total_ms,null);assert.equal(early.after.worker_ms,null);}
 if(e.task==='detect'){assert(b.output?.frame?.url);assert(Array.isArray(b.output.detections));assert.equal(pair.after.detection,'检出 '+b.output.detections.length+' 个目标');if(b.finished_at>e.seek_at)assert.equal(early.after.detection,'结果尚未完成');}
 assert(b.output?.frame?.sha256);assert(b.output.synthetic===false);
 assert(b.tasks[e.task].attempts.every(a=>a.frame_sha256===b.output.frame.sha256));
 checked.push({event:e.label,seconds:e.seek_at-record.start,batch:e.batch_id,before:pair.before,after:pair.after,targets:b.output?.detections?.length});
 }
}
const demo=record.runtime_history.map(r=>r.demo).filter(d=>d?.state==='completed').at(-1);
assert(demo, 'missing completed archived scenario');
const short=demo.short_completed, token=demo.weak_transfer.token;
assert(short && short.status==='completed' && short.remaining_mbit===0 && short.size_bytes===64);
assert.notEqual(short.token,token);
const shortWeak=events.find(e=>e.category==='weak' && e.transfer_token===short.token);
const weak=events.find(e=>e.category==='weak' && e.transfer_token===token);
const cut=events.find(e=>e.category==='disconnected' && e.transfer_token===token);
const failure=demo.evidence.find(e=>e.kind==='transfer_failed' && e.transfer_token===token && e.reason==='link_disconnected');
assert(shortWeak && weak && cut && failure);
const migration=events.find(e=>e.category==='migration' && e.transfer_token===token && e.batch_id===failure.batch_id && e.task===failure.task);
const restored=events.find(e=>e.kind==='node_recovered' && e.node===demo.node && e.at>migration?.at);
assert(migration && restored);
const attempts=report.batches.find(b=>b.id===failure.batch_id).tasks[failure.task].attempts;
const failedIndex=attempts.findIndex(a=>a.token===token && a.error==='link_disconnected');
assert(failedIndex>=0 && attempts[failedIndex+1]?.node===migration.node);
assert(attempts[failedIndex].transfers.some(f=>f.token===token && f.status==='failed'));
assert(events.some(e=>e.category==='offline' && e.node===restored.node && e.at>=migration.at && e.at<restored.at));
assert(shortWeak.at<=short.finished_at && short.finished_at<=weak.at && weak.at<cut.at && cut.at<=failure.at && failure.at<=migration.at);
const chain={short_token:short.token,transfer_token:token,batch:failure.batch_id,task:failure.task,from:migration.previous_node,to:migration.node,recovered_node:restored.node};
const result={short_transfer:chain,task:report.task_id,record:record.record_id,categories,events:events.map(e=>({category:e.category,label:e.label,transfer_token:e.transfer_token,seconds:+(e.seek_at-record.start).toFixed(1)})),migration_checks:checked,counts:report.runtime.counts,telemetry_frames:record.track.length,runtime_snapshots:record.runtime_history.length};
fs.writeFileSync(process.argv[3],JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
