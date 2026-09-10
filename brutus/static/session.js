/* The work-queue screen.
 *
 * Three rules shape all of this:
 *
 *   The work is the screen. The queue owns the largest region; the transcript
 *   is a rail. Raw speech is the input, not the product, and a panel that is
 *   usually empty may not hold the best real estate on the page.
 *
 *   Results arrive, they are not polled. Everything below reacts to the SSE
 *   stream. A fetch only ever happens because you did something.
 *
 *   Voice and typing are the same turn. The mic produces text and posts it to
 *   the same endpoint the textarea does. Nothing downstream can tell them
 *   apart except a `channel` field kept for display.
 */

const $ = (sel) => document.querySelector(sel);

function applyTheme(theme) {
  const t = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try {
    localStorage.setItem("brutus.theme", t);
  } catch {
    /* private mode */
  }
  const btn = $("#theme-toggle");
  if (btn) {
    btn.setAttribute("aria-pressed", t === "light" ? "true" : "false");
    const lab = btn.querySelector(".label");
    if (lab) lab.textContent = t === "light" ? "Light" : "Dark";
  }
}

function initTheme() {
  let cur = "dark";
  try {
    cur = localStorage.getItem("brutus.theme") || "dark";
  } catch {
    /* ignore */
  }
  applyTheme(cur);
  $("#theme-toggle")?.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  });
}

const state = {
  sessionId: null,
  events: null,
  speaking: false,
  listening: false,
  recognizer: null,
  audio: null,
  audioUrl: null,
  voicePhase: "idle",
  voiceTransport: null,
  sayAbort: null,
  speechAbort: null,
  voiceStartAbort: null,
  livekitRoom: null,
  livekitAudio: new Set(),
  livekitStopping: false,
  muted: false,
  seenTurns: new Set(),
  fields: new Map(),
  pendingQuestion: null,
  enrollmentSamples: [],
  enrollmentRecording: false,
};

const audioCaptureDefaults = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: false,
  channelCount: 1,
};

/* --- owner voice enrollment ------------------------------------------- */

const ENROLLMENT_PROMPTS = [
  "Brutus, this is my owner voice enrollment sample one.",
  "I use Brutus to move important work forward deliberately.",
  "Only accept voice commands from me after verification succeeds.",
];

function encodeWav(chunks, sampleRate) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const out = new ArrayBuffer(44 + length * 2);
  const view = new DataView(out);
  const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) for (const value of chunk) {
    view.setInt16(offset, Math.max(-1, Math.min(1, value)) * 0x7fff, true);
    offset += 2;
  }
  return new Blob([out], { type: "audio/wav" });
}

function renderEnrollment() {
  const list = $("#enrollment-prompts");
  if (!list) return;
  list.textContent = "";
  ENROLLMENT_PROMPTS.forEach((prompt, index) => {
    const item = document.createElement("li");
    item.textContent = `${state.enrollmentSamples[index] ? "Recorded" : "Sample " + (index + 1)} — ${prompt}`;
    item.dataset.recorded = state.enrollmentSamples[index] ? "true" : "false";
    list.append(item);
  });
  $("#enrollment-record").disabled = state.enrollmentRecording || state.enrollmentSamples.length >= ENROLLMENT_PROMPTS.length;
  $("#enrollment-record").textContent = state.enrollmentRecording ? "Recording…" : "Record sample";
  $("#enrollment-submit").disabled = state.enrollmentSamples.length !== ENROLLMENT_PROMPTS.length || !$("#enrollment-consent").checked;
}

async function recordEnrollmentSample() {
  if (state.enrollmentRecording || state.enrollmentSamples.length >= ENROLLMENT_PROMPTS.length) return;
  state.enrollmentRecording = true;
  renderEnrollment();
  $("#enrollment-state").textContent = `Reading sample ${state.enrollmentSamples.length + 1} — speak now.`;
  let stream; let context;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: audioCaptureDefaults });
    context = new AudioContext();
    const chunks = [];
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (event) => chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    source.connect(processor); processor.connect(context.destination);
    await new Promise((resolve) => setTimeout(resolve, 4500));
    processor.disconnect(); source.disconnect();
    state.enrollmentSamples.push(encodeWav(chunks, context.sampleRate));
    $("#enrollment-state").textContent = state.enrollmentSamples.length === ENROLLMENT_PROMPTS.length
      ? "All samples recorded. Confirm consent, then enroll."
      : "Sample recorded. Record the next line.";
  } catch (err) {
    $("#enrollment-state").textContent = "Microphone access failed. Allow it, then record again.";
  } finally {
    stream?.getTracks().forEach((track) => track.stop());
    await context?.close();
    state.enrollmentRecording = false;
    renderEnrollment();
  }
}

async function submitEnrollment() {
  const button = $("#enrollment-submit");
  button.disabled = true;
  $("#enrollment-state").textContent = "Building your local speaker profile. This can take a minute the first time.";
  try {
    const body = new FormData();
    state.enrollmentSamples.forEach((sample, index) => body.append("samples", sample, `owner-${index + 1}.wav`));
    body.append("consent", "true");
    const response = await fetch("/api/voice-enrollment", { method: "POST", body });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `server said ${response.status}`);
    $("#enrollment-state").textContent = "Voice enrolled. Brutus will require this local owner profile for live voice.";
    const enrollmentLabel = $("#voice-enroll .label");
    if (enrollmentLabel) enrollmentLabel.textContent = "Voice enrolled";
  } catch (err) {
    $("#enrollment-state").textContent = `Enrollment did not finish — ${err.message}`;
    renderEnrollment();
  }
}

async function openVoiceEnrollment() {
  const dialog = $("#voice-enrollment");
  state.enrollmentSamples = [];
  renderEnrollment();
  dialog.showModal();
  $("#enrollment-state").textContent = "Checking enrollment…";
  try {
    const status = await fetch("/api/voice-enrollment").then((r) => r.json());
    $("#enrollment-state").textContent = status.enrolled
      ? "Your voice is already enrolled. Recording again replaces the local profile."
      : "Record all three samples to create your local owner profile.";
  } catch {
    $("#enrollment-state").textContent = "Could not check enrollment. You can still try recording.";
  }
}

/* --- session ------------------------------------------------------------ */

async function openSession() {
  // A direct session link is the handoff mechanism for a specific proposal or
  // voice task. It deliberately wins over the browser's previous session.
  const requested = new URLSearchParams(window.location.search).get("session");
  if (requested && /^[0-9a-f]{12}$/i.test(requested)) {
    const ok = await fetch(`/api/session/${requested}`).then((r) => r.ok).catch(() => false);
    if (ok) {
      sessionStorage.setItem("brutus.session", requested);
      return hydrate(requested);
    }
  }
  const saved = sessionStorage.getItem("brutus.session");
  if (saved) {
    const ok = await fetch(`/api/session/${saved}`).then((r) => r.ok).catch(() => false);
    if (ok) return hydrate(saved);
  }
  const r = await fetch("/api/session/open", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title: "" }),
  });
  const body = await r.json();
  sessionStorage.setItem("brutus.session", body.session_id);
  return hydrate(body.session_id);
}

/* Reloading is something a person does, not a thing that should cost them the
   conversation. Everything is rebuilt from the server snapshot. */
async function hydrate(sessionId) {
  if (state.sessionId && state.sessionId !== sessionId) teardownVoice();
  state.sessionId = sessionId;
  const snap = await fetch(`/api/session/${sessionId}`).then((r) => r.json());
  $("#conversation").innerHTML = "";
  state.seenTurns.clear();
  $("#intent-readback").textContent = "Start voice or give Brutus a direction.";
  $("#readback-label").textContent = "Brutus · ready";
  state.fields.clear();
  (snap.turns || []).forEach((t) => renderTurn(t, { animate: false }));
  restoreThinking(snap.turns || []);
  (snap.fields || []).forEach((f) => renderField(f, { animate: false }));
  (snap.artifacts || []).forEach(renderProposal);
  if (!(snap.turns || []).length) renderConversationEmpty();
  renderCounts();
  // A rebuilt transcript starts at the newest turn, not scrolled to the top.
  scrollToEnd({ force: true });
  connect(sessionId);
}

function renderConversationEmpty() {
  const empty = document.createElement("div");
  empty.className = "conversation-empty";
  empty.id = "conversation-empty";
  const title = document.createElement("h2");
  title.textContent = "Your voice is the work surface";
  const body = document.createElement("p");
  body.textContent = "Talk naturally. Brutus will judge the work and answer with one useful next move.";
  const action = document.createElement("button");
  action.type = "button";
  action.dataset.startVoice = "";
  action.textContent = "Start talking";
  action.addEventListener("click", () => $("#mic").click());
  empty.append(title, body, action);
  $("#conversation").append(empty);
}

function connect(sessionId) {
  if (state.events) state.events.close();
  const es = new EventSource(`/api/session/${sessionId}/events`);
  state.events = es;
  es.onopen = () => setLiveConnected(true);
  es.onerror = () => setLiveConnected(false);
  es.onmessage = (e) => {
    let event;
    try {
      event = JSON.parse(e.data);
    } catch {
      return;
    }
    handle(event);
  };
}

function handle(event) {
  switch (event.kind) {
    case "turn":
      renderTurn(event.turn);
      break;
    case "reply":
      if (event.spoken && !state.livekitRoom && state.voiceTransport !== "livekit") speak(event.spoken);
      break;
    case "answer":
      resolveThinking(event);
      // Speak the ANSWER, not only the acknowledgement. It used to arrive
      // silently by design, which meant conversational mode said "Ok." and then
      // nothing at all — the thing you actually asked for never reached the ear.
      if (event.spoken && !state.livekitRoom && state.voiceTransport !== "livekit") speak(event.spoken);
      break;
    case "thinking":
      renderThinking(event);
      if (state.listening || state.voiceTransport === "livekit") setVoicePhase("thinking");
      break;

    case "field":
      renderField(event.field);
      break;
    case "proposal":
    case "proposal_settled":
      renderProposal(event.artifact);
      break;
    case "idea":
      applyIdeaEvent(event);
      break;
  }
  renderCounts();
}

/* --- 1. conversation ---------------------------------------------------- */

/* Brutus's system prompt bans headers, tables and rules but explicitly keeps
   **bold** — so bold is the one thing that has to render, or every emphasised
   ticket id reads as literal asterisks. Split on the delimiter and build nodes;
   never assign innerHTML from model output. */
function renderText(el, text) {
  el.textContent = "";
  const parts = String(text ?? "").split(/(\*\*[^*]+\*\*)/g);
  for (const part of parts) {
    if (!part) continue;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      const strong = document.createElement("strong");
      strong.textContent = part.slice(2, -2);
      el.append(strong);
    } else {
      el.append(document.createTextNode(part));
    }
  }
}

