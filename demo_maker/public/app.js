let voices = [];
let currentProject = null; // { id, name, description, output_format, segments: [] }
let draggedSegmentEl = null;
let autosaveTimer = null;
let autosaving = false;
let lastSavedSnapshot = null; // JSON of the editor state as last persisted
let undoStack = []; // deleted segments for the current project, most recent last
let redoStack = []; // undone deletions, available to re-apply

// Type-based authoring defaults. Pause values in STANDARD_PAUSES are treated
// as "not hand-tuned" and get swapped when the type changes; anything else is
// a deliberate per-segment choice and is left alone.
//
// The Type dropdown speaks in question CLASSES (nq/pq) rather than a generic
// "question", because that is the choice being made when authoring. Storage
// keeps role='question' + qtype, so the video builder and every saved project
// are unaffected.
const UI_TYPES = {
  nq: { role: 'question', qtype: 'NQ', answer: 'Yes' },
  pq: { role: 'question', qtype: 'PQ', answer: 'No' },
};
const uiTypeOf = (seg) => (seg.role === 'question' ? ((seg.qtype === 'NQ') ? 'nq' : 'pq') : (seg.role || 'other'));
const isQuestionType = (t) => t === 'nq' || t === 'pq';
// The stock answers, so switching NQ↔PQ can flip an untouched answer but never
// overwrite one that was edited by hand.
const STOCK_ANSWERS = ['Yes', 'No'];

const ROLE_PAUSE_DEFAULTS = { question: 1500, answer: 2000, transition: 1500, nq: 1500, pq: 1500 };
const STANDARD_PAUSES = [400, 1500, 2000];
const DEFAULT_INTRO_TEXT = 'You will now be asked some simple questions which only require accurate yes or no responses.\nBefore answering yes or no, please wait for the question to be completely finished.\nThe questions are about to begin\n';
const DEFAULT_CLOSING_TEXT = 'Please hold while the system processes your answers. Your responses were accepted for evaluation.  Thank you for your cooperation.';
const DEFAULT_TRANSITION_TEXT = 'You will now be asked the same questions a second time.  The questions are about to begin.';
const ROLE_TEXT_DEFAULTS = {
  answer: 'No', intro: DEFAULT_INTRO_TEXT, closing: DEFAULT_CLOSING_TEXT,
  transition: DEFAULT_TRANSITION_TEXT,
};
// Only answer segments get stock text on a type change; a question's text is
// always the author's, never a placeholder dropped in underneath them.
// Question counts each template supports; the server is the authority and
// rejects anything else, this just drives the picker.
const TEMPLATE_COUNTS = { sales: [3, 4, 5], production: [3, 4] };

const el = (id) => document.getElementById(id);

// Optional path prefix when Nucleus same-origin proxies this app
// (window.__DM_BASE__ = "/revops/demo-maker"). Empty string on Studio direct.
function dmBase() {
  const raw = typeof window !== 'undefined' ? window.__DM_BASE__ : '';
  const base = String(raw || '').replace(/\/+$/, '');
  return base;
}
function dmUrl(path) {
  if (!path || path.startsWith('http://') || path.startsWith('https://')) return path;
  if (!path.startsWith('/')) return path;
  return `${dmBase()}${path}`;
}

