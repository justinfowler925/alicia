/**
 * Re-render demo projects in bulk through the studio's own render API.
 *
 * Sequential on purpose: a single render already fans out across cores
 * (video.js uses min(cores-2, 8) headless pages), so running two at once just
 * makes both slower.
 *
 * Publishing is NOT part of this. A republish mints a new Vidyard player (the
 * API cannot swap the video inside an existing one), which stales every link
 * already in circulation — that stays a deliberate, per-demo decision.
 *
 * Usage:
 *   node scripts/rerender-batch.mjs               # only demos whose frames change
 *   node scripts/rerender-batch.mjs --all         # every renderable project
 *   node scripts/rerender-batch.mjs --only=20,32  # specific project ids
 *   node scripts/rerender-batch.mjs --dry-run     # list the plan, render nothing
 *
 * Writes logs/rerender-summary.json next to the human-readable log.
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');
const require = createRequire(path.join(ROOT, 'package.json'));

const db = require(path.join(ROOT, 'server/db'));
const { resolveDemoLocale } = require(path.join(ROOT, 'server/lib/uiLocale'));

const args = process.argv.slice(2);
const ALL = args.includes('--all');
const DRY = args.includes('--dry-run');
const ONLY = (args.find((a) => a.startsWith('--only=')) || '').replace('--only=', '')
  .split(',').map((s) => Number(s.trim())).filter(Boolean);

const BASE = `http://127.0.0.1:${process.env.PORT || 4173}`;
const stamp = () => new Date().toISOString().replace('T', ' ').slice(0, 19);
const log = (...a) => console.log(`[${stamp()}]`, ...a);

/**
 * Which projects to render.
 *
 * Default is "only what changes": an en-US demo renders byte-identically after
 * the localization work, so re-rendering it would replace known-good published
 * audio with a fresh TTS take for no visual difference at all.
 */
function plan() {
  const out = [];
  for (const p of db.prepare('SELECT * FROM projects ORDER BY id').all()) {
    const segments = db.prepare('SELECT * FROM segments WHERE project_id = ? ORDER BY position').all(p.id);
    const questions = segments.filter((s) => (s.role || '') === 'question' && (s.text || '').trim());
    if (!questions.length) continue;
    const locale = resolveDemoLocale(p, segments);
    const lib = db.prepare('SELECT title, vidyard_id FROM library WHERE source_project_id = ?').get(p.id);
    const row = {
      id: p.id,
      name: p.name,
      locale: locale.code,
      dir: locale.dir,
      flag: locale.langFlag,
      questions: questions.length,
      published: Boolean(lib),
      vidyard: Boolean(lib && lib.vidyard_id),
    };
    if (ONLY.length) { if (ONLY.includes(p.id)) out.push(row); continue; }
    if (ALL || locale.code !== 'en-US') out.push(row);
  }
  return out;
}

async function renderOne(row) {
  const started = Date.now();
  const res = await fetch(`${BASE}/api/projects/${row.id}/render-video`, { method: 'POST' });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.jobId) throw new Error(body.error || `render-video returned ${res.status}`);

  let lastPhase = '';
  for (;;) {
    await new Promise((r) => setTimeout(r, 5000));
    const job = await fetch(`${BASE}/api/render-jobs/${body.jobId}`).then((r) => r.json());
    if (job.phase !== lastPhase) {
      lastPhase = job.phase;
      log(`  ${row.id} ${row.name}: ${job.phase}${job.total ? ` ${job.current}/${job.total}` : ''}`);
    }
    if (job.done) {
      if (job.error) throw new Error(job.error);
      return { url: job.url, seconds: Math.round((Date.now() - started) / 1000) };
    }
  }
}

const jobs = plan();
log(`base ${BASE} · ${jobs.length} project(s)${ALL ? ' (--all)' : ONLY.length ? ' (--only)' : ' (locale-changing only)'}`);
for (const j of jobs) {
  log(`  plan ${String(j.id).padStart(3)} ${j.locale.padEnd(6)} ${j.flag} q${j.questions} ${j.vidyard ? 'vidyard' : j.published ? 'published' : 'unpublished'} | ${j.name}`);
}
if (DRY) { log('dry run — nothing rendered'); process.exit(0); }

const results = [];
for (const [i, j] of jobs.entries()) {
  log(`(${i + 1}/${jobs.length}) rendering ${j.id} ${j.name} …`);
  try {
    const { url, seconds } = await renderOne(j);
    results.push({ ...j, ok: true, url, seconds });
    log(`  ✓ ${j.id} ${j.name} → ${url} (${seconds}s)`);
  } catch (err) {
    results.push({ ...j, ok: false, error: err.message });
    log(`  ✗ ${j.id} ${j.name}: ${err.message}`);
  }
}

const ok = results.filter((r) => r.ok);
log(`done — ${ok.length}/${results.length} rendered, ${results.length - ok.length} failed`);
const summaryPath = path.join(ROOT, 'logs', 'rerender-summary.json');
fs.mkdirSync(path.dirname(summaryPath), { recursive: true });
fs.writeFileSync(summaryPath, JSON.stringify({ finishedAt: new Date().toISOString(), results }, null, 2));
log(`summary → ${summaryPath}`);
process.exit(results.length - ok.length === 0 ? 0 : 1);
