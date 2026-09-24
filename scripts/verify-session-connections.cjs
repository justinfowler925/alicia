/* Real HTTP/1 browser regression: multiple pages must leave room for API calls.
 * Run against a running Alicia server; creates and closes one empty test session.
 */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const base = process.env.ALICIA_URL || 'http://127.0.0.1:8768';
(async () => {
  const response = await fetch(`${base}/api/session/open`, {
    method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({title: 'Connection regression check'}),
  });
  assert(response.ok);
  const {session_id: id} = await response.json();
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext();
    const errors = [];
    const pages = [];
    for (let i = 0; i < 3; i++) {
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      const streams = [];
      page.on('request', request => { if (request.url().includes('/events')) streams.push(request.url()); });
      await page.goto(`${base}/session?session=${id}`, {waitUntil: 'domcontentloaded', timeout: 15000});
      await page.waitForFunction(() => document.querySelector('#live')?.textContent === 'live', null, {timeout: 10000});
      assert.equal(await page.evaluate(async () => {
        const r = await fetch('/api/healthz', {signal: AbortSignal.timeout(10000)});
        return r.status;
      }), 200);
      assert.equal(streams.length, 1);
      assert(streams[0].includes("workspace=true"));
      pages.push(page);
    }
    await pages[0].reload({waitUntil: 'domcontentloaded', timeout: 15000});
    for (const page of pages) {
      await page.waitForFunction(() => document.querySelector('#live')?.textContent === 'live');
    }
    assert.deepEqual(errors, []);
    console.log('PASS: three simultaneous tabs stay live, API requests complete, and reload reconnects without page errors.');
  } finally {
    await browser.close();
    await fetch(`${base}/api/session/${id}/close`, {method: 'POST'});
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