function setLiveConnected(on) {
  const el = $("#live");
  if (!el) return;
  el.setAttribute("data-connected", on ? "true" : "false");
  el.textContent = on ? "live" : "offline";
}

function renderTurn(turn, { animate = true, live = animate } = {}) {
  if (!turn || state.seenTurns.has(turn.id)) return;
  state.seenTurns.add(turn.id);
  clearInterim();
  if (turn.role !== "user") {
    const text = String(turn.text || "").trim();
    // A complete first sentence is a compact readback, never a chopped word stream.
    const sentence = text.match(/^[\s\S]*?[.!?](?=\s|$)/)?.[0] || text.split("\n")[0];
    renderText($("#intent-readback"), sentence);
    $("#readback-label").textContent = "Brutus · latest reply";
  } else {
    $("#readback-label").textContent = "Brutus · considering your direction";
  }
  const empty = $("#conversation-empty");
  if (empty) empty.hidden = true;

  const el = document.createElement("article");
  el.className = `turn ${turn.role === "user" ? "user" : "brutus"}`;
  el.dataset.turnId = turn.id;

  const who = document.createElement("div");
  who.className = "who";
  who.append(turn.role === "user" ? "You" : "Brutus");
  if (turn.channel === "voice") {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.append("spoken");
    who.append(badge);
  }

  const body = document.createElement("div");
  body.className = "body";
  renderText(body, turn.text);

  el.append(who, body);
  $("#conversation").append(el);
  // Only a turn arriving NOW may move the queue. `hydrate` replays the whole
  // transcript through here on every reload, so an ungated call re-applied the
  // focus from whatever ticket was last mentioned — the board came back showing
  // one row of six, behind a chip that is easy to miss.
  if (live) boardFollow(turn.text);
  if (animate) scrollToEnd();
}

/* What the recogniser thinks it heard, before it commits. Provisional on
   purpose — you can see it hearing you, and never mistake a guess for a fact. */
function showInterim(text) {
  let el = $("#interim");
  if (!el) {
    el = document.createElement("article");
    el.id = "interim";
    el.className = "turn user interim";
    el.innerHTML = '<div class="who">You</div><div class="body"></div>';
    $("#conversation").append(el);
  }
  el.querySelector(".body").textContent = text;
  scrollToEnd();
}

function clearInterim() {
  const el = $("#interim");
  if (el) el.remove();
}

/* Scrolling up to read something is a deliberate act, and an arriving turn used to
   undo it mid-sentence. Follow the tail only while already at the tail; otherwise
   hold the viewport still and offer a way back. */
const NEAR_BOTTOM_PX = 64;

function atBottom(box) {
  return box.scrollHeight - box.scrollTop - box.clientHeight <= NEAR_BOTTOM_PX;
}

function scrollToEnd({ force = false } = {}) {
  const box = $("#conversation");
  if (!box) return;
  if (force || atBottom(box)) {
    box.scrollTop = box.scrollHeight;
    showToLatest(false);
  } else {
    showToLatest(true);
  }
}

function showToLatest(show) {
  const btn = $("#to-latest");
  if (btn) btn.hidden = !show;
}

/* An empty transcript shrinks to its composer.
 *
 * This is the old screen's central mistake in miniature: a scroll area reserving
 * a fifth of the viewport for history that does not exist yet. The composer is
 * always worth its own height; the scrollback is only worth space once there is
 * something in it. */
function setConversationFilled() {
  const panel = document.querySelector(".voice-stage");
  if (panel) panel.classList.toggle("is-empty", state.seenTurns.size === 0);
}

/* --- session slots ------------------------------------------------------ */

function renderField(field, { animate = true } = {}) {
  if (!field) return;
  state.fields.set(field.name, field.value);
  const list = $("#fields");
  const empty = $("#fields-empty");
  if (empty) empty.hidden = true;

  let row = list.querySelector(`[data-field="${CSS.escape(field.name)}"]`);
  if (!row) {
    row = document.createElement("div");
    row.className = "field";
    row.dataset.field = field.name;
    row.innerHTML = "<dt></dt><dd></dd>";
    list.append(row);
  }
  row.querySelector("dt").textContent = field.name.replace(/_/g, " ");
  row.querySelector("dd").textContent = field.value;
  if (animate) {
    row.classList.remove("new");
    void row.offsetWidth; // restart the animation
    row.classList.add("new");
  }
}

function renderCounts() {
  $("#count-turns").textContent = state.seenTurns.size;
  $("#panel-fields-count").textContent = state.fields.size || "";
  setConversationFilled();
}

/* A rail panel costs nothing while it is empty. Showing "nothing here yet" in a
   permanent box is how Thinking and Proposed came to own 24.6% of the viewport
   between them without ever holding anything. */
function setRailPanel(panelId, hasContent) {
  const panel = $(panelId);
  if (panel) panel.hidden = !hasContent;
}

/* --- thinking ----------------------------------------------------------- */

/* A reload must not blank the Thinking panel. The deep answers are ordinary
   turns in the transcript, tagged with which question they answer, so the cards
   are rebuilt from the same source of truth rather than from live events. */
function restoreThinking(turns) {
  const byId = new Map(turns.map((t) => [t.id, t]));
  const answers = turns.filter((t) => t.meta && t.meta.lane === "deep" && t.meta.answers_turn);
  for (const answer of answers) {
    const asked = byId.get(answer.meta.answers_turn);
    renderThinking({ question: asked ? asked.text : "", turn_id: answer.meta.answers_turn });
    resolveThinking({ answers_turn: answer.meta.answers_turn, turn: answer });
  }
}

function renderThinking(event) {
  const card = document.createElement("div");
  card.className = "thinking";
  card.dataset.answers = event.turn_id;
  card.innerHTML =
    '<div class="q"></div>' +
    '<div class="working"><span class="dot"></span><span>Thinking…</span></div>';
  card.querySelector(".q").textContent = event.question;
  const empty = $("#thinking-empty");
  if (empty) empty.hidden = true;
  $("#thinking").prepend(card);
  setRailPanel("#thinking-panel", true);
  state.pendingQuestion = event.turn_id;
}

function resolveThinking(event) {
  const card = $(`#thinking .thinking[data-answers="${event.answers_turn}"]`);
  if (card) {
    card.classList.add("done");
    const answer = document.createElement("div");
    answer.className = "answer";
    renderText(answer, event.turn?.text || "");
    card.append(answer);

    // The full answer is in the conversation rail where it is readable. This is
    // a pointer to it, not a second copy fighting for a narrow column.
    const turnId = event.turn?.id;
    if (turnId) {
      const jump = document.createElement("button");
      jump.type = "button";
      jump.className = "jump";
      jump.textContent = "read it in the conversation";
      jump.addEventListener("click", () => {
        const target = document.querySelector(`[data-turn-id="${CSS.escape(String(turnId))}"]`);
        if (!target) return;
        $("#conversation-details").open = true;
        target.scrollIntoView({ block: "center", behavior: "smooth" });
        target.classList.remove("jumped");
        void target.offsetWidth;
        target.classList.add("jumped");
      });
      card.append(jump);
    }
  }
  state.pendingQuestion = null;
}

/* --- proposed writes ----------------------------------------------------
 *
 * A write is shown before it happens, and approving it runs the STORED object.
 * The buttons send no payload — nothing the page can say changes what executes,
 * so a stale tab cannot approve something it was never shown.
 */

const SETTLED_WORDS = {
  executed: "done",
  failed: "failed",
  rejected: "cancelled",
  cancelled: "cancelled",
};

function renderProposal(artifact) {
  if (!artifact) return;
  const empty = $("#proposals-empty");
  if (empty) empty.hidden = true;
  const host = $("#proposals");
  let card = host.querySelector(`[data-artifact="${CSS.escape(artifact.id)}"]`);
  if (!card) {
    card = document.createElement("div");
    card.className = "proposal";
    card.dataset.artifact = artifact.id;
    host.prepend(card);
  }
  card.dataset.state = artifact.state;
  card.textContent = "";

  const what = document.createElement("p");
  what.className = "what";
  what.textContent = artifact.summary || artifact.tool;
  card.append(what);

  // The exact call, spelled out. This is the object that runs.
  const args = document.createElement("dl");
  args.className = "args";
  for (const [k, v] of Object.entries(artifact.args || {})) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = String(v);
    args.append(dt, dd);
  }
  card.append(args);

  if (artifact.state === "draft") {
    const row = document.createElement("div");
    row.className = "actions";
    const yes = document.createElement("button");
    yes.className = "primary";
    yes.textContent = "Do it";
    yes.addEventListener("click", () => settleProposal(artifact.id, "approve", row));
    const no = document.createElement("button");
    no.textContent = "Cancel";
    no.addEventListener("click", () => settleProposal(artifact.id, "reject", row));
    row.append(yes, no);
    card.append(row);
  } else {
    const settled = document.createElement("p");
    settled.className = "settled";
    settled.textContent = SETTLED_WORDS[artifact.state] || artifact.state;
    card.append(settled);
  }

  setRailPanel("#proposals-panel", true);
  const pending = host.querySelectorAll('.proposal[data-state="draft"]').length;
  $("#proposals-count").textContent = pending || "";
}

async function settleProposal(artifactId, decision, row) {
  row.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    const response = await fetch(`/api/session/${state.sessionId}/artifact/${artifactId}/${decision}`, {
      method: "POST",
    });
    if (!response.ok) throw new Error(`server said ${response.status}`);
    // The server publishes the settled event, but an EventSource may reconnect
    // while a slow approved action is running. Land the returned result here as
    // well so "Do it" can never remain disabled after the write completed.
    const result = await response.json();
    const artifact = result.artifact || null;
    if (artifact) renderProposal(artifact);
    else await hydrate(state.sessionId);
  } catch (err) {
    setStatus(`Couldn't ${decision} that — ${err.message}`);
    row.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
}

/* --- saying things ------------------------------------------------------ */

async function say(message, channel) {
  const text = (message || "").trim();
  if (!text || !state.sessionId) return;
  clearInterim();
  state.sayAbort?.abort();
  const controller = new AbortController();
  state.sayAbort = controller;
  if (channel === "voice") setVoicePhase("thinking");
  $("#send").disabled = true;
  try {
    const r = await fetch(`/api/session/${state.sessionId}/say`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: text, channel }),
      signal: controller.signal,
    });
    // A non-2xx used to sail straight through as success: the box had already been
    // cleared by the submit handler, so the message simply vanished with no reply
    // and no error. Only a network throw was ever caught.
    if (!r.ok) throw new Error(`server said ${r.status}`);
  } catch (err) {
    if (err.name === "AbortError") return;
    setStatus(`Couldn't send that — ${err.message}. Your text is back in the box.`);
    // Hand the words back rather than eating them.
    const box = $("#say");
    if (box && !box.value.trim()) {
      box.value = text;
      autoGrow(box);
      box.focus();
    }
  } finally {
    if (state.sayAbort === controller) state.sayAbort = null;
    $("#send").disabled = false;
  }
}

