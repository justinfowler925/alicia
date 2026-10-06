// Sensitive library rows must never be pushed to public/library/videos/ in the
// clearspeed-demos repo — the static host serves that dir to anyone, so a
// wrong-side push reopens the hole /api/video exists to close. This exercises
// the REAL publicSync.js (stubbed db, captured fetch — no GitHub writes):
//
//   node scripts/test-public-sync-routing.js
//
const path = require('path');
const fs = require('fs');
const ROOT = path.join(__dirname, '..');

process.env.GITHUB_TOKEN = 'stub-token';

// Stub the db module before publicSync loads it.
let rows = [];
const dbPath = require.resolve(path.join(ROOT, 'server/db'));
require(dbPath);
require.cache[dbPath].exports = { prepare: () => ({ all: () => rows }) };

// Capture every GitHub Contents API call instead of performing it.
const calls = [];
global.fetch = async (url, opts = {}) => {
  const m = String(url).match(/\/contents\/(.+?)(\?|$)/);
  calls.push(`${opts.method || 'GET'} ${m ? m[1] : url}`);
  // getSha always finds a sha so deleteFile actually attempts the DELETE.
  return { ok: true, status: 200, json: async () => ({ sha: 'stub-sha' }), text: async () => '' };
};

const sync = require(path.join(ROOT, 'server/lib/publicSync.js'));

let failed = 0;
const check = (ok, label) => {
  if (!ok) failed++;
  console.log(`${ok ? 'ok' : 'FAIL'} - ${label}`);
};

