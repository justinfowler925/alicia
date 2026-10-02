import json
import httpx
import pytest
from alicia import coworker_watch as watch


def install(monkeypatch,handler):
    real=httpx.Client
    monkeypatch.setattr(watch.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(watch.time,'sleep',lambda _:None)


def reply(data,status=200,headers=None):return httpx.Response(status,json=data,headers=headers)
def auth():return reply({'ok':True,'user_id':watch.OWNER,'team_id':'TL8SFF7J8'})
def message(ts,text,user='UOTHER',**kw):return dict(ts=ts,text=text,user=user,**kw)


def test_membership_history_and_reply_pages_preserve_complete_thread(tmp_path,monkeypatch):
    s=watch.CommitmentStore(tmp_path/'s.db');seen=[]
    def request(r):
        method=r.url.path.rsplit('/',1)[1];p=dict(r.url.params);seen.append((method,p))
        if method=='auth.test':return auth()
        if method=='users.conversations':
            return reply({'ok':True,'channels':[{'id':'D1','is_im':True}] if not p['cursor'] else [{'id':'C1'}],
                          'response_metadata':{'next_cursor':'members2' if not p['cursor'] else ''}})
        if method=='conversations.history':
            if p['channel']=='C1':return reply({'ok':True,'messages':[message('3.0','irrelevant chatter')]})
            return reply({'ok':True,'messages':[message('1.0','Justin, send the proposal.',reply_count=2)] if not p['cursor'] else [message('2.0','Thanks')],
                          'response_metadata':{'next_cursor':'history2' if not p['cursor'] else ''}})
        assert method=='conversations.replies'
        return reply({'ok':True,'messages':[message('1.0','Justin, send the proposal.')] if not p['cursor'] else [message('1.1','I sent the proposal.',watch.OWNER)],
                      'response_metadata':{'next_cursor':'replies2' if not p['cursor'] else ''}})
    install(monkeypatch,request);watch.SlackSource('test').poll(s)
    with s.db() as c:rows=[dict(r) for r in c.execute('select * from sources')]
    assert len(rows)==2
    thread=next(r for r in rows if r['id']=='slack:D1:1.0')
    assert 'Justin, send the proposal.' in thread['context'] and 'I sent the proposal.' in thread['context']
    assert s.setting('slack_completed') and not s.setting('slack_scan')
    assert all(m!='search.messages' for m,_ in seen)
    assert any(m=='users.conversations' and p['cursor']=='members2' for m,p in seen)
    assert any(m=='conversations.history' and p['cursor']=='history2' for m,p in seen)


def test_rate_limit_retains_cursor_and_partial_thread_across_restart(tmp_path,monkeypatch):
    s=watch.CommitmentStore(tmp_path/'s.db');limited=[True];calls=[]
    state={'stage':'history','channels':[],'threads':[{'channel':'C1','ts':'1.0','tracked':True,'cursor':'next','messages':[message('1.0','Original request')]}]}
    s.setting('slack_scan',json.dumps(state))
    def request(r):
        calls.append(r.url.path)
        if r.url.path.endswith('auth.test'):return auth()
        assert r.url.params['cursor']=='next'
        if limited[0]:return reply({'ok':False},429,{'Retry-After':'120'})
        return reply({'ok':True,'messages':[message('1.1','Completed',watch.OWNER)]})
    state['latest']='100';s.setting('slack_scan',json.dumps(state))
    install(monkeypatch,request)
    with pytest.raises(RuntimeError,match='retry scheduled'):watch.SlackSource('test').poll(s)
    assert json.loads(s.setting('slack_scan'))['threads'][0]['cursor']=='next'
    n=len(calls);watch.SlackSource('test').poll(s);assert len(calls)==n
    s.setting('slack_retry_at',0);limited[0]=False
    watch.SlackSource('test').poll(s)
    with s.db() as c:context=c.execute('select context from sources').fetchone()[0]
    assert 'Original request' in context and 'Completed' in context


def test_wrong_account_never_reads_messages(tmp_path,monkeypatch):
    s=watch.CommitmentStore(tmp_path/'s.db');calls=[]
    def request(r):
        calls.append(r.url.path);return reply({'ok':True,'user_id':'someone_else','team_id':'TL8SFF7J8'})
    install(monkeypatch,request)
    with pytest.raises(RuntimeError,match='does not match'):watch.SlackSource('test').poll(s)
    assert len(calls)==1 and not s.setting('slack_checked')


def test_channel_thread_involvement_can_be_in_reply(tmp_path,monkeypatch):
    s=watch.CommitmentStore(tmp_path/'s.db')
    def request(r):
        method=r.url.path.rsplit('/',1)[1]
        if method=='auth.test':return auth()
        if method=='users.conversations':return reply({'ok':True,'channels':[{'id':'C1'}]})
        if method=='conversations.history':return reply({'ok':True,'messages':[message('1.0','Project discussion',reply_count=1)]})
        return reply({'ok':True,'messages':[message('1.0','Project discussion'),message('1.1','<@'+watch.OWNER+'> please send the proposal.')]})
    install(monkeypatch,request);watch.SlackSource('test').poll(s)
    with s.db() as c:assert c.execute('select count(*) from sources').fetchone()[0]==1


def test_budget_resumes_without_losing_history_pages(tmp_path,monkeypatch):
    s=watch.CommitmentStore(tmp_path/'s.db');pages=[]
    def request(r):
        method=r.url.path.rsplit('/',1)[1]
        if method=='auth.test':return auth()
        if method=='users.conversations':return reply({'ok':True,'channels':[{'id':'D1','is_im':True}]})
        n=int(r.url.params['cursor'] or 0);pages.append(n)
        return reply({'ok':True,'messages':[message(str(n+1)+'.0','human message')],
                      'response_metadata':{'next_cursor':str(n+1) if n<45 else ''}})
    install(monkeypatch,request);watch.SlackSource('test').poll(s)
    assert s.setting('slack_scan') and not s.setting('slack_completed')
    watch.SlackSource('test').poll(s)
    assert pages==list(range(46)) and s.setting('slack_completed')
    with s.db() as c:assert c.execute('select count(*) from sources').fetchone()[0]==46


def test_deleted_tracked_thread_cannot_stall_other_conversations(tmp_path,monkeypatch):
    s=watch.CommitmentStore(tmp_path/'s.db')
    s.setting('slack_scan',json.dumps({'stage':'history','latest':'100','oldest':'0',
        'channels':[{'id':'D1','direct':True,'cursor':''}],
        'threads':[{'channel':'GONE','ts':'1.0','tracked':True,'cursor':'','messages':[]}]}))
    def request(r):
        if r.url.path.endswith('auth.test'):return auth()
        if r.url.path.endswith('conversations.replies'):return reply({'ok':False,'error':'thread_not_found'})
        return reply({'ok':True,'messages':[message('2.0','Please send the proposal')]})
    install(monkeypatch,request);watch.SlackSource('test').poll(s)
    with s.db() as c:assert c.execute('select count(*) from sources').fetchone()[0]==1
    assert '1 conversations unavailable' in s.setting('slack_coverage')
    assert s.setting('slack_completed')
