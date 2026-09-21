/* Shine browser proof: actual product DOM with isolated deterministic transport. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const passed=[];
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const id='ef503ece-22a1-40a8-a507-40e39066af43';
  let turns=[],offline=false,uploadFails=true;
  const model='gemma4-31b-4bit';
  await page.route('**/api/forge/request',async route=>{
   const b=route.request().postDataJSON();
   if(offline)return route.fulfill({status:503,json:{detail:'Studio is unavailable. Try reconnecting.'}});
   if(b.action==='send')turns.push({id:b.message_id,message:b.message,answer:'I can help with that. Here is the next step.',status:'succeeded',model});
   return route.fulfill({json:b.action==='list'?{model,threads:turns.length?[{id,title:'Review this idea'}]:[]}:{model,turns}});
  });
  await page.route('**/api/forge/upload/**',r=>r.fulfill(uploadFails?{status:503,json:{detail:'Upload interrupted'}}:{json:{ready:true}}));
  await page.goto(process.env.FORGE_URL || 'http://127.0.0.1:8776/forge');
  await page.waitForFunction(()=>!document.getElementById('forge-new').disabled);
  assert.equal(await page.locator('h1').count(),1);
  assert.equal(await page.locator('#forge-send').isDisabled(),true);
  await page.locator('#forge-message').fill('Review this idea');
  await page.locator('#forge-message').press('Enter');
  await page.locator('#forge-transcript .assistant').waitFor();
  assert.match(await page.locator('#forge-transcript').innerText(),/next step/);
  passed.push('send and rendered response');
  await page.locator('#forge-activity summary').click();
  assert.equal(await page.locator('#forge-activity-detail').isVisible(),true);
  await page.locator('#forge-activity summary').click();
  assert.equal(await page.locator('#forge-activity-detail').isVisible(),false);
  passed.push('progress disclosure');
  await page.locator('#forge-files').setInputFiles({name:'notes.txt',mimeType:'text/plain',buffer:Buffer.from('test attachment')});
  await page.getByRole('button',{name:'Retry upload notes.txt'}).waitFor();
  assert.equal(await page.locator('#forge-send').isDisabled(),true);
  uploadFails=false;
  await page.getByRole('button',{name:'Retry upload notes.txt'}).click();
  await page.waitForFunction(()=>document.getElementById('forge-attachments').textContent.includes('Ready'));
  await page.getByRole('button',{name:'Remove notes.txt'}).click();
  assert.equal(await page.locator('.forge-file').count(),0);
  passed.push('attachment failure retry removal');
  offline=true;await page.reload();
  await page.locator('#forge-reconnect').waitFor();
  assert.match(await page.locator('#forge-status').innerText(),/unavailable/);
  offline=false;await page.locator('#forge-reconnect').click();
  await page.waitForFunction(()=>document.getElementById('forge-reconnect').hidden);
  passed.push('connection recovery');
  await page.locator('#forge-new').click();
  assert.equal(await page.locator('#forge-message').evaluate(e=>e===document.activeElement),true);
  await page.locator('#forge-threads').selectOption(id);
  await page.locator('#forge-transcript .assistant').waitFor();
  passed.push('new chat and saved conversation');
  await page.locator('#forge-message').fill('A draft');
  await page.locator('#forge-message').press('Shift+Enter');
  assert.equal(await page.locator('#forge-message').inputValue(),'A draft\n');
  passed.push('multiline keyboard input');
  for(const theme of ['dark','light']){
   if(await page.locator('html').getAttribute('data-theme') !== theme) await page.locator('#theme-toggle').click();
   await page.waitForTimeout(250);
   for(const width of [390,768,1440,1920]){
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    const composer=await page.locator('#forge-composer').boundingBox();
    assert(composer.y>=0 && composer.y+composer.height<=900);
    const head=await page.locator('.forge-head').boundingBox();
    const transcript=await page.locator('#forge-transcript').boundingBox();
    assert(head.y+head.height<=transcript.y+1);
    await page.screenshot({path:`/tmp/forge-ui-${theme}-${width}.png`});
   }
  }
  passed.push('four viewport widths in light and dark; composer visible; header does not overlap');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('#forge-phase').evaluate(e=>getComputedStyle(e,'::after').animationName),'none');
  passed.push('reduced motion');
  console.log(JSON.stringify({passed},null,2));
  fs.writeFileSync('/tmp/forge-ui-browser-proof.json',JSON.stringify({passed},null,2));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
