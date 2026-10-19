"""System-log receipts/fences/restore with isolated plain text, not NX proof."""
import base64
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nxserver.log_requests import get,cancel
from nxserver.integrity import scan
from nxserver.safety import DomainError
from nxserver.store import Store
from test_management import pilot,HEADERS,ERP_HEADERS

RAW=b'NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\nSynthetic log, no CAM qualification'
def body(raw=RAW,name='test.syslog'):return {'filename':name,'content_base64':base64.b64encode(raw).decode()}
def headers(key):return {**HEADERS,'Idempotency-Key':key}
def count(store):
    with store.db() as db:return db.execute('SELECT COUNT(*) FROM syslogs').fetchone()[0]


def test_replay_returns_original_receipt_without_new_path_or_audit(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4());first=client.post('/api/v1/logs',headers=headers(key),json=body())
    assert first.status_code==201,first.text
    before=store.audit_entries();folders=list((store.root/'evidence').iterdir())
    def forbidden(*args):raise AssertionError('Receipt replay rewrote log bytes')
    monkeypatch.setattr(Path,'write_bytes',forbidden)
    again=client.post('/api/v1/logs',headers=headers(key),json=body())
    assert again.status_code==201 and again.json()==first.json() and count(store)==1
    assert store.audit_entries()==before and list((store.root/'evidence').iterdir())==folders
    assert get(Store(store.root),'admin',key)['result']==first.json()
    assert client.get('/api/v1/logs/'+first.json()['id']+'/metadata',headers=HEADERS).json()==first.json()
    assert scan(store)['error_count']==0


@pytest.mark.parametrize('change',['name','bytes'])
def test_same_key_different_payload_rejected_without_new_write(pilot,change):
    client,store,*_=pilot;key=str(uuid.uuid4());assert client.post('/api/v1/logs',headers=headers(key),json=body()).status_code==201
    before=store.audit_entries();other=body(name='other.log') if change=='name' else body(b'Different valid plain text')
    assert client.post('/api/v1/logs',headers=headers(key),json=other).status_code==409
    assert count(store)==1 and store.audit_entries()==before


def test_absent_receipt_is_not_stop_proof_and_fence_blocks_late_upload(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());before=store.audit_entries()
    assert client.get('/api/v1/log-requests/'+key,headers=HEADERS).status_code==404 and store.audit_entries()==before
    one=client.post('/api/v1/log-requests/'+key+'/cancel',headers=HEADERS);assert one.json()['state']=='cancelled'
    audit=store.audit_entries();assert cancel(store,'admin',key)==one.json() and store.audit_entries()==audit
    assert client.post('/api/v1/logs',headers=headers(key),json=body()).status_code==409 and count(store)==0
    assert get(Store(store.root),'admin',key)['state']=='cancelled'


def test_cancel_committed_upload_does_not_delete_or_qualify_log(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());log=client.post('/api/v1/logs',headers=headers(key),json=body()).json();before=store.audit_entries()
    receipt=cancel(store,'admin',key);assert receipt['state']=='committed' and receipt['result']==log and not receipt['files_verified']
    assert store.audit_entries()==before and count(store)==1 and store.active()['release_id'] is None


def test_cancel_during_write_keeps_partial_and_blocks_backup_until_writer_finishes(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4());entered=threading.Event();release=threading.Event();write=Path.write_bytes
    def delayed(path,content):entered.set();assert release.wait(timeout=15);return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(client.post,'/api/v1/logs',headers=headers(key),json=body());assert entered.wait(timeout=10)
        receipt=get(store,'admin',key);assert receipt['state']=='reserved'
        assert client.post('/api/v1/backups',headers=HEADERS,json={}).status_code==409
        assert cancel(store,'admin',key)['state']=='cancelled'
        assert client.post('/api/v1/backups',headers=HEADERS,json={}).status_code==409
        release.set();assert pending.result(timeout=20).status_code==409
    receipt=get(store,'admin',key);assert receipt['writer_finished_at'] and count(store)==0
    assert (store.root/'evidence'/receipt['allocation']['log_id']/'nx.syslog').read_bytes()==RAW
    assert scan(store)['error_count']==0


