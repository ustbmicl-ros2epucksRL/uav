import * as THREE from 'three';
import {realBuildings} from '/network/static/real-buildings.js';
import {OrbitControls} from '/network/static/vendor/three/OrbitControls.js';
import {localFrame,imageryGround,groundBase} from '/network/static/imagery-ground.js';

const canvas=document.getElementById('scene3d'),stage=canvas.parentElement,Core=window.GCSCore;
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
renderer.setPixelRatio(Math.min(2,window.devicePixelRatio||1));renderer.outputColorSpace=THREE.SRGBColorSpace;
const scene=new THREE.Scene();scene.background=new THREE.Color('#33433a');
const camera=new THREE.PerspectiveCamera(45,1,2,40000);
const controls=new OrbitControls(camera,canvas);
controls.enableDamping=true;controls.minPolarAngle=.1;controls.maxPolarAngle=Math.PI*.36;controls.minDistance=60;controls.maxDistance=3000;controls.screenSpacePanning=false;
scene.add(new THREE.HemisphereLight(0xffffff,0x40504a,.85));
const sun=new THREE.DirectionalLight(0xffffff,1.3);sun.position.set(-900,1200,600);scene.add(sun);
const base=groundBase(60000,'#33433a');scene.add(base);
const dynamic=new THREE.Group();scene.add(dynamic);
const W=(x,y,z=0)=>new THREE.Vector3(x-1000,z,1000-y);
const ignoreRaycast=()=>{};

const status=document.createElement('div');status.className='scene3d-status';stage.appendChild(status);
const tip=document.createElement('div');tip.className='scene3d-tip';tip.hidden=true;stage.appendChild(tip);

let anchorKey='',imagery=null;
const geographicBuildings=realBuildings({host:stage,compact:true,initialVisible:false,panelZ:530,panelBottom:120,scene,frame:localFrame(39.05,115.98),place:W,onView:kind=>{if(kind==='algorithm'&&d.algorithm){const b=d.algorithm.bounds,lo=b.xyz_min,hi=b.xyz_max,c=W((lo[0]+hi[0])/2,(lo[1]+hi[1])/2,(lo[2]+hi[2])/2),span=Math.max(hi[0]-lo[0],hi[1]-lo[1]);controls.target.copy(c);camera.position.copy(c).add(new THREE.Vector3(-span*.3,span*1.8,span*1.3));controls.update();return;}if(kind==='top'){controls.target.set(0,0,0);camera.position.set(0,2300,8);controls.update();}else frame('fence');}});
canvas.addEventListener('pointerup',e=>geographicBuildings.inspect(e,camera,canvas));
function loadGround(anchor){
  const key=`${anchor.latitude},${anchor.longitude}`;if(key===anchorKey)return;anchorKey=key;imagery?.dispose();geographicBuildings.setFrame(localFrame(anchor.latitude,anchor.longitude));
  imagery=imageryGround({renderer,frame:localFrame(anchor.latitude,anchor.longitude),centre:[1000,1000],url:(z,x,y)=>`/network/tiles/imagery/${z}/${x}/${y}`,place:(e,n)=>W(e,n,0),
    levels:[{z:16,from:[-400,-400],to:[2400,2400]},{z:14,from:[-6000,-6000],to:[8000,8000]},{z:12,from:[-16000,-16000],to:[18000,18000]}],
    onStatus:({loaded,total,done,failed})=>{status.textContent=!done?`卫星影像 ${loaded}/${total}`:failed?`${failed} 张影像未载入，切回二维再进入可重试`:'卫星影像已就绪';if(done&&failed)anchorKey='';}});
  scene.add(imagery.group);imagery.load();
}

