"""Private Studio tools for local Forge; no model providers or user browser profiles."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

KNOWLEDGE_TOOLS = {'search_project_knowledge', 'read_project_source', 'project_status'}
BROWSER_TOOLS = {'browser_navigate', 'browser_snapshot', 'browser_click', 'browser_type',
                 'browser_select_option', 'browser_press_key', 'browser_wait_for',
                 'browser_take_screenshot', 'browser_close'}


def definition(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties,
                           'required': required, 'additionalProperties': False}}}


FILE_TOOLS = [
    definition('file_list', 'List up to 200 entries in a chat workspace directory. Paths are relative to this workspace.',
               {'path': {'type': 'string', 'default': '.'}}, []),
    definition('file_read', 'Read UTF-8 text from a chat workspace file, at most 300 lines and 64000 bytes. Use workspace_command for binary document extraction.',
               {'path': {'type': 'string'}, 'start_line': {'type': 'integer', 'minimum': 1},
                'max_lines': {'type': 'integer', 'minimum': 1, 'maximum': 300}}, ['path']),
    definition('file_write', 'Write UTF-8 text in the chat workspace. Creates parent folders. Existing files require overwrite=true. Attachments are read-only; write edited copies elsewhere.',
               {'path': {'type': 'string'}, 'content': {'type': 'string'},
                'overwrite': {'type': 'boolean', 'default': False}}, ['path', 'content']),
]


def workspace_path(workspace, value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('A workspace path is required')
    root = Path(workspace).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path is outside this chat workspace')
    return path


def file_tool(name, args, workspace):
    root = Path(workspace).resolve()
    path = workspace_path(root, args.get('path', '.'))
    if name == 'file_list':
        entries = []
        for entry in sorted(path.iterdir(), key=lambda p: p.name):
            # Do not disclose even metadata from symlink targets outside the workspace.
            if entry.is_symlink():
                kind = 'symlink'
            else:
                kind = 'directory' if entry.is_dir() else 'file'
            entries.append({'name': entry.name, 'type': kind})
            if len(entries) == 201:
                break
        return {'path': str(path), 'entries': entries[:200], 'truncated': len(entries) > 200}
    if name == 'file_read':
        if path.stat().st_size > 2_000_000:
            raise ValueError('Text file exceeds 2 MB; use workspace_command to extract a bounded section')
        start, count = args.get('start_line', 1), args.get('max_lines', 300)
        if type(start) is not int or type(count) is not int or start < 1 or not 1 <= count <= 300:
            raise ValueError('Use start_line >= 1 and max_lines between 1 and 300')
        lines, size, truncated = [], 0, False
        with path.open('r', encoding='utf-8') as stream:
            for index, line in enumerate(stream, 1):
                if index < start:
                    continue
                if len(lines) >= count or size + len(line.encode()) > 64000:
                    truncated = True
                    break
                lines.append(line)
                size += len(line.encode())
        return {'path': str(path), 'start_line': start, 'text': ''.join(lines), 'truncated': truncated}
    if name == 'file_write':
        if path == root or path.is_relative_to(root / 'attachments'):
            raise ValueError('Keep attachments intact; create an edited copy outside attachments/')
        content = args['content']
        if not isinstance(content, str) or len(content.encode()) > 200000:
            raise ValueError('Write at most 200000 bytes of UTF-8 text')
        overwrite = args.get('overwrite', False)
        if type(overwrite) is not bool:
            raise ValueError('overwrite must be true or false')
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_TRUNC if overwrite else os.O_EXCL)
        with os.fdopen(os.open(path, flags, 0o600), 'w', encoding='utf-8') as stream:
            stream.write(content)
        return {'path': str(path), 'bytes': len(content.encode()),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    raise ValueError('Unavailable file tool')


class StdioMCP:
    """One bounded, serial MCP session. Children are always reaped on close."""
    def __init__(self, command, cwd, log_path, cancelled=lambda: False):
        self.cancelled = cancelled
        self.responses = queue.Queue()
        self.next_id = 0
        self.log = log_path.open('a')
        self.proc = None
        try:
            self.proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=self.log,
                                         text=True, start_new_session=True)
            threading.Thread(target=self._read, daemon=True).start()
            self.request('initialize', {'protocolVersion': '2024-11-05',
                         'capabilities': {}, 'clientInfo': {'name': 'forge-local', 'version': '1'}}, timeout=30)
            self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while True:
                line = self.proc.stdout.readline(2_000_001)
                if not line:
                    break
                if len(line) > 2_000_000:
                    raise ValueError('Tool response exceeded 2 MB')
                self.responses.put(json.loads(line))
        except (OSError, ValueError) as exc:
            self.responses.put(exc)
        finally:
            self.responses.put(EOFError('Tool server closed its output'))

    def _send(self, message):
        self.proc.stdin.write(json.dumps(message) + '\n')
        self.proc.stdin.flush()

    def request(self, method, params, timeout=60):
        self.next_id += 1
        request_id = self.next_id
        self._send({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancelled():
                raise InterruptedError('Stopped by user')
            try:
                value = self.responses.get(timeout=.2)
            except queue.Empty:
                continue
            if isinstance(value, Exception):
                raise value
            if value.get('method') and 'id' in value:
                # This client never grants elicitation, sampling, or model access.
                self._send({'jsonrpc': '2.0', 'id': value['id'],
                            'error': {'code': -32601, 'message': 'Client method unavailable'}})
                continue
            if value.get('id') != request_id:
                continue
            if 'error' in value:
                raise ValueError(str(value['error']))
            return value['result']
        raise TimeoutError('Tool server timed out')

    def close(self):
        if self.proc:
            # MCP servers dispose browser contexts when their input closes.
            # Give that cleanup a bounded chance before terminating the group.
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                    self.proc.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
            # Descendants can survive the parent; reap the entire owned group.
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait()
            self.proc.stdin.close()
            self.proc.stdout.close()
        self.log.close()


class ToolSession:
    def __init__(self, workspace, directory, cancelled=lambda: False):
        self.workspace, self.directory = Path(workspace), Path(directory)
        self.cancelled = cancelled
        self.clients, self.routes = {}, {}
        self.tools = list(FILE_TOOLS)
        self.unavailable = {}

    def discover(self):
        browser_output = self.directory / 'browser'
        browser_output.mkdir(exist_ok=True, mode=0o700)
        servers = {
            'knowledge': ([
                '/Volumes/SSD-2TB/milvus/sdk/python/.venv/bin/python',
                '/Volumes/SSD-2TB/milvus/pipelines/project-knowledge/project_knowledge.py', 'serve'], KNOWLEDGE_TOOLS),
            'browser': ([
                '/opt/homebrew/bin/node', str(Path.home() / '.local/share/shine/current/node_modules/playwright/cli.js'),
                'mcp', '--headless', '--isolated', '--block-service-workers', '--image-responses', 'omit',
                '--output-dir', str(browser_output), '--timeout-navigation', '15000', '--timeout-action', '5000'], BROWSER_TOOLS),
        }
        for server, (command, allowed) in servers.items():
            client = None
            try:
                client = StdioMCP(command, self.workspace, self.directory / (server + '-tools.log'), self.cancelled)
                listed = client.request('tools/list', {}, timeout=30)['tools']
                selected = [t for t in listed if t['name'] in allowed]
                missing = allowed - {t['name'] for t in selected}
                if missing:
                    raise ValueError('Missing configured tools: ' + ', '.join(sorted(missing)))
                self.clients[server] = client
                for tool in selected:
                    schema = tool['inputSchema']
                    description = tool.get('description', '')[:3000]
                    if tool['name'] == 'browser_take_screenshot':
                        schema = {**schema, 'properties': {**schema.get('properties', {}),
                                  'filename': {'type': 'string', 'description': 'Optional output filename, such as proof.png, inside this run\'s browser directory.'}}}
                        description += ' Return the exact absolute path from result.artifacts; do not invent a filename.'
                    self.routes[tool['name']] = server
                    self.tools.append({'type': 'function', 'function': {
                        'name': tool['name'], 'description': description,
                        'parameters': schema}})
            except InterruptedError:
                if client:
                    client.close()
                raise
            except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
                if client:
                    client.close()
                self.unavailable[server] = str(exc)
        (self.directory / 'tool-capabilities.json').write_text(json.dumps({
            'tools': [t['function']['name'] for t in self.tools], 'unavailable': self.unavailable,
            'browser': 'isolated headless; no user profile or cookies',
            'file_write_root': str(self.workspace), 'inference': 'Studio resident Gemma only'}, indent=2))
        return self.tools

    def call(self, name, args):
        if name in {'file_list', 'file_read', 'file_write'}:
            return file_tool(name, args, self.workspace)
        server = self.routes.get(name)
        if not server or server not in self.clients:
            raise ValueError('Tool is unavailable in this run: ' + name)
        # Prevent a browser screenshot filename from escaping its private output directory.
        if server == 'browser' and args.get('filename'):
            args = {**args, 'filename': str(workspace_path(self.directory / 'browser', args['filename']))}
        screenshot_path = None
        if name == 'browser_take_screenshot' and args.get('filename'):
            args = dict(args)
            screenshot_path = Path(args.pop('filename'))
            if screenshot_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
                raise ValueError('Screenshot filename must end in .png, .jpg or .jpeg')
            if screenshot_path.exists():
                raise ValueError('Screenshot already exists; choose a new filename')
            args['type'] = 'png' if screenshot_path.suffix.lower() == '.png' else 'jpeg'
        if name == 'search_project_knowledge':
            args = {**args, 'limit': min(max(int(args.get('limit', 4)), 1), 6)}
        if name == 'read_project_source':
            start = max(int(args.get('start_line', 1)), 1)
            args = {**args, 'start_line': start,
                    'end_line': min(int(args.get('end_line', start + 299)), start + 299)}
        client = self.clients[server]
        try:
            result = client.request('tools/call', {'name': name, 'arguments': args})
        except InterruptedError:
            raise  # The worker's finally closes the browser before reaping servers.
        except (TimeoutError, EOFError, OSError):
            client.close()
            del self.clients[server]
            raise
        # Gemma receives text and paths; screenshot pixels stay in the saved artifact.
        text = '\n'.join(block.get('text', '') for block in result.get('content', []) if block.get('type') == 'text')
        artifacts = []
        if server == 'browser':
            for link in re.findall(r'\]\(([^)]+)\)', text):
                path = (self.workspace / link).resolve()
                if not path.is_relative_to((self.directory / 'browser').resolve()) or not path.is_file():
                    continue
                if screenshot_path and path.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    path.rename(screenshot_path)
                    text = text.replace(link, str(screenshot_path))
                    path = screenshot_path
                    screenshot_path = None
                artifacts.append({'path': str(path), 'bytes': path.stat().st_size,
                                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            if screenshot_path and not result.get('isError'):
                raise ValueError('Browser did not return a saved screenshot; requested filename was not created')
        return {'ok': not result.get('isError', False), 'text': text[:32000],
                'artifacts': artifacts, 'truncated': len(text) > 32000}

    def close(self):
        browser = self.clients.get('browser')
        if browser:
            # Closing the context also closes Chromium children that may own a
            # separate process group. Cancellation must not cancel cleanup.
            browser.cancelled = lambda: False
            try:
                browser.request('tools/call', {'name': 'browser_close', 'arguments': {}}, timeout=5)
            except (OSError, ValueError, KeyError, TypeError, EOFError):
                pass
        for client in self.clients.values():
            client.close()
        self.clients.clear()
