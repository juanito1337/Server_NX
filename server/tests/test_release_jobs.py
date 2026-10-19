"""Whole-folder background import/export on isolated native CAM fixtures only."""
import threading
import json
import uuid
from pathlib import Path

import pytest

from nxserver.api import ReleaseInput
from nxserver.backups import FUTURES
from nxserver.integrity import scan
from nxserver.libraries import NativeTable,SPECS
from nxserver.migration_requests import get
from nxserver.store import Store
from nxserver.upgrades import LIVE
from test_management import pilot,HEADERS,ERP_HEADERS,imported,tool_payload
from test_upgrades import upgrade_pilot


def header(key):return {**HEADERS,'Idempotency-Key':key}
def complete(client,response):
    assert response.status_code==202,response.text
    initial=response.json();LIVE[initial['id']].result(timeout=30)
    return initial,client.get('/api/v1/migrations/'+initial['id'],headers=HEADERS).json()


def import_body(client,request):
    body={'source':request['source'],'source_path':request['source_path'],'target':request['target'],'target_path':request['target_path'],
        'nx_version':'2512','name':'Prepared import job','normalize_libraries':True,'legacy_version':'2406'}
    response=client.post('/api/v1/import-preview',headers=HEADERS,json=body)
    assert response.status_code==200 and response.json()['can_import'],response.text
    return {**body,'preview_hash':response.json()['preview_hash']}


def export_body(client,parent):
    report=client.post('/api/v1/releases/'+parent['id']+'/derive-preview',headers=HEADERS,json={}).json()
    assert report['can_export'],report
    return {'parent_release_id':parent['id'],'name':'Daily export job','defaults':report['defaults'],'preview_hash':report['preview_hash']}


