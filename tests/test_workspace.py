import json
from alicia.workspace import scout_projection, read_json


def test_missing_scout_is_unknown(tmp_path):
    result = scout_projection(tmp_path, {'connection': 'unavailable'})
    assert result['installed'] is False
    assert result['jobs'] == []
    assert result['history'] == []


def test_receipts_bounded_and_failures_preserved(tmp_path):
    (tmp_path/'status.json').write_text(json.dumps({'version':'3.3','mode':'production','states':['NV']}))
    for i in range(25):
        folder=tmp_path/'feeds/notices/runs'/f'2026-09-{i:02d}'
        folder.mkdir(parents=True)
        (folder/'receipt.json').write_text(json.dumps({'exit':1,'error':'Authorization: Bearer secret-value'}))
    result=scout_projection(tmp_path, {'connection':'live','jobs':[{'id':'scout-feeds','health':'failed'},{'id':'other'}]})
    assert result['summary']['jobs']==1
    assert result['summary']['failed']==1
    assert len(result['history'])==20
    assert result['history'][0]['status']=='failed'
    assert 'secret-value' not in result['history'][0]['error']


def test_invalid_json_shapes(tmp_path):
    path=tmp_path/'status.json'
    path.write_text('[]')
    assert read_json(path)=={}
    assert scout_projection(tmp_path,{})['installed'] is False


def test_forge_trusted_owner_preserves_origin_boundary():
    import pytest
    from fastapi import HTTPException
    from starlette.requests import Request
    from alicia.forge_chat import require_local_chat
    def request(owner, origin='https://studio.example'):
        return Request({'type':'http','method':'POST','scheme':'https','path':'/',
                        'server':('studio.example',443),'client':('127.0.0.1',1234),
                        'headers':[(b'host',b'studio.example'),(b'origin',origin.encode()),(b'x-alicia-chat',b'forge')],
                        'state':{'studio_owner':owner}})
    require_local_chat(request(True))
    with pytest.raises(HTTPException): require_local_chat(request(False))
    with pytest.raises(HTTPException): require_local_chat(request(True,'https://foreign.example'))
