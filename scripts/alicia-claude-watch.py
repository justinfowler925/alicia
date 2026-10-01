#!/usr/bin/env python3
"""Claude hook bridge. Studio owns decisions; this process only reports and delivers context."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import urllib.request
import uuid

ROOT=Path.home()/'.local/share/alicia-watch'
CONFIG=ROOT/'config.json'


def api(path,data):
    config=json.loads(CONFIG.read_text())
    origin=config['url'].rstrip('/')
    request=urllib.request.Request(origin+'/api/watch'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','Origin':origin})
    with urllib.request.urlopen(request,timeout=12) as response:return json.load(response)


def redact(text):
    text=re.sub(r'\b(?:sk-[\w-]{16,}|gh[pousr]_[\w]{16,}|github_pat_[\w]{16,}|xox[baprs]-[\w-]{12,})\b','[redacted]',text)
    return re.sub(r'(?i)(\b(?:authorization|api[_ -]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+',r'\1[redacted]',text)


def system_prompt(text):
    return str(text).lstrip().startswith(('<task-notification>','<system-reminder>','<local-command-stdout>'))


def context(path,prompt=''):
    """Read bounded first/latest messages, not tool payloads or credential files."""
    p=Path(path).expanduser()
    allowed=Path.home()/'.claude/projects'
    if not p.is_file() or not p.resolve().is_relative_to(allowed.resolve()):return redact(prompt[:16000])
    with p.open('rb') as f:
        first=f.read(64000)
        f.seek(max(0,p.stat().st_size-192000))
        if f.tell():f.readline()
        last=f.read(192000)
    messages=[];seen=set()
    for raw in (first+ b'\n'+last).splitlines():
        try:r=json.loads(raw)
        except ValueError:continue
        key=r.get('uuid') or hashlib.sha256(raw).hexdigest()
        if key in seen:continue
        seen.add(key)
        message=r.get('message',{});role=message.get('role')
        if role not in ('user','assistant'):continue
        content=message.get('content','')
        if isinstance(content,list):content='\n'.join(x.get('text','') for x in content if isinstance(x,dict) and x.get('type')=='text')
        if isinstance(content,str) and content.strip():
            if r.get('promptSource')=='system' or (r.get('origin') or {}).get('kind')=='task-notification' or system_prompt(content):
                role='system observation (not owner authorization)'
            messages.append((role,content[:3500]))
    chosen=messages[:1]+messages[-7:]
    latest_label='SYSTEM OBSERVATION' if system_prompt(prompt) else 'LATEST USER'
    return redact('\n'.join(role.upper()+': '+text for role,text in chosen)+('\n'+latest_label+': '+prompt if prompt else ''))[-20000:]


def run(event):
    observed_at=time.time()
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    config=json.loads(CONFIG.read_text())
    sid=event.get('session_id','')
    if not re.fullmatch(r'[a-fA-F0-9-]{36}',sid):return
    name=event.get('hook_event_name','')
    if name=='UserPromptSubmit' and system_prompt(event.get('prompt','')):name='SystemPrompt'
    if name in ('PostToolUse','PostToolUseFailure'):
        throttle=ROOT/(sid+'.seen')
        if throttle.exists() and time.time()-throttle.stat().st_mtime<25:return
        throttle.touch()
    transcript=event.get('transcript_path','')
    ctx=context(transcript,event.get('prompt','')) if name in ('Stop','StopFailure','UserPromptSubmit','SystemPrompt','Notification') else ''
    if name=='Notification':
        ctx += '\nNOTIFICATION: '+redact(str(event.get('message','')))[:2000]
    if name=='Stop' and event.get('last_assistant_message'):
        ctx += '\nLAST ASSISTANT: '+redact(str(event['last_assistant_message']))[:3000]
    api('/events',{'host':config['host'],'session_id':sid,'event_id':str(uuid.uuid4()),'event':name,
        'cwd':event.get('cwd',''),'title':Path(event.get('cwd','')).name,
        'context':ctx[-23000:],'notification_type':event.get('notification_type',''),'observed_at':observed_at})
    if name=='SessionEnd':return
    # asyncRewake attaches this listener to the existing Claude session. Never start a competing --resume process.
    lock=(ROOT/(sid+'.listener.lock')).open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    started=time.time()
    while time.time()-started<23*3600:
        try:
            result=api('/sessions/'+config['host']+':'+sid+'/poll',{})
            command=result.get('command')
            if command:
                # Claude's documented asyncRewake exit code resumes this same session.
                print(command['text'],file=sys.stderr,flush=True)
                raise SystemExit(2)
        except (OSError,ValueError):pass
        time.sleep(10)


if __name__=='__main__':
    try:run(json.load(sys.stdin))
    except (OSError,ValueError,KeyError):
        # Monitoring failure must not break the user's Claude work.
        pass