(async () => {
  // A real local file so publishVideoFile's existence check passes.
  const LIBRARY_DIR = path.join(ROOT, 'public', 'library', 'videos');
  fs.mkdirSync(LIBRARY_DIR, { recursive: true });
  const base = 'routing-test-tmp.mp4';
  fs.writeFileSync(path.join(LIBRARY_DIR, base), 'not really an mp4');

  try {
    // Path routing
    check(sync.repoVideoPath({ sensitive: 1 }, base) === `library-gated/videos/${base}`, 'sensitive row routes to library-gated/');
    check(sync.repoVideoPath({ sensitive: 0 }, base) === `public/library/videos/${base}`, 'public row routes to public/library/');

    // publishVideoFile: sensitive row -> PUT gated, DELETE public counterpart
    calls.length = 0;
    await sync.publishVideoFile({ sensitive: 1, file_path: base, title: 'T' });
    check(calls.includes(`PUT library-gated/videos/${base}`), `sensitive publish PUTs gated path (${calls.join(', ')})`);
    check(calls.includes(`DELETE public/library/videos/${base}`), 'sensitive publish DELETEs the public counterpart');
    check(!calls.some((c) => c.startsWith('PUT public/')), 'sensitive publish never PUTs under public/');

    // publishVideoFile: public row -> PUT public, DELETE gated counterpart
    calls.length = 0;
    await sync.publishVideoFile({ sensitive: 0, file_path: base, title: 'T' });
    check(calls.includes(`PUT public/library/videos/${base}`), 'public publish PUTs public path');
    check(calls.includes(`DELETE library-gated/videos/${base}`), 'public publish DELETEs the gated counterpart (sensitivity flip)');

    // unpublish removes both sides
    calls.length = 0;
    await sync.unpublishFromPublic(base);
    check(calls.includes(`DELETE public/library/videos/${base}`), 'unpublish DELETEs public path');
    check(calls.includes(`DELETE library-gated/videos/${base}`), 'unpublish DELETEs gated path');

    // Index shapes: redaction + the private `file` contract /api/video reads
    rows = [
      { id: 7, title: 'Open Demo', vertical: 'GDS', use_case: 'Security', file_path: 'open.mp4', published_at: '2026-08-18', vidyard_id: 'VYpub', sensitive: 0 },
      { id: 8, title: 'Secret Demo', vertical: 'GDS', use_case: 'Sensitive', file_path: 'secret.mp4', published_at: '2026-08-18', vidyard_id: 'VYsec', sensitive: 1 },
    ];
    const { pub, priv } = sync.buildLibraryJson();
    check(pub.length === 1 && Object.keys(priv).length === 1, `index counts (pub=${pub.length}, priv=${Object.keys(priv).length})`);
    const open = pub.find((e) => e.id === 'lib-7');
    check(open.src === '/library/videos/open.mp4' && open.vidyardId === 'VYpub', 'public entry keeps src + vidyardId');
    // Even the name is sensitive: the entry must not exist in the public index.
    check(!pub.some((e) => e.id === 'lib-8'), 'sensitive entry is entirely absent from the public index');
    check(!JSON.stringify(pub).includes('Secret Demo'), 'sensitive title appears nowhere in the public index');
    const p = priv['lib-8'];
    check(p.file === 'secret.mp4' && p.vidyardId === 'VYsec', 'private entry carries {file, vidyardId}');
    check(p.title === 'Secret Demo' && p.vertical === 'GDS' && p.useCase === 'Sensitive' && p.sensitive === true, 'private entry carries full card metadata');
    check(!('src' in p), 'private entry has no src (playback URLs are minted per session)');

    // syncAll routes per row (only rows with a local file are pushed)
    rows = [{ id: 9, title: 'S', vertical: 'GDS', use_case: 'Sensitive', file_path: base, published_at: '2026-08-18', vidyard_id: '', sensitive: 1 }];
    calls.length = 0;
    await sync.syncAll();
    check(calls.includes(`PUT library-gated/videos/${base}`), 'syncAll routes sensitive rows to gated');
    check(!calls.some((c) => c === `PUT public/library/videos/${base}`), 'syncAll never PUTs a sensitive row under public/');

    // --- The name guard -----------------------------------------------------
    // On 2026-09-17 three rows had the sensitive flag CLEAR when it should
    // have been set, and lib-32 Emirates Airlines, lib-28 Military Vetting and
    // lib-22 DHS Screening went live in public/library.json for about three
    // hours. The flag is human-entered, so the name is now a second line of
    // defence. These are the exact three rows that leaked.
    for (const [title, org] of [['Emirates Airlines', 'Emirates Airlines'], ['Military Vetting', ''], ['DHS Screening', '']]) {
      check(sync.looksSensitive({ title, org }), `"${title}" is recognised as sensitive by name`);
      check(sync.isSensitive({ sensitive: 0, title, org }), `"${title}" is withheld even with the flag clear`);
      check(sync.repoVideoPath({ sensitive: 0, title, org }, base) === `library-gated/videos/${base}`,
        `"${title}" mp4 routes to gated even with the flag clear`);
    }

    rows = [{ id: 32, title: 'Emirates Airlines', vertical: 'GDS', use_case: 'Applications & Vetting',
              file_path: base, published_at: '2026-08-19', vidyard_id: '', org: 'Emirates Airlines', sensitive: 0 }];
    const guarded = sync.buildLibraryJson();
    check(guarded.pub.length === 0, `a name-sensitive row with the flag clear stays OUT of the public index (${guarded.pub.length} public)`);
    check(Boolean(guarded.priv['lib-32']?.sensitive), 'it lands in the private index flagged sensitive');

    calls.length = 0;
    await sync.syncAll();
    check(!calls.some((c) => c.startsWith('PUT public/library/videos/')), 'syncAll never PUTs its mp4 under public/ either');

    // And the guard must not swallow ordinary demos.
    for (const title of ['Travel - Injury Claims - UK', 'Banking - Account Authorization / ATO', 'Life Insurance - Underwriting', 'International Testing Agency']) {
      check(!sync.looksSensitive({ title, org: '' }), `"${title}" is NOT treated as sensitive`);
    }
    rows = [{ id: 31, title: 'Travel - Injury Claims - UK', vertical: 'Travel', use_case: 'Claims',
              file_path: base, published_at: '2026-08-19', vidyard_id: '', sensitive: 0 }];
    const ordinary = sync.buildLibraryJson();
    check(ordinary.pub.length === 1 && Object.keys(ordinary.priv).length === 0, 'an ordinary demo still publishes publicly');
  } finally {
    fs.rmSync(path.join(LIBRARY_DIR, base), { force: true });
  }

  if (failed > 0) {
    console.error(`\n${failed} check(s) failed`);
    process.exit(1);
  }
  console.log('\nall routing checks passed');
})().catch((e) => { console.error('HARNESS ERROR:', e); process.exit(1); });
