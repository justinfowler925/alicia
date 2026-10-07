#!/usr/bin/env node
/**
 * Prove the headless Vidyard path can see the Fine Uploader file input.
 *
 * Does NOT upload. Opens the widget with the allowlisted Referer and asserts
 * input[name=qqfile] appears (the failure mode when the host redirects to
 * demo.clearspeed.com and Vidyard domain-blocks the embed).
 *
 *   node scripts/test-vidyard-widget-input.js
 */
const { chromium } = require('playwright');
const {
  createTag,
  widgetUploadUrl,
  WIDGET_REFERER,
} = require('../server/lib/vidyardWidgetUpload');

(async () => {
  const tag = createTag();
  const url = widgetUploadUrl(tag).toString();
  console.log('referer', WIDGET_REFERER);
  console.log('widget', url);

  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setExtraHTTPHeaders({ Referer: WIDGET_REFERER });
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });

    let fileInput = null;
    const deadline = Date.now() + 20000;
    let body = '';
    while (!fileInput && Date.now() < deadline) {
      body = await page.evaluate(() => document.body?.innerText || '');
      if (/cannot be embedded on this domain/i.test(body)) {
        throw new Error('domain allowlist blocked the widget — Referer is wrong or revoked');
      }
      fileInput = await page.$('input[type="file"][name="qqfile"]')
        || await page.$('input[type="file"]');
      if (!fileInput) await page.waitForTimeout(400);
    }
    if (!fileInput) {
      throw new Error(`no file input after 20s; body=${JSON.stringify(body.slice(0, 200))}`);
    }
    console.log('PASS: Vidyard widget exposed a file input');
  } finally {
    await browser.close().catch(() => {});
  }
})().catch((err) => {
  console.error('FAIL:', err.message);
  process.exit(1);
});
