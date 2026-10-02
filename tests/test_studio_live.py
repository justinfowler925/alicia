from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from alicia import brain, live_voice
from alicia.config import AliciaCfg
from alicia.studio_access import install


def test_private_studio_identity_and_origin(monkeypatch):
    monkeypatch.setenv('ALICIA_PUBLIC_ORIGIN','https://studio.example:8768')
    monkeypatch.setenv('ALICIA_TAILSCALE_OWNER','owner@example.com')
    app=FastAPI()
    install(app)
    @app.post('/write')
    def write():return {'ok':True}
    client=TestClient(app,client=('127.0.0.1',4567),base_url='https://studio.example:8768')
    assert client.post('/write').status_code==403
    headers={'tailscale-user-login':'owner@example.com','origin':'https://studio.example:8768'}
    assert client.post('/write',headers=headers).status_code==200
    assert client.post('/write',headers={**headers,'origin':'https://evil.example'}).status_code==403
    assert client.post('/write',headers={**headers,'tailscale-user-login':'other@example.com'}).status_code==403


def test_cursor_selected_never_calls_claude(monkeypatch):
    from alicia import cursor_cli
    monkeypatch.setenv('ALICIA_CONVERSATION_PROVIDER','cursor')
    monkeypatch.setattr('alicia.claude.ask_claude',lambda *a,**k:pytest.fail('Claude called'))
    seen=[]
    monkeypatch.setattr(cursor_cli,'complete',lambda prompt:seen.append(prompt) or 'TOOL: capture_note\nARGS: {"text":"test"}')
    result=brain._create_cli(AliciaCfg(),messages=[{'role':'user','content':'Remember this'}],cli_system='system',tools=[{'name':'capture_note','input_schema':{'type':'object'}}])
    assert result.content[0].name=='capture_note'
    assert 'input_schema' in seen[0]


def test_delegation_does_not_run_twice(monkeypatch,tmp_path):
    monkeypatch.setenv('ALICIA_STATE_DIR',str(tmp_path))
    app=FastAPI();app.include_router(live_voice.router)
    app.state.sessions=SimpleNamespace(get_session=lambda _:True)
    app.state.live_voice_jobs=set()
    handle=Mock(return_value=SimpleNamespace(reply='Saved.'))
    app.state.conversation=SimpleNamespace(handle=handle)
    with TestClient(app) as client:
        body={'id':'delegation1','message':'Remember the test'}
        assert client.post('/api/session/session1/live-delegation',json=body).json()['reply']=='Saved.'
        assert client.post('/api/session/session1/live-delegation',json=body).json()['replayed'] is True
        assert client.post('/api/session/session1/live-delegation',json={**body,'message':'different'}).status_code==409
    handle.assert_called_once()
    assert handle.call_args.kwargs['owner_verified'] is False


def test_live_transcript_persists_without_duplicate_or_action(tmp_path):
    from alicia.session import SessionStore
    store=SessionStore(tmp_path/'sessions.sqlite')
    sid=store.open_session()
    first=store.append_live_turn(sid,'user','My project is called Orchard.','event1')
    second=store.append_live_turn(sid,'user','My project is called Orchard.','event1')
    assert first.id==second.id
    assert len(store.transcript(sid))==1
    assert store.history_for_model(sid)[0]['content']=='My project is called Orchard.'


def test_studio_profiles_do_not_select_anthropic(monkeypatch):
    from alicia.model_gateway import default_profile
    monkeypatch.setenv('ALICIA_CONVERSATION_PROVIDER','cursor')
    for name in ('conversation','supervisor','frontier','builder'):
        assert {x.provider for x in default_profile(name,AliciaCfg()).candidates}=={'cursor'}


