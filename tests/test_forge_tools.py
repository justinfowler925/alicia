"""Real filesystem and stdio protocol probes; model-loop failure controls."""
import json
import sys
import threading
from pathlib import Path

import pytest

from alicia import forge_chat, forge_local
from alicia.forge_tools import (
    BROWSER_TOOLS,
    FILE_TOOLS,
    GITHUB_TOOL_NAMES,
    KNOWLEDGE_TOOLS,
    StdioMCP,
    ToolSession,
    file_tool,
    github_tool,
)


def test_file_roundtrip_hash_and_overwrite_guard(tmp_path):
    result = file_tool('file_write', {'path': 'out/report.txt', 'content': 'first\nsecond\n'}, tmp_path)
    assert len(result['sha256']) == 64
    assert file_tool('file_list', {'path': 'out'}, tmp_path)['entries'] == [{'name': 'report.txt', 'type': 'file'}]
    assert file_tool('file_read', {'path': 'out/report.txt', 'start_line': 2}, tmp_path)['text'] == 'second\n'
    with pytest.raises(FileExistsError):
        file_tool('file_write', {'path': 'out/report.txt', 'content': 'oops'}, tmp_path)
    assert (tmp_path / 'out/report.txt').read_text() == 'first\nsecond\n'
    file_tool('file_write', {'path': 'out/report.txt', 'content': 'edited', 'overwrite': True}, tmp_path)
    assert (tmp_path / 'out/report.txt').read_text() == 'edited'


