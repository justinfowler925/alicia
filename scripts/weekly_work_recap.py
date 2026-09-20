#!/usr/bin/env python3
"""Private, evidence-linked weekly work recap. Runs on Studio; no model required."""
from __future__ import annotations
import datetime as dt
import html
import json
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo('America/Chicago')
ACCOUNTS = ('justinfowler925', 'justin-fowler_cspd')


def window(now):
    """Seven complete Central calendar days, ending Saturday midnight; DST aware."""
    local = now.astimezone(CENTRAL)
    end = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end -= dt.timedelta(days=(end.weekday() - 5) % 7)
    return end - dt.timedelta(days=7), end


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def in_window(value, start, end):
    value = timestamp(value) if isinstance(value, str) else value
    return bool(value and start <= value < end)


def api(account, endpoint, params=None, paginate=False):
    # Read existing account credentials without switching global gh identity or logging secrets.
    token = subprocess.check_output(['gh','auth','token','--user',account], text=True, stderr=subprocess.DEVNULL).strip()
    env = dict(os.environ, GH_TOKEN=token)
    cmd = ['gh','api','--method','GET',endpoint]
    if paginate: cmd += ['--paginate','--slurp']
    for key,value in (params or {}).items(): cmd += ['-f',f'{key}={value}']
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
    if p.returncode: raise RuntimeError(f'GitHub request failed ({p.returncode}): {endpoint.split("?")[0]}')
    data = json.loads(p.stdout)
    if paginate and data and isinstance(data[0], list): return [v for page in data for v in page]
    return data


def search_prs(account, start, end):
    dates = f'{start.astimezone(dt.UTC).isoformat()}..{end.astimezone(dt.UTC).isoformat()}'
    pages = api(account,'search/issues',{'q':f'is:pr author:{account} updated:{dates}','per_page':100},True)
    if any(p.get('incomplete_results') or p.get('total_count',0)>1000 for p in pages):
        raise RuntimeError('GitHub PR search is incomplete; cannot claim complete coverage')
    return [item for page in pages for item in page.get('items',[])]


def pr_record(item, start, end):
    merged = item.get('pull_request',{}).get('merged_at')
    status = 'Merged this week' if in_window(merged,start,end) else ('Open' if item['state']=='open' else 'Closed / earlier merge')
    return {'title':item['title'],'url':item['html_url'],'number':item['number'],
            'status':status,'created_at':item['created_at'],'updated_at':item['updated_at'],
            'merged_at':merged,'category':category(item['title'])}


def category(title):
    title = title.lower()
    if re.search(r'\b(fix|repair|restore|resolve|correct|prevent)\b',title): return 'Fixes'
    if re.search(r'\b(add|create|introduce|implement|build|enable|embed)\b',title): return 'Features and creations'
    if re.search(r'\b(deploy|release|publish|ship)\b',title): return 'Release-related changes'
    if re.search(r'\b(docs|document|record|write|readme|plan)\b',title): return 'Documentation and plans'
    return 'Improvements and changes'


def studio_artifacts(start,end):
    roots=[Path.home()/'Projects/reports',Path.home()/'mflux-out',Path.home()/'Sites']
    extensions={'.pdf','.docx','.pptx','.xlsx','.csv','.png','.jpg','.webp','.mp4','.mp3','.wav','.html'}
    items=[];scanned=0
    for root in roots:
        if not root.exists(): continue
        for directory,dirs,files in os.walk(root):
            dirs[:]=[d for d in dirs if d not in {'.git','node_modules','.venv','venv','cache','__pycache__'}]
            for name in files:
                scanned+=1
                if scanned>50000:return items,True
                path=Path(directory)/name
                if path.suffix.lower() not in extensions:continue
                try:
                    stat=path.stat();modified=dt.datetime.fromtimestamp(stat.st_mtime,dt.UTC)
                    if in_window(modified,start,end):items.append({'name':name,'path':str(path.relative_to(Path.home())),'modified_at':modified.isoformat(),'bytes':stat.st_size})
                except OSError:continue
    return sorted(items,key=lambda a:a['modified_at'],reverse=True),False


