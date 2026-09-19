/* Real development server proof. Set ALEXIS_LIVE_PROOF=1 for provider playback. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const fixture = process.env.ALEXIS_TEST_MIC_WAV;
  const browser = await chromium.launch({headless:true,args:fixture ? [
    '--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--autoplay-policy=user-gesture-required',
    `--use-file-for-fake-audio-capture=${fixture}%noloop`,
  ] : []});
  const page = await browser.newPage({viewport:{width:1280,height:900},permissions:fixture ? ['microphone'] : []});
  if (fixture) await page.addInitScript(() => {
    window.microphoneProofStreams = [];
    window.alexisRemoteAudioPeak = 0;
    const makeSource = AudioContext.prototype.createMediaStreamSource;
    AudioContext.prototype.createMediaStreamSource = function(stream) {
      const source = makeSource.call(this, stream);
      if (stream === document.querySelector('#alexis-video')?.srcObject) {
        const analyser = this.createAnalyser(); source.connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        const timer = setInterval(() => {
          analyser.getFloatTimeDomainData(samples);
          window.alexisRemoteAudioPeak = Math.max(window.alexisRemoteAudioPeak, ...samples.map(Math.abs));
          if (stream.getTracks().every(t => t.readyState === 'ended')) clearInterval(timer);
        }, 30);
      }
      return source;
    };
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async options => {
      const stream = await original(options);
      window.microphoneProofStreams.push(stream);
      return stream;
    };
  });
  const base = process.env.BRUTUS_TEST_URL || 'http://127.0.0.1:8789';
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const receipt = {pages:[],liveAvatar:'not_tested',answeredTurn:'not_tested'};
  try {
    await page.goto(base);
    await page.waitForFunction(() => Boolean(state.sessionId));
    assert.equal(await page.locator('.product-tabs [aria-current=page]').innerText(),'Alexis');
    assert(await page.locator('#say').isVisible());
    await page.getByRole('link',{name:'Brutus',exact:true}).click();
    await page.getByRole('navigation',{name:'Workspaces'}).waitFor();
    assert.equal(await page.locator('#alexis-frame').count(),0);
    assert.equal(await page.locator('.product-tabs [aria-current=page]').innerText(),'Brutus');
    receipt.pages.push('Brutus preserved');
    await page.getByRole('link',{name:'Alexis',exact:true}).click();
    await page.waitForFunction(() => Boolean(state.sessionId));
    await page.locator('#alexis-portrait').waitFor();
    assert(await page.locator('#conversation').isVisible(), 'Transcript visible before any interaction');
    assert(await page.locator('#say').isVisible(), 'Composer visible before any interaction');
    await page.locator('#say').click();
    assert.equal(await page.locator('#say').evaluate(e=>e===document.activeElement),true);
    await page.locator('#alexis-improve summary').click();
    await page.waitForFunction(()=>!document.querySelector('#alexis-feedback-status').textContent.startsWith('No feedback'));
    assert.match(await page.locator('#alexis-feedback-status').innerText(),/needed first|Choose a reply/);
    for (const width of [390,768,1280]) {
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
      const frame=await page.locator('#alexis-frame').boundingBox();
      assert(frame.height>150);
      assert(Math.abs(frame.width/frame.height-1.5)<.02);
      await page.screenshot({path:`/tmp/alexis-page-${width}.png`,fullPage:true});
    }
    receipt.pages.push('Alexis responsive frame, keyboard path, feedback empty state');
    if (process.env.ALEXIS_LIVE_PROOF === '1') {
      await page.setViewportSize({width:1280,height:900});
      if (fixture) await page.locator('#mic').click();
      else {
        const result = await page.evaluate(async()=>{
        const controller=new AbortController();
        const timer=setTimeout(()=>controller.abort(),45000);
        try{return await window.alexis.start(state.sessionId,controller.signal);}finally{clearTimeout(timer);}
      });
        assert.equal(result,true,'Real Anam connection');
      }
      await page.waitForFunction(()=>{const v=document.querySelector('#alexis-video');return !v.hidden&&v.readyState>=2&&v.videoWidth>0;},null,{timeout:45000});
      receipt.liveAvatar=await page.locator('#alexis-video').evaluate(v=>({decodedWidth:v.videoWidth,decodedHeight:v.videoHeight,displayedHeight:v.getBoundingClientRect().height}));
      if (fixture) {
        await page.waitForFunction(()=>Array.from(document.querySelectorAll('#conversation .turn.user')).some(e=>/microphone test/i.test(e.textContent)),null,{timeout:60000});
      } else {
        await page.locator('#say').fill('For our UI development test, briefly introduce yourself and name the prototype Orchard.');
        await page.locator('#composer').evaluate(e=>e.requestSubmit());
      }
      await page.waitForFunction(()=>document.querySelectorAll('#conversation .turn.brutus').length>0,null,{timeout:150000});
      const answer=await page.locator('#conversation .turn.brutus').last().textContent();
      assert.match(answer,fixture ? /Alexis/i : /Orchard/i);
      await page.waitForFunction(()=>state.voicePhase==='speaking',null,{timeout:45000});
      if (fixture) {
        await page.waitForFunction(() => window.alexisRemoteAudioPeak > 0.005, null, {timeout:45000});
        receipt.remoteAudioPeak = await page.evaluate(() => window.alexisRemoteAudioPeak);
        const firstTime = await page.locator('#alexis-video').evaluate(v => v.currentTime);
        await page.waitForFunction(t => document.querySelector('#alexis-video').currentTime > t + 0.5, firstTime);
        receipt.videoFramesAdvancing = true;
      }
      await page.screenshot({path:'/tmp/alexis-live-provider.png',fullPage:true});
      receipt.answeredTurn={answer,source:'real shared brain and Anam audio passthrough',microphone:fixture ? 'synthetic audio through real transcription; no physical microphone' : 'not tested; typed input'};
      await page.getByRole('button',{name:'End conversation',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('#alexis-video').hidden);
      assert.equal(await page.locator('#alexis-portrait').isVisible(),true);
      if (fixture) {
        await page.waitForFunction(()=>window.microphoneProofStreams.length>0 && window.microphoneProofStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended')));
        receipt.microphoneReleased=true;
      }
      await page.locator('#alexis-improve summary').click();
      await page.locator('#alexis-improve summary').click();
      await page.waitForFunction(()=>document.querySelector('#alexis-turn').options.length>1);
      await page.locator('#alexis-turn').selectOption({index:1});
      await page.locator('#alexis-rating').selectOption('helpful');
      await page.locator('#alexis-correction').fill('Synthetic verification feedback; exclude from quality baseline.');
      await page.locator('#alexis-feedback-save').click();
      await page.waitForFunction(()=>document.querySelector('#alexis-feedback-status').textContent.includes('Saved to Alexis'));
      receipt.feedback='persisted through central service';
    }
    await page.getByRole('link',{name:'Atlas',exact:true}).click();
    await page.locator('iframe[title="Atlas workspace"]').waitFor();
    assert.equal(await page.locator('.product-tabs [aria-current=page]').innerText(),'Atlas');
    const atlas=page.frameLocator('iframe');
    await atlas.locator('body').waitFor();
    receipt.pages.push('Atlas existing workspace embedded');
    assert.deepEqual(errors,[]);
    receipt.errors=errors;
    fs.writeFileSync('/tmp/alexis-browser-receipt.json',JSON.stringify(receipt,null,2));
    console.log(JSON.stringify(receipt));
  } catch (error) {
    console.error(await page.evaluate(() => ({phase:state.voicePhase, transport:state.voiceTransport,
      detail:document.querySelector('#voice-state-detail')?.textContent,
      avatar:document.querySelector('#alexis-status')?.textContent,
      peak:window.alexisRemoteAudioPeak,
      video:{ready:document.querySelector('#alexis-video')?.readyState,paused:document.querySelector('#alexis-video')?.paused},
    })));
    throw error;
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