/* rows="1" with a max-height and no resize logic meant a long message scrolled
   inside a one-line box while the allowed height went unused. */
function autoGrow(box) {
  if (!box) return;
  box.style.height = "auto";
  box.style.height = `${box.scrollHeight}px`;
}

/* --- voice in ----------------------------------------------------------- */
/* Grammar over dictation while Brutus is speaking: the synthesised voice can
   pronounce the app's own words, so during playback only the transport family
   is honoured. Everything else is ignored rather than sent. */

/* Playback-scoped grammar. Stop and frustration words cut playback; neither
 * becomes a fabricated user request. */
const STOP_WORDS = /\b(stop|mute|quiet|cancel|shut up)\b/i;
const FRUSTRATION_WORDS =
  /^\s*(?:(?:oh|fuck|god|holy|jesus)\s+)*(?:shit|fuck|damn|goddamn|crap|enough|shut up|be quiet|quiet|stop talking|jesus|christ)(?:\s+(?:man|dude|already|please))?[!?.]*\s*$/i;

/* Continuous recognition emits finals as scraps ("hey Brutus", "can you tell
 * me", "for what"). Buffer ~1s of silence, then send one concatenated utterance.
 * STOP / FRUSTRATION still fire immediately so barge-in works. */
const UTTERANCE_SILENCE_MS = 1000;
let utteranceParts = [];
let utteranceTimer = null;

function clearUtteranceBuffer() {
  utteranceParts = [];
  if (utteranceTimer) {
    clearTimeout(utteranceTimer);
    utteranceTimer = null;
  }
}

function flushUtterance() {
  utteranceTimer = null;
  const text = utteranceParts.join(" ").replace(/\s+/g, " ").trim();
  utteranceParts = [];
  if (text) say(text, "voice");
}

function queueVoiceFinal(text) {
  const part = String(text || "").trim();
  if (!part) return;
  utteranceParts.push(part);
  if (utteranceTimer) clearTimeout(utteranceTimer);
  utteranceTimer = setTimeout(flushUtterance, UTTERANCE_SILENCE_MS);
}

/* Everything Brutus has said out loud recently.
 *
 * The mute-while-speaking guard was not enough and the transcript proves it:
 * Brutus said "Let me think about that one.", the mic heard ITSELF, the phrase
 * came back as a user turn, and it answered its own acknowledgement — 17 times
 * in one session. Recognition results are delivered with lag, so by the time a
 * final result arrives the audio has stopped and `speaking` is already false.
 *
 * Timing guards cannot fix that. Content can: a transcript that matches
 * something we just said is our own voice, whatever the clock says. */
const spokenRecently = [];
const SPOKEN_MEMORY_MS = 30000;

const normalise = (s) =>
  String(s || "").toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim();

function rememberSpoken(text) {
  const n = normalise(text);
  if (n) spokenRecently.push({ text: n, at: Date.now() });
}

function isOurOwnVoice(heard) {
  const n = normalise(heard);
  if (!n) return false;
  const now = Date.now();
  while (spokenRecently.length && now - spokenRecently[0].at > SPOKEN_MEMORY_MS) {
    spokenRecently.shift();
  }
  // Substring either way: the recogniser clips and pads, so "let me think about"
  // and "let me think about that one" are the same event.
  return spokenRecently.some(
    (s) => s.text.includes(n) || n.includes(s.text),
  );
}

function supportsSpeech() {
  return "webkitSpeechRecognition" in window || "SpeechRecognition" in window;
}

const LIVEKIT_CLIENT_URL = "https://esm.sh/livekit-client@2.15.13";

async function startVoice() {
  state.voiceStartAbort?.abort();
  const controller = new AbortController();
  state.voiceStartAbort = controller;
  setVoicePhase("buffering", "Connecting voice…");
  try {
    const response = await fetch(`/api/session/${state.sessionId}/voice-token`, {
      method: "POST",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`voice service said ${response.status}`);
    const grant = await response.json();
    if (!grant.enabled || !grant.url || !grant.token) {
      if (grant.owner_enrollment_required) {
        return setVoicePhase("error", grant.reason || "Enroll your voice before starting live voice.");
      }
      return startLegacyVoice();
    }
    const livekit = await import(LIVEKIT_CLIENT_URL);
    // Suppress the easy part of speaker bleed before it leaves the browser:
    // echo from Brutus's own TTS, steady background noise, and aggressive gain
    // pumping. This is not an identity check; owner-only command acceptance is
    // enforced separately at the voice worker once a local voiceprint exists.
    const room = new livekit.Room({
      adaptiveStream: true,
      dynacast: true,
      audioCaptureDefaults,
    });
    state.livekitRoom = room;
    // Claim spoken output before connecting. A fast reply event during the
    // handshake must not start browser TTS while LiveKit is about to speak.
    state.voiceTransport = "livekit";
    state.livekitStopping = false;
    room.on(livekit.RoomEvent.TrackSubscribed, (track) => {
      if (track.kind !== livekit.Track.Kind.Audio && track.kind !== "audio") return;
      const element = track.attach();
      element.autoplay = true;
      element.muted = state.muted;
      element.hidden = true;
      element.setAttribute("aria-hidden", "true");
      document.body.append(element);
      state.livekitAudio.add(element);
      setVoicePhase("buffering", "Preparing reply…");
    });
    room.on(livekit.RoomEvent.TrackUnsubscribed, (track) => {
      for (const element of track.detach()) {
        state.livekitAudio.delete(element);
        element.remove();
      }
    });
    room.on(livekit.RoomEvent.ActiveSpeakersChanged, (participants) => {
      if (participants.some((p) => !p.isLocal)) {
        setVoicePhase("speaking");
      } else {
        for (const element of state.livekitAudio) element.muted = state.muted;
        setVoicePhase("listening");
      }
    });
    room.on(livekit.RoomEvent.Reconnecting, () => setVoicePhase("buffering", "Reconnecting voice…"));
    room.on(livekit.RoomEvent.Reconnected, () => setVoicePhase("listening"));
    room.on(livekit.RoomEvent.Disconnected, () => {
      if (!state.livekitStopping) setVoicePhase("error", "Voice disconnected. Tap Talk to reconnect.");
    });
    await room.connect(grant.url, grant.token);
    await room.localParticipant.setMicrophoneEnabled(true);
    state.listening = true;
    setVoicePhase("listening");
  } catch (err) {
    if (err.name === "AbortError") return;
    await teardownLiveKit();
    state.voiceTransport = null;
    // Once owner-only voice is enabled, browser recognition is an unauthenticated
    // bypass. It must fail closed instead of quietly changing transports.
    const enrolled = await fetch("/api/voice-enrollment").then((r) => r.ok ? r.json() : {}).catch(() => ({}));
    if (enrolled.enrolled) return setVoicePhase("error", "Owner voice is unavailable. Try live voice again; browser fallback is disabled for safety.");
    startLegacyVoice("Live voice unavailable — using this browser’s microphone.");
  } finally {
    if (state.voiceStartAbort === controller) state.voiceStartAbort = null;
  }
}

function startLegacyVoice(note = "") {
  state.voiceTransport = "legacy";
  if (!supportsSpeech()) return setVoicePhase("error", "Voice is unavailable in this browser.");
  startListening();
  if (note) setStatus(note);
}

function startListening() {
  if (!supportsSpeech()) return;
  const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new Ctor();
  rec.continuous = true;
  rec.interimResults = true;
  rec.lang = "en-US";

  rec.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i += 1) {
      const result = e.results[i];
      const text = result[0].transcript;
      if (!result.isFinal) {
        interim += text;
        continue;
      }
      // Barge-in and frustration fire immediately — do not wait for silence.
      if ((state.voicePhase === "speaking" || state.voicePhase === "buffering") &&
          (STOP_WORDS.test(text) || FRUSTRATION_WORDS.test(text))) {
        clearUtteranceBuffer();
        stopSpeaking();
        continue;
      }
      if (state.speaking) {
        // Playback-scoped: ignore non-barge-in while TTS is up.
        continue;
      }
      // ...and after playback too, because results arrive late.
      if (isOurOwnVoice(text)) {
        setStatus("(ignored my own voice)");
        continue;
      }
      queueVoiceFinal(text);
    }
    if (interim) showInterim(interim);
  };

  // Engines end after a result. Re-arm, or "stop" stops landing.
  rec.onend = () => {
    if (state.listening) {
      try {
        rec.start();
      } catch {
        /* already starting */
      }
    }
  };
  rec.onerror = (e) => {
    if (e.error === "not-allowed") {
      state.listening = false;
      setVoicePhase("error", "Microphone blocked. Allow it for this page and try again.");
    }
  };

  state.recognizer = rec;
  state.listening = true;
  rec.start();
  setVoicePhase("listening");
}

function stopListening() {
  state.listening = false;
  try {
    state.recognizer?.stop();
  } catch {
    /* not started */
  }
  state.recognizer = null; // or the next arm silently no-ops
  clearUtteranceBuffer();
  clearInterim();
  setVoicePhase("idle");
}

/* --- voice out ---------------------------------------------------------- */

