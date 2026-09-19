/* Deterministic activity states on the real page; isolated browser, mocked transport. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
 const browser = await chromium.launch({headless:true});
 try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const id='ef503ece-22a1-40a8-a507-40e39066af43';
  let mode='running', stopped=false;
  const started=Date.now()/1000-30;
  await page.addInitScript(id=>localStorage.setItem('brutus.forge.thread',id),id);
  await page.route('**/api/forge/request',async route=>{
   const body=route.request().postDataJSON();
   if(mode==='offline') return route.fulfill({status:503,contentType:'application/json',body:'{"detail":"Test Studio connection unavailable"}'});
   if(body.action==='stop') {stopped=true; mode='cancelled';}
   const now=Date.now()/1000, active=['running','quiet','queued'].includes(mode);
   const state=mode==='quiet'?'running':mode;
   const data=body.action==='list'?{model:'Forge test',threads:[{id,title:'Activity test'}]}:{model:'Forge test',turns:[{id:'turn1',message:'Activity test',answer:active?'':'Done',status:state}],activity:{turn:1,active:active?1:0,phase:mode==='queued'?'Queued':active?'Thinking':mode==='succeeded'?'Complete':'Stopped',status:state,started_at:started,finished_at:active?null:started+40,last_output_at:mode==='quiet'?now-120:now,events:8,tools:2,checked_at:now}};
   await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto((process.env.FORGE_URL||'http://127.0.0.1:8771/')+'#forge');
  await page.locator('#forge-phase').getByText('Thinking',{exact:true}).waitFor();
  assert.match(await page.locator('#forge-turn-count').innerText(),/Turn 1 · 1 active/);
  assert.equal(await page.locator('#forge-activity').getAttribute('data-state'),'working');
  assert.match(await page.locator('#forge-activity-detail').innerText(),/8 updates · 2 tool calls/);
  const first=await page.locator('#forge-activity-detail').innerText();
  await page.waitForTimeout(1200);
  assert.notEqual(await page.locator('#forge-activity-detail').innerText(),first);
  await page.screenshot({path:'/tmp/forge-activity-wide.png'});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'/tmp/forge-activity-narrow.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('#forge-phase').evaluate(e=>getComputedStyle(e,'::after').animationName),'none');
  mode='quiet';
  await page.getByText('No recent activity',{exact:true}).waitFor({timeout:15000});
  assert.equal(await page.locator('#forge-activity').getAttribute('data-state'),'quiet');
  mode='offline';
  await page.getByText('Connection lost',{exact:true}).waitFor({timeout:15000});
  assert.match(await page.locator('#forge-turn-count').innerText(),/last known/);
  mode='running';
  await page.locator('#forge-phase').getByText('Thinking',{exact:true}).waitFor({timeout:15000});
  mode='queued';
  await page.locator('#forge-phase').getByText('Queued',{exact:true}).waitFor({timeout:15000});
  assert.equal(await page.locator('#forge-activity').getAttribute('data-state'),'waiting');
  await page.getByRole('button',{name:'Stop',exact:true}).click();
  await page.locator('#forge-phase').getByText('Stopped',{exact:true}).waitFor();
  assert.equal(stopped,true);
  assert.match(await page.locator('#forge-turn-count').innerText(),/0 active/);
  mode='succeeded';await page.reload();
  await page.locator('#forge-phase').getByText('Complete',{exact:true}).waitFor();
  const done=await page.locator('#forge-activity-detail').innerText();
  await page.waitForTimeout(1100);
  assert.equal(await page.locator('#forge-activity-detail').innerText(),done);
  console.log('PASS: active count, elapsed clock, thinking shimmer, quiet, connection loss, automatic recovery, queue, Stop, completion, reload, reduced motion and narrow layout');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
