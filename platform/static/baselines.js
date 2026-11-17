'use strict';
const $=id=>document.getElementById(id),root=location.pathname.startsWith('/network')?'/network':'';
let result=null,epoch=0;
const profiles={overview:{bounds:[0,2000,0,2000,0,300],cell:50},region:{bounds:[700,900,700,900,120,160],cell:10},fine:{bounds:[700,740,700,740,120,140],cell:2}};
async function api(path,options){const r=await fetch(root+path,options);const v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:JSON.stringify(v.detail));return v;}
function reset(){epoch++;result=null;$('results').hidden=true;$('export').disabled=true;$('status').textContent='输入已变更，请重新运行；历史记录可单独查看。';$('range').textContent='计算边界 [x0,x1,y0,y1,z0,z1] / m：'+profiles[$('profile').value].bounds.join(', ');}
async function loadHistory(){const id=$('scene').value;const rows=await api('/api/scenes/'+id+'/baselines');if(id!==$('scene').value)return;$('history').replaceChildren(new Option('选择已完成运行',''),...rows.map(r=>new Option(r.completed_at+' · '+r.id.slice(0,8),r.id)));}
function show(r){result=r;$('results').hidden=false;$('export').disabled=false;$('evidence').textContent=`${r.stale?'历史输入/代码已变化，结果已过期。':'保存的运行结果。'} 运行 ${r.id} · 模型 ${r.model_version} · 场景版本 ${r.scene_revision} · 完成 ${r.completed_at} · 耗时 ${r.seconds.toFixed(3)}s · 有效体素 ${r.valid_cells} · 网格 ${r.grid.cell}m · 输入摘要 ${r.input_digest}。参数：${JSON.stringify(r.snapshot.settings)}。${r.note}`;
const table=document.createElement('table');const header=table.insertRow();['基线','定义','空间覆盖率','重点区覆盖率','盲区体素'].forEach(v=>{const th=document.createElement('th');th.textContent=v;header.append(th);});const pct=v=>v==null?'无有效样本':(v*100).toFixed(2)+'%';for(const [key,m] of Object.entries(r.models)){const row=table.insertRow();[key,m.definition,pct(m.coverage),pct(m.focus_coverage),m.blind_cells].forEach(v=>row.insertCell().textContent=v);}$('table').replaceChildren(table);
$('altitude').replaceChildren(...r.grid.altitudes.map((z,i)=>new Option(z+' m',i)));$('altitude').value=String(Math.floor(r.grid.altitudes.length/2));draw();showImpact(r.impact);}
function draw(){const r=result,k=Number($('altitude').value);if(!r)return;$('maps').replaceChildren();for(const [name,data,delta] of [...Object.entries(r.models).map(([n,m])=>[n,m,false]),...Object.entries(r.changes).map(([n,m])=>[n.replace('_',' → '),m,true])]){const a=document.createElement('article'),h=document.createElement('h3'),canvas=document.createElement('canvas');h.textContent=name+(delta?`：减少 ${data.lost_cells} / 增加 ${data.gained_cells} 格（全区域）`:'');canvas.width=480;canvas.height=480;canvas.setAttribute('aria-label',name+' 高度切片');a.append(h,canvas);$('maps').append(a);const ctx=canvas.getContext('2d'),layer=data.layers[k],nx=layer.length,ny=layer[0].length;for(let x=0;x<nx;x++)for(let y=0;y<ny;y++){const v=layer[x][y];ctx.fillStyle=v<0?'#87929d':delta?(['#edf2f6','#e6a04d','#428dc4'][v]):v?'#53aa88':'#d87870';ctx.fillRect(x*480/nx,(ny-1-y)*480/ny,480/nx+.3,480/ny+.3);}}}
$('form').addEventListener('input',reset);$('scene').addEventListener('change',()=>loadHistory().catch(e=>$('status').textContent=e.message));$('altitude').onchange=draw;
$('form').onsubmit=async e=>{e.preventDefault();reset();const token=epoch;$('run').disabled=true;$('status').textContent='正在实际计算三组基线…';try{const data={...profiles[$('profile').value],pd_threshold:Number($('threshold').value),cochannel_db:Number($('cochannel').value),communication_db:Number($('communication').value),weather_db:Number($('weather').value),target_emitting:$('emitting').value==='yes',candidate_budget:Number($('budget').value)};const r=await api('/api/scenes/'+$('scene').value+'/baselines',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(token!==epoch)return;show(r);$('status').textContent='运行完成，三组结果已保存。预测差异不代表准确性提升。';await loadHistory();}catch(e){if(token===epoch)$('status').textContent='运行失败：'+e.message;}finally{$('run').disabled=false;}};
$('load').onclick=async()=>{if(!$('history').value)return;reset();const token=epoch;try{const r=await api('/api/baselines/'+$('history').value);if(token!==epoch)return;show(r);$('status').textContent='正在查看已保存结果，并非重新运行。';}catch(e){if(token===epoch)$('status').textContent=e.message;}};
$('export').onclick=()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='baseline-'+result.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
(async()=>{try{const d=await api('/api/scenes');$('scene').replaceChildren(...d.scenes.map(s=>new Option(s.name,s.id)));reset();$('status').textContent='尚未运行，不显示预设结果。';await loadHistory();}catch(e){$('status').textContent='加载失败：'+e.message;}})();

function showImpact(data){
 const host=$('impact');host.replaceChildren();
 const add=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;host.append(e);return e;};
 if(!data){add('p','该历史记录尚无分歧与决策分析，请重新运行。');return;}
 add('h2','分歧区域与补盲决策');
 add('p',`三模型存在分歧的体素：${data.disagreement_cells}，其中重点区：${data.focus_disagreement_cells}。${data.note}`);
 const table=(heads,rows)=>{const t=document.createElement('table');const h=t.insertRow();heads.forEach(x=>{const th=document.createElement('th');th.textContent=x;h.append(th);});rows.forEach(row=>{const tr=t.insertRow();row.forEach(v=>tr.insertCell().textContent=v);});const wrap=document.createElement('div');wrap.style.overflowX='auto';wrap.append(t);host.append(wrap);};
 add('h3','空间分区：按分歧体素数量排序');
 table(['分区','XYZ边界 / m','有效体素','分歧体素','分歧占比','重点区分歧'],data.regions.map(r=>[r.name,r.bounds.join(', '),r.valid_cells,r.disagreement_cells,r.disagreement_rate==null?'无有效体素':(100*r.disagreement_rate).toFixed(2)+'%',r.focus_disagreement_cells]));
 add('h3','相同预算下的单设备补盲排序');
 add('p',`预算 ${data.candidate_budget} 万元。${data.top_sets_differ?'模型之间的最优候选集合存在差异。':'模型之间的最优候选集合一致；本次未发现最优候选集合变化。'}并列集合不同不等于建议完全冲突，需查看共同候选。`);
 table(['评价模型','最优候选（含并列）','全部正增益候选排序'],Object.keys(data.rankings).map(k=>[k,data.top_sets[k].join('、')||'无正增益候选',data.rankings[k].join(' → ')||'无']));
 add('h3','候选增益交叉评价');
 table(['候选 / 类型','成本 / 万元','B0新增体素','B1新增体素','B2新增体素','B2重点区新增'],data.candidates.map(r=>[r.id+' / '+r.type,r.cost,r.gains.B0.new_cells,r.gains.B1.new_cells,r.gains.B2.new_cells,r.gains.B2.focus_new_cells]));
 if(data.excluded.length)add('p','未参评：'+data.excluded.map(r=>r.id+'（'+r.reason+'）').join('；'));
 add('p','下一步取证：优先检查分歧区域与候选排序变化所涉及的位置，并保留一致区域作为对照。这里没有实测真值，不能把后一模型当作正确答案。');
}
