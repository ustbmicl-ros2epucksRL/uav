'use strict';
// Extend the original fleet/map/inspector page; do not create another control state.
const managedLayer=L.layerGroup().addTo(map);
const managedAlerts=L.layerGroup().addTo(map);
let managedCatalog=null,managedArea='',managedSignature='',managedLoading=false;
window.managedAreaMatches=item=>!managedArea||item.area?.id===managedArea;
window.managedOverview=()=>{if(!managedCatalog)return;if(state.scene3d)$('scene-flat').click();map.fitBounds(managedCatalog.areas.flatMap(a=>a.polygon),{padding:[70,70],maxZoom:14});};
function managedRecord(){return managedCatalog?.areas.find(a=>a.id===managedArea);}
function managedFit(){const area=managedRecord();if(!area)return;if(state.scene3d)$('scene-flat').click();map.fitBounds(area.polygon,{padding:[40,40],maxZoom:17});}
function managedInfo(title,rows){
 const box=document.createElement('div');box.className='map-feature-info';
 const heading=document.createElement('strong');heading.textContent=title;box.append(heading);
 for(const [label,value] of rows){const row=document.createElement('p');row.textContent=label+'：'+value;box.append(row);}return box;
}
function renderManaged(){
 const area=managedRecord(),v=vehicle();
 $('managed-target').textContent=area&&v?area.name+' / '+v.name+'（'+v.id+' · 本地仿真）':'先选择区域与飞机';
 $('managed-create').disabled=window.externalMode||managedLoading||state.busy||state.replay||!area||!v||v.armed||v.payload!=='光电吊舱';
 if(!managedCatalog)return;
 $('managed-area-note').textContent='仿真管理范围 · '+managedCatalog.zones.length+' 个禁飞区 · '+(v?.payload==='光电吊舱'?'当前飞机支持巡检':'请选择配置光电吊舱的飞机');
 const signature=JSON.stringify([managedArea,managedCatalog.anchor,managedCatalog.zones]);
 if(signature!==managedSignature){
  managedSignature=signature;managedLayer.clearLayers();
  for(const region of managedCatalog.areas){const title=document.createElement('span');title.textContent=region.name+(region.id===managedArea?' · 当前监管区域':' · 监管区域');L.polygon(region.polygon,{color:region.id===managedArea?'#68bfff':'#8ca9bd',weight:region.id===managedArea?3:1,dashArray:'6 5',fillOpacity:.04}).bindTooltip(title,{permanent:true,direction:'center',className:'region-label'}).bindPopup(managedInfo('监管区域',[['名称',region.name],['用途','区域飞行管理与巡检任务范围'],['数据','仿真示范范围，非真实空域许可']]),{maxWidth:260}).addTo(managedLayer);}
  for(const z of managedCatalog.zones){
   const title=document.createElement('span');title.textContent='仿真禁飞区';
   L.polygon(z.polygon,{color:'#ff6379',weight:2,fillOpacity:.12})
    .bindTooltip(title,{permanent:true,direction:'center',className:'no-fly-label'})
    .bindPopup(managedInfo('仿真禁飞区',[['名称',z.name||z.id],['编号',z.id],['高度范围',z.z_min+'–'+z.z_max+' 米（局部 ENU 高度）'],['用途',z.purpose||'用于仿真航线安全检查和绕障验证'],['说明','平台配置的测试区域，不是真实禁飞公告']]),{maxWidth:270}).addTo(managedLayer);
  }
  window.managedZonesRendered=true;
 }
 managedAlerts.clearLayers();
 if(window.externalMode)return;
 for(const task of window.currentPatrol?.assignments||[]){
  if(!window.managedAreaMatches(task))continue;
  for(const alert of task.alerts||[]){
   const label=document.createElement('span');label.textContent=task.vehicle_id+' · '+(alert.status==='resolved'?'已处置':'待核验');
   L.circleMarker(Core.geo(alert.position,task.anchor).slice(0,2),{radius:8,color:alert.status==='resolved'?'#68cdb3':'#ff937a'}).bindTooltip(label).on('click',()=>openPatrolAlert(task.vehicle_id,alert.id)).addTo(managedAlerts);
  }
 }
}
window.managedInitialFocus=managedFit;
$('managed-fit').onclick=managedFit;
$('managed-area').onchange=()=>{
 const area=managedCatalog?.areas.find(a=>a.id===$('managed-area').value);if(!area)return;
 managedArea=area.id;$('patrol-area').value=area.id;
 // Switching the viewed area never selects a new aircraft or sends a command.
 renderManaged();window.renderPatrol?.();renderPatrolMonitor();managedFit();
 $('managed-result').textContent='已切换查看范围，当前飞机与飞行任务不变。';
};
$('managed-create').onclick=()=>operation(async()=>{
 const area=managedRecord(),id=state.selected;
 if(!area||!id)throw Error('请先选择区域与飞机');
 if(state.replay)throw Error('请先退出回放');
 const task=await api('/api/park-patrol/'+encodeURIComponent(id)+'/assign',{area_id:area.id,altitude:180});
 $('patrol-area').value=area.id;$('patrol-altitude').value=180;
 await refreshPatrol();
 $('managed-result').textContent='已创建 '+task.id+' · '+area.name+' / '+id+'；航线已检查，尚未起飞。';
 switchView('patrol');
},'区域巡检任务已创建，点击“开始巡检”后才会起飞。');
async function refreshManaged(){
 if(managedLoading)return;managedLoading=true;
 try{
  const first=!managedCatalog;managedCatalog=await api('/api/sitl-demo/catalog');
  if(!managedRecord())managedArea=managedCatalog.areas[0]?.id||'';
  $('managed-area').replaceChildren();for(const a of managedCatalog.areas){const option=document.createElement('option');option.value=a.id;option.textContent=a.name;$('managed-area').append(option);}$('managed-area').value=managedArea;
  $('patrol-area').value=managedArea;
  renderManaged();if(first)managedFit();
 }catch(e){managedCatalog=null;window.managedZonesRendered=false;managedLayer.clearLayers();managedAlerts.clearLayers();$('managed-result').textContent='区域数据不可用：'+e.message;}
 finally{managedLoading=false;renderManaged();}
}
refreshManaged();setInterval(refreshManaged,5000);setInterval(renderManaged,1000);