def test_remote_cli_quotes_arguments_and_does_not_run_second_server(monkeypatch,tmp_path):
    import json
    from alicia import remote_cli
    monkeypatch.setattr(remote_cli.Path,'home',lambda:tmp_path)
    monkeypatch.delenv('ALICIA_STATE_DIR',raising=False)
    monkeypatch.delenv('ALICIA_LOCAL_CLI',raising=False)
    (tmp_path/'.alicia').mkdir()
    (tmp_path/'.alicia/remote.json').write_text(json.dumps({'host':'jfstudio@100.102.92.119','url':'https://studio/'}))
    called=Mock(return_value=0)
    monkeypatch.setattr(remote_cli.subprocess,'call',called)
    assert remote_cli.forward(['chat','hello; touch /tmp/no'])==0
    assert "'hello; touch /tmp/no'" in called.call_args.args[0][-1]
    with pytest.raises(RuntimeError):remote_cli.forward(['serve'])


@pytest.mark.parametrize("selection", [None, "auto", "composer-2.5"])
def test_cursor_auto_reaches_cli(monkeypatch, tmp_path, selection):
    from alicia import cursor_cli
    monkeypatch.setenv("ALICIA_REASONING_ROOT", str(tmp_path))
    monkeypatch.delenv("ALICIA_CURSOR_MODEL", raising=False)
    if selection:
        monkeypatch.setenv("ALICIA_CURSOR_MODEL", selection)
    process = Mock(returncode=0)
    process.communicate.return_value = ('{"result":"pong","is_error":false}', '')
    launch = Mock(return_value=process)
    monkeypatch.setattr(cursor_cli.subprocess, "Popen", launch)
    assert cursor_cli.complete("Reply with exactly: pong") == "pong"
    args = launch.call_args.args[0]
    assert args[args.index("--model") + 1] == (selection or "auto")


def test_studio_profiles_default_to_cursor_auto(monkeypatch):
    from alicia.model_gateway import default_profile
    monkeypatch.setenv("ALICIA_CONVERSATION_PROVIDER", "cursor")
    monkeypatch.delenv("ALICIA_CURSOR_MODEL", raising=False)
    for name in ("conversation", "supervisor", "frontier", "builder"):
        profile = default_profile(name, AliciaCfg())
        assert {(c.provider, c.model) for c in profile.candidates} == {("cursor", "auto")}


def test_reviewer_model_override_reaches_cli_without_changing_conversation(monkeypatch,tmp_path):
    from alicia import cursor_cli
    monkeypatch.setenv('ALICIA_REASONING_ROOT',str(tmp_path))
    monkeypatch.setenv('ALICIA_CURSOR_MODEL','auto')
    process=Mock(returncode=0)
    process.communicate.return_value=('{"result":"ok","is_error":false}','')
    launch=Mock(return_value=process)
    monkeypatch.setattr(cursor_cli.subprocess,'Popen',launch)
    assert cursor_cli.complete('Review',model='composer-2.5')=='ok'
    args=launch.call_args.args[0]
    assert args[args.index('--model')+1]=='composer-2.5'
    cursor_cli.complete('Conversation')
    args=launch.call_args.args[0]
    assert args[args.index('--model')+1]=='auto'


def test_delegation_reuses_saved_voice_turn_and_rejects_mismatches(monkeypatch, tmp_path):
    from alicia.session import SessionStore
    monkeypatch.setenv('ALICIA_STATE_DIR', str(tmp_path))
    store = SessionStore(tmp_path / 'sessions.sqlite')
    sid = store.open_session()
    first = store.append_live_turn(sid, 'user', 'Check ', 'part-1')
    last = store.append_live_turn(sid, 'user', 'again.', 'part-2')
    assert store.match_live_request(sid, 'Check again.', ['part-1', 'part-2']).id == last.id
    with pytest.raises(ValueError):
        store.match_live_request(sid, 'Old question Check again.', ['part-1', 'part-2'])
    with pytest.raises(ValueError):
        store.match_live_request(store.open_session(), 'Check ', ['part-1'])
    app = FastAPI(); app.include_router(live_voice.router)
    app.state.sessions = store
    app.state.live_voice_jobs = set()
    handle = Mock(return_value=SimpleNamespace(reply='Done.'))
    app.state.conversation = SimpleNamespace(handle=handle)
    with TestClient(app) as client:
        body = {'id': 'new-work', 'message': 'Wrong', 'transcript_ids': ['part-1', 'part-2']}
        assert client.post(f'/api/session/{sid}/live-delegation', json=body).status_code == 409
        body['message'] = 'Check again.'
        assert client.post(f'/api/session/{sid}/live-delegation', json=body).status_code == 200
    assert handle.call_args.kwargs['live_turn_ids'] == ['part-1', 'part-2']
    assert [t.id for t in store.transcript(sid)] == [first.id, last.id]


