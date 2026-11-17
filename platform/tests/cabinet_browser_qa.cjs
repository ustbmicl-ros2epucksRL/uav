const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_BIN?{executablePath:process.env.CHROME_BIN}:{})});
 const base=process.env.CABINET_BASE_URL||'http://127.0.0.1:8790';
 const password=process.env.CABINET_QA_PASSWORD;if(!password)throw Error('CABINET_QA_PASSWORD required; use isolated test accounts');
 const errors=[];
 async function ready(page){await page.waitForFunction(()=>!document.querySelector('#login-form button').disabled);}
 async function user(name){const context=await browser.newContext({viewport:{width:1280,height:1000}});const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/data-cabinet');await ready(page);await page.fill('#username',name);await page.fill('#password',password);await page.click('#login-form button');await ready(page);if(!await page.locator('#identity').textContent().then(x=>x.startsWith(name)))throw Error('login failed '+name);return page;}
 async function click(page,selector){await page.click(selector);await ready(page);}
 const owner=await user('owner');await click(owner,'#ingest');
 for(const [name,purpose] of [['alice','dataset'],['bobby','stats']]){await owner.fill('#recipient',name);await owner.selectOption('#purpose',purpose);await click(owner,'#grant-form button');}
 const a=await user('alice');await click(a,'#run');if(!(await a.locator('#result-view').textContent()).includes('splits'))throw Error('A dataset missing');
 const b=await user('bobby');await click(b,'#run');if((await b.locator('#result-view').textContent()).includes('splits'))throw Error('B leaked data');
 if(await b.locator('#owner-panel').isVisible())throw Error('B owner controls visible');
 const aJob=await a.locator('#job-select').inputValue();
 const response=await b.evaluate(async({base,aJob})=>(await fetch(base+'/api/cabinet/results/'+aJob)).status,{base,aJob});
 if(response!==404)throw Error('cross-account result accepted');
 await click(owner,'#grants button');await click(a,'#result');if(!(await a.locator('#status').textContent()).includes('撤销'))throw Error('revocation missing');
 await click(b,'#result');if(!(await b.locator('#result-view').textContent()).includes('quality'))throw Error('B revoked unexpectedly');
 await b.reload();await ready(b);if(!(await b.locator('#identity').textContent()).startsWith('bobby'))throw Error('session recovery failed');
 const admin=await user('admin');if(!await admin.locator('#members').textContent().then(x=>x.includes('owner')&&!x.includes('alice')))throw Error('organisation roster leakage');
 await admin.screenshot({path:'/tmp/cabinet-account-admin.png',fullPage:true});
 await owner.screenshot({path:'/tmp/cabinet-account-owner.png',fullPage:true});
 await b.setViewportSize({width:390,height:844});await b.screenshot({path:'/tmp/cabinet-account-mobile.png',fullPage:true});
 if(await b.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('mobile overflow');
 await click(b,'#logout');if(await b.locator('#data-panel').isVisible())throw Error('logout retained data');
 if(errors.length)throw Error(errors.join('\n'));
 console.log('PASS owner/A/B/admin independent login, differential results, IDOR, revoke, reload, logout, mobile');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
