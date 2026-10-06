// Sync the Atlas library to the public clearspeeddemos.com repo so publishing
// in Fowler Demo Maker updates the live site automatically. Uses the GitHub
// Contents API (no local clone) — a push to main triggers a Vercel deploy.

const fs = require('fs');
const path = require('path');
const db = require('../db');

const REPO = process.env.PUBLIC_REPO || 'ClearspeedRevOps/clearspeed-demos';
const BRANCH = process.env.PUBLIC_REPO_BRANCH || 'main';
const { LIBRARY_DIR } = require('./paths');
const token = () => process.env.GITHUB_TOKEN;

function gh(pathInRepo, method, body) {
  return fetch(`https://api.github.com/repos/${REPO}/contents/${pathInRepo}`, {
    method,
    headers: {
      Authorization: `Bearer ${token()}`,
      Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json',
      'User-Agent': 'fowler-demo-maker',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function getSha(pathInRepo) {
  const res = await gh(`${pathInRepo}?ref=${BRANCH}`, 'GET');
  if (res.status === 200) return (await res.json()).sha;
  return null;
}

async function putFile(pathInRepo, buffer, message) {
  const sha = await getSha(pathInRepo);
  const res = await gh(pathInRepo, 'PUT', {
    message, branch: BRANCH, content: buffer.toString('base64'), ...(sha ? { sha } : {}),
  });
  if (!res.ok) throw new Error(`PUT ${pathInRepo} -> ${res.status}: ${await res.text()}`);
}

async function deleteFile(pathInRepo, message) {
  const sha = await getSha(pathInRepo);
  if (!sha) return;
  const res = await gh(pathInRepo, 'DELETE', { message, branch: BRANCH, sha });
  if (!res.ok && res.status !== 404) throw new Error(`DELETE ${pathInRepo} -> ${res.status}`);
}

// Where a row's mp4 lives in the public repo. Sensitive rows must NEVER land
// under public/ — the static host serves that dir to anyone, wall or no wall.
// They go to library-gated/videos/, which only /api/video (email-verified,
// signed URL) can read. The counterpart path is deleted on every push so a
// republish or a sensitivity flip cannot leave a stale copy on the wrong side.
// The `sensitive` flag is entered by a human, and on 2026-09-17 three rows had
// it clear that should not have: lib-32 Emirates Airlines, lib-28 Military
// Vetting and lib-22 DHS Screening went into public/library.json and were live
// on www.clearspeeddemos.com for about three hours. Titles and org names ARE
// the sensitive part — that is the whole reason the gated index exists.
//
// So the flag is no longer the only thing standing between a government or
// airline demo and the public index. Any row whose title or org matches this
// list is treated as sensitive whatever the flag says, and the mismatch is
// logged so the row gets fixed at source rather than silently overridden
// forever.
const SENSITIVE_NAME = /\b(emirates|dhs|homeland|military|embassy|consulate|ministry|police|clearance|vetting|screening|intelligence|defense|defence|classified)\b/i;
const looksSensitive = (row) => SENSITIVE_NAME.test(`${row.title || ''} ${row.org || ''}`);
const isSensitive = (row) => Boolean(row.sensitive) || looksSensitive(row);

// Both the index entry and the mp4 follow the same verdict, or the bytes end
// up on the public static host while the card is hidden.
const repoVideoPath = (row, base) =>
  isSensitive(row) ? `library-gated/videos/${base}` : `public/library/videos/${base}`;
const counterpartVideoPath = (row, base) =>
  isSensitive(row) ? `public/library/videos/${base}` : `library-gated/videos/${base}`;

// Atlas library rows -> the shape the public LibraryPage expects. Sensitive
// entries do not appear in the public index AT ALL — titles, org names, and
// even their existence are sensitive. Their full card metadata goes to
// api/_private-library.json, which Vercel does not serve: /api/sensitive-library
// lists them after email+code verification, and /api/video streams `file` from
// library-gated/videos/.
function buildLibraryJson() {
  const rows = db.prepare('SELECT * FROM library ORDER BY published_at DESC').all();
  const pub = [];
  const priv = {};
  for (const r of rows) {
    const e = {
      id: `lib-${r.id}`,
      title: r.title,
      vertical: r.vertical,
      useCase: r.use_case,
      publishedAt: String(r.published_at || '').slice(0, 10),
    };
    if (r.vidyard_id) e.vidyardId = r.vidyard_id;
    if (r.org) e.org = r.org;
    if (r.language) e.language = r.language;
    if (r.accent) e.accent = r.accent;
    if (r.summary === 'G' || r.summary === 'R') e.summary = r.summary;
    if (r.duration_sec) e.durationSec = r.duration_sec;
    const guarded = !r.sensitive && looksSensitive(r);
    if (guarded) {
      console.warn(
        `publicSync: "${r.title}" (${e.id}) is not flagged sensitive but its name says it is — ` +
        `withholding it from the public index. Fix the row's sensitive flag in Fowler Demo Maker.`
      );
    }
    if (r.sensitive || guarded) {
      priv[e.id] = { ...e, sensitive: true, file: path.basename(r.file_path) };
    } else {
      e.src = `/library/videos/${path.basename(r.file_path)}`;
      pub.push(e);
    }
  }
  return { pub, priv };
}

async function putLibraryJson() {
  const { pub, priv } = buildLibraryJson();
  await putFile('public/library.json', Buffer.from(JSON.stringify(pub, null, 2) + '\n'), 'chore(library): sync index from Fowler Demo Maker');
  await putFile('api/_private-library.json', Buffer.from(JSON.stringify(priv, null, 2) + '\n'), 'chore(library): sync gated-video index');
}

async function publishVideoFile(row) {
  if (!token()) throw new Error('GITHUB_TOKEN not set — cannot sync to public site');
  const base = path.basename(row.file_path);
  const local = path.join(LIBRARY_DIR, base);
  if (!fs.existsSync(local)) throw new Error(`Library video not found: ${base}`);
  await putFile(repoVideoPath(row, base), fs.readFileSync(local), `chore(library): publish "${row.title}"`);
  await deleteFile(counterpartVideoPath(row, base), 'chore(library): remove wrong-side copy').catch(() => {});
}

// Called after a publish: push the mp4, then the refreshed index.
async function publishToPublic(row) {
  await publishVideoFile(row);
  await putLibraryJson();
}

// Called after an unpublish: remove the mp4 (from whichever side of the wall
// it is on — the row is already gone, so delete both), refresh the index.
async function unpublishFromPublic(fileBasename) {
  if (!token()) throw new Error('GITHUB_TOKEN not set');
  const base = path.basename(fileBasename);
  await deleteFile(`public/library/videos/${base}`, 'chore(library): unpublish').catch(() => {});
  await deleteFile(`library-gated/videos/${base}`, 'chore(library): unpublish').catch(() => {});
  await putLibraryJson();
}

// Re-push everything (manual/full resync).
async function syncAll() {
  if (!token()) throw new Error('GITHUB_TOKEN not set');
  const rows = db.prepare('SELECT * FROM library').all();
  for (const r of rows) {
    const base = path.basename(r.file_path);
    const local = path.join(LIBRARY_DIR, base);
    if (fs.existsSync(local)) {
      await putFile(repoVideoPath(r, base), fs.readFileSync(local), `chore(library): sync "${r.title}"`);
      await deleteFile(counterpartVideoPath(r, base), 'chore(library): remove wrong-side copy').catch(() => {});
    }
  }
  await putLibraryJson();
  return rows.length;
}

module.exports = {
  publishToPublic,
  publishVideoFile,
  unpublishFromPublic,
  syncAll,
  putLibraryJson,
  // exported for tests
  repoVideoPath,
  counterpartVideoPath,
  buildLibraryJson,
  SENSITIVE_NAME,
  looksSensitive,
  isSensitive,
};
