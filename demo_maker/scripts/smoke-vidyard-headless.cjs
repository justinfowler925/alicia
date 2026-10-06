#!/usr/bin/env node
/**
 * Smoke: headless widget upload of a local mp4.
 *
 *   node scripts/smoke-vidyard-headless.cjs [file.mp4] --for-real
 *
 * This uploads to the shared Clearspeed Vidyard library for real and there is
 * no API to clean up after it — three stray `vidyard-smoke` assets are still
 * sitting there from earlier runs. Hence the flag, and the reminder below.
 */
const path = require('path');
const { uploadFile } = require('../server/lib/vidyardWidgetUpload');

const args = process.argv.slice(2);
const filePath = args.find((a) => !a.startsWith('--')) || '/tmp/vidyard-smoke.mp4';
if (!args.includes('--for-real')) {
  console.error('Refusing to run: this leaves a real asset in the shared Vidyard library.');
  console.error('Re-run with --for-real, then delete the upload in Vidyard when you are done.');
  process.exit(2);
}
(async () => {
  console.log('uploading', filePath);
  const result = await uploadFile({
    filePath,
    name: path.basename(filePath, path.extname(filePath)),
    timeoutMs: 4 * 60 * 1000,
  });
  console.log('SUCCESS', result);
  console.log('Remember to delete this upload from the Vidyard library.');
})().catch((err) => {
  console.error('FAILED', err.message);
  process.exit(1);
});
