"""Read-only, bounded projections for Alicia's project workspace."""
from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter
from .studio_collector import redact
from .studio_runs import snapshot

router = APIRouter(prefix='/api/workspace', tags=['workspace'])
APPLICATIONS = [
 {'id':'demo-maker','name':'Demo Maker','description':'Create voice demos and publish them to the demo library.','url':os.environ.get('DEMO_MAKER_URL',''),'category':'Create'},
 {'id':'clearspeed-demos','name':'Clearspeed Demos','description':'Insurance, banking and voice verification experiences.','url':'https://www.clearspeeddemos.com/','category':'Present'},
 {'id':'hollywood','name':'Hollywood','description':'Studio media production, assets and render jobs.','url':os.environ.get('HOLLYWOOD_URL',''),'category':'Create'},
]

def read_json(path: Path):
    try:
        if path.stat().st_size > 4_000_000: return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError): return {}


def scout_projection(root: Path, observation: dict):
    status = read_json(root/'status.json')
    installation = read_json(root/'installation.json')
    feeds = read_json(root/'feeds/status.json')
    registry = read_json(root/'registry.json')
    jobs = [j for j in observation.get('jobs', []) if 'scout' in j.get('id','').lower()]
    history = []
    for feed in ('notices','federal_news','adjacent'):
        paths = sorted((root/f'feeds/{feed}/runs').glob('*/receipt.json'), reverse=True)[:20]
        for path in paths:
            run=read_json(path)
            history.append({'feed':feed,'id':path.parent.name,'at':run.get('completed_at') or run.get('checked_at') or path.parent.name,
                'status':run.get('status') or ('failed' if run.get('exit') else 'unknown'),
                'error':redact(str(run.get('error') or run.get('stderr_tail') or ''))[-2000:],
                'source':str(path),'exit':run.get('exit')})
    history.sort(key=lambda r:str(r['at']),reverse=True)
    configured=[]
    for name, config in (registry.get('feeds') or feeds.get('config') or {}).items():
        if isinstance(config,dict): configured.append({'name':name,'enabled':config.get('enabled'),'publication':config.get('publication'),'trigger':config.get('trigger','Scout schedule')})
    return {'checked_at':datetime.now(timezone.utc).isoformat(),'connection':observation.get('connection','unavailable'),
        'collected_at':observation.get('collected_at'),'error':observation.get('error',''),
        'installed':bool(status or installation),'mode':status.get('mode','Unknown'),'version':status.get('version'),
        'status_observed_at':status.get('checked_at'),'revision':installation.get('revision'),
        'states':status.get('states',[]),'feeds':configured,'jobs':jobs,'history':history[:40],
        'history_scope':'Latest 20 receipts per feed; up to 40 shown. Routine output is available in each scheduled job’s log.',
        'summary':{'jobs':len(jobs),'running':sum(j.get('status')=='running' for j in jobs),
                   'failed':sum(j.get('health')=='failed' for j in jobs),
                   'unknown':sum(j.get('health') in ('unknown','stale','never_ran') for j in jobs)}}

@router.get('/apps')
def apps(): return {'apps':APPLICATIONS}

@router.get('/scout')
def scout(): return scout_projection(Path.home()/'.local/share/sled-scout', snapshot())
