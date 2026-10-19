"""Bounded upload receipts/fences with isolated bytes; never NX or real models."""
import base64
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nxserver.asset_requests import get,cancel
from nxserver.integrity import scan
from nxserver.store import Store
from test_management import pilot,HEADERS,ERP_HEADERS


def body(content=b'Synthetic attachment bytes',name='tool.prt'):
    return {'filename':name,'content_base64':base64.b64encode(content).decode()}
def headers(key):return {**HEADERS,'Idempotency-Key':key}
def count(store):
    with store.db() as db:return db.execute('SELECT count(*) FROM assets').fetchone()[0]


def test_upload_replay_is_durable_and_does_not_create_another_path(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4());payload=body()
    first=client.post('/api/v1/assets',headers=headers(key),json=payload);assert first.status_code==201,first.text
    before=store.audit_entries();folders=list((store.root/'assets').iterdir())
    original=Path.write_bytes
    def forbidden(path,content):raise AssertionError('Replay rewrote immutable bytes')
    monkeypatch.setattr(Path,'write_bytes',forbidden)
    again=client.post('/api/v1/assets',headers=headers(key),json=payload)
    assert again.json()==first.json() and count(store)==1 and store.audit_entries()==before
    assert list((store.root/'assets').iterdir())==folders
    assert get(Store(store.root),'admin',key)['result']==first.json()
    assert client.get('/api/v1/asset-requests/'+key,headers=ERP_HEADERS).status_code==403
    assert client.get('/api/v1/asset-requests/'+key).status_code==401
    assert scan(store)['error_count']==0


@pytest.mark.parametrize('change',['filename','bytes'])
def test_same_upload_key_different_bytes_or_name_is_rejected_before_filesystem_write(pilot,change):
    client,store,*_=pilot;key=str(uuid.uuid4());first=client.post('/api/v1/assets',headers=headers(key),json=body())
    assert first.status_code==201
    other=body(name='different.prt') if change=='filename' else body(b'Different bytes')
    before=store.audit_entries();response=client.post('/api/v1/assets',headers=headers(key),json=other)
    assert response.status_code==409 and count(store)==1 and store.audit_entries()==before


def test_missing_receipt_is_not_stop_proof_and_fence_blocks_late_upload(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());before=store.audit_entries()
    assert client.get('/api/v1/asset-requests/'+key,headers=HEADERS).status_code==404
    assert store.audit_entries()==before
    one=client.post('/api/v1/asset-requests/'+key+'/cancel',headers=HEADERS)
    assert one.status_code==200 and one.json()['state']=='cancelled'
    audit=store.audit_entries();assert cancel(store,'admin',key)==one.json() and store.audit_entries()==audit
    assert client.post('/api/v1/assets',headers=headers(key),json=body()).status_code==409 and count(store)==0
    assert get(Store(store.root),'admin',key)['state']=='cancelled'


def test_cancel_after_commit_never_deletes_asset_or_history(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());first=client.post('/api/v1/assets',headers=headers(key),json=body()).json()
    before=store.audit_entries();receipt=cancel(store,'admin',key)
    assert receipt['state']=='committed' and receipt['result']==first and store.audit_entries()==before
    assert count(store)==1 and store.asset(first['id'])[1].read_bytes()==b'Synthetic attachment bytes'


def test_cancel_during_physical_write_prevents_metadata_commit_not_partial_path_deletion(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4());entered=threading.Event();continue_write=threading.Event();write=Path.write_bytes
    def delayed(path,content):entered.set();assert continue_write.wait(timeout=10);return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(client.post,'/api/v1/assets',headers=headers(key),json=body())
        assert entered.wait(timeout=10);receipt=get(store,'admin',key);assert receipt['state']=='reserved'
        assert cancel(store,'admin',key)['state']=='cancelled';continue_write.set()
        response=pending.result(timeout=20)
    assert response.status_code==409 and count(store)==0
    path=store.root/'assets'/receipt['allocation']['asset_id']/receipt['allocation']['filename']
    assert path.read_bytes()==b'Synthetic attachment bytes' and get(store,'admin',key)['state']=='cancelled'
    assert scan(store)['error_count']==0


def test_two_independent_stores_never_write_same_allocation_twice(pilot,monkeypatch):
    client,store,*_=pilot;other=Store(store.root);key=str(uuid.uuid4());entered=threading.Event();release=threading.Event();write=Path.write_bytes
    def delayed(path,content):entered.set();assert release.wait(timeout=10);return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(store.add_asset,'tool.prt',b'Synthetic attachment bytes','admin',request_key=key)
        assert entered.wait(timeout=10)
        from nxserver.safety import DomainError
        with pytest.raises(DomainError,match='partial/fenced'):other.add_asset('tool.prt',b'Synthetic attachment bytes','admin',request_key=key)
        release.set();first=pending.result(timeout=20)
    assert other.add_asset('tool.prt',b'Synthetic attachment bytes','admin',request_key=key)==first and count(store)==1


