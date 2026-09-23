"""Private Studio tools for local Forge; no model providers or user browser profiles.

Capability packs stay out of the model context until enable_capability loads them.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

KNOWLEDGE_TOOLS = {'search_project_knowledge', 'read_project_source', 'project_status'}
BROWSER_TOOLS = {'browser_navigate', 'browser_snapshot', 'browser_click', 'browser_type',
                 'browser_select_option', 'browser_press_key', 'browser_wait_for',
                 'browser_take_screenshot', 'browser_close'}
HOLLYWOOD_TOOLS = {
    'studio_media_capabilities', 'inspect_studio_media', 'submit_studio_media_job',
    'get_studio_media_job', 'cancel_studio_media_job', 'retry_studio_media_job',
    'list_studio_media_projects', 'create_studio_media_project', 'get_studio_media_project',
}
GITHUB_TOOL_NAMES = {
    'github_search', 'github_issue', 'github_pr', 'github_repo', 'github_api',
}
SKILL_TEXT_LIMIT = 12000
GH_BIN = '/opt/homebrew/bin/gh'


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

GITHUB_TOOLS = [
    definition(
        'github_search',
        'Search GitHub issues, PRs, repos, or code via Studio gh. Read-only.',
        {'kind': {'type': 'string', 'enum': ['issues', 'prs', 'repos', 'code']},
         'query': {'type': 'string'},
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 10}},
        ['kind', 'query']),
    definition(
        'github_issue',
        'List or view GitHub issues via Studio gh. Read-only.',
        {'action': {'type': 'string', 'enum': ['list', 'view']},
         'repo': {'type': 'string', 'description': 'owner/name; required for list, optional for view when number is enough with -R'},
         'number': {'type': 'integer', 'minimum': 1},
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 20}},
        ['action']),
    definition(
        'github_pr',
        'List, view, or check GitHub pull requests via Studio gh. Read-only.',
        {'action': {'type': 'string', 'enum': ['list', 'view', 'checks']},
         'repo': {'type': 'string'},
         'number': {'type': 'integer', 'minimum': 1},
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 20}},
        ['action']),
    definition(
        'github_repo',
        'View a GitHub repository summary via Studio gh. Read-only.',
        {'repo': {'type': 'string', 'description': 'owner/name'}},
        ['repo']),
    definition(
        'github_api',
        'Call the GitHub REST API via gh api. GET-only; mutations are rejected.',
        {'path': {'type': 'string', 'description': 'API path such as repos/owner/name'}},
        ['path']),
]


def skill_home(name: str) -> Path:
    home = Path.home()
    mapping = {
        'shine': home / '.agents' / 'skills' / 'shine' / 'SKILL.md',
        'hollywood': home / '.agents' / 'skills' / 'studio-media' / 'SKILL.md',
        'strike-package': home / '.codex' / 'skills' / 'strike-package' / 'SKILL.md',
    }
    return mapping[name]


CAPABILITIES = {
    'github': {
        'kind': 'github',
        'description': 'Read-only GitHub via Studio gh (search, issues, PRs, repos, GET api).',
    },
    'knowledge': {
        'kind': 'mcp',
        'description': 'Private project-knowledge search and source snapshots on Studio.',
    },
    'browser': {
        'kind': 'mcp',
        'description': 'Headless isolated Playwright browser; no user cookies.',
    },
    'shine': {
        'kind': 'skill',
        'description': 'UI/UX design skill (ClearSpeed/Shine standards). Loads SKILL.md on enable.',
        'skill': 'shine',
    },
    'hollywood': {
        'kind': 'skill+mcp',
        'description': 'Hollywood media producer skill; enables Studio media MCP tools when available.',
        'skill': 'hollywood',
    },
    'strike-package': {
        'kind': 'skill',
        'description': 'Evidence-backed professional meeting brief skill. Loads SKILL.md on enable.',
        'skill': 'strike-package',
    },
}


def capability_catalog():
    return {name: {'kind': meta['kind'], 'description': meta['description']}
            for name, meta in CAPABILITIES.items()}


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


def gh_binary():
    if Path(GH_BIN).is_file():
        return GH_BIN
    found = shutil.which('gh')
    if not found:
        raise ValueError('Studio gh is not installed')
    return found


def run_gh(argv, timeout=60):
    """Run allowlisted gh outside the workspace sandbox. Never log secrets."""
    command = [gh_binary(), *argv]
    env = os.environ.copy()
    env['PATH'] = '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    # Prefer machine auth config; do not inject tokens into argv.
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    output = (completed.stdout or '') + (('' if not completed.stderr else '\n' + completed.stderr))
    if len(output) > 32000:
        output = output[:32000] + '\n…truncated'
    if completed.returncode != 0:
        raise ValueError(output.strip() or f'gh exited {completed.returncode}')
    return {'ok': True, 'output': output.strip(), 'argv': argv}


def github_tool(name, args):
    if name == 'github_search':
        kind = args.get('kind')
        query = args.get('query')
        if kind not in {'issues', 'prs', 'repos', 'code'} or not isinstance(query, str) or not query.strip():
            raise ValueError('github_search requires kind and a non-empty query')
        if len(query) > 500:
            raise ValueError('Search query must be at most 500 characters')
        limit = args.get('limit', 10)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('limit must be between 1 and 20')
        return run_gh(['search', kind, query, '--limit', str(limit)])
    if name == 'github_issue':
        action = args.get('action')
        if action not in {'list', 'view'}:
            raise ValueError('github_issue action must be list or view')
        repo = args.get('repo')
        if repo is not None and (not isinstance(repo, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo)):
            raise ValueError('repo must look like owner/name')
        if action == 'list':
            if not repo:
                raise ValueError('github_issue list requires repo')
            limit = args.get('limit', 20)
            if type(limit) is not int or not 1 <= limit <= 50:
                raise ValueError('limit must be between 1 and 50')
            return run_gh(['issue', 'list', '-R', repo, '--limit', str(limit)])
        number = args.get('number')
        if type(number) is not int or number < 1:
            raise ValueError('github_issue view requires number')
        argv = ['issue', 'view', str(number)]
        if repo:
            argv.extend(['-R', repo])
        return run_gh(argv)
    if name == 'github_pr':
        action = args.get('action')
        if action not in {'list', 'view', 'checks'}:
            raise ValueError('github_pr action must be list, view, or checks')
        repo = args.get('repo')
        if repo is not None and (not isinstance(repo, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo)):
            raise ValueError('repo must look like owner/name')
        if action == 'list':
            if not repo:
                raise ValueError('github_pr list requires repo')
            limit = args.get('limit', 20)
            if type(limit) is not int or not 1 <= limit <= 50:
                raise ValueError('limit must be between 1 and 50')
            return run_gh(['pr', 'list', '-R', repo, '--limit', str(limit)])
        number = args.get('number')
        if type(number) is not int or number < 1:
            raise ValueError(f'github_pr {action} requires number')
        argv = ['pr', action, str(number)]
        if repo:
            argv.extend(['-R', repo])
        return run_gh(argv)
    if name == 'github_repo':
        repo = args.get('repo')
        if not isinstance(repo, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
            raise ValueError('repo must look like owner/name')
        return run_gh(['repo', 'view', repo])
    if name == 'github_api':
        path = args.get('path')
        if not isinstance(path, str) or not path.strip():
            raise ValueError('github_api requires a path')
        path = path.strip().lstrip('/')
        if re.search(r'(?i)(^|\s)-(X|-method)\b|method=', path):
            raise ValueError('github_api is GET-only; method overrides are rejected')
        if any(tok in path for tok in (';', '|', '`', '$', '\n', '\r')):
            raise ValueError('github_api path contains forbidden characters')
        # Reject obvious mutation verbs embedded as flags in the path string.
        lowered = path.lower()
        if any(flag in lowered for flag in (' -x ', '--method', ' method=', '\t-x\t')):
            raise ValueError('github_api is GET-only; method overrides are rejected')
        return run_gh(['api', path])
    raise ValueError('Unavailable GitHub tool')


def load_skill_text(skill_name: str):
    path = skill_home(skill_name)
    if not path.is_file():
        raise ValueError(f'Skill is not installed on Studio: {skill_name} ({path})')
    text = path.read_text(encoding='utf-8')
    truncated = len(text) > SKILL_TEXT_LIMIT
    body = text[:SKILL_TEXT_LIMIT]
    references = sorted(p.name for p in path.parent.glob('references/**/*') if p.is_file())[:40]
    return {
        'ok': True,
        'enabled': skill_name,
        'skill': skill_name,
        'path': str(path),
        'text': body,
        'truncated': truncated,
        'references_hint': references or None,
        'instruction': 'Follow this skill for the current request. Do not paste the full skill into the final answer.',
    }


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
        self.enabled = set()
        self.github_enabled = False

    def _server_specs(self):
        browser_output = self.directory / 'browser'
        browser_output.mkdir(exist_ok=True, mode=0o700)
        hollywood_script = Path.home() / '.agents' / 'skills' / 'studio-media' / 'scripts' / 'studio.py'
        return {
            'knowledge': ([
                '/Volumes/SSD-2TB/milvus/sdk/python/.venv/bin/python',
                '/Volumes/SSD-2TB/milvus/pipelines/project-knowledge/project_knowledge.py', 'serve'], KNOWLEDGE_TOOLS),
            'browser': ([
                '/opt/homebrew/bin/node', str(Path.home() / '.local/share/shine/current/node_modules/playwright/cli.js'),
                'mcp', '--headless', '--isolated', '--block-service-workers', '--image-responses', 'omit',
                '--output-dir', str(browser_output), '--timeout-navigation', '15000', '--timeout-action', '5000'], BROWSER_TOOLS),
            'hollywood': ([
                '/usr/bin/python3', str(hollywood_script), 'mcp'], HOLLYWOOD_TOOLS),
        }

    def _write_capabilities(self):
        (self.directory / 'tool-capabilities.json').write_text(json.dumps({
            'tools': [t['function']['name'] for t in self.tools],
            'available': capability_catalog(),
            'enabled': sorted(self.enabled),
            'unavailable': self.unavailable,
            'browser': 'isolated headless; no user profile or cookies',
            'file_write_root': str(self.workspace),
            'inference': 'Studio resident Gemma only',
            'loading': 'on_demand',
        }, indent=2))

    def discover(self):
        """Cold start: file tools only. Optional packs stay enabled via enable()."""
        self._write_capabilities()
        return self.tools

    def _append_mcp_tools(self, server, selected):
        for tool in selected:
            schema = tool['inputSchema']
            description = tool.get('description', '')[:3000]
            if tool['name'] == 'browser_take_screenshot':
                schema = {**schema, 'properties': {**schema.get('properties', {}),
                          'filename': {'type': 'string', 'description': 'Optional output filename, such as proof.png, inside this run\'s browser directory.'}}}
                description += ' Return the exact absolute path from result.artifacts; do not invent a filename.'
            # Avoid duplicate schemas if enable is called twice.
            if any(t['function']['name'] == tool['name'] for t in self.tools):
                self.routes[tool['name']] = server
                continue
            self.routes[tool['name']] = server
            self.tools.append({'type': 'function', 'function': {
                'name': tool['name'], 'description': description,
                'parameters': schema}})

    def _enable_mcp(self, server):
        if server in self.clients:
            self.enabled.add(server if server != 'hollywood' else 'hollywood')
            self._write_capabilities()
            return {'ok': True, 'enabled': server, 'already': True,
                    'tools': [t['function']['name'] for t in self.tools if self.routes.get(t['function']['name']) == server]}
        specs = self._server_specs()
        if server not in specs:
            raise ValueError(f'Unknown MCP pack: {server}')
        command, allowed = specs[server]
        client = None
        try:
            client = StdioMCP(command, self.workspace, self.directory / (server + '-tools.log'), self.cancelled)
            listed = client.request('tools/list', {}, timeout=30)['tools']
            selected = [t for t in listed if t['name'] in allowed]
            missing = allowed - {t['name'] for t in selected}
            if missing:
                raise ValueError('Missing configured tools: ' + ', '.join(sorted(missing)))
            self.clients[server] = client
            self._append_mcp_tools(server, selected)
            self.enabled.add('hollywood' if server == 'hollywood' else server)
            self.unavailable.pop(server, None)
            self._write_capabilities()
            return {'ok': True, 'enabled': 'hollywood' if server == 'hollywood' else server,
                    'tools': [t['name'] for t in selected]}
        except InterruptedError:
            if client:
                client.close()
            raise
        except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
            if client:
                client.close()
            self.unavailable[server] = str(exc)
            self._write_capabilities()
            raise ValueError(f'Capability {server} unavailable: {exc}') from exc

    def _enable_github(self):
        if self.github_enabled:
            self.enabled.add('github')
            self._write_capabilities()
            return {'ok': True, 'enabled': 'github', 'already': True,
                    'tools': sorted(GITHUB_TOOL_NAMES)}
        # Confirm gh exists before advertising tools.
        gh_binary()
        existing = {t['function']['name'] for t in self.tools}
        for tool in GITHUB_TOOLS:
            if tool['function']['name'] not in existing:
                self.tools.append(tool)
        self.github_enabled = True
        self.enabled.add('github')
        self.unavailable.pop('github', None)
        self._write_capabilities()
        return {'ok': True, 'enabled': 'github', 'tools': sorted(GITHUB_TOOL_NAMES)}

    def enable(self, name):
        if name in (None, '', 'list'):
            return {'ok': True, 'available': capability_catalog(), 'enabled': sorted(self.enabled)}
        if not isinstance(name, str) or name not in CAPABILITIES:
            return {'ok': False, 'error': f'Unknown capability: {name}',
                    'available': capability_catalog(), 'enabled': sorted(self.enabled)}
        meta = CAPABILITIES[name]
        kind = meta['kind']
        if kind == 'github':
            try:
                return self._enable_github()
            except ValueError as exc:
                self.unavailable['github'] = str(exc)
                self._write_capabilities()
                return {'ok': False, 'error': str(exc), 'available': capability_catalog(),
                        'enabled': sorted(self.enabled)}
        if kind == 'mcp':
            return self._enable_mcp(name)
        if kind == 'skill':
            result = load_skill_text(meta['skill'])
            self.enabled.add(name)
            self._write_capabilities()
            return result
        if kind == 'skill+mcp':
            result = load_skill_text(meta['skill'])
            self.enabled.add(name)
            media = None
            try:
                media = self._enable_mcp('hollywood')
            except ValueError as exc:
                result['media_tools'] = {'ok': False, 'error': str(exc)}
            else:
                result['media_tools'] = media
            self._write_capabilities()
            return result
        raise ValueError(f'Unsupported capability kind: {kind}')

    def call(self, name, args):
        if name in {'file_list', 'file_read', 'file_write'}:
            return file_tool(name, args, self.workspace)
        if name in GITHUB_TOOL_NAMES:
            if not self.github_enabled:
                raise ValueError('Tool is unavailable in this run: ' + name + '; call enable_capability("github") first')
            return github_tool(name, args)
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