def test_file_boundaries_and_attachment_preservation(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'private.txt'
    outside.write_text('private')
    (workspace / 'link').symlink_to(outside)
    for path in ('../private.txt', str(outside), 'link'):
        for name in ('file_read', 'file_write'):
            with pytest.raises(ValueError, match='outside'):
                file_tool(name, {'path': path, 'content': 'changed'}, workspace)
    (workspace / 'attachments').mkdir()
    original = workspace / 'attachments/original.txt'
    original.write_text('original')
    with pytest.raises(ValueError, match='attachments'):
        file_tool('file_write', {'path': str(original), 'content': 'changed', 'overwrite': True}, workspace)
    assert outside.read_text() == 'private' and original.read_text() == 'original'


def test_bounded_read(tmp_path):
    (tmp_path / 'rows.txt').write_text('row\n' * 500)
    result = file_tool('file_read', {'path': 'rows.txt'}, tmp_path)
    assert len(result['text'].splitlines()) == 300 and result['truncated']


@pytest.fixture
def mcp_server(tmp_path):
    script = tmp_path / 'server.py'
    script.write_text('''import sys,json,time
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 method=r.get('method')
 if method=='slow': time.sleep(10)
 if method=='error':
  print(json.dumps({'jsonrpc':'2.0','id':r['id'],'error':{'code':-1,'message':'intentional failure'}}),flush=True)
  continue
 print(json.dumps({'jsonrpc':'2.0','method':'notifications/message','params':{'message':'progress'}}),flush=True)
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'method':method}}),flush=True)
''')
    return [sys.executable, str(script)]


def test_mcp_protocol_and_process_cleanup(tmp_path, mcp_server):
    client = StdioMCP(mcp_server, tmp_path, tmp_path / 'server.log')
    try:
        assert client.request('tools/list', {}) == {'method': 'tools/list'}
        with pytest.raises(ValueError, match='intentional failure'):
            client.request('error', {})
    finally:
        client.close()
    assert client.proc.poll() is not None


def test_mcp_cancel_and_cleanup(tmp_path, mcp_server):
    cancel = threading.Event()
    client = StdioMCP(mcp_server, tmp_path, tmp_path / 'server.log', cancel.is_set)
    cancel.set()
    try:
        with pytest.raises(InterruptedError):
            client.request('slow', {})
    finally:
        client.close()
    assert client.proc.poll() is not None


def test_discover_is_files_only_by_default(tmp_path, monkeypatch):
    """Cold turns must not spawn MCP or inject optional schemas."""
    def boom(*args, **kwargs):
        raise AssertionError('StdioMCP must not start on discover')
    monkeypatch.setattr('alicia.forge_tools.StdioMCP', boom)
    session = ToolSession(tmp_path, tmp_path)
    names = {t['function']['name'] for t in session.discover()}
    assert names == {t['function']['name'] for t in FILE_TOOLS}
    caps = json.loads((tmp_path / 'tool-capabilities.json').read_text())
    assert caps['loading'] == 'on_demand'
    assert 'github' in caps['available'] and 'shine' in caps['available']
    assert caps['enabled'] == []
    assert 'browser_navigate' not in caps['tools']
    session.close()


def test_enable_knowledge_and_browser_allowlist(tmp_path, monkeypatch):
    instances = []
    class FakeClient:
        def __init__(self, command, *args):
            self.command, self.closed = command, False
            instances.append(self)
        def request(self, method, args, **kwargs):
            names = KNOWLEDGE_TOOLS if self.command[-1] == 'serve' else BROWSER_TOOLS
            if method == 'tools/list':
                return {'tools': [{'name': n, 'inputSchema': {'type': 'object'}} for n in names | {'dangerous_extra'}]}
            return {'isError': True, 'content': [{'type': 'text', 'text': 'blocked'}]}
        def close(self):
            self.closed = True
    monkeypatch.setattr('alicia.forge_tools.StdioMCP', FakeClient)
    session = ToolSession(tmp_path, tmp_path)
    assert {t['function']['name'] for t in session.discover()} == {t['function']['name'] for t in FILE_TOOLS}
    knowledge = session.enable('knowledge')
    assert knowledge['ok'] and set(knowledge['tools']) == KNOWLEDGE_TOOLS
    browser = session.enable('browser')
    assert browser['ok'] and set(browser['tools']) == BROWSER_TOOLS
    names = {t['function']['name'] for t in session.tools}
    assert names == KNOWLEDGE_TOOLS | BROWSER_TOOLS | {t['function']['name'] for t in FILE_TOOLS}
    assert '--headless' in instances[1].command and '--isolated' in instances[1].command
    assert '--no-sandbox' not in instances[1].command
    with pytest.raises(ValueError, match='unavailable'):
        session.call('dangerous_extra', {})
    with pytest.raises(ValueError, match='outside'):
        session.call('browser_take_screenshot', {'filename': '../../outside.png'})
    assert session.call('browser_snapshot', {})['ok'] is False
    session.close()
    assert all(c.closed for c in instances)


def test_enable_knowledge_unavailable_returns_error(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('missing runtime')
    monkeypatch.setattr('alicia.forge_tools.StdioMCP', unavailable)
    session = ToolSession(tmp_path, tmp_path)
    session.discover()
    with pytest.raises(ValueError, match='unavailable'):
        session.enable('knowledge')
    assert {t['function']['name'] for t in session.tools} == {t['function']['name'] for t in FILE_TOOLS}
    assert 'knowledge' in session.unavailable
    session.close()


def test_enable_skill_returns_bounded_text_without_schemas(tmp_path, monkeypatch):
    skill_root = tmp_path / '.agents' / 'skills' / 'shine'
    skill_root.mkdir(parents=True)
    (skill_root / 'SKILL.md').write_text('# Shine\n' + ('x' * 15000))
    (skill_root / 'references').mkdir()
    (skill_root / 'references' / 'a.md').write_text('ref')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    session = ToolSession(tmp_path, tmp_path)
    before = [t['function']['name'] for t in session.discover()]
    result = session.enable('shine')
    assert result['ok'] and result['skill'] == 'shine' and result['truncated'] is True
    assert len(result['text']) == 12000
    assert [t['function']['name'] for t in session.tools] == before
    assert 'shine' in session.enabled
    session.close()


def test_enable_github_adds_compact_tools_and_rejects_mutations(tmp_path, monkeypatch):
    monkeypatch.setattr('alicia.forge_tools.gh_binary', lambda: '/bin/true')
    calls = []
    def fake_run(argv, timeout=60):
        calls.append(argv)
        return {'ok': True, 'output': 'ok', 'argv': argv}
    monkeypatch.setattr('alicia.forge_tools.run_gh', fake_run)
    session = ToolSession(tmp_path, tmp_path)
    session.discover()
    result = session.enable('github')
    assert result['ok'] and set(result['tools']) == GITHUB_TOOL_NAMES
    names = {t['function']['name'] for t in session.tools}
    assert GITHUB_TOOL_NAMES <= names
    assert len(GITHUB_TOOL_NAMES) == 5
    # Mutations via github_api path flags are rejected before gh runs.
    with pytest.raises(ValueError, match='GET-only'):
        github_tool('github_api', {'path': 'repos/o/r -X POST'})
    with pytest.raises(ValueError, match='GET-only'):
        github_tool('github_api', {'path': 'repos/o/r --method DELETE'})
    assert session.call('github_repo', {'repo': 'justinfowler925/alicia'})['ok']
    assert calls == [['repo', 'view', 'justinfowler925/alicia']]
    session.close()


def test_screenshot_returns_verified_requested_path(tmp_path):
    browser = tmp_path / 'browser'
    browser.mkdir()
    original = browser / 'generated.png'
    original.write_bytes(b'fake PNG fixture')
    class Client:
        def request(self, method, params):
            assert 'filename' not in params['arguments']
            return {'content': [{'type': 'text', 'text': '[Screenshot](browser/generated.png)'}]}
    session = ToolSession(tmp_path, tmp_path)
    session.clients['browser'] = Client()
    session.routes['browser_take_screenshot'] = 'browser'
    result = session.call('browser_take_screenshot', {'filename': 'requested.png'})
    assert result['artifacts'][0]['path'] == str(browser / 'requested.png')
    assert (browser / 'requested.png').read_bytes() == b'fake PNG fixture'
    assert not original.exists()
    assert 'generated.png' not in result['text']


def test_content_addressed_bundle_installs_both_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    payload = {'local_source': 'worker-v1', 'tools_source': 'tools-v1'}
    old_path = sys.path[:]
    try:
        scope = {'p': payload, 'sys': sys, 'json': json}
        exec(forge_chat.INSTALL_LOCAL, scope)  # noqa: S102 — exercise the exact trusted installer
        first = scope['root']
        assert (first / 'forge_tools.py').read_text() == 'tools-v1'
        scope['p'] = {**payload, 'tools_source': 'tools-v2'}
        exec(forge_chat.INSTALL_LOCAL, scope)  # noqa: S102 — exercise the exact trusted installer
        assert scope['root'] != first
        assert (first / 'forge_local.py').read_text() == 'worker-v1'
    finally:
        sys.path[:] = old_path


def test_worker_file_tools_receipts_and_all_fail_control(tmp_path, monkeypatch):
    monkeypatch.setattr(forge_local, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(ToolSession, 'discover', lambda self: self.tools)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    for tag, path, expected in [('good', 'proof.txt', 'succeeded'), ('bad', '../outside.txt', 'failed')]:
        run = forge_local.enqueue('forge', 'test files', cwd=str(workspace), work_item=tag)
        replies = iter([
            {'role': 'assistant', 'tool_calls': [{'id': 'file1', 'function': {
                'name': 'file_write', 'arguments': json.dumps({'path': path, 'content': 'proof'})}}]},
            {'role': 'assistant', 'content': 'Done'},
        ])
        monkeypatch.setattr(forge_local, 'completion', lambda *args, responses=replies: next(responses))
        forge_local.worker(run['id'])
        assert forge_local.get(run['id'])['status'] == expected
        receipts = [json.loads(line) for line in (forge_local.STATE / 'runs' / run['id'] / 'tool-receipts.jsonl').read_text().splitlines()]
        assert [r['state'] for r in receipts] == ['started', 'completed']
        assert receipts[-1]['ok'] == (tag == 'good')
    assert (workspace / 'proof.txt').read_text() == 'proof'
    assert not (tmp_path / 'outside.txt').exists()


def test_worker_mid_turn_enable_expands_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(forge_local, 'STATE', tmp_path / 'state')
    monkeypatch.setattr('alicia.forge_tools.gh_binary', lambda: '/bin/true')
    monkeypatch.setattr('alicia.forge_tools.run_gh', lambda argv, timeout=60: {'ok': True, 'output': 'repo-ok', 'argv': argv})
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    run = forge_local.enqueue('forge', 'use github', cwd=str(workspace), work_item='enable-github')
    seen_tools = []

    def complete(messages, tools):
        seen_tools.append([t['function']['name'] for t in tools])
        if len(seen_tools) == 1:
            assert 'github_repo' not in seen_tools[0]
            assert 'enable_capability' in seen_tools[0]
            return {'role': 'assistant', 'tool_calls': [{'id': 'e1', 'function': {
                'name': 'enable_capability', 'arguments': json.dumps({'name': 'github'})}}]}
        if len(seen_tools) == 2:
            assert 'github_repo' in seen_tools[1]
            return {'role': 'assistant', 'tool_calls': [{'id': 'g1', 'function': {
                'name': 'github_repo', 'arguments': json.dumps({'repo': 'justinfowler925/alicia'})}}]}
        return {'role': 'assistant', 'content': 'GitHub ready'}

    monkeypatch.setattr(forge_local, 'completion', complete)
    forge_local.worker(run['id'])
    assert forge_local.get(run['id'])['status'] == 'succeeded'
    assert len(seen_tools) == 3


def test_malformed_arguments_return_an_error():
    for arguments in ('{broken', '[]', '{"unknown":1}'):
        result = json.loads(forge_local.run_tool({'function': {'name': 'file_read', 'arguments': arguments}}, {}, 'test'))
        assert result['error']
