const {chromium}=require('playwright');
(async()=>{
 const base=process.env.CABINET_BASE_URL||'http://127.0.0.1:8790',password=process.env.CABINET_QA_PASSWORD;if(!password)throw Error('Isolated test password required');
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_BIN?{executablePath:process.env.CHROME_BIN}:{})});const errors=[],ranges=[];let firstChunks=0;
 async function ready(p){await p.waitForFunction(()=>!document.querySelector('#login-form button').disabled);}
 async function click(p,s){await p.locator(s).click();await ready(p);}
 async function login(name){const c=await browser.newContext({viewport:{width:1280,height:1000}});const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));await p.goto(base+'/data-cabinet');await ready(p);await p.fill('#username',name);await p.fill('#password',password);await click(p,'#login-form button');return p;}
 const owner=await login('owner'),admin=await login('admin'),a=await login('alice'),b=await login('bobby');
 await click(owner,'#media-example');await owner.locator('#template-definition').evaluate(e=>{const d=JSON.parse(e.value);d.schema.video.misc.max_bytes=2*1024**3;e.value=JSON.stringify(d);});await click(owner,'#template-form button');
 await owner.locator('#template-list').getByRole('button',{name:'提交审核',exact:true}).click();await ready(owner);
 await click(admin,'#refresh');await admin.locator('#template-list').getByRole('button',{name:'审核通过',exact:true}).click();await ready(admin);await admin.locator('#template-list').getByRole('button',{name:'发布',exact:true}).click();await ready(admin);
 owner.on('request',r=>{if(r.url().includes('/chunks/0'))firstChunks++;});a.on('response',r=>{if(r.url().includes('/streams/')){ranges.push(r.status());if(r.status()!==206)console.error('stream response',r.status());}});
 await click(owner,'#refresh');await owner.locator('#registered-ingest-form').locator('..').locator('summary').click();
 for(const [field,path] of [['photo',process.env.CABINET_QA_IMAGE||'/tmp/cabinet-media.png'],['video',process.env.CABINET_QA_VIDEO||'/tmp/cabinet-chunks.mp4']]){
  if(field==='video')await owner.route('**/chunks/1',async route=>{await route.abort('failed');await owner.unroute('**/chunks/1');});
  await owner.selectOption('#attachment-field',field);await owner.setInputFiles('#attachment-file',path);await click(owner,'#attachment-upload');
  if(field==='video'){await owner.screenshot({path:'/tmp/cabinet-chunks-resume.png',fullPage:true});await owner.locator('#upload-sessions').getByRole('button',{name:'选择续传'}).click();await ready(owner);await owner.setInputFiles('#attachment-file',path);await click(owner,'#attachment-upload');}
  if(!(await owner.locator('#staged-attachments').textContent()).includes(field))throw Error('upload failed '+field+': '+await owner.locator('#status').textContent());
 }
 await click(owner,'#registered-ingest-form button[type=submit]');if(!(await owner.locator('#assets').textContent()).includes('影像附件记录'))throw Error('media binding failed');
 for(const [name,purpose] of [['alice','preview'],['bobby','stats']]){await owner.fill('#recipient',name);await owner.selectOption('#purpose',purpose);await click(owner,'#grant-form button');}
 await click(a,'#refresh');await click(a,'#attachments-load');
 await a.locator('#attachment-list tr').filter({hasText:'image/png'}).getByRole('button').click();await ready(a);await a.waitForFunction(()=>document.querySelector('#media-preview img')?.naturalWidth>0);
 await a.screenshot({path:'/tmp/cabinet-chunks-image-preview.png',fullPage:true});
 await a.locator('#attachment-list tr').filter({hasText:'video/mp4'}).getByRole('button').click();await ready(a);await a.waitForFunction(()=>document.querySelector('#media-preview video')?.readyState>=2);await a.locator('#media-preview video').evaluate(v=>{v.muted=true;return v.play();});await a.waitForFunction(()=>document.querySelector('#media-preview video')?.currentTime>0.1);await a.locator('#media-preview video').evaluate(v=>{v.currentTime=8.7;});await a.waitForFunction(()=>document.querySelector('#media-preview video')?.currentTime>=8.7);await a.waitForTimeout(1000);if(!await a.locator('#media-preview video').count())throw Error('Video cleared: '+await a.locator('#media-status').textContent()+' ranges='+ranges);await a.locator('#media-preview video').evaluate(v=>v.pause());if(!ranges.length||ranges.some(x=>x!==206))throw Error('Range failed '+ranges);if(firstChunks!==2)throw Error('acknowledged chunks uploaded twice '+firstChunks);
 await a.screenshot({path:'/tmp/cabinet-chunks-video-preview.png',fullPage:true});
 await click(b,'#refresh');if(await b.locator('#media-panel').isVisible())throw Error('stats account sees media controls');await click(b,'#run');if(!(await b.locator('#result-view').textContent()).includes('present_fields'))throw Error('statistics failed');
 await owner.locator('#grants tr').filter({hasText:'preview'}).getByRole('button',{name:'撤销',exact:true}).click();await ready(owner);
 await a.waitForFunction(()=>document.querySelector('#media-preview').children.length===0,{},{timeout:15000});
 if(!(await a.locator('#media-status').textContent()).includes('清空'))throw Error('revocation recheck failed');
 await click(a,'#attachments-load');if(!(await a.locator('#status').textContent()).includes('撤销'))throw Error('server allowed revoked media');
 await a.setViewportSize({width:390,height:844});await a.screenshot({path:'/tmp/cabinet-chunks-mobile-revoked.png',fullPage:true});if(await a.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('mobile overflow');
 if(errors.length)throw Error(errors.join('\n'));console.log('PASS interrupted upload/resume, no duplicate chunk, encrypted S3, video Range/seek, stats isolation, revoke/recheck, mobile');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
