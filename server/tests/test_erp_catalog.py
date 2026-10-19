"""Current ERP identity diagnostics, never a Business Central live integration."""
import copy
import json
import sqlite3
import uuid

import pytest

from nxserver import erp_catalog
from nxserver.erp_catalog import preview,lookup,STATES
from nxserver.safety import DomainError
from nxserver.store import Store,canonical
from test_management import pilot,HEADERS,ERP_HEADERS
from test_history import rows

BODY={'kind':'gauge','name':'Synthetic CAM fixture','cam':{},'erp':None,'asset_ids':[],'cam_status':'draft'}
ERP={'kind':'gauge','company_id':'company','system_id':'system','number':'ITEM1','description':'Synthetic ERP fixture','unit':'PCS','source_revision':'1'}


def resource(store,**values):return store.save_resource({**copy.deepcopy(BODY),**values},'fixture')
def erp(store,**values):return store.erp_upsert({**ERP,**values},'erp')
def body(value):return {name:copy.deepcopy(data) for name,data in value.items() if name not in ('id','revision','updated_at')}


def alter(store,value,mutate,index=None,head=True):
    payload=body(value);mutate(payload);raw=canonical(payload)
    with store.db() as db:
        db.execute('UPDATE resources SET payload=? WHERE id=?',(raw,value['id']))
        if index is not None:db.execute('UPDATE resources SET external_key=? WHERE id=?',(index,value['id']))
        if head:db.execute('UPDATE resource_versions SET payload=? WHERE id=? AND revision=?',(raw,value['id'],value['revision']))


def test_all_states_and_counts_are_read_only_and_do_not_claim_qualification(tmp_path):
    store=Store(tmp_path/'managed');resource(store);resource(store,cam={'SERIAL':'12'})
    resource(store,kind='template');resource(store,cam_status='retired')
    empty=erp(store);connected=erp(store,system_id='connected')
    store.save_resource({**body(connected),'cam':{'SERIAL':'3'}},'fixture',connected['id'],1)
    bad=erp(store,system_id='bad');alter(store,bad,lambda data:data['erp'].update(unit=3))
    before=rows(store);report=preview(store)
    assert report['total']==report['filtered']==7 and set(report['counts'])==set(STATES)
    assert all(report['counts'][state]==1 for state in STATES)
    assert report['complete'] is report['sqlite_read_only'] is report['sqlite_read_transaction'] is True
    assert all(report[name] is False for name in ('audit_verified','files_verified','erp_connected','automatic_assignment','resources_written','nx_files_written','number_is_identity'))
    assert report['identity_fields']==['company_id','system_id'] and report['cardinality_policy']=='not_configured'
    assert preview(store,state='erp_draft')['items'][0]['id']==empty['id'] and preview(store,state='conflict')['items'][0]['id']==bad['id']
    assert rows(store)==before


def test_current_lookup_is_not_an_original_historical_receipt(tmp_path):
    from nxserver.write_requests import get
    store=Store(tmp_path/'managed');key=str(uuid.uuid4());original=store.erp_upsert(ERP,'erp',request_key=key)
    changed=store.save_resource({**body(original),'name':'Changed CAM description','cam':{'SERIAL':'12'}},'fixture',original['id'],1)
    current=lookup(store,'company','system');assert current['found'] and current['resource']['revision']==2 and current['resource']['name']==changed['name']
    assert current['historical_receipt'] is False and current['resource']['state']=='linked'
    assert get(store,'erp',key)['result']==original and store.erp_upsert(ERP,'erp',request_key=key)==original
    assert lookup(store,'company','missing')['found'] is False


@pytest.mark.parametrize('company,system',[('__proto__','constructor'),(' Company ',' SYSTEM '),('ä-company','🛠system')])
def test_identity_is_exact_opaque_and_never_matched_by_item_number(tmp_path,company,system):
    store=Store(tmp_path/'managed');value=erp(store,company_id=company,system_id=system)
    assert lookup(store,company,system)['resource']['id']==value['id']
    assert lookup(store,company,system+'other')['found'] is False
    assert preview(store,company_id=company)['items'][0]['id']==value['id']


def test_same_number_is_informational_not_a_merge_or_an_identity_conflict(tmp_path):
    store=Store(tmp_path/'managed');first=erp(store);second=erp(store,system_id='other');erp(store,company_id='other-company')
    before=rows(store);report=preview(store,company_id='company');assert report['filtered']==2
    assert all(item['warnings']==['number_not_unique'] and item['number_identity_count']==2 and not item['issues'] for item in report['items'])
    assert lookup(store,'company','system')['resource']['id']==first['id'] and lookup(store,'company','other')['resource']['id']==second['id']
    assert rows(store)==before


