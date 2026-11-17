// Shared spatial view for the existing diagnosis/planning flow; no independent scene state or calculation.
import * as THREE from 'three';
import {realBuildings} from './real-buildings.js';
import {OrbitControls} from './vendor/three/OrbitControls.js';
import {localFrame,imageryGround,groundBase} from './imagery-ground.js';
const $=id=>document.getElementById(id),host=$('network3d-viewport');
try {
const scene=new THREE.Scene();scene.background=new THREE.Color('#c5d0c4');
const camera=new THREE.PerspectiveCamera(45,1,2,40000),renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));host.appendChild(renderer.domElement);renderer.outputColorSpace=THREE.SRGBColorSpace;
const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.minPolarAngle=.12;controls.maxPolarAngle=Math.PI*.36;controls.minDistance=350;controls.maxDistance=4500;controls.enablePan=true;
scene.add(new THREE.HemisphereLight(0xd4eeff,0x364b60,.7));const light=new THREE.DirectionalLight(0xffffff,1.25);light.position.set(-900,420,280);scene.add(light);
const flatGround=groundBase(60000,0xc5d0c4);scene.add(flatGround);
const buildings=new THREE.Group(),devices=new THREE.Group(),cells=new THREE.Group(),proposal=new THREE.Group(),routeGroup=new THREE.Group();scene.add(buildings,devices,cells,proposal,routeGroup);
let shared=null,sceneKey=null,coverageKey=null,planKey=null,routeKey=null,picks=[],isVisible=false;
const LON0=115.95,LAT0=39,EARTH=6378137;


// Backend XYZ is a single local reference plane, not per-point AGL.
// Keep buildings, sensors, routes and coverage in exactly that same frame.
const world=(x,y,z)=>new THREE.Vector3(x-1000,z,1000-y);
function clear(group){for(const o of [...group.children]){group.remove(o);o.traverse(x=>{x.geometry?.dispose();for(const m of [].concat(x.material||[])){m.map?.dispose();m.dispose();}});}}
function home(){controls.target.set(0,50,0);camera.position.set(220,2300,1450);controls.update();}
const frame=localFrame(LAT0,LON0);
const geographicBuildings=realBuildings({host,scene,frame,place:world,initialVisible:false,onView:kind=>{if(kind==='top'){controls.target.set(0,0,0);camera.position.set(0,2300,8);controls.update();}else home();}});
renderer.domElement.addEventListener('pointerup',e=>geographicBuildings.inspect(e,camera,renderer.domElement));
function tileUrl(kind,z,x,y){return new URL(`tiles/${kind}/${z}/${x}/${y}`,new URL('..',import.meta.url)).href;}
const tileStatus=document.createElement('div');tileStatus.className='map-status';tileStatus.setAttribute('role','status');host.appendChild(tileStatus);
const attribution=document.createElement('div');attribution.className='map-attribution';attribution.textContent='影像 © Esri / World Imagery · WGS84 · 模拟站址';host.appendChild(attribution);
const compass=document.createElement('div');compass.className='map-compass';compass.innerHTML='<i>↑</i><b>N</b>';host.appendChild(compass);
function groundHeight(){return 0;}
const imagery=imageryGround({renderer,frame,centre:[1000,1000],url:(z,x,y)=>tileUrl('imagery',z,x,y),place:(e,n)=>world(e,n,0),
  levels:[{z:16,from:[-1000,-1000],to:[3000,3000]},{z:15,from:[-4000,-4000],to:[6000,6000]},{z:12,from:[-16000,-16000],to:[18000,18000]}],
  onStatus:({loaded,total,done,failed})=>{tileStatus.textContent=!done?`卫星影像 ${loaded}/${total}`:failed?`${failed} 张影像未载入 `:'卫星影像已就绪';if(done&&failed){const retry=document.createElement('button');retry.textContent='重试';retry.onclick=loadTerrain;tileStatus.appendChild(retry);}}});
