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
subprocess.run(['launchctl','bootstrap',target,str(p)],check=True)
PY
ssh "$host" /opt/homebrew/bin/tailscale serve --bg --https=8768 http://127.0.0.1:8768