def test_prepared_import_async_progress_result_and_replay_without_second_folder(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;body=import_body(client,request);key=str(uuid.uuid4());before=store.manifest(source)
    changes=[];original=client.app.state.upgrades._update
    def record(identifier,**fields):changes.append(fields);return original(identifier,**fields)
    monkeypatch.setattr(client.app.state.upgrades,'_update',record)
    first,job=complete(client,client.post('/api/v1/release-jobs/import',headers=header(key),json=body))
    assert first['status']=='queued' and job['status']=='completed' and job['progress']==100
    assert any(item.get('status')=='building' for item in changes) and any(item.get('status')=='verifying' for item in changes)
    release=store.get_release(job['release_id']);assert release['status']=='draft' and release['test_launcher']
    assert NativeTable.read(Path(release['folder'])/SPECS['holder-metric']['path']).groups()['CUSTOM']
    assert store.manifest(source)==before and store.active()['release_id'] is None
    audit=store.audit_entries();again=client.post('/api/v1/release-jobs/import',headers=header(key),json=body)
    assert again.status_code==202 and again.json()==first and len(store.releases())==1 and store.audit_entries()==audit
    assert scan(store)['error_count']==0


def test_daily_export_accepted_without_hashing_parent_in_http_and_returns_revision_bound_result(pilot,monkeypatch):
    client,store,source,_=pilot;parent=imported(client);resource=client.post('/api/v1/resources',headers=HEADERS,json=tool_payload()).json()
    body=export_body(client,parent);before=store.manifest(Path(parent['folder']));key=str(uuid.uuid4());original=store.manifest
    # API sync handlers run in another thread; wrap the worker to distinguish
    # background filesystem verification from acceptance itself.
    worker_threads=[];run=client.app.state.upgrades._run
    def worker(identifier):worker_threads.append(threading.get_ident());return run(identifier)
    monkeypatch.setattr(client.app.state.upgrades,'_run',worker)
    def manifest(root):assert threading.get_ident() in worker_threads,'HTTP acceptance performed a full manifest scan';return original(root)
    monkeypatch.setattr(store,'manifest',manifest)
    first,job=complete(client,client.post('/api/v1/release-jobs/derive',headers=header(key),json=body))
    monkeypatch.setattr(store,'manifest',original)
    assert job['status']=='completed',job
    result=store.get_release(job['release_id']);assert result['parent_id']==parent['id'] and result['status']=='draft'
    assert result['catalog']['exported_tools'][resource['id']]['revision']==1
    assert original(Path(parent['folder']))==before and store.manifest(source)==store.manifest(Path(parent['folder']))
    assert get(store,'admin',key)['result']==first
    report=client.get('/api/v1/migrations/'+job['id']+'/report',headers=HEADERS).json()
    assert report['preview_hash']==body['preview_hash'] and not report['automatic_approval']
    assert scan(store)['error_count']==0


@pytest.mark.parametrize('mode',['import','derive'])
def test_stale_preview_is_failed_by_worker_before_creating_release_or_target(pilot,mode):
    client,store,source,_=pilot
    if mode=='derive':parent=imported(client);body=export_body(client,parent);expected=1
    else:
        body=ReleaseInput(source='test',nx_version='2512',name='Bad preview',normalize_libraries=True,preview_hash='0'*64).model_dump();expected=0
    body['preview_hash']='0'*64
    _,job=complete(client,client.post('/api/v1/release-jobs/'+mode,headers=header(str(uuid.uuid4())),json=body))
    assert job['status']=='failed' and not job.get('release_id') and len(store.releases())==expected
    assert store.active()['release_id'] is None


def test_catalog_change_from_independent_writer_during_copy_cannot_commit_stale_export(pilot,monkeypatch):
    client,store,source,_=pilot;parent=imported(client);body=export_body(client,parent)
    other=Store(store.root);snapshot=store._snapshot
    def changed(*args,**kwargs):
        result=snapshot(*args,**kwargs)
        other.save_resource({'kind':'gauge','name':'Changed during copy','cam':{},'erp':None,'cam_status':'draft','asset_ids':[]},'isolated writer')
        return result
    monkeypatch.setattr(store,'_snapshot',changed)
    _,job=complete(client,client.post('/api/v1/release-jobs/derive',headers=header(str(uuid.uuid4())),json=body))
    assert job['status']=='failed' and 'Catalog changed' in job['message'] and job['partial_output_retained']
    failed=store.get_release(job['release_id']);assert failed['status']=='failed' and Path(failed['folder']).is_dir()
    assert store.get_release(parent['id'])['manifest']==parent['manifest'] and store.active()['release_id'] is None


def test_copy_failure_preserves_linked_failed_release_and_never_restarts_same_acceptance(pilot,monkeypatch):
    client,store,source,_=pilot;parent=imported(client);body=export_body(client,parent);key=str(uuid.uuid4())
    def failure(*args,**kwargs):raise OSError('Synthetic storage failure after release registration')
    monkeypatch.setattr(store,'_snapshot',failure)
    first,job=complete(client,client.post('/api/v1/release-jobs/derive',headers=header(key),json=body))
    assert job['status']=='failed' and store.get_release(job['release_id'])['status']=='failed'
    before=store.audit_entries();again=client.post('/api/v1/release-jobs/derive',headers=header(key),json=body)
    assert again.json()==first and store.audit_entries()==before and len(store.releases())==2


def test_catalog_change_during_launcher_creation_is_rejected_inside_final_sql_transaction(pilot,monkeypatch):
    client,store,source,_=pilot;parent=imported(client);body=export_body(client,parent)
    other=Store(store.root);launcher=store._create_test_launcher
    def late_change(*args,**kwargs):
        result=launcher(*args,**kwargs)
        other.save_resource({'kind':'gauge','name':'Late commit-boundary change','cam':{},'erp':None,'cam_status':'draft','asset_ids':[]},'isolated writer')
        return result
    monkeypatch.setattr(store,'_create_test_launcher',late_change)
    _,job=complete(client,client.post('/api/v1/release-jobs/derive',headers=header(str(uuid.uuid4())),json=body))
    assert job['status']=='failed' and 'before export commit' in job['message']
    assert store.get_release(job['release_id'])['status']=='failed' and store.active()['release_id'] is None


def test_release_job_authorization_and_path_guard_before_acceptance(pilot):
    client,store,source,_=pilot;body={'source':'test','nx_version':'2512','name':'Role/path probe','normalize_libraries':True,'preview_hash':'0'*64}
    assert client.post('/api/v1/release-jobs/import',json=body).status_code==401
    assert client.post('/api/v1/release-jobs/import',headers=ERP_HEADERS,json=body).status_code==403
    assert client.post('/api/v1/release-jobs/import',headers=HEADERS,json={**body,'source_path':r'W:\SiemensShare\nx2406'}).status_code==400
    assert client.get('/api/v1/migrations',headers=HEADERS).json()==[] and store.releases()==[]
    assert client.post('/api/v1/release-jobs/import',headers=HEADERS,json={**body,'normalize_libraries':False}).status_code==400


def test_reference_decisions_and_hash_survive_canonical_job_roundtrip(upgrade_pilot):
    from test_references import reference_data
    from nxserver.store import canonical
    client,store,request,source=upgrade_pilot;decisions=reference_data(store,source)
    file=source/SPECS['tool-metric']['path'];file.write_bytes(file.read_bytes().replace(b'KEY1',b'__proto__'))
    decisions={'tool-metric':{'__proto__':decisions['tool-metric']['KEY1'],'KEY2':decisions['tool-metric']['KEY2']}}
    body={'source':'source','source_path':str(source),'target':request['target'],'target_path':request['target_path'],
        'nx_version':'2512','name':'Canonical reference job','normalize_libraries':True,'legacy_version':'2406','reference_resolutions':decisions}
    original=client.post('/api/v1/import-preview',headers=HEADERS,json=body).json()
    reordered=client.post('/api/v1/import-preview',headers=HEADERS,json=json.loads(canonical(body))).json()
    assert original['can_import'] and original['preview_hash']==reordered['preview_hash']
    _,job=complete(client,client.post('/api/v1/release-jobs/import',headers=header(str(uuid.uuid4())),json={**body,'preview_hash':original['preview_hash']}))
    assert job['status']=='completed',job
    groups=NativeTable.read(Path(request['target_path'])/SPECS['tool-metric']['path']).groups()
    assert [r['values']['MATREF'] for r in groups['__proto__']]==['TMC0_000055','TMC0_00007']


def test_backup_and_isolated_restore_retain_export_job_report_and_acceptance(pilot):
    client,store,source,_=pilot;parent=imported(client);body=export_body(client,parent);key=str(uuid.uuid4())
    first,job=complete(client,client.post('/api/v1/release-jobs/derive',headers=header(key),json=body));assert job['status']=='completed'
    def backup_finish(response):
        assert response.status_code==202,response.text
        identifier=response.json()['id'];FUTURES[identifier].result(timeout=30)
        assert client.app.state.backups.get_job(identifier)['status']=='completed'
        return identifier,client.app.state.backups.report(identifier)
    _,receipt=backup_finish(client.post('/api/v1/backups',headers=HEADERS,json={}))
    preview_id,report=backup_finish(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'R'}))
    assert report['can_restore'],report['errors']
    _,result=backup_finish(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={
        'name':'R','preview_id':preview_id,'preview_hash':report['preview_hash'],'accept_unsigned':True}))
    restored=Store(result['target_folder']);assert get(restored,'admin',key)['result']==first
    assert scan(restored)['error_count']==0 and restored.active()['release_id'] is None
