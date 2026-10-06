"""(Re)load the Demo Maker launchd job from the deployed checkout."""
import os, plistlib, subprocess, time
from pathlib import Path

home = Path.home(); root = home / '.alicia'; target = f'gui/{os.getuid()}'
label = 'com.jfstudio.alicia-demo-maker'
state = root / 'state/demo-maker'
if not (state / 'studio.sqlite3').exists():
    raise SystemExit(f'{state} has no database; run scripts/demo-maker-cutover.sh first')
plist = home / f'Library/LaunchAgents/{label}.plist'
plist.write_bytes(plistlib.dumps({
    'Label': label,
    'ProgramArguments': ['/opt/homebrew/bin/node', 'server/index.js'],
    'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
    'WorkingDirectory': str(root / 'app/demo_maker'),
    'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/bin:/bin', 'PORT': '4173', 'DEMO_MAKER_STATE': str(state)},
    'SoftResourceLimits': {'NumberOfFiles': 4096},
    'StandardOutPath': str(root / 'logs/demo-maker.log'),
    'StandardErrorPath': str(root / 'logs/demo-maker.err.log'),
}))
subprocess.run(['launchctl', 'bootout', f'{target}/{label}'], capture_output=True)
for _ in range(20):
    if subprocess.run(['launchctl', 'bootstrap', target, str(plist)], capture_output=True).returncode == 0:
        break
    time.sleep(2)
else:
    raise SystemExit('Demo Maker service could not be registered')