def test_physical_failure_preserves_failed_allocation_and_never_overwrites_on_retry(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4());write=Path.write_bytes
    def failure(path,content):raise OSError('Synthetic disk failure')
    monkeypatch.setattr(Path,'write_bytes',failure)
    response=client.post('/api/v1/assets',headers=headers(key),json=body());assert response.status_code==409
    receipt=get(store,'admin',key);assert receipt['state']=='failed' and receipt['allocation']['asset_id'] and count(store)==0
    monkeypatch.setattr(Path,'write_bytes',write)
    assert client.post('/api/v1/assets',headers=headers(key),json=body()).status_code==409 and count(store)==0
    assert scan(store)['error_count']==0


def test_asset_audit_failure_rolls_back_metadata_and_commit_receipt_but_keeps_allocation(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4());audit=store.audit
    def fail(db,actor,action,payload):
        if action=='asset.created':raise RuntimeError('Synthetic asset audit failure')
        return audit(db,actor,action,payload)
    monkeypatch.setattr(store,'audit',fail)
    with pytest.raises(RuntimeError):client.post('/api/v1/assets',headers=headers(key),json=body())
    receipt=get(store,'admin',key);assert receipt['state']=='failed' and receipt['result'] is None and count(store)==0
    assert (store.root/'assets'/receipt['allocation']['asset_id']/'tool.prt').is_file()
    assert scan(store)['error_count']==0


def test_backup_waits_for_actual_filesystem_writer_even_when_commit_fenced(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4());entered=threading.Event();release=threading.Event();write=Path.write_bytes
    def delayed(path,content):entered.set();assert release.wait(timeout=15);return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        attempt=pool.submit(client.post,'/api/v1/assets',headers=headers(key),json=body())
        assert entered.wait(timeout=10)
        assert client.post('/api/v1/backups',headers=HEADERS,json={}).status_code==409
        assert cancel(store,'admin',key)['state']=='cancelled'
        assert client.post('/api/v1/backups',headers=HEADERS,json={}).status_code==409
        release.set();assert attempt.result(timeout=20).status_code==409
    receipt=get(store,'admin',key);assert receipt['writer_finished_at'] and receipt['state']=='cancelled'
    from nxserver.backups import FUTURES
    response=client.post('/api/v1/backups',headers=HEADERS,json={});assert response.status_code==202,response.text
    FUTURES[response.json()['id']].result(timeout=30)
    assert client.app.state.backups.get_job(response.json()['id'])['status']=='completed'


def test_upload_reservation_rechecks_backup_pause_after_authorization(pilot,monkeypatch):
    client,store,*_=pilot;backups=client.app.state.backups;entered=threading.Event();release=threading.Event();backup=backups._backup
    def waiting(*args):entered.set();assert release.wait(timeout=15);return backup(*args)
    monkeypatch.setattr(backups,'_backup',waiting)
    response=client.post('/api/v1/backups',headers=HEADERS,json={});assert response.status_code==202
    assert entered.wait(timeout=10)
    from nxserver.safety import DomainError
    # Direct Store emulates a request whose authentication passed earlier.
    with pytest.raises(DomainError,match='Backup/recovery writer'):
        store.add_asset('tool.prt',b'Late upload','admin',request_key=str(uuid.uuid4()))
    release.set()
    from nxserver.backups import FUTURES
    FUTURES[response.json()['id']].result(timeout=30)
    assert count(store)==0


def test_corrupt_upload_receipt_is_not_replayed_as_success(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());client.post('/api/v1/assets',headers=headers(key),json=body())
    with store.db() as db:db.execute('UPDATE asset_requests SET response=?',('{"id":"wrong"}',))
    assert client.get('/api/v1/asset-requests/'+key,headers=HEADERS).status_code==409
    assert client.post('/api/v1/assets',headers=headers(key),json=body()).status_code==409
    assert 'invalid_upload_receipt' in {issue['code'] for issue in scan(store)['errors']}


@pytest.mark.parametrize('legacy',[False,True])
def test_backup_restore_keeps_receipt_or_validates_archive_before_upload_table(pilot,legacy):
    from nxserver.backups import FUTURES
    client,store,*_=pilot;key=str(uuid.uuid4())
    first=client.post('/api/v1/assets',headers=HEADERS if legacy else headers(key),json=body()).json()
    if legacy:
        with store.db() as db:db.execute('DROP TABLE asset_requests')  # Own isolated old archive only.
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
    if legacy:
        with restored.db() as db:assert db.execute('SELECT count(*) FROM asset_requests').fetchone()[0]==0
    else:
        assert get(restored,'admin',key)['result']==first
        before=restored.audit_entries();assert restored.add_asset('tool.prt',b'Synthetic attachment bytes','admin',request_key=key)==first
        assert restored.audit_entries()==before and count(restored)==1
    assert scan(restored)['error_count']==0
