/* Direct Forge chat. The browser is a view; Studio owns history and execution. */
(() => {
  const $ = (id) => document.getElementById(id);
  const terminal = new Set(['succeeded', 'failed', 'cancelled', 'interrupted', 'blocked', 'handoff']);
  let thread = localStorage.getItem('brutus.forge.thread') || '';
  let pending = JSON.parse(localStorage.getItem('brutus.forge.pending') || 'null');
  let busy = false, working = false, timer = null, generation = 0, rendered = '';
  let attachments = [];
  let runActivity = null, turnCount = 0, lastCheck = 0, connection = 'connecting';
  const status = (text) => { if ($('forge-status').textContent !== text) $('forge-status').textContent = text; };
  async function api(action, extra = {}) {
    const response = await fetch('/api/forge/request', {
      signal: AbortSignal.timeout(35000),
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Brutus-Chat': 'forge' },
      body: JSON.stringify({ action, ...extra }),
    });
    const data = await response.json();
    if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : 'Forge could not read this request.'); error.status = response.status; throw error; }
    return data;
  }
  function duration(seconds) {
    const value = Math.max(0, Math.floor(seconds || 0));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
  }
  function paintActivity() {
    const a = runActivity;
    const sinceCheck = lastCheck ? (Date.now() - lastCheck) / 1000 : 0;
    const disconnected = connection === 'disconnected' || (a?.active && sinceCheck > 15);
    const checking = connection === 'connecting';
    const now = a ? a.checked_at + sinceCheck : 0;
    const elapsed = a?.started_at ? duration((a.finished_at || now) - a.started_at) : '0:00';
    const quietFor = a ? Math.max(0, now - (a.last_output_at || a.started_at || a.checked_at)) : 0;
    const quiet = a?.active && ['running', 'starting', 'draining'].includes(a.status) && quietFor >= 90;
    const live = a?.active && !disconnected && !checking && !quiet && ['running', 'starting'].includes(a.status) && a.phase !== 'Stopping';
    const label = turnCount ? `Turn ${turnCount}` : '0 turns';
    $('forge-turn-count').textContent = `${label} · ${a?.active || 0} active${disconnected ? ' (last known)' : ''}`;
    const phase = disconnected ? (connection === 'disconnected' ? 'Connection lost' : 'Status stale') : checking ? 'Checking Studio…' : quiet ? 'No recent activity' : a?.phase || 'Ready';
    if ($('forge-phase').textContent !== phase) $('forge-phase').textContent = phase;
    $('forge-activity').dataset.state = disconnected ? 'disconnected' : checking ? 'checking' : quiet ? 'quiet' : live ? 'working' : a?.active ? 'waiting' : 'idle';
    let detail = a ? `${elapsed} elapsed · ${a.events} updates · ${a.tools} tool calls` : 'No turn running.';
    if (a?.active) detail += a.last_output_at ? ` · Last output ${duration(quietFor)} ago` : ' · No output yet';
    if (disconnected) detail += ` · Last confirmed ${lastCheck ? duration(sinceCheck) + ' ago' : 'never'}. Reconnect to check progress.`;
    else if (quiet) detail += ' · Studio responds, but Forge has been quiet. It may still be thinking; you can wait or Stop.';
    else if (connection === 'connected') detail += ' · Studio connected';
    $('forge-activity-detail').textContent = detail;
  }
  setInterval(() => { if (!$('forge-panel').hidden) paintActivity(); }, 1000);
  function controls() {
    $('forge-send').disabled = busy || working || !!pending;
    $('forge-attach').disabled = busy || working || !!pending;
    $('forge-files').disabled = busy || working || !!pending;
    $('forge-send').disabled ||= attachments.some(a => !a.ready);
    $('forge-attachments').querySelectorAll('button').forEach(b => { b.disabled = busy || !!pending; });
    $('forge-stop').hidden = !working;
    $('forge-new').disabled = busy || !!pending;
    $('forge-threads').disabled = busy || !!pending;
  }
  function failure(error) {
    connection = 'disconnected'; paintActivity();
    status(error.message); $('forge-reconnect').hidden = false; controls();
  }
  function sourceLinks(body, text) {
    const links = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>]+)/g;
    let offset = 0;
    for (const match of text.matchAll(links)) {
      body.append(document.createTextNode(text.slice(offset, match.index)));
      const link = document.createElement('a');
      link.href = match[2] || match[3];
      link.textContent = match[1] || match[3];
      link.target = '_blank'; link.rel = 'noopener noreferrer';
      body.append(link); offset = match.index + match[0].length;
    }
    body.append(document.createTextNode(text.slice(offset)));
  }
  function turn(who, text, user = false) {
    const item = document.createElement('article'); item.className = `turn ${user ? 'user' : 'assistant'}`;
    const label = document.createElement('p'); label.className = 'who'; label.textContent = who;
    const body = document.createElement('div'); body.className = 'body';
    if (user) body.textContent = text; else sourceLinks(body, text);
    item.append(label, body); $('forge-transcript').append(item);
  }
  function render(data) {
    runActivity = data.activity || null; turnCount = data.turns.length;
    lastCheck = Date.now(); connection = 'connected'; paintActivity();
    $('forge-model').textContent = `${data.model.split('/').at(-1)} · Local on Studio · No cloud fallback`;
    const key = JSON.stringify(data.turns);
    if (key !== rendered) {
      const log = $('forge-transcript');
      const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
      log.replaceChildren();
      for (const t of data.turns) {
        turn('You', t.message + (t.attachments?.length ? '\n\nAttached: ' + t.attachments.map(a => a.name).join(', ') : ''), true);
        if (t.answer) turn('Forge · ' + (t.model || 'Unknown model').split('/').at(-1), t.answer);
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
    try { const data = await api('get', { thread_id: selected }); if (ticket === generation) { render(data); } }
    catch (error) { if (ticket === generation) failure(error); }
    finally { if (ticket === generation) schedule(); }
  }
  async function history() {
    const data = await api('list');
    $('forge-model').textContent = `${data.model.split('/').at(-1)} · Local on Studio · No cloud fallback`;
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
    attachments = []; saveAttachments(); drawAttachments();
    $('forge-message').value = ''; render(data); await history();
  }
  async function connect() {
    if (busy) return;
    busy = true; connection = 'connecting'; paintActivity(); controls(); status('Connecting to Forge…');
    try {
      if (pending) await sendPending();
      await history();
      loadAttachments();
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
  function saveAttachments() {
    if (thread) localStorage.setItem('brutus.forge.files.' + thread, JSON.stringify(attachments.map(({file, ...a}) => a)));
  }
  function loadAttachments() {
    attachments = thread ? JSON.parse(localStorage.getItem('brutus.forge.files.' + thread) || '[]') : [];
    attachments.forEach(a => { if (!a.ready) a.error = 'Upload incomplete. Remove and select this file again.'; });
    drawAttachments();
  }
  function drawAttachments() {
    const list = $('forge-attachments'); list.replaceChildren();
    for (const a of attachments) {
      const row = document.createElement('div'); row.className = 'forge-file';
      const label = document.createElement('span');
      label.textContent = `${a.name} · ${(a.size / 1024).toFixed(1)} KB · ${a.ready ? 'Ready' : a.error || 'Uploading…'}`;
      row.append(label);
      if (a.error && a.file) {
        const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Retry';
        retry.setAttribute('aria-label', 'Retry upload ' + a.name);
        retry.onclick = () => transfer([a]); row.append(retry);
      }
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove';
      remove.setAttribute('aria-label', 'Remove ' + a.name);
      remove.onclick = () => { attachments = attachments.filter(item => item !== a); saveAttachments(); drawAttachments(); controls(); };
      row.append(remove); list.append(row);
    }
    controls();
  }
  async function ensureThread() {
    if (!thread) thread = crypto.randomUUID();
    await api('create', {thread_id: thread});
    localStorage.setItem('brutus.forge.thread', thread);
  }
  async function transfer(files) {
    if (busy || pending || working) return;
    busy = true; controls();
    try {
      await ensureThread();
      for (const a of files) {
        a.error = ''; drawAttachments(); status('Uploading ' + a.name + ' to Studio…');
        try {
          const response = await fetch(`/api/forge/upload/${thread}/${a.id}?name=${encodeURIComponent(a.name)}`, {
            method: 'POST', headers: {'X-Brutus-Chat': 'forge', 'Content-Type': 'application/octet-stream'}, body: a.file,
          });
          const data = await response.json();
          if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Upload failed. Retry or remove this file.');
          a.ready = true; delete a.file;
        } catch (error) { a.error = error.message; }
        saveAttachments(); drawAttachments();
      }
      await history();
      status(attachments.some(a => !a.ready) ? 'Some files need attention. Retry or remove them before sending.' : 'Files ready. Add a message and send to Forge.');
    } catch (error) {
      files.forEach(a => { if (!a.ready) a.error = error.message; });
      saveAttachments(); drawAttachments(); failure(error);
    } finally { busy = false; controls(); }
  }
  function selectFiles(fileList) {
    if (busy || pending || working) { status('Wait for the current request before attaching files.'); return; }
    const files = Array.from(fileList);
    if (attachments.length + files.length > 10) { status('Attach up to 10 files per message. Remove a file first.'); return; }
    if (!files.length) return;
    const added = files.map(file => ({id: crypto.randomUUID(), name: file.name, size: file.size, file, ready: false}));
    attachments.push(...added); drawAttachments(); transfer(added);
  }
  $('forge-attach').addEventListener('click', () => $('forge-files').click());
  $('forge-files').addEventListener('change', event => { selectFiles(event.target.files); event.target.value = ''; });
  // Prevent the browser from navigating to dropped files, including outside the composer.
  document.addEventListener('dragover', event => {
    if (location.hash === '#forge' && event.dataTransfer.types.includes('Files')) {
      event.preventDefault(); event.dataTransfer.dropEffect = busy || working || pending ? 'none' : 'copy';
      $('forge-panel').classList.add('forge-dragging');
    }
  });
  document.addEventListener('dragleave', event => { if (!event.relatedTarget) $('forge-panel').classList.remove('forge-dragging'); });
  document.addEventListener('drop', event => {
    if (location.hash !== '#forge' || !event.dataTransfer.types.includes('Files')) return;
    event.preventDefault(); $('forge-panel').classList.remove('forge-dragging'); selectFiles(event.dataTransfer.files);
  });
  $('forge-toggle').addEventListener('click', () => { location.hash = $('forge-panel').hidden ? 'forge' : ''; });
  window.addEventListener('hashchange', open);
  $('forge-reconnect').addEventListener('click', connect);
  $('forge-new').addEventListener('click', async () => {
    if (busy || pending) return;
    thread = ''; generation++; working = false; rendered = ''; clearTimeout(timer);
    attachments = []; drawAttachments();
    localStorage.removeItem('brutus.forge.thread'); $('forge-threads').value = '';
    render({ model: $('forge-model').textContent.split(' · ')[0], turns: [] }); $('forge-message').focus();
  });
  $('forge-threads').addEventListener('change', async () => {
    runActivity = null; turnCount = 0; lastCheck = 0;
    thread = $('forge-threads').value; generation++; clearTimeout(timer); rendered = '';
    localStorage.setItem('brutus.forge.thread', thread); await connect();
  });
  $('forge-composer').addEventListener('submit', async (event) => {
    event.preventDefault(); const message = $('forge-message').value.trim();
    if ((!message && !attachments.length) || attachments.some(a => !a.ready) || busy || working || pending) return;
    busy = true; controls(); status('Sending to Forge…');
    try {
      if (!thread) {
        thread = crypto.randomUUID();
        await api('create', { thread_id: thread });
        localStorage.setItem('brutus.forge.thread', thread);
      }
      pending = { thread_id: thread, message_id: crypto.randomUUID(), message: message || 'Please review the attached files.', attachments: attachments.map(a => a.id) };
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
