"""Selective whole-group catalog adoption, no real NX/share/ERP data."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.integrity import scan
from nxserver.libraries import SPECS,NativeTable
from nxserver.migration_requests import get
from nxserver.store import Store,canonical
from nxserver.upgrades import LIVE,Upgrades
from test_management import ADMIN,ERP,HEADERS,ERP_HEADERS
from test_libraries import write,resource_body


def database(keys):
    return '#CLASS MULTI\nFORMAT LIBRF RTYPE SEQ DESCR\n'+''.join(
        'DATA |'+key+'|1|1|Root '+key+'\nDATA |'+key+'|2|2|Child '+key+'\n' for key in keys)+'#END_DATA\n'


def setup(tmp_path,identifiers=('holder-metric','tool-metric')):
    source,standard=tmp_path/'source',tmp_path/'standard'
    for identifier in identifiers:
        write(source,SPECS[identifier]['path'],database(['STD','A','B','__proto__']))
        write(standard,SPECS[identifier]['path'],database(['STD']))
    store=Store(tmp_path/'managed',{'test':source},standards={'2512':standard})
    release=store.import_release('test','2512','Synthetic selection source','fixture')
    return store,release,source,standard


@pytest.fixture
def selected_pilot(tmp_path_factory):
    # Restore keeps immutable starter paths; use a short isolated fixture root
    # instead of bypassing the actual Windows destination-length safeguard.
    store,release,source,standard=setup(tmp_path_factory.mktemp('cat'))
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        yield client,store,release,source,standard
    with store.db() as db:identifiers=[row['id'] for row in db.execute('SELECT id FROM migration_jobs')]
    for identifier in identifiers:
        if identifier in LIVE:LIVE[identifier].result(timeout=15)


def preview(client,release,selection,standard=False):
    body={'libraries':list(selection),'include_standard':standard,'selection':selection}
    response=client.post('/api/v1/releases/'+release['id']+'/catalog-preview',headers=HEADERS,json=body)
    assert response.status_code==200,response.text
    return body,response.json()


def adopt(client,release,body,report,key=None):
    key=key or str(uuid.uuid4())
    response=client.post('/api/v1/release-jobs/catalog',headers={**HEADERS,'Idempotency-Key':key},
        json={**body,'release_id':release['id'],'preview_hash':report['preview_hash']})
    assert response.status_code==202,response.text
    LIVE[response.json()['id']].result(timeout=15)
    done=client.get('/api/v1/migrations/'+response.json()['id'],headers=HEADERS)
    assert done.status_code==200,done.text
    return response,done.json(),key


def test_selection_imports_exact_keys_and_complete_groups_never_whole_library(selected_pilot):
    client,store,release,source,standard=selected_pilot;before=store.manifest(store.release_folder(release['id']))
    body,report=preview(client,release,{'holder-metric':['B'],'tool-metric':['__proto__','A']})
    assert report['count']==3 and report['can_import'] and store.resources()==[]
    assert report['selection']=={'holder-metric':['B'],'tool-metric':['A','__proto__']}
    first,done,key=adopt(client,release,body,report)
    assert done['status']=='completed' and len(done['domain_result']['resources'])==3
    assert {(value['origin']['library_id'],value['origin']['key']) for value in store.resources()}=={
        ('holder-metric','B'),('tool-metric','A'),('tool-metric','__proto__')}
    assert all(value['cam_status']=='draft' and len(value['cam']['records'])==2 and value['erp'] is None and not value['asset_ids'] for value in store.resources())
    assert all(value['geometry']['status']=='needs_preparation' for value in store.resources())
    assert get(store,'admin',key)['result']==first.json() and store.active()['release_id'] is None
    assert store.manifest(store.release_folder(release['id']))==before and store.manifest(source)==before
    assert scan(store)['error_count']==0
    _,again=preview(client,release,body['selection']);assert again['count']==0 and len(again['already_managed'])==3


@pytest.mark.parametrize('identifier',sorted(SPECS))
def test_each_native_adapter_preserves_selected_multirow_group(tmp_path,identifier):
    store,release,source,standard=setup(tmp_path,(identifier,))
    candidates,report=store.catalog_plan(release['id'],[identifier],selection={identifier:['B']})
    assert report['can_import'] and len(candidates)==1 and candidates[0]['kind']==SPECS[identifier]['kind']
    result=store.import_catalog(release['id'],[identifier],False,report['preview_hash'],'fixture',selection={identifier:['B']})
    assert result['imported']==1 and len(store.resources()[0]['cam']['records'])==2
    assert list(NativeTable.read(store.release_folder(release['id'])/SPECS[identifier]['path']).groups())==['STD','A','B','__proto__']


@pytest.mark.parametrize('selection',[{}, {'holder-metric':[]},{'holder-metric':['A','A']},
    {'holder-metric':['MISSING'],'tool-metric':['A']}, {'unknown':['A']},
    {'holder-metric':['']},{'holder-metric':['x'*1025]}, {'holder-metric':['A|B']},
    {'holder-metric':[str(index) for index in range(501)]}])
def test_invalid_or_missing_selection_never_writes_or_widens(selected_pilot,selection):
    client,store,release,*_=selected_pilot;before=store.audit_entries()
    data={'libraries':['holder-metric'],'selection':selection}
    # An explicitly missing key is a preview conflict, not omitted silently.
    if selection=={'holder-metric':['MISSING'],'tool-metric':['A']}:
        data['libraries']=list(selection)
        response=client.post('/api/v1/releases/'+release['id']+'/catalog-preview',headers=HEADERS,json=data)
        assert response.status_code==200 and not response.json()['can_import']
        report=response.json()
        result=client.post('/api/v1/releases/'+release['id']+'/catalog-import',headers=HEADERS,json={**data,'preview_hash':report['preview_hash']})
        assert result.status_code==409
    else:
        response=client.post('/api/v1/releases/'+release['id']+'/catalog-preview',headers=HEADERS,json=data)
        assert response.status_code in (400,422),response.text
    assert store.resources()==[] and store.audit_entries()==before


def test_selected_standard_requires_explicit_readonly_adoption(selected_pilot):
    client,store,release,*_=selected_pilot
    body,blocked=preview(client,release,{'holder-metric':['STD']})
    assert not blocked['can_import'] and blocked['count']==0 and 'standard' in str(blocked['conflicts']).lower()
    body,report=preview(client,release,body['selection'],standard=True)
    assert report['count']==1 and not report['objects'][0]['editable']
    _,done,_=adopt(client,release,body,report);assert done['status']=='completed'
    resource=store.resources()[0];assert not resource['origin']['editable']
    value=resource_body(resource);value['cam']['records'][0]['values']['DESCR']='Cannot overwrite Siemens'
    value['expected_revision']=1;value['cam_status']='approved'
    # Metadata revisions are allowed; they must never grant NX overwrite rights.
    assert client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json=value).status_code==200
    proposal=client.post('/api/v1/releases/'+release['id']+'/derive-preview',headers=HEADERS,json={}).json()
    assert not proposal['can_export'] and 'standard' in str(proposal['conflicts']).lower()
    assert client.post('/api/v1/releases/'+release['id']+'/derive',headers=HEADERS,json={'name':'Must not overwrite Siemens'}).status_code==409
    assert NativeTable.read(store.release_folder(release['id'])/SPECS['holder-metric']['path']).groups()['STD'][0]['values']['DESCR']=='Root STD'


def test_selection_changes_require_new_preview_and_original_key_cannot_change(selected_pilot):
    client,store,release,*_=selected_pilot
    body,report=preview(client,release,{'holder-metric':['A']})
    changed={**body,'selection':{'holder-metric':['B']}}
    stale=client.post('/api/v1/releases/'+release['id']+'/catalog-import',headers=HEADERS,json={**changed,'preview_hash':report['preview_hash']})
    assert stale.status_code==409 and not store.resources()
    first,done,key=adopt(client,release,body,report);assert done['status']=='completed'
    audits=store.audit_entries()
    again=client.post('/api/v1/release-jobs/catalog',headers={**HEADERS,'Idempotency-Key':key},json={**body,'release_id':release['id'],'preview_hash':report['preview_hash']})
    assert again.json()==first.json() and store.audit_entries()==audits
    assert client.post('/api/v1/release-jobs/catalog',headers={**HEADERS,'Idempotency-Key':key},json={**changed,'release_id':release['id'],'preview_hash':report['preview_hash']}).status_code==409
    assert len(store.resources())==1


def test_unselected_different_existing_owner_does_not_block_selected_object(selected_pilot):
    client,store,release,*_=selected_pilot
    body,report=preview(client,release,{'holder-metric':['A']});adopt(client,release,body,report)
    resource=store.resources()[0];value=resource_body(resource);value['cam']['records'][0]['values']['DESCR']='Edited draft'
    store.save_resource(value,'fixture',resource['id'],1)
    body,report=preview(client,release,{'holder-metric':['B']});assert report['can_import'] and report['count']==1
    adopt(client,release,body,report);assert len(store.resources())==2
    _,blocked=preview(client,release,{'holder-metric':['A']});assert not blocked['can_import']


def test_optional_null_keeps_historical_whole_library_request_hash(selected_pilot,monkeypatch):
    client,store,release,*_=selected_pilot
    body={'libraries':['holder-metric'],'include_standard':False}
    report=client.post('/api/v1/releases/'+release['id']+'/catalog-preview',headers=HEADERS,json=body).json()
    assert 'selection' not in report
    first,done,key=adopt(client,release,body,report)
    assert 'selection' not in first.json()['request'] and done['status']=='completed'
    def forbidden(*args,**kwargs):raise AssertionError('Legacy replay reached selection validation/source/submit')
    monkeypatch.setattr(client.app.state.upgrades.pool,'submit',forbidden)
    again=client.post('/api/v1/release-jobs/catalog',headers={**HEADERS,'Idempotency-Key':key},
        json={**body,'selection':None,'release_id':release['id'],'preview_hash':report['preview_hash']})
    assert again.json()==first.json()


def test_selection_api_and_capability_are_admin_only(selected_pilot):
    client,store,release,*_=selected_pilot;body={'libraries':['holder-metric'],'selection':{'holder-metric':['A']}}
    for route in ('/api/v1/releases/'+release['id']+'/catalog-preview','/api/v1/releases/'+release['id']+'/catalog-import','/api/v1/release-jobs/catalog'):
        assert client.post(route,json=body).status_code==401
        assert client.post(route,headers=ERP_HEADERS,json=body).status_code==403
    cap=client.get('/api/v1/capabilities',headers=HEADERS).json()['catalog_selection']
    assert cap['whole_objects'] and cap['max_objects']==500 and not cap['automatic_dependencies']
    assert store.resources()==[]


def test_selected_customer_update_and_retirement_change_only_new_copies(selected_pilot):
    client,store,release,source,standard=selected_pilot
    body,report=preview(client,release,{'holder-metric':['A']});adopt(client,release,body,report)
    resource=store.resources()[0];value=resource_body(resource)
    value['cam']['records'][0]['values']['DESCR']='Reviewed synthetic holder revision'
    value['cam_status']='approved';saved=store.save_resource(value,'fixture',resource['id'],1)
    proposal=store.preview_export(release['id']);assert proposal['can_export'],proposal['conflicts']
    child=store.import_release('','','Synthetic daily export','fixture',parent=release['id'],preview_hash=proposal['preview_hash'])
    original=NativeTable.read(store.release_folder(release['id'])/SPECS['holder-metric']['path']).groups()
    changed=NativeTable.read(store.release_folder(child['id'])/SPECS['holder-metric']['path']).groups()
    assert changed['A'][0]['values']['DESCR']=='Reviewed synthetic holder revision'
    assert original['A'][0]['values']['DESCR']=='Root A' and changed['B']==original['B'] and changed['STD']==original['STD']
    value['cam_status']='retired';store.save_resource(value,'fixture',resource['id'],saved['revision'])
    proposal=store.preview_export(child['id']);assert proposal['can_export']
    retired=store.import_release('','','Synthetic selected retirement','fixture',parent=child['id'],preview_hash=proposal['preview_hash'])
    final=NativeTable.read(store.release_folder(retired['id'])/SPECS['holder-metric']['path']).groups()
    assert 'A' not in final and final['B']==original['B'] and final['STD']==original['STD']
    assert 'A' in NativeTable.read(source/SPECS['holder-metric']['path']).groups()
    assert store.active()['release_id'] is None


def test_selected_job_and_receipt_remain_bound_through_backup_restore(selected_pilot):
    from nxserver.backups import FUTURES
    client,store,release,*_=selected_pilot
    body,report=preview(client,release,{'holder-metric':['B']})
    first,done,key=adopt(client,release,body,report)
    def completed(response):
        assert response.status_code==202,response.text
        FUTURES[response.json()['id']].result(timeout=30)
        value=client.app.state.backups.get_job(response.json()['id'])
        assert value['status']=='completed',value
        return value['id'],client.app.state.backups.report(value['id'])
    _,backup=completed(client.post('/api/v1/backups',headers=HEADERS,json={}))
    proposal,plan=completed(client.post('/api/v1/backups/'+backup['id']+'/restore-preview',headers=HEADERS,json={'name':'Selection'}))
    assert plan['can_restore'],plan['errors']
    _,restored=completed(client.post('/api/v1/backups/'+backup['id']+'/restore',headers=HEADERS,json={
        'name':'Selection','preview_id':proposal,'preview_hash':plan['preview_hash'],'accept_unsigned':True}))
    restored_store=Store(restored['target_folder']);manager=Upgrades(restored_store)
    assert manager.get(done['id'])['request']['selection']==body['selection']
    assert manager.get(done['id'])['domain_result']==done['domain_result']
    assert get(restored_store,'admin',key)['result']==first.json()
    assert len(restored_store.resources())==1 and restored_store.resources()[0]['origin']['key']=='B'
    assert scan(restored_store)['error_count']==0


def test_domain_result_outside_original_selection_is_rejected(selected_pilot):
    client,store,release,*_=selected_pilot
    body,report=preview(client,release,{'holder-metric':['A']});first,done,key=adopt(client,release,body,report)
    other,plan=preview(client,release,{'holder-metric':['B']});_,second,_=adopt(client,release,other,plan)
    with store.db() as db:
        row=db.execute('SELECT payload FROM migration_jobs WHERE id=?',(done['id'],)).fetchone()
        value=json.loads(row[0]);value['domain_result']=second['domain_result']
        db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(value),done['id']))
    assert client.get('/api/v1/migrations/'+done['id'],headers=HEADERS).status_code==409
    assert 'invalid_adoption_result' in {item['code'] for item in scan(store)['errors']}


@pytest.mark.parametrize('drift',['source','baseline','metadata'])
def test_selected_inputs_changing_after_plan_block_draft_commit(selected_pilot,monkeypatch,drift):
    client,store,release,source,standard=selected_pilot
    body,report=preview(client,release,{'holder-metric':['A']});plan=store.catalog_plan
    def changed(*args,**kwargs):
        result=plan(*args,**kwargs)
        if drift=='metadata':
            with store.db() as db:db.execute('UPDATE releases SET nx_version=? WHERE id=?',('2406',release['id']))
        else:
            path=(store.release_folder(release['id']) if drift=='source' else standard)/SPECS['holder-metric']['path']
            path.write_text(database(['STD','A','CHANGED']),encoding='utf-8')
        return result
    monkeypatch.setattr(store,'catalog_plan',changed)
    first,done,key=adopt(client,release,body,report)
    assert done['status']=='failed' and 'before native import commit' in done['message'] and not done.get('domain_result')
    assert store.resources()==[] and get(store,'admin',key)['result']==first.json()