@pytest.mark.parametrize('case',['missing_index','wrong_index','invalid_index','bad_erp','company_standard','head','timestamp','gap','duplicate_claim'])
def test_inconsistent_identity_or_history_is_visible_and_lookup_fails_closed(tmp_path,case):
    store=Store(tmp_path/'managed');value=erp(store)
    if case in ('missing_index','wrong_index','invalid_index'):
        with store.db() as db:db.execute('UPDATE resources SET external_key=? WHERE id=?',(None if case=='missing_index' else canonical(['other','pair']) if case=='wrong_index' else '{}',value['id']))
    elif case=='bad_erp':alter(store,value,lambda data:data['erp'].update(unit=17))
    elif case=='company_standard':alter(store,value,lambda data:data.update(kind='template'))
    elif case=='head':alter(store,value,lambda data:data.update(name='Same revision, different payload'),head=False)
    elif case=='timestamp':
        with store.db() as db:db.execute("UPDATE resources SET updated_at='changed' WHERE id=?",(value['id'],))
    elif case=='gap':
        with store.db() as db:db.execute('DELETE FROM resource_versions WHERE id=?',(value['id'],))
    else:
        second=resource(store,name='Second');alter(store,second,lambda data:data.update(erp=body(value)['erp']))
    before=rows(store);report=preview(store);assert report['counts']['conflict']>=1
    with pytest.raises(DomainError) as failure:lookup(store,'company','system')
    assert failure.value.status==409 and rows(store)==before
    if case=='duplicate_claim':assert report['counts']['conflict']==2 and all(item['duplicate_identity_count']==2 for item in report['items'])


@pytest.mark.parametrize('raw',['{','[]','{"kind":"gauge","erp":NaN}','{"erp":{},"erp":null}'])
def test_unreadable_metadata_is_reported_without_echoing_raw_payload_or_resolving_current_lookup(tmp_path,raw):
    store=Store(tmp_path/'managed');value=erp(store)
    with store.db() as db:
        db.execute('UPDATE resources SET payload=? WHERE id=?',(raw,value['id']));db.execute('UPDATE resource_versions SET payload=? WHERE id=?',(raw,value['id']))
    before=rows(store);report=preview(store);assert report['items'][0]['state']=='conflict' and 'unreadable_resource' in report['items'][0]['issues']
    assert 'payload' not in report['items'][0] and report['items'][0]['erp_index_verified'] is False
    with pytest.raises(DomainError):lookup(store,'company','system')
    assert rows(store)==before


@pytest.mark.parametrize('field,value',[('kind',[]),('name','\ud800'),('erp',[]),('erp',{}),('cam_status',{})])
def test_wrong_json_shapes_do_not_cause_internal_error(tmp_path,field,value):
    store=Store(tmp_path/'managed');record=erp(store)
    def mutate(data):data[field]=value
    raw=json.dumps({**body(record),field:value},ensure_ascii=True)
    with store.db() as db:
        db.execute('UPDATE resources SET payload=? WHERE id=?',(raw,record['id']));db.execute('UPDATE resource_versions SET payload=? WHERE id=?',(raw,record['id']))
    report=preview(store);assert report['items'][0]['state']=='conflict'
    json.dumps(report,ensure_ascii=False).encode('utf-8')


def test_snapshot_bound_paging_filtering_and_complete_counts(tmp_path):
    store=Store(tmp_path/'managed')
    for index in range(61):erp(store,system_id=str(index),number=f'ITEM{index:03}',description=f'Fixture {index}')
    first=preview(store);second=preview(store,offset=50,expected_hash=first['catalog_sha256'])
    assert len(first['items'])==50 and len(second['items'])==11 and first['counts']['erp_draft']==61
    assert not {item['id'] for item in first['items']} & {item['id'] for item in second['items']}
    assert preview(store,query='ITEM060')['filtered']==1 and preview(store,query='fixture 60')['filtered']==1
    assert preview(store,offset=1000)['items']==[]
    resource(store)
    with pytest.raises(DomainError,match='catalog changed'):preview(store,offset=50,expected_hash=first['catalog_sha256'])
    with pytest.raises(DomainError,match='catalog changed'):lookup(store,'company','0',first['catalog_sha256'])


def test_read_only_connection_and_bounded_reader_do_not_change_journal_or_rows(tmp_path,monkeypatch):
    store=Store(tmp_path/'managed');erp(store);before=rows(store)
    with sqlite3.connect(store.root/'management.sqlite3') as db:assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0]=='delete'
    monkeypatch.setattr(store,'db',lambda:pytest.fail('ERP reads must not open writable Store connection.'))
    assert preview(store)['sqlite_read_only'] and lookup(store,'company','system')['found']
    with sqlite3.connect(store.root/'management.sqlite3') as db:assert db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
    for attribute,value in [('MAX_ROWS',0),('MAX_PAYLOAD',1),('MAX_READ',1)]:
        with monkeypatch.context() as patch:
            patch.setattr(erp_catalog,attribute,value)
            with pytest.raises(DomainError,match='bounded|budget'):preview(store)


