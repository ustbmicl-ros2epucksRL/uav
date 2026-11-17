/* SITL uses the same geographic frame and relative altitude as the instruments. */
(function(root){
 const history=[];let lastStamp=null;
 function project(s,now=Date.now()/1000){
  const t=s.telemetry||{},fresh=k=>s.connected&&Number.isFinite(t[k])&&now-(s.updated_at?.[k]||0)>=0&&now-(s.updated_at?.[k]||0)<3;
  const valid=['latitude','longitude','altitude','gps_fix'].every(fresh)&&t.gps_fix>=3;
  const stamp=s.updated_at?.latitude;
  if(stamp<lastStamp){history.length=0;lastStamp=null;}
  if(valid&&stamp!==lastStamp){history.push({latitude:t.latitude,longitude:t.longitude,altitude:t.altitude,at:stamp});if(history.length>2000)history.shift();lastStamp=stamp;}
  const telemetry={};for(const [dst,src] of Object.entries({roll_deg:'roll',pitch_deg:'pitch',yaw_deg:'yaw',relative_altitude_m:'altitude',groundspeed_mps:'speed',battery_percent:'battery',gps_fix:'gps_fix'}))telemetry[dst]=fresh(src)?t[src]:null;
  return {runtime:s.compute_runtime,execution_id:s.execution_binding?.execution_id,phase:s.patrol?.status,compute:s.algorithm_live,algorithm:s.execution_binding?.algorithm,vehicle_id:'SITL-1',connected:s.connected,position_valid:valid,latitude:valid?t.latitude:null,longitude:valid?t.longitude:null,armed:s.connected?t.armed:null,heartbeat_age_s:Number.isFinite(s.updated_at?.mode)?Math.max(0,now-s.updated_at.mode):null,telemetry,track:history.slice(),video:{configured:false},anchor:s.anchor,mission:s.patrol?.route||[],source:'ardupilot-sitl'};
 }
 function scene(s,core,follow=false){
  const t=s.telemetry,a=s.anchor,point=p=>core.enu([p.latitude,p.longitude,p.altitude],a);
  const valid=s.position_valid&&['roll_deg','pitch_deg','yaw_deg'].every(k=>Number.isFinite(t[k]));
  return {anchor:a,anchorKey:`sitl:${a.latitude},${a.longitude}`,selected:'SITL-1',vehicles:valid?[{id:'SITL-1',name:s.historical?'SITL 历史回放':'ArduPilot SITL · 仿真',position:point({...s,altitude:t.relative_altitude_m}),attitude:{roll:t.roll_deg,pitch:t.pitch_deg,yaw:t.yaw_deg},home:[0,0,0],speed:t.groundspeed_mps}]:[],fence:{x0:-300,y0:-300,x1:2000,y1:2000,z1:300},mission:s.mission,track:s.track.map(point),draft:[],footprint:[],buildings:[],cruise:180,follow3d:follow,layers:{mission:true,track:true,fence:false,view:false,buildings:false}};
 }
 function recording(data){
  if(!data.anchor||![data.anchor.latitude,data.anchor.longitude].every(Number.isFinite))throw Error('记录缺少有效坐标原点');
  const track=(data.track||[]).filter(p=>Number.isFinite(p.at)).slice().sort((a,b)=>a.at-b.at);
  if(!track.length)throw Error('暂无可回放的 SITL 记录');
  return {...data,track,start:track[0].at,end:Math.max(track.at(-1).at,...(data.runtime_history||[]).map(p=>p.at))};
 }
 function replay(data,at){
  const time=Math.max(data.start,Math.min(data.end,at)),track=data.track;
  let lo=0,hi=track.length;while(lo<hi){const mid=(lo+hi)>>1;if(track[mid].at<=time)lo=mid+1;else hi=mid;}
  const index=Math.max(0,lo-1),p=track[index],age=time-p.at;
  const fresh=k=>Number.isFinite(p[k])&&age+(p.field_age_s?.[k]??0)<3;
  const telemetry={};for(const [dst,src] of Object.entries({roll_deg:'roll',pitch_deg:'pitch',yaw_deg:'yaw',relative_altitude_m:'altitude',groundspeed_mps:'speed',battery_percent:'battery',gps_fix:'gps_fix'}))telemetry[dst]=fresh(src)?p[src]:null;
  const valid=['latitude','longitude','altitude'].every(fresh)&&(!('gps_fix' in p)||fresh('gps_fix')&&p.gps_fix>=3);
  const runtime=(data.runtime_history||[]).filter(p=>p.at<=time).at(-1)||null;
  return {runtime,aoi_policy:p.aoi_policy,business_task_id:data.business_task_id,compute:p.compute,algorithm:data.algorithm,source:'sitl-recording',historical:true,recorded_at:p.at,cursor_at:time,legacy:!p.field_age_s,vehicle_id:'SITL-1',connected:age<3,position_valid:valid,latitude:valid?p.latitude:null,longitude:valid?p.longitude:null,armed:p.armed,telemetry,anchor:data.anchor,mission:data.patrol?.route||[],track:track.slice(0,index+1).filter(q=>[q.latitude,q.longitude,q.altitude].every(Number.isFinite)),video:{configured:false}};
 }
 const api={project,scene,recording,replay};root.SitlTwin=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
