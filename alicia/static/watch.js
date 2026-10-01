/* One Studio decision record, whether answered here, in conversation or in Slack. */
(()=>{
const host=document.createElement('section');host.className='watch-panel';host.setAttribute('aria-label','Session watch');document.querySelector('.voice-stage').prepend(host);
const drafts=new Map();let fingerprint='',busy=false,notice='';
function node(tag,text,cls){const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;}
async function refresh(){
 if(document.body.dataset.page!=='alicia'||busy||(host.contains(document.activeElement)&&document.activeElement.tagName==='TEXTAREA'))return;
 try{
  const r=await fetch('/api/watch',{signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error('Session watch is unavailable.');const s=await r.json();
  const cr=await fetch('/api/commitments',{signal:AbortSignal.timeout(10000)});if(!cr.ok)throw Error('Coworker follow-ups are unavailable.');const work=await cr.json();
  const key=JSON.stringify([s.sessions.map(x=>[x.id,x.state,x.connected,x.summary]),s.decisions,s.slack,s.error,work]);if(key===fingerprint)return;fingerprint=key;
  const expanded=host.querySelector('.watch-sessions')?.open,workExpanded=host.querySelector('.coworker-followups')?.open;host.replaceChildren();const pending=s.decisions.filter(d=>d.status==='open'),connected=s.sessions.filter(x=>x.connected).length;
  const line=node('div',null,'watch-heading');line.append(node('strong','Claude sessions'),node('span',`${connected} connected · ${pending.length} need you`,'workspace-subtle'));host.append(line);
  if(s.error||s.slack.error)host.append(node('p',s.error||s.slack.error,'workspace-error'));
  if(notice)host.append(node('p',notice,'workspace-error'));
  if(!s.slack.connected)host.append(node('p','Slack is not connected yet. Decisions remain available here.','workspace-subtle'));
  for(const d of pending){
   const card=node('article',null,'watch-decision'),session=s.sessions.find(x=>x.id===d.session_id);
   card.append(node('h3',session?.title||d.session_id),node('p',d.question));if(d.recommendation)card.append(node('p','Recommendation: '+d.recommendation,'workspace-subtle'));
   const form=node('form',null,'workspace-toolbar'),label=node('label','Your reply'),input=node('textarea');input.rows=2;input.required=true;input.maxLength=4000;input.value=drafts.get(d.id)||'';input.addEventListener('input',()=>drafts.set(d.id,input.value));label.append(input);const send=node('button','Reply','workspace-button primary');send.type='submit';form.append(label,send);
   form.addEventListener('submit',async e=>{e.preventDefault();busy=true;send.disabled=true;notice='';try{const r=await fetch('/api/watch/decisions/'+encodeURIComponent(d.id)+'/reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:input.value})});if(!r.ok)throw Error(r.status===409?'Already answered in another channel. Your second reply was not sent.':'Reply was not saved. Please try again.');drafts.delete(d.id);}catch(e){notice=e.message;}finally{busy=false;send.disabled=false;input.blur();fingerprint='';refresh();}});card.append(form);host.append(card);
  }
  const detail=node('details',null,'watch-sessions');detail.open=!!expanded;detail.append(node('summary','Sessions & recent replies'));
  for(const x of s.sessions){const row=node('div',null,'watch-session');row.append(node('strong',x.title),node('span',(x.connected?'':'Connection unavailable · ')+x.state.replaceAll('_',' '),'workspace-subtle'));if(x.summary)row.append(node('p',x.summary,'workspace-subtle'));detail.append(row);}
  if(!s.sessions.length)detail.append(node('p','Waiting for Claude session events.','workspace-subtle'));
  for(const d of s.decisions.filter(x=>x.status!=='open').slice(0,5)){detail.append(node('p',`${d.question} — ${d.status}${d.reply?': '+d.reply:''}${d.delivery?' · '+d.delivery.replaceAll('_',' '):''}`,'workspace-subtle'));}
  host.append(detail);
  const follow=node('details',null,'coworker-followups');follow.open=!!workExpanded;
  const open=work.items.filter(x=>!['done','dismissed'].includes(x.status));
  follow.append(node('summary',`Coworker follow-ups · ${open.length} open`));
  for(const [name,source] of Object.entries(work.sources))follow.append(node('p',`${name==='gmail'?'Work email':'Slack messages'}: ${source.error||source.coverage||'Waiting for first check.'}`,'workspace-subtle'));
  if(work.pending_review)follow.append(node('p',`${work.pending_review} conversations still being reviewed.`,'workspace-subtle'));
  if(work.error)follow.append(node('p',work.error,'workspace-error'));
  for(const item of [...open,...work.items.filter(x=>['done','dismissed'].includes(x.status)).slice(0,5)]){
   const card=node('article',null,'watch-session');card.append(node('h3',item.title));
   const source=node('a','Original '+(item.provider==='gmail'?'email':'Slack message'));source.href=item.url;source.target='_blank';source.rel='noopener noreferrer';card.append(source);
   card.append(node('p',`${item.requester||'Coworker'} · ${item.due?'Due '+item.due:'No stated deadline'} · ${item.status.replaceAll('_',' ')}`,'workspace-subtle'));
   if(item.status==='check_completion')card.append(node('p','A reply suggests this is finished. Check the source before marking done.','workspace-subtle'));
   const form=node('form',null,'workspace-toolbar'),label=node('label','Status'),select=node('select');
   for(const [value,text] of [['open','Open'],['doing','Doing'],['waiting','Waiting'],['check_completion','Check completion'],['done','Done'],['dismissed','Dismissed']]){const opt=node('option',text);opt.value=value;opt.selected=value===item.status;select.append(opt);}label.append(select);
   const save=node('button','Save','workspace-button');save.type='submit';form.append(label,save);
   form.addEventListener('submit',async e=>{e.preventDefault();busy=true;save.disabled=true;notice='';try{const r=await fetch('/api/commitments/'+encodeURIComponent(item.id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:select.value,version:item.version})});if(!r.ok)throw Error(r.status===409?'This follow-up changed elsewhere. Review the latest status.':'Status was not saved; try again.');}catch(e){notice=e.message;}finally{busy=false;fingerprint='';refresh();}});card.append(form);follow.append(card);
  }
  if(!work.items.length)follow.append(node('p','No confirmed coworker obligations found yet.','workspace-subtle'));
  host.append(follow);
 }catch(e){if(!host.childNodes.length)host.append(node('p',e.message,'workspace-error'));}
}
setInterval(refresh,10000);window.addEventListener('hashchange',()=>{fingerprint='';refresh();});refresh();
})();
