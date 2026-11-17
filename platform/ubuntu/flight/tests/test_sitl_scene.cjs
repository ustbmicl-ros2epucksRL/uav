const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),{pathToFileURL}=require('node:url');
test('actual Three scene renders SITL full attitude and clears aircraft on stale data',async()=>{
 const Three=await import(pathToFileURL(path.resolve(__dirname,'../../../static/vendor/three/three.module.js')));
 const core=require('../app/static/gcs-core.js'),adapter=require('../app/static/sitl-twin.js');
 let drawn,animate;
 const context=new Proxy({measureText:()=>({width:200})},{get:(o,k)=>o[k]||(()=>{})});
 const stage={clientWidth:1000,clientHeight:700,appendChild(){}};
 const canvas={parentElement:stage,hidden:false,width:1000,height:700,addEventListener(){},getContext:()=>context};
 class Renderer{constructor(){this.domElement=canvas}setPixelRatio(){}getPixelRatio(){return 1}setSize(){}setAnimationLoop(fn){animate=fn}render(s){drawn=s}}
 class Controls{constructor(){this.target=new Three.Vector3()}update(){}}
 const w={GCSCore:core,devicePixelRatio:1,addEventListener(){},dispatchEvent(){}};
 const c=vm.createContext({THREE:{...Three,WebGLRenderer:Renderer},OrbitControls:Controls,window:w,document:{getElementById:()=>canvas,createElement:()=>({style:{},getContext:()=>context})},realBuildings:()=>({setFrame(){},setVisible(){},inspect(){}}),localFrame:()=>({}),imageryGround:()=>({group:new Three.Group(),load(){},dispose(){}}),groundBase:()=>new Three.Group(),Event:class{},console});
 vm.runInContext(fs.readFileSync(__dirname+'/../app/static/gcs3d.js','utf8').replace(/^import .*;\n/gm,''),c);
 const d=adapter.scene({anchor:{latitude:39,longitude:116},position_valid:true,latitude:39.001,longitude:116.002,telemetry:{relative_altitude_m:80,roll_deg:12,pitch_deg:-8,yaw_deg:90,groundspeed_mps:5},track:[],mission:[]},core);
 w.GCS3D.update(d);animate();let body;
 drawn.traverse(o=>{if(o.userData.scaleWithDistance)body=o});assert(body);assert(Math.abs(body.rotation.x+8*Math.PI/180)<1e-9);assert(Math.abs(body.rotation.y+Math.PI/2)<1e-9);assert(Math.abs(body.rotation.z+12*Math.PI/180)<1e-9);assert.equal(body.rotation.order,'YXZ');
 const previous=body;d.vehicles[0].attitude.roll=25;w.GCS3D.update(d);animate();drawn.traverse(o=>{if(o.userData.scaleWithDistance)body=o});assert.notEqual(body,previous);assert(Math.abs(body.rotation.z+25*Math.PI/180)<1e-9);
 d.vehicles=[];w.GCS3D.update(d);animate();let count=0;drawn.traverse(o=>{if(o.userData.scaleWithDistance)count++});assert.equal(count,0);
});
