/* Isolated browser regression: real shipped assets, no live writes or microphone. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const staticRoot = path.join(__dirname, '../brutus/static');
const sessions = Array.from({ length: 15 }, (_, i) => ({
  id: `test:${i}`, surface: ['claude', 'cursor', 'codex'][i % 3],
  title: i === 0 ? 'Check very long session titles and progress summaries without clipping or hiding the voice controls' : `Session ${i + 1}`,
  live: true, state: 'running', age: '1m ago', status_source: 'test',
  assessment: { goal: 'Keep agent progress readable.', verified_progress: ['The focused layout checks passed.'], recommended_next_action: 'Check the narrow viewport.', evidence: ['Test fixture'], judgment_source: 'model' },
}));
(async () => {
 const browser = await chromium.launch();
 try {
  const page = await browser.newPage();
  const errors = [];
  const archived = new Set();
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
   const u = new URL(route.request().url());
   if (u.pathname === '/session' || u.pathname.startsWith('/static/')) {
    const name = u.pathname === '/session' ? 'session.html' : path.basename(u.pathname);
    return route.fulfill({ body: fs.readFileSync(path.join(staticRoot, name)), contentType: name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'text/html' });
   }
   if (u.pathname.endsWith('/events')) return route.fulfill({contentType:'text/event-stream', body:': fixture\n\n'});
   let data = {};
   if (u.pathname.startsWith('/api/agents/') && route.request().method() === 'PATCH') {
    const id = decodeURIComponent(u.pathname.slice('/api/agents/'.length));
    if (route.request().postDataJSON().archived) archived.add(id); else archived.delete(id);
    data = {id,archived:archived.has(id)};
   }
   if (u.pathname === '/api/agents') data = {agents:sessions.map(s=>({...s,hidden:archived.has(s.id)}))};
   if (u.pathname === '/api/session/open') data = { session_id:'fixture' };
   if (u.pathname === '/api/session/fixture') data = { turns:[{id:'u1',role:'user',text:'Do not show every word.'},{id:'a1',role:'assistant',text:'I’ll keep intent and progress visible. The full response stays available on demand.'}],fields:[],artifacts:[] };
   if (u.pathname === '/api/supervisor') data = {sessions:sessions.filter(s=>!archived.has(s.id)),counts:{total:15-archived.size,live:15-archived.size}};
   if (u.pathname === '/api/todos') data = {todos:[],stages:[]};
   if (u.pathname === '/api/nucleus') data = {projects:Array.from({length:30},(_,i)=>({id:`brutus-${i}`,name:`Brutus ${i}`,status:'needs_you',ticket_count:3,thread_count:6,recent_thread_count:6}))};
   return route.fulfill({json:data});
  });
  await page.goto('http://brutus.test/session');
  await page.waitForFunction(() => document.querySelectorAll('.agent-strip > li').length === 6);
  assert.equal(await page.locator('#conversation-details').getAttribute('open'), null);
  assert.match(await page.locator('#intent-readback').innerText(), /keep intent and progress visible/);
  assert.doesNotMatch(await page.locator('#intent-readback').innerText(), /full response/);
  await page.locator('.agent-strip summary').first().click();
  await page.locator('#supervisor-refresh').click();
  await page.waitForFunction(() => document.querySelector('.agent-strip details').open);
  await page.locator('#sessions-more').click();
  assert.equal(await page.locator('.agent-strip > li').count(),15);
  assert.equal(await page.locator('#sessions-more').isVisible(),false);
  await page.getByRole('button',{name:'Archive from Brutus'}).first().click();
  await page.waitForFunction(()=>document.querySelectorAll('.agent-strip > li').length===14);
  await page.locator('#archived-sessions > summary').click();
  await page.locator('#archive-list button').click();
  await page.waitForFunction(()=>document.querySelectorAll('.agent-strip > li').length===15);
  assert.equal(archived.size,0);
  await page.locator('.work-tray > summary').click();
  for (const tab of ['projects','work','queue','sites','running']) {
   await page.locator(`#tab-${tab}`).click();
   assert.equal(await page.locator(`#panel-${tab}`).isVisible(),true);
  }
  await page.locator('#tab-projects').click();
  for (const width of [390,768,1280,1920]) {
   await page.setViewportSize({width,height:900});
   const layout = await page.evaluate(() => {
    const voice=document.querySelector('.voice-stage').getBoundingClientRect();
    const agents=document.querySelector('.supervisor-rail').getBoundingClientRect();
    const tray=document.querySelector('.work-tray').getBoundingClientRect();
    return {overflow:document.documentElement.scrollWidth>innerWidth, ordered:voice.bottom<=agents.top && agents.bottom<=tray.top};
   });
   if(layout.overflow) console.log(await page.evaluate(()=>[...document.querySelectorAll("body *")].filter(el=>el.getBoundingClientRect().right>innerWidth).map(el=>[el.tagName,el.className,el.getBoundingClientRect().width]).slice(0,20)));
   assert.equal(layout.overflow,false,`horizontal overflow at ${width}`);
   assert.equal(layout.ordered,true,`overlapping sections at ${width}`);
   const scrollers = await page.locator('.work-tray').evaluate(tray=>[...tray.querySelectorAll('*')].filter(el=>{
    const style=getComputedStyle(el);
    return el.getClientRects().length && /auto|scroll/.test(style.overflowY) && el.scrollHeight>el.clientHeight+2;
   }).map(el=>el.className));
   assert.deepEqual(scrollers,[],`nested vertical scrolling at ${width}`);
  }
  await page.locator('#tab-queue').click();
  await page.locator('#ideas-list').evaluate(host=>{
   const col=document.createElement('section');col.className='qcol';
   const list=document.createElement('ul');list.className='qcol-list';
   for(let i=0;i<30;i++){const li=document.createElement('li');li.textContent=`Long queue item ${i}: preserve page scrolling without a separate column scrollbar.`;list.append(li);}
   col.append(list);host.append(col);
  });
  await page.setViewportSize({width:390,height:700});
  assert.equal(await page.locator('.qcol-list').last().evaluate(el=>el.scrollHeight>el.clientHeight+2),false);
  await page.locator('#tab-projects').click();
  await page.locator('#conversation-details > summary').click();
  assert.equal(await page.locator('#say').isVisible(),true);
  await page.locator('#mute').click();
  assert.equal(await page.locator('#mute').getAttribute('aria-pressed'),'true');
  await page.locator('#conversation-details > summary').click();
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('.agent-strip > li').length === 6);
  await page.locator('.work-tray > summary').click();
  await page.locator('#tab-projects').click();
  for (const width of [390,1280]) {
   await page.setViewportSize({width,height:900});
   await page.screenshot({path:`/tmp/brutus-overview-${width}.png`,fullPage:true});
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: archive/restore; 30 projects and long queue without nested vertical scrolling; 15 sessions, paging, readback, five workspace tabs and four viewport layouts.');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
