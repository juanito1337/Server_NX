"""Background prepared-result operations: fake PRT/STEP data, no actual NX."""
import copy
import json
import threading
import uuid
from pathlib import Path

import pytest

from nxserver.integrity import scan
from nxserver.geometry import GeometryJobs
from nxserver.results import Results
from nxserver.store import Store
from nxserver.upgrades import LIVE,Upgrades
from test_results import result_pilot,bundle,HEADERS,ERP
from test_libraries import resource_body


def headers(key):return {**HEADERS,'Idempotency-Key':key}
def upload(client,job,content,key):return client.post('/api/v1/release-jobs/result_upload',params={'geometry_job_id':job['id']},headers={**headers(key),'Content-Type':'application/zip'},content=content)
def finish(client,response):
    assert response.status_code==202,response.text
    accepted=response.json();LIVE[accepted['id']].result(timeout=30)
    job=client.get('/api/v1/migrations/'+accepted['id'],headers=HEADERS);assert job.status_code==200,job.text
    return accepted,job.json()
def adopt(client,key,result,release,plan):
    return client.post('/api/v1/release-jobs/result_adopt',headers=headers(key),json={'result_id':result['id'],'target_release_id':release['id'],'preview_hash':plan['preview_hash']})


def test_upload_replay_and_content_dedup_bind_original_result_without_second_archive(result_pilot):
    client,store,release,resource,job=result_pilot;content,_=bundle(job);key=str(uuid.uuid4())
    accepted,done=finish(client,upload(client,job,content,key));assert done['status']=='completed',done
    result=done['domain_result'];assert result['mode']=='result_upload' and not result['resources'] and not result['reused_existing']
    assert done['result_allocation']['state']=='stored'
    audit=store.audit_entries();before=list((store.root/'geometry-results').iterdir())
    assert upload(client,job,content,key).json()==accepted and store.audit_entries()==audit
    assert list((store.root/'geometry-results').iterdir())==before
    _,again=finish(client,upload(client,job,content,str(uuid.uuid4())));assert again['status']=='completed'
    assert again['domain_result']['result_id']==result['result_id'] and again['domain_result']['reused_existing']
    assert list((store.root/'geometry-results').iterdir())==before and scan(store)['error_count']==0
    assert client.post('/api/v1/release-jobs/result_adopt',headers={'Authorization':'Bearer '+ERP},json={}).status_code==403


def test_apply_atomically_binds_two_drafts_attachments_and_result_with_replay_no_duplicates(result_pilot):
    client,store,release,resource,job=result_pilot;content,_=bundle(job)
    _,uploaded=finish(client,upload(client,job,content,str(uuid.uuid4())));result=Results(store).get(uploaded['domain_result']['result_id'])
    plan=Results(store).preview(result['id'],release['id'])[0];key=str(uuid.uuid4());accepted,done=finish(client,adopt(client,key,result,release,plan))
    assert done['status']=='completed',done
    assert len(done['domain_result']['resources'])==2 and len(done['asset_allocations'])==2
    assert all(item['state']=='stored' for item in done['asset_allocations'])
    before=copy.deepcopy(store.resources());audit=store.audit_entries()
    assert adopt(client,key,result,release,plan).json()==accepted and store.resources()==before and store.audit_entries()==audit
    assert Results(store).get(result['id'])['applied']['resources']
    assert scan(store)['error_count']==0 and not store.active()['release_id']


