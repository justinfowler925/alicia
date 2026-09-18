/* Direct Forge chat. The browser is a view; Studio owns history and execution. */
(() => {
  const $ = (id) => document.getElementById(id);
  const terminal = new Set(['succeeded', 'failed', 'cancelled', 'interrupted', 'blocked', 'handoff']);
  let thread = localStorage.getItem('brutus.forge.thread') || '';
  let pending = JSON.parse(localStorage.getItem('brutus.forge.pending') || 'null');
  let busy = false, working = false, timer = null, generation = 0, rendered = '';
  const status = (text) => { $('forge-status').textContent = text; };
  async function api(action, extra = {}) {
    const response = await fetch('/api/forge/request', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Brutus-Chat': 'forge' },
      body: JSON.stringify({ action, ...extra }),
    });
    const data = await response.json();
    if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : 'Forge could not read this request.'); error.status = response.status; throw error; }
    return data;
  }
  function controls() {
    $('forge-send').disabled = busy || working || !!pending;
    $('forge-stop').hidden = !working;
    $('forge-new').disabled = busy || !!pending;
    $('forge-threads').disabled = busy || !!pending;
  }
  function failure(error) {
    status(error.message); $('forge-reconnect').hidden = false; controls();
  }
  function turn(who, text, user = false) {
    const item = document.createElement('article'); item.className = `turn ${user ? 'user' : 'assistant'}`;
    const label = document.createElement('p'); label.className = 'who'; label.textContent = who;
    const body = document.createElement('div'); body.className = 'body'; body.textContent = text;
    item.append(label, body); $('forge-transcript').append(item);
  }
  function render(data) {
    $('forge-model').textContent = `${data.model} · Studio`;
    const key = JSON.stringify(data.turns);
    if (key !== rendered) {
      const log = $('forge-transcript');
      const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
      log.replaceChildren();
      for (const t of data.turns) {
        turn('You', t.message, true);
        if (t.answer) turn('Forge', t.answer);
        if (t.status !== 'succeeded') turn('Forge · ' + t.status,
          t.cancellation_requested && !terminal.has(t.status) ? 'Stopping…' : t.reason || (terminal.has(t.status) ? 'No completed reply. You can send a follow-up.' : 'Working on Studio…'));
      }
      if (!data.turns.length) {
        const empty = document.createElement('div'); empty.className = 'conversation-empty';
        const title = document.createElement('h3'); title.textContent = 'What would you like to work on?';
        const text = document.createElement('p'); text.textContent = 'Ask Forge a question or give it a task. Your conversation and work stay on Studio.';
        empty.append(title, text); log.append(empty);
      }
      if (nearBottom) log.scrollTop = log.scrollHeight;
      rendered = key;
    }
    working = data.turns.some(t => !terminal.has(t.status));
    const last = data.turns.at(-1);
    status(working ? (last.status === 'queued' ? 'Queued on Studio. You can close this page and return later.' : 'Forge is working on Studio…') : last ? (last.status === 'succeeded' ? 'Reply received.' : `Forge ${last.status}. ${last.reason || 'You can send a follow-up.'}`) : 'Ready when you are.');
    $('forge-reconnect').hidden = true; controls();
  }
  function schedule() {
    clearTimeout(timer);
    if (!$('forge-panel').hidden && working) timer = setTimeout(refresh, 3000);
  }
  async function refresh() {
    const ticket = generation, selected = thread;
    if (!selected || busy) return;
    try { const data = await api('get', { thread_id: selected }); if (ticket === generation) { render(data); schedule(); } }
    catch (error) { if (ticket === generation) failure(error); }
  }
  async function history() {
    const data = await api('list');
    $('forge-model').textContent = `${data.model} · Studio`;
    $('forge-threads').replaceChildren(new Option('New conversation', ''));
    data.threads.forEach(t => $('forge-threads').append(new Option(t.title, t.id)));
    if (thread && !data.threads.some(t => t.id === thread)) thread = '';
    $('forge-threads').value = thread;
  }
  async function sendPending() {
    if (!pending) return;
    // Reuse the durable request ID even after reload or an SSH timeout.
    let data;
    try { data = await api('send', pending); }
    catch (error) {
      if (error.status === 409 || error.status === 422) {
        $('forge-message').value = pending.message;
        pending = null; localStorage.removeItem('brutus.forge.pending');
      }
      throw error;
    }
    thread = pending.thread_id; localStorage.setItem('brutus.forge.thread', thread);
    pending = null; localStorage.removeItem('brutus.forge.pending');
    $('forge-message').value = ''; render(data); await history();
  }
  async function connect() {
    if (busy) return;
    busy = true; controls(); status('Connecting to Forge…');
    try {
      if (pending) await sendPending();
      await history();
      if (thread) render(await api('get', { thread_id: thread }));
      else { working = false; rendered = ''; render({ model: $('forge-model').textContent.split(' · ')[0], turns: [] }); }
    } catch (error) { failure(error); }
    finally { busy = false; controls(); schedule(); }
  }
  function open() {
    const active = location.hash === '#forge';
    $('forge-panel').hidden = !active; document.body.classList.toggle('forge-open', active);
    $('forge-toggle').textContent = active ? 'Back to Brutus' : 'Forge chat';
    $('forge-toggle').setAttribute('aria-pressed', String(active));
    generation++; clearTimeout(timer);
    if (active) connect();
  }
  $('forge-toggle').addEventListener('click', () => { location.hash = $('forge-panel').hidden ? 'forge' : ''; });
  window.addEventListener('hashchange', open);
  $('forge-reconnect').addEventListener('click', connect);
  $('forge-new').addEventListener('click', async () => {
    if (busy || pending) return;
    thread = ''; generation++; working = false; rendered = ''; clearTimeout(timer);
    localStorage.removeItem('brutus.forge.thread'); $('forge-threads').value = '';
    render({ model: $('forge-model').textContent.split(' · ')[0], turns: [] }); $('forge-message').focus();
  });
  $('forge-threads').addEventListener('change', async () => {
    thread = $('forge-threads').value; generation++; clearTimeout(timer); rendered = '';
    localStorage.setItem('brutus.forge.thread', thread); await connect();
  });
  $('forge-composer').addEventListener('submit', async (event) => {
    event.preventDefault(); const message = $('forge-message').value.trim();
    if (!message || busy || working || pending) return;
    busy = true; controls(); status('Sending to Forge…');
    try {
      if (!thread) {
        thread = crypto.randomUUID();
        await api('create', { thread_id: thread });
        localStorage.setItem('brutus.forge.thread', thread);
      }
      pending = { thread_id: thread, message_id: crypto.randomUUID(), message };
      localStorage.setItem('brutus.forge.pending', JSON.stringify(pending));
      await sendPending();
    } catch (error) { failure(error); }
    finally { busy = false; controls(); schedule(); }
  });
  $('forge-message').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('forge-composer').requestSubmit(); }
  });
  $('forge-stop').addEventListener('click', async () => {
    if (busy) return; busy = true; controls();
    try { render(await api('stop', { thread_id: thread })); status('Stop requested. Checking Studio…'); }
    catch (error) { failure(error); }
    finally { busy = false; controls(); schedule(); }
  });
  open();
})();