async function speak(text) {
  if (state.muted || !text) return;
  state.speechAbort?.abort();
  const controller = new AbortController();
  state.speechAbort = controller;
  setVoicePhase("buffering");
  try {
    const r = await fetch("/api/speak", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
    if (!r.ok) throw new Error(`speech service said ${r.status}`);
    const url = URL.createObjectURL(await r.blob());
    stopSpeaking();
    const audio = new Audio(url);
    state.audio = audio;
    state.audioUrl = url;
    state.speaking = true;
    setVoicePhase("speaking");
    audio.onended = () => {
      URL.revokeObjectURL(url);
      state.speaking = false;
      state.audio = null;
      state.audioUrl = null;
      setVoicePhase(state.listening ? "listening" : "idle");
    };
    await audio.play();
    rememberSpoken(text);
  } catch (err) {
    state.speaking = false;
    if (err.name !== "AbortError") setVoicePhase("error", "Couldn’t play that reply. Tap Talk to try again.");
  } finally {
    if (state.speechAbort === controller) state.speechAbort = null;
  }
}

function stopSpeaking() {
  state.speechAbort?.abort();
  state.speechAbort = null;
  if (state.audio) {
    state.audio.pause();
    state.audio = null;
  }
  if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
  state.audioUrl = null;
  for (const element of state.livekitAudio) element.pause();
  state.speaking = false;
  setVoicePhase(state.listening ? "listening" : "idle");
}

async function teardownLiveKit() {
  const room = state.livekitRoom;
  state.livekitStopping = true;
  state.livekitRoom = null;
  for (const element of state.livekitAudio) {
    element.pause();
    element.remove();
  }
  state.livekitAudio.clear();
  if (room) {
    void room.localParticipant.setMicrophoneEnabled(false).catch(() => {});
    room.disconnect();
  }
  state.livekitStopping = false;
}

function teardownVoice() {
  state.voiceStartAbort?.abort();
  state.sayAbort?.abort();
  state.speechAbort?.abort();
  state.voiceStartAbort = state.sayAbort = state.speechAbort = null;
  stopSpeaking();
  stopListening();
  void teardownLiveKit();
  state.voiceTransport = null;
  setVoicePhase("idle");
}

function bargeIn() {
  state.sayAbort?.abort();
  stopSpeaking();
  if (state.voiceTransport === "livekit") {
    for (const element of state.livekitAudio) element.muted = true;
    state.listening = true;
    setVoicePhase("listening", "Listening — reply interrupted.");
  } else if (!state.listening) {
    startLegacyVoice();
  } else {
    setVoicePhase("listening", "Listening — reply interrupted.");
  }
}

/* One control, three states. The same button stops everything at every
   stage — never make someone hunt for a second control while it talks at
   them. State is carried by glyph shape AND label, never by colour alone. */
function setVoicePhase(phase, detail = "") {
  state.voicePhase = phase;
  const btn = $("#mic");
  if (!btn) return;
  btn.dataset.voiceState = phase;
  const glyph = btn.querySelector(".glyph");
  const label = btn.querySelector(".label");
  const stateLabel = $("#voice-state-label");
  const stateDetail = $("#voice-state-detail");
  const presence = document.querySelector(".voice-presence-ring");
  if (presence) presence.dataset.voiceState = phase;
  if (phase === "speaking") {
    glyph.textContent = "■";
    label.textContent = "Stop";
    btn.setAttribute("aria-label", "Stop reading");
    btn.setAttribute("aria-pressed", "true");
    if (stateLabel) stateLabel.textContent = "Brutus is speaking";
    if (stateDetail) stateDetail.textContent = "Reply in progress.";
    setStatus("Speaking…");
  } else if (phase === "thinking" || phase === "buffering") {
    glyph.textContent = "■";
    label.textContent = "Stop";
    btn.setAttribute("aria-label", phase === "thinking" ? "Cancel reply and listen" : "Cancel spoken reply and listen");
    btn.setAttribute("aria-pressed", "true");
    if (stateLabel) stateLabel.textContent = phase === "thinking" ? "Working the question" : "Joining the conversation";
    if (stateDetail) stateDetail.textContent = detail || (phase === "thinking" ? "Checking current evidence before answering." : "Connecting the live voice session.");
    setStatus(detail || (phase === "thinking" ? "Thinking…" : "Preparing reply…"));
  } else if (phase === "listening") {
    glyph.textContent = "●";
    label.textContent = "Listening";
    btn.setAttribute("aria-label", "Stop listening");
    btn.setAttribute("aria-pressed", "true");
    if (stateLabel) stateLabel.textContent = "Listening";
    if (stateDetail) stateDetail.textContent = detail || "Microphone open.";
    setStatus(detail || "Listening…");
  } else if (phase === "error") {
    glyph.textContent = "!";
    label.textContent = "Retry voice";
    btn.setAttribute("aria-label", "Voice commands; last attempt failed");
    btn.setAttribute("aria-pressed", "false");
    if (stateLabel) stateLabel.textContent = "Voice needs attention";
    if (stateDetail) stateDetail.textContent = detail || "The connection failed. Your conversation is still here.";
    setStatus(detail || "Voice failed. Tap Talk to retry.");
  } else {
    glyph.textContent = "◎";
    label.textContent = "Start voice";
    btn.setAttribute("aria-label", "Start voice conversation");
    btn.setAttribute("aria-pressed", "false");
    if (stateLabel) stateLabel.textContent = "Ready when you are";
    if (stateDetail) stateDetail.textContent = "Voice is off.";
    setStatus("");
  }
}

/* --- supervised work ---------------------------------------------------- */

let supervisorSnapshot = null;
let sessionLimit = 6;

function renderSupervisor(payload) {
  const sessions = payload.sessions || payload.agents || [];
  const counts = payload.counts || {};
  const assessment = payload.assessment || payload.intervention || null;
  const count = $("#supervisor-count");
  const detailCount = $("#supervisor-count-detail");
  const stateEl = $("#supervisor-state");
  const nextEl = $("#supervisor-next");
  const evidenceEl = $("#supervisor-evidence");
  const host = $("#supervisor-agents");
  const providerCounts = ["codex", "cursor", "claude"]
    .map((provider) => [provider, Number(counts[provider] || sessions.filter((s) => (s.surface || s.provider) === provider).length)])
    .filter(([, total]) => total > 0)
    .map(([provider, total]) => `${total} ${provider}`)
    .join(" · ");
  const newest = sessions[0] || null;
  const watched = assessment?.session || null;
  const watchedProvider = watched?.surface || watched?.provider || "agent";
  const watchedTitle = watched?.title || "Unnamed session";
  const watchedState = String(watched?.state || assessment?.intervention_type || "unknown").replaceAll("_", " ");
  const genericAction = /^(?:inspect the failing evidence|review the pending action|answer the blocking question|resume the session|let the session continue|complete or assign)/i;
  const liveCount = String(counts.live ?? sessions.filter((s) => s.live).length ?? "");
  if (count) count.textContent = liveCount;
  if (detailCount) detailCount.textContent = liveCount;
  if (stateEl) {
    stateEl.textContent = assessment?.should_intervene
      ? `${watchedProvider} · ${watchedTitle} · ${watchedState}`
      : (providerCounts || "No agent sessions found");
  }
  if (nextEl) {
    const action = String(assessment?.recommended_next_action || "").trim();
    const verified = Array.isArray(assessment?.verified_progress) ? assessment.verified_progress.filter(Boolean) : [];
    nextEl.textContent = assessment?.should_intervene
      ? (genericAction.test(action) ? (verified[0] || "No verified next step yet.") : action)
      : (newest ? `Most recent: ${newest.title || "Untitled session"} · ${String(newest.state || "unknown").replaceAll("_", " ")}` : "");
  }
  if (evidenceEl) {
    const evidence = Array.isArray(assessment?.evidence) ? assessment.evidence : [];
    const source = watched?.status_source || "";
    const age = watched?.age || "";
    evidenceEl.textContent = assessment?.should_intervene
      ? [source && `Status from ${source.replaceAll("_", " ")}`, age].filter(Boolean).join(" · ")
      : "";
  }
  if (!host) return;
  if (stateEl) stateEl.hidden = sessions.length > 0;
  if (nextEl) nextEl.hidden = sessions.length > 0;
  if (evidenceEl) evidenceEl.hidden = sessions.length > 0;
  supervisorSnapshot = payload;
  const ordered = [...sessions].sort((a, b) =>
    Number(Boolean(b.assessment?.should_intervene)) - Number(Boolean(a.assessment?.should_intervene)) ||
    Number(Boolean(b.live)) - Number(Boolean(a.live)));
  if (count) count.textContent = `${sessions.length} observed · ${counts.needs_attention || 0} need you`;
  if (detailCount) detailCount.textContent = `Showing ${Math.min(sessionLimit, sessions.length)} of ${sessions.length} observed sessions · local Claude, Cursor and OpenAI sources`;
  // Keep open details and keyboard focus stable across monitoring frames.
  const expanded = new Set([...host.querySelectorAll("details[open]")].map(el => el.dataset.sessionId));
  const focused = host.contains(document.activeElement) ? document.activeElement.closest("[data-session-id]")?.dataset.sessionId : null;
  host.textContent = "";
  for (const session of ordered.slice(0, sessionLimit)) {
    const a = session.assessment || {};
    const li = document.createElement("li");
    const detail = document.createElement("details");
    detail.dataset.sessionId = session.id;
    detail.open = expanded.has(session.id);
    const summary = document.createElement("summary");
    const title = document.createElement("strong");
    title.className = "agent-title";
    title.textContent = session.title || "Untitled session";
    const status = document.createElement("span");
    status.className = "agent-state";
    status.dataset.attention = String(Boolean(a.should_intervene));
    status.textContent = String(session.state || "unknown").replaceAll("_", " ");
    const intent = document.createElement("p");
    intent.className = "agent-intent";
    intent.textContent = a.goal && a.goal !== session.title ? a.goal : "Intent summary pending";
    const progress = document.createElement("p");
    progress.className = "agent-progress";
    const verified = Array.isArray(a.verified_progress) ? a.verified_progress.filter(Boolean) : [];
    progress.textContent = a.should_intervene ? a.blocker_or_decision :
      (verified[0] || (a.judgment_source === "model" ? a.recommended_next_action :
        (session.live ? "Active; waiting for a meaningful progress update." : "Current progress is not verified.")));
    const meta = document.createElement("span");
    meta.className = "agent-provider";
    const provider = session.surface === "codex" ? "OpenAI · Codex" : session.surface || session.provider || "Agent";
    meta.textContent = `${provider} · ${session.age || "update time unknown"}`;
    summary.append(title, status, intent, progress, meta);
    const body = document.createElement("div");
    body.className = "agent-expanded";
    const next = document.createElement("p");
    next.textContent = `Next: ${a.recommended_next_action || "No next action verified yet."}`;
    const evidence = document.createElement("p");
    evidence.textContent = `Source: ${session.status_source || "unknown"}. ${(a.evidence || []).join(" · ")}`;
    const discuss = document.createElement("button");
    discuss.type = "button";
    discuss.textContent = "Discuss with Brutus";
    discuss.addEventListener("click", () => say(
      `Focus our conversation on agent session ${session.id} (${session.title || "Untitled"}). Check its current evidence and briefly explain the intent it understood, progress, and what needs me.`, "text"));
    body.append(next, evidence, discuss);
    detail.append(summary, body);
    li.append(detail);
    host.append(li);
    if (focused === session.id) summary.focus({ preventScroll: true });
  }
  if (!sessions.length) host.textContent = "No sessions observed from the connected local sources.";
  const more = $("#sessions-more");
  if (more) more.hidden = sessions.length <= sessionLimit;

}

async function loadSupervisor({ force = false } = {}) {
  const stateEl = $("#supervisor-state");
  if (stateEl) stateEl.textContent = "Checking Claude, Cursor, and Codex…";
  try {
    const suffix = force ? "?force=true" : "";
    let response = await fetch(`/api/supervisor${suffix}`);
    if (response.status === 404) response = await fetch(`/api/agents${suffix}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderSupervisor(await response.json());
  } catch {
    if (stateEl) { stateEl.hidden = false; stateEl.textContent = "Couldn’t verify agent work right now. Session summaries below are last known, not current."; }
    const nextEl = $("#supervisor-next");
    if (nextEl) nextEl.textContent = "I won’t guess. Check again when the local session catalog is reachable.";
  }
}

function connectSupervisor() {
  const stream = new EventSource("/api/session/supervisor/events");
  stream.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.kind === "supervisor") renderSupervisor(payload);
    } catch {
      /* A malformed monitoring frame never breaks the conversation. */
    }
  };
}

const setMicState = () =>
  setVoicePhase(state.speaking ? "speaking" : state.listening ? "listening" : "idle");

function setStatus(text) {
  $("#status-line").textContent = text;
}

/* --- wiring ------------------------------------------------------------- */

function init() {
  $("#sessions-more")?.addEventListener("click", () => {
    sessionLimit += 12;
    if (supervisorSnapshot) renderSupervisor(supervisorSnapshot);
  });
  $("#correct-intent")?.addEventListener("click", () => {
    $("#readback-label").textContent = "Correct the intent · speak your correction";
    if (state.voicePhase !== "listening") {
      if (["thinking", "buffering", "speaking"].includes(state.voicePhase)) bargeIn();
      else startVoice();
    }
  });
  $("#mic")?.addEventListener("click", () => {
    if (["thinking", "buffering", "speaking"].includes(state.voicePhase)) return bargeIn();
    if (state.voicePhase === "listening") return teardownVoice();
    startVoice(); // first audio follows a gesture; never on load
  });

  $("#mute").addEventListener("click", (e) => {
    state.muted = !state.muted;
    if (state.muted) stopSpeaking();
    for (const element of state.livekitAudio) {
      element.muted = state.muted;
      if (!state.muted) void element.play().catch(() => {});
    }
    e.currentTarget.setAttribute("aria-pressed", String(state.muted));
    e.currentTarget.querySelector(".label").textContent = state.muted ? "Muted" : "Speaking on";
  });

  $("#supervisor-refresh")?.addEventListener("click", () => loadSupervisor({ force: true }));
  $("#work-tray")?.addEventListener("toggle", (event) => {
    const open = Boolean(event.currentTarget.open);
    document.body.classList.toggle("workspace-open", open);
    document.querySelector(".voice-shell")?.classList.toggle("workspace-open", open);
  });

  $("#composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const box = $("#say");
    const text = box.value;
    box.value = "";
    autoGrow(box);
    say(text, "text");
  });

  $("#say").addEventListener("input", (e) => autoGrow(e.currentTarget));

  $("#conversation").addEventListener(
    "scroll",
    () => {
      if (atBottom($("#conversation"))) showToLatest(false);
    },
    { passive: true },
  );

  $("#to-latest").addEventListener("click", () => {
    scrollToEnd({ force: true });
    $("#conversation").focus();
  });

  $("#say").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("#composer").requestSubmit();
    }
  });

  $("#new-session").addEventListener("click", async () => {
    if (
      !window.confirm(
        "Start a new session? This conversation leaves the screen.",
      )
    ) {
      return;
    }
    sessionStorage.removeItem("brutus.session");
    teardownVoice();
    await openSession();
  });

  $("#voice-enroll")?.addEventListener("click", openVoiceEnrollment);
  $("#enrollment-record")?.addEventListener("click", recordEnrollmentSample);
  $("#enrollment-submit")?.addEventListener("click", submitEnrollment);
  $("#enrollment-consent")?.addEventListener("change", renderEnrollment);

  $("#ideas-add")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const box = $("#idea-text");
    const text = (box.value || "").trim();
    if (!text) return;
    box.value = "";
    const btn = e.submitter || $("#ideas-add button[type=submit]");
    if (btn) btn.disabled = true;
    // Capture is the primary action. Put it in the queue before the request
    // returns so the screen acknowledges the thought at the moment it is
    // committed, not after a round trip to the local store.
    const pendingId = `pending:${Date.now()}`;
    upsertIdea({
      id: pendingId,
      text,
      raw: text,
      status: "todo",
      stage: "Captured",
      source: "typed",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      missing: [],
    }, { animate: true });
    try {
      const r = await fetch("/api/todos", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error(await r.text());
      const note = await r.json();
      ideasState.byId.delete(pendingId);
      upsertIdea(note, { animate: true });
      setStatus(`Captured — drafting a title for it now.`);
    } catch (err) {
      ideasState.byId.delete(pendingId);
      drawIdeas();
      setStatus(`Couldn't add that — ${err.message}`);
      box.value = text;
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  $("#ideas-search")?.addEventListener("input", (e) => {
    ideasState.query = String(e.target.value || "").trim().toLowerCase();
    drawIdeas();
  });

  $("#ideas-sort")?.addEventListener("change", (e) => {
    const [sort, dir] = String(e.target.value || "updated:desc").split(":");
    ideasState.sort = sort || "updated";
    ideasState.dir = dir === "asc" ? "asc" : "desc";
    drawIdeas();
  });

  $("#queue-show-done")?.addEventListener("change", (e) => {
    ideasState.showDone = Boolean(e.target.checked);
    loadIdeas();
  });

  $(".ideas-filters")?.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-filter]");
    if (!btn) return;
    ideasState.filter = btn.dataset.filter || "active";
    if (ideasState.filter === "focus") ideasState.filter = "active";
    for (const b of document.querySelectorAll(".ideas-filters button[data-filter]")) {
      const on = b === btn;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
    drawIdeas();
  });

  setMicState();
  setConversationFilled();
  initTheme();
  openSession();
  initIdeas();
  loadSupervisor();
  connectSupervisor();
}

window.addEventListener("pagehide", teardownVoice);

document.addEventListener("DOMContentLoaded", init);

/* --- the ledger feed ----------------------------------------------------
 *
 * Studio/Linear rows are cards in the same pipeline as Justin's own captures,
 * badged by where they came from. Two boards for "work that needs me" meant
 * checking two places and trusting neither.
 *
 * You still never navigate by mouse if you don't want to: say a ticket id and
 * the queue filters to it.
 */

const boardState = {
  rows: [],
  focus: null, // a ticket id, or null for everything
  recentlyMoved: new Set(),
  doorbellNote: false,
  error: null,
};

const LANES = [
  ["needs_you", "needs you"],
  ["working", "working"],
  ["stuck", "stuck"],
  ["queued", "queued"],
];

/* Where a ledger lane sits in Justin's pipeline. `needs_you` is Refining
   because that is literally what it is — an item that cannot progress until a
   question is answered, which is the same shape as a capture waiting on its
   missing pieces. */
const STAGE_FOR_LANE = {
  needs_you: "Refining",
  queued: "Ready",
  working: "Working",
  stuck: "Working",
};

function flattenBoard(board) {
  const out = [];
  for (const [lane, label] of LANES) {
    const value = board[lane];
    if (!Array.isArray(value)) continue;
    const rows =
      lane === "stuck"
        ? value.flatMap((g) => (g && Array.isArray(g.rows) ? g.rows : []))
        : value;
    for (const r of rows) {
      out.push({
        lane,
        laneLabel: label,
        stage: STAGE_FOR_LANE[lane] || "Ready",
        blocked: lane === "stuck",
        ticket: String(r.ticket || ""),
        title: String(r.title || ""),
        signal: String(r.signal || r.question || r.reason || ""),
        question: String(r.question || ""),
        reason: String(r.reason || r.why || ""),
        why: String(r.why || ""),
        whyFull: String(r.why_full || ""),
        age: String(r.age || ""),
        link: String(r.link || ""),
        verb: String(r.verb || ""),
        threadId: String(r.thread_id || ""),
        key: String(r.ticket || r.thread_id || r.title || ""),
      });
    }
  }
  return out;
}

function setQueueNote(message) {
  const el = $("#queue-note");
  if (!el) return;
  el.textContent = message || "";
  el.hidden = !message;
}

async function loadBoard() {
  try {
    const r = await fetch("/api/board");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const board = await r.json();
    boardState.rows = flattenBoard(board);
    boardState.error = null;
    $("#board-headline").textContent = board.headline || "";
    setQueueNote("");
    drawIdeas();
    if (boardState.focus) showLedgerDetail(boardState.focus);
  } catch {
    /* A ledger we can't reach is not a reason to break the queue — but it must
       never look like an empty ledger. */
    boardState.rows = [];
    boardState.error = "Couldn't reach Linear — retry in a moment. Your own captures are unaffected.";
    $("#board-headline").textContent = "";
    setQueueNote(boardState.error);
    drawIdeas();
    showLedgerDetail(null);
  }
}

function matchesFocus(row, ticket) {
  return row.ticket === ticket;
}

function focusBoard(ticket) {
  boardState.focus = ticket || null;
  const chip = $("#board-focus");
  if (ticket) {
    $("#board-focus-label").textContent = ticket;
    chip.hidden = false;
  } else {
    chip.hidden = true;
  }
  drawIdeas();
  showLedgerDetail(ticket);
}

function showLedgerDetail(ticket) {
  const panel = $("#ledger-detail");
  if (!panel) return;
  if (!ticket) {
    panel.hidden = true;
    return;
  }
  const row = boardState.rows.find((r) => r.ticket === ticket);
  if (!row) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("#ledger-detail-ticket").textContent = row.ticket || "—";
  $("#ledger-detail-title").textContent = row.title || "";
  const signal = row.question || row.reason || row.signal || "";
  const signalEl = $("#ledger-detail-signal");
  signalEl.textContent = signal;
  signalEl.hidden = !signal;
  $("#ledger-detail-age").textContent = row.age || "—";
  $("#ledger-detail-lane").textContent = row.laneLabel || "—";
  const link = $("#ledger-detail-link");
  if (row.link) {
    link.href = row.link;
    link.hidden = false;
  } else {
    link.removeAttribute("href");
    link.hidden = true;
  }
  renderLedgerActions(row);
}

function renderLedgerActions(row) {
  const host = $("#ledger-detail-actions");
  const hint = $("#ledger-detail-hint");
  if (!host) return;
  host.textContent = "";
  const needsYou = row.lane === "needs_you" || row.verb === "answer" || row.verb === "decide";
  if (!needsYou) {
    host.hidden = true;
    if (hint) {
      hint.hidden = false;
      hint.textContent = "This row has no gate on it — watch or open Linear.";
    }
    return;
  }
  host.hidden = false;
  if (hint) hint.hidden = true;

  if (row.verb === "answer") {
    const label = document.createElement("label");
    label.className = "sr-only";
    label.htmlFor = "ledger-answer";
    label.textContent = `Answer for ${row.ticket}`;
    const box = document.createElement("textarea");
    box.id = "ledger-answer";
    box.rows = 3;
    box.placeholder = "Your answer…";
    const send = document.createElement("button");
    send.type = "button";
    send.className = "primary";
    send.textContent = "Send answer";
    send.addEventListener("click", () => ledgerAnswer(row.ticket, box.value));
    host.append(label, box, send);
    return;
  }

  if (!row.threadId) {
    host.hidden = true;
    if (hint) {
      hint.hidden = false;
      hint.textContent = "No thread id for this gate — decide it on Ops.";
    }
    return;
  }
  const why = document.createElement("p");
  why.className = "ledger-detail-why";
  why.textContent = row.whyFull || row.why || row.reason || "Needs your call";
  const approve = document.createElement("button");
  approve.type = "button";
  approve.className = "primary";
  approve.textContent = "Approve";
  approve.addEventListener("click", () => ledgerDecide(row.threadId, false));
  const reject = document.createElement("button");
  reject.type = "button";
  reject.className = "danger";
  reject.textContent = "Reject";
  reject.addEventListener("click", () => {
    if (!window.confirm("Reject this? The bot will stop on this gate.")) return;
    ledgerDecide(row.threadId, true);
  });
  host.append(why, approve, reject);
}

async function ledgerAnswer(ticket, body) {
  const text = String(body || "").trim();
  if (!ticket || !text) return;
  try {
    const r = await fetch("/api/answer_input", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ticket_id: ticket, body: text }),
    });
    if (!r.ok) throw new Error(await r.text());
    setStatus(`Answered ${ticket}`);
    await loadBoard();
    showLedgerDetail(ticket);
  } catch (err) {
    setStatus(`Couldn't answer — ${err.message}`);
  }
}

