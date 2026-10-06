/**
 * Headless Vidyard upload via the account's public uploader widget.
 *
 * Justin's Vidyard package has no Dashboard API token — only the widget. The
 * widget is a Fine Uploader page with input[name=qqfile]; Playwright feeds the
 * local mp4, then we read the share.vidyard.com/watch/<uuid> the widget shows
 * (available immediately, before encoding finishes / webhook fires).
 */
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

// The widget in Clearspeed › RevOps › Demos. Uploads land in the widget's own
// folder, so this constant chooses the destination library — the old default
// (`n3KIDJVi737WcPIbYh2q1g`) is the Clearspeed ROOT folder's widget.
const WIDGET_ID = process.env.VIDYARD_UPLOADER_WIDGET_ID || 'MpWxjM6h_gYSzlY13AicUQ';
const PLAYER_PATTERN = /^[A-Za-z0-9_-]{16,}$/;
const UPLOAD_PAGE_BASE = process.env.VIDYARD_UPLOAD_PAGE_URL
  || 'https://www.clearspeeddemos.com/vidyard-upload/';
const CALLBACK_BASE = process.env.VIDYARD_UPLOAD_CALLBACK_URL
  || 'https://www.clearspeeddemos.com/api/vidyard-upload';

function configured() {
  return Boolean(WIDGET_ID);
}

function createTag() {
  return 'fdm' + crypto.randomBytes(16).toString('hex');
}

/**
 * Vidyard names the asset after the file it received, so `lib-32-ffe066d8b01c.mp4`
 * became a library full of hex. Stage the upload under the demo's own title
 * instead — a hardlink where the filesystem allows it, a copy otherwise.
 */
