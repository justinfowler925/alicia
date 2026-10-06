const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

const { TMP_DIR } = require('./paths');
const { RENDERS_DIR } = require('./paths');

async function downloadFile(url, destPath) {
  // ElevenLabs clips are written locally and referenced as served /renders/
  // paths — copy those straight from disk instead of fetching.
  if (url.startsWith('/renders/')) {
    fs.copyFileSync(path.join(RENDERS_DIR, url.slice('/renders/'.length)), destPath);
    return destPath;
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to download clip: ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(destPath, buf);
  return destPath;
}

async function makeSilence(ms, destPath) {
  const seconds = (ms / 1000).toFixed(3);
  await execFileAsync('ffmpeg', [
    '-y', '-f', 'lavfi', '-i', `anullsrc=r=48000:cl=stereo`,
    '-t', seconds, '-q:a', '9', destPath,
  ]);
  return destPath;
}

async function concatenate(clipPaths, outPath) {
  const listFile = path.join(TMP_DIR, `concat-${Date.now()}.txt`);
  const listContent = clipPaths
    .map((p) => `file '${p.replace(/'/g, "'\\''")}'`)
    .join('\n');
  fs.writeFileSync(listFile, listContent);
  try {
    await execFileAsync('ffmpeg', [
      '-y', '-f', 'concat', '-safe', '0', '-i', listFile,
      '-c:a', 'libmp3lame', '-q:a', '2', outPath,
    ]);
  } finally {
    fs.rmSync(listFile, { force: true });
  }
  return outPath;
}

async function renderProject(projectId, segments, clipUrls, outputFormat) {
  fs.mkdirSync(TMP_DIR, { recursive: true });
  const projectDir = path.join(RENDERS_DIR, String(projectId));
  fs.mkdirSync(projectDir, { recursive: true });

  // All Voicemaker clips are pulled as mp3 and concatenated as mp3, then
  // transcoded to the requested output format as a final step (keeps the
  // ffmpeg concat filter's codec requirements simple).
  const jobId = Date.now();
  const clipPaths = [];

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    // A null clip URL means a text-less segment: it contributes only its
    // pause_after_ms of silence (used as answer gaps between spoken parts).
    if (clipUrls[i]) {
      const clipPath = path.join(TMP_DIR, `job${jobId}-seg${i}.mp3`);
      await downloadFile(clipUrls[i], clipPath);
      clipPaths.push(clipPath);
    }

    const pause = seg.pause_after_ms || 0;
    if (pause > 0 && i < segments.length - 1) {
      const silPath = path.join(TMP_DIR, `job${jobId}-sil${i}.mp3`);
      await makeSilence(pause, silPath);
      clipPaths.push(silPath);
    }
  }

  const mp3Path = path.join(TMP_DIR, `job${jobId}-concat.mp3`);
  await concatenate(clipPaths, mp3Path);
  for (const p of clipPaths) fs.rmSync(p, { force: true });

  const finalFormat = outputFormat || 'mp3';
  const outPath = path.join(projectDir, `render-${jobId}.${finalFormat}`);
  if (finalFormat === 'mp3') {
    fs.renameSync(mp3Path, outPath);
  } else {
    await execFileAsync('ffmpeg', ['-y', '-i', mp3Path, outPath]);
    fs.rmSync(mp3Path, { force: true });
  }

  return outPath;
}

// Save each spoken segment as its own standalone audio file (wav or mp3).
// clipUrls[i] is the Voicemaker-hosted clip already requested in the desired
// output format, so we just download it under a stable, human-readable name.
// Text-less (silence-gap) segments have a null clip and are skipped.
// `renderExtras(segDir)` is called once the segment files are on disk, to add
// files that belong in the same folder and zip but are not project segments
// (the standard component set). It runs after the folder is wiped and refilled,
// never before, and returns `[{ filename, path, label }]`.
async function renderSegmentsSeparately(projectId, segments, clipUrls, format, renderExtras = null) {
  fs.mkdirSync(TMP_DIR, { recursive: true });
  const fmt = format === 'wav' ? 'wav' : 'mp3';
  const projectDir = path.join(RENDERS_DIR, String(projectId));
  const segDir = path.join(projectDir, 'segments');
  // Start clean so stale files from a previous export don't linger.
  fs.rmSync(segDir, { recursive: true, force: true });
  fs.mkdirSync(segDir, { recursive: true });

  const files = [];
  for (let i = 0; i < segments.length; i++) {
    if (!clipUrls[i]) continue;
    const seg = segments[i];
    const safe = (seg.label || `segment-${i + 1}`)
      .replace(/[^a-z0-9\-_ ]+/gi, '')
      .trim()
      .replace(/\s+/g, '-')
      .slice(0, 40) || `segment-${i + 1}`;
    const filename = `${String(i + 1).padStart(2, '0')}-${safe}.${fmt}`;
    const destPath = path.join(segDir, filename);
    await downloadFile(clipUrls[i], destPath);
    files.push({
      position: i,
      label: seg.label || '',
      filename,
      relPath: path.relative(RENDERS_DIR, destPath),
    });
  }

  const extras = renderExtras ? await renderExtras(segDir) : [];

  // Bundle them into a single zip when the `zip` CLI is available, so the user
  // can grab everything in one click. Non-fatal if zip isn't installed.
  let zipRel = null;
  const zipInputs = [
    ...files.map((f) => path.join(RENDERS_DIR, f.relPath)),
    ...extras.map((e) => e.path),
  ];
  if (zipInputs.length > 0) {
    try {
      const zipName = `segments-${Date.now()}.zip`;
      const zipPath = path.join(projectDir, zipName);
      fs.rmSync(zipPath, { force: true });
      await execFileAsync('zip', ['-j', '-q', zipPath, ...zipInputs]);
      zipRel = path.relative(RENDERS_DIR, zipPath);
    } catch {
      zipRel = null;
    }
  }

  return {
    files,
    zip: zipRel,
    extras: extras.map((e) => ({
      label: e.label,
      filename: e.filename,
      relPath: path.relative(RENDERS_DIR, e.path),
    })),
  };
}

module.exports = { renderProject, renderSegmentsSeparately, RENDERS_DIR, TMP_DIR, downloadFile, makeSilence, concatenate };
