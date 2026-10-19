"""Opt-in 1:N ERP model on isolated data, never Business Central or actual NX."""
import copy
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.integrity import scan,snapshot
from nxserver.safety import DomainError
from nxserver.store import Store
from test_erp_catalog import ERP,BODY,body
from test_management import ADMIN,ERP as ERP_TOKEN,HEADERS,ERP_HEADERS


def headers(role=HEADERS,key=None):return {**role,'Idempotency-Key':key or str(uuid.uuid4())}
def model(client):return client.get('/api/v1/erp/model',headers=HEADERS)
def preview(client):return client.get('/api/v1/erp/model/migration-preview',headers=HEADERS)
def enable(client,key=None):
    report=preview(client);assert report.status_code==200,report.text
    response=client.post('/api/v1/erp/model/migrate',headers=headers(key=key),json={'preview_hash':report.json()['preview_hash']})
    assert response.status_code==200,response.text
    return response.json()


@pytest.fixture
def variants(tmp_path):
    store=Store(tmp_path/'managed')
    with TestClient(create_app(store,ADMIN,ERP_TOKEN)) as client:yield client,store


def test_startup_and_preview_never_auto_migrate_or_change_original_rows(variants):
    client,store=variants;before=snapshot(store)['sha256'];state=model(client)
    assert state.status_code==200,state.text
    assert state.json()['enabled'] is False and state.json()['target_policy']=='one_article_many_variants'
    report=preview(client);assert report.status_code==200,report.text
    assert report.json()['can_migrate'] and report.json()['technical_resources_written'] is False
    assert snapshot(store)['sha256']==before
    Store(store.root);assert snapshot(store)['sha256']==before


def test_migration_preserves_all_old_resources_revisions_receipts_and_audits(variants):
    client,store=variants;old=store.erp_upsert(ERP,'erp',request_key=str(uuid.uuid4()))
    plain=store.save_resource(copy.deepcopy(BODY),'fixture')
    original=store.resources();history=store.history(old['id']);prefix=store.audit_entries()
    before=snapshot(store)['sha256'];result=enable(client)
    assert result['legacy_articles']==result['legacy_links']==1 and result['technical_resources_written'] is False
    assert store.resources()==original and store.history(old['id'])==history and store.audit_entries()[-len(prefix):]==prefix
    backup=Path(result['backup_folder']);assert snapshot(type('Backup',(),{'root':backup})())['sha256']==before
    assert model(client).json()['enabled'] is True and scan(store,include_files=False)['error_count']==0
    value=client.get('/api/v1/erp/articles?query=ITEM1',headers=HEADERS).json()['items'][0]
    links=client.get('/api/v1/erp/articles/'+value['id']+'/variants',headers=HEADERS).json()
    assert links['items'][0]['resource_id']==old['id'] and len(links['items'])==1
    assert plain['id']!=old['id']


def test_one_article_many_variants_shared_erp_update_never_changes_cam(variants):
    client,store=variants;enable(client)
    fields={name:value for name,value in ERP.items() if name!='kind'}
    response=client.post('/api/v1/erp/articles/upsert',headers=headers(ERP_HEADERS),json={**fields,'expected_revision':0})
    assert response.status_code==200,response.text
    article=response.json();first=store.save_resource(copy.deepcopy(BODY),'fixture');second=store.save_resource({**copy.deepcopy(BODY),'name':'Second CAM variant'},'fixture')
    for resource in (first,second):
        response=client.put('/api/v1/erp/variants/'+resource['id'],headers=headers(),json={
            'article_id':article['id'],'expected_revision':0,'expected_resource_revision':1,'expected_article_revision':1})
        assert response.status_code==200,response.text
    original=store.resources();history=[store.history(resource['id']) for resource in original]
    response=client.post('/api/v1/erp/articles/upsert',headers=headers(ERP_HEADERS),json={**fields,'description':'Updated commercial description','expected_revision':1})
    assert response.status_code==200,response.text
    assert response.json()['revision']==2 and store.resources()==original
    assert [store.history(resource['id']) for resource in original]==history
    links=client.get('/api/v1/erp/articles/'+article['id']+'/variants',headers=ERP_HEADERS)
    assert links.status_code==200 and len(links.json()['items'])==2
    assert links.json()['article']['erp']['description']=='Updated commercial description'
    assert scan(store,include_files=False)['error_count']==0


