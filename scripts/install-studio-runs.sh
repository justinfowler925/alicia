#!/usr/bin/env bash
# Installs only the read-only observer. Does not alter any feed job.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STUDIO_TARGET=${ALICIA_STUDIO_SSH:-100.102.92.119}
ssh -o BatchMode=yes "$STUDIO_TARGET" 'mkdir -p /Users/jfstudio/.local/share/brutus-studio-runs'
scp -q "$ROOT/alicia/studio_collector.py" "$STUDIO_TARGET:/Users/jfstudio/.local/share/brutus-studio-runs/collector.py.next"
ssh -o BatchMode=yes "$STUDIO_TARGET" /usr/bin/python3 - <<'PY'
import pathlib, plistlib, subprocess
root = pathlib.Path.home()/'.local/share/brutus-studio-runs'
compile((root/'collector.py.next').read_text(), 'collector.py', 'exec')
(root/'collector.py.next').replace(root/'collector.py')
label='com.jfstudio.brutus-studio-runs'
p=pathlib.Path.home()/'Library/LaunchAgents'/f'{label}.plist'
p.write_bytes(plistlib.dumps({'Label':label,'ProgramArguments':['/usr/bin/python3',str(root/'collector.py')], 'StartInterval':60,'RunAtLoad':True,'StandardOutPath':str(root/'observer.out'),'StandardErrorPath':str(root/'observer.err')}))
import os
domain=f'gui/{os.getuid()}'
subprocess.run(['/bin/launchctl','bootout',domain+'/'+label],capture_output=True)
subprocess.run(['/bin/launchctl','bootstrap',domain,str(p)],check=True)
PY
