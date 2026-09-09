"""Studio tab behavior, composed into Brutus's existing shell and native grid."""

STUDIO_JS = r"""
let ST={jobs:[],summary:{}}, STload='loading', STerror='', STbusy=false;
let SF={category:'feed',q:'',health:'',sort:'name',dir:'asc',page:1,size:10,cols:{schedule:true,last_success_at:true}}, STselected='';
function orderedGridRows(rows,key,dir){const sign=dir==='asc'?1:-1;return [...rows].sort((a,b)=>{
  let av=a[key],bv=b[key];if(key.endsWith('_at')){av=av?Date.parse(av):null;bv=bv?Date.parse(bv):null;}if(av==null)return bv==null?0:1;if(bv==null)return -1;
  return (typeof av==='number'&&typeof bv==='number'?av-bv:String(av).localeCompare(String(bv),undefined,{numeric:true}))*sign;});}
function studioTime(value,zone){if(!value)return 'Unknown';try{return new Intl.DateTimeFormat(undefined,{timeZone:zone||'America/Chicago',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZoneName:'short'}).format(new Date(value));}catch(_){return 'Unknown';}}
function studioHealth(value){return ({healthy:'Healthy',failed:'Failed',stale:'Stale',running:'Running',unknown:'Unknown',never_ran:'Never ran'})[value]||'Unknown';}
function studioPopulation(){return ST.jobs.filter(j=>SF.category==='all'||(j.category||'feed')===SF.category);}
function studioRows(){return orderedGridRows(filterRows(studioPopulation(),SF.q,['name','source','id','scheduler']).filter(j=>!SF.health||j.health===SF.health),SF.sort,SF.dir);}
function studioFilter(){SF.q=el('studio-search').value;SF.health=el('studio-health').value;SF.page=1;render();}
function studioClear(){SF.q='';SF.health='';SF.page=1;if(el('studio-search'))el('studio-search').value='';render();}
function studioSort(key){SF.dir=SF.sort===key&&SF.dir==='asc'?'desc':'asc';SF.sort=key;SF.page=1;render();}
function studioSelect(id){STselected=id;render();requestAnimationFrame(()=>el('studio-detail')?.focus());}
function studioColumn(key,on){SF.cols[key]=on;render();}
function studioPage(delta){SF.page+=delta;render();}
function studioHead(key,label){return `<th aria-sort="${SF.sort===key?(SF.dir==='asc'?'ascending':'descending'):'none'}"><button type="button" data-sort-key="${key}" onclick="studioSort('${key}')">${label}${SF.sort===key?(SF.dir==='asc'?' ↑':' ↓'):''}</button></th>`;}
function studioDetail(){const j=ST.jobs.find(j=>j.id===STselected);if(!j)return '';
  const logs=Object.keys(j.logs||{}).map(kind=>`<a target="_blank" rel="noopener" href="/api/studio-runs/${encodeURIComponent(j.id)}/log?kind=${kind}">Open ${kind==='receipt'?'receipt':kind==='stderr'?'error log':'latest log'}</a>`).join(' · ');
  return `<section id="studio-detail" class="nucleus-detail" tabindex="-1" aria-label="Run details"><h2>${esc(j.name)}</h2><p>${esc(j.source)}</p><p class="dim">${esc(j.category_reason||'')}</p>
    <p><strong>${studioHealth(j.health)}</strong> · Last run status: ${esc(j.status)} · Host: Studio</p>
    <p>Last run: ${studioTime(j.last_run_at,j.timezone)} · Last successful run: ${studioTime(j.last_success_at,j.timezone)}</p>
    <p>Duration: ${j.duration_seconds==null?'Unknown':esc(Math.round(j.duration_seconds)+' seconds')} · Next run: ${j.next_run_at?studioTime(j.next_run_at,j.timezone):esc(j.next_run_note)}</p>
    <p>Schedule: ${esc(j.schedule.label)} · ${esc(j.schedule.timezone)}</p>
    ${j.retry_at?`<p>Retry after: ${studioTime(j.retry_at,j.timezone)}</p>`:''}
    <p>${esc(j.health_reason||'')}</p><ul>${(j.notes||[]).map(n=>`<li>${esc(n)}</li>`).join('')}</ul>
    <p>${logs||'No log path registered'}</p>
    <details><summary>Scheduler and receipt details</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify({job:j.id,scheduler:j.scheduler,definition:j.definition,loaded:j.loaded,pid:j.pid,exit_code:j.exit_code,evidence:j.evidence},null,2))}</pre></details>
    <button type="button" onclick="STselected='';render()">Close details</button></section>`;}
function studioHtml(){
  if(STload==='loading')return '<div class="none" role="status" data-state="loading">Reading Studio run evidence…</div>';
  if(STload==='error')return `<div class="none" role="alert" data-state="error">${esc(STerror)} <button type="button" onclick="loadStudio()">Retry</button></div>`;
  const rows=studioRows(),pages=Math.max(1,Math.ceil(rows.length/SF.size));SF.page=Math.min(SF.page,pages);const start=(SF.page-1)*SF.size;
  const counts=Object.fromEntries(Object.keys(ST.summary||{}).map(k=>[k,0]));studioPopulation().forEach(j=>{counts[j.health]=(counts[j.health]||0)+1;});
  const summary=Object.entries(counts).map(([k,v])=>`<button class="chip" type="button" aria-pressed="${SF.health===k}" onclick="SF.health=SF.health==='${k}'?'':'${k}';SF.page=1;render()">${studioHealth(k)} <strong>${v}</strong></button>`).join('');
  return `<div data-cite="shadcn-queue" data-product-pattern="brutus-record-grid">
    <label>Show <select id="studio-category" aria-label="Job category" onchange="SF.category=this.value;SF.health='';SF.page=1;STselected='';render()">${Object.entries({feed:'Data feeds',sandbox:'Sandbox jobs',service:'Supporting services',maintenance:'Maintenance',history:'Historical journals',inactive:'Inactive jobs',unclassified:'Needs classification',all:'All discovered entries'}).map(([k,label])=>`<option value="${k}" ${SF.category===k?'selected':''}>${label} (${k==='all'?ST.jobs.length:ST.jobs.filter(j=>(j.category||'feed')===k).length})</option>`).join('')}</select></label>
    <div class="streams" aria-label="Studio summary">${summary}</div>
    ${ST.connection!=='live'?`<p class="inline-err" role="alert" data-state="error" style="display:block">${esc(ST.error||'Studio observation is older than 3 minutes. Status may have changed.')} <button type="button" onclick="loadStudio()">Retry</button></p>`:''}
    <div class="nucleus-toolbar" id="studio-toolbar"><label class="sr-only" for="studio-search">Search Studio jobs</label><input id="studio-search" placeholder="Search jobs or feeds…" value="${esc(SF.q)}" onkeydown="if(event.key==='Enter')studioFilter()">
      <select id="studio-health" aria-label="Filter by health" onchange="studioFilter()"><option value="">All states</option>${Object.keys(ST.summary||{}).map(k=>`<option value="${k}" ${SF.health===k?'selected':''}>${studioHealth(k)}</option>`).join('')}</select>
      <button type="button" onclick="studioFilter()">Search</button><button type="button" onclick="studioClear()">Clear</button>
      <details class="nucleus-cols"><summary>Columns</summary><div class="menu">${Object.entries({schedule:'Schedule',last_success_at:'Last success'}).map(([k,label])=>`<label><input type="checkbox" ${SF.cols[k]?'checked':''} onchange="studioColumn('${k}',this.checked)">${label}</label>`).join('')}</div></details>
      <button type="button" onclick="loadStudio()" ${STbusy?'disabled':''}>${STbusy?'Checking…':'Refresh'}</button></div>
    <p class="dim">${rows.length} matching jobs · Summary covers this category. ${ST.jobs.filter(j=>j.category==='unclassified').length?'New jobs need classification; choose Needs classification.':''} Scroll across for schedule and run details.</p><div class="nucleus-grid" data-grid-scroll tabindex="0" aria-label="Studio jobs; scroll horizontally for all columns">
      ${!rows.length?`<div class="none" data-state="${SF.q||SF.health?'filtered-empty':'empty'}">${SF.q||SF.health?'No jobs match these filters. Clear to see all jobs.':'No Studio jobs have been observed. Check the collector connection.'}</div>`:''}
      <table class="nucleus-table" id="studio-table" data-shine-contract="table" aria-label="Studio runs"><thead><tr>${studioHead('name','Job / feed')}${studioHead('health','Health')}${studioHead('last_run_at','Last run')}${SF.cols.last_success_at?studioHead('last_success_at','Last success'):''}${SF.cols.schedule?'<th>Schedule / next run</th>':''}<th>Inspect</th></tr></thead><tbody>
      ${rows.slice(start,start+SF.size).map(j=>`<tr><td class="project-cell"><strong>${esc(j.name)}</strong><span>${esc(j.source)}</span><span>Studio</span></td>
        <td><span class="status ${j.health==='failed'?'blocked':j.health==='stale'?'at_risk':j.health==='healthy'?'quiet':''}">${studioHealth(j.health)}</span><span class="cell-sub">${esc(j.health_reason||j.notes?.[0]||'')}</span></td>
        <td>${studioTime(j.last_run_at,j.timezone)}<span class="cell-sub">${esc(j.status)}${j.duration_seconds==null?' · duration unknown':' · '+Math.round(j.duration_seconds)+'s'}</span></td>
        ${SF.cols.last_success_at?`<td>${studioTime(j.last_success_at,j.timezone)}</td>`:''}
        ${SF.cols.schedule?`<td>${esc(j.schedule.label)}<span class="cell-sub">${esc(j.schedule.timezone)}</span><span class="cell-sub">Next: ${j.next_run_at?studioTime(j.next_run_at,j.timezone):esc(j.next_run_note)}</span></td>`:''}
        <td><button type="button" data-row-action data-job="${esc(j.id)}" onclick="studioSelect(this.dataset.job)">Details</button></td></tr>`).join('')}</tbody></table>
      <div class="nucleus-pager" aria-label="Studio pagination"><span>${rows.length?`${start+1}–${Math.min(start+SF.size,rows.length)} of ${rows.length}`:'0 of 0'} jobs</span><label>Rows <select aria-label="Rows per page" onchange="SF.size=Number(this.value);SF.page=1;render()">${[10,20,50,100].map(n=>`<option ${SF.size===n?'selected':''}>${n}</option>`).join('')}</select></label><button type="button" ${SF.page===1?'disabled':''} onclick="studioPage(-1)">Previous</button><span>Page ${SF.page} of ${pages}</span><button type="button" ${SF.page===pages?'disabled':''} onclick="studioPage(1)">Next</button></div></div>
    ${studioDetail()}<details class="brief"><summary>Discovery coverage</summary><div class="body">${esc((ST.coverage?.sources||[]).join(' · '))}<br>${esc(ST.coverage?.launchd_definitions_checked||0)} launchd definitions inspected. ${(ST.coverage?.errors||[]).map(esc).join('<br>')}</div></details></div>`;
}
async function loadStudio(){if(STbusy)return;STbusy=true;if(page==='studio')render();try{
  const r=await fetch('/api/studio-runs',{cache:'no-store'});if(!r.ok)throw new Error('Studio evidence request failed ('+r.status+')');ST=await r.json();STload='ok';STerror='';
}catch(e){STload='error';STerror=e.message;}finally{STbusy=false;if(page==='studio')render();}}
"""
