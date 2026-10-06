import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { renderVideo } = require('../server/lib/video.js');

const slug = process.argv[2] || 'hmpps';
const fps = Number(process.argv[3] || 30);

console.log(`Rendering ${slug} at ${fps}fps...`);
const out = await renderVideo({ slug, fps, onProgress: (p) => process.stdout.write(`\r  ${p}%   `) });
console.log(`\nDone: ${out}`);
