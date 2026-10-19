"""STEP request/physical package races use fake text and isolated storage only."""
import copy
import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nxserver.geometry import GeometryJobs
from nxserver.geometry_requests import get,cancel
from nxserver.integrity import scan
from nxserver.safety import DomainError
from nxserver.store import Store
from test_geometry import pilot,draft,report_for,HEADERS,ERP


def body(resource,asset,**extra):
    return {'resource_id':resource['id'],'expected_revision':resource['revision'],'asset_id':asset['id'],'tool_type':'drilling_solid',**extra}
def headers(key):return {**HEADERS,'Idempotency-Key':key}
def create(client,key,payload):return client.post('/api/v1/geometry-jobs',headers=headers(key),json=payload)
def count(store):
    with store.db() as db:return db.execute('SELECT count(*) FROM geometry_jobs').fetchone()[0]
def update(store,resource):
    value={k:copy.deepcopy(v) for k,v in resource.items() if k not in ('id','revision','updated_at')};value['name']='Changed revision'
    return store.save_resource(value,'fixture',resource['id'],resource['revision'])


def test_replay_returns_original_job_after_resource_report_and_software_change_without_rewrite(pilot,monkeypatch):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());payload=body(resource,asset)
    first=create(client,key,payload);assert first.status_code==201,first.text
    job=first.json();service=GeometryJobs(store);package=service.package(job['id'])
    assert client.get('/api/v1/geometry-jobs/'+job['id']+'/package-metadata',headers=HEADERS).json()['sha256']==hashlib.sha256(package).hexdigest()
    from nxserver.results import Results
    Results(store).collector(job['id'],'tool-metric','CUSTOM','admin')
    service.submit_report(job['id'],report_for(job),'admin');update(store,resource);audit=store.audit_entries()
    def forbidden(*args):raise AssertionError('Replay rewrote an immutable package')
    monkeypatch.setattr(Path,'write_bytes',forbidden);monkeypatch.setattr('nxserver.geometry.worker_sources',forbidden)
    assert create(client,key,payload).json()==job and count(store)==1 and store.audit_entries()==audit
    assert get(Store(store.root),'admin',key)['result']==job
    assert GeometryJobs(store).get(job['id'])['status']=='needs_selection'
    assert GeometryJobs(store).get(job['id'])['collector_packages']
    assert service.package(job['id'])==package and scan(store)['error_count']==0
    assert client.get('/api/v1/geometry-requests/'+key).status_code==401
    assert client.get('/api/v1/geometry-requests/'+key,headers={'Authorization':'Bearer '+ERP}).status_code==403


@pytest.mark.parametrize('change',[{'expected_revision':2},{'tool_type':'milling_square'}])
def test_same_key_cannot_bind_other_revision_or_type(pilot,change):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());payload=body(resource,asset)
    assert create(client,key,payload).status_code==201
    audit=store.audit_entries();assert create(client,key,{**payload,**change}).status_code==409
    assert count(store)==1 and store.audit_entries()==audit


def test_missing_receipt_and_preallocation_fence_block_a_late_create(pilot):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());before=store.audit_entries()
    assert client.get('/api/v1/geometry-requests/'+key,headers=HEADERS).status_code==404
    assert store.audit_entries()==before
    receipt=cancel(store,'admin',key);assert receipt['state']=='cancelled' and receipt['allocation'] is None
    audit=store.audit_entries();assert cancel(store,'admin',key)==receipt and store.audit_entries()==audit
    assert create(client,key,body(resource,asset)).status_code==409 and count(store)==0


def test_fence_after_commit_does_not_stop_delete_or_rewrite_job(pilot):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());job=create(client,key,body(resource,asset)).json()
    package=GeometryJobs(store).package(job['id']);before=store.audit_entries()
    receipt=cancel(store,'admin',key)
    assert receipt['state']=='committed' and receipt['result']==job and store.audit_entries()==before
    assert GeometryJobs(store).package(job['id'])==package and count(store)==1


