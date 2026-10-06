/**
 * Standard Clearspeed assessment audio — the fixed system prompts every demo
 * shares: the two intros, the retry/quality prompts the system plays when an
 * answer can't be used, the iteration transitions, both closings, and a bare
 * three seconds of silence.
 *
 * These are NOT segments. They never enter a project's segment list, never show
 * up in the editor, and never affect a rendered demo video. They are rendered
 * alongside the project's own segments and dropped into the "download all" zip,
 * so every export ships the complete set of stock audio in the demo's own voice.
 *
 * The wording is fixed and English. Audio for a non-English demo therefore comes
 * out as English text in that demo's narrator voice, which the export result
 * flags rather than silently shipping.
 */
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

const tts = require('./tts');
const { makeSilence, concatenate, downloadFile, TMP_DIR, RENDERS_DIR } = require('./audio');

/**
 * `text` components are spoken; the one `silenceMs` component is generated.
 *
 * Order is the running order of an assessment — intros, the in-call prompts,
 * transitions, closings — then the utility silence last, so the files sort into
 * the shape of the call rather than alphabetically.
 */
const STANDARD_COMPONENTS = [
  {
    id: 'intro-1-claims',
    label: 'Intro 1 - Claims',
    text: 'Hello. We are very sorry to learn about the loss to your property.\n'
      + 'By answering some simple questions, you will help us process your claim quickly.',
  },
  {
    id: 'intro-1-underwriting',
    label: 'Intro 1 - Underwriting',
    text: 'By answering a few questions, you will help us process your policy quickly '
      + 'and offer you the best possible rate.',
  },
  {
    id: 'intro-2',
    label: 'Intro 2',
    text: 'You will now be asked some simple questions which only require accurate yes or no responses.\n'
      + 'Before answering yes or no, please wait for the question to be completely finished.\n'
      + 'The questions are about to begin.',
  },
  {
    id: 'waiting-intelligent-response',
    label: 'Waiting Intelligent Response',
    text: 'Please answer the question with a single yes or no response',
  },
  {
    id: 'transition-1',
    label: 'Transition 1',
    text: 'You will now be asked the same questions a second time.  The questions are about to begin',
  },
  {
    id: 'transition-2',
    label: 'Transition 2',
    text: 'You will now be asked the same questions one final time.  The questions are about to begin',
  },
  {
    id: 'closing-successful',
    label: 'Successful Closing',
    text: 'Please hold while the system processes your answers. <break time="500ms"/>'
      + 'Your responses were accepted for evaluation.  Thank you for your cooperation.',
  },
  {
    id: 'closing-unsuccessful',
    label: 'Unsuccessful Closing',
    text: 'Please hold while the system processes your answers. Your responses were not accepted.  '
      + 'Please reach out to the company that asked you to complete this questionnaire and try again.',
  },
  { id: 'too-soft', label: 'Too Soft', text: 'Your answers are too soft.  Please speak louder' },
  { id: 'too-loud', label: 'Too Loud', text: 'Your answers are too loud.  Please speak softer' },
  { id: 'missing-answer', label: 'Missing Answer', text: 'We did not hear your answer, please try again' },
  { id: 'too-fast', label: 'Too Fast', text: 'Your answers are too fast.  Please speak slower' },
  { id: 'too-slow', label: 'Too Slow', text: 'Your answers are too slow.  Please speak faster' },
  {
    id: 'background-noise',
    label: 'Background Noise',
    text: 'There is too much noise in the background.  Please move to a quiet location and try again',
  },
  {
    id: 'unknown-audio',
    label: 'Unknown Audio',
    text: 'We were unable to detect your response.  '
      + 'Please speak clearly and make sure you are in a quiet location.',
  },
  { id: 'silence-3s', label: '3 Second Silence', silenceMs: 3000 },
];

/**
 * The voice the standard components should speak in: the demo's narrator.
 *
 * The project's saved default is the intent, but a translated project's
 * segments are what actually got re-voiced — `demo_language` demos exist whose
 * narrator column still names the English voice they were cloned from — so a
 * narration segment wins whenever the two disagree. Answer segments are skipped
 * because they are the participant, not the system.
 *
 * Returns null when the project has no usable voice at all, in which case the
 * export simply carries no standard set rather than guessing one.
 */
const NARRATION_ROLES = ['intro', 'transition', 'closing', 'question'];

function narratorVoiceFor(project, segments) {
  const list = segments || [];
  const spoken = list.find((s) => NARRATION_ROLES.includes(s.role) && s.voice_id)
    || list.find((s) => s.voice_id && s.role !== 'answer')
    || list.find((s) => s.voice_id);
  if (spoken) {
    return {
      voice_id: spoken.voice_id,
      language_code: spoken.language_code || 'en-US',
      engine: spoken.engine,
    };
  }
  if (project && project.narrator_voice_id) {
    return { voice_id: project.narrator_voice_id, language_code: 'en-US' };
  }
  return null;
}

const BREAK_TAG = /<break\s+time\s*=\s*"(\d+)(ms|s)"\s*\/?>/gi;

/**
 * Split text on `<break time="500ms"/>` into spoken runs and the pauses between.
 *
 * Voicemaker does honour the tag rather than reading it aloud (measured: it
 * turns a 500ms break into ~1.0s of silence, against 683ms for the same text
 * cut locally). It is cut locally anyway because segments route per voice to
 * either Voicemaker or ElevenLabs, which interpret the tag differently — an
 * ffmpeg gap is exactly the requested length and identical on both.
 */
