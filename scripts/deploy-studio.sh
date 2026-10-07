#!/bin/bash
# Deploy the committed source. State and config are deliberately outside it.
set -euo pipefail
# ALICIA_STUDIO_SSH=local (or running on Studio with it unset) deploys in place;
# Studio cannot ssh to itself (host key verification fails).
host="${ALICIA_STUDIO_SSH:-}"
if [ -z "$host" ]; then
  [ "$(scutil --get LocalHostName 2>/dev/null)" = "jf-studio" ] || [ "$(id -un)" = "jfstudio" ] && host=local
fi
[ -n "$host" ] || { echo "set ALICIA_STUDIO_SSH to the Studio SSH target (or 'local' on Studio)"; exit 1; }
on_host() { if [ "$host" = local ]; then bash -c "$*"; else ssh "$host" "$@"; fi; }
root=$(cd "$(dirname "$0")/.." && pwd)
sha=$(git -C "$root" rev-parse HEAD)
git -C "$root" archive "$sha" | on_host 'mkdir -p ~/.alicia/app ~/.alicia/state ~/.alicia/logs; tar xf - -C ~/.alicia/app'
on_host 'cd ~/.alicia/app && /opt/homebrew/bin/uv sync --frozen --extra dev'
on_host "python3 - '$sha'" <<'PY'
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
# Serve fronts the proof-injecting proxy (:8767 → :8768). Direct :8768 stays
# loopback-only for local pairing; forged Tailscale headers there cannot mint studio_owner.
on_host 'mkdir -p ~/.alicia/state ~/.alicia/logs; chmod 700 ~/.alicia/state; chmod 600 ~/.alicia/state/*.sqlite ~/.alicia/state/*.token ~/.alicia/state/*.proof 2>/dev/null || true'
on_host /opt/homebrew/bin/tailscale serve --bg --https=8768 http://127.0.0.1:8767
# Demo Maker (the Alicia #demo-maker tab). Code ships in this repo; state lives
# in ~/.alicia/state/demo-maker (moved there once by scripts/demo-maker-cutover.sh).
on_host 'cd ~/.alicia/app/demo_maker && /opt/homebrew/bin/npm ci --omit=dev --silent'
on_host 'python3 ~/.alicia/app/scripts/demo-maker-service.py'
on_host /opt/homebrew/bin/tailscale serve --bg --https=8790 http://127.0.0.1:4173
