const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/private/tmp/gcs-browser-check/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,timeout:30000}),page=await browser.newPage({viewport:{width:1440,height:1000}}),base=process.env.RESEARCH_BASE||'http://127.0.0.1:8879/network',out=path.resolve(__dirname,'../../outputs/research-analysis-acceptance'),errors=[],checks=[],screenshots=[];
 fs.mkdirSync(out,{recursive:true});page.on('pageerror',e=>errors.push(e.message));
 async function ready(){await page.waitForFunction(()=>document.querySelector('#status')?.textContent==='已完成');}
 async function go(id){await page.goto(base+'/research/'+id);await ready();}
 async function snap(name){await page.screenshot({path:path.join(out,name),fullPage:true});screenshots.push(name);}
 async function slider(id,value){await page.locator('#'+id).evaluate((el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));},String(value));}
 try{
  if(!process.env.RESEARCH_BASE){await page.goto('http://127.0.0.1:8879/');await page.locator('a[href="/network/research"]').click();await ready();assert(page.url().endsWith('/research/1'));checks.push('地面站直达研究问题');}
  for(let id=1;id<=5;id++){
   await go(id);assert.equal(await page.locator('#research-nav a').count(),5);assert.equal(await page.locator('#visual svg').count(),1);
   for(let c=0;c<3;c++){await page.locator(`[data-challenge="${c}"]`).click();assert.equal(await page.locator('.validation-step').count(),4);assert.match(await page.locator('#challenge-detail').innerText(),/失败表现/);}
   await page.locator('[data-challenge="0"]').click();await page.locator('#hypothesis').fill('子任务'+id+'独立草稿');await page.locator('#save-notes').click();await page.reload();await ready();assert.equal(await page.locator('#hypothesis').inputValue(),'子任务'+id+'独立草稿');await snap('task-'+id+'-desktop.png');
   await page.setViewportSize({width:390,height:844});await snap('task-'+id+'-mobile.png');assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.setViewportSize({width:1440,height:1000});checks.push('子任务'+id+'：三个难点、草稿保存、桌面和手机布局');
  }
  await go(1);await page.locator('[data-challenge="0"]').click();await slider('lab-parameter',0);assert.match(await page.locator('#model-note').innerText(),/几何通视/);await slider('lab-parameter',140);checks.push('独立难点实验参数交互');
  await go(5);const downloaded=page.waitForEvent('download');await page.locator('#export-design').click();const download=await downloaded,exported=path.join(out,'exported-design.json');await download.saveAs(exported);const content=JSON.parse(fs.readFileSync(exported));assert.equal(content.evidence_status,'design_only');assert.equal(content.experiment.evidence_status,'synthetic_only');checks.push('设计导出包含合成实验且不产生验收结论');
  const pdf=await page.request.get(base+'/api/research-analysis/proposal');assert.equal(pdf.status(),200);assert((await pdf.body()).subarray(0,4).toString()==='%PDF');checks.push('申请书 PDF 原文');
  if(!process.env.RESEARCH_BASE){for(const scene of ['urban','corridor','electromagnetic']){await page.goto(base+'/demonstration/'+scene);await ready();assert(await page.locator('#scene-guide a[href*="/research/"]').count()>0);}checks.push('三个示范场景回链');}assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify({time:new Date().toISOString(),base,integration_checked:!process.env.RESEARCH_BASE,checks,screenshots,errors},null,2));
  fs.writeFileSync(path.join(out,'验收报告.html'),'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>研究问题页面验收</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto}img{max-width:100%}li{margin:10px}</style><h1>五个研究问题页面验收</h1><p>页面功能验收通过；机制图为示意，研究实验尚未执行。</p><ul>'+checks.map(c=>'<li>通过：'+c+'</li>').join('')+'</ul>'+screenshots.map(s=>'<h2>'+s+'</h2><a href="'+s+'"><img src="'+s+'"></a>').join('')+'</html>');console.log(JSON.stringify({checks,screenshots,errors},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
