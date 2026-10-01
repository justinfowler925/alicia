#!/usr/bin/env python3
"""Install event-driven Claude hooks, preserving every unrelated setting/hook."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time


def install(home, source, host, url):
    root=home/'.local/share/alicia-watch'
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=root/'alicia-claude-watch.py'
    shutil.copy2(source,target)
    config=root/'config.json'
    config.write_text(json.dumps({'host':host,'url':url},indent=2)+'\n')
    config.chmod(0o600)
    settings=home/'.claude/settings.json'
    settings.parent.mkdir(parents=True,exist_ok=True)
    data=json.loads(settings.read_text()) if settings.exists() else {}
    if settings.exists():
        shutil.copy2(settings,root/('settings-backup-'+str(time.time_ns())+'.json'))
    hooks=data.setdefault('hooks',{})
    for event in ('SessionStart','SessionEnd','UserPromptSubmit','PostToolUse','PostToolUseFailure','Stop','StopFailure','Notification'):
        groups=hooks.setdefault(event,[])
        for group in groups:
            group['hooks']=[h for h in group.get('hooks',[]) if h.get('statusMessage')!='Alicia session watch']
        groups[:]=[g for g in groups if g.get('hooks')]
        hook={'type':'command','command':sys.executable,'args':[str(target)],'statusMessage':'Alicia session watch'}
        if event!='SessionEnd':
            hook.update(asyncRewake=True,timeout=86400)
        else:
            hook.update({'async':True,'timeout':15})
        groups.append({'hooks':[hook]})
    temporary=settings.with_suffix('.alicia-tmp')
    temporary.write_text(json.dumps(data,indent=2)+'\n')
    temporary.chmod(0o600)
    temporary.replace(settings)
    return {'installed':str(target),'host':host,'settings':str(settings),'backup_directory':str(root)}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',required=True)
    parser.add_argument('--url',default='https://justins-mac-studio-1.tailbaa084.ts.net:8768')
    args=parser.parse_args()
    print(json.dumps(install(Path.home(),Path(__file__).with_name('alicia-claude-watch.py'),args.host,args.url)))