@pytest.mark.parametrize("wait", [True, False])
def test_real_conversation_reuses_live_turn_and_settles_worker_failure(monkeypatch, tmp_path, wait):
    from alicia.session import SessionStore
    from alicia.conversation import ConversationManager
    monkeypatch.setenv('ALICIA_STATE_DIR', str(tmp_path))
    store = SessionStore(tmp_path/'sessions.sqlite')
    sid = store.open_session()
    original = store.append_live_turn(sid, 'user', 'Check the current work.', 'spoken-1')
    events = []
    manager = ConversationManager(Mock(), AliciaCfg(), store, on_event=lambda k,p:events.append((k,p)), memory=Mock(), todos=Mock())
    monkeypatch.setattr(manager, '_pending_artifact', lambda _:None)
    monkeypatch.setattr(manager, '_run_brain', Mock(side_effect=RuntimeError('synthetic worker death')))
    result = manager.handle(sid, 'Check the current work.', channel='voice', wait=wait, owner_verified=False, live_turn_ids=['spoken-1'])
    manager.wait_for_brain(sid)
    turns = store.transcript(sid)
    assert [t.role for t in turns] == ['user', 'alicia']
    assert turns[0].id == original.id
    if wait:
        assert result.error == 'worker_failed'
    answers = [p for k,p in events if k == 'answer']
    assert len(answers) == 1
    assert answers[0]['answers_turn'] == original.id
    assert answers[0]['turn']['meta']['error'] == 'worker_failed'


def test_browser_voice_turn_contract():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    subprocess.run(['node', 'tests/live_voice_turn_contracts.cjs'], cwd=root, check=True, timeout=15)


def test_superseded_request_is_explicit_not_reported_as_cancelled(monkeypatch, tmp_path):
    import threading
    from alicia.session import SessionStore
    from alicia.conversation import ConversationManager
    monkeypatch.setenv('ALICIA_STATE_DIR', str(tmp_path))
    store = SessionStore(tmp_path/'sessions.sqlite'); sid = store.open_session()
    events = []; release = threading.Event(); entered = threading.Event()
    manager = ConversationManager(Mock(), AliciaCfg(), store, on_event=lambda k,p:events.append((k,p)), memory=Mock(), todos=Mock())
    monkeypatch.setattr(manager, '_pending_artifact', lambda _:None)
    def run(session_id, message, turn_id, channel):
        if message == 'first request':
            entered.set(); assert release.wait(5)
        return message + ' answer', {}
    monkeypatch.setattr(manager, '_run_brain', run)
    manager.handle(sid, 'first request')
    assert entered.wait(5)
    first_thread = manager._brain_threads[sid]
    try:
        manager.handle(sid, 'second request')
        manager.wait_for_brain(sid)
    finally:
        release.set(); first_thread.join(5)
    superseded = [p for k,p in events if k == 'superseded']
    assert len(superseded) == 1
    assert superseded[0]['turn_id'] != superseded[0]['replacement_turn_id']
    assert [t.text for t in store.transcript(sid) if t.role == 'alicia'] == ['second request answer']