def test_migration_is_pinned_and_rolls_back_ddl_when_final_audit_fails(variants,monkeypatch):
    client,store=variants;report=preview(client).json();store.save_resource(copy.deepcopy(BODY),'fixture')
    before=snapshot(store)['sha256']
    response=client.post('/api/v1/erp/model/migrate',headers=headers(),json={'preview_hash':report['preview_hash']})
    assert response.status_code==409 and snapshot(store)['sha256']==before
    report=preview(client).json()
    def fail(*args):raise RuntimeError('Injected final audit failure')
    monkeypatch.setattr(store,'audit',fail)
    with pytest.raises(RuntimeError,match='Injected'):client.post('/api/v1/erp/model/migrate',headers=headers(),json={'preview_hash':report['preview_hash']})
    assert model(client).json()['enabled'] is False and snapshot(store)['sha256']==before
    assert list((store.root/'backups'/'erp-model').glob('*/management.sqlite3'))


def test_new_model_permissions_and_no_automatic_creation_before_migration(variants):
    client,store=variants
    for endpoint in ('model','model/migration-preview','articles'):
        assert client.get('/api/v1/erp/'+endpoint).status_code==401
    assert client.get('/api/v1/erp/model/migration-preview',headers=ERP_HEADERS).status_code==403
    assert client.post('/api/v1/erp/model/migrate',headers=headers(ERP_HEADERS),json={'preview_hash':'0'*64}).status_code==403
    assert client.get('/api/v1/erp/articles',headers=HEADERS).status_code==409
    enable(client)
    assert client.post('/api/v1/erp/articles/upsert',headers=ERP_HEADERS,json={}).status_code==422
    assert client.put('/api/v1/erp/variants/'+str(uuid.uuid4()),headers=headers(ERP_HEADERS),json={'article_id':None,'expected_revision':0,'expected_resource_revision':1,'expected_article_revision':None}).status_code==403


def article(client,**changes):
    fields={name:value for name,value in ERP.items() if name!='kind'}
    response=client.post('/api/v1/erp/articles/upsert',headers=headers(ERP_HEADERS),json={**fields,'expected_revision':0,**changes})
    assert response.status_code==200,response.text
    return response.json()


def assign(client,resource,article,**changes):
    return client.put('/api/v1/erp/variants/'+resource['id'],headers=headers(),json={'article_id':article['id'] if article else None,
        'expected_revision':0,'expected_resource_revision':resource['revision'],'expected_article_revision':article['revision'] if article else None,**changes})


def test_durable_original_receipts_and_legacy_replay_survive_later_article_updates(variants):
    client,store=variants;old_key=str(uuid.uuid4());legacy=store.erp_upsert(ERP,'erp',request_key=old_key)
    enable(client);fields={name:value for name,value in ERP.items() if name!='kind'}
    selected=client.get('/api/v1/erp/articles',headers=HEADERS).json()['items'][0]
    key_value=str(uuid.uuid4());value={**fields,'description':'Commercial revision two','expected_revision':selected['revision']}
    first=client.post('/api/v1/erp/articles/upsert',headers=headers(ERP_HEADERS,key_value),json=value).json()
    article(client,description='Commercial revision three',expected_revision=2)
    before=snapshot(store)['sha256']
    replay=client.post('/api/v1/erp/articles/upsert',headers=headers(ERP_HEADERS,key_value),json=value)
    assert replay.status_code==200 and replay.json()==first
    assert client.get('/api/v1/erp/requests/'+key_value,headers=ERP_HEADERS).json()['result']==first
    assert client.get('/api/v1/erp/requests/'+key_value,headers=HEADERS).status_code==404
    assert client.post('/api/v1/erp/articles/upsert',headers=headers(ERP_HEADERS,key_value),json={**value,'unit':'NEW'}).status_code==409
    assert store.erp_upsert(ERP,'erp',request_key=old_key)==legacy
    with pytest.raises(DomainError,match='shared ERP'):store.erp_upsert({**ERP,'source_revision':'next'},'erp')
    assert snapshot(store)['sha256']==before


