const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/private/tmp/gcs-browser-check/node_modules/playwright');
(async()=>{const browser=await chromium.launch({headless:true,timeout:30000}),page=await browser.newPage({viewport:{width:1440,height:1000}}),base='http://127.0.0.1:8879/network',out=path.resolve(__dirname,'../../outputs/three-scene-acceptance'),errors=[];
page.on('pageerror',e=>errors.push(e.message));
async function ready(){await page.waitForFunction(()=>document.querySelector('#status')?.textContent==='已完成'||document.querySelector('#status')?.className==='error');assert.equal(await page.locator('#status').textContent(),'已完成');}
async function post(url,data){const r=await page.request.post(base+'/api'+url,{data});assert(r.ok(),await r.text());return r.json();}
try{
 await page.goto('http://127.0.0.1:8879/');await page.locator('a[href="/network/demonstration"]').click();await ready();assert.equal(await page.locator('[data-scene]').count(),3);
 const task=await post('/workflow/tasks',{name:'补盲覆盖切换验收',scene:'urban',volume:{bounds:[0,2000,0,2000,50,250],cell:50},budget:50});
 const plan=await post('/workflow/tasks/'+task.id+'/plan',{selected_ids:['C-01']});
 await page.goto(base+'/demonstration/urban');await ready();await page.locator('#new-trial').evaluate(e=>e.open=true);await page.locator('#planning-task').selectOption(task.id);await page.locator('#plan').selectOption(plan.id);await page.locator('#create-trial').click();await ready();await page.locator('#scene-coverage').click();await ready();assert.equal(await page.locator('#coverage-variant option').count(),2);
 for(const v of ['before','after']){await page.locator('#coverage-variant').selectOption(v);await page.locator('#scene-map').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'urban-coverage-'+v+'.png')});}
 await page.locator('#open-resources').click();await ready();assert.equal(await page.locator('#task-select').inputValue(),task.id);
 await page.goto(base+'/demonstration/electromagnetic');await ready();await page.locator('#compare-a').selectOption(await page.locator('#compare-a option').filter({hasText:'合成基准'}).last().getAttribute('value'));await page.locator('#compare-b').selectOption(await page.locator('#compare-b option').filter({hasText:'合成改进'}).first().getAttribute('value'));await page.locator('#compare-conditions').click();await ready();assert.match(await page.locator('#comparison').textContent(),/描述性对比/);
 assert.deepEqual(errors,[]);const report=JSON.parse(fs.readFileSync(path.join(out,'acceptance.json')));report.checks.push('地面站直达示范、建筑区补盲前后图层切换、规划任务跨页关联、不同条件描述对比');report.screenshots.push('urban-coverage-before.png','urban-coverage-after.png');fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify(report,null,2));console.log('Supplementary integration passed');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
