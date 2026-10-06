// Exercise the post-publish pipeline end to end against stubs: no GitHub writes,
//
//   node scripts/test-publish-pipeline.js [happy|no-token|keep-player|fail-push|fail-deploy|fail-vidyard]
//
// Use this instead of publishing to the real public site to test changes.
// no Vercel, no Vidyard. Stubs are injected through the require cache BEFORE
// server/index.js loads, so the real code paths run unmodified.
const path = require('path');
const fs = require('fs');
const ROOT = path.join(__dirname, '..');

process.env.PORT = '4179';
process.env.VIDYARD_API_TOKEN = 'stub-token';     // makes configured() true
process.env.PUBLIC_SITE_URL = 'https://example.invalid';

const scenario = process.argv[2] || 'happy';
const calls = [];

// --- stub publicSync -------------------------------------------------------
const syncPath = require.resolve(path.join(ROOT, 'server/lib/publicSync.js'));
require(syncPath); // load real one first so the cache entry exists
require.cache[syncPath].exports = {
  publishVideoFile: async (row) => {
    calls.push('publishVideoFile:' + path.basename(row.file_path));
    if (scenario === 'fail-push') throw new Error('GitHub rejected the push (409)');
    if (scenario === 'fail-deploy') throw new Error('Timed out waiting for deploy');
  },
  putLibraryJson: async () => { calls.push('putLibraryJson'); },
  unpublishFromPublic: async (name) => { calls.push('unpublish:' + name); },
  publishToPublic: async () => { calls.push('publishToPublic'); },
  syncAll: async () => 0,
};
// Routing (sensitive -> library-gated/) is covered by test-public-sync-routing.js
// against the REAL publicSync — these stubs deliberately test only the pipeline.

// --- stub vidyard ----------------------------------------------------------
const vyPath = require.resolve(path.join(ROOT, 'server/lib/vidyard.js'));
require(vyPath);
let playerSeq = 0;
require.cache[vyPath].exports = {
  configured: () => scenario !== 'no-token',
  // What the pipeline actually calls since the headless widget landed. Every
  // call mints a NEW player, so counting these IS the duplicate-upload gate.
  uploadLocalFile: async ({ name }) => {
    calls.push('uploadLocalFile:' + name);
    if (scenario === 'fail-vidyard') throw new Error('Vidyard widget did not expose a file input');
    playerSeq += 1;
    const uuid = `stubUUID${playerSeq}`;
    return { uuid, shareUrl: `https://share.vidyard.com/watch/${uuid}`, via: 'scrape' };
  },
  createPlayer: async ({ name }) => {
    calls.push('createPlayer:' + name);
    playerSeq += 1;
    const uuid = `stubUUID${playerSeq}`;
    return { id: 1000 + playerSeq, uuid, shareUrl: `https://share.vidyard.com/watch/${uuid}` };
  },
};

require(path.join(ROOT, 'server/index.js'));