@pytest.mark.parametrize('change',[{'offset':-1},{'offset':2**63},{'limit':101},{'state':'unknown'},{'kind':[]},{'query':'x'*201},{'company_id':'x'*101}])
def test_direct_filters_are_bounded(tmp_path,change):
    with pytest.raises(DomainError):preview(Store(tmp_path/'managed'),**change)


def test_http_permissions_current_lookup_and_no_new_tables(pilot):
    client,store,*_=pilot;value=erp(store);before=rows(store)
    assert client.get('/api/v1/erp/catalog-preview').status_code==401
    assert client.get('/api/v1/erp/catalog-preview',headers=ERP_HEADERS).status_code==403
    assert client.get('/api/v1/erp/catalog-preview',headers=HEADERS).status_code==200
    query={'company_id':'company','system_id':'system'}
    assert client.get('/api/v1/erp/resources/lookup',params=query).status_code==401
    for headers in (HEADERS,ERP_HEADERS):assert client.get('/api/v1/erp/resources/lookup',headers=headers,params=query).json()['resource']['id']==value['id']
    for path,params in [('/api/v1/erp/catalog-preview',{'limit':101}),('/api/v1/erp/catalog-preview',{'offset':2**63}),('/api/v1/erp/resources/lookup',{'company_id':'','system_id':'s'})]:
        assert client.get(path,headers=HEADERS,params=params).status_code==422
    cap=client.get('/api/v1/capabilities',headers=HEADERS).json()['erp_catalog'];assert cap['read_only'] and not cap['automatic_assignment'] and not cap['number_is_identity']
    assert rows(store)==before


@pytest.mark.parametrize('field',['company_id','system_id'])
def test_upsert_never_reassigns_an_inconsistent_existing_identity(tmp_path,field):
    store=Store(tmp_path/'managed');value=erp(store);alter(store,value,lambda data:data['erp'].update({field:'different'}));before=rows(store)
    with pytest.raises(DomainError,match='no automatic reassignment'):
        store.erp_upsert({**ERP,'description':'Incoming update','source_revision':'2','expected_revision':1},'erp',request_key=str(uuid.uuid4()))
    assert rows(store)==before


@pytest.mark.parametrize('change',[{'company_id':17},{'system_id':''},{'unit':False},{'source_revision':None},{'kind':'unknown'}])
def test_direct_erp_writers_follow_same_field_contract_as_http(tmp_path,change):
    store=Store(tmp_path/'managed');before=rows(store)
    with pytest.raises(DomainError):store.erp_upsert({**ERP,**change},'erp',request_key=str(uuid.uuid4()))
    assert rows(store)==before


def test_conflicts_can_be_found_by_claimed_and_indexed_company_without_echoing_bad_fields(tmp_path):
    store=Store(tmp_path/'managed');value=erp(store);alter(store,value,lambda data:data['erp'].update(unit=19),index=canonical(['indexed-company','indexed-system']))
    claimed=preview(store,company_id='company');indexed=preview(store,company_id='indexed-company')
    assert claimed['filtered']==indexed['filtered']==1 and claimed['items']==indexed['items']
    item=claimed['items'][0];assert item['erp'] is None and item['claimed_identity']=={'company_id':'company','system_id':'system'} and item['indexed_identity']=={'company_id':'indexed-company','system_id':'indexed-system'}
    assert preview(store,query='indexed-system')['filtered']==1 and preview(store,query='system')['filtered']==1


@pytest.mark.parametrize('case',['json','shape','duplicate_json','head','time','gap'])
def test_current_erp_write_rejects_unreadable_or_incomplete_head_without_new_receipt(tmp_path,case):
    store=Store(tmp_path/'managed');value=erp(store)
    if case in ('json','shape','duplicate_json'):
        raw='{' if case=='json' else '[]' if case=='shape' else '{"erp":null,"erp":'+canonical(value['erp'])+'}'
        with store.db() as db:
            db.execute('UPDATE resources SET payload=? WHERE id=?',(raw,value['id']));db.execute('UPDATE resource_versions SET payload=? WHERE id=?',(raw,value['id']))
    elif case=='head':alter(store,value,lambda data:data.update(name='Changed without history'),head=False)
    else:
        with store.db() as db:
            if case=='time':db.execute("UPDATE resources SET updated_at='changed' WHERE id=?",(value['id'],))
            else:db.execute('DELETE FROM resource_versions WHERE id=?',(value['id'],))
    before=rows(store)
    with pytest.raises(DomainError) as failure:store.erp_upsert({**ERP,'description':'Next','source_revision':'2','expected_revision':1},'erp',request_key=str(uuid.uuid4()))
    assert failure.value.status==409 and rows(store)==before
