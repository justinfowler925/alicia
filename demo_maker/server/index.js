require('dotenv').config({ path: require('./lib/paths').ENV_FILE });
const path = require('path');
const fs = require('fs');
const express = require('express');
const db = require('./db');
const voicemaker = require('./lib/voicemaker');
const elevenlabs = require('./lib/elevenlabs');
const tts = require('./lib/tts');
const { renderProject, renderSegmentsSeparately, RENDERS_DIR } = require('./lib/audio');

// Every segment insert goes through the same column list, so adding a field is
// one edit instead of four that can silently drift apart.
const SEGMENT_COLUMNS = [
  'project_id', 'position', 'label', 'text', 'voice_id', 'language_code', 'effect',
  'master_volume', 'master_speed', 'master_pitch', 'pause_after_ms',
  'role', 'result', 'qtype', 'q_key', 'iteration', 'scored',
];
const INSERT_SEGMENT_SQL = `
  INSERT INTO segments (${SEGMENT_COLUMNS.join(', ')})
  VALUES (${SEGMENT_COLUMNS.map((c) => '@' + c).join(', ')})
`;

// Normalize one incoming/copied segment into exactly the insert's parameters.
function segmentParams(seg, projectId, position) {
  return {
    project_id: projectId,
    position,
    label: seg.label || '',
    text: seg.text || '',
    voice_id: seg.voice_id || '',
    language_code: seg.language_code || 'en-US',
    effect: seg.effect || 'default',
    master_volume: seg.master_volume ?? 0,
    master_speed: seg.master_speed ?? 0,
    master_pitch: seg.master_pitch ?? 0,
    pause_after_ms: seg.pause_after_ms ?? 400,
    role: seg.role || 'other',
    result: seg.result || '',
    qtype: seg.qtype || '',
    q_key: seg.q_key || '',
    iteration: Number(seg.iteration) || 0,
    scored: Number(seg.scored) ? 1 : 0,
  };
}

const app = express();
app.use(express.json({ limit: '2mb' }));

// Gate before static + APIs. When DEMO_MAKER_ACCESS_SECRET is unset (Studio /
// tailnet), this is a no-op except /healthz. When set, Nucleus must mint an
// embed URL — do not put a public origin in front of an ungated process.
require('./lib/access-gate').install(app);

app.use('/library/videos', express.static(require('./lib/paths').LIBRARY_DIR));
app.use(express.static(path.join(__dirname, '..', 'public'), {
  // Never serve a stale app shell / script — always revalidate so a cached
  // old app.js can't run outdated (buggy) code.
  setHeaders: (res, filePath) => {
    if (/\.(html|js)$/.test(filePath)) res.setHeader('Cache-Control', 'no-cache');
  },
}));
app.use('/renders', express.static(RENDERS_DIR));

