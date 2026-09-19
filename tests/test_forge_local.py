"""Regression: Forge must execute local Gemma, never the hosted persona launcher."""
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from brutus import forge_local as local


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(local, 'STATE', tmp_path / 'state')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    return local.enqueue('forge', 'Reply locally', cwd=str(workspace), work_item='test')


def test_local_worker_and_historical_identity(runtime, monkeypatch):
    monkeypatch.setattr(local, 'completion', lambda messages, tools: {'role': 'assistant', 'content': 'Local answer'})
    local.worker(runtime['id'])
    run = local.get(runtime['id'])
    assert run['status'] == 'succeeded' and run['model'] == local.MODEL
    assert (local.STATE / 'runs' / run['id'] / 'answer.md').read_text() == 'Local answer'
    with sqlite3.connect(local.STATE / 'runs.sqlite3') as c:
        c.execute('CREATE TABLE runs(id TEXT, model TEXT, status TEXT)')
        c.execute('INSERT INTO runs VALUES(?,?,?)', ('old', 'gpt-6-astra', 'succeeded'))
    assert local.get('old')['model'] == 'gpt-6-astra'
    assert 'studio_agents' not in Path(local.__file__).read_text()


def test_unavailable_local_model_fails_without_fallback(runtime, monkeypatch):
    calls = []
    def unavailable(*args):
        calls.append(1)
        raise ConnectionError('connection refused')
    monkeypatch.setattr(local, 'completion', unavailable)
    local.worker(runtime['id'])
    run = local.get(runtime['id'])
    assert run['status'] == 'failed'
    assert 'No hosted fallback' in run['reason']
    assert len(calls) == 1


def test_stop_while_inference_is_waiting(runtime, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def waiting(*args):
        started.set()
        release.wait(5)
        return {'role': 'assistant', 'content': 'must not appear'}
    monkeypatch.setattr(local, 'completion', waiting)
    worker = threading.Thread(target=local.worker, args=(runtime['id'],))
    worker.start()
    try:
        assert started.wait(2)
        local.update(runtime['id'], cancel=1)
        worker.join(2)
        assert not worker.is_alive()
        assert local.get(runtime['id'])['status'] == 'cancelled'
        assert not (local.STATE / 'runs' / runtime['id'] / 'answer.md').exists()
    finally:
        release.set()


def test_response_model_must_match(monkeypatch):
    import io
    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url == 'http://127.0.0.1:8081/v1/chat/completions'
            assert json.loads(request.data)['model'] == local.MODEL
            return io.BytesIO(json.dumps({'model': 'gpt-6-astra', 'choices': []}).encode())
    monkeypatch.setattr(local.urllib.request, 'build_opener', lambda *args: FakeOpener())
    with pytest.raises(ValueError, match='different model'):
        local.completion([], [])


def test_tool_results_return_to_same_local_model(runtime, monkeypatch):
    calls = []
    def complete(messages, tools):
        calls.append(list(messages))
        if len(calls) == 1:
            return {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': 't1', 'type': 'function', 'function': {
                    'name': 'workspace_command', 'arguments': '{"command":"pwd"}'}}]}
        assert messages[-1] == {'role': 'tool', 'tool_call_id': 't1', 'content': 'workspace result'}
        return {'role': 'assistant', 'content': 'Done locally'}
    monkeypatch.setattr(local, 'completion', complete)
    monkeypatch.setattr(local, 'workspace_command', lambda *args: 'workspace result')
    local.worker(runtime['id'])
    assert len(calls) == 2 and local.get(runtime['id'])['status'] == 'succeeded'
