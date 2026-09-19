/* Presentation adapter only. Transcript, tools and reasoning belong to the shared brain. */
window.alexis = (() => {
  let client = null, generation = 0, audioStream = null, playback = 0;
  const node = id => document.getElementById(id);
  const status = text => { node('alexis-status').textContent = text; };
  function portrait() { node('alexis-video').hidden = true; node('alexis-portrait').hidden = false; }
  async function stop() {
    generation++; playback++;
    const old = client; client = null; audioStream = null;
    portrait();
    const video = node('alexis-video'); video.pause(); video.srcObject = null;
    status('Conversation ended · microphone off');
    if (old) await old.stopStreaming().catch(() => {});
  }
  function interrupt() {
    playback++;
    try { client?.interruptPersona(); audioStream?.endSequence(); } catch (_) {}
  }
  async function start(sessionId, signal) {
    if (!node('alexis-live').checked) { status('Voice only · same Alexis'); return false; }
    await stop();
    const gen = generation;
    status('Connecting Alexis…');
    let candidate;
    try {
      const response = await fetch(`/api/session/${sessionId}/alexis-token`, {method:'POST', signal});
      if (!response.ok) throw new Error('Avatar unavailable');
      const grant = await response.json();
      const {createClient, AnamEvent} = await import('https://esm.sh/@anam-ai/js-sdk@4.27.0');
      if (signal.aborted || gen !== generation) return false;
      candidate = createClient(grant.sessionToken, {disableInputAudio:true, metrics:{disableClientMetrics:true}});
      client = candidate;
      candidate.addListener(AnamEvent.CONNECTION_CLOSED, () => {
        if (client !== candidate) return;
        client = null; audioStream = null; portrait(); status('Video disconnected · voice remains available');
      });
      const video = node('alexis-video');
      video.onloadeddata = () => {
        if (client !== candidate || gen !== generation) return;
        video.hidden = false; node('alexis-portrait').hidden = true; status('Alexis · live');
      };
      await candidate.streamToVideoElement('alexis-video');
      if (signal.aborted || gen !== generation) { await candidate.stopStreaming(); return false; }
      audioStream = candidate.createAgentAudioInputStream({encoding:'pcm_s16le',sampleRate:16000,channels:1});
      return true;
    } catch (error) {
      if (candidate) await candidate.stopStreaming().catch(() => {});
      if (gen === generation) {
        client = null; audioStream = null; portrait();
        status(signal.aborted ? 'Conversation ended' : 'Video unavailable · continuing with voice');
      }
      return false;
    }
  }
  async function play(blob, signal) {
    if (!client || !audioStream) return false;
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
  function bind({session, end, type}) {
    node('alexis-end').onclick = end;
    node('alexis-type').onclick = type;
    node('alexis-live').onchange = () => { if (!node('alexis-live').checked) void stop(); };
    node('alexis-rating').onchange = () => { node('alexis-correction').required = node('alexis-rating').value === 'needs_work'; };
    node('alexis-improve').ontoggle = async () => {
      if (!node('alexis-improve').open || !session()) return;
      const select = node('alexis-turn'); select.replaceChildren(new Option('Choose an answered reply', ''));
      try {
        const response = await fetch(`/api/session/${session()}`);
        if (!response.ok) throw new Error();
        const data = await response.json();
        const turns = (data.turns || data.session?.turns || []).filter(t => t.role === 'brutus' && !t.meta?.thinking && t.meta?.central_turn_id);
        for (const turn of turns.slice(-12).reverse()) select.add(new Option(turn.text.slice(0, 120), String(turn.id)));
        node('alexis-feedback-status').textContent = turns.length ? 'Choose a reply to review.' : 'An answered turn from the shared brain is needed first.';
      } catch (_) { node('alexis-feedback-status').textContent = 'Could not load replies. Close and reopen to retry.'; }
    };
    node('alexis-feedback').onsubmit = async event => {
      event.preventDefault();
      const button = node('alexis-feedback-save'); button.disabled = true;
      try {
        const response = await fetch(`/api/session/${session()}/feedback`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({turn_id:Number(node('alexis-turn').value), dimension:node('alexis-dimension').value, rating:node('alexis-rating').value, correction:node('alexis-correction').value})});
        if (!response.ok) throw new Error();
        node('alexis-feedback-status').textContent = 'Saved to Alexis’s shared improvement record.';
      } catch (_) { node('alexis-feedback-status').textContent = 'Feedback was not saved. Your correction is still here; retry.'; }
      finally { button.disabled = false; }
    };
    window.addEventListener('pagehide', end);
  }
  return {start, stop, interrupt, play, bind};
})();
