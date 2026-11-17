const {chromium}=require('playwright');
(async()=>{
 const base=process.env.CABINET_BASE_URL||'http://127.0.0.1:8790';const password=process.env.CABINET_QA_PASSWORD;if(!password)throw Error('Test password required');
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_BIN?{executablePath:process.env.CHROME_BIN}:{})});
 async function ready(p){await p.waitForFunction(()=>!document.querySelector('#login-form button').disabled);}
 async function click(p,s){await p.click(s);await ready(p);}
 async function login(name){const c=await browser.newContext();const p=await c.newPage();await p.goto(base+'/data-cabinet');await ready(p);await p.fill('#username',name);await p.fill('#password',password);await click(p,'#login-form button');return p;}
 const admin=await login('admin');const name='container'+Date.now();await admin.fill('#member-name',name);await admin.fill('#member-password',password);await admin.selectOption('#member-role','owner');await click(admin,'#member-form button');
 const p=await login(name);await p.locator('summary').filter({hasText:'容器模板与 JSON 导入'}).click();
 const downloading=p.waitForEvent('download');await click(p,'#template-example');const download=await downloading;await p.setInputFiles('#container-file',await download.path());await click(p,'#container-import');
 if(!(await p.locator('#assets').textContent()).includes('urban-synthetic-features'))throw Error('container import failed');
 await p.screenshot({path:'/tmp/cabinet-container-import.png',fullPage:true});
 console.log('PASS template example download and container JSON upload');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