function clearDynamic(){for(const o of [...dynamic.children]){dynamic.remove(o);o.traverse(m=>{m.geometry?.dispose();const mats=Array.isArray(m.material)?m.material:[m.material];mats.forEach(x=>{x?.map?.dispose();x?.dispose();});});}}
function line(points,color,opacity=1,dashed=false){const g=new THREE.BufferGeometry().setFromPoints(points),mat=dashed?new THREE.LineDashedMaterial({color,dashSize:14,gapSize:10,transparent:true,opacity}):new THREE.LineBasicMaterial({color,transparent:opacity<1,opacity}),l=new THREE.Line(g,mat);if(dashed)l.computeLineDistances();l.raycast=ignoreRaycast;return l;}
function tube(points,color,radius){if(points.length<2)return null;const path=new THREE.CurvePath();for(let i=1;i<points.length;i++)if(points[i].distanceTo(points[i-1])>.01)path.add(new THREE.LineCurve3(points[i-1],points[i]));if(!path.curves.length)return null;const m=new THREE.Mesh(new THREE.TubeGeometry(path,Math.max(8,path.curves.length*12),radius,6,false),new THREE.MeshStandardMaterial({color,emissive:color,emissiveIntensity:.35,roughness:.5}));m.raycast=ignoreRaycast;return m;}
function label(text,color='#f4f7fb'){const c=document.createElement('canvas'),x=c.getContext('2d');x.font='600 30px sans-serif';const w=Math.ceil(x.measureText(text).width)+28;c.width=w;c.height=46;x.font='600 30px sans-serif';x.fillStyle='rgba(12,22,34,.86)';x.beginPath();x.roundRect(0,0,w,46,12);x.fill();x.fillStyle=color;x.textBaseline='middle';x.fillText(text,14,24);const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;const s=new THREE.Sprite(new THREE.SpriteMaterial({map:t,depthTest:false,sizeAttenuation:false}));s.scale.set(w/46*.028,.028,1);s.renderOrder=10;s.raycast=ignoreRaycast;return s;}
function drone(v,color){
  const root=new THREE.Group(),body=new THREE.Group(),paint=new THREE.MeshStandardMaterial({color,roughness:.45,metalness:.2}),dark=new THREE.MeshStandardMaterial({color:'#1b2633',roughness:.6});
  body.add(new THREE.Mesh(new THREE.BoxGeometry(2.4,.9,2.4),paint));
  for(const a of [45,135,225,315]){const r=a*Math.PI/180,arm=new THREE.Mesh(new THREE.BoxGeometry(3.6,.25,.35),dark);arm.position.set(Math.cos(r)*1.8,.2,Math.sin(r)*1.8);arm.rotation.y=-r;body.add(arm);const rotor=new THREE.Mesh(new THREE.CylinderGeometry(1.3,1.3,.12,20),new THREE.MeshStandardMaterial({color,transparent:true,opacity:.55}));rotor.position.set(Math.cos(r)*3.4,.45,Math.sin(r)*3.4);body.add(rotor);}
  const nose=new THREE.Mesh(new THREE.ConeGeometry(.55,1.4,10),new THREE.MeshStandardMaterial({color:'#ffffff',emissive:color,emissiveIntensity:.4}));nose.rotation.x=-Math.PI/2;nose.position.z=-1.9;body.add(nose);
  body.rotation.set((v.attitude?.pitch||0)*Math.PI/180,-(v.attitude?.yaw||0)*Math.PI/180,-(v.attitude?.roll||0)*Math.PI/180,'YXZ');body.userData.scaleWithDistance=true;
  body.traverse(m=>{m.userData.vehicle=v;});root.add(body);return root;
}