@pytest.mark.parametrize('where',['upload_file','upload_audit','apply_asset','apply_audit','report'])
def test_partial_failures_keep_correlated_allocations_and_never_repeat_worker(result_pilot,monkeypatch,where):
    client,store,release,resource,job=result_pilot;content,_=bundle(job);key=str(uuid.uuid4())
    if where.startswith('apply') or where=='report':
        result=Results(store).upload(job['id'],content,'admin');plan=Results(store).preview(result['id'],release['id'])[0]
    write=Path.write_bytes;audit=store.audit
    def writing(path,raw):
        if where=='upload_file' and path.name=='result.zip':raise OSError('Synthetic result disk failure')
        if where=='apply_asset' and path.suffix=='.prt':raise OSError('Synthetic PRT disk failure')
        if where=='report' and path.name=='report.json' and path.parent.parent.name=='upgrades':raise OSError('Synthetic report publication failure')
        return write(path,raw)
    def auditing(db,actor,action,payload):
        if (where=='upload_audit' and action=='geometry.result.received') or (where=='apply_audit' and action=='geometry.result.applied'):
            raise RuntimeError('Synthetic result audit rollback')
        return audit(db,actor,action,payload)
    monkeypatch.setattr(Path,'write_bytes',writing);monkeypatch.setattr(store,'audit',auditing)
    response=upload(client,job,content,key) if where.startswith('upload') else adopt(client,key,result,release,plan)
    accepted,done=finish(client,response);assert done['status']=='failed',done
    if where.startswith('upload'):assert done['result_allocation']['state']=='reserved' and done.get('domain_result') is None
    elif where=='report':assert done['domain_result']['resources'] and Results(store).get(result['id'])['applied']
    else:assert done['asset_allocations'] and done.get('domain_result') is None and not Results(store).get(result['id'])['applied']
    before=copy.deepcopy(store.resources());audit_before=store.audit_entries()
    retry=upload(client,job,content,key) if where.startswith('upload') else adopt(client,key,result,release,plan)
    assert retry.json()==accepted and store.resources()==before and store.audit_entries()==audit_before
    assert scan(store)['error_count']==0


def test_related_owner_change_during_copy_blocks_drafts_inside_final_sql_gate(result_pilot,monkeypatch):
    client,store,release,resource,job=result_pilot;service=Results(store);content,_=bundle(job);result=service.upload(job['id'],content,'admin')
    plan=service.preview(result['id'],release['id'])[0];write=Path.write_bytes;changed=False
    def writing(path,raw):
        nonlocal changed
        value=write(path,raw)
        if path.suffix=='.prt' and not changed:
            changed=True;other=Store(store.root);other.save_resource({'kind':'gauge','name':'Other concurrent catalog change','cam':{},'erp':None,'asset_ids':[],'cam_status':'draft'},'fixture')
        return value
    monkeypatch.setattr(Path,'write_bytes',writing)
    _,done=finish(client,adopt(client,str(uuid.uuid4()),result,release,plan))
    assert done['status']=='failed' and 'Catalog ownership/revisions changed' in done['message']
    assert len(store.resources())==2 and next(r for r in store.resources() if r['id']==resource['id'])['revision']==1
    assert not service.get(result['id'])['applied'] and done['asset_allocations']


def test_corrupt_result_job_binding_is_not_success_and_integrity_detects_it(result_pilot):
    client,store,release,resource,job=result_pilot;content,_=bundle(job);_,done=finish(client,upload(client,job,content,str(uuid.uuid4())))
    with store.db() as db:
        row=db.execute('SELECT payload FROM migration_jobs WHERE id=?',(done['id'],)).fetchone();value=json.loads(row['payload']);value['domain_result']['sha256']='0'*64
        db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(json.dumps(value),done['id']))
    assert client.get('/api/v1/migrations/'+done['id'],headers=HEADERS).status_code==409
    assert 'invalid_adoption_result' in {issue['code'] for issue in scan(store)['errors']}


def test_submit_failure_enqueued_callback_cannot_start_upload_late(result_pilot,monkeypatch):
    client,store,release,resource,job=result_pilot;content,_=bundle(job);manager=client.app.state.upgrades;queued=[]
    def fail(fn,*args):queued.append((fn,args));raise OSError('Synthetic submission failure after enqueue')
    monkeypatch.setattr(manager.pool,'submit',fail)
    response=upload(client,job,content,str(uuid.uuid4()));assert response.status_code==202
    current=manager.get(response.json()['id']);assert current['worker_submission_failed']
    fn,args=queued[0];fn(*args)
    assert not Results(store).list(job['id']) and not (store.root/'upgrades'/current['id']).exists()


