#!/bin/bash
# Deploy the committed source. State and config are deliberately outside it.
set -euo pipefail
host=jfstudio@100.102.92.119
root=$(cd "$(dirname "$0")/.." && pwd)
sha=$(git -C "$root" rev-parse HEAD)
git -C "$root" archive "$sha" | ssh "$host" 'mkdir -p ~/.alicia/app ~/.alicia/state ~/.alicia/logs; tar xf - -C ~/.alicia/app'
ssh "$host" 'cd ~/.alicia/app && /opt/homebrew/bin/uv sync --frozen --extra dev'
ssh "$host" "python3 - '$sha'" <<'PY'
import json,os,plistlib,subprocess,sys,time
from pathlib import Path
h=Path.home();root=h/'.alicia'
(root/'app/.alicia-deploy.json').write_text(json.dumps({'sha':sys.argv[1],'host':'Mac Studio','deployed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})+'\n')
p=h/'Library/LaunchAgents/com.jfstudio.alicia.plist'
job={'Label':'com.jfstudio.alicia','ProgramArguments':['/bin/bash',str(root/'app/scripts/alicia-studio-serve.sh')],
 'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,'WorkingDirectory':str(root/'app'),
 'StandardOutPath':str(root/'logs/service.log'),'StandardErrorPath':str(root/'logs/service.err.log')}
p.write_bytes(plistlib.dumps(job));target='gui/'+str(os.getuid())
subprocess.run(['launchctl','bootout',target+'/com.jfstudio.alicia'],capture_output=True)
for attempt in range(20):
    result=subprocess.run(['launchctl','bootstrap',target,str(p)],capture_output=True)
    if result.returncode == 0:
        break
    time.sleep(2)
else:
    raise RuntimeError('Studio service could not be registered after the previous service stopped')
PY
ssh "$host" /opt/homebrew/bin/tailscale serve --bg --https=8768 http://127.0.0.1:8768
# Demo Maker (the Alicia #demo-maker tab). Code ships in this repo; state lives
# in ~/.alicia/state/demo-maker (moved there once by scripts/demo-maker-cutover.sh).
ssh "$host" 'cd ~/.alicia/app/demo_maker && /opt/homebrew/bin/npm ci --omit=dev --silent'
ssh "$host" 'python3 ~/.alicia/app/scripts/demo-maker-service.py'
ssh "$host" /opt/homebrew/bin/tailscale serve --bg --https=8790 http://127.0.0.1:4173