const mapTiles=imagery.group;scene.add(mapTiles);
function loadTerrain(){return imagery.load();}
function ignoreRaycast(){}
function placeLabel(text,position){const canvas=document.createElement('canvas');canvas.width=320;canvas.height=72;const pen=canvas.getContext('2d');pen.fillStyle='rgba(12,28,42,.88)';pen.fillRect(0,0,320,72);pen.fillStyle='#e7f5ff';pen.font='28px sans-serif';pen.fillText(text,14,46);const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(canvas),transparent:true,depthTest:false,sizeAttenuation:false}));sprite.position.copy(position);sprite.scale.set(.17,.038,1);sprite.center.set(.5,-.2);sprite.raycast=ignoreRaycast;return sprite;}
const TYPE_COLORS={RADAR:0x40c9ff,RF:0xe8b85d,EO:0x66dec1,IR:0xfb8b91,ADSB:0xc0a3ff};
function markers(items,group,color,proposed=false){
 clear(group);
 for(const d of items){
  const tint=d.status==='offline'?0x87929d:proposed?color:(TYPE_COLORS[d.type]||color);
  const gnd=groundHeight(d.x,d.y),alt=d.z,picked={device:d,proposed};
  const root=new THREE.Group();root.position.copy(world(d.x,d.y,gnd));group.add(root);
  const metal=new THREE.MeshStandardMaterial({color:0x2a3138,roughness:.42,metalness:.62});
  const paint=new THREE.MeshStandardMaterial({color:tint,roughness:.38,metalness:.22});
  const shell=new THREE.MeshStandardMaterial({color:0xe8eef3,roughness:.5,metalness:.06});
  const add=(mesh,y)=>{mesh.position.y=y;mesh.userData=picked;root.add(mesh);return mesh;};
  const mastPaint=new THREE.MeshStandardMaterial({color:tint,roughness:.55,metalness:.15});
  add(new THREE.Mesh(new THREE.CylinderGeometry(1.2,1.5,.4,16),metal),.2);
  add(new THREE.Mesh(new THREE.CylinderGeometry(.25,.45,alt,10),mastPaint),alt/2);const guide=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,1,0),new THREE.Vector3(0,alt,0)]),new THREE.LineBasicMaterial({color:tint}));guide.raycast=ignoreRaycast;root.add(guide);
  const shadow=new THREE.Mesh(new THREE.RingGeometry(1.5,2,32),new THREE.MeshBasicMaterial({color:tint,transparent:true,opacity:.55,depthWrite:false,side:THREE.DoubleSide}));shadow.rotation.x=-Math.PI/2;shadow.position.y=1.2;shadow.raycast=ignoreRaycast;root.add(shadow);
  const head=new THREE.Group();head.position.y=alt;head.rotation.y=-(d.azimuth||0)*Math.PI/180;head.scale.setScalar(.3);root.add(head);
  const put0=mesh=>{mesh.userData=picked;head.add(mesh);return mesh;};put0(new THREE.Mesh(new THREE.CylinderGeometry(2.2,2.2,.4,12),metal));
  const put=mesh=>{mesh.userData=picked;head.add(mesh);return mesh;};
  if(d.type==='RADAR'){const dish=put(new THREE.Mesh(new THREE.SphereGeometry(4.6,18,12,0,Math.PI*2,0,Math.PI/2.2),paint));dish.rotation.x=Math.PI/2.4;dish.position.set(0,3.6,-.3);put(new THREE.Mesh(new THREE.CylinderGeometry(.4,.4,3.2,8),metal)).position.y=1.6;}
  else if(d.type==='EO'||d.type==='IR'){put(new THREE.Mesh(new THREE.BoxGeometry(3.4,2,2.2),shell)).position.set(0,2.2,0);const lens=put(new THREE.Mesh(new THREE.CylinderGeometry(.55,.75,2.2,12),paint));lens.rotation.x=Math.PI/2;lens.position.set(0,2.3,-2);}
  else if(d.type==='ADSB'){put(new THREE.Mesh(new THREE.CylinderGeometry(.18,.18,9,6),paint)).position.y=4.6;put(new THREE.Mesh(new THREE.SphereGeometry(.7,10,8),paint)).position.y=9.2;}
  else{put(new THREE.Mesh(new THREE.BoxGeometry(2.6,1.5,1.7),shell)).position.y=1.8;for(const side of [-1.2,1.2]){const ant=put(new THREE.Mesh(new THREE.CylinderGeometry(.12,.12,6,6),paint));ant.position.set(side,4.2,0);}}
  if($('network3d-ranges').checked&&d.status!=='offline'){
   const angle=Math.min(d.fov||360,360),radius=d.range||0,pts=[];
   if(angle<360)pts.push(world(d.x,d.y,gnd+4));
   for(let i=0;i<=96;i++){const a=((d.azimuth||0)-angle/2+angle*i/96)*Math.PI/180;pts.push(world(d.x+radius*Math.sin(a),d.y+radius*Math.cos(a),groundHeight(d.x+radius*Math.sin(a),d.y+radius*Math.cos(a))+4));}
   if(angle<360)pts.push(world(d.x,d.y,gnd+4));
   const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),new THREE.LineBasicMaterial({color:tint,transparent:true,opacity:.7}));line.raycast=ignoreRaycast;group.add(line);
  }
  const pinCanvas=document.createElement('canvas');pinCanvas.width=64;pinCanvas.height=64;const ctx=pinCanvas.getContext('2d');ctx.beginPath();ctx.arc(32,32,21,0,Math.PI*2);ctx.fillStyle='#'+new THREE.Color(tint).getHexString();ctx.fill();ctx.lineWidth=5;ctx.strokeStyle='#fff';ctx.stroke();const pin=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(pinCanvas),sizeAttenuation:false,depthTest:false}));pin.position.set(0,alt,0);pin.scale.set(.025,.025,1);pin.userData=picked;root.add(pin);
  if($('network3d-labels').checked){const label=placeLabel(d.name||d.id,new THREE.Vector3());label.position.set(0,alt+14,0);root.add(label);head.userData.label=label;head.userData.alt=alt;}
 }
}
function zoneVolume(){if(!$('network3d-buildings').checked&&$('network3d-layer').value==='none')return;const z=shared.scene.zone,z0=z.z0??0,z1=z.z1??300,corners=[[z.x0,z.y0],[z.x1,z.y0],[z.x1,z.y1],[z.x0,z.y1]];const ring=height=>new THREE.Line(new THREE.BufferGeometry().setFromPoints([...corners,corners[0]].map(p=>world(p[0],p[1],height))),new THREE.LineBasicMaterial({color:0xe07a3a}));buildings.add(ring(z0+2),ring(z1));for(const [x,y] of corners)buildings.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([world(x,y,z0),world(x,y,z1)]),new THREE.LineBasicMaterial({color:0xe07a3a,transparent:true,opacity:.75})));}
function model(){clear(buildings);if($('network3d-buildings').checked)shared.scene.buildings.forEach((b,i)=>{const h=Math.max(b.h,4),t=Math.min(1,(h-25)/80),g=new THREE.BoxGeometry(b.w,h,b.d),mesh=new THREE.Mesh(g,new THREE.MeshStandardMaterial({color:new THREE.Color().setHSL(.58-t*.05,.1+t*.16,.9-t*.38),roughness:.62,metalness:.04}));mesh.position.copy(world(b.x+b.w/2,b.y+b.d/2,groundHeight(b.x+b.w/2,b.y+b.d/2)+h/2));mesh.userData={building:i};buildings.add(mesh);const edges=new THREE.LineSegments(new THREE.EdgesGeometry(g),new THREE.LineBasicMaterial({color:0x1e3344,transparent:true,opacity:.55}));edges.position.copy(mesh.position);edges.raycast=ignoreRaycast;buildings.add(edges);});markers(shared.scene.devices,devices,0x45d4ed);zoneVolume();}
function coverage(){clear(cells);picks=[];const c=shared.coverage;if(!c)return;const mode=$('network3d-layer').value,delta=shared.coverageMode==='delta';if(mode==='none')return;const points=[];c.layers.forEach((layer,k)=>{const z=k*50+25;if(mode==='slice'&&Math.floor(shared.altitude/50)!==k)return;layer.forEach((row,i)=>row.forEach((n,j)=>{if(n<0||delta&&n===0||!delta&&mode==='blind'&&n!==0||!delta&&mode==='overlap'&&n<2)return;points.push({x:i*50+25,y:j*50+25,z,n});}));});
const mesh=new THREE.InstancedMesh(new THREE.BoxGeometry(mode==='slice'?46:16,mode==='slice'?1:10,mode==='slice'?46:16),new THREE.MeshBasicMaterial({transparent:true,opacity:Number($('network3d-opacity').value)/100,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2}),points.length);const matrix=new THREE.Matrix4();points.forEach((p,i)=>{matrix.setPosition(world(p.x,p.y,p.z));mesh.setMatrixAt(i,matrix);mesh.setColorAt(i,new THREE.Color(delta?(p.n===1?0x67bc94:p.n===2?0x368fd1:0xe66a5b):(p.n===0?0xe66a5b:p.n===1?0x67bc94:0x368fd1)));});mesh.userData={coverage:true};cells.add(mesh);picks=points;}
function routePreview(){clear(routeGroup);const r=shared.route;if(!r)return;markers(r.selected,routeGroup,0xffae51,true);const points=r.samples.map(p=>world(p.x,p.y,p.z));const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:0xffcc71}));routeGroup.add(line);const p=r.samples[shared.routeStep||0];const target=new THREE.Mesh(new THREE.SphereGeometry(20,16,8),new THREE.MeshBasicMaterial({color:p.in_air?0xffffff:0xff6655}));target.position.copy(world(p.x,p.y,p.z));routeGroup.add(target);for(const id of p.sensor_ids||[]){const d=r.sites.find(s=>s.id===id);if(!d)continue;routeGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([world(d.x,d.y,d.z),world(p.x,p.y,p.z)]),new THREE.LineBasicMaterial({color:0x70e0ca,transparent:true,opacity:.7})));}}
function update(s){shared=s;isVisible=s.visible;const sk=s.scene.id+':'+s.scene.revision;if(sk!==sceneKey){sceneKey=sk;model();home();$('network3d-detail').textContent=`${s.scene.name} · 场景版本 ${s.scene.revision} · 与右侧设备管理同步`;}const ck=sk+':'+s.coverage?.computed_at+':'+s.altitude+':'+s.coverageMode+':'+s.plan?.id+':'+$('network3d-layer').value;if(ck!==coverageKey){coverageKey=ck;coverage();}const pk=sk+':'+(s.plan?.id||'');if(pk!==planKey){planKey=pk;markers(s.plan?.selected||[],proposal,0xffae51,true);}proposal.visible=$('network3d-plan').checked;const rk=sk+':'+s.route?.id+':'+s.routeStep;if(rk!==routeKey){routeKey=rk;routePreview();}}
window.network3D={update};window.dispatchEvent(new Event('network3d-ready'));
$('network3d-layer').onchange=()=>{if(shared)model();coverageKey=null;if(shared)update(shared);};$('network3d-plan').onchange=()=>proposal.visible=$('network3d-plan').checked;$('network3d-home').onclick=home;$('network3d-side').onclick=()=>{controls.target.set(0,70,0);camera.position.set(-1500,1400,700);controls.update();};
new ResizeObserver(()=>{if(!host.clientWidth||!host.clientHeight)return;camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();renderer.setSize(host.clientWidth,host.clientHeight);}).observe(host);
const ray=new THREE.Raycaster();const heightTip=document.createElement('div');heightTip.className='map-status';heightTip.hidden=true;host.appendChild(heightTip);
function showHeight(e){const rect=renderer.domElement.getBoundingClientRect();ray.setFromCamera(new THREE.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),camera);const hit=ray.intersectObjects([mapTiles,flatGround,...devices.children,...(proposal.visible?proposal.children:[]),...buildings.children],true).find(h=>!(h.object instanceof THREE.Line)&&!(h.object instanceof THREE.Sprite));if(!hit){heightTip.hidden=true;return;}let obj=hit.object;while(obj&&!obj.userData.device&&obj.userData.building===undefined)obj=obj.parent;const p=hit.point,east=Math.round(p.x+1000),north=Math.round(1000-p.z),cursorH=Math.max(0,Math.round(p.y));let text=`东 ${east} m · 北 ${north} m · 基准高度 ${cursorH} m`;if(obj?.userData.device){const d=obj.userData.device;text=`${d.name} · 部署高度 ${Math.round(d.z)} m · 光标 ${cursorH} m`;}else if(obj?.userData.building!==undefined&&shared){const b=shared.scene.buildings[obj.userData.building];text=`建筑高 ${b.h} m · 东 ${east} m · 北 ${north} m`;}heightTip.hidden=false;heightTip.textContent=text;heightTip.style.left=(e.clientX-rect.left+16)+'px';heightTip.style.top=(e.clientY-rect.top+16)+'px';}
renderer.domElement.addEventListener('pointerdown',e=>{if(e.ctrlKey)controls.enabled=false;},{capture:true});
renderer.domElement.addEventListener('pointermove',e=>{if(!e.ctrlKey){heightTip.hidden=true;controls.enabled=true;return;}controls.enabled=false;showHeight(e);});
renderer.domElement.addEventListener('pointerleave',()=>{heightTip.hidden=true;controls.enabled=true;});
window.addEventListener('keyup',e=>{if(e.key==='Control'){heightTip.hidden=true;controls.enabled=true;}});
let down;renderer.domElement.addEventListener('pointerdown',e=>down=[e.clientX,e.clientY]);renderer.domElement.addEventListener('pointerup',e=>{if(e.ctrlKey||!shared||!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>5)return;const r=renderer.domElement.getBoundingClientRect();ray.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1),camera);const hit=ray.intersectObjects([...devices.children,...(proposal.visible?proposal.children:[]),...buildings.children,...cells.children]).find(h=>!(h.object instanceof THREE.Line));if(!hit)return;const u=hit.object.userData;let text;if(u.device){const d=u.device;text=`${u.proposed?'方案待新增':'已有设备'} ${d.name} · ${d.type} · (${d.x}, ${d.y}, ${d.z}) m`;if(!u.proposed)window.dispatchEvent(new CustomEvent('network3d-edit',{detail:d.id}));}else if(u.building!==undefined){const b=shared.scene.buildings[u.building];text=`建筑 ${u.building+1} · 平面 ${b.w} × ${b.d} m · 高 ${b.h} m`;}else if(u.coverage){const p=picks[hit.instanceId];text=`网格中心 (${p.x}, ${p.y}, ${p.z}) m · ${shared.coverageMode==='delta'?({1:'新增覆盖',2:'多站覆盖增强',3:'失去覆盖'}[p.n]||'无变化'):(p.n===0?'盲区':(p.n===1?'融合覆盖达标':p.n+'设备分别达标'))} · ${shared.coverageMode==='current'?'当前配置':'候选方案'}，50 m网格`;}if(text)$('network3d-detail').textContent=text;});
$('network3d-top').onclick=()=>{camera.position.set(0,1700,8);controls.target.set(0,0,0);controls.update();};
const altitudeLabel=$('altitude').parentElement,altitudeHome=altitudeLabel.parentElement;
$('network3d-expand').onclick=()=>{const panel=$('network3d-panel');panel.classList.toggle('map-expanded');(panel.classList.contains('map-expanded')?panel.querySelector('.map-toolbar'):altitudeHome).appendChild(altitudeLabel);$('network3d-expand').textContent=panel.classList.contains('map-expanded')?'退出大图':'展开地图';};
document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('network3d-panel').classList.remove('map-expanded');altitudeHome.appendChild(altitudeLabel);$('network3d-expand').textContent='展开地图';}});
for(const id of ['network3d-buildings','network3d-labels','network3d-ranges'])$(id).onchange=()=>{if(shared){model();markers(shared.plan?.selected||[],proposal,0xffae51,true);routePreview();}};
$('network3d-opacity').oninput=()=>{if(shared)coverage();};
const probe=new THREE.Vector3();renderer.setAnimationLoop(()=>{if(!isVisible||!host.clientWidth)return;controls.update();controls.target.x=Math.max(-1400,Math.min(1400,controls.target.x));controls.target.z=Math.max(-1400,Math.min(1400,controls.target.z));controls.target.y=Math.max(0,Math.min(220,controls.target.y));const n=new THREE.Vector3(0,0,-1).applyQuaternion(camera.quaternion.clone().invert());compass.querySelector('i').style.transform=`rotate(${Math.atan2(n.x,n.y)*180/Math.PI}deg)`;renderer.render(scene,camera);});home();
loadTerrain().catch(error=>{if($('network3d-detail'))$('network3d-detail').textContent='地图影像暂未载入，仍显示局部坐标中的设备高度。'+error.message;});
} catch(e){$('network3d-detail').textContent='三维渲染不可用：'+e.message+'；可继续使用二维切片。';}
