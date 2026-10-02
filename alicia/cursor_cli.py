"""Bounded Cursor Pro completion for the Studio conversation tool loop."""
import json
import os
import signal
import subprocess
from pathlib import Path

from .session_supervisor import redact_supervisor_transcript


class CursorError(RuntimeError):
    def __init__(self, summary: str, diagnostic: str = ""):
        folded = diagnostic.casefold()
        self.error_code = "brain_service_unavailable"
        if any(term in folded for term in ("not authenticated", "authentication failed", "unauthenticated",
                                            "invalid api key", "expired token", "please log in", "please login")):
            self.error_code = "brain_auth_unavailable"
        elif any(term in folded for term in ("insufficient credits", "credit balance", "usage limit exceeded")):
            self.error_code = "brain_credits_exhausted"
        # Keep the provider's reason without copying injected machine secrets into
        # turn metadata or logs. Redact before truncating, including unlabeled keys.
        for name, value in os.environ.items():
            if value and len(value) >= 8 and any(part in name for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                diagnostic = diagnostic.replace(value, "[redacted]")
        safe = redact_supervisor_transcript(diagnostic).strip()[:400]
        super().__init__(summary + (": " + safe if safe else ""))


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
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise CursorError('Cursor conversation request timed out') from None
    if process.returncode:
        raise CursorError(f'Cursor conversation request failed (exit {process.returncode})', stderr or stdout)
    try:
        result = json.loads(stdout)
    except ValueError:
        raise RuntimeError('Cursor returned an invalid response') from None
    answer = str(result.get('result') or '').strip()
    if result.get('is_error') or not answer:
        raise CursorError('Cursor returned no successful answer', str(result.get('error') or result.get('result') or stderr or ''))
    return answer