async function ledgerDecide(threadId, reject) {
  if (!threadId) return;
  try {
    const r = await fetch(`/api/approve/${encodeURIComponent(threadId)}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reject: !!reject }),
    });
    if (!r.ok) throw new Error(await r.text());
    setStatus(reject ? "Rejected" : "Approved");
    focusBoard(null);
    await loadBoard();
  } catch (err) {
    setStatus(`Couldn't decide — ${err.message}`);
  }
}

/* Conversation drives navigation. Any ticket id in a turn — yours or Brutus's —
   filters the queue, and only if that ticket is actually in it. Saying "back" or
   "everything" widens out again. No clicking required, ever. */
const WIDEN = /\b(back|everything|all of it|whole board|whole queue|wide)\b/i;

function boardFollow(text) {
  if (!text) return;
  if (WIDEN.test(text)) return focusBoard(null);
  const spoken = String(text).toUpperCase().replace(/\bREV[\s-]*(\d+)/g, "REV-$1");
  const hits = spoken.match(/\b[A-Z]{2,5}-\d{1,5}\b/g);
  if (!hits) return;
  const known = new Set(boardState.rows.map((r) => r.ticket));
  const hit = hits.find((t) => known.has(t));
  if (hit) focusBoard(hit);
}

function applyBoardEvent(event) {
  for (const t of event.transitions || []) {
    if (t.ticket) boardState.recentlyMoved.add(t.ticket);
  }
  if (event.headline) $("#board-headline").textContent = event.headline;
  // Visual only. Never speak board deltas — UUIDs were read as "fee needs you"
  // and a leftover EventSource kept talking after the tab was closed.
  const needs = (event.transitions || []).filter((t) => t.kind === "needs_you");
  if (needs.length === 1) {
    boardState.doorbellNote = true;
    setQueueNote(`${humanBoardName(needs[0])} needs you`);
  } else if (needs.length > 1) {
    boardState.doorbellNote = true;
    setQueueNote(`${needs.length} things need you`);
  }
  loadBoard();
  // The flash is movement, not a state. Let it expire.
  setTimeout(() => {
    boardState.recentlyMoved.clear();
    if (boardState.doorbellNote) {
      boardState.doorbellNote = false;
      setQueueNote("");
    }
    drawIdeas();
  }, 4000);
}

function initBoard() {
  $("#board-clear").addEventListener("click", () => focusBoard(null));
  $("#ledger-detail-close")?.addEventListener("click", () => focusBoard(null));

  // The board publishes on the same bus as a conversation, under the reserved
  // id "board" — one stream mechanism for the whole screen, not two.
  const es = new EventSource("/api/session/board/events");
  es.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      if (event.kind === "board") applyBoardEvent(event);
    } catch {
      /* ignore a malformed frame */
    }
  };
  loadBoard();
}

