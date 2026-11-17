import * as THREE from 'three';
import {normalizeBuildings} from './building-data.mjs';
// Geographic reference layer only: never silently substitutes simulation obstacles.
export function realBuildings({host,scene,frame,place,panelZ=5,panelBottom=32,onView,dataUrl='./buildings.geojson',previewLink=true,initialVisible=true,compact=false}){
 const group=new THREE.Group();scene.add(group);let currentFrame=frame,records=[],visible=initialVisible;
 const box=document.createElement(compact?'details':'div');if(compact){const title=document.createElement('summary');title.textContent='建筑参考图层与来源';title.style.cursor='pointer';box.append(title);}Object.assign(box.style,{position:'absolute',left:'12px',bottom:panelBottom+'px',zIndex:panelZ,background:'rgba(255,255,255,.94)',color:'#253b4c',borderRadius:'6px',padding:'8px',fontSize:'12px',maxWidth:'360px'});
 const toggle=document.createElement('input');toggle.type='checkbox';toggle.checked=initialVisible;
 const captionText=document.createElement('span');captionText.textContent=' 地理建筑轮廓';
 const caption=document.createElement('label');caption.append(toggle,captionText);
 const upload=document.createElement('input');upload.type='file';upload.accept='.geojson,.json';upload.hidden=true;
 const button=document.createElement('button');button.textContent='导入 GeoJSON';button.style.cssText='margin-left:12px;padding:3px 8px;font-size:12px';button.onclick=()=>upload.click();
 const status=document.createElement('div');status.style.cssText='margin-top:5px;line-height:1.5';status.textContent='正在检查建筑数据…';
 const note=document.createElement('div');note.textContent='参考图层；未接入覆盖/绕障计算。高度未核验。';note.style.cssText='font-size:10px;color:#687b88';
 const outline=document.createElement('input');outline.type='checkbox';
 const outlineLabel=document.createElement('label');outlineLabel.append(outline,document.createTextNode(' 仅看轮廓（核对底图）'));outlineLabel.style.cssText='display:block;margin-top:5px';
 const credit=document.createElement('a');credit.style.cssText='display:block;font-size:10px;color:#536b7b;margin-top:3px';credit.target='_blank';credit.rel='noopener';
 box.append(caption,button,upload,status,outlineLabel,note,credit);host.appendChild(box);
 if(previewLink){const link=document.createElement('a');link.href=new URL('./building-preview.html',import.meta.url).href;link.textContent='切换区域预览：雄安 / 北京国贸 ↗';link.target='_blank';link.rel='noopener';link.style.cssText='display:block;margin-top:4px;color:#255b83';box.append(link);}
 function displayMode(){for(const o of group.children)o.visible=o.userData.footprint?outline.checked:!outline.checked;}
 outline.onchange=()=>{if(outline.checked){visible=true;toggle.checked=true;group.visible=true;}displayMode();onView?.(outline.checked?'top':'oblique');};
 function provenance(data){const gba=data.metadata?.source==='https://github.com/zhu-xlab/GlobalBuildingAtlas',osm=data.metadata?.source==='https://www.openstreetmap.org/copyright';captionText.textContent=gba?' 历史建筑轮廓（2019 基期）':' 地理建筑轮廓';credit.textContent=gba?'GlobalBuildingAtlas · Zhu et al. (2025) · 高度 CC BY-NC 4.0':osm?'© OpenStreetMap contributors · ODbL':'';credit.hidden=!(gba||osm);credit.href=gba||osm?data.metadata.source:'';note.textContent=gba?'雄安区域裁剪 · 主要影像基期 2019 · 模型估计高度；非商业研究参考，不用于绕障。':osm?'北京国贸对照 · 灰蓝为标注高度，暖灰为楼层估计或缺失高度示意；均未测绘核验。':'参考图层；未接入覆盖/绕障计算。高度未核验。';}
 function clear(){for(const m of [...group.children]){group.remove(m);m.traverse(o=>{o.geometry?.dispose();for(const mat of [].concat(o.material||[]))mat.dispose();});}}
 function draw(){clear();let count=0,estimated=0;const footprintPoints=[];
  for(const b of records){const rings=b.rings.map(r=>r.map(q=>currentFrame.toLocal(...q)));const xs=rings[0].map(p=>p[0]),ys=rings[0].map(p=>p[1]);if(Math.max(...xs)<-1000||Math.min(...xs)>3000||Math.max(...ys)<-1000||Math.min(...ys)>3000)continue;
   for(const ring of rings)for(let i=1;i<ring.length;i++)footprintPoints.push(place(...ring[i-1],.5),place(...ring[i],.5));
   const path=r=>r.map(([x,y])=>new THREE.Vector2(x,y));const shape=new THREE.Shape(path(rings[0]));for(const r of rings.slice(1))shape.holes.push(new THREE.Path(path(r)));
   const geometry=new THREE.ExtrudeGeometry(shape,{depth:b.height,bevelEnabled:false,steps:1});const pos=geometry.getAttribute('position');for(let i=0;i<pos.count;i++){const p=place(pos.getX(i),pos.getY(i),pos.getZ(i));pos.setXYZ(i,p.x,p.y,p.z);}geometry.computeVertexNormals();
   const roof=new THREE.MeshStandardMaterial({color:b.estimated?0xc9c8bf:0xcbd3d5,roughness:1,metalness:0});
   const wall=new THREE.MeshStandardMaterial({color:b.estimated?0xaeb2ac:0xaab9bf,roughness:1,metalness:0,transparent:true,opacity:.82});
   const mesh=new THREE.Mesh(geometry,[roof,wall]);mesh.userData.realBuilding=b;group.add(mesh);
   const edges=new THREE.LineSegments(new THREE.EdgesGeometry(geometry,25),new THREE.LineBasicMaterial({color:0x73858d,transparent:true,opacity:.12}));edges.raycast=()=>{};group.add(edges);count++;if(b.estimated)estimated++;
  }
  if(footprintPoints.length){const footprint=new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(footprintPoints),new THREE.LineBasicMaterial({color:0xffdc71,depthTest:false}));footprint.userData.footprint=true;footprint.raycast=()=>{};footprint.renderOrder=3;group.add(footprint);}
  status.textContent=count?`${count} 栋轮廓 · ${estimated} 栋高度估算（暖灰色）`:'当前范围暂无建筑轮廓，可保留二维示意或导入 GeoJSON。';group.visible=visible;displayMode();
 }
 toggle.onchange=()=>{visible=toggle.checked;group.visible=visible;};
 upload.onchange=async()=>{const file=upload.files[0];if(!file)return;try{if(file.size>20*1024*1024)throw Error('文件不能超过 20 MB');const data=JSON.parse(await file.text()),parsed=normalizeBuildings(data);records=parsed;provenance(data);draw();}catch(e){status.textContent='导入失败：'+e.message;}finally{upload.value='';}};
 let imported=false,loadId=0;upload.addEventListener('change',()=>{imported=true;});
 async function load(url,f){const id=++loadId;imported=false;if(f)currentFrame=f;records=[];clear();status.textContent='正在检查建筑数据…';credit.hidden=true;try{const r=await fetch(new URL(url,import.meta.url),{cache:'no-cache'});if(!r.ok)throw Error('建筑数据暂不可用');const data=await r.json();if(id===loadId&&!imported){records=normalizeBuildings(data);provenance(data);draw();}}catch(e){if(id===loadId&&!imported)status.textContent=e.message+'，可导入 GeoJSON。';}}
 load(dataUrl);
 const ray=new THREE.Raycaster();let down;
 host.addEventListener('pointerdown',e=>down=[e.clientX,e.clientY]);
 return {group,setSource:load,setVisible(value){box.hidden=!value;group.visible=value&&visible;},setFrame(f){currentFrame=f;draw();},inspect(event,camera,canvas){if(!visible||!down||Math.hypot(event.clientX-down[0],event.clientY-down[1])>5)return;const r=canvas.getBoundingClientRect();ray.setFromCamera(new THREE.Vector2((event.clientX-r.left)/r.width*2-1,1-(event.clientY-r.top)/r.height*2),camera);const hit=ray.intersectObjects(group.children).find(h=>h.object.userData.realBuilding);if(hit){const b=hit.object.userData.realBuilding;status.textContent=`${b.name} · ${b.height} m · ${b.heightSource}`;}}};
}
