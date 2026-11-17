'use strict';
const el=id=>document.getElementById(id);
let latestPlan=null,scene,cloud=[],yaw=.7,pitch=.45,zoom=1,drag=null;
const stringify=x=>JSON.stringify(x,null,2);
const root=window.SenseAPI.root;
const api=(path,body)=>body===undefined?window.SenseAPI.request(path):window.SenseAPI.post(path,body);
let activeJob=null,busy=false;
async function computation(kind,parameters){
  try {
    const job=await api(`${root}/api/scenes/${el('scene').value}/jobs`,{kind,parameters});
    activeJob=job.id;el('cancel-computation').hidden=false;
    const completed=await window.SenseAPI.wait(job,j=>{el('status').textContent=j.status==='queued'?'计算已排队，可取消；记录会保留。':'正在计算，可取消；关闭页面后任务仍会继续。';});
    return await api(`${root}/api/jobs/${completed.id}/publish`,{});
  } finally {activeJob=null;el('cancel-computation').hidden=true;}
}
el('cancel-computation').onclick=async()=>{if(activeJob)try{await api(`${root}/api/jobs/${activeJob}/cancel`,{});}catch(e){el('status').textContent=e.message;}};
function base(){return `${root}/api/scenes/${el('scene').value}/engineering`;}
function volumeSettings(){return {bounds:['x0','x1','y0','y1','z0','z1'].map(id=>Number(el(id).value)),...Object.fromEntries(['cell','cochannel_db','communication_db','weather_db','pd_threshold','precision_m'].map(id=>[id,Number(el(id).value)]))};}
function action(id,fn){el(id).onclick=async()=>{
  if(busy)return;busy=true;
  const buttons=[...document.querySelectorAll('button')].filter(b=>b.id!=='cancel-computation');
  const states=buttons.map(b=>b.disabled);buttons.forEach(b=>b.disabled=true);el('scene').disabled=true;
  el('status').textContent='处理中…';
  try{await fn();el('status').textContent='完成。结果为当前场景的数值基线，请查看计算范围与假设。';}
  catch(e){el('status').textContent=e.message;}
  finally{busy=false;buttons.forEach((b,i)=>b.disabled=states[i]);el('apply-plan').disabled=!latestPlan;el('scene').disabled=false;}
};}
async function load(){latestPlan=null;el('apply-plan').disabled=true;el('resources').value='';scene=await api(`${root}/api/scenes/${el('scene').value}`);el('sensor').replaceChildren(...scene.devices.map(d=>{const o=document.createElement('option');o.value=d.id;o.textContent=d.name+' · '+d.id;return o;}));cloud=[];draw();for(const id of ['volume-stats','plan-result','calibration-result','schedule-result','evidence-result'])el(id).textContent='当前场景尚未加载结果';
const saved=await api(base()+'/report');el('evidence-result').textContent=stringify(saved);
if(saved.volume){cloud=saved.volume.cloud;el('volume-stats').textContent=stringify({...saved.volume,cloud:`${cloud.length} 个显示点`,artifact_state:saved.artifact_state?.volume});const v=saved.volume.settings;['x0','x1','y0','y1','z0','z1'].forEach((id,i)=>el(id).value=v.bounds[i]);for(const id of ['cell','cochannel_db','communication_db','weather_db','pd_threshold','precision_m'])el(id).value=v[id];draw();}
if(saved.plan){el('plan-result').textContent=stringify(saved.plan);const stale=saved.artifact_state?.['engineering-plan'];if(!stale?.scene_changed&&!stale?.calibration_changed&&saved.plan.scene_revision===scene.revision){latestPlan=saved.plan;el('apply-plan').disabled=false;}}
if(saved.calibration)el('calibration-result').textContent=stringify(saved.calibration);
if(saved.schedule)el('schedule-result').textContent=stringify(saved.schedule);
}
function draw(){const c=el('cloud'),ctx=c.getContext('2d');c.width=Math.max(600,c.clientWidth)*devicePixelRatio;c.height=480*devicePixelRatio;ctx.scale(devicePixelRatio,devicePixelRatio);const w=c.width/devicePixelRatio,h=480;ctx.clearRect(0,0,w,h);if(!cloud.length){ctx.fillStyle='#fff';ctx.fillText('计算后显示三维点云',24,32);return;}const center=[0,1,2].map(k=>cloud.reduce((s,p)=>s+p[k],0)/cloud.length);const span=Math.max(...[0,1,2].map(k=>Math.max(...cloud.map(p=>p[k]))-Math.min(...cloud.map(p=>p[k]))),1);const scale=Math.min(w,h)*.7/span*zoom;const project=p=>{const a=p.map((v,k)=>v-center[k]);const x=a[0]*Math.cos(yaw)-a[1]*Math.sin(yaw),y=a[0]*Math.sin(yaw)+a[1]*Math.cos(yaw);return [w/2+x*scale,h/2+(y*Math.sin(pitch)-a[2]*Math.cos(pitch))*scale,y*Math.cos(pitch)+a[2]*Math.sin(pitch)];};const k=Number(el('field').value);cloud.map(p=>({p,q:project(p)})).sort((a,b)=>a.q[2]-b.q[2]).forEach(({p,q})=>{let v=k===3?p[k]:k===4?(p[k]+20)/50:1-p[k]/100;ctx.fillStyle=p[k]===null?'#a0a8b0':`hsl(${Math.max(0,Math.min(1,v))*120} 75% 58%)`;ctx.fillRect(q[0],q[1],3,3);});ctx.fillStyle='#fff';ctx.fillText('X / Y / Z 局部米制坐标 · 正交投影旋转点云',20,26);}
const c=el('cloud');c.onpointerdown=e=>{drag=[e.clientX,e.clientY];c.setPointerCapture(e.pointerId);};c.onpointermove=e=>{if(!drag)return;yaw+=(e.clientX-drag[0])*.008;pitch=Math.max(-1.5,Math.min(1.5,pitch+(e.clientY-drag[1])*.008));drag=[e.clientX,e.clientY];draw();};c.onpointerup=()=>drag=null;c.onwheel=e=>{e.preventDefault();zoom=Math.max(.2,Math.min(5,zoom*Math.exp(-e.deltaY*.001)));draw();};el('field').onchange=draw;window.addEventListener('resize',draw);
action('volume',async()=>{const r=await computation('volume',volumeSettings());cloud=r.cloud;el('volume-stats').textContent=stringify({...r,cloud:`${r.cloud.length} 个显示点（统计用 ${r.evaluated_cells} 个计算网格）`});draw();});
action('plan',async()=>{const r=await computation('plan',{volume:volumeSettings(),...Object.fromEntries(['budget','coverage_target','focus_target','precision_rate_target'].map(id=>[id,Number(el(id).value)]))});latestPlan=r;el('apply-plan').disabled=false;el('plan-result').textContent=stringify(r);});
el('residual-file').onchange=async()=>{const f=el('residual-file').files[0];if(f){el('residuals').value=await f.text();el('source').value=f.name;}};
action('calibrate',async()=>{const r=await api(base()+'/calibration',{sensor_id:el('sensor').value,source:el('source').value,residuals:JSON.parse(el('residuals').value)});el('calibration-result').textContent=stringify(r);});
el('tasks').value=stringify(['discovery','localization','tracking','handover','warning'].map((kind,i)=>({id:`T${i+1}`,kind,priority:5-i,bandwidth:2,compute:1})));
action('schedule',async()=>{el('schedule-result').textContent=stringify(await api(base()+'/schedule',{tasks:JSON.parse(el('tasks').value),bandwidth:Number(el('bandwidth').value),compute:Number(el('compute').value)}));});
action('evidence',async()=>{el('evidence-result').textContent=stringify(await api(base()+'/report'));});
action('export',async()=>{const r=await api(base()+'/report');const url=URL.createObjectURL(new Blob([stringify(r)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`感知网络工程报告-${el('scene').value}.json`;a.click();URL.revokeObjectURL(url);});
el('scene').onchange=()=>load().catch(e=>el('status').textContent=e.message);
if(!root)el('gcs-link').hidden=true;
api(root+'/api/scenes').then(async r=>{el('scene').replaceChildren(...r.scenes.map(s=>{const o=document.createElement('option');o.value=s.id;o.textContent=s.name;return o;}));await load();el('status').textContent='场景就绪，可计算效能场与约束规划。';}).catch(e=>el('status').textContent=e.message);

action('read-resources',async()=>{scene=await api(`${root}/api/scenes/${el('scene').value}`);el('resources').value=stringify({source:scene.input_source||'合成演示场景',buildings:scene.buildings,devices:scene.devices,candidates:scene.candidates});});
action('save-resources',async()=>{const r=await fetch(`${root}/api/scenes/${el('scene').value}/resources`,{method:'PUT',headers:{'Content-Type':'application/json'},body:el('resources').value});const d=await r.json();if(!r.ok)throw Error(stringify(d.detail));await load();});
action('apply-plan',async()=>{if(!latestPlan)throw Error('请先生成方案');await api(base()+'/apply/'+latestPlan.id,{});await load();});