document.addEventListener("DOMContentLoaded", initBoard);

/* --- the queue ----------------------------------------------------------
 *
 * Stage columns, left to right, in the order the store defines. Every stage
 * renders even when empty — a column that disappears when it empties makes the
 * pipeline unreadable, and "where did Ready go" is not a question a board
 * should ever raise.
 *
 * Cards are one line of title, one line of summary, one line of meta. That is
 * deliberate: the previous pad rendered whole paragraphs, so a single card was
 * 234px tall and exactly one of 143 items was visible at a time.
 */

const STAGES = ["Captured", "Refining", "Ready", "Working", "Done"];

const STAGE_BLURB = {
  Captured: "Raw. Waiting for a title.",
  Refining: "Drafted — confirm or fix it.",
  Ready: "Enough detail to start.",
  Working: "In flight.",
  Done: "Finished.",
};

/* Per column, not per board. A column caps its own list so one busy stage
   cannot push every other stage off the screen. Refining jammed at 148+;
   that column gets a tighter page so the rest of the board stays usable. */
const COLUMN_PAGE = 30;
const REFINING_PAGE = 12;
const REFINING_BURY_MS = 3 * 24 * 60 * 60 * 1000;
const DISPLAY_STAGES = ["Captured", "Needs answers", "Ready to confirm", "Ready", "Working", "Done"];

const ideasState = {
  byId: new Map(),
  query: "",
  filter: "active", // active | mine | ledger | all  (active = hide meeting dumps)
  sort: "updated", // updated | text
  dir: "desc", // asc | desc
  showDone: false,
  shownByStage: {},
  showOldRefining: false,
  confirmBulkReadyIds: null,
  editingId: null,
  openId: null,
  confirmDeleteId: null,
  loadError: null,
  unrefined: 0,
};

/** Zoom / 1:1 dumps land in the same store — hide them from Active by default. */
function isMeetingDump(text) {
  const t = String(text || "");
  return /\ba1OUG\b/i.test(t) || / — 20\d{2}-\d{2}-\d{2}\b/.test(t);
}

async function initIdeas() {
  const es = new EventSource("/api/session/ideas/events");
  es.onmessage = (e) => {
    try {
      applyIdeaEvent(JSON.parse(e.data));
    } catch {
      /* ignore a malformed frame */
    }
  };
  document.addEventListener("click", (e) => {
    if (e.target.closest("#ideas-list .idea")) return;
    if (ideasState.confirmDeleteId) {
      ideasState.confirmDeleteId = null;
      drawIdeas();
    }
  });
  await loadIdeas();
}

