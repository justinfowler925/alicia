/* Exercises the shipped page and event handlers with isolated transport fixtures.
 * No request reaches a live service or writes a user's session store. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root=process.env.CHAT_STATIC_DIR||path.resolve(__dirname,'../alicia/static');
(async()=>{
 const browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 let sends=0, releaseSend;
 const turns=Array.from({length:24},(_,i)=>({id:i+1,role:i%2?'alicia':'user',text:`Message ${i+1}. `+'A complete conversation paragraph. '.repeat(5),channel:'text',meta:{}}));
 await page.addInitScript(()=>{
  window.__streams=[];
  window.EventSource=class{constructor(url){this.url=url;window.__streams.push(this);setTimeout(()=>this.onopen?.(),0)}close(){} };
  window.__emit=event=>window.__streams.at(-1).onmessage({data:JSON.stringify(event)});
 });
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url()), p=url.pathname;
  if(p.startsWith('/static/'))return route.fulfill({status:200,body:fs.readFileSync(path.join(root,path.basename(p))),contentType:p.endsWith('.css')?'text/css':'text/javascript'});
  if(p==='/session')return route.fulfill({status:200,body:fs.readFileSync(path.join(root,'session.html')),contentType:'text/html'});
  let data={};
  if(p==='/api/session/aaaaaaaaaaaa')data={session_id:'aaaaaaaaaaaa',turns,fields:[],artifacts:[]};
  else if(p.endsWith('/say')){sends++;await new Promise(resolve=>releaseSend=resolve);data={};}
  else if(p==='/api/watch')data={sessions:[],decisions:[],slack:{connected:true}};
  else if(p==='/api/todos')data={todos:[]};
  else if(p==='/api/supervisor')data={sessions:[],queue:[],counts:{}};
  else if(p==='/api/session/open')throw Error('Test must never open a real session');
  return route.fulfill({status:200,body:JSON.stringify(data),contentType:'application/json'});
 });
 try{
  await page.goto('http://chat-fixture.test/session?session=aaaaaaaaaaaa');
  await page.waitForFunction(()=>document.querySelectorAll('#conversation .turn').length===24);
  async function visibleComposer(){return page.locator('#composer').evaluate(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.width>0})}
  assert(await visibleComposer(),'Composer stays inside the viewport');
  assert(await page.locator('.watch-panel').evaluate(e=>!!e.closest('.conversation-activity')),'Ancillary work is in Activity');
  await page.evaluate(()=>window.__emit({kind:'turn',turn:{id:25,role:'alicia',text:'Long answer. '.repeat(500),channel:'text'}}));
  assert(await page.locator('#conversation').evaluate(e=>e.scrollHeight-e.scrollTop-e.clientHeight<5),'Long answer follows tail');
  await page.locator('#conversation').evaluate(e=>e.scrollTop=0);
  await page.evaluate(()=>window.__emit({kind:'turn',turn:{id:26,role:'alicia',text:'New answer while reading history.',channel:'text'}}));
  assert.equal(await page.locator('#conversation').evaluate(e=>e.scrollTop),0,'History reading holds position');
  await page.locator('#to-latest').click();
  assert(await page.locator('#conversation').evaluate(e=>e.scrollHeight-e.scrollTop-e.clientHeight<5));
  await page.evaluate(()=>window.__emit({kind:'thinking',turn_id:26,question:'Work on this'}));
  assert.match(await page.locator('#chat-activity-label').innerText(),/working/);
  await page.evaluate(()=>window.__streams.at(-1).onerror());
  await page.locator('#chat-reconnect').click();
  await page.waitForFunction(()=>document.querySelector('#chat-activity').dataset.state==='working');
  assert.equal(sends,0,'Reconnect never resends work');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.chat-working-dot').evaluate(e=>getComputedStyle(e).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.evaluate(()=>window.__emit({kind:'superseded',turn_id:26,replacement_turn_id:28}));
  await page.evaluate(()=>window.__emit({kind:'thinking',turn_id:28,question:'New direction'}));
  assert.equal(await page.evaluate(()=>state.chatWorking.size),1);
  await page.evaluate(()=>window.__emit({kind:'answer',answers_turn:28,turn:{id:29,text:'Done'}}));
  await page.evaluate(()=>window.__emit({kind:'thinking',turn_id:26,question:'Work on this'}));
  await page.evaluate(()=>{state.chatWorking.set(26,Date.now()-61000);renderChatActivity()});
  assert.match(await page.locator('#chat-activity-label').innerText(),/Waiting/);
  assert.match(await page.locator('#chat-activity-detail').innerText(),/progress has not been confirmed/);
  await page.evaluate(()=>window.__streams.at(-1).onerror());
  assert.match(await page.locator('#chat-activity-label').innerText(),/Connection lost/);
  await page.evaluate(()=>window.__streams.at(-1).onopen());
  await page.evaluate(()=>window.__emit({kind:'answer',answers_turn:26,turn:{id:27,text:'Could not finish.',meta:{error:'worker_failed'}}}));
  assert.equal(await page.locator('#chat-activity').getAttribute('data-state'),'error');
  await page.locator('#say').fill('First message');await page.locator('#say').press('Enter');
  await page.waitForFunction(()=>document.querySelector('#send').disabled);
  await page.locator('#say').fill('Keep this draft');await page.locator('#say').press('Enter');
  assert.equal(sends,1,'Enter cannot abort or duplicate pending sends');
  assert.equal(await page.locator('#say').inputValue(),'Keep this draft');
  releaseSend();await page.waitForFunction(()=>!document.querySelector('#send').disabled);
  await page.locator('#say').press('Shift+Enter');assert.match(await page.locator('#say').inputValue(),/\n/);
  await page.evaluate(()=>window.__emit({kind:'turn',turn:{id:27,role:'alicia',text:'Answer 27',channel:'text'}}));
  await page.evaluate(()=>window.__emit({kind:'turn',turn:{id:27,role:'alicia',text:'Answer 27',channel:'text'}}));
  assert.equal(await page.locator('[data-turn-id="27"]').count(),1,'Duplicate event does not duplicate message');
  await page.evaluate(()=>window.__emit({kind:'turn',turn:{id:0,role:'user',text:'Earlier recovered message',channel:'text'}}));
  assert.equal(await page.locator('#conversation .turn').first().getAttribute('data-turn-id'),'0','Recovered messages maintain order');
  assert(await page.evaluate(()=>{state.voiceTransport='openai_live';state.productBrainSpeak=true;return voiceOwnsPlayback()}),'GPT Live never falls through to second voice');
  if(process.env.CHAT_SCREENSHOT_DIR){fs.mkdirSync(process.env.CHAT_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.CHAT_SCREENSHOT_DIR,'chat-wide.png')})}
  await page.setViewportSize({width:390,height:844});
  assert(await visibleComposer(),'Narrow composer stays inside viewport');
  assert(await page.locator('#conversation').evaluate(e=>e.clientHeight>200),'Narrow transcript remains readable');
  if(process.env.CHAT_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.CHAT_SCREENSHOT_DIR,'chat-narrow.png')});
  console.log('PASS: ordered/deduplicated messages; viewport composer; tail/history scrolling; working/waiting/disconnected/failure; Enter/Shift+Enter; one pending send; one live voice path; wide/narrow.');
 }finally{releaseSend?.();await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
