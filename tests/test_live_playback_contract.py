"""Behavioral proof through the actual browser voice/SSE handlers."""
import subprocess
from pathlib import Path


def test_live_playback_ownership_survives_disconnect_and_retry():
    script = Path(__file__).with_name('live_playback_contract.cjs')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_live_playback_regressions_are_independently_detected():
    script = Path(__file__).with_name('live_playback_contract.cjs')
    source = script.parents[1] / 'alicia/static/session.js'
    for mutation, expected in [
        ('initial', 'initial/secondary tab must not call legacy speech'),
        ('stop', 'Stop must preserve live playback ownership'),
        ('product', 'product-owned flag cannot bypass live ownership'),
    ]:
        result = subprocess.run(['node', str(script), str(source), mutation], capture_output=True, text=True, timeout=20)
        assert result.returncode != 0, f'{mutation} escaped the contract'
        assert expected in result.stderr, result.stdout + result.stderr
