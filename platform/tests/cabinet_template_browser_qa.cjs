const {chromium}=require('playwright');
(async()=>{
 const base=process.env.CABINET_BASE_URL||'http://127.0.0.1:8790',password=process.env.CABINET_QA_PASSWORD;
 if(!password)throw Error('Isolated test password required');
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_BIN?{executablePath:process.env.CHROME_BIN}:{})});const errors=[];
 async function ready(p){await p.waitForFunction(()=>!document.querySelector('#login-form button').disabled);}
 async function click(p,selector){await p.locator(selector).click();await ready(p);}
 async function login(name){const c=await browser.newContext({viewport:{width:1280,height:1000}});const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));await p.goto(base+'/data-cabinet');await ready(p);await p.fill('#username',name);await p.fill('#password',password);await click(p,'#login-form button');return p;}
 const owner=await login('owner'),admin=await login('admin'),alice=await login('alice');
 async function register(example){await click(owner,example);const response=owner.waitForResponse(r=>r.url().endsWith('/api/cabinet/templates')&&r.request().method()==='POST');await click(owner,'#template-form button');const r=await response;if(r.status()!==200)throw Error(await r.text());return r.json();}
 async function publish(v,name){
  const row=p=>p.locator('#template-list tr').filter({hasText:`${name} · v${v.version}`});
  await row(owner).getByRole('button',{name:'提交审核',exact:true}).click();await ready(owner);
  await click(admin,'#refresh');await row(admin).getByRole('button',{name:'审核通过',exact:true}).click();await ready(admin);
  await row(admin).getByRole('button',{name:'发布',exact:true}).click();await ready(admin);await click(owner,'#refresh');
 }
 const numeric=await register('#numeric-example');await publish(numeric,'低空特征样本');
 await owner.locator('#registered-ingest-form').locator('..').locator('summary').click();await owner.selectOption('#published-template',`${numeric.template_id}/1`);await click(owner,'#registered-ingest-form button[type=submit]');
 if(!(await owner.locator('#assets').textContent()).includes('template_binding'))throw Error('missing binding');
 await owner.fill('#recipient','alice');await owner.selectOption('#purpose','dataset');await click(owner,'#grant-form button');
 await click(alice,'#refresh');await click(alice,'#run');if(!(await alice.locator('#result-view').textContent()).includes('splits'))throw Error('registered model result missing');
 const record=await register('#record-example');await publish(record,'设备观测记录');await owner.selectOption('#published-template',`${record.template_id}/1`);await click(owner,'#registered-ingest-form button[type=submit]');
 if(!(await owner.locator('#assets').textContent()).includes('设备观测记录'))throw Error('second data type failed');
 await admin.locator('#template-list tr').filter({hasText:'低空特征样本 · v1'}).getByRole('button',{name:'停用',exact:true}).click();await ready(admin);
 await click(alice,'#result');if(!(await alice.locator('#status').textContent()).includes('模板'))throw Error('retired result accessible');
 await owner.screenshot({path:'/tmp/cabinet-template-owner.png',fullPage:true});await admin.screenshot({path:'/tmp/cabinet-template-admin.png',fullPage:true});
 await owner.setViewportSize({width:390,height:844});await owner.screenshot({path:'/tmp/cabinet-template-mobile.png',fullPage:true});
 if(await owner.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('mobile overflow');if(errors.length)throw Error(errors.join('\n'));
 console.log('PASS registration, separate review/publish, two data types, model result, retirement, mobile');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
