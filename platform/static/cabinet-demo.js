'use strict';
const root=location.pathname.startsWith('/network')?'/network':'';
const el=id=>document.getElementById(id);
const roles={owner:'数据所有者',dataset:'模型数据使用者',stats:'统计使用者',org_admin:'组织管理员'};
let csrf='',current=null,busy=false,templateRows=[],mediaUrl=null,mediaTimer=null,uploadPaused=false;
const show=(id,value)=>{el(id).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);};
async function api(path,body){
 const response=await fetch(`${root}/api/cabinet/${path}`,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Cabinet-Request':'1',...(csrf?{'X-CSRF-Token':csrf}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});
 const data=await response.json();if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:'请求参数不正确');error.status=response.status;throw error;}return data;
}
let securityEpoch=0;
async function showConfiguredMode(unavailable=false){
 const epoch=++securityEpoch;
 show('protection','正在读取密钥模式…');
 try{
  const {mode}=await api('security-mode');
  if(epoch!==securityEpoch)return;
  show('protection',mode==='hardware'?(unavailable?'硬件模式 · 安全服务不可用，访问已停止。':'硬件模式 · 请登录核验远程取钥与检查点。'):'本地演示模式 · 使用软件密钥。');
 }catch(error){if(epoch===securityEpoch)show('protection','密钥模式不可用，请检查服务端配置。');}
}
function clear(unavailable=false){
 clearMedia();
 current=null;csrf='';el('login-panel').hidden=false;
 for(const id of ['account-panel','owner-panel','data-panel','admin-panel','audit-panel','templates-panel','media-panel'])el(id).hidden=true;
 templateRows=[];el('upload-id').value='';el('attachment-file').value='';uploadPaused=true;el('attachment-pause').disabled=true;el('template-definition').value='';el('registered-content').value='';el('template-id').value='';
 for(const id of ['assets','grants','members','result-view','audit','identity','template-list','templates-status','staged-attachments','attachment-list','media-status','upload-sessions','upload-progress'])show(id,'');
 for(const id of ['asset-select','grant-select','job-select','published-template'])el(id).replaceChildren();
 showConfiguredMode(unavailable);
}
function options(id,rows,label){
 const selected=el(id).value;el(id).replaceChildren();
 for(const row of rows){const option=document.createElement('option');option.value=row.id;option.textContent=label(row);el(id).append(option);}
 if(rows.some(r=>r.id===selected))el(id).value=selected;
}
function table(id,columns,rows,render){
 const t=document.createElement('table');const head=t.insertRow();for(const name of columns){const th=document.createElement('th');th.textContent=name;head.append(th);}
 for(const row of rows){const tr=t.insertRow();for(const value of render(row)){const td=tr.insertCell();if(value instanceof Node)td.append(value);else td.textContent=value;}}
 el(id).replaceChildren(t);
}
function button(text,action){const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=()=>perform(action);return b;}
function memberControls(member){
 const group=document.createElement('div');group.className='row';
 group.append(button(member.enabled?'禁用':'启用',()=>api(`members/${encodeURIComponent(member.id)}/enabled`,{enabled:!member.enabled})));
 const select=document.createElement('select');select.setAttribute('aria-label',`${member.username} 的角色`);
 for(const role of ['owner','dataset','stats']){const option=document.createElement('option');option.value=role;option.textContent=roles[role];select.append(option);}select.value=member.role;
 group.append(select,button('修改角色并撤销旧授权',()=>api(`members/${encodeURIComponent(member.id)}/role`,{role:select.value})));return group;
}
async function refresh(){
 const me=await api('me');if(current&&current.id!==me.user.id)clear();current=me.user;csrf=me.csrf;
 const data=await api('state');el('login-panel').hidden=true;el('account-panel').hidden=false;el('audit-panel').hidden=false;
 show('identity',`${current.username} · ${roles[current.role]} · 组织 ${current.org}`);
 ++securityEpoch;
 show('protection',me.key_mode==='hardware' && me.remote_keys_released?'硬件模式 · 本次远程取钥成功，独立检查点已验证。':me.key_mode==='demo'?'本地演示模式 · 使用软件密钥，独立检查点已验证。':'安全状态未知，请检查服务版本。');
 el('media-panel').hidden=!['owner','dataset'].includes(current.role);
 el('owner-panel').hidden=current.role!=='owner';el('data-panel').hidden=current.role==='org_admin';el('consumer-panel').hidden=!['dataset','stats'].includes(current.role);el('admin-panel').hidden=current.role!=='org_admin';
 await refreshTemplates();
 show('assets',data.assets);show('audit',data.audit);
 options('asset-select',data.assets,r=>`${r.name} (${r.id})`);
 options('grant-select',data.grants,r=>`${r.purpose} · ${r.revoked?'已撤销':r.id}`);syncGrantControl();
 options('job-select',data.jobs,r=>r.id);
 table('grants',['授权','用途','额度','状态','操作'],data.grants,g=>[g.id,g.purpose,`${g.used}/${g.quota}`,g.revoked?'已撤销':'按后端期限校验',current.role==='owner'&&!g.revoked?button('撤销',()=>api(`grants/${encodeURIComponent(g.id)}/revoke`,{})):'—']);
 if(current.role==='org_admin'){
  const members=await api('members');table('members',['用户名','角色','状态','操作'],members,m=>[m.username,roles[m.role],m.enabled?'启用':'禁用',m.role==='org_admin'?'离线管理':memberControls(m)]);
 }
}
async function perform(action,{endSession=false}={}){
 if(busy)return;clearMedia();busy=true;for(const b of document.querySelectorAll('button'))b.disabled=true;show('result-view','');
 try{const result=await action();if(endSession)clear();else{await refresh();if(result&&result.result)show('result-view',result.result);if(result&&(result.previewBlob||result.streamUrl))renderMedia(result);}show('status',endSession?'已退出，请重新登录。':'操作成功。');}
 catch(error){show('result-view','');try{await refresh();}catch(refreshError){clear([409,503].includes(refreshError.status));}show('status',error.message);}
 finally{busy=false;for(const b of document.querySelectorAll('button'))b.disabled=false;}
}
el('login-form').onsubmit=e=>{e.preventDefault();perform(async()=>{const password=el('password').value;el('password').value='';const r=await api('login',{username:el('username').value,password});csrf=r.csrf;return r;});};
el('logout').onclick=()=>perform(()=>api('logout',{}),{endSession:true});
el('refresh').onclick=()=>perform(async()=>{});
el('ingest').onclick=()=>perform(()=>api('ingest',{}));
el('grant-form').onsubmit=e=>{e.preventDefault();perform(()=>api('grants',{asset_id:el('asset-select').value,username:el('recipient').value,purpose:el('purpose').value}));};
el('run').onclick=()=>perform(()=>{if(!el('grant-select').value)throw Error('没有可执行授权');return api(`grants/${encodeURIComponent(el('grant-select').value)}/run`,{});});
el('result').onclick=()=>perform(()=>{if(!el('job-select').value)throw Error('尚无任务结果');return api(`results/${encodeURIComponent(el('job-select').value)}`);});
el('member-form').onsubmit=e=>{e.preventDefault();perform(()=>{const password=el('member-password').value;el('member-password').value='';return api('members',{username:el('member-name').value,password,role:el('member-role').value});});};
el('password-form').onsubmit=e=>{e.preventDefault();perform(()=>{const old=el('old-password').value,newPassword=el('new-password').value;el('old-password').value='';el('new-password').value='';return api('password',{old,new:newPassword});},{endSession:true});};
busy=true;for(const b of document.querySelectorAll('button'))b.disabled=true;
refresh().then(()=>show('status','已恢复当前账号登录。')).catch(error=>{clear([409,503].includes(error.status));show('status',error.message);}).finally(()=>{busy=false;for(const b of document.querySelectorAll('button'))b.disabled=false;});

