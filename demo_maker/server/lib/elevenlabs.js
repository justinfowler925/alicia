// ElevenLabs TTS provider. Voices live in the same voices_cache as Voicemaker
// (provider = 'elevenlabs'); synthesis writes a local clip under
// renders/el-clips/ and returns a served /renders/... path so the existing
// downloadFile()/preview flows work unchanged.

const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);
const BASE_URL = 'https://api.elevenlabs.io';
const CLIPS_DIR = path.join(require('./paths').RENDERS_DIR, 'el-clips');
const DEFAULT_MODEL = 'eleven_multilingual_v2';

function apiKey() {
  const key = process.env.ELEVENLABS_API_KEY;
  if (!key) throw new Error('ELEVENLABS_API_KEY is not set. Add it to .env');
  return key;
}

const configured = () => Boolean(process.env.ELEVENLABS_API_KEY);

/**
 * Accent label → region subtag. ElevenLabs reports the language in
 * `labels.language` and the accent in `labels.accent`, and only the PAIR
 * identifies a locale — so this is combined with the language, never used alone.
 *
 * That matters: `labels.language: 'uk'` is **Ukrainian**, not the United
 * Kingdom. Four Ukrainian voices (Volodymyr, Yevhen, Evgeniy, Artem) carry it,
 * and mapping an accent straight to a region would have made them British.
 */
const ACCENT_REGION = {
  british: 'GB', english: 'GB', scottish: 'GB', welsh: 'GB', irish: 'IE',
  american: 'US', 'en-american': 'US', 'us-american': 'US',
  australian: 'AU', canadian: 'CA', quebec: 'CA',
  indian: 'IN', 'en-hindi': 'IN', 'en-indian': 'IN',
  peninsular: 'ES', stockholm: 'SE',
};

/**
 * The BCP-47 tag for a voice, from its language + accent labels.
 *
 * Every ElevenLabs voice used to be stamped `en-US` regardless — which is how
 * `Pet Insurance UK`, cast with "Alice" (an ElevenLabs *british* voice), came
 * out with an American interface and a 🇺🇸 region chip. A language with no
 * accent mapping is returned bare so parseLocaleTag's DEFAULT_REGION fills it
 * in (`uk` → `uk-UA`, `it` → `it-IT`) rather than guessing a country here.
 */
function localeTagFor(labels = {}) {
  const raw = String(labels.language || '').trim().toLowerCase();
  const lang = /^[a-z]{2,3}$/.test(raw) ? raw : 'en';
  const accent = String(labels.accent || '').trim().toLowerCase();
  const region = ACCENT_REGION[accent] || '';
  return region ? `${lang}-${region.toUpperCase()}` : lang;
}

// Map ElevenLabs voices into the Voicemaker list shape the cache loader eats.
// LanguageName "ElevenLabs, <accent>" groups them under a single "ElevenLabs"
// language in the pickers (rowToVoice splits language/accent on the comma), so
// changing `Language` below moves nothing in the UI — it only fixes the locale
// the player resolves from a segment.
async function listVoices() {
  const out = [];
  let pageToken = '';
  do {
    const url = `${BASE_URL}/v2/voices?page_size=100${pageToken ? `&next_page_token=${encodeURIComponent(pageToken)}` : ''}`;
    const res = await fetch(url, { headers: { 'xi-api-key': apiKey() } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`ElevenLabs voices error (${res.status}): ${JSON.stringify(data.detail || data).slice(0, 150)}`);
    for (const v of data.voices || []) {
      const labels = v.labels || {};
      const accent = labels.accent || labels.description || '';
      out.push({
        VoiceId: v.voice_id,
        VoiceWebname: v.name,
        Language: localeTagFor(labels),
        LanguageName: accent ? `ElevenLabs, ${accent}` : 'ElevenLabs',
        Country: labels.language || 'Multilingual',
        VoiceGender: (labels.gender || '').replace(/^\w/, (c) => c.toUpperCase()),
        Engine: DEFAULT_MODEL,
      });
    }
    pageToken = data.next_page_token || '';
  } while (pageToken);
  return out;
}

// Drop clips older than a day so el-clips doesn't grow forever.
function sweepOldClips() {
  try {
    const cutoff = Date.now() - 24 * 3600 * 1000;
    for (const f of fs.readdirSync(CLIPS_DIR)) {
      const p = path.join(CLIPS_DIR, f);
      if (fs.statSync(p).mtimeMs < cutoff) fs.rmSync(p, { force: true });
    }
  } catch { /* best effort */ }
}

// Same contract as voicemaker.convert(): takes a segment, returns
// { path, usedChars } where path is fetchable/serveable.
async function convert(segment) {
  fs.mkdirSync(CLIPS_DIR, { recursive: true });
  sweepOldClips();
  const wantWav = segment.output_format === 'wav';
  const outputFormat = wantWav ? 'pcm_44100' : 'mp3_44100_128';
  const res = await fetch(
    `${BASE_URL}/v1/text-to-speech/${encodeURIComponent(segment.voice_id)}?output_format=${outputFormat}`,
    {
      method: 'POST',
      headers: { 'xi-api-key': apiKey(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: segment.text,
        model_id: (segment.engine || '').startsWith('eleven_') ? segment.engine : DEFAULT_MODEL,
      }),
    }
  );
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`ElevenLabs TTS error (${res.status}): ${detail.slice(0, 200)}`);
  }
  const buf = Buffer.from(await res.arrayBuffer());
  const base = `el-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  let file;
  if (wantWav) {
    // pcm_44100 is raw 16-bit mono — wrap it in a WAV container.
    const rawPath = path.join(CLIPS_DIR, `${base}.pcm`);
    file = `${base}.wav`;
    fs.writeFileSync(rawPath, buf);
    await execFileAsync('ffmpeg', ['-y', '-f', 's16le', '-ar', '44100', '-ac', '1', '-i', rawPath, path.join(CLIPS_DIR, file)]);
    fs.rmSync(rawPath, { force: true });
  } else {
    file = `${base}.mp3`;
    fs.writeFileSync(path.join(CLIPS_DIR, file), buf);
  }
  return { path: `/renders/el-clips/${file}`, usedChars: (segment.text || '').length };
}

module.exports = { listVoices, convert, configured, localeTagFor };
