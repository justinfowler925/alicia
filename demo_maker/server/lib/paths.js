// Where Demo Maker keeps durable state.
//
// Studio: DEMO_MAKER_STATE (default ~/.alicia/state/demo-maker). Code deploys
// into ~/.alicia/app and is replaced on every deploy; runtime writes live here.
//
// Container: set DATA_DIR to the volume mount (Fly/Render use /data). That
// roots the same relative layout so a migrated Studio state tree drops in
// without rewriting paths.
const os = require('os');
const path = require('path');
const fs = require('fs');

const DATA_DIR = (process.env.DATA_DIR || '').trim();
const STATE_DIR =
  DATA_DIR ||
  process.env.DEMO_MAKER_STATE ||
  path.join(os.homedir(), '.alicia', 'state', 'demo-maker');

fs.mkdirSync(STATE_DIR, { recursive: true });

module.exports = {
  DATA_DIR: DATA_DIR || STATE_DIR,
  STATE_DIR,
  DB_PATH: path.join(STATE_DIR, 'studio.sqlite3'),
  RENDERS_DIR: path.join(STATE_DIR, 'renders'),
  TMP_DIR: path.join(STATE_DIR, 'tmp'),
  LIBRARY_DIR: path.join(STATE_DIR, 'library', 'videos'),
  ENV_FILE: path.join(STATE_DIR, '.env'),
};