@pytest.mark.parametrize('state',['uploaded','adopted','reserved'])
def test_result_job_domain_and_partial_intents_survive_backup_restore(result_pilot,monkeypatch,state):
    client,store,release,resource,job=result_pilot;content,_=bundle(job);write=Path.write_bytes
    if state=='reserved':
        def failure(path,raw):
            if path.name=='result.zip':raise OSError('Synthetic retained archive intent')
            return write(path,raw)
        monkeypatch.setattr(Path,'write_bytes',failure)
    _,done=finish(client,upload(client,job,content,str(uuid.uuid4())))
    monkeypatch.setattr(Path,'write_bytes',write)
    if state=='adopted':
        result=Results(store).get(done['domain_result']['result_id']);plan=Results(store).preview(result['id'],release['id'])[0]
        _,done=finish(client,adopt(client,str(uuid.uuid4()),result,release,plan))
    from nxserver.backups import FUTURES
    def backup_finish(response):
        assert response.status_code==202,response.text
        key=response.json()['id'];FUTURES[key].result(timeout=30)
        assert client.app.state.backups.get_job(key)['status']=='completed'
        return key,client.app.state.backups.report(key)
    _,receipt=backup_finish(client.post('/api/v1/backups',headers=HEADERS,json={}))
    preview,plan=backup_finish(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'J'}))
    assert plan['can_restore'],plan['errors']
    _,report=backup_finish(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={
        'name':'J','preview_id':preview,'preview_hash':plan['preview_hash'],'accept_unsigned':True}))
    restored=Store(report['target_folder']);current=Upgrades(restored).get(done['id'])
    assert current.get('domain_result')==done.get('domain_result')
    assert current.get('result_allocation')==done.get('result_allocation')
    assert current.get('asset_allocations')==done.get('asset_allocations') and scan(restored)['error_count']==0


def test_target_changes_during_copy_leave_old_drafts_and_retained_assets(result_pilot,monkeypatch):
    client,store,release,resource,job=result_pilot;service=Results(store);content,_=bundle(job);result=service.upload(job['id'],content,'admin')
    plan=service.preview(result['id'],release['id'])[0];write=Path.write_bytes;changed=False
    def writing(path,raw):
        nonlocal changed
        saved=write(path,raw)
        if path.suffix=='.prt' and not changed:
            changed=True
            target=store.release_folder(release['id'])/ 'MACH/resource/library/tool/metric/tool_database.dat'
            target.write_bytes(target.read_bytes()+b'\n# Synthetic target drift\n')
        return saved
    monkeypatch.setattr(Path,'write_bytes',writing)
    _,done=finish(client,adopt(client,str(uuid.uuid4()),result,release,plan))
    assert done['status']=='failed' and done['asset_allocations'] and not service.get(result['id'])['applied']
    assert store.resources()==[resource]


def test_independent_collectors_wait_before_payload_read_and_keep_both_snapshots(result_pilot,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    client,store,release,resource,job=result_pilot;other=Store(store.root)
    entered=threading.Event();allow=threading.Event();second=threading.Event();audit=store.audit;other_db=other.db
    def auditing(db,actor,action,payload):
        value=audit(db,actor,action,payload)
        if action=='geometry.collector.created':entered.set();assert allow.wait(timeout=15)
        return value
    monkeypatch.setattr(store,'audit',auditing)
    @contextmanager
    def traced_db():
        with other_db() as db:
            db.set_trace_callback(lambda sql:second.set() if sql.strip().upper()=='BEGIN IMMEDIATE' else None)
            yield db
    monkeypatch.setattr(other,'db',traced_db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(Results(store).collector,job['id'],'tool-metric','C1','admin');assert entered.wait(timeout=10)
        another=pool.submit(Results(other).collector,job['id'],'tool-metric','C2','admin')
        try:assert second.wait(timeout=10)
        finally:allow.set()
        _,one=first.result(timeout=20);_,two=another.result(timeout=20)
    assert {one,two}==set(GeometryJobs(other).get(job['id'])['collector_packages']) and scan(other)['error_count']==0


def test_raw_upload_route_has_64mib_bound_and_never_accepts_a_false_length(result_pilot):
    client,store,release,resource,job=result_pilot;content,_=bundle(job)
    response=client.post('/api/v1/release-jobs/result_upload',params={'geometry_job_id':job['id']},headers={**HEADERS,
        'Content-Type':'application/zip','Content-Length':str(31*1024*1024)},content=content)
    assert response.status_code==400 and 'differs from Content-Length' in response.text
    too_big=client.post('/api/v1/release-jobs/result_upload',params={'geometry_job_id':job['id']},headers={**HEADERS,
        'Content-Type':'application/zip','Content-Length':str(64*1024*1024+1)},content=content)
    assert too_big.status_code==413
