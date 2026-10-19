"""Multi-draft/attachment commits, partial failures and replay; isolated fixtures."""
import json
import uuid
from pathlib import Path

import pytest

from nxserver.integrity import scan
from nxserver.libraries import SPECS
from nxserver.store import Store,canonical
from nxserver.upgrades import LIVE
from test_management import HEADERS,ERP_HEADERS
from test_upgrades import upgrade_pilot
from test_filepackages import packages
from test_release_jobs import complete


def headers(key):return {**HEADERS,'Idempotency-Key':key}


def catalog(client,store):
    release=store.import_release('source','2406','Catalog source','fixture')
    data={'libraries':['holder-metric'],'include_standard':False}
    preview=client.post('/api/v1/releases/'+release['id']+'/catalog-preview',headers=HEADERS,json=data).json()
    assert preview['can_import'] and preview['count']==1,preview
    return release,{**data,'release_id':release['id'],'preview_hash':preview['preview_hash']}


def file_job(packages,count=2):
    client,store,_,source,standard=packages
    paths=['UGII/templates/CUSTOM'+str(index)+'.prt' for index in range(count)]
    for index,name in enumerate(paths):
        file=source/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'Synthetic PRT '+str(index).encode())
    release=store.import_release('test','2512','Customer files','fixture')
    body={'kind':'template','paths':paths,'resource_id':None}
    preview=client.post('/api/v1/releases/'+release['id']+'/file-packages/preview',headers=HEADERS,json=body).json()
    assert preview['can_adopt'],preview
    return release,{**body,'release_id':release['id'],'name':'Adopted fixture','preview_hash':preview['preview_hash']}


