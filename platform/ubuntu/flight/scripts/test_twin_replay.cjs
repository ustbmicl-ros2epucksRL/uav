// Validate the actual SITL acceptance recording through the production replay adapter.
const fs=require('node:fs'),assert=require('node:assert/strict');
const adapter=require('../app/static/sitl-twin.js'),core=require('../app/static/gcs-core.js');
const report=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const recording=adapter.recording(report.result);let checked=0,stale=0;
for(let i=0;i<recording.track.length;i+=Math.max(1,Math.floor(recording.track.length/100))){
 const p=recording.track[i],snapshot=adapter.replay(recording,p.at),scene=adapter.scene(snapshot,core);
 assert(snapshot.track.every(q=>q.at<=snapshot.cursor_at));assert.equal(snapshot.telemetry.relative_altitude_m,p.altitude);
 if(scene.vehicles.length){const v=scene.vehicles[0];assert.equal(v.position[2],p.altitude);assert.equal(v.attitude.roll,p.roll);assert.equal(v.attitude.pitch,p.pitch);assert.equal(v.attitude.yaw,p.yaw);const geo=core.geo(v.position,recording.anchor);assert(Math.abs(geo[0]-p.latitude)<1e-8);assert(Math.abs(geo[1]-p.longitude)<1e-8);checked++;}else stale++;
}
assert(checked>10);assert(!adapter.replay(recording,recording.end).armed);
console.log(JSON.stringify({passed:true,source:'actual-ardupilot-sitl-recording',samples:recording.track.length,checked,unavailable:stale,duration_seconds:recording.end-recording.start,final_armed:false},null,2));
