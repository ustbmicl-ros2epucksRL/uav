/* Read-only acceptance against a running learning service. No study notes are written. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '/Users/duan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=process.env.LEARNING_BASE || 'http://127.0.0.1:8772/network';
const output=path.resolve('outputs/learning-bilingual-acceptance');fs.mkdirSync(output,{recursive:true});
(async()=>{const browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1600,height:1050}});const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
const report=[];
try {
 const library=await (await page.request.get(base+'/api/learning/courses')).json();
 for(const course of library.courses){
  const catalog=await (await page.request.get(base+'/api/learning/catalog?course='+course.id)).json();
  const before=JSON.stringify(catalog.records);
  for(const lesson of catalog.lessons){
   const chinese=lesson.resources.find(r=>r.id==='zh-study') || lesson.resources.find(r=>r.id==='zh');
   if(!chinese){if(process.env.ALLOW_PARTIAL==='1')continue;throw Error('Missing Chinese PDF: '+course.id+'/'+lesson.id);}
   assert(chinese.available);
   await page.goto(base+'/learning?course='+course.id+'&lesson='+lesson.id);
   await page.waitForSelector('#pdf-canvas canvas');
   await page.selectOption('#reading-mode','compare');
   await page.waitForFunction(()=>document.querySelector('#translation-title').textContent.length>0);
   const first=await page.locator('#translation-text').innerText();assert(first.length>0);
   const last=lesson.resources.find(r=>r.id==='original').pages;
   await page.fill('#page',String(last));await page.click('#jump');
   await page.waitForFunction(n=>document.querySelector('#translation-quality').textContent.startsWith('原第 '+n+' 页'),last);
   assert((await page.locator('#translation-summary').innerText()).length>0);
   assert((await page.locator('#translation-annotation').innerText()).length>0);
   await page.selectOption('#resource',chinese.id);
   const target=String(chinese.source_page_map[String(last)]);assert.equal(await page.locator('#page').inputValue(),target);
   await page.waitForFunction(n=>document.querySelector('#pdf-status').textContent.startsWith('第 '+n+' /'),target);
   await page.selectOption('#reading-mode','compare');assert.equal(await page.locator('#page').inputValue(),String(last));
   assert.equal(await page.locator('#resource').inputValue(),'original');
   const response=await page.request.get(base+'/api/learning/files/'+lesson.id+'/'+chinese.id+'?course='+course.id,{headers:{Range:'bytes=0-15'}});
   assert([200,206].includes(response.status()));assert((await response.body()).toString().startsWith('%PDF'));
   report.push({course:course.id,lesson:lesson.id,sourcePages:last,chinesePages:chinese.pages});
   console.log('PASS',course.id,lesson.id);
  }
  const after=await (await page.request.get(base+'/api/learning/catalog?course='+course.id)).json();assert.equal(JSON.stringify(after.records),before);
 }
 await page.goto(base+'/learning?course=hku-data8010&lesson=lecture-02');await page.waitForSelector('#pdf-canvas canvas');await page.fill('#page','15');await page.click('#jump');await page.selectOption('#reading-mode','compare');await page.waitForFunction(()=>document.querySelector('#translation-quality').textContent.startsWith('原第 15 页'));
 await page.locator('#workspace').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(500);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({checked:report.length,lessons:report,errors},null,2));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