def collect(now, efficiency=None):
    start,end=window(now);projects={};gaps=[];coverage=[]
    for account in ACCOUNTS:
        try:
            prs=search_prs(account,start,end)
            repos=api(account,'user/repos',{'affiliation':'owner,collaborator,organization_member','sort':'pushed','per_page':100},True)
            coverage.append({'source':account,'status':'ok','repositories_visible':len(repos),'prs_updated':len(prs)})
        except Exception as exc:
            gaps.append(f'{account}: {exc}');continue
        for item in prs:
            name=item['repository_url'].split('/repos/')[-1]
            project=projects.setdefault(name,{'name':name,'url':f'https://github.com/{name}','prs':[],'commits':[],'deployments':[],'releases':[],'new':False})
            project['prs'].append(pr_record(item,start,end))
        relevant={r['full_name']:r for r in repos if in_window(r.get('created_at'),start,end) or (timestamp(r.get('pushed_at')) and timestamp(r['pushed_at']) >= start)}
        for name in projects:
            if name.startswith(account+'/') and name not in relevant: relevant[name]={'full_name':name,'created_at':None,'default_branch':'main'}
        def repo_work(repo):
            name=repo['full_name']; result={'name':name,'url':f'https://github.com/{name}','prs':[],'commits':[],'deployments':[],'releases':[],'new':in_window(repo.get('created_at'),start,end)};errors=[]
            try:
                commits=api(account,f'repos/{name}/commits',{'sha':repo.get('default_branch') or 'main','since':start.isoformat(),'until':end.isoformat(),'author':account,'per_page':100},True)
                result['commits']=[{'sha':c['sha'],'url':c['html_url'],'title':c['commit']['message'].splitlines()[0],'date':c['commit']['committer']['date'],'category':category(c['commit']['message'].splitlines()[0])} for c in commits if in_window(c['commit']['committer']['date'],start,end)]
            except Exception: errors.append(f'{name}: default-branch commits unavailable (empty repository or access failure)')
            try:
                # Bound the provider history; mark truncation rather than silently losing a busy week.
                deployments=[]
                if not result['commits'] and name not in projects and not result['new']:
                    return result,errors
                for page in range(1,11):
                    batch=api(account,f'repos/{name}/deployments',{'per_page':100,'page':page})
                    deployments.extend(batch)
                    if len(batch)<100 or timestamp(batch[-1]['created_at'])<start: break
                else: errors.append(f'{name}: deployment history exceeds 1000 recent entries; partial coverage')
                for dep in deployments:
                    if not in_window(dep['created_at'],start,end): continue
                    statuses=api(account,dep['statuses_url'],{'per_page':100})
                    success=next((s for s in statuses if s['state']=='success' and in_window(s['created_at'],start,end)),None)
                    if success:
                        result['deployments'].append({'sha':dep['sha'],'environment':dep['environment'],'url':success.get('log_url') or success.get('target_url') or dep['statuses_url'],'date':success['created_at'],'evidence':'GitHub deployment status: success; not a fresh live workflow check'})
            except Exception: errors.append(f'{name}: deployment records unavailable')
            try:
                releases=api(account,f'repos/{name}/releases',{'per_page':100})
                result['releases']=[{'title':r['name'] or r['tag_name'],'url':r['html_url'],'date':r['published_at']} for r in releases if not r['draft'] and in_window(r.get('published_at'),start,end)]
                if len(releases)==100 and timestamp(releases[-1].get('published_at')) and timestamp(releases[-1]['published_at'])>=start: errors.append(f'{name}: release history exceeds 100 entries; partial coverage')
            except Exception: errors.append(f'{name}: releases unavailable')
            return result,errors
        with ThreadPoolExecutor(max_workers=4) as pool:
            for result,errors in pool.map(repo_work,relevant.values()):
                gaps.extend(errors)
                if any(result[k] for k in ('prs','commits','deployments','releases','new')) or result['name'] in projects:
                    prior=projects.get(result['name']);result['prs']=prior['prs'] if prior else []
                    if prior:
                        for key,identity in [('commits','sha'),('deployments','url'),('releases','url')]:
                            result[key]=list({row[identity]:row for row in prior[key]+result[key]}.values())
                    projects[result['name']]=result
    projects={name:p for name,p in projects.items() if any(p[k] for k in ('prs','commits','new','releases'))}
    for project in projects.values():
        project['prs']=list({p['url']:p for p in project['prs']}.values())
    # Existing collector owns optional laptop metadata. An offline laptop is a visible gap.
    optional=(efficiency or {}).get('source_coverage',{})
    for source in ('codex','claude','cursor','canon'):
        if not optional.get(source): gaps.append(f'{source}: laptop activity unavailable in existing collector; GitHub collection continued on Studio')
    artifacts,truncated=studio_artifacts(start,end)
    if truncated:gaps.append('Studio artifact scan exceeded 50,000 files; partial coverage')
    return {'artifacts':artifacts,'schema_version':1,'generated_at':now.isoformat(),'window':{'start':start.isoformat(),'end_exclusive':end.isoformat(),'timezone':'America/Chicago'},
            'projects':sorted(projects.values(),key=lambda p:(-len(p['prs']),p['name'])), 'coverage':coverage,'gaps':gaps,
            'efficiency':{'metrics':(efficiency or {}).get('scorecard',{}).get('metrics',{}),'recommended_action':(efficiency or {}).get('recommended_action',{})},
            'limits':['PRs include both configured accounts’ authored PRs updated in the window. Default-branch commits are filtered to the configured GitHub authors. Other collaborators and unlinked author identities are excluded.',
                      'Feature/fix categories are title-based labels. Commit and PR counts overlap and must not be added as unique outcomes.',
                      'Deployment records describe repository activity, which can include collaborators and previews. Missing GitHub deployment records do not mean nothing was deployed. Deployment success is provider evidence, not proof of current production behavior. Local-only files, unsaved work, and non-Git creations are not fully covered.']}


