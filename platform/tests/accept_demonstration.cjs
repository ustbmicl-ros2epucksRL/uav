/* Run only against an isolated loopback service. Flight adapter check uses only the verified in-process simulator on isolated port 8879. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/private/tmp/gcs-browser-check/node_modules/playwright');
const base=process.env.SENSE_ACCEPT_URL||'http://127.0.0.1:8879/network';
if(!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Only local isolated service supported');
const output=path.resolve(__dirname,'../../outputs/three-scene-acceptance');fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,timeout:30000,...(process.env.PLAYWRIGHT_BROWSERS_PATH?{}:{channel:'chrome'})}),page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true}),errors=[],checks=[],screenshots=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 const request=async(url,data)=>{const r=await page.request.post(base+'/api'+url,{data});assert(r.ok(),await r.text());return r.json();};
 async function ready(){await page.waitForFunction(()=>['已完成'].includes(document.querySelector('#status')?.textContent)||document.querySelector('#status')?.className==='error');assert.equal(await page.locator('#status').textContent(),'已完成');}
 async function click(id){await page.locator('#'+id).click();await ready();}
 async function shot(name,selector){if(selector)await page.locator(selector).scrollIntoViewIfNeeded();else await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(output,name+'.png')});screenshots.push(name+'.png');}
 async function overflow(){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'page overflow');}
 async function download(id,file){const promise=page.waitForEvent('download');await page.locator('#'+id).click();const d=await promise;await d.saveAs(path.join(output,file));}
 try{
  for(const scene of ['urban','corridor','electromagnetic']){
   const task=await request('/workflow/tasks',{name:'示范验收-'+scene,scene,volume:{bounds:[700,800,700,800,120,160],cell:20},budget:50});
   await page.goto(base+'/demonstration/'+scene);await ready();await page.locator('#new-trial').evaluate(e=>e.open=true);
   await page.locator('#planning-task').selectOption(task.id);await page.locator('#name').fill('浏览器示范-'+scene);await click('create-trial');assert(await page.locator('#scene-map').isVisible());await click('scene-coverage');assert.match(await page.locator('#scene-coverage-result').textContent(),/模型覆盖率/);
   await click('baseline');await download('download-template','template-'+scene+'.json');await click('save-run');
   assert.match(await page.locator('#metrics').textContent(),/86.9%/);
   await page.locator('#view-mode').selectOption('3d');await page.locator('#time').fill('30');await page.locator('#time').dispatchEvent('input');
   await shot(scene+'-replay','#replay-map');assert(await page.locator('#scene-insights').textContent());await shot(scene+'-insights','#scene-insights');await overflow();
   await click('improved');await click('save-run');assert.match(await page.locator('#metrics').textContent(),/98.4%/);await click('calibrate');assert.match(await page.locator('#calibrations').textContent(),/本次留出集改善/);await click('compare');
   assert.match(await page.locator('#comparison').textContent(),/共同匹配样本/);await download('export-report','report-'+scene+'.json');await download('export-csv','residuals-'+scene+'.csv');
   const report=JSON.parse(fs.readFileSync(path.join(output,'report-'+scene+'.json')));assert.equal(report.trial.scene,scene);assert.equal(report.run.provenance,'synthetic');
   checks.push(scene+'：创建→快照→导入→三维回放→评价→共同样本对比→双格式导出');
  }
  await shot('desktop-overview');await shot('desktop-comparison','#comparison');await shot('desktop-metrics','#metrics');await shot('desktop-calibration','#calibrations');
  await page.locator('#time').fill('0');await page.locator('#time').dispatchEvent('input');await page.locator('#play').click();await page.waitForFunction(()=>Number(document.querySelector('#time').value)>0);await page.locator('#play').click();assert.equal(await page.locator('#play').textContent(),'播放');checks.push('时间轴播放与暂停');checks.push('离线校正候选、按时间留出验证与报告关联');
  await page.reload();await ready();assert.match(await page.locator('#trial-detail').textContent(),/浏览器示范-electromagnetic/);await page.locator('[data-run]').first().click();await ready();checks.push('刷新保留任务与不可变试验记录');
  // Exercise actual JSON file ingestion and the zero-observation display.
  const data=JSON.parse(fs.readFileSync(path.join(output,'template-electromagnetic.json')));data.name='无观测文件导入';data.observations=[];
  await page.locator('#file').setInputFiles({name:'no-observations.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});await ready();await click('save-run');assert.match(await page.locator('#metrics').textContent(),/暂无感知观测/);checks.push('文件导入与缺少观测时的空值展示');
  await page.locator('[data-run]').nth(1).click();await ready();
  // Validate the flight-log adapter against the isolated in-process simulator.
  if(new URL(base).port==='8879'){
   const origin=new URL(base).origin,meta=await (await page.request.get(origin+'/api/station')).json();
   assert.equal(meta.mode,'simulation');assert.equal(meta.transport.active,'simulator');assert.equal(meta.transport.mavlink_commands,false);
   const simPost=async(url,data)=>{const r=await page.request.post(origin+url,{data});assert(r.ok(),await r.text());return r.json();};
   await simPost('/api/vehicles/UAV-02/command',{cmd:'stop'});
   await simPost('/api/vehicles/UAV-02/mission',{waypoints:[{x:900,y:400,z:100},{x:1000,y:500,z:100}]});
   await simPost('/api/vehicles/UAV-02/command',{cmd:'arm'});await simPost('/api/vehicles/UAV-02/command',{cmd:'start'});
   await simPost('/api/tick?steps=5&vehicle_id=UAV-02',{});await simPost('/api/vehicles/UAV-02/command',{cmd:'stop'});
   await click('baseline');await page.locator('#gcs-track').evaluate(e=>e.closest('details').open=true);
   await page.locator('#vehicle-id').fill('UAV-02');await page.locator('#frame-confirm').check();await click('gcs-track');
   const imported=JSON.parse(await page.locator('#import-json').inputValue());assert.equal(imported.truth.length,6);assert.equal(imported.truth[0].t,0);assert.equal(imported.truth[0].target_id,'UAV-02');assert.equal(imported.observations.length,0);assert.equal(imported.provenance,'synthetic');
   await click('save-run');assert.match(await page.locator('#metrics').textContent(),/暂无感知观测/);checks.push('地面站仿真飞行记录读取、坐标确认、时钟归一与感知数据隔离');
   await page.locator('[data-run]').nth(2).click();await ready();
  }
  // Mobile layout and three-dimensional viewer interaction.
  await page.setViewportSize({width:390,height:844});await overflow();await shot('mobile-overview');await shot('mobile-metrics','#metrics');await page.locator('#time').fill('30');await page.locator('#time').dispatchEvent('input');await shot('mobile-replay','#replay-map');checks.push('390px 移动布局与轨迹视图');
  await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/demonstration');await ready();assert.equal(await page.locator('[data-scene]').count(),3);await shot('scene-overview');await page.setViewportSize({width:390,height:844});await overflow();await shot('scene-overview-mobile');await page.locator('[data-scene=urban]').click();await ready();assert.match(await page.locator('h1').textContent(),/建筑区/);assert(!(await page.locator('#trial-list').textContent()).includes('electromagnetic'));checks.push('三场景独立入口、总览统计、场景任务隔离及覆盖计算');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'acceptance.json'),JSON.stringify({passed:true,checks,errors,screenshots,base,created_at:new Date().toISOString()},null,2));
  fs.writeFileSync(path.join(output,'验收报告.html'),'<!doctype html><meta charset="utf-8"><title>示范验证验收</title><style>body{font:16px system-ui;max-width:1050px;margin:40px auto;padding:20px;color:#203344}img{max-width:100%;border:1px solid #ddd}li{margin:12px 0}</style><h1>示范验证平台：软件与可视化验收</h1><p>通过；合成数据用于验证软件流程，不代表现场示范已验收。</p><ul>'+checks.map(c=>'<li>'+c+'</li>').join('')+'</ul>'+screenshots.map(s=>'<h2>'+s+'</h2><img src="'+s+'">').join(''));
  console.log(JSON.stringify({passed:true,checks,screenshots,output}));
 }catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});fs.writeFileSync(path.join(output,'acceptance.json'),JSON.stringify({passed:false,error:e.stack,errors,checks,screenshots},null,2));throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
