// Satellite imagery laid on a local east/north reference plane (metres). Shared by 感知诊断 and 地面站 3D views.
import * as THREE from 'three';
const EARTH=6378137,RAD=Math.PI/180;

export function localFrame(lat0,lon0){
  const k=EARTH*Math.cos(lat0*RAD)*RAD;
  return {lat0,lon0,toLocal:(lon,lat)=>[(lon-lon0)*k,(lat-lat0)*EARTH*RAD],toLonLat:(east,north)=>[lon0+east/k,lat0+north/(EARTH*RAD)]};
}
function tileOf(lon,lat,z){const n=2**z,r=lat*RAD;return [Math.floor((lon+180)/360*n),Math.floor((1-Math.log(Math.tan(r)+1/Math.cos(r))/Math.PI)/2*n)];}
const tileWest=(x,z)=>x/2**z*360-180;
const tileNorth=(y,z)=>Math.atan(Math.sinh(Math.PI-2*Math.PI*y/2**z))/RAD;
function loadImage(url){return new Promise((resolve,reject)=>{const image=new Image(),timer=setTimeout(()=>{image.src='';reject(Error('影像请求超时'));},30000);image.onload=()=>{clearTimeout(timer);resolve(image);};image.onerror=()=>{clearTimeout(timer);reject(Error('影像请求失败'));};image.src=url;});}

// Ground layers are coplanar, so they skip depth testing and are painted coarse→fine via renderOrder;
// anything above the ground still depth-tests against them. Keep every ground mesh at renderOrder < 0.
export const GROUND_BASE_ORDER=-100;
export function groundBase(size,color){const m=new THREE.Mesh(new THREE.PlaneGeometry(size,size),new THREE.MeshBasicMaterial({color,depthTest:false}));m.rotation.x=-Math.PI/2;m.renderOrder=GROUND_BASE_ORDER;m.userData.ground=true;return m;}

// levels: finest first, [{z, from:[east,north], to:[east,north]}]; place(east,north) maps a local point to scene space.
export function imageryGround({renderer,frame,levels,centre,url,place,onStatus=()=>{},workers=6}){
  const group=new THREE.Group(),jobs=[];let running=null,disposed=false;
  levels.forEach((level,priority)=>{
    const a=tileOf(...frame.toLonLat(...level.from),level.z),b=tileOf(...frame.toLonLat(...level.to),level.z),c=tileOf(...frame.toLonLat(...centre),level.z);
    for(let x=Math.min(a[0],b[0]);x<=Math.max(a[0],b[0]);x++)for(let y=Math.min(a[1],b[1]);y<=Math.max(a[1],b[1]);y++)
      jobs.push({z:level.z,x,y,order:-2-priority,loaded:false,rank:priority*1e6+(x-c[0])**2+(y-c[1])**2});
  });
  jobs.sort((p,q)=>p.rank-q.rank);
  const report=()=>{const loaded=jobs.filter(t=>t.loaded).length;onStatus({loaded,total:jobs.length,done:!running,failed:running?0:jobs.length-loaded});};
  async function add(t){
    const image=await loadImage(url(t.z,t.x,t.y));if(disposed)return;
    const [west,north]=frame.toLocal(tileWest(t.x,t.z),tileNorth(t.y,t.z)),[east,south]=frame.toLocal(tileWest(t.x+1,t.z),tileNorth(t.y+1,t.z));
    const texture=new THREE.Texture(image);texture.colorSpace=THREE.SRGBColorSpace;texture.anisotropy=renderer.capabilities.getMaxAnisotropy();texture.needsUpdate=true;
    const mesh=new THREE.Mesh(new THREE.PlaneGeometry(east-west,north-south),new THREE.MeshBasicMaterial({map:texture,depthTest:false}));
    mesh.rotation.x=-Math.PI/2;mesh.position.copy(place((west+east)/2,(north+south)/2));mesh.renderOrder=t.order;mesh.userData.ground=true;group.add(mesh);t.loaded=true;
  }
  function load(){
    if(running)return running;
    const pending=jobs.filter(t=>!t.loaded);let next=0;
    const worker=async()=>{while(next<pending.length&&!disposed){const t=pending[next++];try{await add(t);}catch{}report();}};
    running=Promise.all(Array.from({length:workers},worker)).then(()=>{running=null;if(!disposed)report();});
    report();return running;
  }
  function dispose(){disposed=true;for(const m of [...group.children]){group.remove(m);m.geometry.dispose();m.material.map?.dispose();m.material.dispose();}group.parent?.remove(group);}
  return {group,load,dispose};
}
