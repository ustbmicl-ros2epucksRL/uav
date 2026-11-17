const test=require('node:test'),assert=require('node:assert/strict');
const {index}=require('../app/static/compute-replay-events.js');
test('events are deduplicated across snapshots and seek first saved evidence rather than earlier event time',()=>{
 const event={kind:'link_changed',profile:'weak',at:10.2};
 const h=(at,events)=>({at,runtime_id:'r',network:{profile:'weak'},scheduling_events:events});
 const rows=index({start:10,end:20,runtime_history:[h(12,[event]),h(11,[event]),h(13,[event,{kind:'node_offline',at:14,node:'edge-a'}])]});
 assert.equal(rows.length,1);assert.equal(rows[0].seek_at,11);assert.equal(rows[0].at,10.2);
});
test('older archives have state markers and unrelated failures are never linked',()=>{
 const rows=index({start:10,end:20,runtime_history:[{at:10,network:{profile:'normal'}},{at:12,network:{profile:'weak'}},{at:14,network:{profile:'normal'},scheduling_events:[{kind:'node_offline',at:13.9,node:'edge-a'}],events:[{at:11,node:'edge-a',batch_id:'unrelated'}]}]});
 assert.deepEqual(rows.map(r=>r.category),['normal','weak','offline','recovery']);assert.equal(rows.find(r=>r.category==='offline').batch_id,undefined);
});
test('filtered navigation respects boundaries, empty filters and simultaneous events',()=>{
 const {filtered,neighbor}=require('../app/static/compute-replay-events.js');
 const events=[{id:'a',category:'weak',seek_at:1},{id:'b',category:'migration',seek_at:2},{id:'c',category:'migration',seek_at:2}];
 const subset=filtered(events,'migration');assert.equal(subset.length,2);
 assert.equal(neighbor(subset,'b',2,1).id,'c');assert.equal(neighbor(subset,'c',2,-1).id,'b');
 assert.equal(neighbor(subset,'b',2,-1),null);assert.equal(neighbor(subset,'c',2,1),null);
 assert.equal(neighbor(filtered(events,'offline'),null,0,1),null);
 assert.equal(neighbor(subset,null,1,1).id,'b');assert.equal(neighbor(subset,null,3,-1).id,'c');
});
test('migration comparison selects the event pair and hides future timing and detections',()=>{
 const {comparison}=require('../app/static/compute-replay-events.js');
 const batch={finished_at:8,output:{detections:[{}]},tasks:{detect:{attempts:[
 {node:'cloud',sent_at:1,received_at:2,status:'failed',error:'timeout'},
 {node:'device',sent_at:3,received_at:4,status:'completed',worker_roundtrip_s:.2,network_wait_s:.1,roundtrip_s:.3},
 {node:'edge-a',sent_at:9,received_at:10,status:'completed'}]}}};
 const event={kind:'task_migrated',task:'detect',node:'device',at:3};
 const early=comparison(batch,event,3);assert.equal(early.before.node,'cloud');assert.equal(early.before.worker_ms,null);assert.equal(early.after.total_ms,null);assert.equal(early.after.detection,'结果尚未完成');
 assert.equal(comparison(batch,event,5).after.detection,'结果尚未完成');
 const late=comparison(batch,event,11);assert.equal(late.after.node,'device');assert.equal(late.after.total_ms,300);assert.equal(late.after.detection,'检出 1 个目标');assert.equal(late.before.detection,'失败，无有效检测结果');
 assert.equal(comparison(batch,{task:'unknown'},11),null);
});