def atomic_write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False,encoding='utf-8') as f: f.write(value);temp=Path(f.name)
    temp.chmod(0o600);temp.replace(path)


def render(report):
    esc=lambda s:html.escape(str(s),quote=True)
    def link(url,label):
        return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>' if str(url).startswith('https://') else esc(label)
    projects=report['projects']; prs=[p for r in projects for p in r['prs']]; merged=sum(p['status']=='Merged this week' for p in prs)
    deploys=sum(len(p['deployments']) for p in projects);new=sum(p['new'] for p in projects)
    parts=[]
    for project in projects:
        groups=defaultdict(list)
        for pr in project['prs']: groups[pr['category']].append(pr)
        items=''.join(f'<h3>{esc(cat)}</h3><ul>'+''.join(f'<li>{link(p["url"],p["title"])} <span class="tag">{esc(p["status"])}</span></li>' for p in rows)+'</ul>' for cat,rows in groups.items())
        if project['prs']:
            highlights=[p for p in project['prs'] if p['status']=='Merged this week' and p['category'] in ('Fixes','Features and creations','Improvements and changes')][:3]
            if not highlights: highlights=project['prs'][:3]
            highlights_html='<ul>'+''.join(f'<li>{link(p["url"],p["title"])} <span class="tag">{esc(p["status"])}</span></li>' for p in highlights)+'</ul>'
            items=highlights_html+f'<details><summary>All {len(project["prs"])} PRs, grouped by change</summary>'+items+'</details>'
        if project['commits']:
            items+=f'<details><summary>{len(project["commits"])} default-branch commits · inspect changes</summary><ul>'+''.join(f'<li>{link(c["url"],c["title"])} <small>{esc(c["sha"][:7])}</small></li>' for c in project['commits'])+'</ul></details>'
        if project['deployments']:
            items+='<details><summary>Successful deployment records</summary><ul>'+''.join(f'<li>{link(d["url"],d["environment"])} · {esc(d["sha"][:7])} · {esc(d["date"])}<br><small>{esc(d["evidence"])}</small></li>' for d in project['deployments'])+'</ul></details>'
        if project['releases']: items+='<h3>Published releases</h3><ul>'+''.join(f'<li>{link(r["url"],r["title"])}</li>' for r in project['releases'])+'</ul>'
        parts.append(f'<section class="project" data-name="{esc(project["name"].lower())}"><h2>{link(project["url"],project["name"].split("/")[-1])}</h2><p class="meta">{esc(project["name"])}'+(' · New repository this week' if project['new'] else '')+f' · {len(project["prs"])} PRs</p>{items}</section>')
    start=report['window']['start'][:10];last=(timestamp(report['window']['end_exclusive'])-dt.timedelta(days=1)).date().isoformat()
    artifacts_html='<section><h2>Reports and media</h2><p class="meta">Files modified during the week in Studio reports, generated-image, and Sites folders. Modification time is not proof of creation or publication.</p><details><summary>'+str(len(report.get('artifacts',[])))+' artifact files · inspect inventory</summary><ul>'+''.join(f'<li>{esc(a["name"])}<br><small>{esc(a["path"])} · {esc(a["modified_at"])}</small></li>' for a in report.get('artifacts',[]))+'</ul></details></section>'
    gaps='<ul>'+''.join(f'<li>{esc(g)}</li>' for g in report['gaps'])+'</ul>'
    coverage='<ul>'+''.join(f'<li>{esc(c["source"])}: {c["repositories_visible"]} repositories visible; {c["prs_updated"]} authored PRs updated.</li>' for c in report['coverage'])+'</ul>'
    return '''<!doctype html><html lang="en" data-cite="shadcn-blog"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Saturday work recap — Justin Fowler</title><style>
:root{color-scheme:dark;--bg:#11110f;--fg:#f5f3ed;--muted:#bcb9ae;--line:#393932;--accent:#edbd85}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.65 system-ui,sans-serif}main{max-width:1080px;margin:auto;padding:40px 24px}a{color:var(--accent);text-underline-offset:4px}h1{font:clamp(32px,5vw,54px)/1.1 Georgia,serif;letter-spacing:-.03em;margin:20px 0}h2{font:30px/1.2 Georgia,serif;margin:0}h3{font-size:18px;margin:28px 0 8px}.meta,small{color:var(--muted)}.stats{display:flex;gap:32px;flex-wrap:wrap;border-block:1px solid var(--line);padding:24px 0;margin:28px 0}.stats strong{display:block;font-size:30px}.project{padding:32px 0;border-bottom:1px solid var(--line)}li{margin:12px 0;overflow-wrap:anywhere}summary{cursor:pointer;min-height:44px;padding:10px 0;color:var(--accent)}.tag{font-size:12px;border:1px solid var(--line);border-radius:4px;padding:3px 7px;white-space:nowrap}input{display:block;width:100%;font:inherit;padding:12px;background:var(--bg);color:var(--fg);border:1px solid var(--muted);border-radius:4px}label{display:block;margin-bottom:6px}footer{margin-top:32px}nav{display:flex;gap:24px;flex-wrap:wrap}button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px}[hidden]{display:none}@media(max-width:600px){main{padding:24px 18px}.stats{gap:20px}.stats>div{min-width:40%}}@media print{input,label,nav{display:none}details{display:block}body{color:#111;background:white}a{color:#111}}
</style><main><nav aria-label="Recap navigation"><a href="index.html">Latest recap</a><a href="archive.html">Past Saturdays</a><a href="#coverage">Sources and coverage</a></nav><header><p class="meta">JUSTIN FOWLER · PRIVATE SATURDAY REVIEW</p><h1>Your week, in work.</h1>'''+f'<p>{esc(start)} – {esc(last)} · Central time · generated {esc(report["generated_at"])}</p><p>Projects, PRs, features, fixes, releases, and deployment records. Open a source to inspect what changed.</p></header><div class="stats"><div><strong>{len(projects)}</strong>active projects</div><div><strong>{merged}</strong>PRs merged this week</div><div><strong>{deploys}</strong>successful deployment records</div><div><strong>{new}</strong>new repositories</div></div><label for="search">Find a project or change</label><input id="search" type="search" placeholder="Search this week’s work"><p id="result-count" class="meta" aria-live="polite">{len(projects)} projects</p><div id="projects">'+''.join(parts)+f'</div>{artifacts_html}<section id="coverage"><h2>Sources and coverage</h2>{coverage}<details><summary>Unavailable or partial sources ({len(report["gaps"])})</summary>{gaps}</details><ul>'+''.join(f'<li>{esc(l)}</li>' for l in report['limits'])+'</ul></section><footer><p>Runs on Mac Studio every Saturday at 9:00 AM America/Chicago. This report stays on your private tailnet.</p></footer></main><script>const input=document.querySelector("#search");input.addEventListener("input",()=>{let count=0;document.querySelectorAll(".project").forEach(p=>{p.hidden=!p.textContent.toLowerCase().includes(input.value.toLowerCase());if(!p.hidden)count++});document.querySelector("#result-count").textContent=count+" projects"});</script></html>'


def write_report(report, directory):
    date=report['window']['end_exclusive'][:10]
    atomic_write(directory/f'{date}.json',json.dumps(report,indent=2)+'\n')
    page=render(report);atomic_write(directory/f'{date}.html',page);atomic_write(directory/'index.html',page)
    dates=sorted((p.stem for p in directory.glob('????-??-??.html')),reverse=True)
    atomic_write(directory/'archive.html','<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><meta charset="utf-8"><title>Saturday recap archive</title><body style="font:18px/1.6 system-ui;max-width:800px;margin:40px auto;padding:24px"><h1>Saturday recap archive</h1><a href="index.html">Latest recap</a><ul>'+''.join(f'<li><a href="{d}.html">Week ending {d}</a></li>' for d in dates)+'</ul></body></html>')
    return {'date':date,'projects':len(report['projects']),'gaps':len(report['gaps']),'page':str(directory/'index.html')}
