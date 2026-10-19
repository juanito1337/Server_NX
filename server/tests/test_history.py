"""Metadata-only revision reads on isolated SQLite fixtures, never CAD qualification."""
import copy
import json
import sqlite3
import uuid

import pytest

from nxserver import history
from nxserver.history import compare,sha,summary
from nxserver.libraries import SPECS
from nxserver.safety import DomainError
from nxserver.store import Store
from test_management import pilot,HEADERS,ERP_HEADERS


BODY={'kind':'gauge','name':'First','cam':{},'erp':None,'asset_ids':[],
      'cam_status':'draft','geometry':{'mode':'none','status':'needs_preparation'}}


def pair(store,left=None,right=None):
    first=store.save_resource(copy.deepcopy(BODY),'fixture')
    second=store.save_resource({**copy.deepcopy(BODY),'name':'Second'},'fixture',first['id'],1)
    # Historical nonstandard data must remain inspectable, not requalified.
    # Intentional fixture injection is not an audit-valid production write.
    if left is not None or right is not None:
        with store.db() as db:
            if left is not None:db.execute('UPDATE resource_versions SET payload=? WHERE id=? AND revision=1',(json.dumps(left,ensure_ascii=False),first['id']))
            if right is not None:
                raw=json.dumps(right,ensure_ascii=False)
                db.execute('UPDATE resource_versions SET payload=? WHERE id=? AND revision=2',(raw,first['id']))
                db.execute('UPDATE resources SET payload=? WHERE id=?',(raw,first['id']))
    return first['id']


def diff(store,identifier,**kwargs):
    versions=store.history(identifier)
    return compare(store,identifier,1,2,sha(versions[0]['payload']),sha(versions[1]['payload']),2,**kwargs)


def rows(store):
    with store.db() as db:
        tables=sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        return {name:[dict(row) for row in db.execute('SELECT * FROM '+name)] for name in tables}


def test_summary_pages_are_descending_without_payloads_and_all_rows_unchanged(tmp_path):
    store=Store(tmp_path/'managed');value=store.save_resource(copy.deepcopy(BODY),'fixture')
    for revision in range(1,25):value=store.save_resource({**BODY,'name':str(revision)},'fixture',value['id'],revision)
    before=rows(store);first=summary(store,value['id']);last=summary(store,value['id'],20,20,25)
    assert [item['revision'] for item in first['items']]==list(range(25,5,-1))
    assert [item['revision'] for item in last['items']]==list(range(5,0,-1))
    assert first['total']==25 and last['offset']==20
    for item in first['items']:assert set(item)=={'revision','actor','created_at','payload_bytes','payload_sha256'}
    assert summary(store,value['id'],100)['items']==[]
    assert compare(store,value['id'],25,25,first['items'][0]['payload_sha256'],first['items'][0]['payload_sha256'],25)['total']==0
    assert rows(store)==before


@pytest.mark.parametrize('field,area',[('name','identity'),('kind','identity'),('cam','cam'),('erp','erp'),('cam_status','approval'),('geometry','geometry'),('asset_ids','attachments'),('file_bindings','attachments'),('model_package','model'),('file_package','package'),('origin','origin'),('future_column','other')])
def test_all_metadata_areas_are_read_only(tmp_path,field,area,monkeypatch):
    store=Store(tmp_path/'managed');identifier=pair(store,{field:'old'},{field:'new'})
    before=rows(store)
    monkeypatch.setattr(store,'asset',lambda *args,**kwargs:pytest.fail('History must not dereference CAD/attachment paths.'))
    result=diff(store,identifier,area=area)
    assert result['total']==result['filtered']==1 and result['items'][0]['area']==area and result['counts'][area]==1
    assert result['audit_verified'] is result['files_verified'] is result['automatic_restore'] is result['nx_files_written'] is False
    assert result['complete'] is result['arrays_positional'] is result['sqlite_read_transaction'] is True
    assert rows(store)==before


def test_exact_types_missing_null_json_pointer_and_positional_arrays(tmp_path):
    store=Store(tmp_path/'managed')
    left={'cam':{'boolean':True,'number':1,'deleted':None,'a/b~c':'old','array':['a','b'],'empty':{}},'origin':{'path':r'W:\SiemensShare\nx2406'}}
    right={'cam':{'boolean':1,'number':1.0,'added':None,'a/b~c':'new','array':['b','a','c'],'empty':[]},'origin':{'path':r'\\synthetic\never-read'}}
    identifier=pair(store,left,right);items={item['path']:item for item in diff(store,identifier)['items']}
    assert items['/cam/boolean']['before']['type']=='boolean' and items['/cam/boolean']['after']['type']=='number'
    assert items['/cam/number']['change']=='changed'
    assert items['/cam/added']['before']['present'] is False and items['/cam/added']['after']['type']=='null'
    assert items['/cam/deleted']['after']['present'] is False
    assert items['/cam/a~1b~0c']['tokens']==['cam','a/b~c']
    assert [items['/cam/array/'+str(index)]['change'] for index in range(3)]==['changed','changed','added']
    assert items['/cam/empty']['before']['type']=='object' and items['/cam/empty']['after']['type']=='array'
    assert '/origin/path' in items