def test_fence_during_package_write_keeps_bytes_and_marks_writer_finished_without_job(pilot,monkeypatch):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());entered=threading.Event();release=threading.Event();write=Path.write_bytes
    def delayed(path,content):entered.set();assert release.wait(timeout=15);return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(create,client,key,body(resource,asset));assert entered.wait(timeout=10)
        allocated=get(store,'admin',key);assert allocated['state']=='reserved'
        assert client.post('/api/v1/backups',headers=HEADERS,json={}).status_code==409
        assert cancel(store,'admin',key)['state']=='cancelled'
        assert client.post('/api/v1/backups',headers=HEADERS,json={}).status_code==409
        release.set();assert pending.result(timeout=20).status_code==409
    receipt=get(store,'admin',key);assert receipt['writer_finished_at'] and count(store)==0
    path=store.root/'geometry-packages'/allocated['allocation']['job_id']/'NX_STEP.zip';assert path.is_file()
    assert scan(store)['error_count']==0
    from nxserver.backups import FUTURES
    response=client.post('/api/v1/backups',headers=HEADERS,json={});assert response.status_code==202,response.text
    FUTURES[response.json()['id']].result(timeout=30)
    assert client.app.state.backups.get_job(response.json()['id'])['status']=='completed'


def test_second_store_does_not_create_another_package_for_same_reservation(pilot,monkeypatch):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());entered=threading.Event();release=threading.Event();write=Path.write_bytes
    def delayed(path,content):entered.set();assert release.wait(timeout=10);return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',delayed)
    other=GeometryJobs(Store(store.root))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(create,client,key,body(resource,asset));assert entered.wait(timeout=10)
        with pytest.raises(DomainError,match='partial allocation'):other.create(resource['id'],1,asset['id'],'drilling_solid','admin',request_key=key)
        release.set();first=pending.result(timeout=20).json()
    assert other.create(resource['id'],1,asset['id'],'drilling_solid','admin',request_key=key)==first
    assert count(store)==1 and len(list((store.root/'geometry-packages').iterdir()))==1


@pytest.mark.parametrize('failure',['write','audit','revision'])
def test_failure_keeps_diagnostic_allocation_and_no_job_commit(pilot,monkeypatch,failure):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());write=Path.write_bytes;audit=store.audit
    def writing(path,content):
        if failure=='write':raise OSError('Synthetic package disk failure')
        result=write(path,content)
        if failure=='revision':update(Store(store.root),resource)
        return result
    def auditing(db,actor,action,payload):
        if failure=='audit' and action=='geometry.job.created':raise RuntimeError('Synthetic geometry audit failure')
        return audit(db,actor,action,payload)
    monkeypatch.setattr(Path,'write_bytes',writing);monkeypatch.setattr(store,'audit',auditing)
    if failure=='audit':
        with pytest.raises(RuntimeError):create(client,key,body(resource,asset))
    else:assert create(client,key,body(resource,asset)).status_code==409
    receipt=get(store,'admin',key);assert receipt['state']=='failed' and receipt['writer_finished_at'] and count(store)==0
    assert create(client,key,body(resource,asset)).status_code==409
    assert scan(store)['error_count']==0


def test_package_tamper_does_not_recreate_or_discard_commit_receipt(pilot):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());job=create(client,key,body(resource,asset)).json()
    (store.root/'geometry-packages'/job['id']/'NX_STEP.zip').write_bytes(b'Changed isolated bytes')
    assert client.get('/api/v1/geometry-jobs/'+job['id']+'/package-metadata',headers=HEADERS).status_code==409
    assert get(store,'admin',key)['result']==job and count(store)==1


def test_receipt_corruption_cannot_be_replayed_as_a_success(pilot):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());create(client,key,body(resource,asset))
    with store.db() as db:db.execute('UPDATE geometry_requests SET snapshot_sha256=?',('0'*64,))
    assert client.get('/api/v1/geometry-requests/'+key,headers=HEADERS).status_code==409
    assert create(client,key,body(resource,asset)).status_code==409
    assert 'invalid_geometry_receipt' in {issue['code'] for issue in scan(store)['errors']}


