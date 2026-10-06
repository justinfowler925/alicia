const Anthropic = require('@anthropic-ai/sdk');

// Translation is powered by Claude (Anthropic). Voicemaker only does TTS — it
// reads whatever text it's given in the voice's language — so segment text must
// be translated before it's sent for synthesis.
const MODEL = process.env.TRANSLATE_MODEL || 'claude-opus-4-8';

function client() {
  // Accept the Doppler-native name too, so `doppler run` injection works
  // without a rename (revops-ai-stack/prd exposes ANTHROPIC_PCM3_API_KEY).
  const key = process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_PCM3_API_KEY;
  if (!key || key === 'your_anthropic_api_key_here') {
    throw new Error('ANTHROPIC_API_KEY is not set (Doppler: ANTHROPIC_PCM3_API_KEY). Add it to .env to enable translation.');
  }
  return new Anthropic({ apiKey: key });
}

// Translate an array of { i, text } items into targetLanguage.
// Returns a Map of i -> translated text. Structured outputs guarantee the
// response is valid JSON matching the schema, so no brittle parsing/retry loop.
async function translateSegments(items, targetLanguage) {
  if (!items.length) return new Map();

  const schema = {
    type: 'object',
    additionalProperties: false,
    required: ['translations'],
    properties: {
      translations: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['i', 'text'],
          properties: { i: { type: 'integer' }, text: { type: 'string' } },
        },
      },
    },
  };

  const system =
    `You are a professional translator localizing a multi-voice audio demo script into ${targetLanguage}. ` +
    `Translate each segment's text naturally and idiomatically into ${targetLanguage}, preserving meaning, tone, ` +
    `register, and any names, numbers, or reference IDs. Do not add, drop, merge, or reorder segments. ` +
    `Return every input segment by its "i" index with the translated "text".`;

  const res = await client().messages.create({
    model: MODEL,
    max_tokens: 16000,
    system,
    output_config: { format: { type: 'json_schema', schema } },
    messages: [
      {
        role: 'user',
        content: `Translate these segments into ${targetLanguage}:\n${JSON.stringify(items.map(({ i, text }) => ({ i, text })))}`,
      },
    ],
  });

  const textBlock = res.content.find((b) => b.type === 'text');
  if (!textBlock) throw new Error('Translation returned no content');
  const parsed = JSON.parse(textBlock.text);
  const map = new Map();
  for (const t of parsed.translations || []) map.set(t.i, t.text);
  return map;
}

/**
 * Translate the player's chrome (panel titles, column headers, workflow labels)
 * into targetLanguage.
 *
 * `strings` is the English pack from lib/uiLocale (UI_EN). Returns a plain
 * object of the same keys, or throws — callers fall back to English chrome,
 * which is never worse than what the player showed before.
 *
 * The player is a fixed 1920×1080 layout with nowrap pills and buttons, so
 * length matters as much as accuracy here.
 */
async function translateUiStrings(strings, targetLanguage) {
  const keys = Object.keys(strings);
  if (!keys.length) return {};

  const schema = {
    type: 'object',
    additionalProperties: false,
    required: ['strings'],
    properties: {
      strings: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['key', 'text'],
          properties: { key: { type: 'string' }, text: { type: 'string' } },
        },
      },
    },
  };

  const system =
    `You are localizing the user interface of Clearspeed's voice risk-assessment platform into ${targetLanguage}. ` +
    `These are short UI labels: breadcrumbs, table column headers, buttons, status chips and a live status feed. ` +
    `Rules:\n` +
    `- Return every key you are given, translated into ${targetLanguage}.\n` +
    `- Keep each translation close to the English length (never more than ~1.3×) — the layout is fixed-width and does not wrap.\n` +
    `- Keep the placeholders {n} and {org} exactly as they appear.\n` +
    `- Do not translate "Clearspeed", "IRN", or "SUM…" (a truncated column header — keep it short and truncated).\n` +
    `- "Pertinent" and "Neutral" are assessment terms of art: a pertinent question is scored for risk, a neutral one is a baseline question that is not scored.\n` +
    `- Preserve the case convention where the target script has case: ALL-CAPS keys stay all-caps, lower-case feed lines stay lower-case. Where the script has no case, use the ordinary form.\n` +
    `- Keep the leading/trailing punctuation of the English (an ellipsis "…", a colon, an em dash) in place.`;

  const res = await client().messages.create({
    model: MODEL,
    max_tokens: 8000,
    // No `temperature` — the API rejects it for this model class. A pack is
    // stored per project, so a regeneration paraphrasing a label affects only
    // demos whose pack is regenerated, never one already rendered.
    system,
    output_config: { format: { type: 'json_schema', schema } },
    messages: [
      {
        role: 'user',
        content: `Localize these interface strings into ${targetLanguage}:\n${JSON.stringify(strings)}`,
      },
    ],
  });

  const textBlock = res.content.find((b) => b.type === 'text');
  if (!textBlock) throw new Error('UI translation returned no content');
  const parsed = JSON.parse(textBlock.text);
  const out = {};
  for (const s of parsed.strings || []) {
    if (keys.includes(s.key) && typeof s.text === 'string' && s.text.trim()) out[s.key] = s.text.trim();
  }
  if (!Object.keys(out).length) throw new Error('UI translation returned no usable strings');
  return out;
}

module.exports = { translateSegments, translateUiStrings };