const B = 'http://127.0.0.1:4179';
const j = async (url, opts) => {
  const r = await fetch(B + url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  return { code: r.status, body: await r.json() };
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  await sleep(900);

  // Need a render row to publish from: fabricate one on a throwaway project.
  const proj = (await j('/api/projects', { method: 'POST', body: JSON.stringify({ name: 'PublishPipeline Test' }) })).body;
  const RENDERS = path.join(ROOT, 'renders');
  fs.mkdirSync(RENDERS, { recursive: true });
  const fake = 'pipeline-test.mp4';
  // A tiny real mp4 so ffprobe succeeds; copy an existing render if present.
  const existing = fs.readdirSync(RENDERS).find((f) => f.endsWith('.mp4'));
  if (!existing) { console.log('SKIP: no existing mp4 in renders/ to copy'); process.exit(0); }
  fs.copyFileSync(path.join(RENDERS, existing), path.join(RENDERS, fake));

  const pub = await j('/api/library/publish', {
    method: 'POST',
    body: JSON.stringify({
      render_file_path: fake, source_project_id: proj.id,
      title: 'Pipeline Test', vertical: 'GDS', use_case: 'Security',
      org: 'Test Org', summary: 'R',
    }),
  });
  const t0 = Date.now();
  const respondedInMs = Date.now() - t0;
  console.log(`publish responded: ${pub.code} in <${respondedInMs + 50}ms  status=${pub.body.publish_status}`);
  const libId = pub.body.id;

  // Poll to completion
  let s, spins = 0;
  do {
    await sleep(150);
    s = (await j(`/api/library/${libId}/publish-status`)).body;
    spins += 1;
  } while (!s.done && spins < 80);

  console.log(`final: status=${s.status} phase=${s.phase} percent=${s.percent}`);
  if (s.error) console.log(`  error surfaced: ${s.error}`);
  if (s.vidyard_id) console.log(`  vidyard_id=${s.vidyard_id}`);
  console.log(`  call order: ${calls.join(' -> ')}`);

  const republish = async (extra = {}) => {
    calls.length = 0;
    await j('/api/library/publish', {
      method: 'POST',
      body: JSON.stringify({
        render_file_path: fake, source_project_id: proj.id,
        title: 'Pipeline Test', vertical: 'GDS', use_case: 'Security',
        org: 'Test Org', summary: 'R', ...extra,
      }),
    });
    let s2, n = 0;
    do { await sleep(150); s2 = (await j(`/api/library/${libId}/publish-status`)).body; n += 1; } while (!s2.done && n < 80);
    return s2;
  };
  const uploaded = () => calls.some((c) => c.startsWith('uploadLocalFile'));

  // A plain republish must NOT upload again. Every widget upload mints a new
  // player, so an upload here is another copy of the same demo in Vidyard.
  if (scenario === 'happy' || scenario === 'keep-player') {
    const s2 = await republish();
    console.log(`republish: status=${s2.status} vidyard_id=${s2.vidyard_id} action=${s2.vidyard_action} superseded=[${s2.vidyard_superseded.join(', ')}]`);
    console.log(`  uploaded again? ${uploaded() ? 'YES (BUG)' : 'no (correct)'}`);
    const libs = (await j('/api/library')).body;
    console.log(`library rows for this project: ${libs.filter((r) => r.source_project_id === proj.id).length} (must be 1)`);
  }

  // …but an explicit replace must, and must record the superseded player.
  if (scenario === 'happy' || scenario === 'replace-player') {
    const s3 = await republish({ replace_vidyard: true });
    console.log(`replace: vidyard_id=${s3.vidyard_id} action=${s3.vidyard_action} superseded=[${s3.vidyard_superseded.join(', ')}]`);
    console.log(`  uploaded on request? ${uploaded() ? 'yes (correct)' : 'NO (BUG)'}`);
  }

  // Retry path
  if (scenario === 'fail-vidyard') {
    console.log('--- retry after failure ---');
    calls.length = 0;
    const r = await j(`/api/library/${libId}/retry-publish`, { method: 'POST' });
    console.log(`retry accepted: ${r.code}`);
    let s3, n = 0;
    do { await sleep(150); s3 = (await j(`/api/library/${libId}/publish-status`)).body; n += 1; } while (!s3.done && n < 80);
    console.log(`after retry: status=${s3.status} phase=${s3.phase} error=${s3.error || '(none)'}`);
    console.log(`  call order: ${calls.join(' -> ')}`);
  }

  // cleanup
  await j(`/api/library/${libId}`, { method: 'DELETE' });
  await j(`/api/projects/${proj.id}`, { method: 'DELETE' });
  fs.rmSync(path.join(RENDERS, fake), { force: true });
  console.log('cleaned up');
  process.exit(0);
})().catch((e) => { console.error('HARNESS ERROR:', e); process.exit(1); });
