'use strict';
let deviceVideo=null,deviceVideoTimer=null,deviceVideoGeneration=0;
function stopDeviceVideo(){deviceVideoGeneration++;clearInterval(deviceVideoTimer);deviceVideoTimer=null;deviceVideo=null;$('device-video').hidden=true;$('device-video').removeAttribute('src');$('device-video-state').textContent='画面连接已关闭；真实视频未配置';}
$('device-video-open').onclick=async()=>{stopDeviceVideo();const id=state.selected,generation=deviceVideoGeneration;try{const session=await api('/api/devices/'+encodeURIComponent(id)+'/video/sessions',{source:'simulation'});if(state.selected!==id||generation!==deviceVideoGeneration)return;deviceVideo=session;const update=()=>{if(state.selected!==id||Date.now()/1000>=session.expires_at){stopDeviceVideo();return;}$('device-video').src=session.playback_url+'?t='+Date.now();};$('device-video').hidden=false;$('device-video-state').textContent=id+' · 仿真帧接口连接中（非机载视频）';update();deviceVideoTimer=setInterval(update,1000);}catch(e){if(generation===deviceVideoGeneration)$('device-video-state').textContent=e.message;}};
$('device-video-close').onclick=stopDeviceVideo;
$('device-video').onload=()=>{if(deviceVideo&&deviceVideo.source!=='test-pattern')$('device-video-state').textContent=deviceVideo.vehicle_id+' · 合成帧已接收 · '+new Date().toLocaleTimeString()+' · 非真实视频';};
$('device-video').onerror=()=>{stopDeviceVideo();$('device-video-state').textContent='帧接口不可用，请重新连接';};

$('test-video-open').onclick=async()=>{
  stopDeviceVideo();const generation=deviceVideoGeneration;
  try{const info=await api('/api/test-video/status');if(generation!==deviceVideoGeneration)return;
    if(!info.available)throw Error('服务器未安装 FFmpeg');
    deviceVideo={vehicle_id:'独立测试图（未绑定飞机）',source:'test-pattern'};
    $('device-video').hidden=false;$('device-video').src=info.url+'?t='+Date.now();
    $('device-video-state').textContent='FFmpeg · 10 fps MJPEG 测试流 · 非机载画面，不作为巡检证据';
  }catch(e){if(generation===deviceVideoGeneration)$('device-video-state').textContent=e.message;}
};