def test_full_value_search_bounded_previews_and_complete_pagination(tmp_path):
    store=Store(tmp_path/'managed');left={'cam':{f'field{index:03}':'old' for index in range(123)}}
    right={'cam':{name:'new' for name in left['cam']}};right['cam']['field000']='🙂'*1400+' UNIQUE_TAIL'
    identifier=pair(store,left,right);first=diff(store,identifier,limit=50)
    assert first['total']==first['filtered']==123 and first['items'][0]['after']['truncated'] is True
    assert len(first['items'][0]['after']['text'])==1200 and first['items'][0]['after']['sha256']==sha(json.dumps(right['cam']['field000'],ensure_ascii=False,separators=(',',':')))
    pages=[first,diff(store,identifier,offset=50,expected_comparison=first['comparison_sha256']),diff(store,identifier,offset=100,expected_comparison=first['comparison_sha256'])]
    assert len({item['path'] for page in pages for item in page['items']})==123
    found=diff(store,identifier,query='unique_tail');assert found['filtered']==1 and found['total']==123
    assert diff(store,identifier,query='/CAM/FIELD001')['filtered']==1
    assert diff(store,identifier,area='erp')['filtered']==0


@pytest.mark.parametrize('identifier',list(SPECS))
def test_historical_native_library_metadata_is_not_reinterpreted_or_qualified(tmp_path,identifier):
    store=Store(tmp_path/'managed')
    resource_id=pair(store,{'cam':{'library_id':identifier,'cam_table':{'future':1}}},{'cam':{'library_id':identifier,'cam_table':{'future':2}}})
    result=diff(store,resource_id)
    assert result['items'][0]['path']=='/cam/cam_table/future' and not result['audit_verified']


@pytest.mark.parametrize('case',['head','gap','head_payload','head_time','selected_payload','binding'])
def test_history_drift_is_rejected_without_writes(tmp_path,case):
    store=Store(tmp_path/'managed');identifier=pair(store);old=summary(store,identifier);versions=store.history(identifier)
    params=[1,2,sha(versions[0]['payload']),sha(versions[1]['payload']),2]
    if case=='head':store.save_resource({**BODY,'name':'Third'},'fixture',identifier,2)
    elif case=='binding':pass
    else:
        with store.db() as db:
            if case=='gap':db.execute('DELETE FROM resource_versions WHERE id=? AND revision=1',(identifier,))
            elif case=='head_payload':db.execute("UPDATE resources SET payload='{}' WHERE id=?",(identifier,))
            elif case=='head_time':db.execute("UPDATE resources SET updated_at='tampered' WHERE id=?",(identifier,))
            else:db.execute("UPDATE resource_versions SET payload='{}' WHERE id=? AND revision=1",(identifier,))
    before=rows(store)
    with pytest.raises(DomainError) as failure:compare(store,identifier,*params,expected_comparison='0'*64 if case=='binding' else None)
    assert failure.value.status==409 and rows(store)==before
    if case not in ('selected_payload','binding'):
        with pytest.raises(DomainError):summary(store,identifier,expected=old['current_revision'])


@pytest.mark.parametrize('raw',['{','[]','{"cam":NaN}','{"cam":Infinity}','{"cam":1e999}','{"cam":1,"cam":2}'])
def test_selected_invalid_json_is_not_reported_as_complete(tmp_path,raw):
    store=Store(tmp_path/'managed');identifier=pair(store)
    with store.db() as db:db.execute('UPDATE resource_versions SET payload=? WHERE id=? AND revision=1',(raw,identifier))
    before=rows(store)
    with pytest.raises(DomainError):diff(store,identifier)
    assert rows(store)==before and summary(store,identifier)['items'][1]['payload_sha256']==sha(raw)


@pytest.mark.parametrize('shape',['same','added','removed','changed'])
def test_depth_limit_includes_unchanged_and_whole_added_composites(tmp_path,shape):
    value='leaf'
    for _ in range(70):value={'child':value}
    store=Store(tmp_path/'managed');identifier=pair(store,{'cam':value} if shape!='added' else {},{'cam':value} if shape!='removed' else {})
    if shape=='changed':
        with store.db() as db:
            raw=json.dumps({'cam':{'different':value}});db.execute('UPDATE resource_versions SET payload=? WHERE id=? AND revision=2',(raw,identifier));db.execute('UPDATE resources SET payload=? WHERE id=?',(raw,identifier))
    with pytest.raises(DomainError,match='bounded depth'):diff(store,identifier)