// ---------- Library use cases ----------
// The library owns the list (Salesforce Opportunity use cases); proxied so the
// browser needs no cross-origin call.
const USE_CASES_URL = process.env.LIBRARY_USE_CASES_URL || 'https://demo.clearspeed.com/api/use-cases';
app.get('/api/use-cases', async (req, res) => {
  try {
    const r = await fetch(USE_CASES_URL, { headers: { 'User-Agent': 'alicia-demo-maker' } });
    if (!r.ok) throw new Error(`${USE_CASES_URL} ${r.status}`);
    res.json(await r.json());
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

// ---------- Voices ----------
// Pulls Voicemaker + (when configured) ElevenLabs and upserts into the cache.
// Upsert-only — the old refresh did DELETE + plain INSERT, and one duplicate
// VoiceId from the API aborted the transaction and left the cache EMPTY.
async function reloadVoicesCache() {
  const lists = [];
  lists.push((await voicemaker.listVoices()).map((v) => ({ ...v, __provider: 'voicemaker' })));
  if (elevenlabs.configured()) {
    lists.push((await elevenlabs.listVoices()).map((v) => ({ ...v, __provider: 'elevenlabs' })));
  }
  const insert = db.prepare(`
    INSERT INTO voices_cache (voice_id, name, language_code, gender, engine, raw_json, provider, fetched_at)
    VALUES (@voice_id, @name, @language_code, @gender, @engine, @raw_json, @provider, datetime('now'))
    ON CONFLICT(voice_id) DO UPDATE SET name=excluded.name, language_code=excluded.language_code,
      gender=excluded.gender, engine=excluded.engine, raw_json=excluded.raw_json,
      provider=excluded.provider, fetched_at=excluded.fetched_at
  `);
  let count = 0;
  const tx = db.transaction((all) => {
    for (const list of all) {
      for (const v of list) {
        insert.run({
          voice_id: v.VoiceId,
          name: v.VoiceWebname || v.VoiceId,
          language_code: v.Language || v.LanguageName || '',
          gender: v.VoiceGender || '',
          engine: v.Engine || '',
          raw_json: JSON.stringify(v),
          provider: v.__provider,
        });
        count += 1;
      }
    }
  });
  tx(lists);
  return count;
}

app.get('/api/voices', async (req, res) => {
  try {
    const cached = db.prepare('SELECT * FROM voices_cache').all();
    if (cached.length > 0) return res.json(cached.map(rowToVoice));
    await reloadVoicesCache();
    res.json(db.prepare('SELECT * FROM voices_cache').all().map(rowToVoice));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/voices/refresh', async (req, res) => {
  try {
    const count = await reloadVoicesCache();
    res.json({ count });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Raw VoiceGender is inconsistent ("Female", "female", "Female (Kid)",
// "Male (child)", "Neural"...) — collapse to a small consistent set.
function normalizeGender(g) {
  const s = (g || '').trim().toLowerCase();
  if (!s) return 'Unspecified';
  const child = /\((child|kid)\)/.test(s);
  if (s.startsWith('female')) return child ? 'Female (Child)' : 'Female';
  if (s.startsWith('male')) return child ? 'Male (Child)' : 'Male';
  if (s === 'neural' || s === 'neutral') return 'Neutral';
  return g.trim();
}

function rowToVoice(row) {
  // LanguageName comes in three shapes: "English, US" (accent after comma),
  // "Albanian (Albania)" (region in parens), or bare "Arabic" (Country field
  // is the only region hint).
  let language = '';
  let accent = '';
  let country = '';
  try {
    const v = JSON.parse(row.raw_json || '{}');
    country = v.Country || '';
    const ln = (v.LanguageName || '').trim();
    if (ln.includes(',')) {
      const parts = ln.split(',').map((s) => s.trim());
      language = parts[0];
      accent = parts.slice(1).join(', ');
    } else {
      const m = ln.match(/^(.*?)\s*\((.+)\)$/);
      if (m) {
        language = m[1].trim();
        accent = m[2].trim();
      } else {
        language = ln;
      }
    }
  } catch {}
  if (!language) language = row.language_code || 'Unknown';
  if (!accent) accent = country || 'Standard';
  return {
    voice_id: row.voice_id,
    name: row.name,
    language_code: row.language_code,
    gender: normalizeGender(row.gender),
    engine: row.engine,
    provider: row.provider || 'voicemaker',
    language,
    accent,
  };
}

// ---------- Projects ----------
app.get('/api/projects', (req, res) => {
  const projects = db.prepare('SELECT * FROM projects ORDER BY updated_at DESC').all();
  res.json(projects);
});

app.post('/api/projects', (req, res) => {
  const { name, description = '', output_format = 'mp3' } = req.body;
  if (!name) return res.status(400).json({ error: 'name is required' });
  const info = db
    .prepare('INSERT INTO projects (name, description, output_format) VALUES (?, ?, ?)')
    .run(name, description, output_format);
  res.json(db.prepare('SELECT * FROM projects WHERE id = ?').get(info.lastInsertRowid));
});

app.get('/api/projects/:id', (req, res) => {
  const project = db.prepare('SELECT * FROM projects WHERE id = ?').get(req.params.id);
  if (!project) return res.status(404).json({ error: 'not found' });
  const segments = db
    .prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC')
    .all(req.params.id);
  const renders = db
    .prepare('SELECT * FROM renders WHERE project_id = ? ORDER BY created_at DESC')
    .all(req.params.id)
    .map((r) => ({ ...r, url: `/renders/${r.file_path}` }));
  res.json({ ...project, segments, renders });
});

app.put('/api/projects/:id', (req, res) => {
  const { name, description, output_format, demo_org, demo_irn, demo_region,
          default_sex, narrator_voice_id, answer_voice_id, demo_format, demo_language,
          demo_vertical, demo_use_case, demo_summary, demo_sensitive } = req.body;
  const project = db.prepare('SELECT * FROM projects WHERE id = ?').get(req.params.id);
  if (!project) return res.status(404).json({ error: 'not found' });
  const nextLanguage = demo_language ?? project.demo_language ?? '';
  // Changing the UI language invalidates the chrome translation; the next render
  // regenerates it for the new language rather than painting the old one.
  const uiStrings = nextLanguage === (project.demo_language || '') ? (project.demo_ui_strings || '') : '';
  const nextSensitive = demo_sensitive == null
    ? (project.demo_sensitive ? 1 : 0)
    : (demo_sensitive ? 1 : 0);
  db.prepare(
    `UPDATE projects SET name = ?, description = ?, output_format = ?, demo_org = ?, demo_irn = ?, demo_region = ?, default_sex = ?, narrator_voice_id = ?, answer_voice_id = ?, demo_format = ?, demo_language = ?, demo_ui_strings = ?, demo_vertical = ?, demo_use_case = ?, demo_summary = ?, demo_sensitive = ?, updated_at = datetime('now') WHERE id = ?`
  ).run(
    name ?? project.name,
    description ?? project.description,
    output_format ?? project.output_format,
    demo_org ?? project.demo_org,
    demo_irn ?? project.demo_irn,
    demo_region ?? project.demo_region,
    default_sex ?? project.default_sex,
    narrator_voice_id ?? project.narrator_voice_id,
    answer_voice_id ?? project.answer_voice_id,
    demo_format ?? project.demo_format,
    nextLanguage,
    uiStrings,
    demo_vertical ?? project.demo_vertical ?? '',
    demo_use_case ?? project.demo_use_case ?? '',
    demo_summary ?? project.demo_summary ?? '',
    nextSensitive,
    req.params.id
  );
  res.json(db.prepare('SELECT * FROM projects WHERE id = ?').get(req.params.id));
});

app.delete('/api/projects/:id', (req, res) => {
  // Remove render files on disk before the DB rows cascade away, so nothing orphans.
  const renders = db.prepare('SELECT file_path FROM renders WHERE project_id = ?').all(req.params.id);
  for (const r of renders) {
    fs.rmSync(path.join(RENDERS_DIR, r.file_path), { force: true });
  }
  db.prepare('DELETE FROM projects WHERE id = ?').run(req.params.id);
  res.json({ ok: true });
});

// Clone a project (its settings + segments; renders are not copied).
app.post('/api/projects/:id/clone', (req, res) => {
  const src = db.prepare('SELECT * FROM projects WHERE id = ?').get(req.params.id);
  if (!src) return res.status(404).json({ error: 'not found' });

  const newId = db.transaction(() => {
    const info = db
      .prepare(
        'INSERT INTO projects (name, description, output_format, demo_org, demo_irn, demo_region, default_sex, narrator_voice_id, answer_voice_id, demo_format, demo_language, demo_ui_strings, demo_vertical, demo_use_case, demo_summary, demo_sensitive) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
      )
      .run(`${src.name} (copy)`, src.description, src.output_format, src.demo_org, src.demo_irn, src.demo_region,
        src.default_sex || '', src.narrator_voice_id || '', src.answer_voice_id || '', src.demo_format || 'sales',
        src.demo_language || '', src.demo_ui_strings || '',
        src.demo_vertical || '', src.demo_use_case || '', src.demo_summary || '', src.demo_sensitive ? 1 : 0);
    const id = info.lastInsertRowid;
    const segs = db.prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC').all(req.params.id);
    const insert = db.prepare(INSERT_SEGMENT_SQL);
    segs.forEach((s, i) => insert.run(segmentParams(s, id, i)));
    return id;
  })();

  res.json(db.prepare('SELECT * FROM projects WHERE id = ?').get(newId));
});

// Translate a project's segments into another language, saved as a new project.
// The translated text is stored on each segment (so it's displayed in the UI)
// and each segment is re-voiced with a matching voice in the target language.
// The player's own chrome is translated too, and the new project is stamped with
// the target language tag — a demo whose questions are in Arabic must not render
// under an English interface with a US flag in the language selector.
app.post('/api/projects/:id/translate', async (req, res) => {
  const { translateSegments, translateUiStrings } = require('./lib/translate');
  const { UI_EN, parseLocaleTag } = require('./lib/uiLocale');
  const src = db.prepare('SELECT * FROM projects WHERE id = ?').get(req.params.id);
  if (!src) return res.status(404).json({ error: 'not found' });
  const language = (req.body.language || '').trim();
  if (!language) return res.status(400).json({ error: 'language is required' });

  const segments = db.prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC').all(req.params.id);
  if (segments.length === 0) return res.status(400).json({ error: 'project has no segments' });

  // Resolve the target-language voice pool, matching each original segment's
  // gender where possible so the translated project keeps the same voice cast.
  const allVoices = db.prepare('SELECT * FROM voices_cache').all().map(rowToVoice);
  const inLang = allVoices.filter((v) => v.language === language);
  if (inLang.length === 0) {
    return res.status(400).json({ error: `No Voicemaker voices available for ${language}.` });
  }
  const pickVoice = (origVoiceId) => {
    const orig = allVoices.find((v) => v.voice_id === origVoiceId);
    const byGender = orig && inLang.find((v) => v.gender === orig.gender);
    return byGender || inLang[0];
  };

  try {
    // Only spoken segments need translating; text-less silence gaps stay empty.
    const spoken = segments.map((s, i) => ({ i, text: s.text })).filter((s) => s.text.trim());
    const translations = await translateSegments(spoken, language);

    // The player's UI language: the tag the target-language voices speak. Stamped
    // explicitly so it survives a later re-voice, and so an ElevenLabs re-voice
    // (those voices all report en-US) can't quietly flip the demo back to English.
    const langTag = parseLocaleTag(inLang[0].language_code || '').code;
    // Chrome translation is best-effort — a failure here leaves English labels on
    // a correctly flagged demo rather than losing the whole translation.
    let uiPack = '';
    if (!/^en\b/.test(langTag)) {
      try {
        uiPack = JSON.stringify({ code: langTag, t: await translateUiStrings(UI_EN, language) });
      } catch (err) {
        console.error(`UI chrome translation to ${language} failed: ${err.message}`);
      }
    }

    const newId = db.transaction(() => {
      const info = db
        .prepare('INSERT INTO projects (name, description, output_format, demo_org, demo_irn, demo_region, demo_format, demo_language, demo_ui_strings, demo_vertical, demo_use_case, demo_summary, demo_sensitive) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
        .run(`${src.name} — ${language}`, src.description, src.output_format, src.demo_org, src.demo_irn, src.demo_region, src.demo_format || 'sales', langTag, uiPack,
          src.demo_vertical || '', src.demo_use_case || '', src.demo_summary || '', src.demo_sensitive ? 1 : 0);
      const id = info.lastInsertRowid;
      const insert = db.prepare(INSERT_SEGMENT_SQL);
      segments.forEach((s, i) => {
        const voice = pickVoice(s.voice_id);
        insert.run(segmentParams({
          ...s,
          text: s.text.trim() ? (translations.get(i) ?? s.text) : s.text,
          voice_id: voice.voice_id,
          language_code: voice.language_code,
        }, id, i));
      });
      return id;
    })();

    res.json(db.prepare('SELECT * FROM projects WHERE id = ?').get(newId));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ---------- Templates ----------
// A template stamps out the whole segment list for a known demo format, tagged
// so the video builder knows which asks share a question row and which score.
const { TEMPLATES, buildSegments } = require('./lib/templates');

app.get('/api/templates', (req, res) => {
  res.json(Object.values(TEMPLATES).map((t) => ({
    id: t.id,
    label: t.label,
    counts: t.counts,
    describe: t.counts.map((n) => ({ count: n, text: t.describe(n) })),
  })));
});

app.post('/api/projects/:id/apply-template', (req, res) => {
  const projectId = req.params.id;
  const project = db.prepare('SELECT * FROM projects WHERE id = ?').get(projectId);
  if (!project) return res.status(404).json({ error: 'not found' });

  const template = String(req.body.template || '');
  const count = Number(req.body.count);
  const spec = TEMPLATES[template];
  if (!spec) return res.status(400).json({ error: `unknown template: ${template || '(none)'}` });
  if (!spec.counts.includes(count)) {
    return res.status(400).json({ error: `${spec.label} supports ${spec.counts.join(', ')} questions — got ${req.body.count}` });
  }

  // Stamp the project's own default voices onto the new segments so the result
  // is renderable immediately rather than needing every segment re-voiced.
  const voiceLang = (id) => {
    const row = id ? db.prepare('SELECT language_code FROM voices_cache WHERE voice_id = ?').get(id) : null;
    return (row && row.language_code) || 'en-US';
  };
  const segments = buildSegments({
    template,
    count,
    voices: {
      narrator: project.narrator_voice_id || '',
      answer: project.answer_voice_id || '',
      narratorLang: voiceLang(project.narrator_voice_id),
      answerLang: voiceLang(project.answer_voice_id || project.narrator_voice_id),
    },
  });

  const tx = db.transaction(() => {
    db.prepare('DELETE FROM segments WHERE project_id = ?').run(projectId);
    const insert = db.prepare(INSERT_SEGMENT_SQL);
    segments.forEach((seg, i) => insert.run(segmentParams(seg, projectId, i)));
    db.prepare(`UPDATE projects SET demo_format = ?, updated_at = datetime('now') WHERE id = ?`)
      .run(template, projectId);
  });
  tx();

  const saved = db.prepare('SELECT * FROM projects WHERE id = ?').get(projectId);
  res.json({
    ...saved,
    segments: db.prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC').all(projectId),
  });
});

/**
 * Repair question keys on a project that was built before templates existed, or
 * whose keys were allocated wrongly.
 *
 * Groups question segments by (class, question text) and gives every copy the
 * same key, numbered by first appearance. That shared key is what collapses the
 * two iterations onto one row on screen. Also fills `iteration` from the
 * transition segment's position and marks the second-iteration pertinent
 * questions scored, which is the definition of the production format.
 *
 * Text is never modified — only the tags.
 */
app.post('/api/projects/:id/rekey-questions', (req, res) => {
  const projectId = req.params.id;
  const project = db.prepare('SELECT * FROM projects WHERE id = ?').get(projectId);
  if (!project) return res.status(404).json({ error: 'not found' });
  const segments = db.prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC').all(projectId);

  const norm = (s) => (s || '').trim().toLowerCase().replace(/\s+/g, ' ');
  // Everything after the transition segment is the second pass.
  const transitionAt = segments.findIndex((s) => s.role === 'transition'
    || (s.role === 'other' && /same questions a second time/i.test(s.text || '')));
  const twoIterations = transitionAt >= 0;

  const keyByText = new Map();
  const counters = { PQ: 0, NQ: 0 };
  const updates = [];
  for (const s of segments) {
    if (s.role !== 'question') continue;
    const qtype = (s.qtype || 'PQ') === 'NQ' ? 'NQ' : 'PQ';
    const sig = `${qtype}|${norm(s.text)}`;
    let key = keyByText.get(sig);
    if (!key) {
      counters[qtype] += 1;
      key = `${qtype}${counters[qtype]}`;
      keyByText.set(sig, key);
    }
    const iteration = twoIterations ? (s.position > transitionAt ? 2 : 1) : 1;
    const scored = twoIterations ? (iteration === 2 && qtype === 'PQ' ? 1 : 0) : 1;
    updates.push({ id: s.id, qtype, key, iteration, scored });
  }

  const tx = db.transaction(() => {
    const up = db.prepare('UPDATE segments SET qtype = ?, q_key = ?, iteration = ?, scored = ? WHERE id = ?');
    for (const u of updates) up.run(u.qtype, u.key, u.iteration, u.scored, u.id);
    // An answer inherits its question's tags so the pair stays coherent.
    const q = db.prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC').all(projectId);
    let cur = null;
    for (const s of q) {
      if (s.role === 'question') cur = s;
      else if (s.role === 'answer' && cur) {
        db.prepare('UPDATE segments SET qtype = ?, q_key = ?, iteration = ? WHERE id = ?')
          .run(cur.qtype, cur.q_key, cur.iteration, s.id);
      }
    }
    db.prepare(`UPDATE projects SET demo_format = ?, updated_at = datetime('now') WHERE id = ?`)
      .run(twoIterations ? 'production' : project.demo_format, projectId);
  });
  tx();

  const distinct = new Set(updates.map((u) => u.key)).size;
  res.json({
    ok: true,
    demo_format: twoIterations ? 'production' : project.demo_format,
    asks: updates.length,
    distinctQuestions: distinct,
    scored: updates.filter((u) => u.scored).length,
    keys: updates.map((u) => `${u.key}/it${u.iteration}${u.scored ? '*' : ''}`),
  });
});

// ---------- Segments ----------
app.put('/api/projects/:id/segments', (req, res) => {
  // Replace the full segment list for a project (simplest way to handle add/reorder/edit/delete together)
  const projectId = req.params.id;
  const project = db.prepare('SELECT * FROM projects WHERE id = ?').get(projectId);
  if (!project) return res.status(404).json({ error: 'not found' });

  const segments = Array.isArray(req.body.segments) ? req.body.segments : [];
  const tx = db.transaction(() => {
    db.prepare('DELETE FROM segments WHERE project_id = ?').run(projectId);
    const insert = db.prepare(INSERT_SEGMENT_SQL);
    segments.forEach((seg, i) => insert.run(segmentParams(seg, projectId, i)));
    db.prepare(`UPDATE projects SET updated_at = datetime('now') WHERE id = ?`).run(projectId);
  });
  tx();

  const saved = db.prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC').all(projectId);
  res.json(saved);
});

// ---------- Preview a single segment (no save) ----------
app.post('/api/segments/preview', async (req, res) => {
  try {
    if (!(req.body.text || '').trim()) {
      return res.status(400).json({ error: 'This segment has no text — it will render as a silence gap.' });
    }
    const result = await tts.convert(req.body);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ---------- Render a project ----------
app.post('/api/projects/:id/render', async (req, res) => {
  const projectId = req.params.id;
  const project = db.prepare('SELECT * FROM projects WHERE id = ?').get(projectId);
  if (!project) return res.status(404).json({ error: 'not found' });
  const segments = db
    .prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC')
    .all(projectId);
  if (segments.length === 0) return res.status(400).json({ error: 'project has no segments' });
  if (segments.every((s) => !s.text.trim())) {
    return res.status(400).json({ error: 'project has no segments with text' });
  }

  try {
    let usedChars = 0;
    const clipUrls = [];
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      // Text-less segments are silence gaps — no Voicemaker call.
      if (!seg.text.trim()) {
        clipUrls.push(null);
        continue;
      }
      try {
        const result = await tts.convert(seg);
        clipUrls.push(result.path);
        usedChars += result.usedChars || seg.text.length;
      } catch (err) {
        const name = seg.label ? `"${seg.label}"` : `#${i + 1}`;
        throw new Error(`Segment ${name}: ${err.message}`);
      }
    }

    const outPath = await renderProject(projectId, segments, clipUrls, project.output_format);
    const relPath = path.relative(RENDERS_DIR, outPath);

    const info = db
      .prepare(
        'INSERT INTO renders (project_id, file_path, format, used_chars, segment_count) VALUES (?, ?, ?, ?, ?)'
      )
      .run(projectId, relPath, project.output_format, usedChars, segments.length);

    res.json({
      ...db.prepare('SELECT * FROM renders WHERE id = ?').get(info.lastInsertRowid),
      url: `/renders/${relPath}`,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ---------- Export each segment as its own audio file (wav/mp3) ----------
app.post('/api/projects/:id/render-segments', async (req, res) => {
  const projectId = req.params.id;
  const project = db.prepare('SELECT * FROM projects WHERE id = ?').get(projectId);
  if (!project) return res.status(404).json({ error: 'not found' });
  const format = req.body.format === 'wav' ? 'wav' : 'mp3';
  const segments = db
    .prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC')
    .all(projectId);
  const spoken = segments.filter((s) => s.text.trim());
  if (spoken.length === 0) return res.status(400).json({ error: 'project has no segments with text' });

  try {
    // Request each spoken segment directly in the target format so wav exports
    // aren't a lossy mp3 re-encode. Text-less segments export nothing.
    const clipUrls = [];
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      if (!seg.text.trim()) { clipUrls.push(null); continue; }
      try {
        const result = await tts.convert({ ...seg, output_format: format });
        clipUrls.push(result.path);
      } catch (err) {
        const name = seg.label ? `"${seg.label}"` : `#${i + 1}`;
        throw new Error(`Segment ${name}: ${err.message}`);
      }
    }

    // Every export also carries the standard Clearspeed system audio — the
    // intros, retry prompts, transitions, closings and a 3s silence — rendered
    // in this demo's own narrator voice. They are not segments and never touch
    // the project; they just ride along in the zip.
    const standard = require('./lib/standardAudio');
    const voice = standard.narratorVoiceFor(project, segments);
    let standardFailed = [];
    const renderExtras = voice
      ? async (segDir) => {
        const out = await standard.renderStandardComponents({ destDir: segDir, voice, format });
        standardFailed = out.failed;
        return out.files;
      }
      : null;

    const { files, zip, extras } = await renderSegmentsSeparately(
      projectId, segments, clipUrls, format, renderExtras,
    );
    // Surface what was skipped so a segment with its content accidentally in
    // the label (empty text box) doesn't silently vanish from the export.
    const skipped = segments
      .map((s, i) => (!s.text.trim() ? (s.label || `#${i + 1}`) : null))
      .filter(Boolean);
    res.json({
      files: files.map((f) => ({ ...f, url: `/renders/${f.relPath}` })),
      zip: zip ? { url: `/renders/${zip}`, filename: `${project.name.replace(/[^a-z0-9\-_ ]+/gi, '').trim() || 'project'}-segments.zip` } : null,
      skipped,
      standard: {
        files: (extras || []).map((e) => ({ ...e, url: `/renders/${e.relPath}` })),
        failed: standardFailed,
        // The component wording is fixed English. Say so when the narrator
        // isn't an English voice, rather than shipping mangled audio quietly.
        voice: voice ? `${voice.voice_id} (${voice.language_code})` : '',
        englishTextNonEnglishVoice: Boolean(voice) && !/^en[-_]/i.test(voice.language_code || ''),
      },
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ---------- Render a project to a demo video (mp4) ----------
// Runs in the background so the UI can poll progress. Jobs are kept in memory.
const videoJobs = new Map();

/**
 * The `ui` block for a render — which language the player paints ITSELF in, and
 * which flag its language selector shows.
 *
 * The chrome translation is generated on the first render of a non-English demo
 * and stored on the project, so demos translated before this existed pick it up
 * by being re-rendered, and later renders cost nothing. A translation failure
 * degrades to English labels (with the right flag and language name) rather than
 * failing the render.
 */
async function resolveRenderUi(project, segments) {
  const { resolveDemoLocale, buildUiConfig, isEnglish, hasPackFor, englishName, UI_EN } = require('./lib/uiLocale');
  const locale = resolveDemoLocale(project, segments);
  let stored = project.demo_ui_strings || '';

  if (!isEnglish(locale) && !hasPackFor(stored, locale.code)) {
    try {
      const { translateUiStrings } = require('./lib/translate');
      const t = await translateUiStrings(UI_EN, englishName(locale));
      stored = JSON.stringify({ code: locale.code, t });
      db.prepare(`UPDATE projects SET demo_ui_strings = ?, updated_at = datetime('now') WHERE id = ?`)
        .run(stored, project.id);
    } catch (err) {
      console.error(`UI chrome translation for ${locale.code} failed: ${err.message}`);
    }
  }
  return buildUiConfig(locale, stored, project.demo_region);
}

app.post('/api/projects/:id/render-video', (req, res) => {
  const projectId = req.params.id;
  const project = db.prepare('SELECT * FROM projects WHERE id = ?').get(projectId);
  if (!project) return res.status(404).json({ error: 'not found' });
  const segments = db
    .prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position ASC')
    .all(projectId);
  if (segments.length === 0) return res.status(400).json({ error: 'project has no segments' });

  const jobId = String(Date.now());
  const job = {
    phase: 'starting', startedAt: Date.now(), framesStartedAt: null, done: false,
    current: 0, total: 0, framesDone: 0, framesTotal: 0, url: null, error: null,
  };
  videoJobs.set(jobId, job);

  (async () => {
    try {
      const { buildDemoVideo } = require('./lib/demoVideo');
      const ui = await resolveRenderUi(project, segments);
      const { outPath } = await buildDemoVideo(project, segments, (p) => {
        Object.assign(job, p);
        if (p.phase === 'frames' && !job.framesStartedAt && p.framesDone === 0) job.framesStartedAt = Date.now();
      }, ui);
      const relPath = path.relative(RENDERS_DIR, outPath);
      // Distinct questions, not asks — a production demo asks each one twice and
      // "20 questions" for a 10-question demo would just read as wrong.
      const asks = segments.filter((s) => s.role === 'question');
      const questionCount = new Set(asks.map((s, i) => (s.q_key || '').trim() || `__pos${i}`)).size;
      db.prepare(
        'INSERT INTO renders (project_id, file_path, format, used_chars, segment_count) VALUES (?, ?, ?, ?, ?)'
      ).run(projectId, relPath, 'mp4', questionCount, segments.length);
      job.url = `/renders/${relPath}`;
      job.phase = 'done';
      job.done = true;
    } catch (err) {
      job.error = err.message;
      job.phase = 'error';
      job.done = true;
    }
  })();

  res.json({ jobId });
});

app.get('/api/render-jobs/:jobId', (req, res) => {
  const j = videoJobs.get(req.params.jobId);
  if (!j) return res.status(404).json({ error: 'unknown job' });
  const elapsedSeconds = Math.round((Date.now() - j.startedAt) / 1000);
  let percent = 0;
  let etaSeconds = null;
  if (j.phase === 'speech') percent = j.total ? Math.round((j.current / j.total) * 12) : 0;
  else if (j.phase === 'audio') percent = 13;
  else if (j.phase === 'frames' || j.phase === 'encoding') {
    const fp = j.framesTotal ? j.framesDone / j.framesTotal : 0;
    percent = 15 + Math.round(fp * 82);
    if (j.framesStartedAt && j.framesDone > 0) {
      const el = (Date.now() - j.framesStartedAt) / 1000;
      const rate = j.framesDone / el;
      if (rate > 0) etaSeconds = Math.max(0, Math.round((j.framesTotal - j.framesDone) / rate));
    }
    if (j.phase === 'encoding') percent = 98;
  } else if (j.phase === 'done') { percent = 100; etaSeconds = 0; }
  res.json({
    phase: j.phase, percent, etaSeconds, elapsedSeconds,
    current: j.current, total: j.total, framesDone: j.framesDone, framesTotal: j.framesTotal,
    done: j.done, url: j.url, error: j.error,
  });
});

app.get('/api/projects/:id/renders', (req, res) => {
  const renders = db
    .prepare('SELECT * FROM renders WHERE project_id = ? ORDER BY created_at DESC')
    .all(req.params.id)
    .map((r) => ({ ...r, url: `/renders/${r.file_path}` }));
  res.json(renders);
});

app.delete('/api/renders/:id', (req, res) => {
  const render = db.prepare('SELECT * FROM renders WHERE id = ?').get(req.params.id);
  if (render) {
    const full = path.join(RENDERS_DIR, render.file_path);
    fs.rmSync(full, { force: true });
    db.prepare('DELETE FROM renders WHERE id = ?').run(req.params.id);
  }
  res.json({ ok: true });
});

// ---------- Demo library ----------
const { LIBRARY_DIR } = require('./lib/paths');

function ffprobeDurationSync(file) {
  try {
    const { execFileSync } = require('child_process');
    const out = execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file]);
    return Math.round(parseFloat(String(out).trim()) || 0);
  } catch { return 0; }
}

app.get('/api/library', (req, res) => {
  const rows = db.prepare('SELECT * FROM library ORDER BY published_at DESC').all()
    .map((r) => ({ ...r, src: `/library/videos/${path.basename(r.file_path)}` }));
  res.json(rows);
});

// Accept a full Vidyard URL (share.vidyard.com/watch/<uuid>, play.vidyard.com/<uuid>, …) or a bare id.
function parseVidyardId(v) {
  if (!v) return '';
  const s = String(v).trim();
  const m = s.match(/([0-9a-zA-Z_-]{16,})(?:\.html)?\/?$/);
  return m ? m[1].replace(/\.html$/, '') : s;
}

app.get('/api/vidyard/status', (req, res) => {
  const vidyard = require('./lib/vidyard');
  res.json({
    configured: vidyard.configured(),
    mode: require('./lib/vidyardWidgetUpload').configured()
      ? 'widget'
      : (vidyard.apiConfigured() ? 'api' : 'none'),
  });
});

// ---------- Post-publish pipeline ----------
// Pushing the mp4 to GitHub, waiting for Vercel to serve it, and having Vidyard
// import it takes minutes, so it runs as a tracked background job rather than
// holding the publish request open. Progress lives in memory for the live UI and
// is mirrored onto the library row so a failure survives a restart and stays
// visible instead of becoming a console line nobody reads.
const publishJobs = new Map(); // libraryId -> { phase, error, done, startedAt }
// Ids with a pipeline in flight. A second run for the same entry would upload
// the same mp4 to Vidyard again — the widget has no idempotency key, so every
// call mints another player.
const publishRunning = new Set();

const PUBLISH_PHASES = ['cleanup', 'vidyard', 'push-video', 'push-index', 'done'];

function setPublishState(id, { phase, status, error }) {
  const job = publishJobs.get(id) || { startedAt: Date.now(), done: false };
  if (phase) job.phase = phase;
  if (error !== undefined) job.error = error;
  job.done = status === 'ready' || status === 'failed';
  job.status = status || job.status || 'pending';
  publishJobs.set(id, job);
  db.prepare('UPDATE library SET publish_status = ?, publish_phase = ?, publish_error = ? WHERE id = ?')
    .run(job.status, job.phase || '', error || '', id);
}

/**
 * Push a published library entry to the public site and upload the mp4 into
 * Vidyard in the background (headless widget — no Dashboard API needed).
 *
 * Order: local file → Vidyard player UUID → public mp4 + library.json. The
 * index is written last so it lands with the player uuid already set.
 */
async function runPublishPipeline(id, { oldName, autoUploadToVidyard, previousVidyardId = '', title, org, vertical, use_case }) {
  const sync = require('./lib/publicSync');
  const vidyardService = require('./lib/vidyard');
  if (publishRunning.has(id)) {
    console.warn(`[publish] lib-${id}: a publish is already running — ignoring the duplicate request`);
    return;
  }
  publishRunning.add(id);
  setPublishState(id, { phase: 'cleanup', status: 'pending', error: '' });
  let pushedVideo = false;
  try {
    let row = db.prepare('SELECT * FROM library WHERE id = ?').get(id);
    if (!row) throw new Error('library entry disappeared mid-publish');
    const libName = path.basename(row.file_path);
    const localFile = path.join(LIBRARY_DIR, libName);

    if (oldName && oldName !== libName) await sync.unpublishFromPublic(oldName);

    if (autoUploadToVidyard) {
      // Headless widget upload of the local file. Every upload mints a NEW
      // player — the widget cannot swap the video inside an existing one — so
      // this branch is reserved for entries with no player yet, or an explicit
      // "replace the Vidyard video" request. Anything looser turns each
      // republish into another copy of the same demo in the Vidyard library.
      setPublishState(id, { phase: 'vidyard' });
      const player = await vidyardService.uploadLocalFile({
        filePath: localFile,
        name: title,
        description: [org, vertical, use_case].filter(Boolean).join(' · '),
      });
      db.prepare('UPDATE library SET vidyard_id = ?, vidyard_action = ? WHERE id = ?')
        .run(player.uuid, 'created', id);
      if (previousVidyardId && previousVidyardId !== player.uuid) {
        const superseded = (row.vidyard_superseded || '').split(/\s+/).filter(Boolean);
        if (!superseded.includes(previousVidyardId)) superseded.push(previousVidyardId);
        db.prepare('UPDATE library SET vidyard_superseded = ? WHERE id = ?')
          .run(superseded.join(' '), id);
      }
      console.log(`[vidyard] created ${player.uuid} for library item ${id} via ${player.via}`);
    } else {
      db.prepare('UPDATE library SET vidyard_action = ? WHERE id = ?')
        .run(previousVidyardId ? 'kept-existing' : '', id);
      if (previousVidyardId) {
        console.log(`[vidyard] lib-${id}: kept existing player ${previousVidyardId}`);
      }
    }

    setPublishState(id, { phase: 'push-video' });
    row = db.prepare('SELECT * FROM library WHERE id = ?').get(id);
    await sync.publishVideoFile(row);
    pushedVideo = true;

    setPublishState(id, { phase: 'push-index' });
    await sync.putLibraryJson();

    setPublishState(id, { phase: 'done', status: 'ready', error: '' });
    console.log(`[publicSync] published lib-${id} to public site`);
  } catch (err) {
    // Loud on purpose: at this point the video may be public with no player
    // attached, which is exactly the half-finished state that must not pass for
    // success.
    const job = publishJobs.get(id) || {};
    console.error(`[publish] lib-${id} FAILED at ${job.phase}: ${err.message}`);
    setPublishState(id, { status: 'failed', error: err.message });
    // If the new video already reached the public site, still write the index so
    // it references the file that now exists. Otherwise a republish that fails
    // after `cleanup` removed the old file leaves the live gallery pointing at a
    // deleted mp4 — a broken video is a far worse failure than a missing Vidyard
    // player, which simply falls back to playing the public mp4.
    if (pushedVideo) {
      try {
        await sync.putLibraryJson();
        console.error(`[publish] lib-${id} index written anyway so the public gallery still plays`);
      } catch (e2) {
        console.error(`[publish] lib-${id} index repair ALSO failed: ${e2.message}`);
      }
    }
  } finally {
    publishRunning.delete(id);
  }
}

function publishStatusFor(id) {
  const row = db.prepare('SELECT publish_status, publish_phase, publish_error, vidyard_id, vidyard_superseded, vidyard_action FROM library WHERE id = ?').get(id);
  if (!row) return null;
  const job = publishJobs.get(Number(id)) || {};
  const status = job.status || row.publish_status || '';
  const phase = job.phase || row.publish_phase || '';
  const idx = PUBLISH_PHASES.indexOf(phase);
  return {
    status,
    phase,
    error: job.error !== undefined ? (job.error || '') : (row.publish_error || ''),
    percent: status === 'ready' ? 100 : idx < 0 ? 0 : Math.round((idx / (PUBLISH_PHASES.length - 1)) * 100),
    done: status === 'ready' || status === 'failed',
    vidyard_id: row.vidyard_id || '',
    vidyard_superseded: (row.vidyard_superseded || '').split(/\s+/).filter(Boolean),
    vidyard_action: row.vidyard_action || '',
    elapsedSeconds: job.startedAt ? Math.round((Date.now() - job.startedAt) / 1000) : null,
  };
}

app.get('/api/library/:id/publish-status', (req, res) => {
  const s = publishStatusFor(req.params.id);
  if (!s) return res.status(404).json({ error: 'not found' });
  res.json(s);
});

// Re-run the pipeline for an entry that already has its local file — used to
// recover from a failed push or Vidyard import without re-rendering.
app.post('/api/library/:id/retry-publish', (req, res) => {
  const row = db.prepare('SELECT * FROM library WHERE id = ?').get(req.params.id);
  if (!row) return res.status(404).json({ error: 'not found' });
  const local = path.join(LIBRARY_DIR, path.basename(row.file_path));
  if (!fs.existsSync(local)) {
    return res.status(400).json({ error: `the local video file is gone (${path.basename(row.file_path)}) — re-publish from the render instead` });
  }
  const vidyardService = require('./lib/vidyard');
  const id = row.id;
  runPublishPipeline(id, {
    oldName: null, // the file is already the current one; nothing to unpublish
    autoUploadToVidyard: !row.vidyard_id && vidyardService.configured(),
    previousVidyardId: row.vidyard_id || '',
    title: row.title, org: row.org, vertical: row.vertical, use_case: row.use_case,
  });
  res.json({ ok: true, ...publishStatusFor(id) });
});

app.post('/api/library/publish', (req, res) => {
  const { render_file_path, source_project_id, title, vertical, use_case, org = '', summary = '', vidyard = '', sensitive = false, language = '', accent = '', replace_vidyard = false } = req.body || {};
  if (!render_file_path || !title || !vertical || !use_case) {
    return res.status(400).json({ error: 'render_file_path, title, vertical, and use_case are required' });
  }
  const srcFile = path.join(RENDERS_DIR, render_file_path);
  if (!fs.existsSync(srcFile)) return res.status(404).json({ error: 'render file not found' });

  try {
    fs.mkdirSync(LIBRARY_DIR, { recursive: true });
    const duration = ffprobeDurationSync(srcFile);
    // Re-publishing a project replaces its existing library entry — one entry
    // per project, never a duplicate row/file.
    const existing = source_project_id
      ? db.prepare('SELECT * FROM library WHERE source_project_id = ? ORDER BY id DESC LIMIT 1').get(source_project_id)
      : null;
    const vidyardService = require('./lib/vidyard');
    const requestedVidyardId = parseVidyardId(vidyard);
    // Always carry the existing player forward. An auto-upload replaces it only
    // once Vidyard has actually returned a new uuid, so a failed import leaves
    // the entry pointing at the player that still works rather than at nothing.
    const previousVidyardId = existing ? (existing.vidyard_id || '') : '';
    // Upload automatically only when the entry has no player yet. The widget
    // can't replace the video inside a player, so uploading on every republish
    // piled a fresh copy of the same demo into the Vidyard library each time.
    // Replacing an existing player is now an explicit, per-publish request.
    const autoUploadToVidyard = !requestedVidyardId
      && vidyardService.configured()
      && (!previousVidyardId || replace_vidyard === true);
    const vidyardId = requestedVidyardId || previousVidyardId;
    let id;
    if (existing) {
      id = existing.id;
      db.prepare(
        "UPDATE library SET title = ?, vertical = ?, use_case = ?, org = ?, summary = ?, duration_sec = ?, vidyard_id = ?, sensitive = ?, language = ?, accent = ?, published_at = datetime('now') WHERE id = ?"
      ).run(title, vertical, use_case, org, summary, duration, vidyardId, sensitive ? 1 : 0, language, accent, id);
      if (requestedVidyardId && previousVidyardId && requestedVidyardId !== previousVidyardId) {
        const superseded = (existing.vidyard_superseded || '').split(/\s+/).filter(Boolean);
        if (!superseded.includes(previousVidyardId)) superseded.push(previousVidyardId);
        db.prepare('UPDATE library SET vidyard_superseded = ? WHERE id = ?')
          .run(superseded.join(' '), id);
      }
    } else {
      const info = db.prepare(
        'INSERT INTO library (title, vertical, use_case, org, summary, file_path, duration_sec, vidyard_id, source_project_id, sensitive, language, accent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
      ).run(title, vertical, use_case, org, summary, 'pending', duration, vidyardId, source_project_id || null, sensitive ? 1 : 0, language, accent);
      id = info.lastInsertRowid;
    }
    // node:sqlite hands back a BigInt rowid on insert; the job Map/Set and
    // publish-status lookups key on a plain number.
    id = Number(id);
    // Every publish gets a fresh URL. Besides keeping sensitive URLs
    // unguessable, this prevents Vidyard from importing the previous Vercel
    // deployment while a replacement video is still rolling out.
    const libName = `lib-${id}-${require('crypto').randomBytes(6).toString('hex')}.mp4`;
    fs.copyFileSync(srcFile, path.join(LIBRARY_DIR, libName));
    db.prepare('UPDATE library SET file_path = ? WHERE id = ?').run(libName, id);
    // Sensitive names rotate on every publish; drop the superseded local file.
    const oldName = existing ? path.basename(existing.file_path) : null;
    if (oldName && oldName !== libName) {
      fs.rmSync(path.join(LIBRARY_DIR, oldName), { force: true });
    }
    // The row and the local file are done, so answer now. Everything that
    // touches the network — GitHub, Vercel, Vidyard — runs as a tracked job the
    // UI polls, because together it takes minutes.
    setPublishState(id, { phase: 'cleanup', status: 'pending', error: '' });
    const row = db.prepare('SELECT * FROM library WHERE id = ?').get(id);
    res.json({ ...row, src: `/library/videos/${libName}`, publish_status: 'pending' });

    runPublishPipeline(id, {
      oldName, autoUploadToVidyard, previousVidyardId, title, org, vertical, use_case,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/library/:id', (req, res) => {
  const row = db.prepare('SELECT * FROM library WHERE id = ?').get(req.params.id);
  if (row) {
    fs.rmSync(path.join(LIBRARY_DIR, path.basename(row.file_path)), { force: true });
    db.prepare('DELETE FROM library WHERE id = ?').run(req.params.id);
  }
  res.json({ ok: true });
  if (row) {
    require('./lib/publicSync').unpublishFromPublic(path.basename(row.file_path))
      .catch((e) => console.error('[publicSync] unpublish failed:', e.message));
  }
});

// Manual full resync of the public site (also runs automatically on publish).
app.post('/api/library/sync', async (req, res) => {
  try {
    const n = await require('./lib/publicSync').syncAll();
    res.json({ ok: true, synced: n });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Email gate before download — capture the lead, then the client proceeds.
app.post('/api/library/gate', async (req, res) => {
  const { email, library_id, title } = req.body || {};
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return res.status(400).json({ error: 'valid email required' });
  db.prepare('INSERT INTO library_leads (email, library_id, title, ip, ua) VALUES (?, ?, ?, ?, ?)')
    .run(email, library_id || null, title || '', String(req.headers['x-forwarded-for'] || req.socket.remoteAddress || ''), req.headers['user-agent'] || '');
  if (process.env.FORWARD_WEBHOOK_URL) {
    fetch(process.env.FORWARD_WEBHOOK_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'download', email, library_id, title, at: new Date().toISOString() }) }).catch(() => {});
  }
  res.json({ ok: true });
});

// Watch events (play / complete) — basic analytics; Vidyard will supersede this.
app.post('/api/library/event', (req, res) => {
  const { event, library_id, email } = req.body || {};
  if (!event) return res.status(400).json({ error: 'event required' });
  db.prepare('INSERT INTO library_events (event, library_id, email) VALUES (?, ?, ?)').run(event, library_id || null, email || null);
  if (process.env.FORWARD_WEBHOOK_URL && (event === 'complete' || email)) {
    fetch(process.env.FORWARD_WEBHOOK_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'watch', event, library_id, email, at: new Date().toISOString() }) }).catch(() => {});
  }
  res.json({ ok: true });
});

const PORT = process.env.PORT || 4173;
// 0.0.0.0 so a container's port mapping can reach it; localhost-only binding
// makes the process unreachable from outside the container.
const HOST = process.env.HOST || '0.0.0.0';
app.listen(PORT, HOST, () => {
  console.log(`Fowler Demo Maker running at http://localhost:${PORT}`);
});
