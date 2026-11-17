/* Shared local-coordinate scene viewer. All layers use the supplied computation snapshot. */
'use strict';
class PlanningMap {
  constructor(canvas, options={}) {
    this.canvas=canvas;this.options=options;this.mode='2d';this.yaw=-.65;this.tilt=.7;this.zoom=1;this.field=3;this.height=null;this.drag=null;this.moved=false;this.hits=[];
    canvas.onpointerdown=e=>{this.drag=[e.clientX,e.clientY];this.moved=false;canvas.setPointerCapture(e.pointerId);};
    canvas.onpointermove=e=>{if(this.drag&&this.mode==='3d'){this.yaw+=(e.clientX-this.drag[0])*.008;this.tilt=Math.max(.15,Math.min(1.45,this.tilt+(e.clientY-this.drag[1])*.008));this.drag=[e.clientX,e.clientY];this.moved=true;this.draw();}};
    canvas.onpointerup=e=>{this.drag=null;if(!this.moved)this.pick(e);};canvas.onpointercancel=()=>this.drag=null;
    canvas.onwheel=e=>{e.preventDefault();this.zoom=Math.max(.5,Math.min(8,this.zoom*Math.exp(-e.deltaY*.001)));this.draw();};
    this.resize=new ResizeObserver(()=>this.draw());this.resize.observe(canvas);this.draw();
  }
  destroy(){this.resize.disconnect();}
  update(options){Object.assign(this.options,options);this.draw();}
  color(p){const v=p[this.field];if(v==null)return '#91a0b0';if(this.field===5)return v<=this.options.precision?'#28c4ba':'#f37864';if(this.field===4)return `hsl(${Math.max(0,Math.min(1,(v+20)/50))*150} 70% 57%)`;return `hsl(${Math.max(0,Math.min(1,v))*150} 70% 57%)`;}
  draw(){
    const c=this.canvas;if(!c.isConnected)return;const ctx=c.getContext('2d'),rect=c.getBoundingClientRect(),w=Math.max(250,rect.width),h=420,dpr=window.devicePixelRatio||1;c.width=w*dpr;c.height=h*dpr;ctx.scale(dpr,dpr);ctx.fillStyle='#102d40';ctx.fillRect(0,0,w,h);ctx.font='12px system-ui';this.hits=[];
    const o=this.options,b=o.bounds||[0,2000,0,2000,0,300],cx=(b[0]+b[1])/2,cy=(b[2]+b[3])/2;
    const scale=Math.min((w-85)/(b[1]-b[0]),(h-75)/(b[3]-b[2]))*this.zoom;
    const project=(x,y,z=0)=>{let dx=x-cx,dy=y-cy;if(this.mode==='3d'){const a=dx*Math.cos(this.yaw)-dy*Math.sin(this.yaw),bb=dx*Math.sin(this.yaw)+dy*Math.cos(this.yaw);return [w/2+a*scale,h/2+35+(bb*Math.sin(this.tilt)-(z-(b[4]||0))*Math.cos(this.tilt))*scale];}return [w/2+dx*scale,h/2-dy*scale];};
    this.inverse=(px,py)=>[cx+(px-w/2)/scale,cy-(py-h/2)/scale];
    const line=(points,color,width=1,close=false,fill=null)=>{ctx.beginPath();points.forEach((p,i)=>{const q=project(...p);if(i)ctx.lineTo(...q);else ctx.moveTo(...q);});if(close)ctx.closePath();if(fill){ctx.fillStyle=fill;ctx.fill();}ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();};
    ctx.save();ctx.beginPath();ctx.rect(20,25,w-40,h-50);ctx.clip();
    const step=(b[1]-b[0])>1000?250:50;
    for(let x=Math.ceil(b[0]/step)*step;x<=b[1];x+=step)line([[x,b[2],0],[x,b[3],0]],'#25475b');
    for(let y=Math.ceil(b[2]/step)*step;y<=b[3];y+=step)line([[b[0],y,0],[b[1],y,0]],'#25475b');
    for(const a of o.scene?.buildings||[]){const base=[[a.x,a.y,0],[a.x+a.w,a.y,0],[a.x+a.w,a.y+a.d,0],[a.x,a.y+a.d,0]];if(this.mode==='3d'){const top=base.map(p=>[p[0],p[1],a.h]);for(let i=0;i<4;i++)line([base[i],base[(i+1)%4],top[(i+1)%4],top[i]],'#496579',1,true,'#29475b');line(top,'#668297',1,true,'#3d5a70');}else line(base,'#496579',1,true,'#304c60');}
    if(o.focus){const z=o.focus;line([[z.x0,z.y0,0],[z.x1,z.y0,0],[z.x1,z.y1,0],[z.x0,z.y1,0]],'#d9b776',2,true);}
    if(o.taskBounds){const t=o.taskBounds;line([[t[0],t[2],0],[t[1],t[2],0],[t[1],t[3],0],[t[0],t[3],0]],'#7dcced',2,true);}
    const cloud=o.cloud||[],size=Math.max(3,Math.min(16,(o.cell||10)*scale*.85));
    for(const p of cloud){if(this.mode==='2d'&&this.height!=null&&Math.abs(p[2]-this.height)>(o.cell||10)*.45)continue;const q=project(p[0],p[1],p[2]);if(q[0]<20||q[0]>w-20||q[1]<25||q[1]>h-25)continue;ctx.fillStyle=this.color(p);ctx.globalAlpha=this.mode==='3d'?.65:.85;ctx.fillRect(q[0]-size/2,q[1]-size/2,this.mode==='3d'?3:size,this.mode==='3d'?3:size);this.hits.push({q,p});}ctx.globalAlpha=1;
    if(o.route?.length){for(let i=1;i<o.route.length;i++){const a=o.route[i-1],bb=o.route[i];line([[a.x,a.y,a.z],[bb.x,bb.y,bb.z]],bb.sensor_ids?.length?'#45e0cb':'#ff947c',3);}const pos=o.route[o.routeIndex||0];const q=project(pos.x,pos.y,pos.z);ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(...q,7,0,Math.PI*2);ctx.fill();}
    for(const track of o.tracks||[]){
      const points=track.points||[];
      for(let i=1;i<points.length;i++){const a=points[i-1],b=points[i];if(b.t-a.t>(o.trackGap||2)||a.track_id!==b.track_id)continue;line([[a.x,a.y,a.z],[b.x,b.y,b.z]],track.color,3);}
      const p=points[points.length-1];if(p && (o.replayTime==null||o.replayTime-p.t<=(o.trackGap||2))){const q=project(p.x,p.y,p.z);ctx.fillStyle=track.color;ctx.beginPath();ctx.arc(...q,6,0,Math.PI*2);ctx.fill();}
    }
    const selected=new Set((o.selected||[]).map(d=>d.id));
    for(const d of [...(o.scene?.devices||[]),...(o.showCandidates?o.scene?.candidates||[]:[])]){const q=project(d.x,d.y,d.z);const candidate=selected.has(d.id)||(o.showCandidates&&(o.scene?.candidates||[]).some(x=>x.id===d.id));ctx.fillStyle=d.status==='offline'?'#8b99a7':candidate?'#ffbf69':'#6cddf3';ctx.beginPath();ctx.arc(...q,candidate?7:5,0,Math.PI*2);ctx.fill();ctx.fillStyle='#e2f0f6';ctx.fillText(d.id.length>16?d.id.slice(0,13)+'…':d.id,q[0]+9,q[1]-7);this.hits.push({q,d});}
    if(o.marker){const q=project(...o.marker);ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.beginPath();ctx.arc(...q,10,0,Math.PI*2);ctx.stroke();}
    ctx.restore();ctx.fillStyle='#bed5e3';ctx.fillText(this.mode==='3d'?'三维 · 拖动旋转 / 滚轮缩放':'二维 · 北 ↑ / 东 → / 滚轮缩放',20,18);ctx.fillText(`X ${b[0]}–${b[1]} m   Y ${b[2]}–${b[3]} m${this.mode==='2d'&&this.height!=null?'   Z '+this.height+' m':''}`,20,h-10);
  }
  pick(event){const r=this.canvas.getBoundingClientRect(),q=[event.clientX-r.left,event.clientY-r.top];const hit=this.hits.slice().reverse().find(h=>Math.hypot(h.q[0]-q[0],h.q[1]-q[1])<10);if(hit){this.options.onInspect?.(hit);if(hit.d)this.options.onDevice?.(hit.d);}else if(this.mode==='2d'){const p=this.inverse(...q).map(x=>Math.round(Math.max(0,Math.min(2000,x))));this.options.onPoint?.(p);}}
}
window.PlanningMap=PlanningMap;
