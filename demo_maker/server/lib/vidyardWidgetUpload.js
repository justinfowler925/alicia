/**
 * Headless Vidyard upload via the account's public uploader widget.
 *
 * Justin's Vidyard package has no Dashboard API token — only the widget. The
 * widget is a Fine Uploader page with input[name=qqfile]; Playwright feeds the
 * local mp4, then we read the share.vidyard.com/watch/<uuid> the widget shows
 * (available immediately, before encoding finishes / webhook fires).
 *
 * Domain allowlist: Vidyard only embeds the widget on owner-approved hosts.
 * clearspeeddemos.com 308s to demo.clearspeed.com, which is NOT allowlisted, so
 * loading the public upload page leaves an empty iframe ("cannot be embedded on
 * this domain") and publish fails with "did not expose a file input". Fix: open
 * the widget URL directly with Referer set to the allowlisted
 * www.clearspeeddemos.com upload path (the Referer alone is enough for Vidyard).
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
// Allowlisted parent used only as Referer — the live site redirects to
// demo.clearspeed.com, which Vidyard rejects as an embed host.
const WIDGET_REFERER = process.env.VIDYARD_UPLOAD_REFERER
  || 'https://www.clearspeeddemos.com/vidyard-upload/';
const CALLBACK_BASE = process.env.VIDYARD_UPLOAD_CALLBACK_URL
  || 'https://www.clearspeeddemos.com/api/vidyard-upload';
const DOMAIN_BLOCK = /cannot be embedded on this domain/i;

function configured() {
  return Boolean(WIDGET_ID);
}

function createTag() {
  return 'fdm' + crypto.randomBytes(16).toString('hex');
}

function widgetUploadUrl(tag) {
  const uploader = new URL(`https://secure.vidyard.com/uploader_widgets/${WIDGET_ID}`);
  uploader.searchParams.append('options[]', 'upload');
  uploader.searchParams.set('tags', tag);
  return uploader;
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

async function frameBodyText(frame) {
  try {
    return await frame.evaluate(() => document.body?.innerText || '');
  } catch {
    return '';
  }
}

async function scrapePlayerUuid(page) {
  const frames = page.frames();
  for (const frame of frames) {
    const url = frame.url();
    if (!url.includes('secure.vidyard.com') && frame !== page.mainFrame()) continue;
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

async function findFileInput(page) {
  // Direct widget page (current path) — input lives on the main document.
  const onPage = await page.$('input[type="file"][name="qqfile"]')
    || await page.$('input[type="file"]');
  if (onPage) return onPage;

  for (const frame of page.frames()) {
    if (!frame.url().includes('secure.vidyard.com')) continue;
    const handle = await frame.$('input[type="file"][name="qqfile"]')
      || await frame.$('input[type="file"]');
    if (handle) return handle;
  }
  return null;
}

async function assertNotDomainBlocked(page) {
  const texts = [];
  for (const frame of page.frames()) {
    const body = await frameBodyText(frame);
    if (body) texts.push(body);
  }
  const joined = texts.join('\n');
  if (DOMAIN_BLOCK.test(joined)) {
    throw new Error(
      'Vidyard widget refused this host (domain allowlist). '
      + 'Upload must send Referer from an allowlisted clearspeeddemos.com path; '
      + 'demo.clearspeed.com is not allowlisted.',
    );
  }
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
  const named = stageNamedCopy(filePath, title);
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    // Referer must be an allowlisted host — not demo.clearspeed.com (redirect target).
    await page.setExtraHTTPHeaders({ Referer: WIDGET_REFERER });
    await page.goto(widgetUploadUrl(tag).toString(), {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });

    let fileInput = null;
    const findDeadline = Date.now() + 30000;
    while (!fileInput && Date.now() < findDeadline) {
      await assertNotDomainBlocked(page);
      fileInput = await findFileInput(page);
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
  configured,
  uploadFile,
  createTag,
  parsePlayerUuid,
  stageNamedCopy,
  canonicalPlayerUuid,
  widgetUploadUrl,
  WIDGET_REFERER,
};
