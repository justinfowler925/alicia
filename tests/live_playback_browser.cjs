// Full shipped page on an ephemeral local server. Every API, microphone and RTC adapter is inert.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../alicia/static');
const server = http.createServer((req,res) => {
  const file = path.join(root, req.url === '/' ? 'session.html' : req.url.replace('/static/','').split('?')[0]);
  if (!file.startsWith(root + '/') || !fs.existsSync(file)) {res.writeHead(404).end(); return;}
  res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    const requests = [], errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url()).pathname;
      requests.push(url);
      let body = {};
      if(url === '/api/voice') body = {provider:'openai_live',enabled:true};
      else if(url === '/api/voice-ready') body = {provider:'openai_live',ready:true};
      else if(url === '/api/session/open') body = {session_id:'abcdef123456'};
      else if(url === '/api/session/abcdef123456') body = {turns:[],fields:[],artifacts:[]};
      else if(url.endsWith('/live')) body = {transport:{sdp:'fixture answer'}};
      else if(url === '/api/todos') body = {todos:[]};
      else if(url === '/api/watch') body = {sessions:[],decisions:[],slack:{connected:true}};
      else if(url.includes('supervisor') || url === '/api/agents') body = {sessions:[],agents:[]};
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
    });
    await page.addInitScript(() => {
      window.fixture = {sources:[], peers:[], mediaPlays:0};
      window.EventSource = class {constructor(url){this.url=url; fixture.sources.push(this);} close(){} };
      HTMLMediaElement.prototype.play = async function(){fixture.mediaPlays++;};
      HTMLMediaElement.prototype.pause = function(){};
      Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>({getAudioTracks:()=>[{}],getTracks:()=>[{stop(){}}]})}});
      window.RTCPeerConnection = class {
        constructor(){this.iceGatheringState='complete'; this.connectionState='connected'; fixture.peers.push(this);}
        addTrack(){} createDataChannel(){return this.channel={readyState:'open',send(){},close(){}};}
        async createOffer(){return {type:'offer',sdp:'fixture offer'};}
        async setLocalDescription(description){this.localDescription=description;}
        async setRemoteDescription(){this.channel.onmessage({data:JSON.stringify({type:'session.started'})});}
        close(){} removeEventListener(){} addEventListener(){}
      };
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>fixture.sources.some(s=>s.url.includes('/events')));
    async function deliver(text,id){
      await page.evaluate(({text,id})=>{
        const source=fixture.sources.find(s=>s.url.includes('/session/') && s.url.includes('/events'));
        source.onmessage({data:JSON.stringify({kind:'turn',turn:{id,role:'assistant',text}})});
        source.onmessage({data:JSON.stringify({kind:'answer',spoken:text})});
      },{text,id});
      await page.locator('#conversation .body').filter({hasText:text}).waitFor();
    }
    await deliver('Initial secondary tab reply.',1);
    await page.locator('#mic').click();
    await page.waitForFunction(()=>document.querySelector('#mic').dataset.voiceState==='listening');
    await page.evaluate(()=>fixture.peers.at(-1).channel.onmessage({data:JSON.stringify({type:'session.output_transcript.delta',delta:'Native voice reply.'})}));
    await page.waitForFunction(()=>document.querySelector('#mic').dataset.voiceState==='speaking');
    await page.locator('#mic').click();
    await page.waitForFunction(()=>document.querySelector('#mic').dataset.voiceState==='idle');
    await deliver('Delayed work completed after Stop.',2);
    await page.locator('#mic').click();
    await page.waitForFunction(()=>document.querySelector('#mic').dataset.voiceState==='listening');
    await page.evaluate(()=>fixture.peers.at(-1).channel.onclose());
    await page.waitForFunction(()=>document.querySelector('#mic').dataset.voiceState==='error');
    await deliver('Delayed work completed after disconnect.',3);
    await page.locator('#mic').click();
    await page.waitForFunction(()=>document.querySelector('#mic').dataset.voiceState==='listening');
    assert.equal(requests.filter(u=>u === '/api/speak').length,0);
    assert.equal(requests.filter(u=>u.endsWith('/live')).length,3,'each retry starts native transport');
    assert.equal(await page.evaluate(()=>fixture.mediaPlays),0,'no legacy Audio playback');
    assert.deepEqual(errors,[]);
    if(process.env.PLAYBACK_SCREENSHOT) await page.screenshot({path:process.env.PLAYBACK_SCREENSHOT});
    console.log(JSON.stringify({result:'PASS',nativeConnections:3,legacyRequests:0,legacyPlays:0,visibleReplies:3,errors,proof:'actual mic clicks, shipped LiveVoice, channel disconnect, retry; inert RTC/microphone/provider adapters'}));
  } finally {await browser.close(); server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
