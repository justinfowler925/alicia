const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');
const execFileAsync = promisify(execFile);
const express = require('express');
const { chromium } = require('playwright');

const PUBLIC_DIR = path.join(__dirname, '..', '..', 'public');
const { RENDERS_DIR } = require('./paths');
const { TMP_DIR } = require('./paths');

/**
 * Render a demo to a finished MP4 — optimized.
 *
 * Speed comes from two things:
 *   1. JPEG frame capture (Chromium encodes JPEG far faster than PNG, and the
 *      frames are smaller to move over CDP).
 *   2. Parallel capture across N headless pages, each pulling frame indices from
 *      a shared counter. Every frame is a pure function of time (window.__renderAt),
 *      so order doesn't matter — frames are written as numbered files and muxed
 *      by ffmpeg at the end.
 */
async function renderVideo({ slug = 'hmpps', fps = 30, workers, quality = 92, onProgress } = {}) {
  fs.mkdirSync(RENDERS_DIR, { recursive: true });
  fs.mkdirSync(TMP_DIR, { recursive: true });
  const outPath = path.join(RENDERS_DIR, `${slug}-${Date.now()}.mp4`);

  const demo = JSON.parse(fs.readFileSync(path.join(PUBLIC_DIR, 'demo', 'data', `${slug}.json`), 'utf8'));
  const audioAbs = path.join(PUBLIC_DIR, demo.audioSrc.replace(/^\//, ''));
  if (!fs.existsSync(audioAbs)) throw new Error(`audio not found: ${audioAbs}`);

  const framesDir = path.join(TMP_DIR, `frames-${Date.now()}`);
  fs.mkdirSync(framesDir, { recursive: true });

  // Serve /public on an ephemeral port for the headless pages.
  const app = express();
  app.use(express.static(PUBLIC_DIR));
  const server = await new Promise((res) => { const s = app.listen(0, () => res(s)); });
  const port = server.address().port;

  const nWorkers = Math.max(1, workers || Math.min(os.cpus().length - 2, 8));
  const browser = await chromium.launch({ args: ['--disable-dev-shm-usage'] });

  try {
    // Spin up N ready pages.
    const pages = [];
    for (let w = 0; w < nWorkers; w++) {
      const pg = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
      await pg.goto(`http://127.0.0.1:${port}/demo/player.html?demo=${slug}&render=1`, { waitUntil: 'networkidle' });
      await pg.waitForFunction('window.__ready === true', { timeout: 20000 });
      pages.push(pg);
    }

    const end = await pages[0].evaluate('window.__demoEnd');
    const total = Math.ceil(end * fps);
    const reportEvery = Math.max(1, Math.round(fps / 2));

    let next = 0;
    let done = 0;
    const claim = () => (next < total ? next++ : -1);
    if (onProgress) onProgress({ phase: 'frames', framesDone: 0, framesTotal: total });

    async function work(pg) {
      let i;
      while ((i = claim()) >= 0) {
        await pg.evaluate((tt) => window.__renderAt(tt), i / fps);
        const buf = await pg.screenshot({ type: 'jpeg', quality });
        fs.writeFileSync(path.join(framesDir, `f${String(i).padStart(6, '0')}.jpg`), buf);
        done += 1;
        if (onProgress && done % reportEvery === 0) onProgress({ phase: 'frames', framesDone: done, framesTotal: total });
      }
    }
    await Promise.all(pages.map((pg) => work(pg)));

    if (onProgress) onProgress({ phase: 'encoding', framesDone: total, framesTotal: total });
    await execFileAsync('ffmpeg', [
      '-y',
      '-framerate', String(fps), '-start_number', '0', '-i', path.join(framesDir, 'f%06d.jpg'),
      '-i', audioAbs,
      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'veryfast', '-crf', '20',
      '-c:a', 'aac', '-b:a', '192k',
      '-r', String(fps), '-shortest', '-movflags', '+faststart',
      outPath,
    ]);
    return outPath;
  } finally {
    await browser.close();
    server.close();
    fs.rmSync(framesDir, { recursive: true, force: true });
  }
}

module.exports = { renderVideo, RENDERS_DIR };
