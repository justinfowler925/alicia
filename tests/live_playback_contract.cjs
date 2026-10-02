// Exercise shipped handlers with inert media/network adapters; never contacts a live session.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
let source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../alicia/static/session.js'), 'utf8');
const mutant = process.argv[3];
if (mutant === 'stop') source = source.replace('function teardownVoice() {', 'function teardownVoice() { state.voiceProvider = "legacy";');
if (mutant === 'initial') source = source.replace('async function loadVoiceProvider() {', 'async function loadVoiceProvider() { if (state.voiceProvider === null) return "legacy";');
if (mutant === 'product') {
  source = source.replace('if (state.voiceProvider === "openai_live") return true;', 'if (state.voiceProvider === "openai_live" && !state.productBrainSpeak) return true;');
  source = source.replace('provider === "openai_live" || epoch', '(provider === "openai_live" && !productOwned) || epoch');
}
source = source.replace('await import("/static/live-voice.js")', '({LiveVoice: globalThis.TestLiveVoice})');
const requests = [], played = [];
let provider = 'openai_live', ready = true, failStart = false, pendingBlob;
const element = {addEventListener() {}, setAttribute() {}, querySelector() {return null;}, classList: {toggle() {}}, dataset: {}};
const context = vm.createContext({
  console, AbortController, setTimeout, clearTimeout,
  document: {addEventListener() {}, querySelector() { return element; }},
  window: {addEventListener() {}},
  URL: {createObjectURL() { return 'blob:fixture'; }, revokeObjectURL() {}},
  Audio: class {play() {played.push('audio'); return Promise.resolve();} pause() {}},
  TestLiveVoice: class {
    mute() {} stop() {}
    async start() { if(failStart) throw new Error('fixture disconnected'); }
  },
  fetch: async (url) => {
    requests.push(url);
    if(url === '/api/voice') return {ok: true, json: async () => ({provider})};
    if(url === '/api/voice-ready') return {ok: true, json: async () => ({provider, ready, reason: 'fixture unavailable'})};
    if(url === '/api/speak') return {ok: true, blob: async () => pendingBlob ? pendingBlob : ({})};
    throw new Error(`Unexpected network path ${url}`);
  }
});
vm.runInContext(source, context);
const run = code => vm.runInContext(code, context);
run(`setVoicePhase = phase => {state.voicePhase = phase;}; resolveThinking = () => {};
  stopListening = () => {}; teardownConvAI = async () => {}; teardownLiveKit = async () => {};
  rememberSpoken = () => {};`);
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  // An untouched second tab has no active transport; configured provider still owns output.
  run(`handle({kind:'reply', spoken:'from another tab'}); handle({kind:'answer', spoken:'completed work'});`);
  await settle();
  assert.equal(requests.filter(x => x === '/api/speak').length, 0, 'initial/secondary tab must not call legacy speech');
  await run('startVoice()');
  assert.equal(run('state.voiceTransport'), 'openai_live');
  run(`bargeIn(); handle({kind:'answer', spoken:'late after Stop'});`);
  await settle();
  assert.equal(run('state.voiceTransport'), null, 'real Stop tears down transport');
  assert.equal(requests.filter(x => x === '/api/speak').length, 0, 'Stop must preserve live playback ownership');
  failStart = true;
  await run('startVoice()');
  run(`handle({kind:'answer', spoken:'late after failed reconnect'});`);
  await settle();
  assert.equal(run('state.voicePhase'), 'error');
  assert.equal(requests.filter(x => x === '/api/speak').length, 0, 'failed reconnect never changes voice');
  failStart = false;
  await run('startVoice()');
  assert.equal(run('state.voiceTransport'), 'openai_live', 'retry reconnects same provider');
  run('teardownVoice()');
  await run(`speak('forced product speech', {productOwned:true})`);
  assert.equal(requests.filter(x => x === '/api/speak').length, 0, 'product-owned flag cannot bypass live ownership');
  ready = false;
  await run('startVoice()');
  assert.equal(run('state.voiceProvider'), 'openai_live', 'unready service retains provider');
  assert.equal(played.length, 0);
  // Positive control: the original legacy output still works.
  provider = 'legacy';
  run(`state.voiceProvider = null; state.voiceTransport = 'legacy';`);
  await run(`speak('legacy positive control')`);
  assert.equal(played.length, 1, 'legacy Audio adapter must be reached');
  // A response arriving after stop cannot create a new audio player.
  let release;
  pendingBlob = new Promise(resolve => { release = resolve; });
  const speaking = run(`speak('pending old voice')`);
  await settle();
  run('teardownVoice()');
  release({});
  await speaking;
  assert.equal(played.length, 1, 'stopped pending response must not play');
  console.log('PASS: initial/secondary tab, Stop + delayed reply, failed start/retry, product override, unavailable provider, legacy positive control, stopped pending audio');
})().catch(error => { console.error(error); process.exitCode = 1; });
