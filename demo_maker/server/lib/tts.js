// Provider router: looks up which provider owns a voice and dispatches the
// synthesis call. Everything downstream (preview, render, per-segment export,
// demo video) talks to this instead of a specific provider.

const db = require('../db');
const voicemaker = require('./voicemaker');
const elevenlabs = require('./elevenlabs');

function providerFor(voiceId) {
  const row = db.prepare('SELECT provider FROM voices_cache WHERE voice_id = ?').get(voiceId);
  return row?.provider || 'voicemaker';
}

async function convert(segment) {
  if (providerFor(segment.voice_id) === 'elevenlabs') return elevenlabs.convert(segment);
  return voicemaker.convert(segment);
}

module.exports = { convert, providerFor };
