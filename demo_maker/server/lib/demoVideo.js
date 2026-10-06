const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');
const execFileAsync = promisify(execFile);

const tts = require('./tts');
const { TMP_DIR, downloadFile, makeSilence, concatenate } = require('./audio');
const { renderVideo } = require('./video');

const PUBLIC_DIR = path.join(__dirname, '..', '..', 'public');
const DATA_DIR = path.join(PUBLIC_DIR, 'demo', 'data');
const AUDIO_DIR = path.join(PUBLIC_DIR, 'demo', 'audio');

// Seconds after the answer finishes before the result badge posts. This window
// holds the brief transcript + the "analyzing" beat, so give it real room. The
// player lets the analysis finish even after the next question starts reading.
const POST_DELAY = 4.0;

// An unscored ask (a neutral question, or any first-iteration question in a
// production demo) still gets recorded — it just settles into "captured" rather
// than running the analysis. Shorter beat, since nothing is being computed.
const UNSCORED_DELAY = 1.6;

async function ffprobeDuration(file) {
  const { stdout } = await execFileAsync('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration',
    '-of', 'default=noprint_wrappers=1:nokey=1', file,
  ]);
  return parseFloat(stdout.trim()) || 0;
}

function slugify(s) {
  return String(s || 'demo').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'demo';
}

/**
 * Turn measured segment timings into the player's on-screen model.
 *
 * `segTimes` is [{ seg, start, end }] in playback order. Pure function of its
 * inputs — no I/O — so the mapping can be exercised without generating audio.
 *
 *   questions[] — one row per DISTINCT question (segments sharing a q_key are
 *                 the same question asked twice, so they share one row)
 *   columns[]   — one results column per SCORED ask, in ask order
 *   cues[]      — one per ask, carrying `qi` (which row highlights) and `col`
 *                 (which column posts, or null when the ask isn't scored)
 *
 * Keeping `qi` and `col` distinct from the cue's own index is what lets the
 * same question highlight twice while posting a badge only once.
 *
 * Untagged projects (built before templates existed) fall back to the sales
 * shape: every question is its own row and every question is scored.
 *
 * `ui` is the player's own language (lib/uiLocale). Omitting it leaves the
 * player on its built-in English chrome, exactly as before this existed.
 */
function deriveConfig(project, segTimes, totalDur, slug, ui = null) {
  const asks = segTimes.filter((s) => (s.seg.role || 'other') === 'question');
  if (asks.length === 0) {
    throw new Error('No segments are marked as "question". Set each question segment\'s role before rendering video.');
  }
  // Legacy projects carry no `scored` flags at all — treat every question as
  // scored so demos built before templates render exactly as they used to.
  const anyScored = asks.some((s) => Number(s.seg.scored) === 1);

  const rows = [];             // questions[] — one per distinct question
  const rowIndexByKey = new Map();
  const columns = [];          // results columns — one per scored ask
  const cues = [];

  for (let i = 0; i < segTimes.length; i++) {
    const { seg, start, end } = segTimes[i];
    if ((seg.role || 'other') !== 'question') continue;

    // The answer is the next `answer` segment before the next question.
    let ans = null;
    for (let j = i + 1; j < segTimes.length; j++) {
      const r = segTimes[j].seg.role || 'other';
      if (r === 'answer') { ans = segTimes[j]; break; }
      if (r === 'question') break;
    }
    const answerText = ans ? (ans.seg.text || '') : '';
    const answerEnd = ans ? ans.end : end;

    // Segments sharing a q_key are the same question asked again — one row.
    // No q_key means "its own question", which is the legacy/sales shape.
    const key = (seg.q_key || '').trim() || `__pos${i}`;
    let qi = rowIndexByKey.get(key);
    if (qi === undefined) {
      qi = rows.length;
      rowIndexByKey.set(key, qi);
      rows.push({
        text: seg.text || '',
        answer: answerText,
        // Untagged questions are pertinent — that is what the old demos showed.
        type: (seg.qtype || 'PQ') === 'NQ' ? 'NQ' : 'PQ',
      });
    }

    const scored = anyScored ? Number(seg.scored) === 1 : true;
    let col = null;
    if (scored) {
      col = columns.length;
      columns.push({
        qi,
        label: `Q${columns.length + 1}`,
        result: seg.result === 'R' ? 'R' : 'G',
      });
    }

    cues.push({
      qi,
      col,
      iter: Number(seg.iteration) || 1,
      ask: start,
      answer: ans ? ans.start : end,
      answerEnd,
      answerText,
      // When the panel reaches its final state for this ask. A scored ask posts
      // its badge here; an unscored one just settles into "captured".
      post: Math.min(totalDur, answerEnd + (scored ? POST_DELAY : UNSCORED_DELAY)),
    });
  }

  if (columns.length === 0) {
    throw new Error('No question is marked as scored, so the results panel would stay empty. Mark at least one question as scored before rendering video.');
  }

  return {
    slug,
    format: project.demo_format === 'production' ? 'production' : 'sales',
    org: project.name || 'Demo',
    // The ui block already resolved this from the demo's locale, and carries the
    // flag computed from the same name. A config with NO ui block — every demo
    // rendered before the locale feature — keeps the old literal fallback, which
    // is what makes those renders reproducible.
    region: (ui && ui.regionName) || project.demo_region || 'United States',
    irn: project.demo_irn || '00034',
    ...(ui ? { ui } : {}),
    summary: columns.some((c) => c.result === 'R') ? 'R' : 'G',
    audioSrc: `/demo/audio/${slug}.mp3`,
    questions: rows,
    columns,
    timeline: { intro: cues[0] ? cues[0].ask : 0, cues, end: totalDur },
  };
}