async function loadIdeas() {
  try {
    const r = await fetch(`/api/todos${ideasState.showDone ? "?include_done=true" : ""}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    ideasState.byId.clear();
    ideasState.loadError = null;
    ideasState.unrefined = Number(data.unrefined || 0);
    for (const note of data.todos || []) {
      ideasState.byId.set(note.id, note);
    }
    drawIdeas();
  } catch {
    ideasState.loadError = "Couldn't reach the queue.";
    drawIdeas();
  }
}

function applyIdeaEvent(event) {
  if (!event || (event.kind && event.kind !== "idea")) return;
  const action = event.action || (event.note ? "upsert" : "");
  if (action === "delete") {
    const id = event.note_id || event.note?.id;
    if (id) {
      ideasState.byId.delete(id);
      if (ideasState.editingId === id) ideasState.editingId = null;
      if (ideasState.openId === id) ideasState.openId = null;
      drawIdeas();
    }
    return;
  }
  if (event.note && event.note.id) {
    upsertIdea(event.note, { animate: true });
  }
}

function upsertIdea(note, { animate = false } = {}) {
  if (!note || !note.id) return;
  ideasState.byId.set(note.id, note);
  drawIdeas({ flashId: animate ? note.id : null });
}

/* Both sources become the same card shape, so one renderer draws the board and
   there is no second code path to drift. */
function todoCard(note) {
  const stage = STAGES.includes(note.stage) ? note.stage : "Captured";
  return {
    kind: "todo",
    id: note.id,
    stage,
    title: note.text || "",
    summary: note.summary || "",
    raw: note.raw || note.text || "",
    missing: Array.isArray(note.missing) ? note.missing : [],
    blocked: Boolean(note.blocked),
    ticket: note.promoted_ticket || "",
    source: note.source || "",
    refined: Boolean(note.refined_at),
    at: note.updated_at || note.created_at || "",
    note,
  };
}

function ledgerCard(row) {
  return {
    kind: "ledger",
    id: `ledger:${row.key}`,
    stage: row.stage,
    title: row.title || row.ticket,
    summary: row.signal || "",
    raw: row.whyFull || row.signal || "",
    missing: [],
    blocked: row.blocked,
    ticket: row.ticket,
    source: "ledger",
    refined: true,
    age: row.age,
    at: "",
    row,
  };
}

/* One piece of work, one card.
 *
 * Promoting a capture registers it in the ledger, and the ledger then feeds it
 * back — so the same job arrived twice, once as the note Justin dictated and
 * once as the thread tracking it. Fifteen of those pairs were on screen at the
 * same time, each pair disagreeing about state, because the note stopped
 * updating the moment the ledger took over. The ledger row wins: it is the copy
 * that knows the current lane, whether it is blocked, and why. */
function queueCards() {
  const claimed = new Set();
  for (const row of boardState.rows) {
    if (row.ticket) claimed.add(row.ticket);
    if (row.threadId) claimed.add(row.threadId);
    if (row.key) claimed.add(row.key);
  }

  const cards = [];
  for (const note of ideasState.byId.values()) {
    if (!ideasState.showDone && (note.stage === "Done" || note.status === "done")) continue;
    if (note.promoted_ticket && claimed.has(note.promoted_ticket)) continue;
    cards.push(todoCard(note));
  }
  for (const row of boardState.rows) cards.push(ledgerCard(row));
  return cards;
}

/* A ticket key is worth showing; an internal thread id is not.
 *
 * Promotion records whatever the ledger hands back, which is a REV-style key
 * when Linear has one and a bare UUID when it does not. The UUID was being
 * printed on the card as though it were a reference Justin could use — a line
 * of hex where a ticket number belongs, on cards whose ledger twin was showing
 * the real thing one column over. */
const TICKET_KEY = /^[A-Z][A-Z0-9]*-\d+$/;
function ticketLabel(ticket) {
  return TICKET_KEY.test((ticket || "").trim()) ? ticket.trim() : "";
}

function humanBoardName(t) {
  const labeled = ticketLabel(t && t.ticket);
  if (labeled) return labeled;
  const title = String((t && t.title) || "").trim();
  if (!title) return "Something on the board";
  const short = title.split("—")[0].split(" - ")[0].trim();
  return short.length > 72 ? `${short.slice(0, 72).replace(/\s+\S*$/, "")}` : short;
}

function cardJustMoved(card) {
  const moved = boardState.recentlyMoved;
  if (!moved.size) return false;
  const keys = [card.ticket, card.id, card.row && card.row.key, card.row && card.row.threadId];
  return keys.some((k) => k && moved.has(k));
}

function filterCards(cards) {
  const q = ideasState.query;
  let pool = cards;

  if (boardState.focus) {
    pool = pool.filter((c) => c.ticket === boardState.focus);
  }
  if (q) {
    // Search spans everything, including the verbatim capture — otherwise a
    // refined item is unreachable by the words actually spoken into it.
    return pool.filter((c) =>
      `${c.title} ${c.summary} ${c.raw} ${c.ticket}`.toLowerCase().includes(q),
    );
  }
  switch (ideasState.filter) {
    case "mine":
      return pool.filter((c) => c.kind === "todo");
    case "ledger":
      return pool.filter((c) => c.kind === "ledger");
    case "all":
      return pool;
    case "active":
    case "focus": // legacy localStorage / bookmarks
    default:
      return pool.filter((c) => !(c.kind === "todo" && isMeetingDump(c.raw)));
  }
}

function isOldRefining(card) {
  if (card.stage !== "Refining" || card.kind !== "todo") return false;
  const at = Date.parse(card.at || "");
  if (!Number.isFinite(at)) return false;
  return Date.now() - at > REFINING_BURY_MS;
}

function readyDraftIds(cards) {
  return cards
    .filter(
      (c) =>
        c.kind === "todo" &&
        c.stage === "Refining" &&
        !c.missing.length &&
        !c.blocked,
    )
    .map((c) => c.id);
}

/* “Refining” is how the store records a draft. It is not the job Justin has.
   Once a title exists, the only useful distinction is whether an answer is
   still needed or the draft is ready for an explicit confirmation. */
function displayStage(card) {
  if (card.stage !== "Refining") return card.stage;
  return card.missing.length || card.blocked ? "Needs answers" : "Ready to confirm";
}

function sortCards(cards) {
  const dir = ideasState.dir === "asc" ? 1 : -1;
  const key = ideasState.sort;
  return [...cards].sort((a, b) => {
    // Blocked first inside a stage, whatever the sort — a stalled item is the
    // one worth seeing, and burying it under a date sort hides the problem.
    if (a.blocked !== b.blocked) return a.blocked ? -1 : 1;
    const av = key === "text" ? a.title.toLowerCase() : a.at;
    const bv = key === "text" ? b.title.toLowerCase() : b.at;
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return 0;
  });
}

function drawIdeas({ flashId = null } = {}) {
  const host = $("#ideas-list");
  const empty = $("#ideas-empty");
  if (!host) return;

  if (ideasState.loadError) {
    const emptyHost = prepareQueueEmpty();
    if (emptyHost) {
      const title = document.createElement("strong");
      title.textContent = "The queue is unavailable.";
      const msg = document.createElement("p");
      msg.textContent = ideasState.loadError;
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "ideas-retry";
      retry.textContent = "Retry";
      retry.setAttribute("aria-label", "Retry loading the queue");
      retry.addEventListener("click", () => loadIdeas());
      emptyHost.append(title, msg, retry);
    }
    return;
  }

  const all = queueCards();
  const visible = sortCards(filterCards(all));
  const availableStages = DISPLAY_STAGES.filter((s) => ideasState.showDone || s !== "Done");

  const countEl = $("#ideas-count");
  if (countEl) countEl.textContent = visible.length ? `${visible.length}` : "";
  renderDrafting();

  if (!all.length) {
    renderQueueEmpty({
      title: "Nothing needs your attention yet.",
      body: "Capture the next thing on your mind. Brutus keeps the original words and drafts a useful title in the background.",
      action: "Capture your first item",
    });
    return;
  }
  if (!visible.length) {
    renderQueueEmpty({
      title: "Nothing matching this view.",
      body: boardState.focus
        ? `Nothing matches ${boardState.focus}. Clear focus to see the whole queue.`
        : ideasState.query
          ? "Try a different search, or clear it to see the full queue."
          : ideasState.filter === "active" || ideasState.filter === "focus"
            ? "Active is clear. Meeting dumps remain available under All."
            : "Choose another filter to see more work.",
      action: ideasState.query ? "Clear search" : "Capture an item",
      onAction: ideasState.query ? () => {
        ideasState.query = "";
        const search = $("#ideas-search");
        if (search) search.value = "";
        drawIdeas();
        search?.focus();
      } : () => $("#idea-text")?.focus(),
    });
    return;
  }
  if (empty) empty.hidden = true;

  const byStage = new Map(availableStages.map((s) => [s, []]));
  for (const card of visible) {
    const stage = displayStage(card);
    if (byStage.has(stage)) byStage.get(stage).push(card);
  }

  // Captured is transient. Keep it visible while it actually contains work,
  // but do not reserve a fifth column once the refiner has picked it up.
  const stages = availableStages.filter(
    (stage) => stage !== "Captured" || (byStage.get(stage) || []).length,
  );

  host.textContent = "";
  for (const stage of stages) {
    const cards = byStage.get(stage) || [];
    host.append(renderColumn(stage, cards, flashId));
  }
}

function renderQueueEmpty({ title, body, action, onAction } = {}) {
  const empty = prepareQueueEmpty();
  if (!empty) return;
  const heading = document.createElement("strong");
  heading.textContent = title || "Nothing here yet.";
  const copy = document.createElement("p");
  copy.textContent = body || "Capture the next thing to get started.";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "primary";
  button.textContent = action || "Capture an item";
  button.addEventListener("click", onAction || (() => $("#idea-text")?.focus()));
  empty.append(heading, copy, button);
}

function prepareQueueEmpty() {
  const host = $("#ideas-list");
  if (!host) return null;
  let empty = $("#ideas-empty");
  if (!empty) {
    empty = document.createElement("div");
    empty.id = "ideas-empty";
    empty.className = "queue-empty";
    empty.dataset.testid = "queue-empty";
  }
  empty.hidden = false;
  empty.replaceChildren();
  host.replaceChildren(empty);
  return empty;
}

function renderColumn(stage, cards, flashId) {
  const col = document.createElement("section");
  col.className = cards.length ? "qcol" : "qcol is-empty";
  col.dataset.stage = stage;
  col.setAttribute("aria-label", `${stage} — ${cards.length}`);

  let visibleCards = cards;
  let buried = [];
  const draftColumn = stage === "Needs answers" || stage === "Ready to confirm";
  if (draftColumn && !ideasState.showOldRefining && !ideasState.query) {
    buried = cards.filter(isOldRefining);
    visibleCards = cards.filter((c) => !isOldRefining(c));
  }

  const head = document.createElement("header");
  head.className = "qcol-head";
  const name = document.createElement("h3");
  name.textContent = stage;
  const n = document.createElement("span");
  n.className = "n";
  n.textContent = String(cards.length);
  head.append(name, n);
  col.append(head);

  if (stage === "Ready to confirm") {
    const readyIds = readyDraftIds(cards);
    if (ideasState.confirmBulkReadyIds) {
      const confirm = document.createElement("div");
      confirm.className = "qcol-confirm";
      const copy = document.createElement("p");
      copy.textContent = `Move ${readyIds.length} clear drafts to Ready? This changes only their stage.`;
      const accept = document.createElement("button");
      accept.type = "button";
      accept.className = "primary";
      accept.dataset.testid = "confirm-bulk-ready";
      accept.textContent = `Move ${readyIds.length} to Ready`;
      accept.addEventListener("click", () => {
        ideasState.confirmBulkReadyIds = null;
        bulkReady(readyIds);
      });
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", () => {
        ideasState.confirmBulkReadyIds = null;
        drawIdeas();
      });
      confirm.append(copy, accept, cancel);
      col.append(confirm);
    } else if (readyIds.length) {
      const bulk = document.createElement("button");
      bulk.type = "button";
      bulk.className = "qcol-bulk";
      bulk.dataset.testid = "confirm-clear-drafts";
      bulk.textContent = `Confirm ${readyIds.length} clear draft${readyIds.length === 1 ? "" : "s"}`;
      bulk.addEventListener("click", () => {
        ideasState.confirmBulkReadyIds = readyIds;
        drawIdeas();
      });
      col.append(bulk);
    }
  }

  const list = document.createElement("ul");
  list.className = "qcol-list";
  const pageSize = draftColumn ? REFINING_PAGE : COLUMN_PAGE;
  const shown = ideasState.shownByStage[stage] || pageSize;

  if (!visibleCards.length && !buried.length) {
    const li = document.createElement("li");
    li.className = "qcol-empty";
    li.textContent = STAGE_BLURB[stage] || "";
    list.append(li);
  }

  for (const card of visibleCards.slice(0, shown)) {
    list.append(renderCard(card, flashId));
  }
  col.append(list);

  if (visibleCards.length > shown) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "qcol-more";
    more.textContent = `${visibleCards.length - shown} more`;
    more.addEventListener("click", () => {
      ideasState.shownByStage[stage] = shown + pageSize;
      drawIdeas();
    });
    col.append(more);
  }

  if (buried.length && !ideasState.showOldRefining) {
    const old = document.createElement("button");
    old.type = "button";
    old.className = "qcol-more";
    old.textContent = `${buried.length} older draft${buried.length === 1 ? "" : "s"} (>3d)`;
    old.addEventListener("click", () => {
      ideasState.showOldRefining = true;
      drawIdeas();
    });
    col.append(old);
  }
  return col;
}

async function bulkReady(ids) {
  if (!ids.length) return;
  let ok = 0;
  for (const id of ids) {
    try {
      const note = await patchTodo(id, { stage: "Ready" });
      if (note && note.id) {
        ideasState.byId.set(note.id, note);
        ok++;
      }
    } catch {
      /* keep going */
    }
  }
  drawIdeas();
  setStatus(ok ? `Moved ${ok} draft${ok === 1 ? "" : "s"} to Ready.` : "Could not move drafts.");
}

function renderCard(card, flashId) {
  const li = document.createElement("li");
  li.className = "idea card";
  li.dataset.id = card.id;
  li.dataset.kind = card.kind;
  if (card.blocked) li.dataset.blocked = "true";
  if (flashId && card.id === flashId) li.classList.add("new");
  if (cardJustMoved(card)) li.classList.add("new");
  const isOpen = ideasState.openId === card.id;
  if (isOpen) li.classList.add("is-open");

  if (ideasState.editingId === card.id) {
    li.classList.add("editing");
    const box = document.createElement("textarea");
    box.className = "edit-box";
    box.rows = 3;
    box.value = card.title;
    box.setAttribute("aria-label", "Rename or edit this item");

    const actions = document.createElement("div");
    actions.className = "actions";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = "Save";
    save.addEventListener("click", () => ideaSaveEdit(card.id, box.value));
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => {
      ideasState.editingId = null;
      drawIdeas();
    });
    actions.append(save, cancel);
    li.append(box, actions);
    // Focus after mount, or the caret lands nowhere.
    requestAnimationFrame(() => {
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    });
    return li;
  }

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "card-open";
  toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");

  const title = document.createElement("span");
  title.className = "what";
  title.textContent = card.title;
  title.title = card.title;
  toggle.append(title);

  if (card.summary) {
    const sum = document.createElement("span");
    sum.className = "signal";
    sum.textContent = card.summary;
    sum.title = card.summary;
    toggle.append(sum);
  }

  const meta = document.createElement("span");
  meta.className = "meta";
  // Source is a word, never a colour: a ticket id if the ledger owns it,
  // otherwise how it was captured.
  const src = document.createElement("span");
  src.className = "src";
  src.textContent =
    ticketLabel(card.ticket) || (card.kind === "ledger" ? "ledger" : card.source || "typed");
  meta.append(src);
  if (card.blocked) {
    const flag = document.createElement("span");
    flag.className = "flag";
    flag.textContent = "blocked";
    meta.append(flag);
  }
  if (card.kind === "todo" && !card.refined) {
    const flag = document.createElement("span");
    flag.className = "flag drafting";
    flag.textContent = "no title yet";
    meta.append(flag);
  }
  if (card.missing.length) {
    const q = document.createElement("span");
    q.className = "flag questions";
    q.textContent = `${card.missing.length} open`;
    meta.append(q);
  }
  if (card.age) {
    const age = document.createElement("span");
    age.className = "age";
    age.textContent = card.age;
    meta.append(age);
  }
  toggle.append(meta);

  toggle.setAttribute(
    "aria-label",
    `${card.title}${card.summary ? ` — ${card.summary}` : ""}. ${
      card.kind === "ledger" ? "Ledger item" : "Your capture"
    }, ${card.stage}.`,
  );
  toggle.addEventListener("click", () => {
    if (card.kind === "ledger") return focusBoard(card.ticket || null);
    ideasState.openId = isOpen ? null : card.id;
    ideasState.confirmDeleteId = null;
    drawIdeas();
  });
  li.append(toggle);

  if (isOpen && card.kind === "todo") li.append(renderCardDetail(card));
  return li;
}

/* The expanded card is where refining actually happens: the verbatim capture,
   the open questions, and the one action that moves it forward. */
function renderCardDetail(card) {
  const box = document.createElement("div");
  box.className = "card-detail";

  if (card.raw && card.raw !== card.title) {
    const raw = document.createElement("p");
    raw.className = "card-raw";
    raw.textContent = card.raw;
    box.append(raw);
  }

  if (card.missing.length) {
    const label = document.createElement("p");
    label.className = "card-qlabel";
    label.textContent = "Still needs an answer";
    const list = document.createElement("ul");
    list.className = "card-questions";
    for (const q of card.missing) {
      const li = document.createElement("li");
      li.textContent = q;
      list.append(li);
    }
    box.append(label, list);
  }

  const actions = document.createElement("div");
  actions.className = "card-actions";

  // The confirm step. Justin chose auto-draft-then-confirm, so a drafted title
  // waits here rather than promoting itself into Ready.
  if (card.stage === "Refining") {
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "primary";
    ok.textContent = card.missing.length ? "Ready anyway" : "Looks right";
    ok.addEventListener("click", () => ideaStage(card.id, "Ready", "Moved to Ready."));
    actions.append(ok);
  } else if (card.stage === "Captured") {
    const now = document.createElement("button");
    now.type = "button";
    now.className = "primary";
    now.textContent = "Draft a title now";
    now.addEventListener("click", (e) => ideaRefine(card.id, e.currentTarget));
    actions.append(now);
  } else if (card.stage === "Ready") {
    const start = document.createElement("button");
    start.type = "button";
    start.className = "primary";
    start.textContent = "Start";
    start.addEventListener("click", () => ideaStage(card.id, "Working", "Moved to Working."));
    actions.append(start);
  }

  const edit = document.createElement("button");
  edit.type = "button";
  edit.textContent = "Edit";
  edit.setAttribute("aria-label", `Edit or rename: ${card.title}`);
  edit.addEventListener("click", () => {
    ideasState.editingId = card.id;
    ideasState.confirmDeleteId = null;
    drawIdeas();
  });
  actions.append(edit);

  if (card.stage !== "Done") {
    const done = document.createElement("button");
    done.type = "button";
    done.textContent = "Done";
    done.addEventListener("click", () => ideaStage(card.id, "Done", "Marked done."));
    actions.append(done);
  }

  const block = document.createElement("button");
  block.type = "button";
  block.textContent = card.blocked ? "Unblock" : "Blocked";
  block.setAttribute("aria-pressed", card.blocked ? "true" : "false");
  block.addEventListener("click", () => ideaBlocked(card.id, !card.blocked));
  actions.append(block);

  const promote = document.createElement("button");
  promote.type = "button";
  promote.textContent = "Promote";
  promote.disabled = Boolean(card.ticket);
  promote.title = card.ticket
    ? `Already on the ledger as ${ticketLabel(card.ticket) || "a tracked thread"}`
    : "Register on the ledger";
  promote.addEventListener("click", () => ideaPromote(card.id));
  actions.append(promote);

  const del = document.createElement("button");
  del.type = "button";
  del.className = "danger";
  del.textContent = "Delete";
  del.setAttribute("aria-label", `Delete: ${card.title}`);
  del.addEventListener("click", () => {
    ideasState.confirmDeleteId = card.id;
    drawIdeas();
  });
  actions.append(del);
  box.append(actions);

  if (ideasState.confirmDeleteId === card.id) {
    const confirm = document.createElement("div");
    confirm.className = "card-confirm";
    const label = document.createElement("span");
    label.className = "confirm-label";
    label.textContent = "Delete this? The verbatim capture goes too.";
    const yes = document.createElement("button");
    yes.type = "button";
    yes.className = "danger";
    yes.textContent = "Yes, delete";
    yes.addEventListener("click", () => ideaDelete(card.id));
    const no = document.createElement("button");
    no.type = "button";
    no.textContent = "Cancel";
    no.addEventListener("click", () => {
      ideasState.confirmDeleteId = null;
      drawIdeas();
    });
    confirm.append(label, yes, no);
    box.append(confirm);
  }

  return box;
}

/* How many captures are still waiting on a drafted title. The sweeper works
   through them one at a time, so this is a progress reading, not a warning. */
function renderDrafting() {
  const chip = $("#queue-drafting");
  const label = $("#queue-drafting-label");
  if (!chip || !label) return;
  const n = ideasState.unrefined;
  chip.hidden = n <= 0;
  label.textContent = n === 1 ? "drafting 1 title" : `drafting ${n} titles`;
}

/* --- queue writes ------------------------------------------------------- */

async function patchTodo(id, body) {
  const r = await fetch(`/api/todos/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function ideaSaveEdit(id, text) {
  const next = String(text || "").trim();
  if (!next) {
    setStatus("An item needs some words.");
    return;
  }
  try {
    const note = await patchTodo(id, { text: next });
    ideasState.editingId = null;
    upsertIdea(note);
    setStatus("Updated.");
  } catch (err) {
    setStatus(`Couldn't update that — ${err.message}`);
  }
}

async function ideaStage(id, stage, okStatus) {
  try {
    const note = await patchTodo(id, { stage });
    if (stage === "Done" && !ideasState.showDone) {
      ideasState.byId.delete(id);
      ideasState.openId = null;
      drawIdeas();
    } else {
      upsertIdea(note);
    }
    setStatus(okStatus);
  } catch (err) {
    setStatus(`Couldn't move that — ${err.message}`);
  }
}

async function ideaBlocked(id, blocked) {
  try {
    const note = await patchTodo(id, { blocked });
    upsertIdea(note);
    setStatus(blocked ? "Marked blocked." : "Unblocked.");
  } catch (err) {
    setStatus(`Couldn't update that — ${err.message}`);
  }
}

/* Jump the sweeper's queue for one item. The button reports that it is working
   because a cold model takes ~55s and a silent button reads as a dead one. */
async function ideaRefine(id, btn) {
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Drafting…";
  }
  try {
    const r = await fetch(`/api/todos/${id}/refine`, { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    const note = await r.json();
    upsertIdea(note);
    setStatus("Drafted a title.");
  } catch (err) {
    setStatus(`Couldn't draft that — ${err.message}`);
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Draft a title now";
    }
  }
}

async function ideaPromote(id) {
  try {
    const r = await fetch(`/api/todos/${id}/promote`, { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    const body = await r.json();
    const ticket = body.ticket || "registered";
    if (body.note) upsertIdea(body.note);
    else await loadIdeas();
    setStatus(`Promoted to the ledger — ${ticket}`);
  } catch (err) {
    setStatus(`Couldn't promote that — ${err.message}`);
  }
}

async function ideaDelete(id) {
  try {
    const r = await fetch(`/api/todos/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(await r.text());
    ideasState.byId.delete(id);
    if (ideasState.editingId === id) ideasState.editingId = null;
    if (ideasState.openId === id) ideasState.openId = null;
    ideasState.confirmDeleteId = null;
    drawIdeas();
    setStatus("Deleted.");
  } catch (err) {
    setStatus(`Couldn't delete that — ${err.message}`);
    drawIdeas();
  }
}
