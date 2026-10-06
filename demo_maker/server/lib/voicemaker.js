const BASE_URL = 'https://developer.voicemaker.in/api/v1';

function apiKey() {
  const key = process.env.VOICEMAKER_API_KEY;
  if (!key || key === 'your_voicemaker_api_key_here') {
    throw new Error('VOICEMAKER_API_KEY is not set. Add it to .env');
  }
  return key;
}

async function post(path, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey()}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.success === false) {
    throw new Error(data.message || `Voicemaker API error (${res.status})`);
  }
  return data;
}

async function listVoices(language) {
  const data = await post('/voice/list', language ? { language } : {});
  const payload = data.data || data;
  return payload.voices_list || payload.voiceList || payload.voices || [];
}

async function convert(segment) {
  const body = {
    Engine: segment.engine || 'neural',
    VoiceId: segment.voice_id,
    LanguageCode: segment.language_code || 'en-US',
    Text: segment.text,
    OutputFormat: segment.output_format || 'mp3',
    SampleRate: segment.sample_rate || '48000',
    Effect: segment.effect || 'default',
    MasterVolume: String(segment.master_volume ?? 0),
    MasterSpeed: String(segment.master_speed ?? 0),
    MasterPitch: String(segment.master_pitch ?? 0),
    ResponseType: 'file',
  };
  const data = await post('/voice/convert', body);
  return data; // { success, path, usedChars, remainChars, ... }
}

module.exports = { listVoices, convert };
