const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'}),page=await browser.newPage({viewport:{width:1440,height:1100}});
 const base=process.env.LEARNING_BASE||'http://127.0.0.1:8876/network',out=path.resolve(__dirname,'../../outputs/learning-library-acceptance');fs.mkdirSync(out,{recursive:true});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const ready=()=>page.waitForSelector('#pdf-canvas canvas',{timeout:60000});
 const save=async notes=>{await page.fill('#notes',notes);await page.click('#save');await page.waitForFunction(()=>document.querySelector('#save-status').textContent.startsWith('已保存'));};
 try{
  await page.goto(base+'/learning?course=hku-data8010&lesson=lecture-01');await ready();assert.equal(await page.locator('#course-select option').count(),3);await save('HKU library acceptance');
  await page.selectOption('#course-select','jhu-video-ai');await page.waitForFunction(()=>document.querySelectorAll('.lesson').length===12);await ready();await page.locator('.lesson').first().click();await ready();await save('JHU independent notes');
  await page.selectOption('#course-select','cmu-eai-safety');await page.waitForFunction(()=>document.querySelectorAll('.lesson').length===18);await ready();await page.locator('.lesson').first().click();await ready();await save('CMU independent notes');
  await page.reload();await ready();assert.equal(await page.locator('#notes').inputValue(),'CMU independent notes');
  await page.selectOption('#course-select','hku-data8010');await page.waitForFunction(()=>document.querySelector('#notes').value==='HKU library acceptance');await ready();
  await page.fill('#global-search','世界模型');await page.locator('#global-results button').filter({hasText:'JHU'}).count();
  const result=page.locator('#global-results button').filter({hasText:'规划与控制'});assert.equal(await result.count(),1);await result.click();await page.waitForFunction(()=>document.querySelector('#lesson-title').textContent.includes('世界模型'));await ready();assert.match(page.url(),/course=jhu-video-ai/);
  await page.fill('#global-search','');await page.screenshot({path:path.join(out,'library-desktop.png'),fullPage:true});
  await page.fill('#notes','Unsaved draft');page.once('dialog',d=>d.dismiss());await page.selectOption('#course-select','cmu-eai-safety');assert.equal(await page.locator('#course-select').inputValue(),'jhu-video-ai');assert.equal(await page.locator('#notes').inputValue(),'Unsaved draft');await save('');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>{const c=document.querySelector('#pdf-canvas canvas');return c&&c.getBoundingClientRect().width<390;});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(out,'library-mobile.png'),fullPage:true});
  assert.equal(await page.locator('.reference-item').count(),5);
  const exported=await page.request.get(base+'/api/learning/export?course=jhu-video-ai');assert.equal((await exported.json()).records['lecture-01'].notes,'JHU independent notes');
  // Render every newly downloaded file, without mutating any progress.
  await page.setViewportSize({width:1440,height:1100});
  let rendered=0;
  for(const id of ['jhu-video-ai','cmu-eai-safety']){
   await page.selectOption('#course-select',id);await page.waitForFunction(id=>document.querySelector('#course-select').value===id&&document.querySelector('#course-meta').textContent.includes(id==='jhu-video-ai'?'Homanga':'卡内基'),id);await ready();
   const count=await page.locator('.lesson').count();for(let i=0;i<count;i++){await page.locator('.lesson').nth(i).click();await ready();rendered++;}
  }
  assert.equal(rendered,30);assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify({rendered,errors,checks:['三课程选择','原笔记兼容与跨课程隔离','刷新恢复','跨课程检索跳转','未保存切换保护','课程记录导出','手机布局','外部课程状态','30份新增PDF实际渲染']},null,2));console.log('Multi-course acceptance passed; rendered',rendered);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
