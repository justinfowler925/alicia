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