def test_catalog_result_atomic_draft_and_retry_never_imports_again(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;release,body=catalog(client,store);before=store.manifest(store.release_folder(release['id']));key=str(uuid.uuid4())
    initial,job=complete(client,client.post('/api/v1/release-jobs/catalog',headers=headers(key),json=body))
    assert job['status']=='completed' and len(store.resources())==1
    result=job['domain_result'];assert result['resources']==[{'id':store.resources()[0]['id'],'revision':1}] and not result['nx_files_changed']
    assert store.resources()[0]['cam_status']=='draft' and store.manifest(store.release_folder(release['id']))==before
    audit=store.audit_entries()
    monkeypatch.setattr(store,'catalog_plan',lambda *args:(_ for _ in ()).throw(AssertionError('Replay read source')))
    assert client.post('/api/v1/release-jobs/catalog',headers=headers(key),json=body).json()==initial
    assert store.audit_entries()==audit and scan(store)['error_count']==0


def test_catalog_independent_writer_between_plan_and_commit_is_not_overwritten(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;release,body=catalog(client,store);other=Store(store.root);plan=store.catalog_plan
    def race(*args):
        result=plan(*args)
        other.save_resource({'kind':'gauge','name':'Independent writer','cam':{},'erp':None,'cam_status':'draft','asset_ids':[]},'isolated writer')
        return result
    monkeypatch.setattr(store,'catalog_plan',race)
    _,job=complete(client,client.post('/api/v1/release-jobs/catalog',headers=headers(str(uuid.uuid4())),json=body))
    assert job['status']=='failed' and 'before native import commit' in job['message'] and not job.get('domain_result')
    assert len(store.resources())==1 and store.resources()[0]['kind']=='gauge'


def test_catalog_audit_failure_rolls_back_drafts_and_domain_result(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;release,body=catalog(client,store);audit=store.audit
    def failure(db,actor,action,payload):
        if action=='catalog.imported':raise RuntimeError('Synthetic catalog audit failure')
        return audit(db,actor,action,payload)
    monkeypatch.setattr(store,'audit',failure)
    _,job=complete(client,client.post('/api/v1/release-jobs/catalog',headers=headers(str(uuid.uuid4())),json=body))
    assert job['status']=='failed' and not job.get('domain_result') and store.resources()==[]


def test_file_adoption_tracks_every_asset_and_commits_result_with_resource(packages):
    client,store,_,source,standard=packages;release,body=file_job(packages);before=store.manifest(store.release_folder(release['id']));key=str(uuid.uuid4())
    first,job=complete(client,client.post('/api/v1/release-jobs/file_adopt',headers=headers(key),json=body))
    assert job['status']=='completed' and len(job['asset_allocations'])==2 and {a['state'] for a in job['asset_allocations']}=={'stored'}
    resource=store.resources()[0];assert resource['cam_status']=='draft' and len(resource['asset_ids'])==2
    assert job['domain_result']['resources']==[{'id':resource['id'],'revision':1}]
    with store.db() as db:assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==2
    audit=store.audit_entries()
    assert client.post('/api/v1/release-jobs/file_adopt',headers=headers(key),json=body).json()==first
    assert store.audit_entries()==audit and len(store.resources())==1 and store.manifest(store.release_folder(release['id']))==before
    assert scan(store)['error_count']==0


def test_physical_write_failure_has_reserved_intent_but_no_stored_asset_or_draft(packages,monkeypatch):
    client,store,_,source,standard=packages;release,body=file_job(packages,1);original=Path.write_bytes
    def fail(path,content):
        if store.root/'assets' in path.parents:raise OSError('Synthetic physical write failure')
        return original(path,content)
    monkeypatch.setattr(Path,'write_bytes',fail)
    _,job=complete(client,client.post('/api/v1/release-jobs/file_adopt',headers=headers(str(uuid.uuid4())),json=body))
    assert job['status']=='failed' and len(job['asset_allocations'])==1 and job['asset_allocations'][0]['state']=='reserved'
    with store.db() as db:assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==0
    assert store.resources()==[] and not job.get('domain_result') and scan(store)['error_count']==0


def test_resource_audit_failure_retains_stored_assets_but_rolls_back_result(packages,monkeypatch):
    client,store,_,source,standard=packages;release,body=file_job(packages);audit=store.audit
    def fail(db,actor,action,payload):
        if action=='resource.saved':raise RuntimeError('Synthetic resource audit failure')
        return audit(db,actor,action,payload)
    monkeypatch.setattr(store,'audit',fail)
    _,job=complete(client,client.post('/api/v1/release-jobs/file_adopt',headers=headers(str(uuid.uuid4())),json=body))
    assert job['status']=='failed' and not job.get('domain_result') and store.resources()==[]
    assert len(job['asset_allocations'])==2 and {a['state'] for a in job['asset_allocations']}=={'stored'}
    assert scan(store)['error_count']==0


def test_changed_baseline_during_copy_blocks_draft_and_keeps_asset_evidence(packages,monkeypatch):
    client,store,_,source,standard=packages;release,body=file_job(packages,1);add=store.add_asset
    def change(*args,**kwargs):
        result=add(*args,**kwargs);file=standard/body['paths'][0];file.write_bytes((source/body['paths'][0]).read_bytes());return result
    monkeypatch.setattr(store,'add_asset',change)
    _,job=complete(client,client.post('/api/v1/release-jobs/file_adopt',headers=headers(str(uuid.uuid4())),json=body))
    assert job['status']=='failed' and 'inputs changed during copying' in job['message'] and store.resources()==[]
    assert job['asset_allocations'][0]['state']=='stored' and not job.get('domain_result')


def test_report_write_failure_preserves_atomic_committed_result_and_replay(packages,monkeypatch):
    client,store,_,source,standard=packages;release,body=file_job(packages,1);write=Path.write_bytes;key=str(uuid.uuid4())
    def fail(path,content):
        if path.name=='report.json':raise OSError('Synthetic report publication failure')
        return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',fail)
    first,job=complete(client,client.post('/api/v1/release-jobs/file_adopt',headers=headers(key),json=body))
    assert job['status']=='failed' and len(job['domain_result']['resources'])==1 and len(store.resources())==1
    audit=store.audit_entries();assert client.post('/api/v1/release-jobs/file_adopt',headers=headers(key),json=body).json()==first
    assert store.audit_entries()==audit and scan(store)['error_count']==0


@pytest.mark.parametrize('tamper',['result','allocation'])
def test_corrupt_domain_evidence_not_presented_as_success(packages,tamper):
    client,store,_,source,standard=packages;release,body=file_job(packages,1)
    _,job=complete(client,client.post('/api/v1/release-jobs/file_adopt',headers=headers(str(uuid.uuid4())),json=body))
    if tamper=='result':job['domain_result']['resources'][0]['revision']=99
    else:job['asset_allocations'][0]['sha256']='0'*64
    with store.db() as db:db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(job),job['id']))
    assert client.get('/api/v1/migrations/'+job['id'],headers=HEADERS).status_code==409
    assert 'invalid_adoption_result' in {issue['code'] for issue in scan(store)['errors']}


def test_adoption_job_erp_cannot_create_or_read_results(packages):
    client,store,release,*_=packages
    body={'release_id':release['id'],'libraries':['tool-metric'],'include_standard':True,'preview_hash':'0'*64}
    assert client.post('/api/v1/release-jobs/catalog',json=body).status_code==401
    assert client.post('/api/v1/release-jobs/catalog',headers=ERP_HEADERS,json=body).status_code==403
    assert store.resources()==[]


@pytest.mark.parametrize('partial',[False,True])
def test_backup_restore_preserves_success_or_reserved_attachment_intent(packages,monkeypatch,partial):
    from nxserver.backups import FUTURES
    from nxserver.migration_requests import get
    client,store,_,source,standard=packages;release,body=file_job(packages,1);write=Path.write_bytes;key=str(uuid.uuid4())
    if partial:
        def fail(path,content):
            if store.root/'assets' in path.parents:raise OSError('Synthetic allocated attachment failure')
            return write(path,content)
        monkeypatch.setattr(Path,'write_bytes',fail)
    first,job=complete(client,client.post('/api/v1/release-jobs/file_adopt',headers=headers(key),json=body))
    assert job['status']==('failed' if partial else 'completed')
    monkeypatch.setattr(Path,'write_bytes',write)
    def finish(response):
        assert response.status_code==202,response.text
        identifier=response.json()['id'];FUTURES[identifier].result(timeout=30)
        assert client.app.state.backups.get_job(identifier)['status']=='completed'
        return identifier,client.app.state.backups.report(identifier)
    _,receipt=finish(client.post('/api/v1/backups',headers=HEADERS,json={}))
    preview_id,plan=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'R'}))
    assert plan['can_restore'],plan['errors']
    _,report=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={
        'name':'R','preview_id':preview_id,'preview_hash':plan['preview_hash'],'accept_unsigned':True}))
    restored=Store(report['target_folder'])
    from nxserver.upgrades import Upgrades
    old=Upgrades(restored).get(job['id']);assert old['asset_allocations']==job['asset_allocations']
    assert get(restored,'admin',key)['result']==first
    assert len(restored.resources())==(0 if partial else 1) and scan(restored)['error_count']==0


