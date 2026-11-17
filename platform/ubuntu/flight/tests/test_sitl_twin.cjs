const test=require('node:test'),assert=require('node:assert/strict');
const twin=require('../app/static/sitl-twin.js'),core=require('../app/static/gcs-core.js');
const sample=()=>({connected:true,anchor:{latitude:39.05,longitude:115.98},telemetry:{latitude:39.051,longitude:115.982,altitude:80,roll:12,pitch:-8,yaw:90,speed:5,battery:85,gps_fix:6,armed:true},updated_at:Object.fromEntries(['latitude','longitude','altitude','roll','pitch','yaw','speed','battery','gps_fix','mode'].map(k=>[k,100])),patrol:{route:[[0,0,40],[200,100,80]]}});
test('SITL position, complete attitude and planned route share the instrument frame',()=>{const s=twin.project(sample(),101),d=twin.scene(s,core,true),v=d.vehicles[0];assert.equal(v.position[2],80);assert.equal(v.attitude.roll,12);assert.equal(v.attitude.pitch,-8);assert.equal(v.attitude.yaw,90);const geo=core.geo(v.position,s.anchor);assert(Math.abs(geo[0]-39.051)<1e-9);assert(Math.abs(geo[1]-115.982)<1e-9);assert.equal(d.track.at(-1)[2],80);assert.equal(d.mission.length,2);assert(d.follow3d);});
test('stale or disconnected SITL removes live aircraft without fabricating a replacement',()=>{const s=twin.project(sample(),104);assert(!s.position_valid);assert.equal(s.telemetry.roll_deg,null);assert.equal(twin.scene(s,core).vehicles.length,0);assert(s.track.length);const p=sample();p.connected=false;assert(!twin.project(p,101).position_valid);});
test('polling the same sample does not duplicate track points; new session clock resets history',()=>{const a=twin.project(sample(),101);assert.equal(twin.project(sample(),101).track.length,a.track.length);const p=sample();p.updated_at=Object.fromEntries(Object.keys(p.updated_at).map(k=>[k,10]));assert.equal(twin.project(p,11).track.length,1);});
test('replay samples all instruments and geometry from one timestamp, clips future track, and exposes gaps',()=>{
 const data=twin.recording({anchor:{latitude:39,longitude:116},track:[{at:10,latitude:39,longitude:116,altitude:10,roll:1,pitch:2,yaw:359,speed:3,battery:80},{at:11,latitude:39.001,longitude:116.001,altitude:20,roll:4,pitch:5,yaw:1,speed:6,battery:79},{at:20,latitude:39.002,longitude:116.002,altitude:30,roll:7,pitch:8,yaw:90,speed:7,battery:78,field_age_s:{roll:5}}]});
 const s=twin.replay(data,11.5);assert.equal(s.recorded_at,11);assert.equal(s.telemetry.roll_deg,4);assert.equal(s.telemetry.relative_altitude_m,20);assert.equal(s.track.length,2);assert.equal(twin.scene(s,core).vehicles[0].attitude.yaw,1);
 assert.equal(twin.replay(data,15).position_valid,false);assert.equal(twin.replay(data,20).telemetry.roll_deg,null);assert.equal(twin.scene(twin.replay(data,20),core).vehicles.length,0);
 assert.equal(twin.replay(data,999).cursor_at,20);assert.throws(()=>twin.recording({anchor:data.anchor,track:[]}));
});
test('historical compute follows the same telemetry cursor and never borrows future decisions',()=>{
 const data=twin.recording({anchor:{latitude:39,longitude:116},algorithm:{experiment_id:'run'},track:[{at:10,latitude:39,longitude:116,altitude:40,compute:{at:10,result:{node:'edge-a'}}},{at:20,latitude:39,longitude:116,altitude:80,compute:{at:20,result:{node:'edge-b'}}}]});
 const first=twin.replay(data,12),second=twin.replay(data,20);
 assert.equal(first.compute.result.node,'edge-a');assert.equal(second.compute.result.node,'edge-b');
 assert.equal(first.algorithm.experiment_id,'run');assert.equal(first.recorded_at,10);
});

test('actual process evidence follows archive cursor and includes late returned results',()=>{
 const data=twin.recording({anchor:{latitude:39,longitude:116},track:[{at:10,latitude:39,longitude:116,altitude:40},{at:11,latitude:39,longitude:116,altitude:40}],runtime_history:[{at:10.5,state:'running',counts:{completed:0}},{at:13,state:'completed',counts:{completed:1}}]});
 assert.equal(data.end,13);
 assert.equal(twin.replay(data,10).runtime,null);
 assert.equal(twin.replay(data,11).runtime.counts.completed,0);
 assert.equal(twin.replay(data,13).runtime.counts.completed,1);
});

test('node outage and recovery replay cannot leak a future recovery',()=>{
 const data=twin.recording({anchor:{latitude:39,longitude:116},track:[{at:10,latitude:39,longitude:116,altitude:40},{at:20,latitude:39,longitude:116,altitude:40}],runtime_history:[{at:11,nodes:[{id:'cloud',offline:true}],scheduling_events:[{kind:'node_offline',at:11}]},{at:15,nodes:[{id:'cloud',offline:false}],scheduling_events:[{kind:'node_offline',at:11},{kind:'node_recovered',at:15}]}]});
 assert.equal(twin.replay(data,12).runtime.nodes[0].offline,true);
 assert.equal(twin.replay(data,12).runtime.scheduling_events.length,1);
 assert.equal(twin.replay(data,16).runtime.nodes[0].offline,false);
 assert.equal(twin.replay(data,16).runtime.scheduling_events[1].kind,'node_recovered');
});

test('network replay preserves link revision and does not reveal future recovery',()=>{
 const data=twin.recording({anchor:{latitude:39,longitude:116},track:[{at:10,latitude:39,longitude:116,altitude:40},{at:20,latitude:39,longitude:116,altitude:40}],runtime_history:[{at:11,network:{profile:'weak',revision:1}},{at:13,network:{profile:'disconnected',revision:2}},{at:16,network:{profile:'normal',revision:3}}]});
 assert.equal(twin.replay(data,12).runtime.network.profile,'weak');
 assert.equal(twin.replay(data,14).runtime.network.profile,'disconnected');
 assert.equal(twin.replay(data,17).runtime.network.revision,3);
});