el('template-example').onclick=()=>perform(async()=>{
 const data=await api('container-template');const url=URL.createObjectURL(new Blob([JSON.stringify(data.example,null,2)],{type:'application/json'}));
 const link=document.createElement('a');link.href=url;link.download='lowair-container-v1.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
el('container-import').onclick=()=>perform(async()=>{
 const file=el('container-file').files[0];if(!file)throw Error('请选择容器 JSON');if(file.size>1024*1024)throw Error('容器 JSON 不得超过 1 MiB');
 let body;try{body=JSON.parse(await file.text());}catch(_){throw Error('JSON 格式错误');}
 if(!body||typeof body!=='object'||Array.isArray(body)||!body.format)throw Error('文件不是容器数据包');
 const result=await api('ingest',body);el('container-file').value='';return result;
});

const templateStates={draft:'草稿',submitted:'待审核',approved:'已审核',published:'已发布',rejected:'已拒绝',retired:'已停用'};
async function refreshTemplates(){
 el('templates-panel').hidden=!['owner','org_admin'].includes(current.role);
 try{templateRows=await api('templates');show('templates-status','所有版本均经独立检查点校验。');}
 catch(error){templateRows=[];show('template-list','');show('templates-status',error.message);el('published-template').replaceChildren();if(error.message.includes('模板注册表迁移'))return;throw error;}
 const published=templateRows.filter(r=>r.status==='published').map(r=>({...r,id:`${r.template_id}/${r.version}`}));
 options('published-template',published,r=>`${r.definition.name} · v${r.version} · ${r.definition.purposes.join('/')}`);
 attachmentFields();
 if(current.role==='owner'){await refreshStaged();await refreshUploads();}
 table('template-list',['名称 / 版本','状态','摘要','操作'],templateRows,r=>{
  const controls=document.createElement('div');controls.className='row';
  const path=`templates/${encodeURIComponent(r.template_id)}/${r.version}/`;
  controls.append(button('查看 / 创建新版',async()=>{el('template-id').value=r.template_id;el('template-definition').value=JSON.stringify(r.definition,null,2);}));
  const acts=[];if(r.status==='draft'&&r.author===current.id)acts.push(['submit','提交审核']);
  if(current.role==='org_admin'&&r.author!==current.id){if(r.status==='submitted')acts.push(['approve','审核通过'],['reject','拒绝']);if(r.status==='approved')acts.push(['publish','发布']);if(r.status==='published')acts.push(['retire','停用']);}
  for(const [action,label] of acts)controls.append(button(label,()=>api(path+action,{})));
  return [`${r.definition.name} · v${r.version}`,templateStates[r.status],r.digest,controls];
 });
}
async function loadTemplateExample(index){const examples=await api('template-examples');el('template-id').value='';el('template-definition').value=JSON.stringify(examples[index].definition,null,2);el('registered-content').value=JSON.stringify(examples[index].content,null,2);}
el('numeric-example').onclick=()=>perform(()=>loadTemplateExample(0));
el('record-example').onclick=()=>perform(()=>loadTemplateExample(1));
el('template-form').onsubmit=e=>{e.preventDefault();perform(()=>{const definition=JSON.parse(el('template-definition').value);return api('templates',{definition,template_id:el('template-id').value.trim()||null});});};
el('registered-ingest-form').onsubmit=e=>{e.preventDefault();perform(()=>{const row=templateRows.find(r=>`${r.template_id}/${r.version}`===el('published-template').value);if(!row)throw Error('请选择已发布版本');return api('ingest',{format:'cabinet-registered-container-v1',template_id:row.template_id,template_version:row.version,template_sha256:row.digest,content:JSON.parse(el('registered-content').value)});});};

function clearMedia(){
 if(mediaTimer){clearInterval(mediaTimer);mediaTimer=null;}
 const video=el('media-preview').querySelector('video');if(video){video.pause();video.removeAttribute('src');video.load();}
 el('media-preview').replaceChildren();if(mediaUrl){URL.revokeObjectURL(mediaUrl);mediaUrl=null;}
 show('media-status','');
}
function attachmentFields(){
 const row=templateRows.find(r=>`${r.template_id}/${r.version}`===el('published-template').value);const fields=[];
 function walk(schema,path=[]){for(const name of schema._ord||schema._head||[]){const spec=schema[name],p=[...path,name];if([4,5].includes(spec.t))fields.push({id:p.join('.'),spec});else if([8,9].includes(spec.t))walk(spec.misc,p);}}
 if(row)walk(row.definition.schema);options('attachment-field',fields,f=>`${f.id} · ${f.spec.misc.mime.join(', ')}`);
}
async function refreshStaged(){
 let rows=[];try{rows=await api('attachments/staged');}catch(error){if(error.message.includes('迁移附件表')){show('staged-attachments',error.message);return;}throw error;}
 table('staged-attachments',['未绑定附件 ID','字段 / 类型','操作'],rows,r=>[r.id,`${r.field} · ${r.mime}`,button('删除暂存',()=>api(`attachments/${r.id}/discard`,{}))]);
}
function selectedMediaScope(){
 if(current.role==='owner'){const aid=el('asset-select').value;if(!aid)throw Error('请选择资产');return `assets/${encodeURIComponent(aid)}`;}
 const gid=el('grant-select').value;if(!gid)throw Error('请选择独立 preview 授权');return `grants/${encodeURIComponent(gid)}`;
}
async function previewMedia(scope,item){
 if(item.chunked){
  const lease=await api(`${scope}/attachments/${item.id}/stream`,{});
  const url=`${root}/api/cabinet/streams/${lease.stream_id}`;
  if(item.mime.startsWith('video/'))return {streamUrl:url,scope,mime:item.mime};
  const parts=[];let offset=0;
  while(offset<item.size){
   const response=await fetch(url,{credentials:'same-origin',cache:'no-store',headers:{Range:`bytes=${offset}-`}});
   if(!response.ok){const d=await response.json();throw Error(d.detail||'分块读取失败');}
   const part=await response.blob();if(!part.size)throw Error('空分块');parts.push(part);offset+=part.size;
  }
  return {previewBlob:new Blob(parts,{type:item.mime}),scope,mime:item.mime};
 }

 const response=await fetch(`${root}/api/cabinet/${scope}/attachments/${item.id}/preview`,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'X-Cabinet-Request':'1','X-CSRF-Token':csrf}});
 if(!response.ok){const d=await response.json();throw Error(d.detail||'附件预览被拒绝');}
 return {previewBlob:await response.blob(),scope,mime:item.mime};
}
function renderMedia(result){
 clearMedia();if(result.previewBlob)mediaUrl=URL.createObjectURL(result.previewBlob);
 const element=document.createElement(result.mime.startsWith('image/')?'img':'video');element.src=result.streamUrl||mediaUrl;
 if(element.tagName==='VIDEO'){element.controls=true;element.preload='metadata';element.onerror=()=>{clearMedia();show('media-status','流式预览已停止，请检查授权或重新开启预览会话。');};}else element.alt='已授权图片预览';
 el('media-preview').append(element);show('media-status','已通过当前账号与独立授权校验。');
 let polling=false;mediaTimer=setInterval(async()=>{if(busy||polling)return;polling=true;try{await api(`${result.scope}/attachments`);}catch(error){clearMedia();show('attachment-list','');show('media-status',`预览已清空：${error.message}`);}finally{polling=false;}},5000);
}
el('media-example').onclick=()=>perform(()=>loadTemplateExample(2));
el('published-template').onchange=()=>{clearMedia();attachmentFields();};
function syncGrantControl(){el('run').hidden=!!el('grant-select').selectedOptions[0]?.textContent.startsWith('preview');}
for(const id of ['asset-select','grant-select'])el(id).onchange=()=>{clearMedia();show('attachment-list','');syncGrantControl();};
async function refreshUploads(){
 try{
  const rows=await api('uploads');
  table('upload-sessions',['未完成上传','字段 / 大小','操作'],rows,r=>{
   const controls=document.createElement('div');controls.className='row';
   controls.append(button('选择续传',async()=>{el('upload-id').value=r.id;el('published-template').value=`${r.template_id}/${r.version}`;attachmentFields();el('attachment-field').value=r.field;show('upload-progress','重新选择原文件后点击上传／续传；服务器将核对已有分块摘要。');}),button('取消上传',()=>api(`uploads/${r.id}/cancel`,{})));
   return [r.id,`${r.field} · ${r.size} 字节`,controls];
  });
 }catch(error){show('upload-sessions',error.message);}
}
el('attachment-pause').onclick=()=>{uploadPaused=true;show('upload-progress','当前分块提交后暂停；稍后可重新选择原文件续传。');};
async function chunkDigest(blob){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer())),b=>b.toString(16).padStart(2,'0')).join('');}
el('attachment-upload').onclick=()=>perform(async()=>{
 const file=el('attachment-file').files[0],field=el('attachment-field').value;
 if(!file||!field)throw Error('请选择已发布模板的附件字段和文件');
 const row=templateRows.find(r=>`${r.template_id}/${r.version}`===el('published-template').value);if(!row)throw Error('请选择已发布版本');
 let id=el('upload-id').value.trim();
 if(!id){const started=await api(`templates/${row.template_id}/${row.version}/attachments/uploads`,{field,mime:file.type,size:file.size});id=started.upload_id;el('upload-id').value=id;}
 const state=await api(`uploads/${encodeURIComponent(id)}`);
 if(state.size!==file.size||state.mime!==file.type||state.template_id!==row.template_id||state.version!==row.version||state.field!==field)throw Error('续传文件或模板字段与会话不一致');
 uploadPaused=false;el('attachment-pause').disabled=false;
 try{
  // Check every acknowledged chunk before adding new data to an existing upload.
  for(const part of state.chunks){
   if(await chunkDigest(file.slice(part.idx*state.chunk_bytes,part.idx*state.chunk_bytes+part.size))!==part.sha256)throw Error('原文件已变化：已有分块摘要不一致，请取消旧会话并新建上传');
   if(uploadPaused)throw Error('上传已暂停，进度已保存在服务器');
  }
  const present=new Set(state.chunks.map(p=>p.idx));
  for(let i=0;i<Math.ceil(file.size/state.chunk_bytes);i++){
   if(uploadPaused)throw Error('上传已暂停，进度已保存在服务器');
   if(!present.has(i)){
    const response=await fetch(`${root}/api/cabinet/uploads/${encodeURIComponent(id)}/chunks/${i}`,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/octet-stream','X-Cabinet-Request':'1','X-CSRF-Token':csrf},body:file.slice(i*state.chunk_bytes,(i+1)*state.chunk_bytes)});
    if(!response.ok){const d=await response.json();throw Error(d.detail||'分块上传失败，可重试续传');}
   }
   show('upload-progress',`已确认 ${Math.min((i+1)*state.chunk_bytes,file.size)} / ${file.size} 字节`);
  }
  if(uploadPaused)throw Error('上传已暂停，可继续完成校验');
  show('upload-progress','正在校验所有分块、媒体元数据和检查点，请等待完成。');
 }finally{el('attachment-pause').disabled=true;}
 const data=await api(`uploads/${encodeURIComponent(id)}/complete`,{});
 el('upload-id').value='';show('upload-progress','分块校验完成，附件可绑定入柜。');
 try{const content=JSON.parse(el('registered-content').value||'{}');const parts=field.split('.');let target=content;
  for(const part of parts.slice(0,-1)){if(Array.isArray(target[part]))throw Error('请在表格行中手动填写附件 ID');if(!Object.prototype.hasOwnProperty.call(target,part))target[part]=Object.create(null);target=target[part];}
  Object.defineProperty(target,parts.at(-1),{value:{attachment_id:data.attachment_id},enumerable:true,writable:true,configurable:true});el('registered-content').value=JSON.stringify(content,null,2);
 }catch(_){/* Staged table retains the reference for explicit row-level entry. */}
 el('attachment-file').value='';return data;
});
el('attachments-load').onclick=()=>perform(async()=>{
 const scope=selectedMediaScope();const rows=await api(`${scope}/attachments`);
 table('attachment-list',['字段','类型','字节数','操作'],rows,r=>[r.field,r.mime,r.size,button('授权预览',()=>previewMedia(scope,r))]);
});
window.addEventListener('pagehide',clearMedia);
