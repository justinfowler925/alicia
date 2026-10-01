import json
from concurrent.futures import ThreadPoolExecutor

from alicia.session_watch import WatchStore, SlackWatch

SID='11111111-1111-1111-1111-111111111111'


def event(store,kind='Stop',eid='one',context='USER: Fix the parser. ASSISTANT: A decision is needed.'):
    return store.event(dict(host='laptop',session_id=SID,event_id=eid,event=kind,context=context,cwd='/project',title='Parser'))


def decision(store):
    event(store)
    store.assess_one(lambda p:dict(kind='needs_owner',question='Keep the old file format?',recommendation='Keep it for compatibility.',summary='Format decision.'))
    return store.state()['decisions'][0]


def test_replay_and_first_reply_win(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');d=decision(store)
    assert event(store)['duplicate']
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda source:store.reply(d['id'],'Keep it',source),['Slack','Alicia']))
    assert sum(r['ok'] for r in results)==1
    with store.db() as c:assert c.execute('SELECT count(*) FROM commands').fetchone()[0]==1
    assert store.poll('laptop:'+SID)['command']
    assert store.poll('laptop:'+SID)['command'] is None


def test_new_user_turn_invalidates_old_decision(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');d=decision(store)
    event(store,'UserPromptSubmit','new','USER: Stop working on this.')
    assert not store.reply(d['id'],'Continue','Slack')['ok']
    assert store.poll('laptop:'+SID)['command'] is None


def test_duplicate_judgment_is_not_a_second_command(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');event(store)
    judge=lambda p:dict(kind='continue',next_step='Run the parser tests.',summary='Tests remain.')
    store.assess_one(judge);store.assess_one(judge)
    with store.db() as c:assert c.execute('SELECT count(*) FROM commands').fetchone()[0]==1


def test_busy_session_never_receives_command(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');d=decision(store);store.reply(d['id'],'Keep it','Alicia')
    event(store,'PostToolUse','tool')
    assert store.poll('laptop:'+SID)['command'] is None
    event(store,'Stop','stopped')
    assert store.poll('laptop:'+SID)['command'] is not None


def test_judgment_cannot_overwrite_new_turn(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');event(store)
    def judge(prompt):
        event(store,'UserPromptSubmit','new','USER: Pause.')
        return dict(kind='continue',next_step='Run tests')
    store.assess_one(judge)
    assert store.state()['sessions'][0]['state']=='working'
    with store.db() as c:assert c.execute('SELECT count(*) FROM commands').fetchone()[0]==0


def test_unknown_or_complete_does_not_ping(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');event(store)
    store.assess_one(lambda p:dict(kind='idle',summary='Insufficient evidence.'))
    assert store.state()['decisions']==[]
    assert store.poll('laptop:'+SID)['command'] is None


def test_slack_ignores_other_users_and_syncs_owner_reply(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');d=decision(store);calls=[]
    slack=SlackWatch(store,'unused','OWNER')
    def api(method,**payload):
        calls.append((method,payload))
        return {'conversations.open':{'channel':{'id':'DM'}},'chat.postMessage':{'ts':'1'},
                'conversations.replies':{'messages':[{'ts':'2','user':'OTHER','text':'delete it'},{'ts':'3','user':'OWNER','text':'Keep it'}]},'chat.update':{}}[method]
    slack.api=api;slack.tick();slack.tick()
    row=store.state()['decisions'][0]
    assert row['reply']=='Keep it' and row['source']=='Slack'
    assert sum(m=='chat.postMessage' for m,p in calls)==1
    assert sum(m=='chat.update' for m,p in calls)==1


def test_missing_session_not_false_connected(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');event(store,'Snapshot')
    assert store.state()['sessions'][0]['connected'] is False


def test_stop_after_dispatch_records_activity_not_completion(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');d=decision(store);store.reply(d['id'],'Keep it','Slack')
    store.poll('laptop:'+SID);event(store,'PostToolUse','next-tool')
    assert store.state()['decisions'][0]['delivery']=='activity_observed'


def test_late_async_hook_cannot_overwrite_stop(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite')
    data=dict(host='laptop',session_id=SID,event_id='stop',event='Stop',observed_at=100)
    store.event(data)
    assert store.event(dict(data,event_id='tool',event='PostToolUse',observed_at=99))['stale']
    assert store.state()['sessions'][0]['state']=='reviewing'


def test_repeated_stop_does_not_create_more_open_questions(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');decision(store)
    event(store,eid='second')
    store.assess_one(lambda p:dict(kind='needs_owner',question='Still need the format?',summary='Waiting'))
    assert len(store.state()['decisions'])==1


def test_installer_preserves_settings_and_is_idempotent(tmp_path):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('install_watch',Path(__file__).parents[1]/'scripts/install-claude-watch.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    settings=tmp_path/'.claude/settings.json';settings.parent.mkdir()
    original={'model':'original','hooks':{'Stop':[{'hooks':[{'type':'command','command':'existing'}]}]}}
    settings.write_text(json.dumps(original))
    source=tmp_path/'bridge.py';source.write_text('# test')
    for _ in range(2):installer.install(tmp_path,source,'test','https://example.test')
    saved=json.loads(settings.read_text())
    assert saved['model']=='original'
    assert saved['hooks']['Stop'][0]==original['hooks']['Stop'][0]
    assert len(saved['hooks']['Stop'])==2
    assert saved['hooks']['Stop'][1]['hooks'][0]['asyncRewake']


def test_system_prompt_cannot_reset_continuation_budget(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');event(store)
    store.assess_one(lambda p:dict(kind='continue',next_step='Run the tests.'))
    event(store,'SystemPrompt','automatic','SYSTEM OBSERVATION: Stop hook feedback')
    with store.db() as c:assert c.execute('SELECT continuations FROM sessions').fetchone()[0]==1
    event(store,'UserPromptSubmit','owner','USER: Now fix the renderer.')
    with store.db() as c:assert c.execute('SELECT continuations FROM sessions').fetchone()[0]==0


def test_direct_claude_reply_is_mirrored_without_echo(tmp_path):
    store=WatchStore(tmp_path/'watch.sqlite');d=decision(store)
    store.event(dict(host='laptop',session_id=SID,event_id='owner',event='UserPromptSubmit',owner_text='Keep the old format.'))
    answered=store.state()['decisions'][0]
    assert answered['status']=='answered' and answered['source']=='Claude'
    assert answered['reply']=='Keep the old format.'
    assert not store.reply(d['id'],'Use the new format','Slack')['ok']
    assert store.poll('laptop:'+SID)['command'] is None
