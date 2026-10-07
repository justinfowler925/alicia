"""Route the retired laptop CLI to Studio, avoiding two writable Canon stores."""
import json
import os
from pathlib import Path
import shlex
import subprocess


def forward(args):
    if os.environ.get('ALICIA_LOCAL_CLI') == '1' or os.environ.get('ALICIA_STATE_DIR'):
        return None
    marker=Path.home()/'.alicia/remote.json'
    if not marker.exists():
        return None
    data=json.loads(marker.read_text())
    expected=(os.environ.get('ALICIA_STUDIO_SSH') or '').strip()
    if not expected:
        raise RuntimeError('Set ALICIA_STUDIO_SSH to forward the CLI to Studio')
    if data.get('host') != expected:
        raise RuntimeError('Unrecognized Alicia remote host')
    if args and args[0] in ('serve','ear'):
        raise RuntimeError('Alicia runs on Studio. Open '+data['url'])
    command='~/.alicia/app/scripts/alicia-studio-serve.sh '+shlex.join(args or ['--help'])
    return subprocess.call(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',data['host'],command])
