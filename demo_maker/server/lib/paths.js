// Where Demo Maker keeps durable state. Code deploys into ~/.alicia/app and is
// replaced on every deploy; everything written at runtime lives here instead.
const os = require('os');
const path = require('path');

const STATE_DIR = process.env.DEMO_MAKER_STATE || path.join(os.homedir(), '.alicia', 'state', 'demo-maker');

module.exports = {
  STATE_DIR,
  DB_PATH: path.join(STATE_DIR, 'studio.sqlite3'),
  RENDERS_DIR: path.join(STATE_DIR, 'renders'),
  TMP_DIR: path.join(STATE_DIR, 'tmp'),
  LIBRARY_DIR: path.join(STATE_DIR, 'library', 'videos'),
  ENV_FILE: path.join(STATE_DIR, '.env'),
};
