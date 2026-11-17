/* Original platform coordinate and route utilities. X east, Y north, Z up. */
(function(root){
'use strict';const R=6378137,rad=Math.PI/180;
function geo(p,a){return [a.latitude+p[1]/R/rad,a.longitude+p[0]/(R*Math.cos(a.latitude*rad))/rad,p[2]||0];}
function enu(p,a){return [(p[1]-a.longitude)*rad*R*Math.cos(a.latitude*rad),(p[0]-a.latitude)*rad*R,p[2]||0];}
function validate(points){if(!Array.isArray(points)||points.length<2||points.length>20)throw Error('航线须有 2–20 个航点');return points.map(p=>{const a=Array.isArray(p)?p:[p.x,p.y,p.z];if(a.length!==3||a.some(x=>typeof x!=='number'||!Number.isFinite(x)))throw Error('每个航点须为有限数值 X/Y/Z');if(a[0]<-500||a[0]>5000||a[1]<-500||a[1]>5000||a[2]<0||a[2]>500)throw Error('航点超出接口坐标范围');return [...a];});}
function allowed(v,online,replay){if(!v||!online||replay)return {};const hold=v.mode==='LOITER';return {arm:!v.armed&&hold,disarm:v.armed&&hold,auto:v.armed&&hold&&v.mission.length>=2,loiter:v.armed&&v.mode!=='LOITER'&&v.mode!=='LAND',guided:v.armed&&hold,rtl:v.armed&&!['RTL','LAND'].includes(v.mode),land:v.armed&&['LOITER','AUTO','RTL','GUIDED'].includes(v.mode),upload:hold||v.mode==='GUIDED'};}
function routeLength(p,home){return p.reduce((s,x,i)=>s+Math.hypot(...x.map((v,k)=>v-(i?p[i-1]:home)[k])),0);}
const api={geo,enu,validate,allowed,routeLength};if(typeof module!=='undefined')module.exports=api;root.GCSCore=api;
})(typeof window==='undefined'?globalThis:window);