def test_variant_reassignment_unlink_and_both_independent_histories(variants):
    client,store=variants;enable(client);one=article(client);two=article(client,system_id='second')
    resource=store.save_resource(copy.deepcopy(BODY),'fixture');original=copy.deepcopy(resource)
    assert assign(client,resource,one).status_code==200
    value=client.get('/api/v1/erp/variants/'+resource['id'],headers=ERP_HEADERS).json()
    assert value['article']['id']==one['id'] and value['link']['revision']==1 and value['legacy_erp_snapshot'] is None
    assert assign(client,resource,two,expected_revision=1).status_code==200
    assert assign(client,resource,None,expected_revision=2).status_code==200
    value=client.get('/api/v1/erp/variants/'+resource['id'],headers=HEADERS).json()
    assert value['article'] is None and value['link']['revision']==3 and value['link']['article_id'] is None
    history=client.get('/api/v1/erp/variants/'+resource['id']+'/history',headers=HEADERS).json()
    assert [item['article_id'] for item in history['items']]==[one['id'],two['id'],None]
    history=client.get('/api/v1/erp/articles/'+one['id']+'/history',headers=HEADERS).json()
    assert history['items'][0]['erp']==one['erp'] and store.resources()[0]==original
    assert scan(store,include_files=False)['error_count']==0


@pytest.mark.parametrize('change',[{'expected_revision':1},{'expected_resource_revision':2},{'expected_article_revision':2}])
def test_stale_link_expectations_never_change_any_rows(variants,change):
    client,store=variants;enable(client);value=article(client);resource=store.save_resource(copy.deepcopy(BODY),'fixture')
    before=snapshot(store)['sha256'];assert assign(client,resource,value,**change).status_code==409
    assert snapshot(store)['sha256']==before


def test_shared_number_is_never_identity_or_an_automatic_merge(variants):
    client,store=variants;store.erp_upsert(ERP,'erp');store.erp_upsert({**ERP,'system_id':'different'},'erp');enable(client)
    result=client.get('/api/v1/erp/articles',headers=HEADERS).json()
    assert result['total']==2 and {item['erp']['number'] for item in result['items']}=={'ITEM1'}
    result=client.get('/api/v1/erp/articles?company_id=company&system_id=different',headers=ERP_HEADERS).json()
    assert result['total']==1 and result['items'][0]['erp']['system_id']=='different'


def test_history_and_article_pages_are_snapshot_bound_without_any_writes(variants):
    client,store=variants;enable(client)
    from nxserver.erp_variants import ERPVariants
    manager=ERPVariants(store);fields={name:value for name,value in ERP.items() if name!='kind'}
    for index in range(61):manager.upsert({**fields,'system_id':str(index),'description':'Article '+str(index)},0,'erp',str(uuid.uuid4()))
    before=snapshot(store)['sha256'];first=manager.read();second=manager.read(offset=50,expected_hash=first['snapshot_sha256'])
    assert len(first['items'])==50 and len(second['items'])==11 and first['total']==61
    assert manager.read(query='Article 60')['filtered']==1 and snapshot(store)['sha256']==before
    manager.upsert({**fields,'system_id':'new'},0,'erp',str(uuid.uuid4()))
    with pytest.raises(DomainError,match='changed'):manager.read(offset=50,expected_hash=first['snapshot_sha256'])