let latest=null,framed='',followTarget=null,drawn='';
function update(d){
  latest=d;if(!d||canvas.hidden)return;loadGround(d.anchor);
  const signature=JSON.stringify([d.algorithm,d.regions,d.zones,d.patrol,d.dynamicEvidence,d.situation?.airspace?.revision,d.safetyPlan?.id,d.safetyPlan?.selected_route,d.situation?.incidents?.map(i=>[i.id,i.active,i.status]),d.situation?.tasks,d.situation?.conflicts,d.supervision,d.businessLayers,d.anchorKey,d.selected,d.fence,d.mission,d.track.length,d.track.at(-1),d.draft,d.footprint,d.buildings.length,d.buildings[0],d.cruise,d.follow?.estimate,d.follow3d,d.layers,d.vehicles.map(v=>[v.position,v.attitude,v.disposition_route,v.disposition_wp])]);
  if(signature===drawn)return;drawn=signature;clearDynamic();followTarget=null;
  const f=d.fence;
  for(const region of d.regions||[]){const points=region.polygon.map(p=>Core.enu([p[0],p[1],2],d.anchor));if(points.length){points.push(points[0]);dynamic.add(line(points.map(p=>W(...p)),'#68bfff',.9,true));const tag=label(region.name+' · 仿真监管区域','#8ccfff');tag.position.copy(W(...points[0]));dynamic.add(tag);}}
  for(const zone of d.zones||[]){const points=zone.polygon.map(p=>Core.enu([p[0],p[1],zone.z_min||0],d.anchor));if(points.length){points.push(points[0]);for(const h of [zone.z_min||0,zone.z_max||300])dynamic.add(line(points.map(p=>W(p[0],p[1],h)),'#ff6379',.8,true));for(const p of points.slice(0,-1))dynamic.add(line([W(p[0],p[1],zone.z_min||0),W(p[0],p[1],zone.z_max||300)],'#ff6379',.4));const tag=label('仿真禁飞区 · '+(zone.name||zone.id),'#ff879e');tag.position.copy(W(points[0][0],points[0][1],zone.z_max||300));dynamic.add(tag);}}
  if(d.layers.fence){const top=f.z1??300,ring=z=>[W(f.x0,f.y0,z),W(f.x1,f.y0,z),W(f.x1,f.y1,z),W(f.x0,f.y1,z),W(f.x0,f.y0,z)];dynamic.add(line(ring(1.5),'#efb954',.95),line(ring(top),'#efb954',.55,true));for(const [x,y] of [[f.x0,f.y0],[f.x1,f.y0],[f.x1,f.y1],[f.x0,f.y1]])dynamic.add(line([W(x,y,0),W(x,y,top)],'#efb954',.55,true));const cap=label(`围栏上限 ${Math.round(top)} m`,'#f5ca78');cap.position.copy(W(f.x0,f.y1,top));dynamic.add(cap);}
  if(d.patrol)for(const a of d.patrol.assignments){const [x,y,x1,y1]=a.area.bounds;const points=[[x,y,1],[x1,y,1],[x1,y1,1],[x,y1,1],[x,y,1]].map(p=>window.GCSCore.enu(window.GCSCore.geo(p,a.anchor),d.anchor));dynamic.add(line(points.map(p=>W(...p)),'#53c8be',.7,true));const tag=label(a.vehicle_id+' · '+a.area.name,'#8ee6d7');tag.position.copy(W(...points[0]));dynamic.add(tag);}
  if(d.layers.buildings){
    // Safety geometry stays exact; translucent envelopes avoid pretending to be surveyed buildings.
    for(const b of d.buildings){
      const tall=b.h>=d.cruise;
      const wall=new THREE.MeshStandardMaterial({color:'#b3c3c9',roughness:1,metalness:0,transparent:true,opacity:.19,depthWrite:false});
      const roof=new THREE.MeshStandardMaterial({color:tall?'#d7c49b':'#d3dce0',roughness:1,metalness:0,transparent:true,opacity:.32,depthWrite:false});
      const m=new THREE.Mesh(new THREE.BoxGeometry(b.w,b.h,b.d),[wall,wall,roof,wall,wall,wall]);
      m.position.copy(W(b.x+b.w/2,b.y+b.d/2,b.h/2));m.userData.building=b;dynamic.add(m);
      const ring=[[b.x,b.y,b.h],[b.x+b.w,b.y,b.h],[b.x+b.w,b.y+b.d,b.h],[b.x,b.y+b.d,b.h],[b.x,b.y,b.h]];
      dynamic.add(line(ring.map(p=>W(...p)),tall?'#d5b77d':'#b8ced7',.65));
      const e=new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry),new THREE.LineBasicMaterial({color:'#b1c4cd',transparent:true,opacity:.16,depthWrite:false}));e.position.copy(m.position);e.raycast=ignoreRaycast;dynamic.add(e);
    }
  }
  const route=(points,color,radius,tag)=>{if(points.length<1)return;const pts=points.map(p=>W(p[0],p[1],p[2]));const t=tube(pts,color,radius);if(t)dynamic.add(t);pts.forEach((p,i)=>{if(p.y>2)dynamic.add(line([new THREE.Vector3(p.x,0,p.z),p],color,.6,true));if(!tag)return;const s=new THREE.Mesh(new THREE.SphereGeometry(radius*2.2,14,10),new THREE.MeshStandardMaterial({color:'#ffffff',emissive:color,emissiveIntensity:.6}));s.position.copy(p);s.userData.waypoint={tag,index:i+1,z:points[i][2]};dynamic.add(s);});};
  if(d.dynamicEvidence){
    for(const points of Object.values(d.dynamicEvidence.original))route(points,'#c4ccd4',2,'原路线');
    for(const e of d.dynamicEvidence.evidence){
      if(e.original_route)route(e.original_route,'#9aa7b5',1.5,null);
      if(e.route)route(e.route,'#ffa629',3,'调整路线');
      for(const r of e.reasons||[])if(r.position){const m=new THREE.Mesh(new THREE.SphereGeometry(10,16,12),new THREE.MeshBasicMaterial({color:'#ff526d'}));m.position.copy(W(...r.position));dynamic.add(m);const tag=label(`冲突 ${r.target||r.obstacle_id||r.kind}${r.seconds!==undefined?' · '+r.seconds+'s / '+r.separation_m+'m':' · 安全间隔边界'}`,'#ff879e');tag.position.copy(W(r.position[0],r.position[1],r.position[2]+25));dynamic.add(tag);}
      if(e.state==='holding'&&e.position){const tag=label('暂停：'+(e.reasons||[]).map(r=>r.obstacle_id||r.target||r.kind).join(' / '),'#c69aff');tag.position.copy(W(e.position[0],e.position[1],e.position[2]+30));dynamic.add(tag);}
    }
  }
  if(d.businessLayers&&d.situation)for(const t of d.situation.tasks){
    if(!t.anchor||t.service_status==='cancelled')continue;
    const points=t.route.map(p=>window.GCSCore.enu(window.GCSCore.geo([p.x,p.y,p.z],t.anchor),d.anchor));
    const status=t.execution_status||t.service_status,color=t.validation_passed===false?'#ee8794':status==='completed'?'#62d4ad':status==='flying'?'#55c9ee':'#f2be64';
    route(points,color,1.8,null);
    if(points.length){const p=points[0],tag=label(`${t.id} · ${status}`,color);tag.position.copy(W(p[0],p[1],p[2]+42));dynamic.add(tag);}
  }
  if(d.supervision&&d.situation){const positions=Object.fromEntries(d.situation.vehicles.map(v=>[v.id,v.position]));if(d.situation.track?.estimate)positions['unconfirmed-target']=d.situation.track.estimate;
    for(const c of d.situation.conflicts){const p=c.pair.map(id=>positions[id]);if(p.every(Boolean)){dynamic.add(line(p.map(q=>W(...q)),'#ff687b',1,true));const marker=new THREE.Mesh(new THREE.SphereGeometry(12,16,12),new THREE.MeshBasicMaterial({color:'#ff687b'}));marker.position.copy(W(...p[0]).lerp(W(...p[1]),.5));marker.userData.incident=d.situation.incidents?.find(i=>i.active&&i.kind==='predicted_conflict'&&i.pair.slice().sort().join()===c.pair.slice().sort().join())?.id;dynamic.add(marker);}}
  }
  for(const v of d.vehicles)if(v.disposition_route?.length)route([v.position,...v.disposition_route.slice(v.disposition_wp||0)],'#ffa629',3,'处置航点');
  if(d.supervision&&d.situation?.airspace){const space=d.situation.airspace;for(const z of space.zones){const p=window.GCSCore.enu(window.GCSCore.geo([z.x+z.w/2,z.y+z.d/2,(z.z_min+z.z_max)/2],space.anchor),d.anchor);const mesh=new THREE.Mesh(new THREE.BoxGeometry(z.w,z.z_max-z.z_min,z.d),new THREE.MeshBasicMaterial({color:'#ff6379',transparent:true,opacity:.18,depthWrite:false}));mesh.position.copy(W(...p));dynamic.add(mesh);}}
  if(d.safetyPlan){const plan=d.safetyPlan,choice=plan.choices.find(c=>c.id===plan.selected_route),local=p=>window.GCSCore.enu(window.GCSCore.geo(p,plan.anchor),d.anchor);
    if(choice)route(choice.route.map(local),'#ffa629',3,'候选处置航点');
    for(const z of plan.airspace.zones){const p=local([z.x+z.w/2,z.y+z.d/2,(z.z_min+z.z_max)/2]);const mesh=new THREE.Mesh(new THREE.BoxGeometry(z.w,z.z_max-z.z_min,z.d),new THREE.MeshBasicMaterial({color:'#ff6379',transparent:true,opacity:.18,depthWrite:false}));mesh.position.copy(W(...p));dynamic.add(mesh);}
  }
  if(d.layers.mission)route(d.mission,'#55c9ee',2.6,'航点');
  if(d.layers.track)route(d.track,'#62d4ad',1.6,null);
  if(d.draft.length)route(d.draft,'#f8c774',2.4,'草案航点');
  if(d.algorithm){
    route(d.algorithm.points,'#f8c774',2.8,'算法巡航航点');
    for(const n of [...(d.algorithm.topology?.nodes||d.algorithm.nodes.filter(n=>n.kind==='edge')),...(d.algorithm.hideSample?[]:[{id:'算法采样点',position:d.algorithm.position}])]){
      const marker=new THREE.Mesh(new THREE.SphereGeometry(7,12,8),new THREE.MeshBasicMaterial({color:n.color||'#b6a0ff'}));marker.position.copy(W(...n.position));marker.userData.computeNode=n.id;dynamic.add(marker);
      const tag=label(n.label||n.id,n.color||'#d4c7ff');if(d.algorithm.topology){tag.userData.computeNode=n.id;tag.raycast=THREE.Sprite.prototype.raycast;}tag.position.copy(W(n.position[0],n.position[1],n.position[2]+(n.kind==='device'?85:n.kind==='cloud'?65:35)));dynamic.add(tag);
    }
    for(const l of d.algorithm.topology?.links||[]){const points=l.points.map(p=>W(p[0],p[1],p[2]+12));if(l.status==='online'){const link=tube(points,l.color,1.8);if(link){link.raycast=THREE.Mesh.prototype.raycast;link.userData.computeDetail=['link',l.source+'→'+l.target];dynamic.add(link);}}else {for(let i=0;i<10;i+=2){const link=tube([points[0].clone().lerp(points[1],i/10),points[0].clone().lerp(points[1],(i+1)/10)],l.color,2.4);if(link){link.raycast=THREE.Mesh.prototype.raycast;link.userData.computeDetail=['link',l.source+'→'+l.target];dynamic.add(link);}}}}
    for(const m of d.algorithm.topology?.migrations||[]){
      const points=m.points.map(p=>W(p[0],p[1],p[2]+15)),path=line(points,m.color,1,true);path.raycast=THREE.Line.prototype.raycast;path.userData.computeDetail=['migration',m.batch_id+'|'+m.task+'|'+m.at];dynamic.add(path);
      const tag=label((m.label.includes('迁移完成')?'迁移完成':'迁移中')+' · '+m.previous_node+' → '+m.node,m.color);tag.position.copy(points[1]);tag.raycast=THREE.Sprite.prototype.raycast;tag.userData.computeDetail=['migration',m.batch_id+'|'+m.task+'|'+m.at];dynamic.add(tag);
      const origin=points.at(-2),direction=points.at(-1).clone().sub(origin),length=direction.length();if(length>1)dynamic.add(new THREE.ArrowHelper(direction.normalize(),origin,length,m.color,Math.min(25,length*.2),12));
    }
    for(const b of d.algorithm.boxes){const lo=b.lower,hi=b.upper;const geometry=new THREE.BoxGeometry(hi[0]-lo[0],hi[2]-lo[2],hi[1]-lo[1]);const wire=new THREE.LineSegments(new THREE.EdgesGeometry(geometry),new THREE.LineBasicMaterial({color:b.kind==='no_fly'?'#ff6379':'#8fa2b4',transparent:true,opacity:.5}));geometry.dispose();wire.position.copy(W((lo[0]+hi[0])/2,(lo[1]+hi[1])/2,(lo[2]+hi[2])/2));dynamic.add(wire);}
  }

  if(d.layers.view&&d.footprint.length>2){const shape=new THREE.Shape(d.footprint.map(p=>new THREE.Vector2(p[0]-1000,p[1]-1000))),m=new THREE.Mesh(new THREE.ShapeGeometry(shape),new THREE.MeshBasicMaterial({color:'#b695fc',transparent:true,opacity:.35,depthWrite:false,side:THREE.DoubleSide}));m.rotation.x=-Math.PI/2;m.position.y=1;m.raycast=ignoreRaycast;dynamic.add(m);}
  if(d.follow?.estimate){const e=d.follow.estimate;if(d.follow.track?.length>1){const t=tube(d.follow.track.map(p=>W(p[0],p[1],p[2])),'#e07a8a',1.4);if(t)dynamic.add(t);}const s=new THREE.Mesh(new THREE.SphereGeometry(5,16,12),new THREE.MeshStandardMaterial({color:'#e07a8a',emissive:'#e07a8a',emissiveIntensity:.5}));s.position.copy(W(e[0],e[1],e[2]));s.userData.target=d.follow;s.userData.incident=d.situation?.incidents?.find(i=>i.active&&i.kind==='identity_unconfirmed')?.id;dynamic.add(s);dynamic.add(line([W(e[0],e[1],0),W(e[0],e[1],e[2])],'#e07a8a',.7,true));const l=label(`跟踪点 · ${d.follow.identity}`,'#ffc2cb');l.position.copy(W(e[0],e[1],e[2]+22));dynamic.add(l);}
  for(const v of d.vehicles){const mine=v.id===d.selected,color=mine?'#55c9ee':'#9cb2c8',p=v.position,alt=Math.max(p[2],0),g=drone(v,color);g.position.copy(W(p[0],p[1],alt+1.5));dynamic.add(g);dynamic.add(line([W(p[0],p[1],0),W(p[0],p[1],alt)],color,.8));const ring=new THREE.Mesh(new THREE.RingGeometry(5,8,32),new THREE.MeshBasicMaterial({color,transparent:true,opacity:.7,depthWrite:false,side:THREE.DoubleSide}));ring.rotation.x=-Math.PI/2;ring.position.copy(W(p[0],p[1],1.2));ring.raycast=ignoreRaycast;dynamic.add(ring);const home=label('H','#bdedce');home.position.copy(W(v.home[0],v.home[1],8));dynamic.add(home);const l=label(`${v.name} · ${alt.toFixed(0)} m`,mine?'#bff0ff':'#e6edf7');l.position.copy(W(p[0],p[1],alt+26));g.userData.label=l;dynamic.add(l);if(mine&&d.follow3d)followTarget=W(p[0],p[1],alt);}
  if(framed!==d.anchorKey){framed=d.anchorKey;frame('fence');}
}

