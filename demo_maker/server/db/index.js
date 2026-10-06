const path = require('path');
const fs = require('fs');
const { DatabaseSync } = require('node:sqlite');

const { DB_PATH: dbPath, STATE_DIR } = require('../lib/paths');
fs.mkdirSync(STATE_DIR, { recursive: true });
const raw = new DatabaseSync(dbPath);
raw.exec('PRAGMA journal_mode = WAL');
raw.exec('PRAGMA foreign_keys = ON');

const schema = fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8');
raw.exec(schema);

// Migrations for DBs created before these columns existed (schema.sql only
// runs CREATE IF NOT EXISTS, so it never alters existing tables).
try {
  raw.exec(`ALTER TABLE segments ADD COLUMN label TEXT NOT NULL DEFAULT ''`);
} catch {
  // column already exists
}

// Demo-video columns. Each is added independently so a partially-migrated DB
// still picks up the ones it is missing.
for (const stmt of [
  `ALTER TABLE segments ADD COLUMN role TEXT NOT NULL DEFAULT 'other'`,
  `ALTER TABLE segments ADD COLUMN result TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN demo_org TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN demo_irn TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN demo_region TEXT NOT NULL DEFAULT 'United States'`,
  `ALTER TABLE library ADD COLUMN vidyard_id TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN default_sex TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN narrator_voice_id TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN answer_voice_id TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE library ADD COLUMN sensitive INTEGER NOT NULL DEFAULT 0`,
  `ALTER TABLE library ADD COLUMN language TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE library ADD COLUMN accent TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE voices_cache ADD COLUMN provider TEXT NOT NULL DEFAULT 'voicemaker'`,
  // Demo-format columns (production demos: two iterations of the same questions,
  // only the second-iteration pertinent ones are scored).
  //   qtype     PQ | NQ | ''      pertinent vs neutral, drives the TYPE column
  //   q_key     'PQ1' | 'NQ3'     stable identity — both iterations of a question
  //                               share one key, so they share one row on screen
  //   iteration 1 | 2 | 0         which pass this ask belongs to
  //   scored    0 | 1             whether this ask posts a risk badge
  `ALTER TABLE segments ADD COLUMN qtype TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE segments ADD COLUMN q_key TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE segments ADD COLUMN iteration INTEGER NOT NULL DEFAULT 0`,
  `ALTER TABLE segments ADD COLUMN scored INTEGER NOT NULL DEFAULT 0`,
  `ALTER TABLE projects ADD COLUMN demo_format TEXT NOT NULL DEFAULT 'sales'`,
  // The language the player's own UI is drawn in — a translated demo shows a
  // translated interface and that language's flag, not "🇺🇸 English".
  //   demo_language     BCP47 tag ('de', 'ar-AE'); '' = infer from the voices
  //   demo_ui_strings   {"code","t":{…}} — the translated chrome, generated once
  //                     at translate/render time and reused by every render
  `ALTER TABLE projects ADD COLUMN demo_language TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN demo_ui_strings TEXT NOT NULL DEFAULT ''`,
  // Library taxonomy for clearspeeddemos.com — vertical / use case / outcome /
  // sensitive gate. Persist on the project so Publish doesn't re-ask every time.
  `ALTER TABLE projects ADD COLUMN demo_vertical TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN demo_use_case TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN demo_summary TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE projects ADD COLUMN demo_sensitive INTEGER NOT NULL DEFAULT 0`,
  // Post-publish pipeline state. Pushing the mp4, waiting for Vercel, and
  // importing to Vidyard take minutes and can each fail independently — these
  // make a failure visible and retryable instead of a console line nobody reads.
  //   publish_status  '' | pending | ready | failed
  //   publish_phase   last phase reached, so a failure says WHERE it broke
  //   publish_error   the message to show and act on
  //   vidyard_superseded  player uuids this entry used to point at. Vidyard
  //     cannot swap the video inside a player, so a republish mints a new one;
  //     the old uuid is kept (never auto-deleted) because links to it may
  //     already be in circulation.
  `ALTER TABLE library ADD COLUMN publish_status TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE library ADD COLUMN publish_phase TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE library ADD COLUMN publish_error TEXT NOT NULL DEFAULT ''`,
  `ALTER TABLE library ADD COLUMN vidyard_superseded TEXT NOT NULL DEFAULT ''`,
  // What the last publish did about Vidyard: '' | created | kept-existing.
  // "kept-existing" means the entry already had a player and we deliberately did
  // NOT auto-create another — the API can't swap a player's video, so a new one
  // would stale every link already shared. Swap the file in the Vidyard UI.
  `ALTER TABLE library ADD COLUMN vidyard_action TEXT NOT NULL DEFAULT ''`,
  // One library entry per project — publish upserts, this enforces it. Kept
  // here (not schema.sql) so a DB that still holds duplicates boots anyway.
  `CREATE UNIQUE INDEX idx_library_source_project ON library(source_project_id) WHERE source_project_id IS NOT NULL`,
]) {
  try { raw.exec(stmt); } catch { /* column already exists */ }
}

// Use-case labels from before the library adopted Salesforce's list. Idempotent:
// once migrated no row matches. 'Sensitive' was a use case standing in for the
// sensitive flag, which is its own column.
for (const [from, to] of Object.entries({
  'Insurance - Claims': 'Travel',
  'Insurance - Underwriting': 'Life & Health - Underwriting',
  'Insurance - Workers Compensation': 'Workers Comp',
  'Banking - Account Take Over': 'Transactions',
  'Applications & Vetting': 'Screening & Vetting',
  'Security': 'Physical Security',
  'Anti-Doping': 'Sports Integrity',
  'Sensitive': '',
})) {
  raw.prepare('UPDATE library SET use_case = ? WHERE use_case = ?').run(to, from);
  raw.prepare('UPDATE projects SET demo_use_case = ? WHERE demo_use_case = ?').run(to, from);
}

// Thin better-sqlite3-compatible shim over node:sqlite's DatabaseSync so the
// rest of the app can use db.prepare(...).run/get/all and db.transaction(fn).
const db = {
  prepare(sql) {
    return raw.prepare(sql);
  },
  transaction(fn) {
    return (...args) => {
      raw.exec('BEGIN');
      try {
        const result = fn(...args);
        raw.exec('COMMIT');
        return result;
      } catch (err) {
        raw.exec('ROLLBACK');
        throw err;
      }
    };
  },
};

module.exports = db;