function splitOnBreaks(text) {
  const parts = [];
  let last = 0;
  for (const m of String(text).matchAll(BREAK_TAG)) {
    const spoken = text.slice(last, m.index).trim();
    if (spoken) parts.push({ speak: spoken });
    parts.push({ silenceMs: m[2].toLowerCase() === 's' ? Number(m[1]) * 1000 : Number(m[1]) });
    last = m.index + m[0].length;
  }
  const tail = text.slice(last).trim();
  if (tail) parts.push({ speak: tail });
  return parts;
}

function fileName(index, label, format) {
  const safe = label
    .replace(/[^a-z0-9\-_ ]+/gi, '')
    .trim()
    .replace(/\s+/g, '-')
    .slice(0, 40) || `standard-${index + 1}`;
  return `std-${String(index + 1).padStart(2, '0')}-${safe}.${format}`;
}

/**
 * Render the whole standard set into `destDir` in the project's narrator voice.
 *
 * A component that fails to synthesize is reported and skipped — the standard
 * set is an extra in someone's export, and losing the whole zip over one stock
 * prompt would be the worse failure.
 *
 * @returns {Promise<{files: Array<{filename, path, label}>, failed: Array<{label, error}>}>}
 */
async function renderStandardComponents({ destDir, voice, format = 'mp3' }) {
  fs.mkdirSync(destDir, { recursive: true });
  fs.mkdirSync(TMP_DIR, { recursive: true });
  const fmt = format === 'wav' ? 'wav' : 'mp3';
  // The set is fixed text in a fixed voice, so it is the same audio every time.
  // Cached per voice+format+wording, or every segment export would spend 15
  // fresh TTS calls (~700 characters) re-making files identical to the last lot.
  const cacheDir = path.join(RENDERS_DIR, '_standard', `${voice.voice_id}-${voice.language_code}-${fmt}-${wordingHash()}`);
  fs.mkdirSync(cacheDir, { recursive: true });

  const files = [];
  const failed = [];

  for (let i = 0; i < STANDARD_COMPONENTS.length; i++) {
    const component = STANDARD_COMPONENTS[i];
    const name = fileName(i, component.label, fmt);
    const cachePath = path.join(cacheDir, name);
    const destPath = path.join(destDir, name);
    try {
      // An empty file from an interrupted render must not be served as a hit.
      if (!(fs.existsSync(cachePath) && fs.statSync(cachePath).size > 0)) {
        if (component.silenceMs) await makeSilence(component.silenceMs, cachePath);
        else await renderSpoken(component, { destPath: cachePath, voice, fmt });
      }
      fs.copyFileSync(cachePath, destPath);
      files.push({ filename: name, path: destPath, label: component.label });
    } catch (err) {
      fs.rmSync(cachePath, { force: true });
      failed.push({ label: component.label, error: err.message });
    }
  }

  return { files, failed };
}

// Cache key component: changing any component's wording must not serve the old
// audio back. Truncated because it only has to differ, not be unguessable.
let wordingHashCache = '';
function wordingHash() {
  if (!wordingHashCache) {
    wordingHashCache = crypto
      .createHash('sha256')
      .update(JSON.stringify(STANDARD_COMPONENTS))
      .digest('hex')
      .slice(0, 8);
  }
  return wordingHashCache;
}

async function renderSpoken(component, { destPath, voice, fmt }) {
  const parts = splitOnBreaks(component.text);
  const stamp = `${component.id}-${process.pid}-${nextSeq()}`;

  // A single spoken run is requested in the target format and used as-is. Only
  // a component carrying a break needs stitching, and `concatenate` always
  // encodes mp3 — so that path assembles in mp3 and transcodes at the end,
  // exactly as renderProject does. Writing the concat straight to a .wav name
  // would put mp3 bytes behind a wav extension.
  const pieceFmt = parts.length > 1 ? 'mp3' : fmt;
  const pieces = [];

  for (let p = 0; p < parts.length; p++) {
    const piecePath = path.join(TMP_DIR, `std-${stamp}-${p}.${pieceFmt}`);
    if (parts[p].silenceMs) {
      await makeSilence(parts[p].silenceMs, piecePath);
    } else {
      const result = await tts.convert({
        text: parts[p].speak,
        voice_id: voice.voice_id,
        language_code: voice.language_code,
        engine: voice.engine,
        effect: 'default',
        master_volume: 0,
        master_speed: 0,
        master_pitch: 0,
        output_format: pieceFmt,
      });
      await downloadFile(result.path, piecePath);
    }
    pieces.push(piecePath);
  }

  if (pieces.length === 1) {
    fs.renameSync(pieces[0], destPath);
    return;
  }

  const joined = path.join(TMP_DIR, `std-${stamp}-joined.mp3`);
  await concatenate(pieces, joined);
  for (const p of pieces) fs.rmSync(p, { force: true });
  if (fmt === 'mp3') {
    fs.renameSync(joined, destPath);
  } else {
    await execFileAsync('ffmpeg', ['-y', '-i', joined, destPath]);
    fs.rmSync(joined, { force: true });
  }
}

// Monotonic within a process, so two exports running at once cannot collide on
// a temp piece name. Date.now() alone is not enough at this granularity.
let seq = 0;
function nextSeq() { seq += 1; return seq; }

module.exports = {
  STANDARD_COMPONENTS, renderStandardComponents, splitOnBreaks, fileName, narratorVoiceFor,
};
