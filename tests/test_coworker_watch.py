import base64
from alicia.coworker_watch import CommitmentStore,message_text

def source(s,text='Justin, please send the proposal by Friday.'):
 s.ingest('gmail','thread','https://mail.google.com/test',text)

def assess(s,**kw):
 s.assess(lambda p:dict(actionable=True,confidence=.99,title='Send the proposal',requester='Coworker',due='',evidence='Justin, please send the proposal by Friday.',**kw))

def test_dedup_and_owner_disposition_survive_rescan(tmp_path):
 s=CommitmentStore(tmp_path/'c.db');source(s);assess(s);item=s.snapshot()['items'][0]
 s.update(item['id'],'done','Delivered','Alicia',item['version'])
 source(s,'Justin, please send the proposal by Friday.\nThanks!');assess(s)
 assert len(s.snapshot()['items'])==1 and s.snapshot()['items'][0]['status']=='done'

def test_quote_required_and_fyi_not_added(tmp_path):
 s=CommitmentStore(tmp_path/'c.db');source(s,'FYI team news')
 assess(s)
 assert not s.snapshot()['items'] and s.snapshot()['pending_review']==0

def test_completion_needs_review_not_silent_closure(tmp_path):
 s=CommitmentStore(tmp_path/'c.db');source(s);assess(s)
 source(s,'Justin, please send the proposal by Friday.\nI sent the proposal.')
 with s.db() as c:c.execute('UPDATE sources SET retry_after=0')
 assess(s,completion_evidence='I sent the proposal.')
 assert s.snapshot()['items'][0]['status']=='check_completion'

def test_stale_page_cannot_overwrite_slack_status(tmp_path):
 s=CommitmentStore(tmp_path/'c.db');source(s);assess(s);item=s.snapshot()['items'][0]
 assert s.update(item['id'],'waiting','','Slack',item['version'])['ok']
 assert not s.update(item['id'],'done','','Alicia',item['version'])['ok']
 assert s.snapshot()['items'][0]['status']=='waiting'

def test_read_failure_not_false_healthy(tmp_path):
 s=CommitmentStore(tmp_path/'c.db');s.setting('slack_error','Needs reconnect')
 assert s.snapshot()['sources']['slack']['error']=='Needs reconnect'
 assert s.snapshot()['sources']['gmail']['last_checked']==''

def test_plain_body_and_quoted_email_not_new_request():
 body='Latest answer\nOn Tuesday coworker wrote:\nOld request'
 p={'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(body.encode()).decode()}}
 assert message_text(p)=='Latest answer'

def test_low_confidence_obligation_not_added(tmp_path):
 s=CommitmentStore(tmp_path/'c.db');source(s)
 s.assess(lambda p:dict(actionable=True,confidence=.5,title='Send',evidence='Justin, please send the proposal by Friday.'))
 assert not s.snapshot()['items']

def test_assessment_race_preserves_updated_source(tmp_path):
 s=CommitmentStore(tmp_path/'c.db');source(s)
 def judge(p):
  source(s,'Please disregard the old request.')
  return dict(actionable=True,confidence=1,title='Send',evidence='Justin, please send the proposal by Friday.')
 s.assess(judge)
 assert not s.snapshot()['items'] and s.snapshot()['pending_review']==1
