const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
function client(){
 const requests=[],sent=[];
 const context={Audio:class{},crypto:require('node:crypto').webcrypto,setTimeout:()=>0,clearTimeout:()=>{},fetch:async(url,opts)=>{requests.push({url,body:JSON.parse(opts.body)});return {ok:true,json:async()=>({reply:'Done'})}}};
 vm.createContext(context);
 vm.runInContext(fs.readFileSync('alicia/static/live-voice.js','utf8').replace('export class LiveVoice','class LiveVoice')+'\nglobalThis.LiveVoice=LiveVoice;',context);
 const voice=new context.LiveVoice('scratch',()=>{},()=>{});voice.send=e=>sent.push(e);
 return {voice,requests,sent};
}
function input(v,id,text,start,end){v.receive({type:'session.input_transcript.delta',event_id:id,delta:text,start_ms:start,end_ms:end})}
function output(v,id,text,start,end){v.receive({type:'session.output_transcript.delta',event_id:id,delta:text,start_ms:start,end_ms:end})}
(async()=>{
 {const {voice,requests}=client();
 input(voice,'u1','What command?',0,1000);output(voice,'a1','Use the CLI.',1200,2200);
 input(voice,'u2','Now check ',2500,2800);input(voice,'u3','again.',2800,3000);
 await voice.delegate({offset_ms:3100,delegation:{id:'work-1'}});
 const work=requests.filter(x=>x.url.endsWith('/live-delegation'));
 assert.equal(work.length,1);assert.equal(work[0].body.message,'Now check again.');assert.equal(work[0].body.transcript_ids.length,1);
 const saved=requests.filter(x=>x.url.endsWith('/live-transcript'));
 assert(saved.some(x=>x.body.id===work[0].body.transcript_ids[0]&&x.body.text==='Now check again.'));
 assert.deepEqual(saved.find(x=>x.body.role==='user').body.event_ids,['u1']);
 }
 {const {voice,requests}=client();
 input(voice,'u1','Check the ',2000,2400);
 output(voice,'a1','I am still speaking.',1800,2500); // delayed overlapping output
 input(voice,'u2','other project.',2400,2900);
 output(voice,'a2','Here is the last detail.',2500,3300);
 input(voice,'u2','other project.',2400,2900); // replay must not repeat words
 await voice.delegate({offset_ms:3400,delegation:{id:'overlap'}});
 const work=requests.find(x=>x.url.endsWith('/live-delegation'));
 assert.equal(work.body.message,'other project.');
 assert(requests.some(x=>x.body.role==='user'&&x.body.text==='Check the '),'Earlier instruction stays in durable history');
 assert.equal(work.body.transcript_ids.length,1,'Delegation references the latest actual saved user caption');
 const saved=requests.find(x=>x.body.id===work.body.transcript_ids[0]);
 assert.equal(saved.body.start_ms,2400);assert.equal(saved.body.end_ms,2900);
 input(voice,'u3','Then check the next one.',3600,4100);
 await voice.delegate({offset_ms:4200,delegation:{id:'second'}});
 assert.equal(requests.filter(x=>x.url.endsWith('/live-delegation'))[1].body.message,'Then check the next one.');
 }
 {const {voice,requests}=client();
 input(voice,'old','Old question',0,900);output(voice,'first','Old answer',1000,1100);
 input(voice,'new','New overlapping instruction',1100,1200);output(voice,'continued','More old answer',1400,1600);
 await voice.delegate({offset_ms:1700,delegation:{id:'exact-overlap'}});
 assert.equal(requests.find(x=>x.url.endsWith('/live-delegation')).body.message,'New overlapping instruction');
 assert(requests.some(x=>x.body.text==='Old question'));
 }
 {const {voice,requests,sent}=client();
 input(voice,'u1','First part ',0,1000);input(voice,'u2','future words',1500,2200);
 await voice.delegate({offset_ms:1200,delegation:{id:'crossing'}});
 assert.equal(requests.length,0,'A caption crossing the cutoff must never dispatch partial words');
 assert.match(sent[0].content,/have not run it/);assert.equal(voice.input.length,2);
 }
 {const {voice,requests}=client();
 input(voice,'u1','Unknown-timing words');output(voice,'a1','Do not assume those words were answered');
 await voice.delegate({delegation:{id:'legacy'}});
 assert.equal(requests.find(x=>x.url.endsWith('/live-delegation')).body.message,'Unknown-timing words');
 }
 console.log('PASS receive→persist→delegate: latest user caption plus saved history, overlapping input preserved, replay deduped, two delegations separate, timestamps retained, ambiguous cutoff refused');
})().catch(e=>{console.error(e);process.exitCode=1});
