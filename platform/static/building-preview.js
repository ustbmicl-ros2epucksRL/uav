import * as THREE from 'three';
import {OrbitControls} from './vendor/three/OrbitControls.js';
import {localFrame,imageryGround,groundBase} from './imagery-ground.js';
import {realBuildings} from './real-buildings.js';

const stage=document.getElementById('stage'),canvas=stage.querySelector('canvas'),status=document.getElementById('image-status'),retry=document.getElementById('retry');
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});renderer.setPixelRatio(Math.min(2,devicePixelRatio));renderer.outputColorSpace=THREE.SRGBColorSpace;
const scene=new THREE.Scene();scene.background=new THREE.Color('#33433a');scene.add(groundBase(60000,'#33433a'));
scene.add(new THREE.HemisphereLight(0xffffff,0x40504a,1));const sun=new THREE.DirectionalLight(0xffffff,1.3);sun.position.set(-900,1200,600);scene.add(sun);
const camera=new THREE.PerspectiveCamera(45,1,2,40000),controls=new OrbitControls(camera,canvas);controls.enableDamping=true;controls.minPolarAngle=.01;controls.maxPolarAngle=Math.PI*.38;controls.minDistance=100;controls.maxDistance=6000;controls.screenSpacePanning=false;
const regions={beijing:{lat:39.901,lon:116.452,file:'./buildings-beijing.geojson',centre:[700,800]},flight:{lat:39.05,lon:115.98,file:'./buildings.geojson',centre:[1000,1000]},planning:{lat:39,lon:115.95,file:'./buildings.geojson',centre:[1000,1000]}};
const W=(x,y,z=0)=>new THREE.Vector3(x-1000,z,1000-y);let current=regions.beijing,ground,generation=0,top=false;
function home(){controls.target.copy(W(...current.centre,0));camera.position.copy(controls.target).add(top?new THREE.Vector3(0,2200,8):new THREE.Vector3(-280,1650,1250));controls.update();}
const buildings=realBuildings({host:stage,scene,frame:localFrame(current.lat,current.lon),place:W,panelBottom:36,dataUrl:current.file,previewLink:false,onView:mode=>{top=mode==='top';home();}});
function selectRegion(){const id=++generation;current=regions[document.getElementById('region').value];const frame=localFrame(current.lat,current.lon);ground?.dispose();buildings.setSource(current.file,frame);
 ground=imageryGround({renderer,frame,centre:current.centre,place:(e,n)=>W(e,n),url:(z,x,y)=>new URL(`../tiles/imagery/${z}/${x}/${y}`,import.meta.url).href,levels:[{z:16,from:[-400,-400],to:[2400,2400]},{z:14,from:[-5000,-5000],to:[7000,7000]},{z:12,from:[-12000,-12000],to:[14000,14000]}],onStatus:({loaded,total,done,failed})=>{if(id!==generation)return;retry.hidden=!(done&&failed);status.textContent=done?(failed?`${failed} 张影像加载失败`:'卫星影像已就绪'):`卫星影像 ${loaded}/${total}`;}});scene.add(ground.group);ground.load();home();}
retry.onclick=()=>ground?.load();document.getElementById('region').onchange=selectRegion;document.getElementById('home').onclick=home;canvas.addEventListener('pointerup',e=>buildings.inspect(e,camera,canvas));
renderer.setAnimationLoop(()=>{const width=stage.clientWidth,height=stage.clientHeight;if(canvas.width!==Math.floor(width*renderer.getPixelRatio())||canvas.height!==Math.floor(height*renderer.getPixelRatio())){renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();}controls.update();renderer.render(scene,camera);});selectRegion();
