function horizon(v,p,a,speed,canvasId="horizon"){
  const c=$(canvasId),dpr=window.devicePixelRatio||1,w=c.clientWidth||280,h=320;
  c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);
  const x=c.getContext('2d');x.setTransform(dpr,0,0,dpr,0,0);
  const head=34,foot=28,tapeW=50,gap=6;
  const left=tapeW+gap,right=w-tapeW-gap,top=head+46,bottom=h-foot-4;
  const cx=(left+right)/2,cy=(top+bottom)/2,ppd=2.0,half=Math.max(24,(right-left)/2-8);
  const turn=d=>((d%360)+360)%360;
  x.fillStyle='#071018';x.fillRect(0,0,w,h);
  x.save();x.beginPath();x.rect(left,head+50,right-left,bottom-head-50);x.clip();
  x.translate(cx,cy);x.rotate(-a.roll*Math.PI/180);x.translate(0,Math.max(-120,Math.min(120,a.pitch))*ppd);
  x.fillStyle='#1b6ea6';x.fillRect(-500,-600,1000,600);x.fillStyle='#6b5432';x.fillRect(-500,0,1000,600);
  x.strokeStyle='#f4f7ef';x.lineWidth=2;x.beginPath();x.moveTo(-half,0);x.lineTo(half,0);x.stroke();
  x.font='11px monospace';x.fillStyle='#f4f7ef';x.lineWidth=1.4;
  for(let deg=-60;deg<=60;deg+=10){if(!deg)continue;const y=-deg*ppd,major=deg%20===0,len=Math.min(major?22:12,half-16);
    x.beginPath();x.moveTo(-len,y);x.lineTo(len,y);x.stroke();
    if(major&&len>14){const label=String(Math.abs(deg));x.textAlign='right';x.fillText(label,-len-3,y+4);x.textAlign='left';x.fillText(label,len+3,y+4);}}
  x.restore();
  x.save();x.beginPath();x.rect(left,head,right-left,44);x.clip();x.translate(cx,head+4);x.rotate(-a.roll*Math.PI/180);
  x.strokeStyle='#f7f4ea';x.lineWidth=1.5;
  for(const deg of [-60,-45,-30,-20,-10,0,10,20,30,45,60]){const t=(deg+90)*Math.PI/180,r0=24,r1=deg%30===0?38:32;
    x.beginPath();x.moveTo(Math.cos(t)*r0,Math.sin(t)*r0);x.lineTo(Math.cos(t)*r1,Math.sin(t)*r1);x.stroke();}
  x.restore();
  x.fillStyle='#ffde8b';x.beginPath();x.moveTo(cx,head+46);x.lineTo(cx-6,head+36);x.lineTo(cx+6,head+36);x.closePath();x.fill();
  const wing=Math.min(48,half-18);
  x.strokeStyle='#ffde8b';x.lineWidth=3;x.lineJoin='round';x.beginPath();
  x.moveTo(cx-wing,cy);x.lineTo(cx-14,cy);x.lineTo(cx,cy+7);x.lineTo(cx+14,cy);x.lineTo(cx+wing,cy);x.moveTo(cx,cy-7);x.lineTo(cx,cy+11);x.stroke();
  const tapeScale=2.4;
  const tapes=[[0,Number(speed)||0,n=>n.toFixed(0),'地速',1],[w-tapeW,p[2],n=>String(Math.round(n)),'高度',0]];
  for(const [x0,value,fmt,title,digits] of tapes){
    const boxTop=cy-13;
    x.save();x.beginPath();x.rect(x0,head,tapeW,bottom-head);x.clip();x.fillStyle='#0c1c2c';x.fillRect(x0,head,tapeW,bottom-head);
    x.fillStyle='#9db4c8';x.font='10px sans-serif';x.textAlign='center';x.fillText(title,x0+tapeW/2,head+14);
    for(let n=Math.floor(value-60);n<=Math.ceil(value+60);n++){if(n<0||n%5)continue;const sy=cy-(n-value)*tapeScale;if(sy<head+24||sy>bottom-8||Math.abs(sy-cy)<20)continue;
      x.strokeStyle='#d7e6f4';x.beginPath();x.moveTo(x0+(x0?2:tapeW-16),sy);x.lineTo(x0+(x0?14:tapeW-2),sy);x.stroke();
      if(n%10)continue;x.fillStyle='#e8f2fb';x.font='10px monospace';x.textAlign=x0?'left':'right';x.fillText(fmt(n),x0+(x0?16:tapeW-18),sy+3);}
    x.restore();
    x.fillStyle='#10283a';x.strokeStyle='#ffde8b';x.fillRect(x0+3,boxTop,tapeW-6,26);x.strokeRect(x0+3.5,boxTop+0.5,tapeW-7,25);
    x.fillStyle='#fff';x.font='12px monospace';x.textAlign='center';x.fillText(value.toFixed(digits),x0+tapeW/2,cy+4);
  }
  x.fillStyle='#0b1826';x.fillRect(0,0,w,head);
  for(let d=Math.floor(a.yaw-50);d<=Math.ceil(a.yaw+50);d+=5){const sx=w/2-(a.yaw-d)*3.1;if(sx<8||sx>w-8)continue;x.strokeStyle='#d7e6f4';x.beginPath();x.moveTo(sx,d%10===0?16:22);x.lineTo(sx,head);x.stroke();
    if(d%10===0){const n=turn(d),label={0:'N',90:'E',180:'S',270:'W'}[n]||String(n).padStart(3,'0');x.fillStyle=label.length===1?'#ffde8b':'#e8f2fb';x.font='11px monospace';x.textAlign='center';x.fillText(label,sx,13);}}
  x.fillStyle='#ffde8b';x.beginPath();x.moveTo(w/2-6,head);x.lineTo(w/2+6,head);x.lineTo(w/2,head-8);x.fill();
  const home=Math.hypot(p[0]-v.home[0],p[1]-v.home[1]);
  x.fillStyle='#0b1826';x.fillRect(0,h-foot,w,foot);x.fillStyle='#e8f2fb';x.font='11px sans-serif';x.textAlign='left';
  x.fillText(`${(modes[v.mode]||v.mode).split(' ')[0]} · ${v.armed?'已解锁':'未解锁'}`,8,h-10);x.textAlign='right';x.fillText(`${Number.isFinite(v.battery)?v.battery.toFixed(0):"—"}% · ${home.toFixed(0)} m`,w-8,h-10);
}
