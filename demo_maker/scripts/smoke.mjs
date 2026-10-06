import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const express=require('express'); const {chromium}=require('playwright');
const PUB=path.join(__dirname,'..','public');
const app=express(); app.use(express.static(PUB));
const server=await new Promise(r=>{const s=app.listen(0,()=>r(s));});
const port=server.address().port;
const b=await chromium.launch();
const page=await b.newPage({viewport:{width:1920,height:1080}});
const errs=[]; page.on('pageerror',e=>errs.push(String(e)));
await page.goto(`http://127.0.0.1:${port}/demo/player.html?demo=hmpps&render=1`,{waitUntil:'networkidle'});
await page.waitForFunction('window.__ready===true',{timeout:20000});
const OUT='/private/tmp/claude-502/-Users-justinfowler-Projects/960a63a2-3f0a-46e7-a1b9-7cd42a944391/scratchpad';
// 12=Q1 listening, 22=Q1 proc burst, 25=Q1 posted, 52=Q4 proc, 55.5=Q4 posted(HR)
for(const t of [12,22,25,52,55.5]){ await page.evaluate(tt=>window.__renderAt(tt),t); await page.screenshot({path:`${OUT}/v2-${t}.png`}); }
console.log('errors:',JSON.stringify(errs));
await b.close(); server.close();