def test_independent_report_writer_waits_before_reading_and_cannot_overwrite(pilot,monkeypatch):
    from contextlib import contextmanager
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4());job=create(client,key,body(resource,asset)).json()
    entered=threading.Event();release=threading.Event();second_started=threading.Event();audit=store.audit
    def auditing(db,actor,action,payload):
        result=audit(db,actor,action,payload)
        if action=='geometry.report.received':entered.set();assert release.wait(timeout=15)
        return result
    monkeypatch.setattr(store,'audit',auditing)
    other=Store(store.root);other_db=other.db;statements=[]
    @contextmanager
    def observed_db():
        with other_db() as db:
            def traced(sql):
                statements.append(sql)
                if sql.strip().upper()=='BEGIN IMMEDIATE':second_started.set()
            db.set_trace_callback(traced);yield db
    monkeypatch.setattr(other,'db',observed_db)
    one=report_for(job);two={**one,'warnings':['Another isolated run']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(GeometryJobs(store).submit_report,job['id'],one,'admin')
        assert entered.wait(timeout=10)
        second=pool.submit(GeometryJobs(other).submit_report,job['id'],two,'admin')
        try:assert second_started.wait(timeout=10)
        finally:release.set()
        assert first.result(timeout=20)['report']==one
        with pytest.raises(DomainError,match='already bound'):second.result(timeout=20)
    assert GeometryJobs(other).get(job['id'])['report']==one and scan(other)['error_count']==0


def test_geometry_reservation_rechecks_backup_after_earlier_authorization(pilot,monkeypatch):
    client,store=pilot;resource,asset=draft(client);backups=client.app.state.backups
    entered=threading.Event();release=threading.Event();backup=backups._backup
    def waiting(*args):entered.set();assert release.wait(timeout=15);return backup(*args)
    monkeypatch.setattr(backups,'_backup',waiting)
    response=client.post('/api/v1/backups',headers=HEADERS,json={});assert response.status_code==202
    assert entered.wait(timeout=10)
    try:
        with pytest.raises(DomainError,match='Backup/recovery writer'):
            GeometryJobs(store).create(resource['id'],1,asset['id'],'drilling_solid','admin',request_key=str(uuid.uuid4()))
    finally:release.set()
    from nxserver.backups import FUTURES
    FUTURES[response.json()['id']].result(timeout=30)
    assert count(store)==0


@pytest.mark.parametrize('legacy',[False,True])
def test_backup_restore_retains_new_receipts_and_validates_legacy_digest(pilot,legacy):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4())
    response=client.post('/api/v1/geometry-jobs',headers=HEADERS if legacy else headers(key),json=body(resource,asset));job=response.json()
    if legacy:
        with store.db() as db:db.execute('DROP TABLE geometry_requests')  # Own old fixture archive only.
    from nxserver.backups import FUTURES
    def finish(response):
        assert response.status_code==202,response.text
        identifier=response.json()['id'];FUTURES[identifier].result(timeout=30)
        assert client.app.state.backups.get_job(identifier)['status']=='completed'
        return identifier,client.app.state.backups.report(identifier)
    _,receipt=finish(client.post('/api/v1/backups',headers=HEADERS,json={}))
    preview_id,plan=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'G'}))
    assert plan['can_restore'],plan['errors']
    _,report=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={
        'name':'G','preview_id':preview_id,'preview_hash':plan['preview_hash'],'accept_unsigned':True}))
    restored=Store(report['target_folder'])
    if legacy:
        with restored.db() as db:assert db.execute('SELECT count(*) FROM geometry_requests').fetchone()[0]==0
    else:
        assert get(restored,'admin',key)['result']==job
        assert GeometryJobs(restored).create(resource['id'],1,asset['id'],'drilling_solid','admin',request_key=key)==job
    assert GeometryJobs(restored).package(job['id'])==GeometryJobs(store).package(job['id']) and scan(restored)['error_count']==0
