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
    monkeypatch.setattr(local.ToolSession, 'discover', lambda self: self.tools)
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


def test_web_search_returns_real_source_fields(monkeypatch):
    def read(url):
        assert 'q=512GB+Mac+Studio' in url
        return url, '<rss><channel><item><title>512GB unified memory</title><link>https://store.example/studio</link><description>Listing details</description></item></channel></rss>'
    monkeypatch.setattr(local, 'read_web', read)
    result = json.loads(local.web_search('512GB Mac Studio'))
    assert result['results'][0]['url'] == 'https://store.example/studio'
    assert result['query'] == '512GB Mac Studio'


def test_web_fetch_extracts_text_without_scripts(monkeypatch):
    monkeypatch.setattr(local, 'read_web', lambda url: (url, '<h1>512GB RAM</h1><script>PRIVATE_SCRIPT</script><p>Out of stock</p>'))
    result = json.loads(local.web_fetch('https://store.example/studio'))
    assert '512GB RAM' in result['text'] and 'Out of stock' in result['text']
    assert 'PRIVATE_SCRIPT' not in result['text']


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://user:password@example.com', 'http://127.0.0.1:8081', 'http://localhost'])
def test_public_web_boundary(url):
    with pytest.raises(ValueError):
        local.public_url(url)


def test_web_failure_is_returned_to_local_model(runtime, monkeypatch):
    monkeypatch.setattr(local, 'web_search', lambda query: (_ for _ in ()).throw(OSError('Search unavailable')))
    result = json.loads(local.run_tool({'function': {'name': 'web_search', 'arguments': '{"query":"computer"}'}}, runtime, runtime['id']))
    assert result['error'] == 'Search unavailable'
    assert result['tool'] == 'web_search'


def test_irrelevant_search_retries_with_numeric_constraint(monkeypatch):
    seen = []
    def read(url):
        seen.append(url)
        title = 'Cosmetics' if len(seen) == 1 else '512GB unified memory workstation'
        return url, f'<rss><channel><item><title>{title}</title><link>https://store.example/product</link></item></channel></rss>'
    monkeypatch.setattr(local, 'read_web', read)
    result = json.loads(local.web_search('Mac Studio 512GB'))
    assert len(seen) == 2 and 'q=512GB+Mac+Studio' in seen[-1]
    assert '512GB' in result['results'][0]['title']


def test_web_answer_without_sources_gets_corrected(runtime, monkeypatch):
    calls = []
    def complete(messages, tools):
        calls.append(list(messages))
        if len(calls) == 1:
            return {'role': 'assistant', 'tool_calls': [{'id': 'web1', 'function': {'name': 'web_search', 'arguments': '{"query":"computer"}'}}]}
        if len(calls) == 2:
            return {'role': 'assistant', 'content': 'An unsupported claim with no source'}
        if len(calls) == 3:
            assert 'omitted retrieved sources' in messages[-1]['content']
            return {'role': 'assistant', 'tool_calls': [{'id': 'web2', 'function': {'name': 'web_fetch', 'arguments': '{"url":"https://store.example/product"}'}}]}
        return {'role': 'assistant', 'content': 'Source: https://store.example/product'}
    monkeypatch.setattr(local, 'completion', complete)
    monkeypatch.setattr(local, 'web_search', lambda query: json.dumps({'results': [{'url': 'https://store.example/product'}]}))
    monkeypatch.setattr(local, 'web_fetch', lambda url: json.dumps({'url': url, 'text': 'Listing details'}))
    local.worker(runtime['id'])
    assert len(calls) == 4 and local.get(runtime['id'])['status'] == 'succeeded'
    assert (local.STATE / 'runs' / runtime['id'] / 'web-sources.jsonl').exists()


def test_browser_read_satisfies_search_source_verification(runtime, monkeypatch):
    replies = iter([
        {'role': 'assistant', 'tool_calls': [{'id': 's', 'function': {'name': 'web_search', 'arguments': '{"query":"example"}'}}]},
        {'role': 'assistant', 'tool_calls': [{'id': 'b', 'function': {'name': 'browser_snapshot', 'arguments': '{}'}}]},
        {'role': 'assistant', 'content': 'Observed page: https://example.com/'},
    ])
    monkeypatch.setattr(local, 'completion', lambda *args: next(replies))
    monkeypatch.setattr(local, 'web_search', lambda query: json.dumps({'results': [{'url': 'https://example.com/'}]}))
    monkeypatch.setattr(local.ToolSession, 'call', lambda *args: {'ok': True, 'text': '- Page URL: https://example.com/\n### Snapshot\nExample Domain'})
    local.worker(runtime['id'])
    assert local.get(runtime['id'])['status'] == 'succeeded'
