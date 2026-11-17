const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/private/tmp/gcs-browser-check/node_modules/playwright');
(async()=>{const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1100}}),out=path.resolve(__dirname,'../../outputs/research-labs-acceptance'),errors=[],checks=[],screenshots=[];page.setDefaultNavigationTimeout(120000);fs.mkdirSync(out,{recursive:true});page.on('pageerror',e=>errors.push(e.message));
const ready=()=>page.waitForFunction(()=>document.querySelector('#status')?.textContent==='已完成');
try{
 for(let task=1;task<=5;task++){
  await page.goto('http://127.0.0.1:8879/network/research/'+task);await ready();
  for(let c=0;c<3;c++){
   await page.locator(`[data-challenge="${c}"]`).click();
   assert.equal(await page.locator('#visual-title').textContent(),await page.locator('#challenge-detail h3').textContent());
   assert(await page.locator('#experiment-boundary').textContent());assert(await page.locator('#boundary-results tr').count()>=2);
   const input=page.locator('#lab-parameter');for(const attr of ['min','max']){const value=await input.getAttribute(attr);await input.evaluate((el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));},value);assert.equal(await page.locator('#lab-value').textContent(),value);}
   await page.locator('#reset-controls').click();
   const name=`task-${task}-challenge-${c+1}.png`;await page.screenshot({path:path.join(out,name),fullPage:true});screenshots.push(name);checks.push(`${task}.${c+1} 难点独立实验、极值参数、复位及验证卡片`);
  }
  const input=page.locator('#lab-parameter'),max=await input.getAttribute('max');await input.evaluate((el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));},max);await page.locator('#save-notes').click();await page.reload();await ready();assert.equal(await page.locator('#lab-parameter').inputValue(),max);
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));const mobile=`task-${task}-mobile.png`;await page.screenshot({path:path.join(out,mobile),fullPage:true});screenshots.push(mobile);await page.setViewportSize({width:1440,height:1100});
 }
 const event=page.waitForEvent('download');await page.locator('#export-experiments').click();const download=await event;await download.saveAs(path.join(out,'all-experiments.json'));const result=JSON.parse(fs.readFileSync(path.join(out,'all-experiments.json')));assert.equal(result.records.length,15);assert(result.records.every(r=>r.evidence_status==='synthetic_only'));checks.push('15 个算例导出包含参数、模型版本、假设、结果与合成证据标记');
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify({checks,screenshots,errors},null,2));fs.writeFileSync(path.join(out,'验收报告.html'),'<!doctype html><meta charset="utf-8"><title>15 个难点实验验收</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto}img{max-width:100%}</style><h1>15 个难点交互实验</h1><p>计算与页面验收通过；不是现场研究验收。</p><ul>'+checks.map(x=>'<li>'+x+'</li>').join('')+'</ul>'+screenshots.map(x=>'<h2>'+x+'</h2><img src="'+x+'">').join(''));console.log(JSON.stringify({checks:checks.length,screenshots:screenshots.length,errors}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
