const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
 const browser = await chromium.launch({headless:true,channel:'chrome'});
 const page = await browser.newPage({viewport:{width:1440,height:1050}});
 const base = process.env.LEARNING_BASE || 'http://127.0.0.1:8876/network';
 const out = path.resolve(__dirname,'../../outputs/learning-acceptance');fs.mkdirSync(out,{recursive:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 try {
  await page.goto(base+'/learning?lesson=lecture-01');await page.waitForFunction(()=>document.querySelectorAll('.lesson').length===11);
  assert.equal(await page.locator('#lesson-title').textContent(),'具身智能导论');
  await page.selectOption('#resource','zh');await page.fill('#page','14');await page.click('#jump');
  assert.match(await page.locator('#pdf').getAttribute('data-src'),/\/network\/api\/learning\/files\/lecture-01\/zh\?course=hku-data8010#page=14/);
  await page.selectOption('#study-status','studying');await page.fill('#notes','验收笔记：感知—行动闭环');await page.click('#save');
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent.startsWith('已保存'));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#notes').value==='验收笔记：感知—行动闭环');
  assert.equal(await page.locator('#page').inputValue(),'14');assert.equal(await page.locator('#resource').inputValue(),'zh');
  await page.waitForFunction(()=>document.querySelector('#pdf-canvas canvas')); await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
  await page.fill('#search','强化');assert.equal(await page.locator('.lesson').count(),1);await page.locator('.lesson').click();
  assert.equal(await page.locator('#notes').inputValue(),'');
  await page.fill('#search','');await page.click('[data-filter="tutorial"]');assert.equal(await page.locator('.lesson').count(),3);
  await page.locator('.lesson').first().click();assert.match(await page.locator('#lesson-title').textContent(),/Isaac/);
  await page.fill('#notes','暂不保存');let dialog=false;page.once('dialog',async d=>{dialog=true;await d.dismiss();});await page.locator('.lesson').nth(1).click();assert(dialog);assert.equal(await page.locator('#notes').inputValue(),'暂不保存');
  await page.fill('#notes','');await page.click('#save');await page.waitForFunction(()=>document.querySelector('#save-status').textContent.startsWith('已保存'));
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>{const c=document.querySelector('#pdf-canvas canvas');return c && c.getBoundingClientRect().width<390;});await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  const exported=await page.request.get(base+'/api/learning/export');assert.equal(exported.status(),200);assert.equal((await exported.json()).records['lecture-01'].page,14);
  await page.goto(base+'/learning/');await page.waitForFunction(()=>document.querySelectorAll('.lesson').length===11);
  const pdf=await page.request.get(base+'/api/learning/files/lecture-08/original',{headers:{Range:'bytes=0-31'}});assert([200,206].includes(pdf.status()));
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify({checks:['11 份资料目录','中英文版本与页码跳转','笔记和状态保存恢复','章节隔离','搜索和教程筛选','未保存切换保护','移动端布局','导出记录','挂载前缀与尾斜杠','PDF 文件读取'],errors},null,2));
  console.log('Learning browser acceptance passed');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