function frame(kind){const d=latest;if(!d)return;if(kind==='algorithm'&&d.algorithm){const b=d.algorithm.bounds,lo=b.xyz_min,hi=b.xyz_max,c=W((lo[0]+hi[0])/2,(lo[1]+hi[1])/2,(lo[2]+hi[2])/2),span=Math.max(hi[0]-lo[0],hi[1]-lo[1]);controls.target.copy(c);camera.position.copy(c).add(new THREE.Vector3(-span*.3,span*1.8,span*1.3));controls.update();return;}if(kind==='top'){const f=d.fence;const c=W((f.x0+f.x1)/2,(f.y0+f.y1)/2,0);controls.target.copy(c);camera.position.copy(c).add(new THREE.Vector3(0,Math.max(f.x1-f.x0,f.y1-f.y0)*1.15,1));controls.update();return;}if(kind==='vehicle'){const v=d.vehicles.find(x=>x.id===d.selected);if(v){const p=W(v.position[0],v.position[1],v.position[2]||0);controls.target.copy(p);camera.position.copy(p).add(new THREE.Vector3(-220,150,280));controls.update();return;}}const f=d.fence,c=W((f.x0+f.x1)/2,(f.y0+f.y1)/2,Math.min(120,(f.z1||120)*.28)),span=Math.max(f.x1-f.x0,f.y1-f.y0);controls.target.copy(c);camera.position.copy(c).add(new THREE.Vector3(-span*.15,span*1.1,span*.65));controls.update();}

