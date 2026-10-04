const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=process.env.CHAT_STATIC_DIR||path.resolve(__dirname,'../alicia/static');
(async()=>{const browser=await chromium.launch({channel:'chrome'});try{
 const page=await browser.newPage({viewport:{width:1440,height:900}});let uploaded=[],sent=[],fail=false;const turns=[];
 await page.addInitScript(()=>{window.EventSource=class {constructor(){setTimeout(()=>this.onopen?.(),0)}close(){}}});
 await page.route('**/*',async route=>{const req=route.request(),url=new URL(req.url()),p=url.pathname;let data={};
  if(p.startsWith('/static/'))return route.fulfill({body:fs.readFileSync(path.join(root,path.basename(p))),contentType:p.endsWith('.css')?'text/css':'text/javascript'});
  if(p==='/session')return route.fulfill({body:fs.readFileSync(path.join(root,'session.html')),contentType:'text/html'});
  if(p.endsWith('/attachments')){uploaded.push({name:url.searchParams.get('name'),text:req.postData()});if(url.searchParams.get('name')==='bad.png')return route.fulfill({status:422,json:{detail:'Images are not supported'}});data={id:'file'+uploaded.length,name:url.searchParams.get('name'),size:req.postDataBuffer().length};}
  else if(p.endsWith('/say')){sent.push(req.postDataJSON());if(fail)return route.fulfill({status:503,json:{detail:'Try again'}});turns.push({id:1,role:'user',text:sent.at(-1).message,meta:{attachments:[{name:'report.txt'}]}});}
  else if(p==='/api/session/aaaaaaaaaaaa')data={session_id:'aaaaaaaaaaaa',turns,fields:[],artifacts:[]};
  else if(p==='/api/watch')data={sessions:[],decisions:[],slack:{connected:true}};
  else if(p==='/api/todos')data={todos:[]};
  else if(p==='/api/supervisor')data={sessions:[],queue:[],counts:{}};
  return route.fulfill({json:data});
 });
 await page.goto('http://chat-fixture.test/session?session=aaaaaaaaaaaa');await page.waitForSelector('#chat-attach');
 const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'Attach files',exact:true}).click();
 await (await chooser).setFiles({name:'report.txt',mimeType:'text/plain',buffer:Buffer.from('attachment browser sentinel')});
 await page.waitForFunction(()=>document.querySelectorAll('.chat-attachment').length===1);
 assert.equal(uploaded[0].text,'attachment browser sentinel');
 await page.getByRole('button',{name:'Remove report.txt'}).focus();await page.keyboard.press('Enter');assert.equal(await page.locator('.chat-attachment').count(),0);
 await page.locator('#chat-files').setInputFiles({name:'bad.png',mimeType:'image/png',buffer:Buffer.from('bad')});await page.waitForFunction(()=>document.querySelector('#chat-upload-status').textContent.includes('not supported'));
 await page.locator('#chat-files').setInputFiles({name:'report.txt',mimeType:'text/plain',buffer:Buffer.from('real contents')});await page.waitForFunction(()=>document.querySelectorAll('.chat-attachment').length===1);
 fail=true;await page.locator('#send').click();await page.waitForFunction(()=>!state.chatSending);assert.equal(await page.locator('.chat-attachment').count(),1);assert(sent.at(-1).attachments.length===1);
 fail=false;await page.locator('#say').fill('');await page.locator('#say').press('Enter');await page.waitForFunction(()=>document.querySelectorAll('.chat-attachment').length===0);assert.equal(sent.at(-1).message,'Please review the attached documents.');
 await page.reload();await page.waitForSelector('.chat-attachment-name');assert.match(await page.locator('.chat-attachment-name').textContent(),/report.txt/);
 const out=process.env.CHAT_SCREENSHOT_DIR;if(out)fs.mkdirSync(out,{recursive:true});
 for(const [name,width,height] of [['wide',1440,900],['narrow',390,844]]){await page.setViewportSize({width,height});const box=await page.locator('#composer').boundingBox();assert(box.y+box.height<=height);const attach=await page.locator('#chat-attach').boundingBox();assert(attach.width>=40&&attach.height>=40);if(out)await page.screenshot({path:path.join(out,`attachments-${name}.png`)});}
 console.log('PASS attachment picker, actual bytes, keyboard removal, upload error, send failure preserves draft, attachment-only send, durable filename reload, wide/narrow composer');
 }finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
