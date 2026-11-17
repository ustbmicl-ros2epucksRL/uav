/* Real Chromium acceptance. Run against an isolated local SENSE_DATA_DIR only.
   PLAYWRIGHT_MODULE=/path/to/playwright SENSE_ACCEPT_URL=http://127.0.0.1:8878/network node tests/accept_portal.cjs
   Creates named acceptance data, never resets or deletes existing records. */
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SENSE_ACCEPT_URL||'http://127.0.0.1:8878/network';
if(!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Acceptance supports loopback services only');
const output=path.resolve(process.env.SENSE_ACCEPT_OUTPUT||path.join(__dirname,'../../outputs/planning-v1-acceptance'));
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1,acceptDownloads:true});
 const page=await context.newPage(),errors=[],checks=[],screenshots=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('favicon'))errors.push(m.text());});
 page.on('dialog',d=>d.accept());
 async function ready(){await page.waitForFunction(()=>document.querySelector('#status')?.textContent!=='处理中…'&&document.querySelector('#status')?.textContent!=='正在加载…');assert.equal(await page.locator('#status').textContent(),'已完成');}
 async function visit(name){await page.goto(base+'/'+name);await ready();}
 async function click(id){await page.locator('#'+id).click();await ready();}
 async function shot(name){await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});screenshots.push(name+'.png');}
 async function overflow(){const size=await page.evaluate(()=>({w:innerWidth,scroll:document.documentElement.scrollWidth}));assert(size.scroll<=size.w+1,JSON.stringify(size));}
 async function fill(id,value){await page.locator('#'+id).fill(String(value));}
 async function download(selector,name){const wait=page.waitForEvent('download');await page.locator(selector).click();const file=await wait;await file.saveAs(path.join(output,name));assert(fs.statSync(path.join(output,name)).size>10);}
 try{
  await visit('tasks');await fill('name','完整版本浏览器验收 '+Date.now());for(const [id,value] of Object.entries({b0:0,b1:2000,b2:0,b3:2000,b4:50,b5:250,cell:50,coverage_target:.9,focus_target:.95,precision_rate_target:.65,budget:200}))await fill(id,value);await page.locator('#task-form button:not([type="button"])').click();await ready();checks.push('任务表单创建与持久化');await shot('01-tasks-desktop');await overflow();
  await visit('resources');await page.locator('#resource-map-mode').selectOption('3d');await shot('02-resources-3d');await page.locator('#resource-map-mode').selectOption('2d');
  const siteId='VISUAL-SITE-'+Date.now(),deviceId='VISUAL-DEVICE-'+Date.now();
  await fill('sid',siteId);await fill('sname','验收候选站址');await fill('sx',1100);await fill('sy',800);await fill('smax_height',180);await fill('ssetup_cost',2);await page.locator('#site-form button:not([type="button"])').click();await ready();
  await fill('did',deviceId);await fill('dname','验收新增雷达');await page.locator('#site_id').selectOption(siteId);await page.locator('#model_id').selectOption('RADAR');await fill('dz',125);await page.locator('#device-form button:not([type="button"])').click();await ready();await click('save-resources');await page.reload();await ready();assert(await page.locator('body').textContent().then(t=>t.includes('验收候选站址')));checks.push('站址、型号模板、设备配置保存与重载');await overflow();
  await visit('diagnosis');await click('diagnose');await page.locator('#diagnosis-map').scrollIntoViewIfNeeded();await shot('03-diagnosis-2d');await page.locator('#diagnosis-map-mode').selectOption('3d');const box=await page.locator('#diagnosis-map').boundingBox();await page.mouse.move(box.x+200,box.y+150);await page.mouse.down();await page.mouse.move(box.x+280,box.y+190,{steps:5});await page.mouse.up();await shot('04-diagnosis-3d');await page.locator('#diagnosis-map-field').selectOption('5');await shot('05-diagnosis-precision');await download('#diagnosis-export','diagnosis.json');await page.reload();await ready();assert(await page.locator('#diagnosis-map').count());checks.push('二维切片、三维旋转、精度图层、诊断持久化与导出');
  await visit('planning');await click('generate');await click('plan-preview');await shot('06-planning-comparison');await page.locator('#plan-preview-area-config').selectOption('before');await page.locator('#plan-preview-area-config').selectOption('after');
  await page.locator('#plan-mode').selectOption('manual');for(const checkbox of await page.locator('input[name=candidate]:not(:disabled)').all())await checkbox.check();await click('generate');checks.push('自动与人工方案、前后覆盖对比');
  await page.locator('#offline-device').selectOption('RADAR-01');await click('route-run');await page.locator('#route-map-mode').selectOption('3d');await page.locator('#route-position').focus();for(let i=0;i<5;i++)await page.locator('#route-position').press('ArrowRight');await shot('07-route-failure');await download('#route-export','route.json');await page.reload();await ready();assert.equal(await page.locator('#offline-device').inputValue(),'RADAR-01');checks.push('固定部署故障推演、参数恢复、航路位置与导出');
  await visit('plans');await click('compare');await page.locator('[data-plan]').first().click();await ready();await click('detail-preview');await shot('08-plans-review');await download('#export-json','plan.json');await download('a[href$="deployment.csv"]','deployment.csv');await click('confirm');await shot('09-plan-confirmed');await click('apply');assert.match(await page.locator('#plan-detail').textContent(),/已应用到场景版本/);assert.equal(await page.locator('#apply').count(),0);await click('archive');checks.push('同条件方案比较、确认、应用、归档与双格式导出');
  await visit('resources');const rows=await page.locator('tr').filter({hasText:'验收新增雷达'}).allTextContents();assert(rows.some(r=>r.includes('已有')));checks.push('应用方案后资源同步');
  for(const name of ['tasks','resources','diagnosis','planning','plans']){await page.setViewportSize({width:390,height:844});await visit(name);await overflow();await shot('mobile-'+name);}
  checks.push('五页 390px 移动视口无页面级横向溢出');
  assert.deepEqual(errors,[],'浏览器控制台错误');
  fs.writeFileSync(path.join(output,'acceptance.json'),JSON.stringify({passed:true,base,checks,screenshots,errors,created_at:new Date().toISOString()},null,2));
  console.log(JSON.stringify({passed:true,checks:checks.length,screenshots:screenshots.length,output}));
 }catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});fs.writeFileSync(path.join(output,'acceptance.json'),JSON.stringify({passed:false,error:e.stack,checks,errors,screenshots},null,2));throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