const ray=new THREE.Raycaster(),pointer=new THREE.Vector2();
function hideTip(){tip.hidden=true;controls.enabled=true;}
function showHeight(e){
  const r=canvas.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);
  const hit=ray.intersectObjects([...dynamic.children,...(imagery?imagery.group.children:[]),base],true)[0];if(!hit){hideTip();return;}
  const p=hit.point,east=Math.round(p.x+1000),north=Math.round(1000-p.z),h=Math.max(0,Math.round(p.y)),u=hit.object.userData;let text=`东 ${east} m · 北 ${north} m · 相对高度 ${h} m`;
  if(u.vehicle)text=`${u.vehicle.name} · 相对高度 ${u.vehicle.position[2].toFixed(1)} m · 地速 ${(u.vehicle.speed_mps??u.vehicle.speed??0).toFixed(1)} m/s`;
  else if(u.waypoint)text=`${u.waypoint.tag} ${u.waypoint.index} · 高度 ${u.waypoint.z} m`;
  else if(u.building)text=`合成建筑 · 顶高 ${u.building.h} m · 光标 ${h} m`;
  else if(u.target)text=`跟踪点 · ${u.target.identity} · 高度 ${Number(u.target.estimate[2]).toFixed(0)} m`;
  else if(u.ground)text=`地面 · 东 ${east} m · 北 ${north} m · 相对起飞面 0 m`;
  tip.textContent=text;tip.hidden=false;const s=stage.getBoundingClientRect();tip.style.left=Math.min(e.clientX-s.left+16,s.width-tip.offsetWidth-8)+'px';tip.style.top=Math.max(8,e.clientY-s.top-34)+'px';
}
canvas.addEventListener('pointerdown',e=>{if(e.ctrlKey)controls.enabled=false;},true);
canvas.addEventListener('pointermove',e=>{if(!e.ctrlKey){if(!tip.hidden)hideTip();return;}controls.enabled=false;showHeight(e);});
canvas.addEventListener('pointerleave',hideTip);
window.addEventListener('keyup',e=>{if(e.key==='Control')hideTip();});
canvas.addEventListener('contextmenu',e=>e.preventDefault());

