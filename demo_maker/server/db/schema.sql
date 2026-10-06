CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  output_format TEXT NOT NULL DEFAULT 'mp3',
  demo_org TEXT NOT NULL DEFAULT '',
  demo_irn TEXT NOT NULL DEFAULT '',
  demo_region TEXT NOT NULL DEFAULT 'United States',
  demo_format TEXT NOT NULL DEFAULT 'sales',
  demo_language TEXT NOT NULL DEFAULT '',
  demo_ui_strings TEXT NOT NULL DEFAULT '',
  -- Library taxonomy (clearspeeddemos.com). Set once on the project; Publish
  -- pre-fills from these so every render lands in the right vertical / use case.
  demo_vertical TEXT NOT NULL DEFAULT '',
  demo_use_case TEXT NOT NULL DEFAULT '',
  demo_summary TEXT NOT NULL DEFAULT '',
  demo_sensitive INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '',
  voice_id TEXT NOT NULL,
  language_code TEXT NOT NULL DEFAULT 'en-US',
  effect TEXT NOT NULL DEFAULT 'default',
  master_volume INTEGER NOT NULL DEFAULT 0,
  master_speed INTEGER NOT NULL DEFAULT 0,
  master_pitch INTEGER NOT NULL DEFAULT 0,
  pause_after_ms INTEGER NOT NULL DEFAULT 400,
  role TEXT NOT NULL DEFAULT 'other',
  result TEXT NOT NULL DEFAULT '',
  qtype TEXT NOT NULL DEFAULT '',
  q_key TEXT NOT NULL DEFAULT '',
  iteration INTEGER NOT NULL DEFAULT 0,
  scored INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS renders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  file_path TEXT NOT NULL,
  format TEXT NOT NULL,
  used_chars INTEGER NOT NULL DEFAULT 0,
  segment_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS library (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  vertical TEXT NOT NULL,
  use_case TEXT NOT NULL,
  org TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  file_path TEXT NOT NULL,
  duration_sec INTEGER NOT NULL DEFAULT 0,
  vidyard_id TEXT NOT NULL DEFAULT '',
  publish_status TEXT NOT NULL DEFAULT '',
  publish_phase TEXT NOT NULL DEFAULT '',
  publish_error TEXT NOT NULL DEFAULT '',
  vidyard_superseded TEXT NOT NULL DEFAULT '',
  vidyard_action TEXT NOT NULL DEFAULT '',
  source_project_id INTEGER,
  published_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS library_leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,
  library_id INTEGER,
  title TEXT,
  ip TEXT DEFAULT '',
  ua TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS library_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event TEXT NOT NULL,
  library_id INTEGER,
  email TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS voices_cache (
  voice_id TEXT PRIMARY KEY,
  name TEXT,
  language_code TEXT,
  gender TEXT,
  engine TEXT,
  raw_json TEXT,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_segments_project ON segments(project_id, position);
CREATE INDEX IF NOT EXISTS idx_renders_project ON renders(project_id, created_at);