@pytest.mark.parametrize('kind',['template','ui_standard'])
def test_company_standards_cannot_become_erp_variants(variants,kind):
    client,store=variants;enable(client);value=article(client)
    resource=store.save_resource({**copy.deepcopy(BODY),'kind':kind},'fixture');before=snapshot(store)['sha256']
    assert assign(client,resource,value).status_code==400 and snapshot(store)['sha256']==before


@pytest.mark.parametrize('case',['article_head','article_index','variant_reference','variant_history','receipt','partial_model'])
def test_corrupt_model_fails_closed_and_integrity_reports_inconsistency(variants,case):
    client,store=variants;enable(client);value=article(client);resource=store.save_resource(copy.deepcopy(BODY),'fixture');assert assign(client,resource,value).status_code==200
    with store.db() as db:
        if case=='article_head':db.execute("UPDATE erp_articles SET payload='{}'")
        elif case=='article_index':db.execute("UPDATE erp_articles SET external_key='[\"wrong\",\"pair\"]'")
        elif case=='variant_reference':db.execute('DELETE FROM erp_articles')
        elif case=='variant_history':db.execute('DELETE FROM erp_variant_versions')
        elif case=='receipt':db.execute("UPDATE erp_requests SET response='{}' WHERE entity_type='article'")
        else:db.execute('DROP TABLE erp_variant_versions')
    assert client.get('/api/v1/erp/articles',headers=HEADERS).status_code==409
    assert scan(store,include_files=False)['error_count']>0


def test_cancel_fences_before_migration_and_does_not_retract_committed_article(variants):
    client,store=variants;key_value=str(uuid.uuid4())
    response=client.post('/api/v1/erp/requests/'+key_value+'/cancel',headers=HEADERS,json={})
    assert response.status_code==200 and response.json()['state']=='cancelled' and model(client).json()['enabled'] is False
    plan=preview(client).json();before=snapshot(store)['sha256']
    assert client.post('/api/v1/erp/model/migrate',headers=headers(key=key_value),json={'preview_hash':plan['preview_hash']}).status_code==409
    assert snapshot(store)['sha256']==before
    enable(client);value=article(client);key_value=str(uuid.uuid4());fields=value['erp']
    response=client.post('/api/v1/erp/articles/upsert',headers=headers(ERP_HEADERS,key_value),json={**fields,'expected_revision':1})
    assert response.status_code==200
    before=snapshot(store)['sha256'];response=client.post('/api/v1/erp/requests/'+key_value+'/cancel',headers=ERP_HEADERS,json={})
    assert response.status_code==200 and response.json()['state']=='committed' and response.json()['result']==value
    assert snapshot(store)['sha256']==before


def test_migration_can_repeat_same_key_after_precommit_failure_using_its_verified_backup(variants,monkeypatch):
    client,store=variants;key_value=str(uuid.uuid4());plan=preview(client).json();original=store.audit
    with monkeypatch.context() as patch:
        patch.setattr(store,'audit',lambda *args:(_ for _ in ()).throw(RuntimeError('Injected failure')))
        with pytest.raises(RuntimeError):client.post('/api/v1/erp/model/migrate',headers=headers(key=key_value),json={'preview_hash':plan['preview_hash']})
    response=client.post('/api/v1/erp/model/migrate',headers=headers(key=key_value),json={'preview_hash':plan['preview_hash']})
    assert response.status_code==200,response.text
    before=snapshot(store)['sha256'];replay=client.post('/api/v1/erp/model/migrate',headers=headers(key=key_value),json={'preview_hash':plan['preview_hash']})
    assert replay.json()==response.json() and snapshot(store)['sha256']==before