/**
 * Build a finished demo MP4 from a project's segments.
 *
 * Generates each segment's TTS, measures its real duration, stitches the audio,
 * hands the timings to deriveConfig, then renders the player headless to MP4.
 *
 * `ui` (from lib/uiLocale.buildUiConfig) sets the player's own language; the
 * caller resolves it because it needs the DB. Null = English chrome.
 */
async function buildDemoVideo(project, segments, onProgress = () => {}, ui = null) {
  fs.mkdirSync(TMP_DIR, { recursive: true });
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.mkdirSync(AUDIO_DIR, { recursive: true });

  const jobId = Date.now();
  const clipPaths = [];
  let offset = 0;
  const segTimes = []; // { seg, start, end }

  const spoken = segments.filter((s) => s.text && s.text.trim()).length;
  let spokenDone = 0;
  onProgress({ phase: 'speech', current: 0, total: spoken });

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    let dur = 0;
    if (seg.text && seg.text.trim()) {
      const conv = await tts.convert(seg);
      const rawPath = path.join(TMP_DIR, `demo${jobId}-raw${i}.mp3`);
      await downloadFile(conv.path, rawPath);
      // Normalize to a canonical format (48kHz stereo) so clips and silence
      // gaps concat cleanly — TTS clips can come back at varying rates/channels.
      const clipPath = path.join(TMP_DIR, `demo${jobId}-seg${i}.mp3`);
      await execFileAsync('ffmpeg', ['-y', '-i', rawPath, '-ar', '48000', '-ac', '2', '-c:a', 'libmp3lame', '-q:a', '2', clipPath]);
      fs.rmSync(rawPath, { force: true });
      dur = await ffprobeDuration(clipPath);
      clipPaths.push(clipPath);
      spokenDone += 1;
      onProgress({ phase: 'speech', current: spokenDone, total: spoken });
    }
    const start = offset;
    offset += dur;
    segTimes.push({ seg, start, end: offset });

    const pause = seg.pause_after_ms || 0;
    if (pause > 0 && i < segments.length - 1) {
      const silPath = path.join(TMP_DIR, `demo${jobId}-sil${i}.mp3`);
      await makeSilence(pause, silPath);
      clipPaths.push(silPath);
      offset += pause / 1000;
    }
  }

  const slug = slugify(project.name);
  const audioOut = path.join(AUDIO_DIR, `${slug}.mp3`);
  onProgress({ phase: 'audio' });
  await concatenate(clipPaths, audioOut);
  for (const p of clipPaths) fs.rmSync(p, { force: true });
  const totalDur = await ffprobeDuration(audioOut);

  const config = deriveConfig(project, segTimes, totalDur, slug, ui);
  fs.writeFileSync(path.join(DATA_DIR, `${slug}.json`), JSON.stringify(config, null, 2));

  const outPath = await renderVideo({ slug, fps: 30, onProgress });
  return { outPath, slug, config };
}

module.exports = { buildDemoVideo, deriveConfig };