async function api(path, opts = {}) {
  const res = await fetch(dmUrl(path), {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

async function loadVoices() {
  voices = await api('/api/voices');
}

function uniqSorted(arr) {
  // "ElevenLabs" is a provider group, not a language — pin it to the top of
  // language dropdowns so those voices are impossible to miss.
  return [...new Set(arr)].sort((a, b) =>
    (a === 'ElevenLabs' ? -1 : b === 'ElevenLabs' ? 1 : a.localeCompare(b)));
}

// The player's own UI language. "Auto" reads the language of the voices actually
// speaking, which is what a translated project already carries — so this only
// needs setting when the voices lie about their language (every ElevenLabs voice
// reports en-US regardless of what it is saying).
function fillDemoLanguages(selected) {
  const sel = el('demoLanguage');
  const tags = [...new Set(voices.map((v) => v.language_code).filter(Boolean))];
  const label = (tag) => {
    const v = voices.find((x) => x.language_code === tag);
    return v && v.language && v.language !== 'ElevenLabs' ? `${v.language} — ${tag}` : tag;
  };
  const opts = tags.sort((a, b) => label(a).localeCompare(label(b)));
  // A hand-set tag that no cached voice uses must still survive a round-trip.
  if (selected && !opts.includes(selected)) opts.unshift(selected);
  sel.innerHTML = [`<option value="">Auto (from voices)</option>`]
    .concat(opts.map((t) => `<option value="${escapeHtml(t)}" ${t === selected ? 'selected' : ''}>${escapeHtml(label(t))}</option>`))
    .join('');
  sel.value = selected || '';
}

// clearspeeddemos.com library taxonomy: Salesforce Opportunity use cases,
// served by the library's /api/use-cases. This copy renders first;
// loadUseCases() replaces it with the live list. Same lists drive the project
// editor and the publish modal so a demo lands in the right shelf every time.
let LIB_TAXONOMY = {"Enterprise": ["Personal Auto - Claims", "Personal Property - Claims", "Personal Home - Claims", "Transactions", "Life & Health - Underwriting", "Life & Health - Claims", "Workers Comp", "Personal Auto - Underwriting", "Commercial - Underwriting", "Commercial - Claims", "HR", "Travel", "Device / Gadget", "AML / KYC", "Applications", "Personal Property - Underwriting", "Pet", "Personal Home - Underwriting"], "GDS": ["Screening & Vetting", "Insider Threat", "Sports Integrity", "Supply Chain Risk Management", "Physical Security"]};

async function loadUseCases() {
  try {
    const t = await api('/api/use-cases');
    if (Array.isArray(t?.Enterprise) && Array.isArray(t?.GDS)) LIB_TAXONOMY = t;
  } catch { /* keep the bundled list */ }
}

function fillDemoUseCases(selected) {
  const vSel = el('demoVertical');
  const ucSel = el('demoUseCase');
  const cases = LIB_TAXONOMY[vSel.value] || [];
  ucSel.innerHTML = (cases.length
    ? cases.map((u) => `<option>${escapeHtml(u)}</option>`)
    : ['<option value="">— pick vertical first —</option>']).join('');
  if (selected && [...ucSel.options].some((o) => o.value === selected)) ucSel.value = selected;
  else if (cases.length) ucSel.value = cases[0];
}

function fillDemoLibraryFields(project) {
  el('demoOrg').value = project.demo_org || '';
  const vertical = project.demo_vertical || '';
  el('demoVertical').value = LIB_TAXONOMY[vertical] ? vertical : '';
  fillDemoUseCases(project.demo_use_case || '');
  const summary = project.demo_summary || '';
  el('demoSummary').value = (summary === 'G' || summary === 'R') ? summary : '';
  el('demoSensitive').checked = !!project.demo_sensitive;
}

function collectDemoLibraryFields() {
  return {
    demo_org: el('demoOrg').value.trim(),
    demo_vertical: el('demoVertical').value,
    demo_use_case: el('demoUseCase').value,
    demo_summary: el('demoSummary').value,
    demo_sensitive: el('demoSensitive').checked ? 1 : 0,
  };
}

function fillSelect(sel, options, selected) {
  sel.innerHTML = options
    .map((o) => `<option value="${escapeHtml(o)}" ${o === selected ? 'selected' : ''}>${escapeHtml(o)}</option>`)
    .join('');
}

// Cascading narrow-down: Language -> Accent -> Sex -> Voice. Each step only
// offers values that actually have voices, so no combination dead-ends.
// Pass a voice_id to derive all four selections from an existing voice.
function refreshVoiceFilters(wrap, desiredVoiceId) {
  const langSel = wrap.querySelector('.seg-lang');
  const accentSel = wrap.querySelector('.seg-accent');
  const sexSel = wrap.querySelector('.seg-sex');
  const voiceSel = wrap.querySelector('.seg-voice');
  const desired = voices.find((v) => v.voice_id === desiredVoiceId);

  const langs = uniqSorted(voices.map((v) => v.language));
  const lang = desired
    ? desired.language
    : langs.includes(langSel.value) ? langSel.value : (langs.includes('English') ? 'English' : langs[0]);
  fillSelect(langSel, langs, lang);

  const inLang = voices.filter((v) => v.language === lang);
  const accents = uniqSorted(inLang.map((v) => v.accent));
  const accent = desired
    ? desired.accent
    : accents.includes(accentSel.value) ? accentSel.value : accents[0];
  fillSelect(accentSel, accents, accent);

  const inAccent = inLang.filter((v) => v.accent === accent);
  const sexes = uniqSorted(inAccent.map((v) => v.gender));
  const sex = desired
    ? desired.gender
    : sexes.includes(sexSel.value) ? sexSel.value : sexes[0];
  fillSelect(sexSel, sexes, sex);

  const matching = inAccent.filter((v) => v.gender === sex)
    .sort((a, b) => a.name.localeCompare(b.name));
  const voiceId = desired
    ? desired.voice_id
    : matching.some((v) => v.voice_id === voiceSel.value) ? voiceSel.value : matching[0]?.voice_id;
  voiceSel.innerHTML = matching
    .map((v) => `<option value="${v.voice_id}" ${v.voice_id === voiceId ? 'selected' : ''}>${escapeHtml(v.name)}</option>`)
    .join('');
}

async function loadProjectList() {
  const projects = await api('/api/projects');
  const list = el('projectList');
  list.innerHTML = '';
  for (const p of projects) {
    const li = document.createElement('li');
    li.dataset.id = p.id;
    li.innerHTML = `
      <div class="proj-info">
        <div class="proj-name">${escapeHtml(p.name)}</div>
        <div class="proj-meta">${p.output_format}</div>
      </div>
      <div class="proj-actions">
        <button class="proj-act" data-act="rename" title="Rename">✎</button>
        <button class="proj-act" data-act="clone" title="Duplicate">⧉</button>
        <button class="proj-act proj-act-danger" data-act="delete" title="Delete">✕</button>
      </div>`;
    if (currentProject && currentProject.id === p.id) li.classList.add('active');
    li.querySelector('.proj-info').addEventListener('click', () => openProject(p.id));
    li.querySelector('[data-act="rename"]').addEventListener('click', (e) => { e.stopPropagation(); renameProject(p); });
    li.querySelector('[data-act="clone"]').addEventListener('click', (e) => { e.stopPropagation(); cloneProject(p.id); });
    li.querySelector('[data-act="delete"]').addEventListener('click', (e) => { e.stopPropagation(); deleteProjectById(p.id, p.name); });
    list.appendChild(li);
  }
}

async function renameProject(p) {
  const name = prompt('Rename project', p.name);
  if (name == null) return;
  const trimmed = name.trim();
  if (!trimmed || trimmed === p.name) return;
  await api(`/api/projects/${p.id}`, { method: 'PUT', body: JSON.stringify({ name: trimmed }) });
  if (currentProject && currentProject.id === p.id) {
    currentProject.name = trimmed;
    el('projName').value = trimmed;
  }
  await loadProjectList();
}

async function cloneProject(id) {
  const copy = await api(`/api/projects/${id}/clone`, { method: 'POST' });
  await loadProjectList();
  await openProject(copy.id);
}

async function deleteProjectById(id, name) {
  if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
  await api(`/api/projects/${id}`, { method: 'DELETE' });
  if (currentProject && currentProject.id === id) {
    currentProject = null;
    el('editor').classList.add('hidden');
    el('emptyState').classList.remove('hidden');
  }
  await loadProjectList();
}

async function openProject(id) {
  // Never discard on-screen edits: persist (or at least locally back up) the
  // current project before swapping the editor to another one.
  await flushUnsavedEdits();
  currentProject = await api(`/api/projects/${id}`);
  el('emptyState').classList.add('hidden');
  el('editor').classList.remove('hidden');
  el('projName').value = currentProject.name;
  el('projDescription').value = currentProject.description || '';
  el('outputFormat').value = currentProject.output_format;
  el('demoIrn').value = currentProject.demo_irn || '';
  el('demoRegion').value = currentProject.demo_region || '';
  fillDemoLanguages(currentProject.demo_language || '');
  el('demoFormat').value = currentProject.demo_format === 'production' ? 'production' : 'sales';
  fillDemoLibraryFields(currentProject);
  // Default the template picker to the format and size this project already is.
  el('tplName').value = el('demoFormat').value;
  fillTemplateCounts(currentPertinentCount());
  el('segmentFiles').innerHTML = '';
  el('segExportStatus').textContent = '';
  undoStack = [];
  redoStack = [];
  updateUndoRedoBtns();
  // Voice defaults bar: derive the filters from the saved narrator voice,
  // then pin the saved voices even if they sit outside the current filters.
  fillDefaultFilters(currentProject.narrator_voice_id || undefined);
  if (!currentProject.narrator_voice_id && currentProject.default_sex
      && [...el('defSex').options].some((o) => o.value === currentProject.default_sex)) {
    el('defSex').value = currentProject.default_sex;
    fillDefaultVoiceSelects();
  }
  // Clear first: fillDefaultVoiceSelects preserves the previous project's
  // selection, which must not leak into a project with no saved defaults.
  el('defNarrator').value = '';
  el('defAnswer').value = '';
  setVoiceSelect(el('defNarrator'), currentProject.narrator_voice_id);
  setVoiceSelect(el('defAnswer'), currentProject.answer_voice_id);
  renderSegments();
  renderRenderHistory();
  await loadProjectList();
  lastSavedSnapshot = snapshotProject();
  maybeOfferBackupRestore();
}

function updateUndoRedoBtns() {
  const u = el('undoBtn');
  u.disabled = undoStack.length === 0;
  u.textContent = undoStack.length ? `↶ Undo (${undoStack.length})` : '↶ Undo';
  const r = el('redoBtn');
  r.disabled = redoStack.length === 0;
  r.textContent = redoStack.length ? `↷ Redo (${redoStack.length})` : '↷ Redo';
}

// Cascading Language -> Accent -> Sex filters for the defaults bar, mirroring
// the per-segment pickers. Pass a voice_id to derive all three from it.
function fillDefaultFilters(desiredVoiceId) {
  const langSel = el('defLang');
  const accentSel = el('defAccent');
  const sexSel = el('defSex');
  const desired = voices.find((v) => v.voice_id === desiredVoiceId);

  const langs = uniqSorted(voices.map((v) => v.language));
  const lang = desired
    ? desired.language
    : langs.includes(langSel.value) ? langSel.value : (langs.includes('English') ? 'English' : langs[0]);
  fillSelect(langSel, langs, lang);

  const inLang = voices.filter((v) => v.language === lang);
  const accents = uniqSorted(inLang.map((v) => v.accent));
  const accent = desired
    ? desired.accent
    : accents.includes(accentSel.value) ? accentSel.value : (accents.includes('US') ? 'US' : accents[0]);
  fillSelect(accentSel, accents, accent);

  const inAccent = inLang.filter((v) => v.accent === accent);
  const sexes = uniqSorted(inAccent.map((v) => v.gender));
  const sex = desired
    ? desired.gender
    : sexes.includes(sexSel.value) ? sexSel.value : sexes[0];
  fillSelect(sexSel, sexes, sex);

  fillDefaultVoiceSelects();
}

function fillDefaultVoiceSelects() {
  const lang = el('defLang').value;
  const accent = el('defAccent').value;
  const sex = el('defSex').value;
  const list = voices
    .filter((v) => v.language === lang && v.accent === accent && v.gender === sex)
    .sort((a, b) => a.name.localeCompare(b.name));
  for (const id of ['defNarrator', 'defAnswer']) {
    const sel = el(id);
    const current = sel.value;
    sel.innerHTML = '<option value="">—</option>' + list
      .map((v) => `<option value="${v.voice_id}">${escapeHtml(v.name)}</option>`)
      .join('');
    setVoiceSelect(sel, current); // keep the chosen voice even outside the filter
  }
}

// Select voiceId in sel; if it isn't among the options (it falls outside the
// current filters), pin it as an extra "Name — Accent" option so it survives.
function setVoiceSelect(sel, voiceId) {
  if (!voiceId) return;
  sel.value = voiceId;
  if (sel.value === voiceId) return;
  const v = voices.find((x) => x.voice_id === voiceId);
  if (!v) return;
  sel.insertAdjacentHTML(
    'beforeend',
    `<option value="${v.voice_id}">${escapeHtml(v.name)} — ${escapeHtml(v.accent)}</option>`
  );
  sel.value = voiceId;
}

// Apply a default voice to every existing segment of its role class
// (narrator = everything except answers).
function applyDefaultVoice(kind) {
  const voiceId = el(kind === 'answer' ? 'defAnswer' : 'defNarrator').value;
  if (!voiceId) return;
  el('segments').querySelectorAll('.segment').forEach((wrap) => {
    const isAnswer = wrap.querySelector('.seg-role').value === 'answer';
    if ((kind === 'answer') === isAnswer) refreshVoiceFilters(wrap, voiceId);
  });
}

function renderSegments() {
  const container = el('segments');
  container.innerHTML = '';
  for (const seg of currentProject.segments) {
    container.appendChild(buildSegmentEl(seg));
  }
  if (currentProject.segments.length === 0) {
    container.appendChild(buildSegmentEl(defaultSegment()));
  }
  renumberSegments();
}

// Number every segment by its position, and label each question with exactly
// what it becomes on screen: its pass, its question key (both iterations of a
// question share one, so they share one row), and — when scored — which results
// column it posts to. Re-run on any type/order/count/scored change.
function renumberSegments() {
  const all = [...el('segments').querySelectorAll('.segment')];
  const questions = all.filter((w) => isQuestionType(w.querySelector('.seg-role').value));
  // Mirror the video builder: a project with nothing marked scored is the legacy
  // sales shape, where every question scores.
  const anyScored = questions.some((w) => w.querySelector('.seg-scored').checked);
  let col = 0;
  all.forEach((wrap, i) => {
    wrap.querySelector('.seg-num').textContent = i + 1;

    const badge = wrap.querySelector('.seg-qnum');
    if (!isQuestionType(wrap.querySelector('.seg-role').value)) {
      badge.textContent = '';
      badge.classList.remove('show', 'unscored');
      return;
    }
    const key = wrap.querySelector('.seg-qkey').value.trim();
    const iter = Number(wrap.querySelector('.seg-iter').value) || 0;
    const scored = anyScored ? wrap.querySelector('.seg-scored').checked : true;
    const parts = [];
    if (iter) parts.push('IT' + iter);
    if (key) parts.push(key);
    if (scored) parts.push('→ Q' + (col += 1));
    else parts.push('not scored');
    badge.textContent = parts.join(' · ');
    badge.classList.add('show');
    badge.classList.toggle('unscored', !scored);
  });
}

// ---------- Templates ----------

function templateSummary(template, count) {
  const outcomes = `Outcomes default to clear, with PQ${count} high risk.`;
  if (template === 'production') {
    return `${count} pertinent + ${count + 2} neutral questions, asked twice`
      + ` (${2 * (count + (count + 2))} question/answer segments), `
      + `${count} scored columns — only the second-iteration pertinent questions score. ${outcomes}`;
  }
  return `${count} pertinent questions asked once, all ${count} scored. ${outcomes}`;
}

// How many pertinent questions the open project actually has, so re-applying a
// template defaults to the project's own size instead of the smallest option.
function currentPertinentCount() {
  const keys = new Set();
  for (const s of (currentProject && currentProject.segments) || []) {
    if (s.role !== 'question') continue;
    if ((s.qtype || 'PQ') !== 'PQ') continue;
    keys.add((s.q_key || '').trim() || String(keys.size));
  }
  return keys.size;
}

function fillTemplateCounts(preferred) {
  const template = el('tplName').value;
  const counts = TEMPLATE_COUNTS[template] || [3, 4];
  const sel = el('tplCount');
  const want = Number(preferred ?? sel.value);
  sel.innerHTML = counts.map((n) => `<option value="${n}">${n}</option>`).join('');
  sel.value = String(counts.includes(want) ? want : counts[0]);
  el('tplHint').textContent = templateSummary(template, Number(sel.value));
}

// Applying a template replaces the segment list wholesale, so it has to be
// confirmed — this is the one action in the editor that discards work outright.
async function applyTemplate() {
  const template = el('tplName').value;
  const count = Number(el('tplCount').value);
  const existing = [...el('segments').querySelectorAll('.segment')]
    .filter((w) => w.querySelector('.seg-text').value.trim()).length;
  const warning = existing
    ? `This replaces all ${existing} segment(s) with content in this project. That cannot be undone.\n\n`
    : '';
  if (!confirm(`${warning}Apply the ${template === 'production' ? 'Production' : 'Sales'} demo template with ${count} questions?\n\n${templateSummary(template, count)}`)) return;

  const btn = el('applyTemplateBtn');
  btn.disabled = true;
  const status = el('renderStatus');
  status.textContent = 'Applying template…';
  try {
    // Persist the current voice defaults first — the template stamps them onto
    // every segment it creates.
    await persistProject();
    await api(`/api/projects/${currentProject.id}/apply-template`, {
      method: 'POST',
      body: JSON.stringify({ template, count }),
    });
    await openProject(currentProject.id);
    status.textContent = `Applied ${template} template · ${currentProject.segments.length} segments. Now fill in the question text.`;
  } catch (err) {
    status.textContent = 'Template failed: ' + err.message;
  } finally {
    btn.disabled = false;
  }
}

function defaultSegment() {
  const narratorId = el('defNarrator').value;
  const dv = voices.find((v) => v.voice_id === narratorId)
    || voices.find((v) => v.language_code === 'en-US' && v.gender === 'Female') || voices[0];
  return {
    label: '',
    text: '',
    voice_id: dv ? dv.voice_id : '',
    language_code: dv ? dv.language_code : 'en-US',
    effect: 'default',
    master_volume: 0,
    master_speed: 0,
    master_pitch: 0,
    pause_after_ms: 400,
  };
}

const normQ = (s) => (s || '').trim().toLowerCase().replace(/\s+/g, ' ');

/**
 * The key for a question, which decides which row it occupies on screen.
 *
 * A question asked in BOTH iterations must reuse the first iteration's key —
 * that shared key is the entire mechanism by which the two passes collapse onto
 * one row. So match on the question text first and reuse; only allocate a fresh
 * ordinal when the text is genuinely new.
 *
 * Allocating "next unused" unconditionally is what mis-keyed the first
 * hand-built production demo: its second-iteration neutrals became NQ7–NQ12
 * instead of reusing NQ1–NQ6, and the video rendered 20 question rows instead
 * of 10.
 */
function nextQuestionKey(qtype, exclude, text) {
  const used = new Set();
  const wanted = normQ(text);
  for (const w of el('segments').querySelectorAll('.segment')) {
    if (w === exclude) continue;
    const t = w.querySelector('.seg-role').value;
    if (!isQuestionType(t) || UI_TYPES[t].qtype !== qtype) continue;
    const key = w.querySelector('.seg-qkey').value.trim();
    // Same question, asked again — share its row.
    if (wanted && key && normQ(w.querySelector('.seg-text').value) === wanted) return key;
    const m = key.match(/^[A-Z]{2}(\d+)$/i);
    if (m) used.add(Number(m[1]));
  }
  let n = 1;
  while (used.has(n)) n += 1;
  return `${qtype}${n}`;
}

// Every question gets an answer directly after it, defaulting to the answer its
// class expects — Yes for a neutral question, No for a pertinent one. If one is
// already there, its class/key are re-synced and a still-stock answer flips to
// match; a hand-edited answer is left exactly as written.
function ensureAnswerAfter(questionWrap, type) {
  const { qtype, answer } = UI_TYPES[type];
  const key = questionWrap.querySelector('.seg-qkey').value.trim();
  const iter = questionWrap.querySelector('.seg-iter').value;
  const next = questionWrap.nextElementSibling;

  if (next && next.classList.contains('segment') && next.querySelector('.seg-role').value === 'answer') {
    const txt = next.querySelector('.seg-text');
    if (STOCK_ANSWERS.includes(txt.value.trim())) txt.value = answer;
    next.dataset.qtype = qtype;
    next.querySelector('.seg-qkey').value = key;
    next.querySelector('.seg-iter').value = iter;
    return next;
  }

  const seg = defaultSegment();
  seg.role = 'answer';
  seg.text = answer;
  seg.qtype = qtype;
  seg.q_key = key;
  seg.iteration = Number(iter) || 0;
  seg.pause_after_ms = ROLE_PAUSE_DEFAULTS.answer;
  const answerVoice = el('defAnswer').value;
  if (answerVoice) {
    seg.voice_id = answerVoice;
    const v = voices.find((x) => x.voice_id === answerVoice);
    if (v) seg.language_code = v.language_code;
  }
  const node = buildSegmentEl(seg);
  questionWrap.after(node);
  return node;
}

function buildSegmentEl(seg) {
  const tpl = el('segmentTemplate').content.cloneNode(true);
  const wrap = tpl.querySelector('.segment');

  tpl.querySelector('.seg-label').value = seg.label || '';
  refreshVoiceFilters(wrap, seg.voice_id);
  for (const cls of ['.seg-lang', '.seg-accent', '.seg-sex']) {
    wrap.querySelector(cls).addEventListener('change', () => refreshVoiceFilters(wrap));
  }

  tpl.querySelector('.seg-effect').value = seg.effect || 'default';
  tpl.querySelector('.seg-text').value = seg.text || '';
  tpl.querySelector('.seg-volume').value = seg.master_volume ?? 0;
  tpl.querySelector('.seg-speed').value = seg.master_speed ?? 0;
  tpl.querySelector('.seg-pitch').value = seg.master_pitch ?? 0;
  tpl.querySelector('.seg-pause').value = seg.pause_after_ms ?? 400;

  const roleSel = tpl.querySelector('.seg-role');
  roleSel.value = uiTypeOf(seg);
  tpl.querySelector('.seg-result').value = seg.result || '';
  tpl.querySelector('.seg-qkey').value = seg.q_key || '';
  tpl.querySelector('.seg-iter').value = String(Number(seg.iteration) || 0);
  const scoredBox = tpl.querySelector('.seg-scored');
  scoredBox.checked = !!Number(seg.scored);
  // A non-question segment's class isn't editable but must survive a round-trip
  // (the template tags answers with their question's class), so it rides along here.
  wrap.dataset.qtype = seg.qtype || '';

  // The question-only tags (key / pass / scored / outcome) are noise on any other
  // segment type, and an outcome only means anything when scored.
  const questionOnly = [...wrap.querySelectorAll('.seg-key-label,.seg-iter-label,.seg-scored-label')];
  const resultLabel = tpl.querySelector('.seg-result-label');
  const syncQuestionVis = () => {
    const isQ = isQuestionType(roleSel.value);
    for (const l of questionOnly) l.style.display = isQ ? '' : 'none';
    resultLabel.style.display = isQ && scoredBox.checked ? '' : 'none';
  };
  scoredBox.addEventListener('change', () => {
    // Marking a question scored gives it an outcome; clear is the default, since
    // only the last pertinent question is meant to come back high risk.
    const outcome = wrap.querySelector('.seg-result');
    if (scoredBox.checked && !outcome.value) outcome.value = 'G';
    syncQuestionVis(); renumberSegments();
  });
  syncQuestionVis();
  // Color the segment by type for quick scanning
  const applyRoleClass = () => { wrap.dataset.role = roleSel.value; };
  applyRoleClass();

  roleSel.addEventListener('change', () => {
    const type = roleSel.value;
    syncQuestionVis();
    applyRoleClass();
    // Stock text / pause / voice for the new type.
    const text = wrap.querySelector('.seg-text');
    if (!text.value.trim() && ROLE_TEXT_DEFAULTS[type]) text.value = ROLE_TEXT_DEFAULTS[type];
    const pause = wrap.querySelector('.seg-pause');
    if (STANDARD_PAUSES.includes(Number(pause.value))) pause.value = ROLE_PAUSE_DEFAULTS[type] ?? 400;
    const dv = el(type === 'answer' ? 'defAnswer' : 'defNarrator').value;
    if (dv) refreshVoiceFilters(wrap, dv);

    if (isQuestionType(type)) {
      // Every question needs a key (both iterations of one question share it)
      // and an answer of its own — neither should have to be added by hand.
      const keyInput = wrap.querySelector('.seg-qkey');
      if (!keyInput.value.trim()) {
        keyInput.value = nextQuestionKey(UI_TYPES[type].qtype, wrap, wrap.querySelector('.seg-text').value);
      }
      ensureAnswerAfter(wrap, type);
    }
    renumberSegments();
    scheduleAutosave();
  });

  wrap.querySelector('.seg-delete').addEventListener('click', () => {
    const all = [...el('segments').querySelectorAll('.segment')];
    undoStack.push({ data: collectSegmentData(wrap), index: all.indexOf(wrap) });
    redoStack = []; // a fresh delete invalidates the redo chain
    updateUndoRedoBtns();
    wrap.remove();
    renumberSegments();
    scheduleAutosave();
  });

  wrap.querySelector('.seg-preview').addEventListener('click', async (e) => {
    const btn = e.target;
    btn.disabled = true;
    btn.textContent = '...';
    try {
      const data = collectSegmentData(wrap);
      const result = await api('/api/segments/preview', {
        method: 'POST',
        body: JSON.stringify(data),
      });
      const audio = wrap.querySelector('.seg-audio-preview');
      audio.src = result.path;
      audio.style.display = 'block';
      audio.play();
    } catch (err) {
      alert('Preview failed: ' + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '▶ Preview';
    }
  });

  // drag to reorder
  wrap.addEventListener('dragstart', () => {
    draggedSegmentEl = wrap;
    wrap.classList.add('dragging');
  });
  wrap.addEventListener('dragend', () => {
    wrap.classList.remove('dragging');
    draggedSegmentEl = null;
    renumberSegments();
    scheduleAutosave();
  });
  wrap.addEventListener('dragover', (e) => {
    e.preventDefault();
    const container = el('segments');
    const after = getDragAfterElement(container, e.clientY);
    if (!draggedSegmentEl) return;
    if (after == null) container.appendChild(draggedSegmentEl);
    else container.insertBefore(draggedSegmentEl, after);
  });

  const div = document.createElement('div');
  div.appendChild(tpl);
  return div.firstElementChild;
}

function getDragAfterElement(container, y) {
  const els = [...container.querySelectorAll('.segment:not(.dragging)')];
  return els.reduce(
    (closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) return { offset, element: child };
      return closest;
    },
    { offset: Number.NEGATIVE_INFINITY, element: null }
  ).element;
}

function collectSegmentData(wrap) {
  // nq/pq are UI-only types — they store as role='question' plus the class.
  const type = wrap.querySelector('.seg-role').value;
  const mapped = UI_TYPES[type];
  return {
    label: wrap.querySelector('.seg-label').value,
    voice_id: wrap.querySelector('.seg-voice').value,
    language_code: (voices.find((v) => v.voice_id === wrap.querySelector('.seg-voice').value) || {}).language_code || 'en-US',
    effect: wrap.querySelector('.seg-effect').value,
    text: wrap.querySelector('.seg-text').value,
    master_volume: Number(wrap.querySelector('.seg-volume').value),
    master_speed: Number(wrap.querySelector('.seg-speed').value),
    master_pitch: Number(wrap.querySelector('.seg-pitch').value),
    pause_after_ms: Number(wrap.querySelector('.seg-pause').value),
    role: mapped ? mapped.role : type,
    result: wrap.querySelector('.seg-result').value,
    qtype: mapped ? mapped.qtype : (wrap.dataset.qtype || ''),
    q_key: wrap.querySelector('.seg-qkey').value.trim(),
    iteration: Number(wrap.querySelector('.seg-iter').value) || 0,
    scored: wrap.querySelector('.seg-scored').checked ? 1 : 0,
  };
}

function collectAllSegments() {
  return [...el('segments').querySelectorAll('.segment')].map(collectSegmentData);
}

// Persist the on-screen editor state without re-rendering (no focus loss),
// so it is safe to call from autosave while the user is typing.
async function persistProject() {
  const name = el('projName').value.trim();
  if (!name) throw new Error('Project name is required');
  const prevName = currentProject.name;
  await api(`/api/projects/${currentProject.id}`, {
    method: 'PUT',
    body: JSON.stringify({
      name,
      description: el('projDescription').value,
      output_format: el('outputFormat').value,
      demo_irn: el('demoIrn').value.trim(),
      demo_region: el('demoRegion').value.trim(),
      demo_language: el('demoLanguage').value,
      default_sex: el('defSex').value,
      narrator_voice_id: el('defNarrator').value,
      answer_voice_id: el('defAnswer').value,
      ...collectDemoLibraryFields(),
    }),
  });

  const segments = collectAllSegments();
  await api(`/api/projects/${currentProject.id}/segments`, {
    method: 'PUT',
    body: JSON.stringify({ segments }),
  });

  currentProject.name = name;
  Object.assign(currentProject, collectDemoLibraryFields());
  lastSavedSnapshot = snapshotProject();
  if (name !== prevName) await loadProjectList();
}

async function saveProject() {
  const name = el('projName').value.trim();
  if (!name) return alert('Project name is required');
  await persistProject();
  await openProject(currentProject.id);
  flash('Saved');
}

// ---------- Autosave ----------
// The editor state lives only in the DOM, so every edit is debounce-saved to
// the server AND snapshotted to a localStorage ring buffer. The local ring is
// the parachute if the server save fails or the tab dies mid-edit.

function editorActive() {
  return !!currentProject && !el('editor').classList.contains('hidden');
}

function snapshotProject() {
  return JSON.stringify({
    name: el('projName').value.trim(),
    description: el('projDescription').value,
    output_format: el('outputFormat').value,
    demo_irn: el('demoIrn').value.trim(),
    demo_region: el('demoRegion').value.trim(),
    demo_language: el('demoLanguage').value,
    demo_format: el('demoFormat').value,
    default_sex: el('defSex').value,
    narrator_voice_id: el('defNarrator').value,
    answer_voice_id: el('defAnswer').value,
    ...collectDemoLibraryFields(),
    segments: collectAllSegments(),
  });
}

function setAutosaveStatus(msg, isError) {
  const s = el('autosaveStatus');
  s.textContent = msg;
  s.classList.toggle('autosave-error', !!isError);
}

function scheduleAutosave() {
  if (!editorActive()) return;
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(autosaveNow, 1200);
}

async function autosaveNow() {
  if (!editorActive() || autosaving) return;
  writeLocalBackup();
  if (snapshotProject() === lastSavedSnapshot) return;
  if (!el('projName').value.trim()) {
    setAutosaveStatus('⚠ not saved — project needs a name', true);
    return;
  }
  autosaving = true;
  try {
    await persistProject();
    setAutosaveStatus(`Autosaved ${new Date().toLocaleTimeString()}`);
  } catch (err) {
    setAutosaveStatus(`⚠ autosave failed: ${err.message} — backup kept locally`, true);
  } finally {
    autosaving = false;
    // Edits that landed while the save was in flight get their own pass.
    if (editorActive() && snapshotProject() !== lastSavedSnapshot) scheduleAutosave();
  }
}

// Best-effort save before anything replaces the editor contents.
async function flushUnsavedEdits() {
  clearTimeout(autosaveTimer);
  if (!editorActive()) return;
  writeLocalBackup();
  if (snapshotProject() === lastSavedSnapshot) return;
  if (!el('projName').value.trim()) return; // local backup is all we can do
  try {
    await persistProject();
  } catch {
    // Server save failed; the localStorage backup above still has the edits.
  }
}

function backupKey() {
  return `vmBackup:${currentProject.id}`;
}

function writeLocalBackup() {
  if (!editorActive()) return;
  try {
    const snap = snapshotProject();
    const ring = JSON.parse(localStorage.getItem(backupKey()) || '[]');
    if (ring.length && ring[ring.length - 1].data === snap) return;
    ring.push({ at: new Date().toISOString(), data: snap });
    while (ring.length > 10) ring.shift();
    localStorage.setItem(backupKey(), JSON.stringify(ring));
  } catch { /* localStorage full/blocked — nothing else to do */ }
}

function latestUsefulBackup() {
  try {
    const ring = JSON.parse(localStorage.getItem(backupKey()) || '[]');
    for (let i = ring.length - 1; i >= 0; i--) {
      const data = JSON.parse(ring[i].data);
      if ((data.segments || []).some((s) => (s.text || '').trim())) return { at: ring[i].at, data };
    }
  } catch { /* corrupt ring — treat as no backup */ }
  return null;
}

// Disaster recovery: the project is empty on the server but this browser has
// a backup with real content (e.g. edits were discarded before ever saving).
function maybeOfferBackupRestore() {
  if ((currentProject.segments || []).some((s) => (s.text || '').trim())) return;
  const backup = latestUsefulBackup();
  if (!backup) return;
  const when = new Date(backup.at).toLocaleString();
  const n = backup.data.segments.length;
  if (!confirm(`This project is empty on the server, but a local backup from ${when} has ${n} segment(s). Restore it?`)) return;
  if (backup.data.name) el('projName').value = backup.data.name;
  el('projDescription').value = backup.data.description || '';
  el('outputFormat').value = backup.data.output_format || currentProject.output_format;
  el('demoIrn').value = backup.data.demo_irn || '';
  el('demoRegion').value = backup.data.demo_region || '';
  fillDemoLanguages(backup.data.demo_language || '');
  if (backup.data.demo_format) el('demoFormat').value = backup.data.demo_format;
  fillDemoLibraryFields({
    demo_org: backup.data.demo_org || '',
    demo_vertical: backup.data.demo_vertical || '',
    demo_use_case: backup.data.demo_use_case || '',
    demo_summary: backup.data.demo_summary || '',
    demo_sensitive: backup.data.demo_sensitive || 0,
  });
  currentProject.segments = backup.data.segments;
  renderSegments();
  scheduleAutosave();
}

async function deleteProject() {
  await deleteProjectById(currentProject.id, currentProject.name);
}

async function renderProject() {
  const status = el('renderStatus');
  status.textContent = 'Saving...';
  await saveProject();

  status.textContent = 'Generating audio (this calls Voicemaker per segment, then stitches)...';
  el('renderBtn').disabled = true;
  try {
    const result = await api(`/api/projects/${currentProject.id}/render`, { method: 'POST' });
    const url = renderUrl(result);
    const filename = (result.file_path || `render.${result.format || 'mp3'}`).split('/').pop();
    status.innerHTML = `Done — used ${result.used_chars} characters · <a href="${url}" download="${filename}" class="download-link">Download</a>`;
    await openProject(currentProject.id);
  } catch (err) {
    status.textContent = 'Failed: ' + err.message;
  } finally {
    el('renderBtn').disabled = false;
  }
}

function fmtDuration(s) {
  if (s == null) return '';
  s = Math.max(0, Math.round(s));
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`;
}

async function renderVideo() {
  const status = el('renderStatus');
  const result = el('videoResult');
  status.textContent = 'Saving...';
  await saveProject();

  el('renderVideoBtn').disabled = true;
  el('renderBtn').disabled = true;
  result.innerHTML = `
    <div class="prog">
      <div class="prog-bar"><div class="prog-fill" id="progFill"></div></div>
      <div class="prog-meta"><span id="progPhase">Starting…</span><span id="progEta"></span></div>
    </div>`;
  status.textContent = 'Rendering video…';

  try {
    const { jobId } = await api(`/api/projects/${currentProject.id}/render-video`, { method: 'POST' });
    await pollRenderJob(jobId, result, status);
  } catch (err) {
    status.textContent = 'Video failed: ' + err.message;
    result.innerHTML = '';
    el('renderVideoBtn').disabled = false;
    el('renderBtn').disabled = false;
  }
}

function pollRenderJob(jobId, result, status) {
  const fill = el('progFill');
  const phaseEl = el('progPhase');
  const etaEl = el('progEta');
  return new Promise((resolve) => {
    const timer = setInterval(async () => {
      let j;
      try { j = await api(`/api/render-jobs/${jobId}`); } catch { return; }
      if (fill) fill.style.width = j.percent + '%';
      const label =
        j.phase === 'speech' ? `Generating speech ${j.current}/${j.total}` :
        j.phase === 'audio' ? 'Stitching audio' :
        j.phase === 'frames' ? `Rendering video · ${j.framesTotal ? Math.round((j.framesDone / j.framesTotal) * 100) : 0}%` :
        j.phase === 'encoding' ? 'Encoding mp4' :
        j.phase === 'done' ? 'Done' : j.phase === 'error' ? 'Error' : 'Starting…';
      if (phaseEl) phaseEl.textContent = label;
      if (etaEl) {
        etaEl.textContent = j.done
          ? `${fmtDuration(j.elapsedSeconds)} total`
          : (j.etaSeconds != null ? `~${fmtDuration(j.etaSeconds)} left · ${fmtDuration(j.elapsedSeconds)} elapsed` : `${fmtDuration(j.elapsedSeconds)} elapsed`);
      }
      if (j.done) {
        clearInterval(timer);
        el('renderVideoBtn').disabled = false;
        el('renderBtn').disabled = false;
        if (j.error) {
          status.textContent = 'Video failed: ' + j.error;
          result.innerHTML = '';
        } else {
          status.textContent = 'Video ready.';
          const filename = (j.url || 'demo.mp4').split('/').pop();
          result.innerHTML = `
            <video controls src="${j.url}" style="max-width:100%;border-radius:8px;margin-top:12px"></video>
            <div><a href="${j.url}" download="${filename}" class="btn btn-small">⭳ Download mp4</a></div>`;
          // Refresh render history so the new video appears in the list.
          try {
            currentProject.renders = await api(`/api/projects/${currentProject.id}/renders`);
            renderRenderHistory();
          } catch { /* ignore */ }
        }
        resolve();
      }
    }, 1500);
  });
}

// Translate the current project into one or more languages. Each becomes a new
// project ("<name> — <language>") with translated text + matching voices, so the
// question text is displayed and the audio can be generated as usual.
function openTranslateModal() {
  const existing = document.getElementById('translateModal');
  if (existing) existing.remove();

  // Offer every language that actually has voices, so no variant can dead-end.
  const langs = uniqSorted(voices.map((v) => v.language));
  const wrap = document.createElement('div');
  wrap.id = 'translateModal';
  wrap.className = 'pub-modal';
  wrap.innerHTML = `
    <div class="pub-box">
      <button class="pub-x" type="button">✕</button>
      <h3>Translate project</h3>
      <p class="tr-hint">Creates a new project per language with translated segment text and matching voices. Translation is powered by Claude.</p>
      <input id="trFilter" class="tr-filter" placeholder="Filter languages…" />
      <div id="trLangList" class="tr-lang-list">
        ${langs.map((l) => `<label class="tr-lang"><input type="checkbox" value="${escapeHtml(l)}" /> ${escapeHtml(l)}</label>`).join('')}
      </div>
      <div id="trStatus" class="pub-status"></div>
      <button id="trGo" class="btn btn-primary" type="button">🌐 Create translated version(s)</button>
    </div>`;
  document.body.appendChild(wrap);

  const close = () => wrap.remove();
  wrap.querySelector('.pub-x').addEventListener('click', close);
  wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });

  const filter = wrap.querySelector('#trFilter');
  filter.addEventListener('input', () => {
    const q = filter.value.trim().toLowerCase();
    wrap.querySelectorAll('.tr-lang').forEach((el) => {
      el.style.display = el.textContent.trim().toLowerCase().includes(q) ? '' : 'none';
    });
  });

  wrap.querySelector('#trGo').addEventListener('click', async () => {
    const chosen = [...wrap.querySelectorAll('#trLangList input:checked')].map((c) => c.value);
    const status = wrap.querySelector('#trStatus');
    if (chosen.length === 0) { status.textContent = 'Pick at least one language.'; return; }

    // Persist current edits first so the translation reflects what's on screen.
    await saveProject();

    let firstNew = null;
    for (let i = 0; i < chosen.length; i++) {
      status.textContent = `Translating to ${chosen[i]} (${i + 1}/${chosen.length})…`;
      try {
        const project = await api(`/api/projects/${currentProject.id}/translate`, {
          method: 'POST',
          body: JSON.stringify({ language: chosen[i] }),
        });
        if (!firstNew) firstNew = project.id;
      } catch (err) {
        status.textContent = `Failed on ${chosen[i]}: ${err.message}`;
        await loadProjectList();
        return;
      }
    }
    status.textContent = 'Done.';
    await loadProjectList();
    if (firstNew) await openProject(firstNew);
    close();
  });
}

async function exportSegments() {
  const status = el('segExportStatus');
  const listEl = el('segmentFiles');
  const btn = el('renderSegmentsBtn');
  status.textContent = 'Saving…';
  await saveProject();

  const format = el('segExportFormat').value;
  btn.disabled = true;
  status.textContent = `Generating ${format} files (one Voicemaker call per segment)…`;
  listEl.innerHTML = '';
  try {
    const { files, zip, skipped, standard } = await api(`/api/projects/${currentProject.id}/render-segments`, {
      method: 'POST',
      body: JSON.stringify({ format }),
    });
    const stdCount = standard?.files?.length || 0;
    if (zip) {
      const li = document.createElement('li');
      li.className = 'segment-files-all';
      const total = files.length + stdCount;
      li.innerHTML = `<span>${total} file${total === 1 ? '' : 's'}${stdCount ? ` (${files.length} segments + ${stdCount} standard)` : ''}</span>
        <a href="${zip.url}" download="${escapeHtml(zip.filename)}" class="btn btn-small">⭳ Download all (zip)</a>`;
      listEl.appendChild(li);
    }
    for (const f of files) {
      const li = document.createElement('li');
      li.innerHTML = `
        <div class="seg-file-name">${escapeHtml(f.filename)}${f.label ? ` <span class="seg-file-label">${escapeHtml(f.label)}</span>` : ''}</div>
        <audio controls src="${f.url}"></audio>
        <a href="${f.url}" download="${escapeHtml(f.filename)}" class="btn btn-small">⭳ Download</a>`;
      listEl.appendChild(li);
    }
    status.textContent = files.length ? `Exported ${files.length} segment${files.length === 1 ? '' : 's'}.` : 'No spoken segments to export.';
    if (skipped && skipped.length) {
      const names = skipped.slice(0, 4).join(', ') + (skipped.length > 4 ? `, +${skipped.length - 4} more` : '');
      status.textContent += ` ⚠ Skipped ${skipped.length} with an empty text box (silence gaps): ${names}`;
    }
    // The standard components are zip-only, so the status line is the only
    // place they can be accounted for — including when some failed to render.
    if (stdCount) status.textContent += ` Plus ${stdCount} standard component${stdCount === 1 ? '' : 's'} in the zip.`;
    if (standard?.englishTextNonEnglishVoice) {
      status.textContent += ` ⚠ Standard components are fixed English text, voiced by ${standard.voice}.`;
    }
    if (standard?.failed?.length) {
      status.textContent += ` ⚠ ${standard.failed.length} standard component${standard.failed.length === 1 ? '' : 's'} failed: ${standard.failed.map((f) => f.label).join(', ')}.`;
    }
  } catch (err) {
    status.textContent = 'Export failed: ' + err.message;
  } finally {
    btn.disabled = false;
  }
}

function openPublishModal(render) {
  const existing = document.getElementById('publishModal');
  if (existing) existing.remove();
  const wrap = document.createElement('div');
  wrap.id = 'publishModal';
  wrap.className = 'pub-modal';
  const verticals = Object.keys(LIB_TAXONOMY);
  const orgDefault = (currentProject.demo_org || '').trim() || currentProject.name;
  wrap.innerHTML = `
    <div class="pub-box">
      <button class="pub-x" type="button">✕</button>
      <h3>Publish to library</h3>
      <p class="pub-lead">Publishes in the background: Vidyard upload on the first publish (headless) → clearspeeddemos.com with the categories below. Stay on this page.</p>
      <label>Title <input id="pubTitle" value="${escapeHtml(currentProject.name)}" /></label>
      <div class="pub-row">
        <label>Vertical <select id="pubVertical">${verticals.map((v) => `<option>${v}</option>`).join('')}</select></label>
        <label>Use case <select id="pubUseCase"></select></label>
      </div>
      <div class="pub-row">
        <label>Org <input id="pubOrg" value="${escapeHtml(orgDefault)}" /></label>
        <label>Outcome <select id="pubSummary"><option value="">—</option><option value="G">G · clear</option><option value="R">R · high risk</option></select></label>
      </div>
      <div class="pub-row">
        <label>Language <select id="pubLanguage"></select></label>
        <label>Accent <select id="pubAccent"></select></label>
      </div>
      <label style="display:flex;align-items:center;gap:8px;margin-top:6px">
        <input id="pubSensitive" type="checkbox" style="width:auto" />
        <span>Sensitive — public site requires a @clearspeed.com email + verification code to view</span>
      </label>
      <div id="pubVidyardExisting" class="pub-lead" hidden>
        <label style="display:flex;flex-direction:row;align-items:flex-start;gap:8px">
          <input id="pubReplaceVidyard" type="checkbox" style="width:auto;margin-top:4px" />
          <span>Upload a <strong>new</strong> Vidyard video for this demo. Off by default: the
          widget cannot swap the file inside a player, so every upload leaves another copy
          in the Vidyard library. Leave it off and this republish keeps
          <a id="pubVidyardCurrent" href="#" target="_blank" rel="noopener">the current player</a>,
          which goes on playing the previous cut.</span>
        </label>
      </div>
      <details class="pub-advanced">
        <summary>Advanced — skip Vidyard upload, use an existing player</summary>
        <label>Vidyard player URL or ID
          <input id="pubVidyard" placeholder="share.vidyard.com/watch/… or the video ID" /></label>
      </details>
      <div id="pubStatus" class="pub-status"></div>
      <button id="pubGo" class="btn btn-primary" type="button">▲ Publish</button>
    </div>`;
  document.body.appendChild(wrap);

  const vSel = wrap.querySelector('#pubVertical');
  const ucSel = wrap.querySelector('#pubUseCase');
  const sensCheck = wrap.querySelector('#pubSensitive');
  const vidyardInput = wrap.querySelector('#pubVidyard');
  const pubGo = wrap.querySelector('#pubGo');

  const langSel = wrap.querySelector('#pubLanguage');
  const accSel = wrap.querySelector('#pubAccent');
  const primaryVoiceId = el('defNarrator').value
    || (currentProject.segments.find((s) => s.voice_id) || {}).voice_id;
  const pv = voices.find((v) => v.voice_id === primaryVoiceId);
  const fillPubAccents = () => {
    const accents = uniqSorted(voices.filter((v) => v.language === langSel.value).map((v) => v.accent));
    const want = pv && pv.language === langSel.value ? pv.accent
      : accents.includes(accSel.value) ? accSel.value
      : accents.includes('US') ? 'US' : accents[0];
    fillSelect(accSel, accents, want);
  };
  fillSelect(langSel, uniqSorted(voices.map((v) => v.language)), pv ? pv.language : 'English');
  fillPubAccents();
  langSel.addEventListener('change', fillPubAccents);
  const fillUseCases = () => { ucSel.innerHTML = LIB_TAXONOMY[vSel.value].map((u) => `<option>${u}</option>`).join(''); };
  fillUseCases();
  vSel.addEventListener('change', fillUseCases);

  const draftKey = `pubDraft:${currentProject.id}`;
  const collectForm = () => ({
    title: wrap.querySelector('#pubTitle').value,
    vertical: vSel.value,
    use_case: ucSel.value,
    org: wrap.querySelector('#pubOrg').value,
    summary: wrap.querySelector('#pubSummary').value,
    sensitive: sensCheck.checked,
    language: langSel.value,
    accent: accSel.value,
    vidyard: vidyardInput.value,
  });
  const applySaved = (d) => {
    if (!d) return;
    if (d.title) wrap.querySelector('#pubTitle').value = d.title;
    if (d.vertical && LIB_TAXONOMY[d.vertical]) { vSel.value = d.vertical; fillUseCases(); }
    if (d.use_case && [...ucSel.options].some((o) => o.value === d.use_case)) ucSel.value = d.use_case;
    if (d.org != null) wrap.querySelector('#pubOrg').value = d.org;
    if (d.summary != null) wrap.querySelector('#pubSummary').value = d.summary;
    if (typeof d.sensitive === 'boolean') sensCheck.checked = d.sensitive;
    else if (d.sensitive === 0 || d.sensitive === 1) sensCheck.checked = !!d.sensitive;
    if (d.language && [...langSel.options].some((o) => o.value === d.language)) {
      langSel.value = d.language;
      fillPubAccents();
    }
    if (d.accent && [...accSel.options].some((o) => o.value === d.accent)) accSel.value = d.accent;
    // Never restore a prior player id into the form — that would skip the
    // headless upload and leave the gallery on the old video.
  };
  let draft = null;
  try { draft = JSON.parse(localStorage.getItem(draftKey) || 'null'); } catch {}
  if (draft) applySaved(draft);
  {
    api('/api/library').then((rows) => {
      const prev = rows.find((r) => r.source_project_id === currentProject.id);
      if (!prev) return;
      // A player already exists, so an upload would add a copy rather than
      // replace anything. Surface that choice instead of taking it silently.
      if (prev.vidyard_id) {
        const link = wrap.querySelector('#pubVidyardCurrent');
        link.href = `https://share.vidyard.com/watch/${encodeURIComponent(prev.vidyard_id)}`;
        wrap.querySelector('#pubVidyardExisting').hidden = false;
      }
      if (draft) return;
      const patch = {};
      if (!wrap.querySelector('#pubTitle').value) patch.title = prev.title;
      if (!vSel.value && prev.vertical) patch.vertical = prev.vertical;
      if (!ucSel.value && prev.use_case) patch.use_case = prev.use_case;
      if (!(wrap.querySelector('#pubOrg').value || '').trim()) patch.org = prev.org;
      if (!wrap.querySelector('#pubSummary').value && prev.summary) patch.summary = prev.summary;
      if (prev.language) patch.language = prev.language;
      if (prev.accent) patch.accent = prev.accent;
      if (typeof prev.sensitive === 'number') patch.sensitive = !!prev.sensitive;
      applySaved(patch);
    }).catch(() => {});
  }
  applySaved({
    title: currentProject.name,
    vertical: currentProject.demo_vertical,
    use_case: currentProject.demo_use_case,
    org: orgDefault,
    summary: currentProject.demo_summary,
    sensitive: !!currentProject.demo_sensitive,
  });
  const saveDraft = () => { try { localStorage.setItem(draftKey, JSON.stringify(collectForm())); } catch {} };
  wrap.addEventListener('input', saveDraft);
  wrap.addEventListener('change', saveDraft);

  const close = () => wrap.remove();
  wrap.querySelector('.pub-x').addEventListener('click', close);
  wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });

  const persistTaxonomyToProject = async () => {
    const form = collectForm();
    try {
      await api(`/api/projects/${currentProject.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          demo_org: form.org.trim(),
          demo_vertical: form.vertical,
          demo_use_case: form.use_case,
          demo_summary: form.summary,
          demo_sensitive: form.sensitive ? 1 : 0,
        }),
      });
      currentProject.demo_org = form.org.trim();
      currentProject.demo_vertical = form.vertical;
      currentProject.demo_use_case = form.use_case;
      currentProject.demo_summary = form.summary;
      currentProject.demo_sensitive = form.sensitive ? 1 : 0;
      fillDemoLibraryFields(currentProject);
    } catch { /* non-fatal */ }
  };

  pubGo.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    if (btn.disabled) return;
    const status = wrap.querySelector('#pubStatus');
    const form = collectForm();
    if (!form.title.trim()) { status.textContent = 'Title is required.'; return; }
    if (!form.vertical || !form.use_case) {
      status.textContent = 'Set Vertical and Use case (on the project or here) before publishing.';
      return;
    }
    btn.disabled = true;
    status.textContent = 'Publishing…';
    try {
      await persistTaxonomyToProject();
      const published = await api('/api/library/publish', {
        method: 'POST',
        body: JSON.stringify({
          render_file_path: render.file_path,
          source_project_id: currentProject.id,
          title: form.title.trim(),
          vertical: form.vertical,
          use_case: form.use_case,
          org: form.org.trim(),
          summary: form.summary,
          vidyard: form.vidyard.trim(),
          sensitive: form.sensitive,
          language: form.language,
          accent: form.accent,
          // Deliberately not part of collectForm(): this must never be
          // restored from a saved draft, only ticked for the publish at hand.
          replace_vidyard: wrap.querySelector('#pubReplaceVidyard').checked,
        }),
      });
      await followPublish(published.id, status, btn, close);
    } catch (err) {
      status.textContent = 'Failed: ' + err.message;
      btn.disabled = false;
    }
  });
}

const PUBLISH_PHASE_LABELS = {
  cleanup: 'Preparing',
  vidyard: 'Uploading to Vidyard',
  'push-video': 'Uploading video to the public site',
  'push-index': 'Updating the library index',
  done: 'Done',
};

// Poll a publish through to success or failure. On failure it offers a retry that
// resumes from the local file, so a network blip doesn't mean re-rendering.
function followPublish(libraryId, status, btn, close) {
  return new Promise((resolve) => {
    const tick = async () => {
      let s;
      try {
        s = await api(`/api/library/${libraryId}/publish-status`);
      } catch {
        setTimeout(tick, 2000);
        return;
      }
      if (!s.done) {
        const label = PUBLISH_PHASE_LABELS[s.phase] || 'Publishing';
        status.textContent = `${label}… ${s.percent}%${s.elapsedSeconds ? ` · ${fmtDuration(s.elapsedSeconds)}` : ''}`;
        setTimeout(tick, 2000);
        return;
      }
      if (s.status === 'failed') {
        status.innerHTML = `<strong>Publish failed at “${escapeHtml(PUBLISH_PHASE_LABELS[s.phase] || s.phase)}”:</strong> ${escapeHtml(s.error)}
          <div class="pub-warn">The video may already be live on the public site without a Vidyard
          player attached. Retry picks up from the local file — no re-render needed.</div>
          <button id="pubRetry" class="btn btn-small" type="button">↻ Retry publish</button>`;
        const retry = status.querySelector('#pubRetry');
        if (retry) {
          retry.addEventListener('click', async () => {
            retry.disabled = true;
            status.textContent = 'Retrying…';
            try {
              await api(`/api/library/${libraryId}/retry-publish`, { method: 'POST' });
              await followPublish(libraryId, status, btn, close);
            } catch (err) {
              status.textContent = 'Retry failed: ' + err.message;
            }
            resolve();
          });
        }
        btn.disabled = false;
        resolve();
        return;
      }
      const shareUrl = `https://www.clearspeeddemos.com/library/lib-${encodeURIComponent(libraryId)}`;
      const vy = s.vidyard_id
        ? ` · <a href="https://share.vidyard.com/watch/${encodeURIComponent(s.vidyard_id)}" target="_blank">open Vidyard</a>`
        : '';
      const superseded = s.vidyard_superseded && s.vidyard_superseded.length
        ? `<div class="pub-warn">Replaced Vidyard player ${escapeHtml(s.vidyard_superseded.join(', '))} —
           the old player still exists; delete it in Vidyard if nobody has the link.</div>`
        : '';
      const kept = s.vidyard_action === 'kept-existing'
        ? ' · Vidyard untouched (the player still plays the previous cut)'
        : '';
      status.innerHTML = `Published ✓ · <a href="${shareUrl}" target="_blank">share link</a>
        · <button type="button" class="btn btn-small" id="pubCopyShare">Copy share link</button>
        · <a href="/library/" target="_blank">open library</a>${vy}${kept}${superseded}`;
      const copyBtn = status.querySelector('#pubCopyShare');
      if (copyBtn) {
        copyBtn.addEventListener('click', async () => {
          try {
            await navigator.clipboard.writeText(shareUrl);
            copyBtn.textContent = 'Copied';
          } catch {
            window.prompt('Copy this share link (Clearspeed login required):', shareUrl);
          }
        });
      }
      setTimeout(close, superseded ? 9000 : (vy ? 4500 : 2800));
      resolve();
    };
    tick();
  });
}

