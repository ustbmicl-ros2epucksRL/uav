'use strict';
const root=location.pathname.startsWith('/network')?'/network':'',taskId=Number(location.pathname.split('/').pop()),$=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/申请书|申报书/g,'任务').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const titles={tasks:'规划任务',resources:'资源配置',diagnosis:'感知诊断',planning:'网络规划',plans:'方案管理',acceptance:'任务验证',demonstration:'示范应用',research:'研究问题',agents:'研究智能体',learning:'课程学习'};
let task,challengeIndex=0;
const draftKey='research-design-v1-'+taskId;
const text=(x,y,t,extra='')=>`<text x="${x}" y="${y}" ${extra}>${esc(t)}</text>`;
let labSettings={},lastExperiment=null;
function mountControls(){
 const spec=ResearchLab.all.find(x=>x.task===taskId&&x.challenge===challengeIndex);
 const value=labSettings[spec.key]??spec.value;
 $('controls').innerHTML=`<label>${esc(spec.label)} / ${esc(spec.unit)} <output id="lab-value">${value}</output><input id="lab-parameter" type="range" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${value}"></label>`;
 $('lab-parameter').oninput=()=>{labSettings[spec.key]=Number($('lab-parameter').value);renderVisual();};
 renderVisual();
}
function renderVisual(){
 const spec=ResearchLab.all.find(x=>x.task===taskId&&x.challenge===challengeIndex),v=Number($('lab-parameter').value),r=ResearchLab.execute(spec.key,v);lastExperiment=r;
 $('lab-value').textContent=v;$('visual-title').textContent=task.challenges[challengeIndex].name;
 $('visual-explanation').textContent=spec.assumption;
 const max=Math.max(1,...r.values.filter(x=>x!==null)),height=64+r.values.length*60;
 let body=text(28,30,'同条件算例对照 · '+r.unit);
 r.values.forEach((value,i)=>{const y=60+i*60;body+=text(28,y,r.labels[i]);if(value!==null)body+=`<rect x="260" y="${y-17}" width="${Math.max(1,value/max*330)}" height="24" rx="4" fill="${i===0?'#688ca3':'#38b2ae'}"/>`;
 body+=text(605,y,value===null?'不可判定':Number(value.toFixed(3)).toLocaleString());});
 $('visual').innerHTML=`<svg viewBox="0 0 760 ${height}" role="img" aria-label="${esc(task.challenges[challengeIndex].name)}"><title>${esc(task.challenges[challengeIndex].name)}</title>${body}</svg>`;
 $('observations').innerHTML=`<div class="observation">实验编号<strong>${taskId}.${challengeIndex+1}</strong></div><div class="observation">证据类型<strong>解析 / 合成算例</strong></div>`;
 $('model-note').textContent=r.detail;
 $('experiment-boundary').textContent=spec.boundary;
 $('boundary-results').innerHTML=[spec.min,spec.value,spec.max].filter((x,i,a)=>a.indexOf(x)===i).map(value=>{const result=ResearchLab.execute(spec.key,value);return `<tr><td>${value}</td><td>${result.values.map((x,i)=>esc(result.labels[i])+': '+(x===null?'不可判定':Number(x.toFixed(3)))).join('<br>')}</td></tr>`;}).join('');
}
function chooseChallenge(i){challengeIndex=i;const c=task.challenges[i];$('challenge-buttons').innerHTML=task.challenges.map((v,j)=>`<button data-challenge="${j}" class="${j===i?'active':''}" aria-pressed="${j===i}">${String(j+1).padStart(2,'0')} · ${esc(v.name)}</button>`).join('');document.querySelectorAll('[data-challenge]').forEach(b=>b.onclick=()=>chooseChallenge(Number(b.dataset.challenge)));$('challenge-detail').innerHTML=`<h3>${esc(c.name)}</h3><p>${esc(c.problem)}</p><p><strong>需要警惕的失败表现</strong><br>${esc(c.failure)}</p><p class="muted small">左侧实验与下方验证设计均对应当前难点。</p>`;$('validation').innerHTML=[['控制变量',c.variable],['基线与对照',c.baseline],['观察指标',c.metric],['需要的证据',c.evidence]].map(([h,v])=>`<div class="validation-step"><b>${h}</b><p>${esc(v)}</p></div>`).join('');mountControls();}
function currentDesign(){return {schema_version:'1',task_id:taskId,title:task.title,pdf_pages:task.pages,challenge:task.challenges[challengeIndex],lab_settings:labSettings,experiment:lastExperiment,hypothesis:$('hypothesis').value,notes:$('notes').value,evidence_status:'design_only',saved_at:new Date().toISOString()};}
$('save-notes').onclick=()=>{try{localStorage.setItem(draftKey,JSON.stringify(currentDesign()));$('draft-status').textContent='实验草稿已保存到当前浏览器；没有创建验收结论。';}catch(e){$('draft-status').textContent='本机存储不可用，请使用导出保存草稿。';}};
$('export-design').onclick=()=>{const u=URL.createObjectURL(new Blob([JSON.stringify(currentDesign(),null,2)],{type:'application/json'})),a=document.createElement('a');a.href=u;a.download='子任务'+taskId+'-问题验证设计.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);};
$('reset-controls').onclick=()=>{delete labSettings[taskId+'-'+challengeIndex];mountControls();};
$('navigation').innerHTML=Object.entries(titles).map(([k,v])=>`<a class="${k==='research'?'active':''}" href="${root}/${k}">${v}</a>`).join('');
(async()=>{try{const response=await fetch(root+'/api/research-analysis/tasks');if(!response.ok)throw Error('研究任务内容加载失败');const tasks=await response.json();task=tasks.find(t=>t.id===taskId);if(!task)throw Error('未知研究子任务');document.title='子任务'+taskId+' · '+task.short;document.querySelector('h1').textContent='子任务 '+taskId+' · '+task.short;$('research-nav').innerHTML=tasks.map(t=>`<a class="${t.id===taskId?'active':''}" href="${root}/research/${t.id}${esc(location.search)}">${t.id}. ${esc(t.short)}</a>`).join('');$('hero').innerHTML=`<small>RESEARCH TASK ${String(taskId).padStart(2,'0')}</small><p>${esc(task.title)}</p><h2>${esc(task.question)}</h2><p>${esc(task.summary)}</p>`;$('solutions').innerHTML=task.solutions.map(s=>`<article class="solution"><h3><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)} ↗</a></h3><p><b>可复用：</b>${esc(s.reuse)}</p><p><b>仍需研究与验证：</b>${esc(s.gap)}</p></article>`).join('');$('input-output').innerHTML=`<b>输入：</b>${esc(task.input)}<br><b>交付：</b>${esc(task.output)}`;$('requirements').textContent=task.requirements;$('demo-links').innerHTML=task.scenes.map(s=>`<a href="${root}/demonstration/${s}">${{urban:'建筑区补盲',corridor:'道路协同追踪',electromagnetic:'复杂电磁区'}[s]} →</a>`).join('');$('proposal-link').href=root+'/api/research-analysis/proposal#page='+task.pages[0];$('pdf-pages').textContent='任务依据 PDF 文件页 '+task.pages.join('、')+'；实验设计与示意交互为本平台的研究建议。';
 let draft=null;try{draft=JSON.parse(localStorage.getItem(draftKey)||'null');}catch{}if(draft?.task_id===taskId){for(const spec of ResearchLab.all.filter(x=>x.task===taskId)){const v=draft.lab_settings?.[spec.key];if(Number.isFinite(v)&&v>=spec.min&&v<=spec.max)labSettings[spec.key]=v;}$('hypothesis').value=String(draft.hypothesis||'').slice(0,2000);$('notes').value=String(draft.notes||'').slice(0,3000);const i=task.challenges.findIndex(c=>c.name===draft.challenge?.name);if(i>=0)challengeIndex=i;}
 chooseChallenge(challengeIndex);$('status').textContent='已完成';
}catch(e){$('status').className='error';$('status').textContent=e.message;}})();

$('export-experiments').onclick=()=>{const records=ResearchLab.all.map(spec=>ResearchLab.execute(spec.key,labSettings[spec.key]??spec.value));const blob=new Blob([JSON.stringify({schema_version:1,evidence_status:'synthetic_only',description:'15 个确定性示意实验；本页使用当前参数，其余页面使用默认参数；非科研验收结果',records},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='15个难点-合成实验结果.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};

// Show numerical evidence from the selected planning task beside the explanatory experiment.
(async function loadCalculationEvidence(){
 const host=$('calculation-result');
 if(!host)return;
 const params=new URLSearchParams(location.search);
 const planningTask=params.get('task')||localStorage.getItem('planning-task');
 const link=`${root}/acceptance${planningTask?'?task='+encodeURIComponent(planningTask):''}`;
 try{
  let identifier=params.get('assessment');
  if(!identifier&&planningTask){
   const response=await fetch(`${root}/api/workflow/tasks/${encodeURIComponent(planningTask)}/assessments`);
   if(!response.ok)throw Error('关联任务的计算记录读取失败');
   const records=await response.json();identifier=records.find(record=>record.status==='succeeded')?.id;
  }
  if(!identifier){host.innerHTML=`<p>选择规划任务并运行指标验证后，这里展示对应的后端计算结果。</p><a href="${link}">进入任务指标验证 →</a>`;return;}
  const response=await fetch(`${root}/api/assessments/${encodeURIComponent(identifier)}`);
  if(!response.ok)throw Error('计算证据读取失败');
  const job=await response.json();
  if(job.status!=='succeeded'){host.innerHTML=`<p>计算状态 ${esc(job.status)}</p><a href="${link}">查看计算进度 →</a>`;return;}
  if(planningTask&&job.snapshot.task.id!==planningTask)throw Error('计算记录与选定规划任务不一致');
  localStorage.setItem('planning-task',job.snapshot.task.id);
  const relevant=job.indicators.filter(row=>row.research===taskId);
  const report=job.task_validation?.find(item=>item.id===taskId);
  if(report){
   const list=values=>'<ul>'+values.map(value=>`<li>${esc(value)}</li>`).join('')+'</ul>';
   host.innerHTML=`<p>${esc(job.snapshot.task.name)} · ${job.stale?'历史版本，需重新验证':'当前模型已复核'} · 合成模型计算</p><h3>测试方法</h3><p>${esc(report.method)}</p><h3>本次结果</h3>${list(report.results)}<h3>下一步测试</h3>${list(report.next_tests)}${report.failed_requirements.length?'<h3>指标缺口</h3>'+list(report.failed_requirements):''}<h3>待完成证据</h3>${report.pending_requirements.length?list(report.pending_requirements):'<p>本任务所列指标暂无待补证据项。</p>'}<a href="${root}/acceptance?task=${encodeURIComponent(job.snapshot.task.id)}&assessment=${job.id}">查看完整结果与三维精度缺口 →</a>`;
   return;
  }
  host.innerHTML=`<p>${esc(job.snapshot.task.name)} · 合成模型实际计算 · ${job.result.evaluated_cells.toLocaleString()} 网格${job.stale?' · 历史输入已更新':''}</p>`+relevant.map(row=>`<p><strong>${esc(row.requirement)}</strong><br>${typeof row.value==='number'?(row.value*100).toFixed(2)+'%':esc(row.value)} · ${{simulation_pass:'本次仿真通过',simulation_fail:'本次仿真未通过',pending_evidence:'待补验收证据'}[row.status]}</p>`).join('')+`<a href="${root}/acceptance?task=${encodeURIComponent(job.snapshot.task.id)}&assessment=${job.id}">查看同任务三维场、规划对比与证据 →</a>`;
 }catch(error){host.textContent=error.message;}
})();