def test_payload_and_node_limits_and_unused_historical_payload(tmp_path,monkeypatch):
    store=Store(tmp_path/'managed');identifier=pair(store,{'cam':list(range(20))},{'cam':list(range(20))})
    with monkeypatch.context() as patch:
        patch.setattr(history,'MAX_NODES',10)
        with pytest.raises(DomainError,match='bounded depth'):diff(store,identifier)
    with monkeypatch.context() as patch:
        patch.setattr(history,'MAX_PAYLOAD',10)
        with pytest.raises(DomainError,match='bounded history'):summary(store,identifier)
    with store.db() as db:db.execute("UPDATE resource_versions SET payload='invalid archived JSON' WHERE id=? AND revision=1",(identifier,))
    latest=summary(store,identifier)['items'][0]
    assert compare(store,identifier,2,2,latest['payload_sha256'],latest['payload_sha256'],2)['total']==0


def test_read_transaction_is_consistent_during_another_store_commit(tmp_path,monkeypatch):
    store=Store(tmp_path/'managed');identifier=pair(store);other=Store(store.root);versions=store.history(identifier)
    original=history._version;written=False
    def concurrent(db,target,revision):
        nonlocal written
        if not written:
            written=True;other.save_resource({**BODY,'name':'Concurrent third'},'fixture',identifier,2)
        return original(db,target,revision)
    monkeypatch.setattr(history,'_version',concurrent)
    value=compare(store,identifier,1,2,sha(versions[0]['payload']),sha(versions[1]['payload']),2)
    assert value['current_revision']==2 and value['items'][0]['path']=='/name'
    assert other.resources()[0]['revision']==3
    with pytest.raises(DomainError,match='head changed'):summary(store,identifier,expected=2)


@pytest.mark.parametrize('args',[(0,0),(-1,20),(0,51),(True,20),(0,True),(2**63,20)])
def test_invalid_direct_summary_pages(tmp_path,args):
    store=Store(tmp_path/'managed');identifier=pair(store)
    with pytest.raises(DomainError):summary(store,identifier,*args)


def test_http_scope_validation_static_script_and_legacy_history(pilot):
    client,store,*_=pilot;identifier=pair(store);base='/api/v1/resources/'+identifier
    result=client.get(base+'/history-summary',headers=HEADERS);assert result.status_code==200
    revisions=result.json()['items'];query={'before':1,'after':2,'before_sha256':revisions[1]['payload_sha256'],'after_sha256':revisions[0]['payload_sha256'],'expected_revision':2}
    assert client.get(base+'/history-diff',headers=HEADERS,params=query).status_code==200
    for suffix in ('history-summary','history-diff'):
        assert client.get(base+'/'+suffix,params=query).status_code==401
        assert client.get(base+'/'+suffix,headers=ERP_HEADERS,params=query).status_code==403
    for change in ({'limit':101},{'offset':-1},{'before':0},{'before':2**63},{'after':2**63},{'offset':2**63},{'expected_revision':2**63},{'after_sha256':'wrong'},{'query':'x'*201}):
        assert client.get(base+'/history-diff',headers=HEADERS,params={**query,**change}).status_code==422
    assert client.get(base+'/history-diff',headers=HEADERS,params={**query,'area':'unknown'}).status_code==400
    assert client.get('/api/v1/resources/not-uuid/history-summary',headers=HEADERS).status_code==400
    assert client.get('/api/v1/resources/'+str(uuid.uuid4())+'/history-summary',headers=HEADERS).status_code==404
    assert client.get(base+'/history-diff',headers=HEADERS,params={**query,'after':3}).status_code==404
    assert client.get(base+'/history',headers=HEADERS).json()==store.history(identifier)
    script=client.get('/history.js');assert script.status_code==200 and script.headers['cache-control']=='no-store'


def test_history_uses_readonly_sqlite_and_does_not_change_journal_mode(tmp_path,monkeypatch):
    store=Store(tmp_path/'managed');identifier=pair(store);versions=store.history(identifier)
    with sqlite3.connect(store.root/'management.sqlite3') as db:
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0]=='delete'
    monkeypatch.setattr(store,'db',lambda:pytest.fail('History must not open the writable Store connection.'))
    original=history._version
    def check(db,target,revision):
        assert db.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):db.execute("UPDATE resources SET payload='{}'")
        return original(db,target,revision)
    monkeypatch.setattr(history,'_version',check)
    assert summary(store,identifier)['sqlite_read_only'] is True
    assert compare(store,identifier,1,2,sha(versions[0]['payload']),sha(versions[1]['payload']))['sqlite_read_only'] is True
    with sqlite3.connect(store.root/'management.sqlite3') as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'


@pytest.mark.parametrize('change',[{'before':2**63},{'after':2**63},{'expected':2**63}])
def test_direct_diff_does_not_overflow_sqlite_integer_bindings(tmp_path,change):
    store=Store(tmp_path/'managed');identifier=pair(store);versions=store.history(identifier)
    args={'before':1,'after':2,'before_sha':sha(versions[0]['payload']),'after_sha':sha(versions[1]['payload']),**change}
    with pytest.raises(DomainError):compare(store,identifier,**args)