function resize(){const w=stage.clientWidth,h=stage.clientHeight;if(w<2||h<2)return;const c=renderer.domElement;if(c.width!==Math.floor(w*renderer.getPixelRatio())||c.height!==Math.floor(h*renderer.getPixelRatio())){renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();}}
const probe=new THREE.Vector3();
renderer.setAnimationLoop(()=>{
  if(canvas.hidden)return;resize();
  if(followTarget){controls.target.lerp(followTarget,.08);}
  controls.target.x=THREE.MathUtils.clamp(controls.target.x,-1800,1800);controls.target.z=THREE.MathUtils.clamp(controls.target.z,-1800,1800);controls.target.y=THREE.MathUtils.clamp(controls.target.y,0,500);controls.update();
  for(const o of dynamic.children)for(const part of o.children)if(part.userData.scaleWithDistance){o.getWorldPosition(probe);const s=Math.max(1,Math.min(6,camera.position.distanceTo(probe)/160));part.scale.setScalar(s);const l=o.userData.label;if(l)l.position.y=o.position.y+12+6*s;}
  renderer.render(scene,camera);
});

function show(){geographicBuildings.setVisible(true);canvas.hidden=false;status.hidden=false;drawn='';resize();if(latest)update(latest);}
function hide(){geographicBuildings.setVisible(false);canvas.hidden=true;status.hidden=true;hideTip();}
status.hidden=true;geographicBuildings.setVisible(false);
window.GCS3D={show,hide,update,frame};
window.dispatchEvent(new Event('gcs3d-ready'));

let incidentDown=null;
canvas.addEventListener('pointerdown',e=>{incidentDown=[e.clientX,e.clientY];});
canvas.addEventListener('pointerup',e=>{if(!incidentDown||Math.hypot(e.clientX-incidentDown[0],e.clientY-incidentDown[1])>5)return;const r=canvas.getBoundingClientRect();ray.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,1-(e.clientY-r.top)/r.height*2),camera);const hit=ray.intersectObjects(dynamic.children,true).find(h=>h.object.userData.incident||h.object.userData.computeNode||h.object.userData.computeDetail);if(hit?.object.userData.computeDetail){window.showComputeDetail?.(...hit.object.userData.computeDetail);return;}if(hit?.object.userData.computeNode){window.showComputeNode?.(hit.object.userData.computeNode);return;}if(hit)window.dispatchEvent(new CustomEvent('gcs-incident',{detail:hit.object.userData.incident}));});
