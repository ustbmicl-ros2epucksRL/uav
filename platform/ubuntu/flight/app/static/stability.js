(()=>{
 const el=id=>document.getElementById(id),dialog=el('stability-dialog');
 const selectionKey="stability.selectedJob";
 let selected=localStorage.getItem(selectionKey),busy=false,polling=false,lastRendered=null,lastCatalogue=null;
 const names={running:'运行中',passed:'通过',failed:'失败',interrupted:'中断',not_run:'未运行',unknown:'待确认'};
 const overall=job=>job.retry_acceptance?.status||regressionStatus(job);
 const regressionStatus=job=>job.page_acceptance?.status==='failed'?'failed':job.status==='passed'&&job.page_acceptance?job.page_acceptance.status:job.status;
 const stages={'sitl-flow':'任务创建 / SITL 飞行与故障恢复',archive:'归档校验',frontend:'前端逻辑测试',browser:'Chrome 回放验收'};
 const fileUrl=(id,path)=>'/api/stability/'+encodeURIComponent(id)+'/files/'+path.split('/').map(encodeURIComponent).join('/');
 const text=(parent,value,tag='p')=>{const n=document.createElement(tag);n.textContent=value;parent.append(n);return n;};
 const link=(parent,id,path,label)=>{const a=text(parent,label,'a');a.href=fileUrl(id,path);a.target='_blank';a.rel='noopener';return a;};
 async function api(path,body){const response=await fetch('/api/stability'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await response.json();if(!response.ok)throw Error(data.detail||'请求失败');return data;}
 function render(job){
  const snapshot=JSON.stringify(job);if(snapshot===lastRendered)return;
  const box=el('stability-detail'),open=new Set([...box.querySelectorAll('details[open]')].map(n=>n.dataset.round));box.replaceChildren();lastRendered=snapshot;
  text(box,(names[overall(job)]||overall(job))+' · '+job.requested_rounds+' 轮 · '+job.id,'h3');
  box.dataset.roundStatus=job.status;
  if(job.retry_acceptance){
   text(box,'双机失败重试验收 · '+job.retry_acceptance.step,'h4');
   link(box,job.id,'retry-acceptance.log','验收日志');
   if(job.retry_progress){
    for(const r of job.retry_progress.rounds.filter(r=>r.round>=2))
     text(box,'续跑第 '+r.round+' 轮 · '+(names[r.status]||r.status)+(r.task_id?' · '+r.task_id:''));
   }
   if(job.retry_acceptance.report)link(box,job.id,'retry-acceptance.html','打开双机失败重试统一报告');
  }
  if(job.reason)text(box,job.reason);
  if(job.launcher_log)link(box,job.id,'launcher.log','查看启动日志 ↗');
  if(job.report_available)link(box,job.id,'summary.html','打开统一稳定性报告 ↗');
  if(job.retry_of)link(text(box,''),job.retry_of,'summary.html','原失败报告 · '+job.retry_of);
  for(const id of job.retries||[])link(text(box,''),id,'summary.html','重试报告 · '+id);
  const ui=job.page_acceptance;
  if(ui){
   text(box,'页面验收 · '+(names[ui.status]||ui.status)+(ui.status==='running'?' · '+ui.step:''),'h4');
   if(ui.error)text(box,ui.error);
   if(ui.status==='failed'){
    const retry=text(box,'重试页面验收','button');
    retry.id='stability-retry';
    retry.onclick=()=>start(true,job.id);
   }
   const files=ui.evidence||[];
   if(files.length){
    const details=document.createElement('details');details.dataset.round=job.id+':ui';
    details.open=ui.status==='failed'||open.has(details.dataset.round);
    text(details,'页面验收日志与截图','summary');box.append(details);
    for(const file of files)link(text(details,''),job.id,file,file.split('/').pop());
   }
  }
  for(const round of job.rounds){
   const row=document.createElement('section');row.className='stability-round';box.append(row);
   text(row,'第 '+round.round+' 轮 · '+(names[round.status]||round.status)+' · '+(round.task_id||'尚未创建任务'),'h4');
   text(row,(stages[round.stage]||round.stage||'等待执行')+(round.duration_s!=null?' · '+round.duration_s+' 秒':''));
   if((job.cross_platform || job.dual_exercise) && round.status==='failed' && job.status==='failed' && !job.retries?.length){
    const retry=text(row,'重试第 '+round.round+' 轮','button');
    retry.onclick=async()=>{
     retry.disabled=true;
     try{selected=(await api('/'+encodeURIComponent(job.id)+'/rounds/'+round.round+'/retry',{})).id;await refresh();}
     catch(error){el('stability-feedback').textContent=error.message;retry.disabled=false;}
    };
   }
   if(round.reason)text(row,'失败原因：'+round.reason);
   for(const warning of round.warnings||[])text(row,'提示：'+warning);
   if(round.artifacts.length){
    const details=document.createElement('details');details.dataset.round=job.id+':'+round.round;details.open=open.has(details.dataset.round);text(details,'日志、报告与截图','summary');row.append(details);
    for(const file of round.artifacts){
     const p=document.createElement('p');details.append(p);link(p,job.id,file.path,file.name);
     if(file.name.endsWith('.png')){const image=document.createElement('img');image.src=fileUrl(job.id,file.path);image.alt='第 '+round.round+' 轮 '+file.name;image.loading='lazy';image.style.maxWidth='100%';details.append(image);}
    }
   }
  }
 }
 async function refresh(){
  if(polling)return;polling=true;
  try{
   const data=await api(''),select=el('stability-history'),key=JSON.stringify(data.items.map(j=>[j.id,overall(j),j.requested_rounds]));
   if(key!==lastCatalogue){select.replaceChildren();lastCatalogue=key;
   for(const job of data.items){const option=document.createElement('option');option.value=job.id;option.textContent=job.id+' · '+(job.retry_acceptance?'双机失败重试验收 · ':job.page_acceptance?'页面验收 · ':'')+(names[overall(job)]||overall(job))+' · '+job.requested_rounds+' 轮';select.append(option);}}
   if(!data.items.some(j=>j.id===selected))selected=(data.items.find(j=>j.status==="running")||data.items[0])?.id||null;
   if(selected)localStorage.setItem(selectionKey,selected);
   select.value=selected||'';el('stability-start').disabled=busy||data.items.some(j=>j.status==='running'||j.page_acceptance?.status==='running');
   el('stability-page-start').disabled=el('stability-start').disabled;
   if(el('cross-start'))el('cross-start').disabled=el('stability-start').disabled;
   if(el('dual-stability-start'))el('dual-stability-start').disabled=el('stability-start').disabled;
   if(el('dual-accept-start'))el('dual-accept-start').disabled=el('stability-start').disabled;
   if(selected){const detail=await api('/'+encodeURIComponent(selected));
    if(detail.retry_acceptance && detail.retries?.length)detail.retry_progress=await api('/'+encodeURIComponent(detail.retries.at(-1)));
    if(selected!==detail.id)return;
    render(detail);if(el('stability-retry'))el('stability-retry').disabled=el('stability-start').disabled;}else el('stability-detail').textContent='暂无历史回归';
  }catch(error){el('stability-feedback').textContent=error.message;}finally{polling=false;}
 }
 el('stability-open').onclick=()=>{el('settings-dialog').close();dialog.showModal();refresh();};
 el('stability-close').onclick=()=>dialog.close();
 el('stability-refresh').onclick=refresh;
 el('stability-history').onchange=()=>{selected=el('stability-history').value;refresh();};
 async function start(pageAcceptance,retryOf,crossPlatform=false,dualExercise=false,dualAcceptance=false){
  if(busy)return;
  const rounds=(pageAcceptance||dualAcceptance)?3:Number(el('stability-rounds').value);if(!Number.isInteger(rounds)||rounds<1||rounds>20){el('stability-feedback').textContent='运行轮数需为 1–20 的整数';return;}
  busy=true;el('stability-page-start').disabled=true;el('stability-start').disabled=true;
  try{const job=await api(retryOf?'/'+encodeURIComponent(retryOf)+'/retry':'',{rounds,page_acceptance:pageAcceptance,cross_platform:crossPlatform,dual_exercise:dualExercise,dual_retry_acceptance:dualAcceptance});selected=job.id;localStorage.setItem(selectionKey,selected);el('stability-feedback').textContent='回归已启动，可关闭窗口，稍后查看进度和报告。';}
  catch(error){el('stability-feedback').textContent=error.message;}
  finally{busy=false;await refresh();}
 };
 if(location.port==='8772'){
  const button=document.createElement('button');
  button.id='cross-start';button.textContent='跨平台任务回归';
  button.onclick=()=>start(false,null,true);
  el('stability-page-start').after(button);
  const dual=document.createElement('button');
  dual.id='dual-stability-start';dual.textContent='双机禁飞区与交会回归';
  dual.onclick=()=>start(false,null,false,true);
  button.after(dual);
  const acceptance=document.createElement('button');
  acceptance.id='dual-accept-start';acceptance.textContent='三轮双机失败重试验收';
  acceptance.onclick=()=>start(false,null,false,true,true);
  dual.after(acceptance);
 }
 el('stability-start').onclick=()=>start(false);
 el('stability-page-start').onclick=()=>start(true);
 setInterval(()=>{if(dialog.open)refresh();},3000);
})();