function renderUrl(r) {
  return r.url || (r.file_path ? `/renders/${r.file_path}` : '');
}

function renderRenderHistory() {
  const list = el('renderList');
  list.innerHTML = '';
  for (const r of currentProject.renders || []) {
    const url = renderUrl(r);
    const filename = (r.file_path || `render.${r.format || 'mp3'}`).split('/').pop();
    const isVideo = r.format === 'mp4';
    const media = isVideo
      ? `<video controls src="${url}" style="max-width:260px;border-radius:6px"></video>`
      : `<audio controls src="${url}"></audio>`;
    const meta = isVideo
      ? `🎬 video · ${r.segment_count} segments · ${r.used_chars} questions`
      : `${r.segment_count} segments · ${r.used_chars} chars · ${r.format}`;
    const li = document.createElement('li');
    li.innerHTML = `
      <div>
        <div>${new Date(r.created_at).toLocaleString()}</div>
        <div class="render-meta">${meta}</div>
      </div>
      ${media}
      <a href="${url}" download="${filename}" class="btn btn-small">⭳ Download</a>
      ${isVideo ? '<button class="btn btn-small btn-publish">▲ Publish</button>' : ''}
      <button class="btn btn-small btn-danger">✕</button>
    `;
    if (isVideo) {
      li.querySelector('.btn-publish').addEventListener('click', () => openPublishModal(r));
    }
    // (delete handler is bound to .btn-danger specifically — see below)
    li.querySelector('.btn-danger').addEventListener('click', async () => {
      if (!confirm('Delete this render? This permanently removes the file.')) return;
      await api(`/api/renders/${r.id}`, { method: 'DELETE' });
      renderRenderHistory();
      await openProject(currentProject.id);
    });
    list.appendChild(li);
  }
}

