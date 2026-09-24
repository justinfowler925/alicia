const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
(async()=>{
 const browser = await chromium.launch({headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 await page.goto((process.env.FORGE_URL || 'http://127.0.0.1:8768/')+'#forge');
 await page.getByText('Ready when you are.',{exact:true}).waitFor();
 let failOnce = true;
 await page.route('**/api/forge/upload/**', async route => {
   if (failOnce) { failOnce = false; await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Test connection interruption. Retry this upload.'})}); }
   else await route.continue();
 });
 await page.waitForFunction(()=>!document.getElementById('forge-attach').disabled);
 await page.locator('#forge-files').setInputFiles({name:'retry-note.txt',mimeType:'text/plain',buffer:Buffer.from('Retry fixture')});
 await page.getByRole('button',{name:'Retry upload retry-note.txt',exact:true}).click();
 await page.locator('#forge-attachments').getByText(/retry-note.txt.*Ready/).waitFor({timeout:40000});
 await page.getByRole('button',{name:'Remove retry-note.txt',exact:true}).click();
 await page.unroute('**/api/forge/upload/**');
 await page.waitForFunction(()=>!document.getElementById('forge-attach').disabled);
 await page.locator('#forge-files').setInputFiles({name:'large-file.bin',mimeType:'text/plain',buffer:Buffer.alloc(32*1024*1024)});
 await page.locator('#forge-attachments').getByText(/large-file.bin.*Ready/).waitFor({timeout:120000});
 await page.getByRole('button',{name:'Remove large-file.bin',exact:true}).click();
 await page.waitForFunction(()=>!document.getElementById('forge-attach').disabled);
 await page.locator('#forge-files').setInputFiles({name:'picker-note.txt',mimeType:'text/plain',buffer:Buffer.from('The picker verification phrase is: violet submarine.\n')});
 await page.locator('#forge-attachments').getByText(/picker-note.txt.*Ready/).waitFor({timeout:40000});
 await page.waitForFunction(()=>!document.getElementById('forge-attach').disabled);
 const drop=await page.evaluateHandle(()=>{const d=new DataTransfer();d.items.add(new File(['The drop verification phrase is: silver orchard.\n'],'drop-note.txt',{type:'text/plain'}));return d;});
 await page.locator('#forge-composer').dispatchEvent('drop',{dataTransfer:drop});
 await page.locator('#forge-attachments').getByText(/drop-note.txt.*Ready/).waitFor({timeout:40000});
 await page.waitForFunction(()=>!document.getElementById('forge-attach').disabled);
 await page.locator('#forge-files').setInputFiles({name:'remove-me.txt',mimeType:'text/plain',buffer:Buffer.from('Temporary upload, remove before send.')});
 await page.locator('#forge-attachments').getByText(/remove-me.txt.*Ready/).waitFor({timeout:40000});
 await page.getByRole('button',{name:'Remove remove-me.txt',exact:true}).click();
 if(await page.locator('#forge-attachments').innerText().then(t=>t.includes('remove-me')))throw Error('Removal failed');
 await page.reload();
 await page.locator('#forge-attachments').getByText(/picker-note.txt.*Ready/).waitFor({timeout:40000});
 await page.locator('#forge-message').fill('Read the two attached text files and reply with only their two verification phrases. Do not change files.');
 await page.waitForFunction(()=>!document.getElementById('forge-send').disabled);
 await page.screenshot({path:'/tmp/forge-attachments-wide.png'});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'/tmp/forge-attachments-narrow.png',fullPage:true});
 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)) throw Error('Narrow page overflow');
 await page.getByRole('button',{name:'Send',exact:true}).click();
 await page.getByText('Reply received.',{exact:true}).waitFor({timeout:240000});
 const log=await page.locator('#forge-transcript').innerText();
 if(!log.includes('violet submarine')||!log.includes('silver orchard'))throw Error('Forge did not read file contents: '+log);
 await page.reload();
 await page.getByText('Reply received.',{exact:true}).waitFor({timeout:40000});
 if(!(await page.locator('#forge-transcript').innerText()).includes('picker-note.txt'))throw Error('Attachment history missing');
 const thread=await page.evaluate(()=>localStorage.getItem('alicia.forge.thread'));
 fs.writeFileSync('/tmp/forge-attachments-browser-result.json',JSON.stringify({passed:true,thread,log},null,2));
 console.log('PASS: picker, drop, remove, reload, narrow layout and Forge file read; thread '+thread);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