def test_new_model_is_preserved_by_full_backup_and_isolated_restore(tmp_path):
    from nxserver.backups import FUTURES
    store=Store(tmp_path/'managed')
    with TestClient(create_app(store,ADMIN,ERP_TOKEN)) as client:
        def finish(response):
            assert response.status_code==202,response.text
            value=response.json();FUTURES[value['id']].result(timeout=30)
            return client.get('/api/v1/backup-jobs/'+value['id'],headers=HEADERS).json()
        enable(client);value=article(client);resource=store.save_resource(copy.deepcopy(BODY),'fixture');assert assign(client,resource,value).status_code==200
        old_resources=store.resources();job=finish(client.post('/api/v1/backups',headers=HEADERS,json={}))
        assert job['status']=='completed',job['message']
        receipt=client.app.state.backups.report(job['id'])
        job=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'Restored ERP variants'}))
        assert job['status']=='completed',job['message']
        plan=client.app.state.backups.report(job['id'])
        result=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,
            json={'name':'Restored ERP variants','preview_id':job['id'],'preview_hash':plan['preview_hash'],'accept_unsigned':True}))
        assert result['status']=='completed',result['message']
        restored=Store(Path(client.app.state.backups.report(result['id'])['target_folder']))
    from nxserver.erp_variants import ERPVariants
    assert ERPVariants(restored).status()['enabled'] and ERPVariants(restored).variant(resource['id'])['article']['id']==value['id']
    assert restored.resources()==old_resources and scan(restored,include_files=False)['error_count']==0


@pytest.mark.parametrize('name,bound',[('MAX_ROWS',0),('MAX_BYTES',1),('MAX_ROW_BYTES',1),('MAX_PAGE_BYTES',1)])
def test_reader_limits_fail_closed_without_partial_results_or_writes(variants,monkeypatch,name,bound):
    from nxserver import erp_variants
    client,store=variants;enable(client);article(client);before=snapshot(store)['sha256']
    monkeypatch.setattr(erp_variants,name,bound)
    response=client.get('/api/v1/erp/articles',headers=HEADERS)
    assert response.status_code==409 and snapshot(store)['sha256']==before


@pytest.mark.parametrize('options',[{'expected_revision':True},{'expected_revision':-1},{'expected_revision':2**53},{'description':''},{'kind':'gauge'}])
def test_article_input_is_strict_and_bounded(variants,options):
    client,store=variants;enable(client);fields={name:value for name,value in ERP.items() if name!='kind'};before=snapshot(store)['sha256']
    assert client.post('/api/v1/erp/articles/upsert',headers=headers(ERP_HEADERS),json={**fields,'expected_revision':0,**options}).status_code==422
    assert snapshot(store)['sha256']==before


def test_model_migration_conflicts_and_preexisting_mismatched_backup_are_not_overwritten(variants):
    client,store=variants;resource=store.save_resource(copy.deepcopy(BODY),'fixture');plan=preview(client).json();key_value=str(uuid.uuid4())
    folder=store.root/'backups'/'erp-model'/key_value;Store(folder)
    before=snapshot(store)['sha256'];old=snapshot(type('Backup',(),{'root':folder})())['sha256']
    response=client.post('/api/v1/erp/model/migrate',headers=headers(key=key_value),json={'preview_hash':plan['preview_hash']})
    assert response.status_code==409 and model(client).json()['enabled'] is False and snapshot(store)['sha256']==before
    assert snapshot(type('Backup',(),{'root':folder})())['sha256']==old
    with store.db() as db:db.execute("UPDATE resources SET payload='{}' WHERE id=?",(resource['id'],))
    plan=preview(client).json();assert not plan['can_migrate'] and plan['conflicts']


