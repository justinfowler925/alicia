import datetime as dt
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('recap',Path(__file__).parents[1]/'scripts/weekly_work_recap.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

def test_week_is_complete_central_days_and_dst_aware():
    start,end=r.window(dt.datetime(2026,11,7,15,tzinfo=dt.UTC))
    assert start.isoformat()=='2026-10-31T00:00:00-05:00'
    assert end.isoformat()=='2026-11-07T00:00:00-06:00'
    assert (end.astimezone(dt.UTC)-start.astimezone(dt.UTC)).total_seconds()==169*3600
    assert not r.in_window(end,start,end)
    assert r.in_window(start,start,end)

def test_merged_is_not_deployed_and_prior_merge_is_not_this_week():
    start,end=r.window(dt.datetime(2026,9,19,14,tzinfo=dt.UTC))
    p={'title':'Fix avatar','html_url':'https://github.com/u/r/pull/1','number':1,'state':'closed','created_at':'2026-09-01T00:00:00Z','updated_at':'2026-09-18T00:00:00Z','pull_request':{'merged_at':'2026-09-17T00:00:00Z'}}
    assert r.pr_record(p,start,end)['status']=='Merged this week'
    p['pull_request']['merged_at']='2026-09-01T00:00:00Z'
    assert r.pr_record(p,start,end)['status']=='Closed / earlier merge'

def test_all_sources_unavailable_still_generates_explicit_gaps(monkeypatch,tmp_path):
    def fail(*args,**kwargs):raise RuntimeError('offline')
    monkeypatch.setattr(r,'api',fail)
    monkeypatch.setattr(r,'studio_artifacts',lambda *args:([],False))
    report=r.collect(dt.datetime(2026,9,19,14,tzinfo=dt.UTC))
    assert len(report['gaps'])==6
    assert report['coverage']==[]
    r.write_report(report,tmp_path)
    assert 'offline' in (tmp_path/'index.html').read_text()
    assert (tmp_path/'2026-09-19.json').exists()

def test_output_escapes_untrusted_titles_and_unsafe_links():
    report={'projects':[{'name':'u/<script>','url':'javascript:alert(1)','prs':[{'title':'<img onerror=alert(1)>','url':'https://github.com/u/r/pull/1','status':'Open','category':'Fixes'}],'commits':[],'deployments':[],'releases':[],'new':False}], 'window':{'start':'2026-09-12T00:00:00-05:00','end_exclusive':'2026-09-19T00:00:00-05:00'},'generated_at':'2026-09-19','coverage':[],'gaps':[],'limits':[]}
    page=r.render(report)
    assert '<img onerror' not in page
    assert 'href="javascript:' not in page
    assert '&lt;img onerror' in page
