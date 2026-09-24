/* Presentation adapter only. Transcript, tools and reasoning belong to the shared brain. */
// Expose the server's actual release identity for browser verification.
fetch('/version').then(response => response.ok ? response.json() : null).then(version => {
  if (!version?.sha || !version?.deployed_at) return;
  for (const [name, content] of Object.entries({
    'shine-source-commit': version.sha,
    'shine-build-id': `${version.sha}:${version.deployed_at}`,
  })) {
    const meta = document.createElement('meta'); meta.name = name; meta.content = content;
    document.head.append(meta);
  }
}).catch(() => {});
window.alicia = (() => {
  let client = null, generation = 0, audioStream = null, playback = 0;
  let outputContext = null, remoteAudio = null, localAudio = null;
  function unlock() {
    outputContext ||= new AudioContext();
    void outputContext.resume().catch(() => {});
  }
  function connectAudio(video) {
    if (!remoteAudio && outputContext && video.srcObject?.getAudioTracks().length) {
      remoteAudio = outputContext.createMediaStreamSource(video.srcObject);
      remoteAudio.connect(outputContext.destination);
    }
  }
  const node = id => document.getElementById(id);
  const status = text => { node('alicia-status').textContent = text; };
  function portrait() { node('alicia-video').hidden = true; node('alicia-portrait').hidden = false; }
  async function stop() {
    generation++; playback++;
    const old = client; client = null; audioStream = null;
    remoteAudio?.disconnect(); remoteAudio = null;
    try { localAudio?.stop(); } catch (_) {}
    localAudio = null;
    portrait();
    const video = node('alicia-video'); video.pause(); video.srcObject = null;
    status('Video off');
    if (old) await old.stopStreaming().catch(() => {});
  }
  function interrupt() {
    playback++;
    try { localAudio?.stop(); } catch (_) {}
    localAudio = null;
    try { client?.interruptPersona(); audioStream?.endSequence(); } catch (_) {}
  }
  async function start(sessionId, signal) {
    if (!node('alicia-live').checked) { status('Voice only · same Alicia'); return false; }
    await stop();
    const gen = generation;
    status('Connecting Alicia…');
    let candidate, timer;
    const video = node('alicia-video');
    node('alicia-video-retry').hidden = true;
    try {
      const response = await fetch(`/api/session/${sessionId}/alicia-token`, {method:'POST', signal});
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}));
        throw new Error(typeof failure.detail === 'string' ? failure.detail : 'Avatar unavailable');
      }
      const grant = await response.json();
      const {createClient, AnamEvent} = await import('https://esm.sh/@anam-ai/js-sdk@4.27.0');
      if (signal.aborted || gen !== generation) return false;
      candidate = createClient(grant.sessionToken, {disableInputAudio:true, metrics:{disableClientMetrics:true}});
      client = candidate;
      candidate.addListener(AnamEvent.CONNECTION_CLOSED, () => {
        if (client !== candidate) return;
        client = null; audioStream = null; remoteAudio?.disconnect(); remoteAudio = null; portrait(); status('Video disconnected · voice remains available'); node('alicia-video-retry').hidden = false;
      });
      video.muted = true; // Playback is routed through the gesture-unlocked audio context.
      video.onloadeddata = () => {
        if (client !== candidate || gen !== generation) return;
        video.hidden = false; node('alicia-portrait').hidden = true; connectAudio(video); status('Alicia · live');
      };
      await Promise.race([
        (async () => { await candidate.streamToVideoElement('alicia-video'); await video.play(); })(),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Video connection timed out')), 25000); }),
      ]);
      clearTimeout(timer);
      connectAudio(video);
      if (signal.aborted || gen !== generation) { await candidate.stopStreaming(); return false; }
      audioStream = candidate.createAgentAudioInputStream({encoding:'pcm_s16le',sampleRate:16000,channels:1});
      return true;
    } catch (error) {
      clearTimeout(timer);
      if (candidate) await candidate.stopStreaming().catch(() => {});
      if (gen === generation) {
        client = null; audioStream = null; portrait();
        status(signal.aborted ? 'Conversation ended' : `Video unavailable: ${error.message}. Voice can still connect.`);
        node('alicia-video-retry').hidden = signal.aborted;
      }
      return false;
    }
  }
  async function play(blob, signal) {
    if (!client || !audioStream) {
      if (!outputContext) return false;
      const buffer = await outputContext.decodeAudioData(await blob.arrayBuffer());
      if (signal.aborted) return true;
      const source = outputContext.createBufferSource(); source.buffer = buffer;
      source.connect(outputContext.destination); localAudio = source;
      await new Promise(resolve => { source.onended = resolve; source.start(); });
      if (localAudio === source) localAudio = null;
      return true;
    }
    connectAudio(node('alicia-video'));
    const target = client, stream = audioStream, sequence = ++playback;
    const context = new AudioContext({sampleRate:16000});
    try {
      const decoded = await context.decodeAudioData(await blob.arrayBuffer());
      const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
      const source = offline.createBufferSource(); source.buffer = decoded; source.connect(offline.destination); source.start();
      const samples = (await offline.startRendering()).getChannelData(0);
      for (let offset = 0; offset < samples.length; offset += 1600) {
        if (signal.aborted || sequence !== playback || client !== target) return true;
        const length = Math.min(1600, samples.length - offset);
        const bytes = new Uint8Array(length * 2), view = new DataView(bytes.buffer);
        for (let i = 0; i < length; i++) {
          const value = Math.max(-1, Math.min(1, samples[offset + i]));
          view.setInt16(i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
        }
        stream.sendAudioChunk(btoa(String.fromCharCode(...bytes)));
        // Pace PCM delivery so long answers never overflow the provider buffer.
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (sequence === playback && client === target) stream.endSequence();
      return true;
    } finally { await context.close(); }
  }
  let pending = false, currentPhase = 'idle', files = [];
  const notice = text => { node('alicia-composer-status').textContent = text; };
  const isBusy = () => pending || ['thinking', 'buffering', 'speaking'].includes(currentPhase);
  function updateComposer() {
    const button = node('send'), stopping = isBusy();
    button.disabled = false;
    button.textContent = stopping ? 'Stop' : 'Send';
    button.setAttribute('aria-label', stopping ? 'Stop reply' : 'Send message');
    node('alicia-attach').disabled = pending;
  }
  function busy(value) { pending = value; updateComposer(); }
  function phase(value) { currentPhase = value; updateComposer(); }
  function renderAttachments() {
    const host = node('alicia-attachments'); host.replaceChildren();
    files.forEach((file, index) => {
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = `${file.name} ×`; button.setAttribute('aria-label', `Remove ${file.name}`);
      button.onclick = () => { files.splice(index, 1); renderAttachments(); };
      host.append(button);
    });
  }
  function clearAttachments() { files = []; renderAttachments(); }
  function bind({session, end, type}) {
    node('alicia-sessions').onclick = () => { const panel = document.querySelector('.alicia-work-context'); panel.open = !panel.open; };
    node('alicia-attach').onclick = () => node('alicia-files').click();
    node('alicia-files').onchange = async event => {
      notice('');
      try {
        const selected = Array.from(event.target.files);
        if (files.length + selected.length > 5) throw new Error('Attach up to five files.');
        const additions = [];
        for (const file of selected) {
          if (!/\.(txt|md|csv|json|log|py|js|ts|tsx|html|css|ya?ml|xml|sql)$/i.test(file.name) && !file.type.startsWith('text/')) {
            throw new Error('Choose a text, Markdown, CSV, JSON, or source-code file. PDF and images are not supported yet.');
          }
          if (file.size > 100000) throw new Error(`${file.name} is too large. Use a file under 100 KB.`);
          const text = await file.text();
          if (!text.trim() || text.includes('\0') || text.length > 25000) throw new Error(`${file.name} must contain readable text under 25,000 characters.`);
          additions.push({name:file.name, text});
        }
        if ([...files,...additions].reduce((total,file) => total + file.text.length, 0) > 50000) throw new Error('Attachments exceed 50,000 characters in total.');
        files.push(...additions); renderAttachments();
      } catch (error) { notice(error.message); }
      event.target.value = '';
    };
    node('alicia-video-retry').onclick = () => { unlock(); void start(session(), new AbortController().signal); };
    node('alicia-type').onclick = type;
    node('alicia-live').onchange = () => { if (!node('alicia-live').checked) void stop(); };
    node('alicia-rating').onchange = () => { node('alicia-correction').required = node('alicia-rating').value === 'needs_work'; };
    node('alicia-improve').ontoggle = async () => {
      if (!node('alicia-improve').open || !session()) return;
      const select = node('alicia-turn'); select.replaceChildren(new Option('Choose an answered reply', ''));
      try {
        const response = await fetch(`/api/session/${session()}`);
        if (!response.ok) throw new Error();
        const data = await response.json();
        const turns = (data.turns || data.session?.turns || []).filter(t => t.role === 'brutus' && !t.meta?.thinking && t.meta?.central_turn_id);
        for (const turn of turns.slice(-12).reverse()) select.add(new Option(turn.text.slice(0, 120), String(turn.id)));
        node('alicia-feedback-status').textContent = turns.length ? 'Choose a reply to review.' : 'An answered turn from the shared brain is needed first.';
      } catch (_) { node('alicia-feedback-status').textContent = 'Could not load replies. Close and reopen to retry.'; }
    };
    node('alicia-feedback').onsubmit = async event => {
      event.preventDefault();
      const button = node('alicia-feedback-save'); button.disabled = true;
      try {
        const response = await fetch(`/api/session/${session()}/feedback`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({turn_id:Number(node('alicia-turn').value), dimension:node('alicia-dimension').value, rating:node('alicia-rating').value, correction:node('alicia-correction').value})});
        if (!response.ok) throw new Error();
        node('alicia-feedback-status').textContent = 'Saved to Alicia’s shared improvement record.';
      } catch (_) { node('alicia-feedback-status').textContent = 'Feedback was not saved. Your correction is still here; retry.'; }
      finally { button.disabled = false; }
    };
    window.addEventListener('pagehide', end);
  }
  return {start, stop, interrupt, play, bind, unlock, busy, phase, isBusy, notice, clearAttachments, attachments: () => files.map(file => ({...file}))};
})();
