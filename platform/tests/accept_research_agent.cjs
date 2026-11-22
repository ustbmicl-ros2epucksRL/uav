const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async()=>{const browser=await chromium.launch({headless:true,timeout:30000,executablePath:process.env.CHROMIUM_EXECUTABLE}),page=await browser.newPage({viewport:{width:1440,height:1050}}),out=path.resolve(__dirname,'../../outputs/research-agent-acceptance'),errors=[];fs.mkdirSync(out,{recursive:true});page.on('pageerror',e=>errors.push(e.message));page.setDefaultNavigationTimeout(120000);
try{
 await page.goto((process.env.AGENT_BASE_URL || 'http://127.0.0.1:8879') + '/network/agents');await page.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('可开始研究'),{},{timeout:90000});
 await page.locator('.scope-controls > summary').click();await page.locator('#workflow').selectOption('research');
 await page.locator('#task').selectOption('3');await page.locator('#mode').selectOption('review');await page.locator('#question').fill('只运行实验 3-0，预算为 6。比较贪心与穷举覆盖格数，说明能否推广到真实网络。回答简短，每段一句。');
 await page.screenshot({path:path.join(out,'before.png'),fullPage:true});await page.locator('#run').click();
 await page.waitForFunction(()=>document.querySelector('#stage').textContent.includes('研究流程完成')||document.querySelector('#stage').textContent.includes('执行失败'),{},{timeout:750000});
 const stage=await page.locator('#stage').innerText();assert.match(stage,/研究流程完成/,stage);
 await page.screenshot({path:path.join(out,'result-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(out,'result-mobile.png'),fullPage:true});
 const event=page.waitForEvent('download');await page.locator('#export').click();const d=await event;await d.saveAs(path.join(out,'live-run.json'));const r=JSON.parse(fs.readFileSync(path.join(out,'live-run.json')));
 assert.equal(r.status,'completed');assert.equal(r.model,'deepseek-r1:32b');assert(r.report&&r.review);const actual=r.trace.find(t=>t.tool==='run_research_experiment');assert.equal(actual.arguments.key,'3-0');assert.equal(actual.arguments.value,6);assert.deepEqual(actual.result.values,[8,10]);assert.equal(r.evidence_status,'synthetic_only');assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify({model:r.model,run:r.id,checks:['真实本机模型选择实验','平台实际计算 8/12 与 10/12','模型解释与第二角色复核','完整证据导出','桌面与手机布局'],errors},null,2));
 fs.writeFileSync(path.join(out,'验收报告.html'),'<!doctype html><meta charset="utf-8"><title>DeepSeek 智能体验收</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto}img{max-width:100%}</style><h1>DeepSeek 本机智能体实测</h1><p>真实模型：'+r.model+'；完成选参、计算、分析和复核。结果为合成算例，非现场科研验证。</p><a href="live-run.json">完整研究记录</a><h2>桌面</h2><img src="result-desktop.png"><h2>手机</h2><img src="result-mobile.png">');console.log(JSON.stringify({run:r.id,status:r.status,model:r.model,values:actual.result.values,errors}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
