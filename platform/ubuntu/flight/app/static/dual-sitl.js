(()=>{
 const el=id=>document.getElementById('dual-'+id), dialog=el('dialog');
 let record,playing=false,pending=false;
 const api=async(path='',options={})=>{
  const res=await fetch('/api/dual-sitl'+path,options),data=await res.json();
  if(!res.ok)throw Error(data.detail);return data;
 };
 function render(){
  const frames=record.frames,index=Number(el('time').value),f=frames[index],ctx=el('chart').getContext('2d');
  ctx.clearRect(0,0,640,290);el('slot').textContent='';
  el('status').textContent=record.id+' · '+({starting:'启动两台飞控',flying:'双机飞行中',completed:'双机已落地上锁',failed:'失败'}[record.status])+' '+(record.error||'');
  el('create').disabled=['starting','flying'].includes(record.status);el('play').disabled=!frames.length;
  const events=el('events');events.replaceChildren();
  frames.forEach((entry,i)=>{if(!entry.blocked_slot&&entry.dispatch!=='blocked')return;
   const button=document.createElement('button');button.textContent=entry.slot+' · '+(entry.injection||entry.phase);
   button.onclick=()=>{el('time').value=i;el('time').oninput();};events.append(button);
  });
  if(!f)return;
  const visible=frames.slice(0,index+1),east=frames.flatMap(p=>p.after||p.telemetry.map(t=>t.xyz)).map(p=>p[0]);
  const left=Math.min(0,...east),right=Math.max(25,...east,...frames.flatMap(p=>p.targets.map(t=>t[0])));
  function line(points,color){
   ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=2;
   points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.stroke();
   const [x,y]=points.at(-1);ctx.fillStyle=color;ctx.beginPath();ctx.arc(x,y,3,0,2*Math.PI);ctx.fill();
  }
  ctx.fillStyle='#71aac1';ctx.fillText('本地东 / 北 (m) · 蓝 1 号机 · 橙 2 号机',8,14);
  for(let i=0;i<2;i++)line(visible.flatMap(p=>[p.telemetry[i].xyz,...(p.after?[p.after[i]]:[])]).map(v=>[15+590*(v[0]-left)/(right-left),185-1.65*v[1]]),['#2faacb','#ef973d'][i]);
  const blocked=f.dispatch==='blocked'?f:frames.find(p=>p.slot===f.blocked_slot);
  if(blocked){
   const xy=v=>[15+590*(v[0]-left)/(right-left),185-1.65*v[1]];
   ctx.setLineDash([4,3]);
   blocked.targets.forEach((v,i)=>line([xy(blocked.telemetry[i].xyz),xy(v)],'#dc6578'));ctx.setLineDash([]);
   if(f.blocked_slot)f.targets.forEach((v,i)=>line([xy(f.telemetry[i].xyz),xy(v)],'#54b287'));
   for(const c of blocked.safety.contacts||[]){const [x,y]=xy(c.position);ctx.fillStyle='#dc6578';ctx.fillText('×',x-3,y+3);}
   ctx.fillStyle='#71aac1';ctx.fillText('红虚线 原拟航段 · × 冲突位置 · 绿色 调整航段',310,14);
  }
  ctx.fillStyle='#71aac1';ctx.fillText('共享队列平均 AoI (模型秒)',8,209);
  const peak=Math.max(1,...frames.map(p=>p.mean_age_s));
  line(visible.map((p,i)=>[12+i*610/Math.max(1,frames.length-1),280-60*p.mean_age_s/peak]),'#54b287');
  el('slot').textContent='时隙 '+f.slot+' · 两机分配传感器 '+f.sensor.join(' / ')+' · '+f.delivered.map(v=>v?'送达':'未送达').join(' / ')+' · 平均 AoI '+f.mean_age_s.toFixed(2)+' s · '+(f.safety.passed?'改航检查通过':'改航已阻止')+' · 预测最近距离 '+f.safety.separation_m+' m · '+(f.phase||'巡检')+(blocked?' · '+blocked.safety.reasons.join(' / '):'');
 }
 async function load(id){
  const data=await api('/'+id);if(el('history').value!==id)return;
  const live=!record||record.id!==id||record.status==='flying'||record.status==='starting';
  record=data;el('time').max=Math.max(0,data.frames.length-1);if(live)el('time').value=el('time').max;
  render();
 }
 async function open(id){
  const ids=await api();el('history').replaceChildren(...ids.map(v=>new Option(v,v)));
  if(!dialog.open)dialog.showModal();if(ids.length){el('history').value=ids.includes(id)?id:ids[0];await load(el('history').value);}
 }
 const guard=fn=>async()=>{try{await fn();}catch(e){el('status').textContent=e.message;}};
 el('open').onclick=guard(async()=>{document.getElementById('settings-dialog').close();await open();});
 el('create').onclick=guard(async()=>{el('create').disabled=true;try{await open((await api(el('exercise').checked?'?exercise=true':'',{method:'POST'})).id);}catch(e){el('create').disabled=false;throw e;}});
 el('close').onclick=()=>dialog.close();el('history').onchange=guard(()=>load(el('history').value));
 el('time').oninput=()=>{playing=false;el('play').textContent='播放';render();};
 el('play').onclick=()=>{playing=!playing;if(playing&&el('time').value===el('time').max)el('time').value=0;el('play').textContent=playing?'暂停':'播放';render();};
 setInterval(guard(async()=>{if(!dialog.open||!record||pending)return;
  if(playing){if(+el('time').value<+el('time').max){el('time').value=Number(el('time').value)+1;render();}else{playing=false;el('play').textContent='播放';}}
  else if(['starting','flying'].includes(record.status)){pending=true;try{await load(record.id);}finally{pending=false;}}
 }),800);
 const id=new URLSearchParams(location.search).get('dual');if(id)guard(()=>open(id))();
})();
