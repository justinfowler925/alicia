"""Bounded Cursor Pro completion for the Studio conversation tool loop."""
import json
import os
from pathlib import Path
import signal
import subprocess


def complete(prompt: str, *, timeout: float = 90, model: str | None = None) -> str:
    root = Path(os.environ.get('ALICIA_REASONING_ROOT', '~/.alicia/reasoning')).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    binary = os.environ.get('ALICIA_CURSOR_BIN', str(Path.home()/'.local/bin/cursor-studio'))
    model = model or os.environ.get('ALICIA_CURSOR_MODEL', 'auto')
    if any(s in model.lower() for s in ('claude', 'anthropic')):
        raise RuntimeError('Use Cursor Auto or a non-Anthropic Cursor model')
    child_env = os.environ.copy()
    child_env.pop("CREDENTIAL_CONTRACT", None)
    child_env.pop("CREDENTIAL_READER", None)
    process = subprocess.Popen([binary, '--trust', '--print', '--mode', 'ask', '--output-format', 'json',
        '--model', model, prompt], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True, env=child_env)
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise RuntimeError('Cursor conversation request timed out') from None
    if process.returncode:
        raise RuntimeError('Cursor conversation request failed; check Studio credentials')
    try:
        result = json.loads(stdout)
    except ValueError:
        raise RuntimeError('Cursor returned an invalid response') from None
    answer = str(result.get('result') or '').strip()
    if result.get('is_error') or not answer:
        raise RuntimeError('Cursor returned no successful answer')
    return answer