async function createProject() {
  const project = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({ name: 'Untitled project' }),
  });
  await loadProjectList();
  await openProject(project.id);
  const nameInput = el('projName');
  nameInput.focus();
  nameInput.select();
}

function flash(msg) {
  const status = el('renderStatus');
  status.textContent = msg;
  setTimeout(() => {
    if (status.textContent === msg) status.textContent = '';
  }, 2000);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

el('newProjectBtn').addEventListener('click', createProject);
el('deleteProjectBtn').addEventListener('click', deleteProject);
el('translateBtn').addEventListener('click', openTranslateModal);
el('addSegmentBtn').addEventListener('click', () => {
  el('segments').appendChild(buildSegmentEl(defaultSegment()));
  renumberSegments();
  scheduleAutosave();
});

el('undoBtn').addEventListener('click', () => {
  const entry = undoStack.pop();
  if (!entry) return;
  const container = el('segments');
  const all = container.querySelectorAll('.segment');
  const node = buildSegmentEl(entry.data);
  if (entry.index >= 0 && entry.index < all.length) container.insertBefore(node, all[entry.index]);
  else container.appendChild(node);
  redoStack.push(entry);
  updateUndoRedoBtns();
  renumberSegments();
  scheduleAutosave();
});

el('redoBtn').addEventListener('click', () => {
  const entry = redoStack.pop();
  if (!entry) return;
  const all = [...el('segments').querySelectorAll('.segment')];
  const target = all[entry.index];
  if (target) target.remove();
  undoStack.push(entry);
  updateUndoRedoBtns();
  renumberSegments();
  scheduleAutosave();
});

// Wrapped, not passed directly: a listener would hand the Event object to
// fillTemplateCounts's `preferred` parameter and lose the current selection.
el('tplName').addEventListener('change', () => fillTemplateCounts());
el('tplCount').addEventListener('change', () => {
  el('tplHint').textContent = templateSummary(el('tplName').value, Number(el('tplCount').value));
});
el('applyTemplateBtn').addEventListener('click', applyTemplate);

el('defLang').addEventListener('change', () => fillDefaultFilters());
el('defAccent').addEventListener('change', () => fillDefaultFilters());
el('defSex').addEventListener('change', fillDefaultVoiceSelects);
el('defNarrator').addEventListener('change', () => applyDefaultVoice('narrator'));
el('defAnswer').addEventListener('change', () => applyDefaultVoice('answer'));

el('demoVertical').addEventListener('change', () => {
  fillDemoUseCases();
  if (el('demoUseCase').value === 'Sensitive') el('demoSensitive').checked = true;
});
el('demoUseCase').addEventListener('change', () => {
  if (el('demoUseCase').value === 'Sensitive') el('demoSensitive').checked = true;
});

// Any typing or control change in the editor triggers a debounced autosave.
el('editor').addEventListener('input', scheduleAutosave);
el('editor').addEventListener('change', scheduleAutosave);

// Last line of defense: block accidental tab close/refresh with unsaved edits.
window.addEventListener('beforeunload', (e) => {
  if (editorActive() && snapshotProject() !== lastSavedSnapshot) {
    writeLocalBackup();
    e.preventDefault();
    e.returnValue = '';
  }
});
el('renderBtn').addEventListener('click', renderProject);
el('renderVideoBtn').addEventListener('click', renderVideo);
el('renderSegmentsBtn').addEventListener('click', exportSegments);

// Collapsible left nav — state persists across reloads.
function setNavCollapsed(collapsed) {
  document.querySelector('.app').classList.toggle('nav-collapsed', collapsed);
  try { localStorage.setItem('navCollapsed', collapsed ? '1' : '0'); } catch {}
}
el('navCollapseBtn').addEventListener('click', () => setNavCollapsed(true));
el('navOpenBtn').addEventListener('click', () => setNavCollapsed(false));
try { setNavCollapsed(localStorage.getItem('navCollapsed') === '1'); } catch {}

(async function init() {
  await loadUseCases();
  try {
    await loadVoices();
  } catch (err) {
    alert('Could not load voices from Voicemaker: ' + err.message + '\n\nCheck your VOICEMAKER_API_KEY in .env');
  }
  await loadProjectList();
})();
