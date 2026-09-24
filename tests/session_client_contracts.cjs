// Offline behavioral contract for the shipped client. No browser, network or media.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const name = process.argv[2];
let source = fs.readFileSync(path.join(__dirname, '../alicia/static/session.js'), 'utf8');
if (process.argv[3] === 'mutant') {
  const mutations = {
    providers: ['session?.surface === "codex"', 'false'],
    lifecycle: ['"Progress not verified — see Needs eyes."', 'a.recommended_next_action'],
    playback: ['function voiceOwnsPlayback() {', 'function voiceOwnsPlayback() { return false;'],
  };
  const [before, after] = mutations[name];
  assert(source.includes(before), 'mutation target must exist');
  source = source.replace(before, after);
}
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.hidden = false; this.classList = { toggle() {} }; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  querySelectorAll() { return []; }
  contains() { return false; }
  addEventListener() {}
  setAttribute() {}
}
const elements = new Map();
const document = {
  querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  },
  createElement: tag => new Element(tag),
  addEventListener() {},
  activeElement: null,
};
const context = vm.createContext({ document, window: { addEventListener() {} }, console });
vm.runInContext(source, context, { filename: 'session.js' });
const run = code => vm.runInContext(code, context);
function descendants(el) { return [el, ...el.children.flatMap(descendants)]; }
function nodes(className) {
  return descendants(elements.get('#supervisor-agents')).filter(el => el.className === className);
}
if (name === 'providers') {
  run(`glance.mode = 'all'; renderSupervisor({sessions: ['codex', 'cursor', 'claude'].map(surface => ({
    id: surface, surface, title: surface + ' task', age: '2m', state: 'running',
    status_source: 'local fixture', assessment: {judgment_source: 'model', verified_progress: ['Verified change']}
  }))});`);
  assert.deepEqual(nodes('agent-provider').map(el => el.textContent),
    ['OpenAI · Codex · 2m', 'cursor · 2m', 'claude · 2m'], 'provider labels');
  assert.equal(nodes('agent-title').length, 3, 'all providers rendered');
  assert(nodes('agent-expanded').every(el => el.textContent.includes('Source: local fixture')));
} else if (name === 'lifecycle') {
  run(`glance.mode = 'all'; renderSupervisor({sessions: [
    {id: 'pending', title: 'Pending', assessment: {judgment_source: 'pending', recommended_next_action: 'Monitor lifecycle forever'}},
    {id: 'verified', title: 'Verified', assessment: {judgment_source: 'model', verified_progress: ['Tests passed']}},
    {id: 'blocked', title: 'Blocked', assessment: {should_intervene: true, blocker_or_decision: 'Approve scope'}}
  ]});`);
  const progress = nodes('agent-progress').map(el => el.textContent);
  assert.equal(progress.length, 3);
  assert(progress.includes('Tests passed'));
  assert(progress.includes('Approve scope'));
  assert(progress.includes('Progress not verified — see Needs eyes.'), 'pending progress stays truthful');
  assert(!progress.some(text => text.includes('Monitor lifecycle')), 'no generic lifecycle lecture in summary');
} else if (name === 'playback') {
  run(`globalThis.spoken = []; speak = text => spoken.push(text); resolveThinking = () => {};
    for (const kind of ['reply', 'answer']) {
      for (const transport of ['livekit', 'convai']) {
        state.voiceTransport = transport;
        state.livekitRoom = null; state.convai = null;
        handle({kind, spoken: 'handshake'});
      }
      state.voiceTransport = null;
      state.livekitRoom = {}; handle({kind, spoken: 'room'});
      state.livekitRoom = null;
      state.convai = {}; handle({kind, spoken: 'convai session'});
      state.convai = null;
    }
  `);
  assert.equal(run('spoken.length'), 0, 'transport owns playback before and after connection');
  run(`state.voiceTransport = 'legacy'; handle({kind: 'reply', spoken: 'legacy reply'});
    handle({kind: 'answer', spoken: 'legacy answer'});
    handle({kind: 'reply', spoken: ''});`);
  assert.deepEqual(Array.from(run('spoken')), ['legacy reply', 'legacy answer'], 'legacy positive path reached');
} else { throw new Error('unknown case'); }
console.log(`PASS ${name}`);
