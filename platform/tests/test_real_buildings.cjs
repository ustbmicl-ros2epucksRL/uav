const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
test('import renders actual footprint, preserves courtyard and reports invalid files',async()=>{
 const root=path.resolve(__dirname,'../static'),url=f=>pathToFileURL(path.join(root,f)).href;
 const THREE=await import(url('vendor/three/three.module.js'));
 const elements=[];
 class Element{constructor(tag){this.tag=tag;this.style={};this.children=[];this.listeners={};elements.push(this);}appendChild(x){this.children.push(x);}append(...x){this.children.push(...x);}addEventListener(k,v){this.listeners[k]=v;}}
 const oldDocument=global.document,oldFetch=global.fetch;
 global.document={createElement:t=>new Element(t),createTextNode:t=>t};global.fetch=async()=>({ok:true,json:async()=>({type:'FeatureCollection',features:[]})});
 try{
  const src=fs.readFileSync(path.join(root,'real-buildings.js'),'utf8').replace("'three'",JSON.stringify(url('vendor/three/three.module.js'))).replace("'./building-data.mjs'",JSON.stringify(url('building-data.mjs'))).replaceAll('import.meta.url',JSON.stringify(url('real-buildings.js')));
  const {realBuildings}=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
  const scene=new THREE.Scene(),host=new Element('host');const layer=realBuildings({host,scene,initialVisible:false,frame:{toLocal:(x,y)=>[x*1000,y*1000]},place:(x,y,z)=>new THREE.Vector3(x,z,-y)});
  await new Promise(r=>setImmediate(r));assert.equal(layer.group.visible,false,'historical reference may start hidden');
  const upload=elements.find(e=>e.type==='file');const ring=[[0,0],[1,0],[1,1],[0,1],[0,0]],hole=[[.4,.4],[.6,.4],[.6,.6],[.4,.6],[.4,.4]];
  upload.files=[{size:100,text:async()=>JSON.stringify({type:'FeatureCollection',features:[{type:'Feature',properties:{height:30},geometry:{type:'Polygon',coordinates:[ring,hole]}}]})}];
  await upload.onchange();const mesh=layer.group.children.find(x=>x.isMesh);assert(mesh);mesh.geometry.computeBoundingBox();assert.equal(mesh.geometry.boundingBox.max.y,30);
  scene.updateMatrixWorld(true);const ray=new THREE.Raycaster(new THREE.Vector3(500,100,-500),new THREE.Vector3(0,-1,0));assert.equal(ray.intersectObject(mesh).length,0,'courtyard must remain empty');ray.set(new THREE.Vector3(200,100,-200),new THREE.Vector3(0,-1,0));assert(ray.intersectObject(mesh).length>0);
  const outline=elements.filter(e=>e.type==='checkbox')[1];outline.checked=true;outline.onchange();assert.equal(layer.group.visible,true);assert.equal(elements.filter(e=>e.type==='checkbox')[0].checked,true);assert.equal(mesh.visible,false);const footprint=layer.group.children.find(x=>x.userData.footprint);assert.equal(footprint.visible,true);footprint.geometry.computeBoundingBox();assert.equal(footprint.geometry.boundingBox.max.y,.5);outline.checked=false;outline.onchange();assert.equal(mesh.visible,true);assert.equal(footprint.visible,false);
  upload.files=[{size:100,text:async()=>'{invalid'}];await upload.onchange();assert(elements.some(e=>String(e.textContent).startsWith('导入失败')));assert.equal(layer.group.children.find(x=>x.isMesh),mesh,'bad import preserves good layer');
  const pending=[];global.fetch=()=>new Promise(resolve=>pending.push(resolve));
  const oldRegion=layer.setSource('./old-region.geojson'),newRegion=layer.setSource('./new-region.geojson',{toLocal:(x,y)=>[x*1000+100,y*1000]});
  const response=height=>({ok:true,json:async()=>({type:'FeatureCollection',features:[{type:'Feature',properties:{height},geometry:{type:'Polygon',coordinates:[ring]}}]})});
  pending[1](response(42));await newRegion;pending[0](response(99));await oldRegion;
  const selected=layer.group.children.find(x=>x.isMesh);assert.equal(selected.userData.realBuilding.height,42,'late old-region response must not overwrite the selected region');selected.geometry.computeBoundingBox();assert.equal(selected.geometry.boundingBox.min.x,100,'selected region uses its own coordinate frame');
 }finally{global.document=oldDocument;global.fetch=oldFetch;}
});