def test_erp_write_keys_cannot_be_reused_by_legacy_cam_writer_or_other_way_round(variants):
    from nxserver.erp_variants import ERPVariants
    client,store=variants;enable(client);manager=ERPVariants(store);key_value=str(uuid.uuid4());fields={name:value for name,value in ERP.items() if name!='kind'}
    manager.upsert(fields,0,'admin',key_value);before=snapshot(store)['sha256']
    with pytest.raises(DomainError,match='ERP model write'):store.save_resource(copy.deepcopy(BODY),'admin',request_key=key_value)
    assert snapshot(store)['sha256']==before
    key_value=str(uuid.uuid4());store.save_resource(copy.deepcopy(BODY),'admin',request_key=key_value);before=snapshot(store)['sha256']
    with pytest.raises(DomainError,match='legacy metadata'):manager.upsert({**fields,'system_id':'second'},0,'admin',key_value)
    assert snapshot(store)['sha256']==before


def test_new_erp_model_cannot_create_legacy_snapshot_resources_but_can_edit_old_cam(variants):
    client,store=variants;old=store.erp_upsert(ERP,'erp');enable(client)
    payload=body(old);payload['name']='Technical label, not ERP description'
    updated=store.save_resource(payload,'fixture',old['id'],old['revision'])
    assert updated['erp']==old['erp'] and updated['name']==payload['name']
    with pytest.raises(DomainError,match='shared ERP'):store.save_resource(payload,'fixture')
    assert client.get('/api/v1/erp/catalog-preview',headers=HEADERS).status_code==409
    assert client.get('/api/v1/erp/resources/lookup?company_id=company&system_id=system',headers=ERP_HEADERS).status_code==409


def test_erp_commercial_changes_leave_actual_nx_files_and_export_plan_identical(tmp_path):
    from test_exportfiles import files_setup
    from nxserver.erp_variants import ERPVariants
    store,parent,resource,*_=files_setup(tmp_path);before=store.manifest(store.release_folder(parent['id']));plan=store.preview_export(parent['id'])
    assert plan['can_export']
    with TestClient(create_app(store,ADMIN,ERP_TOKEN)) as client:
        enable(client);value=article(client);assert assign(client,resource,value).status_code==200
        article(client,description='Shared commercial update',expected_revision=1)
        assert client.get('/api/v1/erp/variants/'+resource['id'],headers=HEADERS).json()['article']['revision']==2
    assert store.manifest(store.release_folder(parent['id']))==before and store.preview_export(parent['id'])==plan
    assert store.resources()[0]==resource and scan(store)['error_count']==0


def test_variant_history_paging_is_bound_and_unlinked_state_survives_restart(variants):
    from nxserver.erp_variants import ERPVariants
    client,store=variants;enable(client);value=article(client);resource=store.save_resource(copy.deepcopy(BODY),'fixture')
    assert assign(client,resource,value).status_code==200
    first=client.get('/api/v1/erp/variants/'+resource['id']+'/history?limit=1',headers=HEADERS).json()
    assert assign(client,resource,None,expected_revision=1).status_code==200
    response=client.get('/api/v1/erp/variants/'+resource['id']+'/history?snapshot_sha256='+first['snapshot_sha256'],headers=HEADERS)
    assert response.status_code==409
    reopened=Store(store.root);manager=ERPVariants(reopened)
    assert manager.variant(resource['id'])['link']['article_id'] is None
    assert manager.history('variant',resource['id'],offset=1,limit=1)['items'][0]['article_id'] is None


def test_cancelled_article_write_is_role_scoped_and_cannot_commit(variants):
    client,store=variants;enable(client);key_value=str(uuid.uuid4())
    response=client.post('/api/v1/erp/requests/'+key_value+'/cancel',headers=ERP_HEADERS,json={});assert response.json()['state']=='cancelled'
    fields={name:value for name,value in ERP.items() if name!='kind'};before=snapshot(store)['sha256']
    assert client.post('/api/v1/erp/articles/upsert',headers=headers(ERP_HEADERS,key_value),json={**fields,'expected_revision':0}).status_code==409
    assert client.get('/api/v1/erp/requests/'+key_value,headers=HEADERS).status_code==404 and snapshot(store)['sha256']==before