def test_two_stores_do_not_rewrite_one_allocation(pilot,monkeypatch):
    client,store,*_=pilot;other=Store(store.root);key=str(uuid.uuid4());entered=threading.Event();release=threading.Event();write=Path.write_bytes
    def delayed(path,content):entered.set();assert release.wait(timeout=15);return write(path,content)
    monkeypatch.setattr(Path,'write_bytes',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(store.add_log,'test.syslog',RAW,'admin',request_key=key);assert entered.wait(timeout=10)
        with pytest.raises(DomainError,match='partial/fenced'):other.add_log('test.syslog',RAW,'admin',request_key=key)
        release.set();first=pending.result(timeout=20)
    assert other.add_log('test.syslog',RAW,'admin',request_key=key)==first and count(store)==1


@pytest.mark.parametrize('failure',['disk','corrupt_bytes','audit'])
def test_failed_upload_never_commits_success_or_rewrites_partial(pilot,monkeypatch,failure):
    client,store,*_=pilot;key=str(uuid.uuid4());write=Path.write_bytes;audit=store.audit
    if failure=='disk':
        def fail(*args):raise OSError('Synthetic disk failure')
        monkeypatch.setattr(Path,'write_bytes',fail)
    elif failure=='corrupt_bytes':monkeypatch.setattr(Path,'write_bytes',lambda path,raw:write(path,raw+b'changed'))
    else:
        def fail(db,actor,action,payload):
            if action=='syslog.uploaded':raise RuntimeError('Synthetic commit/audit failure')
            return audit(db,actor,action,payload)
        monkeypatch.setattr(store,'audit',fail)
    if failure=='audit':
        with pytest.raises(RuntimeError):client.post('/api/v1/logs',headers=headers(key),json=body())
    else:assert client.post('/api/v1/logs',headers=headers(key),json=body()).status_code==409
    receipt=get(store,'admin',key);assert receipt['state']=='failed' and receipt['writer_finished_at'] and count(store)==0
    monkeypatch.setattr(Path,'write_bytes',write);monkeypatch.setattr(store,'audit',audit)
    assert client.post('/api/v1/logs',headers=headers(key),json=body()).status_code==409 and count(store)==0
    assert scan(store)['error_count']==0


def test_receipt_is_metadata_only_tampering_blocks_replay_and_file_access(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());log=client.post('/api/v1/logs',headers=headers(key),json=body()).json()
    path=store.root/'evidence'/log['id']/'nx.syslog';path.write_bytes(b'Externally changed synthetic log')
    assert get(store,'admin',key)['result']==log
    assert client.post('/api/v1/logs',headers=headers(key),json=body()).json()==log
    assert client.get('/api/v1/logs/'+log['id']+'/metadata',headers=HEADERS).status_code==409
    with store.db() as db:db.execute('UPDATE log_requests SET response=?',('{"id":"wrong"}',))
    assert client.get('/api/v1/log-requests/'+key,headers=HEADERS).status_code==409
    assert 'invalid_log_receipt' in {item['code'] for item in scan(store)['errors']}


def test_role_key_filename_boundaries_and_backup_pause(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4())
    for endpoint in ('/api/v1/log-requests/'+key,'/api/v1/logs/uploaded-'+key+'/metadata'):
        assert client.get(endpoint).status_code==401 and client.get(endpoint,headers=ERP_HEADERS).status_code==403
    assert client.post('/api/v1/log-requests/'+key+'/cancel',headers=ERP_HEADERS).status_code==403
    assert client.post('/api/v1/logs',headers=headers('wrong'),json=body()).status_code==400
    for name in ('txt','.log','../wrong.log',r'W:\SiemensShare\nx2406\test.log'):
        assert client.post('/api/v1/logs',headers=headers(key),json=body(name=name)).status_code==400
    monkeypatch.setattr('nxserver.log_requests.backup_active',lambda store:True)
    with pytest.raises(DomainError,match='Backup/recovery'):store.add_log('test.log',RAW,'admin',request_key=key)
    assert count(store)==0


@pytest.mark.parametrize('legacy',[False,True])
def test_backup_restore_preserves_log_receipt_and_supports_pre_receipt_archive(pilot,legacy):
    from nxserver.backups import FUTURES
    client,store,*_=pilot;key=str(uuid.uuid4())
    log=client.post('/api/v1/logs',headers=HEADERS if legacy else headers(key),json=body()).json()
    if legacy:
        with store.db() as db:db.execute('DROP TABLE log_requests')
    def finish(response):
        assert response.status_code==202,response.text
        identifier=response.json()['id'];FUTURES[identifier].result(timeout=30)
        assert client.app.state.backups.get_job(identifier)['status']=='completed'
        return identifier,client.app.state.backups.report(identifier)
    _,receipt=finish(client.post('/api/v1/backups',headers=HEADERS,json={}))
    preview_id,plan=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'R'}))
    assert plan['can_restore'],plan['errors']
    _,report=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={'name':'R','preview_id':preview_id,'preview_hash':plan['preview_hash'],'accept_unsigned':True}))
    restored=Store(report['target_folder']);assert restored.log_metadata(log['id'])==log
    if legacy:
        with restored.db() as db:assert db.execute('SELECT COUNT(*) FROM log_requests').fetchone()[0]==0
    else:assert get(restored,'admin',key)['result']==log
    assert restored.active()['release_id'] is None and scan(restored)['error_count']==0