def test_owner_loss_after_domain_commit_keeps_result_and_cannot_resubmit(packages,monkeypatch):
    client,store,_,source,standard=packages;release,body=file_job(packages,1);write=Path.write_bytes;key=str(uuid.uuid4())
    def abrupt(path,content):
        if path.name=='report.json':raise SystemExit('Synthetic abrupt worker termination after domain commit')
        return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',abrupt)
    response=client.post('/api/v1/release-jobs/file_adopt',headers=headers(key),json=body);assert response.status_code==202
    with pytest.raises(SystemExit):LIVE[response.json()['id']].result(timeout=30)
    monkeypatch.delitem(LIVE,response.json()['id'])  # A real restart has no old process Future.
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:None)
    from nxserver.upgrades import Upgrades
    restarted=Upgrades(store);job=restarted.get(response.json()['id'])
    assert job['status']=='interrupted' and len(job['domain_result']['resources'])==1 and len(store.resources())==1
    audit=store.audit_entries();assert client.post('/api/v1/release-jobs/file_adopt',headers=headers(key),json=body).json()==response.json()
    assert store.audit_entries()==audit and len(store.resources())==1


def test_abrupt_future_after_domain_commit_is_observed_without_restart(packages,monkeypatch):
    client,store,_,source,standard=packages;release,body=file_job(packages,1)
    write=Path.write_bytes;key=str(uuid.uuid4())
    def abrupt(path,content):
        if path.name=='report.json':raise SystemExit('Synthetic unexpected task exit after committed draft')
        return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',abrupt)
    response=client.post('/api/v1/release-jobs/file_adopt',headers=headers(key),json=body)
    assert response.status_code==202
    identifier=response.json()['id']
    with pytest.raises(SystemExit):LIVE[identifier].result(timeout=30)
    # Owner/server remains live: reconciliation runs on an ordinary API read.
    job=client.get('/api/v1/migrations/'+identifier,headers=HEADERS).json()
    assert job['status']=='failed' and job['execution']['future']=='failed'
    assert len(job['domain_result']['resources'])==1 and len(store.resources())==1
    assert job['asset_allocations'][0]['state']=='stored'
    assert job['execution_observation']['terminal_evidence_verified']
    audits=store.audit_entries()
    assert client.get('/api/v1/migrations',headers=HEADERS).json()[0]['status']=='failed'
    assert client.post('/api/v1/release-jobs/file_adopt',headers=headers(key),json=body).json()==response.json()
    assert store.audit_entries()==audits and scan(store)['error_count']==0