function stageNamedCopy(filePath, title) {
  const ext = path.extname(filePath) || '.mp4';
  const base = String(title || '')
    .replace(/[\\/:*?"<>|]/g, ' ')
    .replace(/[\u0000-\u001f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidyard-upload-'));
  const staged = path.join(dir, `${base || 'Clearspeed demo'}${ext}`);
  try {
    fs.linkSync(filePath, staged);
  } catch {
    fs.copyFileSync(filePath, staged); // different volume, or a filesystem without links
  }
  return { dir, staged };
}

/**
 * The canonical player uuid for an upload.
 *
 * An uploader-widget upload mints a WRAPPER player around the real one, and the
 * share link the widget shows is the wrapper — so the library ends up with two
 * cards per upload under the same name, and the site points at the extra
 * object. A hand-uploaded video has exactly one player, whose `facadeUuid` is
 * itself; a wrapper's `facadeUuid` points at the player it wraps. Following it
 * gives the same single asset the manual path produces.
 *
 * Best-effort: the public player JSON is not part of the upload contract, so a
 * miss returns the scraped uuid unchanged rather than failing the publish.
 */
async function canonicalPlayerUuid(uuid) {
  try {
    const res = await fetch(`https://play.vidyard.com/player/${encodeURIComponent(uuid)}.json`);
    if (!res.ok) return uuid;
    const data = await res.json();
    const payload = data.payload || data;
    const facade = ((payload.chapters || [])[0] || {}).facadeUuid;
    return PLAYER_PATTERN.test(String(facade || '')) ? facade : uuid;
  } catch {
    return uuid;
  }
}

function parsePlayerUuid(text) {
  const m = String(text || '').match(/share\.vidyard\.com\/watch\/([A-Za-z0-9_-]{16,})/i)
    || String(text || '').match(/play\.vidyard\.com\/([A-Za-z0-9_-]{16,})/i);
  return m ? m[1] : '';
}

async function scrapePlayerUuid(page) {
  for (const frame of page.frames()) {
    if (!frame.url().includes('secure.vidyard.com')) continue;
    try {
      const found = await frame.evaluate(() => {
        const values = [];
        for (const el of document.querySelectorAll('input, a, textarea')) {
          values.push(el.value || el.href || el.textContent || '');
        }
        values.push(document.body?.innerText || '');
        return values.join('\n');
      });
      const uuid = parsePlayerUuid(found);
      if (uuid) return uuid;
    } catch {
      // frame may navigate mid-upload
    }
  }
  // Parent page status link after webhook (fallback).
  try {
    const parentText = await page.evaluate(() => document.body?.innerText || '');
    return parsePlayerUuid(parentText) || '';
  } catch {
    return '';
  }
}

async function pollCallback(tag, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${CALLBACK_BASE}?tag=${encodeURIComponent(tag)}`, { cache: 'no-store' });
      if (res.ok) {
        const body = await res.json();
        if (body.player_uuid) return body;
      }
    } catch {
      // network blip — keep trying
    }
    await new Promise((r) => setTimeout(r, 2500));
  }
  return null;
}

/**
 * Upload a local video file through the Vidyard uploader widget.
 * @returns {{ uuid: string, shareUrl: string, tag: string, via: 'scrape'|'webhook' }}
 */
async function uploadFile({ filePath, name, timeoutMs = 10 * 60 * 1000 } = {}) {
  if (!configured()) throw new Error('Vidyard uploader widget is not configured');
  if (!filePath) throw new Error('filePath is required');

  const tag = createTag();
  const title = name || path.basename(filePath, path.extname(filePath));
  const uploadUrl = new URL(UPLOAD_PAGE_BASE);
  uploadUrl.searchParams.set('tag', tag);
  uploadUrl.searchParams.set('name', title);
  // A widget belongs to the Vidyard FOLDER it was created in and cannot deposit
  // anywhere else, so the widget id is the destination. Until 2026-08-08 this
  // was never sent and the upload page's own hardcoded id won — which is how
  // every upload since the headless path shipped landed in the Clearspeed root
  // instead of RevOps › Demos, where the demo library actually lives.
  uploadUrl.searchParams.set('widget', WIDGET_ID);

  const named = stageNamedCopy(filePath, title);
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(uploadUrl.toString(), { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForSelector('#uploader[src*="vidyard.com"]', { timeout: 20000 });

    let fileInput = null;
    const findDeadline = Date.now() + 30000;
    while (!fileInput && Date.now() < findDeadline) {
      for (const frame of page.frames()) {
        if (!frame.url().includes('secure.vidyard.com')) continue;
        const handle = await frame.$('input[type="file"][name="qqfile"]');
        if (handle) {
          fileInput = handle;
          break;
        }
      }
      if (!fileInput) await page.waitForTimeout(400);
    }
    if (!fileInput) throw new Error('Vidyard widget did not expose a file input');

    await fileInput.setInputFiles(named.staged);

    // Share URL appears as soon as Vidyard accepts the upload — before encoding
    // finishes and before the webhook (status=ready) fires. Prefer that.
    const scrapeDeadline = Date.now() + Math.min(timeoutMs, 3 * 60 * 1000);
    while (Date.now() < scrapeDeadline) {
      const uuid = await scrapePlayerUuid(page);
      if (uuid) {
        const canonical = await canonicalPlayerUuid(uuid);
        return {
          uuid: canonical,
          shareUrl: `https://share.vidyard.com/watch/${canonical}`,
          tag,
          via: 'scrape',
        };
      }
      await page.waitForTimeout(1500);
    }

    // Encoding-complete webhook is slower but authoritative when scrape fails.
    const remaining = Math.max(10000, timeoutMs - (Date.now() - (scrapeDeadline - Math.min(timeoutMs, 3 * 60 * 1000))));
    const callback = await pollCallback(tag, remaining);
    if (callback?.player_uuid) {
      const canonical = await canonicalPlayerUuid(callback.player_uuid);
      return {
        uuid: canonical,
        shareUrl: `https://share.vidyard.com/watch/${canonical}`,
        tag,
        via: 'webhook',
      };
    }

    throw new Error('Vidyard upload finished without a player UUID (scrape + webhook both empty)');
  } finally {
    await browser.close().catch(() => {});
    fs.rmSync(named.dir, { recursive: true, force: true });
  }
}

module.exports = {
  configured, uploadFile, createTag, parsePlayerUuid, stageNamedCopy, canonicalPlayerUuid,
};
